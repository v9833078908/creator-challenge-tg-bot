"""Игровая логика онбординга и переходов статусов. Чистые функции над БД — без Telegram API."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo
from bot.db.models import Participant, ParticipantStatus, Stream, utcnow


@dataclass
class StartOutcome:
    kind: str  # invited | already_active | refused | kicked | no_stream
    stream: Stream | None = None
    participant: Participant | None = None


@dataclass
class JoinOutcome:
    activated: bool
    reason: str = ""  # unknown | already_active | ""
    prev_status: ParticipantStatus | None = None
    restored_penalties: int = 0
    participant: Participant | None = None


async def process_start(
    session: AsyncSession, *, tg_user_id: int, username: str | None, full_name: str
) -> StartOutcome:
    """`/start` в личке: whitelist-проверка → инвайт-ссылка."""
    stream = await repo.get_active_stream(session)
    if stream is None:
        return StartOutcome(kind="no_stream")

    participant = await repo.get_participant_by_tg_id(session, stream.id, tg_user_id)
    if participant is None and username:
        participant = await repo.get_participant_by_username(session, stream.id, username)
        if participant is not None and participant.tg_user_id is not None:
            participant = None

    if participant is None:
        return StartOutcome(kind="refused", stream=stream)
    if participant.status == ParticipantStatus.KICKED:
        return StartOutcome(kind="kicked", stream=stream, participant=participant)

    # Привязываем tg id/имя при первом контакте
    if participant.tg_user_id is None:
        participant.tg_user_id = tg_user_id
    if full_name:
        participant.full_name = full_name
    if username:
        participant.username = username
    await session.commit()

    if participant.status == ParticipantStatus.ACTIVE:
        return StartOutcome(kind="already_active", stream=stream, participant=participant)
    return StartOutcome(kind="invited", stream=stream, participant=participant)


async def process_join(
    session: AsyncSession,
    *,
    stream: Stream,
    tg_user_id: int,
    username: str | None,
    full_name: str,
    grace_hours: int,
    occurred_at: datetime | None = None,
) -> JoinOutcome:
    """Вход в чат: approved/left/kicked → active.

    - left: открытые штрафы восстанавливаются с новым дедлайном (+grace_hours)
    - kicked (ручной unban владельцем в обход /approve): штрафы обнуляются
    - неизвестный вошедший (ссылка ушла дальше): не участник, обязательств нет
    """
    participant = await repo.get_participant_by_tg_id(session, stream.id, tg_user_id)
    if participant is None and username:
        participant = await repo.get_participant_by_username(session, stream.id, username)
        if participant is not None and participant.tg_user_id is not None:
            participant = None
        elif participant is not None:
            participant.tg_user_id = tg_user_id

    if participant is None:
        return JoinOutcome(activated=False, reason="unknown")
    if participant.status == ParticipantStatus.ACTIVE:
        return JoinOutcome(activated=False, reason="already_active", participant=participant)

    prev = participant.status
    participant.status = ParticipantStatus.ACTIVE
    participant.joined_at = occurred_at or utcnow()
    participant.left_at = None
    if full_name:
        participant.full_name = full_name
    if username:
        participant.username = username

    restored = 0
    if prev == ParticipantStatus.LEFT:
        restored = await repo.extend_open_penalties(session, participant.id, grace_hours)
    elif prev == ParticipantStatus.KICKED:
        await repo.cancel_open_penalties(session, participant.id)

    await repo.record_join(session, participant, participant.joined_at)

    await session.commit()
    return JoinOutcome(
        activated=True, prev_status=prev, restored_penalties=restored, participant=participant
    )


async def process_leave(
    session: AsyncSession, *, stream: Stream, tg_user_id: int, occurred_at: datetime | None = None
) -> bool:
    """Выход из чата: active → left (пауза). Штрафы НЕ аннулируются."""
    participant = await repo.get_participant_by_tg_id(session, stream.id, tg_user_id)
    if participant is None or participant.status != ParticipantStatus.ACTIVE:
        return False
    await repo.mark_left(session, participant, occurred_at)
    await session.commit()
    return True


async def process_manual_kick(session: AsyncSession, *, stream: Stream, tg_user_id: int) -> bool:
    """Кого-то забанили вручную в чате (не ботом) — фиксируем статус."""
    participant = await repo.get_participant_by_tg_id(session, stream.id, tg_user_id)
    if participant is None or participant.status != ParticipantStatus.ACTIVE:
        return False
    await repo.mark_kicked(session, participant)
    await session.commit()
    return True
