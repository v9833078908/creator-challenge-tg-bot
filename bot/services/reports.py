"""Валидация отчётов #пост. Чистые функции — без БД и Telegram API."""

import re
from dataclasses import dataclass

POST_HASHTAG_RE = re.compile(r"#пост\b", re.IGNORECASE)
PENALTY_HASHTAG_RE = re.compile(r"#штраф\b", re.IGNORECASE)
TG_LINK_RE = re.compile(r"(?:https?://)?(?:www\.)?t(?:elegram)?\.me/\S+", re.IGNORECASE)


@dataclass
class ReportCandidate:
    kind: str  # link | forward | text
    char_count: int


def has_post_hashtag(content: str | None) -> bool:
    return bool(content) and bool(POST_HASHTAG_RE.search(content))


def has_penalty_hashtag(content: str | None) -> bool:
    return bool(content) and bool(PENALTY_HASHTAG_RE.search(content))


def validate_report(
    content: str | None, *, is_forward: bool, min_chars: int
) -> ReportCandidate | None:
    """Правила зачёта (как у оригинала):

    - текст/подпись с #пост длиной >= min_chars  → kind=text
    - ссылка на пост t.me/...                    → kind=link (длина не проверяется)
    - форвард из канала с #пост в подписи        → kind=forward
    - иначе — None (не засчитано)
    """
    content = content or ""
    if not has_post_hashtag(content):
        return None

    if is_forward:
        return ReportCandidate(kind="forward", char_count=len(content))
    if TG_LINK_RE.search(content):
        return ReportCandidate(kind="link", char_count=len(content))
    if len(content) >= min_chars:
        return ReportCandidate(kind="text", char_count=len(content))
    return None


# Эмодзи дня — свой набор, ротация по дате (как у оригинала: ☘️🙌✊🌋🍩…)
DAY_EMOJIS = [
    "☘️", "🙌", "✊", "🌋", "🍩", "☕️", "🍉", "🧿", "🔥", "⚡️",
    "🚀", "🎯", "🧠", "🎩", "🦄", "🌊", "🍀", "🎸", "🏆", "💎",
    "🌵", "🧩", "🎨", "🏄", "🌙", "🍋", "🥑", "🎲", "🛡", "🌈",
]


def day_emoji(report_date) -> str:
    return DAY_EMOJIS[report_date.toordinal() % len(DAY_EMOJIS)]
