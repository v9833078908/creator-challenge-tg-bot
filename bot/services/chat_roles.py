"""Resolve the current Telegram chat owner without confusing ordinary admins."""

from aiogram import Bot
from aiogram.enums import ChatMemberStatus


async def get_chat_owner_id(bot: Bot, chat_id: int) -> int:
    """Return the authoritative creator ID; fail if Telegram cannot establish it.

    Roles are fetched each time so transferring chat ownership takes effect without
    a bot restart. Callers must skip obligations when this lookup fails.
    """
    administrators = await bot.get_chat_administrators(chat_id)
    for member in administrators:
        if member.status == ChatMemberStatus.CREATOR:
            return member.user.id
    raise RuntimeError(f"Telegram returned no creator for chat {chat_id}")
