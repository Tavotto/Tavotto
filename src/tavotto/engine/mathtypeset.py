"""标注里的公式排版（ADR 0152 PR 4）：`$…$` → matplotlib mathtext 排出的矢量路径，按内容缓存。

**为什么在子进程**：Flask 父进程与 RenderCore 的闭包里没有 matplotlib（`pyproject` 的依赖、ADR 0072）。
公式由一个带 matplotlib 的解释器跑独立脚本 `mathtext_child.py` 排出来——优先内置 runtime（桌面版），
源码模式退到当前解释器，再退到设置里指定的 worker 解释器。**排出来的东西只有数字**（em 单位的路径与盒子），
画布预览（`/api/mathtext`）与导出（`plan.compile_page` 的 `math=`）读同一份结果，所以两边逐点相同。

缓存：`data_dir()/cache/mathtext/<sha256>.json`（键 = 版本 + 族 + 源码），外加进程内一份。排不出来的
（语法错、没有可用解释器）也缓存——同一个错不必每次重跑；但「没有解释器」不落盘（装好之后要能好）。

纯标准库。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from pathlib import Path

from .. import richtext
from ..rendercore.typography import math_key
from . import config, runtime

#: 结果格式 / 字体配方变了就加一：旧缓存全部作废
CACHE_VERSION = 1
CHILD = Path(__file__).with_name("mathtext_child.py")
FAMILIES = ("serif", "sans-serif", "monospace")
_FONT_FILES = {
    "serif": "LiberationSerif",
    "sans-serif": "LiberationSans",
    "monospace": "LiberationMono",
}
_STYLES = ("Regular", "Italic", "Bold", "BoldItalic")
TIMEOUT_S = 60.0
#: 没有可用解释器时的错误码（界面与问题条目按它说「装好内置环境后可用」）
NO_RUNTIME = "no_runtime"

_lock = threading.Lock()
_memory: dict[str, dict] = {}
_python: list[str | None] = []  # 探过一次就记住（[] = 还没探）


#: 查找键与排版模型同一个（`typography.math_key`）
key_of = math_key


def family_of(name: object) -> str:
    """任意取值 → 三族之一（与 `typography.latin_family` 同一条合同）。"""
    return name if name in FAMILIES else FAMILIES[0]


def _cache_path(key: str) -> Path:
    digest = hashlib.sha256(f"{CACHE_VERSION}\x00{key}".encode()).hexdigest()
    return config.data_dir() / "cache" / "mathtext" / f"{digest}.json"


def _fonts() -> dict[str, list[str]]:
    from ..rendercore import fonts

    root = fonts.fonts_dir() / "liberation"
    return {
        fam: [str(root / f"{stem}-{s}.ttf") for s in _STYLES] for fam, stem in _FONT_FILES.items()
    }


def _has_matplotlib(python: str, *, bundled: bool) -> bool:
    try:
        p = subprocess.run(
            [python, *runtime.probe_args(bundled=bundled), "-c", "import matplotlib"],
            env=runtime.probe_env(python, bundled=bundled),
            capture_output=True,
            timeout=TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0


def _pick_python() -> tuple[str, bool] | None:
    """排版用哪个解释器：内置 runtime → 源码模式的当前解释器 → 设置里的 worker 解释器。"""
    bundled = runtime.bundled_python()
    candidates: list[tuple[str | None, bool]] = [(bundled, True)]
    if not runtime.is_frozen():
        candidates.append((sys.executable, False))
    candidates.append((config.worker_python(), False))
    for python, is_bundled in candidates:
        if python and _has_matplotlib(python, bundled=is_bundled):
            return python, is_bundled
    return None


def _run_child(items: list[tuple[str, str]]) -> list[dict]:
    with _lock:
        if not _python:
            picked = _pick_python()
            _python.append(None if picked is None else picked[0])
            _python.append("1" if picked and picked[1] else "")
        python, bundled = _python[0], bool(_python[1])
    if python is None:
        return [{"ok": False, "error": NO_RUNTIME} for _ in items]
    req = {"fonts": _fonts(), "items": [{"src": s, "family": f} for s, f in items]}
    try:
        p = subprocess.run(
            [python, *runtime.probe_args(bundled=bundled), str(CHILD)],
            input=json.dumps(req),
            env=runtime.probe_env(python, bundled=bundled),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT_S,
        )
        results = json.loads(p.stdout)["results"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        return [{"ok": False, "error": f"child_failed: {type(exc).__name__}"} for _ in items]
    if len(results) != len(items):
        return [{"ok": False, "error": "child_failed: count"} for _ in items]
    return results


def typeset_many(items: list[tuple[str, str]]) -> dict[str, dict]:
    """[(源码, 族)] → {键: 结果}。结果是 `mathtext_child` 的形状（em 单位、y 向上），或 `{"ok": False, "error"}`。"""
    out: dict[str, dict] = {}
    todo: list[tuple[str, str]] = []
    for src, fam in items:
        fam = family_of(fam)
        key = key_of(src, fam)
        if key in out:
            continue
        hit = _memory.get(key)
        if hit is None:
            path = _cache_path(key)
            try:
                hit = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                hit = None
        if hit is not None:
            _memory[key] = hit
            out[key] = hit
        else:
            todo.append((src, fam))
            out[key] = {}
    if todo:
        for (src, fam), res in zip(todo, _run_child(todo)):
            key = key_of(src, fam)
            out[key] = res
            if res.get("error") == NO_RUNTIME or str(res.get("error", "")).startswith(
                "child_failed"
            ):
                continue  # 环境问题不缓存：装好之后要能好
            _memory[key] = res
            path = _cache_path(key)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(res), encoding="utf-8")
                tmp.replace(path)
            except OSError:
                pass  # 缓存写不进去不影响这一次的结果
    return out


def math_items(objects: list[dict]) -> list[tuple[str, str]]:
    """画布对象（导出载荷的形状）里所有 LaTeX 写法标注的公式段 → [(源码, 族)]。"""
    items: list[tuple[str, str]] = []
    for o in objects:
        if not isinstance(o, dict) or o.get("type") != "text" or o.get("hidden"):
            continue
        if o.get("text_format") != "latex":
            continue
        fam = family_of(o.get("font_family"))
        for run in richtext.parse_runs(str(o.get("text") or ""), "latex"):
            if run.math:
                items.append((run.text, fam))
    return items


def layouts_for(objects: list[dict]) -> dict[str, dict]:
    """导出用：这批对象里公式的排版结果，交给 `plan.compile_page(math=…)`。没有公式就不起子进程。"""
    items = math_items(objects)
    return typeset_many(items) if items else {}
