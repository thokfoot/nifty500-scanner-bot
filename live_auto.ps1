# live_auto.ps1 - local broker-mode loop (same behavior as GitHub workflow)
# Usage:  powershell -File live_auto.ps1          (runs forever, 15-min interval)
#         powershell -File live_auto.ps1 -Once    (single run)

param([switch]$Once)

$IntervalSec = 900   # 15 minutes

function Invoke-BotRun {
    Write-Host "=== BOT RUN $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" -ForegroundColor Cyan
    $env:EXECUTE_NOW = "1"
    $env:LIVE_MODE = "1"
    python main.py
    Remove-Item Env:EXECUTE_NOW -ErrorAction SilentlyContinue
    Remove-Item Env:LIVE_MODE -ErrorAction SilentlyContinue

    git add data/ logs/ -f 2>$null
    git diff --cached --quiet 2>$null
    if ($LASTEXITCODE -ne 0) {
        $ts = Get-Date -Format 'yyyy-MM-dd HH:mm'
        git commit -m "state: bot run $ts IST" 2>$null
        git pull --rebase origin main 2>$null
        git push 2>$null
        Write-Host "State committed + pushed" -ForegroundColor Green
    } else {
        Write-Host "No state changes" -ForegroundColor DarkGray
    }
}

if ($Once) {
    Invoke-BotRun
    exit 0
}

while ($true) {
    $now = Get-Date
    # Run only weekdays; pause outside 08:30-16:45 IST window
    $isWeekday = $now.DayOfWeek -ge [DayOfWeek]::Monday -and $now.DayOfWeek -le [DayOfWeek]::Friday
    $inWindow = ($now.Hour -gt 8 -or ($now.Hour -eq 8 -and $now.Minute -ge 30)) -and
                ($now.Hour -lt 16 -or ($now.Hour -eq 16 -and $now.Minute -le 45))

    if ($isWeekday -and $inWindow) {
        Invoke-BotRun
        Start-Sleep -Seconds $IntervalSec
    } else {
        Write-Host "Outside market window - sleeping 5 min" -ForegroundColor DarkGray
        Start-Sleep -Seconds 300
    }
}
