"""Дайджест, напоминания, штрафы, кик — чистая логика над БД (без Telegram API)."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo
from bot.db.models import (
    DailyClosure,
    DailyReport,
    MembershipPeriod,
    Participant,
    ParticipantStatus,
    Penalty,
    PenaltyStatus,
    Stream,
    utcnow,
)


def today_local(stream: Stream, now_utc: datetime) -> date:
    return now_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(stream.timezone)).date()


def _exclude_users(query, exclude_tg_user_ids: frozenset[int]):
    if exclude_tg_user_ids:
        query = query.where(
            Participant.tg_user_id.is_(None)
            | Participant.tg_user_id.not_in(exclude_tg_user_ids)
        )
    return query


async def list_active(
    session: AsyncSession, stream_id: int, *, exclude_tg_user_ids: frozenset[int] = frozenset(),
) -> list[Participant]:
    q = select(Participant).where(
        Participant.stream_id == stream_id,
        Participant.status == ParticipantStatus.ACTIVE,
    )
    return list((await session.execute(_exclude_users(q, exclude_tg_user_ids))).scalars().all())


async def active_at_deadline(
    session: AsyncSession, stream: Stream, day: date, report_deadline: str,
    *, exclude_tg_user_ids: frozenset[int] = frozenset(),
) -> list[Participant]:
    hour, minute = map(int, report_deadline.split(":"))
    local_cutoff = datetime.combine(
        day, time(hour, minute, 59, 999999), tzinfo=ZoneInfo(stream.timezone)
    )
    cutoff = local_cutoff.astimezone(timezone.utc).replace(tzinfo=None)
    q = (
        select(Participant)
        .join(MembershipPeriod, MembershipPeriod.participant_id == Participant.id)
        .where(
            MembershipPeriod.stream_id == stream.id,
            MembershipPeriod.joined_at <= cutoff,
            (MembershipPeriod.left_at.is_(None) | (MembershipPeriod.left_at > cutoff)),
        )
        .distinct()
    )
    return list((await session.execute(_exclude_users(q, exclude_tg_user_ids))).scalars().all())


async def _reporter_ids(session: AsyncSession, stream_id: int, day: date) -> set[int]:
    q = select(DailyReport.participant_id).where(
        DailyReport.stream_id == stream_id, DailyReport.report_date == day
    )
    return set((await session.execute(q)).scalars().all())


async def missing_reporters(
    session: AsyncSession, stream: Stream, now_utc: datetime,
    *, exclude_tg_user_ids: frozenset[int] = frozenset(),
) -> list[Participant]:
    """Кто из активных ещё не сдал #пост сегодня (для напоминания 18:00)."""
    active = await list_active(session, stream.id, exclude_tg_user_ids=exclude_tg_user_ids)
    reported = await _reporter_ids(session, stream.id, today_local(stream, now_utc))
    return [p for p in active if p.id not in reported]


@dataclass
class CloseDayOutcome:
    for_date: date
    total: int
    reported: list[Participant]
    penalized: list[Participant]


async def close_day(
    session: AsyncSession, stream: Stream, for_date: date, grace_hours: int,
    report_deadline: str = "23:59",
    *, exclude_tg_user_ids: frozenset[int] = frozenset(),
) -> CloseDayOutcome:
    """00:00: итоги дня + штрафы несдавшим. Идемпотентно (повторный прогон не дублирует)."""
    active = await active_at_deadline(
        session, stream, for_date, report_deadline, exclude_tg_user_ids=exclude_tg_user_ids,
    )
    reported_ids = await _reporter_ids(session, stream.id, for_date)
    reported = [p for p in active if p.id in reported_ids]
    missed = [p for p in active if p.id not in reported_ids]

    if await session.get(DailyClosure, (stream.id, for_date)) is not None:
        return CloseDayOutcome(for_date, len(active), reported, [])

    penalized: list[Participant] = []
    for p in missed:
        q = select(Penalty).where(
            Penalty.stream_id == stream.id,
            Penalty.participant_id == p.id,
            (Penalty.status == PenaltyStatus.OPEN)
            | ((Penalty.issued_for_date == for_date) & (Penalty.reason == "missed_report")),
        )
        if (await session.execute(q.limit(1))).scalar_one_or_none() is not None:
            continue  # существующий долг не суммируется и его срок не продлевается
        session.add(
            Penalty(
                stream_id=stream.id,
                participant_id=p.id,
                issued_for_date=for_date,
                status=PenaltyStatus.OPEN,
                due_at=utcnow() + timedelta(hours=grace_hours),
            )
        )
        penalized.append(p)

    session.add(DailyClosure(stream_id=stream.id, report_date=for_date))
    await session.commit()
    return CloseDayOutcome(for_date=for_date, total=len(active), reported=reported, penalized=penalized)


