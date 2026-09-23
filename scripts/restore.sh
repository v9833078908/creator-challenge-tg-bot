#!/usr/bin/env bash
# Восстановить локальный compose volume: ./scripts/restore.sh backups/creator-bot-....db
set -euo pipefail

BACKUP="${1:?usage: ./scripts/restore.sh path/to/backup.db}"
BACKUP_DIR="$(cd "$(dirname "$BACKUP")" && pwd)"
BACKUP_FILE="$(basename "$BACKUP")"
test -f "$BACKUP_DIR/$BACKUP_FILE"

docker compose stop bot
trap 'docker compose up -d bot' EXIT
docker run --rm -v creator-bot-data:/data -v "$BACKUP_DIR:/backup:ro" creator-bot \
  python -m bot.backup restore "/backup/$BACKUP_FILE" /data/creator_hero.db
docker compose up -d bot
trap - EXIT
echo "✓ restored $BACKUP_FILE"
