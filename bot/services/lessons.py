"""Personal optional lessons after private activation; ambiguous sends fail closed."""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from weakref import WeakValueDictionary
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import CourseRun, LessonDelivery, Participant, ParticipantStatus, Stream, utcnow

logger = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")
_participant_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "lesson-catalog.json"


@dataclass(frozen=True)
class Lesson:
    day: int
    lesson_id: str
    text: str
    button_text: str
    article_url: str


def load_catalog(path: Path | None = None) -> list[Lesson]:
    """Never deliver a partially published catalog or a placeholder URL."""
    data = json.loads((path or CATALOG_PATH).read_text(encoding="utf-8"))
    items = data.get("items", [])
    if data.get("brand") != "Creator Hero" or data.get("count") != 33 or len(items) != 33:
        raise ValueError("Каталог должен содержать 33 урока Creator Hero")
    lessons = []
    ids = set()
    for day, item in enumerate(items, 1):
        url = item.get("article_url")
        if not isinstance(url, str) or urlparse(url).scheme != "https" or not urlparse(url).hostname:
            raise ValueError(f"Урок {day}: нужен публичный HTTPS URL")
        lesson_id = item.get("lesson_id")
        if item.get("day") != day or not isinstance(lesson_id, str) or not lesson_id or lesson_id in ids:
            raise ValueError("Нужны уникальные ID и последовательные дни 1–33")
        if not isinstance(item.get("text"), str) or not item["text"].strip() or len(item["text"]) > 4000:
            raise ValueError(f"Урок {day}: неверный текст анонса")
        if not isinstance(item.get("button_text"), str) or not item["button_text"].strip():
            raise ValueError(f"Урок {day}: нет текста кнопки")
        ids.add(lesson_id)
        lessons.append(Lesson(day, lesson_id, item["text"], item["button_text"], url))
    return lessons


def moscow_now(now_utc: datetime) -> datetime:
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(MOSCOW)


def first_lesson_due_at(run: CourseRun) -> datetime:
    """Naive UTC; repeated /start never changes the original activation clock."""
    return run.activated_at + timedelta(minutes=10)


async def activate_course(
    session: AsyncSession,
    participant: Participant,
    now_utc: datetime,
) -> CourseRun | None:
    """A repeated /start never restarts an existing personal course."""
    if participant.status not in {ParticipantStatus.APPROVED, ParticipantStatus.ACTIVE}:
        return None
    if participant.tg_user_id is None:
        return None
    stream = await session.get(Stream, participant.stream_id)
    if stream is None or not stream.is_active:
        return None
    participant_id = participant.id
    existing = await session.get(CourseRun, participant_id)
    if existing is not None:
        return existing
    local = moscow_now(now_utc)
    first_date = local.date()
    run = CourseRun(
        participant_id=participant.id,
        stream_id=participant.stream_id,
        activated_at=local.astimezone(timezone.utc).replace(tzinfo=None),
        first_delivery_date=first_date,
    )
    session.add(run)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return await session.get(CourseRun, participant_id)
    return run


async def course_deliveries(session: AsyncSession, participant_id: int) -> list[LessonDelivery]:
    return list(
        (
            await session.execute(
                select(LessonDelivery)
                .where(
                    LessonDelivery.participant_id == participant_id,
                )
                .order_by(LessonDelivery.lesson_day)
            )
        )
        .scalars()
        .all()
    )


def participant_lock(participant_id: int) -> asyncio.Lock:
    # One bot process; weak values avoid retaining event-loop-bound test locks.
    lock = _participant_locks.get(participant_id)
    if lock is None:
        lock = asyncio.Lock()
        _participant_locks[participant_id] = lock
    return lock


async def deliver_due_lesson(
    session: AsyncSession,
    stream: Stream,
    participant: Participant,
    bot,
    catalog: list[Lesson],
    now_utc: datetime,
    *,
    clock: Callable[[], datetime] = utcnow,
) -> None:
    async with participant_lock(participant.id):
        await _deliver_due_lesson(session, stream, participant, bot, catalog, now_utc, clock=clock)


