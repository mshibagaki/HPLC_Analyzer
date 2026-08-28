@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "HPLC_NO_PAUSE="
if /i "%~1"=="--no-pause" set "HPLC_NO_PAUSE=1"
set "PYTHONHOME="
set "PYTHONPATH="
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PIP_NO_INDEX=1"
set "PIP_NO_CACHE_DIR=1"

rem Derive every build path from the batch file itself, then canonicalize it.
rem %%~fI guarantees that the Python bootstrapper receives a fully-qualified path.
for %%I in ("%~dp0.") do set "PROJECT_ROOT=%%~fI"
set "OFFLINE_ROOT=%PROJECT_ROOT%\win7_offline"
set "WHEEL_DIR=%OFFLINE_ROOT%\wheels"
set "PYTHON_INSTALLER=%OFFLINE_ROOT%\installers\python-3.8.10.exe"
set "INNO_INSTALLER=%OFFLINE_ROOT%\installers\innosetup-6.7.3.exe"
set "VC_REDIST=%OFFLINE_ROOT%\installers\VC_redist.x86.exe"
set "REQUIRED_WHEELS_FILE=%OFFLINE_ROOT%\REQUIRED_WHEELS.txt"
set "OFFLINE_MANIFEST=%OFFLINE_ROOT%\MANIFEST.sha256"
set "HPLC_ANALYZER_CONFIG_DIR=%PROJECT_ROOT%\.build-test-settings\presets"
set "HPLC_ANALYZER_TEST_SETTINGS_DIR=%PROJECT_ROOT%\.build-test-settings\qsettings"
for %%I in ("%PROJECT_ROOT%\.build-tools") do set "TOOLS_ROOT=%%~fI"
for %%I in ("%TOOLS_ROOT%\Python38-32") do set "PYTHON_DIR=%%~fI"
set "LOCAL_PYTHON_EXE=%PYTHON_DIR%\python.exe"
set "PYTHON_EXE=%LOCAL_PYTHON_EXE%"
set "PYTHON_LOG=%TOOLS_ROOT%\python-3.8.10-install.log"
set "INNO_DIR=%TOOLS_ROOT%\Inno Setup 6"
set "ISCC_EXE="

ver | findstr /c:"6.1.7601" >nul
if errorlevel 1 (
  echo [ERROR] This build must run on Windows 7 SP1 ^(build 7601^) itself.
  echo Do not build the Windows 7 executable on Windows 10 or 11.
  goto :failed
)
if /i not "%PROCESSOR_ARCHITECTURE%"=="x86" (
  echo [ERROR] A 32-bit Windows 7 installation is required.
  goto :failed
)
if defined PROCESSOR_ARCHITEW6432 (
  echo [ERROR] This is a 64-bit Windows installation. Build on Windows 7 SP1 32-bit.
  goto :failed
)

echo [INFO] Checking the complete pinned wheel list before installing Python...
call :verify_required_wheels
if errorlevel 1 goto :failed
echo [INFO] Verifying every offline installer and wheel against MANIFEST.sha256...
call :verify_manifest_native
if errorlevel 1 goto :failed

echo [INFO] Verifying the three offline installers before running them...
call :verify_hash "%PYTHON_INSTALLER%" ad07633a1f0cd795f3bf9da33729f662281df196b4567fa795829f3bb38a30ac
if errorlevel 1 goto :failed
call :verify_hash "%INNO_INSTALLER%" 9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732
if errorlevel 1 goto :failed
call :verify_hash "%VC_REDIST%" 1acd8d5ea1cdc3eb2eb4c87be3ab28722d0825c15449e5c9ceef95d897de52fa
if errorlevel 1 goto :failed

if not exist "%TOOLS_ROOT%" mkdir "%TOOLS_ROOT%"
if errorlevel 1 goto :failed

echo [INFO] Installing or confirming the Windows 7-compatible VC++ x86 runtime...
"%VC_REDIST%" /install /quiet /norestart
set "VC_RESULT=%ERRORLEVEL%"
if "%VC_RESULT%"=="3010" (
  echo [ERROR] The VC++ runtime requested a restart.
  echo Restart Windows, then run build_windows7_offline.bat again.
  goto :failed
)
if not "%VC_RESULT%"=="0" if not "%VC_RESULT%"=="1638" (
  echo [ERROR] VC++ runtime installation returned %VC_RESULT%.
  goto :failed
)

