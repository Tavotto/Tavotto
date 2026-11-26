"""文档目录的派生出入口：命名文档、自动保存、版本时间线共用同一套路径。

**单一存储。** schema 3 与 schema 4 的文档落在同一组目录（项目 `tavottofile/`、
未打开项目时的数据目录 `layouts/`），出现在同一个列表、共用同一条版本历史，
只由文档正文里的 schema 号区分——这里没有格式参数、没有第二套子目录。来源附件
（`documentassets`）同样以这里派生的目录为根，路径只由摘要派生。

调用方传现有权威目录（`project_layout_dir()` / 数据目录 `layouts/`），不得自己
拼路径。每个自有目录组件都拒绝符号链接 / 目录联接（包括仍指向同一项目内部的重
定向）：上级允许系统级路径别名，根本身及其下每层都核对。
"""

from __future__ import annotations

import stat
from pathlib import Path

from . import documents


def _directory(path: Path, *, create: bool) -> None:
    """目录守卫：不是目录、`is_symlink()`、`resolve()` 与父目录 + 名字对不上，三者任一即拒。"""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        if not create:
            return
        path.mkdir(parents=True, exist_ok=True)
        mode = path.lstat().st_mode
    if (
        not stat.S_ISDIR(mode)
        or path.is_symlink()
        or path.resolve() != path.parent.resolve() / path.name
    ):
        raise documents.DocumentError("layout_outside_project", "文档目录不能通过链接重定向")


def _owned(base: Path, parts: tuple[str, ...], *, create: bool) -> Path:
    requested = Path(base).expanduser().absolute()
    if ".." in requested.parts:
        raise documents.DocumentError("layout_outside_project", "无效的文档目录")
    # 上级是调用方的权威目录；允许系统级路径别名，根本身及其下每层均核对。
    path = requested.parent.resolve() / requested.name
    _directory(path, create=create)
    for part in parts:
        path /= part
        _directory(path, create=create)
    return path


def named_dir(project_layout_dir: Path, *, create: bool = False) -> Path:
    """命名文档目录：项目 `tavottofile/`；未打开项目时数据目录 `layouts/`。"""
    return _owned(project_layout_dir, (), create=create)


def autosave_dir(layout_dir: Path, *, create: bool = False) -> Path:
    """自动保存槽位目录：`<布局根>/_autosave`。"""
    return _owned(layout_dir, (documents.AUTOSAVE_DIRNAME,), create=create)


def versions_dir(
    project_store: Path | None,
    legacy_versions_dir: Path,
    *,
    create: bool = False,
) -> Path:
    """版本时间线目录：项目 `tavottofile/versions/`；未打开项目时 `layouts/_versions/`。"""
    if project_store is not None:
        return _owned(project_store, ("versions",), create=create)
    legacy = Path(legacy_versions_dir).expanduser().absolute()
    return _owned(legacy.parent, (legacy.name,), create=create)
