@echo off
rem ============================================================
rem  SongBot background launcher (songbot / S7)
rem  ASCII only on purpose: batch files are parsed in the OEM
rem  codepage; keep this file pure ASCII.
rem
rem  Opens a NEW detached console window titled "SongBot" running
rem  the songbot bot (python -m songbot.bot), then returns
rem  immediately (background mount).
rem
rem  Independent from the M7 news bot: separate process, separate
rem  window, separate scripts (S7 requirement 4).
rem  Server-migration friendly: all paths are relative to %~dp0..
rem  (S7 requirement 1); the same scripts move with the repo.
rem
rem  Stop: Ctrl+C inside that window / run scripts\stop_songbot.cmd
rem  (graceful: writes data\songbot.stop, bot sends the shutdown
rem  notice to notify_groups, then exits).
rem
rem  Usage:  start_songbot.cmd [extra args forwarded to bot.py]
rem          e.g. start_songbot.cmd --dry-run
rem ============================================================
setlocal
set "PYTHONUTF8=1"
set "ROOT=%~dp0.."

rem Remove any stale stop file from a previous force-kill run
rem (otherwise the bot would immediately stop itself on start).
if exist "%ROOT%\data\songbot.stop" del "%ROOT%\data\songbot.stop"

rem Best-effort: restore NapCat event-reporting config (httpClients ->
rem 127.0.0.1:8090/event) BEFORE starting the bot. NapCat Desktop may
rem have wiped it on restart (known behavior), which would leave the
rem bot unable to receive @bot events. Idempotent: does nothing when
rem the entry already exists. Failure only warns - the bot still
rem starts (startup/shutdown notices still work via the 3000 channel).
echo Checking NapCat event-reporting config...
python "%~dp0restore_napcat_webhook.py"
if errorlevel 1 (
    echo [WARN] NapCat webhook restore failed - bot may not receive @bot events.
    echo        Fix later with: python scripts\restore_napcat_webhook.py
)

rem Collect extra args to forward (--dry-run, --port, ...)
set "ARGS="
:loop
if "%~1"=="" goto :run
set "ARGS=%ARGS% %~1"
shift
goto :loop

:run
start "SongBot" cmd /c ""chcp 65001 >nul && cd /d "%ROOT%" && python -m songbot.bot%ARGS%""
echo SongBot started in a new background window (title: "SongBot").
echo Stop: Ctrl+C in that window / run scripts\stop_songbot.cmd (graceful stop).
endlocal
