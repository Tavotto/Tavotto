"""v2 来源事实 → 预检能用的东西（ADR 0155 §14 修订，2026-11-17 用户拍板）。

纯函数、纯标准库：输入一份已验签的 v2 刊包，输出两样——

* `rules` / `sources`：**v1 同形**的期刊规则与来源（`journals.py` 的 rule / source 形状），
  前端叠到该刊的 v1 条目上之后走**现有**折叠与求值器（`journals.ts` 的 `overrideOf` 与两侧预检）；
  这里不判任何东西、不新增判据，只决定「哪些 v2 事实可以当 v1 规则用」。
* `confirm`：带文字条件（或名称条件判不出）、程序没法自动判断的数值要求，只给人看，不报警、不计数。

只折**无条件**（无 `conditions`、无 `condition_text`）、数值与方向相符、语气是必须 / 建议 / 偏好的事实；
其余不折叠（示例、允许、通常不是约束，见 FACTS-V2.md）；语气没说清（`unspecified`）的数值要求只进 `confirm`。同一路径有两条不同的无条件值时不猜，全部进 `confirm`。
看护：`tests/test_journalfacts_fold.py` + `tests/golden/journal_v2_fold_vectors.json`。
"""

from __future__ import annotations

from typing import Any

#: v2 来源 id 在叠加后的目录里的前缀；前端靠它认出「这条出处来自 v2」（出处卡标版本）。
V2_SOURCE_PREFIX = "v2:"

#: 只有这几种语气是「对作者的要求」，才可能折叠；示例 / 允许 / 通常既不折叠也不进确认。
PRESCRIPTIVE = ("required", "recommended", "preferred")
#: 「需要你确认」另收原文没说清语气的（`unspecified`）：它仍是写在指南里的数值要求，只是不拿它报警。
LISTABLE = (*PRESCRIPTIVE, "unspecified")
#: 字体 / 字号的「举例」「约」也列出来（用户 2026-11-17 裁决：只举例的字体、约数字号放进「需要你确认」，不进预检报警）。
EXTRA_LISTABLE = {"font_family": ("example", "typical"), "font_size": ("example", "typical")}

#: (subject, relation, unit) → (v1 规则路径, v1 qualifier)。方向相符才折叠（同 `QUALIFIER_DIRECTION`）。
FOLDABLE: dict[tuple[str, str, str | None], tuple[str, str]] = {
    ("raster_dpi", "gte", "dpi"): ("min_raster_dpi", "min"),
    ("min_line_width", "gte", "pt"): ("min_line_width_pt", "min"),
    ("font_family", "in", None): ("font_family.latin_accepted", "exact"),
}

#: 进「需要你确认」的数值要求的（主语 × 单位）闭集（界面词表 `journal.field.v2_<主语>_<单位>`）与可读的比较词。
CONFIRM_UNITS: dict[str, tuple[str, ...]] = {
    "raster_dpi": ("dpi",),
    "min_line_width": ("pt",),
    "width": ("mm", "in"),
    "figure_piece_width": ("mm", "in"),
    "figure_size_max": ("mm", "in"),
    "font_size": ("pt",),
}
CONFIRM_RELATIONS = ("gte", "lte", "eq")
#: 名单型要求（色彩模式、文件格式）：预检没有对应判据，只列出来给人看（用户 2026-11-17 批准的方案原文：先只进「需要你确认」）。
CONFIRM_LISTS = ("color_mode", "file_format", "font_family")

#: v1 只有三档阶段；v2 的 unknown 等同 any，revision / accepted 没有对应档，不折叠。
_STAGE = {"unknown": "any", "any": "any", "submission": "submission", "production": "production"}


def _unconditional(fact: dict) -> bool:
    return not fact.get("conditions") and not fact.get("condition_text")


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _listable(fact: dict) -> bool:
    value = fact["value"]
    return (
        fact["subject"] in CONFIRM_LISTS
        and fact["relation"] == "in"
        and isinstance(value, list)
        and bool(value)
        and all(isinstance(x, str) and x.strip() for x in value)
    )


