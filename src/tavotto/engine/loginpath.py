"""macOS 桌面版的 PATH：补上终端（login shell）会有、Finder / Dock 启动的 GUI 进程没有的系统目录。

从 Finder / Dock 启动的进程继承的是 GUI 的最小 PATH（`/usr/bin:/bin:/usr/sbin:/sbin`），没有
`/Library/TeX/texbin`（MacTeX）、`/opt/homebrew/bin` 等；终端里的 PATH 由 `/usr/libexec/path_helper` 按
`/etc/paths` 与 `/etc/paths.d/*`（按文件名排序、每行一个目录）拼出。这里**只读那两处文件**、不执行
`path_helper`、不起 shell：继承的 PATH 原样在前，文件里列出的、真实存在且还没在 PATH 里的目录按
文件顺序追加在末尾（不改既有顺序、不去重既有项；用户自己设置的完整 PATH 因此不会被打乱）。非 macOS 不变。

桌面 sidecar 启动时调一次 `apply_to_environ()`（`desktop.run`）：之后渲染 worker、探测（`_has_matplotlib`）、
用户脚本自己 `subprocess` 调的命令、AI CLI 的 `spawn_env` 全都继承同一份，「像在终端里那样跑」
（ADR 0107）只有这一个出处。装完 TeX 之后要重启 Tavotto 才读到新装的 `/etc/paths.d/TeX`。

纯标准库（Flask 父进程侧）。
"""

from __future__ import annotations

import os
import sys

_ETC = "/etc"


def _listed_dirs(root: str) -> list[str]:
    """`<root>/paths` 再 `<root>/paths.d/*`（文件名排序）里列出的目录，保持出现顺序；读不了的文件跳过。"""
    files = [os.path.join(root, "paths")]
    d = os.path.join(root, "paths.d")
    try:
        files += [
            p for p in (os.path.join(d, n) for n in sorted(os.listdir(d))) if os.path.isfile(p)
        ]
    except OSError:
        pass
    out: list[str] = []
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def augmented_path(
    current: str | None, *, etc_root: str | None = None, platform: str | None = None
) -> str | None:
    """`current`（继承的 PATH）+ 系统路径文件里列出、存在且尚未出现的目录；没有要补的 / 非 macOS → 原样返回。"""
    if (platform or sys.platform) != "darwin":
        return current
    parts = [p for p in (current or "").split(os.pathsep) if p]
    seen = set(parts)
    added = False
    for d in _listed_dirs(etc_root or _ETC):
        if d not in seen and os.path.isdir(d):
            parts.append(d)
            seen.add(d)
            added = True
    return os.pathsep.join(parts) if added else current


def apply_to_environ(
    environ=None, *, etc_root: str | None = None, platform: str | None = None
) -> bool:
    """把补好的 PATH 写回 `environ`（默认 `os.environ`）；→ 是否改了。"""
    env = os.environ if environ is None else environ
    cur = env.get("PATH")
    new = augmented_path(cur, etc_root=etc_root, platform=platform)
    if new is None or new == cur:
        return False
    env["PATH"] = new
    return True
