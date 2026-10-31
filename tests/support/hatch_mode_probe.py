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
sys.path.insert(0, os.path.join(_REPO, "tests"))

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


def build_tl():
    """#956：文本框 / 图例框 / 线集合 / 误差线 / 茎线那张图（脚本文本与 worker 用例共用一份）。"""
    from support import rc_hatch_text_legend_figure as tl  # noqa: PLC0415

    ns: dict = {}
    exec(tl.LIBRARY, ns)  # noqa: S102 — 夹具脚本文本，仓库自带
    fig = ns["build"]()
    state = O.FigState(fig)
    M.instrument(state)
    M.build_manifest(state, "P")
    return fig, state


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
    """带花纹色模式的成员（误差线 / 茎线的数据线、端帽是 Line2D，没有这个模式，不在内）。"""
    if hasattr(a, "members"):
        ms = a.members()
    elif hasattr(a, "artists"):
        ms = list(a.artists)
    elif hasattr(a, "get_bbox_patch"):  # 文本：背景框那个 FancyBboxPatch
        ms = [a.get_bbox_patch()]
    elif hasattr(a, "get_frame"):  # 图例：框
        ms = [a.get_frame()]
    else:
        ms = [a]
    return [m for m in ms if m is not None and hasattr(m, "_hatch_color")]


def _mode_none(a) -> bool:
    return all(getattr(m, "_original_hatchcolor", 0) is None for m in _members(a))


def run_one(name, gid, edits, builder=None, hatch_explicit=False):
    builder = builder or build
    plt.rcParams["hatch.color"] = "magenta"
    _fa, sa = builder()
    _fb, sb = builder()
    art_a, art_b = sa.resolve(gid), sb.resolve(gid)
    warns = O.apply(sa, edits)
    mid = _mode_none(art_a) is not hatch_explicit  # hatchcolor 编辑期间模式本来就是显式的
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
    if out["has_set_hatchcolor"]:
        # hatchcolor 编辑（第二步）：显式模式只活在编辑期间，撤空回到「未设」并继续跟随 rc
        for name in ("bar", "series", "rect"):
            gid = TARGETS[name]
            hc = {"gid": gid, "prop": "hatchcolor", "value": "#0000ff"}
            out["cases"][f"{name}:hatchcolor"] = run_one(name, gid, [hc], hatch_explicit=True)
            out["cases"][f"{name}:hatchcolor+alpha"] = run_one(
                name,
                gid,
                [hc, {"gid": gid, "prop": "alpha", "value": 0.4}],
                hatch_explicit=True,
            )
        out["cases"]["bar:hatchcolor-edge"] = run_one(
            "bar",
            TARGETS["bar"],
            [{"gid": TARGETS["bar"], "prop": "hatchcolor", "value": "edge"}],
            hatch_explicit=True,
        )
    from support import rc_hatch_text_legend_figure as tl  # noqa: PLC0415

    for name, (gid, prop) in tl.TARGETS.items():
        out["cases"][f"{name}:alpha"] = run_one(
            name, gid, [{"gid": gid, "prop": prop, "value": 0.4}], builder=build_tl
        )
    out["cases"]["bar:stale-base"] = run_stale(TARGETS["bar"])
    out["cases"]["scatter:stale-base"] = run_stale(TARGETS["scatter"])
    out["subclass_cases"] = sub_cases() if out["has_set_hatchcolor"] else {}
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


# --- 用户子类：重写 set_alpha / set_fill 并在里面自己调 set_hatchcolor（Codex #950 r4246080546）------
from matplotlib.collections import PolyCollection  # noqa: E402


class AlphaRect(Rectangle):
    def set_alpha(self, alpha):
        self.set_hatchcolor("blue")  # 有意装的模式
        super().set_alpha(alpha)


class RaisingRect(Rectangle):
    def set_alpha(self, alpha):
        self.set_hatchcolor("blue")
        raise RuntimeError("子类 set_alpha 装完模式后抛了")


class FillRect(Rectangle):
    def set_fill(self, b):
        self.set_hatchcolor("blue")
        super().set_fill(b)


class AlphaColl(PolyCollection):
    def set_alpha(self, alpha):
        self.set_hatchcolor("blue")
        super().set_alpha(alpha)


class CopyHatchRect(Rectangle):
    """重写 `set_hatchcolor`、先复制 / 规范化颜色再交给父类：存进去的是相等但不同的对象
    （Codex #950 r4246402299）。它的 set_alpha / set_fill 没装任何模式——钉住必须照常还原。"""

    def set_hatchcolor(self, color):
        if isinstance(color, str):
            color = "".join(list(color))
        elif color is not None:
            color = tuple(float(x) for x in color)
        super().set_hatchcolor(color)


