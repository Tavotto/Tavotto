#!/usr/bin/env python3
"""docs-only 分类器：这次改动是不是**只**碰了 `docs/release-notes/` 下的发行说明。

    # pull_request：PR 文件列表（pulls/{n}/files 的分页输出）
    python3 -I scripts/ci/docs_scope.py --event pull_request \
        --files-json pr-files.json --expected-files 1
    # merge_group：队列候选相对 base 的 git diff
    python3 -I scripts/ci/docs_scope.py --event merge_group \
        --base-sha <merge_group.base_sha> --head-sha <merge_group.head_sha>

为什么存在：发版当天补发行说明的纯文档 PR 以前要跑满 backend / platforms / e2e /
package / smoke 加合并队列（v0.19.2 两个，约 3 小时）。这个脚本只回答一个问题，
结论写进 `$GITHUB_OUTPUT` 的 `docs_only=true|false`，由 ci.yml 的 `docs-scope` job
产出；重型 job 据此短路，两个 Gate（`scripts/ci/aggregate_gate.py --classifier`）
据此**有据地**接受它们的 skipped。

「docs-only」的范围（只此一条，别扩到整个 docs/）：

* 路径在 `docs/release-notes/` 之下、是 `.md`、规范化后没有 `..` / 反斜杠 / 绝对路径；
* **不含 `UNRELEASED.md` 与 `unreleased/` 片段目录**：`tests/test_claude_plugin.py` /
  `test_dsh_bundle.py` / `test_install_commands_from_brand.py` 会读它们断言「待发说明里有
  安装那一行」，改它们可能让 backend 的测试红，不能跳过那些测试；
* 改名 / 移动：旧路径与新路径**都**要在范围内（把别处的文件挪进来也是改动）；
* 一个文件都没有 → 不是 docs-only（空 diff 不该拿到快车道）。
队列里多个 PR 组合成的候选：`base_sha..head_sha` 里**任一**文件在范围外就不是 docs-only。

fail-closed：

* PR 文件列表条数对不上 PR 声明的 `changed_files`（API 封顶 3000）→ 退出 2；
* git diff 失败 / base 或 head 缺失 → 退出 2。
退出 2 让 `docs-scope` job 红，两个 Gate 随之红（分类器自己坏了不能算通过）。
"不是 docs-only" 是正常结论，退出 0、`docs_only=false`，走完整 CI。

第二个输出 `e2e_specs_changed=true|false`（与 docs-only 无关的另一个问题）：这次改动有没有碰
**e2e 腿（windows-exe-smoke / posix-e2e）的输入**——`web/e2e/` 下任何文件、`web/playwright.config.ts`、
`web/tsconfig.e2e.json`（改名取新旧两条路径，删除也算）。为什么存在：重型腿平时在 PR 上整体
skipped、只在合并组里第一次跑；一个「只改 e2e 用例」的 risk:low PR 于是在 PR 阶段一次都没被
它自己改的那条腿验过（#1736，merge group run 41817554523 在 Windows webkit 上红了被踢）。
为 true 时 ci.yml 让这两条腿在 PR 上也跑，integration gate 据此把它们当必需（见 aggregate_gate.py
的 `--e2e-legs`）。与 docs_only 互不相交（发行说明不在 e2e 路径里）。

纯标准库。stdout 恒输出一行 JSON。
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SCOPE_PREFIX = "docs/release-notes/"
#: 范围内但被测试读取的文件：改它必须跑完整 CI。
EXCLUDED = frozenset({"docs/release-notes/UNRELEASED.md"})
#: 同上：待发片段目录（每个 PR 一个文件），与 UNRELEASED.md 同等对待。
EXCLUDED_PREFIXES = ("docs/release-notes/unreleased/",)
KNOWN_EVENTS = ("pull_request", "merge_group")

#: e2e 腿的输入：目录前缀与单文件。`web/playwright.perf.config.ts` 不在内——它只驱动 nightly 的
#: perf 用例，不进这两条腿（`web/playwright.config.ts` 的 testIgnore 把 perf 用例排除了）。
E2E_PREFIXES = ("web/e2e/",)
E2E_FILES = frozenset({"web/playwright.config.ts", "web/tsconfig.e2e.json"})


class ConfigError(Exception):
    """输入 / 环境错误——让分类 job 红，进而让 Gate 红。"""


def in_scope(path: str) -> bool:
    """单个路径是否属于 docs-only 范围。"""
    if not path or "\\" in path or "\x00" in path or path.startswith("/"):
        return False
    if posixpath.normpath(path) != path:  # 挡 `docs/release-notes/../../src/x.md`
        return False
    if not path.startswith(SCOPE_PREFIX) or not path.endswith(".md"):
        return False
    return path not in EXCLUDED and not path.startswith(EXCLUDED_PREFIXES)


def is_e2e_input(path: str) -> bool:
    """单个路径是否是 e2e 腿的输入（`web/e2e/` 下任何文件，或登记的两个配置文件）。"""
    # 不做 in_scope 那种规范化拒绝：这里判错的代价方向相反（漏判 = e2e 腿又在 PR 上白白跳过），宁可多跑。
    return path in E2E_FILES or path.startswith(E2E_PREFIXES)


def e2e_inputs(paths: list[str]) -> list[str]:
    return sorted({p for p in paths if is_e2e_input(p)})


def decide(paths: list[str]) -> dict:
    """paths = 本次改动涉及的全部路径（含改名前的旧路径）。纯函数。"""
    e2e = e2e_inputs(paths)
    extra = {"e2e_specs_changed": bool(e2e), "e2e_paths": e2e[:20]}
    if not paths:
        return {"docs_only": False, "reason": "no_changes", "outside": [], **extra}
    outside = sorted({p for p in paths if not in_scope(p)})
    if outside:
        return {
            "docs_only": False,
            "reason": "touches_outside_scope",
            "outside": outside[:20],
            **extra,
        }
    return {"docs_only": True, "reason": "release_notes_only", "outside": [], **extra}


def paths_from_pr_files(files: list[dict]) -> list[str]:
    out: list[str] = []
    for f in files:
        out.append(f["filename"])
        prev = f.get("previous_filename")
        if prev:
            out.append(prev)
    return out


def load_files(path: Path, expected: int | None) -> list[dict]:
    """读 `pulls/{n}/files` 的分页输出（单个数组、或多个数组首尾相接，都接受）。"""
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        raise ConfigError(f"files JSON 是空的（{path}）——取数那步多半失败了")
    dec = json.JSONDecoder()
    files: list = []
    idx = 0
    while idx < len(raw):
        try:
            obj, end = dec.raw_decode(raw, idx)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"files JSON 解析失败（{path}）：{exc}") from exc
        if not isinstance(obj, list):
            raise ConfigError("files JSON 的每一页必须是数组")
        files.extend(obj)
        idx = end
        while idx < len(raw) and raw[idx].isspace():
            idx += 1
    for f in files:
        if not isinstance(f, dict) or not isinstance(f.get("filename"), str):
            raise ConfigError("files JSON 里有条目缺 filename")
    if expected is None:
        raise ConfigError("pull_request 必须给 --expected-files：没有对账就不能信列表是全的")
    if len(files) != expected:
        raise ConfigError(
            f"取到 {len(files)} 个文件，PR 声明有 {expected} 个——列表不全，不能据此判定。"
        )
    return files


def paths_from_git_diff(base: str, head: str, repo: Path) -> list[str]:
    """`git diff --name-status -z --no-renames base head`：改名拆成 D 旧 + A 新，两条都进列表。"""
    proc = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-status", "-z", "--no-renames", base, head],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ConfigError(
            f"git diff {base}..{head} 失败（rc={proc.returncode}）："
            f"{proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    fields = proc.stdout.decode("utf-8", "surrogateescape").split("\x00")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 2:
        raise ConfigError("git diff -z 输出不是 状态/路径 成对")
    return [fields[i + 1] for i in range(0, len(fields), 2)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--event", required=True)
    ap.add_argument("--files-json", type=Path)
    ap.add_argument("--expected-files", type=int)
    ap.add_argument("--base-sha")
    ap.add_argument("--head-sha")
    ap.add_argument("--repo", type=Path, default=Path("."))
    args = ap.parse_args(argv)

    try:
        if args.event == "pull_request":
            if args.files_json is None:
                raise ConfigError("pull_request 事件必须给 --files-json")
            paths = paths_from_pr_files(load_files(args.files_json, args.expected_files))
        elif args.event == "merge_group":
            if not args.base_sha or not args.head_sha:
                raise ConfigError("merge_group 事件必须给 --base-sha 与 --head-sha")
            paths = paths_from_git_diff(args.base_sha, args.head_sha, args.repo)
        else:
            raise ConfigError(f"没设计过的事件：{args.event!r}（只认 {', '.join(KNOWN_EVENTS)}）")
        result = decide(paths)
        result["files"] = len(paths)
        code = 0
    except ConfigError as exc:
        result = {"docs_only": False, "reason": "config_error", "error": str(exc)}
        code = 2

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    out = os.environ.get("GITHUB_OUTPUT")
    if out and code == 0:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"docs_only={'true' if result['docs_only'] else 'false'}\n")
            fh.write(f"e2e_specs_changed={'true' if result['e2e_specs_changed'] else 'false'}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### docs-only 分类：{result['reason']}（docs_only={result['docs_only']}）\n")
            for p in result.get("outside", []):
                fh.write(f"- 范围外：`{p}`\n")
            if "e2e_specs_changed" in result:  # 配置错误的结果里没有（也不写 output）
                fh.write(f"### e2e 腿输入：e2e_specs_changed={result['e2e_specs_changed']}\n")
                for p in result.get("e2e_paths", []):
                    fh.write(f"- `{p}`\n")
    if code:
        print(f"::error::{result['error']}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
