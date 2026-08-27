@echo off
rem ============================================================
rem  Graceful stop for SongBot (start_songbot.cmd).
rem
rem  1) Writes data\songbot.stop -> the bot detects it (5s poll),
rem     sends the shutdown notice to notify_groups, then exits.
rem  2) Waits up to ~40s for the songbot process to disappear.
rem     The bot is detected by command line ("python -m songbot.bot"),
rem     NOT by window title: window titles are unreliable in
rem     non-interactive sessions / Windows services (S7 test finding).
rem  3) Fallback: force-kill that process /T /F
rem     (NOTE: force-kill skips the shutdown notice).
rem
rem  Primary stop is Ctrl+C inside the SongBot window; this script
rem  is the background-mount companion (S7).
rem ============================================================
setlocal
set "ROOT=%~dp0.."
set "STOP_FILE=%ROOT%\data\songbot.stop"

if not exist "%ROOT%\data" mkdir "%ROOT%\data"
echo Requesting graceful stop (writing data\songbot.stop)...
> "%STOP_FILE%" echo.

rem --- find the songbot bot process id by command line ---
set "SB_PID="
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'songbot.bot' } | Select-Object -ExpandProperty ProcessId"`) do set "SB_PID=%%P"
if "%SB_PID%"=="" goto done

echo SongBot process found: %SB_PID% - waiting for graceful exit...
set /a waited=0
:waitloop
tasklist /FI "PID eq %SB_PID%" 2>nul | find "%SB_PID%" >nul
if errorlevel 1 goto done
set /a waited+=2
if %waited% geq 40 goto force
timeout /t 2 /nobreak >nul
goto waitloop

:force
echo Process %SB_PID% still alive after %waited%s - force killing...
taskkill /PID %SB_PID% /T /F >nul 2>&1
if errorlevel 1 (
    echo Force-kill failed (process may already be gone).
) else (
    echo SongBot force-stopped (shutdown notice was skipped).
)
goto end

:done
echo SongBot stopped gracefully.
:end
endlocal
