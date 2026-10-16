"""Daily report reminders follow the challenge clock and target missing posts."""

from datetime import datetime, timezone
from types import SimpleNamespace

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.scheduler import jobs
from bot.services import texts


async def test_report_reminder_runs_daily_at_18_moscow(session, stream, monkeypatch):
    monkeypatch.delenv("REPORT_REMINDER_TIME", raising=False)
    monkeypatch.setattr(jobs, "settings", Settings(_env_file=None))
    monkeypatch.setattr(jobs, "SessionFactory", async_sessionmaker(
        session.bind, expire_on_commit=False,
    ))
    scheduler = AsyncIOScheduler(timezone="UTC")

    await jobs.register_stream_jobs(scheduler, object(), stream.id)

    trigger = scheduler.get_job(f"report_reminder:{stream.id}").trigger
    assert str(trigger.timezone) == "Europe/Moscow"
    before_reminder = datetime(2026, 10, 15, 14, 59, tzinfo=timezone.utc)
    today = trigger.get_next_fire_time(None, before_reminder)
    assert today.astimezone(timezone.utc) == datetime(2026, 10, 15, 15, tzinfo=timezone.utc)
    tomorrow = trigger.get_next_fire_time(today, today)
    assert tomorrow.astimezone(timezone.utc) == datetime(2026, 10, 16, 15, tzinfo=timezone.utc)


def test_report_reminder_gives_deadline_without_implying_it_is_imminent(monkeypatch):
    monkeypatch.setattr(texts.settings, "report_deadline", "23:59")
    person = SimpleNamespace(full_name="Участник", username="participant", tg_user_id=123)

    reminder = texts.reminder_report([person])

    assert "23:59" in reminder
    assert "#пост" in reminder
    assert "Участник" in reminder
    assert "совсем чуть-чуть" not in reminder
