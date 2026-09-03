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

**三个方向都要钉住，缺一条就是空门禁：**

* 文件在、还有 `## ` 段落 → 红（没并入就不给发）；
* 文件在、段落已并入 → 绿（剩下的说明注释不算条目）；
* **文件不在 / 读不出来 → 红**。这一条最容易漏：把「找不到」读成「已迁移」，
  闸就在它唯一该拦的时刻放行——`UNRELEASED.md` 被删掉，连同里面那条迁移
  提示一起消失，而发布链全绿。判据在，但它要读的东西不在时它不红，
  这正是本仓库反复清理的那个家族。

用法：`python scripts/check_pending_release_notes.py [--pending PATH] [--tag TAG]`
待发条目为空时静默退出 0；其余情况把原因打到 stderr 并退出 1。
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

    rel = args.pending.relative_to(ROOT) if args.pending.is_relative_to(ROOT) else args.pending

    # 报错文案也是断言：读不到时要说清缺的是什么，而不是甩一个栈出来——
    # 发布链上看到栈的人第一反应是「脚本坏了」，然后把这一步拿掉。
    try:
        text = args.pending.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(
            f"找不到 {rel} —— 待发条目的暂存文件不该消失。"
            "它被删掉的话，里面那条还没发出去的迁移提示会跟着一起消失，"
            "而这道闸永远不会再红。请恢复它（没有待发条目时只留说明注释）。",
            file=sys.stderr,
        )
        return 1
    except (OSError, UnicodeDecodeError) as exc:
        print(
            f"读不了 {rel}（{type(exc).__name__}: {exc}）—— 读不出来就不算已并入。", file=sys.stderr
        )
        return 1

    sections = pending_sections(text)
    if not sections:
        return 0

    target = f"docs/release-notes/{args.tag}.md" if args.tag else "docs/release-notes/<tag>.md"
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
