@echo off
setlocal enabledelayedexpansion
title RxRaksha Prescription Safety System

echo =========================================================
echo   RxRaksha - Prescription Safety & Decision Support
echo =========================================================
echo.

:: Ensure we are in the script's root directory
cd /d "%~dp0"

:: 1. Check if Python is installed
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not found in your PATH.
    echo Please install Python 3.10+ from https://www.python.org/
    echo and ensure "Add Python to PATH" is checked during installation.
    echo.
    pause
    exit /b 1
)

echo [1/3] Python detected:
python --version
echo.

:: 2. Check for backend folder
if not exist "backend" (
    echo [ERROR] "backend" directory not found in "%~dp0".
    echo Please run this batch file from the RxRaksha project root.
    echo.
    pause
    exit /b 1
)

:: 3. Optional: Install/Verify dependencies
echo [2/3] Checking dependencies...
if exist "backend\requirements.txt" (
    pip install -q -r backend\requirements.txt
) else (
    echo [WARN] requirements.txt not found, skipping package verification.
)
echo Dependencies verified.
echo.

:: 4. Navigate to backend and start server
cd backend

echo [3/3] Starting RxRaksha Server at http://127.0.0.1:8000 ...
echo Press CTRL+C at any time in this window to stop the server.
echo.

:: Wait 2 seconds and open browser automatically
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8000"

:: Start Uvicorn ASGI server
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

pause
