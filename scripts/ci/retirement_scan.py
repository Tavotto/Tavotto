#!/usr/bin/env python3
"""零旧库退役扫描——PyMuPDF 退役后的看护门禁（统一实施包 U10，ADR 0072）。

**主语是 Tavotto 应用 / 发行 / runtime 闭包**（06 §3、D15）：不是整个仓库，不是用户硬盘，不是测试的读取器。
五把尺子各自量不同的主语，互相不能当代理（一把绿不代表另一把绿）：

| 尺子 | 主语 | 判据 |
|---|---|---|
| `source` | 应用源码集合（`SOURCE_ROOTS`：`src/tavotto`、MCP server、技能脚本、`packaging/entry.py` + `tavotto.spec`、发行链脚本） | **AST** 里没有 `import pymupdf / fitz`（含 `importlib.import_module` / `__import__` 的字面量）。注释 / docstring / 显示名字符串（`depresolve` 的 `"fitz": "PyMuPDF"`）AST 看不到——有意 |
| `deps` | `--python` 环境里 `tavotto` 发行版**声明的**运行时依赖闭包（Requires-Dist 去掉带 extra 的、marker 按本平台求值、递归展开） | 闭包里没有 pymupdf。**装在同一个 site-packages 里但不在闭包里的不算**（测试的读取器） |
| `block` | 干净新进程（`-I`）装上 meta_path 阻断器之后的主要路径：`import tavotto.app`、契约层的 probe / 文字 / 合成（PDF + PNG + TIFF）/ 预览 / 原图三格式 / 标注写回 / 字体清单 / 像素比较、`doctor --json`、MCP bridge | 每条路径跑通，且 `sys.modules` 里没有被阻断的名字；阻断器先在一个标准库模块上证明自己会咬（自检） |
| `native` | 产物（`--dist` PyInstaller 目录 / `--wheel`）里的 `.so / .dylib / .pyd / .dll` 与 wheel 的 METADATA | 没有 mupdf / fitz；PDFium 与 qpdf 的库**在**（正例：新闭包真的进了产物）；批准字体 13 张脸随包 |
| `sbom` | `--sbom` SPDX JSON 的 packages | 没有 pymupdf / mupdf。没给就记 `not_run`（不是绿） |
| `run` | `--smoke`：`scripts/smoke_app.py --python` 全路径，父进程经 `sitecustomize` 带阻断器 | 冒烟退出 0，且 marker 文件证明阻断器在父进程里装上了；worker 子进程不带（`child_env` 摘 PYTHONPATH——那是用户的科学环境，不是主语） |

例外按**类别**写明（不是藏在路径 glob 里）：`tests/**` 是测试的独立读取器；`scripts/dev/**` 是隔离的历史差分工具；
`scripts/build_brand_assets.py` 那几份是维护者资产脚本（产出进 git 的位图，不随发行物执行）；用户科学脚本自己的
`import fitz` 在 worker 侧、用户环境里（`--selftest` 里有它的正例：不被判成残留）；git 历史与文档字样 AST / METADATA
都看不到。`--selftest` 跑正负例：往源码集合的副本里注一条 `import fitz` 必须红、fixture 用户脚本不红、阻断器在
标准库上咬、伪造带 pymupdf 的 METADATA 必须红。

    python scripts/ci/retirement_scan.py --python <venv>/bin/python [--dist dist/Tavotto] [--wheel dist/*.whl]
                                         [--sbom out/tavotto-sbom.spdx.json] [--smoke] [--out report.json]
    python scripts/ci/retirement_scan.py --selftest

退出码：0 = 跑过的每把尺子都过；1 = 任一红；2 = 用法错。`not_run` 的尺子在报告里单列，不算过。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "foundation" / "pdf_png_assets"

#: 退役的名字（顶层模块名 / 发行版名，小写比较）。`fitz` 是 PyMuPDF 的旧 import 名。
RETIRED_MODULES = ("pymupdf", "fitz", "pymupdf_fonts", "pymupdfb")
RETIRED_DISTS = ("pymupdf", "pymupdfb", "pymupdf-fonts")
#: 产物里不许出现的原生库名片段 / 必须出现的（新闭包的正例）。
NATIVE_FORBIDDEN = ("mupdf", "fitz")
NATIVE_REQUIRED = ("pdfium", "qpdf")
NATIVE_SUFFIXES = (".so", ".dylib", ".pyd", ".dll")

#: 应用源码集合：发行物 / 运行时里会**执行**的 Python（相对仓库根）。目录递归、文件逐个。
SOURCE_ROOTS = (
    "src/tavotto",
    "codex-plugin/mcp",
    "codex-plugin/skills",
    "packaging/entry.py",
    "packaging/tavotto.spec",
    "scripts/build_desktop.py",
    "scripts/build_worker_runtime.py",
    "scripts/build_frontend.py",
    "scripts/fetch_fonts.py",
    "scripts/smoke_app.py",
    "scripts/smoke_desktop.py",
)
#: 有意**不在**集合里的类别（写在这里是为了让读的人知道它们被想过，不是漏掉）。
OUT_OF_SCOPE = {
    "tests/**": "测试的独立读取器（D15）：读产物、造夹具；不进任何发行闭包",
    "scripts/dev/**": "隔离的历史差分工具（06 §3）：需要旧后端时只经可选 extra legacy-pymupdf",
    "scripts/build_brand_assets.py / build_dmg_background.py / build_installer_assets.py / recover_frac_positions.py / bench_render.py / scripts/ci/compat_matrix.py": "维护者 / 实验室脚本：产出进 git 的位图或只读产物；不随发行物执行（U00 ledger bypasses 已登记）",
    "用户科学脚本": "worker 侧、用户自己的环境；`import fitz` 是用户的事，不是应用残留",
    "git 历史 / 文档 / 注释": "AST 与 METADATA 都看不到；按 06 §3 保留、分类清楚",
}


# ---------------------------------------------------------------------------
# source：AST
# ---------------------------------------------------------------------------
def _imports_in(tree: ast.AST) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0].lower() in RETIRED_MODULES:
                    hits.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            if (
                node.module
                and node.level == 0
                and node.module.split(".")[0].lower() in RETIRED_MODULES
            ):
                hits.append((node.lineno, f"from {node.module} import …"))
        elif isinstance(node, ast.Call):
            fn = node.func
            name = None
            if isinstance(fn, ast.Attribute) and fn.attr == "import_module":
                name = "importlib.import_module"
            elif isinstance(fn, ast.Name) and fn.id == "__import__":
                name = "__import__"
            if name and node.args and isinstance(node.args[0], ast.Constant):
                val = node.args[0].value
                if isinstance(val, str) and val.split(".")[0].lower() in RETIRED_MODULES:
                    hits.append((node.lineno, f"{name}({val!r})"))
    return hits


def scan_source(root: Path = ROOT, roots: tuple[str, ...] = SOURCE_ROOTS) -> dict:
    files: list[Path] = []
    for rel in roots:
        p = root / rel
        if p.is_dir():
            files += sorted(q for q in p.rglob("*.py") if "__pycache__" not in q.parts)
        elif p.is_file():
            files.append(p)
    hits: list[dict] = []
    parsed = 0
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except SyntaxError as exc:  # .spec 也是 Python；解析不了就是它自己坏了
            hits.append(
                {
                    "file": str(f.relative_to(root)),
                    "line": exc.lineno,
                    "what": f"SyntaxError: {exc.msg}",
                }
            )
            continue
        parsed += 1
        for line, what in _imports_in(tree):
            hits.append({"file": str(f.relative_to(root)), "line": line, "what": what})
    return {"ok": not hits, "files": parsed, "hits": hits}


# ---------------------------------------------------------------------------
# deps：发行版声明的运行时闭包
# ---------------------------------------------------------------------------
_DEPS_PROBE = r"""
import json, sys
from importlib import metadata
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

