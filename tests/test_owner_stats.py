from datetime import date
from types import SimpleNamespace

from bot.config import settings
from bot.db import repo
from bot.db.models import ParticipantStatus, Penalty, PenaltyStatus
from bot.handlers import admin


async def test_stats_excludes_chat_creator_but_keeps_other_participants(session, stream, monkeypatch):
    monkeypatch.setattr(settings, "owner_tg_id", 42)
    monkeypatch.setattr(admin, "SessionFactory", lambda: session)
    for user_id, username in [(101, "chatowner"), (202, "friend")]:
        person = await repo.approve_participant(session, stream.id, username)
        person.tg_user_id = user_id
        person.status = ParticipantStatus.ACTIVE
        session.add(Penalty(
            stream_id=stream.id, participant_id=person.id,
            issued_for_date=date(2026, 10, 14), status=PenaltyStatus.OPEN,
        ))
    await session.commit()
    replies = []

    async def answer(text):
        replies.append(text)

    class Bot:
        async def get_chat_administrators(self, chat_id):
            assert chat_id == stream.chat_id
            return [
                SimpleNamespace(status="creator", user=SimpleNamespace(id=101)),
                SimpleNamespace(status="administrator", user=SimpleNamespace(id=42)),
            ]

    message = SimpleNamespace(from_user=SimpleNamespace(id=42), answer=answer)
    await admin.stats(message, Bot())
    assert "Активных участников: 1" in replies[0]
    assert "Открытых штрафов: 1" in replies[0]


async def test_stats_reports_role_lookup_failure_instead_of_including_owner(session, stream, monkeypatch):
    monkeypatch.setattr(settings, "owner_tg_id", 42)
    monkeypatch.setattr(admin, "SessionFactory", lambda: session)
    replies = []

    async def answer(text):
        replies.append(text)

    class Bot:
        async def get_chat_administrators(self, chat_id):
            raise RuntimeError("role lookup unavailable")

    message = SimpleNamespace(from_user=SimpleNamespace(id=42), answer=answer)
    await admin.stats(message, Bot())
    assert "Не удалось проверить владельца чата" in replies[0]
    assert "Активных участников" not in replies[0]
