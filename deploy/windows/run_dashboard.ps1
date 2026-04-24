$ErrorActionPreference = "Stop"
$AppDir = "C:\apps\tiktok-review-bot"
Set-Location $AppDir
$Python = Join-Path $AppDir ".venv\Scripts\python.exe"
$Log = Join-Path $AppDir "dashboard_windows.log"

& $Python "$AppDir\dashboard_server.py" *>> $Log
