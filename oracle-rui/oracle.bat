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
    echo [ERRORE] Python non trovato. Assicurati di aver installato Python e che sia nel PATH.
    echo         Scarica Python da: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: ─── Check .env ─────────────────────────────────────────────────
if not exist ".env" (
    echo [AVVISO] File .env non trovato.
    echo          Copia .env.example in .env e configura la tua API key.
    echo.
    choice /c CN /N /M "[C]ontinua comunque o [N]on uscire? (C/N) "
    if errorlevel 2 exit /b 1
)

:: ─── UI Mode (default) ─────────────────────────────────────────
if "%MODE%"=="ui" goto ui_mode

:: ─── CLI Mode ───────────────────────────────────────────────────
:cli_mode
echo.
echo  ^|  Oracle CLI - AI Coding Agent
if "%MODEL_FLAG%"=="--deep" echo  ^|  Modalita' PRO (modello di fascia alta)
echo  ^|  Scrivi 'exit' o premi Ctrl+C per uscire
echo.
python cli.py %MODEL_FLAG%

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERRORE] Oracle si e' chiuso con codice %ERRORLEVEL%
    pause
)
exit /b %ERRORLEVEL%

:: ─── UI Mode ────────────────────────────────────────────────────
:ui_mode
echo.
echo  ^|  Oracle - AI Coding Agent
if "%MODEL_FLAG%"=="--deep" echo  ^|  Modalita' PRO (modello di fascia alta)
echo  ^|  http://localhost:%PORT%/ui
echo  ^|  Premi Ctrl+C per fermare server e interfaccia
echo.

:: Ferma eventuali server precedenti
taskkill /f /fi "WINDOWTITLE eq Oracle Server" >nul 2>&1
timeout /t 1 /nobreak >nul

:: Installa dipendenze mancanti (solo primo avvio)
pip install httpx fastapi uvicorn python-multipart -q 2>nul

:: Avvia il server direttamente in questa finestra
echo [OK] Server in avvio su http://localhost:%PORT% ...
echo.

:: Apre il browser dopo 3 secondi (in background)
start "" /B cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:%PORT%/ui" >nul 2>&1

:: Avvia il server (bloccante - Ctrl+C per fermare)
python coding_agent.py --port %PORT% %MODEL_FLAG%

:: ─── Cleanup (dopo Ctrl+C o chiusura) ───────────────────────────
echo.
echo Arresto in corso...

:: Chiude le schede del browser aperte su localhost:%PORT%
echo Chiusura interfaccia web...
powershell -NoProfile -Command "try { $w = (New-Object -ComObject Shell.Application).Windows(); $w | Where-Object { $_.LocationURL -like '*localhost:%PORT%*' } | ForEach-Object { $_.Quit(); Start-Sleep -Milliseconds 100 } } catch {}" >nul 2>&1

:: Ferma eventuali processi rimasti
taskkill /f /fi "WINDOWTITLE eq Oracle Server" >nul 2>&1

echo Server arrestato.
echo.
echo Grazie per aver usato Oracle!
timeout /t 2 /nobreak >nul
exit /b 0

:: ─── Stop Server (da un altro terminale) ───────────────────────
:stop_server
echo Fermo il server Oracle in esecuzione...
taskkill /f /fi "WINDOWTITLE eq Oracle Server" >nul 2>&1
echo Chiusura interfaccia web...
powershell -NoProfile -Command "try { $w = (New-Object -ComObject Shell.Application).Windows(); $w | Where-Object { $_.LocationURL -like '*localhost:*' } | ForEach-Object { $_.Quit(); Start-Sleep -Milliseconds 100 } } catch {}" >nul 2>&1
echo Fatto.
exit /b 0

:: ─── Help ───────────────────────────────────────────────────────
:help
echo Oracle - AI Coding Agent
echo.
echo Utilizzo:  oracle.bat [opzioni]
echo.
echo Opzioni:
echo   --cli          Avvia l'interfaccia a riga di comando (CLI)
echo   --ui           Avvia l'interfaccia web (default)
echo   --deep, --pro  Usa il modello Pro (massima qualita')
echo   --flash        Usa il modello Flash (economico, default)
echo   --port PORT    Usa una porta specifica (default: 8000)
echo   --stop         Ferma il server Oracle in esecuzione
echo   --help         Mostra questo messaggio
echo.
echo Esempi:
echo   oracle.bat                  Avvia interfaccia web
echo   oracle.bat --deep           Avvia con il modello Pro
echo   oracle.bat --cli --deep     Avvia CLI con modello Pro
echo   oracle.bat --port 8080      Avvia su porta 8080
echo   oracle.bat --stop           Ferma il server in background
echo.
pause
exit /b 0
