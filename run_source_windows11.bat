@echo off
setlocal
cd /d "%~dp0"
if not exist .venv-win11-x64\Scripts\python.exe (
  echo Run build_windows11.bat once to create the environment.
  pause
  exit /b 1
)
.venv-win11-x64\Scripts\python.exe app.py
