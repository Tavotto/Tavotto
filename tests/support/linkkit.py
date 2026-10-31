"""造符号链接 / 别名的夹具 helper——非管理员 Windows 上建不了符号链接（WinError 1314）。

hosted `windows-latest` 是管理员，建得出来；用户机器和 Windows Lab（非管理员 `tavotto-lab`）建不出来。
所以：

* 场景只需要「同一 inode 的第二个目录项」时用 `hardlink`——NTFS 上不要特权，用例照样真跑；
* 场景离不开符号链接本身（比如「产品必须跳过符号链接」）时用 `symlink_or_skip`：**只**在
  WinError 1314（客户端没有所需特权）时 skip 并写明原因，其余错误（含 POSIX 的任何 OSError）原样抛出，
  不许把真故障吞成 skip。
"""

import os
from pathlib import Path

import pytest

#: ERROR_PRIVILEGE_NOT_HELD：`os.symlink` 在没有 SeCreateSymbolicLinkPrivilege 且未开开发者模式时抛它。
WIN_ERROR_PRIVILEGE_NOT_HELD = 1314


def is_privilege_error(exc: BaseException) -> bool:
    """`winerror` 只在 Windows 的 OSError 上存在；其它平台恒为 False。"""
    return getattr(exc, "winerror", None) == WIN_ERROR_PRIVILEGE_NOT_HELD


def symlink_or_skip(target, link, *, is_dir: bool = False) -> None:
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except OSError as exc:
        if not is_privilege_error(exc):
            raise
        pytest.skip("本账户没有创建符号链接的特权（WinError 1314）；这条场景离不开符号链接本身")


def hardlink(target: Path, link: Path) -> None:
    """第二个目录项指向同一个 inode；不需要特权。"""
    os.link(target, link)
