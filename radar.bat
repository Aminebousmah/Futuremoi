@echo off
REM Raccourci : lance radar sans avoir a activer l'environnement virtuel.
REM   radar list --min-score 65
REM Sans argument, affiche l'aide.
REM
REM On passe par `python -m` et non par .venv\Scripts\radar.exe : ce shim est un
REM executable non signe, regenere a chaque reinstallation, que Device Guard
REM bloque sur certains postes. L'interpreteur, lui, est signe.
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo L'environnement n'est pas installe.
  echo Lancez : python -m venv .venv ^&^& .venv\Scripts\python -m pip install -e .
  exit /b 1
)
if "%~1"=="" (
  "%PY%" -m freelance_radar.cli --help
) else (
  "%PY%" -m freelance_radar.cli %*
)
