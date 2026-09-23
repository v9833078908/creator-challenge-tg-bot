"""Планировщик: дайджест 00:00, напоминания 21:00/20:00, кик 00:05 (время локальное челленджа).

Ошибки отправок логируются, джоб не падает.
"""

import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select

from bot.config import settings
from bot.db import SessionFactory, repo
from bot.db.models import DailyClosure, utcnow
from bot.services import digest, texts

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _parse_hhmm(value: str) -> tuple[int, int]:
    hh, mm = value.strip().split(":")
    return int(hh), int(mm)


def _now_naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- Джобы -----------------------------------------------------------------


async def job_report_reminder(bot: Bot, stream_id: int) -> None:
    async with SessionFactory() as session:
        stream = await repo.get_stream(session, stream_id)
        if stream is None or not stream.is_active:
            return
        missing = await digest.missing_reporters(session, stream, _now_naive_utc())
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
        yesterday = digest.today_local(stream, _now_naive_utc()) - timedelta(days=1)
        await digest.close_day(
            session, stream, yesterday, settings.penalty_grace_hours, settings.report_deadline
        )
    await send_digest_if_needed(bot, stream_id, yesterday)


async def send_digest_if_needed(bot: Bot, stream_id: int, day) -> None:
    async with SessionFactory() as session:
        stream = await repo.get_stream(session, stream_id)
        closure = await session.get(DailyClosure, (stream_id, day))
        if stream is None or closure is None or closure.digest_sent_at is not None:
            return
        outcome = await digest.day_outcome(session, stream, day, settings.report_deadline)
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
    last_closed = (await session.execute(select(func.max(DailyClosure.report_date)).where(
        DailyClosure.stream_id == stream_id
    ))).scalar_one()
    first = (
        last_closed + timedelta(days=1)
        if last_closed else max(today - timedelta(days=1), digest.today_local(stream, stream.created_at))
    )
    return [first + timedelta(days=i) for i in range(max(0, (today - first).days))]


async def recover_missed_days(bot: Bot) -> None:
    async with SessionFactory() as session:
        streams = await repo.get_active_streams(session)
    for stream in streams:
        async with SessionFactory() as session:
            today = digest.today_local(stream, _now_naive_utc())
            days = await pending_close_dates(session, stream.id, today)
            unsent = list((await session.execute(select(DailyClosure.report_date).where(
                DailyClosure.stream_id == stream.id,
                DailyClosure.digest_sent_at.is_(None),
            ))).scalars().all())
        for day in days:
            async with SessionFactory() as session:
                await digest.close_day(
                    session, stream, day, settings.penalty_grace_hours, settings.report_deadline
                )
            logger.info("recovered close_day stream=%s day=%s", stream.id, day)
        for day in sorted(set(days + unsent)):
            await send_digest_if_needed(bot, stream.id, day)


async def job_penalty_reminder(bot: Bot, stream_id: int) -> None:
    async with SessionFactory() as session:
        stream = await repo.get_stream(session, stream_id)
        if stream is None or not stream.is_active:
            return
        holders = await digest.open_penalty_holders(session, stream.id)
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
        debtors = await digest.expired_penalty_debtors(session, stream.id, _now_naive_utc())
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
