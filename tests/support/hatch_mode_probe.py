"""在 **worker 解释器**里跑的探针：alpha / fill 编辑与撤空之后，花纹色的**模式**回到「未设」了没有（#874）。

native 会话里图是用户自己的对象：屏障释放 `apply([])` 之后用户的 Python 继续跑，若他再改
`rcParams['hatch.color']` 并 `set_alpha` / `set_fill`，这个 artist 必须像没被 Tavotto 编辑过的一样
跟随新的 rc（Codex #950 r4245786615）。探针如实报事实，判定归调用方 `tests/test_hatch_mode_restored.py`。

每个目标：A 图编辑 → 查模式（编辑中）→ `apply([])` → 查模式 / 花纹色 → 改环境 rc 再 `set_alpha`；
B 图（同一构造、从没被 Tavotto 碰过）只做最后的 `set_alpha`。两边的花纹色应逐位相同。
"""

from __future__ import annotations

import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "src", "tavotto", "engine"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

import manifest as M  # noqa: E402
import overrides as O  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

TARGETS = {
    "bar": "axes_0.barseries_0.bar_0",
    "series": "axes_0.barseries_0",
    "rect": "axes_0.patches_2",
    "fill_between": "axes_0.fill_0",
    "scatter": "axes_0.scatter_1",
}


def build():
    fig, ax = plt.subplots(figsize=(4.2, 3.0))
    with plt.rc_context({"hatch.color": "red"}):
        ax.bar([0, 1], [3, 5], 0.6, facecolor="#cccccc", hatch="//")
    with plt.rc_context({"hatch.color": (0.0, 0.5, 0.0, 0.25)}):
        ax.add_patch(Rectangle((2.2, 0.5), 0.8, 2.5, facecolor="#cccccc", hatch="xx"))
    with plt.rc_context({"hatch.color": "blue"}):
        ax.fill_between([3.5, 4.5, 5.5], [1, 4, 2], facecolor="#cccccc", hatch="\\\\")
        ax.scatter([4, 5], [5, 6], s=300, facecolor="#cccccc", hatch="++")
    state = O.FigState(fig)
    M.instrument(state)
    M.build_manifest(state, "P")
    return fig, state


def _val(a):
    h = getattr(a, "_hatchcolors", None)
    h = a._hatch_color if h is None else h  # noqa: SLF001
    return (
        h
        if isinstance(h, str)
        else [[round(float(x), 4) for x in r] for r in (h if hasattr(h[0], "__len__") else [h])]
    )


def _members(a):
    return list(a.artists) if hasattr(a, "artists") else [a]


def _mode_none(a) -> bool:
    return all(getattr(m, "_original_hatchcolor", 0) is None for m in _members(a))


def run_one(name, gid, edits):
    plt.rcParams["hatch.color"] = "magenta"
    _fa, sa = build()
    _fb, sb = build()
    art_a, art_b = sa.resolve(gid), sb.resolve(gid)
    warns = O.apply(sa, edits)
    mid = _mode_none(art_a)
    warns += O.apply(sa, [])
    after = _mode_none(art_a)
    hatch_a = [_val(m) for m in _members(art_a)]
    hatch_b = [_val(m) for m in _members(art_b)]
    plt.rcParams["hatch.color"] = "cyan"  # 用户恢复运行后改了环境 rc
    for m in _members(art_a):
        m.set_alpha(0.6)
    for m in _members(art_b):
        m.set_alpha(0.6)
    return {
        "warnings": warns,
        "mode_none_during_edit": mid,
        "mode_none_after_clear": after,
        "hatch_after_clear_equals_pristine": hatch_a == hatch_b,
        "follows_new_rc_like_pristine": [_val(m) for m in _members(art_a)]
        == [_val(m) for m in _members(art_b)],
    }


def main() -> int:
    out = {
        "matplotlib": matplotlib.__version__,
        "has_set_hatchcolor": hasattr(Rectangle, "set_hatchcolor"),
        "cases": {},
    }
    for name, gid in TARGETS.items():
        edits = [{"gid": gid, "prop": "alpha", "value": 0.4}]
        out["cases"][f"{name}:alpha"] = run_one(name, gid, edits)
        if name == "rect":
            out["cases"]["rect:fill"] = run_one(
                name, gid, [{"gid": gid, "prop": "fill", "value": False}]
            )
            out["cases"]["rect:fill+alpha"] = run_one(
                name,
                gid,
                [
                    {"gid": gid, "prop": "fill", "value": False},
                    {"gid": gid, "prop": "alpha", "value": 0.4},
                ],
            )
    out["cases"]["bar:stale-base"] = run_stale(TARGETS["bar"])
    out["cases"]["scatter:stale-base"] = run_stale(TARGETS["scatter"])
    print(json.dumps(out))
    return 0


def run_stale(gid):
    """屏障之间用户的 Python 改了 rc 并 `set_alpha`：记着的「创建时的值」过期了，必须按实况重记。"""
    plt.rcParams["hatch.color"] = "magenta"
    _fa, sa = build()
    _fb, sb = build()
    art_a, art_b = sa.resolve(gid), sb.resolve(gid)
    warns = O.apply(sa, [{"gid": gid, "prop": "alpha", "value": 0.4}])
    warns += O.apply(sa, [])
    plt.rcParams["hatch.color"] = "cyan"
    for m in _members(art_a) + _members(art_b):
        m.set_alpha(0.6)  # 用户代码：与 Tavotto 无关
    warns += O.apply(sa, [{"gid": gid, "prop": "alpha", "value": 0.3}])
    warns += O.apply(sa, [])
    return {
        "warnings": warns,
        "mode_none_after_clear": _mode_none(art_a),
        "hatch_after_clear_equals_pristine": [_val(m) for m in _members(art_a)]
        == [_val(m) for m in _members(art_b)],
        "follows_new_rc_like_pristine": True,
    }


if __name__ == "__main__":
    raise SystemExit(main())
