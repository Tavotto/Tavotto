"""登记的入口过时：脚本被改写后，注册表里的 `entry` 函数已经不存在（TVD-CHR5）。

注册表随图库走，`entry` 是首次打开时静态起草登记的函数名；之后用户把脚本改成顶层直接画图（没有任何函数）时，
worker 的 `getattr(module, entry)()` 只能撞上 `AttributeError`。**登记的入口必须对照当前源码**，这个判据与重判
只在这一个模块里：

* `is_stale(path, entry)`：入口是函数名（不是 `__main__`），源码能解析，而且顶层**没有**这个名字（def / async def /
  class / 赋值 / import 进来的同名都算「还在」；`from x import *` 或模块级 `__getattr__` 说不准，也当「还在」）。
  源码读不动 / 解析不了 = 判不出 = 不过时（维持原行为，运行期的报错更有解释力）。
* `reconcile(figures_dir, script)`：过时就按 `probe.entry_candidates(static_only=True)`（注册表草稿与试运行同一把尺）
  重判入口，只把这一条的 `entry` 一个字段改写进注册表（经 `discover.write_config`，唯一的注册表写入函数；
  stems / cost / notes / artifacts 以及手写的其它键原样保留），返回 `{script: 新入口}`。
* `diagnose(path, entry)`：只读，给 AI 修复的运行报告用。

所有「用登记入口去跑」的消费点（app 的取 worker / 准备目标 / 准备会话规划 / AI 修复）都经 `reconcile`，不各写一份判据。
**刷新项目 / 静态合并不碰它**：「现有条目永远优先」不变（`tests/test_project_refresh.py`），只有真要用这个入口去跑时才对照源码。
纯标准库，Flask 父进程可安全 import。
"""

from __future__ import annotations

import ast
import json
import threading
from pathlib import Path

from . import discover, inputremap, probe, projectenv, registry

#: 说不准顶层有没有这个名字（`import *` / 模块级 `__getattr__`）：当「还在」
_UNKNOWN = None

_cache_lock = threading.Lock()
#: realpath -> ((mtime_ns, size), 顶层名字集合 | None(说不准) | False(解析不了))
_names_cache: dict[str, tuple[tuple[int, int], object]] = {}
_CACHE_MAX = 512


