"""Конфигурация из переменных окружения (.env)."""

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    owner_tg_id: int = 0

    database_url: str = "sqlite+aiosqlite:///./data/creator_hero.db"

    # Правила челленджа (конфигурируемые)
    challenge_timezone: str = "Europe/Moscow"
    report_deadline: str = "23:59"
    min_post_chars: int = 400
    penalty_amount_rub: int = 500
    penalty_grace_hours: int = 24
    digest_time: str = "00:00"  # дайджест + выставление штрафов
    report_reminder_time: str = "21:00"  # напоминание несдавшим
    penalty_reminder_time: str = "20:00"  # напоминание штрафникам
    kick_time: str = "00:05"  # кик просрочивших штрафы

    @field_validator("report_deadline", "digest_time", "report_reminder_time",
                     "penalty_reminder_time", "kick_time")
    @classmethod
    def valid_hhmm(cls, value: str) -> str:
        if not re.fullmatch(r"\d{2}:\d{2}", value):
            raise ValueError("время должно быть в формате HH:MM")
        hour, minute = map(int, value.split(":"))
        if hour > 23 or minute > 59:
            raise ValueError("время должно быть в диапазоне 00:00–23:59")
        return value

    @field_validator("challenge_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("неизвестный часовой пояс") from exc
        return value

    @property
    def sync_database_url(self) -> str:
        """URL для Alembic (синхронный драйвер)."""
        return self.database_url.replace("sqlite+aiosqlite", "sqlite")


settings = Settings()
