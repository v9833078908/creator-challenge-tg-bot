from aiogram import Dispatcher

from bot.handlers import admin, dm, group


def register_handlers(dp: Dispatcher) -> None:
    dp.include_router(dm.router)
    dp.include_router(admin.router)
    dp.include_router(group.router)
