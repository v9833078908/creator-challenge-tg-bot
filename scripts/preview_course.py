#!/usr/bin/env python3
"""Finite owner-only preview. Never polls Telegram or writes the bot database."""

import fcntl
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

STATE = Path("/var/lib/creator-course-preview/state.json")


class PreviewStopped(Exception):
    """Manual review is needed before any further preview delivery."""


def atomic_state(path, state):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as output:
        json.dump(state, output)
        output.flush()
        os.fsync(output.fileno())
        temporary = output.name
    os.replace(temporary, path)
    path.chmod(0o600)


class Preview:
    def __init__(self, api, path, owner_fingerprint, catalog_hash, count=33):
        self.api, self.path, self.count = api, path, count
        self.state = (
            json.loads(path.read_text())
            if path.exists()
            else {
                "owner_fingerprint": owner_fingerprint,
                "catalog_sha256": catalog_hash,
                "count": count,
                "items": {},
            }
        )
        if (
            self.state["owner_fingerprint"] != owner_fingerprint
            or self.state["catalog_sha256"] != catalog_hash
            or self.state["count"] != count
        ):
            raise PreviewStopped("Owner or catalog changed; no preview sent")
        if any(x["status"] != "sent" for x in self.state["items"].values()):
            raise PreviewStopped("Unfinished delivery requires review; no automatic repeat")
        days = sorted(map(int, self.state["items"]))
        if days != list(range(1, len(days) + 1)):
            raise PreviewStopped("Invalid preview progress")

    def next_due(self, now):
        sent = self.state["items"]
        if len(sent) >= self.count:
            return None
        if not sent:
            return now
        return sent[str(len(sent))]["confirmed_at"] + 60

    def tick(self, now, clock=time.time):
        due = self.next_due(now)
        if due is None or now < due or not self.api.eligible():
            return False
        day = len(self.state["items"]) + 1
        row = {"status": "sending", "claimed_at": now}
        self.state["items"][str(day)] = row
        atomic_state(self.path, self.state)
        try:
            message_id = self.api.send(day)
        except Exception as exc:
            row.update(status="uncertain", error_type=type(exc).__name__)
            atomic_state(self.path, self.state)
            raise PreviewStopped("Delivery outcome uncertain; automatic preview stopped") from None
        row.update(status="sent", confirmed_at=max(now, clock()), message_id=message_id)
        atomic_state(self.path, self.state)
        return True


class OwnerAPI:
    def __init__(self):
        # Captured only into this process memory; never printed or persisted.
        script = """import json,sqlite3
from pathlib import Path
from bot.config import settings
owner=settings.owner_tg_id
c=sqlite3.connect('file:/app/data/creator_hero.db?mode=ro',uri=True)
sql=('SELECT p.id FROM participants p JOIN course_runs r '
     'ON r.participant_id=p.id WHERE p.tg_user_id=?')
rows=c.execute(sql,(owner,)).fetchall()
if owner<=0 or len(rows)!=1:raise ValueError('Configured owner is not the activated participant')
print(json.dumps({'token':settings.bot_token,'owner':owner,'participant_id':rows[0][0],'catalog':json.loads(Path('/app/bot/data/lesson-catalog.json').read_text())}))
"""
        result = subprocess.run(
            ["docker", "exec", "-i", "creator-bot", "python", "-"],
            input=script,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise PreviewStopped("Owner identity verification failed")
        data = json.loads(result.stdout)
        self._token, self._owner = data["token"], data["owner"]
        self._participant = data["participant_id"]
        volume = subprocess.run(
            ["docker", "volume", "inspect", "creator-bot-data", "--format", "{{.Mountpoint}}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        self._database = Path(volume) / "creator_hero.db"
        self.catalog = data["catalog"]
        self.owner_fingerprint = hashlib.sha256(str(self._owner).encode()).hexdigest()
        self.catalog_hash = hashlib.sha256(json.dumps(self.catalog, sort_keys=True).encode()).hexdigest()
        if self.catalog["count"] != 33 or [x["day"] for x in self.catalog["items"]] != list(range(1, 34)):
            raise PreviewStopped("Expected exactly 33 consecutive lessons")
        chat = self.call("getChat", {"chat_id": self._owner})
        if chat["id"] != self._owner or chat["type"] != "private":
            raise PreviewStopped("Owner private chat could not be verified")

    def eligible(self):
        try:
            with sqlite3.connect(f"file:{self._database}?mode=ro", uri=True, timeout=3) as db:
                row = db.execute(
                    "SELECT p.status,s.is_active FROM participants p "
                    "JOIN streams s ON s.id=p.stream_id WHERE p.id=? AND p.tg_user_id=?",
                    (self._participant, self._owner),
                ).fetchone()
            return bool(row and row[0] in {"approved", "active"} and row[1])
        except (sqlite3.Error, OSError):
            return False

    def call(self, method, data):
        request = Request(
            "https://api.telegram.org/bot" + self._token + "/" + method,
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                result = json.load(response)
        except Exception:
            raise PreviewStopped("Telegram request failed; details withheld") from None
        if not result.get("ok"):
            raise PreviewStopped("Telegram rejected the request; details withheld")
        return result["result"]

    def send(self, day):
        lesson = self.catalog["items"][day - 1]
        message = self.call(
            "sendMessage",
            {
                "chat_id": self._owner,
                "text": lesson["text"],
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {
                                "text": lesson["button_text"],
                                "url": lesson["article_url"],
                            }
                        ]
                    ]
                },
                "link_preview_options": {"is_disabled": True},
            },
        )
        return message["message_id"]


def main():
    os.umask(0o077)
    STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with STATE.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        api = OwnerAPI()
        preview = Preview(api, STATE, api.owner_fingerprint, api.catalog_hash)
        print("Owner private chat and 33-lesson catalog verified", flush=True)
        while (due := preview.next_due(time.time())) is not None:
            now = time.time()
            if now < due:
                time.sleep(min(60, due - now))
                continue
            if preview.tick(now):
                print(json.dumps({"sent": len(preview.state["items"]), "total": 33}), flush=True)
            else:
                time.sleep(60)
        print("Finite preview completed: 33/33", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (PreviewStopped, BlockingIOError, ValueError, KeyError):
        print("Preview stopped safely; inspect root-only state before any retry", flush=True)
        raise SystemExit(1) from None
