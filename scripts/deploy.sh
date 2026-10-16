#!/usr/bin/env bash
# Деплой на VPS через rsync: ./scripts/deploy.sh user@host [dir] [--stage-only]
set -euo pipefail

REMOTE="${1:?usage: ./scripts/deploy.sh user@host [dir]}"
DIR="${2:-creator-hero-bot}"
MODE="${3:---start}"
if [ "$DIR" = "--stage-only" ] || [ "$DIR" = "--start" ]; then
  MODE="$DIR"
  DIR="creator-hero-bot"
fi
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$MODE" != "--start" ] && [ "$MODE" != "--stage-only" ]; then
  echo "usage: ./scripts/deploy.sh user@host [dir] [--stage-only]" >&2
  exit 2
fi

echo "→ rsync $LOCAL_DIR → $REMOTE:$DIR"
rsync -az --delete \
  --include '/bot/data/' --include '/bot/data/lesson-catalog.json' \
  --exclude '/bot/data/*' \
  --exclude '.venv' --exclude 'venv' --exclude '__pycache__' \
  --exclude '.git' --exclude '.env' --exclude 'data' \
  --exclude 'backups' --exclude '.omx' \
  --exclude '*.egg-info' --exclude '.pytest_cache' --exclude '.ruff_cache' \
  "$LOCAL_DIR/" "$REMOTE:$DIR/"

if [ "$MODE" = "--stage-only" ]; then
  echo "✓ code staged on $REMOTE:$DIR; container not started"
  exit 0
fi

echo "→ build & restart on $REMOTE"
ssh "$REMOTE" "cd $DIR && docker compose up -d --build && docker compose ps"

echo "✓ deployed. .env на сервере не трогаем (настраивается вручную на VPS)."
