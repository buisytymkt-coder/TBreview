$ErrorActionPreference = "Stop"
$AppDir = "C:\apps\tiktok-review-bot"
Set-Location $AppDir
$Python = Join-Path $AppDir ".venv\Scripts\python.exe"
$Log = Join-Path $AppDir "bot_windows.log"

& $Python "$AppDir\tiktok_review_notifier.py" --headless *>> $Log
