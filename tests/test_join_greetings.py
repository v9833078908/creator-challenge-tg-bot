"""Group greetings follow membership history, while introductions remain informal."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from bot.db import repo
from bot.db.models import DailyReport, MembershipPeriod, ParticipantStatus, Penalty, PenaltyStatus
from bot.handlers import group
from bot.services import onboarding, texts
from tests.test_owner_handlers import RoleBot


class GroupMessage:
    def __init__(self, stream, user):
        self.chat = SimpleNamespace(id=stream.chat_id, title=stream.title)
        self.new_chat_members = [user]
        self.date = datetime.now(timezone.utc)
        self.answers = []

    async def answer(self, text):
        self.answers.append(text)


def member(username="friend", full_name="Друг"):
    return SimpleNamespace(id=101, username=username, full_name=full_name, is_bot=False)


async def first_join(session, stream, monkeypatch, user=None):
    monkeypatch.setattr(group, "SessionFactory", lambda: session)
    await repo.approve_participant(session, stream.id, "friend")
    await session.commit()
    message = GroupMessage(stream, user or member())
    await group.on_new_members(message, RoleBot(creator_id=999))
    return message


async def test_first_entry_requests_about_me_and_goal(session, stream, monkeypatch):
    message = await first_join(session, stream, monkeypatch)
    assert message.answers == [
        "Да это же @friend, привет! Представься с хештегом #обомне и напиши свою #цель "
        "чтобы другие участники познакомились с тобой 👋"
    ]
    assert stream.invite_link == "https://t.me/+TestInvite"


async def test_return_by_same_invite_after_username_change_welcomes_back(
    session, stream, monkeypatch,
):
    message = await first_join(session, stream, monkeypatch)
    user = member(username="newname")
    leave = SimpleNamespace(
        chat=message.chat, left_chat_member=user, from_user=user,
        date=message.date + timedelta(minutes=1),
    )
    await group.on_left_member(leave, RoleBot(creator_id=999))
    invited = await onboarding.process_start(
        session, tg_user_id=user.id, username=user.username, full_name=user.full_name,
    )
    assert invited.kind == "invited"
    assert invited.stream.invite_link == "https://t.me/+TestInvite"
    returning = GroupMessage(stream, user)
    returning.date = leave.date + timedelta(minutes=1)
    await group.on_new_members(returning, RoleBot(creator_id=999))
    assert returning.answers == ["Оооо, да это же @newname! С возвращением!"]
    participant = await repo.get_participant_by_tg_id(session, stream.id, user.id)
    assert participant.status == ParticipantStatus.ACTIVE
    assert participant.username == "newname"
    assert await session.scalar(select(func.count()).select_from(MembershipPeriod)) == 2


async def test_duplicate_join_does_not_repeat_greeting_or_membership(session, stream, monkeypatch):
    message = await first_join(session, stream, monkeypatch)
    await group.on_new_members(message, RoleBot(creator_id=999))
    assert len(message.answers) == 1
    assert await session.scalar(select(func.count()).select_from(MembershipPeriod)) == 1


async def test_return_with_open_penalty_uses_existing_debt_reminder(session, stream, monkeypatch):
    message = await first_join(session, stream, monkeypatch)
    participant = await repo.get_participant_by_tg_id(session, stream.id, 101)
    penalty = Penalty(
        stream_id=stream.id, participant_id=participant.id,
        issued_for_date=date(2026, 9, 19), status=PenaltyStatus.OPEN,
        due_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
    )
    session.add(penalty)
    await session.commit()
    left_at = message.date + timedelta(minutes=1)
    await onboarding.process_leave(
        session, stream=stream, tg_user_id=101, occurred_at=left_at.replace(tzinfo=None),
    )
    message.date = left_at + timedelta(minutes=1)
    message.answers.clear()
    await group.on_new_members(message, RoleBot(creator_id=999))
    assert message.answers == [texts.welcome_back_with_penalties("Друг", "friend", 1)]
    assert "#штраф" in message.answers[0]
    assert "#обомне" not in message.answers[0]
    assert penalty.status == PenaltyStatus.OPEN
    assert penalty.due_at > datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.mark.parametrize(
    ("full_name", "username", "safe_name"),
    [("<Друг & Co>", None, "&lt;Друг &amp; Co&gt;"), ("", None, "друг"),
     ("Друг", "friend<name", "@friend&lt;name")],
)
def test_greetings_escape_display_name_without_username(full_name, username, safe_name):
    welcome = texts.welcome_joined(full_name, username, first_join=True)
    assert welcome.startswith(f"Да это же {safe_name}, привет!")
    assert "#обомне" in welcome
    assert "#цель" in welcome
    assert "#intro" not in welcome
    assert texts.welcome_joined(full_name, username) == (
        f"Оооо, да это же {safe_name}! С возвращением!"
    )


async def test_about_me_and_goal_do_not_count_as_report(session, stream, monkeypatch):
    message = await first_join(session, stream, monkeypatch)
    message.text = "#обомне Я автор канала. #цель — писать каждый день."
    message.from_user = member()
    await group.on_group_message(message, RoleBot(creator_id=999))
    assert len(message.answers) == 1
    assert await session.scalar(select(func.count()).select_from(DailyReport)) == 0
    assert await session.scalar(select(func.count()).select_from(Penalty)) == 0
