"""Приём #пост: нумерация, идемпотентность, статусы, граница дня по таймзоне потока."""

from datetime import datetime, timedelta

from bot.db import repo
from bot.db.models import utcnow
from bot.services import challenge, onboarding

GRACE = 24


async def _mk_active(session, stream, tg_id: int, username: str):
    p = await repo.approve_participant(session, stream.id, username)
    p.tg_user_id = tg_id
    await session.commit()
    await onboarding.process_join(
        session, stream=stream, tg_user_id=tg_id, username=username,
        full_name="Участник", grace_hours=GRACE,
    )
    return p


def _report(session, stream, tg_id, msg_id, content="#пост https://t.me/x/1"):
    return challenge.accept_report(
        session, stream=stream, tg_user_id=tg_id, message_id=msg_id,
        content=content, is_forward=False, min_chars=400,
        now_utc=utcnow(),
    )


async def test_accept_first_report_seq1(session, stream):
    await _mk_active(session, stream, 1, "user1")
    out = await _report(session, stream, 1, 101)
    assert out.kind == "accepted"
    assert out.seq == 1 and out.total == 1 and out.emoji


async def test_numbering_increments_per_day(session, stream):
    await _mk_active(session, stream, 1, "user1")
    await _mk_active(session, stream, 2, "user2")
    await _mk_active(session, stream, 3, "user3")
    out1 = await _report(session, stream, 1, 101)
    out2 = await _report(session, stream, 2, 102)
    out3 = await _report(session, stream, 3, 103)
    assert (out1.seq, out2.seq, out3.seq) == (1, 2, 3)
    assert out1.total == 3 and out3.total == 3


async def test_duplicate_report_same_day(session, stream):
    await _mk_active(session, stream, 1, "user1")
    out1 = await _report(session, stream, 1, 101)
    out2 = await _report(session, stream, 1, 102)  # второй #пост в тот же день
    assert out1.kind == "accepted"
    assert out2.kind == "already_counted"
    assert out2.seq == out1.seq


async def test_not_participant_rejected(session, stream):
    out = await _report(session, stream, 999, 101)
    assert out.kind == "not_participant"


async def test_not_active_rejected(session, stream):
    await _mk_active(session, stream, 1, "user1")
    await onboarding.process_leave(session, stream=stream, tg_user_id=1)
    out = await _report(session, stream, 1, 101)
    assert out.kind == "not_active"


async def test_invalid_report_rejected(session, stream):
    await _mk_active(session, stream, 1, "user1")
    out = await _report(session, stream, 1, 101, content="#пост коротко")
    assert out.kind == "invalid"


async def test_total_excludes_left(session, stream):
    """N в «№k из N» — только активные; вышедший на паузу не считается."""
    await _mk_active(session, stream, 1, "user1")
    await _mk_active(session, stream, 2, "user2")
    await onboarding.process_leave(session, stream=stream, tg_user_id=2)
    out = await _report(session, stream, 1, 101)
    assert out.total == 1


async def test_new_day_resets_counter(session, stream):
    await _mk_active(session, stream, 1, "user1")
    await _mk_active(session, stream, 2, "user2")
    # «вчера» сдал только user1
    yesterday = utcnow() - timedelta(days=1)
    out_y = await challenge.accept_report(
        session, stream=stream, tg_user_id=1, message_id=100,
        content="#пост t.me/x/1", is_forward=False, min_chars=400, now_utc=yesterday,
    )
    # сегодня нумерация заново
    out_t1 = await _report(session, stream, 2, 102)
    out_t2 = await _report(session, stream, 1, 101)
    assert out_y.seq == 1
    assert (out_t1.seq, out_t2.seq) == (1, 2)
    # «вчерашний» участник сегодня ещё не сдавал — дубликата нет
    out_again_yesterday = await challenge.accept_report(
        session, stream=stream, tg_user_id=1, message_id=101,
        content="#пост t.me/x/2", is_forward=False, min_chars=400, now_utc=yesterday,
    )
    assert out_again_yesterday.kind == "already_counted"


async def test_forward_accepted_without_length(session, stream):
    await _mk_active(session, stream, 1, "user1")
    out = await challenge.accept_report(
        session, stream=stream, tg_user_id=1, message_id=101,
        content="#пост", is_forward=True, min_chars=400, now_utc=utcnow(),
    )
    assert out.kind == "accepted"


async def test_report_after_configured_deadline_is_rejected(session, stream):
    await _mk_active(session, stream, 1, "user1")
    out = await challenge.accept_report(
        session, stream=stream, tg_user_id=1, message_id=101,
        content="#пост t.me/x/1", is_forward=False, min_chars=400,
        now_utc=datetime(2026, 9, 22, 19, 1), report_deadline="22:00",
    )
    assert out.kind == "late"


def test_local_date_treats_naive_datetime_as_utc(stream, monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Moscow")
    import time

    time.tzset()
    try:
        assert challenge.local_date(stream, datetime(2026, 9, 22, 21, 30)).isoformat() == "2026-09-23"
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
