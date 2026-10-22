"""卸载记号：Windows 上「卸载后重新安装」要被当成新手（ADR 0136 修订 2026-10-21）。

NSIS 卸载器（非 `/UPDATE`）在 Tavotto 配置目录写一个 `reinstall-marker`；卸载默认不删
`%LOCALAPPDATA%\\com.tavotto.tavotto\\EBWebView`（前端 localStorage 在里面）和配置目录，
所以重装后前端仍记得「教程已完成」。下次启动前端问一句「有没有记号」，有就把 onboarding
迁回新手态，再 ack 删除记号——两步而不是读完即删：前端没加载 / 请求中途失败时记号还在，下次接着来。

只在 Windows 生效；其它平台没有写入方，即使目录里有同名文件也不读。应用内更新（`/UPDATE`）
不写记号。内容只是时间戳，从不对外暴露（接口只回布尔）。
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import config_dir

MARKER_NAME = "reinstall-marker"


def _supported() -> bool:
    return os.name == "nt"


def marker_path() -> Path:
    return config_dir() / MARKER_NAME


def is_reinstall() -> bool:
    if not _supported():
        return False
    try:
        return marker_path().is_file()
    except OSError:
        return False


def consume() -> bool:
    """删除记号；返回删除前是否存在。删不掉（被占用 / 权限）当作没有，下次启动再来。"""
    if not _supported():
        return False
    try:
        marker_path().unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True
