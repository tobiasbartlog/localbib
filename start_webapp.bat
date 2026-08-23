@echo off
chcp 65001 >nul 2>&1
echo.
echo   ========================================
echo   Literatur-Manager Web UI
echo   ========================================
echo.

cd /d "%~dp0"

:: Pruefen ob Port 8000 bereits belegt ist
netstat -ano | findstr ":8000 " | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo   Port 8000 ist bereits in Benutzung!
    echo.
    set /p KILL_OLD="   Alten Server beenden und neu starten? [J/n]: "
    if /i "%KILL_OLD%"=="n" (
        echo.
        echo   Abgebrochen. Server laeuft weiterhin auf Port 8000.
        echo.
        pause
        exit /b
    )
    echo.
    echo   Beende alten Prozess auf Port 8000...
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
        taskkill /F /PID %%a >nul 2>&1
    )
    timeout /t 1 /nobreak >nul
    echo   Alter Server beendet.
    echo.
)

echo   Starte Server auf http://localhost:8000
echo   Druecke Strg+C zum Beenden
echo.

:: Browser oeffnen nach kurzer Verzoegerung
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8000"

:: Server starten (py launcher oder python)
where py >nul 2>&1 && (
    py webapp.py
) || (
    python webapp.py
)

pause
