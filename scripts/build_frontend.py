#!/usr/bin/env python3
"""构建前端并把产物放进包里（src/magplot/web），wheel 直接带着走。

打包前必须先跑这个——用户装 wheel 时不该需要 node/pnpm。
CI 的发布流水线和本地 `python -m build` 之前都走同一条命令：

    python scripts/build_frontend.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# Windows 上 stdout 被重定向成管道时会退回系统区域编码（cp1252/cp936），
# 打印带中文或 ✓ 的进度就会 UnicodeEncodeError——构建明明成功了却以非零退出。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
WEB_SRC = ROOT / "web"
DIST = WEB_SRC / "dist"
TARGET = ROOT / "src" / "magplot" / "web"


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=WEB_SRC, check=True)


def main() -> int:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        print("找不到 pnpm：npm i -g pnpm，或用 corepack enable", file=sys.stderr)
        return 1
    if "--skip-install" not in sys.argv:
        run([pnpm, "install", "--frozen-lockfile"])
    run([pnpm, "build"])

    if not (DIST / "index.html").is_file():
        print(f"构建没产出 {DIST}/index.html", file=sys.stderr)
        return 1
    # 整目录替换：留着旧构建的残余文件会让 /api/version 的 build id 对不上
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(DIST, TARGET)
    n = sum(1 for _ in TARGET.rglob("*") if _.is_file())
    print(f"✓ 前端已就位: {TARGET.relative_to(ROOT)}（{n} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
