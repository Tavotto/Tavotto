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
import re
import subprocess
import sys
import threading
import time
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
#: 找到的解释器按项目记住；**找不到的不记死**——隔 `RETRY_S` 再探：装好 matplotlib / 换了解释器之后
#: 不重启也要能恢复（Codex #1497）
_picked: dict[str, tuple[str, bool]] = {}
_missed: dict[str, float] = {}
RETRY_S = 30.0


#: 查找键与排版模型同一个（`typography.math_key`）
key_of = math_key


def family_of(name: object) -> str:
    """任意取值 → 三族之一（与 `typography.latin_family` 同一条合同）。"""
    return name if name in FAMILIES else FAMILIES[0]


#: 图内文字的公式用「图上那张脸」排：`face:<族名>`（ADR 0152 后续）。族名只是名字、不是路径——
#: 由子进程经 matplotlib `font_manager` 解析；字符集收窄，免得名字里的 `:` `,` `=` 污染 fontconfig 模式
FACE_PREFIX = "face:"
_FACE_NAME = re.compile(r"[\w .()+&'-]{1,80}")


def face_of(name: object) -> str | None:
    """`face:<族名>` 且族名合法 → 原样返回；其余 None。"""
    if not isinstance(name, str) or not name.startswith(FACE_PREFIX):
        return None
    return name if _FACE_NAME.fullmatch(name[len(FACE_PREFIX) :].strip()) else None


def request_family(name: object) -> str:
    """画布预览端点（`/api/mathtext`）的族：三族之一或合法的 `face:`；其余落到 serif。

    导出与标注只走 `family_of`（严格三族）——`face:` 只属于图内文字的预览，RenderCore 不认。"""
    if isinstance(name, str) and face_of(name):
        return name.strip()
    return family_of(name)


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
            # GUI（Flask 进程）拥有的子进程：Windows 上不弹控制台窗口
            creationflags=runtime.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0


def _pick_python(figures_dir: str | Path | None = None) -> tuple[str, bool] | None:
    """排版用哪个解释器（每一条都要 import 得到 matplotlib）：

    1. 内置 runtime——排版结果最可复现（同一份 matplotlib、同一套 STIX）；
    2. 图渲染用的那一个：`pool.resolve_worker_python(项目)`——环境变量 `TAVOTTO_WORKER_PYTHON`、设置、
       项目记住的 / 项目自带的环境都在这条决策里，与图同一个出处（Codex #1497：只读全局设置会漏掉它们，
       图画得出来、公式却一直 `no_runtime`）；没有项目时用 `select_worker_python()` 的全局链条；
    3. 源码模式的当前解释器。
    """
    bundled = runtime.bundled_python()
    candidates: list[tuple[str | None, bool]] = [(bundled, True)]
    try:
        from . import pool

        python, _source = (
            pool.resolve_worker_python(figures_dir) if figures_dir else pool.select_worker_python()
        )
        candidates.append((python, bool(bundled) and pool.same_python(python, bundled)))
    except Exception:  # noqa: BLE001 —— 显式指定的解释器不可用等：图那边会自己报，这里只是少一条候选
        pass
    if not runtime.is_frozen():
        candidates.append((sys.executable, False))
    seen: set[str] = set()
    for python, is_bundled in candidates:
        if not python or python in seen:
            continue
        seen.add(python)
        if _has_matplotlib(python, bundled=is_bundled):
            return python, is_bundled
    return None


def _python_for(figures_dir: str | Path | None) -> tuple[str, bool] | None:
    key = str(figures_dir or "")
    with _lock:
        hit = _picked.get(key)
        if hit and Path(hit[0]).exists():
            return hit
        missed = _missed.get(key)
        if missed is not None and time.monotonic() - missed < RETRY_S:
            return None
    picked = _pick_python(figures_dir)
    with _lock:
        if picked is None:
            _missed[key] = time.monotonic()
        else:
            _picked[key] = picked
            _missed.pop(key, None)
    return picked


def _run_child(items: list[tuple[str, str]], figures_dir: str | Path | None = None) -> list[dict]:
    picked = _python_for(figures_dir)
    if picked is None:
        return [{"ok": False, "error": NO_RUNTIME} for _ in items]
    python, bundled = picked
    req = {"fonts": _fonts(), "items": [{"src": s, "family": f} for s, f in items]}
    try:
        p = subprocess.run(
            [python, *runtime.probe_args(bundled=bundled), str(CHILD)],
            input=json.dumps(req),
            env=runtime.probe_env(python, bundled=bundled),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # 子进程的 stderr 可能是本机代码页（cp936 等）；结果只读 stdout 的 JSON
            timeout=TIMEOUT_S,
            creationflags=runtime.CREATE_NO_WINDOW,
        )
        results = json.loads(p.stdout)["results"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        return [{"ok": False, "error": f"child_failed: {type(exc).__name__}"} for _ in items]
    if len(results) != len(items):
        return [{"ok": False, "error": "child_failed: count"} for _ in items]
    return results


def typeset_many(
    items: list[tuple[str, str]], figures_dir: str | Path | None = None
) -> dict[str, dict]:
    """[(源码, 族)] → {键: 结果}。结果是 `mathtext_child` 的形状（em 单位、y 向上），或 `{"ok": False, "error"}`。"""
    out: dict[str, dict] = {}
    todo: list[tuple[str, str]] = []
    for src, fam in items:
        fam = request_family(fam)
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
        for (src, fam), res in zip(todo, _run_child(todo, figures_dir)):
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


def layouts_for(objects: list[dict], figures_dir: str | Path | None = None) -> dict[str, dict]:
    """导出用：这批对象里公式的排版结果，交给 `plan.compile_page(math=…)`。没有公式就不起子进程。"""
    items = math_items(objects)
    return typeset_many(items, figures_dir) if items else {}
