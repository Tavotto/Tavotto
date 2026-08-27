#!/usr/bin/env python3
"""把 docs/support-matrix.json 渲染成 Release body 的「下载与支持平台」段。

Release 下载说明曾经每一版手写平台清单，而手写副本必然与
docs/support-matrix.json 漂移（issue #34：官网、Release、README、应用内必须
同一支持矩阵）。这里从唯一权威文件**生成**发布页那段英文：release.yml 在拼
release body 时追加，改支持范围只改矩阵一处。

纪律：
- 状态词（Supported / Beta / Not supported）由 status 字段派生，`en` 成文里
  只写渠道与出路——支持等级没有第二个出处；
- 认不出来的 status 直接抛错，绝不猜一个词把新档位「翻译」过去；
- 缺 label_en / en 的目标直接抛错——新加目标忘了写英文成文时，这条链在
  发布前就红，而不是发布页悄悄少一行。

用法：python scripts/make_release_support_section.py [--matrix PATH]
输出写到 stdout（release.yml 直接 `>> release-body.md`）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# 仓库地址的唯一出处是 brand 常量（AGENTS.md：别处不得手写）——仓库改名 /
# 转移 / fork 上跑发布链时，硬编码副本会继续指向老地址
from tavotto.engine import brand  # noqa: E402

DEFAULT_MATRIX = ROOT / "docs" / "support-matrix.json"

STATUS_EN = {
    "supported": "Supported",
    "beta": "Beta",
    "unsupported": "Not supported",
}


def render(matrix: dict) -> str:
    lines: list[str] = ["### Downloads and supported platforms", ""]
    for target in matrix["targets"]:
        status = target["status"]
        if status not in STATUS_EN:
            raise SystemExit(
                f"support-matrix: unknown status {status!r} on {target.get('id')!r}"
                " — teach make_release_support_section.py the new word first"
            )
        label_en = target.get("label_en")
        prose_en = target.get("en")
        if not label_en or not prose_en:
            raise SystemExit(
                f"support-matrix: target {target.get('id')!r} is missing"
                " label_en/en — the release page renders from the matrix,"
                " so English copy lives there too"
            )
        lines.append(f"- **{label_en}** — {STATUS_EN[status]}. {prose_en}")
    tested = matrix["python"]["tested"]
    lines += [
        "",
        f"PyPI install (all platforms): Python {tested[0]}–{tested[-1]}.",
        "",
        "The single source of truth for what is supported, beta, and"
        " unsupported is"
        f" [`docs/support-matrix.json`]({brand.REPO_URL}/blob/main/docs/support-matrix.json).",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    args = parser.parse_args()
    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    print(render(matrix), end="")


if __name__ == "__main__":
    main()
