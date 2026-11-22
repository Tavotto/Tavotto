"""科学可视化指导条目（`profiles/sciviz_guidance.json`）的只读加载与渲染。

条目是唯一出处（docs/sciviz/README.md）：Tavotto Agent、数据出图准则、插件技能都从这里取，
不各写一套。预检判据仍以 `profiles/publication.json` 为准，条目的 `checks` 只是指向。

`render()` 的输出是**确定的**：同一份 JSON、同一组参数，逐字节相同——它会进模型提示词与缓存键，
文本只应随条目本身的修改而变。每条只渲染 id + 一句 `agent` 准则，不带书目与引文（省 token）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

GUIDANCE_FILE = "sciviz_guidance.json"

#: 闭集（`tests/test_sciviz_guidance.py` 钉住每条条目只取这些值）
BASES = ("encoding", "statistics", "visual", "undecidable", "taste")
STRENGTHS = ("firm", "strong", "preference", "inference")
DECIDABLE = ("auto", "context", "no")
TAGS = ("原文", "二手", "推导")

#: 渲染时每类的小标题（给模型看的语气提示，taste 一类明确「不是对错」）
_BASIS_HEADINGS = {
    "encoding": "数据与编码必须一致（违反即错图）",
    "statistics": "统计表达（依赖上下文，说明而不替用户定）",
    "visual": "感知建议（指导性原则，有例外）",
    "undecidable": "只能由作者确认（不猜）",
    "taste": "作者偏好（不是对错：按用户或期刊，不要当问题报告）",
}


def guidance_path() -> Path:
    """包内那条走 `importlib.resources`（装成 wheel 后源码树相对路径不存在），开发态兜底同 `profiles.profiles_path`。"""
    try:
        from importlib.resources import files

        cand = Path(str(files("tavotto").joinpath("profiles", GUIDANCE_FILE)))
        if cand.is_file():
            return cand
    except (ImportError, ModuleNotFoundError, TypeError, OSError):
        pass
    return Path(__file__).resolve().parent.parent / "profiles" / GUIDANCE_FILE


@lru_cache(maxsize=1)
def load() -> dict:
    """整份文档（只读；调用方不得改返回值）。"""
    return json.loads(guidance_path().read_text(encoding="utf-8"))


def entries() -> list[dict]:
    return list(load()["entries"])


def entry(entry_id: str) -> dict:
    for e in load()["entries"]:
        if e["id"] == entry_id:
            return e
    raise KeyError(entry_id)


def render(ids: list[str] | None = None, bases: tuple[str, ...] | None = None) -> str:
    """给模型看的准则文本。`ids` 取子集（未知 id 抛 KeyError，不静默漏掉）；`bases` 按类过滤；都不给则全部。

    顺序固定：先按 `BASES` 的类序分组，组内按条目在文件里的顺序——与传入 `ids` 的顺序无关，保证确定。"""
    if ids is not None:
        wanted = set(ids)
        known = {e["id"] for e in load()["entries"]}
        missing = sorted(wanted - known)
        if missing:
            raise KeyError(", ".join(missing))
    picked = [
        e
        for e in load()["entries"]
        if (ids is None or e["id"] in wanted) and (bases is None or e["basis"] in bases)
    ]
    out: list[str] = []
    for basis in BASES:
        group = [e for e in picked if e["basis"] == basis]
        if not group:
            continue
        if out:
            out.append("")
        out.append(f"## {_BASIS_HEADINGS[basis]}")
        out += [f"- [{e['id']}] {e['agent']}" for e in group]
    return "\n".join(out) + ("\n" if out else "")
