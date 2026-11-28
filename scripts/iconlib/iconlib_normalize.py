"""规范化与预检（ADR 0222 §四）：用画布导入同一套判据（`rendercore.svgimport`）先筛，再给出色槽 / 分组计划与路径质量统计。

不改 `svgimport`、不改 SVG 本身：这里只**判断**与**出计划**。预检不过的 = 用户插入时会遇到「整张当图片」兜底的，直接不入库。
"""

from __future__ import annotations

import collections
import colorsys
import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tavotto.engine import pathquality  # noqa: E402
from tavotto.rendercore import svgimport  # noqa: E402

#: 体积闸门起步值（样本分布估的，要更多数据校准；ADR 0222 待定项）
DEFAULT_MAX_BYTES = 100 * 1024
DEFAULT_MAX_PATHS = 500
MAX_SLOTS = 5

_IMAGE_RE = re.compile(rb"<\s*image\b|data:image", re.IGNORECASE)
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _luma(hexcol: str) -> float:
    r, g, b = (int(hexcol[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)[1]


def color_slots(objects: list[dict]) -> list[dict]:
    """按「面积加权」挑出最多 5 个主要填充色，标角色（main / highlight / shade）。纯计划，不改对象。"""
    weight: dict[str, float] = collections.defaultdict(float)
    for o in objects:
        f = o.get("fill")
        if isinstance(f, str) and _HEX_RE.match(f):
            weight[f.lower()] += float(o.get("w") or 0) * float(o.get("h") or 0)
    ranked = sorted(weight.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_SLOTS]
    if not ranked:
        return []
    main_luma = _luma(ranked[0][0])
    slots = []
    for i, (col, wt) in enumerate(ranked):
        if i == 0:
            role = "main"
        else:
            role = "highlight" if _luma(col) > main_luma else "shade"
        slots.append({"slot": f"c{i + 1}", "role": role, "color": col, "weight": round(wt, 3)})
    return slots


def group_plan(objects: list[dict], slots: list[dict]) -> list[dict]:
    """最小版自动分组：没有分组的对象按它所属色槽成组（组名中英）。已有分组的原样保留，不重分。

    更好的分组（包围盒包含、同 clipPath、部件语义）与 `groupNames` 的真实字段留给后续；这里只证明流水线形状。"""
    if any(o.get("groupId") for o in objects):
        return [{"name": "(kept)", "name_zh": "(保留原分组)", "kept": True, "object_ids": []}]
    by_slot = {s["color"]: s for s in slots}
    buckets: dict[str, list[str]] = collections.defaultdict(list)
    for o in objects:
        f = o.get("fill")
        s = by_slot.get(f.lower()) if isinstance(f, str) else None
        buckets[s["slot"] if s else "other"].append(str(o.get("id")))
    names = {"main": "主色", "highlight": "高光", "shade": "阴影"}
    plan = []
    for s in slots:
        if s["slot"] in buckets:
            plan.append(
                {
                    "name": f"{s['role']}-{s['slot']}",
                    "name_zh": f"{names[s['role']]}-{s['slot']}",
                    "object_ids": buckets[s["slot"]],
                }
            )
    if "other" in buckets:
        plan.append({"name": "other", "name_zh": "其他", "object_ids": buckets["other"]})
    return plan


def quality_summary(objects: list[dict]) -> dict:
    """逐路径过 `pathquality`，汇成一份统计。不据此丢图（见 ADR 0222：先看分布再定闸门）。"""
    verdicts: collections.Counter = collections.Counter()
    codes: collections.Counter = collections.Counter()
    for o in objects:
        r = pathquality.check_path(o)
        verdicts[r["verdict"]] += 1
        for i in r["issues"]:
            codes[i["code"]] += 1
    if verdicts["fail"]:
        worst = "fail"
    elif verdicts["acceptable"]:
        worst = "acceptable"
    elif verdicts["clean"]:
        worst = "clean"
    else:
        worst = "skipped"
    n = sum(verdicts.values())
    return {
        "verdict": worst,
        "paths": n,
        "by_verdict": dict(verdicts),
        "fail_fraction": round(verdicts["fail"] / n, 3) if n else 0.0,
        "issue_codes": dict(codes),
    }


def precheck(
    svg: bytes,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_paths: int = DEFAULT_MAX_PATHS,
) -> dict:
    """一个 SVG 能不能入库。返回 `{ok, reasons, ...}`；`reasons` 是原因码列表。

    原因码：`too_big` / `bitmap` / `not_text` / `import_error` / `unsupported:<code>` / `no_objects` / `too_many_paths`。"""
    out: dict = {"ok": False, "reasons": [], "bytes": len(svg)}
    if len(svg) > max_bytes:
        out["reasons"].append("too_big")
        return out  # 太大的连导入都不试（省时间，也是闸门的意义）
    if _IMAGE_RE.search(svg):
        out["reasons"].append("bitmap")
        return out
    try:
        text = svg.decode("utf-8")
    except UnicodeDecodeError:
        out["reasons"].append("not_text")
        return out
    try:
        res = svgimport.import_svg(text)
    except Exception as e:  # noqa: BLE001 — 不可信输入，什么异常都按原因码记，不让一个坏文件拖垮整批
        out["reasons"].append("import_error")
        out["error"] = type(e).__name__
        return out
    for code in sorted(res.get("unsupported") or {}):
        out["reasons"].append(f"unsupported:{code}")
    objs = res.get("objects") or []
    if not objs and not out["reasons"]:
        out["reasons"].append("no_objects")
    paths = [o for o in objs if o.get("type") == "path"]
    out["objects"] = len(objs)
    out["paths"] = len(paths)
    if len(paths) > max_paths:
        out["reasons"].append("too_many_paths")
    if out["reasons"]:
        return out
    slots = color_slots(objs)
    out.update(
        ok=True,
        size_mm=[round(v, 2) for v in res["size_mm"]],
        slots=slots,
        groups=group_plan(objs, slots),
        quality=quality_summary(objs),
    )
    return out
