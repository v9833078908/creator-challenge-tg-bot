import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage
from sqlalchemy import select

from bot.db import models, repo
from bot.services import lessons


@pytest.fixture(autouse=True)
def scheduler_chat_owner(monkeypatch):
    """These course fixtures are ordinary participants, with a separate chat owner."""
    monkeypatch.setattr(
        "bot.scheduler.jobs.get_chat_owner_id", AsyncMock(return_value=999999),
    )


def catalog_file(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "brand": "Creator Hero",
                "count": 33,
                "items": [
                    {
                        "day": i,
                        "lesson_id": f"lesson-{i}",
                        "text": f"👑 Creator Hero\nУрок {i}",
                        "button_text": "Открыть урок",
                        "article_url": f"https://example.org/lesson-{i}",
                    }
                    for i in range(1, 34)
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest_asyncio.fixture
async def participant(session, stream):
    person = await repo.approve_participant(session, stream.id, "firstfriend")
    person.tg_user_id = 12345
    await session.commit()
    return person


async def send(session, stream, participant, bot, tmp_path, now):
    return await lessons.deliver_due_lesson(
        session,
        stream,
        participant,
        bot,
        lessons.load_catalog(catalog_file(tmp_path)),
        now,
        clock=lambda: now,
    )


async def test_deploy_does_not_start_course(session, stream, participant, tmp_path):
    bot = SimpleNamespace(send_message=AsyncMock())
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 6))
    bot.send_message.assert_not_called()


async def test_first_lesson_after_activation_then_next_morning_at_eight(
    session, stream, participant, tmp_path
):
    activated = datetime(2026, 10, 14, 17)
    await lessons.activate_course(session, participant, activated)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=123)))
    await send(session, stream, participant, bot, tmp_path, activated + timedelta(minutes=10))
    bot.send_message.assert_awaited_once()
    args = bot.send_message.call_args
    assert args.args[0] == participant.tg_user_id
    assert args.kwargs["reply_markup"].inline_keyboard[0][0].url == "https://example.org/lesson-1"
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 4, 59))
    bot.send_message.assert_awaited_once()
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 5))
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 5, 1))
    assert bot.send_message.await_count == 2
    assert "Урок 2" in bot.send_message.call_args.args[1]


async def test_outage_sends_one_next_lesson_and_does_not_skip_content(session, stream, participant, tmp_path):
    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 20, 10))
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 20, 11))
    assert bot.send_message.await_count == 1
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 21, 5))
    assert bot.send_message.await_count == 2
    assert "Урок 2" in bot.send_message.call_args.args[1]


async def test_course_stops_after_thirty_three(session, stream, participant, tmp_path):
    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    for i in range(35):
        await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 5) + timedelta(days=i))
    assert bot.send_message.await_count == 33


async def test_network_ambiguity_blocks_retry_until_owner_resolves(session, stream, participant, tmp_path):
    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    bot = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=TelegramNetworkError(
                method=SendMessage(chat_id=stream.chat_id, text="x"),
                message="timed out",
            )
        )
    )
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 5))
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 16, 5))
    assert bot.send_message.await_count == 1
    delivery = (await session.execute(select(models.LessonDelivery))).scalar_one()
    assert delivery.status == "uncertain"
    await lessons.resolve_delivery(session, participant.id, "sent", datetime(2026, 10, 16, 5))
    bot.send_message.side_effect = None
    bot.send_message.return_value = SimpleNamespace(message_id=2)
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 16, 6))
    assert bot.send_message.await_count == 1
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 17, 5))
    assert bot.send_message.await_count == 2


async def test_interrupted_sending_is_never_blindly_retried(session, stream, participant, tmp_path):
    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    session.add(
        models.LessonDelivery(
            stream_id=stream.id,
            participant_id=participant.id,
            lesson_day=1,
            status="sending",
            attempted_at=datetime(2026, 10, 15, 5),
        )
    )
    await session.commit()
    bot = SimpleNamespace(send_message=AsyncMock())
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 16, 5))
    bot.send_message.assert_not_called()
    await lessons.resolve_delivery(session, participant.id, "retry", datetime(2026, 10, 16, 5))
    bot.send_message.return_value = SimpleNamespace(message_id=5)
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 16, 5))
    bot.send_message.assert_awaited_once()


async def test_rate_limit_retries_no_earlier_than_requested(session, stream, participant, tmp_path):
    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    bot = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=TelegramRetryAfter(
                method=SendMessage(chat_id=stream.chat_id, text="x"),
                message="wait",
                retry_after=120,
            )
        )
    )
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 5))
    bot.send_message.side_effect = None
    bot.send_message.return_value = SimpleNamespace(message_id=2)
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 5, 1))
    assert bot.send_message.await_count == 1
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 5, 2))
    assert bot.send_message.await_count == 2


