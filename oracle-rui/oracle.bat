@echo off
title Oracle - AI Coding Agent
cd /d "%~dp0"

:: ─── Parse arguments ───────────────────────────────────────────
set MODE=ui
set PORT=8000
set MODEL_FLAG=

:parse
if "%~1"=="--cli" set MODE=cli& shift & goto parse
if "%~1"=="--ui" set MODE=ui& shift & goto parse
if "%~1"=="--port" set PORT=%~2& shift & shift & goto parse
if "%~1"=="--deep" set MODEL_FLAG=--deep& shift & goto parse
if "%~1"=="--pro" set MODEL_FLAG=--model-tier pro& shift & goto parse
if "%~1"=="--flash" set MODEL_FLAG=--model-tier flash& shift & goto parse
if "%~1"=="--stop" goto stop_server
if "%~1"=="--help" goto help

:: ─── Check Python ──────────────────────────────────────────────
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python not found. Make sure Python is installed and in PATH.
    echo         Download Python from: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: ─── Check .env ─────────────────────────────────────────────────
if not exist ".env" (
    echo [WARNING] .env file not found.
    echo          Copy .env.example to .env and configure your API key.
    echo.
    choice /c CN /N /M "[C]ontinue anyway or e[N]xit? (C/N) "
    if errorlevel 2 exit /b 1
)

:: ─── UI Mode (default) ─────────────────────────────────────────
if "%MODE%"=="ui" goto ui_mode

:: ─── CLI Mode ───────────────────────────────────────────────────
:cli_mode
echo.
echo  ^|  Oracle CLI - AI Coding Agent
if "%MODEL_FLAG%"=="--deep" echo  ^|  PRO mode (high-tier model)
echo  ^|  Type 'exit' or press Ctrl+C to quit
echo.
python cli.py %MODEL_FLAG%

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Oracle exited with code %ERRORLEVEL%
    pause
)
exit /b %ERRORLEVEL%

:: ─── UI Mode ────────────────────────────────────────────────────
:ui_mode
echo.
echo  ^|  Oracle - AI Coding Agent
if "%MODEL_FLAG%"=="--deep" echo  ^|  PRO mode (high-tier model)
echo  ^|  http://localhost:%PORT%/ui
echo  ^|  Press Ctrl+C to stop server and interface
echo.

:: Kill any previous server instances
taskkill /f /fi "WINDOWTITLE eq Oracle Server" >nul 2>&1
timeout /t 1 /nobreak >nul

:: Install missing dependencies (first run only)
pip install httpx fastapi uvicorn python-multipart -q 2>nul

:: Start the server directly in this window
echo [OK] Server starting on http://localhost:%PORT% ...
echo.

:: Open browser after 3 seconds (in background)
start "" /B cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:%PORT%/ui" >nul 2>&1

:: Start server (blocking - Ctrl+C to stop)
python coding_agent.py --port %PORT% %MODEL_FLAG%

:: ─── Cleanup (after Ctrl+C or close) ───────────────────────────
echo.
echo Shutting down...

:: Close browser tabs open on localhost:%PORT%
echo Closing web interface...
powershell -NoProfile -Command "try { $w = (New-Object -ComObject Shell.Application).Windows(); $w | Where-Object { $_.LocationURL -like '*localhost:%PORT%*' } | ForEach-Object { $_.Quit(); Start-Sleep -Milliseconds 100 } } catch {}" >nul 2>&1

:: Kill any remaining processes
taskkill /f /fi "WINDOWTITLE eq Oracle Server" >nul 2>&1

echo Server stopped.
echo.
echo Thanks for using Oracle!
timeout /t 2 /nobreak >nul
exit /b 0

:: ─── Stop Server (from another terminal) ───────────────────────
:stop_server
echo Stopping Oracle server...
taskkill /f /fi "WINDOWTITLE eq Oracle Server" >nul 2>&1
echo Closing web interface...
powershell -NoProfile -Command "try { $w = (New-Object -ComObject Shell.Application).Windows(); $w | Where-Object { $_.LocationURL -like '*localhost:*' } | ForEach-Object { $_.Quit(); Start-Sleep -Milliseconds 100 } } catch {}" >nul 2>&1
echo Done.
exit /b 0

:: ─── Help ───────────────────────────────────────────────────────
:help
echo Oracle - AI Coding Agent
echo.
echo Usage:  oracle.bat [options]
echo.
echo Options:
echo   --cli          Start command-line interface (CLI)
echo   --ui           Start web interface (default)
echo   --deep, --pro  Use Pro model (maximum quality)
echo   --flash        Use Flash model (economical, default)
echo   --port PORT    Use specific port (default: 8000)
echo   --stop         Stop running Oracle server
echo   --help         Show this message
echo.
echo Examples:
echo   oracle.bat                  Start web interface
echo   oracle.bat --deep           Start with Pro model
echo   oracle.bat --cli --deep     Start CLI with Pro model
echo   oracle.bat --port 8080      Start on port 8080
echo   oracle.bat --stop           Stop background server
echo.
pause
exit /b 0
