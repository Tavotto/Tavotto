; Inno Setup 脚本：把 PyInstaller 的 onedir 产物打成 Windows 安装程序。
;
; PrivilegesRequired=lowest 是有意的：安装到用户目录，不弹 UAC。软件没有代码
; 签名证书，再要一次管理员授权只会让 SmartScreen 的警告显得更可疑。
;
; 编译： iscc /DVersion=0.1.1 packaging\magplot.iss

#ifndef Version
  #define Version "0.0.0"
#endif

[Setup]
AppId={{9F3B2C41-6D5E-4A7C-9E18-2B0D4F6A8C31}
AppName=Magplot
AppVersion={#Version}
AppPublisher=erwanjun
AppPublisherURL=https://github.com/erwanjun/magplot
DefaultDirName={autopf}\Magplot
DefaultGroupName=Magplot
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\out
OutputBaseFilename=Magplot-{#Version}-Windows-Setup
SetupIconFile=..\assets\icon\icon.ico
UninstallDisplayIcon={app}\Magplot.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\Magplot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Magplot"; Filename: "{app}\Magplot.exe"
Name: "{autodesktop}\Magplot"; Filename: "{app}\Magplot.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Magplot.exe"; Description: "Launch Magplot"; Flags: nowait postinstall skipifsilent
