#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C
export LANG=C

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DIST_DIR/tiktok-review-bot-vps-${STAMP}.tar.gz"

mkdir -p "$DIST_DIR"

cd "$ROOT_DIR"

tar \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='dist' \
  --exclude='browser_profile' \
  --exclude='seen_reviews.db' \
  --exclude='bot.log' \
  --exclude='bot.pid' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.env' \
  -czf "$OUT" \
  .

echo "Bundle created: $OUT"
