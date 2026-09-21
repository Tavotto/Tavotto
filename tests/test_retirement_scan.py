"""零旧库退役扫描（`scripts/ci/retirement_scan.py`，统一实施包 U10，ADR 0072）。

两组主语：

1. **扫描器自己**（任何机器）：正负例——往应用源码副本注一条 `import fitz` 必红、fixture 里的用户脚本与
   显示名字符串 / 注释不红、伪造带 pymupdf 的 wheel METADATA 必红、产物目录里的 libmupdf 必红而
   pdfium + qpdf + 13 张脸的产物过、SBOM 点名 PyMuPDF 必红、阻断器在**装了**的标准库模块上真的咬。
   空门禁比没有门禁更坏：这些例子每条都是「扫描器要是坏了会静默绿」的形状。
2. **这个仓库 / 这个环境**：应用源码集合零 import、发行版声明的运行时闭包零 pymupdf、干净新进程装上阻断器
   之后主要路径全部跑通（候选包 + 批准字体在时才跑；不在就 skip 并写理由——skip 不是绿）。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ci" / "retirement_scan.py"
sys.path.insert(0, str(SCRIPT.parent))
import retirement_scan as rs  # noqa: E402

HAS_CANDIDATE = all(
    importlib.util.find_spec(m) is not None for m in ("pypdfium2", "pikepdf", "uharfbuzz", "PIL")
)


def _fonts_ready() -> bool:
    if not HAS_CANDIDATE:
        return False
    from tavotto.rendercore import fonts

    return not fonts.FontRegistry.discover().missing


# ---------------------------------------------------------------- 1. 扫描器自己


def test_selftest_positive_and_negative_examples_all_hold():
    report = rs.selftest(Path(sys.executable))
    failed = [c["check"] for c in report["checks"] if not c["ok"]]
    assert not failed, failed
    assert len(report["checks"]) == 7


def test_the_source_ruler_sees_dynamic_imports_and_ignores_strings(tmp_path):
    (tmp_path / "src" / "tavotto").mkdir(parents=True)
    f = tmp_path / "src" / "tavotto" / "x.py"
    f.write_text("m = __import__('fitz')\n", encoding="utf-8")
    assert rs.scan_source(tmp_path, ("src/tavotto",))["hits"][0]["what"] == "__import__('fitz')"
    f.write_text("from fitz import open\n", encoding="utf-8")
    assert not rs.scan_source(tmp_path, ("src/tavotto",))["ok"]
    f.write_text("from .fitz import open\nDOC = 'import pymupdf'\n", encoding="utf-8")
    # 相对 import（自家模块碰巧叫 fitz）与字符串都不是残留
    assert rs.scan_source(tmp_path, ("src/tavotto",))["ok"]


def test_the_wheel_ruler_ignores_extras_but_not_runtime_requires(tmp_path):
    import zipfile

    whl = tmp_path / "t-1.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as zf:
        zf.writestr(
            "t-1.0.dist-info/METADATA",
            "Name: t\nRequires-Dist: pymupdf>=1.24; extra == 'legacy-pymupdf'\nRequires-Dist: PyMuPDF (>=1.24)\n",
        )
        zf.writestr("tavotto/pdfbackend/canvas_coverage.json", "{}")
        for i in range(rs._fonts_expected()):
            zf.writestr(f"tavotto/resources/fonts/f{i}.ttf", "x")
    r = rs.scan_wheel(whl)
    assert r["retired"] == ["PyMuPDF (>=1.24)"] and not r["ok"]
    with zipfile.ZipFile(whl, "w") as zf:
        zf.writestr(
            "t-1.0.dist-info/METADATA",
            "Name: t\nRequires-Dist: pymupdf>=1.24; extra == 'legacy-pymupdf'\nRequires-Dist: pikepdf>=10\n",
        )
        zf.writestr("tavotto/pdfbackend/canvas_coverage.json", "{}")
        for i in range(rs._fonts_expected()):
            zf.writestr(f"tavotto/resources/fonts/f{i}.ttf", "x")
    assert rs.scan_wheel(whl)["ok"]


def test_the_cli_exit_code_follows_the_selftest():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--selftest", "--python", sys.executable],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("PASS ") == 7


# ---------------------------------------------------------------- 2. 这个仓库 / 这个环境


def test_the_application_source_set_has_no_retired_import():
    """主语：`SOURCE_ROOTS` 的 AST。测试目录、scripts/dev、维护者资产脚本有意不在集合里（扫描器头部逐类写明）。"""
    r = rs.scan_source()
    assert r["ok"], r["hits"]
    assert r["files"] > 100


def test_the_declared_runtime_closure_has_no_retired_distribution():
    """主语：本环境里 `tavotto` 发行版声明的运行时依赖闭包（递归、无 extra）。同一 site-packages 里装着的
    测试读取器 pymupdf 不在闭包里——它不该被数进来，也不该让这条绿掉。"""
    r = rs.scan_deps(Path(sys.executable))
    assert "error" not in r, r
    assert r["retired_in_closure"] == [], r
    assert not r["missing"], r["missing"]
    assert "flask" in r["closure"]
    for name in ("pikepdf", "pypdfium2", "uharfbuzz", "fonttools", "pillow"):
        assert name in r["closure"], (
            f"{name} 不在运行时闭包里（切换后它们是运行时依赖，不是 extra）"
        )


@pytest.mark.skipif(not HAS_CANDIDATE, reason="候选包未装（not_run，不是绿）")
def test_every_main_path_runs_in_a_clean_process_with_the_blocker_installed():
    if not _fonts_ready():
        pytest.skip("批准字体不全（not_run）：先跑 scripts/fetch_fonts.py")
    r = rs.scan_block(Path(sys.executable))
    assert r["selftest"]["ok"]
    failed = [s for s in r.get("steps", []) if not s["ok"]]
    assert not failed, json.dumps(failed, ensure_ascii=False, indent=1)
    assert r["leaked_modules"] == []
    names = [s["step"] for s in r["steps"]]
    for must in (
        "compose → save_pdf / save_png / save_tiff",
        "annotate_asset",
        "MCP bridge / server import",
    ):
        assert must in names
