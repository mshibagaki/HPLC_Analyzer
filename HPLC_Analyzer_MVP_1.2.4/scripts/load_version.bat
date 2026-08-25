@echo off
setlocal EnableExtensions
if "%~1"=="" (
  echo [ERROR] scripts\load_version.bat requires a Python command.
  exit /b 2
)

set "HPLC_VERSION_TEMP=%TEMP%\hplc_analyzer_version_%RANDOM%_%RANDOM%.bat"
%* "%~dp0read_version.py" --format batch > "%HPLC_VERSION_TEMP%"
if errorlevel 1 goto :failed
%* "%~dp0artifact_names.py" --format batch >> "%HPLC_VERSION_TEMP%"
if errorlevel 1 goto :failed

set "APP_VERSION="
set "APP_VERSION_NUMERIC="
set "WINDOWS11_INSTALLER_NAME="
set "WINDOWS11_INSTALLER_NAME_BASE="
set "WINDOWS7_INSTALLER_NAME="
set "WINDOWS7_INSTALLER_NAME_BASE="
set "WINDOWS7_OFFLINE_ARCHIVE_NAME="
for /f "usebackq delims=" %%L in ("%HPLC_VERSION_TEMP%") do call %%L
del /q "%HPLC_VERSION_TEMP%" >nul 2>nul
if not defined APP_VERSION goto :failed_after_cleanup
if not defined APP_VERSION_NUMERIC goto :failed_after_cleanup
if not defined WINDOWS11_INSTALLER_NAME goto :failed_after_cleanup
if not defined WINDOWS11_INSTALLER_NAME_BASE goto :failed_after_cleanup
if not defined WINDOWS7_INSTALLER_NAME goto :failed_after_cleanup
if not defined WINDOWS7_INSTALLER_NAME_BASE goto :failed_after_cleanup
if not defined WINDOWS7_OFFLINE_ARCHIVE_NAME goto :failed_after_cleanup

endlocal & set "APP_VERSION=%APP_VERSION%" & set "APP_VERSION_NUMERIC=%APP_VERSION_NUMERIC%" & set "WINDOWS11_INSTALLER_NAME=%WINDOWS11_INSTALLER_NAME%" & set "WINDOWS11_INSTALLER_NAME_BASE=%WINDOWS11_INSTALLER_NAME_BASE%" & set "WINDOWS7_INSTALLER_NAME=%WINDOWS7_INSTALLER_NAME%" & set "WINDOWS7_INSTALLER_NAME_BASE=%WINDOWS7_INSTALLER_NAME_BASE%" & set "WINDOWS7_OFFLINE_ARCHIVE_NAME=%WINDOWS7_OFFLINE_ARCHIVE_NAME%" & exit /b 0

:failed
del /q "%HPLC_VERSION_TEMP%" >nul 2>nul
:failed_after_cleanup
echo [ERROR] Could not load the canonical HPLC Analyzer version.
endlocal & exit /b 1
