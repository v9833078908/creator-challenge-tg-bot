"""Команды владельца (OWNER_TG_ID). Тонкие хендлеры — вся логика в services/repo."""

import logging
import re
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import settings
from bot.db import SessionFactory, repo
from bot.db.models import CourseRun, ParticipantStatus
from bot.services import chat_roles, digest, lessons, texts

logger = logging.getLogger(__name__)
router = Router(name="admin")

USERNAME_RE = re.compile(r"^@?([A-Za-z0-9_]{5,32})$")


def _is_owner(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id == settings.owner_tg_id


@router.message(Command("init_stream"), F.chat.type.in_({"group", "supergroup"}))
async def init_stream(message: Message, bot: Bot) -> None:
    """В группе: создать поток, привязать чат, сгенерировать бессрочную инвайт-ссылку."""
    if not _is_owner(message):
        await message.reply(texts.owner_only())
        return

    async with SessionFactory() as session:
        if await repo.get_stream_by_chat_id(session, message.chat.id) is not None:
            await message.reply(texts.stream_exists())
            return

    try:
        link = await bot.create_chat_invite_link(message.chat.id)
    except Exception as e:
        logger.warning("create_chat_invite_link failed: %s", e)
        await message.reply(texts.stream_need_admin())
        return

    async with SessionFactory() as session:
        stream = await repo.create_stream(
            session,
            title=message.chat.title or "Creator Hero",
            chat_id=message.chat.id,
            invite_link=link.invite_link,
            timezone=settings.challenge_timezone,
        )
        await session.commit()

        # Подключаем расписание для нового потока на лету
        from bot.scheduler import jobs as sched_jobs

        if sched_jobs.get_scheduler() is not None:
            await sched_jobs.register_stream_jobs(sched_jobs.get_scheduler(), bot, stream.id)

    await message.answer(texts.stream_created(message.chat.title or "Creator Hero", link.invite_link))


@router.message(Command("approve"), F.chat.type == "private")
async def approve(message: Message, command: CommandObject, bot: Bot) -> None:
    """В личке: /approve @username — добавить в whitelist или вернуть кикнутого."""
    if not _is_owner(message):
        await message.answer(texts.owner_only())
        return

    target = (command.args or "").strip()
    match = USERNAME_RE.fullmatch(target)
    if not target.isdecimal() and match is None:
        await message.answer(texts.approve_usage())
        return
    username = target if target.isdecimal() else match.group(1)

    async with SessionFactory() as session:
        stream = await repo.get_active_stream(session)
        if stream is None:
            await message.answer(texts.no_stream_admin())
            return

        participant = await _course_participant(session, stream.id, target)
        if participant is None:
            if target.isdecimal():
                await message.answer(
                    "Не нашёл привязанного участника с таким Telegram ID. "
                    "Нового участника сначала добавь через /approve @username."
                )
                return
            await repo.approve_participant(session, stream.id, username)
            await session.commit()
            await message.answer(texts.approve_ok(username))
            return

        if participant.status == ParticipantStatus.REVOKED:
            participant.status = ParticipantStatus.APPROVED
            await session.commit()
            await message.answer(texts.approve_ok(username))
            return

        if participant.status == ParticipantStatus.KICKED:
            if participant.tg_user_id is not None:
                try:
                    await bot.unban_chat_member(stream.chat_id, participant.tg_user_id)
                except Exception as e:
                    logger.warning("unban failed for %s: %s", username, e)
                    await message.answer(texts.unban_failed(username))
                    return
            participant.status = ParticipantStatus.APPROVED
            await repo.cancel_open_penalties(session, participant.id)
            await session.commit()
            await message.answer(texts.approve_back(username))
            return

        await message.answer(texts.approve_already(username))


@router.message(Command("revoke"), F.chat.type == "private")
async def revoke(message: Message, command: CommandObject) -> None:
    """В личке: /revoke @username — убрать из whitelist (только ещё не вступивших)."""
    if not _is_owner(message):
        await message.answer(texts.owner_only())
        return

    target = (command.args or "").strip()
    match = USERNAME_RE.fullmatch(target)
    if not target.isdecimal() and match is None:
        await message.answer(texts.revoke_usage())
        return
    username = target if target.isdecimal() else match.group(1)

    async with SessionFactory() as session:
        stream = await repo.get_active_stream(session)
        if stream is None:
            await message.answer(texts.no_stream_admin())
            return

        participant = await _course_participant(session, stream.id, target)
        if participant is not None and participant.status == ParticipantStatus.APPROVED:
            await repo.revoke_participant(session, participant)
            await session.commit()
            await message.answer(texts.revoke_ok(username))
            return

        await message.answer(texts.revoke_not_found(username))


@router.message(Command("stats"), F.chat.type == "private")
async def stats(message: Message, bot: Bot) -> None:
    """В личке владельцу: быстрая статистика челленджа."""
    if not _is_owner(message):
        await message.answer(texts.owner_only())
        return

    async with SessionFactory() as session:
        stream = await repo.get_active_stream(session)
        if stream is None:
            await message.answer(texts.no_stream_admin())
            return
        try:
            chat_owner_id = await chat_roles.get_chat_owner_id(bot, stream.chat_id)
        except Exception:
            logger.warning("stats owner lookup failed stream=%s", stream.id)
            await message.answer("Не удалось проверить владельца чата. Попробуй /stats чуть позже.")
            return
        s = await digest.collect_stats(
            session, stream, datetime.now(timezone.utc).replace(tzinfo=None),
            exclude_tg_user_ids=frozenset({chat_owner_id}),
        )
    await message.answer(texts.stats_text(s))


async def _course_participant(session, stream_id: int, target: str):
    if target.isdecimal():
        return await repo.get_participant_by_tg_id(session, stream_id, int(target))
    if USERNAME_RE.fullmatch(target):
        return await repo.get_participant_by_username(session, stream_id, target)
    return None


@router.message(Command("course_status"), F.chat.type == "private")
async def course_status(message: Message, command: CommandObject) -> None:
    if not _is_owner(message):
        await message.answer(texts.owner_only())
        return
    target = (command.args or "").strip()
    async with SessionFactory() as session:
        stream = await repo.get_active_stream(session)
        if stream is None:
            await message.answer(texts.no_stream_admin())
            return
        person = await _course_participant(session, stream.id, target)
        if person is None:
            await message.answer("Укажи участника: /course_status @username или Telegram ID.")
            return
        access_note = ""
        if person.status == ParticipantStatus.LEFT:
            access_note = "Уроки на паузе: участнику нужно вернуться в чат по ссылке."
        elif person.status in {ParticipantStatus.KICKED, ParticipantStatus.REVOKED}:
            access_note = (
                "Уроки на паузе. Сначала организатору нужно восстановить доступ: "
                f"/approve {person.tg_user_id}. Затем участник сможет вернуться в чат."
            )
        run = await session.get(CourseRun, person.id)
        if run is None:
            await message.answer(
                "Участник ещё не активировал уроки. " + (access_note or "Нужно написать боту /start в личке.")
            )
            return
        deliveries = await lessons.course_deliveries(session, person.id)
        sent = sum(row.status == "sent" for row in deliveries)
        blocked = next((row for row in deliveries if row.status in {"sending", "uncertain", "failed"}), None)
        if sent == 33:
            status = "Курс завершён."
        elif sent == 0:
            due = lessons.moscow_now(lessons.first_lesson_due_at(run))
            status = f"Первый урок запланирован на {due:%d.%m.%Y, %H:%M} МСК; следующие — в 08:00 МСК."
        else:
            status = "Следующий урок — в личку, не раньше 08:00 МСК."
        if access_note and sent < 33:
            status = access_note
        if blocked:
            status = (
                (access_note + " " if access_note else "")
                + f"Урок {blocked.lesson_day} остановлен ({blocked.status}). Проверь личку с участником: "
                f"/resolve_lesson {person.tg_user_id} sent — урок уже есть; "
                f"/resolve_lesson {person.tg_user_id} retry — урока точно нет."
            )
    await message.answer(
        f"Creator Hero · участник {person.tg_user_id} · 08:00 МСК\nОтправлено {sent}/33. {status}"
    )


@router.message(Command("resolve_lesson"), F.chat.type == "private")
async def resolve_lesson(message: Message, command: CommandObject) -> None:
    if not _is_owner(message):
        await message.answer(texts.owner_only())
        return
    parts = (command.args or "").split()
    if len(parts) != 2 or parts[1] not in {"sent", "retry"}:
        await message.answer("После проверки у участника: /resolve_lesson @username sent или retry.")
        return
    async with SessionFactory() as session:
        stream = await repo.get_active_stream(session)
        if stream is None:
            await message.answer(texts.no_stream_admin())
            return
        person = await _course_participant(session, stream.id, parts[0])
        if person is None:
            await message.answer("Не нашёл участника. Укажи @username или его Telegram ID.")
            return
        try:
            await lessons.resolve_delivery(
                session,
                person.id,
                parts[1],
                datetime.now(timezone.utc).replace(tzinfo=None),
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return
    await message.answer("Решение сохранено. /course_status @username — состояние личной цепочки.")
