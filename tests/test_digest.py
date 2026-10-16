"""Закрытие дня, штрафы, отработка #штраф, кик, статистика."""

from datetime import date, datetime, timedelta

from sqlalchemy import select

from bot.db import repo
from bot.db.models import DailyClosure, ParticipantStatus, Penalty, PenaltyStatus, utcnow
from bot.services import challenge, digest, onboarding

GRACE = 24


async def _mk_active(session, stream, tg_id: int, username: str):
    p = await repo.approve_participant(session, stream.id, username)
    p.tg_user_id = tg_id
    await session.commit()
    await onboarding.process_join(
        session, stream=stream, tg_user_id=tg_id, username=username,
        full_name=username, grace_hours=GRACE,
        occurred_at=utcnow() - timedelta(days=2),
    )
    return await repo.get_participant_by_tg_id(session, stream.id, tg_id)


def _yesterday(stream):
    return digest.today_local(stream, utcnow()) - timedelta(days=1)


def test_today_local_treats_naive_datetime_as_utc(stream, monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Moscow")
    import time

    time.tzset()
    try:
        assert digest.today_local(stream, datetime(2026, 9, 22, 21, 30)).isoformat() == "2026-09-23"
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


async def _report(session, stream, tg_id, msg_id, when=None):
    return await challenge.accept_report(
        session, stream=stream, tg_user_id=tg_id, message_id=msg_id,
        content="#пост t.me/x/1", is_forward=False, min_chars=400,
        now_utc=when or utcnow(),
    )


# --- close_day -------------------------------------------------------------


async def test_close_day_penalizes_only_missed(session, stream):
    await _mk_active(session, stream, 1, "good")
    await _mk_active(session, stream, 2, "lazy")
    yesterday = _yesterday(stream)
    await _report(session, stream, 1, 10, when=utcnow() - timedelta(days=1))

    out = await digest.close_day(session, stream, yesterday, GRACE)
    assert out.total == 2
    assert [p.username for p in out.reported] == ["good"]
    assert [p.username for p in out.penalized] == ["lazy"]

    q = select(Penalty).where(Penalty.status == PenaltyStatus.OPEN)
    pens = (await session.execute(q)).scalars().all()
    assert len(pens) == 1
    assert pens[0].due_at > utcnow() + timedelta(hours=GRACE - 1)
    assert pens[0].issued_for_date == yesterday


async def test_close_day_idempotent(session, stream):
    await _mk_active(session, stream, 1, "lazy")
    yesterday = _yesterday(stream)
    out1 = await digest.close_day(session, stream, yesterday, GRACE)
    out2 = await digest.close_day(session, stream, yesterday, GRACE)
    assert len(out1.penalized) == 1
    assert len(out2.penalized) == 0  # повторный прогон — без дублей
    assert await session.get(DailyClosure, (stream.id, yesterday)) is not None


async def test_catchup_uses_membership_at_deadline(session, stream):
    day = date(2026, 9, 20)
    p = await repo.approve_participant(session, stream.id, "friend")
    p.tg_user_id = 55
    await session.commit()
    await onboarding.process_join(
        session, stream=stream, tg_user_id=55, username="friend", full_name="Друг",
        grace_hours=GRACE, occurred_at=datetime(2026, 9, 20, 10),
    )
    await onboarding.process_leave(
        session, stream=stream, tg_user_id=55, occurred_at=datetime(2026, 9, 21, 10),
    )
    out = await digest.close_day(session, stream, day, GRACE)
    assert [person.id for person in out.penalized] == [p.id]


async def test_catchup_does_not_penalize_later_joiner(session, stream):
    day = date(2026, 9, 20)
    p = await repo.approve_participant(session, stream.id, "newfriend")
    p.tg_user_id = 56
    await session.commit()
    await onboarding.process_join(
        session, stream=stream, tg_user_id=56, username="newfriend", full_name="Новичок",
        grace_hours=GRACE, occurred_at=datetime(2026, 9, 20, 21, 1),
    )
    out = await digest.close_day(session, stream, day, GRACE)
    assert out.total == 0
    assert out.penalized == []


async def test_close_day_skips_left(session, stream):
    """Вышедший на паузу не получает штраф за дни вне чата."""
    await _mk_active(session, stream, 1, "leaver")
    await onboarding.process_leave(
        session, stream=stream, tg_user_id=1, occurred_at=utcnow() - timedelta(days=1)
    )
    out = await digest.close_day(session, stream, _yesterday(stream), GRACE)
    assert out.total == 0 and not out.penalized


# --- #штраф -----------------------------------------------------------------


async def test_pay_penalty_closes_one_open_preserving_paid_history(session, stream):
    p = await _mk_active(session, stream, 1, "debtor")
    for i in range(2):
        session.add(Penalty(
            stream_id=stream.id, participant_id=p.id,
            issued_for_date=_yesterday(stream) - timedelta(days=i),
            status=PenaltyStatus.OPEN if i == 0 else PenaltyStatus.PAID,
            due_at=utcnow() + timedelta(hours=10),
        ))
    await session.commit()

    out = await digest.pay_open_penalties(session, stream, tg_user_id=1, message_id=777)
    assert out.kind == "paid" and out.count == 1

    q = select(Penalty).where(Penalty.participant_id == p.id)
    for pen in (await session.execute(q)).scalars().all():
        assert pen.status == PenaltyStatus.PAID
        assert pen.paid_message_id == (777 if pen.issued_for_date == _yesterday(stream) else None)


async def test_pay_penalty_none(session, stream):
    await _mk_active(session, stream, 1, "clean")
    out = await digest.pay_open_penalties(session, stream, tg_user_id=1, message_id=1)
    assert out.kind == "none"


async def test_pay_penalty_not_participant(session, stream):
    out = await digest.pay_open_penalties(session, stream, tg_user_id=999, message_id=1)
    assert out.kind == "not_participant"


# --- Кик --------------------------------------------------------------------


async def test_expired_debtors_and_kick(session, stream):
    p = await _mk_active(session, stream, 1, "expired")
    session.add(Penalty(
        stream_id=stream.id, participant_id=p.id,
        issued_for_date=_yesterday(stream), status=PenaltyStatus.OPEN,
        due_at=utcnow() - timedelta(minutes=5),  # просрочен
    ))
    await session.commit()

    debtors = await digest.expired_penalty_debtors(session, stream.id, utcnow())
    assert [d.id for d in debtors] == [p.id]

    await digest.expire_penalties_and_mark_kicked(session, p)
    assert p.status == ParticipantStatus.KICKED
    q = select(Penalty).where(Penalty.participant_id == p.id)
    assert (await session.execute(q)).scalar_one().status == PenaltyStatus.EXPIRED
    # после кика — больше не должник
    assert await digest.expired_penalty_debtors(session, stream.id, utcnow()) == []


async def test_not_expired_yet_no_kick(session, stream):
    p = await _mk_active(session, stream, 1, "ontime")
    session.add(Penalty(
        stream_id=stream.id, participant_id=p.id,
        issued_for_date=_yesterday(stream), status=PenaltyStatus.OPEN,
        due_at=utcnow() + timedelta(hours=5),
    ))
    await session.commit()
    assert await digest.expired_penalty_debtors(session, stream.id, utcnow()) == []


async def test_left_debtor_not_kicked(session, stream):
    """Вышел на паузу с открытым штрафом — не кикаем, ждём возврата."""
    p = await _mk_active(session, stream, 1, "leftdebtor")
    session.add(Penalty(
        stream_id=stream.id, participant_id=p.id,
        issued_for_date=_yesterday(stream), status=PenaltyStatus.OPEN,
        due_at=utcnow() - timedelta(hours=1),
    ))
    await session.commit()
    await onboarding.process_leave(session, stream=stream, tg_user_id=1)

    assert await digest.expired_penalty_debtors(session, stream.id, utcnow()) == []


# --- Статистика --------------------------------------------------------------


async def test_collect_stats(session, stream):
    await _mk_active(session, stream, 1, "reporter")
    await _mk_active(session, stream, 2, "debtor")
    await _report(session, stream, 1, 10)
    p2 = await repo.get_participant_by_tg_id(session, stream.id, 2)
    session.add(Penalty(
        stream_id=stream.id, participant_id=p2.id,
        issued_for_date=_yesterday(stream), status=PenaltyStatus.OPEN,
        due_at=utcnow() + timedelta(hours=3),
    ))
    await session.commit()

    s = await digest.collect_stats(session, stream, utcnow())
    assert s.total_active == 2
    assert s.reported_today == 1
    assert s.open_penalties == 1
    assert s.penalized_names == ["debtor"]
    assert s.total_kicked == 0
