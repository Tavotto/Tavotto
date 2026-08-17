; Inno Setup 脚本：把 PyInstaller 的 onedir 产物打成 Windows 安装程序。
;
; PrivilegesRequired=lowest 是有意的：安装到用户目录，不弹 UAC。软件没有代码
; 签名证书，再要一次管理员授权只会让 SmartScreen 的警告显得更可疑。
;
; 安装包里含 **Magplot 内置渲染 runtime**（`_internal\runtime\`，由
; scripts/build_worker_runtime.py 生成、经 packaging/magplot.spec 进包）：
; 用户不需要自己装 Python，首次渲染也不联网。下面 [Files] 那条 recursesubdirs
; 已经把它一起收进来了，但**必须显式检查它在不在**——漏了照样能编译出一个
; 安装包，而那个包装到用户机器上才会发现渲染用不了。
;
; 编译： iscc /DVersion=0.1.1 packaging\magplot.iss
;   跳过检查（仅用于本地调试界面）： iscc /DSkipRuntimeCheck ...

#ifndef Version
  #define Version "0.0.0"
#endif

#define RuntimeManifest AddBackslash(SourcePath) + "..\dist\Magplot\_internal\runtime\runtime-manifest.json"
#ifndef SkipRuntimeCheck
  #if !FileExists(RuntimeManifest)
    #error 缺少内置渲染 runtime（dist\Magplot\_internal\runtime\）。先跑 python scripts\build_worker_runtime.py，再用 MAGPLOT_REQUIRE_RUNTIME=1 重新 pyinstaller。
  #endif
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
; recursesubdirs 一并收走 _internal\runtime\（内置 Python + 科学栈 + 许可证）
Source: "..\dist\Magplot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; 运行期一个字节都不往安装目录写（.pyc 与 matplotlib 缓存由 engine/runtime.py
; 改道到用户数据目录），所以这里只清理理论上的残留，正常卸载它是空转。
[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal\runtime\Lib\site-packages\__pycache__"

[Icons]
Name: "{group}\Magplot"; Filename: "{app}\Magplot.exe"
Name: "{autodesktop}\Magplot"; Filename: "{app}\Magplot.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Magplot.exe"; Description: "Launch Magplot"; Flags: nowait postinstall skipifsilent