if exist "%LOCAL_PYTHON_EXE%" goto :python_ready
call :install_local_python
if errorlevel 1 goto :failed
:python_ready
if not exist "%PYTHON_EXE%" (
  echo [ERROR] A usable Python interpreter was not found.
  echo [ERROR] Checked local path: "%LOCAL_PYTHON_EXE%"
  echo [INFO] Python installer log: "%PYTHON_LOG%"
  goto :failed
)

"%PYTHON_EXE%" scripts\verify_windows7_x86.py --host
if errorlevel 1 goto :failed
"%PYTHON_EXE%" scripts\verify_windows7_x86.py --interpreter
if errorlevel 1 goto :failed
call scripts\load_version.bat "%PYTHON_EXE%"
if errorlevel 1 goto :failed
"%PYTHON_EXE%" scripts\verify_windows7_offline_bundle.py --root "%PROJECT_ROOT%"
if errorlevel 1 goto :failed
"%PYTHON_EXE%" scripts\verify_windows7_wheelhouse.py --root "%PROJECT_ROOT%"
if errorlevel 1 goto :failed

if not exist .venv-win7-x86\Scripts\python.exe (
  "%PYTHON_EXE%" -m venv .venv-win7-x86
  if errorlevel 1 goto :failed
)
.venv-win7-x86\Scripts\python.exe scripts\verify_windows7_x86.py --interpreter
if errorlevel 1 (
  echo [ERROR] Existing .venv-win7-x86 has the wrong Python architecture or version.
  echo Rename that folder and run this script again.
  goto :failed
)

echo [INFO] Installing every Python dependency from the bundled wheel directory only...
.venv-win7-x86\Scripts\python.exe -m pip install --no-index --find-links="%WHEEL_DIR%" --only-binary=:all: -r requirements-win7-bootstrap.txt
if errorlevel 1 goto :failed
.venv-win7-x86\Scripts\python.exe -m pip install --no-index --find-links="%WHEEL_DIR%" --only-binary=:all: -r requirements-win7.txt
if errorlevel 1 goto :failed
echo [INFO] Importing every native dependency in a separate process before PyInstaller...
.venv-win7-x86\Scripts\python.exe scripts\windows7_import_preflight.py
if errorlevel 1 goto :failed

set "QT_QPA_PLATFORM=offscreen"
.venv-win7-x86\Scripts\python.exe -m unittest discover -s tests -v
if errorlevel 1 goto :failed
set "QT_QPA_PLATFORM="

set "HPLC_VERSION_FILE=%PROJECT_ROOT%\build\version-info\windows7-x86.txt"
set "HPLC_DEBUG_VERSION_FILE=%PROJECT_ROOT%\build\version-info\windows7-x86-debug.txt"
.venv-win7-x86\Scripts\python.exe scripts\write_windows_version_info.py --target windows7-x86 --output "%HPLC_VERSION_FILE%"
if errorlevel 1 goto :failed
.venv-win7-x86\Scripts\python.exe scripts\write_windows_version_info.py --target windows7-x86 --debug --output "%HPLC_DEBUG_VERSION_FILE%"
if errorlevel 1 goto :failed
set "HPLC_ANALYZER_BUILD_DEBUG=1"
.venv-win7-x86\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath dist\windows7-x86 --workpath build\windows7-x86 HPLC_Analyzer.spec
if errorlevel 1 goto :failed
.venv-win7-x86\Scripts\python.exe scripts\verify_windows7_x86.py --exe dist\windows7-x86\HPLC_Analyzer.exe
if errorlevel 1 goto :failed
.venv-win7-x86\Scripts\python.exe scripts\verify_windows7_x86.py --exe dist\windows7-x86\HPLC_Analyzer_Debug.exe
if errorlevel 1 goto :failed

echo [INFO] Running both frozen executables on this Windows 7 machine...
set "QT_QPA_PLATFORM=offscreen"
dist\windows7-x86\HPLC_Analyzer.exe --startup-smoke-test
if errorlevel 1 goto :failed
dist\windows7-x86\HPLC_Analyzer_Debug.exe --startup-smoke-test
if errorlevel 1 goto :failed
set "QT_QPA_PLATFORM="

echo [INFO] Publishing the two verified executables at the dist root...
copy /y dist\windows7-x86\HPLC_Analyzer.exe dist\HPLC_Analyzer.exe >nul
if errorlevel 1 goto :failed
copy /y dist\windows7-x86\HPLC_Analyzer_Debug.exe dist\HPLC_Analyzer_Debug.exe >nul
if errorlevel 1 goto :failed

if not exist installer\redist mkdir installer\redist
copy /y "%VC_REDIST%" installer\redist\VC_redist.x86.exe >nul
if errorlevel 1 goto :failed

