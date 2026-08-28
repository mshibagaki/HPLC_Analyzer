@echo off
setlocal
cd /d "%~dp0"
for %%I in ("%~dp0.") do set "PROJECT_ROOT=%%~fI"
set "HPLC_NO_PAUSE="
if /i "%~1"=="--no-pause" set "HPLC_NO_PAUSE=1"
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHON_CMD="
set "HPLC_ANALYZER_CONFIG_DIR=%PROJECT_ROOT%\.build-test-settings\presets"
set "HPLC_ANALYZER_TEST_SETTINGS_DIR=%PROJECT_ROOT%\.build-test-settings\qsettings"

where py >nul 2>nul
if not errorlevel 1 (
  py -3.11-64 scripts\verify_windows11_x64.py --interpreter >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3.11-64"
)

if not defined PYTHON_CMD (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3.11 scripts\verify_windows11_x64.py --interpreter >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3.11"
  )
)

if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 (
    python scripts\verify_windows11_x64.py --interpreter >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
  )
)

if not defined PYTHON_CMD (
  echo Python 3.11 x64 was not found.
  echo Install CPython 3.11 64-bit and enable the Python Launcher or add it to PATH.
  if not defined HPLC_NO_PAUSE pause
  exit /b 1
)

%PYTHON_CMD% scripts\verify_windows11_x64.py --interpreter
if errorlevel 1 goto :failed
call scripts\load_version.bat %PYTHON_CMD%
if errorlevel 1 goto :failed

if not exist .venv-win11-x64\Scripts\python.exe (
  %PYTHON_CMD% -m venv .venv-win11-x64
  if errorlevel 1 goto :failed
)

.venv-win11-x64\Scripts\python.exe scripts\verify_windows11_x64.py --interpreter
if errorlevel 1 (
  echo Existing .venv-win11-x64 has the wrong Python architecture or version.
  echo Rename that folder and run this script again.
  goto :failed
)

call .venv-win11-x64\Scripts\activate.bat
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :failed
python -m pip install -r requirements-win11.txt
if errorlevel 1 goto :failed
python scripts\verify_windows11_x64.py --packages
if errorlevel 1 goto :failed

set "QT_QPA_PLATFORM=offscreen"
python -m unittest discover -s tests -v
if errorlevel 1 goto :failed
set "QT_QPA_PLATFORM="
set "HPLC_ANALYZER_BUILD_DEBUG="
set "HPLC_VERSION_FILE=%PROJECT_ROOT%\build\version-info\windows11-x64.txt"
python scripts\write_windows_version_info.py --target windows11-x64 --output "%HPLC_VERSION_FILE%"
if errorlevel 1 goto :failed
python -m PyInstaller --noconfirm --clean --distpath dist\windows11-x64 --workpath build\windows11-x64 HPLC_Analyzer.spec
if errorlevel 1 goto :failed
python scripts\verify_windows11_x64.py --exe dist\windows11-x64\HPLC_Analyzer.exe
if errorlevel 1 goto :failed
call scripts\build_installer.bat windows11-x64
if errorlevel 1 goto :failed

echo.
echo Build complete ^(Windows 11 x64 / 64-bit^):
echo   App      : dist\windows11-x64\HPLC_Analyzer.exe
echo   Installer: dist\installers\%WINDOWS11_INSTALLER_NAME%
if not defined HPLC_NO_PAUSE pause
exit /b 0

:failed
echo.
echo Build failed. Review the messages above.
if not defined HPLC_NO_PAUSE pause
exit /b 1
