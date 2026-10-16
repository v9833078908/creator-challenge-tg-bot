"""Accepted posts keep Content Hero's usual reply with an occasional encouragement."""

import random
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db import repo
from bot.db.models import DailyReport, ParticipantStatus
from bot.handlers import group
from bot.services import texts


@pytest.mark.parametrize("draw", range(5))
def test_accepted_reply_keeps_original_proportions_and_dynamic_counts(monkeypatch, draw):
    choose = Mock(return_value=draw)
    monkeypatch.setattr(random, "randrange", choose)

    reply = texts.report_accepted(3, 8, "🔥")

    expected = "вау, справилось 3 из 8 героев! ⚡️" if draw == 0 else "№3 из 8 🔥"
    assert reply == expected
    choose.assert_called_once_with(5)


async def test_duplicate_post_does_not_choose_another_encouragement(session, stream, monkeypatch):
    monkeypatch.setattr(group, "SessionFactory", async_sessionmaker(
        session.bind, expire_on_commit=False,
    ))
    participant = await repo.approve_participant(session, stream.id, "friend")
    participant.tg_user_id = 202
    participant.status = ParticipantStatus.ACTIVE
    await session.commit()
    choose = Mock(return_value=0)
    monkeypatch.setattr(random, "randrange", choose)
    bot = SimpleNamespace(get_chat_administrators=AsyncMock(return_value=[
        SimpleNamespace(status="creator", user=SimpleNamespace(id=101)),
    ]))
    message = SimpleNamespace(
        chat=SimpleNamespace(id=stream.chat_id),
        from_user=SimpleNamespace(id=202, is_bot=False),
        text="#пост https://t.me/my_channel/1", caption=None, forward_origin=None,
        date=datetime(2026, 10, 15, 12, tzinfo=timezone.utc), message_id=123,
        reply=AsyncMock(),
    )

    await group.on_group_message(message, bot)
    message.message_id = 124
    await group.on_group_message(message, bot)

    assert [call.args[0] for call in message.reply.await_args_list] == [
        "вау, справилось 1 из 1 героев! ⚡️",
        texts.report_already_counted(),
    ]
    choose.assert_called_once_with(5)
    assert await session.scalar(select(func.count()).select_from(DailyReport)) == 1
