"""Игровая логика онбординга и переходов статусов. Чистые функции над БД — без Telegram API."""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo
from bot.db.models import CourseRun, Participant, ParticipantStatus, Stream, utcnow
from bot.services import lessons


@dataclass
class StartOutcome:
    kind: str  # invited | already_active | refused | kicked | no_stream
    stream: Stream | None = None
    participant: Participant | None = None
    first_lesson_date: date | None = None
    course_newly_activated: bool = False
    course_state: str = ""
    first_lesson_due_at: datetime | None = None


@dataclass
class JoinOutcome:
    activated: bool
    reason: str = ""  # unknown | already_active | ""
    prev_status: ParticipantStatus | None = None
    restored_penalties: int = 0
    participant: Participant | None = None
    first_join: bool = False


async def process_start(
    session: AsyncSession,
    *,
    tg_user_id: int,
    username: str | None,
    full_name: str,
    occurred_at: datetime | None = None,
) -> StartOutcome:
    """`/start` в личке: whitelist-проверка → инвайт-ссылка."""
    stream = await repo.get_active_stream(session)
    if stream is None:
        return StartOutcome(kind="no_stream")

    participant = await repo.get_participant_by_tg_id(session, stream.id, tg_user_id)
    if participant is None and username:
        participant = await repo.get_participant_by_username(session, stream.id, username)
        if participant is not None and participant.tg_user_id is not None:
            participant = None

    if participant is None:
        return StartOutcome(kind="refused", stream=stream)
    if participant.status == ParticipantStatus.REVOKED:
        return StartOutcome(kind="refused", stream=stream, participant=participant)
    if participant.status == ParticipantStatus.KICKED:
        return StartOutcome(kind="kicked", stream=stream, participant=participant)

    # Привязываем tg id/имя при первом контакте
    if participant.tg_user_id is None:
        participant.tg_user_id = tg_user_id
    if full_name:
        participant.full_name = full_name
    if username:
        participant.username = username
    await session.commit()

    previous_run = await session.get(CourseRun, participant.id)
    activated_run = await lessons.activate_course(session, participant, occurred_at or utcnow())
    run = activated_run or previous_run
    newly_activated = activated_run is not None and previous_run is None
    deliveries = await lessons.course_deliveries(session, participant.id) if run is not None else []
    sent_count = sum(row.status == "sent" for row in deliveries)
    if sent_count == 33:
        course_state = "completed"
    elif participant.status == ParticipantStatus.LEFT:
        course_state = "paused" if run is not None else "not_enrolled_paused"
    elif any(row.status in {"sending", "uncertain", "failed"} for row in deliveries):
        course_state = "blocked"
    elif newly_activated:
        course_state = "new"
    elif run is not None:
        course_state = "waiting" if sent_count == 0 else "ongoing"
    else:
        course_state = ""
    due = lessons.first_lesson_due_at(run) if run is not None else None
    first_delivery = next((row for row in deliveries if row.lesson_day == 1), None)
    if due is not None and first_delivery is not None and first_delivery.retry_at is not None:
        due = max(due, first_delivery.retry_at)
    return StartOutcome(
        kind="already_active" if participant.status == ParticipantStatus.ACTIVE else "invited",
        stream=stream,
        participant=participant,
        first_lesson_date=run.first_delivery_date if run is not None else None,
        course_newly_activated=newly_activated,
        course_state=course_state,
        first_lesson_due_at=due,
    )


async def process_join(
    session: AsyncSession,
    *,
    stream: Stream,
    tg_user_id: int,
    username: str | None,
    full_name: str,
    grace_hours: int,
    occurred_at: datetime | None = None,
) -> JoinOutcome:
    """Вход в чат: approved/left/kicked → active.

    - left: открытые штрафы восстанавливаются с новым дедлайном (+grace_hours)
    - kicked (ручной unban владельцем в обход /approve): штрафы обнуляются
    - неизвестный вошедший (ссылка ушла дальше): не участник, обязательств нет
    """
    participant = await repo.get_participant_by_tg_id(session, stream.id, tg_user_id)
    if participant is None and username:
        participant = await repo.get_participant_by_username(session, stream.id, username)
        if participant is not None and participant.tg_user_id is not None:
            participant = None
        elif participant is not None:
            participant.tg_user_id = tg_user_id

    if participant is None or participant.status == ParticipantStatus.REVOKED:
        return JoinOutcome(activated=False, reason="unknown")
    if participant.status == ParticipantStatus.ACTIVE:
        return JoinOutcome(activated=False, reason="already_active", participant=participant)

    prev = participant.status
    first_join = participant.joined_at is None
    participant.status = ParticipantStatus.ACTIVE
    participant.joined_at = occurred_at or utcnow()
    participant.left_at = None
    if full_name:
        participant.full_name = full_name
    if username:
        participant.username = username

    restored = 0
    if prev == ParticipantStatus.LEFT:
        restored = await repo.extend_open_penalties(session, participant.id, grace_hours)
    elif prev == ParticipantStatus.KICKED:
        await repo.cancel_open_penalties(session, participant.id)

    await repo.record_join(session, participant, participant.joined_at)

    await session.commit()
    return JoinOutcome(
        activated=True, prev_status=prev, restored_penalties=restored,
        participant=participant, first_join=first_join,
    )


async def process_leave(
    session: AsyncSession, *, stream: Stream, tg_user_id: int, occurred_at: datetime | None = None
) -> bool:
    """Выход из чата: active → left (пауза). Штрафы НЕ аннулируются."""
    participant = await repo.get_participant_by_tg_id(session, stream.id, tg_user_id)
    if participant is None or participant.status != ParticipantStatus.ACTIVE:
        return False
    await repo.mark_left(session, participant, occurred_at)
    await session.commit()
    return True


async def process_manual_kick(session: AsyncSession, *, stream: Stream, tg_user_id: int) -> bool:
    """Кого-то забанили вручную в чате (не ботом) — фиксируем статус."""
    participant = await repo.get_participant_by_tg_id(session, stream.id, tg_user_id)
    if participant is None or participant.status != ParticipantStatus.ACTIVE:
        return False
    await repo.mark_kicked(session, participant)
    await session.commit()
    return True