if exist "%INNO_DIR%\ISCC.exe" set "ISCC_EXE=%INNO_DIR%\ISCC.exe"
if not defined ISCC_EXE if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC_EXE (
  echo [INFO] Installing the bundled Inno Setup compiler into .build-tools...
  "%INNO_INSTALLER%" /CURRENTUSER /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR="%INNO_DIR%"
  if errorlevel 1 goto :failed
  if exist "%INNO_DIR%\ISCC.exe" set "ISCC_EXE=%INNO_DIR%\ISCC.exe"
)
if not defined ISCC_EXE (
  echo [ERROR] ISCC.exe could not be installed or found.
  goto :failed
)

set "HPLC_ISCC_EXE=%ISCC_EXE%"
set "HPLC_PYTHON_EXE=%PROJECT_ROOT%\.venv-win7-x86\Scripts\python.exe"
call scripts\build_installer.bat windows7-x86
if errorlevel 1 goto :failed

echo.
echo Build complete on Windows 7 SP1 x86:
echo   Normal   : dist\HPLC_Analyzer.exe
echo   Debug    : dist\HPLC_Analyzer_Debug.exe
echo   Installer: dist\installers\%WINDOWS7_INSTALLER_NAME%
if not defined HPLC_NO_PAUSE pause
exit /b 0

:install_local_python
if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%"
if not exist "%PYTHON_DIR%" (
  echo [ERROR] Could not create the Python installation directory.
  echo [ERROR] Attempted path: "%PYTHON_DIR%"
  exit /b 1
)

echo [INFO] Installing the bundled CPython 3.8.10 x86...
echo [INFO] Python target directory ^(absolute^): "%PYTHON_DIR%"
if exist "%PYTHON_LOG%" del /q "%PYTHON_LOG%" >nul 2>nul
"%PYTHON_INSTALLER%" /quiet /log "%PYTHON_LOG%" InstallAllUsers=0 TargetDir="%PYTHON_DIR%" DefaultJustForMeTargetDir="%PYTHON_DIR%" Include_doc=0 Include_debug=0 Include_dev=0 Include_exe=1 Include_launcher=0 InstallLauncherAllUsers=0 Include_lib=1 Include_pip=1 Include_symbols=0 Include_tcltk=0 Include_test=0 AssociateFiles=0 Shortcuts=0 PrependPath=0
set "PYTHON_RESULT=%ERRORLEVEL%"
echo [INFO] Python installer exit code: %PYTHON_RESULT%
if not "%PYTHON_RESULT%"=="0" (
  echo [ERROR] Python installer returned %PYTHON_RESULT%.
  echo [ERROR] Checked path: "%LOCAL_PYTHON_EXE%"
  echo [INFO] Installer log: "%PYTHON_LOG%"
  exit /b 1
)

if exist "%LOCAL_PYTHON_EXE%" (
  set "PYTHON_EXE=%LOCAL_PYTHON_EXE%"
  echo [OK] Local Python executable: "%LOCAL_PYTHON_EXE%"
  exit /b 0
)

echo [WARN] Python installer completed but did not create the requested local executable.
echo [WARN] Checked path: "%LOCAL_PYTHON_EXE%"
echo [INFO] Installer log: "%PYTHON_LOG%"
echo [INFO] Looking for an already-installed Python 3.8.10 x86...
call :find_existing_python
if not errorlevel 1 exit /b 0

echo [ERROR] The local Python installation was not created, and no compatible existing interpreter was found.
echo [ERROR] Checked path: "%LOCAL_PYTHON_EXE%"
echo [INFO] Target directory contents:
dir /a "%PYTHON_DIR%"
echo [INFO] Review the installer log: "%PYTHON_LOG%"
exit /b 1

:find_existing_python
call :python_from_launcher
if not errorlevel 1 exit /b 0
call :python_from_registry "HKCU\Software\Python\PythonCore\3.8\InstallPath"
if not errorlevel 1 exit /b 0
call :python_from_registry "HKLM\Software\Python\PythonCore\3.8\InstallPath"
if not errorlevel 1 exit /b 0
call :accept_python "%LocalAppData%\Programs\Python\Python38-32\python.exe"
if not errorlevel 1 exit /b 0
call :accept_python "%LocalAppData%\Programs\Python\Python38\python.exe"
if not errorlevel 1 exit /b 0
call :accept_python "%ProgramFiles%\Python38-32\python.exe"
if not errorlevel 1 exit /b 0
call :accept_python "%ProgramFiles%\Python38\python.exe"
if not errorlevel 1 exit /b 0
call :python_from_path
if not errorlevel 1 exit /b 0
exit /b 1