async def test_bad_request_pauses_and_requires_owner_fix(session, stream, participant, tmp_path):
    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    bot = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=TelegramBadRequest(
                method=SendMessage(chat_id=stream.chat_id, text="x"),
                message="chat not found",
            )
        )
    )
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 5))
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 16, 5))
    assert bot.send_message.await_count == 1
    delivery = (await session.execute(select(models.LessonDelivery))).scalar_one()
    assert delivery.status == "failed"


async def test_repeated_activation_does_not_reset_progress(session, stream, participant):
    first = await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    again = await lessons.activate_course(session, participant, datetime(2026, 10, 20, 7))
    assert again.participant_id == first.participant_id
    assert again.activated_at == datetime(2026, 10, 14, 7)
    assert again.first_delivery_date == date(2026, 10, 14)


def test_catalog_rejects_missing_url_and_nonconsecutive_days(tmp_path):
    path = catalog_file(tmp_path)
    data = json.loads(path.read_text())
    data["items"][2]["article_url"] = None
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        lessons.load_catalog(path)
    data["items"][2]["article_url"] = "https://example.org/x"
    data["items"][2]["day"] = 5
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        lessons.load_catalog(path)


async def test_lesson_job_notifies_owner_once_for_ambiguous_delivery(
    session, stream, participant, tmp_path, monkeypatch
):
    from bot.config import settings
    from bot.scheduler import jobs

    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    session.add(
        models.LessonDelivery(
            stream_id=stream.id,
            participant_id=participant.id,
            lesson_day=1,
            status="sending",
            attempted_at=datetime(2026, 10, 15, 5),
        )
    )
    await session.commit()
    monkeypatch.setattr(jobs, "SessionFactory", lambda: session)
    monkeypatch.setattr(jobs, "_now_naive_utc", lambda: datetime(2026, 10, 16, 5))
    monkeypatch.setattr(lessons, "CATALOG_PATH", catalog_file(tmp_path))
    monkeypatch.setattr(settings, "owner_tg_id", 42)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    await jobs.job_lesson(bot, stream.id)
    await jobs.job_lesson(bot, stream.id)
    bot.send_message.assert_awaited_once()
    assert bot.send_message.call_args.args[0] == 42
    assert "/resolve_lesson" in bot.send_message.call_args.args[1]


async def test_lesson_job_registered_in_moscow_even_for_other_stream_timezone(session, stream, monkeypatch):
    from bot.scheduler import jobs

    stream.timezone = "Asia/Tokyo"
    await session.commit()
    monkeypatch.setattr(jobs, "SessionFactory", lambda: session)
    registered = []
    scheduler = SimpleNamespace(add_job=lambda *args, **kwargs: registered.append((args, kwargs)))
    await jobs.register_stream_jobs(scheduler, object(), stream.id)
    lesson_args, lesson_job = next(
        (args, kwargs) for args, kwargs in registered if kwargs["id"] == f"lesson:{stream.id}"
    )
    assert str(lesson_args[1].timezone) == "Europe/Moscow"
    assert lesson_job["max_instances"] == 1
    assert lesson_job["coalesce"] is True


async def test_only_owner_can_resolve_lesson(session, stream, participant, monkeypatch):
    from bot.config import settings
    from bot.handlers import admin

    monkeypatch.setattr(settings, "owner_tg_id", 42)
    answer = AsyncMock()
    message = SimpleNamespace(from_user=SimpleNamespace(id=43), answer=answer)
    await admin.resolve_lesson(message, SimpleNamespace(args="@firstfriend retry"))
    assert "организатора" in answer.call_args.args[0].lower()


async def test_owner_resolution_normalizes_aware_time_to_utc(session, stream, participant):
    from zoneinfo import ZoneInfo

    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    row = models.LessonDelivery(
        stream_id=stream.id, participant_id=participant.id, lesson_day=1, status="uncertain"
    )
    session.add(row)
    await session.commit()
    await lessons.resolve_delivery(
        session, participant.id, "sent", datetime(2026, 10, 15, 8, tzinfo=ZoneInfo("Europe/Moscow"))
    )
    assert row.sent_at == datetime(2026, 10, 15, 5)


