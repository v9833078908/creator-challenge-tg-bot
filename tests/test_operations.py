import sqlite3
from pathlib import Path

from bot.backup import backup_database, restore_database
from bot.healthcheck import is_healthy


def test_backup_and_restore_round_trip(tmp_path: Path):
    source = tmp_path / "live.db"
    backup = tmp_path / "backup.db"
    restored = tmp_path / "restored.db"
    with sqlite3.connect(source) as db:
        db.execute("create table reports (value text not null)")
        db.execute("insert into reports values ('друг')")

    (tmp_path / "restored.db-wal").write_bytes(b"stale")
    (tmp_path / "restored.db-shm").write_bytes(b"stale")

    backup_database(source, backup)
    restore_database(backup, restored)
    assert not (tmp_path / "restored.db-wal").exists()
    assert not (tmp_path / "restored.db-shm").exists()

    with sqlite3.connect(restored) as db:
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert db.execute("select value from reports").fetchone()[0] == "друг"


def test_healthcheck_rejects_stale_heartbeat(tmp_path: Path):
    heartbeat = tmp_path / "heartbeat"
    heartbeat.write_text("ok")
    assert is_healthy(heartbeat, now=heartbeat.stat().st_mtime + 10)
    assert not is_healthy(heartbeat, now=heartbeat.stat().st_mtime + 150)
