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
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from . import journalfacts_fold, journals, license as engine_license

LOG = logging.getLogger("tavotto.submissioncheck")

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
SIDECAR_FIELDS = (  # `id` / `aliases`（稳定 id）只在开源侧
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
                row = {
                    "check": "width_final",
                    "relation": "lte" if qualifier == "max" else "eq",
                    "value": width,
                    "unit": "mm",
                    "conditions": {"column": column} if column else {},
                    **base,
                }
                row["source_state"] = (
                    "preflight" if judged else "confirm"
                )  # 按这一栏的 qualifier，不按整条规则
                rows.append(row)
            continue
        if path in V1_WIDTH_PATHS:
            if _number(src.get("value")):
                rows.append(
                    {
                        "check": "width_final",
                        "relation": "eq",
                        "value": src["value"],
                        "unit": "mm",
                        "conditions": {"column": V1_WIDTH_PATHS[path]},
                        **base,
                    }
                )
            continue
        mapped = V1_PATHS.get(path)
        if mapped is None:
            rows.append(
                {
                    "check": None,
                    "relation": None,
                    "value": copy.deepcopy(src.get("value")),
                    "unit": None,
                    "conditions": {},
                    **base,
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
            {
                "check": check,
                "relation": relation,
                "value": copy.deepcopy(value),
                "unit": V1_UNITS.get(check),
                "conditions": {},
                **base,
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
    fold_paths = {r.get("fact_id"): path for path, r in folded["rules"].items()}
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
                    "fold_path": fold_paths.get(fid),
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
        row["id"] = stable_id(row["origin"], row["conditions"])
        row["aliases"] = [stable_id(o, {}) for o in row.get("also", [])]
    return rows


def stable_id(origin: dict, conditions: dict) -> str:
    """跨次调用不变的行 id（ADR 0216 §九；`accept` 用它，`ref` 每次现编）：v2 `v2:<fact_id>`，
    v1 `v1:<规则路径>`，栏宽再加 `#<column>`。"""
    if origin.get("catalog") == "v2":
        return f"v2:{origin.get('fact_id')}"
    sid = f"v1:{origin.get('path')}"
    column = (conditions or {}).get("column")
    if origin.get("path") == "page_columns" and column:
        sid += f"#{column}"
    return sid


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


# --------------------------------------------------------------------------- 量值、给组件的请求、校验（ADR 0216 §九）
class SubmissionCheckError(Exception):
    """这次没法一键改：`code` 给前端选一句话（`not_licensed` / `unavailable` / `invalid_plan` …）。
    组件给的结果一条不对就整份作废（所有要求退回 confirm，文档不动）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


STATES = ("pass", "choose", "fixable", "confirm", "not_applicable")
OPS = ("canvas.width", "text.size", "text.family")
MIN_PT, MAX_PT = 1.0, 200.0
MAX_WIDTH_MM = 2000.0


def _label_letter(text: object) -> bool:
    from .quicklayoutfig import LABEL_RE

    return isinstance(text, str) and LABEL_RE.match(text.strip()) is not None


def measure_figure(
    manifest: dict, scale: float, prefix: str
) -> tuple[list[dict], dict[str, str], bool]:
    """一张图里画出来的文字：`(texts, ref → gid, 分图标签是否都认出了归属)`。

    `size_pt` 是读者在页面上量到的字号（脚本字号 × 缩放）。分图标签用一键排版同一套识别
    （`quicklayoutfig.analyze`，一个字母、从 a 起连续、每个都认得出管哪个轴）；看着像标签（单个字母）
    却没认出来的，算「认不出」——这块画布上那条字号上限只能 confirm（ADR 0216 §九）。
    """
    from . import preflight, quicklayoutfig

    try:
        labels = {lab.gid for lab in quicklayoutfig.analyze(manifest).labels}
    except ValueError:
        labels = set()
    hidden = preflight._hidden_legends(manifest)
    texts: list[dict] = []
    refs: dict[str, str] = {}
    known = True
    for i, (el, size) in enumerate(preflight._iter_font_elements(manifest, hidden), 1):
        gid = str(el.get("gid") or "")
        if not gid:
            continue
        is_label = gid in labels
        if not is_label and _label_letter(preflight._field(el, "text")):
            known = False
        ref = f"{prefix}t{i}"
        refs[ref] = gid
        family = preflight._field(el, "fontfamily")
        texts.append(
            {
                "ref": ref,
                "role": str(el.get("role") or "text"),
                "size_pt": round(size * scale, 3),
                "family": family if isinstance(family, str) else None,
                "panel_label": is_label,
                "editable": True,
            }
        )
    return texts, refs, known


def canvas_texts(raw: object, prefix: str) -> list[dict]:
    """前端送来的画布级文字（排版文档里的子图标签、文字框）：只量不改（`editable = false`）。"""
    out: list[dict] = []
    for i, t in enumerate(raw if isinstance(raw, list) else [], 1):
        if not isinstance(t, dict) or not _number(t.get("size_pt")):
            continue
        out.append(
            {
                "ref": f"{prefix}k{i}",
                "role": "canvas-text",
                "size_pt": float(t["size_pt"]),
                "family": t.get("family") if isinstance(t.get("family"), str) else None,
                "panel_label": t.get("panel_label") is True,
                "editable": False,
            }
        )
    return out


def validate_result(result: object, payload: dict) -> dict:
    """组件结果的逐条校验（ADR 0216 §九）。任何一条不过抛 `invalid_plan`，不做部分采用。

    形状不对（集合类型错、ref 不是字符串……）同样是 `invalid_plan`，不让 `TypeError` 之类漏出去。"""
    try:
        return _validate_result(result, payload)
    except SubmissionCheckError:
        raise
    except (TypeError, KeyError, AttributeError, ValueError) as exc:
        raise SubmissionCheckError("invalid_plan", f"shape: {exc}") from exc


def _strs(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise SubmissionCheckError("invalid_plan", "expected a list of refs")
    return value


def _validate_result(result: object, payload: dict) -> dict:
    if not isinstance(result, dict):
        raise SubmissionCheckError("invalid_plan", "result")
    rows = {r["ref"]: r for r in payload["requirements"]}
    canvases = {c["ref"]: c for c in payload["canvases"]}
    texts = {t["ref"]: (c["ref"], t) for c in payload["canvases"] for t in c["texts"]}
    ops: dict[str, dict] = {}
    raw_ops, raw_reqs = result.get("ops", []), result.get("requirements", [])
    if not isinstance(raw_ops, list) or not isinstance(raw_reqs, list):
        raise SubmissionCheckError("invalid_plan", "ops / requirements")
    for op in raw_ops:
        if (
            not isinstance(op, dict)
            or op.get("op") not in OPS
            or not isinstance(op.get("ref"), str)
        ):
            raise SubmissionCheckError("invalid_plan", "op")
        if op["ref"] in ops or op.get("canvas") not in canvases:
            raise SubmissionCheckError("invalid_plan", "op ref / canvas")
        canvas = canvases[op["canvas"]]
        if op["op"] == "canvas.width":
            w = op.get("width_mm")
            if not canvas.get("width_adjustable") or not _number(w) or not 0 < w <= MAX_WIDTH_MM:
                raise SubmissionCheckError("invalid_plan", "canvas.width")
        else:
            refs = _strs(op.get("texts"))
            if not refs:
                raise SubmissionCheckError("invalid_plan", "texts")
            for ref in refs:
                owner = texts.get(ref)
                if owner is None or owner[0] != canvas["ref"] or not owner[1]["editable"]:
                    raise SubmissionCheckError("invalid_plan", "text ref")
            if op["op"] == "text.size":
                size = op.get("size_pt")
                if not _number(size) or not MIN_PT <= size <= MAX_PT:
                    raise SubmissionCheckError("invalid_plan", "size_pt")
            elif not isinstance(op.get("family"), str) or not op["family"].strip():
                raise SubmissionCheckError("invalid_plan", "family")
        ops[op["ref"]] = op
    states: dict[str, dict] = {}
    for item in raw_reqs:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("ref"), str)
            or item["ref"] not in rows
            or item["ref"] in states
        ):
            raise SubmissionCheckError("invalid_plan", "requirement ref")
        row = rows[item["ref"]]
        if item.get("state") not in STATES:
            raise SubmissionCheckError("invalid_plan", "state")
        per = item.get("per_canvas") or []
        if not isinstance(per, list):
            raise SubmissionCheckError("invalid_plan", "per_canvas")
        for pc in per:
            if (
                not isinstance(pc, dict)
                or not isinstance(pc.get("canvas"), str)
                or pc["canvas"] not in canvases
            ):
                raise SubmissionCheckError("invalid_plan", "per_canvas ref")
            if pc.get("state") not in STATES:
                raise SubmissionCheckError("invalid_plan", "per_canvas state")
            for oref in _strs(pc.get("ops", [])):
                op = ops.get(oref)
                if op is None or op["canvas"] != pc["canvas"]:
                    raise SubmissionCheckError("invalid_plan", "per_canvas op")
                _check_op_against_row(op, row, texts)
        if row["check"] == "font_size_max":
            for pc in per:
                if pc["state"] == "fixable" and not canvases[pc["canvas"]].get(
                    "panel_labels_known"
                ):
                    raise SubmissionCheckError(
                        "invalid_plan", "max size on a canvas with unknown labels"
                    )
        if (
            "pass" in {item["state"], *(pc["state"] for pc in per)}
            and row["source_state"] != "preflight"
        ):
            raise SubmissionCheckError("invalid_plan", "pass on a row the engine did not judge")
        states[item["ref"]] = item
    return {"requirements": states, "ops": ops}


def _check_op_against_row(op: dict, row: dict, texts: dict) -> None:
    """操作必须对得上它所属的那条要求：字体在名单里；字号上限不碰分图标签（这本刊另有分图标签要求时）。"""
    if op["op"] == "text.family":
        if row["check"] != "font_family" or op["family"] not in (row["value"] or []):
            raise SubmissionCheckError("invalid_plan", "family not in the list")
    if op["op"] == "text.size":
        if row["check"] not in ("font_size_min", "font_size_max"):
            raise SubmissionCheckError("invalid_plan", "text.size on a non-size row")
        if row["conditions"].get("text_role") == "other_text" or row["check"] == "font_size_max":
            if any(texts[r][1]["panel_label"] for r in op["texts"]):
                raise SubmissionCheckError("invalid_plan", "panel label in a body-text op")
    if op["op"] == "canvas.width" and row["check"] != "width_final":
        raise SubmissionCheckError("invalid_plan", "canvas.width on a non-width row")


def chosen_ops(checked: dict, rows: list[dict], accept: list[str]) -> list[dict]:
    """这次要落的操作：判过（preflight）的行自动落；别的行只有用户点过（`accept` 里有它的稳定 id 或别名）才落。
    同一个操作被几条要求引用只落一次。图宽先落（字号要按新的缩放算）。"""
    wanted = set(accept)
    by_ref = {r["ref"]: r for r in rows}
    picked: dict[str, dict] = {}
    for ref, item in checked["requirements"].items():
        row = by_ref[ref]
        if row["source_state"] != "preflight" and not ({row["id"], *row["aliases"]} & wanted):
            continue
        accepted = bool({row["id"], *row["aliases"]} & wanted)
        for pc in item.get("per_canvas") or []:
            # 判过的行只自动落「能改」的；「要你选」（如栏宽二选一）只在用户点了那一条之后才落（ADR 0216 §五）
            ok_states = ("fixable", "choose") if accepted else ("fixable",)
            if pc.get("state") not in ok_states:
                continue
            for oref in pc.get("ops") or []:
                picked.setdefault(oref, {**checked["ops"][oref], "row": ref})
    order = {"canvas.width": 0, "text.size": 1, "text.family": 2}
    return sorted(picked.values(), key=lambda o: (order[o["op"]], o["ref"]))


def specfix_request(
    op: dict, row: dict, refs: dict[str, str], profile: dict
) -> tuple[dict, list[dict]]:
    """把一条字号 / 字体操作翻成免费 specfix 事务的输入：`(这次用的规范, only)`（ADR 0216 §九）。
    只修 `texts` 对应的 gid；目标值写进这次事务的规范，层级、回滚、真实渲染裁决都照单图修正走。"""
    prof = copy.deepcopy(profile)
    gids = [refs[r] for r in op["texts"] if r in refs]
    if op["op"] == "text.family":
        fam = dict(prof.get("font_family") or {})
        fam["latin"], fam["latin_accepted"] = op["family"], [op["family"]]
        prof["font_family"] = fam
        rule = "font-family-substituted"
    elif row["check"] == "font_size_max":
        prof["max_font_size_pt"] = float(op["size_pt"])
        rule = "font-too-large"
    else:
        target = float(op["size_pt"])
        prof["min_effective_font_size_pt"] = target
        floor = prof.get("absolute_min_font_size_pt")
        if _number(floor) and floor > target:
            prof["absolute_min_font_size_pt"] = target
        rule = "font-too-small"
    sev = dict(prof.get("severity") or {})
    sev[rule] = "warn"
    prof["severity"] = sev
    # 低于绝对下限的字报的是 `font-below-absolute-floor`，不是 `font-too-small`：抬字号时两条都点名，否则修不到它
    rules = [rule, "font-below-absolute-floor"] if rule == "font-too-small" else [rule]
    return prof, [{"rule": r, "gid": g} for g in gids for r in rules]


def satisfied(row: dict, value: float | str | None) -> bool:
    """重新量到的值满足这条要求没有（不信组件给的数，ADR 0216 §九）。"""
    target = row["value"]
    if row["check"] == "font_family":
        return isinstance(value, str) and value.lower() in {str(x).lower() for x in target}
    if not _number(value):
        return False
    tol = 0.05
    if row["relation"] == "lte":
        return value <= target + tol
    if row["relation"] == "gte":
        return value >= target - tol
    if row["relation"] == "eq":
        return abs(value - target) <= (0.5 if row["check"] == "width_final" else tol)
    if row["relation"] == "interval" and isinstance(target, dict):
        return target["lower"] - tol <= value <= target["upper"] + tol
    return False


CMD = "submission_check.plan"


def ask(payload: dict, *, solver=None) -> dict:
    """问闭源组件、校验结果。`solver` 给测试注入；缺省经 `prohost.call`（调用方已判过 `licensed()`）。
    组件不在、版本旧、超时、崩溃一律 `unavailable`；结果不合契约一律 `invalid_plan`。"""
    from . import prohost

    try:
        result = (
            solver(payload) if solver is not None else prohost.call(CMD, payload, feature=FEATURE)
        )
    except prohost.ProUnavailable as exc:
        raise SubmissionCheckError("unavailable", exc.code) from exc
    return validate_result(result, payload)


# --------------------------------------------------------------------------- 编排（ADR 0216 §八.2：只算不写）
@dataclass(eq=False)
class Figure:
    """画布上的一张图。`render(patches)` 由调用方给（app.py 用 worker，测试注入）；本模块不碰文档、不落盘。"""

    key: str
    rel_id: str
    base: list
    box: list[float]  # 画布上的摆放 [x, y, w, h]（mm）
    render: Callable[[list], dict]
    patches: list = field(default_factory=list)
    manifest: dict | None = None
    refs: dict[str, str] = field(default_factory=dict)  # 文字短号 → gid
    labels_known: bool = True

    @property
    def size_mm(self) -> list[float]:
        size = (self.manifest or {}).get("size_mm")
        return [float(size[0]), float(size[1])]

    @property
    def scale(self) -> float:
        w = self.size_mm[0]
        return self.box[2] / w if w > 0 else 1.0


@dataclass
class Canvas:
    key: str
    page: dict  # {w, h}（mm）
    figures: list[Figure]
    canvas_texts: list = field(default_factory=list)  # 前端送来的画布级文字
    canvas_labels_known: bool = True
    only_figure: bool = False  # 前端说画布上除了这张图没有别的对象（改图宽的前提，见 `_fills`）
    profile: dict | None = None


#: 单图画布：图铺满画布（摆放与页面之差不超过这个数）才算「改图宽 = 改这张图的图幅」
FILL_TOL_MM = 0.5
SCALE_TOL = 0.01


def _render(fig: Figure, patches: list) -> None:
    resp = fig.render(patches)
    m = resp.get("manifest") if isinstance(resp, dict) else None
    size = (m or {}).get("size_mm")
    if (
        not isinstance(m, dict)
        or resp.get("warnings")
        or not (
            isinstance(size, list) and len(size) == 2 and all(_number(v) and v > 0 for v in size)
        )
    ):
        raise SubmissionCheckError("render_failed", fig.rel_id)
    fig.manifest = m
    fig.patches = list(patches)


def _fills(c: Canvas) -> bool:
    """改图宽 = 改这张图的图幅，只在最稳妥的情形下成立：画布上只有这一张图、铺满画布、按原尺寸显示（缩放 = 1），
    并且前端说了画布上没有别的对象（箭头、形状、子图标签——它们不会跟着挪）。别的情形改宽度是一键排版的事。"""
    if len(c.figures) != 1 or not c.only_figure:
        return False
    fig = c.figures[0]
    x, y, w, h = fig.box
    return (
        abs(x) <= FILL_TOL_MM
        and abs(y) <= FILL_TOL_MM
        and abs(w - float(c.page["w"])) <= FILL_TOL_MM
        and abs(h - float(c.page["h"])) <= FILL_TOL_MM
        and abs(fig.scale - 1.0) <= SCALE_TOL
    )


def _measure(ci: int, c: Canvas) -> tuple[dict, dict[str, tuple[Figure, str]]]:
    """一块画布的量值（给组件）与这次发出去的文字短号 → (图, gid)。短号只在这一次解析，之后一律用 gid。"""
    texts: list[dict] = []
    known = c.canvas_labels_known
    where: dict[str, tuple[Figure, str]] = {}
    for fi, fig in enumerate(c.figures, 1):
        t, refs, ok = measure_figure(fig.manifest or {}, fig.scale, f"c{ci}f{fi}")
        where |= {r: (fig, g) for r, g in refs.items()}
        texts += t
        known = known and ok
    texts += canvas_texts(c.canvas_texts, f"c{ci}")
    payload = {
        "ref": f"c{ci}",
        "width_mm": round(float(c.page["w"]), 3),
        "height_mm": round(float(c.page["h"]), 3),
        "width_adjustable": _fills(c),
        "texts": texts,
        "panel_labels_known": known,
        "preflight": sorted({i["id"] for f in c.figures for i in _issues(f, c.profile)}),
    }
    return payload, where


def _issues(fig: Figure, profile: dict | None) -> list[dict]:
    """这张图此刻的免费预检问题（与问题面板同一个求值器；`specfix.profile_issues`）。"""
    from . import specfix

    if profile is None or fig.manifest is None:
        return []
    return specfix.profile_issues(fig.manifest, profile, fig.scale)


def _upsert(patches: list, gid: str, prop: str, value) -> list:
    out = [p for p in patches if not (p.get("gid") == gid and p.get("prop") == prop)]
    return out + [{"gid": gid, "prop": prop, "value": value}]


def _touched(before: list, after: list) -> set[str]:
    old = {(p.get("gid"), p.get("prop"), repr(p.get("value"))) for p in before}
    return {
        p.get("gid")
        for p in after
        if (p.get("gid"), p.get("prop"), repr(p.get("value"))) not in old
    }


def _apply_width(c: Canvas, op: dict) -> bool:
    """单图画布改图宽（只在 `_fills` 成立时会到这里）：缩放本来就是 1，高度等比，摆放与页面跟着改。"""
    fig = c.figures[0]
    ratio = float(c.page["h"]) / float(c.page["w"])
    w = float(op["width_mm"])
    h = round(w * ratio, 3)
    _render(fig, _upsert(fig.patches, "figure", "size_mm", [w, h]))
    fig.box = [0.0, 0.0, w, h]
    c.page = {"w": w, "h": h}
    return True


def _apply_text(
    c: Canvas, op: dict, row: dict, profile: dict, gids: dict[Figure, set[str]]
) -> bool:
    """字号 / 字体：逐张图交给免费的 specfix 事务；实际改到的元素必须都在这条操作里、且不是分图标签。"""
    from . import specfix

    done = True
    for fig, wanted in gids.items():
        refs = {f"g{i}": g for i, g in enumerate(sorted(wanted))}
        prof, only = specfix_request({**op, "texts": list(refs)}, row, refs, profile)
        before = list(fig.patches)
        out = specfix.transaction(fig.render, before, fig.scale, prof, only)
        after = out.get("patches") if out.get("ok") else None
        if not isinstance(after, list) or not _touched(before, after) <= wanted:
            _render(fig, before)  # specfix 改到了别处（层级补齐等）或没修成：这张图不采用
            done = False
            continue
        _render(fig, after)
    return done


def _remeasure_ok(op: dict, row: dict, c: Canvas, gids: dict[Figure, set[str]]) -> bool:
    """落完之后对真实渲染重新量（按 gid，不按短号）：这条要求在这块画布上满足了没有。"""
    if op["op"] == "canvas.width":
        return satisfied(row, float(c.page["w"]))
    key = "family" if op["op"] == "text.family" else "size_pt"
    got = []
    for fig, wanted in gids.items():
        texts, refs, _ = measure_figure(fig.manifest or {}, fig.scale, "z")
        got += [t[key] for t in texts if refs[t["ref"]] in wanted]
    return bool(got) and all(satisfied(row, v) for v in got)


def _rule_of(row: dict) -> str | None:
    """判过的行对应预检的哪条规则（`journals.SEVERITY_BUMP`）；栏宽另按页面宽度判。"""
    path = row["origin"].get("path") or row["origin"].get("fold_path")
    return journals.SEVERITY_BUMP.get(path) if path else None


def _rejudge(row: dict, c: Canvas) -> bool | None:
    """最终画布上重新判一次判过的行：True = 符合、False = 不符合、None = 这条判不了（交给组件的说法）。"""
    if row["source_state"] != "preflight":
        return None
    if row["check"] == "width_final":
        return satisfied(row, float(c.page["w"]))
    rule = _rule_of(row)
    if rule is None:
        return None
    return not any(i["id"] == rule for f in c.figures for i in _issues(f, c.profile))


def _final_state(row: dict, item: dict | None, results: dict, canvases: dict) -> dict:
    """每条要求最后怎么说（开源侧判，ADR 0216 §九）：
    落了操作的按重新量到的值记 `fixed`，没成退回 `confirm`；没落操作的判过的行在**最终画布**上重新判，
    `pass` 只给重新判过还符合的；改宽度后不再符合的退回 `confirm`。"""
    per = []
    for pc in (item or {}).get("per_canvas") or []:
        c = canvases[pc["canvas"]]
        ran = [results[o] for o in pc.get("ops") or [] if o in results]
        st = pc["state"]
        if ran:
            st = "fixed" if all(ran) else "confirm"
        else:
            ok = _rejudge(row, c)
            if st == "pass" and ok is not True:
                st = "confirm"
            elif ok is False and st in ("not_applicable",):
                st = "confirm"
        per.append({"canvas": pc["canvas"], "state": st})
    if not per:
        state = (item or {}).get("state", "confirm")
        if state in ("pass", "fixed"):
            state = "confirm"  # 没有逐画布的依据就不说「判过」
        return {"state": state, "per_canvas": per}
    for worst in ("confirm", "choose", "fixable"):
        if any(p["state"] == worst for p in per):
            return {"state": worst, "per_canvas": per}
    if any(p["state"] == "fixed" for p in per):
        return {"state": "fixed", "per_canvas": per}
    if all(p["state"] == "not_applicable" for p in per):
        return {"state": "not_applicable", "per_canvas": per}
    return {"state": "pass", "per_canvas": per}


def run(
    canvases: list[Canvas],
    *,
    journal_id: str,
    profile: dict,
    accept: list[str] | None = None,
    solver=None,
    catalog: dict | None = None,
    bundle: dict | None = None,
) -> dict:
    """投稿核对的一次计算（ADR 0216 §九）。**只算不写**：回每块画布每张图的最终全量覆盖与逐条要求的状态，
    由前端一次 commit。某块画布按 B0 都出不了图就放进 `skipped`，其余照常（§五）；
    其余失败抛 `SubmissionCheckError`，调用方把每张图的 worker 回到 B0（或作废）。"""
    if not licensed():
        raise SubmissionCheckError("not_licensed")
    rows = requirements(journal_id, catalog=catalog, bundle=bundle)
    ready: list[Canvas] = []
    skipped: list[dict] = []
    for c in canvases:
        c.profile = profile
        try:
            for fig in c.figures:
                _render(fig, list(fig.base))
        except Exception as exc:  # noqa: BLE001 — 这块画布这次先不带，其余照常
            LOG.info("投稿核对：画布 %s 出不了图，先不带它: %s", c.key, exc)
            skipped.append({"key": c.key, "reason": "render_failed"})
            continue
        ready.append(c)
    measured = [_measure(i, c) for i, c in enumerate(ready, 1)]
    payload = {"requirements": sidecar_rows(rows), "canvases": [m[0] for m in measured]}
    checked = ask(payload, solver=solver)
    where = {r: loc for _, refs in measured for r, loc in refs.items()}
    by_ref = {r["ref"]: r for r in rows}
    by_canvas = {f"c{i}": c for i, c in enumerate(ready, 1)}
    results: dict[str, bool] = {}
    for op in chosen_ops(checked, rows, list(accept or [])):
        c = by_canvas[op["canvas"]]
        row = by_ref[op["row"]]
        gids: dict[Figure, set[str]] = {}
        for ref in op.get("texts") or []:
            fig, gid = where[ref]
            gids.setdefault(fig, set()).add(gid)
        if op["op"] == "canvas.width":
            ok = _apply_width(c, op)
        else:
            ok = _apply_text(c, op, row, profile, gids)
        results[op["ref"]] = ok and _remeasure_ok(op, row, c, gids)
    reqs = []
    for row in rows:
        item = checked["requirements"].get(row["ref"])
        if row["check"] in CHECKS:
            final = _final_state(row, item, results, by_canvas)
        else:
            final = {"state": "confirm", "per_canvas": []}
        reqs.append(
            {
                "id": row["id"],
                "aliases": row["aliases"],
                "check": row["check"],
                "relation": row["relation"],
                "value": row["value"],
                "unit": row["unit"],
                "strength": row["strength"],
                "stage": row["stage"],
                "source_state": row["source_state"],
                "conditions": row["conditions"],
                "origin": row["origin"],
                "also": row.get("also", []),
                "state": final["state"],
                "per_canvas": [
                    {**pc, "canvas": by_canvas[pc["canvas"]].key} for pc in final["per_canvas"]
                ],
            }
        )
    return {
        "ok": True,
        "canvases": [
            {
                "key": c.key,
                "page": dict(c.page),
                "figures": [{"key": f.key, "patches": f.patches, "box": f.box} for f in c.figures],
            }
            for c in ready
        ],
        "requirements": reqs,
        "skipped": skipped,
    }