async def test_owner_cannot_resolve_an_inflight_send(session, stream, participant, tmp_path):
    import asyncio

    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_send(*args, **kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(message_id=10)

    task = asyncio.create_task(
        send(
            session,
            stream,
            participant,
            SimpleNamespace(send_message=delayed_send),
            tmp_path,
            datetime(2026, 10, 15, 5),
        )
    )
    await entered.wait()
    resolution = asyncio.create_task(
        lessons.resolve_delivery(
            session,
            participant.id,
            "retry",
            datetime(2026, 10, 15, 5),
        )
    )
    await asyncio.sleep(0.01)
    try:
        assert not resolution.done()
    finally:
        release.set()
        await task
        try:
            await resolution
        except ValueError:
            pass
    row = (await session.execute(select(models.LessonDelivery))).scalar_one()
    assert row.status == "sent"


async def test_two_sessions_send_once_and_reopened_database_retains_progress(tmp_path):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from bot.db import repo

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'course.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as initial:
        saved_stream = await repo.create_stream(
            initial, title="Friends", chat_id=-100, invite_link="https://t.me/+test", timezone="Europe/Moscow"
        )
        await initial.commit()
        person = await repo.approve_participant(initial, saved_stream.id, "samefriend")
        person.tg_user_id = 123
        await initial.commit()
        await lessons.activate_course(initial, person, datetime(2026, 10, 14, 7))
        person_id = person.id
        stream_id = saved_stream.id
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=22)))

    async def run_once():
        async with factory() as independent:
            own_stream = await independent.get(models.Stream, stream_id)
            own_person = await independent.get(models.Participant, person_id)
            await send(independent, own_stream, own_person, bot, tmp_path, datetime(2026, 10, 15, 5))

    await asyncio.gather(run_once(), run_once())
    bot.send_message.assert_awaited_once()
    await engine.dispose()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as reopened:
        own_stream = await reopened.get(models.Stream, stream_id)
        own_person = await reopened.get(models.Participant, person_id)
        await send(reopened, own_stream, own_person, bot, tmp_path, datetime(2026, 10, 15, 10))
        bot.send_message.assert_awaited_once()
        await send(reopened, own_stream, own_person, bot, tmp_path, datetime(2026, 10, 16, 5))
        assert bot.send_message.await_count == 2
    await engine.dispose()


async def test_rate_limit_delay_starts_when_slow_api_response_arrives(session, stream, participant, tmp_path):
    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    response_time = datetime(2026, 10, 15, 5, 1, 30)
    bot = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=TelegramRetryAfter(
                method=SendMessage(chat_id=stream.chat_id, text="x"),
                message="wait",
                retry_after=120,
            )
        )
    )
    await lessons.deliver_due_lesson(
        session,
        stream,
        participant,
        bot,
        lessons.load_catalog(catalog_file(tmp_path)),
        datetime(2026, 10, 15, 5),
        clock=lambda: response_time,
    )
    delivery = (await session.execute(select(models.LessonDelivery))).scalar_one()
    assert delivery.retry_at == datetime(2026, 10, 15, 5, 3, 30)
    bot.send_message.side_effect = None
    bot.send_message.return_value = SimpleNamespace(message_id=10)
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 5, 2))
    assert bot.send_message.await_count == 1
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 5, 3, 30))
    assert bot.send_message.await_count == 2


async def test_start_enrolls_two_people_independently_and_repeated_start_preserves_progress(
    session,
    stream,
    tmp_path,
):
    from bot.services import onboarding

    first = await repo.approve_participant(session, stream.id, "firstfriend")
    second = await repo.approve_participant(session, stream.id, "secondfriend")
    await session.commit()
    first_activation = datetime(2026, 10, 14, 7)
    await onboarding.process_start(
        session, tg_user_id=123, username="firstfriend", full_name="First", occurred_at=first_activation
    )
    await onboarding.process_start(
        session,
        tg_user_id=456,
        username="secondfriend",
        full_name="Second",
        occurred_at=datetime(2026, 10, 16, 7),
    )
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=11)))
    await send(session, stream, first, bot, tmp_path, datetime(2026, 10, 15, 5))
    await send(session, stream, second, bot, tmp_path, datetime(2026, 10, 15, 5))
    bot.send_message.assert_awaited_once()
    assert bot.send_message.call_args.args[0] == 123
    await onboarding.process_start(
        session,
        tg_user_id=123,
        username="renamedfriend",
        full_name="First",
        occurred_at=datetime(2026, 10, 16, 7),
    )
    assert (await session.get(models.CourseRun, first.id)).activated_at == first_activation
    await send(session, stream, first, bot, tmp_path, datetime(2026, 10, 17, 5))
    assert "Урок 2" in bot.send_message.call_args.args[1]
    await send(session, stream, second, bot, tmp_path, datetime(2026, 10, 17, 5))
    assert bot.send_message.call_args.args[0] == 456
    assert "Урок 1" in bot.send_message.call_args.args[1]


