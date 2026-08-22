@echo off
rem Compatibility entry point. The Windows 7 build is intentionally local and offline.
call "%~dp0build_windows7_offline.bat" %*
exit /b %errorlevel%
