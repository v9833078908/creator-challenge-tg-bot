"""Валидация #пост: текст ≥400 симв. / ссылка t.me / форвард. Античит: edited не ловим."""

from bot.config import settings
from bot.services import reports

MIN = settings.min_post_chars  # 400


def test_hashtag_required():
    assert reports.validate_report("просто текст " + "а" * 500, is_forward=False, min_chars=MIN) is None


def test_hashtag_case_insensitive():
    r = reports.validate_report("#ПОСТ https://t.me/channel/123", is_forward=False, min_chars=MIN)
    assert r is not None and r.kind == "link"


def test_link_short_ok():
    r = reports.validate_report("#пост https://t.me/mychannel/42", is_forward=False, min_chars=MIN)
    assert r is not None
    assert r.kind == "link"


def test_link_without_scheme_ok():
    r = reports.validate_report("#пост t.me/mychannel/42", is_forward=False, min_chars=MIN)
    assert r is not None and r.kind == "link"


def test_forward_ok():
    r = reports.validate_report("#пост", is_forward=True, min_chars=MIN)
    assert r is not None and r.kind == "forward"


def test_long_text_ok():
    content = "#пост сегодня пишу о важном. " + "Текст. " * 60
    r = reports.validate_report(content, is_forward=False, min_chars=MIN)
    assert r is not None and r.kind == "text"
    assert r.char_count >= MIN


def test_short_text_rejected():
    r = reports.validate_report("#пост коротко", is_forward=False, min_chars=MIN)
    assert r is None


def test_empty_content_rejected():
    assert reports.validate_report(None, is_forward=False, min_chars=MIN) is None
    assert reports.validate_report("", is_forward=False, min_chars=MIN) is None


def test_day_emoji_stable_and_rotating():
    import datetime as dt

    d = dt.date(2026, 9, 20)
    assert reports.day_emoji(d) == reports.day_emoji(d)
    assert reports.day_emoji(d) != "" and reports.day_emoji(d) in reports.DAY_EMOJIS
    # ротация по дате
    others = {reports.day_emoji(d + dt.timedelta(days=i)) for i in range(len(reports.DAY_EMOJIS))}
    assert len(others) == len(reports.DAY_EMOJIS)
