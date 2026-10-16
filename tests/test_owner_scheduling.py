"""The actual Telegram chat owner has no group challenge obligations."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatMemberStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db import repo
from bot.db.models import CourseRun, DailyClosure, LessonDelivery, ParticipantStatus, Penalty, PenaltyStatus
from bot.scheduler import jobs
from bot.services import challenge, digest, lessons, onboarding

NOW = datetime(2026, 10, 15, 8)
YESTERDAY = date(2026, 10, 14)
OWNER_ID = 42
ADMIN_ID = 43


def _bot():
    bot = AsyncMock()
    bot.get_chat_administrators.return_value = [
        SimpleNamespace(status=ChatMemberStatus.CREATOR, user=SimpleNamespace(id=OWNER_ID)),
        SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR, user=SimpleNamespace(id=ADMIN_ID)),
    ]
    return bot


async def _participants(session, stream):
    people = []
    for tg_id, username in [(OWNER_ID, "owner"), (ADMIN_ID, "admin")]:
        person = await repo.approve_participant(session, stream.id, username)
        person.tg_user_id = tg_id
        await session.commit()
        await onboarding.process_join(
            session, stream=stream, tg_user_id=tg_id, username=username,
            full_name=username, grace_hours=24, occurred_at=NOW - timedelta(days=2),
        )
        people.append(person)
    return people


def _configure(session, monkeypatch):
    monkeypatch.setattr(jobs, "SessionFactory", async_sessionmaker(
        session.bind, expire_on_commit=False,
    ))
    monkeypatch.setattr(jobs, "_now_naive_utc", lambda: NOW)


async def test_close_day_excludes_chat_owner_but_includes_regular_admin(session, stream, monkeypatch):
    owner, admin = await _participants(session, stream)
    _configure(session, monkeypatch)
    bot = _bot()

    await jobs.job_close_day(bot, stream.id)

    penalties = (await session.execute(select(Penalty))).scalars().all()
    assert [row.participant_id for row in penalties] == [admin.id]
    text = bot.send_message.await_args.args[1]
    assert "участников — 1" in text
    assert "owner" not in text
    assert "admin" in text
    assert owner.status == ParticipantStatus.ACTIVE


async def test_report_reminder_does_not_mention_owner(session, stream, monkeypatch):
    await _participants(session, stream)
    reporter = await repo.approve_participant(session, stream.id, "already_posted")
    reporter.tg_user_id = 44
    await session.commit()
    await onboarding.process_join(
        session, stream=stream, tg_user_id=44, username="already_posted",
        full_name="already_posted", grace_hours=24, occurred_at=NOW - timedelta(days=2),
    )
    await challenge.accept_report(
        session, stream=stream, tg_user_id=44, message_id=8,
        content="#пост t.me/x/1", is_forward=False, min_chars=400, now_utc=NOW,
    )
    _configure(session, monkeypatch)
    bot = _bot()

    await jobs.job_report_reminder(bot, stream.id)

    text = bot.send_message.await_args.args[1]
    assert "owner" not in text
    assert "already_posted" not in text
    assert "admin" in text


@pytest.mark.parametrize("job", [jobs.job_penalty_reminder, jobs.job_kick_expired])
async def test_old_owner_penalty_does_not_trigger_reminder_or_kick(
    session, stream, monkeypatch, job,
):
    owner, admin = await _participants(session, stream)
    for person in (owner, admin):
        session.add(Penalty(
            stream_id=stream.id, participant_id=person.id, issued_for_date=YESTERDAY,
            status=PenaltyStatus.OPEN, due_at=NOW - timedelta(hours=1),
        ))
    await session.commit()
    _configure(session, monkeypatch)
    bot = _bot()

    await job(bot, stream.id)

    assert all("owner" not in call.args[1] for call in bot.send_message.await_args_list)
    if job is jobs.job_kick_expired:
        bot.ban_chat_member.assert_awaited_once_with(stream.chat_id, ADMIN_ID)
        await session.refresh(owner)
        assert owner.status == ParticipantStatus.ACTIVE
    else:
        assert "admin" in bot.send_message.await_args.args[1]


async def test_recovery_excludes_owner_without_losing_other_participants(session, stream, monkeypatch):
    owner, admin = await _participants(session, stream)
    stream.created_at = NOW - timedelta(days=4)
    session.add(DailyClosure(
        stream_id=stream.id, report_date=date(2026, 10, 12), digest_sent_at=NOW,
    ))
    await session.commit()
    _configure(session, monkeypatch)
    bot = _bot()

    await jobs.recover_missed_days(bot)
    await jobs.recover_missed_days(bot)

    penalties = (await session.execute(select(Penalty))).scalars().all()
    assert {row.participant_id for row in penalties} == {admin.id}
    assert [row.issued_for_date for row in penalties] == [date(2026, 10, 13)]
    bot.send_message.assert_awaited_once()
    assert "owner" not in bot.send_message.await_args.args[1]
    assert "14/10/26" in bot.send_message.await_args.args[1]
    assert owner.status == ParticipantStatus.ACTIVE


@pytest.mark.parametrize("job", [
    jobs.job_close_day, jobs.job_report_reminder, jobs.job_penalty_reminder,
    jobs.job_kick_expired, jobs.recover_missed_days,
])
async def test_failed_role_lookup_pauses_obligations(session, stream, monkeypatch, job):
    owner, _ = await _participants(session, stream)
    stream.created_at = NOW - timedelta(days=4)
    session.add(Penalty(
        stream_id=stream.id, participant_id=owner.id, issued_for_date=YESTERDAY,
        status=PenaltyStatus.OPEN, due_at=NOW - timedelta(hours=1),
    ))
    await session.commit()
    _configure(session, monkeypatch)
    bot = _bot()
    bot.get_chat_administrators.side_effect = RuntimeError("Telegram unavailable")

    if job is jobs.recover_missed_days:
        await job(bot)
    else:
        await job(bot, stream.id)

    bot.send_message.assert_not_awaited()
    bot.ban_chat_member.assert_not_awaited()
    assert (await session.execute(select(DailyClosure))).scalars().all() == []
    assert len((await session.execute(select(Penalty))).scalars().all()) == 1


async def test_stats_omit_existing_owner_reports_and_penalties(session, stream):
    owner, admin = await _participants(session, stream)
    await challenge.accept_report(
        session, stream=stream, tg_user_id=OWNER_ID, message_id=7,
        content="#пост t.me/x/1", is_forward=False, min_chars=400, now_utc=NOW,
    )
    for person in (owner, admin):
        session.add(Penalty(
            stream_id=stream.id, participant_id=person.id, issued_for_date=YESTERDAY,
            status=PenaltyStatus.OPEN, due_at=NOW,
        ))
    await session.commit()

    stats = await digest.collect_stats(
        session, stream, NOW, exclude_tg_user_ids=frozenset({OWNER_ID}),
    )

    assert stats.total_active == 1
    assert stats.reported_today == 0
    assert stats.open_penalties == 1
    assert stats.penalized_names == ["admin"]


async def test_old_owner_report_is_not_counted_in_digest(session, stream, monkeypatch):
    await _participants(session, stream)
    await challenge.accept_report(
        session, stream=stream, tg_user_id=OWNER_ID, message_id=7,
        content="#пост t.me/x/1", is_forward=False, min_chars=400,
        now_utc=NOW - timedelta(days=1),
    )
    _configure(session, monkeypatch)
    bot = _bot()

    await jobs.job_close_day(bot, stream.id)

    text = bot.send_message.await_args.args[1]
    assert "участников — 1" in text
    assert "Написали пост — 0" in text
    assert "owner" not in text


async def test_lesson_job_skips_owner_without_deleting_course_history(session, stream, monkeypatch):
    owner, admin = await _participants(session, stream)
    for person in (owner, admin):
        await lessons.activate_course(session, person, NOW - timedelta(hours=1))
    _configure(session, monkeypatch)
    bot = _bot()
    bot.send_message.return_value = SimpleNamespace(message_id=1)

    await jobs.job_lesson(bot, stream.id)

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args[0] == ADMIN_ID
    assert await session.get(CourseRun, owner.id) is not None
    assert await session.get(LessonDelivery, (owner.id, 1)) is None


@pytest.mark.parametrize("recover", [False, True])
async def test_owner_only_chat_does_not_receive_empty_digest(session, stream, monkeypatch, recover):
    owner = await repo.approve_participant(session, stream.id, "owner")
    owner.tg_user_id = OWNER_ID
    stream.created_at = NOW - timedelta(days=2)
    await session.commit()
    await onboarding.process_join(
        session, stream=stream, tg_user_id=OWNER_ID, username="owner", full_name="owner",
        grace_hours=24, occurred_at=NOW - timedelta(days=2),
    )
    _configure(session, monkeypatch)
    bot = _bot()

    for _ in range(2):
        if recover:
            await jobs.recover_missed_days(bot)
        else:
            await jobs.job_close_day(bot, stream.id)

    bot.send_message.assert_not_awaited()
    assert (await session.execute(select(Penalty))).scalars().all() == []
    async with jobs.SessionFactory() as verification:
        closure = await verification.get(DailyClosure, (stream.id, YESTERDAY))
        assert closure is not None
        assert closure.digest_sent_at is None
