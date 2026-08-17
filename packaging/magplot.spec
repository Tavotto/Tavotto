# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：macOS .app / Windows .exe。

三件事容易踩，都在下面处理了：

1. **worker 侧的三个模块必须是磁盘上的真 .py 文件**。渲染 worker 是**另一个
   真解释器**起的子进程（用户的论文脚本要动态 import 各种东西，冻结成第二个
   黑盒立刻就 import 不进去），所以 `engine/worker.py` 以及它 `import` 的
   `manifest.py` / `overrides.py` 得能被外部解释器按路径读到——只编进
   PyInstaller 归档是不够的。

2. **Flask 主进程里不打包 matplotlib**。科学栈跑在 worker 子进程里，主进程
   有它没用，白白多出一两百 MB，还会把「主程序 / 渲染环境分离」这条边界废掉。

3. **Windows 桌面版把内置渲染 runtime 一起带上**（`runtime/`，由
   `scripts/build_worker_runtime.py` 生成）。这样没装过 Python 的用户装完就能
   渲染，首次渲染也不联网。runtime 作为 datas 进包 → 落在 `_internal/runtime`，
   `engine/runtime.py` 按 `sys._MEIPASS` 解析，安装程序与免安装 zip 自动都含它。
   macOS / 没构建 runtime 时这一段整个跳过，行为与从前一致。

用法（在仓库根目录）：
    python scripts/build_frontend.py
    python scripts/build_worker_runtime.py      # 仅 Windows 桌面版需要
    pyinstaller packaging/magplot.spec --noconfirm
"""
import os
import sys
from pathlib import Path

# Windows 上 stdout 被重定向成管道时会退回系统区域编码（cp1252/cp936），
# 下面带中文的 print 会 UnicodeEncodeError 打死整个 PyInstaller 构建。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

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

# 内置渲染 runtime（见文件头说明 3）。
# MAGPLOT_RUNTIME_SRC 可指到别处；MAGPLOT_REQUIRE_RUNTIME=1 时缺了就直接失败——
# 发行流水线必须打开它，否则「忘了构建 runtime」会安静地产出一个装完不能渲染的
# 安装包，而这种包只有到了用户手里才会暴露。
RUNTIME = Path(os.environ.get("MAGPLOT_RUNTIME_SRC") or (ROOT / "runtime"))
_require = os.environ.get("MAGPLOT_REQUIRE_RUNTIME") in ("1", "true", "yes")
if (RUNTIME / "runtime-manifest.json").is_file():
    datas.append((str(RUNTIME), "runtime"))
    print(f"[magplot.spec] 内置 runtime: {RUNTIME}")
elif _require:
    raise SystemExit(
        f"MAGPLOT_REQUIRE_RUNTIME=1 但 {RUNTIME} 里没有可用的内置 runtime——"
        "先跑 python scripts/build_worker_runtime.py")
else:
    print(f"[magplot.spec] 未附带内置 runtime（{RUNTIME} 不存在）——"
          "渲染将回退到用户自己的 Python")

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
    # 科学栈刻意排除（见文件头说明 2）：主进程不需要，打包机上装了也不要进包。
    # 内置 runtime 里的那一套走 datas，与这里互不影响。
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
