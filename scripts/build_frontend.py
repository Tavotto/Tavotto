#!/usr/bin/env python3
"""构建前端并把产物放进包里（src/tavotto/web），wheel 直接带着走。

打包前必须先跑这个——用户装 wheel 时不该需要 node/pnpm。
CI 的发布流水线和本地 `python -m build` 之前都走同一条命令：

    python scripts/build_frontend.py
"""

from __future__ import annotations

import os
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
TARGET = ROOT / "src" / "tavotto" / "web"

# Node 默认堆上限按机器内存算：macOS CI runner（7 GB）上约 2 GB，`pnpm build`（tsc -b + vite）
# 已经顶到——11-26、11-27 合并组 macos-app-smoke 两次 `JavaScript heap out of memory`（2026 MB）。
# 固定给 4 GB：比 runner 内存小得多，本机与 Windows 也够；调用方自己给了上限就不动。
NODE_HEAP_MB = 4096


def build_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    opts = env.get("NODE_OPTIONS", "")
    if "--max-old-space-size" not in opts:
        env["NODE_OPTIONS"] = f"{opts} --max-old-space-size={NODE_HEAP_MB}".strip()
    return env


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=WEB_SRC, check=True, env=build_env())


def main() -> int:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        print("找不到 pnpm：npm i -g pnpm，或用 corepack enable", file=sys.stderr)
        return 1
    if "--skip-install" not in sys.argv:
        run([pnpm, "install", "--frozen-lockfile"])
    # 翻译检查放在这里而不是只放在 CI 的某个 job 里：**每一条把前端打进产物的
    # 路径都经过这个脚本**（release / nightly / desktop-tauri / 本地 python -m
    # build），挂在这儿就不会有哪条发布链绕过去。漏翻的表现是界面上一串
    # `project.actions.create`——那种东西一旦发出去，用户装的就是它。
    run([pnpm, "i18n:check"])
    run([pnpm, "build"])

    if not (DIST / "index.html").is_file():
        print(f"构建没产出 {DIST}/index.html", file=sys.stderr)
        return 1

    # 先拷到旁边再原子换名，而不是 rmtree 目标目录后往里拷。
    # 从源码树跑的实例正是从 TARGET 提供前端的；先删后拷会留下几百毫秒的空窗，
    # 那期间 index.html 与 assets 全都 404——正在用的人看到的就是白屏。
    # 整目录替换本身要保留：留着旧构建的残余文件会让 /api/version 的 build id 对不上。
    staging = TARGET.with_name(TARGET.name + ".new")
    old = TARGET.with_name(TARGET.name + ".old")
    for leftover in (staging, old):
        if leftover.exists():
            shutil.rmtree(leftover)
    shutil.copytree(DIST, staging)
    if TARGET.exists():
        TARGET.rename(old)  # 同一文件系统内的 rename 是原子的
    staging.rename(TARGET)
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)

    n = sum(1 for _ in TARGET.rglob("*") if _.is_file())
    print(f"✓ 前端已就位: {TARGET.relative_to(ROOT)}（{n} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
