; Inno Setup script for the Mulligan Coach overlay.
;
; Wraps the PyInstaller one-folder build (dist/MulliganCoach/) into a
; single MulliganCoachSetup.exe that gives the user a real
; Programs-and-Features entry, a Start-menu shortcut, and a clean
; uninstall — the friendly alternative to "unzip this folder anywhere".
;
; Build (from the repo root, after build_distribution.py has produced
; dist/MulliganCoach/):
;
;     ISCC.exe /DMyAppVersion=1.0.0 packages\overlay\packaging\mulligan_coach.iss
;
; The CI workflow (.github/workflows/build-release.yml) does exactly
; that on a Windows runner. If ISCC isn't on PATH it lives at
;   C:\Program Files (x86)\Inno Setup 6\ISCC.exe
;
; Relative paths below are resolved against THIS script's directory
; (packages/overlay/packaging/), so RepoRoot walks three levels up.

#define MyAppName "Mulligan Coach"
#define MyAppPublisher "Bastian von Beschwitz"
#define MyAppURL "https://github.com/vonbeschwitz/mulligan_coach_data"
#define MyAppExeName "MulliganCoach.exe"

; Version is normally passed in with /DMyAppVersion=... by the build
; workflow (from the git tag or the workflow_dispatch input). The
; fallback keeps a bare `ISCC mulligan_coach.iss` working for a quick
; local smoke build.
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

; packaging -> overlay -> packages -> <repo root>
#define RepoRoot "..\..\.."
#define BundleDir RepoRoot + "\dist\MulliganCoach"

[Setup]
; A stable AppId ties every version to the same Programs-and-Features
; entry so upgrades replace in place instead of stacking. Never change
; it once shipped.
AppId={{7B4C9E2A-3F1D-4A6B-9C8E-2D5F1A0B3C4D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
; Per-user install (no admin / UAC prompt). This matters for an
; UNSIGNED build: requesting elevation on an unsigned installer is a
; worse SmartScreen experience, and the overlay only ever writes to
; the user's own profile anyway. {autopf} + "lowest" resolves to
; %LOCALAPPDATA%\Programs\MulliganCoach.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
DefaultDirName={autopf}\MulliganCoach
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#RepoRoot}\dist\installer
OutputBaseFilename=MulliganCoachSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; The bundle is 64-bit (PyInstaller freezes the host Python). Refuse to
; install on 32-bit Windows rather than fail mysteriously at runtime.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; ---------------------------------------------------------------------
; CODE SIGNING (intentionally disabled — unsigned build for now).
;
; Signing is deferred (roadmap Step 7 decision, 2026-07-03). When a
; certificate is available:
;   1. Register a sign tool in the build environment, e.g.
;        ISCC ... /Smysigntool="signtool.exe sign /f cert.pfx ..."
;      or via Inno's "Tools > Configure Sign Tools" (name it "signtool").
;   2. Uncomment the directive below so BOTH the installer and the
;      uninstaller get signed. The PyInstaller EXE inside the bundle
;      should be signed separately in build_distribution.py before this
;      installer wraps it.
; SignTool=signtool
; ---------------------------------------------------------------------

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; Default-CHECKED (no "unchecked" flag): the overlay hides itself until
; MTG Arena runs, so "always running, appears with Arena" is the whole
; product experience — a fresh install should land with autostart on
; without the user having to discover the gear-menu toggle. Unticking
; here is respected by the app (see the [Code] marker below). Inno
; remembers the choice for future upgrades of the same install.
Name: "autostart"; Description: "Start {#MyAppName} when Windows starts (recommended; the overlay only appears while MTG Arena is running)"; GroupDescription: "Startup:"

[Files]
; The launcher.
Source: "{#BundleDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; Everything PyInstaller put next to it (Python runtime, Qt, xgboost,
; the bundled model + data). recursesubdirs/createallsubdirs mirrors the
; whole _internal/ tree; ignoreversion because these are our own files,
; not shared system DLLs subject to version arbitration.
Source: "{#BundleDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Same per-user Run entry the app's own gear-menu toggle manages
; (overlay/autostart.py): value name "MulliganCoach", quoted EXE path,
; --autostart flag so login launches identify themselves (no tray
; balloon at every boot). Writing it at install time (rather than only
; on the app's first launch) means autostart is on the moment the
; install finishes, and an upgrade/reinstall re-points a stale entry
; at the new location. uninsdeletevalue removes it on uninstall.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "MulliganCoach"; ValueData: """{app}\{#MyAppExeName}"" --autostart"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
; Offer to launch after install; skipifsilent so an automated/silent
; install doesn't pop the overlay.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
{ The app's first launch would otherwise default-enable autostart itself
  (autostart.enable_default_if_first_run) — that behaviour remains for
  zip-installed copies, and it's suppressed by a marker file once an
  autostart decision has been made on this account. The installer just
  made that decision (the "autostart" task, whether the user left it
  ticked or not), so record the marker here. Without this, unticking
  the task would be silently overridden on first launch.
  Best-effort: a failed write is ignored — the worst case is the app
  re-applying the default, which for a ticked task is a no-op anyway. }
procedure CurStepChanged(CurStep: TSetupStep);
var
  StateDir: string;
begin
  if CurStep = ssPostInstall then
  begin
    StateDir := ExpandConstant('{localappdata}') + '\MulliganCoach';
    if ForceDirectories(StateDir) then
      SaveStringToFile(StateDir + '\_autostart_seeded.txt',
        ExpandConstant('{app}\{#MyAppExeName}'), False);
  end;
end;
