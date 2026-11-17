; Dualyrics installer, for Inno Setup 6.3 or later (built and tested with 7.1).
;
; tools\build.ps1 builds dist\Dualyrics.exe, fetches the Farsi messages file and then runs:
;   ISCC.exe /DAppVersion=2.0.0 /DFarsiIsl=<path to Farsi.isl> installer\Dualyrics.iss
; Output: dist\Dualyrics-Setup-<version>.exe
;
; - Per-user install, no admin rights: %LOCALAPPDATA%\Programs\Dualyrics.
; - User data stays in %APPDATA%\Dualyrics. The uninstaller asks before deleting it;
;   a silent uninstall keeps it unless /PURGE=1 is given.
; - Upgrades in place (same AppId). Settings from v1 (the portable exe) are picked up and migrated by the app.
; - Silent install: Dualyrics-Setup-<version>.exe /VERYSILENT /SUPPRESSMSGBOXES [/TASKS="desktopicon,startup"]

#ifndef AppVersion
  #error Pass the version, for example: ISCC /DAppVersion=2.0.0 installer\Dualyrics.iss
#endif
#ifndef FarsiIsl
  #define FarsiIsl "..\build\inno\Farsi.isl"
#endif
#define AppName "Dualyrics"
#define AppExe "Dualyrics.exe"
#define RepoUrl "https://github.com/morpheusadam/Dualyrics"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"

[Setup]
AppId={{42E05E09-3AE9-43C0-9506-DE94A296A19D}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Dualyrics contributors
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
AppUpdatesURL={#RepoUrl}/releases
AppCopyright=MIT License
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
SetupIconFile=..\dualyrics\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
LicenseFile=..\LICENSE
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=force
RestartApplications=no
ShowLanguageDialog=yes
LanguageDetectionMethod=uilanguage
SetupMutex=DualyricsSetupMutex

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "persian"; MessagesFile: "{#FarsiIsl}"

[CustomMessages]
english.StartWithWindows=Start Dualyrics when I sign in to Windows
english.StartupGroup=Startup:
english.AppComment=Synced lyrics and live captions with translation
english.DeleteUserData=Do you also want to delete your Dualyrics settings, cache, logs and downloaded speech models?%n%n%1%n%nChoose No to keep them (recommended if you will install Dualyrics again).
persian.StartWithWindows=اجرای خودکار Dualyrics هنگام ورود به ویندوز
persian.StartupGroup=اجرای خودکار:
persian.AppComment=متن همگام آهنگ‌ها و زیرنویس زنده، همراه با ترجمه
persian.DeleteUserData=تنظیمات، حافظهٔ موقت، گزارش‌ها و مدل‌های گفتار دانلودشدهٔ Dualyrics هم پاک شوند؟%n%n%1%n%nاگر بعداً دوباره نصبش می‌کنید، «خیر» را بزنید تا نگه داشته شوند.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "{cm:StartWithWindows}"; GroupDescription: "{cm:StartupGroup}"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "{cm:AppComment}"; AppUserModelID: "Dualyrics"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "{cm:AppComment}"; AppUserModelID: "Dualyrics"; Tasks: desktopicon

[Registry]
; same value name and command as dualyrics/startup.py, so the app's own "Start with Windows" switch sees it
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "Dualyrics"; ValueData: """{app}\{#AppExe}"" --startup"; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; closes the running app first (it saves its settings on the way out)
Filename: "{app}\{#AppExe}"; Parameters: "--quit"; RunOnceId: "QuitDualyrics"; Flags: waituntilterminated skipifdoesntexist

[Code]
const
  AppMutex = 'Local\Dualyrics.SingleInstance.Mutex';

var
  TasksPageSeen: Boolean;

function RunValue(var Value: String): Boolean;
begin
  Result := RegQueryStringValue(HKCU, '{#RunKey}', 'Dualyrics', Value);
end;

function RunPointsToDualyrics(): Boolean;
var
  V: String;
begin
  Result := RunValue(V) and (Pos('dualyrics.exe', Lowercase(V)) > 0);
end;

{ Close Dualyrics before files are replaced: the installed copy quits on request; an older copy somewhere else
  (v1 was a portable exe and does not know --quit) still holds the single-instance mutex and is stopped. }
procedure StopRunningCopies();
var
  ResultCode: Integer;
  Exe: String;
begin
  Exe := ExpandConstant('{app}\{#AppExe}');
  if FileExists(Exe) and CheckForMutexes(AppMutex) then
    Exec(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if CheckForMutexes(AppMutex) then
  begin
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1000);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningCopies();
  Result := '';
end;

function TasksGivenOnCommandLine(): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if (Pos('/TASKS=', Uppercase(ParamStr(I))) = 1) or (Pos('/MERGETASKS=', Uppercase(ParamStr(I))) = 1) then
      Result := True;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { show the current state: checked when Dualyrics (any copy) already starts with Windows.
    CurPageChanged also runs in silent installs, where /TASKS (or the defaults) must win. }
  if (CurPageID = wpSelectTasks) and not TasksPageSeen and not WizardSilent() and not TasksGivenOnCommandLine() then
  begin
    TasksPageSeen := True;
    if RunPointsToDualyrics() then
      WizardSelectTasks('startup')
    else
      WizardSelectTasks('!startup');
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    Log(Format('startup task: %d, Run entry for Dualyrics: %d, silent: %d', [Ord(WizardIsTaskSelected('startup')),
      Ord(RunPointsToDualyrics()), Ord(WizardSilent())]));
  if (CurStep = ssPostInstall) and not WizardIsTaskSelected('startup') and RunPointsToDualyrics() then
  begin
    if WizardSilent() then
      { silent upgrade: keep the choice made in an older copy, but start the installed one }
      RegWriteStringValue(HKCU, '{#RunKey}', 'Dualyrics', '"' + ExpandConstant('{app}\{#AppExe}') + '" --startup')
    else
      { the box was shown checked and the user cleared it }
      RegDeleteValue(HKCU, '{#RunKey}', 'Dualyrics');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  V, DataDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    { the value may have been written by the app's own switch rather than by Setup }
    if RunValue(V) and (Pos(Lowercase(ExpandConstant('{app}\{#AppExe}')), Lowercase(V)) > 0) then
      RegDeleteValue(HKCU, '{#RunKey}', 'Dualyrics');
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\{#AppName}');
    if DirExists(DataDir) then
    begin
      if UninstallSilent() then
      begin
        if ExpandConstant('{param:PURGE|0}') = '1' then
          DelTree(DataDir, True, True, True);
      end
      else if MsgBox(FmtMessage(CustomMessage('DeleteUserData'), [DataDir]), mbConfirmation,
                     MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