async def day_outcome(
    session: AsyncSession, stream: Stream, for_date: date, report_deadline: str,
    *, exclude_tg_user_ids: frozenset[int] = frozenset(),
) -> CloseDayOutcome:
    active = await active_at_deadline(
        session, stream, for_date, report_deadline, exclude_tg_user_ids=exclude_tg_user_ids,
    )
    reported_ids = await _reporter_ids(session, stream.id, for_date)
    penalties = (await session.execute(select(Penalty.participant_id).where(
        Penalty.stream_id == stream.id,
        Penalty.issued_for_date == for_date,
        Penalty.reason == "missed_report",
    ))).scalars().all()
    penalty_ids = set(penalties)
    return CloseDayOutcome(
        for_date, len(active),
        [p for p in active if p.id in reported_ids],
        [p for p in active if p.id in penalty_ids],
    )


async def open_penalty_holders(
    session: AsyncSession, stream_id: int, *, exclude_tg_user_ids: frozenset[int] = frozenset(),
) -> list[Participant]:
    """Активные участники с неотработанными штрафами (напоминание 20:00)."""
    q = (
        select(Participant)
        .join(Penalty, Penalty.participant_id == Participant.id)
        .where(
            Participant.stream_id == stream_id,
            Participant.status == ParticipantStatus.ACTIVE,
            Penalty.status == PenaltyStatus.OPEN,
        )
        .distinct()
    )
    return list((await session.execute(_exclude_users(q, exclude_tg_user_ids))).scalars().all())


async def expired_penalty_debtors(
    session: AsyncSession, stream_id: int, now_utc: datetime,
    *, exclude_tg_user_ids: frozenset[int] = frozenset(),
) -> list[Participant]:
    """Активные участники с просроченными штрафами (кик 00:05)."""
    q = (
        select(Participant)
        .join(Penalty, Penalty.participant_id == Participant.id)
        .where(
            Participant.stream_id == stream_id,
            Participant.status == ParticipantStatus.ACTIVE,
            Penalty.status == PenaltyStatus.OPEN,
            Penalty.due_at < now_utc,
        )
        .distinct()
    )
    return list((await session.execute(_exclude_users(q, exclude_tg_user_ids))).scalars().all())


async def expire_penalties_and_mark_kicked(session: AsyncSession, participant: Participant) -> None:
    q = select(Penalty).where(
        Penalty.participant_id == participant.id, Penalty.status == PenaltyStatus.OPEN
    )
    for pen in (await session.execute(q)).scalars().all():
        pen.status = PenaltyStatus.EXPIRED
    await repo.mark_kicked(session, participant)
    await session.commit()


@dataclass
class PayOutcome:
    kind: str  # paid | none | not_participant | not_active
    count: int = 0


async def pay_open_penalties(
    session: AsyncSession, stream: Stream, tg_user_id: int, message_id: int
) -> PayOutcome:
    """#штраф: закрыть все открытые штрафы участника. Факт доната не проверяем."""
    participant = await repo.get_participant_by_tg_id(session, stream.id, tg_user_id)
    if participant is None:
        return PayOutcome(kind="not_participant")
    if participant.status != ParticipantStatus.ACTIVE:
        return PayOutcome(kind="not_active")

    q = select(Penalty).where(
        Penalty.participant_id == participant.id, Penalty.status == PenaltyStatus.OPEN
    )
    penalties = (await session.execute(q)).scalars().all()
    if not penalties:
        return PayOutcome(kind="none")

    now = utcnow()
    for pen in penalties:
        pen.status = PenaltyStatus.PAID
        pen.paid_at = now
        pen.paid_message_id = message_id
    await session.commit()
    return PayOutcome(kind="paid", count=len(penalties))


@dataclass
class Stats:
    total_active: int
    reported_today: int
    open_penalties: int
    penalized_names: list[str]
    total_kicked: int


async def collect_stats(
    session: AsyncSession, stream: Stream, now_utc: datetime,
    *, exclude_tg_user_ids: frozenset[int] = frozenset(),
) -> Stats:
    from bot.services import texts  # локальный импорт: избегаем цикла

    active = await list_active(session, stream.id, exclude_tg_user_ids=exclude_tg_user_ids)
    reported = await _reporter_ids(session, stream.id, today_local(stream, now_utc))
    holders = await open_penalty_holders(session, stream.id, exclude_tg_user_ids=exclude_tg_user_ids)

    q = select(Penalty).join(Participant, Participant.id == Penalty.participant_id).where(
        Penalty.stream_id == stream.id, Penalty.status == PenaltyStatus.OPEN,
    )
    open_count = len((await session.execute(_exclude_users(q, exclude_tg_user_ids))).scalars().all())

    q2 = select(Participant).where(
        Participant.stream_id == stream.id, Participant.status == ParticipantStatus.KICKED
    )
    kicked = len((await session.execute(_exclude_users(q2, exclude_tg_user_ids))).scalars().all())

    return Stats(
        total_active=len(active),
        reported_today=len([p for p in active if p.id in reported]),
        open_penalties=open_count,
        penalized_names=[texts.display_name(p) for p in holders],
        total_kicked=kicked,
    )
