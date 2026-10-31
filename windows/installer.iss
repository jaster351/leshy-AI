#ifndef MyAppVersion
  #define MyAppVersion "1.10.3"
#endif

[Setup]
AppId={{B4F54CC2-AC3C-4A3E-9E38-9A9E53A71D03}
AppName=Леший
AppVersion={#MyAppVersion}
AppPublisher=Leshy AI
DefaultDirName={autopf}\Leshy AI
DefaultGroupName=Леший
OutputDir=..\dist
OutputBaseFilename=LeshySetup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Leshy.exe
SetupLogging=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\Leshy\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Леший"; Filename: "{app}\Leshy.exe"
Name: "{autodesktop}\Леший"; Filename: "{app}\Leshy.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные значки:"; Flags: unchecked

[Run]
Filename: "{app}\Leshy.exe"; Description: "Запустить Лешего"; Flags: nowait postinstall skipifsilent
