import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def test_single_open_penalty_migration_preserves_history_and_enforces_cap(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "penalty-cap.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite+aiosqlite:///{db_path}")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0003_lessons"],
        cwd=root, env=env, check=True, capture_output=True,
    )
    with sqlite3.connect(db_path) as db:
        db.execute(
            "insert into streams (id,title,chat_id,created_at) "
            "values (1,'friends',-100,'2026-09-20 00:00:00')"
        )
        db.execute(
            "insert into participants (id,stream_id,username,full_name,status) "
            "values (1,1,'friend','Friend','active')"
        )
        db.executemany(
            "insert into penalties "
            "(id,stream_id,participant_id,reason,issued_for_date,status,issued_at,due_at) "
            "values (?,1,1,'missed_report',?,?,?,?)",
            [
                (1, "2026-09-01", "open", "2026-09-02 00:00:00", "2026-10-01 00:00:00"),
                (2, "2026-09-02", "open", "2026-09-03 00:00:00", "2026-09-04 00:00:00"),
                (3, "2026-09-03", "open", "2026-09-04 00:00:00", "2026-09-05 00:00:00"),
                (4, "2026-08-01", "paid", "2026-08-02 00:00:00", "2026-08-03 00:00:00"),
                (5, "2026-08-02", "expired", "2026-08-03 00:00:00", "2026-08-04 00:00:00"),
            ],
        )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root, env=env, check=True, capture_output=True,
    )
    with sqlite3.connect(db_path) as db:
        assert db.execute("select id,status from penalties order by id").fetchall() == [
            (1, "cancelled"), (2, "open"), (3, "cancelled"), (4, "paid"), (5, "expired"),
        ]
        assert db.execute("select due_at from penalties where id=2").fetchone()[0] == "2026-09-04 00:00:00"
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "insert into penalties "
                "(stream_id,participant_id,reason,issued_for_date,status,issued_at) "
                "values (1,1,'missed_report','2026-09-10','open','2026-09-11 00:00:00')"
            )
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"


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


def test_course_migration_preserves_stream_without_starting_lessons(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "course-upgrade.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite+aiosqlite:///{db_path}")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0002_recovery"],
        cwd=root, env=env, check=True, capture_output=True,
    )
    with sqlite3.connect(db_path) as db:
        db.execute(
            "insert into streams (id,title,chat_id,created_at) "
            "values (1,'friends',-100,'2026-09-20 00:00:00')"
        )
        db.execute(
            "insert into participants (id,stream_id,tg_user_id,username,full_name,status,joined_at) "
            "values (1,1,42,'friend','Friend','active','2026-09-20 10:00:00')"
        )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root, env=env, check=True, capture_output=True,
    )
    with sqlite3.connect(db_path) as db:
        assert db.execute("select count(*) from course_runs").fetchone()[0] == 0
        assert db.execute("select count(*) from lesson_deliveries").fetchone()[0] == 0
        assert db.execute("select tg_user_id,status from participants").fetchone() == (42, "active")
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert db.execute("select version_num from alembic_version").fetchone()[0] == (
            "0004_single_open_penalty"
        )


def test_in_process_migration_preserves_application_logging(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "logging.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite+aiosqlite:///{db_path}")
    script = """
import logging
import sys
from bot.__main__ import run_migrations

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='APP:%(message)s')
logger = logging.getLogger('bot.scheduler.jobs')
handler = logging.getLogger().handlers[0]
run_migrations()
assert logging.getLogger().level == logging.INFO, 'migration reset root log level'
assert logging.getLogger().handlers[0] is handler, 'migration replaced application log handler'
assert not logger.disabled, 'migration disabled an existing application logger'
logger.info('startup-marker-after-migration')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "APP:startup-marker-after-migration" in result.stdout
    with sqlite3.connect(db_path) as db:
        assert db.execute("select version_num from alembic_version").fetchone()[0] == (
            "0004_single_open_penalty"
        )
