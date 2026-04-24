#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 /absolute/path/to/tiktok-review-bot"
  exit 1
fi

APP_DIR="$1"
SERVICE_NAME="tiktok-review-bot.service"
CURRENT_USER="$(whoami)"
TEMPLATE="$APP_DIR/deploy/vps/tiktok-review-bot.service.template"
TMP_FILE="/tmp/$SERVICE_NAME"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "Template not found: $TEMPLATE"
  exit 1
fi

sed \
  -e "s|__USER__|$CURRENT_USER|g" \
  -e "s|__APP_DIR__|$APP_DIR|g" \
  "$TEMPLATE" > "$TMP_FILE"

sudo mv "$TMP_FILE" "/etc/systemd/system/$SERVICE_NAME"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo "Service installed and restarted: $SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager
