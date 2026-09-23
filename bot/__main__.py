"""Точка входа: миграции, бот, диспетчер, планировщик, long polling."""

import asyncio
import logging
from contextlib import suppress
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import settings
from bot.db import engine
from bot.handlers import register_handlers
from bot.healthcheck import HEARTBEAT, heartbeat_loop
from bot.scheduler import jobs as sched_jobs

logger = logging.getLogger(__name__)


async def drain_pending_updates(bot: Bot, dp: Dispatcher) -> None:
    """Обработать накопленные события до пересчёта пропущенных дней."""
    offset = None
    while True:
        updates = await bot.get_updates(offset=offset, limit=1, timeout=0)
        if not updates:
            return
        update = updates[0]
        await dp.feed_update(bot, update)
        offset = update.update_id + 1


def run_migrations() -> None:
    """Alembic upgrade head при старте — бот сам накатывает схему."""
    from alembic.config import Config

    from alembic import command

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(cfg, "head")


def ensure_data_dir() -> None:
    """Для file-based SQLite создаём каталог заранее."""
    url = settings.database_url
    if url.startswith("sqlite") and "///" in url and ":memory:" not in url:
        db_path = Path(url.split("///", 1)[1])
        db_path.parent.mkdir(parents=True, exist_ok=True)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан — заполни .env (см. .env.example)")
    if not settings.owner_tg_id:
        logger.warning("OWNER_TG_ID не задан — команды владельца будут недоступны")

    HEARTBEAT.unlink(missing_ok=True)
    ensure_data_dir()
    run_migrations()
    logger.info("migrations applied")

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    register_handlers(dp)

    await bot.delete_webhook(drop_pending_updates=False)
    await drain_pending_updates(bot, dp)

    # Сначала восстанавливаем закрытие прошедших дней, затем запускаем расписание.
    await sched_jobs.recover_missed_days(bot)
    scheduler = AsyncIOScheduler()
    sched_jobs.set_scheduler(scheduler)
    await sched_jobs.register_all_jobs(scheduler, bot)
    scheduler.start()
    logger.info("scheduler started")
    heartbeat_task = asyncio.create_task(heartbeat_loop())

    logger.info("bot started (long polling)")
    try:
        await dp.start_polling(bot)
    finally:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
        scheduler.shutdown(wait=False)
        await engine.dispose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