@pytest.mark.parametrize("status", [models.ParticipantStatus.LEFT, models.ParticipantStatus.KICKED])
async def test_leave_or_kick_pauses_without_losing_progress(session, stream, participant, tmp_path, status):
    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 5))
    participant.status = status
    await session.commit()
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 16, 5))
    assert bot.send_message.await_count == 1
    participant.status = models.ParticipantStatus.ACTIVE
    await session.commit()
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 17, 5))
    assert bot.send_message.await_count == 2
    assert "Урок 2" in bot.send_message.call_args.args[1]


async def test_unknown_kicked_left_and_username_takeover_do_not_enroll(session, stream):
    from bot.services import onboarding

    person = await repo.approve_participant(session, stream.id, "boundfriend")
    person.tg_user_id = 1
    await session.commit()
    await onboarding.process_start(session, tg_user_id=2, username="boundfriend", full_name="Impostor")
    await onboarding.process_start(session, tg_user_id=3, username="unknown", full_name="Unknown")
    person.status = models.ParticipantStatus.KICKED
    await session.commit()
    await onboarding.process_start(session, tg_user_id=1, username="boundfriend", full_name="Kicked")
    person.status = models.ParticipantStatus.LEFT
    await session.commit()
    await onboarding.process_start(session, tg_user_id=1, username="boundfriend", full_name="Left")
    assert (await session.execute(select(models.CourseRun))).scalars().all() == []


async def test_revoked_approved_participant_cannot_receive_lessons_or_reset_after_reapproval(
    session,
    stream,
    participant,
    tmp_path,
    monkeypatch,
):
    from bot.config import settings
    from bot.handlers import admin
    from bot.services import onboarding

    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 14, 7, 10))
    await repo.revoke_participant(session, participant)
    await session.commit()
    outcome = await onboarding.process_start(
        session, tg_user_id=participant.tg_user_id, username=participant.username, full_name="Friend"
    )
    assert outcome.kind == "refused"
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 5))
    assert bot.send_message.await_count == 1
    monkeypatch.setattr(admin, "SessionFactory", lambda: session)
    monkeypatch.setattr(settings, "owner_tg_id", 42)
    message = SimpleNamespace(from_user=SimpleNamespace(id=42), answer=AsyncMock())
    await admin.approve(message, SimpleNamespace(args="@firstfriend"), bot)
    assert participant.status == models.ParticipantStatus.APPROVED
    await onboarding.process_start(
        session, tg_user_id=participant.tg_user_id, username=participant.username, full_name="Friend"
    )
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 5))
    assert bot.send_message.await_count == 2
    assert "Урок 2" in bot.send_message.call_args.args[1]
    assert len((await session.execute(select(models.CourseRun))).scalars().all()) == 1


@pytest.mark.parametrize(
    ("activation", "first_date"),
    [
        (datetime(2026, 10, 14, 4, 59, 59), date(2026, 10, 14)),
        (datetime(2026, 10, 14, 5), date(2026, 10, 14)),
        (datetime(2026, 10, 14, 20), date(2026, 10, 14)),
    ],
)
async def test_first_lesson_date_is_moscow_activation_date(
    session, stream, participant, activation, first_date
):
    run = await lessons.activate_course(session, participant, activation)
    assert run.first_delivery_date == first_date


@pytest.mark.parametrize("status", [models.ParticipantStatus.APPROVED, models.ParticipantStatus.ACTIVE])
async def test_private_start_explains_personal_schedule(
    session,
    stream,
    participant,
    monkeypatch,
    status,
    tmp_path,
):
    from bot.handlers import dm
    from bot.services import onboarding

    participant.status = status
    await session.commit()
    monkeypatch.setattr(dm, "SessionFactory", lambda: session)
    monkeypatch.setattr(dm.invites, "ensure_invite_link", AsyncMock(return_value=stream.invite_link))
    monkeypatch.setattr(onboarding, "utcnow", lambda: datetime(2026, 10, 14, 7))
    answer = AsyncMock()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=participant.tg_user_id, username=participant.username, full_name="Друг"),
        answer=answer,
    )
    from bot.scheduler import jobs

    monkeypatch.setattr(jobs, "SessionFactory", lambda: session)
    monkeypatch.setattr(jobs, "_now_naive_utc", lambda: datetime(2026, 10, 14, 7))
    monkeypatch.setattr(lessons, "CATALOG_PATH", catalog_file(tmp_path))
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    await dm.cmd_start(message, bot)
    text = answer.call_args.args[0]
    assert "через 10 минут после первой активации" in text and "08:00 МСК" in text and "личку" in text
    bot.send_message.assert_not_called()
    await dm.cmd_start(message, bot)
    bot.send_message.assert_not_called()
    assert "не начинает курс заново" in answer.call_args.args[0]
    assert "07:10" not in answer.call_args.args[0]  # UTC must not leak into user time.
    assert "10:10" in answer.call_args.args[0]  # First activation was 10:00 Moscow.
    monkeypatch.setattr(jobs, "_now_naive_utc", lambda: datetime(2026, 10, 14, 7, 10))
    await jobs.job_lesson(bot, stream.id, participant.id)
    bot.send_message.assert_awaited_once()
    assert bot.send_message.call_args.args[0] == participant.tg_user_id
    assert await session.get(models.CourseRun, participant.id) is not None


