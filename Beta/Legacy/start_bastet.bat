@echo off
setlocal
title Bastet AI V2
cd /d "%~dp0"

echo.
echo ========================================
echo       BASTET AI V2 - Launcher
echo ========================================
echo.

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERREUR: Python n'est pas installe ou pas dans le PATH
    pause
    exit /b 1
)

REM Check if config exists
if not exist "config.json" (
    echo Configuration non trouvee. Lancement du wizard...
    python config_wizard.py
    if errorlevel 1 (
        echo Configuration annulee.
        pause
        exit /b 1
    )
)

echo Demarrage du frontend React...
pushd web
start "Bastet Frontend" cmd /c "npm run dev 2>nul"
popd

echo Demarrage du backend Python...
echo.
echo Appuyez sur Ctrl+C pour arreter, puis fermez cette fenetre.
echo.

python main.py

echo.
echo Arret en cours...

REM Tuer les processus
taskkill /F /FI "WINDOWTITLE eq Bastet Frontend" 2>nul
taskkill /F /IM node.exe 2>nul

endlocal
exit /b 0
