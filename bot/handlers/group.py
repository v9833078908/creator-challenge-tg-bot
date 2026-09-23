"""Групповые события: вход/выход участников, отчёты #пост. (#штраф — Этап 4.)

Отредактированные сообщения (edited_message) сознательно НЕ обрабатываются —
античит, как у оригинала: нельзя сдать пустышку и дописать позже.
"""

import logging
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.types import Message

from bot.config import settings
from bot.db import SessionFactory, repo
from bot.db.models import ParticipantStatus
from bot.services import challenge, digest, onboarding, reports, texts

logger = logging.getLogger(__name__)
router = Router(name="group")


def _event_utc(message: Message) -> datetime:
    event_time = message.date
    if event_time.tzinfo is None:
        return event_time
    return event_time.astimezone(timezone.utc).replace(tzinfo=None)


@router.message(F.new_chat_members)
async def on_new_members(message: Message, bot: Bot) -> None:
    """Вход в чат: approved/left/kicked → active (+восстановление/сброс штрафов)."""
    async with SessionFactory() as session:
        stream = await repo.get_stream_by_chat_id(session, message.chat.id)
        if stream is None:
            return

        for user in message.new_chat_members:
            if user.is_bot:
                continue
            outcome = await onboarding.process_join(
                session,
                stream=stream,
                tg_user_id=user.id,
                username=user.username,
                full_name=user.full_name,
                grace_hours=settings.penalty_grace_hours,
                occurred_at=_event_utc(message),
            )
            if not outcome.activated:
                if outcome.reason == "unknown":
                    logger.info(
                        "unknown user %s joined chat %s (invite leaked?)", user.id, message.chat.id
                    )
                    if settings.owner_tg_id:
                        try:
                            await bot.send_message(
                                settings.owner_tg_id,
                                texts.unknown_joined(
                                    user.full_name, user.username, message.chat.title or "чат"
                                ),
                            )
                        except Exception as e:
                            logger.warning("unknown_join notification failed: %s", e)
                continue

            if outcome.restored_penalties > 0:
                await message.answer(
                    texts.welcome_back_with_penalties(
                        user.full_name, user.username, outcome.restored_penalties
                    )
                )
            else:
                await message.answer(texts.welcome_joined(user.full_name, user.username))


@router.message(F.left_chat_member)
async def on_left_member(message: Message, bot: Bot) -> None:
    """Выход: active → left (пауза). Если забанили вручную — active → kicked."""
    user = message.left_chat_member
    if user is None or user.is_bot:
        return

    async with SessionFactory() as session:
        stream = await repo.get_stream_by_chat_id(session, message.chat.id)
        if stream is None:
            return

        participant = await repo.get_participant_by_tg_id(session, stream.id, user.id)
        if participant is None or participant.status != ParticipantStatus.ACTIVE:
            return

        if user.id == message.from_user.id:
            await repo.mark_left(session, participant, _event_utc(message))
            await session.commit()
            logger.info("participant %s left chat %s (pause)", user.id, message.chat.id)
            return

        # Ушёл не сам — кто-то удалил. Проверяем, бан это или кик.
        try:
            member = await bot.get_chat_member(message.chat.id, user.id)
        except Exception:
            member = None

        if member is not None and member.status == "kicked":
            await repo.mark_kicked(session, participant, _event_utc(message))
            await session.commit()
            logger.info("participant %s was banned manually in chat %s", user.id, message.chat.id)
        else:
            # Обычный кик админом — для челленджа считаем паузой
            await repo.mark_left(session, participant, _event_utc(message))
            await session.commit()
            logger.info("participant %s was removed from chat %s", user.id, message.chat.id)


# --- Отчёты #пост -----------------------------------------------------------


def _is_forward(message: Message) -> bool:
    """Форвард (в т.ч. из канала) — засчитываем без проверки длины."""
    if message.forward_origin is not None:  # aiogram 3.7+
        return True
    # старые поля на случай совместимости
    return bool(
        getattr(message, "forward_from_chat", None) or getattr(message, "forward_from", None)
    )


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def on_group_message(message: Message) -> None:
    """Сообщения в группе: ловим #пост и #штраф. Остальное игнорируем."""
    content = message.text or message.caption
    if message.from_user is None or message.from_user.is_bot:
        return

    if reports.has_post_hashtag(content):
        await _handle_post(message, content)
    elif reports.has_penalty_hashtag(content):
        await _handle_penalty(message)


async def _handle_post(message: Message, content: str) -> None:
    if message.from_user is None:
        return

    async with SessionFactory() as session:
        stream = await repo.get_stream_by_chat_id(session, message.chat.id)
        if stream is None:
            return

        outcome = await challenge.accept_report(
            session,
            stream=stream,
            tg_user_id=message.from_user.id,
            message_id=message.message_id,
            content=content,
            is_forward=_is_forward(message),
            min_chars=settings.min_post_chars,
            now_utc=_event_utc(message),
            report_deadline=settings.report_deadline,
        )

    match outcome.kind:
        case "accepted":
            await message.reply(
                texts.report_accepted(outcome.seq, outcome.total, outcome.emoji)
            )
        case "already_counted":
            await message.reply(texts.report_already_counted())
        case "not_participant":
            await message.reply(texts.report_not_participant())
        case "not_active":
            await message.reply(texts.report_not_active())
        case "invalid":
            await message.reply(texts.report_invalid(settings.min_post_chars))
        case "late":
            await message.reply(texts.report_late(settings.report_deadline))


async def _handle_penalty(message: Message) -> None:
    """#штраф: закрываем все открытые штрафы автора. Факт доната не проверяем."""
    if message.from_user is None:
        return

    async with SessionFactory() as session:
        stream = await repo.get_stream_by_chat_id(session, message.chat.id)
        if stream is None:
            return

        outcome = await digest.pay_open_penalties(
            session,
            stream=stream,
            tg_user_id=message.from_user.id,
            message_id=message.message_id,
        )

    match outcome.kind:
        case "paid":
            await message.reply(texts.penalty_paid(outcome.count))
        case "none":
            await message.reply(texts.penalty_none())
        case "not_participant":
            await message.reply(texts.report_not_participant())
        case "not_active":
            await message.reply(texts.report_not_active())
