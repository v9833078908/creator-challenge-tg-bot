"""Invite delivery uses real onboarding/database with a fake Telegram boundary."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import GetChat
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db import repo
from bot.db.models import Base, ParticipantStatus
from bot.handlers import dm
from bot.scheduler import jobs
from bot.services import invites

STALE_LINK = "https://t.me/+UnmanagedInvite"
HEALTHY_LINK = "https://t.me/+CurrentPrimary"


@pytest_asyncio.fixture
async def invite_context(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'invites.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        stream = await repo.create_stream(
            session,
            title="Friends",
            chat_id=-1001234567890,
            invite_link=STALE_LINK,
            timezone="Europe/Moscow",
        )
        participant = await repo.approve_participant(session, stream.id, "Elis_efi")
        await session.commit()
    monkeypatch.setattr(dm, "SessionFactory", factory)
    monkeypatch.setattr(invites, "SessionFactory", factory)
    monkeypatch.setattr(jobs, "register_first_lesson_job", AsyncMock())
    monkeypatch.setattr(jobs, "job_lesson", AsyncMock())
    yield SimpleNamespace(factory=factory, stream=stream, participant=participant)
    await engine.dispose()


def start_message(username="Elis_efi", user_id=123):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, username=username, full_name="Елизавета Ефимова"),
        answer=AsyncMock(),
    )


def fake_bot():
    return SimpleNamespace(
        id=999,
        get_chat=AsyncMock(return_value=SimpleNamespace(invite_link=HEALTHY_LINK)),
        edit_chat_invite_link=AsyncMock(return_value=SimpleNamespace(
            invite_link=STALE_LINK, creator=SimpleNamespace(id=999), is_revoked=False,
            creates_join_request=False, expire_date=None, member_limit=None,
        )),
        create_chat_invite_link=AsyncMock(),
        export_chat_invite_link=AsyncMock(),
        revoke_chat_invite_link=AsyncMock(),
        unban_chat_member=AsyncMock(),
    )


def assert_no_invite_mutations(bot):
    bot.edit_chat_invite_link.assert_not_awaited()
    bot.create_chat_invite_link.assert_not_awaited()
    bot.export_chat_invite_link.assert_not_awaited()
    bot.revoke_chat_invite_link.assert_not_awaited()
    bot.unban_chat_member.assert_not_awaited()


@pytest.mark.parametrize(
    "status", [ParticipantStatus.APPROVED, ParticipantStatus.ACTIVE, ParticipantStatus.LEFT],
)
async def test_start_sends_current_telegram_invite_instead_of_stale_stored_url(invite_context, status):
    async with invite_context.factory() as session:
        person = await session.get(type(invite_context.participant), invite_context.participant.id)
        person.status = status
        await session.commit()
    bot = fake_bot()
    message = start_message()

    await dm.cmd_start(message, bot)

    assert STALE_LINK not in message.answer.call_args.args[0]
    assert HEALTHY_LINK in message.answer.call_args.args[0]
    bot.get_chat.assert_awaited_once_with(chat_id=invite_context.stream.chat_id)
    assert_no_invite_mutations(bot)
    async with invite_context.factory() as session:
        assert (await repo.get_stream(session, invite_context.stream.id)).invite_link == HEALTHY_LINK


async def test_repeated_starts_preserve_current_invite_and_course_schedule(invite_context):
    bot = fake_bot()
    first, second = start_message(), start_message()

    await dm.cmd_start(first, bot)
    await dm.cmd_start(second, bot)

    assert HEALTHY_LINK in first.answer.call_args.args[0]
    assert HEALTHY_LINK in second.answer.call_args.args[0]
    assert bot.get_chat.await_count == 2
    assert "не начинает курс заново" in second.answer.call_args.args[0]
    assert_no_invite_mutations(bot)


async def test_current_stored_primary_is_preserved(invite_context):
    bot = fake_bot()
    bot.get_chat.return_value = SimpleNamespace(invite_link=STALE_LINK)

    assert await invites.ensure_invite_link(bot, invite_context.stream.id) == STALE_LINK

    assert_no_invite_mutations(bot)


@pytest.mark.parametrize("status", [None, ParticipantStatus.REVOKED, ParticipantStatus.KICKED])
async def test_unapproved_starts_never_lookup_disclose_or_unban(invite_context, status):
    if status is not None:
        async with invite_context.factory() as session:
            person = await session.get(type(invite_context.participant), invite_context.participant.id)
            person.status = status
            await session.commit()
    bot = fake_bot()
    message = start_message(username="stranger" if status is None else "Elis_efi")

    await dm.cmd_start(message, bot)

    text = message.answer.call_args.args[0]
    assert STALE_LINK not in text and HEALTHY_LINK not in text
    bot.get_chat.assert_not_awaited()
    assert_no_invite_mutations(bot)


@pytest.mark.parametrize(
    "error_kind", ["network", "retry", "forbidden", "admin_required", "bad_request", "os", "timeout"],
)
async def test_lookup_failure_never_sends_stale_url_or_mutates_invites(invite_context, error_kind):
    method = GetChat(chat_id=invite_context.stream.chat_id)
    error = {
        "network": TelegramNetworkError(method=method, message="Network unavailable"),
        "retry": TelegramRetryAfter(method=method, message="Too Many Requests", retry_after=30),
        "forbidden": TelegramForbiddenError(method=method, message="Forbidden"),
        "admin_required": TelegramBadRequest(method=method, message="Bad Request: CHAT_ADMIN_REQUIRED"),
        "bad_request": TelegramBadRequest(method=method, message="Bad Request: unexpected failure"),
        "os": OSError("Connection lost"),
        "timeout": TimeoutError("Timed out"),
    }[error_kind]
    bot = fake_bot()
    bot.get_chat.side_effect = error
    message = start_message()

    await dm.cmd_start(message, bot)

    text = message.answer.call_args.args[0]
    assert STALE_LINK not in text and HEALTHY_LINK not in text
    assert "/start" in text and "позже" in text
    assert_no_invite_mutations(bot)
    jobs.register_first_lesson_job.assert_awaited_once()
    async with invite_context.factory() as session:
        assert (await repo.get_stream(session, invite_context.stream.id)).invite_link == STALE_LINK


@pytest.mark.parametrize("primary", [None, ""])
async def test_missing_primary_never_falls_back_to_stored_invite(invite_context, primary):
    bot = fake_bot()
    bot.get_chat.return_value = SimpleNamespace(invite_link=primary)
    message = start_message()

    await dm.cmd_start(message, bot)

    assert STALE_LINK not in message.answer.call_args.args[0]
    assert "позже" in message.answer.call_args.args[0]
    assert_no_invite_mutations(bot)
    async with invite_context.factory() as session:
        assert (await repo.get_stream(session, invite_context.stream.id)).invite_link == STALE_LINK


async def test_concurrent_requests_converge_on_current_primary_without_rotation(invite_context):
    bot = fake_bot()
    results = await asyncio.gather(*(
        invites.ensure_invite_link(bot, invite_context.stream.id) for _ in range(5)
    ))

    assert results == [HEALTHY_LINK] * 5
    assert_no_invite_mutations(bot)
    async with invite_context.factory() as session:
        assert (await repo.get_stream(session, invite_context.stream.id)).invite_link == HEALTHY_LINK
