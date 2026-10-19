"""worker 自报 build 实际执行的源码 sha256（TAF 改图验证的判据，ADR 0138 Phase 3b）：真 worker 往返。"""

from __future__ import annotations

import hashlib

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([0, 1], [0, 1])
    fig.savefig("Fig1.pdf")
"""


def test_build_reports_the_sha256_of_the_executed_source_and_a_second_build_keeps_it(tmp_path):
    script = tmp_path / "fig1.py"
    script.write_bytes(SCRIPT.encode())
    executed = hashlib.sha256(SCRIPT.encode()).hexdigest()
    w = pool.one_shot("fig1.py", str(tmp_path), "main")
    try:
        assert w.last_build_source_sha256 is None
        resp = w.ensure_built()
        assert resp["source_sha256"] == executed
        assert w.last_build_source_sha256 == executed
        # 磁盘在 build 之后被改：二次 build（workerd 去重回旧会话时前端 / after 就是这么拿摘要）报的仍是执行版本
        script.write_bytes(SCRIPT.encode() + b"\n# edited\n")
        w.built = False
        w.ensure_built()
        assert w.last_build_source_sha256 == executed
        assert executed != hashlib.sha256(script.read_bytes()).hexdigest()
    finally:
        pool.discard(w)


SELF_REWRITING = SCRIPT.replace(
    "    fig.savefig",
    "    import os, pathlib\n"
    "    p = pathlib.Path(__file__)\n"
    "    p.write_bytes(p.read_bytes() + b'\\n# rewritten\\n')\n"
    "    os.utime(p, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))\n"
    "    fig.savefig",
)


def test_rewrite_during_execution_reports_none(tmp_path):
    (tmp_path / "fig1.py").write_bytes(SELF_REWRITING.encode())
    w = pool.one_shot("fig1.py", str(tmp_path), "main")
    try:
        resp = w.ensure_built()
        assert resp["source_sha256"] is None
        assert w.last_build_source_sha256 is None
    finally:
        pool.discard(w)


def test_rewrite_by_paper_style_import_before_execution_reports_none(tmp_path):
    script = tmp_path / "fig1.py"
    script.write_bytes(SCRIPT.encode())
    (tmp_path / "paper_style.py").write_text(
        "import pathlib\n"
        "p = pathlib.Path(__file__).with_name('fig1.py')\n"
        "p.write_bytes(p.read_bytes() + b'\\n# edited by import\\n')\n"
        "def save(*a, **k):\n    pass\n",
        encoding="utf-8",
    )
    w = pool.one_shot("fig1.py", str(tmp_path), "main")
    try:
        resp = w.ensure_built()
        assert resp["source_sha256"] is None
        assert w.last_build_source_sha256 is None
    finally:
        pool.discard(w)
