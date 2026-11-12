"""`tavotto_guide`：把技能 `tavotto-figure` 的全文交给没有技能目录的宿主（只读、不联网、不写文件）。

技能来源：冻结的桌面版是 `_MEIPASS/skills/tavotto-figure`；插件 / 源码树里是 `<包>/../../skills/tavotto-figure`
（`codex-plugin/mcp/tavotto_mcp` → `codex-plugin/skills`）。`section` 只认 `references/` 里真实存在的文件名
（白名单，来自目录列表，不拼用户给的路径），路径穿越一律 `guide_section_unknown`；读不到回 `guide_unavailable`。
"""

from __future__ import annotations

import os
import sys

SKILL_NAME = "tavotto-figure"
ERR_UNAVAILABLE = "guide_unavailable"
ERR_SECTION = "guide_section_unknown"


def skill_dir() -> str | None:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        cand = os.path.join(base, "skills", SKILL_NAME) if base else None
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        cand = os.path.normpath(os.path.join(here, "..", "..", "skills", SKILL_NAME))
    return cand if cand and os.path.isfile(os.path.join(cand, "SKILL.md")) else None


def sections(root: str) -> list[str]:
    """references/ 下真实存在的 .md 文件名（不含扩展名），已排序。"""
    try:
        names = os.listdir(os.path.join(root, "references"))
    except OSError:
        return []
    return sorted(
        n[:-3]
        for n in names
        if n.endswith(".md") and os.path.isfile(os.path.join(root, "references", n))
    )


def read_guide(section: object = None) -> dict:
    """成功 {ok, section, text, sections}；失败 {ok: False, code, error}。"""
    root = skill_dir()
    if root is None:
        return {"ok": False, "code": ERR_UNAVAILABLE, "error": "这份 Tavotto 里没有技能文件。"}
    avail = sections(root)
    if section in (None, ""):
        rel, name = "SKILL.md", ""
    else:
        name = section[:-3] if isinstance(section, str) and section.endswith(".md") else section
        if not isinstance(name, str) or name not in avail:
            return {
                "ok": False,
                "code": ERR_SECTION,
                "error": "没有这一节。可选：" + "、".join(avail),
                "sections": avail,
            }
        rel = os.path.join("references", name + ".md")
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return {"ok": False, "code": ERR_UNAVAILABLE, "error": "技能文件读不出来。"}
    return {"ok": True, "section": name or "SKILL", "text": text, "sections": avail}
