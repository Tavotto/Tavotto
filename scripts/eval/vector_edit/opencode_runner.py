"""起一次 `opencode run`（只起 opencode，不起 Tavotto 实例）：独立进程组、不给 stdin，超时只结束自己起的那个进程组。

单独成文件是为了让 `harness.py` 里「起 Tavotto 实例」与「起 agent 子进程」分开：前者经 `tests/support/foundation_app.sidecar_app`
（桌面握手与会话凭据在夹具里），后者与 Tavotto 的 API 无关。
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path


def run_agent(cwd: Path, argv: list[str], env: dict, timeout: int, log: Path) -> tuple[bool, float]:
    """→ (是否超时, 用时秒)。输出（含 stderr）写进 `log`。"""
    t0 = time.time()
    timed_out = False
    proc = None
    with log.open("w", encoding="utf-8") as fh:
        try:
            proc = subprocess.Popen(  # noqa: S603
                argv,
                cwd=cwd,
                env=env,
                stdout=fh,
                stderr=subprocess.STDOUT,
                text=True,
                stdin=subprocess.DEVNULL,  # 新会话里读继承来的终端会被挂起：明确不给 stdin
                start_new_session=True,
            )
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:  # 只结束自己起的这个进程组
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=15)
            except Exception:  # noqa: BLE001
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:  # noqa: BLE001
                    pass
    return timed_out, round(time.time() - t0, 1)
