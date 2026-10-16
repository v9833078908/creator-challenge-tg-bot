"""Recovery restores accounting without publishing an obsolete daily backlog."""

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatMemberStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db import repo
from bot.db.models import DailyClosure, Penalty
from bot.scheduler import jobs
from bot.services import digest, onboarding


def _bot():
    bot = AsyncMock()
    bot.get_chat_administrators.return_value = [SimpleNamespace(
        status=ChatMemberStatus.CREATOR, user=SimpleNamespace(id=999),
    )]
    return bot


async def _join_participant(session, stream):
    person = await repo.approve_participant(session, stream.id, "friend")
    person.tg_user_id = 42
    await session.commit()
    await onboarding.process_join(
        session, stream=stream, tg_user_id=42, username="friend", full_name="Друг",
        grace_hours=24, occurred_at=datetime(2026, 10, 10, 8),
    )
    return person


@pytest.mark.parametrize("already_closed", [False, True])
async def test_restart_recovers_accounting_but_sends_only_yesterdays_digest(
    session, stream, monkeypatch, already_closed,
):
    """Oct 10–14 accounting survives two restarts; the chat sees only Oct 14."""
    stream.created_at = datetime(2026, 10, 9, 8)
    person = await _join_participant(session, stream)
    session.add(DailyClosure(
        stream_id=stream.id, report_date=date(2026, 10, 9),
        digest_sent_at=datetime(2026, 10, 9, 21),
    ))
    await session.commit()
    recovered_dates = [date(2026, 10, day) for day in range(10, 15)]
    if already_closed:
        for day in recovered_dates:
            await digest.close_day(session, stream, day, grace_hours=24)

    monkeypatch.setattr(jobs, "SessionFactory", async_sessionmaker(
        session.bind, expire_on_commit=False,
    ))
    monkeypatch.setattr(jobs, "_now_naive_utc", lambda: datetime(2026, 10, 15, 8))
    bot = _bot()

    await jobs.recover_missed_days(bot)
    await jobs.recover_missed_days(bot)

    # Restoring missed accounting must remain complete and idempotent.
    async with jobs.SessionFactory() as verification:
        closures = (await verification.execute(select(DailyClosure).where(
            DailyClosure.stream_id == stream.id,
            DailyClosure.report_date.in_(recovered_dates),
        ))).scalars().all()
        penalties = (await verification.execute(select(Penalty).where(
            Penalty.participant_id == person.id,
        ))).scalars().all()
        assert sorted(row.report_date for row in closures) == recovered_dates
        # Every day closes, but an unpaid debt cannot accumulate across recovered days.
        assert [row.issued_for_date for row in penalties] == [recovered_dates[0]]
        assert all(row.digest_sent_at is None for row in closures if row.report_date.day < 14)
        yesterday = next(row for row in closures if row.report_date.day == 14)
        assert yesterday.digest_sent_at is not None

    # Publishing past daily digests is a separate, time-sensitive side effect.
    messages = [call.args[1] for call in bot.send_message.await_args_list]
    assert len(messages) == 1, messages
    assert "14/10/26" in messages[0]
    assert all(f"{day}/10/26" not in messages[0] for day in range(10, 14))


@pytest.mark.parametrize(("now_utc", "day", "should_send"), [
    # UTC is still Oct 14; Moscow has already entered Oct 15.
    (datetime(2026, 10, 14, 21, 30), date(2026, 10, 14), True),
    (datetime(2026, 10, 14, 21, 30), date(2026, 10, 13), False),
    (datetime(2026, 10, 14, 21, 30), date(2026, 10, 15), False),
    # Just before Moscow midnight the Oct 14 digest is still premature.
    (datetime(2026, 10, 14, 20, 59), date(2026, 10, 13), True),
    (datetime(2026, 10, 14, 20, 59), date(2026, 10, 14), False),
])
async def test_direct_digest_call_respects_stream_local_yesterday(
    session, stream, monkeypatch, now_utc, day, should_send,
):
    await _join_participant(session, stream)
    session.add(DailyClosure(stream_id=stream.id, report_date=day))
    await session.commit()
    monkeypatch.setattr(jobs, "SessionFactory", async_sessionmaker(
        session.bind, expire_on_commit=False,
    ))
    monkeypatch.setattr(jobs, "_now_naive_utc", lambda: now_utc)
    bot = _bot()

    await jobs.send_digest_if_needed(bot, stream.id, day)
    await jobs.send_digest_if_needed(bot, stream.id, day)

    assert bot.send_message.await_count == int(should_send)
    async with jobs.SessionFactory() as verification:
        closure = await verification.get(DailyClosure, (stream.id, day))
        assert (closure.digest_sent_at is not None) == should_send


async def test_failed_yesterdays_digest_remains_retryable(session, stream, monkeypatch):
    await _join_participant(session, stream)
    day = date(2026, 10, 14)
    session.add(DailyClosure(stream_id=stream.id, report_date=day))
    await session.commit()
    monkeypatch.setattr(jobs, "SessionFactory", async_sessionmaker(
        session.bind, expire_on_commit=False,
    ))
    monkeypatch.setattr(jobs, "_now_naive_utc", lambda: datetime(2026, 10, 15, 8))
    bot = _bot()
    bot.send_message.side_effect = RuntimeError("chat temporarily unavailable")

    await jobs.recover_missed_days(bot)
    async with jobs.SessionFactory() as verification:
        closure = await verification.get(DailyClosure, (stream.id, day))
        assert closure.digest_sent_at is None

    bot.send_message.side_effect = None
    await jobs.recover_missed_days(bot)
    await jobs.recover_missed_days(bot)

    assert bot.send_message.await_count == 2
    async with jobs.SessionFactory() as verification:
        closure = await verification.get(DailyClosure, (stream.id, day))
        assert closure.digest_sent_at is not None
