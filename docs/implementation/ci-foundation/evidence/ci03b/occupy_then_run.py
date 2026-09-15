#!/usr/bin/env python3
"""本机复现「租约窗口里端口被抢」的驱动（CI03b §3.2 的证据，不进 CI）。

    python occupy_then_run.py --state-file S --port P -- <python> -m tavotto --port P --no-browser

第一次起（S 不存在）：先在本进程里把 P bind 住（监听着不放），再把真产品当子进程起在同一个
P 上——产品的 `resolve_port` 看见 P 被占，会顺延到 P+1 并打「* 端口 P 被占用，改用 P+1」。
冒烟脚本应当认出租约丢了、终止整个进程组（本驱动 + 产品）、换端口重来；第二次起（S 已在）
本驱动什么都不占，直接把产品起在 P 上。用法上它就是 `package_smoke.py --launch` 的一个模板：
`--launch "python occupy_then_run.py --state-file S --port {port} -- <venv>/python -m tavotto --port {port} --no-browser"`。
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--state-file", type=Path, required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        ap.error("`--` 之后要给被起的命令")
    squatter: socket.socket | None = None
    if not args.state_file.exists():
        args.state_file.write_text("occupied once\n", encoding="utf-8")
        squatter = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        squatter.bind(("127.0.0.1", args.port))
        squatter.listen(1)
        print(f"occupy_then_run: 抢先占住了 {args.port}", flush=True)
    else:
        print("occupy_then_run: 第二次，不占", flush=True)
    try:
        return subprocess.run(cmd).returncode
    finally:
        if squatter is not None:
            squatter.close()


if __name__ == "__main__":
    sys.exit(main())
