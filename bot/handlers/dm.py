"""Личка: /start — whitelist-проверка и выдача инвайт-ссылки."""

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.db import SessionFactory
from bot.services import invites, lessons, onboarding, texts

router = Router(name="dm")


@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message, bot: Bot) -> None:
    user = message.from_user
    if user is None:
        return

    async with SessionFactory() as session:
        outcome = await onboarding.process_start(
            session,
            tg_user_id=user.id,
            username=user.username,
            full_name=user.full_name,
        )

    course_note = ""
    match outcome.course_state:
        case "new":
            course_note = (
                "\n\nПервый урок придёт через 10 минут после первой активации. "
                "Дальше — по одному в день в 08:00 МСК. Уроки приходят сюда, в личку. "
                "Маленькие шаги — по желанию."
            )
        case "waiting":
            due = lessons.moscow_now(outcome.first_lesson_due_at)
            course_note = (
                f"\n\nПервый урок запланирован на {due:%d.%m, %H:%M} МСК. "
                "Прогресс сохранён. Повторный /start не начинает курс заново и не меняет время первого урока."
            )
        case "ongoing":
            course_note = (
                "\n\nПрогресс уроков сохранён. Повторный /start не начинает курс заново. "
                "Следующие уроки приходят сюда, в личку, в 08:00 МСК."
            )
        case "completed":
            course_note = "\n\nВсе 33 урока уже отправлены. Повторный /start их не отправляет заново."
        case "paused":
            course_note = (
                "\n\nУроки на паузе, прогресс сохранён. Вернись в группу по ссылке — "
                "продолжим со следующего урока."
            )
        case "not_enrolled_paused":
            course_note = "\n\nДля активации уроков сначала вернись в группу по ссылке, затем напиши /start."
        case "blocked":
            course_note = (
                "\n\nОтправка уроков приостановлена. Прогресс сохранён. "
                "Организатор поможет продолжить цепочку."
            )

    invite_link = None
    if outcome.kind in {"invited", "already_active"}:
        try:
            invite_link = await invites.ensure_invite_link(bot, outcome.stream.id)
        except invites.InviteLinkUnavailable:
            await message.answer(
                "Не удалось проверить ссылку на чат. Ты в списке участников — "
                "попробуй /start чуть позже. Если не получится, напиши организатору."
            )

    match outcome.kind:
        case "invited" if invite_link is not None:
            await message.answer(
                texts.start_invited(
                    user.full_name, user.username, outcome.stream.title, invite_link
                )
                + course_note
            )
        case "already_active" if invite_link is not None:
            await message.answer(
                texts.start_already_active(user.full_name, user.username, invite_link)
                + course_note
            )
        case "invited" | "already_active":
            pass  # The temporary invite error was already explained above.
        case "refused":
            await message.answer(texts.start_refused())
        case "kicked":
            await message.answer(texts.start_kicked())
        case _:
            await message.answer(texts.start_no_stream())

    if outcome.course_state in {"new", "waiting", "ongoing"}:
        # Persisted activation drives both the precise timer and safe catch-up.
        from bot.scheduler import jobs

        await jobs.register_first_lesson_job(jobs.get_scheduler(), bot, outcome.participant.id)
        await jobs.job_lesson(bot, outcome.stream.id, participant_id=outcome.participant.id)
