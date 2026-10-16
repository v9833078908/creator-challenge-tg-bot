from datetime import date
from types import SimpleNamespace

import pytest

from bot.services import texts


@pytest.mark.parametrize("count", [1, 21])
def test_return_with_penalty_describes_one_obligation(monkeypatch, count):
    monkeypatch.setattr(texts.settings, "penalty_grace_hours", 24)

    assert texts.welcome_back_with_penalties("Имя", "newname", count) == (
        "Оооо, да это же @newname! С возвращением!\n"
        "У тебя остался неотработанный штраф — у тебя 24 часа, "
        "чтобы закинуть #штраф, иначе вылет."
    )


def test_return_with_penalty_escapes_name_without_username():
    message = texts.welcome_back_with_penalties("Имя <друга>", None, 1)

    assert message.startswith("Оооо, да это же Имя &lt;друга&gt;! С возвращением!\n")


def test_digest_without_new_penalties_does_not_claim_everyone_reported():
    message = texts.digest(date(2026, 10, 15), total=1, reported=[], penalized=[])

    assert message == (
        "#дайджест 👑 Creator Hero\n"
        "📆 15/10/26: участников — 1\n"
        "✅ Написали пост — 0. Тихий день 🌫\n"
        "💥 Новых штрафов — 0."
    )


def test_digest_labels_new_penalties():
    participant = SimpleNamespace(full_name="Друг", username="friend", tg_user_id=101)
    message = texts.digest(date(2026, 10, 15), total=1, reported=[], penalized=[participant])

    assert message.endswith('💥 Новых штрафов — 1: <a href="tg://user?id=101">Друг</a>')
