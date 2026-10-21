#!/usr/bin/env python3
"""Playwright 分片的**每片自验**（CI03c；三片：chromium 内部再用 `--shard=i/n` 切两半）。

一片（slice）= 若干 `--project=NAME`，外加至多一个 `--shard=i/n`（Playwright 原生分片，只在
这几个 project 内按文件顺序等分）。本脚本在运行时对着 Playwright 自己的 `--list` 钉死：
所有片的并集 == 全集、两两不交、每片非空。

    python scripts/ci/playwright_shard_check.py \
        --shard 2 --projects="--project=webkit --project=chromium-en" \
        --others="--project=chromium --shard=1/2|--project=chromium --shard=2/2" \
        --web web --out "$RUNNER_TEMP/playwright-shard"

    # 或离线：已经存好的 `playwright test --list` 输出（others 的清单按顺序给）
    python scripts/ci/playwright_shard_check.py --shard 2 --projects="--project=webkit" \
        --others="--project=chromium" \
        --full list_all.txt --mine list_shard2.txt --other-list list_chromium.txt

`--projects` 是本片；`--others` 是**其余每一片**，用 `|` 隔开（matrix 值里不能有逗号，
`|` 可以）。值以 `--` 开头，**必须写成 `--opt=值`**：空格形式会被 argparse 当成下一个选项
（「expected one argument」，rc 2）。

为什么要有这一步（而不是只靠合同测试）：

* `windows-exe-smoke` 的每片各自只跑自己的参数，每一片都看不见别的片有没有跑、也看不见
  「配置里还有别的 project」「`--shard=i/n` 的 n 与另一片对不上」。合同测试
  （`tests/test_merge_queue_workflows.py`）在源码层钉 matrix 与 `web/playwright.config.ts`；
  这一步在**运行时**、对着 Playwright 自己算出来的 `--list` 再钉一次。
* 判据的主语是 **(project, file:line:col, title) 的集合**，不是条数：两片各漏一条再各多
  一条，条数照样对得上。

判据（任一条不成立 → 退出码 1，job 红在 e2e 之前）：

1. 每片的 project 集非空；同一个 project 被多片声称时，必须每片都带 `--shard=i/n`、n 相同、
   i 恰好是 1..n 各一次（不带 `--shard` 的整 project 与任何别的片共用同一 project = 重叠）；
2. 所有片 claim 的 project 并集 == 全集（`--list` 全量）里出现的 project 集——少一个是漏片，
   多一个是 matrix 里写了配置里没有的 project；
3. 每片的 `--list`（带该片参数）非空，且每条都在全集里、project ∈ 该片；不带 `--shard` 的片
   == 全集里 project ∈ 该片的全部；
4. 各片清单两两不交，并集 == 全集。

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
_SHARD_ARG = re.compile(r"^--shard=(?P<i>\d+)/(?P<n>\d+)$")


class ListError(ValueError):
    """`--list` 输出或参数本身不合形状——这是输入错误（rc 2），不是分片错误（rc 1）。"""


class Slice(NamedTuple):
    """一片的参数：project 集 + 可选的 Playwright 原生 `--shard=i/n`。"""

    projects: frozenset[str]
    shard: tuple[int, int] | None


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
    sl = parse_slice(arg, label)
    if sl.shard is not None:
        raise ListError(f"{label}：这里不接受 --shard")
    return set(sl.projects)


def parse_slice(arg: str, label: str) -> Slice:
    """`"--project=chromium --shard=1/2"` → `Slice({"chromium"}, (1, 2))`。

    token 只认 `--project=NAME` 与至多一个 `--shard=i/n`（n >= 2，1 <= i <= n）；空串、别的
    token、重复 project、重复 / 越界的 shard 都抛。
    """
    names: list[str] = []
    shard: tuple[int, int] | None = None
    for tok in arg.split():
        m = _PROJECT_ARG.match(tok)
        if m:
            names.append(m.group("name"))
            continue
        sm = _SHARD_ARG.match(tok)
        if sm:
            if shard is not None:
                raise ListError(f"{label}：--shard 出现了不止一次")
            i, n = int(sm.group("i")), int(sm.group("n"))
            if n < 2 or not 1 <= i <= n:
                raise ListError(f"{label}：`{tok}` 越界（要 n >= 2 且 1 <= i <= n）")
            shard = (i, n)
            continue
        raise ListError(f"{label}：`{tok}` 不是 `--project=NAME` / `--shard=i/n` 的形状")
    if not names:
        raise ListError(f"{label}：一个 --project= 都没有（空片）")
    if len(set(names)) != len(names):
        raise ListError(f"{label}：project 重复：{names}")
    return Slice(frozenset(names), shard)


def parse_others(arg: str) -> list[Slice]:
    """`"A | B"`（`|` 隔开的其余每一片）→ 片列表；至少一片，每片都合形状。"""
    parts = arg.split("|")
    return [parse_slice(p, f"--others 第 {k} 片") for k, p in enumerate(parts, 1)]


def verify(
    full: list[Entry],
    mine: list[Entry],
    my_slice: Slice,
    others: list[tuple[Slice, list[Entry]]],
) -> list[str]:
    """返回问题清单；空 = 分片完整。主语是集合，不是计数。

    `others` 是其余每一片的（参数, 该片 `--list` 清单）。
    """
    problems: list[str] = []
    slices = [(my_slice, mine), *others]
    full_set = set(full)
    all_projects = {e.project for e in full}

    # 1. 静态：同一 project 的归属
    by_project: dict[str, list[Slice]] = {}
    for sl, _ in slices:
        for p in sl.projects:
            by_project.setdefault(p, []).append(sl)
    for p, owners in sorted(by_project.items()):
        if len(owners) == 1:
            if owners[0].shard is not None:
                problems.append(
                    f"project {p} 只有一片带 --shard={owners[0].shard[0]}/{owners[0].shard[1]}——另一半没人跑，漏片"
                )
            continue
        if any(o.shard is None for o in owners):
            problems.append(
                f"{len(owners)} 片都声称要跑 project {p}，其中有不带 --shard 的整 project——"
                "两片都声称同一内容，跑两遍、判定却只算一次"
            )
            continue
        ns = {o.shard[1] for o in owners if o.shard}
        idx = sorted(o.shard[0] for o in owners if o.shard)
        if len(ns) != 1 or idx != list(range(1, next(iter(ns)) + 1)):
            problems.append(
                f"project {p} 的 --shard 分法对不上：各片是 "
                f"{sorted(f'{o.shard[0]}/{o.shard[1]}' for o in owners if o.shard)}"
                "（要 n 相同、i 恰好 1..n 各一次）"
            )
    claimed = set(by_project)
    missing = all_projects - claimed
    unknown = claimed - all_projects
    if missing:
        problems.append(f"配置里的 project {sorted(missing)} 不在任何一片里——漏片")
    if unknown:
        problems.append(
            f"matrix 里的 project {sorted(unknown)} 在全集 `--list` 里不存在（拼错了，或配置删了它）"
        )

    # 2. 动态：每片清单
    for k, (sl, entries) in enumerate(slices):
        who = "本片" if k == 0 else f"另一片（{k}）"
        got = set(entries)
        if not got:
            problems.append(f"{who}的 `--list` 是空的")
        outside = got - full_set
        if outside:
            problems.append(f"{who}有 {len(outside)} 条不在全集里：{sorted(outside)[:3]}")
        foreign = {e for e in got & full_set if e.project not in sl.projects}
        if foreign:
            problems.append(
                f"{who}多了 {len(foreign)} 条不属于本片 project 的用例：{sorted(foreign)[:3]}"
            )
        if sl.shard is None:
            expected = {e for e in full if e.project in sl.projects}
            lacking = expected - got
            if lacking:
                problems.append(
                    f"{who}少了 {len(lacking)} 条属于本片 project 的用例：{sorted(lacking)[:3]}"
                )

    # 3. 各片之间：不交、并集 == 全集
    seen: dict[Entry, int] = {}
    for _, entries in slices:
        for e in set(entries):
            seen[e] = seen.get(e, 0) + 1
    twice = sorted(e for e, c in seen.items() if c > 1)
    if twice:
        problems.append(f"有 {len(twice)} 条用例被不止一片跑到（重叠）：{twice[:3]}")
    uncovered = sorted(full_set - set(seen))
    if uncovered:
        problems.append(f"全集里有 {len(uncovered)} 条不在任何一片里（漏片）：{uncovered[:3]}")
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
        "--projects",
        required=True,
        help='本片的参数，如 "--project=chromium --shard=1/2"（--shard 可省）',
    )
    ap.add_argument(
        "--others",
        required=True,
        help="其余每一片的参数，用 | 隔开（matrix 里另外几片的 projects）",
    )
    src = ap.add_argument_group("输入（二选一）")
    src.add_argument(
        "--web", type=Path, help="web/ 目录：脚本自己跑 `--list`（全量 + 本片 + 其余每一片）"
    )
    src.add_argument("--full", type=Path, help="已存好的全量 `--list` 输出")
    src.add_argument("--mine", type=Path, help="已存好的本片 `--list` 输出")
    src.add_argument(
        "--other-list",
        type=Path,
        action="append",
        default=[],
        help="已存好的其余某一片的 `--list` 输出；按 --others 里的顺序，每片一份",
    )
    ap.add_argument(
        "--out", type=Path, help="把各份 `--list` 与 check.json 写到这个目录（证据，不参与判定）"
    )
    args = ap.parse_args(argv)

    try:
        my_slice = parse_slice(args.projects, "--projects")
        other_slices = parse_others(args.others)
        other_texts: list[str]
        if args.web is not None:
            if args.full or args.mine or args.other_list:
                raise ListError("--web 与 --full/--mine/--other-list 只能选一种")
            full_text = capture(args.web, [])
            mine_text = capture(args.web, args.projects.split())
            other_texts = [capture(args.web, a.split()) for a in args.others.split("|")]
        elif args.full is not None and args.mine is not None:
            if len(args.other_list) != len(other_slices):
                raise ListError(
                    f"--others 有 {len(other_slices)} 片，--other-list 给了 {len(args.other_list)} 份"
                )
            full_text = args.full.read_text(encoding="utf-8")
            mine_text = args.mine.read_text(encoding="utf-8")
            other_texts = [f.read_text(encoding="utf-8") for f in args.other_list]
        else:
            raise ListError(
                "要么给 --web，要么同时给 --full 与 --mine（与每个其余片的 --other-list）"
            )
        full = parse_list(full_text, "全量 --list")
        mine = parse_list(mine_text, "本片 --list")
        others = [
            (sl, parse_list(t, f"其余第 {k} 片 --list"))
            for k, (sl, t) in enumerate(zip(other_slices, other_texts, strict=True), 1)
        ]
    except ListError as exc:
        print(f"ERROR（输入）: {exc}", file=sys.stderr)
        return 2

    problems = verify(full, mine, my_slice, others)
    per_project = {
        p: sum(1 for e in full if e.project == p) for p in sorted({e.project for e in full})
    }
    summary = {
        "shard": args.shard,
        "my_projects": sorted(my_slice.projects),
        "my_shard": list(my_slice.shard) if my_slice.shard else None,
        "other_slices": [
            {"projects": sorted(sl.projects), "shard": list(sl.shard) if sl.shard else None}
            for sl, _ in others
        ],
        "all_projects": sorted(per_project),
        "full_total": len(full),
        "per_project_total": per_project,
        "mine_total": len(mine),
        "others_total": [len(es) for _, es in others],
        "problems": problems,
        "ok": not problems,
    }
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "list_all.txt").write_text(full_text, encoding="utf-8")
        (args.out / f"list_shard{args.shard}.txt").write_text(mine_text, encoding="utf-8")
        for k, t in enumerate(other_texts, 1):
            (args.out / f"list_other{k}.txt").write_text(t, encoding="utf-8")
        (args.out / "check.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
    print(json.dumps(summary, ensure_ascii=False))
    if problems:
        for p in problems:
            print(f"ERROR（分片）: {p}", file=sys.stderr)
        return 1
    total = len(mine) + sum(len(es) for _, es in others)
    print(
        f"✓ 第 {args.shard} 片 {args.projects.strip()}：{len(mine)} 条；"
        f"与其余 {len(others)} 片（{summary['others_total']}）合计 {total} 条 == 全集 {len(full)} 条，两两不交"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