async def test_blocked_dm_notifies_owner_once_and_does_not_retry(
    session, stream, participant, tmp_path, monkeypatch
):
    from aiogram.exceptions import TelegramForbiddenError

    from bot.config import settings
    from bot.scheduler import jobs

    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    monkeypatch.setattr(jobs, "SessionFactory", lambda: session)
    monkeypatch.setattr(jobs, "_now_naive_utc", lambda: datetime(2026, 10, 16, 5))
    monkeypatch.setattr(lessons, "CATALOG_PATH", catalog_file(tmp_path))
    monkeypatch.setattr(settings, "owner_tg_id", 42)
    calls = []

    async def blocked(chat_id, text, **kwargs):
        calls.append((chat_id, text))
        if chat_id == participant.tg_user_id:
            raise TelegramForbiddenError(method=SendMessage(chat_id=chat_id, text="x"), message="bot blocked")
        return SimpleNamespace(message_id=100)

    bot = SimpleNamespace(send_message=blocked)
    await jobs.job_lesson(bot, stream.id)
    await jobs.job_lesson(bot, stream.id)
    assert [call[0] for call in calls] == [participant.tg_user_id, 42]
    assert str(participant.tg_user_id) in calls[-1][1]
    assert "/resolve_lesson" in calls[-1][1]


async def test_success_uses_telegram_message_time_to_prevent_two_deliveries_on_actual_date(
    session,
    stream,
    participant,
    tmp_path,
):
    from datetime import timezone

    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    # A slow request started just before midnight, but Telegram accepted it after midnight.
    telegram_date = datetime(2026, 10, 15, 21, 0, 2, tzinfo=timezone.utc)
    bot = SimpleNamespace(
        send_message=AsyncMock(
            return_value=SimpleNamespace(
                message_id=42,
                date=telegram_date,
            )
        )
    )
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 20, 59, 59))
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 16, 5))
    bot.send_message.assert_awaited_once()


async def test_activation_before_eight_never_sends_second_lesson_same_morning(
    session, stream, participant, tmp_path
):
    activated = datetime(2026, 10, 14, 4, 49)
    await lessons.activate_course(session, participant, activated)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    await send(session, stream, participant, bot, tmp_path, activated + timedelta(minutes=10))
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 14, 5))
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 4, 59))
    bot.send_message.assert_awaited_once()
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 10, 15, 5))
    assert bot.send_message.await_count == 2


async def test_completed_course_never_restarts_on_start(session, stream, participant, tmp_path):
    from bot.services import onboarding

    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    for day in range(33):
        await send(
            session, stream, participant, bot, tmp_path, datetime(2026, 10, 14, 7, 10) + timedelta(days=day)
        )
    await onboarding.process_start(
        session, tg_user_id=participant.tg_user_id, username=participant.username, full_name="Friend"
    )
    await send(session, stream, participant, bot, tmp_path, datetime(2026, 12, 1, 5))
    assert bot.send_message.await_count == 33


async def test_preloaded_person_paused_while_another_dm_is_inflight_does_not_receive_lesson(
    tmp_path, monkeypatch
):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from bot.scheduler import jobs

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pause.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as initial:
        stream = await repo.create_stream(
            initial, title="Friends", chat_id=-100, invite_link="https://t.me/+test", timezone="Europe/Moscow"
        )
        await initial.commit()
        first = await repo.approve_participant(initial, stream.id, "firstfriend")
        second = await repo.approve_participant(initial, stream.id, "secondfriend")
        first.tg_user_id, second.tg_user_id = 123, 456
        await initial.commit()
        for person in [first, second]:
            await lessons.activate_course(initial, person, datetime(2026, 10, 14, 7))
        stream_id, second_id = stream.id, second.id
    monkeypatch.setattr(jobs, "SessionFactory", factory)
    monkeypatch.setattr(jobs, "_now_naive_utc", lambda: datetime(2026, 10, 15, 5))
    monkeypatch.setattr(lessons, "CATALOG_PATH", catalog_file(tmp_path))
    recipients = []

    async def send_with_leave(chat_id, *args, **kwargs):
        recipients.append(chat_id)
        if chat_id == 123:
            async with factory() as other:
                paused = await other.get(models.Participant, second_id)
                paused.status = models.ParticipantStatus.LEFT
                await other.commit()
        return SimpleNamespace(message_id=1)

    await jobs.job_lesson(SimpleNamespace(send_message=send_with_leave), stream_id)
    assert recipients == [123]
    await engine.dispose()


