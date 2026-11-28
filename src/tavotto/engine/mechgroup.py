"""机理图的命名与分组（ADR 0220 §五，开源，不判权益、不碰闭源）。

* **命名** = 「角色 + 身份」（`name_for`）：角色词来自 `profiles/mechanism_names.json`（中 / 英），身份是条目里给的 `identity` / 标题 / 块名。
  创建当时按调用方传入的界面语言固化进文档（组名进 `groupNames`、对象名进 `name`），之后不随语言切换改。
  同一批里同名自动加序号（`Names.unique`），所以「同一层无重名」由构造保证，`mechcheck` 再独立核一遍。
* **分组**：示意图框是一个带名字的组，下面按条目的 `block` 分「结构块」，箭头 / 连线等流程元素在「流程」组；不留单成员组；深度受字段表
  `max_group_depth` 约束（`group_path` 由 `mechanism` 交给 `agentobjects` 铸 id）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PROFILE = Path(__file__).resolve().parent.parent / "profiles" / "mechanism_names.json"
MAX_NAME_CHARS = 120


@lru_cache(maxsize=1)
def vocabulary() -> dict:
    return json.loads(PROFILE.read_text(encoding="utf-8"))


def locale_of(raw: object) -> str:
    """界面语言 → 词表语言：`zh*` 用中文，其余一律英文（词表只有这两份）。"""
    return "zh" if isinstance(raw, str) and raw.lower().startswith("zh") else "en"


def role_word(role: str, locale: str) -> str:
    """角色词。`role` 形如 `frame` / `flow` / `glyph:membrane`；词表里没有的图元退回 kind 本身。"""
    v = vocabulary()
    loc = locale_of(locale)
    if role.startswith("glyph:"):
        kind = role.split(":", 1)[1]
        return (v["glyphs"].get(kind) or {}).get(loc) or kind
    return (v["roles"].get(role) or {}).get(loc) or role


def name_for(role: str, identity: str | None, locale: str) -> str:
    """「角色 + 身份」；没有身份就只有角色词。截到 `MAX_NAME_CHARS`。"""
    word = role_word(role, locale)
    ident = " ".join(str(identity).split()) if identity else ""
    name = f"{word}{vocabulary()['separator']}{ident}" if ident else word
    return name[:MAX_NAME_CHARS]


class Names:
    """一批里已经用过的名字：同层同名加序号（「X」「X 2」「X 3」）。"""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def unique(self, name: str) -> str:
        n = self._seen.get(name, 0) + 1
        self._seen[name] = n
        return name if n == 1 else f"{name[: MAX_NAME_CHARS - 6]} {n}"
