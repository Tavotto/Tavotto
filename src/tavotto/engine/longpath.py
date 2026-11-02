"""Windows 长路径（> 260 字符）安全的文件操作入口。

`LongPathsEnabled=0` 的 Windows 上，普通路径超过 `MAX_PATH` 会 `WinError 206`。
Tavotto 自己建的候选工作区（`data_dir()/cache/ai_convert/<sid>/project_context/…`）
会把项目里的相对路径原样接在数据目录后面，项目目录深就超长。

用法：只在 Tavotto 自己的文件操作（mkdir / 读写 / 遍历 / 删除）里用 `long()` 包一层；
交给外部程序的路径（Codex 的 `-C`、提示词里的路径）保持普通形式，不带 `\\\\?\\`。
非 Windows 原样返回，所以可以无条件调用。
"""

from __future__ import annotations

import errno
import ntpath
import os
from pathlib import Path

_PREFIX = "\\\\?\\"
_UNC_PREFIX = "\\\\?\\UNC\\"
_WINERROR_TOO_LONG = 206  # ERROR_FILENAME_EXCED_RANGE
_TOO_LONG_ERRNO = frozenset({errno.ENAMETOOLONG})


def extended(path: str | os.PathLike[str], *, windows: bool | None = None) -> str:
    """把绝对路径转成 `\\\\?\\` 扩展长度形式；非 Windows、相对路径、已带前缀、设备路径原样返回。"""
    text = os.fspath(path)
    if windows is None:
        windows = os.name == "nt"
    if not windows or text.startswith(_PREFIX) or text.startswith("\\\\.\\"):
        return text
    drive, _ = ntpath.splitdrive(text)
    if not drive or not ntpath.isabs(text):
        return text  # 相对路径或无盘符的根相对路径：加前缀会改变含义
    norm = ntpath.normpath(text)  # 扩展形式不再由系统规整 `/`、`..`，这里先规整
    if norm.startswith("\\\\"):  # UNC：\\server\share\x → \\?\UNC\server\share\x
        return _UNC_PREFIX + norm[2:]
    return _PREFIX + norm


def long(path: str | os.PathLike[str]) -> Path:
    """`extended()` 的 Path 版：对 Tavotto 自己的 mkdir / 读写 / os.walk / rmtree 用。"""
    return Path(extended(path))


def is_too_long(exc: BaseException) -> bool:
    """`exc` 是不是「路径太长」：Windows `WinError 206` 或 POSIX `ENAMETOOLONG`。"""
    if not isinstance(exc, OSError):
        return False
    return getattr(exc, "winerror", None) == _WINERROR_TOO_LONG or exc.errno in _TOO_LONG_ERRNO
