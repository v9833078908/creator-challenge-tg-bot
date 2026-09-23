#!/usr/bin/env bash
# Согласованный бэкап SQLite: ./scripts/backup.sh [user@host]
set -euo pipefail

REMOTE="${1:-}"
VOLUME="creator-bot-data"
STAMP="$(date +%Y-%m-%d_%H-%M-%S)"
OUT_DIR="$(cd "$(dirname "$0")/.." && pwd)/backups"
FILE="creator-bot-$STAMP.db"
mkdir -p "$OUT_DIR"

if [ -n "$REMOTE" ]; then
  ssh "$REMOTE" "docker run --rm -v $VOLUME:/data:ro -v /tmp:/out creator-bot python -m bot.backup backup /data/creator_hero.db /out/$FILE"
  scp "$REMOTE:/tmp/$FILE" "$OUT_DIR/$FILE"
  ssh "$REMOTE" "rm -f /tmp/$FILE"
else
  docker run --rm -v "$VOLUME:/data:ro" -v "$OUT_DIR:/out" creator-bot \
    python -m bot.backup backup /data/creator_hero.db "/out/$FILE"
fi

echo "✓ $OUT_DIR/$FILE"
