"""Repository-слой: все запросы к БД. Функции делают flush, но НЕ commit — коммитит вызывающий."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import (
    MembershipPeriod,
    Participant,
    ParticipantStatus,
    Penalty,
    PenaltyStatus,
    Stream,
    utcnow,
)

# --- Потоки ---------------------------------------------------------------


async def create_stream(
    session: AsyncSession, *, title: str, chat_id: int, invite_link: str, timezone: str
) -> Stream:
    stream = Stream(title=title, chat_id=chat_id, invite_link=invite_link, timezone=timezone)
    session.add(stream)
    await session.flush()
    return stream


async def get_stream_by_chat_id(session: AsyncSession, chat_id: int) -> Stream | None:
    q = select(Stream).where(Stream.chat_id == chat_id, Stream.is_active.is_(True))
    return (await session.execute(q)).scalar_one_or_none()


async def get_active_stream(session: AsyncSession) -> Stream | None:
    """v1: единственный активный поток."""
    q = select(Stream).where(Stream.is_active.is_(True)).order_by(Stream.id)
    return (await session.execute(q)).scalars().first()


async def get_active_streams(session: AsyncSession) -> list[Stream]:
    q = select(Stream).where(Stream.is_active.is_(True)).order_by(Stream.id)
    return list((await session.execute(q)).scalars().all())


async def get_stream(session: AsyncSession, stream_id: int) -> Stream | None:
    return await session.get(Stream, stream_id)


# --- Участники ------------------------------------------------------------


async def get_participant_by_username(
    session: AsyncSession, stream_id: int, username: str
) -> Participant | None:
    q = select(Participant).where(
        Participant.stream_id == stream_id,
        Participant.username.ilike(username.lstrip("@")),
    )
    return (await session.execute(q)).scalar_one_or_none()


async def get_participant_by_tg_id(
    session: AsyncSession, stream_id: int, tg_user_id: int
) -> Participant | None:
    q = select(Participant).where(
        Participant.stream_id == stream_id,
        Participant.tg_user_id == tg_user_id,
    )
    return (await session.execute(q)).scalar_one_or_none()


async def approve_participant(session: AsyncSession, stream_id: int, username: str) -> Participant:
    p = Participant(stream_id=stream_id, username=username.lstrip("@"), status=ParticipantStatus.APPROVED)
    session.add(p)
    await session.flush()
    return p


async def revoke_participant(session: AsyncSession, participant: Participant) -> None:
    """Удалить из whitelist. Вызывающий проверяет, что участник ещё не вступил."""
    await session.delete(participant)
    await session.flush()


async def record_join(session: AsyncSession, participant: Participant, when: datetime) -> None:
    session.add(MembershipPeriod(
        stream_id=participant.stream_id, participant_id=participant.id, joined_at=when,
    ))
    await session.flush()


async def close_membership(session: AsyncSession, participant: Participant, when: datetime) -> None:
    q = select(MembershipPeriod).where(
        MembershipPeriod.participant_id == participant.id,
        MembershipPeriod.left_at.is_(None),
    ).order_by(MembershipPeriod.id.desc())
    period = (await session.execute(q)).scalars().first()
    if period is not None:
        period.left_at = max(when, period.joined_at)
    await session.flush()


async def mark_left(session: AsyncSession, participant: Participant, when: datetime | None = None) -> None:
    participant.status = ParticipantStatus.LEFT
    participant.left_at = when or utcnow()
    await close_membership(session, participant, participant.left_at)
    await session.flush()


async def mark_kicked(session: AsyncSession, participant: Participant, when: datetime | None = None) -> None:
    participant.status = ParticipantStatus.KICKED
    participant.kick_count += 1
    await close_membership(session, participant, when or utcnow())
    await session.flush()


# --- Штрафы ---------------------------------------------------------------


async def extend_open_penalties(
    session: AsyncSession, participant_id: int, grace_hours: int
) -> int:
    """Возврат из паузы: продлить дедлайн открытых (и просроченных в отсутствие) штрафов."""
    q = select(Penalty).where(
        Penalty.participant_id == participant_id,
        Penalty.status.in_([PenaltyStatus.OPEN, PenaltyStatus.EXPIRED]),
    )
    penalties = (await session.execute(q)).scalars().all()
    new_due = utcnow() + timedelta(hours=grace_hours)
    for p in penalties:
        p.status = PenaltyStatus.OPEN
        p.due_at = new_due
    await session.flush()
    return len(penalties)


async def cancel_open_penalties(session: AsyncSession, participant_id: int) -> int:
    """Обнулить штрафы (возврат кикнутого через /approve)."""
    q = select(Penalty).where(
        Penalty.participant_id == participant_id,
        Penalty.status.in_([PenaltyStatus.OPEN, PenaltyStatus.EXPIRED]),
    )
    penalties = (await session.execute(q)).scalars().all()
    for p in penalties:
        p.status = PenaltyStatus.CANCELLED
    await session.flush()
    return len(penalties)
