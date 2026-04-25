# TikTok Shop US Review Notifier -> Telegram

## 1) Local setup

```bash
cd /Users/buisyty/Thongbao/tiktok-review-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
```

Edit `.env`:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## 2) First login (interactive)

Run once without headless so browser opens and you can login TikTok Seller Center:

```bash
python tiktok_review_notifier.py --once
```

- Browser profile is saved at `browser_profile/`.
- On first run, script will collect visible reviews and send new ones to Telegram.

## 3) Normal polling (local)

After login is stored, run:

```bash
python tiktok_review_notifier.py --headless
```

Default polling interval: `300` seconds (`POLL_SECONDS` in `.env`).

If login takes time (QR/OTP/captcha), increase `LOGIN_WAIT_SECONDS` in `.env` (default `300`).

## 4) Useful notes

- Seen review IDs are stored in `seen_reviews.db` to avoid duplicate alerts.
- If TikTok UI changes, parser may need selector updates.
- To re-send everything for testing, stop script and delete `seen_reviews.db`.
- If you want to skip manual login, set `TIKTOK_COOKIES_FILE` in `.env` to a JSON cookie file path.

## 5) Quick test Telegram

You can force startup ping by setting:

```env
SEND_STARTUP_MESSAGE=true
```

Then run once to check bot delivery.

## 5.1) Cookie login mode (optional)

1. Export cookies from a logged-in browser session to `cookies.json` (array format or `{ \"cookies\": [...] }`).
2. Put file in project root: `tiktok-review-bot/cookies.json`.
3. Set in `.env`:

```env
TIKTOK_COOKIES_FILE=cookies.json
```

4. Run:

```bash
python tiktok_review_notifier.py --headless
```

If your extension exports `cookies.txt` (Netscape format), convert first:

```bash
python tools/convert_cookies_txt.py /path/to/cookies.txt /Users/buisyty/Thongbao/tiktok-review-bot/cookies.json
```

## 6) Dashboard run history

Run dashboard locally:

```bash
source .venv/bin/activate
python dashboard_server.py
```

Open:
- `http://127.0.0.1:8080/`
- `http://127.0.0.1:8080/healthz`

## 7) VPS packaging

- Build bundle: `bash deploy/make_bundle.sh`
- VPS guide: `deploy/vps/README_VPS.md`
