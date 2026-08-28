@echo off
setlocal
cd /d "%~dp0\.."

set "TARGET=%~1"
set "ISS_FILE="
set "SETUP_FILE="
set "ARTIFACT_BASE_NAME="
set "PYTHON_EXE=python"
if defined HPLC_PYTHON_EXE set "PYTHON_EXE=%HPLC_PYTHON_EXE%"
call scripts\load_version.bat "%PYTHON_EXE%"
if errorlevel 1 exit /b 1

if /i "%TARGET%"=="windows11-x64" (
  set "ISS_FILE=installer\windows11_x64.iss"
  set "SETUP_FILE=dist\installers\%WINDOWS11_INSTALLER_NAME%"
  set "ARTIFACT_BASE_NAME=%WINDOWS11_INSTALLER_NAME_BASE%"
)
if /i "%TARGET%"=="windows7-x86" (
  set "ISS_FILE=installer\windows7_x86.iss"
  set "SETUP_FILE=dist\installers\%WINDOWS7_INSTALLER_NAME%"
  set "ARTIFACT_BASE_NAME=%WINDOWS7_INSTALLER_NAME_BASE%"
)

if not defined ISS_FILE (
  echo [ERROR] Unknown installer target: %TARGET%
  echo Use windows11-x64 or windows7-x86.
  exit /b 2
)

"%PYTHON_EXE%" scripts\verify_installer.py --target "%TARGET%" --script "%ISS_FILE%" --version "%APP_VERSION%"
if errorlevel 1 exit /b 1

if /i "%TARGET%"=="windows7-x86" (
  if not exist installer\redist\VC_redist.x86.exe (
    echo [ERROR] Bundled VC_redist.x86.exe was not copied into installer\redist.
    echo Run build_windows7_offline.bat on Windows 7 SP1 x86.
    exit /b 1
  )
)

set "ISCC_EXE="
if defined HPLC_ISCC_EXE if exist "%HPLC_ISCC_EXE%" set "ISCC_EXE=%HPLC_ISCC_EXE%"
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE if exist "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe" set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
if not defined ISCC_EXE if exist "%ProgramFiles%\Inno Setup 7\ISCC.exe" set "ISCC_EXE=%ProgramFiles%\Inno Setup 7\ISCC.exe"
if not defined ISCC_EXE (
  for /f "delims=" %%I in ('where ISCC.exe 2^>nul') do if not defined ISCC_EXE set "ISCC_EXE=%%I"
)

if not defined ISCC_EXE (
  echo [ERROR] Inno Setup compiler ^(ISCC.exe^) was not found.
  echo Install Inno Setup 6 from the official download page, then run the build again:
  echo https://jrsoftware.org/isdl.php
  exit /b 1
)

echo [INFO] Installer compiler: %ISCC_EXE%
"%ISCC_EXE%" "--define=AppVersion=%APP_VERSION%" "--define=AppVersionNumeric=%APP_VERSION_NUMERIC%" "--define=ArtifactBaseName=%ARTIFACT_BASE_NAME%" "%ISS_FILE%"
if errorlevel 1 exit /b 1

"%PYTHON_EXE%" scripts\verify_installer.py --target "%TARGET%" --script "%ISS_FILE%" --setup "%SETUP_FILE%" --version "%APP_VERSION%"
if errorlevel 1 exit /b 1

echo [OK] Offline installer: %SETUP_FILE%
exit /b 0
