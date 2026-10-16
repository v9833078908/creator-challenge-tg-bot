"""Планировщик: дайджест 00:00, напоминания 18:00/20:00, кик 00:05 (время локальное челленджа).

Ошибки отправок логируются, джоб не падает.
"""

import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import func, select

from bot.config import settings
from bot.db import SessionFactory, repo
from bot.db.models import (
    CourseRun,
    DailyClosure,
    LessonDelivery,
    Participant,
    ParticipantStatus,
    Stream,
    utcnow,
)
from bot.services import digest, lessons, texts
from bot.services.chat_roles import get_chat_owner_id

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _parse_hhmm(value: str) -> tuple[int, int]:
    hh, mm = value.strip().split(":")
    return int(hh), int(mm)


def _now_naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _owner_exclusion(bot: Bot, stream: Stream) -> frozenset[int] | None:
    try:
        return frozenset({await get_chat_owner_id(bot, stream.chat_id)})
    except Exception:
        logger.warning("chat owner lookup failed; scheduled obligations paused stream=%s", stream.id)
        return None


# --- Джобы -----------------------------------------------------------------


async def job_lesson(bot: Bot, stream_id: int, participant_id: int | None = None) -> None:
    async with SessionFactory() as session:
        stream = await repo.get_stream(session, stream_id)
        if stream is None or not stream.is_active:
            return
        people = list(
            (
                await session.execute(
                    select(Participant)
                    .join(
                        CourseRun,
                        CourseRun.participant_id == Participant.id,
                    )
                    .where(
                        Participant.stream_id == stream_id,
                        Participant.status.in_([ParticipantStatus.APPROVED, ParticipantStatus.ACTIVE]),
                        Participant.tg_user_id.is_not(None),
                    )
                    .order_by(Participant.id)
                )
            )
            .scalars()
            .all()
        )
        if not people:
            return
        excluded = await _owner_exclusion(bot, stream)
        if excluded is None:
            return
        people = [person for person in people if person.tg_user_id not in excluded]
        if not people:
            return
        try:
            catalog = lessons.load_catalog()
        except (OSError, ValueError, TypeError, KeyError):
            logger.error("lesson catalog unavailable; stream=%s remains paused", stream_id)
            return
        for person in people:
            if participant_id is not None and person.id != participant_id:
                continue
            await lessons.deliver_due_lesson(session, stream, person, bot, catalog, _now_naive_utc())
            for row in await lessons.course_deliveries(session, person.id):
                if row.status not in {"sending", "uncertain", "failed"} or row.owner_notified_at:
                    continue
                if not settings.owner_tg_id:
                    continue
                detail = (
                    "Telegram явно отклонил отправку. Исправь причину перед повтором."
                    if row.status == "failed"
                    else "Результат отправки неизвестен: сообщение могло прийти участнику."
                )
                try:
                    await bot.send_message(
                        settings.owner_tg_id,
                        f"⚠️ Урок {row.lesson_day}, участник {person.tg_user_id}: отправка остановлена.\n"
                        f"{detail}\n"
                        "Уточни у участника, пришёл ли урок. Если уже есть: "
                        f"/resolve_lesson {person.tg_user_id} sent\n"
                        f"Если урока точно нет: /resolve_lesson {person.tg_user_id} retry\n"
                        "Повтор разрешён только после проверки, иначе возможен дубль.",
                    )
                except Exception:
                    logger.warning("lesson owner notification failed participant=%s", person.id)
                else:
                    row.owner_notified_at = utcnow()
                    await session.commit()


async def recover_lessons(bot: Bot) -> None:
    """At most one next personal lesson per participant, never an outage backlog burst."""
    async with SessionFactory() as session:
        streams = await repo.get_active_streams(session)
    for stream in streams:
        await job_lesson(bot, stream.id)


async def job_report_reminder(bot: Bot, stream_id: int) -> None:
    async with SessionFactory() as session:
        stream = await repo.get_stream(session, stream_id)
        if stream is None or not stream.is_active:
            return
        excluded = await _owner_exclusion(bot, stream)
        if excluded is None:
            return
        missing = await digest.missing_reporters(
            session, stream, _now_naive_utc(), exclude_tg_user_ids=excluded,
        )
    if not missing:
        return
    try:
        await bot.send_message(stream.chat_id, texts.reminder_report(missing))
    except Exception as e:
        logger.warning("report_reminder stream=%s: %s", stream_id, e)


