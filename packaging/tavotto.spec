# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：macOS .app / Windows .exe。

三件事容易踩，都在下面处理了：

1. **worker 侧的三个模块必须是磁盘上的真 .py 文件**。渲染 worker 是**另一个
   真解释器**起的子进程（用户的论文脚本要动态 import 各种东西，冻结成第二个
   黑盒立刻就 import 不进去），所以 `engine/worker.py` 以及**它平铺 import 的
   那一整条传递闭包**（`manifest.py` / `overrides.py` / `patchspec.py` /
   `pathgeom.py` / `figcapture.py`）得能被外部解释器按路径读到——只编进
   PyInstaller 归档是不够的。
   漏一个的表现是「装完的桌面版一渲染就 ModuleNotFoundError」，而源码模式
   一切正常（tests/test_runtime_build.py::test_spec_ships_every_module_the_worker_imports
   看护，它自己也是按传递闭包算的：只看 worker.py 一层的话，`manifest.py`
   新引进来的模块会一路绿灯到用户手里）。

2. **Flask 主进程里不打包 matplotlib**。科学栈跑在 worker 子进程里，主进程
   有它没用，白白多出一两百 MB，还会把「主程序 / 渲染环境分离」这条边界废掉。

3. **两个桌面平台都把内置渲染 runtime 一起带上**（`runtime/`，由
   `scripts/build_worker_runtime.py` 生成）。这样没装过 Python 的用户装完就能
   渲染，首次渲染也不联网。runtime 作为 datas 进包 → 落在 `_internal/runtime`，
   `engine/runtime.py` 按 `sys._MEIPASS` 解析；macOS 上这一整坨再被 Tauri 收进
   `Tavotto.app/Contents/Resources/sidecar/Tavotto/`，`_MEIPASS` 照样指得准。

   走 **datas 而不是 binaries** 是有意的：binaries 会被做依赖分析并改写 rpath，
   而这份 runtime 的内部引用（`bin/python3.13` → `lib/libpython3.13.dylib`、
   各 .so 之间）本来就是自洽的，改写只会把它弄坏。实测 PyInstaller 6.x 仍会
   把 datas 里的 Mach-O 重新 adhoc 签一遍（原来的 linker 签名被换掉），**这不影响
   运行**，而且发行链随后会用 Developer ID 全部重签一遍——最终说了算的是那一次。
   符号链接与可执行位都被保留（实测），所以 `bin/python3` 不会被拍平成第二个 18 MB 副本。

4. **除了 GUI 的 Tavotto，还出一个 console 版 `tavotto-cli`**。两个 exe 出自
   同一个 Analysis、共用同一份 `_internal/`（只多一个 ~1.5 MB 的 bootloader），
   代码也是同一份 `packaging/entry.py`——差别只有 Windows 的子系统。
   为什么非要多这一个：`console=False` 的 exe 在没有真终端时 `sys.stdout`
   是 None，entry.py 会把输出改道到 app.log，外部程序 `capture_output` 拿到
   的是**空 stdout**，不是那行 JSON。于是「只装了桌面版」的用户那里，Codex
   插件永远发现不了 Tavotto。落点与发现规则见 `engine/locate.py`
   （tests/test_install_locate.py + test_runtime_build.py 看护）。

5. **Rust supervisor `tavotto-workerd` 必须进包**（两个平台都要）。它作为
   binaries 落在 `_internal/`，也就是冻结后的 `sys._MEIPASS`——
   `engine/workerd_client.find_workerd()` 的第一条查找路径。这里**缺了就直接
   失败**，不像 runtime 那样可选：回退到 Python 渲染池是**静默**的，做出来的
   包功能一样不缺、只是慢，装到用户机器上也不会有任何报错，等于永远没人发现。

用法（在仓库根目录）：
    python scripts/build_frontend.py
    cargo build --release --manifest-path workerd/Cargo.toml
    python scripts/build_worker_runtime.py      # Windows / macOS 桌面版都需要
    pyinstaller packaging/tavotto.spec --noconfirm

（`python scripts/build_desktop.py` 会按顺序把上面这些都做掉。）
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
PKG = ROOT / "src" / "tavotto"

if not (PKG / "web" / "index.html").is_file():
    raise SystemExit(
        "缺少前端构建产物 src/tavotto/web/——先跑 python scripts/build_frontend.py")

datas = [
    # 前端构建产物：app.py 按 PKG_ROOT/"web" 找，冻结后 PKG_ROOT 落在 _MEIPASS/tavotto
    (str(PKG / "web"), "tavotto/web"),
]
# worker 子进程要用的源码（见文件头说明 1）
for name in ("worker.py", "manifest.py", "overrides.py", "patchspec.py",
             "pathgeom.py", "figcapture.py"):
    datas.append((str(PKG / "engine" / name), "tavotto/engine"))

