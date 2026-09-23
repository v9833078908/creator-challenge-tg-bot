#!/usr/bin/env bash
# Деплой на VPS через rsync: ./scripts/deploy.sh user@host [dir]
set -euo pipefail

REMOTE="${1:?usage: ./scripts/deploy.sh user@host [dir]}"
DIR="${2:-creator-hero-bot}"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "→ rsync $LOCAL_DIR → $REMOTE:$DIR"
rsync -az --delete \
  --exclude '.venv' --exclude 'venv' --exclude '__pycache__' \
  --exclude '.git' --exclude '.env' --exclude 'data' \
  --exclude '*.egg-info' --exclude '.pytest_cache' --exclude '.ruff_cache' \
  "$LOCAL_DIR/" "$REMOTE:$DIR/"

echo "→ build & restart on $REMOTE"
ssh "$REMOTE" "cd $DIR && docker compose up -d --build && docker compose ps"

echo "✓ deployed. .env на сервере не трогаем (настраивается вручную на VPS)."
