from datetime import datetime, timezone
from types import SimpleNamespace

from bot.config import settings
from bot.db import repo
from bot.db.models import ParticipantStatus
from bot.handlers import admin, group
from bot.services import texts


async def test_unknown_join_notifies_owner_without_removing_user(session, stream, monkeypatch):
    monkeypatch.setattr(settings, "owner_tg_id", 42)
    monkeypatch.setattr(group, "SessionFactory", lambda: session)
    calls = []

    class Bot:
        async def send_message(self, chat_id, text):
            calls.append((chat_id, text))

        async def ban_chat_member(self, *_):
            raise AssertionError("Unknown friends must not be auto-kicked")

    user = SimpleNamespace(id=777, username="guest", full_name="Гость", is_bot=False)
    message = SimpleNamespace(
        chat=SimpleNamespace(id=stream.chat_id, title=stream.title),
        new_chat_members=[user],
        date=datetime.now(timezone.utc),
    )
    await group.on_new_members(message, Bot())
    assert len(calls) == 1
    assert calls[0][0] == settings.owner_tg_id
    assert "Гость" in calls[0][1]


async def test_failed_unban_keeps_kicked_status(session, stream, monkeypatch):
    monkeypatch.setattr(settings, "owner_tg_id", 42)
    monkeypatch.setattr(admin, "SessionFactory", lambda: session)
    p = await repo.approve_participant(session, stream.id, "friend")
    p.tg_user_id = 123
    p.status = ParticipantStatus.KICKED
    await session.commit()
    replies = []

    class Bot:
        async def unban_chat_member(self, *_):
            raise RuntimeError("missing permission")

    async def answer(text):
        replies.append(text)

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=settings.owner_tg_id), answer=answer,
    )
    await admin.approve(message, SimpleNamespace(args="@friend"), Bot())
    assert p.status == ParticipantStatus.KICKED
    assert "не смог" in replies[-1].lower()


def test_stream_title_escaped_in_html_messages():
    assert "&lt;друзья&gt;" in texts.start_invited("Ира", "ira", "<друзья>", "https://t.me/+x")
    assert "&lt;друзья&gt;" in texts.stream_created("<друзья>", "https://t.me/+x")
