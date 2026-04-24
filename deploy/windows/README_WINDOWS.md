# Windows VPS Deploy (Server 2022, no UI)

## 1) Upload source to VPS

Copy project to: `C:\apps\tiktok-review-bot`

## 2) Install dependencies (PowerShell as Administrator)

```powershell
cd C:\apps\tiktok-review-bot
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m playwright install chromium
copy .env.example .env
```

Edit `.env` and set:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `POLL_SECONDS=180`
- `DASHBOARD_HOST=0.0.0.0`
- `DASHBOARD_PORT=80` (or 8080)

## 3) Keep login session (recommended)

Copy from local machine:
- `browser_profile/`
- `seen_reviews.db`

## 4) Create startup tasks

```powershell
cd C:\apps\tiktok-review-bot
powershell -ExecutionPolicy Bypass -File .\deploy\windows\setup_tasks.ps1
```

This creates 2 tasks:
- `TikTokReviewBot` (polling bot)
- `TikTokReviewDashboard` (web dashboard)

## 5) Open firewall for dashboard

If using port 80:

```powershell
netsh advfirewall firewall add rule name="TikTokDashboard80" dir=in action=allow protocol=TCP localport=80
```

If using 8080:

```powershell
netsh advfirewall firewall add rule name="TikTokDashboard8080" dir=in action=allow protocol=TCP localport=8080
```

## 6) Verify

```powershell
schtasks /Query /TN TikTokReviewBot
schtasks /Query /TN TikTokReviewDashboard
```

Open in browser:
- `http://tb.buisyty.online/` (if port 80)
- or `http://tb.buisyty.online:8080/`