@pytest.mark.parametrize(
    "activated",
    [
        datetime(2026, 10, 14, 17, 21, 37),
        datetime(2026, 10, 14, 20, 55, 0),  # Moscow midnight falls inside the delay.
        datetime(2026, 10, 14, 4, 55, 0),  # First at 08:05 Moscow; second is tomorrow.
    ],
)
async def test_first_lesson_waits_full_ten_minutes_from_original_activation(
    session,
    stream,
    participant,
    tmp_path,
    activated,
):
    await lessons.activate_course(session, participant, activated)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    await send(session, stream, participant, bot, tmp_path, activated + timedelta(minutes=9, seconds=59))
    bot.send_message.assert_not_called()
    await lessons.activate_course(session, participant, activated + timedelta(minutes=7))
    run = await session.get(models.CourseRun, participant.id)
    assert run.activated_at == activated
    await send(session, stream, participant, bot, tmp_path, activated + timedelta(minutes=10))
    bot.send_message.assert_awaited_once()
    await send(session, stream, participant, bot, tmp_path, activated + timedelta(minutes=11))
    bot.send_message.assert_awaited_once()
    first_date = lessons.moscow_now(activated + timedelta(minutes=10)).date()
    next_morning = datetime.combine(first_date + timedelta(days=1), datetime.min.time()).replace(hour=5)
    await send(session, stream, participant, bot, tmp_path, next_morning - timedelta(seconds=1))
    bot.send_message.assert_awaited_once()
    await send(session, stream, participant, bot, tmp_path, next_morning)
    assert bot.send_message.await_count == 2


async def test_precise_first_lesson_job_uses_persisted_due_time_after_restart(
    session,
    stream,
    participant,
    monkeypatch,
):
    from apscheduler.triggers.date import DateTrigger

    from bot.scheduler import jobs

    activated = datetime(2026, 10, 14, 17, 21, 37)
    await lessons.activate_course(session, participant, activated)
    monkeypatch.setattr(jobs, "SessionFactory", lambda: session)
    monkeypatch.setattr(jobs, "_now_naive_utc", lambda: activated + timedelta(minutes=3))
    registered = []
    scheduler = SimpleNamespace(add_job=lambda *args, **kwargs: registered.append((args, kwargs)))
    await jobs.register_stream_jobs(scheduler, object(), stream.id)
    args, options = next(
        (args, options) for args, options in registered if options["id"] == f"lesson_first:{participant.id}"
    )
    assert isinstance(args[1], DateTrigger)
    assert args[1].run_date.replace(tzinfo=None) == activated + timedelta(minutes=10)
    assert options["args"][1:] == [stream.id, participant.id]
    assert options["replace_existing"] is True
    await lessons.activate_course(session, participant, activated + timedelta(minutes=4))
    registered.clear()
    await jobs.register_first_lesson_job(scheduler, object(), participant.id)
    args, _ = registered[0]
    assert args[1].run_date.replace(tzinfo=None) == activated + timedelta(minutes=10)


@pytest.mark.parametrize(
    ("state", "phrase"),
    [
        ("completed", "Все 33 урока"),
        ("paused", "Уроки на паузе"),
        ("blocked", "Отправка уроков приостановлена"),
    ],
)
async def test_repeated_start_message_describes_actual_course_state(
    session,
    stream,
    participant,
    monkeypatch,
    state,
    phrase,
):
    from bot.handlers import dm
    from bot.scheduler import jobs

    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    if state == "paused":
        participant.status = models.ParticipantStatus.LEFT
    elif state == "completed":
        for day in range(1, 34):
            session.add(
                models.LessonDelivery(
                    stream_id=stream.id,
                    participant_id=participant.id,
                    lesson_day=day,
                    status="sent",
                    sent_at=datetime(2026, 10, 14, 7, 10) + timedelta(days=day),
                )
            )
    else:
        session.add(
            models.LessonDelivery(
                stream_id=stream.id, participant_id=participant.id, lesson_day=1, status="uncertain"
            )
        )
    await session.commit()
    monkeypatch.setattr(dm, "SessionFactory", lambda: session)
    monkeypatch.setattr(dm.invites, "ensure_invite_link", AsyncMock(return_value=stream.invite_link))
    monkeypatch.setattr(jobs, "SessionFactory", lambda: session)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=participant.tg_user_id, username=participant.username, full_name="Друг"),
        answer=AsyncMock(),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    await dm.cmd_start(message, bot)
    assert phrase in message.answer.call_args.args[0]
    assert "08:00" not in message.answer.call_args.args[0]
    bot.send_message.assert_not_called()


