"""把一个项目文件夹另存为一份可写的副本（Tavotto 对原文件夹没有写入权限时的出口）。

**只做最小的安全版本**：把目录树里的普通文件原样复制到用户选的父文件夹下的新目录里，
再由调用方打开那份副本。纯标准库（Flask 父进程的 import 链）。

* **只复制内容，不复制权限位**：源文件夹正是因为只读才走到这里，`shutil.copytree` /
  `copy2` 会把只读的目录与文件模式一并带过去，副本一出生就还是只读的。这里目录用默认模式新建，
  文件只拷内容（`shutil.copyfile`），副本天然可写。
* **体量护栏**：先走一遍目录树数文件数与字节数，超过 `MAX_FILES` / `MAX_BYTES` 就在写入第一个字节
  之前拒绝（`project_copy_too_large`）。大数据文件夹、同步盘、整个主目录这类选错的项目由此挡在门外；
  不做后台进度条与断点续传——需要时是单独的功能。
* **符号链接与特殊文件不跟随、不复制**：跟随会把项目外的东西（或循环）拷进来。
* **失败不留残缺目录**：复制中途失败（磁盘满 / 权限）就清掉已建的副本再报错。
* 读不了的单个文件跳过并计数（`skipped`），不让一个坏文件拖垮整次另存。

项目级设置（导出目录 / 解释器选择等）按路径存在用户配置里，**不随副本走**——副本是一个新项目。
"""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

#: 总字节数上限（2 GiB）与文件数上限：科研项目文件夹（脚本 + 数据 + 导出）远小于此；超了多半是选错了文件夹。
MAX_BYTES = 2 * 1024**3
MAX_FILES = 50_000

#: 不复制的目录名 / 文件名（解释器缓存与系统噪音；副本里用不上）。
_SKIP_NAMES = frozenset({"__pycache__", ".DS_Store", "Thumbs.db"})

CODE_TOO_LARGE = "project_copy_too_large"
CODE_DEST_INVALID = "project_copy_dest_invalid"
CODE_DEST_UNWRITABLE = "project_copy_dest_unwritable"
CODE_FAILED = "project_copy_failed"
#: 给 `tests/test_error_codes.py` 门禁读的注册表（它按字面量扫源码，常量式的 code 看不见）。
ERROR_CODES = (CODE_TOO_LARGE, CODE_DEST_INVALID, CODE_DEST_UNWRITABLE, CODE_FAILED)


class CopyError(Exception):
    """可翻译的另存失败：稳定 `code` + 结构化 `params`（文案归前端）。"""

    def __init__(self, code: str, message: str, params: dict | None = None):
        super().__init__(message)
        self.code = code
        self.params = params or {}


@dataclass(frozen=True)
class CopyResult:
    path: Path
    files: int
    bytes: int
    skipped: int


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _walk(root: Path):
    """目录树里要复制的 (相对目录, 条目) ；不跟随符号链接。读不了的子目录静默略过（由复制阶段计 skipped）。"""
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            if entry.name in _SKIP_NAMES:
                continue
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                    yield cur, entry, True
                elif entry.is_file(follow_symlinks=False):
                    yield cur, entry, False
            except OSError:
                continue


def measure(src: Path) -> tuple[int, int]:
    """`(文件数, 总字节数)`；一旦超限立刻抛 `project_copy_too_large`（不把大目录走完）。"""
    files = total = 0
    for _cur, entry, is_dir in _walk(src):
        if is_dir:
            continue
        files += 1
        try:
            total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        if files > MAX_FILES or total > MAX_BYTES:
            raise CopyError(
                CODE_TOO_LARGE,
                "项目文件夹太大，没有另存",
                {"limit_mb": MAX_BYTES // 1024**2},
            )
    return files, total


def _reserve_destination(parent: Path, name: str) -> Path:
    """原子地占下 `parent/<name>`（重名依次试 `<name> 2` …）：`mkdir` 本身就是占位，
    `FileExistsError` = 被别的请求 / 进程先占了，换下一个名字。先 `exists()` 再 `mkdir()` 有缝，
    败者会把胜者刚存好的项目当成自己的半成品清掉。只有这里**成功创建**的目录才归本次调用清理。"""
    for n in range(1, 100):
        candidate = parent / (name if n == 1 else f"{name} {n}")
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise CopyError(CODE_DEST_INVALID, "目标位置已有太多同名文件夹", {"name": name})


def _copy_file(src: str, target: Path) -> None:
    """复制内容，并保留源的执行位、补上属主可读写：`copyfile` 只给普通非可执行权限（0555 的辅助脚本会变成
    不可执行），而源因为只读才走到这里，原样拷权限位则副本也只读。chmod 失败（Windows 等）忽略。"""
    shutil.copyfile(src, target)
    try:
        mode = os.stat(src).st_mode & 0o777
        os.chmod(target, mode | stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def copy_project(src: Path, parent: Path) -> CopyResult:
    """把 `src` 复制到 `parent/<src 的名字>`（重名则加序号），返回副本路径。"""
    src = src.resolve()
    parent = Path(parent).expanduser()
    if not parent.is_dir():
        raise CopyError(CODE_DEST_INVALID, "目标不是一个文件夹", {})
    parent = parent.resolve()
    # 父文件夹落在项目里面 / 就是项目本身：副本会被复制进自己的源头
    if parent == src or _is_inside(parent, src):
        raise CopyError(CODE_DEST_INVALID, "目标在项目文件夹里面", {})
    if not os.access(parent, os.W_OK | os.X_OK):
        raise CopyError(CODE_DEST_UNWRITABLE, "目标文件夹不可写", {})
    files, total = measure(src)
    dest = _reserve_destination(parent, src.name or "project")  # 此后 dest 是本次调用创建的
    copied = skipped = 0
    try:
        # 先建全部目录再拷文件：_walk 对目录先于其内容产出
        for cur, entry, is_dir in _walk(src):
            target = dest / Path(entry.path).relative_to(src)
            if is_dir:
                target.mkdir(exist_ok=True)
                continue
            try:
                _copy_file(entry.path, target)
                copied += 1
            except PermissionError:
                skipped += 1  # 单个文件读不了：跳过，不拖垮整次另存
            except FileNotFoundError:
                skipped += 1  # 复制期间被外部删了
    except CopyError:
        _discard(dest)
        raise
    except OSError as exc:
        _discard(dest)
        raise CopyError(CODE_FAILED, f"另存失败: {exc}", {"reason": str(exc)}) from exc
    return CopyResult(dest, copied, total, skipped)


def _discard(dest: Path) -> None:
    """清掉没复制完的副本；清不掉就留着（不吞更早的那个错误，也不让清理失败盖过它）。"""
    try:
        shutil.rmtree(dest)
    except OSError:  # FileNotFoundError 是它的子类：没建出来就没什么要清
        pass
