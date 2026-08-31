@echo off
REM Double-cliquez ce fichier : il demarre l'interface et ouvre le navigateur.
REM Pour arreter, fermez cette fenetre ou faites Ctrl+C.
title freelance-radar
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo L'environnement n'est pas installe.
  echo Lancez : python -m venv .venv ^&^& .venv\Scripts\python -m pip install -e ".[web]"
  pause
  exit /b 1
)
REM Le navigateur s'ouvre en differe : le serveur a besoin d'une seconde pour
REM ecouter, sinon l'onglet tombe sur une page d'erreur.
start "" /b cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8000"
"%PY%" -m freelance_radar.cli web
echo.
echo Interface arretee.
pause
