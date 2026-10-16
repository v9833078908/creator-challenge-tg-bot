"""The actual Telegram creator never participates through group handlers."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiogram.enums import ChatMemberStatus
from sqlalchemy import func, select

from bot.db import repo
from bot.db.models import DailyReport, ParticipantStatus, Penalty, PenaltyStatus
from bot.handlers import group


class RoleBot:
    def __init__(self, creator_id=101, *, lookup_fails=False):
        self.creator_id = creator_id
        self.lookup_fails = lookup_fails
        self.sent = []

    async def get_chat_administrators(self, chat_id):
        if self.lookup_fails:
            raise RuntimeError("Telegram unavailable")
        return [
            SimpleNamespace(status=ChatMemberStatus.CREATOR, user=SimpleNamespace(id=self.creator_id)),
            SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR, user=SimpleNamespace(id=202)),
        ]

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


class Message:
    def __init__(self, stream, user_id=101, content=None):
        self.chat = SimpleNamespace(id=stream.chat_id, title=stream.title)
        self.from_user = SimpleNamespace(
            id=user_id, username="friend", full_name="Друг", is_bot=False,
        )
        self.new_chat_members = [self.from_user]
        self.left_chat_member = self.from_user
        self.date = datetime.now(timezone.utc)
        self.message_id = 123
        self.text = content
        self.caption = None
        self.forward_origin = None
        self.answers = []

    async def answer(self, text):
        self.answers.append(text)

    async def reply(self, text):
        self.answers.append(text)


async def test_creator_join_is_silent_without_activation(session, stream, monkeypatch):
    monkeypatch.setattr(group, "SessionFactory", lambda: session)
    participant = await repo.approve_participant(session, stream.id, "friend")
    await session.commit()
    bot = RoleBot()
    message = Message(stream)
    await group.on_new_members(message, bot)
    assert participant.status == ParticipantStatus.APPROVED
    assert participant.tg_user_id is None
    assert not message.answers
    assert not bot.sent


async def test_unknown_creator_does_not_notify_organizer(session, stream, monkeypatch):
    monkeypatch.setattr(group, "SessionFactory", lambda: session)
    monkeypatch.setattr(group.settings, "owner_tg_id", 999)
    bot = RoleBot()
    message = Message(stream)
    await group.on_new_members(message, bot)
    assert not bot.sent
    assert not message.answers


async def test_creator_leave_does_not_change_participant(session, stream, monkeypatch):
    monkeypatch.setattr(group, "SessionFactory", lambda: session)
    participant = await repo.approve_participant(session, stream.id, "friend")
    participant.tg_user_id = 101
    participant.status = ParticipantStatus.ACTIVE
    await session.commit()
    await group.on_left_member(Message(stream), RoleBot())
    assert participant.status == ParticipantStatus.ACTIVE


@pytest.mark.parametrize("content", ["#пост https://t.me/my_channel/1", "#штраф"])
async def test_creator_hashtags_are_silent_and_preserve_data(
    session, stream, monkeypatch, content,
):
    monkeypatch.setattr(group, "SessionFactory", lambda: session)
    participant = await repo.approve_participant(session, stream.id, "friend")
    participant.tg_user_id = 101
    participant.status = ParticipantStatus.ACTIVE
    session.add(Penalty(
        stream_id=stream.id, participant_id=participant.id,
        issued_for_date=datetime.now().date(), status=PenaltyStatus.OPEN,
        due_at=datetime.now(),
    ))
    await session.commit()
    message = Message(stream, content=content)
    await group.on_group_message(message, RoleBot())
    assert not message.answers
    assert await session.scalar(select(func.count()).select_from(DailyReport)) == 0
    assert await session.scalar(select(Penalty.status)) == PenaltyStatus.OPEN


async def test_noncreator_administrator_still_joins(session, stream, monkeypatch):
    monkeypatch.setattr(group, "SessionFactory", lambda: session)
    participant = await repo.approve_participant(session, stream.id, "friend")
    await session.commit()
    message = Message(stream, user_id=202)
    await group.on_new_members(message, RoleBot())
    assert participant.status == ParticipantStatus.ACTIVE
    assert "#обомне" in message.answers[0]


async def test_owner_lookup_failure_does_not_activate_or_notify(session, stream, monkeypatch):
    monkeypatch.setattr(group, "SessionFactory", lambda: session)
    participant = await repo.approve_participant(session, stream.id, "friend")
    await session.commit()
    message = Message(stream)
    bot = RoleBot(lookup_fails=True)
    await group.on_new_members(message, bot)
    assert participant.status == ParticipantStatus.APPROVED
    assert not message.answers
    assert not bot.sent


@pytest.mark.parametrize("content", ["#пост https://t.me/my_channel/1", "#штраф"])
async def test_ordinary_admin_still_reports_even_if_configured_organizer(
    session, stream, monkeypatch, content,
):
    monkeypatch.setattr(group, "SessionFactory", lambda: session)
    monkeypatch.setattr(group.settings, "owner_tg_id", 202)
    participant = await repo.approve_participant(session, stream.id, "friend")
    participant.tg_user_id = 202
    participant.status = ParticipantStatus.ACTIVE
    penalty = Penalty(
        stream_id=stream.id, participant_id=participant.id,
        issued_for_date=datetime.now().date(), status=PenaltyStatus.OPEN,
        due_at=datetime.now(),
    )
    session.add(penalty)
    await session.commit()
    message = Message(stream, user_id=202, content=content)
    message.date = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0)
    await group.on_group_message(message, RoleBot())
    assert len(message.answers) == 1
    if content.startswith("#пост"):
        assert await session.scalar(select(func.count()).select_from(DailyReport)) == 1
    else:
        assert penalty.status == PenaltyStatus.PAID


async def test_owner_lookup_failure_does_not_handle_report(session, stream, monkeypatch):
    monkeypatch.setattr(group, "SessionFactory", lambda: session)
    message = Message(stream, content="#пост https://t.me/my_channel/1")
    await group.on_group_message(message, RoleBot(lookup_fails=True))
    assert not message.answers
    assert await session.scalar(select(func.count()).select_from(DailyReport)) == 0


async def test_chat_owner_resolution_follows_ownership_transfer():
    from bot.services.chat_roles import get_chat_owner_id

    bot = RoleBot(creator_id=101)
    assert await get_chat_owner_id(bot, -123) == 101
    bot.creator_id = 202
    assert await get_chat_owner_id(bot, -123) == 202


async def test_chat_owner_resolution_rejects_missing_creator():
    from bot.services.chat_roles import get_chat_owner_id

    class NoCreatorBot:
        async def get_chat_administrators(self, chat_id):
            return [SimpleNamespace(
                status=ChatMemberStatus.ADMINISTRATOR, user=SimpleNamespace(id=202),
            )]

    with pytest.raises(RuntimeError, match="no creator"):
        await get_chat_owner_id(NoCreatorBot(), -123)


async def test_report_total_excludes_active_creator_without_changing_owner_history(
    session, stream, monkeypatch,
):
    monkeypatch.setattr(group, "SessionFactory", lambda: session)
    monkeypatch.setattr(group.texts.random, "randrange", lambda _: 1)
    owner = await repo.approve_participant(session, stream.id, "creator")
    owner.tg_user_id = 101
    owner.status = ParticipantStatus.ACTIVE
    owner.joined_at = datetime(2026, 9, 24, 11, 30)
    participant = await repo.approve_participant(session, stream.id, "friend")
    participant.tg_user_id = 202
    participant.status = ParticipantStatus.ACTIVE
    penalty = Penalty(
        stream_id=stream.id, participant_id=owner.id,
        issued_for_date=datetime.now().date(), status=PenaltyStatus.OPEN,
        due_at=datetime.now(),
    )
    session.add(penalty)
    await session.commit()

    class CountingRoleBot(RoleBot):
        def __init__(self):
            super().__init__()
            self.role_queries = 0

        async def get_chat_administrators(self, chat_id):
            self.role_queries += 1
            return await super().get_chat_administrators(chat_id)

    bot = CountingRoleBot()
    message = Message(stream, user_id=202, content="#пост https://t.me/my_channel/1")
    message.date = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0)
    await group.on_group_message(message, bot)
    assert len(message.answers) == 1
    assert message.answers[0].startswith("№1 из 1 ")
    assert bot.role_queries == 1
    assert owner.status == ParticipantStatus.ACTIVE
    assert owner.joined_at == datetime(2026, 9, 24, 11, 30)
    assert penalty.status == PenaltyStatus.OPEN
    assert await session.scalar(
        select(func.count()).select_from(DailyReport).where(DailyReport.participant_id == owner.id)
    ) == 0
