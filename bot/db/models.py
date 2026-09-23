"""Модели данных Creator Hero. Мультипоточность заложена через stream_id во всех таблицах."""

import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC datetime (совместимо с py3.10+ и SQLite)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


_str_enum = {"native_enum": False, "length": 16, "validate_strings": True}


class ParticipantStatus(str, enum.Enum):
    APPROVED = "approved"  # в whitelist, ещё не вступил в чат
    ACTIVE = "active"  # в чате, обязан сдавать отчёты
    LEFT = "left"  # сам вышел — пауза без новых штрафов
    KICKED = "kicked"  # кикнут ботом за просроченный штраф


class PenaltyStatus(str, enum.Enum):
    OPEN = "open"
    PAID = "paid"  # отработан через #штраф
    EXPIRED = "expired"  # просрочен (участника кикнули / кикнули бы)
    CANCELLED = "cancelled"  # обнулён (возврат кикнутого через /approve)


class Stream(Base):
    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    invite_link: Mapped[str] = mapped_column(String(512), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    participants: Mapped[list["Participant"]] = relationship(back_populates="stream")


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[int] = mapped_column(primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id"), index=True)
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # узнаём при /start
    username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[ParticipantStatus] = mapped_column(
        Enum(ParticipantStatus, values_callable=lambda e: [x.value for x in e], **_str_enum),
        default=ParticipantStatus.APPROVED,
    )
    joined_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    left_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    kick_count: Mapped[int] = mapped_column(Integer, default=0)

    stream: Mapped[Stream] = relationship(back_populates="participants")


class DailyReport(Base):
    __tablename__ = "daily_reports"
    __table_args__ = (
        UniqueConstraint("stream_id", "participant_id", "report_date", name="uq_report_per_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id"), index=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id"), index=True)
    report_date: Mapped[date] = mapped_column(Date)  # локальная дата потока
    message_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(16))  # link | forward | text
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    seq_number: Mapped[int] = mapped_column(Integer)  # №k за день
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Penalty(Base):
    __tablename__ = "penalties"
    __table_args__ = (
        UniqueConstraint(
            "stream_id", "participant_id", "issued_for_date", "reason",
            name="uq_penalty_per_day_reason",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id"), index=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id"), index=True)
    reason: Mapped[str] = mapped_column(String(32), default="missed_report")
    issued_for_date: Mapped[date] = mapped_column(Date)
    status: Mapped[PenaltyStatus] = mapped_column(
        Enum(PenaltyStatus, values_callable=lambda e: [x.value for x in e], **_str_enum),
        default=PenaltyStatus.OPEN,
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class DailyCounter(Base):
    """Эмодзи дня и счётчик отчётов (№k)."""

    __tablename__ = "daily_counters"

    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id"), primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    emoji: Mapped[str] = mapped_column(String(8), default="")
    count: Mapped[int] = mapped_column(Integer, default=0)


class MembershipPeriod(Base):
    """Интервал нахождения участника в чате для честного закрытия прошлых дней."""

    __tablename__ = "membership_periods"

    id: Mapped[int] = mapped_column(primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id"), index=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id"), index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime)
    left_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DailyClosure(Base):
    """День, за который штрафы уже рассчитаны."""

    __tablename__ = "daily_closures"

    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id"), primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    digest_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
