; Inno Setup script -> dist\CopilotVoiceSetup.exe
#define AppName "Copilot Voice"
#define AppExe "CopilotVoice.exe"
#define TaskName "CopilotVoiceAutostart"

[Setup]
AppName={#AppName}
AppVersion=0.6.1
AppPublisher=aryasuneesh
DefaultDirName={autopf}\CopilotVoice
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=dist
OutputBaseFilename=CopilotVoiceSetup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
; the key hook needs elevation to suppress the Copilot key
PrivilegesRequired=admin
WizardStyle=modern

[Files]
Source: "dist\CopilotVoice\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"

[Tasks]
Name: "autostart"; Description: "Start {#AppName} when I log in"

[Run]
; scheduled task rather than a Run key: /rl HIGHEST gives elevation with no UAC prompt
Filename: "schtasks"; Parameters: "/create /f /tn {#TaskName} /sc onlogon /rl HIGHEST /tr """"""{app}\{#AppExe}"""""""; \
  Flags: runhidden; Tasks: autostart
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName} now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "schtasks"; Parameters: "/delete /f /tn {#TaskName}"; Flags: runhidden; RunOnceId: "DelTask"
; config.json in %APPDATA%\CopilotVoice is deliberately left behind on uninstall:
; the uninstaller runs elevated, so {userappdata} may not be your profile.
