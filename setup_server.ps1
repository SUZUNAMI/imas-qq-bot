# setup_server.ps1 - M9 deploy script for Windows Server
# Run as Administrator from the project root (where this file lives).
# Steps: check Python/Edge, install deps, generate config, smoke test.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "=== [1/6] Python check ===" -ForegroundColor Cyan
$pyOut = $null
try { $pyOut = (python --version 2>&1) } catch {}
if ($pyOut -match 'Python (\d+)\.(\d+)') {
    $maj = [int]$Matches[1]; $min = [int]$Matches[2]
    Write-Host "Python found: $pyOut"
    if ($maj -lt 3 -or ($maj -eq 3 -and $min -lt 11)) {
        Write-Host "ERROR: need Python 3.11+, got $pyOut. Install from python.org (check 'Add to PATH')." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "ERROR: Python not found. Install Python 3.11+ (check 'Add to PATH'), then rerun." -ForegroundColor Red
    exit 1
}

Write-Host "=== [2/6] Edge check (songbot image render) ===" -ForegroundColor Cyan
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($edge) {
    Write-Host "Edge found: $edge"
} else {
    Write-Host "WARN: Edge not found. songbot image rendering needs Edge (playwright channel=msedge)." -ForegroundColor Yellow
    Write-Host "      Install from https://www.microsoft.com/edge/download or run: python -m playwright install chromium"
}

Write-Host "=== [3/6] Install Python deps ===" -ForegroundColor Cyan
python -m pip install --upgrade pip
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
pip install playwright -i https://mirrors.aliyun.com/pypi/simple/

Write-Host "=== [4/6] Generate config.yaml from template ===" -ForegroundColor Cyan
if (-not (Test-Path config.yaml)) {
    Copy-Item config.example.yaml config.yaml
    Write-Host "Created config.yaml - EDIT IT: napcat.group_ids, napcat.base_url, songbot.notify_groups, orchestrator.notify_groups" -ForegroundColor Yellow
} else {
    Write-Host "config.yaml already exists, keeping it."
}

Write-Host "=== [5/6] Generate .env from template ===" -ForegroundColor Cyan
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Created .env - EDIT IT: DEEPSEEK_API_KEY, NAPCAT_BASE_URL, NAPCAT_GROUP_IDS, NAPCAT_INTERVAL_SEC" -ForegroundColor Yellow
} else {
    Write-Host ".env already exists, keeping it."
}

Write-Host "=== [6/6] Smoke test ===" -ForegroundColor Cyan
$env:PYTHONPATH = "$root\vendor"
Write-Host "--- songbot --dry-run (first 20 lines) ---"
python -m songbot.bot --dry-run 2>&1 | Select-Object -First 20
Write-Host "--- main import check ---"
python -c "import sys; sys.path.insert(0, 'src'); import main; print('main import OK')" 2>&1 | Select-Object -First 5

Write-Host ""
Write-Host "=== DONE ===" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Edit config.yaml / .env (group ids, DeepSeek API key, NapCat url)"
Write-Host "  2. Install NapCatQQ + NTQQ on this server, login bot account, configure OneBot:"
Write-Host "     HTTP server 127.0.0.1:3000 + postUrls http://127.0.0.1:8090/event (songbot)"
Write-Host "  3. Run M7:   python src/main.py        (or scripts\start_bot.cmd)"
Write-Host "     Run songbot: python -m songbot.bot  (or scripts\start_songbot.cmd)"
Write-Host "  4. Optional: register as Windows services (WinSW) for auto-start"
