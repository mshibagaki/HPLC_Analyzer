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
OutputBaseFilename=HPLC_Analyzer_Setup_1.2.4_Windows7_x86
SetupIconFile=assets\app_icon.ico
UninstallDisplayIcon={app}\HPLC_Analyzer.exe
UninstallDisplayName=HPLC Analyzer 1.2.4 (Windows 7 32-bit)
VersionInfoVersion=1.2.4.0
VersionInfoCompany=Research Tools
VersionInfoDescription=HPLC Analyzer 1.2.4 installer for Windows 7 32-bit
VersionInfoProductName=HPLC Analyzer
VersionInfoProductVersion=1.2.4
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
CloseApplications=yes
RestartApplications=no
MinVersion=6.1sp1
ArchitecturesAllowed=x86compatible and not x64compatible
ArchitecturesInstallIn64BitMode=
SourceDir={#SourcePath}\..

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
japanese.InstallingVCRuntime=Microsoft Visual C++ 2015-2019 (x86) ランタイムを確認しています...
english.InstallingVCRuntime=Checking the Microsoft Visual C++ 2015-2019 (x86) runtime...

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "installer\redist\VC_redist.x86.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "dist\windows7-x86\HPLC_Analyzer.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\windows7-x86\HPLC_Analyzer_Debug.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; DestName: "README.txt"; Flags: ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "third_party_licenses\*"; DestDir: "{app}\third_party_licenses"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "sample_data\*"; DestDir: "{app}\sample_data"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\HPLC Analyzer"; Filename: "{app}\HPLC_Analyzer.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\HPLC Analyzer (Debug)"; Filename: "{app}\HPLC_Analyzer_Debug.exe"; WorkingDir: "{app}"; Comment: "Diagnostic console build for startup troubleshooting"
Name: "{autoprograms}\HPLC Analyzer README"; Filename: "{app}\README.txt"
Name: "{autodesktop}\HPLC Analyzer"; Filename: "{app}\HPLC_Analyzer.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\VC_redist.x86.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "{cm:InstallingVCRuntime}"; Flags: waituntilterminated; Check: VCRedistNeedsInstall
Filename: "{app}\HPLC_Analyzer.exe"; Description: "{cm:LaunchProgram,HPLC Analyzer}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
function VCRedistNeedsInstall: Boolean;
var
  Installed: Cardinal;
  Major: Cardinal;
  Minor: Cardinal;
begin
  if not RegQueryDWordValue(HKLM32,
    'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x86',
    'Installed', Installed) then
  begin
    Result := True;
    Exit;
  end;
  if not RegQueryDWordValue(HKLM32,
    'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x86',
    'Major', Major) then
  begin
    Result := True;
    Exit;
  end;
  if not RegQueryDWordValue(HKLM32,
    'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x86',
    'Minor', Minor) then
  begin
    Result := True;
    Exit;
  end;
  Result := (Installed <> 1) or (Major < 14) or
    ((Major = 14) and (Minor < 29));
end;
