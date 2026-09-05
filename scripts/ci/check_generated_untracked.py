#!/usr/bin/env python3
"""发行生成物**不许进索引**（ADR 0043）——问索引，不问 .gitignore 写了什么。

    python scripts/ci/check_generated_untracked.py            # 0 干净 / 1 有生成物被跟踪或没被忽略
    python scripts/ci/check_generated_untracked.py --repo DIR

为什么要有它：画布 `codex-plugin/mcp/widget/canvas.html` 从 2026-09-05 起不再入库。
一个功能提交把它加回索引不会带来任何好处（安装入口读的是发行分支 `plugin-stable`，
源码分支里那份没人装得到），只会把合并队列拖回「两个前端 PR 为同一份 HTML 必撞」。
这种回归**没有别的红灯**：文件加回去了，构建照过、测试照绿。所以 PR 快线（frontend job）
与 main 落地审计各查一次。

两条判据缺一不可：`git ls-files` 里不能有（判「在不在索引」）；`git check-ignore` 必须命中
（判「`git add -A` 会不会顺手收进来」）。只做第一条的话，下一次 `git add -A` 就把它带回来。
纯标准库。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

#: 本仓库里**不许**进索引的生成物（路径或目录）。新增一类生成物时往这里加一行，
#: 并在 .gitignore 里挡住它——本脚本会把「没挡住」也当成失败。
GENERATED = (
    "codex-plugin/mcp/widget/canvas.html",
    "web/dist-mcp",
    "web/dist-playground",
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def check(repo: Path, generated=GENERATED) -> list[str]:
    problems: list[str] = []
    for rel in generated:
        listed = _git(repo, "ls-files", "-z", "--", rel)
        if listed.returncode != 0:
            problems.append(f"git ls-files {rel} 失败：{listed.stderr.strip()}")
            continue
        tracked = [p for p in listed.stdout.split("\0") if p]
        if tracked:
            problems.append(
                f"{rel} 在索引里（{len(tracked)} 个文件）——发行生成物不许进源码分支；"
                f"`git rm --cached` 之后重新提交"
            )
        # 目录规则（`web/dist-mcp/`）只匹配目录，而目录此刻多半不存在——探它下面的一个假文件
        probe = rel if "." in rel.rsplit("/", 1)[-1] else f"{rel}/probe"
        ignored = _git(repo, "check-ignore", "-q", "--", probe)
        # check-ignore 的退出码：0 命中，1 没命中，128 出错
        if ignored.returncode == 1:
            problems.append(f"{rel} 没被 .gitignore 挡住——`git add -A` 会把本地构建物收进提交")
        elif ignored.returncode not in (0, 1):
            problems.append(f"git check-ignore {rel} 失败：{ignored.stderr.strip()}")
    return problems


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    args = ap.parse_args(argv)
    problems = check(args.repo)
    if problems:
        for p in problems:
            print(f"::error::{p}")
        return 1
    print(f"发行生成物不在索引里且已被忽略：{', '.join(GENERATED)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
