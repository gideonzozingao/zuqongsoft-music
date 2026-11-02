; Zuqongsoft Music - Windows installer (Inno Setup 6.3 or newer)
; Build with build_installer.bat, which builds the app first and passes the version in.
; Output: dist\GaramutMusic-Setup-<version>.exe

#define MyAppName      "Garamut Music"
#define MyAppExeName   "GaramutMusic.exe"
; MyAppFolder must equal NAME in MusicPlayer.spec.
; MyAppUserModel must equal APP_USER_MODEL_ID in app/ui.py.
#define MyAppFolder    "GaramutMusic"
#define MyAppPublisher "Zuqongtech"
#define MyAppUserModel "Zuqongtech.GaramutMusic"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
; Never change AppId: Windows uses it to recognise upgrades of this app.
AppId={{F91B3573-5578-475C-9A07-6481576C61EA}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
; Per-user install by default (no administrator rights needed); the first page lets
; people choose "Install for all users" instead.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=dist
OutputBaseFilename=GaramutMusic-Setup-{#MyAppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
; Close a running copy before files are replaced or removed.
CloseApplications=yes
RestartApplications=no
#ifdef UseSignTool
; build_installer.bat defines UseSignTool and the "zsign" command when signing is configured.
SignTool=zsign
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Upgrades: remove the previous version's libraries first, so nothing stale is left behind.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\{#MyAppFolder}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppUserModel}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppUserModel}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// When uninstalling, offer to remove the library database, playlists, stats and settings too.
// Music files are never touched. Silent uninstalls keep the data.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) then
  begin
    if MsgBox('Also delete your library, playlists, listening stats and settings?' + #13#10#13#10 +
              'Choose No to keep them for a future install. Your music files are never touched.',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    begin
      DelTree(ExpandConstant('{userappdata}\Zuqongtech\MusicPlayer'), True, True, True);
      RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER, 'Software\Zuqongtech\MusicPlayer');
    end;
  end;
end;
