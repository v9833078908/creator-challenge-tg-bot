#!/usr/bin/env python3
"""Consistent WriteFreely snapshot and isolated restore verification."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

DATA = Path("/var/lib/writefreely")
BACKUPS = Path("/var/backups/creator-writefreely")
CONFIG = Path("/etc/writefreely/config.ini")
ADMIN = Path("/etc/creator-lessons/writefreely-admin.json")


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_database(path):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        counts = {}
        for table in ("posts", "users", "collections"):
            counts[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return counts


def restore(archive, destination):
    destination.mkdir(mode=0o700)
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(destination, filter="data")
    metadata = json.loads((destination / "metadata.json").read_text())
    for name, expected in metadata["files"].items():
        path = destination / name
        if not path.resolve().is_relative_to(destination.resolve()):
            raise RuntimeError("Invalid path in backup manifest")
        if checksum(path) != expected:
            raise RuntimeError(f"Backup checksum mismatch: {name}")
    counts = inspect_database(destination / "database.sqlite")
    if counts != metadata["counts"]:
        raise RuntimeError("Restored database counts differ from snapshot")
    return {"integrity_check": "ok", "restored_counts": counts, "checksums_match": True}


def verify(archive):
    with tempfile.TemporaryDirectory(prefix="creator-writefreely-restore-") as temporary:
        return restore(archive, Path(temporary) / "restored")


def backup():
    BACKUPS.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    archive = BACKUPS / f"writefreely-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=BACKUPS) as temporary:
        snapshot = Path(temporary)
        database = snapshot / "database.sqlite"
        with (
            sqlite3.connect(f"file:{DATA / 'writefreely.db'}?mode=ro", uri=True) as source,
            sqlite3.connect(database) as target,
        ):
            source.backup(target)
        shutil.copyfile(CONFIG, snapshot / "config.ini")
        shutil.copyfile(ADMIN, snapshot / "admin-credentials.json")
        shutil.copytree(DATA / "keys", snapshot / "keys")
        metadata = {
            "created_utc": stamp,
            "release": "0.17.2",
            "counts": inspect_database(database),
            "files": {
                path.relative_to(snapshot).as_posix(): checksum(path)
                for path in snapshot.rglob("*")
                if path.is_file()
            },
        }
        (snapshot / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        pending = snapshot / "backup.tar.gz"
        with tarfile.open(pending, "w:gz") as bundle:
            for name in ("database.sqlite", "config.ini", "admin-credentials.json", "keys", "metadata.json"):
                bundle.add(snapshot / name, arcname=name)
        result = verify(pending)
        os.replace(pending, archive)
    archive.chmod(0o600)
    return {"archive": str(archive), "sha256": checksum(archive), **result}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "verify", "restore"))
    parser.add_argument("archive", type=Path, nargs="?")
    parser.add_argument("--restore-to", type=Path)
    args = parser.parse_args()
    if args.action == "backup":
        result = backup()
    elif not args.archive:
        parser.error("archive is required for verify/restore")
    elif args.action == "restore":
        if not args.restore_to:
            parser.error("--restore-to must name a new directory")
        result = restore(args.archive, args.restore_to)
    else:
        result = verify(args.archive)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
