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


#: AI 改图助手（figure edit）提示词带上的子集：改图时最容易无意中引入的数据 / 编码错误、只能由作者确认的事、
#: 以及全部品味条目（防止助手把偏好当成错误去「修」）。改动这张表会改变改图提示词——要同步跑改图评测。
FIGURE_EDIT_IDS: tuple[str, ...] = (
    "G-001",
    "G-002",
    "G-004",
    "G-005",
    "G-009",
    "G-010",
    "G-011",
    "G-022",
    "G-024",
    "G-030",
    "G-035",
    "G-036",
    "G-038",
    "G-041",
    "G-042",
    "G-043",
    "G-044",
    "G-045",
    "G-046",
    "G-047",
    "G-048",
    "G-049",
    "G-050",
    "G-051",
    "G-058",
)

#: 插件技能参考文件（由 `render_markdown()` 生成，`scripts/gen_sciviz_reference.py --check` 看护）
SKILL_REFERENCE = "codex-plugin/skills/tavotto-figure/references/visualization-guidance.md"

_STRENGTH_LABELS = {
    "firm": "硬性",
    "strong": "强建议",
    "preference": "偏好",
    "inference": "Tavotto 推导",
}


def render_markdown() -> str:
    """插件技能里的参考文件全文：按类分组，每条给编号、标题、强度、一句准则、例外与出处（书 + 位置，不含引文）。确定输出。"""
    doc = load()
    out = [
        "# 科学图形准则（选图、画图、改图前读）",
        "",
        "<!-- 由 Tavotto 仓库的 src/tavotto/profiles/sciviz_guidance.json 生成，不要手改 -->",
        "",
        "依据三本书（Wilkinson《The Grammar of Graphics》、Munzner《Visualization Analysis and Design》、"
        "Wilke《Fundamentals of Data Visualization》）与 Tavotto 自己的推导。",
        "「作者偏好」一类不是对错：按用户或期刊来，不要当成问题报告给用户，也不要擅自改掉。",
        "「只能由作者确认」一类不要猜：问用户，或如实说不知道。",
        "",
    ]
    for basis in BASES:
        group = [e for e in doc["entries"] if e["basis"] == basis]
        if not group:
            continue
        out += [f"## {_BASIS_HEADINGS[basis]}", ""]
        for e in group:
            out.append(
                f"- **{e['id']} {e['title']}**（{_STRENGTH_LABELS[e['strength']]}）：{e['agent']}"
            )
            if e["exceptions"]:
                out.append(f"  - 例外：{'；'.join(e['exceptions'])}")
            srcs = "；".join(f"{ev['src']} {ev['loc']}" for ev in e["evidence"])
            out.append(f"  - 出处：{srcs}")
        out.append("")
    out.append("书目：" + "；".join(f"{k} = {v}" for k, v in doc["sources"].items()))
    return "\n".join(out) + "\n"
