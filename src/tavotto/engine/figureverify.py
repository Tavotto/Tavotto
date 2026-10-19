"""TAF Figure Verifier（ADR 0138 §二，Phase 2）：在「改前 / 改后」两张 manifest 上客观判定图任务的断言。纯标准库，Flask 父进程 import。

主语：Tavotto worker 产出的 manifest（`engine/manifest.py`）——元素的 `editable` 当前值、`bbox`、只读的
`data_fingerprint`。不看 Agent 的自述，不看像素（像素相似不等于数据没变，ADR 0138 §二）。

断言类型与 FigureBench 同名（`tests/fixtures/figurebench/`，闭集 `ASSERTION_KINDS`）；FigureBench 的探针从原生 matplotlib
读同一组事实，`tests/test_figure_verifier.py` 在全部探针场景上对拍：参考解 → verified、反例 → failed。

**看不见就是 unknown**（`03-contracts.md` §四）：manifest 里没有这个事实（共享轴、误差棒线宽）、数据指纹超预算 / 量不了、
断言指名的对象改前就不存在 → unknown；对象改前在、改后没了 → failed（被删）。unknown 永不折算成 verified。

L3 的「这次修改新引入的版式问题」只复用 `interference.detect` 与 `preflight.run`，不复写判据；只报改后新出现的问题
（改前就有的不算这次的账）。
"""

from __future__ import annotations

from . import interference, preflight, profiles
from .ai_tasks import FAILED, UNKNOWN, VERIFIED, CheckResult

#: 与 FigureBench 同名的断言闭集 → 所属验证层
KINDS: dict[str, str] = {
    "line_property": "L3",
    "line_property_unchanged": "L3",
    "series_data_unchanged": "L2",
    "series_count_unchanged": "L2",
    "text_equals": "L3",
    "text_contains": "L3",
    "text_unchanged": "L2",
    "fontsize": "L3",
    "fontsize_unchanged": "L3",
    "legend_loc": "L3",
    "axis_scale": "L2",
    "axis_scale_unchanged": "L2",
    "label_alignment": "L3",
    "shared_x": "L2",
    "fig_size": "L3",
    "fig_size_unchanged": "L3",
    "axes_count_unchanged": "L1",
    "errorbar_linewidth": "L3",
    "errorbar_data_unchanged": "L2",
    "errorbar_points_unchanged": "L2",
    "image_cmap": "L3",
    "image_data_unchanged": "L2",
    "image_clim_unchanged": "L2",
    "bar_property": "L3",
    "bar_property_unchanged": "L3",
    "bar_heights_unchanged": "L2",
    "bar_count_unchanged": "L2",
}

#: manifest 说不出的事实：判 unknown，并写明为什么（不是验证器偷懒，是证据不在 manifest 里）
NOT_OBSERVABLE: dict[str, str] = {
    "shared_x": "manifest 不带 axes 之间的共享关系",
    "errorbar_linewidth": "manifest 的误差棒 linewidth 字段取的是数据线，不是误差线（elinewidth）",
}

_WHICH = {"title": "title", "xlabel": "xlabel", "ylabel": "ylabel"}
_LINE_PROPS = {"color", "linewidth", "linestyle", "markersize"}
_BAR_PROPS = {"color": "facecolor", "width": "bar_width"}
#: manifest 把数值字段舍入到两位小数（线宽 / 字号）、尺寸到 0.1 mm：比较的容差跟着这个精度走
_NUM_TOL = 0.006
_SIZE_TOL_IN = 0.1 / 25.4 + 1e-9


class _Missing(LookupError):
    """断言指名的对象在这一张 manifest 里找不到。"""


class _Unobservable(Exception):
    """对象在，但这一项量不出来（指纹超预算 / 量不了）。"""


def _elements(man: dict) -> dict[str, dict]:
    return {e.get("gid", ""): e for e in man.get("elements") or [] if isinstance(e, dict)}


def _field(el: dict, prop: str):
    for f in el.get("editable") or []:
        if f.get("prop") == prop:
            return f.get("value")
    raise _Missing(prop)


def _el(man: dict, gid: str) -> dict:
    el = _elements(man).get(gid)
    if el is None:
        raise _Missing(gid)
    return el


def _series(man: dict, axes: int, label: str) -> dict:
    prefix = f"axes_{axes}."
    hits = [
        e
        for gid, e in _elements(man).items()
        if gid.startswith(prefix) and e.get("role") == "line" and _label_of(e) == label
    ]
    if len(hits) != 1:
        raise _Missing(f"axes_{axes} 里 label={label!r} 的曲线")
    return hits[0]


def _label_of(el: dict):
    try:
        return _field(el, "label")
    except _Missing:
        return None


def _text_el(man: dict, a: dict) -> dict:
    return _el(man, f"axes_{a['axes']}.{_WHICH[a['which']]}")


def _fingerprint_part(el: dict, part: str) -> dict:
    fp = el.get("data_fingerprint")
    if not isinstance(fp, dict):
        raise _Unobservable("元素没有数据指纹")
    if fp.get("status") != "ok":
        raise _Unobservable(f"数据指纹 {fp.get('status')}")
    got = (fp.get("parts") or {}).get(part)
    if got is None:
        raise _Unobservable(f"数据指纹缺 {part}")
    return got


