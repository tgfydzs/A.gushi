@echo off
REM ============================================================
REM  Self-elevating wrapper around setup_python_path.ps1
REM
REM  PURE ASCII ONLY. See start.bat for the full explanation:
REM  cmd.exe parses .bat files byte-by-byte using the active
REM  codepage, so non-ASCII here gets mis-parsed and a comment
REM  line can end up executed as a command.
REM ============================================================

REM Already elevated? net session succeeds only as admin.
net session >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo   Needs administrator rights. A UAC prompt will appear.
    echo.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_python_path.ps1"

echo.
pause
