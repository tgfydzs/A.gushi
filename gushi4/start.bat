@echo off
REM ============================================================
REM  gushi4 launcher - forwards to start.py
REM
REM  THIS FILE MUST STAY PURE ASCII.
REM  cmd.exe parses a .bat byte-by-byte using the active codepage,
REM  so non-ASCII text here gets mis-parsed and a comment line can
REM  end up being executed as a command. That already happened once.
REM ============================================================
cd /d "%~dp0"
title gushi4

set "PY="

REM Prefer the py launcher. It lives in C:\Windows and is on the
REM SYSTEM path even when the interpreter itself was never added
REM to PATH -- which is exactly the situation on this machine.
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY=py -3"

if not defined PY (
    python --version >nul 2>nul
    if not errorlevel 1 set "PY=python"
)

REM Last resort: look where the official installer puts it by default.
if not defined PY (
    for %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if exist "%%~fD\python.exe" set "PY=%%~fD\python.exe"
    )
)

if not defined PY (
    echo.
    echo   Python not found.
    echo.
    echo   Install Python 3.10+ from https://www.python.org/downloads/
    echo   and tick "Add python.exe to PATH" during setup.
    echo.
    echo   If it is already installed, open a terminal and run:
    echo       python start.py
    echo.
    pause
    exit /b 1
)

echo   using: %PY%
%PY% start.py %*
if errorlevel 1 pause
