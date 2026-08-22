@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo Building HPLC Analyzer 1.2.4 for Windows 11 x64
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
echo   dist\installers\HPLC_Analyzer_Setup_1.2.4_Windows11_x64.exe
echo   dist\offline\HPLC_Analyzer_1.2.4_Windows7_Offline_Build.zip
echo Build the Windows 7 EXEs and installer on the Windows 7 SP1 x86 machine itself.
pause
exit /b 0

:failed
echo.
echo The combined preparation stopped because one step failed.
pause
exit /b 1
