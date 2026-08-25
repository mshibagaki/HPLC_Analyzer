@echo off
setlocal
cd /d "%~dp0"
set "HPLC_NO_PAUSE="
if /i "%~1"=="--no-pause" set "HPLC_NO_PAUSE=1"
set "PYTHON_CMD="

where py >nul 2>nul
if not errorlevel 1 (
  py -3.11 scripts\read_version.py >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3.11"
)
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 (
    python scripts\read_version.py >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
  )
)
if not defined PYTHON_CMD (
  echo [ERROR] Python was not found on this packaging PC.
  goto :failed
)

call scripts\load_version.bat %PYTHON_CMD%
if errorlevel 1 goto :failed

%PYTHON_CMD% scripts\verify_windows7_offline_bundle.py --root "%CD%"
if errorlevel 1 goto :failed
%PYTHON_CMD% scripts\package_windows7_offline_bundle.py --root "%CD%"
if errorlevel 1 goto :failed

echo.
echo Windows 7 offline build kit:
echo   dist\offline\HPLC_Analyzer_%APP_VERSION%_Windows7_Offline_Build.zip
if not defined HPLC_NO_PAUSE pause
exit /b 0

:failed
echo.
echo Offline kit packaging failed. Review the messages above.
if not defined HPLC_NO_PAUSE pause
exit /b 1
