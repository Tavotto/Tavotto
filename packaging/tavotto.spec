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
   主进程自己的渲染闭包是 RenderCore（pikepdf / fontTools / uharfbuzz / Pillow +
   PDFium render child；U10 起 PyMuPDF 退役，ADR 0072）：PDFium 与 qpdf 的共享库要
   显式收（下面 `collect_dynamic_libs` / `collect_all`），批准字体随 `resources/` datas
   整棵进包（构建前先 `scripts/fetch_fonts.py`，缺了在这里就失败）。

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

# **批准字体必须在**（统一实施包 U10，ADR 0060 / 0072）：RenderCore 是默认后端，字体不进 git，
# 由 scripts/fetch_fonts.py 按 allowlist 的 sha256 取到 resources/fonts/，再随下面那条 `resources/`
# datas 整棵进包。漏了这一步的表现是「装完的桌面版一导出就 fonts_dir_missing」，而源码树上一切
# 正常——与前端产物同一种坏法，同一种拦法：在这里就失败，不留到用户机器上。判据与
# `fetch_fonts.py --check` 同源（逐张脸算 sha256），不是数文件。
sys.path.insert(0, str(ROOT / "scripts"))
from fetch_fonts import check as check_fonts  # noqa: E402

_font_problems = check_fonts(PKG / "resources" / "fonts")
if _font_problems:
    raise SystemExit(
        "批准字体不齐（先跑 python scripts/fetch_fonts.py）:\n  " + "\n  ".join(_font_problems))

datas = [
    # 前端构建产物：app.py 按 PKG_ROOT/"web" 找，冻结后 PKG_ROOT 落在 _MEIPASS/tavotto
    (str(PKG / "web"), "tavotto/web"),
    # **包内数据文件 PyInstaller 不会自己收**：Analysis 只把 .py 编进 PYZ，
    # `importlib.resources.files("tavotto")` 在冻结产物里落在 _MEIPASS/tavotto，
    # 数据得显式放到同一处。漏一条的表现是源码树 / wheel 里一切正常、桌面版
    # 第一次用到时报「文件不存在」。
    #   profiles/   出版规范（预检两侧共读的唯一权威，engine/profiles.py）
    #   resources/  离线教程项目（engine/tutorial.py，ADR 0039）
    #   pdfbackend/canvas_coverage.json  画布字形覆盖表（glyphplan.coverage_table_path，
    #               preflight 的文字检查读它；wheel/sdist 随包自然收录，冻结产物要显式列）
    (str(PKG / "profiles"), "tavotto/profiles"),
    (str(PKG / "resources"), "tavotto/resources"),
    (str(PKG / "pdfbackend" / "canvas_coverage.json"), "tavotto/pdfbackend"),
    #   rendercore/fonts_allowlist.json  批准字体 allowlist（fonts.load_allowlist 读它：字体注册表按它的 sha256 收脸，
    #               ADR 0060）。U10 切默认后冻结产物第一次真导出就是在这里 ENOENT 的——源码树 / wheel 里它自然在
    (str(PKG / "rendercore" / "fonts_allowlist.json"), "tavotto/rendercore"),
]
# 执行侧子进程要用的源码（见文件头说明 1）。两条入口：
#   safe worker      —— worker.py 及它平铺 import 的传递闭包；
#   native bridge    —— bridge_runner.py（用户自己的 Python 按绝对路径起它）
#                       + bridgeboot.py（私有命名空间装载器）。
# 两份清单由 tests/test_runtime_build.py 从**源码的 import 闭包**反推校验，
# 不靠人记得回来改这一行。
for name in ("worker.py", "manifest.py", "overrides.py", "patchspec.py",
             "pathgeom.py", "axestraversal.py", "spinemodel.py", "tickmodel.py", "colorbarmodel.py", "legendmodel.py", "figcapture.py", "figsession.py", "wireproto.py",
             "previewbudget.py", "preview_complexity.py", "preview_hybrid.py",
             "bridge_runner.py", "bridgeboot.py"):
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

