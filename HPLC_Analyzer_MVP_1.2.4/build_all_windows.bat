@echo off
setlocal
cd /d "%~dp0"

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
  echo [ERROR] A Python interpreter capable of reading the application version was not found.
  goto :failed
)
call scripts\load_version.bat %PYTHON_CMD%
if errorlevel 1 goto :failed

echo ============================================================
echo Building HPLC Analyzer %APP_VERSION% for Windows 11 x64
echo ============================================================
call build_windows11.bat --no-pause
if errorlevel 1 goto :failed

echo.
echo ============================================================
echo Packaging the ready-to-copy Windows 7 offline build kit
echo ============================================================
call package_windows7_offline_bundle.bat --no-pause
if errorlevel 1 goto :failed

echo.
echo Windows 11 installer and Windows 7 offline build kit are ready:
echo   dist\installers\HPLC_Analyzer_Setup_%APP_VERSION%_Windows11_x64.exe
echo   dist\offline\HPLC_Analyzer_%APP_VERSION%_Windows7_Offline_Build.zip
echo Build the Windows 7 EXEs and installer on the Windows 7 SP1 x86 machine itself.
pause
exit /b 0

:failed
echo.
echo The combined preparation stopped because one step failed.
pause
exit /b 1
