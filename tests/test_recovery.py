from datetime import date, datetime
from types import SimpleNamespace

from bot.__main__ import drain_pending_updates
from bot.db.models import DailyClosure
from bot.scheduler import jobs


async def test_drains_pending_updates_in_order():
    updates = [SimpleNamespace(update_id=10), SimpleNamespace(update_id=11)]
    offsets = []
    fed = []

    class Bot:
        async def get_updates(self, *, offset=None, limit=None, timeout=None):
            offsets.append(offset)
            return [u for u in updates if offset is None or u.update_id >= offset][:1]

    class Dispatcher:
        async def feed_update(self, bot, update):
            fed.append(update.update_id)

    await drain_pending_updates(Bot(), Dispatcher())
    assert fed == [10, 11]
    assert offsets == [None, 11, 12]


async def test_recovery_dates_resume_after_last_closed_day(session, stream):
    session.add(DailyClosure(
        stream_id=stream.id, report_date=date(2026, 9, 20), closed_at=datetime(2026, 9, 20)
    ))
    await session.commit()
    assert await jobs.pending_close_dates(session, stream.id, date(2026, 9, 23)) == [
        date(2026, 9, 21), date(2026, 9, 22),
    ]


async def test_first_recovery_closes_yesterday_only(session, stream):
    stream.created_at = datetime(2026, 9, 20)
    await session.commit()
    assert await jobs.pending_close_dates(session, stream.id, date(2026, 9, 23)) == [
        date(2026, 9, 22),
    ]


async def test_new_stream_does_not_receive_yesterdays_digest(session, stream):
    stream.created_at = datetime(2026, 9, 23, 8)
    await session.commit()
    assert await jobs.pending_close_dates(session, stream.id, date(2026, 9, 23)) == []
