"""投稿核对（ADR 0216，高级功能 `submission-pack`）：开源侧的「要求表」与权益判断。

**纯函数、纯标准库、只读**：把一本期刊在 v1 目录与 v2 刊包里的要求合成「一条要求一行」（ADR 0216 §九）。
每行分两部分：

* 发给闭源组件 `submission_check.plan` 的字段（`SIDECAR_FIELDS`）：检查项、关系、数值、单位、语气、阶段、
  `source_state`、有名字的条件、有没有文字条件——**不带引文、截图、链接**（ADR 0198 §三）；
* 只留在开源侧的出处（`quote` / `snapshot` / `link` …），界面与报告按 `ref` 对回去。

合并规则（ADR 0216 §九，与预检的 0155 §14 一致）：同一检查项 v2 有就用 v2、v1 的那几行不要；v2 直接读刊包事实，
不读 `fold()` 的折叠结果（`fold()` 不列 `interval`、`accepted` 会显示成 `any`），`fold()` 只用来认出哪几条事实
真的折进了预检。`source_state` 只有 `preflight` 能在界面上说「判过」；`advice`（如 Nature 的 5 pt 下限）与
`confirm` 都不能。

权益判断只在这里与投稿核对的端点（`license.has("submission-pack")`，ADR 0216 §七）；被它调用的检查、修正、
导出、核验模块里不出现任何判断。
"""

from __future__ import annotations

import copy
from typing import Any

from . import journalfacts_fold, journals, license as engine_license

FEATURE = "submission-pack"

#: 组件认的检查项（ADR 0216 §九，与 ADR 0218 §一 A 档同名）。
CHECKS = (
    "width_final",
    "font_size_min",
    "font_size_max",
    "font_family",
    "raster_dpi_min",
    "line_width_min",
)
SOURCE_STATES = ("preflight", "advice", "confirm")
#: 发给组件的字段；其余（出处）只留在开源侧。
SIDECAR_FIELDS = (
    "ref",
    "check",
    "relation",
    "value",
    "unit",
    "strength",
    "stage",
    "source_state",
    "conditions",
    "has_condition_text",
)
#: 组件认的有名字的条件（ADR 0218 §二 的 required 值集里，投稿核对用到的那几个）；别的键不发。
SIDECAR_CONDITIONS = ("column", "text_role")
MM_PER_IN = 25.4

#: v1 规则路径 → (检查项, 关系)。`page_columns` 另拆成每栏一行。
V1_PATHS: dict[str, tuple[str, str]] = {
    "min_effective_font_size_pt": ("font_size_min", "gte"),
    "max_font_size_pt": ("font_size_max", "lte"),
    "font_family.latin_accepted": ("font_family", "in"),
    "min_raster_dpi": ("raster_dpi_min", "gte"),
    "min_line_width_pt": ("line_width_min", "gte"),
    "info.min_line_width_pt": ("line_width_min", "gte"),
}
V1_UNITS = {
    "font_size_min": "pt",
    "font_size_max": "pt",
    "raster_dpi_min": "dpi",
    "line_width_min": "pt",
    "width_final": "mm",
}
#: v1 旧写法的栏宽（没有 `page_columns` 时才有意义；有它时这两项由它派生，见 `journals._override_of`）
V1_WIDTH_PATHS = {"widths_mm.single": "single", "widths_mm.double": "double"}
#: v1 `page_columns` 栏位 id → `column` 条件值
V1_COLUMNS = {"single-column": "single", "double-column": "double"}
#: v1 `affects` → `source_state`
V1_STATES = {"preflight": "preflight", "advice": "advice", "info": "confirm"}
#: v2 的阶段：`accepted` 并入 production（ADR 0218 §三），`unknown` 当作 any
V2_STAGES = {
    "any": "any",
    "unknown": "any",
    "submission": "submission",
    "production": "production",
    "accepted": "production",
}


