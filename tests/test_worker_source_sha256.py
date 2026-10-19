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
