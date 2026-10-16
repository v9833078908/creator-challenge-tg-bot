"""Игровая логика челленджа: приём отчётов #пост. Чистые функции над БД — без Telegram API."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo
from bot.db.models import (
    DailyCounter,
    DailyReport,
    Participant,
    ParticipantStatus,
    Stream,
)
from bot.services import reports


@dataclass
class ReportOutcome:
    kind: str  # accepted | already_counted | not_participant | not_active | invalid
    seq: int = 0
    total: int = 0  # активных участников (N в «№k из N»)
    emoji: str = ""


def local_date(stream: Stream, now_utc: datetime) -> date:
    """Локальная дата потока для наивного UTC-datetime."""
    return now_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(stream.timezone)).date()


async def _active_count(
    session: AsyncSession, stream_id: int, *, exclude_tg_user_ids: frozenset[int] = frozenset(),
) -> int:
    q = select(Participant).where(
        Participant.stream_id == stream_id,
        Participant.status == ParticipantStatus.ACTIVE,
    )
    if exclude_tg_user_ids:
        q = q.where(
            Participant.tg_user_id.is_(None)
            | Participant.tg_user_id.not_in(exclude_tg_user_ids)
        )
    return len((await session.execute(q)).scalars().all())


async def accept_report(
    session: AsyncSession,
    *,
    stream: Stream,
    tg_user_id: int,
    message_id: int,
    content: str | None,
    is_forward: bool,
    min_chars: int,
    now_utc: datetime,
    report_deadline: str = "23:59",
    exclude_tg_user_ids: frozenset[int] = frozenset(),
) -> ReportOutcome:
    """Приём #пост: валидация → идемпотентная запись → №k из N + эмодзи дня."""
    participant = await repo.get_participant_by_tg_id(session, stream.id, tg_user_id)
    total = await _active_count(session, stream.id, exclude_tg_user_ids=exclude_tg_user_ids)

    if participant is None:
        return ReportOutcome(kind="not_participant", total=total)
    if participant.status != ParticipantStatus.ACTIVE:
        return ReportOutcome(kind="not_active", total=total)

    candidate = reports.validate_report(content, is_forward=is_forward, min_chars=min_chars)
    if candidate is None:
        return ReportOutcome(kind="invalid", total=total)

    local_now = now_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(stream.timezone))
    deadline_hour, deadline_minute = map(int, report_deadline.split(":"))
    if (local_now.hour, local_now.minute) > (deadline_hour, deadline_minute):
        return ReportOutcome(kind="late", total=total)
    report_date = local_now.date()

    # Уже сдавал сегодня? — идемпотентность
    q = select(DailyReport).where(
        DailyReport.stream_id == stream.id,
        DailyReport.participant_id == participant.id,
        DailyReport.report_date == report_date,
    )
    existing = (await session.execute(q)).scalar_one_or_none()
    if existing is not None:
        return ReportOutcome(
            kind="already_counted", seq=existing.seq_number, total=total,
            emoji=reports.day_emoji(report_date),
        )

    # Счётчик дня (№k) + эмодзи дня
    counter = await session.get(DailyCounter, (stream.id, report_date))
    if counter is None:
        counter = DailyCounter(
            stream_id=stream.id, report_date=report_date,
            emoji=reports.day_emoji(report_date), count=0,
        )
        session.add(counter)
        await session.flush()
    counter.count += 1

    session.add(
        DailyReport(
            stream_id=stream.id,
            participant_id=participant.id,
            report_date=report_date,
            message_id=message_id,
            kind=candidate.kind,
            char_count=candidate.char_count,
            seq_number=counter.count,
        )
    )
    await session.commit()

    return ReportOutcome(
        kind="accepted", seq=counter.count, total=total, emoji=counter.emoji
    )