# PDF 后端契约层 `pdfbackend/__init__.py` 按 `TAVOTTO_RENDER_BACKEND` 用 importlib **按名字**装载
# 实现模块（U08，ADR 0067）——静态分析看不见这条边，冻结产物里就没有那个模块，表现是 `probe_asset`
# 一调就 ModuleNotFoundError、「示例项目里一个面板都没扫到」，而源码模式一切正常（2026-09-22 #476 的
# macOS / Windows 三条冒烟腿）。清单从契约层自己的 `_IMPL_MODULES` 取，**不在这里抄第二份**：U10 删旧
# 后端 / 换默认时它自动跟着变（今天只剩 rendercore/facade）；
# tests/test_runtime_build.py::test_spec_ships_every_backend_the_contract_layer_can_select 看护。
sys.path.insert(0, str(ROOT / "src"))
from tavotto import pdfbackend as _pdfbackend  # noqa: E402

BACKEND_IMPLS = sorted(_pdfbackend._IMPL_MODULES.values())
print(f"[tavotto.spec] 后端实现模块（hiddenimports）: {', '.join(BACKEND_IMPLS)}")

# RenderCore 的原生闭包（U10，ADR 0072）：PDFium 的共享库住在 pypdfium2_raw 的包目录里（不是
# Python 扩展，PyInstaller 的依赖分析看不见它），pikepdf 的 qpdf 库同理——两者都要显式收。
# 少了前者的表现是 render child 起来就 `render_child_died`（找不到 libpdfium），少了后者是
# 导出时 ImportError。配方与 scripts/dev/u07_freeze_child.py 的 --collect-binaries / --collect-all
# 同一份（那份在四个平台上验过 child 自起、真渲染）。
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs  # noqa: E402

binaries += collect_dynamic_libs("pypdfium2_raw")
_pk_datas, _pk_binaries, _pk_hidden = collect_all("pikepdf")
datas += _pk_datas
binaries += _pk_binaries
_pdfium_libs = [b for b in binaries if "pdfium" in os.path.basename(b[0]).lower()]
if not _pdfium_libs:
    raise SystemExit(
        "pypdfium2_raw 里没找到 PDFium 共享库——打包环境里没装 pypdfium2？"
        "（pip install -r requirements.txt）")
print(f"[tavotto.spec] PDFium: {[os.path.basename(b[0]) for b in _pdfium_libs]}；"
      f"pikepdf 收进 {len(_pk_binaries)} 个二进制")

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        # Flask 的这几个依赖是运行时按名字取的，静态分析看不见
        "jinja2", "markupsafe", "itsdangerous", "click", "werkzeug",
        # render child 在冻结产物里由同一个 exe 以 `--render-child` 自起（packaging/entry.py 分派），
        # child_main 里才 import pypdfium2——入口没有静态 import 它，点名收进 PYZ
        "tavotto.rendercore.renderchild", "tavotto.rendercore.renderhost", "pypdfium2",
        # 契约层按名字装载的后端实现（见上；清单取自 _IMPL_MODULES，不手写）
        *BACKEND_IMPLS,
        *_pk_hidden,
    ],
    hookspath=[],
    runtime_hooks=[],
    # 科学栈刻意排除（见文件头说明 2）：主进程不需要，打包机上装了也不要进包。
    # 内置 runtime 里的那一套走 datas，与这里互不影响。**PIL 不在这张表上**（U10 起）：
    # Pillow 是 RenderCore 的直接依赖（rasterio 解码位图素材、pikepdf 的硬依赖，ADR 0066），
    # 排除它的表现是导出位图面板时 ImportError——源码树上永远复现不了。
    excludes=["matplotlib", "numpy", "scipy", "pandas", "tkinter",
              "pytest", "setuptools", "pip", "pymupdf", "fitz"],
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
