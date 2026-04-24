# Deploy to VPS (Prepared Package)

## 1) Build bundle on local machine

```bash
cd /Users/buisyty/Thongbao/tiktok-review-bot
bash deploy/make_bundle.sh
```

Bundle will be created in `dist/`.

## 2) Copy to VPS

```bash
scp dist/tiktok-review-bot-vps-*.tar.gz user@YOUR_VPS_IP:/home/user/
```

## 3) Extract on VPS

```bash
ssh user@YOUR_VPS_IP
mkdir -p /home/user/apps/tiktok-review-bot
cd /home/user/apps/tiktok-review-bot
tar -xzf /home/user/tiktok-review-bot-vps-*.tar.gz
```

## 4) Install runtime dependencies on VPS

```bash
cd /home/user/apps/tiktok-review-bot
bash deploy/vps/install_on_vps.sh /home/user/apps/tiktok-review-bot
```

## 5) Configure `.env`

```bash
cd /home/user/apps/tiktok-review-bot
nano .env
```

Recommended values:
- `POLL_SECONDS=180`
- `SEND_STARTUP_MESSAGE=false`

## 6) Keep login session (recommended)

To reduce re-login, copy browser profile and seen db from local to VPS.

From local machine:

```bash
scp -r /Users/buisyty/Thongbao/tiktok-review-bot/browser_profile user@YOUR_VPS_IP:/home/user/apps/tiktok-review-bot/
scp /Users/buisyty/Thongbao/tiktok-review-bot/seen_reviews.db user@YOUR_VPS_IP:/home/user/apps/tiktok-review-bot/
```

Then on VPS:

```bash
cd /home/user/apps/tiktok-review-bot
chmod -R 700 browser_profile
```

## 7) Setup systemd service

```bash
cd /home/user/apps/tiktok-review-bot
bash deploy/vps/setup_service.sh /home/user/apps/tiktok-review-bot
```

## 8) Manage service

```bash
cd /home/user/apps/tiktok-review-bot
bash deploy/vps/manage_service.sh status
bash deploy/vps/manage_service.sh logs
bash deploy/vps/manage_service.sh restart
```

## 9) Login troubleshooting on VPS

If TikTok invalidates session, run interactive login once on VPS (non-headless), then restart service:

```bash
cd /home/user/apps/tiktok-review-bot
source .venv/bin/activate
python tiktok_review_notifier.py --once
bash deploy/vps/manage_service.sh restart
```

If VPS has no GUI, use one of:
- temporary X11 forwarding
- VNC desktop session
- copy refreshed `browser_profile` from local machine again.
