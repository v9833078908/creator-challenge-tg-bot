"""Personal course activation and durable private lesson delivery claims.

Revision ID: 0003_lessons
Revises: 0002_recovery
"""

import sqlalchemy as sa

from alembic import op

revision = "0003_lessons"
down_revision = "0002_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ParticipantStatus.REVOKED is stored in the existing VARCHAR(16), with no
    # native enum/CHECK in 0001. No rewrite or activation of existing people.
    op.create_table(
        "course_runs",
        sa.Column("participant_id", sa.Integer(),
                  sa.ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("stream_id", sa.Integer(), sa.ForeignKey("streams.id"), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=False),
        sa.Column("first_delivery_date", sa.Date(), nullable=False),
    )
    op.create_index("ix_course_runs_stream_id", "course_runs", ["stream_id"])
    op.create_table(
        "lesson_deliveries",
        sa.Column("participant_id", sa.Integer(),
                  sa.ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("lesson_day", sa.Integer(), primary_key=True),
        sa.Column("stream_id", sa.Integer(), sa.ForeignKey("streams.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempted_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("retry_at", sa.DateTime(), nullable=True),
        sa.Column("owner_notified_at", sa.DateTime(), nullable=True),
    )

    op.create_index("ix_lesson_deliveries_stream_id", "lesson_deliveries", ["stream_id"])


def downgrade() -> None:
    op.drop_index("ix_lesson_deliveries_stream_id", table_name="lesson_deliveries")
    op.drop_table("lesson_deliveries")
    op.drop_index("ix_course_runs_stream_id", table_name="course_runs")
    op.drop_table("course_runs")
