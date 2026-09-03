#!/usr/bin/env python3
"""发布闸：`docs/release-notes/UNRELEASED.md` 里的待发条目必须先并进这一版。

存在的理由（issue #244）：#215 修好了标注旋转的导出方向，存量文档里手工补偿
过角度的用户升级后会拿到反向的导出。这条迁移提示当时写在 PR 正文的「遗留」段
里——发行说明是发版那天写的，写的人不会回头翻每一个 PR 正文，于是它没有进
任何一版，用户升级后一个字也看不到。同一段里的另外两条遗留都建了 issue，
只有需要**告诉用户**的这条什么都没留下。

「记得在发版时写一句」不是纪律，是漏掉的方式。所以待发条目落在
`UNRELEASED.md`，由这个脚本在发布链上变成一道会红的闸：`release.yml` 的
「拼 release body」在读手写正文之前先跑它，带着没并入的段落打 tag 当场失败。

判据只数 `## ` 段落，不看行数也不看字数：**并入 = 把段落搬进
`docs/release-notes/<tag>.md` 并从暂存文件里删掉**，说明性的注释留在原处，
所以「还有没有待发条目」与「文件是不是空的」不是一回事。

用法：`python scripts/check_pending_release_notes.py [--pending PATH] [--tag TAG]`
待发条目为空时静默退出 0；还有条目时把每一段的标题打出来并退出 1。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PENDING = ROOT / "docs" / "release-notes" / "UNRELEASED.md"


def pending_sections(text: str) -> list[str]:
    """待发条目的标题行（`## ` 开头）。

    只认行首的 `## `：注释块与正文里的其它标记不参与判定，段落被搬走之后
    文件里剩下的说明文字不会把这道闸一直钉红。
    """
    return [line.strip() for line in text.splitlines() if line.startswith("## ")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pending", type=Path, default=DEFAULT_PENDING)
    ap.add_argument("--tag", default=None, help="这一版的 tag，只用于错误信息")
    args = ap.parse_args(argv)

    if not args.pending.exists():
        return 0
    sections = pending_sections(args.pending.read_text(encoding="utf-8"))
    if not sections:
        return 0

    target = f"docs/release-notes/{args.tag}.md" if args.tag else "docs/release-notes/<tag>.md"
    rel = args.pending.relative_to(ROOT) if args.pending.is_relative_to(ROOT) else args.pending
    print(f"{rel} 里还有 {len(sections)} 条待发条目没有并进 {target}：", file=sys.stderr)
    for s in sections:
        print(f"  {s}", file=sys.stderr)
    print(
        "把它们搬进这一版的发行说明再打 tag——留在这里等于用户升级后一个字也看不到。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
