"""Команды владельца (OWNER_TG_ID). Тонкие хендлеры — вся логика в services/repo."""

import logging
import re
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import settings
from bot.db import SessionFactory, repo
from bot.db.models import ParticipantStatus
from bot.services import digest, texts

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

    m = USERNAME_RE.match(command.args or "") if command.args else None
    if not m:
        await message.answer(texts.approve_usage())
        return
    username = m.group(1)

    async with SessionFactory() as session:
        stream = await repo.get_active_stream(session)
        if stream is None:
            await message.answer(texts.no_stream_admin())
            return

        participant = await repo.get_participant_by_username(session, stream.id, username)
        if participant is None:
            await repo.approve_participant(session, stream.id, username)
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

    m = USERNAME_RE.match(command.args or "") if command.args else None
    if not m:
        await message.answer(texts.revoke_usage())
        return
    username = m.group(1)

    async with SessionFactory() as session:
        stream = await repo.get_active_stream(session)
        if stream is None:
            await message.answer(texts.no_stream_admin())
            return

        participant = await repo.get_participant_by_username(session, stream.id, username)
        if participant is not None and participant.status == ParticipantStatus.APPROVED:
            await repo.revoke_participant(session, participant)
            await session.commit()
            await message.answer(texts.revoke_ok(username))
            return

        await message.answer(texts.revoke_not_found(username))


@router.message(Command("stats"), F.chat.type == "private")
async def stats(message: Message) -> None:
    """В личке владельцу: быстрая статистика челленджа."""
    if not _is_owner(message):
        await message.answer(texts.owner_only())
        return

    async with SessionFactory() as session:
        stream = await repo.get_active_stream(session)
        if stream is None:
            await message.answer(texts.no_stream_admin())
            return
        s = await digest.collect_stats(
            session, stream, datetime.now(timezone.utc).replace(tzinfo=None)
        )
    await message.answer(texts.stats_text(s))
