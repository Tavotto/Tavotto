#!/usr/bin/env python3
"""最小复现：用户脚本的异常 message 经 app.py 的 ERROR 行原样进诊断包（report.json.recent_errors）。

链路（端到端证据见 rel05_privacy_probe.py 的 canary_hits）：
  worker 回 WorkerError("脚本执行失败: <用户 raise 的 message>")
  → app.py `LOG.error("引擎渲染失败: %s: %s", stem, exc)` 写进 app.log
  → diagnostics.recent_errors 对 ` ERROR ` 行只做 shorten_paths（路径缩写），message 原样
  → report.json 的 recent_errors、包里的 app.log、/api/diagnostics/summary（复制诊断）都带着它。

对照：同一个 message 若以 traceback 收尾行的形状出现，`_closer_for_export` 只留类型名——
docs/rules/backend/diagnostics.md 明说「能保证的只有 message 不出门」「ValueError: patient-123
缩路径救不了」。ERROR 行这条旁路没被那道闸覆盖。

用法（worktree 根目录）：
    PYTHONPATH=<worktree>/src PYTHONDONTWRITEBYTECODE=1 /Volumes/Projects/Tavotto/.venv/bin/python \
        docs/qa/2026-09-24/rel/repro/repro_diag_error_message_leak.py
退出码：0 = 缺陷复现（ERROR 行里的 message 原样留下、traceback 形状的同一 message 被抹）；1 = 未复现。
"""

from __future__ import annotations

import sys

from tavotto.engine import diagnostics

SECRET = "patient_0931_BloodGlucose=7318.0931"
ERROR_LINE = f"2026-09-25 02:46:03,688 ERROR tavotto: 引擎渲染失败: boom: 脚本执行失败: {SECRET}"
TB = [
    "Traceback (most recent call last):",
    '  File "/Users/x/proj/boom.py", line 7, in main',
    f"ValueError: {SECRET}",
]

out_err = diagnostics.recent_errors([ERROR_LINE])
out_tb = diagnostics.recent_errors(TB)
print("ERROR 行 →", out_err)
print("traceback →", out_tb)
leaked_via_error_line = any(SECRET in s for s in out_err)
redacted_via_traceback = not any(SECRET in s for s in out_tb)
print("leaked_via_error_line =", leaked_via_error_line)
print("redacted_via_traceback =", redacted_via_traceback)
ok = leaked_via_error_line and redacted_via_traceback
print("REPRODUCED" if ok else "NOT_REPRODUCED")
sys.exit(0 if ok else 1)
