"""`scripts/build_frontend.py` 给 node 固定堆上限（macOS runner 默认约 2 GB，pnpm build 顶到过）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "build_frontend", Path(__file__).resolve().parents[1] / "scripts" / "build_frontend.py"
)
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)


def test_heap_limit_is_added_when_absent():
    env = bf.build_env({"PATH": "/bin"})
    assert env["NODE_OPTIONS"] == f"--max-old-space-size={bf.NODE_HEAP_MB}"
    assert env["PATH"] == "/bin"


def test_other_node_options_are_kept():
    env = bf.build_env({"NODE_OPTIONS": "--no-experimental-webstorage"})
    assert (
        env["NODE_OPTIONS"]
        == f"--no-experimental-webstorage --max-old-space-size={bf.NODE_HEAP_MB}"
    )


def test_a_caller_supplied_limit_wins():
    env = bf.build_env({"NODE_OPTIONS": "--max-old-space-size=8192"})
    assert env["NODE_OPTIONS"] == "--max-old-space-size=8192"


def test_the_limit_fits_a_ci_runner():
    assert 2048 < bf.NODE_HEAP_MB <= 5120, "高于默认约 2 GB、远低于 macOS runner 的 7 GB 内存"


def test_every_pnpm_step_gets_the_limit(monkeypatch):
    seen = []
    monkeypatch.setattr(bf.subprocess, "run", lambda cmd, **kw: seen.append(kw.get("env") or {}))
    monkeypatch.delenv("NODE_OPTIONS", raising=False)
    bf.run(["pnpm", "build"])
    assert seen and "--max-old-space-size" in seen[0].get("NODE_OPTIONS", "")
