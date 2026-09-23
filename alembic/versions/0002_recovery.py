"""Membership history and durable daily closures.

Revision ID: 0002_recovery
Revises: 0001_initial
"""

import sqlalchemy as sa

from alembic import op

revision = "0002_recovery"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "membership_periods",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stream_id", sa.Integer(), sa.ForeignKey("streams.id"), nullable=False),
        sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participants.id"), nullable=False),
        sa.Column("joined_at", sa.DateTime(), nullable=False),
        sa.Column("left_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_membership_periods_stream_id", "membership_periods", ["stream_id"])
    op.create_index("ix_membership_periods_participant_id", "membership_periods", ["participant_id"])
    op.execute(
        "INSERT INTO membership_periods (stream_id, participant_id, joined_at, left_at) "
        "SELECT stream_id, id, joined_at, "
        "CASE WHEN status IN ('left', 'kicked') "
        "THEN COALESCE(left_at, joined_at) ELSE NULL END "
        "FROM participants WHERE joined_at IS NOT NULL"
    )
    op.create_table(
        "daily_closures",
        sa.Column("stream_id", sa.Integer(), sa.ForeignKey("streams.id"), primary_key=True),
        sa.Column("report_date", sa.Date(), primary_key=True),
        sa.Column("closed_at", sa.DateTime(), nullable=False),
        sa.Column("digest_sent_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "uq_penalty_per_day_reason", "penalties",
        ["stream_id", "participant_id", "issued_for_date", "reason"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_penalty_per_day_reason", table_name="penalties")
    op.drop_table("daily_closures")
    op.drop_index("ix_membership_periods_participant_id", table_name="membership_periods")
    op.drop_index("ix_membership_periods_stream_id", table_name="membership_periods")
    op.drop_table("membership_periods")
