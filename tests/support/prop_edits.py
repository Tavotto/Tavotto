"""属性测试共用的生成器：按 manifest 里每个字段**自己宣称**的类型 / 选项 / 区间抽取值，再配上使能项成一组编辑。

`tests/test_prop_export_py_roundtrip.py` 与 `tests/test_prop_override_idempotent.py` 共用：生成域是「产品声称支持的输入」，
不是「让测试变绿的输入」——哪些键不进生成域，由调用方的 `accept(el, field)` 谓词决定并写在调用方的文档里。
结构化字段（pair / order / number_list）没有可抽的通用值，`value_strategy` 回 None，由专用用例负责。
"""

from __future__ import annotations

import math
from collections.abc import Callable

from hypothesis import strategies as st

from support.overridesample import _ENABLERS, TEXT_DOMAINS

#: 前景色只给彼此分得开的几种；文字只给字体一定有的字形（`$` / 反斜杠会进数学文字解析，另有专用用例）。
COLORS = ("#d62728", "#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf")
TEXT = st.text(alphabet="abcXYZ 012-", min_size=1, max_size=6).filter(lambda s: s.strip())


def value_strategy(field: dict):
    """按字段自己宣称的类型 / 区间给取值策略；抽不出通用值的类型返回 None。"""
    kind, cur = field.get("type"), field.get("value")
    if kind == "color":
        return st.sampled_from(COLORS)
    if kind == "bool":
        return st.booleans()
    if kind == "enum":
        opts = list(field.get("options") or [])
        return st.sampled_from(opts) if opts else None
    if kind == "text":
        if field.get("prop") in TEXT_DOMAINS:
            return st.sampled_from(TEXT_DOMAINS[field["prop"]])
        return TEXT
    if kind == "number":
        if cur is None:
            return None
        lo, hi = field.get("min"), field.get("max")
        step = abs(float(field.get("step") or 1.0)) or 1.0
        if lo is not None and hi is not None:
            n = max(int(math.floor((float(hi) - float(lo)) / step)), 0)
            return st.integers(0, n).map(lambda k: round(float(lo) + k * step, 4))
        base = float(cur)

        def _ok(v):
            return (lo is None or v >= float(lo)) and (hi is None or v <= float(hi))

        return st.integers(-6, 6).map(lambda k: round(base + k * step, 4)).filter(_ok)
    return None


def candidates(man: dict, accept: Callable[[dict, dict], bool]) -> list[tuple[dict, dict]]:
    """(元素, 字段)：`accept` 点头、并且类型有通用取值的全部可编辑字段。"""
    return [
        (el, f)
        for el in man["elements"]
        for f in el.get("editable") or []
        if accept(el, f) and value_strategy(f) is not None
    ]


@st.composite
def edits(draw, man: dict, accept: Callable[[dict, dict], bool], max_size: int = 6):
    """1–`max_size` 条编辑：每条 = 使能项（该元素也宣称了才附加）+ 抽到的值；同一 (gid, prop) 只留最后一条。"""
    cands = candidates(man, accept)
    picked = draw(
        st.lists(st.sampled_from(range(len(cands))), min_size=1, max_size=max_size, unique=True)
    )
    patches: list[dict] = []
    for i in picked:
        el, f = cands[i]
        advertised = {x["prop"] for x in el.get("editable") or []}
        for ep, ev in _ENABLERS.get(f["prop"], ()):
            if ep != f["prop"] and ep in advertised:
                patches.append({"gid": el["gid"], "prop": ep, "value": ev})
        patches.append({"gid": el["gid"], "prop": f["prop"], "value": draw(value_strategy(f))})
    last: dict[tuple[str, str], dict] = {}
    for p in patches:
        last[(p["gid"], p["prop"])] = p  # 保留首次出现的位置：使能项总是先于自己的主 prop
    return list(last.values())
