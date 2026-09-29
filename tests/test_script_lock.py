"""改用户脚本的锁与唯一写入口（ADR 0110 §五，`engine/scriptlock.py`）。

三道门：写的那一刻必须持锁（`write_script` 自己判）；`script_guard` 的持有者清单与源码对账（从 AST
里枚举出来，不是手登记）；除 `scriptlock` 之外没有代码直接往名字里带 script 的目标写字节。
"""

from __future__ import annotations

import ast
import re
import threading
from pathlib import Path

import pytest

from tavotto.engine import scriptlock

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "tavotto"


def test_write_script_refuses_without_the_guard_and_the_guard_is_reentrant(tmp_path):
    script = tmp_path / "fig.py"
    script.write_bytes(b"A = 1\n")
    with pytest.raises(RuntimeError):
        scriptlock.write_script(script, b"A = 2\n")
    assert script.read_bytes() == b"A = 1\n"
    with scriptlock.script_guard(script):
        with scriptlock.script_guard(tmp_path / "." / "fig.py"):  # 同一真实路径、同一线程：可重入
            scriptlock.write_script(script, b"A = 2\n")
        assert scriptlock.held(script)
    assert not scriptlock.held(script)
    assert script.read_bytes() == b"A = 2\n"


def test_another_thread_holding_the_guard_does_not_count(tmp_path):
    script = tmp_path / "fig.py"
    script.write_bytes(b"A = 1\n")
    holding, release = threading.Event(), threading.Event()

    def hold():
        with scriptlock.script_guard(script):
            holding.set()
            release.wait(10)

    t = threading.Thread(target=hold, daemon=True)
    t.start()
    assert holding.wait(10)
    try:
        assert not scriptlock.held(script)
        with pytest.raises(RuntimeError):
            scriptlock.write_script(script, b"A = 2\n")
    finally:
        release.set()
        t.join(10)


def _functions_calling(name: str) -> set[str]:
    """源码里调用 `<任何>.name(...)` / `name(...)` 的函数 → `模块.函数`（模块取 engine 下的短名 / app）。"""
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        if path.name == "scriptlock.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        mod = path.stem
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    f = node.func
                    called = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                    if called == name:
                        found.add(f"{mod}.{fn.name}")
    return found


def _documented_holders() -> set[str]:
    doc = ast.get_docstring(ast.parse((SRC / "engine" / "scriptlock.py").read_text("utf-8")))
    return set(re.findall(r"^\* `([\w.]+)`", doc or "", flags=re.M))


def test_the_guard_holders_are_exactly_the_documented_list():
    """持有者清单写在 `scriptlock` 的模块说明里；新增一处拿锁（= 一个新的写脚本入口）不更新清单就红，
    清单里的某个入口不再拿锁也红。"""
    documented = _documented_holders()
    assert len(documented) >= 5, "清单解析成空集合：判据量在空集上"
    assert _functions_calling("script_guard") == documented


def test_script_bytes_are_written_only_through_write_script():
    """除 `scriptlock` 之外，写字节的调用（`atomicio.write_bytes` / `publish_file` / `os.replace` /
    `shutil.copy*` / `.write_bytes` / `.write_text`）的**目标**名字里不许出现 script——用户脚本只经
    `write_script`（它自己判锁）。快照是从脚本**读**的（目标是快照），不算。"""
    writers = {
        "write_bytes": 0,
        "write_text": None,
        "publish_file": 1,
        "replace": 1,
        "copy": 1,
        "copy2": 1,
        "copyfile": 1,
    }
    offenders = []
    examined = 0
    for path in SRC.rglob("*.py"):
        if path.name in ("scriptlock.py", "atomicio.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            attr = node.func.attr
            if attr not in writers:
                continue
            owner = ast.unparse(node.func.value)
            if attr in ("replace",) and owner != "os":
                continue
            idx = writers[attr]
            if idx is None or (attr == "write_bytes" and owner != "atomicio"):
                target = owner  # `Path(...).write_bytes(...)`：接收者就是目标
            else:
                target = ast.unparse(node.args[idx]) if len(node.args) > idx else ""
            examined += 1
            if "script" in target.lower():
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {ast.unparse(node)[:80]}")
    assert examined >= 30, "几乎没扫到写字节的调用：判据量在空集上"
    assert offenders == []
