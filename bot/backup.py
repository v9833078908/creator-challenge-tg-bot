"""Согласованные копии SQLite и проверяемое восстановление."""

import argparse
import os
import sqlite3
import tempfile
from pathlib import Path


def _copy_database(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        with sqlite3.connect(source) as src, sqlite3.connect(temp) as dst:
            if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError(f"SQLite integrity check failed: {source}")
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError(f"SQLite integrity check failed: {temp}")
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def backup_database(source: Path, destination: Path) -> None:
    _copy_database(source, destination)


def restore_database(backup: Path, destination: Path) -> None:
    _copy_database(backup, destination)
    for suffix in ("-wal", "-shm"):
        Path(f"{destination}{suffix}").unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.action == "backup":
        backup_database(args.source, args.destination)
    else:
        restore_database(args.source, args.destination)


if __name__ == "__main__":
    main()