def _confirmable(fact: dict) -> bool:
    """能进「需要你确认」清单的事实：单位在表里的数值要求，或名单型（色彩模式 / 文件格式 / 字体）。"""
    return (
        fact.get("unit") in CONFIRM_UNITS.get(fact["subject"], ())
        and fact["relation"] in CONFIRM_RELATIONS
        and _number(fact["value"])
    ) or _listable(fact)


def _source_id(fact: dict) -> str:
    return f"{V2_SOURCE_PREFIX}{fact['source']}:{fact['origin']}"


def _rule(fact: dict, qualifier: str) -> dict:
    rule: dict[str, Any] = {
        "value": fact["value"],
        "src": _source_id(fact),
        "quote": fact["quote"],
        "qualifier": qualifier,
        "strength": fact["strength"],
        "stage": _STAGE[fact["stage"]],
    }
    for key in ("anchor", "mark", "derivation", "snapshot"):
        if key in fact:
            rule[key] = fact[key]
    return rule


def _source(bundle: dict, fact: dict) -> dict:
    src = bundle["sources"][fact["source"]]
    out: dict[str, Any] = {
        "url": src["url"],
        "kind": src.get("kind", "html"),
        "retrieved": src["retrieved"],
        "coverage": "journal_specific" if fact["origin"] == "journal" else "publisher_general",
    }
    page = src.get("page")
    if type(page) is int and page >= 1:
        out["page"] = page
    return out


def _condition_text(fact: dict) -> str:
    """原文条件**逐字**；只有名称条件（没有 `condition_text`）时没有原文可引，留空——不拼技术键名给人看。"""
    text = fact.get("condition_text")
    return text if isinstance(text, str) and text.strip() else ""


def _confirm_item(bundle: dict, fact: dict) -> dict:
    return {
        "id": fact["id"],
        "subject": fact["subject"],
        "relation": fact["relation"],
        "value": fact["value"],
        "unit": fact.get("unit"),
        "condition": _condition_text(fact),
        "quote": fact["quote"],
        "strength": fact["strength"],
        "stage": _STAGE.get(fact["stage"], "any"),
        **{key: fact[key] for key in ("anchor", "mark", "derivation", "snapshot") if key in fact},
        "source": _source(bundle, fact),
    }


def fold(bundle: dict) -> dict:
    """把一份已校验的 v2 刊包折成 `{rules, sources, confirm}`（见模块说明）。"""
    facts = [
        f
        for f in bundle.get("facts", [])
        if f.get("origin") in ("journal", "publisher")
        and (
            f.get("strength") in LISTABLE
            or f.get("strength") in EXTRA_LISTABLE.get(f.get("subject"), ())
        )
    ]
    candidates: dict[str, list[tuple[dict, str]]] = {}
    confirm: list[dict] = []
    for fact in facts:
        key = (fact["subject"], fact["relation"], fact.get("unit"))
        target = FOLDABLE.get(key)
        foldable = (
            target is not None
            and fact["strength"] in PRESCRIPTIVE
            and _unconditional(fact)
            and fact["stage"] in _STAGE
            and (
                (fact["subject"] == "font_family")
                and isinstance(fact["value"], list)
                and bool(fact["value"])
                and all(isinstance(x, str) and x.strip() for x in fact["value"])
                or (fact["subject"] != "font_family" and _number(fact["value"]))
            )
        )
        if foldable and target is not None:
            candidates.setdefault(target[0], []).append((fact, target[1]))
            continue
        if _confirmable(fact):
            confirm.append(_confirm_item(bundle, fact))
    rules: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    for path in sorted(candidates):
        items = candidates[path]
        values = {repr(f["value"]) for f, _ in items}
        if len(values) > 1:
            # 同一项两个不同的无条件值：不猜，全交给人确认（数值型与名单型——字体——都进；形状进不了清单的才丢）
            for fact, _ in items:
                if _listable(fact) or _number(fact["value"]):
                    confirm.append(_confirm_item(bundle, fact))
            continue
        fact, qualifier = items[0]
        rules[path] = _rule(fact, qualifier)
        # `fact_id`：期刊线索（ADR 0213）用 `v2:<fact id>` 指认是哪条事实；`src` 的值（`v2:<来源>:<origin>`）不变
        sources[_source_id(fact)] = {**_source(bundle, fact), "fact_id": fact["id"]}
    return {"rules": rules, "sources": sources, "confirm": confirm}
