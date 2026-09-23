"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-20

"""

import sqlalchemy as sa

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

PARTICIPANT_STATUS = sa.Enum(
    "approved", "active", "left", "kicked",
    name="participantstatus", native_enum=False, length=16,
)
PENALTY_STATUS = sa.Enum(
    "open", "paid", "expired", "cancelled",
    name="penaltystatus", native_enum=False, length=16,
)


def upgrade() -> None:
    op.create_table(
        "streams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("invite_link", sa.String(512), nullable=False, server_default=""),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Moscow"),
        sa.Column("settings_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "participants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stream_id", sa.Integer(), sa.ForeignKey("streams.id"), nullable=False, index=True),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=True),
        sa.Column("username", sa.String(64), nullable=True, index=True),
        sa.Column("full_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", PARTICIPANT_STATUS, nullable=False, server_default="approved"),
        sa.Column("joined_at", sa.DateTime(), nullable=True),
        sa.Column("left_at", sa.DateTime(), nullable=True),
        sa.Column("kick_count", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "daily_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stream_id", sa.Integer(), sa.ForeignKey("streams.id"), nullable=False, index=True),
        sa.Column(
            "participant_id", sa.Integer(),
            sa.ForeignKey("participants.id"), nullable=False, index=True,
        ),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("seq_number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "stream_id", "participant_id", "report_date", name="uq_report_per_day"
        ),
    )

    op.create_table(
        "penalties",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stream_id", sa.Integer(), sa.ForeignKey("streams.id"), nullable=False, index=True),
        sa.Column(
            "participant_id", sa.Integer(),
            sa.ForeignKey("participants.id"), nullable=False, index=True,
        ),
        sa.Column("reason", sa.String(32), nullable=False, server_default="missed_report"),
        sa.Column("issued_for_date", sa.Date(), nullable=False),
        sa.Column("status", PENALTY_STATUS, nullable=False, server_default="open"),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("paid_message_id", sa.BigInteger(), nullable=True),
    )

    op.create_table(
        "daily_counters",
        sa.Column("stream_id", sa.Integer(), sa.ForeignKey("streams.id"), primary_key=True),
        sa.Column("report_date", sa.Date(), primary_key=True),
        sa.Column("emoji", sa.String(8), nullable=False, server_default=""),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("daily_counters")
    op.drop_table("penalties")
    op.drop_table("daily_reports")
    op.drop_table("participants")
    op.drop_table("streams")
