$ErrorActionPreference = "Stop"

$AppDir = "C:\apps\tiktok-review-bot"
$RunBot = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"$AppDir\deploy\windows\run_bot.ps1\""
$RunDash = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"$AppDir\deploy\windows\run_dashboard.ps1\""

schtasks /Delete /TN "TikTokReviewBot" /F 2>$null | Out-Null
schtasks /Delete /TN "TikTokReviewDashboard" /F 2>$null | Out-Null

schtasks /Create /TN "TikTokReviewBot" /SC ONSTART /RL HIGHEST /TR $RunBot /F
schtasks /Create /TN "TikTokReviewDashboard" /SC ONSTART /RL HIGHEST /TR $RunDash /F

schtasks /Run /TN "TikTokReviewBot"
schtasks /Run /TN "TikTokReviewDashboard"

Write-Host "Scheduled tasks created and started."
