@echo off
rem ============================================================
rem  M7 background launcher (ASCII only on purpose: batch files
rem  are parsed in the OEM codepage; keep this file pure ASCII).
rem
rem  Opens a NEW detached console window titled "M7 Bot" running
rem  the orchestrator, then returns immediately (background mount).
rem  Inside that window: pick brands interactively, logs scroll.
rem  Stop: Ctrl+C / close the window / run scripts\stop_bot.cmd
rem
rem  Usage:  start_bot.cmd [--brands SHINYCOLORS,GAKUEN] [--interval 300]
rem  (extra args are forwarded verbatim to src\main.py)
rem ============================================================
setlocal
set "PYTHONUTF8=1"
set "ROOT=%~dp0.."

rem Collect extra args to forward (--brands, --interval, ...)
set "ARGS="
:loop
if "%~1"=="" goto :run
set "ARGS=%ARGS% %~1"
shift
goto :loop

:run
start "M7 Bot" cmd /k ""chcp 65001 >nul && cd /d "%ROOT%" && python src\main.py%ARGS%""
echo M7 Bot started in a new background window (title: "M7 Bot").
echo Pick brands inside that window; logs scroll there.
echo Stop: Ctrl+C / close that window / run scripts\stop_bot.cmd
endlocal
