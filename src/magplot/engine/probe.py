"""试运行探测：按脚本**真实产出**的文件名建立 stem↔script 映射。

静态扫描（engine/discover.py）能解开绝大多数写法，但 stem 来自运行期数据的
脚本（遍历数据目录、读配置、命令行参数……）静态永远解不出来。那类脚本以前
只能手工登记 mm_registry.json——用户根本不知道要登记什么。

这里换个思路：worker 本来就在 build 阶段拦截 `Figure.savefig` / `paper_style.save`
并按**真实文件名**捕获 Figure（不写盘），所以把脚本跑一遍就能拿到权威的 stem
列表。跑得起来的脚本 = 能参数化的脚本，不再靠猜。

代价是要真的执行脚本（冷启动秒级到分钟级），所以只在用户主动触发时做，
绝不在打开项目时静默全跑。

纯标准库，Flask 父进程 import。
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import discover, pool, registry

LOG = logging.getLogger("mm.probe")

# entry 猜错的表现是 AttributeError / 脚本不执行，换一个再试即可。
# 顺序：静态推断的 > 常见入口名 > 内联脚本。
FALLBACK_ENTRIES = ("main", "render", registry.INLINE_ENTRY)


def entry_candidates(figures_dir: str | Path, script: str) -> list[str]:
    """该脚本值得一试的 entry 列表（静态推断优先，去重保序）。"""
    out: list[str] = []
    path = Path(figures_dir) / script
    info = discover.analyze_script(path, Path(figures_dir))
    if info:
        out.append(info["entry"])
    for e in FALLBACK_ENTRIES:
        if e not in out:
            out.append(e)
    return out


def probe(figures_dir: str | Path, script: str,
          entries: list[str] | None = None) -> dict:
    """跑一次脚本，返回它真实产出的 stem。

    返回 {"script", "entry", "stems", "error", "tried"}；每个候选 entry 都
    失败时 error 是**第一个**候选的报错（静态推断的那个，对用户最有解释力），
    而不是最后一个兜底候选的。
    """
    figures_dir = str(Path(figures_dir))
    script_path = Path(figures_dir) / script
    if not script_path.is_file():
        return {"script": script, "entry": None, "stems": [],
                "error": f"脚本不存在: {script}", "tried": []}

    tried: list[str] = []
    first_error: str | None = None
    for entry in (entries or entry_candidates(figures_dir, script)):
        tried.append(entry)
        # 每次换 entry 都要换掉旧会话：worker 的 entry 是启动参数，
        # 复用旧进程等于一直用错的入口重试。
        pool.invalidate(script, figures_dir)
        try:
            worker = pool.get(script, figures_dir, entry)
            resp = worker.ensure_built()
        except pool.WorkerError as exc:
            LOG.info("探测失败 %s [entry=%s]: %s", script, entry, exc)
            if first_error is None:
                first_error = _short(str(exc), exc.traceback_text)
            pool.invalidate(script, figures_dir)
            continue
        stems = sorted(resp.get("stems") or {})
        if stems:
            LOG.info("探测成功 %s [entry=%s] → %s", script, entry, stems)
            return {"script": script, "entry": entry, "stems": stems,
                    "error": None, "tried": tried}
        # 跑通了但一张图都没产出：这个 entry 大概率不是出图入口，换下一个
        if first_error is None:
            first_error = "脚本跑通了，但没有捕获到任何 Figure（入口可能不出图）"
        pool.invalidate(script, figures_dir)

    return {"script": script, "entry": None, "stems": [],
            "error": first_error or "无法确定入口", "tried": tried}


def _short(message: str, traceback_text: str = "", limit: int = 600) -> str:
    """给用户看的错误：优先 traceback 末尾几行（真正的异常在那儿）。"""
    tail = ""
    if traceback_text:
        lines = [ln for ln in traceback_text.strip().splitlines() if ln.strip()]
        tail = "\n".join(lines[-6:])
    text = f"{message}\n{tail}".strip() if tail else message
    return text[:limit]


def probe_and_register(figures_dir: str | Path, script: str,
                       cost: str = "medium") -> dict:
    """探测成功就写进 mm_registry.json 并重载注册表（失败原样返回，不写盘）。"""
    result = probe(figures_dir, script)
    if not result["stems"]:
        return {**result, "registered": False}
    discover.register(figures_dir, script, result["stems"],
                      entry=result["entry"], cost=cost)
    registry.load(figures_dir)
    return {**result, "registered": True}
