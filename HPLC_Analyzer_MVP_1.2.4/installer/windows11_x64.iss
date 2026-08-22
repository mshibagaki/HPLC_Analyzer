[Setup]
AppId={{D21F975D-644A-48D3-8A67-40DC7BD85AAF}
AppName=HPLC Analyzer
AppVersion=1.2.4
AppVerName=HPLC Analyzer 1.2.4
AppPublisher=Research Tools
AppCopyright=Copyright (C) 2026
DefaultDirName={autopf}\HPLC Analyzer
DefaultGroupName=HPLC Analyzer
DisableProgramGroupPage=yes
LicenseFile=LICENSE.txt
OutputDir=dist\installers
OutputBaseFilename=HPLC_Analyzer_Setup_1.2.4_Windows11_x64
SetupIconFile=assets\app_icon.ico
UninstallDisplayIcon={app}\HPLC_Analyzer.exe
UninstallDisplayName=HPLC Analyzer 1.2.4 (Windows 11 64-bit)
VersionInfoVersion=1.2.4.0
VersionInfoCompany=Research Tools
VersionInfoDescription=HPLC Analyzer 1.2.4 installer for Windows 11 64-bit
VersionInfoProductName=HPLC Analyzer
VersionInfoProductVersion=1.2.4
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
CloseApplications=yes
RestartApplications=no
MinVersion=10.0.22000
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SourceDir={#SourcePath}\..

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\windows11-x64\HPLC_Analyzer.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; DestName: "README.txt"; Flags: ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "third_party_licenses\*"; DestDir: "{app}\third_party_licenses"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "sample_data\*"; DestDir: "{app}\sample_data"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\HPLC Analyzer"; Filename: "{app}\HPLC_Analyzer.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\HPLC Analyzer README"; Filename: "{app}\README.txt"
Name: "{autodesktop}\HPLC Analyzer"; Filename: "{app}\HPLC_Analyzer.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\HPLC_Analyzer.exe"; Description: "{cm:LaunchProgram,HPLC Analyzer}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
