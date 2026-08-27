@echo off
rem ============================================================
rem  Stop the M7 Bot window launched by scripts\start_bot.cmd
rem  Kills the cmd window titled "M7 Bot" and its process tree
rem  (the python orchestrator child). Other cmd windows are safe.
rem  Primary stop is Ctrl+C inside the window; this is a backup.
rem ============================================================
taskkill /FI "WINDOWTITLE eq M7 Bot*" /T /F >nul 2>&1
if errorlevel 1 (
    echo No window titled "M7 Bot" found - it may already be stopped,
    echo or the window title was changed. You can also stop it by
    echo pressing Ctrl+C inside the M7 Bot window.
) else (
    echo M7 Bot stopped.
)