def licensed() -> bool:
    return engine_license.has(FEATURE)


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _v1_rows(journal_id: str, catalog: dict | None) -> list[dict]:
    rows: list[dict] = []
    sources = journals.journal_sources(journal_id, catalog)
    for path, src in sources.items():
        if src.get("unset"):
            continue
        if path in V1_WIDTH_PATHS and "page_columns" in sources:
            continue  # 有 `page_columns` 时这两项由它派生，再列一遍就重了
        base = {
            "strength": src.get("strength", "unspecified"),
            "stage": src.get("stage", "any"),
            "source_state": V1_STATES.get(src["affects"], "confirm"),
            "has_condition_text": False,
            "origin": {
                "catalog": "v1",
                "path": path,
                "quote": src["quote"],
                "mark": src.get("mark", ""),
                "snapshot": copy.deepcopy(src.get("snapshot")),
                "link": src["link"],
                "retrieved": src["retrieved"],
            },
        }
        if path == "page_columns" and isinstance(src.get("value"), list):
            for col in src["value"]:
                width = col.get("width_mm") if isinstance(col, dict) else None
                if not _number(width):
                    continue
                column = V1_COLUMNS.get(str(col.get("id")))
                qualifier = col.get("qualifier", "exact")
                # 与预检同一口径（`preflight.judged_page_columns`）：只有 exact（等于）与 max（上限）真判过；
                # typical / recommended / range 只是写在指南里的数，记成 confirm，关系照旧是「等于这个宽度」
                judged = src["affects"] == "preflight" and qualifier in ("exact", "max")
                rows.append(
                    base
                    | {
                        "check": "width_final",
                        "relation": "lte" if qualifier == "max" else "eq",
                        "value": width,
                        "unit": "mm",
                        "conditions": {"column": column} if column else {},
                        "source_state": "preflight" if judged else "confirm",
                    }
                )
            continue
        if path in V1_WIDTH_PATHS:
            if _number(src.get("value")):
                rows.append(
                    base
                    | {
                        "check": "width_final",
                        "relation": "eq",
                        "value": src["value"],
                        "unit": "mm",
                        "conditions": {"column": V1_WIDTH_PATHS[path]},
                    }
                )
            continue
        mapped = V1_PATHS.get(path)
        if mapped is None:
            rows.append(
                base
                | {
                    "check": None,
                    "relation": None,
                    "value": copy.deepcopy(src.get("value")),
                    "unit": None,
                    "conditions": {},
                }
            )
            continue
        check, relation = mapped
        value = src.get("value")
        if check == "font_family":
            if not (isinstance(value, list) and value and all(isinstance(x, str) for x in value)):
                continue
        elif not _number(value):
            continue
        rows.append(
            base
            | {
                "check": check,
                "relation": relation,
                "value": copy.deepcopy(value),
                "unit": V1_UNITS.get(check),
                "conditions": {},
            }
        )
    return rows


def _v2_check(fact: dict) -> tuple[str, str, Any, str | None] | None:
    """v2 事实 → (检查项, 关系, 值, 单位)；认不出的 = None（只进「需要你确认」，没有操作）。"""
    subject, relation, value, unit = (
        fact.get("subject"),
        fact.get("relation"),
        fact.get("value"),
        fact.get("unit"),
    )
    if subject == "width" and unit in ("mm", "in"):

        def mm(v):
            return round(v * MM_PER_IN, 3) if unit == "in" else v

        if relation == "eq" and _number(value):
            return "width_final", "eq", mm(value), "mm"
        # v2 的区间是对象 `{lower, upper, lower_inclusive, upper_inclusive}`（`journalfacts_contract._value`）
        if (
            relation == "interval"
            and isinstance(value, dict)
            and _number(value.get("lower"))
            and _number(value.get("upper"))
        ):
            return (
                "width_final",
                "interval",
                {**value, "lower": mm(value["lower"]), "upper": mm(value["upper"])},
                "mm",
            )
        return None
    if subject == "font_size" and unit == "pt" and _number(value):
        if relation in ("gte", "gt"):
            return "font_size_min", "gte", value, "pt"
        if relation in ("lte", "lt"):
            return "font_size_max", "lte", value, "pt"
        return None
    if subject == "font_family" and relation == "in" and isinstance(value, list) and value:
        if all(isinstance(x, str) and x.strip() for x in value):
            return "font_family", "in", list(value), None
        return None
    if subject == "raster_dpi" and unit == "dpi" and relation == "gte" and _number(value):
        return "raster_dpi_min", "gte", value, "dpi"
    if subject == "min_line_width" and unit == "pt" and relation == "gte" and _number(value):
        return "line_width_min", "gte", value, "pt"
    return None


def _is_requirement(fact: dict) -> bool:
    strength, subject = fact.get("strength"), fact.get("subject")
    return fact.get("origin") in ("journal", "publisher") and (
        strength in journalfacts_fold.LISTABLE
        or strength in journalfacts_fold.EXTRA_LISTABLE.get(subject, ())
    )


