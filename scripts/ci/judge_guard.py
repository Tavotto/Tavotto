#!/usr/bin/env python3
"""判据路径分类器：这次改动有没有碰「评判 PR 的尺子」（`.github/judge-paths.json`）。

    python3 -I scripts/ci/judge_guard.py --paths-file changed.txt --config judge-paths.json

为什么存在：改动不能修改评判它自己的检查。两个 Gate 的判定器、docs-scope / release-note /
CLA 判定已经取自默认分支；但 workflow 自身、树内看护用例与棘轮、Guardian 夹具、根
`AGENTS.md` 的 Review guidelines 都跟着 PR 走，没法从结构上堵死。这里能做的是**看见**：
改到名单里的路径就在 CI 上标出来，入队前交维护者确认（规则全文
`docs/rules/ci/judge-guard.md`）。

* 名单与本脚本都由 ci.yml 从默认分支取（bootstrap 空窗退回本 revision 副本）。
* `mode`：`off` 什么都不说；`observe` 打 notice + step summary，**永远退出 0**。
  未知的 mode 按 observe 处理并 warning——这一步不许因为配置问题让任何 PR 红。
* 名单条目：以 `/` 结尾 = 目录前缀；以 `*` 结尾 = 同一目录下的文件名前缀（不跨目录）；否则 = 精确
  路径。不支持别的通配，免得一条宽模式悄悄吞掉半个仓库。

纯标准库。stdout 恒输出一行 JSON。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

KNOWN_MODES = ("off", "observe")


class ConfigError(Exception):
    """名单读不出来 / 格式不对。"""


def load_config(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"读不了判据名单 {path}：{exc}") from exc
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("判据名单缺 entries 或为空")
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("path"), str) or not e["path"]:
            raise ConfigError(f"判据名单条目缺 path：{e!r}")
    return data


def matches(entry: str, path: str) -> bool:
    if entry.endswith("/"):
        return path.startswith(entry)
    if entry.endswith("*"):
        stem = entry[:-1]
        return path.startswith(stem) and "/" not in path[len(stem) :]
    return path == entry


def decide(paths: list[str], config: dict) -> dict:
    """paths = 本次改动涉及的全部路径（含改名前的旧路径）。纯函数。"""
    mode = config.get("mode", "observe")
    hits: dict[str, list[str]] = {}
    for p in sorted(set(paths)):
        for e in config["entries"]:
            if matches(e["path"], p):
                hits.setdefault(e["path"], []).append(p)
    return {
        "mode": mode if mode in KNOWN_MODES else "observe",
        "unknown_mode": None if mode in KNOWN_MODES else mode,
        "touched": bool(hits),
        "hits": hits,
        "files": len(set(paths)),
    }


def read_paths(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"读不了改动文件列表 {path}：{exc}") from exc
    return [line for line in text.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--paths-file", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    args = ap.parse_args(argv)

    try:
        result = decide(read_paths(args.paths_file), load_config(args.config))
    except ConfigError as exc:
        # observe 档：配置坏了只 warning，不让任何 PR 因为这一步红。
        result = {"mode": "error", "touched": False, "error": str(exc)}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        print(f"::warning title=判据路径检测没跑成::{exc}", file=sys.stderr)
        return 0

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["unknown_mode"]:
        print(
            f"::warning title=判据名单 mode 未知::{result['unknown_mode']!r}，按 observe 处理",
            file=sys.stderr,
        )
    if result["mode"] == "off" or not result["touched"]:
        return 0

    listed = "、".join(sorted(result["hits"]))
    print(
        "::notice title=这次改了判据路径::"
        f"{listed}——入队前交维护者确认（docs/rules/ci/judge-guard.md）",
        file=sys.stderr,
    )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### 这次改了判据路径（observe，不判红）\n")
            fh.write("入队前交维护者确认，见 `docs/rules/ci/judge-guard.md`。\n\n")
            for entry, files in sorted(result["hits"].items()):
                shown = ", ".join(f"`{f}`" for f in files[:10])
                more = f" 等 {len(files)} 个" if len(files) > 10 else ""
                fh.write(f"- `{entry}`：{shown}{more}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
