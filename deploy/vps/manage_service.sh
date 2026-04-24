#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="tiktok-review-bot.service"
ACTION="${1:-status}"

case "$ACTION" in
  start|stop|restart|status)
    sudo systemctl "$ACTION" "$SERVICE_NAME"
    ;;
  logs)
    sudo journalctl -u "$SERVICE_NAME" -f --no-pager
    ;;
  enable|disable)
    sudo systemctl "$ACTION" "$SERVICE_NAME"
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs|enable|disable}"
    exit 1
    ;;
esac
