"""Онбординг и переходы статусов: whitelist → /start → вход → пауза → возврат → кик."""

from datetime import date, timedelta

from sqlalchemy import select

from bot.db import repo
from bot.db.models import ParticipantStatus, Penalty, PenaltyStatus, utcnow
from bot.services import onboarding

GRACE = 24


# --- /start в личке --------------------------------------------------------


async def test_start_refused_for_stranger(session, stream):
    outcome = await onboarding.process_start(
        session, tg_user_id=101, username="stranger", full_name="Stranger"
    )
    assert outcome.kind == "refused"


async def test_start_no_stream(session):
    outcome = await onboarding.process_start(
        session, tg_user_id=101, username="someone", full_name="Someone"
    )
    assert outcome.kind == "no_stream"


async def test_approve_then_start_invited(session, stream):
    await repo.approve_participant(session, stream.id, "friend")
    await session.commit()

    outcome = await onboarding.process_start(
        session, tg_user_id=202, username="Friend", full_name="Друг"
    )
    assert outcome.kind == "invited"
    assert outcome.stream.invite_link == "https://t.me/+TestInvite"
    assert outcome.participant.tg_user_id == 202  # привязали tg id
    assert outcome.participant.full_name == "Друг"


async def test_start_username_case_insensitive(session, stream):
    await repo.approve_participant(session, stream.id, "MixedCase")
    await session.commit()

    outcome = await onboarding.process_start(
        session, tg_user_id=303, username="mixedcase", full_name="Кейс"
    )
    assert outcome.kind == "invited"


async def test_old_username_cannot_take_over_bound_participant(session, stream):
    p = await repo.approve_participant(session, stream.id, "oldname")
    p.tg_user_id = 303
    await session.commit()

    outcome = await onboarding.process_start(
        session, tg_user_id=404, username="oldname", full_name="Другой человек"
    )
    assert outcome.kind == "refused"
    assert p.tg_user_id == 303


async def test_old_username_cannot_activate_bound_participant(session, stream):
    p = await repo.approve_participant(session, stream.id, "oldname")
    p.tg_user_id = 303
    await session.commit()

    outcome = await onboarding.process_join(
        session, stream=stream, tg_user_id=404, username="oldname",
        full_name="Другой человек", grace_hours=GRACE,
    )
    assert outcome.reason == "unknown"
    assert p.tg_user_id == 303
    assert p.status == ParticipantStatus.APPROVED


async def test_start_kicked_contact_owner(session, stream):
    p = await repo.approve_participant(session, stream.id, "kickeduser")
    p.tg_user_id = 404
    await repo.mark_kicked(session, p)
    await session.commit()

    outcome = await onboarding.process_start(
        session, tg_user_id=404, username="kickeduser", full_name="Кикнутый"
    )
    assert outcome.kind == "kicked"


async def test_start_already_active(session, stream):
    p = await repo.approve_participant(session, stream.id, "activeuser")
    p.tg_user_id = 505  # tg id появляется после /start, ещё до входа в чат
    await session.commit()
    await onboarding.process_join(
        session, stream=stream, tg_user_id=505, username="activeuser",
        full_name="Активный", grace_hours=GRACE,
    )

    outcome = await onboarding.process_start(
        session, tg_user_id=505, username="activeuser", full_name="Активный"
    )
    assert outcome.kind == "already_active"



# --- Вход в чат ------------------------------------------------------------


async def test_join_activates_approved(session, stream):
    await repo.approve_participant(session, stream.id, "newbie")
    await session.commit()

    outcome = await onboarding.process_join(
        session, stream=stream, tg_user_id=606, username="newbie",
        full_name="Новичок", grace_hours=GRACE,
    )
    assert outcome.activated is True
    assert outcome.prev_status == ParticipantStatus.APPROVED
    assert outcome.participant.status == ParticipantStatus.ACTIVE
    assert outcome.participant.joined_at is not None


async def test_join_unknown_not_activated(session, stream):
    outcome = await onboarding.process_join(
        session, stream=stream, tg_user_id=777, username="random",
        full_name="Случайный", grace_hours=GRACE,
    )
    assert outcome.activated is False
    assert outcome.reason == "unknown"


async def test_join_by_tg_id_without_username(session, stream):
    """Пользователь сменил юзернейм — находим по tg_user_id."""
    p = await repo.approve_participant(session, stream.id, "oldname")
    p.tg_user_id = 888
    await session.commit()

    outcome = await onboarding.process_join(
        session, stream=stream, tg_user_id=888, username="newname",
        full_name="Сменил", grace_hours=GRACE,
    )
    assert outcome.activated is True
    assert outcome.participant.username == "newname"


# --- Пауза (выход) и возврат -----------------------------------------------


async def _active_participant(session, stream, tg_id: int, username: str):
    p = await repo.approve_participant(session, stream.id, username)
    p.tg_user_id = tg_id
    await session.commit()
    await onboarding.process_join(
        session, stream=stream, tg_user_id=tg_id, username=username,
        full_name="Участник", grace_hours=GRACE,
    )
    return await repo.get_participant_by_tg_id(session, stream.id, tg_id)


async def test_leave_pauses(session, stream):
    p = await _active_participant(session, stream, 909, "leaver")

    assert await onboarding.process_leave(session, stream=stream, tg_user_id=909) is True
    assert p.status == ParticipantStatus.LEFT
    assert p.left_at is not None


async def test_leave_non_active_ignored(session, stream):
    assert await onboarding.process_leave(session, stream=stream, tg_user_id=999) is False


async def test_return_from_leave_extends_open_penalties(session, stream):
    """Открытый штраф НЕ сгорает при выходе: после возврата — новый дедлайн +grace."""
    p = await _active_participant(session, stream, 111, "penalized")
    session.add(
        Penalty(
            stream_id=stream.id,
            participant_id=p.id,
            issued_for_date=date(2026, 9, 19),
            status=PenaltyStatus.OPEN,
            due_at=utcnow() - timedelta(hours=1),  # уже «просрочен» по старому дедлайну
        )
    )
    await session.commit()

    await onboarding.process_leave(session, stream=stream, tg_user_id=111)
    outcome = await onboarding.process_join(
        session, stream=stream, tg_user_id=111, username="penalized",
        full_name="Штрафник", grace_hours=GRACE,
    )

    assert outcome.activated is True
    assert outcome.restored_penalties == 1
    pen = (await session.execute(select(Penalty).where(Penalty.participant_id == p.id))).scalar_one()
    assert pen.status == PenaltyStatus.OPEN
    assert pen.due_at > utcnow() + timedelta(hours=GRACE - 1)


async def test_return_from_leave_without_penalties(session, stream):
    await _active_participant(session, stream, 112, "cleanuser")
    await onboarding.process_leave(session, stream=stream, tg_user_id=112)
    outcome = await onboarding.process_join(
        session, stream=stream, tg_user_id=112, username="cleanuser",
        full_name="Чистый", grace_hours=GRACE,
    )
    assert outcome.activated is True
    assert outcome.restored_penalties == 0


async def test_kicked_return_cancels_penalties(session, stream):
    """Возврат кикнутого (владелец снял бан вручную) — штрафы обнуляются."""
    p = await _active_participant(session, stream, 113, "kickeduser")
    session.add(
        Penalty(
            stream_id=stream.id,
            participant_id=p.id,
            issued_for_date=date(2026, 9, 19),
            status=PenaltyStatus.EXPIRED,
            due_at=utcnow() - timedelta(days=1),
        )
    )
    await repo.mark_kicked(session, p)
    await session.commit()

    outcome = await onboarding.process_join(
        session, stream=stream, tg_user_id=113, username="kickeduser",
        full_name="Вернулся", grace_hours=GRACE,
    )

    assert outcome.activated is True
    pen = (await session.execute(select(Penalty).where(Penalty.participant_id == p.id))).scalar_one()
    assert pen.status == PenaltyStatus.CANCELLED
