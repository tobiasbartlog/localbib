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
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

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
; removes exactly that.
