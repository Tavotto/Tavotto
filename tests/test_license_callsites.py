"""看护 ADR 0193 §一「付费只拦新功能」：全仓库对 `license.has(` 的调用点，只许出现在登记表里的模块。

登记表 `REGISTERED_CALLERS` 现在有一键排版（`quick-layout`，ADR 0199）与批注重改（`change-plan-rerun`，ADR 0206 §五）与机理图（`mechanism-diagram`，ADR 0220）；`hosted-ai` 内置 AI 额度、`review-plus` 外部审阅加量
各自的 ADR / PR 才会加）。**加调用点要同步改 ADR 0193 §一**（登记新功能键与它的模块），并把模块写进下面的登记表；
凡随任何一个正式版免费发布过的能力，代码里都不许出现权益判断。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
#: 登记过的、允许调用 `license.has(...)` 的模块（相对仓库根的 posix 路径）。空 = 现在零调用点。
REGISTERED_CALLERS: frozenset[str] = frozenset(
    {
        "src/tavotto/engine/quicklayout.py",
        "src/tavotto/engine/proinstall.py",
        "src/tavotto/engine/changeplan.py",
        "src/tavotto/engine/mechanism.py",
    }
)
LICENSE_MODULE = "src/tavotto/engine/license.py"
SKIP_DIRS = {".venv", "node_modules", ".git", "target", "target-wincheck", ".pnpm-store", "dist"}


def _python_files():
    for base in (SRC, ROOT / "tests", ROOT / "scripts"):
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if not SKIP_DIRS.intersection(p.relative_to(ROOT).parts):
                yield p


def _license_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """文件里指向授权模块的名字：`(模块别名集合, 直接 import 的 has 别名集合)`。"""
    modules: set[str] = set()
    funcs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for a in node.names:
                if a.name == "license" and (
                    mod.endswith("engine") or mod == "" or mod == "tavotto.engine"
                ):
                    modules.add(a.asname or a.name)
                if a.name == "has" and mod.split(".")[-1] == "license":
                    funcs.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.endswith("engine.license"):
                    modules.add(a.asname or a.name.split(".")[0])
    return modules, funcs


def call_sites(source: str, filename: str = "<src>") -> list[int]:
    """源码里对授权 `has` 的调用行号（`license.has(…)` / `engine_license.has(…)` / `from … import has`）。"""
    tree = ast.parse(source, filename)
    modules, funcs = _license_names(tree)
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "has":
            base = f.value
            dotted = base.id if isinstance(base, ast.Name) else ast.unparse(base)
            if (
                dotted in modules
                or dotted.endswith("engine.license")
                or dotted.split(".")[-1]
                in (
                    "license",
                    "engine_license",
                )
            ):
                lines.append(node.lineno)
        elif isinstance(f, ast.Name) and f.id in funcs:
            lines.append(node.lineno)
    return lines


def test_has_is_called_only_from_registered_modules():
    offenders = []
    for p in _python_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel == LICENSE_MODULE or rel.startswith("tests/test_license"):
            continue  # 授权模块自己与它的测试
        lines = call_sites(p.read_text(encoding="utf-8"), rel)
        if lines and rel not in REGISTERED_CALLERS:
            offenders.append(f"{rel}:{lines}")
    assert not offenders, (
        "license.has(…) 只许新功能模块调用，且模块要登记在 REGISTERED_CALLERS 并同步 ADR 0193 §一"
        f"（付费只拦新功能）：{offenders}"
    )


def test_registry_lists_only_registered_new_features():
    # 登记表与 ADR 0193 §一 的新功能一一对应：一键排版（ADR 0199）、批注重改（ADR 0206 §五）。加调用点要同时改这里与 ADR
    assert REGISTERED_CALLERS == frozenset(
        {
            "src/tavotto/engine/quicklayout.py",
            "src/tavotto/engine/proinstall.py",
            "src/tavotto/engine/changeplan.py",
            "src/tavotto/engine/mechanism.py",
        }
    )


def test_the_scanner_actually_finds_call_sites():
    # 反证：扫描器不是因为写法认不出才「零调用点」
    for src in (
        "from tavotto.engine import license\nlicense.has('x')\n",
        "from tavotto.engine import license as engine_license\nif engine_license.has('x'): pass\n",
        "from . import license as lic\nlic.has('x')\n",
        "import tavotto.engine.license as L\nL.has('x')\n",
        "from tavotto.engine.license import has\nhas('x')\n",
        "from tavotto.engine.license import has as h\nh('x')\n",
        "import tavotto.engine.license\ntavotto.engine.license.has('x')\n",
    ):
        assert call_sites(src), src
    for src in ("d = {}\nd.has('x')\n", "from tavotto.engine import license\nlicense.status()\n"):
        assert not call_sites(src), src
    assert Path(ROOT / LICENSE_MODULE).exists()