def _v2_rows(bundle: dict) -> list[dict]:
    folded = journalfacts_fold.fold(bundle)
    folded_ids = {r.get("fact_id") for r in folded["rules"].values()}
    sources = bundle.get("sources") or {}
    rows: list[dict] = []
    for fact in bundle.get("facts") or []:
        fid = fact.get("id")
        # 是不是「对作者的要求」与 `fold()` 同一道门（origin 与语气）：示例 / 允许 / 外来 origin 不收。
        # 不再按 `fold()` 的确认清单过滤——那份清单不列 `interval`（1.5 栏），这里要
        if not _is_requirement(fact):
            continue
        mapped = _v2_check(fact)
        check, relation, value, unit = (
            mapped if mapped else (None, fact.get("relation"), fact.get("value"), fact.get("unit"))
        )
        conditions = fact.get("conditions") if isinstance(fact.get("conditions"), dict) else {}
        src = sources.get(fact.get("source")) or {}
        text = fact.get("condition_text")
        rows.append(
            {
                "check": check,
                "relation": relation,
                "value": copy.deepcopy(value),
                "unit": unit,
                "strength": fact.get("strength", "unspecified"),
                "stage": V2_STAGES.get(fact.get("stage"), "any"),
                "source_state": "preflight" if fid in folded_ids else "confirm",
                "conditions": copy.deepcopy(conditions),
                "has_condition_text": isinstance(text, str) and bool(text.strip()),
                "origin": {
                    "catalog": "v2",
                    "fact_id": fid,
                    "subject": fact.get("subject"),
                    "quote": fact.get("quote", ""),
                    "condition_text": text if isinstance(text, str) else "",
                    "mark": fact.get("mark", ""),
                    "snapshot": copy.deepcopy(fact.get("snapshot")),
                    "link": src.get("url", ""),
                    "retrieved": src.get("retrieved", ""),
                },
            }
        )
    return rows


def _same(a: dict, b: dict) -> bool:
    """同一条要求：检查项、关系、数值相同，`column` 条件不冲突（一边没写算不冲突）。"""
    ca, cb = (a["conditions"] or {}).get("column"), (b["conditions"] or {}).get("column")
    return (
        a["check"] == b["check"]
        and a["relation"] == b["relation"]
        and a["value"] == b["value"]
        and (ca is None or cb is None or ca == cb)
    )


def _merge(v1: list[dict], bundle: dict | None) -> list[dict]:
    """v1 / v2 合并，**以预检实际怎么判为准**（与 0155 §14 一致）：

    * v2 真折进预检的那条 v1 路径（`fold()` 的 rules 键）：v1 那几行不要，用 v2；
    * v1 判过（preflight）或只给建议（advice）的行一律保留——v2 的栏宽、字号今天不折，预检仍按 v1 判；
      v2 里数值相同的那条并进去（条件取 v2 的、出处多记一份），不再单列；
    * v1 只是展示（confirm）的行：同一检查项 v2 有就让给 v2。
    """
    if bundle is None:
        return v1
    folded_paths = set(journalfacts_fold.fold(bundle)["rules"])
    v2 = _v2_rows(bundle)
    v2_checks = {r["check"] for r in v2 if r["check"] is not None}
    kept: list[dict] = []
    for row in v1:
        path = row["origin"].get("path")
        if path in folded_paths:
            continue
        if row["source_state"] == "confirm" and row["check"] in v2_checks:
            continue
        kept.append(row)
    out = list(kept)
    for row in v2:
        twin = next(
            (
                k
                for k in kept
                if k["check"] is not None and k["source_state"] != "confirm" and _same(k, row)
            ),
            None,
        )
        if twin is None:
            out.append(row)
            continue
        twin["conditions"] = {**(twin["conditions"] or {}), **(row["conditions"] or {})}
        twin["has_condition_text"] = twin["has_condition_text"] or row["has_condition_text"]
        twin.setdefault("also", []).append(row["origin"])
    return out


def effective_catalog() -> dict | None:
    """本机已同步、已验签的 v1 目录（`journalremote.load_cached`）；没有缓存 = None（`journals` 用随包种子）。"""
    from . import journalremote

    cached = journalremote.load_cached()
    return cached.catalog if cached is not None else None


def requirements(
    journal_id: str, *, catalog: dict | None = None, bundle: dict | None = None
) -> list[dict]:
    """这本期刊的要求表（ADR 0216 §九）。`bundle` 是本机已验签的 v2 刊包（没有 = 只用 v1）。

    v1 里没有这本刊、只有 v2 的（ADR 0155：只在 v2 的期刊）也行；两边都没有抛 `JournalCatalogError`。
    """
    try:
        v1 = _v1_rows(journal_id, catalog)
    except journals.JournalCatalogError:
        if bundle is None:
            raise
        v1 = []
    rows = _merge(v1, bundle)
    for i, row in enumerate(rows, 1):
        row["ref"] = f"r{i}"
    return rows


def sidecar_rows(rows: list[dict]) -> list[dict]:
    """只发组件认得的检查项、只发 `SIDECAR_FIELDS`；条件只留 `SIDECAR_CONDITIONS` 里的键（ADR 0198 §三）。"""
    out: list[dict] = []
    for row in rows:
        if row["check"] not in CHECKS:
            continue
        item = {k: copy.deepcopy(row[k]) for k in SIDECAR_FIELDS}
        raw = row["conditions"] or {}
        item["conditions"] = {
            k: v for k, v in raw.items() if k in SIDECAR_CONDITIONS and isinstance(v, str)
        }
        # 丢掉了任何条件（别的键：文章类型、图类……；或认得的键但值不是字符串）组件都判不了，
        # 记成「有文字条件」，组件只回 confirm
        if len(item["conditions"]) != len(raw):
            item["has_condition_text"] = True
        out.append(item)
    return out