async def job_close_day(bot: Bot, stream_id: int) -> None:
    async with SessionFactory() as session:
        stream = await repo.get_stream(session, stream_id)
        if stream is None or not stream.is_active:
            return
        excluded = await _owner_exclusion(bot, stream)
        if excluded is None:
            return
        yesterday = digest.today_local(stream, _now_naive_utc()) - timedelta(days=1)
        await digest.close_day(
            session, stream, yesterday, settings.penalty_grace_hours, settings.report_deadline,
            exclude_tg_user_ids=excluded,
        )
    await send_digest_if_needed(bot, stream_id, yesterday)


async def send_digest_if_needed(bot: Bot, stream_id: int, day) -> None:
    """Publish only yesterday's outcome; old unsent digests are no longer relevant."""
    async with SessionFactory() as session:
        stream = await repo.get_stream(session, stream_id)
        closure = await session.get(DailyClosure, (stream_id, day))
        if stream is None or closure is None or closure.digest_sent_at is not None:
            return
        if day != digest.today_local(stream, _now_naive_utc()) - timedelta(days=1):
            return
        excluded = await _owner_exclusion(bot, stream)
        if excluded is None:
            return
        outcome = await digest.day_outcome(
            session, stream, day, settings.report_deadline, exclude_tg_user_ids=excluded,
        )
        if outcome.total == 0:
            return
    try:
        await bot.send_message(
            stream.chat_id,
            texts.digest(outcome.for_date, outcome.total, outcome.reported, outcome.penalized),
        )
    except Exception as e:
        logger.warning("close_day stream=%s: %s", stream_id, e)
        return
    async with SessionFactory() as session:
        closure = await session.get(DailyClosure, (stream_id, day))
        if closure is not None and closure.digest_sent_at is None:
            closure.digest_sent_at = utcnow()
            await session.commit()


async def pending_close_dates(session, stream_id: int, today) -> list:
    stream = await repo.get_stream(session, stream_id)
    if stream is None:
        return []
    last_closed = (
        await session.execute(
            select(func.max(DailyClosure.report_date)).where(DailyClosure.stream_id == stream_id)
        )
    ).scalar_one()
    first = (
        last_closed + timedelta(days=1)
        if last_closed
        else max(today - timedelta(days=1), digest.today_local(stream, stream.created_at))
    )
    return [first + timedelta(days=i) for i in range(max(0, (today - first).days))]


async def recover_missed_days(bot: Bot) -> None:
    async with SessionFactory() as session:
        streams = await repo.get_active_streams(session)
    for stream in streams:
        excluded = await _owner_exclusion(bot, stream)
        if excluded is None:
            continue
        async with SessionFactory() as session:
            today = digest.today_local(stream, _now_naive_utc())
            days = await pending_close_dates(session, stream.id, today)
        for day in days:
            async with SessionFactory() as session:
                await digest.close_day(
                    session, stream, day, settings.penalty_grace_hours, settings.report_deadline,
                    exclude_tg_user_ids=excluded,
                )
            logger.info("recovered close_day stream=%s day=%s", stream.id, day)
        # Restore all accounting above, but do not flood the chat after an outage.
        await send_digest_if_needed(bot, stream.id, today - timedelta(days=1))


async def job_penalty_reminder(bot: Bot, stream_id: int) -> None:
    async with SessionFactory() as session:
        stream = await repo.get_stream(session, stream_id)
        if stream is None or not stream.is_active:
            return
        excluded = await _owner_exclusion(bot, stream)
        if excluded is None:
            return
        holders = await digest.open_penalty_holders(
            session, stream.id, exclude_tg_user_ids=excluded,
        )
    if not holders:
        return
    try:
        await bot.send_message(stream.chat_id, texts.penalty_reminder(holders))
    except Exception as e:
        logger.warning("penalty_reminder stream=%s: %s", stream_id, e)


