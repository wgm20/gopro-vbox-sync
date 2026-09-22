#ifndef AppVersion
  #define AppVersion "1.5.0b5"
#endif
[Setup]
AppId={{91BD245C-AF16-4BCB-95E5-0D9F2F294092}
AppName=GoPro VBOX Sync
AppVersion={#AppVersion}
AppVerName=GoPro VBOX Sync {#AppVersion} (beta)
AppPublisher=GoPro VBOX Sync contributors
AppPublisherURL=https://github.com/wgm20/gopro-vbox-sync
AppSupportURL=https://github.com/wgm20/gopro-vbox-sync/issues
AppUpdatesURL=https://github.com/wgm20/gopro-vbox-sync/releases
DefaultDirName={localappdata}\Programs\GoPro VBOX Sync
DefaultGroupName=GoPro VBOX Sync
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=..\LICENSE
InfoBeforeFile=beta-notice.txt
UninstallDisplayIcon={app}\GoProVBOXSync.exe
OutputDir=..\release-output
OutputBaseFilename=GoPro-VBOX-Sync-{#AppVersion}-Setup
SetupIconFile=..\goprovbox\assets\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
CloseApplications=no
DisableProgramGroupPage=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\GoProVBOXSync\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\GoPro VBOX Sync"; Filename: "{app}\GoProVBOXSync.exe"; IconFilename: "{app}\_internal\goprovbox\assets\icon.ico"
Name: "{group}\Quick-start guide"; Filename: "{app}\_internal\goprovbox\assets\quick-start.html"
Name: "{autodesktop}\GoPro VBOX Sync"; Filename: "{app}\GoProVBOXSync.exe"; IconFilename: "{app}\_internal\goprovbox\assets\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\GoProVBOXSync.exe"; Description: "Open GoPro VBOX Sync"; Flags: nowait postinstall skipifsilent

; No UninstallDelete entries: retain settings, downloaded tools, demos and exports.