def requires(dist_name):
    try:
        d = metadata.distribution(dist_name)
    except metadata.PackageNotFoundError:
        return None, None
    return d.version, list(d.requires or [])

closure, missing, edges = {}, [], []
todo = ["tavotto"]
while todo:
    name = canonicalize_name(todo.pop())
    if name in closure:
        continue
    ver, reqs = requires(name)
    if reqs is None:
        missing.append(name)
        continue
    closure[name] = ver
    for spec in reqs:
        req = Requirement(spec)
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue  # 带 extra 的、或 marker 在本平台不成立的：不在运行时闭包里
        dep = canonicalize_name(req.name)
        edges.append([name, dep])
        todo.append(dep)
print(json.dumps({"closure": closure, "missing": missing, "edges": edges}))
"""


def scan_deps(python: Path) -> dict:
    proc = subprocess.run(
        [str(python), "-I", "-c", _DEPS_PROBE],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout).strip()[-800:]}
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    retired = sorted(n for n in data["closure"] if n in RETIRED_DISTS)
    return {
        "ok": not retired and not data["missing"],
        "closure": data["closure"],
        "retired_in_closure": retired,
        "missing": data["missing"],
        "edges": data["edges"],
    }


# ---------------------------------------------------------------------------
# block：干净新进程里的阻断器
# ---------------------------------------------------------------------------
BLOCKER_SOURCE = r'''
import importlib.abc, sys
_BLOCKED = %(blocked)r
class _RetirementBlocker(importlib.abc.MetaPathFinder):
    """import pymupdf / fitz 当场 ImportError——不是「找不到」，是「被拒绝」（消息里带 ADR 号）。"""
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0].lower() in _BLOCKED:
            raise ImportError("retirement_scan: import %%r 被阻断——PyMuPDF 已退役（ADR 0072）" %% name)
        return None
sys.meta_path.insert(0, _RetirementBlocker())
'''


def blocker_code(blocked: tuple[str, ...] = RETIRED_MODULES) -> str:
    return BLOCKER_SOURCE % {"blocked": tuple(blocked)}


_BLOCK_PATHS = r"""
import json, os, sys, tempfile, traceback
from pathlib import Path
FIXTURE = Path(%(fixture)r)
MCP = Path(%(mcp)r)
results = []
def step(name, fn):
    try:
        info = fn()
        results.append({"step": name, "ok": True, "info": info})
    except Exception as exc:
        results.append({"step": name, "ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:]})
tmp = Path(tempfile.mkdtemp(prefix="retire-"))

def s_app():
    import tavotto.app as app
    return {"routes": len(list(app.app.url_map.iter_rules()))}
step("import tavotto.app", s_app)

from tavotto import pdfbackend
step("pdfbackend.selected", lambda: {"selected": pdfbackend.selected(), "name": pdfbackend.BACKEND_NAME, "version": pdfbackend.BACKEND_VERSION})
step("probe_asset pdf", lambda: pdfbackend.probe_asset(FIXTURE / "page.pdf", "pdf"))
step("probe_asset raster", lambda: pdfbackend.probe_asset(FIXTURE / "original.png", "raster"))
step("text_width / text_plan / missing_glyphs", lambda: {
    "width": pdfbackend.text_width("Hello 你好 m⁻²", 10.0),
    "plan": pdfbackend.text_plan("Hello 你好"),
    "missing": pdfbackend.missing_glyphs("Hello 𝔸"),
})
step("coverage_ranges", lambda: {layer: len(v) for layer, v in pdfbackend.coverage_ranges().items()})

def s_compose():
    canvas = pdfbackend.compose(120, 60)
    canvas.place({"type": "panel", "id": "figs/p.pdf", "x_mm": 5, "y_mm": 5, "w_mm": 67.5, "h_mm": 40}, 150, lambda o, d: FIXTURE / "page.pdf")
    canvas.place({"type": "text", "id": "t", "text": "Hello 图", "x_mm": 80, "y_mm": 10, "w_mm": 30, "h_mm": 10, "size_pt": 10}, 150, lambda o, d: None)
    canvas.save_pdf(tmp / "c.pdf"); canvas.save_png(tmp / "c.png", 150); facts = canvas.save_tiff(tmp / "c.tiff", 150); canvas.close()
    pdf = (tmp / "c.pdf").read_bytes(); png = (tmp / "c.png").read_bytes()
    assert pdf.startswith(b"%%PDF") and png[:8] == b"\x89PNG\r\n\x1a\n", "产物签名不对"
    return {"pdf_bytes": len(pdf), "png_bytes": len(png), "tiff": facts, "size_pt": canvas.size_pt}
step("compose → save_pdf / save_png / save_tiff", s_compose)
step("pdf_fonts", lambda: {"fonts": pdfbackend.pdf_fonts(tmp / "c.pdf")})
step("compare_png", lambda: pdfbackend.compare_png(tmp / "c.png", tmp / "c.png"))

def s_preview():
    pdfbackend.render_preview_png(FIXTURE / "page.pdf", 200, tmp / "prev.png")
    return {"bytes": (tmp / "prev.png").stat().st_size}
step("render_preview_png", s_preview)
step("original_pdf", lambda: pdfbackend.original_pdf(FIXTURE / "page.pdf", tmp / "o.pdf"))
step("original_png", lambda: pdfbackend.original_png(FIXTURE / "original.png", tmp / "o.png", 300))
step("original_tiff", lambda: pdfbackend.original_tiff(FIXTURE / "original.png", tmp / "o.tiff", 300, dpi_meta=None))

def s_annotate():
    import shutil
    shutil.copy(FIXTURE / "page.pdf", tmp / "ann.pdf")
    pdfbackend.annotate_asset(tmp / "ann.pdf", tmp / "ann.png", [
        {"type": "text", "id": "a", "text": "note", "x_mm": 5, "y_mm": 5, "w_mm": 30, "h_mm": 8, "size_pt": 9}])
    return {"png": (tmp / "ann.png").stat().st_size, "pdf": (tmp / "ann.pdf").stat().st_size}
step("annotate_asset", s_annotate)

def s_doctor():
    import io, contextlib
    from tavotto.engine import cli
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.dispatch(["doctor", "--json"])
    line = buf.getvalue().strip().splitlines()[-1]
    json.loads(line)
    return {"rc": rc}
step("engine.cli doctor --json", s_doctor)

def s_mcp():
    sys.path.insert(0, str(MCP))
    import tavotto_mcp.bridge, tavotto_mcp.server  # noqa: F401
    return {"bridge": tavotto_mcp.bridge.__name__}
step("MCP bridge / server import", s_mcp)

def s_child():
    from tavotto.rendercore import renderhost
    renderhost.shutdown_shared()
    return {}
step("render child reaped", s_child)

leaked = sorted(m for m in sys.modules if m.split(".")[0].lower() in %(blocked)r)
print(json.dumps({"steps": results, "leaked_modules": leaked, "all_ok": all(r["ok"] for r in results) and not leaked}))
"""


def _clean_env(tmp: Path) -> dict:
    # 后端开关与字体目录放行：切换前用它验「候选在阻断器下跑得通」；切换后默认就是 rendercore，不设即可
    keep = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LANG", "LC_ALL")
    keep += ("TAVOTTO_RENDER_BACKEND", "TAVOTTO_FONTS_DIR")
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update(
        {
            "TAVOTTO_NO_TELEMETRY": "1",
            "TAVOTTO_NO_UPDATE_CHECK": "1",
            "TAVOTTO_DATA_DIR": str(tmp / "data"),
            "TAVOTTO_CONFIG_DIR": str(tmp / "config"),
            "PYTHONUTF8": "1",
        }
    )
    return env


def blocker_selftest(python: Path) -> dict:
    """阻断器要在一个**装了**的模块上证明会咬——`import pymupdf` 在没装 pymupdf 的机器上本来就会 ImportError，
    那不是阻断器的功劳。拿标准库 `colorsys` 当靶子。"""
    code = blocker_code(("colorsys",)) + (
        "\nimport json\n"
        "try:\n    import colorsys\n    print(json.dumps({'bit': False}))\n"
        "except ImportError as exc:\n    print(json.dumps({'bit': True, 'message': str(exc)}))\n"
    )
    proc = subprocess.run(
        [str(python), "-I", "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": (proc.stderr or proc.stdout)[-500:]}
    ok = data.get("bit") is True and "ADR 0072" in data.get("message", "")
    return {"ok": ok, **data}


def scan_block(python: Path) -> dict:
    self_test = blocker_selftest(python)
    if not self_test["ok"]:
        return {"ok": False, "selftest": self_test, "error": "阻断器自检没咬：后面的绿不算数"}
    code = blocker_code() + _BLOCK_PATHS % {
        "fixture": str(FIXTURE),
        "mcp": str(ROOT / "codex-plugin" / "mcp"),
        "blocked": tuple(RETIRED_MODULES),
    }
    with tempfile.TemporaryDirectory(prefix="retire-block-") as tmp:
        proc = subprocess.run(
            [str(python), "-I", "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_clean_env(Path(tmp)),
            timeout=600,
            cwd=str(ROOT),
        )
    lines = proc.stdout.strip().splitlines()
    try:
        data = json.loads(lines[-1]) if lines else {}
    except ValueError:
        data = {}
    if proc.returncode != 0 or not data:
        return {
            "ok": False,
            "selftest": self_test,
            "error": (proc.stderr or proc.stdout).strip()[-1500:],
        }
    return {"ok": bool(data.get("all_ok")), "selftest": self_test, **data}


# ---------------------------------------------------------------------------
# native：产物目录 / wheel
# ---------------------------------------------------------------------------
def _fonts_expected() -> int:
    allow = json.loads(
        (ROOT / "src" / "tavotto" / "rendercore" / "fonts_allowlist.json").read_text(
            encoding="utf-8"
        )
    )
    return len(allow["faces"])


def scan_dist(dist: Path) -> dict:
    natives = sorted(
        str(p.relative_to(dist))
        for p in dist.rglob("*")
        if p.is_file() and p.suffix.lower() in NATIVE_SUFFIXES
    )
    forbidden = [n for n in natives if any(tok in Path(n).name.lower() for tok in NATIVE_FORBIDDEN)]
    required_missing = [
        tok for tok in NATIVE_REQUIRED if not any(tok in n.lower() for n in natives)
    ]
    fonts = [
        p
        for p in dist.rglob("*")
        if p.is_file() and p.suffix.lower() in (".ttf", ".otf") and "fonts" in p.parts
    ]
    expected = _fonts_expected()
    return {
        "ok": not forbidden and not required_missing and len(fonts) == expected,
        "native_files": len(natives),
        "forbidden": forbidden,
        "required_missing": required_missing,
        "fonts_found": len(fonts),
        "fonts_expected": expected,
        "sample": [n for n in natives if any(tok in n.lower() for tok in NATIVE_REQUIRED)][:8],
    }


def _requires_dist(metadata_text: str) -> list[str]:
    out = []
    for line in metadata_text.splitlines():
        if line.lower().startswith("requires-dist:"):
            out.append(line.split(":", 1)[1].strip())
    return out


def _req_name(requirement: str) -> str:
    """PEP 508 字符串的规范化发行版名（小写，`_` / `.` 折成 `-`）。"""
    m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    return re.sub(r"[-_.]+", "-", m.group(1)).lower() if m else ""


def scan_wheel(wheel: Path) -> dict:
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        meta = next((n for n in names if n.endswith(".dist-info/METADATA")), None)
        text = zf.read(meta).decode("utf-8") if meta else ""
    reqs = _requires_dist(text)
    runtime = [r for r in reqs if "extra ==" not in r]
    retired = [r for r in runtime if _req_name(r) in RETIRED_DISTS]
    natives = [n for n in names if n.lower().endswith(NATIVE_SUFFIXES)]
    fonts = [
        n
        for n in names
        if n.startswith("tavotto/resources/fonts/") and n.lower().endswith((".ttf", ".otf"))
    ]
    expected = _fonts_expected()
    coverage = "tavotto/pdfbackend/canvas_coverage.json" in names
    return {
        "ok": not retired and not natives and len(fonts) == expected and coverage,
        "requires_dist_runtime": runtime,
        "retired": retired,
        "native_files": natives,
        "fonts_found": len(fonts),
        "fonts_expected": expected,
        "canvas_coverage": coverage,
    }


# ---------------------------------------------------------------------------
# sbom
# ---------------------------------------------------------------------------
def scan_sbom(sbom: Path) -> dict:
    data = json.loads(sbom.read_text(encoding="utf-8"))
    pkgs = data.get("packages") or data.get("components") or []
    names = sorted({str(p.get("name", "")).lower() for p in pkgs})
    hits = [n for n in names if any(tok in n for tok in ("pymupdf", "mupdf", "fitz"))]
    return {"ok": not hits and bool(names), "packages": len(names), "hits": hits}


# ---------------------------------------------------------------------------
# run：冒烟全路径，父进程带阻断器
# ---------------------------------------------------------------------------
def scan_smoke(python: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="retire-smoke-") as tmp:
        tmpp = Path(tmp)
        site = tmpp / "site"
        site.mkdir()
        marker = tmpp / "blocker-active"
        (site / "sitecustomize.py").write_text(
            blocker_code()
            + f"\nimport pathlib\npathlib.Path({str(marker)!r}).write_text('active', encoding='utf-8')\n",
            encoding="utf-8",
        )
        env = {**os.environ, "PYTHONPATH": str(site), "TAVOTTO_NO_TELEMETRY": "1"}
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "smoke_app.py"),
                "--python",
                str(python),
                "--workdir",
                str(tmpp / "work"),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=1800,
            cwd=str(ROOT),
        )
        active = marker.is_file()
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-20:]
    return {
        "ok": proc.returncode == 0 and active,
        "returncode": proc.returncode,
        "blocker_active_in_parent": active,
        "tail": tail,
    }


# ---------------------------------------------------------------------------
# selftest：正负例
# ---------------------------------------------------------------------------
def selftest(python: Path) -> dict:
    checks: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="retire-selftest-") as tmp:
        root = Path(tmp)
        # 负例 1：源码集合的副本里注一条 import fitz → 必须红（落点：src/tavotto/engine/injected.py）
        (root / "src" / "tavotto" / "engine").mkdir(parents=True)
        (root / "src" / "tavotto" / "engine" / "injected.py").write_text(
            "import fitz\n", encoding="utf-8"
        )
        r = scan_source(root, ("src/tavotto",))
        checks.append(
            {
                "check": "injected `import fitz` in app source is flagged",
                "ok": (not r["ok"]) and r["hits"][0]["what"] == "import fitz",
            }
        )
        # 负例 2：动态 import 的字面量也要抓
        (root / "src" / "tavotto" / "engine" / "injected.py").write_text(
            "import importlib\nm = importlib.import_module('pymupdf')\n", encoding="utf-8"
        )
        r = scan_source(root, ("src/tavotto",))
        checks.append({"check": "importlib.import_module('pymupdf') is flagged", "ok": not r["ok"]})
        # 正例 1：用户科学脚本（fixture 目录）里的 import fitz 不在主语里 → 不红
        (root / "tests" / "fixtures" / "user").mkdir(parents=True)
        (root / "tests" / "fixtures" / "user" / "figure.py").write_text(
            "import fitz\nimport matplotlib\n", encoding="utf-8"
        )
        (root / "src" / "tavotto" / "engine" / "injected.py").write_text(
            "NAMES = {'fitz': 'PyMuPDF'}  # 显示名映射\n# import pymupdf 只是注释\n",
            encoding="utf-8",
        )
        r = scan_source(root, ("src/tavotto",))
        checks.append(
            {
                "check": "user script under tests/ and display-name strings / comments are not flagged",
                "ok": r["ok"] and r["files"] == 1,
            }
        )
        # 负例 3：伪造带 pymupdf 的 wheel METADATA → 必须红
        whl = root / "fake-0.0.0-py3-none-any.whl"
        with zipfile.ZipFile(whl, "w") as zf:
            zf.writestr(
                "fake-0.0.0.dist-info/METADATA",
                "Name: fake\nRequires-Dist: flask>=3\nRequires-Dist: pymupdf>=1.24,<2\nRequires-Dist: pytest; extra == 'dev'\n",
            )
        r = scan_wheel(whl)
        checks.append(
            {
                "check": "wheel METADATA with pymupdf in runtime Requires-Dist is flagged",
                "ok": (not r["ok"]) and r["retired"] == ["pymupdf>=1.24,<2"],
            }
        )
        # 负例 4：产物目录里有 libmupdf → 必须红；正例：有 pdfium + qpdf + 13 张脸 → 绿
        dist = root / "dist"
        (dist / "_internal" / "tavotto" / "resources" / "fonts").mkdir(parents=True)
        for i in range(_fonts_expected()):
            (dist / "_internal" / "tavotto" / "resources" / "fonts" / f"f{i}.ttf").write_bytes(b"x")
        (dist / "_internal" / "libpdfium.dylib").write_bytes(b"x")
        (dist / "_internal" / "libqpdf.30.dylib").write_bytes(b"x")
        r_good = scan_dist(dist)
        (dist / "_internal" / "libmupdf.so").write_bytes(b"x")
        r_bad = scan_dist(dist)
        checks.append(
            {
                "check": "dist with pdfium+qpdf+fonts passes; adding libmupdf fails",
                "ok": r_good["ok"]
                and not r_bad["ok"]
                and r_bad["forbidden"] == ["_internal/libmupdf.so"],
            }
        )
        # 负例 5：SBOM 里有 PyMuPDF → 红
        sb = root / "sbom.json"
        sb.write_text(
            json.dumps({"packages": [{"name": "flask"}, {"name": "PyMuPDF"}]}), encoding="utf-8"
        )
        checks.append({"check": "SBOM naming PyMuPDF is flagged", "ok": not scan_sbom(sb)["ok"]})
    # 阻断器自检：在标准库模块上咬
    bt = blocker_selftest(python)
    checks.append(
        {
            "check": "blocker bites on an installed (stdlib) module with the ADR message",
            "ok": bt["ok"],
            "detail": bt,
        }
    )
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="装了 tavotto 的解释器（deps / block / run 的主语）",
    )
    ap.add_argument("--dist", type=Path, help="PyInstaller onedir 产物目录（native 尺子）")
    ap.add_argument(
        "--wheel", type=Path, nargs="*", default=[], help="wheel 文件（native + METADATA 尺子）"
    )
    ap.add_argument("--sbom", type=Path, help="SPDX / CycloneDX JSON（sbom 尺子）")
    ap.add_argument(
        "--smoke", action="store_true", help="跑 scripts/smoke_app.py 全路径（父进程带阻断器）"
    )
    ap.add_argument(
        "--skip",
        default="",
        help="逗号分隔：跳过的尺子（source,deps,block）——跳过记 not_run，不是绿",
    )
    ap.add_argument("--out", type=Path, help="报告 JSON")
    ap.add_argument("--selftest", action="store_true", help="只跑正负例")
    args = ap.parse_args(argv)

    report: dict = {
        "schema": 1,
        "kind": "u10_retirement_scan",
        "python": str(args.python),
        "out_of_scope": OUT_OF_SCOPE,
        "rulers": {},
    }
    if args.selftest:
        report["selftest"] = selftest(args.python)
        for c in report["selftest"]["checks"]:
            print(("PASS " if c["ok"] else "FAIL ") + c["check"])
        _write(args.out, report)
        return 0 if report["selftest"]["ok"] else 1

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    rulers = report["rulers"]
    rulers["source"] = (
        scan_source() if "source" not in skip else {"status": "not_run", "why": "--skip"}
    )
    rulers["deps"] = (
        scan_deps(args.python) if "deps" not in skip else {"status": "not_run", "why": "--skip"}
    )
    rulers["block"] = (
        scan_block(args.python) if "block" not in skip else {"status": "not_run", "why": "--skip"}
    )
    if args.dist:
        rulers["native_dist"] = scan_dist(args.dist)
    for whl in args.wheel:
        rulers[f"wheel:{whl.name}"] = scan_wheel(whl)
    if not args.dist and not args.wheel:
        rulers["native"] = {
            "status": "not_run",
            "why": "没给 --dist / --wheel（产物在别的 job 里）",
        }
    rulers["sbom"] = (
        scan_sbom(args.sbom)
        if args.sbom
        else {"status": "not_run", "why": "没给 --sbom（只有发布链产 SBOM）"}
    )
    rulers["run"] = (
        scan_smoke(args.python) if args.smoke else {"status": "not_run", "why": "没给 --smoke"}
    )

    ran = {k: v for k, v in rulers.items() if v.get("status") != "not_run"}
    report["ran"] = sorted(ran)
    report["not_run"] = sorted(k for k in rulers if k not in ran)
    report["all_ok"] = bool(ran) and all(v.get("ok") for v in ran.values())
    for k, v in rulers.items():
        if v.get("status") == "not_run":
            print(f"SKIP {k}: {v['why']}（not_run，不是绿）")
        else:
            print(
                ("PASS " if v.get("ok") else "FAIL ")
                + k
                + (
                    ""
                    if v.get("ok")
                    else f": {json.dumps({kk: vv for kk, vv in v.items() if kk in ('hits', 'retired_in_closure', 'missing', 'error', 'forbidden', 'required_missing', 'retired', 'leaked_modules', 'tail', 'fonts_found')}, ensure_ascii=False)[:1200]}"
                )
            )
    if "block" in ran and ran["block"].get("steps"):
        for s in ran["block"]["steps"]:
            print(
                ("  ok   " if s["ok"] else "  FAIL ")
                + s["step"]
                + ("" if s["ok"] else f" — {s.get('error')}")
            )
    _write(args.out, report)
    print("ALL OK" if report["all_ok"] else "FAILED")
    return 0 if report["all_ok"] else 1


def _write(out: Path | None, report: dict) -> None:
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
