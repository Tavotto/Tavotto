#!/usr/bin/env python3
"""LONG-03 发现的缺陷复现（Python 渲染池）：**同一个故障（worker 在两次请求之间被 SIGKILL）
按时间窗落进三种不同的错误分类**。

HTTP 实测（`logs/LONG-03.log` 的 `try03` 段、app.log 摘录）：请求间杀 worker 后立即渲染，
三种结果都出现过——
  1. `session_dead` + 退出状态（管道 EOF 分支，#435 设计的那条路）；
  2. `code=""`、文案「worker 进程已退出」——`pool.get()` 看它还活着交出去，`request()`
     拿锁后 `alive()` 已为假：`pool.py` 里 `raise WorkerError("worker 进程已退出", …)`
     **没有 code、没有退出状态**；
  3. `internal_error`（`BrokenPipeError`）——`alive()` 为真的一瞬间进程已关掉管道，
     `self.proc.stdin.write(...)` 抛 `BrokenPipeError`，**不是 WorkerError**，一路冒到
     Flask 的未处理异常处理器（app.log「未处理异常: POST /api/engine/render」）。
恢复不受影响（下一次 `get()` 重建会话），但前端拿不到 `session_dead` 的解释
（它怎么死的 / worker.log 在哪），诊断口径分叉。

本脚本把两个竞态窗口**确定性地**复现在池层（不起 Flask）：
  A. 杀掉并收尸后直接 `request()`（= 窗口 2）；
  B. 同上，再把 `alive()` 钉成 True（= 窗口 3：进程已死但 poll 还没看见）。
期望（按 worker-protocol-and-lifecycle.md「管道 EOF 先问死因」的口径推广）：两种都应是
`WorkerError(code="session_dead")`。实测：A 是 code ""，B 抛 BrokenPipeError。
退出码：0 = 与期望一致（缺陷已修）；1 = 复现了缺陷；2 = 环境不满足。

用法（worktree 根目录）：
    PYTHONPATH=$PWD/src TAVOTTO_WORKER_PYTHON=/opt/homebrew/opt/python@3.13/libexec/bin/python3 \\
      /Volumes/Projects/Tavotto/.venv/bin/python docs/qa/2026-09-24/long/repro/worker_death_classification.py
"""

from __future__ import annotations

import os
import signal
import sys
import tempfile
from pathlib import Path

from tavotto.engine import pool

SCRIPT = (
    "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
    "fig, ax = plt.subplots()\nax.plot([0, 1], [0, 1])\nfig.savefig('Tiny.pdf')\n"
)


def one(case: str, figs: Path) -> str:
    w = pool.one_shot("tiny.py", str(figs), "__main__")
    print(f"[{case}] 控制面: {type(w).__module__}.{type(w).__name__}")
    try:
        w.ensure_built()
        os.kill(w.proc.pid, signal.SIGKILL)
        w.proc.wait(10)
        if case == "B":
            w.alive = lambda: True  # 竞态窗口：进程已死、管道已关，但 poll() 还没看见
        try:
            w.override("Tiny", [])
        except pool.WorkerError as exc:
            return f"WorkerError(code={exc.code!r}, msg={str(exc)[:40]!r})"
        except BaseException as exc:  # noqa: BLE001
            return f"{type(exc).__name__}（不是 WorkerError）"
        return "成功（不应发生）"
    finally:
        pool.discard(w)


def main() -> int:
    try:
        pool.find_worker_python()
    except pool.WorkerError:
        print("缺装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）")
        return 2
    with tempfile.TemporaryDirectory() as d:
        figs = Path(d)
        (figs / "tiny.py").write_text(SCRIPT, encoding="utf-8")
        results = {c: one(c, figs) for c in ("A", "B")}
    bad = 0
    for c, r in results.items():
        ok = r.startswith("WorkerError(code='session_dead'")
        bad += not ok
        print(f"窗口 {c}: {r}  期望 session_dead → {'一致' if ok else '不一致'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