def _same_part(after_el: dict, before_el: dict, part: str) -> bool:
    a, b = _fingerprint_part(after_el, part), _fingerprint_part(before_el, part)
    return a.get("sha256") == b.get("sha256") and a.get("shape") == b.get("shape")


def _num_eq(x, y, tol: float = _NUM_TOL) -> bool:
    return abs(float(x) - float(y)) <= tol


def _value_eq(got, expected) -> bool:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return _num_eq(got, expected)
    if isinstance(expected, str) and isinstance(got, str) and expected.startswith("#"):
        return got.lower() == expected.lower()
    return got == expected


def _edge(bbox: list, edge: str) -> float:
    """manifest 的 bbox 是 figure 分数、y 向下的 `[x0, top, w, h]`；换成 y 向上的边，与探针口径一致。"""
    x0, top, w, h = (float(v) for v in bbox)
    return {"x0": x0, "x1": x0 + w, "y0": 1.0 - (top + h), "y1": 1.0 - top}[edge]


def _subject_gids(a: dict, man: dict) -> list[str]:
    """断言指名的对象在这张 manifest 里是哪些 gid（只用来判「改前在不在」，判不出就空）。"""
    try:
        k = a["kind"]
        if "series" in a:
            return [_series(man, a["axes"], a["series"])["gid"]]
        if "which" in a and isinstance(a.get("axes"), int):
            return [_text_el(man, a)["gid"]]
        if k.startswith("errorbar"):
            return [_el(man, f"axes_{a['axes']}.errorbar_{a['index']}")["gid"]]
        if k.startswith("bar"):
            return [_el(man, f"axes_{a['axes']}.barseries_{a['index']}")["gid"]]
        if k.startswith("image"):
            return [_el(man, f"axes_{a['axes']}.images_0")["gid"]]
        if k == "legend_loc":
            return [_el(man, f"axes_{a['axes']}.legend")["gid"]]
    except (_Missing, KeyError, TypeError):
        return []
    return []


def _holds(a: dict, after: dict, before: dict) -> bool:
    """断言在 `after` 上成立吗（`before` 是改前）。对象找不到抛 `_Missing`，量不出抛 `_Unobservable`。"""
    k = a["kind"]
    if k == "line_property":
        if a["prop"] not in _LINE_PROPS:
            raise _Unobservable(f"未登记的曲线属性 {a['prop']}")
        return _value_eq(_field(_series(after, a["axes"], a["series"]), a["prop"]), a["expected"])
    if k == "line_property_unchanged":
        return _field(_series(after, a["axes"], a["series"]), a["prop"]) == _field(
            _series(before, a["axes"], a["series"]), a["prop"]
        )
    if k == "series_data_unchanged":
        return _same_part(
            _series(after, a["axes"], a["series"]), _series(before, a["axes"], a["series"]), "xy"
        )
    if k == "series_count_unchanged":

        def count(man):
            prefix = f"axes_{a['axes']}."
            return sum(
                1
                for g, e in _elements(man).items()
                if g.startswith(prefix) and e.get("role") == "line"
            )

        return count(after) == count(before)
    if k in ("text_equals", "text_contains", "text_unchanged"):
        got = _field(_text_el(after, a), "text")
        if k == "text_equals":
            return got == a["expected"]
        if k == "text_contains":
            return a["expected"] in got
        return got == _field(_text_el(before, a), "text")
    if k == "fontsize":
        return _num_eq(_field(_text_el(after, a), "fontsize"), a["expected"])
    if k == "fontsize_unchanged":
        return _num_eq(
            _field(_text_el(after, a), "fontsize"), _field(_text_el(before, a), "fontsize")
        )
    if k == "legend_loc":
        return _field(_el(after, f"axes_{a['axes']}.legend"), "loc") == a["expected"]
    if k in ("axis_scale", "axis_scale_unchanged"):
        prop = a["axis"] + "scale"
        got = _field(_el(after, f"axes_{a['axes']}"), prop)
        want = (
            a["expected"] if k == "axis_scale" else _field(_el(before, f"axes_{a['axes']}"), prop)
        )
        return got == want
    if k == "label_alignment":
        vals = [
            _edge(_el(after, f"axes_{i}.{_WHICH[a['which']]}")["bbox"], a["edge"])
            for i in a["axes"]
        ]
        return max(vals) - min(vals) <= a["tol"]
    if k in ("fig_size", "fig_size_unchanged"):
        i = 1 if a["dim"] == "height" else 0
        got = float(_field(_el(after, "figure"), "size_mm")[i]) / 25.4
        want = (
            a["expected"]
            if k == "fig_size"
            else float(_field(_el(before, "figure"), "size_mm")[i]) / 25.4
        )
        return abs(got - want) <= _SIZE_TOL_IN
    if k == "axes_count_unchanged":

        def axes(man):
            return sum(1 for e in _elements(man).values() if e.get("role") in ("axes", "axes3d"))

        return axes(after) == axes(before)
    if k in ("errorbar_data_unchanged", "errorbar_points_unchanged"):
        gid = f"axes_{a['axes']}.errorbar_{a['index']}"
        return _same_part(
            _el(after, gid),
            _el(before, gid),
            "bars" if k == "errorbar_data_unchanged" else "points",
        )
    if k in ("image_cmap", "image_data_unchanged", "image_clim_unchanged"):
        gid = f"axes_{a['axes']}.images_0"
        if k == "image_cmap":
            return _field(_el(after, gid), "cmap") == a["expected"]
        if k == "image_data_unchanged":
            return _same_part(_el(after, gid), _el(before, gid), "array")
        return all(
            _num_eq(_field(_el(after, gid), p), _field(_el(before, gid), p), 1e-9)
            for p in ("vmin", "vmax")
        )
    if k in (
        "bar_property",
        "bar_property_unchanged",
        "bar_heights_unchanged",
        "bar_count_unchanged",
    ):
        gid = f"axes_{a['axes']}.barseries_{a['index']}"
        if k == "bar_property":
            if a["prop"] not in _BAR_PROPS:
                raise _Unobservable(f"未登记的柱属性 {a['prop']}")
            return _value_eq(_field(_el(after, gid), _BAR_PROPS[a["prop"]]), a["expected"])
        if k == "bar_property_unchanged":
            prop = _BAR_PROPS.get(a["prop"], a["prop"])
            return _field(_el(after, gid), prop) == _field(_el(before, gid), prop)
        if k == "bar_heights_unchanged":
            return _same_part(_el(after, gid), _el(before, gid), "bars")
        return (
            _fingerprint_part(_el(after, gid), "bars")["shape"][:1]
            == _fingerprint_part(_el(before, gid), "bars")["shape"][:1]
        )
    raise _Unobservable(f"未登记的断言类型 {k}")


