"""A missed day cannot stack debt; days outside membership carry no obligations."""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bot.db import repo
from bot.db.models import Penalty, PenaltyStatus
from bot.services import digest, onboarding


async def _join(session, stream, when):
    participant = await repo.approve_participant(session, stream.id, "friend")
    participant.tg_user_id = 42
    await session.commit()
    await onboarding.process_join(
        session, stream=stream, tg_user_id=42, username="friend", full_name="Friend",
        grace_hours=24, occurred_at=when,
    )
    return participant


async def _penalties(session):
    return list((await session.execute(select(Penalty).order_by(Penalty.id))).scalars())


async def test_recovery_of_many_missed_days_creates_one_debt_without_extending_due(session, stream):
    await _join(session, stream, datetime(2026, 9, 1, 10))
    first = await digest.close_day(session, stream, date(2026, 9, 1), 24)
    original_due = (await _penalties(session))[0].due_at
    assert len(first.penalized) == 1

    for offset in range(1, 21):
        outcome = await digest.close_day(session, stream, date(2026, 9, 1) + timedelta(days=offset), 24)
        assert outcome.penalized == []

    penalties = await _penalties(session)
    assert len(penalties) == 1
    assert penalties[0].due_at == original_due


async def test_after_payment_a_later_missed_day_can_create_one_new_debt(session, stream):
    await _join(session, stream, datetime(2026, 9, 1, 10))
    await digest.close_day(session, stream, date(2026, 9, 1), 24)
    paid = await digest.pay_open_penalties(session, stream, 42, 100)
    assert paid.kind == "paid" and paid.count == 1
    await digest.close_day(session, stream, date(2026, 9, 2), 24)
    await digest.close_day(session, stream, date(2026, 9, 3), 24)
    assert [p.status for p in await _penalties(session)] == [PenaltyStatus.PAID, PenaltyStatus.OPEN]


async def test_multiday_pause_and_return_after_deadline_have_no_debt_for_absent_days(session, stream):
    await _join(session, stream, datetime(2026, 9, 1, 10))
    # Moscow is UTC+3; leave during the last minute, before the 23:59:59 cutoff.
    await onboarding.process_leave(
        session, stream=stream, tg_user_id=42, occurred_at=datetime(2026, 9, 1, 20, 59, 30),
    )
    await onboarding.process_join(
        session, stream=stream, tg_user_id=42, username="friend", full_name="Friend",
        grace_hours=24, occurred_at=datetime(2026, 9, 5, 21),
    )
    for day in range(1, 6):
        outcome = await digest.close_day(session, stream, date(2026, 9, day), 24)
        assert outcome.total == 0
        assert outcome.penalized == []
    assert await _penalties(session) == []

    # Their first deadline after returning is September 6, not September 5.
    outcome = await digest.close_day(session, stream, date(2026, 9, 6), 24)
    assert len(outcome.penalized) == 1


async def test_database_rejects_a_second_open_penalty_even_for_a_different_day(session, stream):
    participant = await _join(session, stream, datetime(2026, 9, 1, 10))
    await digest.close_day(session, stream, date(2026, 9, 1), 24)
    session.add(Penalty(
        stream_id=stream.id, participant_id=participant.id,
        issued_for_date=date(2026, 9, 2), status=PenaltyStatus.OPEN,
    ))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.parametrize("has_open_debt", [False, True])
async def test_return_does_not_reopen_expired_history(session, stream, monkeypatch, has_open_debt):
    participant = await _join(session, stream, datetime(2026, 9, 1, 10))
    old_due = datetime(2026, 9, 2, 10)
    for day in (1, 2):
        session.add(Penalty(
            stream_id=stream.id, participant_id=participant.id,
            issued_for_date=date(2026, 9, day), status=PenaltyStatus.EXPIRED, due_at=old_due,
        ))
    if has_open_debt:
        session.add(Penalty(
            stream_id=stream.id, participant_id=participant.id,
            issued_for_date=date(2026, 9, 3), status=PenaltyStatus.OPEN, due_at=old_due,
        ))
    await session.commit()
    await onboarding.process_leave(
        session, stream=stream, tg_user_id=42, occurred_at=datetime(2026, 9, 4, 10),
    )
    returned_at = datetime(2026, 9, 10, 10)
    monkeypatch.setattr(repo, "utcnow", lambda: returned_at)
    outcome = await onboarding.process_join(
        session, stream=stream, tg_user_id=42, username="friend", full_name="Friend",
        grace_hours=24, occurred_at=returned_at,
    )

    assert outcome.activated
    assert outcome.restored_penalties == int(has_open_debt)
    penalties = await _penalties(session)
    assert [(p.status, p.due_at) for p in penalties[:2]] == [
        (PenaltyStatus.EXPIRED, old_due), (PenaltyStatus.EXPIRED, old_due),
    ]
    if has_open_debt:
        assert penalties[2].status == PenaltyStatus.OPEN
        assert penalties[2].due_at == returned_at + timedelta(hours=24)
