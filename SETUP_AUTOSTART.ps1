# Dalal Street Scout — One-time Auto-start Setup
# Run this script ONCE as Administrator
# Right-click SETUP_AUTOSTART.ps1 -> "Run with PowerShell" -> allow when asked

Write-Host ""
Write-Host "======================================================"
Write-Host "  Dalal Street Scout — Auto-start Setup"
Write-Host "======================================================"
Write-Host ""

# ── Check for Admin ──────────────────────────────────────────────
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]"Administrator")) {
    Write-Host "ERROR: Please run this script as Administrator."
    Write-Host "Right-click the file -> Run with PowerShell -> Yes"
    pause
    exit
}

# ── Enable Wake Timers in current power plan ─────────────────────
Write-Host "Enabling wake timers in power plan..."
powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg /setactive SCHEME_CURRENT
Write-Host "  Done."

# ── Task 1: Wake PC + Start server at 7:30 AM (Mon-Fri) ─────────
Write-Host ""
Write-Host "Creating Task Scheduler task: Start server at 7:30 AM..."

$action1   = New-ScheduledTaskAction -Execute "d:\Dalal_street\MARKET_START.bat"
$trigger1  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "7:30AM"
$settings1 = New-ScheduledTaskSettingsSet -WakeToRun -ExecutionTimeLimit "00:05:00" -StartWhenAvailable
$principal1 = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType Interactive

Register-ScheduledTask `
    -TaskName  "Dalal Street - Start Server" `
    -Action    $action1 `
    -Trigger   $trigger1 `
    -Settings  $settings1 `
    -Principal $principal1 `
    -Force | Out-Null

Write-Host "  Done."

# ── Task 2: Restore sleep at 4:00 PM (Mon-Fri) ──────────────────
Write-Host "Creating Task Scheduler task: Restore sleep at 4:00 PM..."

$action2   = New-ScheduledTaskAction -Execute "d:\Dalal_street\RESTORE_SLEEP.bat"
$trigger2  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "4:00PM"
$settings2 = New-ScheduledTaskSettingsSet -ExecutionTimeLimit "00:01:00" -StartWhenAvailable
$principal2 = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType Interactive

Register-ScheduledTask `
    -TaskName  "Dalal Street - Restore Sleep" `
    -Action    $action2 `
    -Trigger   $trigger2 `
    -Settings  $settings2 `
    -Principal $principal2 `
    -Force | Out-Null

Write-Host "  Done."

# ── Summary ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "======================================================"
Write-Host "  Setup complete!"
Write-Host "======================================================"
Write-Host ""
Write-Host "What happens automatically every weekday:"
Write-Host "  7:30 AM  - PC wakes from sleep"
Write-Host "  7:30 AM  - Dalal Street Scout starts scanning"
Write-Host "  9:15 AM  - Market opens, scanner is ready"
Write-Host "  4:00 PM  - Normal sleep restored"
Write-Host ""
Write-Host "======================================================"
Write-Host "  Next step: Set up Tailscale for phone access"
Write-Host "======================================================"
Write-Host ""
Write-Host "1. Download Tailscale on this PC:"
Write-Host "   https://tailscale.com/download/windows"
Write-Host ""
Write-Host "2. Download Tailscale on your phone:"
Write-Host "   Android: Play Store -> search 'Tailscale'"
Write-Host "   iPhone:  App Store  -> search 'Tailscale'"
Write-Host ""
Write-Host "3. Sign in with the SAME account on both devices"
Write-Host "   (Google/GitHub login works)"
Write-Host ""
Write-Host "4. On PC: open Tailscale in system tray -> note your"
Write-Host "   Tailscale IP (looks like 100.x.x.x)"
Write-Host ""
Write-Host "5. On phone browser open:"
Write-Host "   http://100.x.x.x:5000"
Write-Host ""
Write-Host "Done! Your scanner is now accessible from anywhere!"
Write-Host ""
pause
