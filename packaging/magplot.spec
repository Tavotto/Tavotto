# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：macOS .app / Windows .exe。

两件事容易踩，都在下面处理了：

1. **worker 侧的三个模块必须是磁盘上的真 .py 文件**。渲染 worker 是用**用户
   自己的 Python** 起的子进程（用户的论文脚本要 import 他自己那套 scipy /
   pandas，我们塞进包里的解释器满足不了），所以 `engine/worker.py` 以及它
   `import` 的 `manifest.py` / `overrides.py` 得能被外部解释器按路径读到——
   只编进 PyInstaller 归档是不够的。

2. **不打包 matplotlib**。理由同上：包里的科学栈对用户脚本没用，白白多出
   一两百 MB。独立应用靠 `pool.find_worker_python()` 找用户已有的环境。

用法（在仓库根目录）：
    python scripts/build_frontend.py
    pyinstaller packaging/magplot.spec --noconfirm
"""
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
PKG = ROOT / "src" / "magplot"

if not (PKG / "web" / "index.html").is_file():
    raise SystemExit(
        "缺少前端构建产物 src/magplot/web/——先跑 python scripts/build_frontend.py")

datas = [
    # 前端构建产物：app.py 按 PKG_ROOT/"web" 找，冻结后 PKG_ROOT 落在 _MEIPASS/magplot
    (str(PKG / "web"), "magplot/web"),
]
# worker 子进程要用的源码（见文件头说明 1）
for name in ("worker.py", "manifest.py", "overrides.py"):
    datas.append((str(PKG / "engine" / name), "magplot/engine"))

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # Flask 的这几个依赖是运行时按名字取的，静态分析看不见
        "jinja2", "markupsafe", "itsdangerous", "click", "werkzeug",
    ],
    hookspath=[],
    runtime_hooks=[],
    # 科学栈刻意排除（见文件头说明 2）；打包机上装了也不要进包
    excludes=["matplotlib", "numpy", "scipy", "pandas", "PIL", "tkinter",
              "pytest", "setuptools", "pip"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Magplot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # UPX 压缩会让 Windows Defender 误报
    console=False,                  # 双击不弹黑窗；日志走数据目录里的 app.log
    icon=str(ROOT / "assets" / "icon" /
             ("icon.icns" if sys.platform == "darwin" else "icon.ico")),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Magplot",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Magplot.app",
        icon=str(ROOT / "assets" / "icon" / "icon.icns"),
        bundle_identifier="com.erwanjun.magplot",
        info_plist={
            "CFBundleName": "Magplot",
            "CFBundleDisplayName": "Magplot",
            "CFBundleShortVersionString": __import__(
                "runpy").run_path(str(PKG / "__init__.py"))["__version__"],
            "CFBundleVersion": __import__(
                "runpy").run_path(str(PKG / "__init__.py"))["__version__"],
            "NSHighResolutionCapable": True,
            # 纯本地工具，不需要任何隐私权限；显式声明避免系统弹无谓的授权框
            "LSApplicationCategoryType": "public.app-category.productivity",
        },
    )