async def _deliver_due_lesson(
    session: AsyncSession,
    stream: Stream,
    participant: Participant,
    bot,
    catalog: list[Lesson],
    now_utc: datetime,
    *,
    clock: Callable[[], datetime] = utcnow,
) -> None:
    """Claim+commit before send. Never replay sending/uncertain rows automatically.

    Telegram sendMessage has no idempotency key: a timeout/process crash may have
    delivered the message already. Only an owner who checked with the recipient may resolve it.
    """
    # The scheduler may have awaited another participant's request since its query.
    # Refresh under the personal lock so a leave/revoke committed meanwhile wins.
    participant = await session.get(Participant, participant.id, populate_existing=True)
    stream = await session.get(Stream, stream.id, populate_existing=True)
    if participant is None or stream is None:
        return
    run = await session.get(CourseRun, participant.id)
    local = moscow_now(now_utc)
    if (
        not stream.is_active
        or participant.stream_id != stream.id
        or participant.status not in {ParticipantStatus.APPROVED, ParticipantStatus.ACTIVE}
        or participant.tg_user_id is None
        or run is None
        or local.date() < run.first_delivery_date
    ):
        return
    deliveries = await course_deliveries(session, participant.id)
    if any(row.status in {"sending", "uncertain", "failed"} for row in deliveries):
        return
    sent = [row for row in deliveries if row.status == "sent"]
    if sent and local.hour < 8:
        return
    if not sent and local.astimezone(timezone.utc).replace(tzinfo=None) < first_lesson_due_at(run):
        return
    if local.astimezone(timezone.utc).replace(tzinfo=None) < run.activated_at:
        return
    if len(sent) >= len(catalog):
        return
    if sent and max(moscow_now(row.sent_at).date() for row in sent) >= local.date():
        return
    lesson = catalog[len(sent)]
    row = next((row for row in deliveries if row.lesson_day == lesson.day), None)
    if row is None:
        row = LessonDelivery(
            stream_id=stream.id,
            participant_id=participant.id,
            lesson_day=lesson.day,
            status="pending",
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return  # Another invocation created the delivery; its job owns this attempt.
    # Conditional durable claim handles overlapping jobs and multiple DB sessions.
    now_naive = local.astimezone(timezone.utc).replace(tzinfo=None)
    claim = await session.execute(
        update(LessonDelivery)
        .where(
            LessonDelivery.participant_id == participant.id,
            LessonDelivery.lesson_day == lesson.day,
            LessonDelivery.status == "pending",
            (LessonDelivery.retry_at.is_(None) | (LessonDelivery.retry_at <= now_naive)),
        )
        .values(status="sending", attempted_at=now_naive, owner_notified_at=None)
    )
    await session.commit()
    if claim.rowcount != 1:
        return
    await session.refresh(row)
    try:
        message = await bot.send_message(
            participant.tg_user_id,
            lesson.text,
            parse_mode=None,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text=lesson.button_text, url=lesson.article_url),
                    ]
                ]
            ),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramRetryAfter as exc:
        # A 429 API response explicitly rejected this request; safe to retry later.
        row.status = "pending"
        response_at = moscow_now(clock()).astimezone(timezone.utc).replace(tzinfo=None)
        row.retry_at = response_at + timedelta(seconds=max(1, exc.retry_after))
        logger.warning("lesson rate limited stream=%s day=%s", stream.id, lesson.day)
    except TelegramAPIError as exc:
        # Network/server failures are ambiguous; explicit API refusals require a fix.
        from aiogram.exceptions import TelegramNetworkError, TelegramServerError

        row.status = "uncertain" if isinstance(exc, (TelegramNetworkError, TelegramServerError)) else "failed"
        logger.warning("lesson delivery stream=%s day=%s status=%s", stream.id, lesson.day, row.status)
    except Exception:
        row.status = "uncertain"
        logger.exception("lesson delivery ambiguous stream=%s day=%s", stream.id, lesson.day)
    else:
        row.status = "sent"
        row.message_id = message.message_id
        row.sent_at = (
            moscow_now(getattr(message, "date", None) or clock())
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )
        row.retry_at = None
    await session.commit()


async def resolve_delivery(
    session: AsyncSession,
    participant_id: int,
    outcome: str,
    now_utc: datetime,
) -> None:
    # An owner command must wait for any in-flight Telegram request to settle.
    async with participant_lock(participant_id):
        await _resolve_delivery(session, participant_id, outcome, now_utc)


async def _resolve_delivery(
    session: AsyncSession,
    participant_id: int,
    outcome: str,
    now_utc: datetime,
) -> None:
    """Owner checked with the recipient: mark delivered or explicitly permit retry."""
    if outcome not in {"sent", "retry"}:
        raise ValueError("Используй /resolve_lesson @username sent или /resolve_lesson @username retry")
    blocked = [
        row
        for row in await course_deliveries(session, participant_id)
        if row.status in {"sending", "uncertain", "failed"}
    ]
    if len(blocked) != 1:
        raise ValueError("Нет одной остановленной отправки; проверь /course_status")
    row = blocked[0]
    row.status = "sent" if outcome == "sent" else "pending"
    row.sent_at = (
        moscow_now(now_utc).astimezone(timezone.utc).replace(tzinfo=None) if outcome == "sent" else None
    )
    row.retry_at = None
    row.owner_notified_at = None
    await session.commit()
