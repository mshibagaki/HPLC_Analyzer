The Windows 7 build does not download a redistributable here.

build_windows7_offline.bat verifies the bundled, fixed Visual C++ 2015-2019
x86 14.29 payload in win7_offline\installers, then copies it into this
directory immediately before Inno Setup compiles the Windows 7 installer.
