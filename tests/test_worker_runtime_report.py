"""真 worker 的自报（ADR 0053）：v1 build 响应里的 `runtime` 是**那个子进程此刻量到的**。

判据的主语：**执行侧进程**的 `sys.prefix` / `sys.executable` / 包版本 / cwd——不是父进程
以为它会用哪个解释器。对拍的尺子刻意不同源：另起一个同样的解释器打印同一批值，
与 worker 自报逐字段比（`prefix` 与 `base_prefix` 两个都比，venv 与基础解释器就
差在这里）。legacy 信封的形状一字不变（`test_worker_roundtrip` 钉着）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import execspec, pool, receipt

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT = """\
import os
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(2, 1.5))
ax.plot([0, 1], [1, 0])
fig.savefig("Rt.pdf")
"""


@pytest.fixture
def figs(tmp_path):
    root = tmp_path / "figs"
    root.mkdir()
    (root / "rt.py").write_text(SCRIPT, encoding="utf-8")
    yield root
    pool.shutdown_all(str(root), wait=True)


def _independent_facts(python: str) -> dict:
    code = (
        "import sys, json, platform, importlib.metadata as md\n"
        "print(json.dumps({'executable': sys.executable, 'prefix': sys.prefix,"
        " 'base_prefix': sys.base_prefix, 'python_version': platform.python_version(),"
        " 'matplotlib': md.version('matplotlib')}))\n"
    )
    out = subprocess.run(
        [python, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_worker_reports_the_prefix_of_the_process_that_actually_ran(figs):
    worker, resp = pool.build("rt.py", str(figs), "__main__")
    rt = resp["runtime"]
    facts = _independent_facts(worker.python)
    assert rt["executable"] == facts["executable"]
    assert rt["prefix"] == facts["prefix"]
    assert rt["base_prefix"] == facts["base_prefix"]
    assert rt["python_version"] == facts["python_version"]
    assert rt["packages"]["matplotlib"] == facts["matplotlib"]
    # cwd 是脚本**真正**看到的：默认模式就是那个沙盒目录（写入边界）
    assert Path(rt["cwd"]).resolve() == worker.sandbox.resolve()
    assert rt["argv0"] == str((figs / "rt.py").resolve())
    assert worker.last_build_runtime == rt  # 控制面把它记在账上
    assert "runtime" in resp and resp["stems"] == {"Rt": resp["stems"]["Rt"]}


def test_the_receipt_assembled_from_a_real_worker_is_complete(figs):
    worker, resp = pool.build("rt.py", str(figs), "__main__")
    rcpt = receipt.from_worker(
        worker, resp, control_plane=pool.control_plane_of(worker), grant=None
    )
    assert rcpt.completeness == receipt.COMPLETENESS_COMPLETE
    assert rcpt.generation == worker.generation
    assert rcpt.source_revision == worker.script_sha1 != ""
    assert rcpt.launch_context["cwd_origin"] == execspec.CWD_ORIGIN_SANDBOX
    assert [d["stem"] for d in rcpt.descriptors] == ["Rt"]
    private = rcpt.to_payload(include_private=True)
    assert private["runtime"]["prefix"] == resp["runtime"]["prefix"]
    assert private["interpreter"] == worker.python
    # 同一条会话再取一次回执：id 相同（同一次执行实例）
    again = receipt.from_worker(worker, resp, control_plane="python_pool", grant=None)
    assert again.receipt_id == rcpt.receipt_id
