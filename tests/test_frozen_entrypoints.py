"""冻结产物的入口不许依赖包里的 `__main__` 子模块（TVD-BCZ0）。

PyInstaller 不收 `pkg/__main__.py`：`from .agent_runner import __main__` 在源码里能跑，
在安装包里当场 `ImportError: cannot import name '__main__'`。0.19.0–0.19.2 的安装包里
「用 Key 连接」的内置执行器因此一次都没起来——每次约 0.1 秒失败，测试与源码冒烟一直绿。

判据的主语：`src/tavotto/` 下**产品代码**的 import 语句（AST，不是子串）。`__main__.py`
自己只许是薄壳（`python -m` 的入口），实现放在可被冻结的普通模块里。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "tavotto"


def _main_imports(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "__main__" or mod.endswith(".__main__"):
                lines.append(node.lineno)
            elif any(alias.name == "__main__" for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.Import):
            if any(
                alias.name == "__main__" or alias.name.endswith(".__main__") for alias in node.names
            ):
                lines.append(node.lineno)
    return lines


def test_product_code_never_imports_a_dunder_main_submodule():
    bad = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line in _main_imports(tree):
            bad.append(f"{path.relative_to(ROOT).as_posix()}:{line}")
    assert not bad, (
        "这些 import 依赖包里的 `__main__` 子模块；PyInstaller 冻结产物不收它，安装包里会 ImportError："
        + ", ".join(bad)
    )


def test_detector_sees_the_shapes_that_broke_the_installer():
    for src in (
        "from .agent_runner import __main__ as m",
        "from tavotto.engine.agent_runner.__main__ import main",
        "import tavotto.engine.agent_runner.__main__",
    ):
        assert _main_imports(ast.parse(src)), src
    assert not _main_imports(ast.parse("from .agent_runner import runner_cli"))


def test_cli_agent_run_dispatches_to_the_freezable_module():
    """`tavotto-cli agent-run --help` 经 cli.dispatch 真的走到 runner_cli 的 argparse（退出码 0）。"""
    import contextlib
    import io

    import pytest

    from tavotto.engine import cli

    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit) as exc:
        cli.dispatch(["agent-run", "--help"])
    assert exc.value.code == 0
    assert "--wire" in out.getvalue()
