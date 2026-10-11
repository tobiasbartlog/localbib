; Inno Setup script for the LocalBib Windows installer (issue #146, ADR-0015).
;
; It wraps the PyInstaller *onedir* bundle — dist\LocalBib\, produced by
; `pyinstaller --noconfirm --clean localbib.spec` — into a single setup .exe.
; The onefile PyInstaller build is retired: onedir starts faster, and the
; installer is what the user downloads either way.
;
; The version is always passed in, never guessed, so exactly one thing decides
; it: the release tag (see .github/workflows/release.yml, which writes the same
; value into the VERSION file that the frozen app reads at startup).
;
;   iscc /DMyAppVersion=0.3.0 installer\localbib.iss
;   -> dist\installer\LocalBib-Setup-0.3.0.exe
;
; The asset name is a contract, not cosmetics: issue #148's one-click update
; resolves the release asset by the literal name LocalBib-Setup-<version>.exe.
; Change it and in-app updating breaks.
;
; Launch is UNSIGNED (ADR-0015 defers Azure Trusted Signing until revenue
; exists); the release workflow carries the marked slot for the signing step.

#ifndef MyAppVersion
  ; Local builds without /D get a numeric placeholder — VersionInfoVersion
  ; below rejects anything that is not purely numeric.
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "LocalBib"
#define MyAppPublisher "Tobias Bartlog"
#define MyAppURL "https://github.com/tobiasbartlog/localbib"
#define MyAppExeName "LocalBib.exe"
; Start with Windows (#214, ADR-0023): the same HKCU Run value the app reads
; and writes (autostart.py - RUN_KEY, VALUE_NAME, AUTOSTART_ARG; the tests pin
; that both sides agree). The argument makes the app start silently: tray
; icon only, no browser tab at every sign-in.
#define AutostartRunKey "Software\Microsoft\Windows\CurrentVersion\Run"
#define AutostartApprovedKey "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
#define AutostartValue "LocalBib"
#define AutostartArg "--autostart"

[Setup]
; Stable AppId — it is the identity an upgrade or uninstall is matched on.
; NEVER change it; a new AppId turns every update into a parallel install.
AppId={{B3F1D6A2-7C4E-4B1D-9A55-2E0C7F5A9D14}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
VersionInfoVersion={#MyAppVersion}
; Per-user install, with no way up: PrivilegesRequiredOverridesAllowed is
; deliberately NOT set. An unsigned installer that also demands UAC would be two
; scary dialogs instead of one — but the deciding reason is #148, and it was
; measured rather than assumed. Allowing `dialog` let a user install
; machine-wide; UsePreviousPrivileges (default yes) then made the silent
; one-click update detect that install and re-launch itself elevated, appending
; /ALLUSERS to its own command line. Setup's log said "Administrative install
; mode: Yes" and the run took ~40 s instead of 1 s — the time a UAC prompt sat
; there waiting. The app has already exited by then (routers/version.py leaves
; one second after starting us), so the user's window vanishes and a rights
; prompt appears out of nowhere for an update advertised as one click. A
; per-user install never elevates, so that path cannot arise.
PrivilegesRequired=lowest
; The bundle is 64-bit, so it belongs in Program Files and the native registry
; view — without this, a machine-wide install landed in Program Files (x86) and
; HKLM\...\WOW6432Node. Needs Inno Setup 6.3+ for the `x64compatible` value.
ArchitecturesInstallIn64BitMode=x64compatible
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist\installer
OutputBaseFilename=LocalBib-Setup-{#MyAppVersion}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
; Icon of the setup .exe itself. The shortcuts and the taskbar entry take
; theirs from LocalBib.exe, into which PyInstaller compiles the same .ico
; (see localbib.spec) — this line only covers the downloaded installer.
SetupIconFile=..\static\icons\localbib.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; One-click update (#148): the app exits itself before starting this installer,
; but a lingering handle must not turn the update into a "file in use" dead end
; — so close it by force. The relaunch afterwards is the [Run] entry below and
; nothing else; Restart Manager must not also bring the app back, or the user
; ends up with two LocalBib windows.
CloseApplications=force
RestartApplications=no

[Languages]
; Englisch zuerst: Inno waehlt nach Systemsprache und faellt auf den ERSTEN
; Eintrag zurueck, wenn es nichts erkennt. Seit ADR-0018 ist Englisch die
; Voreinstellung des Produkts - der Installer darf davon nicht abweichen.
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[CustomMessages]
english.AutostartTask=Start with Windows
german.AutostartTask=Mit Windows starten

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; Off by default, like the desktop icon. The [Code] below shows the entry's
; real state on a reinstall and writes or removes the Run value accordingly.
Name: "autostart"; Description: "{cm:AutostartTask}"; GroupDescription: "{cm:AutoStartProgramGroupDescription}"; Flags: unchecked

[InstallDelete]
; Wipe the old bundle before writing the new one. `ignoreversion` below only
; overwrites files the new build also has — a file the new build dropped just
; stays. That is how one install ended up carrying two interpreters at once:
; python311.dll from an older build next to python314.dll from a newer one,
; with the bootloader picking 3.11. The version that answers an HTTPS request
; then decided whether certificate checking used the same defaults as the
; source tree (VERIFY_X509_PARTIAL_CHAIN is only on from 3.13), and the app
; failed against TLS endpoints the source ran against fine. An upgrade must
; leave exactly the files the new build produced, so remove the whole bundle
; directory first — everything in it is reinstalled from dist\LocalBib on the
; next lines, and the user's library lives outside {app}.
Type: filesandordirs; Name: "{app}\_internal"
; Older onedir layouts put the interpreter next to the .exe rather than under
; _internal\ — clear those out too, or they linger forever.
Type: files; Name: "{app}\*.dll"
Type: files; Name: "{app}\*.pyd"

[Files]
; The whole onedir bundle. `recursesubdirs` matters: PyInstaller puts the
; interpreter, the bundled static/ and templates/ trees and the VERSION file
; under _internal\.
Source: "..\dist\LocalBib\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Interactive install: the usual "run now" checkbox on the last wizard page.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
; Silent install — that is the in-app one-click update (#148). The running app
; shut itself down so these files could be replaced, so bringing it back is not
; optional: without this line the user's window simply disappears. `skipifsilent`
; above and `skipifnotsilent` here are mutually exclusive, so exactly one of the
; two entries ever fires.
;
; `runasoriginaluser` is the belt to the braces above: should Setup ever run
; elevated anyway (someone right-clicks "run as administrator"), the relaunched
; app must still belong to the logged-in user. Without the flag the [Run] entry
; inherits Setup's elevated token — measured on a machine where elevation
; switches to a separate admin account, and LocalBib would come back with a
; different %USERPROFILE%: another ~\Literatur, another .env, another database,
; i.e. an empty library where the user's own one used to be.
Filename: "{app}\{#MyAppExeName}"; Flags: nowait skipifnotsilent runasoriginaluser

; There is deliberately no uninstall-delete section: the user's library lives
; outside {app} (LITERATUR_BASE_DIR, default ~\Literatur) and an uninstall must
; never touch it. Everything the installer wrote is inside {app} and Inno
; removes exactly that - plus the autostart value below, which is not a file.

[Code]
// Start with Windows (#214, ADR-0023). The HKCU Run value is the one truth:
// the app's Settings switch and its tray menu read and write the same entry.
// So the task shows what is there, and only a run someone looked at changes it.

var
  AutostartTaskShown: Boolean;

// On = the Run value exists and Task Manager has not disabled it (an odd
// first byte under StartupApproved) - the rule of autostart.is_enabled.
function AutostartIsOn(): Boolean;
var
  Approved: AnsiString;
begin
  Result := RegValueExists(HKCU, '{#AutostartRunKey}', '{#AutostartValue}');
  if Result and RegQueryBinaryValue(HKCU, '{#AutostartApprovedKey}', '{#AutostartValue}', Approved) then
    if (Length(Approved) > 0) and ((Ord(Approved[1]) and 1) = 1) then
      Result := False;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpSelectTasks) and not AutostartTaskShown then
  begin
    AutostartTaskShown := True;
    if AutostartIsOn() then
      WizardSelectTasks('autostart')
    else
      WizardSelectTasks('!autostart');
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // A silent run is the in-app one-click update (#148): nobody saw the task,
  // so the entry stays exactly as the user last set it.
  if (CurStep = ssPostInstall) and not WizardSilent() then
  begin
    if WizardIsTaskSelected('autostart') then
    begin
      RegWriteStringValue(HKCU, '{#AutostartRunKey}', '{#AutostartValue}',
        '"' + ExpandConstant('{app}\{#MyAppExeName}') + '" {#AutostartArg}');
      // Switching on lifts a Task Manager "disabled", as the app does.
      RegDeleteValue(HKCU, '{#AutostartApprovedKey}', '{#AutostartValue}');
    end
    else if AutostartTaskShown then
    begin
      RegDeleteValue(HKCU, '{#AutostartRunKey}', '{#AutostartValue}');
      RegDeleteValue(HKCU, '{#AutostartApprovedKey}', '{#AutostartValue}');
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // Nothing stays behind, however the entry got there (this task, the
  // Settings switch or the tray menu).
  if CurUninstallStep = usUninstall then
  begin
    RegDeleteValue(HKCU, '{#AutostartRunKey}', '{#AutostartValue}');
    RegDeleteValue(HKCU, '{#AutostartApprovedKey}', '{#AutostartValue}');
  end;
end;
