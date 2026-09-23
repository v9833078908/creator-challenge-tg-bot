"""Проверка, что event loop регулярно выполняется."""

import asyncio
import time
from pathlib import Path

HEARTBEAT = Path("/tmp/creator-bot-heartbeat")


def is_healthy(path: Path = HEARTBEAT, *, now: float | None = None) -> bool:
    try:
        age = (time.time() if now is None else now) - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return 0 <= age < 120


async def heartbeat_loop(path: Path = HEARTBEAT) -> None:
    while True:
        path.touch()
        await asyncio.sleep(20)


if __name__ == "__main__":
    raise SystemExit(0 if is_healthy() else 1)
