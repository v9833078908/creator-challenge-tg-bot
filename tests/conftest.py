import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db import repo
from bot.db.models import Base, Stream


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def stream(session) -> Stream:
    s = await repo.create_stream(
        session,
        title="👑 Тест | Creator Hero",
        chat_id=-1001234567890,
        invite_link="https://t.me/+TestInvite",
        timezone="Europe/Moscow",
    )
    await session.commit()
    return s