# 内置渲染 runtime（见文件头说明 3）。
# TAVOTTO_RUNTIME_SRC 可指到别处；TAVOTTO_REQUIRE_RUNTIME=1 时缺了就直接失败——
# 发行流水线必须打开它，否则「忘了构建 runtime」会安静地产出一个装完不能渲染的
# 安装包，而这种包只有到了用户手里才会暴露。
RUNTIME = Path(os.environ.get("TAVOTTO_RUNTIME_SRC") or (ROOT / "runtime"))
_require = os.environ.get("TAVOTTO_REQUIRE_RUNTIME") in ("1", "true", "yes")
_MANIFEST = RUNTIME / "runtime-manifest.json"

# 「这份 runtime 配不配得上这次构建」的判据只有一份，在构建脚本里
# （build_desktop.py 复用同一个函数）。分头各写一遍的话，迟早一边放行
# 另一边拦住，而放行的那一边才是发出去的。
sys.path.insert(0, str(ROOT / "scripts"))
from build_worker_runtime import BuildError, check_runtime_dir  # noqa: E402

if _MANIFEST.is_file():
    try:
        _info = check_runtime_dir(_MANIFEST, require_smoke=_require)
    except BuildError as exc:
        raise SystemExit(f"[tavotto.spec] {exc}")
    datas.append((str(RUNTIME), "runtime"))
    print(f"[tavotto.spec] 内置 runtime: {RUNTIME} "
          f"（{_info['platform']['os']}/{_info['platform']['arch']}，"
          f"Python {_info['python']['version']}，冒烟 {_info['build']['smoke']}）")
elif _require:
    raise SystemExit(
        f"TAVOTTO_REQUIRE_RUNTIME=1 但 {RUNTIME} 里没有可用的内置 runtime——"
        "先跑 python scripts/build_worker_runtime.py")
else:
    print(f"[tavotto.spec] 未附带内置 runtime（{RUNTIME} 不存在）——"
          "渲染将回退到用户自己的 Python")

# Rust supervisor（见文件头说明 5）。约定位置就是 cargo 自己的产出目录——
# `workerd_client._dev_tree_candidates()` 认的也是它，别再造第二个落点。
# 走 binaries 而不是 datas：PyInstaller 只对 binaries 保留可执行位。
WORKERD_NAME = "tavotto-workerd.exe" if sys.platform == "win32" else "tavotto-workerd"
WORKERD = Path(os.environ.get("TAVOTTO_WORKERD_BIN")
               or (ROOT / "workerd" / "target" / "release" / WORKERD_NAME))
if not WORKERD.is_file():
    raise SystemExit(
        f"缺少 Rust supervisor 二进制: {WORKERD}\n"
        "  先跑 cargo build --release --manifest-path workerd/Cargo.toml\n"
        "  （或者直接用 python scripts/build_desktop.py，它会一并构建）\n"
        "  桌面产物必须自带 workerd：缺了它渲染会静默回退到 Python 池，"
        "功能全在、只是慢，没有任何报错。")
binaries = [(str(WORKERD), ".")]
print(f"[tavotto.spec] Rust supervisor: {WORKERD}")

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
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

ICON = str(ROOT / "assets" / "icon" /
           ("icon.icns" if sys.platform == "darwin" else "icon.ico"))

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Tavotto",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # UPX 压缩会让 Windows Defender 误报
    console=False,                  # 双击不弹黑窗；日志走数据目录里的 app.log
    icon=ICON,
)

# console 版命令行（见文件头说明 4）。名字**必须**与
# engine/locate.CLI_NAME 一致：安装清单和已知安装位置两条发现链找的都是它。
cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tavotto-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,                   # 这一行就是它存在的全部理由
    icon=ICON,
)

coll = COLLECT(
    exe,
    cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Tavotto",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Tavotto.app",
        icon=str(ROOT / "assets" / "icon" / "icon.icns"),
        bundle_identifier="com.tavotto.tavotto",
        info_plist={
            "CFBundleName": "Tavotto",
            "CFBundleDisplayName": "Tavotto",
            "CFBundleShortVersionString": __import__(
                "runpy").run_path(str(PKG / "__init__.py"))["__version__"],
            "CFBundleVersion": __import__(
                "runpy").run_path(str(PKG / "__init__.py"))["__version__"],
            "NSHighResolutionCapable": True,
            # 纯本地工具，不需要任何隐私权限；显式声明避免系统弹无谓的授权框
            "LSApplicationCategoryType": "public.app-category.productivity",
        },
    )
