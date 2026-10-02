; Inno Setup 스크립트 - Windows 설치 파일(RumiSetup-x.y.z.exe) 생성
; 1) python packaging/build.py  로 dist\Rumi 를 먼저 만든 뒤
; 2) Inno Setup 의 iscc packaging\windows\rumi.iss 실행

#define AppName "Rumi"
#define AppVersion "0.3.4"

[Setup]
AppId={{6C1E7B0E-6D3B-4E0B-9C61-7A2B7F5B1C11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=drmoon-cmd
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=..\..\dist
OutputBaseFilename=RumiSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
ChangesAssociations=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; Flags: unchecked
Name: "assoc"; Description: "동영상 파일을 Rumi 로 열기 (mp4, mkv, avi, mov, wmv, webm)"; Flags: unchecked

[Files]
Source: "..\..\dist\Rumi\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Rumi.exe"
Name: "{group}\{#AppName} 제거"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Rumi.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\Rumi.Video"; ValueType: string; ValueData: "동영상"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\Rumi.Video\shell\open\command"; ValueType: string; ValueData: """{app}\Rumi.exe"" ""%1"""; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\.mp4\OpenWithProgids"; ValueType: string; ValueName: "Rumi.Video"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\.mkv\OpenWithProgids"; ValueType: string; ValueName: "Rumi.Video"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\.avi\OpenWithProgids"; ValueType: string; ValueName: "Rumi.Video"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\.mov\OpenWithProgids"; ValueType: string; ValueName: "Rumi.Video"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\.wmv\OpenWithProgids"; ValueType: string; ValueName: "Rumi.Video"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\.webm\OpenWithProgids"; ValueType: string; ValueName: "Rumi.Video"; Flags: uninsdeletevalue; Tasks: assoc

[Run]
Filename: "{app}\Rumi.exe"; Description: "Rumi 실행"; Flags: nowait postinstall skipifsilent
