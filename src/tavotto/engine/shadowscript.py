"""影子脚本登记（ADR 0143 修订）：校验运行不往用户项目里写任何文件。

生成可编辑版本的校验要“在原脚本旁真跑一遍候选”，以前的做法是把候选写成紧挨原脚本的临时文件——坚果云 / Dropbox / OneDrive
这类同步盘会把它同步出去，用户看见项目里冒出文件，同步客户端自己的临时 / 冲突文件还会让“项目没被改动”的复核误判。

现在候选字节留在候选目录（`data_dir()/cache/ai_convert/<sid>/`），项目里**没有**这个文件。本模块只管一件事：
把“项目里将来的位置（显示路径，`figures_dir/rel`）”映射到“字节所在的真实文件”，由 `execspec.safe_spec` 取出放进
`ExecutionSpec.script_source` → worker 的 `--script-source`：worker 读 `source` 的字节、以显示路径为 `__file__`
（`os.path.dirname(__file__)` 读数据、traceback 文件名、`sys.argv[0]` 都与采用之后一致），cwd 仍是原脚本所在目录。

键里带 `write_roots_key`（候选目录）：同一个显示路径的两次并发转换各是各的，也不会串到别的会话。
纯标准库；登记只在 `with shadow(...)` 期间有效，出作用域即摘。
"""

from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path

_LOCK = threading.Lock()
_ENTRIES: dict[tuple[str, str, str], str] = {}


def _key(figures_dir, rel, roots_key: str) -> tuple[str, str, str]:
    return (
        os.path.normcase(os.path.realpath(os.fspath(figures_dir))),
        Path(rel).as_posix(),
        roots_key,
    )


@contextlib.contextmanager
def shadow(figures_dir, rel: str, source, roots_key: str):
    """登记 `figures_dir/rel` 的影子脚本字节来自 `source`；出作用域摘掉。"""
    key = _key(figures_dir, rel, roots_key)
    with _LOCK:
        _ENTRIES[key] = os.fspath(source)
    try:
        yield
    finally:
        with _LOCK:
            _ENTRIES.pop(key, None)


def lookup(figures_dir, rel: str, roots_key: str) -> str | None:
    """这个（项目, 显示路径, 写入根）有没有影子脚本；有回字节所在的真实路径。"""
    if not roots_key:
        return None
    with _LOCK:
        return _ENTRIES.get(_key(figures_dir, rel, roots_key))


def lookup_any(figures_dir, script) -> str | None:
    """只知道（项目, 路径）、不知道写入根的只读静态分析（依赖调研 / 工作目录证据）用：恰好登记了一份才回，
    没有或有歧义（同一显示路径两次并发转换）回 None——宁可当“文件不存在”，也不拿别人的字节判。
    `script` 可以是项目相对路径，也可以是（已 realpath 的）项目内绝对路径。"""
    try:
        root = os.path.normcase(os.path.realpath(os.fspath(figures_dir)))
        real = os.path.normcase(os.path.realpath(os.path.join(root, os.fspath(script))))
        rel = Path(os.path.relpath(real, root)).as_posix()
    except (OSError, ValueError):
        return None
    with _LOCK:
        hits = {src for (d, r, _k), src in _ENTRIES.items() if d == root and r == rel}
    if len(hits) != 1:
        return None
    return next(iter(hits))