:python_from_launcher
set "PYTHON_CANDIDATE="
for /f "usebackq delims=" %%P in (`py -3.8-32 -c "import sys; print(sys.executable)" 2^>nul`) do if not defined PYTHON_CANDIDATE set "PYTHON_CANDIDATE=%%P"
if not defined PYTHON_CANDIDATE exit /b 1
call :accept_python "%PYTHON_CANDIDATE%"
exit /b %ERRORLEVEL%

:python_from_registry
set "PYTHON_CANDIDATE="
for /f "tokens=2,*" %%A in ('reg query "%~1" /ve 2^>nul ^| find "REG_SZ"') do set "PYTHON_CANDIDATE=%%B"
if not defined PYTHON_CANDIDATE exit /b 1
call :accept_python "%PYTHON_CANDIDATE%\python.exe"
exit /b %ERRORLEVEL%

:python_from_path
set "PYTHON_CANDIDATE="
for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined PYTHON_CANDIDATE set "PYTHON_CANDIDATE=%%P"
if not defined PYTHON_CANDIDATE exit /b 1
call :accept_python "%PYTHON_CANDIDATE%"
exit /b %ERRORLEVEL%

:accept_python
if not exist "%~1" exit /b 1
"%~1" scripts\verify_windows7_x86.py --interpreter >nul 2>nul
if errorlevel 1 exit /b 1
for %%I in ("%~1") do set "PYTHON_EXE=%%~fI"
echo [OK] Using compatible existing Python: "%PYTHON_EXE%"
exit /b 0

:verify_hash
if not exist "%~1" (
  echo [ERROR] Missing offline asset: %~1
  exit /b 1
)
set "HPLC_ACTUAL_HASH="
for /f "skip=1 tokens=* delims=" %%H in ('certutil -hashfile "%~1" SHA256 2^>nul') do if not defined HPLC_ACTUAL_HASH set "HPLC_ACTUAL_HASH=%%H"
set "HPLC_ACTUAL_HASH=%HPLC_ACTUAL_HASH: =%"
if /i not "%HPLC_ACTUAL_HASH%"=="%~2" (
  echo [ERROR] SHA-256 mismatch: %~1
  echo Expected: %~2
  echo Actual  : %HPLC_ACTUAL_HASH%
  exit /b 1
)
echo [OK] SHA-256: %~1
exit /b 0

:verify_required_wheels
if not exist "%REQUIRED_WHEELS_FILE%" (
  echo [ERROR] Missing required-wheel lock file: "%REQUIRED_WHEELS_FILE%"
  exit /b 1
)
set "HPLC_MISSING_WHEEL="
for /f "usebackq eol=# delims=" %%W in ("%REQUIRED_WHEELS_FILE%") do call :verify_required_wheel "%%W"
if defined HPLC_MISSING_WHEEL exit /b 1
echo [OK] Every wheel named by REQUIRED_WHEELS.txt exists.
exit /b 0

:verify_required_wheel
if not exist "%WHEEL_DIR%\%~1" (
  echo [ERROR] Missing required offline wheel: "%WHEEL_DIR%\%~1"
  set "HPLC_MISSING_WHEEL=1"
)
exit /b 0

:verify_manifest_native
if not exist "%OFFLINE_MANIFEST%" (
  echo [ERROR] Missing offline manifest: "%OFFLINE_MANIFEST%"
  exit /b 1
)
set "HPLC_MANIFEST_ERROR="
for /f "usebackq eol=# tokens=1,*" %%H in ("%OFFLINE_MANIFEST%") do call :verify_manifest_entry "%%H" "%%I"
if defined HPLC_MANIFEST_ERROR exit /b 1
echo [OK] Every offline asset matches MANIFEST.sha256.
exit /b 0

:verify_manifest_entry
set "HPLC_MANIFEST_RELATIVE=%~2"
if "%HPLC_MANIFEST_RELATIVE:~0,1%"=="*" set "HPLC_MANIFEST_RELATIVE=%HPLC_MANIFEST_RELATIVE:~1%"
call :verify_hash "%PROJECT_ROOT%\%HPLC_MANIFEST_RELATIVE%" %~1
if errorlevel 1 set "HPLC_MANIFEST_ERROR=1"
exit /b 0

:failed
set "QT_QPA_PLATFORM="
echo.
echo Build failed. Review the messages above.
echo No package download is attempted by this Windows 7 build script.
if not defined HPLC_NO_PAUSE pause
exit /b 1
