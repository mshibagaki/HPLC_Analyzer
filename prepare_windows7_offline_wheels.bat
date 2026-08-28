@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "WHEEL_STAGE=win7_offline\wheels-stage"
if exist "%WHEEL_STAGE%" rmdir /s /q "%WHEEL_STAGE%"
mkdir "%WHEEL_STAGE%"
if errorlevel 1 goto :failed

echo [INFO] Refreshing the complete CPython 3.8 win32 wheelhouse on Windows 11...
py -3.11-64 -m pip download --platform win32 --python-version 3.8 --implementation cp --abi cp38 --only-binary=:all: --dest "%WHEEL_STAGE%" -r requirements-win7-bootstrap.txt -r requirements-win7.txt
if errorlevel 1 goto :failed

if exist win7_offline\wheels rmdir /s /q win7_offline\wheels
move "%WHEEL_STAGE%" win7_offline\wheels >nul
if errorlevel 1 goto :failed

py -3.11-64 scripts\verify_windows7_wheelhouse.py --root "%CD%"
if errorlevel 1 goto :failed
py -3.11-64 scripts\make_windows7_offline_manifest.py --root "%CD%"
if errorlevel 1 goto :failed
py -3.11-64 scripts\verify_windows7_offline_bundle.py --root "%CD%"
if errorlevel 1 goto :failed

echo [OK] The Windows 7 wheelhouse and SHA-256 manifest are complete.
exit /b 0

:failed
echo [ERROR] Windows 7 wheelhouse preparation failed.
exit /b 1
