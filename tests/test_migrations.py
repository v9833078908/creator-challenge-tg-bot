import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_kicked_participant_is_not_left_active_by_migration(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "existing.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite+aiosqlite:///{db_path}")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0001_initial"],
        cwd=root, env=env, check=True, capture_output=True,
    )
    with sqlite3.connect(db_path) as db:
        db.execute(
            "insert into streams (id,title,chat_id,created_at) "
            "values (1,'friends',-100,'2026-09-20 00:00:00')"
        )
        db.execute(
            "insert into participants (id,stream_id,tg_user_id,username,full_name,status,joined_at) "
            "values (1,1,42,'friend','Friend','kicked','2026-09-20 10:00:00')"
        )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root, env=env, check=True, capture_output=True,
    )
    with sqlite3.connect(db_path) as db:
        joined, left = db.execute(
            "select joined_at, left_at from membership_periods where participant_id=1"
        ).fetchone()
    assert left == joined