def build_sub():
    fig, ax = plt.subplots(figsize=(4.2, 3.0))
    with plt.rc_context({"hatch.color": "red"}):
        ax.add_patch(AlphaRect((0, 0), 1, 2, facecolor="#cccccc", hatch="//"))
        ax.add_patch(FillRect((2, 0), 1, 2, facecolor="#cccccc", hatch="//"))
        ax.add_patch(RaisingRect((6, 0), 1, 2, facecolor="#cccccc", hatch="//"))
        ax.add_collection(AlphaColl([[(4, 0), (5, 0), (5, 2)]], facecolor="#cccccc", hatch="++"))
        ax.add_patch(CopyHatchRect((8, 0), 1, 2, facecolor="#cccccc", hatch="//"))
    ax.set_xlim(-1, 10)
    ax.set_ylim(0, 3)
    state = O.FigState(fig)
    M.instrument(state)
    man = M.build_manifest(state, "P")
    gids = {type(state.resolve(e["gid"])).__name__: e["gid"] for e in man["elements"]}
    return fig, state, gids


def _mode(a):
    m = getattr(a, "_original_hatchcolor", 0)
    return m if isinstance(m, str) or m is None else "other"


def run_sub(cls_name, prop, value, reresolve):
    """`reresolve`：clear 之后、改 rc 时在两边各调一次的**继承来的**重解析入口（不经过子类装模式的那个）。"""
    plt.rcParams["hatch.color"] = "magenta"
    _fa, sa, ga = build_sub()
    _fb, sb, _gb = build_sub()
    gid = ga[cls_name]
    art_a, art_b = sa.resolve(gid), sb.resolve(gid)
    warns = O.apply(sa, [{"gid": gid, "prop": prop, "value": value}])
    mid = _mode(art_a)
    warns += O.apply(sa, [])
    after = _mode(art_a)
    # B：用户自己直接调同一个子类 setter 往返（Tavotto 的编辑 + 撤空对它做的就是这件事）
    original = {"alpha": None, "fill": True}[prop]
    getattr(art_b, "set_" + prop)(value)
    getattr(art_b, "set_" + prop)(original)
    plt.rcParams["hatch.color"] = "cyan"
    if reresolve:
        reresolve(art_a)
        reresolve(art_b)
    return {
        "warnings": warns,
        "mode_during_edit": mid,
        "mode_after_clear": after,
        "mode_after_clear_equals_user_calls": _mode(art_a) == _mode(art_b),
        "hatch_follows_like_user_calls": _val(art_a) == _val(art_b),
    }


def run_raising():
    """子类 setter 装完模式后抛异常：模式照留（异常路径与正常路径同一条规则）。"""
    plt.rcParams["hatch.color"] = "magenta"
    _f, sa, ga = build_sub()
    gid = ga["RaisingRect"]
    art = sa.resolve(gid)
    warns = O.apply(sa, [{"gid": gid, "prop": "alpha", "value": 0.4}])
    return {"warnings": warns, "mode_after_edit": _mode(art)}


def run_copy():
    """子类的 `set_hatchcolor` 存的是复制品：模式仍要回到「未设」，改 rc 后与从未编辑过的同构图一致。"""
    plt.rcParams["hatch.color"] = "magenta"
    _fa, sa, ga = build_sub()
    _fb, sb, _gb = build_sub()
    gid = ga["CopyHatchRect"]
    art_a, art_b = sa.resolve(gid), sb.resolve(gid)
    warns = O.apply(sa, [{"gid": gid, "prop": "alpha", "value": 0.4}])
    warns += O.apply(sa, [])
    after = _mode(art_a)
    plt.rcParams["hatch.color"] = "cyan"
    art_a.set_alpha(0.6)
    art_b.set_alpha(0.6)
    return {
        "warnings": warns,
        "mode_after_clear": after,
        "hatch_follows_like_pristine": _val(art_a) == _val(art_b),
    }


def sub_cases():
    return {
        "CopyHatchRect:alpha": run_copy(),
        "RaisingRect:alpha": run_raising(),
        "AlphaRect:alpha": run_sub("AlphaRect", "alpha", 0.4, lambda a: a.set_fill(True)),
        "FillRect:fill": run_sub("FillRect", "fill", False, lambda a: a.set_alpha(None)),
        "AlphaColl:alpha": run_sub("AlphaColl", "alpha", 0.4, None),
    }


if __name__ == "__main__":
    raise SystemExit(main())