class _TopLevelNames(ast.NodeVisitor):
    """模块**顶层作用域**绑定的名字（不进函数 / 类 / lambda 体）。多收（推导式目标之类）只会让「还在」更宽，是安全方向。"""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.unknown = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)
        if node.name == "__getattr__":
            self.unknown = True

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.names.add(alias.asname or alias.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                self.unknown = True
            else:
                self.names.add(alias.asname or alias.name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.names.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.names.add(node.rest)
        self.generic_visit(node)


#: 出现即说不准：这些调用 / 属性可以在运行时往模块命名空间里装名字（`globals()['render'] = …`、`globals().update`、
#: `vars()[…]`、`exec` / `eval`、`setattr(sys.modules[__name__], …)`、`__dict__` 写入）。整份源码里出现就算
#: （函数体里写、顶层调用它也生效），宁可不改写也不把「运行时才有的入口」当成已删除。
_DYNAMIC_NAMES = frozenset({"globals", "vars", "locals", "exec", "eval", "setattr", "compile"})
_DYNAMIC_ATTRS = frozenset({"__dict__", "modules"})


def _writes_namespace_dynamically(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _DYNAMIC_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _DYNAMIC_ATTRS:
            return True
    return False


def top_level_names(tree: ast.Module) -> set[str] | None:
    """顶层名字集合；说不准（`import *` / 模块级 `__getattr__`）回 None。"""
    if _writes_namespace_dynamically(tree):
        return None
    visitor = _TopLevelNames()
    for stmt in tree.body:
        visitor.visit(stmt)
    return None if visitor.unknown else visitor.names


def _names_of(path: Path) -> object:
    """`path` 的顶层名字集合（按 mtime+size 缓存）；None = 说不准；False = 读不动 / 解析不了。"""
    key = str(path)
    try:
        st = path.stat()
    except OSError:
        return False
    stamp = (st.st_mtime_ns, st.st_size)
    with _cache_lock:
        hit = _names_cache.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
    text, problem = discover.read_source(path)
    tree = discover.parse_source(text)[0] if problem is None and text is not None else None
    value: object = False if tree is None else top_level_names(tree)
    with _cache_lock:
        if len(_names_cache) >= _CACHE_MAX:
            _names_cache.clear()
        _names_cache[key] = (stamp, value)
    return value


def is_stale(path: str | Path, entry: object) -> bool:
    """登记的 `entry` 在 `path` 的当前源码里已经不存在？只有「能解析、且顶层确实没有这个名字」才是 True。"""
    if not isinstance(entry, str) or entry == registry.INLINE_ENTRY or not entry.isidentifier():
        return False
    names = _names_of(Path(path))
    if names is False or names is _UNKNOWN:
        return False
    return entry not in names  # type: ignore[operator]


def _script_path(figures_dir: str | Path, script: str) -> str | None:
    return projectenv.contained_path(figures_dir, script)


def redetect(figures_dir: str | Path, script: str) -> str | None:
    """按现有入口候选（`probe.entry_candidates`，只认能静态解析出的）重判；判不出回 None。"""
    candidates = probe.entry_candidates(figures_dir, script, static_only=True)
    return candidates[0] if candidates else None


def diagnose(figures_dir: str | Path, script: str, entry: object) -> dict | None:
    """只读：登记的入口过时就回 `{"registered": 旧入口, "suggested": 重判结果}`，否则 None。"""
    path = _script_path(figures_dir, script)
    if path is None or not is_stale(path, entry):
        return None
    return {"registered": str(entry), "suggested": redetect(figures_dir, script)}


def reconcile(figures_dir: str | Path, script: str | None = None) -> dict[str, str]:
    """把注册表里过时的入口换成重判结果（只改 `entry` 一个字段）；`script=None` = 全部脚本。

    返回 `{script: 新入口}`，没有改动回 `{}`。读 / 写失败一律当没有改动（不抛）：这是运行前的纠偏，
    纠不成就维持原行为，由运行期的失败码说话。整段读改写在项目锁里（与 `discover.register` 同一把）。
    """
    base = registry.existing_registry_path(figures_dir)
    if base is None:
        return {}
    try:
        quick = json.loads(base.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    scripts = quick.get("scripts") if isinstance(quick, dict) else None
    if not isinstance(scripts, dict):
        return {}
    names = [script] if script is not None else list(scripts)
    # 无锁的快速判：绝大多数调用什么都不用改，不去抢项目锁
    if not any(
        isinstance(scripts.get(n), dict)
        and (p := _script_path(figures_dir, n)) is not None
        and is_stale(p, scripts[n].get("entry", "main"))
        for n in names
    ):
        return {}
    changed: dict[str, str] = {}
    with inputremap.project_mutex(figures_dir):
        path = registry.existing_registry_path(figures_dir)
        try:
            cfg = json.loads(path.read_text(encoding="utf-8")) if path else {}
        except (OSError, ValueError):
            return {}
        current = cfg.get("scripts") if isinstance(cfg, dict) else None
        if not isinstance(current, dict):
            return {}
        for name in names:
            cfg_entry = current.get(name)
            real = _script_path(figures_dir, name)
            if not isinstance(cfg_entry, dict) or real is None:
                continue
            if not is_stale(real, cfg_entry.get("entry", "main")):
                continue
            new = redetect(figures_dir, name)
            if new is None or not registry.valid_entry(new):
                continue
            cfg_entry["entry"] = new
            changed[name] = new
        if changed:
            try:
                discover.write_config(figures_dir, cfg)
            except (OSError, RuntimeError):
                return {}
    return changed


def effective_entry(figures_dir: str | Path, script: str, entry: str | None) -> str | None:
    """准备会话规划用的入口：`entry` 就是注册表登记的那个、而它已过时 -> 纠正后的入口；其余（用户/调用方显式给的、
    没过时、判不出）原样返回。会话的 `target` 在脚本被改写之前就定了入口，之后每次重新规划都经这里对照一次当前源码。"""
    path = _script_path(figures_dir, script) if entry else None
    if path is None or not is_stale(path, entry):
        return entry
    base = registry.existing_registry_path(figures_dir)
    try:
        scripts = json.loads(base.read_text(encoding="utf-8")).get("scripts") if base else None
    except (OSError, ValueError, AttributeError):
        return entry
    registered = scripts.get(script) if isinstance(scripts, dict) else None
    if not isinstance(registered, dict) or registered.get("entry", "main") != entry:
        return entry
    return reconcile(figures_dir, script).get(script, entry)