async def job_kick_expired(bot: Bot, stream_id: int) -> None:
    async with SessionFactory() as session:
        stream = await repo.get_stream(session, stream_id)
        if stream is None or not stream.is_active:
            return
        excluded = await _owner_exclusion(bot, stream)
        if excluded is None:
            return
        debtors = await digest.expired_penalty_debtors(
            session, stream.id, _now_naive_utc(), exclude_tg_user_ids=excluded,
        )
        if not debtors:
            return

        kicked = []
        for p in debtors:
            try:
                await bot.ban_chat_member(stream.chat_id, p.tg_user_id)  # без unban: возврат — через /approve
                kicked.append(p)
                await digest.expire_penalties_and_mark_kicked(session, p)
                try:
                    await bot.send_message(p.tg_user_id, texts.kicked_dm(texts.display_name(p)))
                except Exception:
                    pass  # не пишет боту — не страшно
            except Exception as e:
                logger.error("kick failed stream=%s user=%s: %s", stream_id, p.tg_user_id, e)
                if settings.owner_tg_id:
                    try:
                        await bot.send_message(
                            settings.owner_tg_id,
                            f"⚠️ Не смог кикнуть {texts.display_name(p)} — "
                            f"проверь, что я админ чата с правом блокировки.",
                        )
                    except Exception:
                        pass

    if kicked:
        try:
            await bot.send_message(stream.chat_id, texts.kick_notice(kicked))
        except Exception as e:
            logger.warning("kick_notice stream=%s: %s", stream_id, e)


# --- Регистрация ------------------------------------------------------------


async def register_first_lesson_job(
    scheduler: AsyncIOScheduler | None,
    bot: Bot,
    participant_id: int,
) -> None:
    """Reconstruct the precise one-off timer from durable activation, never /start time."""
    if scheduler is None:
        # During update draining the scheduler is not created yet. Startup rebuilds it.
        return
    async with SessionFactory() as session:
        run = await session.get(CourseRun, participant_id)
        person = await session.get(Participant, participant_id)
        if run is None or person is None:
            return
        if person.status not in {ParticipantStatus.APPROVED, ParticipantStatus.ACTIVE}:
            return
        stream = await session.get(Stream, run.stream_id)
        if stream is None or not stream.is_active:
            return
        first_delivery = await session.get(LessonDelivery, (participant_id, 1))
        if first_delivery is not None and first_delivery.status != "pending":
            return
        due = lessons.first_lesson_due_at(run)
        if due <= _now_naive_utc():
            # The existing catch-up/minutely path handles overdue timers safely.
            return
    scheduler.add_job(
        job_lesson,
        DateTrigger(run_date=due.replace(tzinfo=timezone.utc)),
        id=f"lesson_first:{participant_id}",
        replace_existing=True,
        args=[bot, run.stream_id, participant_id],
        max_instances=1,
        misfire_grace_time=None,
    )


async def register_stream_jobs(scheduler: AsyncIOScheduler, bot: Bot, stream_id: int) -> None:
    async with SessionFactory() as session:
        stream = await repo.get_stream(session, stream_id)
    if stream is None:
        return
    tz = stream.timezone

    for job_id, hhmm, job_func in [
        (f"report_reminder:{stream_id}", settings.report_reminder_time, job_report_reminder),
        (f"close_day:{stream_id}", settings.digest_time, job_close_day),
        (f"penalty_reminder:{stream_id}", settings.penalty_reminder_time, job_penalty_reminder),
        (f"kick_expired:{stream_id}", settings.kick_time, job_kick_expired),
    ]:
        hour, minute = _parse_hhmm(hhmm)
        scheduler.add_job(
            job_func,
            CronTrigger(hour=hour, minute=minute, timezone=tz),
            id=job_id,
            replace_existing=True,
            args=[bot, stream_id],
        )
    # Every minute checks due state, including a safe 429 retry. Sending is gated
    # at 08:00 Moscow after the first lesson due at activation + 10 minutes.
    scheduler.add_job(
        job_lesson,
        CronTrigger(minute="*", timezone="Europe/Moscow"),
        id=f"lesson:{stream_id}",
        replace_existing=True,
        args=[bot, stream_id],
        max_instances=1,
        coalesce=True,
    )
    async with SessionFactory() as session:
        participant_ids = list(
            (
                await session.execute(
                    select(CourseRun.participant_id).where(
                        CourseRun.stream_id == stream_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    for participant_id in participant_ids:
        await register_first_lesson_job(scheduler, bot, participant_id)
    logger.info("jobs registered for stream=%s tz=%s", stream_id, tz)


async def register_all_jobs(scheduler: AsyncIOScheduler, bot: Bot) -> None:
    async with SessionFactory() as session:
        streams = await repo.get_active_streams(session)
    for s in streams:
        await register_stream_jobs(scheduler, bot, s.id)


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler


def set_scheduler(scheduler: AsyncIOScheduler) -> None:
    global _scheduler
    _scheduler = scheduler
