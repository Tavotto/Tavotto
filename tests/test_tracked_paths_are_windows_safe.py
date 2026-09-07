"""入库路径必须在 Windows 上 checkout 得出来。

这条门禁是 2026-09-07 用一次真实事故换来的：`web/` 下混进了一个**文件名本身是
一段 TypeScript 源码**（含换行）的文件——某次 shell 重定向写歪了，`>` 的目标被
当成了代码片段。POSIX 上换行是合法的文件名字符，`git add` / `pytest` /
`pnpm test` 一路全绿；到 Windows 上 `git checkout` 直接退 128：

    error: invalid path 'web/            inTutorial
              ? undefined
              : { label: sg(...), onClick: () => void runTutorialEntry(...) }'

**红的不是某个用例，是 checkout 本身**——那台 runner 上什么都没跑起来。所以这
条判据必须在**平台无关的**那一层跑（backend-fast 就够），不能指望 Windows 腿：
它自己就是受害者。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Windows 文件名里非法的字符。控制字符（含换行）是这次事故的那一类；
#: `:` 只在盘符位置合法，`/` 是 git 的分隔符所以不在这里判。
_ILLEGAL = re.compile(r'[\x00-\x1f<>:"|?*\\]')

#: 设备名。它们**不带扩展名也算**（`CON.txt` 一样打不开），所以比的是 stem。
_RESERVED = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def _tracked_paths() -> list[bytes]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True
    ).stdout
    return [p for p in out.split(b"\0") if p]


def test_every_tracked_path_can_be_checked_out_on_windows() -> None:
    bad: list[str] = []
    for raw in _tracked_paths():
        # 先按字节判控制字符：解码之后换行还在，但报错信息会被它拆成好几行
        if any(c < 32 for c in raw):
            bad.append(f"{raw!r}：路径里有控制字符（Windows 上 checkout 会退 128）")
            continue
        path = raw.decode("utf-8", "surrogateescape")
        for part in path.split("/"):
            if _ILLEGAL.search(part):
                bad.append(f"{path}：分量 {part!r} 含 Windows 非法字符")
            if part != part.rstrip(" .") and part not in (".", ".."):
                bad.append(f"{path}：分量 {part!r} 以点或空格结尾（Windows 会悄悄截掉）")
            if part.split(".")[0].upper() in _RESERVED:
                bad.append(f"{path}：分量 {part!r} 是 Windows 设备名")
    assert not bad, "这些入库路径在 Windows 上 checkout 不出来：\n" + "\n".join(bad)
