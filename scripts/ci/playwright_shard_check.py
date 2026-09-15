#!/usr/bin/env python3
"""Playwright 按 project 分片的**每片自验**（CI03c）：本片 == 全集里属于本片 project 的那些。

    python scripts/ci/playwright_shard_check.py \
        --shard 2 --projects="--project=webkit --project=chromium-en" \
        --others="--project=chromium" \
        --web web --out "$RUNNER_TEMP/playwright-shard"

    # 或离线：两份已经存好的 `playwright test --list` 输出
    python scripts/ci/playwright_shard_check.py --shard 1 --projects="--project=chromium" \
        --others="--project=webkit --project=chromium-en" \
        --full list_all.txt --mine list_shard1.txt

`--projects` / `--others` 的值以 `--` 开头，**必须写成 `--opt=值`**：空格形式会被 argparse
当成下一个选项（「expected one argument」，rc 2）。

为什么要有这一步（而不是只靠合同测试）：

* `windows-exe-smoke` 的两片各自只跑自己的 `--project=…`，每一片都看不见另一片有没有
  跑、也看不见「配置里还有第三个 project」。合同测试（`tests/test_merge_queue_workflows.py`）
  在源码层钉 matrix 与 `web/playwright.config.ts` 的 project 集合；这一步在**运行时**、对着
  Playwright 自己算出来的 `--list` 再钉一次——配置里 project 是动态拼的、或合同测试的
  正则没看见的形状，这里才看得见。
* 判据的主语是 **(project, file:line:col, title) 的集合**，不是条数：两片各漏一条再各多
  一条，条数照样对得上。

判据（任一条不成立 → 退出码 1，job 红在 e2e 之前）：

1. 本片的 project 集非空，另一片的 project 集非空，两者不交；
2. 本片 ∪ 另一片 == 全集（`--list` 全量）里出现的 project 集——少一个是漏片，多一个是
   matrix 里写了配置里没有的 project；
3. 本片的 `--list`（带本片 `--project=` 参数）的集合 == 全集里 project ∈ 本片的那些，
   且非空。

输入本身也不信：空输出、解析不出的行、重复条目、`Total:` 行的数字与解析出的条数不一致，
一律退出码 2（输入 / 用法错误——判定器自己坏了不能算通过）。纯标准库。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# Windows 上 stdout 一旦不是真控制台（被 CI 捕获 / 管道 / 重定向）就退回系统区域
# 编码（cp1252/cp936），第一句中文或 ✓ 的输出就 UnicodeEncodeError——脚本明明
# 做完了却以非零退出，而父进程只看得见「它挂了」。写法与 aggregate_gate.py 同源。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

#: `playwright test --list` 的一行：`  [project] › file:line:col › title`。
#: 标题里可以再出现 ` › `（describe 块），所以标题用贪婪的 `.+`；文件名不含空格。
_ENTRY = re.compile(r"^\s*\[(?P<project>[^\]]+)\] › (?P<location>\S+:\d+:\d+) › (?P<title>.+?)\s*$")
_HEADER = "Listing tests:"
_TOTAL = re.compile(r"^Total: (?P<n>\d+) tests? in (?P<files>\d+) files?\s*$")
_PROJECT_ARG = re.compile(r"^--project=(?P<name>\S+)$")


class ListError(ValueError):
    """`--list` 输出或参数本身不合形状——这是输入错误（rc 2），不是分片错误（rc 1）。"""


class Entry(NamedTuple):
    project: str
    location: str
    title: str


def parse_list(text: str, label: str) -> list[Entry]:
    """解析一份 `playwright test --list` 输出；**每一行都要认得出**，认不出当场抛。

    空输出、没有 `Total:` 行、`Total:` 的数字与解析出的条数不一致、重复条目都抛：
    一份看不懂的清单不能当成「零条」或「差不多」去比。
    """
    entries: list[Entry] = []
    total: int | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line == _HEADER:
            continue
        tm = _TOTAL.match(line)
        if tm:
            if total is not None:
                raise ListError(f"{label}：出现了两行 Total:")
            total = int(tm.group("n"))
            continue
        em = _ENTRY.match(line)
        if not em:
            raise ListError(
                f"{label}：这一行认不出（不是 `[project] › file:line:col › title`）：{line!r}"
            )
        entries.append(Entry(em.group("project"), em.group("location"), em.group("title")))
    if not entries:
        raise ListError(f"{label}：`--list` 输出里一条用例都没有")
    if total is None:
        raise ListError(f"{label}：没有 `Total: N tests in M files` 行——输出被截断了？")
    if total is not None and total != len(entries):
        raise ListError(
            f"{label}：Total: 说有 {total} 条，解析出 {len(entries)} 条——有行没认出来或多认了"
        )
    dup = sorted({e for e in entries if entries.count(e) > 1})
    if dup:
        raise ListError(f"{label}：重复条目 {len(dup)} 条：{dup[:3]}")
    return entries


def parse_projects(arg: str, label: str) -> set[str]:
    """`"--project=a --project=b"` → `{"a", "b"}`；每个 token 都必须是 `--project=NAME`。

    空串是空片、别的 token 是 matrix 里混进了别的参数、重复是同一个 project 写了两遍——
    三种都抛：它们进了 `pnpm e2e` 的命令行之后不会有任何东西再提醒你。
    """
    names: list[str] = []
    for tok in arg.split():
        m = _PROJECT_ARG.match(tok)
        if not m:
            raise ListError(f"{label}：`{tok}` 不是 `--project=NAME` 的形状")
        names.append(m.group("name"))
    if not names:
        raise ListError(f"{label}：一个 --project= 都没有（空片）")
    if len(set(names)) != len(names):
        raise ListError(f"{label}：project 重复：{names}")
    return set(names)


def verify(
    full: list[Entry], mine: list[Entry], my_projects: set[str], other_projects: set[str]
) -> list[str]:
    """返回问题清单；空 = 分片完整。主语是集合，不是计数。"""
    problems: list[str] = []
    overlap = my_projects & other_projects
    if overlap:
        problems.append(
            f"两片都声称要跑 project {sorted(overlap)}——同一片内容跑两遍，判定却只算一次"
        )
    all_projects = {e.project for e in full}
    claimed = my_projects | other_projects
    missing = all_projects - claimed
    unknown = claimed - all_projects
    if missing:
        problems.append(f"配置里的 project {sorted(missing)} 不在任何一片里——漏片")
    if unknown:
        problems.append(
            f"matrix 里的 project {sorted(unknown)} 在全集 `--list` 里不存在（拼错了，或配置删了它）"
        )

    expected = {e for e in full if e.project in my_projects}
    got = set(mine)
    if not got:
        problems.append("本片的 `--list` 是空的")
    outside = got - set(full)
    if outside:
        problems.append(f"本片有 {len(outside)} 条不在全集里：{sorted(outside)[:3]}")
    lacking = expected - got
    if lacking:
        problems.append(f"本片少了 {len(lacking)} 条属于本片 project 的用例：{sorted(lacking)[:3]}")
    extra = (got & set(full)) - expected
    if extra:
        problems.append(f"本片多了 {len(extra)} 条不属于本片 project 的用例：{sorted(extra)[:3]}")
    return problems


def capture(web: Path, project_args: list[str]) -> str:
    """在 `web/` 下跑 `node node_modules/@playwright/test/cli.js test --list …`，按 UTF-8 解码。

    直接起 node（真 .exe），不经 `pnpm` / `npx` 的 .cmd shim，也不经 pwsh 的 `>` 重定向——
    后者会按控制台代码页把中文标题转一遍（Windows 上是 cp1252 / cp936），两份文件被同样
    地转坏时集合比对照样绿，但坏掉的标题会把本该不同的条目粘成同一条。
    """
    # 绝对路径：下面 cwd 切到 web/ 之后，相对的 `web/node_modules/…` 会被 node 按 cwd 再拼一次
    web = web.resolve()
    cli = web / "node_modules" / "@playwright" / "test" / "cli.js"
    if not cli.is_file():
        raise ListError(f"{cli} 不存在——web 依赖没装？")
    proc = subprocess.run(
        ["node", str(cli), "test", "--list", *project_args],
        cwd=str(web),
        capture_output=True,
        timeout=300,
    )
    out = proc.stdout.decode("utf-8", errors="strict")
    if proc.returncode != 0:
        raise ListError(
            f"`playwright test --list {' '.join(project_args)}` 退出码 {proc.returncode}：\n"
            f"{proc.stderr.decode('utf-8', errors='replace')}"
        )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--shard", type=int, required=True, help="本片的片号（只进摘要，不参与判定）")
    ap.add_argument(
        "--projects", required=True, help='本片的 --project= 参数，如 "--project=chromium"'
    )
    ap.add_argument("--others", required=True, help="其余片的 --project= 参数（matrix 里另一片的）")
    src = ap.add_argument_group("输入（二选一）")
    src.add_argument("--web", type=Path, help="web/ 目录：脚本自己跑两次 `--list`（全量 + 本片）")
    src.add_argument("--full", type=Path, help="已存好的全量 `--list` 输出")
    src.add_argument("--mine", type=Path, help="已存好的本片 `--list` 输出")
    ap.add_argument(
        "--out", type=Path, help="把两份 `--list` 与 check.json 写到这个目录（证据，不参与判定）"
    )
    args = ap.parse_args(argv)

    try:
        my_projects = parse_projects(args.projects, "--projects")
        other_projects = parse_projects(args.others, "--others")
        if args.web is not None:
            if args.full or args.mine:
                raise ListError("--web 与 --full/--mine 只能选一种")
            full_text = capture(args.web, [])
            mine_text = capture(args.web, args.projects.split())
        elif args.full is not None and args.mine is not None:
            full_text = args.full.read_text(encoding="utf-8")
            mine_text = args.mine.read_text(encoding="utf-8")
        else:
            raise ListError("要么给 --web，要么同时给 --full 与 --mine")
        full = parse_list(full_text, "全量 --list")
        mine = parse_list(mine_text, "本片 --list")
    except ListError as exc:
        print(f"ERROR（输入）: {exc}", file=sys.stderr)
        return 2

    problems = verify(full, mine, my_projects, other_projects)
    per_project = {
        p: sum(1 for e in full if e.project == p) for p in sorted({e.project for e in full})
    }
    summary = {
        "shard": args.shard,
        "my_projects": sorted(my_projects),
        "other_projects": sorted(other_projects),
        "all_projects": sorted(per_project),
        "full_total": len(full),
        "per_project_total": per_project,
        "mine_total": len(mine),
        "mine_expected": sum(per_project[p] for p in my_projects if p in per_project),
        "problems": problems,
        "ok": not problems,
    }
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "list_all.txt").write_text(full_text, encoding="utf-8")
        (args.out / f"list_shard{args.shard}.txt").write_text(mine_text, encoding="utf-8")
        (args.out / "check.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
    print(json.dumps(summary, ensure_ascii=False))
    if problems:
        for p in problems:
            print(f"ERROR（分片）: {p}", file=sys.stderr)
        return 1
    print(
        f"✓ 第 {args.shard} 片 {sorted(my_projects)}：{len(mine)} 条 == 全集 {len(full)} 条里属于本片的 "
        f"{summary['mine_expected']} 条；{sorted(my_projects)} ∪ {sorted(other_projects)} == {sorted(per_project)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
