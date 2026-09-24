"""证据记录器：跑一条命令，把「命令 / 时间 / SHA / 输出 / 退出码」写进 logs/<CASE>.log。

用法（从 worktree 根目录）::

    python3 docs/qa/2026-09-24/geo/repro/run_case.py <CASE_ID> '<shell command>' [--append]

输出超过 190 行时保留头 40 行 + 尾 150 行并注明截断。werkzeug 的逐请求 INFO 行
（`INFO werkzeug:`）在 e2e 输出里成百上千条，与判据无关，先滤掉再截断，并注明滤掉了多少行。
退出码取**命令本身**的（不经管道），最后一行固定写 `[exit=N]`。
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(os.path.dirname(HERE), "logs")


def main() -> int:
    case, cmd = sys.argv[1], sys.argv[2]
    append = "--append" in sys.argv
    os.makedirs(LOGS, exist_ok=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    t0 = datetime.datetime.now()
    p = subprocess.run(cmd, shell=True, env=env, capture_output=True, text=True)
    lines = (p.stdout + p.stderr).splitlines()
    noisy = [ln for ln in lines if "INFO werkzeug:" in ln]
    lines = [ln for ln in lines if "INFO werkzeug:" not in ln]
    if len(lines) > 190:
        lines = lines[:40] + [f"... [截断：省略中间 {len(lines) - 190} 行] ..."] + lines[-150:]
    path = os.path.join(LOGS, f"{case}.log")
    with open(path, "a" if append else "w", encoding="utf-8") as f:
        f.write(f"# case: {case}\n# time: {t0.isoformat(timespec='seconds')}\n# HEAD: {sha}\n")
        f.write(f"# cwd: <worktree root>\n$ {cmd}\n")
        if noisy:
            f.write(f"[已滤掉 {len(noisy)} 行 werkzeug 逐请求日志]\n")
        f.write("\n".join(lines) + "\n")
        f.write(f"[exit={p.returncode}]\n\n")
    print(f"{case}: exit={p.returncode} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
