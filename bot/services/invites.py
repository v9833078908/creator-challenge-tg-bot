"""Read Telegram's current primary invitation before sending an approved user in."""

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot.db import SessionFactory, repo

logger = logging.getLogger(__name__)
_stream_locks: dict[int, asyncio.Lock] = {}


class InviteLinkUnavailable(Exception):
    """Telegram could not confirm a current invitation; never send a cached URL."""


async def ensure_invite_link(bot: Bot, stream_id: int) -> str:
    """Synchronize the saved URL with getChat, without editing or rotating links.

    The single polling process serializes updates per stream and rereads the row
    inside the lock. Existing additional links remain untouched and usable.
    """
    lock = _stream_locks.setdefault(stream_id, asyncio.Lock())
    async with lock:
        async with SessionFactory() as session:
            stream = await repo.get_stream(session, stream_id)
            if stream is None or not stream.is_active:
                raise InviteLinkUnavailable("No active stream")
            try:
                chat = await bot.get_chat(chat_id=stream.chat_id)
            except (TelegramAPIError, OSError, TimeoutError) as error:
                logger.warning(
                    "Invite lookup unavailable stream=%s error=%s", stream_id, type(error).__name__,
                )
                raise InviteLinkUnavailable("Telegram invitation lookup unavailable") from error
            if not chat.invite_link:
                logger.warning("Current primary invitation missing stream=%s", stream_id)
                raise InviteLinkUnavailable("Telegram did not return a current primary invitation")
            if stream.invite_link != chat.invite_link:
                stream.invite_link = chat.invite_link
                await session.commit()
                logger.info("Current primary invitation synchronized stream=%s", stream_id)
            return chat.invite_link
