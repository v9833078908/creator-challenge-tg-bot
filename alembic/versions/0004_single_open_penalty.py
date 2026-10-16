"""One outstanding penalty per participant, preserving historical rows.

Revision ID: 0004_single_open_penalty
Revises: 0003_lessons
"""

import sqlalchemy as sa

from alembic import op

revision = "0004_single_open_penalty"
down_revision = "0003_lessons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the earliest debt deadline; migration must not grant another grace period.
    # Cancel duplicates instead of deleting records so their history remains inspectable.
    op.execute(
        "UPDATE penalties SET status = 'cancelled' WHERE status = 'open' AND id IN ("
        "SELECT id FROM (SELECT id, ROW_NUMBER() OVER ("
        "PARTITION BY stream_id, participant_id "
        "ORDER BY COALESCE(due_at, issued_at), issued_at, id"
        ") AS position FROM penalties WHERE status = 'open') AS ranked "
        "WHERE position > 1)"
    )
    op.create_index(
        "uq_one_open_penalty", "penalties", ["stream_id", "participant_id"], unique=True,
        sqlite_where=sa.text("status = 'open'"), postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    # Cancelled duplicate debt stays cancelled; restoring accumulated debt is unsafe.
    op.drop_index("uq_one_open_penalty", table_name="penalties")
