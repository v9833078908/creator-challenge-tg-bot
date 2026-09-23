"""Личка: /start — whitelist-проверка и выдача инвайт-ссылки."""

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.db import SessionFactory
from bot.services import onboarding, texts

router = Router(name="dm")


@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message) -> None:
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

    match outcome.kind:
        case "invited":
            await message.answer(
                texts.start_invited(
                    user.full_name, user.username, outcome.stream.title, outcome.stream.invite_link
                )
            )
        case "already_active":
            await message.answer(
                texts.start_already_active(user.full_name, user.username, outcome.stream.invite_link)
            )
        case "refused":
            await message.answer(texts.start_refused())
        case "kicked":
            await message.answer(texts.start_kicked())
        case _:
            await message.answer(texts.start_no_stream())
