#!/usr/bin/env python3
"""QA 记录器：跑一条命令，把「命令 + 时间 + SHA + 退出码 + 输出」写进日志。

用法（从 worktree 根目录）：
    python3 docs/qa/2026-09-24/rel/repro/run_logged.py <日志路径> [--cwd DIR] -- <命令...>

- 日志开头是逐字命令、cwd、UTC 时间、HEAD SHA；结尾是 exit code。
- 超过 190 行时保留头 40 行 + 尾 150 行并注明截断（原始全文另存 <日志>.full，
  调用方决定是否入库）。
- 自己的退出码 == 被跑命令的退出码（判据用退出码，别用眼睛）。
"""

from __future__ import annotations

import datetime as _dt
import os
import shlex
import subprocess
import sys


def main(argv: list[str]) -> int:
    if "--" not in argv:
        print(__doc__)
        return 2
    sep = argv.index("--")
    head, cmd = argv[:sep], argv[sep + 1 :]
    log_path = head[0]
    cwd = os.getcwd()
    if "--cwd" in head:
        cwd = os.path.abspath(head[head.index("--cwd") + 1])
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    started = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    proc = subprocess.run(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
    )
    out = proc.stdout.decode("utf-8", "replace")
    ended = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    lines = out.splitlines()
    full_header = [
        f"# command: {shlex.join(cmd)}",
        f"# cwd: {cwd}",
        f"# started_utc: {started}",
        f"# ended_utc: {ended}",
        f"# head_sha: {sha}",
        f"# exit_code: {proc.returncode}",
        "",
    ]
    body = lines
    if len(lines) > 190:
        body = [
            *lines[:40],
            f"... [截断：原输出 {len(lines)} 行，保留头 40 行 + 尾 150 行] ...",
            *lines[-150:],
        ]
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join([*full_header, *body, "", f"# exit_code: {proc.returncode}", ""]))
    with open(log_path + ".full", "w", encoding="utf-8") as fh:
        fh.write("\n".join([*full_header, *lines, ""]))
    print(f"exit_code={proc.returncode} lines={len(lines)} log={log_path}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
