#!/usr/bin/env python3
"""发行说明闸：用户可见的 PR 必须在 `docs/release-notes/UNRELEASED.md` 里新增待发条目。

    python3 scripts/ci/release_note_gate.py \
        --event pull_request \
        --pr-author someone \
        --labels-json '["bug"]' \
        --files-json pr-files.json \
        [--expected-files 12]

为什么要有这道闸：发行说明以前是发版当天回头补的（v0.19.2 补了两个纯文档 PR，
每个都跑满整条 CI 加合并队列，前后约 3 小时）。约定早就有——用户可见的变更在
PR 里往 UNRELEASED.md 加一个 `## ` 段落（见 docs/RELEASING.md）——但没有任何东西
执行它。这里把约定变成合并资格：它经 `CI fast gate` 参与合并，判定逻辑写成
纯函数（`decide()`），不散在 workflow 的 Bash 里，才能单测。

判定规则（详见 `decide()`）：

* `--event merge_group`：判 `not_applicable` 并**成功**。理由：这条判据只回答
  「这个 PR 有没有写待发条目」，输入是 PR 的文件列表与标签，队列候选上两样都
  没有（候选是「最新 main + 队列前序 PR + 当前 PR」的组合提交，payload 里没有
  pull_request.labels）；PR 阶段已经验过，组合提交上再验只会把前序 PR 的改动
  算到当前 PR 头上。**这一格必须是成功而不是 skipped**——`aggregate_gate.py
  --mode fast` 把 skipped 一律当失败。
* `--event pull_request`：
  1. PR 作者是 `dependabot[bot]`（精确匹配登录名，不是「带 [bot] 后缀」）→ 豁免：
     依赖升级的用户可见影响不由 PR 作者描述，真有影响由发版人写。
  2. 带 `no-release-note` 标签 → 豁免（内部 PR：CI、重构、测试、仅内部文档）。
  3. 没改任何「用户可见路径」→ 通过（不需要条目）。
  4. 新增了 `docs/release-notes/vX.Y.Z.md` → 通过（这是发版 PR 本身：它把待发条目
     搬进正式说明，同时会改 `__version__`，不能要求它再新增一条待发条目）。
  5. 否则 UNRELEASED.md 的 diff 里必须**新增至少一行 `## `**（二级标题）。
     只改了顶部注释、只删段落、只改错别字，都不算——约定是「加一个段落」。
     已知边界：不解析 HTML 注释，把 `## ` 写进注释里也会被数到；这是人审该
     挡的事，不值得为它在脚本里写一个 Markdown 解析器。
* 其他事件：配置错误 → 失败。没设计过的上下文不许静默变绿。

用户可见路径（`USER_VISIBLE_PREFIXES`）与理由：

* `src/tavotto/`        引擎 / 服务 / 内置资源，用户装的 wheel 就是它；
* `web/src/`            前端界面；
* `src-tauri/src/`      桌面壳（窗口、更新、安装/卸载行为）；
* `src-tauri/tauri.conf.json`  桌面壳的打包与更新配置；
* `codex-plugin/`       随版本发给用户的 Codex 插件（含 skills 文案）。
不算（`is_test_or_internal_path`）：

* 测试：`test`/`tests`/`testing`/`__tests__`/`__fixtures__`/`__mocks__` 目录
  （已核实：`web/src/test/`，其余三处根目录下没有测试子目录），
  `*.test.*` / `*.spec.*` / `test_*.py` / `*_test.py` / `*_tests.rs` / `conftest.py`；
* 开发者文档：任何位置的 `AGENTS.md`（速查表，不随产品分发）。
`packaging/`、`workerd/`、`.github/`、`scripts/`、`docs/`、`tests/` 不在清单内：
它们要么是构建/发布管线，要么是文档与测试；真改变了用户行为（比如打包内容）
的，由作者主动写条目，或评审要求补——这里宁可漏判一个、不去错拦一堆内部 PR。

fail-closed 的几处：

* 文件列表条数与 PR 声明的 `changed_files` 对不上（`--expected-files`，
  `pulls/{n}/files` 封顶 3000）→ 配置错误，不拿不完整的列表判通过；
* UNRELEASED.md 被改了但 API 没给 patch（diff 太大被省略）→ 配置错误。

退出码：0 = success / exempt / not_applicable；1 = 缺条目；2 = 配置 / 输入错误
（同样让 Gate 红——判定器自己坏了不能算通过）。

stdout 恒输出一行机器可读 JSON；`$GITHUB_STEP_SUMMARY` 存在时另写
人类可读的 Markdown。纯标准库。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath

# Windows 上 stdout 被捕获时退回系统区域编码，中文输出会 UnicodeEncodeError
# （写法与 cla_gate.py 同源）。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

KNOWN_EVENTS = ("pull_request", "merge_group")

EXEMPT_LABEL = "no-release-note"
#: 精确登录名，不做「像机器人就放行」。
EXEMPT_AUTHORS = ("dependabot[bot]",)

UNRELEASED_PATH = "docs/release-notes/UNRELEASED.md"
#: 正式发行说明：`docs/release-notes/v0.19.2.md`（含 -beta 之类后缀）。
RELEASE_NOTE_RE = re.compile(r"^docs/release-notes/v\d+\.\d+\.\d+[^/]*\.md$")

USER_VISIBLE_PREFIXES = (
    "src/tavotto/",
    "web/src/",
    "src-tauri/src/",
    "src-tauri/tauri.conf.json",
    "codex-plugin/",
)

TEST_DIR_NAMES = frozenset({"test", "tests", "testing", "__tests__", "__fixtures__", "__mocks__"})
TEST_FILE_GLOBS = (
    "*.test.*",
    "*.spec.*",
    "test_*.py",
    "*_test.py",
    "*_tests.rs",
    "conftest.py",
)
#: 开发者文档，不随产品分发。
INTERNAL_DOC_NAMES = frozenset({"AGENTS.md"})

FAIL_MESSAGE = (
    "这个 PR 改了用户可见的代码（{examples}），却没往 {unreleased} 新增 `## ` 待发条目。"
    "补一个 `## ` 段落（按症状和触发条件写，见 docs/RELEASING.md）；"
    "如果这是纯内部改动，给 PR 打 `{label}` 标签。"
)


class ConfigError(Exception):
    """输入 / 配置错误。它也让 Gate 失败：判定器自己坏了不能算通过。"""


def is_test_or_internal_path(path: str) -> bool:
    p = PurePosixPath(path)
    if p.name in INTERNAL_DOC_NAMES:
        return True
    if any(part in TEST_DIR_NAMES for part in p.parts[:-1]):
        return True
    return any(fnmatch.fnmatchcase(p.name, g) for g in TEST_FILE_GLOBS)


def is_user_visible(path: str) -> bool:
    if not any(path == pre or path.startswith(pre) for pre in USER_VISIBLE_PREFIXES):
        return False
    return not is_test_or_internal_path(path)


def _touched_paths(entry: dict) -> list[str]:
    """一个文件条目涉及的路径：改名要把旧路径也算上（把用户可见文件挪出去也是改动）。"""
    paths = [entry["filename"]]
    prev = entry.get("previous_filename")
    if prev:
        paths.append(prev)
    return paths


def adds_section_heading(patch: str) -> bool:
    """diff 是否**净增**了 `## ` 二级标题（`###` 不算）：新增行数 > 删除行数。

    只数 `+## ` 不够——改正一个已有标题（`-## Fix plot lables` / `+## Fix plot labels`）
    也会出现一行 `+## `，但没有新条目。
    """
    lines = patch.splitlines()
    added = sum(1 for line in lines if line.startswith("+## "))
    removed = sum(1 for line in lines if line.startswith("-## "))
    return added > removed


def decide(
    event: str,
    files: list[dict],
    labels: list[str],
    pr_author: str = "",
) -> dict:
    """返回 {"status": ..., "message": ...}。status ∈ success / exempt /
    not_applicable / failure。纯函数：不读环境、不碰文件系统。"""
    if event == "merge_group":
        return {
            "status": "not_applicable",
            "message": "合并队列候选：资格在 PR 阶段已验过，这里直接放行（必须是成功，不是 skipped）。",
        }
    if event != "pull_request":
        raise ConfigError(f"没设计过的事件：{event!r}（只认 {', '.join(KNOWN_EVENTS)}）")

    if pr_author in EXEMPT_AUTHORS:
        return {"status": "exempt", "message": f"{pr_author} 的 PR 默认豁免。"}
    if EXEMPT_LABEL in labels:
        return {"status": "exempt", "message": f"带 `{EXEMPT_LABEL}` 标签，豁免。"}

    visible = sorted(
        {p for f in files for p in _touched_paths(f) if is_user_visible(p)},
    )
    if not visible:
        return {"status": "success", "message": "没有改动用户可见路径，不需要待发条目。"}

    if any(f.get("status") == "added" and RELEASE_NOTE_RE.match(f["filename"]) for f in files):
        return {"status": "success", "message": "新增了正式发行说明（发版 PR），不再要求待发条目。"}

    for f in files:
        if f["filename"] != UNRELEASED_PATH:
            continue
        patch = f.get("patch")
        if patch is None:
            raise ConfigError(
                f"{UNRELEASED_PATH} 有改动但 API 没给 diff（太大被省略？）——无法核对。"
            )
        if adds_section_heading(patch):
            return {"status": "success", "message": f"{UNRELEASED_PATH} 新增了 `## ` 条目。"}
        break

    examples = "、".join(visible[:3]) + (" 等" if len(visible) > 3 else "")
    return {
        "status": "failure",
        "message": FAIL_MESSAGE.format(
            examples=examples, unreleased=UNRELEASED_PATH, label=EXEMPT_LABEL
        ),
    }


def load_files(path: Path, expected: int | None) -> list[dict]:
    """读 `pulls/{n}/files` 的分页输出（单个数组、或多个数组首尾相接，都接受）。"""
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        raise ConfigError(f"files JSON 是空的（{path}）——取数那步多半失败了")
    docs: list = []
    dec = json.JSONDecoder()
    idx = 0
    while idx < len(raw):
        try:
            obj, end = dec.raw_decode(raw, idx)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"files JSON 解析失败（{path}）：{exc}") from exc
        docs.append(obj)
        idx = end
        while idx < len(raw) and raw[idx].isspace():
            idx += 1
    files: list = []
    for d in docs:
        if not isinstance(d, list):
            raise ConfigError("files JSON 的每一页必须是数组")
        files.extend(d)
    for f in files:
        if not isinstance(f, dict) or not isinstance(f.get("filename"), str):
            raise ConfigError("files JSON 里有条目缺 filename")
    if expected is not None and len(files) != expected:
        raise ConfigError(
            f"取到 {len(files)} 个文件，PR 声明有 {expected} 个——列表不全，不能据此判定。"
        )
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--event", required=True)
    ap.add_argument("--pr-author", default="")
    ap.add_argument("--labels-json", default="[]", help="标签名的 JSON 数组")
    ap.add_argument("--files-json", type=Path)
    ap.add_argument("--expected-files", type=int)
    args = ap.parse_args(argv)

    try:
        files: list[dict] = []
        if args.event == "pull_request":
            if args.files_json is None:
                raise ConfigError("pull_request 事件必须给 --files-json")
            files = load_files(args.files_json, args.expected_files)
        try:
            labels = json.loads(args.labels_json)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"--labels-json 不是合法 JSON：{exc}") from exc
        if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
            raise ConfigError("--labels-json 必须是字符串数组")
        result = decide(args.event, files, labels, args.pr_author)
    except ConfigError as exc:
        result = {"status": "config_error", "message": str(exc)}
        code = 2
    else:
        code = 1 if result["status"] == "failure" else 0

    print(json.dumps(result, ensure_ascii=False))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### 发行说明闸：{result['status']}\n\n{result['message']}\n")
    if code:
        print(f"::error::{result['message']}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