def check(
    a: dict, after: dict, before: dict, *, role: str, stage: str = "postcommit"
) -> CheckResult:
    """把一条断言判成一条 `CheckResult`。`role` 由调用方给（目标 / 保持）：同一类断言在不同任务里角色不同。"""
    kind = a.get("kind")
    layer = KINDS.get(kind, "L3")
    base = {
        "id": a.get("id", kind or "?"),
        "layer": layer,
        "stage": stage,
        "role": role,
        "method": "manifest",
    }
    if kind in NOT_OBSERVABLE:
        return CheckResult(
            **base,
            verdict=UNKNOWN,
            reason_code="not_observable",
            not_covered=(NOT_OBSERVABLE[kind],),
        )
    try:
        ok = _holds(a, after, before)
    except _Unobservable as exc:
        return CheckResult(
            **base, verdict=UNKNOWN, reason_code="not_observable", not_covered=(str(exc),)
        )
    except (_Missing, KeyError, IndexError, TypeError, ValueError):
        # 改前在、改后没了 = 被删（failed）；改前就找不到 = 量不了（unknown）
        existed = bool(_subject_gids(a, before))
        return CheckResult(
            **base,
            verdict=FAILED if existed else UNKNOWN,
            reason_code="subject_removed" if existed else "subject_not_found",
        )
    return CheckResult(
        **base, verdict=VERIFIED if ok else FAILED, reason_code=None if ok else "assertion_failed"
    )


def verify(
    goal: list[dict], preserve: list[dict], after: dict, before: dict, *, stage: str = "postcommit"
) -> list[CheckResult]:
    """一组目标 + 保持断言 → 检查结果。"""
    return [check(a, after, before, role="goal", stage=stage) for a in goal] + [
        check(a, after, before, role="preserve", stage=stage) for a in preserve
    ]


def _issue_keys(man: dict, profile: dict) -> set[tuple[str, tuple[str, ...]]]:
    issues = interference.detect(man, profile) + preflight.run(
        preflight.spec_from_manifest(man), profile
    )
    return {
        (i["id"], tuple(sorted(i.get("gids") or ())))
        for i in issues
        if i.get("severity") in ("error", "warn")
    }


def layout_regressions(
    after: dict, before: dict, *, profile: dict | None = None, stage: str = "postcommit"
) -> CheckResult:
    """L3：这次修改**新引入**的 error / warn 级版式问题（文字重叠、出界、字号过小……）。改前就有的不算这次的账。"""
    prof = profile if profile is not None else profiles.load()
    base = {
        "id": "L3.no_new_layout_issues",
        "layer": "L3",
        "stage": stage,
        "role": "preserve",
        "method": "interference+preflight",
    }
    try:
        new = sorted(_issue_keys(after, prof) - _issue_keys(before, prof))
    except Exception:  # noqa: BLE001 —— 判据自己崩了是 unknown，不是通过
        return CheckResult(**base, verdict=UNKNOWN, reason_code="verifier_error")
    if new:
        return CheckResult(
            **base,
            verdict=FAILED,
            reason_code="new_layout_issues",
            observed={"issues": [{"id": i, "gids": list(g)} for i, g in new]},
        )
    return CheckResult(**base, verdict=VERIFIED)


__all__ = ["KINDS", "NOT_OBSERVABLE", "check", "layout_regressions", "verify"]