@pytest.mark.parametrize("paused_status", [models.ParticipantStatus.REVOKED, models.ParticipantStatus.KICKED])
async def test_owner_can_restore_renamed_bound_person_by_telegram_id_without_reset(
    session,
    stream,
    participant,
    monkeypatch,
    paused_status,
):
    from bot.config import settings
    from bot.handlers import admin
    from bot.services import onboarding

    activated = datetime(2026, 10, 14, 7)
    await lessons.activate_course(session, participant, activated)
    participant.status = paused_status
    session.add(
        models.LessonDelivery(
            stream_id=stream.id,
            participant_id=participant.id,
            lesson_day=1,
            status="sent",
            sent_at=activated + timedelta(minutes=10),
        )
    )
    await session.commit()
    await onboarding.process_start(
        session, tg_user_id=participant.tg_user_id, username="renamedfriend", full_name="Друг"
    )
    monkeypatch.setattr(admin, "SessionFactory", lambda: session)
    monkeypatch.setattr(settings, "owner_tg_id", 42)
    message = SimpleNamespace(from_user=SimpleNamespace(id=42), answer=AsyncMock())
    bot = SimpleNamespace(unban_chat_member=AsyncMock())
    await admin.approve(message, SimpleNamespace(args=str(participant.tg_user_id)), bot)
    assert participant.status == models.ParticipantStatus.APPROVED
    assert [person.id for person in (await session.execute(select(models.Participant))).scalars()] == [
        participant.id
    ]
    outcome = await onboarding.process_start(
        session, tg_user_id=participant.tg_user_id, username="renamedfriend", full_name="Друг"
    )
    assert outcome.kind == "invited" and outcome.course_newly_activated is False
    run = await session.get(models.CourseRun, participant.id)
    assert run.activated_at == activated
    assert len(await lessons.course_deliveries(session, participant.id)) == 1
    if paused_status == models.ParticipantStatus.KICKED:
        bot.unban_chat_member.assert_awaited_once_with(stream.chat_id, participant.tg_user_id)
    assert "@12345" not in message.answer.call_args.args[0]


async def test_owner_approve_unknown_telegram_id_does_not_create_or_bind_anyone(session, stream, monkeypatch):
    from bot.config import settings
    from bot.handlers import admin

    monkeypatch.setattr(admin, "SessionFactory", lambda: session)
    monkeypatch.setattr(settings, "owner_tg_id", 42)
    message = SimpleNamespace(from_user=SimpleNamespace(id=42), answer=AsyncMock())
    await admin.approve(message, SimpleNamespace(args="123456789"), SimpleNamespace())
    assert (await session.execute(select(models.Participant))).scalars().all() == []
    assert "привязанного" in message.answer.call_args.args[0]


async def test_owner_revoke_by_telegram_id_preserves_bound_identity_and_course(
    session, stream, participant, monkeypatch
):
    from bot.config import settings
    from bot.handlers import admin

    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    monkeypatch.setattr(admin, "SessionFactory", lambda: session)
    monkeypatch.setattr(settings, "owner_tg_id", 42)
    message = SimpleNamespace(from_user=SimpleNamespace(id=42), answer=AsyncMock())
    await admin.revoke(message, SimpleNamespace(args=str(participant.tg_user_id)))
    assert participant.status == models.ParticipantStatus.REVOKED
    assert participant.tg_user_id == 12345
    assert await session.get(models.CourseRun, participant.id) is not None


async def test_database_reopen_keeps_original_ten_minute_deadline(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'ten-minute-restart.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    activated = datetime(2026, 10, 14, 2, 55, 37)
    async with factory() as session:
        stream = await repo.create_stream(
            session, title="Friends", chat_id=-100, invite_link="https://t.me/+test", timezone="Europe/Moscow"
        )
        person = await repo.approve_participant(session, stream.id, "firstfriend")
        person.tg_user_id = 123
        await session.commit()
        await lessons.activate_course(session, person, activated)
        stream_id, person_id = stream.id, person.id
    await engine.dispose()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    async with factory() as session:
        stream = await session.get(models.Stream, stream_id)
        person = await session.get(models.Participant, person_id)
        await send(session, stream, person, bot, tmp_path, activated + timedelta(minutes=9, seconds=59))
        bot.send_message.assert_not_called()
        await send(session, stream, person, bot, tmp_path, activated + timedelta(minutes=10))
        bot.send_message.assert_awaited_once()
        assert (await session.get(models.CourseRun, person_id)).activated_at == activated
    await engine.dispose()


async def test_owner_status_shows_real_first_due_date_when_ten_minutes_cross_midnight(
    session,
    stream,
    participant,
    monkeypatch,
):
    from bot.config import settings
    from bot.handlers import admin

    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 20, 55))
    monkeypatch.setattr(admin, "SessionFactory", lambda: session)
    monkeypatch.setattr(settings, "owner_tg_id", 42)
    message = SimpleNamespace(from_user=SimpleNamespace(id=42), answer=AsyncMock())
    await admin.course_status(message, SimpleNamespace(args=str(participant.tg_user_id)))
    assert "15.10.2026, 00:05 МСК" in message.answer.call_args.args[0]


