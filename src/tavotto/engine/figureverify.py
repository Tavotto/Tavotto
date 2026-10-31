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

import math
import re

from . import interference, preflight, profiles
from .ai_tasks import FAILED, NOT_APPLICABLE, UNKNOWN, VERIFIED, CheckResult

#: 与 FigureBench 同名的断言闭集 → 所属验证层
KINDS: dict[str, str] = {
    "line_property": "L3",
    "line_property_unchanged": "L3",
    "series_data_unchanged": "L2",
    "line_label": "L3",
    "line_data_unchanged": "L2",
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
        if k in ("line_label", "line_data_unchanged"):
            return [_el(man, f"axes_{a['axes']}.lines_{a['index']}")["gid"]]
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
        # 只按轴 / 图定位的断言：主语就是 `_holds` 里 `_el` 查的那个元素，同一个查找函数
        if k in ("axis_scale", "axis_scale_unchanged"):
            return [_el(man, f"axes_{a['axes']}")["gid"]]
        if k in ("fig_size", "fig_size_unchanged"):
            return [_el(man, "figure")["gid"]]
        if k == "label_alignment":
            # 对齐的是多个轴上的标签：改前要全都在才算主语（有一个改前就没有 = 本来就量不了），否则空
            return [_el(man, f"axes_{i}.{_WHICH[a['which']]}")["gid"] for i in a["axes"]]
    except (_Missing, KeyError, TypeError):
        return []
    return []


#: 同一 gid 上身份（ADR 0083）变了时的三种结论（`_identity_change`）
RENAMED = (
    "renamed"  # 数据逐部件相同、新名字没被别的数据元素用过 = 只是改了名（用户点名改图例也是这样）
)
SWAPPED = "swapped"  # 数据相同，但新名字是同图里另一个数据元素的旧名字 = 名字对调 / 抢了别人的名字（贴错标签）
REPLACED = "replaced"  # 数据也不同（或量不了）= 主体被删、后面的对象重编号顶了上来


#: 按 `lines_N` 序号定位的两类断言：用户点名改图例名字时，名字是被改的东西、数据是要保持的东西，两者都不能靠 label 找曲线
RENAME_AWARE_KINDS = frozenset({"line_label", "line_data_unchanged"})


def _identity_of(el: dict | None) -> str | None:
    v = (el or {}).get("identity")
    return v if isinstance(v, str) and v else None


def _same_data(old: dict, new: dict) -> bool:
    """两侧数据指纹都量到了（`ok`）且逐部件相同。任一侧没量到 / 不是数据元素 = 不能说「数据没变」。"""
    bfp, afp = old.get("data_fingerprint"), new.get("data_fingerprint")
    return (
        isinstance(bfp, dict)
        and isinstance(afp, dict)
        and bfp.get("status") == "ok"
        and afp.get("status") == "ok"
        and afp.get("parts") == bfp.get("parts")
    )


def _identity_change(
    gid: str, old: dict, new: dict, before_els: dict[str, dict]
) -> tuple[str, list[str]] | None:
    """同一 gid 在改前 / 改后的身份冲突怎么定性：`(RENAMED | SWAPPED | REPLACED, 被抢名字的 gid)`；没有冲突回 None。

    两侧都有 identity 且不同才算冲突；任一侧缺 identity（没有显式 label 的对象，ADR 0083 §二「已知边界」）不比，维持
    按位置匹配的现状——不因缺字段把结论变严或变松。身份 = 显式 label 的摘要，所以「身份变了」有两种来源：改了名（数据没动），
    或后面的对象重编号顶上了这个 gid（数据也变了）；数据指纹是把两者分开的独立证据（ADR 0083 §五）。
    被抢名字的判据只看**改前**的数据元素：新名字等于改前另一个数据元素的旧名字 = 这个数据现在顶着别人的名字。"""
    o, n = _identity_of(old), _identity_of(new)
    if o is None or n is None or o == n:
        return None
    if not _same_data(old, new):
        return REPLACED, []
    taken = sorted(
        h
        for h, el in before_els.items()
        if h != gid and isinstance(el.get("data_fingerprint"), dict) and _identity_of(el) == n
    )
    return (SWAPPED, taken) if taken else (RENAMED, [])


def _subject_replaced(a: dict, after: dict, before: dict) -> bool:
    """改前主体的 gid 在改后还在，但身份（ADR 0083）变了 = 主体被删、后面的对象重编号顶了上来。

    只有**按序号定位、本来就在谈改名**的两类断言（`RENAME_AWARE_KINDS`：曲线名字 / 曲线数据）把「只改名」（数据逐部件相同、
    名字没被别的数据元素用过，`RENAMED`）当成主体还在；其余断言（按 label 找曲线、按位置的样式 / 文字断言……）身份一变仍算顶替，
    与引入改名判定之前逐字相同。缺 identity 的一侧不比（见 `_identity_change`）。
    """
    aware = a.get("kind") in RENAME_AWARE_KINDS
    after_els = _elements(after)
    before_els = _elements(before)
    for gid in _subject_gids(a, before):
        new, old = after_els.get(gid), before_els.get(gid)
        if new is None or old is None:
            continue
        change = _identity_change(gid, old, new, before_els)
        if change is not None and not (aware and change[0] == RENAMED):
            return True
    return False


_AXES_GID = re.compile(r"axes_\d+")


def _axes_count(man: dict) -> int:
    return sum(1 for g in _elements(man) if _AXES_GID.fullmatch(g))


def _identity_confirms(a: dict, after: dict, before: dict) -> bool:
    """改前主体在改后同 gid 处、两侧 identity 都有且相等——位置之外有独立证据说它还是同一个对象。"""
    after_els, before_els = _elements(after), _elements(before)
    gids = _subject_gids(a, before)
    for gid in gids:
        o = (before_els.get(gid) or {}).get("identity")
        n = (after_els.get(gid) or {}).get("identity")
        if not (isinstance(o, str) and o and o == n):
            return False
    return bool(gids)


def _positions_shifted(a: dict, after: dict, before: dict) -> bool:
    """按轴序号定位的断言，而轴的个数前后不同：没有显式 label 的轴没有 identity（ADR 0083 §二），删掉前面的子图后
    后面的轴会顶上同一个 `axes_N`——这时按位置得到的「成立」不可信，记 unknown（不是 verified，也不臆断 failed）。"""
    if "axes" not in a or _axes_count(after) == _axes_count(before):
        return False
    return not _identity_confirms(a, after, before)


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
    if k == "line_label":
        # 按 gid 序号定位（不按 label——label 正是被改的东西）：这条线现在的显式名字
        return _field(_el(after, f"axes_{a['axes']}.lines_{a['index']}"), "label") == a["expected"]
    if k == "line_data_unchanged":
        gid = f"axes_{a['axes']}.lines_{a['index']}"
        return _same_part(_el(after, gid), _el(before, gid), "xy")
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
    if _subject_replaced(a, after, before):
        # 位置式 gid 在删前面的子图 / 曲线后会重编号，后面的对象顶上同一个 gid：gid 在不等于主体还在
        return CheckResult(**base, verdict=FAILED, reason_code="subject_removed")
    shifted = _positions_shifted(a, after, before)
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
    if shifted:
        return CheckResult(**base, verdict=UNKNOWN, reason_code="subject_renumbered")
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


def data_unchanged(after: dict, before: dict, *, stage: str = "postcommit") -> list[CheckResult]:
    """figure_edit 的默认保持断言「数据不变」（`03-contracts.md` §四）：改前每个带 `data_fingerprint` 的元素，改后同 gid
    （且 identity 不冲突）的指纹逐部件相同 → verified；不同 → failed（`data_changed`）；改后没了 / 被别的对象顶替 →
    failed（`subject_removed`）；任一侧这次没量（over_budget / unavailable）→ unknown。

    **改名不是顶替**（ADR 0083 §五）：同 gid 的 identity（显式 label 的摘要）变了，但数据指纹逐部件相同 → verified，
    并在 `observed.relabeled` 记 `{from_identity, to_identity}`；identity 变了且数据也变 → `subject_removed`；新名字等于
    改前另一个数据元素的旧名字（名字对调 / 抢名字，数据顶着别人的名字）→ failed（`label_swapped`）。改后新出现的数据元素不在这里判
    （新增一条曲线可以就是用户要的）。改前一个数据元素都没有 → 空列表（调用方据此记 not_applicable）。"""
    out: list[CheckResult] = []
    after_els = _elements(after)
    before_els = _elements(before)
    for gid, old in sorted(before_els.items()):
        bfp = old.get("data_fingerprint")
        if not isinstance(bfp, dict):
            continue
        base = {
            "id": f"data.{gid}",
            "layer": "L2",
            "stage": stage,
            "role": "preserve",
            "method": "data_fingerprint",
        }
        new = after_els.get(gid)
        if new is None:
            out.append(CheckResult(**base, verdict=FAILED, reason_code="subject_removed"))
            continue
        change = _identity_change(gid, old, new, before_els)
        if change is not None:
            kind, taken = change
            if kind == RENAMED:
                # 数据逐部件相同、只是显式 label 变了：数据这一项**通过**，并如实记下改名（只记身份摘要，不记 label
                # 原文，ADR 0083）——提交后的改名由后端报给前端，让画布上已有的编辑跟着换身份（§五）
                out.append(
                    CheckResult(
                        **base,
                        verdict=VERIFIED,
                        observed={
                            "relabeled": {
                                "from_identity": _identity_of(old),
                                "to_identity": _identity_of(new),
                            }
                        },
                    )
                )
            elif kind == SWAPPED:
                out.append(
                    CheckResult(
                        **base,
                        verdict=FAILED,
                        reason_code="label_swapped",
                        observed={"taken_from": taken},
                    )
                )
            else:
                out.append(CheckResult(**base, verdict=FAILED, reason_code="subject_removed"))
            continue
        afp = new.get("data_fingerprint")
        if bfp.get("status") != "ok" or not isinstance(afp, dict) or afp.get("status") != "ok":
            out.append(CheckResult(**base, verdict=UNKNOWN, reason_code="not_measured"))
            continue
        if afp.get("parts") == bfp.get("parts"):
            out.append(CheckResult(**base, verdict=VERIFIED))
        else:
            changed = sorted(
                k
                for k in (afp.get("parts") or {}).keys() | (bfp.get("parts") or {}).keys()
                if (afp.get("parts") or {}).get(k) != (bfp.get("parts") or {}).get(k)
            )
            out.append(
                CheckResult(
                    **base, verdict=FAILED, reason_code="data_changed", observed={"parts": changed}
                )
            )
    return out


def data_matches_expected(
    man: dict, expected: list[dict], *, stage: str = "postcommit"
) -> list[CheckResult]:
    """data_to_figure 的保持断言「画的就是这份数据」：`expected` 的每项 `{label, part, digest}`（父进程按同一解析规则从 CSV
    算出，`ai_data_figure.expected_fingerprints`）在 manifest 里找 **label 唯一相同**的数据元素，`data_fingerprint.parts[part]`
    的 sha256 与形状都相等 → verified；不等 → failed（`data_mismatch`）；找不到 / 不唯一 → failed（`series_missing`）；
    没量（over_budget / unavailable）→ unknown。"""
    out: list[CheckResult] = []
    for e in expected:
        base = {
            "id": f"data.{e['label']}",
            "layer": "L2",
            "stage": stage,
            "role": "preserve",
            "method": "data_fingerprint",
        }
        hits = [
            el
            for el in _elements(man).values()
            if isinstance(el.get("data_fingerprint"), dict) and _label_of(el) == e["label"]
        ]
        if len(hits) != 1:
            out.append(
                CheckResult(
                    **base,
                    verdict=FAILED,
                    reason_code="series_missing",
                    observed={"matches": len(hits)},
                )
            )
            continue
        try:
            got = _fingerprint_part(hits[0], e["part"])
        except _Unobservable:
            out.append(CheckResult(**base, verdict=UNKNOWN, reason_code="not_measured"))
            continue
        want = e["digest"]
        same = got.get("sha256") == want.get("sha256") and got.get("shape") == want.get("shape")
        out.append(
            CheckResult(**base, verdict=VERIFIED)
            if same
            else CheckResult(
                **base,
                verdict=FAILED,
                reason_code="data_mismatch",
                observed={"shape": got.get("shape"), "expected_shape": want.get("shape")},
            )
        )
    return out


def _editable_values(el: dict) -> dict:
    return {
        f.get("prop"): f.get("value")
        for f in el.get("editable") or []
        if isinstance(f, dict) and isinstance(f.get("prop"), str)
    }


def _same_values(x, y) -> bool:
    if isinstance(x, bool) or isinstance(y, bool):
        return x == y
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return _num_eq(x, y)
    if isinstance(x, (list, tuple)) and isinstance(y, (list, tuple)):
        return len(x) == len(y) and all(_same_values(a, b) for a, b in zip(x, y, strict=True))
    if isinstance(x, dict) and isinstance(y, dict):
        return x.keys() == y.keys() and all(_same_values(x[k], y[k]) for k in x)
    if isinstance(x, str) and isinstance(y, str) and x.startswith("#"):
        return x.lower() == y.lower()
    return x == y


#: 轴上由自动刻度器 / 自动缩放算出来的量：它们随数据、刻度类型、坐标范围、被点的轴上的任何「真改动」而变，本身不是谁改的
_AUTO_AXES_PROPS = frozenset({"xlim", "ylim"})
_AUTO_TICKS_MAJOR = frozenset({"major_values", "major_step"})
_AUTO_TICKS_MINOR = frozenset({"minor_values", "minor_step", "minor_visible", "minor_format"})
#: 只有文字 / 图例类元素的改动不会移动坐标范围与刻度，不能当作「自动量」的起因
_TEXT_ONLY_ROLES = frozenset({"title", "axis_label", "legend", "legend_text", "text", "figure"})
#: 色条与它的映射对象共享的色图字段
_CMAP_PROPS = frozenset({"cmap", "vmin", "vmax"})
_AXIS_LABEL_GID = re.compile(r"(axes_\d+)\.(xlabel|ylabel)$")


def _owners(gid: str, prop: str | None, els: dict[str, dict]) -> set[str]:
    """一条改动「算谁的账」：它自己，加上 manifest 里明说它跟着谁走的那些对象（结构，不是按名字猜）。

    * 图例项的 `handle_*`（示意线 / 色块）与 `text`（图例文字由曲线 label 生成，改名后跟着变）跟着 `legend_entry.source_gid`
      那条曲线 / 柱组走；
    * 图像与色条的 `cmap / vmin / vmax` 共享同一个映射（图像的 `cmap_group_gids`、色条的 `mappable_gid`）；
    * 色条的 `label` 就是色条所在轴的 y（水平色条为 x）轴标题——同一个文字，两处出现；反过来亦然。
    """
    owners = {gid}
    el = els.get(gid) or {}
    role = el.get("role")
    if role == "legend_text" and (prop == "text" or (prop or "").startswith("handle_")):
        src = (el.get("legend_entry") or {}).get("source_gid")
        if isinstance(src, str):
            owners.add(src)
    if prop in _CMAP_PROPS:
        if role == "colorbar" and isinstance(el.get("mappable_gid"), str):
            owners.add(el["mappable_gid"])
        for g in el.get("cmap_group_gids") or ():
            if isinstance(g, str):
                owners.add(g)
    if role == "colorbar" and prop == "label" and isinstance(el.get("geom_gid"), str):
        owners |= {f"{el['geom_gid']}.xlabel", f"{el['geom_gid']}.ylabel"}
    m = _AXIS_LABEL_GID.match(gid)
    if prop == "text" and m:
        owners |= {
            g
            for g, e in els.items()
            if e.get("role") == "colorbar" and e.get("geom_gid") == m.group(1)
        }
    return owners


_AXES_PREFIX = re.compile(r"axes_\d+")
_TICK_AXIS = re.compile(r"\.([xyz])tick")


def _range_is_autoscaled(prop: str, new: dict, old: dict) -> bool:
    """范围是不是自动缩放算的。`autoscale` 开关证明不了（`set_xlim(auto=True)`、先设再重开自动缩放，两侧都是 True），
    dataLim 的变化也证明不了（改柱宽的同时显式放宽范围，新范围照样「包住」新数据）。能证明的只有一件事：manifest 记下的
    `autoscale.expected`——自动缩放**此刻会算出**的范围——与实际范围相等。所以两条同时成立：改前改后两侧自动缩放都开着；
    改后实际范围（排序后）与改后 `expected` 在容差内相等。manifest 没发 `expected`（旧版本 / 合成输入 / 探测失败）= 没证据。
    sharex 兄弟轴：`autoscale_view` 本身按共享轴的数据并集算，`expected` 已含。
    剩余宽松：脚本显式写的范围恰好等于自动缩放会给的范围——此时画面与自动缩放逐位一致，视为自动。"""
    axis = prop[0]
    if [((e or {}).get("autoscale") or {}).get(axis) for e in (new, old)] != [True, True]:
        return False
    exp = (((new.get("autoscale") or {}).get("expected")) or {}).get(axis)
    lim = _editable_values(new).get(prop)
    try:
        elo, ehi = sorted(float(v) for v in exp)
        lo, hi = sorted(float(v) for v in lim)
    except (TypeError, ValueError):
        return False
    if not all(map(math.isfinite, (elo, ehi, lo, hi))):
        return False
    tol = 1e-9 * max(1.0, abs(ehi - elo))
    return abs(lo - elo) <= tol and abs(hi - ehi) <= tol


def _auto_kind(
    gid: str,
    prop: str | None,
    new: dict | None,
    old: dict | None,
    els: dict[str, dict],
) -> tuple[str, str | None] | None:
    """这条改动是不是「自动算出来的量」。返回 `("range", 轴字母)`、`("layout", None)` 或 None（不是）。

    * `range`：轴的自动缩放范围、自动刻度器给出的刻度值、刻度文字（刻度器 + 格式化器的输出）。**范围只有在改前改后两侧都还
      开着自动缩放、且改后实际范围等于 manifest 记下的「自动缩放此刻会算出的范围」时才算自动**（`_range_is_autoscaled`：
      `autoscale` 开关加 `expected`；开关与 dataLim 都证明不了范围没被显式写）；manifest 没发这些事实（旧版本 / 合成输入）= 没证据，不免责；
    * `layout`：轴的 `position`（tight_layout / constrained 随文字尺寸重排）。
    色条轴的范围由映射对象的 vmin/vmax 经 `set_ylim` 写入（自动缩放是关的），是派生量：照旧算自动。"""
    role = (new or old or {}).get("role")
    if role == "ticklabel" or role == "ticks":
        m = _TICK_AXIS.search(gid)
        axis = m.group(1) if m else None
        if role == "ticks":
            if prop is None or new is None or old is None:
                return None
            for props, mode in (
                (_AUTO_TICKS_MAJOR, "major_mode"),
                (_AUTO_TICKS_MINOR, "minor_mode"),
            ):
                if prop in props:
                    got = (_editable_values(new).get(mode), _editable_values(old).get(mode))
                    return ("range", axis) if got == ("auto", "auto") else None
            return None
        return ("range", axis)  # 刻度文字
    if role == "axes" and prop == "position":
        return ("layout", None)
    if role == "axes" and prop in _AUTO_AXES_PROPS:
        axis = prop[0]
        if (new or {}).get("is_colorbar") and (old or {}).get("is_colorbar"):
            return ("range", axis)
        if new is None or old is None:
            return None
        return ("range", axis) if _range_is_autoscaled(prop, new, old) else None
    return None


def _range_cause_axes(gid: str, axis: str | None, els: dict[str, dict]) -> set[str] | None:
    """一条自动范围改动的起因必须落在哪些 `axes_N` 里：它自己的轴，加上与它共享这根轴范围的兄弟轴（sharex / twinx）。
    None = 任何地方的起因都行（色条轴：范围来自映射对象，映射对象可以在别的轴里）。"""
    m = _AXES_PREFIX.match(gid)
    if m is None:
        return set()
    ax = els.get(m.group(0)) or {}
    if ax.get("is_colorbar"):
        return None
    peers = ((ax.get("autoscale") or {}).get("peers") or {}).get(axis or "", [])
    return {m.group(0), *(p for p in peers if isinstance(p, str))}


def _in_target(gid: str, *, scope: str, target: str | None) -> bool:
    if scope == "figure":
        return True
    if not target or scope == "none":
        return False
    if scope == "axes":
        m = _AXES_GID.match(target)
        if m is None:
            return gid == target or gid.startswith(target + ".")
        return gid == m.group(0) or gid.startswith(m.group(0) + ".")
    return gid == target or gid.startswith(target + ".")


def _changes(after: dict, before: dict) -> list[tuple[str, str | None]]:
    """改前 / 改后 manifest 之间 `editable` 值不同的 `(gid, prop)`；只在一侧出现的元素、身份冲突（ADR 0083）的元素记 `(gid, None)`。"""
    a_els, b_els = _elements(after), _elements(before)
    out: list[tuple[str, str | None]] = []
    for gid in sorted(a_els.keys() | b_els.keys()):
        new, old = a_els.get(gid), b_els.get(gid)
        if new is None or old is None:
            out.append((gid, None))
            continue
        o, n = old.get("identity"), new.get("identity")
        if isinstance(o, str) and isinstance(n, str) and o and n and o != n:
            out.append((gid, None))
            continue
        nv, ov = _editable_values(new), _editable_values(old)
        out += [
            (gid, k)
            for k in sorted(nv.keys() | ov.keys())
            if k not in nv or k not in ov or not _same_values(nv[k], ov[k])
        ]
    return out


def changes_outside_target(
    after: dict, before: dict, *, scope: str, gid: str | None, stage: str = "postcommit"
) -> list[CheckResult]:
    """L2 保持断言「目标范围以外没有改动」（`05-verification-design.md` 第七节）。

    `before` / `after` 是同一个 stem 的两张 manifest，**两边都不带用户 override**（调用方保证）。逐元素按 gid 配对，比
    `editable` 的 `{prop: value}`；只在一侧出现 / 身份不同的元素也算改动。范围：`element` = 目标 gid 及其后代（`gid.` 前缀）；
    `axes` = 目标所在的 `axes_N`；`figure` 或缺 gid = 整张图都是目标 → `not_applicable`（`whole_figure_target`）；
    `none` = 这个 stem 里没有目标（目标在别的 stem）。

    范围外有改动 → **unknown**（`changes_outside_target`，`observed` 带前 20 个 gid 与总数），**不是 failed**：用户的话可能本来
    就要改多处（说「两条都改」却只选中一条），系统判不了意图，如实说「这些地方也变了，请确认」，结局最高 partial。

    不算范围外的派生改动——都按结构判，不看场景：
    1. **跟随目标的对象**：`_owners`——图例项的 `handle_*` 与文字跟着它代表的那条曲线 / 柱组；图像与色条共享 `cmap/vmin/vmax`；色条
       的 `label` 与所在轴的轴标题是同一个文字。它们的账记在 manifest 明说的那个主人名下，主人在范围内就在范围内。
       柱子（`barseries_N.bar_M`）是柱组的后代，已被前缀覆盖。
    2. **自动算出来的量**（`_auto_kind`）：轴的 `xlim / ylim`（**两侧自动缩放开着 + 数据范围真变了 + 新范围包住它才算**；显式 `set_[xy]lim` 不是缩放算的）、
       `major_mode / minor_mode` 都是 `auto` 时刻度器的输出（`major_values / major_step / minor_*`）、刻度文字元素（刻度器 +
       格式化器的输出，格式字段在 `ticks` 元素上另算）、轴的版面 `position`（tight_layout / constrained 随文字尺寸重排）。
       它们**只在有起因时**免责：范围类（范围 / 刻度 / 刻度文字）的起因必须在**同一个 `axes_N`**（或与它共享这根轴范围的
       sharex / twinx 兄弟轴，manifest `autoscale.peers`）里，是范围内一条非「只有文字」角色（`_TEXT_ONLY_ROLES`）元素的真改动
       （线宽 / 色图范围 / 柱宽 / 刻度类型……）；色条轴的范围来自映射对象的 vmin/vmax：起因必须是这个色条（或其映射对象 / 同色图组）的 `vmin/vmax` 真改动，可在别的轴里；Agent 单独 `cbar.ax.set_ylim` 不被放过（已知宽松：同时改了 vmin/vmax 又显式写色条轴范围）。`position` 是全图重排，起因是范围内
       任一真改动（已知宽松：目标内有真改动时，别处显式 `set_position` 也被当重排放过）。没有起因的、或显式设的范围变化照常算范围外。
    3. **不比** `bbox / geometry / paint_order` 等只读几何：它们不在 `editable` 里，是渲染结果，随上面任一类改动而变。
    """
    base = {
        "id": "scope.outside_target",
        "layer": "L2",
        "stage": stage,
        "role": "preserve",
        "method": "manifest_editable_diff",
    }
    if scope == "figure" or (scope != "none" and not gid):
        return [CheckResult(**base, verdict=NOT_APPLICABLE, reason_code="whole_figure_target")]
    after_els, before_els = _elements(after), _elements(before)
    els = {**before_els, **after_els}
    changes = _changes(after, before)

    def owned(g: str, prop: str | None) -> bool:
        return any(_in_target(o, scope=scope, target=gid) for o in _owners(g, prop, els))

    kind = {
        c: k
        for c in changes
        if (k := _auto_kind(*c, after_els.get(c[0]), before_els.get(c[0]), els))
    }
    real_in = [c for c in changes if c not in kind and owned(*c)]
    cause_any = bool(real_in)
    #: 范围起因：范围内一条非「只有文字」角色元素的真改动，按它所在的 axes_N 记
    cause_axes: set[str] = set()
    for g, _ in real_in:
        if (els.get(g) or {}).get("role") in _TEXT_ONLY_ROLES:
            continue
        if m := _AXES_PREFIX.match(g):
            cause_axes.add(m.group(0))

    def colorbar_cause(axes_gid: str) -> bool:
        """色条轴的范围是映射对象的 vmin/vmax 经 set_ylim 写进去的；Agent 自己 `cbar.ax.set_ylim` 也是同一个量，不能凭「别处有
        任何真改动」放过。要求：这个色条（或它的映射对象 / 同色图组）的 vmin/vmax 在范围内真的变了。"""
        rel: set[str] = set()
        for g, e in els.items():
            if e.get("role") == "colorbar" and e.get("geom_gid") == axes_gid:
                rel.add(g)
                if isinstance(e.get("mappable_gid"), str):
                    rel.add(e["mappable_gid"])
                rel |= {x for x in e.get("cmap_group_gids") or () if isinstance(x, str)}
        return any(
            p in ("vmin", "vmax") and g in rel and owned(g, p) for g, p in changes if g in rel
        )

    outside: list[str] = []
    for g, prop in changes:
        k = kind.get((g, prop))
        if k is not None:
            if k[0] == "layout":
                exempt = cause_any
            else:
                need = _range_cause_axes(g, k[1], els)
                if need is None:
                    exempt = colorbar_cause(_AXES_PREFIX.match(g).group(0))  # type: ignore[union-attr]
                else:
                    exempt = bool(need & cause_axes)
            if exempt or owned(g, prop):
                continue
        elif owned(g, prop):
            continue
        if g not in outside:
            outside.append(g)
    if not outside:
        return [CheckResult(**base, verdict=VERIFIED)]
    return [
        CheckResult(
            **base,
            verdict=UNKNOWN,
            reason_code="changes_outside_target",
            observed={"elements": outside[:20], "total": len(outside)},
        )
    ]


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


__all__ = [
    "KINDS",
    "NOT_OBSERVABLE",
    "RENAME_AWARE_KINDS",
    "changes_outside_target",
    "check",
    "data_unchanged",
    "layout_regressions",
    "verify",
]
