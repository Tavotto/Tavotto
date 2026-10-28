"""影子脚本登记（ADR 0143 修订）：校验运行不往用户项目里写任何文件。

生成可编辑版本的校验要“在原脚本旁真跑一遍候选”，以前的做法是把候选写成紧挨原脚本的临时文件——坚果云 / Dropbox / OneDrive
这类同步盘会把它同步出去，用户看见项目里冒出文件，同步客户端自己的临时 / 冲突文件还会让“项目没被改动”的复核误判。

现在候选字节留在候选目录（`data_dir()/cache/ai_convert/<sid>/`），项目里**没有**这个文件。本模块只管一件事：
把“项目里将来的位置（显示路径，`figures_dir/rel`）”映射到“字节所在的真实文件”，由 `execspec.safe_spec` 取出放进
`ExecutionSpec.script_source` → worker 的 `--script-source`：worker 读 `source` 的字节、以显示路径为 `__file__`
（`os.path.dirname(__file__)` 读数据、traceback 文件名、`sys.argv[0]` 都与采用之后一致），cwd 仍是原脚本所在目录。

键里带 `write_roots_key`（候选目录）：同一个显示路径的两次并发转换各是各的，也不会串到别的会话。
只读静态分析（依赖调研 / 工作目录证据 / 入口推断）**只在那次校验运行自己的上下文里**才读影子：`shadow()` 同时在
当前线程 / 任务的 `contextvars` 上标出“这次校验”，`lookup_active` 只认它；别的线程里的普通会话（用户同时打开 / 重跑原脚本）
对同一路径的分析永远读磁盘原件，不会被候选污染。没有“全局恰好登记一份就优先读”的隐式查找。
纯标准库；登记只在 `with shadow(...)` 期间有效，出作用域即摘。
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import threading
from pathlib import Path

_LOCK = threading.Lock()
_ENTRIES: dict[tuple[str, str, str], str] = {}
#: 当前上下文里正在进行的那次校验（`(figures_dir 规范键, rel 规范键, roots_key)`）；线程 / 任务各自一份，不跨会话
_ACTIVE: contextvars.ContextVar[tuple[str, str, str] | None] = contextvars.ContextVar(
    "tavotto_shadow_active", default=None
)


def _norm_dir(path) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def _norm_rel(rel) -> str:
    """相对路径的规范键：登记与查询**共用这一个函数**。Windows 上 `normcase` 小写化并把 `/` 换成 `\\`，
    所以先 normcase 再统一成 `/`——大小写 / 分隔符写法不同的同一个路径得到同一个键。"""
    return os.path.normcase(os.fspath(rel)).replace("\\", "/")


def _key(figures_dir, rel, roots_key: str) -> tuple[str, str, str]:
    return (_norm_dir(figures_dir), _norm_rel(Path(rel).as_posix()), roots_key)


@contextlib.contextmanager
def shadow(figures_dir, rel: str, source, roots_key: str):
    """登记 `figures_dir/rel` 的影子脚本字节来自 `source`，并在当前上下文标出“这次校验”；出作用域摘掉。"""
    key = _key(figures_dir, rel, roots_key)
    with _LOCK:
        _ENTRIES[key] = os.fspath(source)
    token = _ACTIVE.set(key)
    try:
        yield
    finally:
        _ACTIVE.reset(token)
        with _LOCK:
            _ENTRIES.pop(key, None)


def lookup(figures_dir, rel: str, roots_key: str) -> str | None:
    """这个（项目, 显示路径, 写入根）有没有影子脚本；有回字节所在的真实路径。"""
    if not roots_key:
        return None
    with _LOCK:
        return _ENTRIES.get(_key(figures_dir, rel, roots_key))


def lookup_active(figures_dir, script) -> str | None:
    """只读静态分析（依赖调研 / 工作目录证据）用：**当前上下文正在进行的那次校验**登记的就是这个（项目, 路径）时回字节
    所在的真实路径；否则（没有校验在进行 / 别的路径 / 别的线程里的普通会话）回 None——只分析磁盘原件。
    `script` 可以是项目相对路径，也可以是（已 realpath 的）项目内绝对路径。"""
    active = _ACTIVE.get()
    if active is None:
        return None
    try:
        root = _norm_dir(figures_dir)
        real = os.path.realpath(os.path.join(os.fspath(figures_dir), os.fspath(script)))
        rel = _norm_rel(
            os.path.relpath(real, os.path.realpath(os.fspath(figures_dir))).replace(os.sep, "/")
        )
    except (OSError, ValueError):
        return None
    if active[0] != root or active[1] != rel:
        return None
    with _LOCK:
        return _ENTRIES.get(active)