@pytest.mark.parametrize("return_minutes", [5, 15])
async def test_registered_first_timer_pauses_on_leave_and_return_keeps_original_deadline(
    session,
    stream,
    participant,
    tmp_path,
    monkeypatch,
    return_minutes,
):
    from bot.scheduler import jobs
    from bot.services import onboarding

    activated = datetime(2026, 10, 14, 4, 55)
    await lessons.activate_course(session, participant, activated)
    await onboarding.process_join(
        session,
        stream=stream,
        tg_user_id=participant.tg_user_id,
        username=participant.username,
        full_name="Друг",
        grace_hours=24,
        occurred_at=activated,
    )
    monkeypatch.setattr(jobs, "SessionFactory", lambda: session)
    current_time = [activated]
    monkeypatch.setattr(jobs, "_now_naive_utc", lambda: current_time[0])
    monkeypatch.setattr(lessons, "CATALOG_PATH", catalog_file(tmp_path))
    registered = []
    scheduler = SimpleNamespace(add_job=lambda *args, **kwargs: registered.append((args, kwargs)))
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    await jobs.register_first_lesson_job(scheduler, bot, participant.id)
    assert registered[0][0][1].run_date.replace(tzinfo=None) == activated + timedelta(minutes=10)
    await onboarding.process_leave(
        session,
        stream=stream,
        tg_user_id=participant.tg_user_id,
        occurred_at=activated + timedelta(minutes=1),
    )
    # While still left: inspect the due instant for a late return, or a minute
    # before an early return. Keep the simulated event order chronological.
    current_time[0] = activated + timedelta(minutes=min(10, return_minutes - 1))
    await jobs.job_lesson(bot, stream.id, participant.id)
    bot.send_message.assert_not_called()
    await onboarding.process_join(
        session,
        stream=stream,
        tg_user_id=participant.tg_user_id,
        username=participant.username,
        full_name="Друг",
        grace_hours=24,
        occurred_at=activated + timedelta(minutes=return_minutes),
    )
    assert (await session.get(models.CourseRun, participant.id)).activated_at == activated
    current_time[0] = activated + timedelta(minutes=return_minutes)
    await jobs.job_lesson(bot, stream.id, participant.id)
    if return_minutes < 10:
        bot.send_message.assert_not_called()
        current_time[0] = activated + timedelta(minutes=10)
        await jobs.job_lesson(bot, stream.id, participant.id)
    bot.send_message.assert_awaited_once()


@pytest.mark.parametrize(
    "paused_status",
    [
        models.ParticipantStatus.LEFT,
        models.ParticipantStatus.KICKED,
        models.ParticipantStatus.REVOKED,
    ],
)
@pytest.mark.parametrize("ambiguous", [False, True])
async def test_owner_status_explains_access_recovery_before_resuming_course(
    session,
    stream,
    participant,
    monkeypatch,
    paused_status,
    ambiguous,
):
    from bot.config import settings
    from bot.handlers import admin

    await lessons.activate_course(session, participant, datetime(2026, 10, 14, 7))
    participant.status = paused_status
    if ambiguous:
        session.add(
            models.LessonDelivery(
                stream_id=stream.id, participant_id=participant.id, lesson_day=1, status="uncertain"
            )
        )
    await session.commit()
    monkeypatch.setattr(admin, "SessionFactory", lambda: session)
    monkeypatch.setattr(settings, "owner_tg_id", 42)
    message = SimpleNamespace(from_user=SimpleNamespace(id=42), answer=AsyncMock())
    await admin.course_status(message, SimpleNamespace(args=str(participant.tg_user_id)))
    text = message.answer.call_args.args[0]
    if paused_status == models.ParticipantStatus.LEFT:
        assert "вернуться в чат" in text
    else:
        assert "организатор" in text and f"/approve {participant.tg_user_id}" in text
    if ambiguous:
        assert "/resolve_lesson" in text


def test_start_refusal_directs_user_to_the_organizer():
    from bot.services import texts

    text = texts.start_refused()
    assert "организатора" in text and "@username" in text
    assert "того, кто тебя позвал" not in text
