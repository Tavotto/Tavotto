"""边框（spine）模型：一档「全部」+ 四条可各自覆盖，写进 cfg 再**整体重建**。

2026-09-18 审计任务书 PR D 第二步的第一刀，从 `overrides.py` 按 artist family 切出来的
第一个族（最小的一个，用来验证切割配方本身）。只依赖 matplotlib：不 import `overrides`，
也不 import `manifest`——`overrides.HANDLERS` 只登记这里导出的 getter / setter（`HANDLERS_*`
两张表按原位置展开进去，顺序一个字节不变），`manifest` 直接从这里取只读判据。正文逐字未改。

与刻度模型同一套路数（写进 cfg 再整体重建），原因也一样：「全部边框改成灰色」与「只把上
边框改成红色」是两条会互相盖写的 setter，谁先谁后就会得到两张不同的图。改成一次重建之后，
两者的应用顺序不影响结果——热会话与全量重放才收敛。

优先级：某一条自己的设定 > 「全部」的设定 > 脚本原样。「全部」这一档故意作用于 `ax.spines`
的**每一条**（含色条轴的 'outline'）——那是它原本的口径，收窄成四条会让色条的外框突然改不动了。
"""

from __future__ import annotations

from matplotlib.axes import Axes


def _spines_get(ax: Axes, fn, default):
    sp = ax.spines.get("left") or next(iter(ax.spines.values()), None)
    return fn(sp) if sp is not None else default


_SPINE_SIDES = ("top", "right", "bottom", "left")
_SPINE_CFG_KEYS = (
    "all_color",
    "all_width",
    *(f"{s}_{k}" for s in _SPINE_SIDES for k in ("color", "width")),
)


def spine_cfg(ax: Axes) -> dict:
    """取（必要时新建）一条 axes 的边框模型缓存。`instrument` 在 build 之后
    对每个 2D axes 调一次，保证 `orig` 采的是**脚本原样**。"""
    cfg = getattr(ax, "_mm_spine_cfg", None)
    if cfg is None:
        cfg = {k: None for k in _SPINE_CFG_KEYS}
        cfg["orig"] = {
            name: (sp.get_edgecolor(), float(sp.get_linewidth())) for name, sp in ax.spines.items()
        }
        ax._mm_spine_cfg = cfg  # noqa: SLF001
    return cfg


def apply_spine_model(ax: Axes) -> None:
    """按 cfg **整体重建**每一条边框的颜色与线宽。"""
    cfg = spine_cfg(ax)
    for name, sp in ax.spines.items():
        orig = cfg["orig"].get(name)
        if orig is None:
            continue
        color = cfg.get(f"{name}_color")
        if color is None:
            color = cfg["all_color"]
        width = cfg.get(f"{name}_width")
        if width is None:
            width = cfg["all_width"]
        sp.set_edgecolor(orig[0] if color is None else color)
        sp.set_linewidth(orig[1] if width is None else float(width))
    ax.stale = True


def spine_all_color(ax: Axes):
    cfg = spine_cfg(ax)
    if cfg["all_color"] is not None:
        return cfg["all_color"]
    return _spines_get(ax, lambda s: s.get_edgecolor(), (0, 0, 0, 1))


def spine_all_width(ax: Axes) -> float:
    cfg = spine_cfg(ax)
    if cfg["all_width"] is not None:
        return float(cfg["all_width"])
    return float(_spines_get(ax, lambda s: float(s.get_linewidth()), 0.8))


def spine_side_color(ax: Axes, side: str):
    cfg = spine_cfg(ax)
    if cfg[f"{side}_color"] is not None:
        return cfg[f"{side}_color"]
    sp = ax.spines.get(side)
    return sp.get_edgecolor() if sp is not None else spine_all_color(ax)


def spine_side_width(ax: Axes, side: str) -> float:
    cfg = spine_cfg(ax)
    if cfg[f"{side}_width"] is not None:
        return float(cfg[f"{side}_width"])
    sp = ax.spines.get(side)
    return float(sp.get_linewidth()) if sp is not None else spine_all_width(ax)


def _mk_spine_handler(key: str, read):
    def g(ax: Axes):
        return read(ax)

    def s(ax: Axes, v) -> None:
        spine_cfg(ax)[key] = v
        apply_spine_model(ax)

    return (g, s)


def _mk_spine_restore(key: str):
    """撤销一条边框设定 = **退回未表态**（落回「全部」那一档，或脚本原样），
    不是把当前推断出来的值钉死成一条显式配置。"""

    def r(ax: Axes, _orig) -> None:
        spine_cfg(ax)[key] = None
        apply_spine_model(ax)

    return r


def _mk_spine_get(name: str):
    return lambda a: bool(a.spines[name].get_visible()) if name in a.spines else True


def _mk_spine_set(name: str):
    def s(a: Axes, v) -> None:
        if name in a.spines:
            a.spines[name].set_visible(bool(v))

    return s


#: 颜色 / 线宽那 10 条（「全部」两条 + 四边各两条）。`overrides.HANDLERS` 按原位置 `**` 展开。
HANDLERS_STYLE: dict[tuple[str, str], tuple] = {
    ("axes", "spine_top_color"): _mk_spine_handler(
        "top_color", lambda a, _s="top": spine_side_color(a, _s)
    ),
    ("axes", "spine_top_linewidth"): _mk_spine_handler(
        "top_width", lambda a, _s="top": spine_side_width(a, _s)
    ),
    ("axes", "spine_right_color"): _mk_spine_handler(
        "right_color", lambda a, _s="right": spine_side_color(a, _s)
    ),
    ("axes", "spine_right_linewidth"): _mk_spine_handler(
        "right_width", lambda a, _s="right": spine_side_width(a, _s)
    ),
    ("axes", "spine_bottom_color"): _mk_spine_handler(
        "bottom_color", lambda a, _s="bottom": spine_side_color(a, _s)
    ),
    ("axes", "spine_bottom_linewidth"): _mk_spine_handler(
        "bottom_width", lambda a, _s="bottom": spine_side_width(a, _s)
    ),
    ("axes", "spine_left_color"): _mk_spine_handler(
        "left_color", lambda a, _s="left": spine_side_color(a, _s)
    ),
    ("axes", "spine_left_linewidth"): _mk_spine_handler(
        "left_width", lambda a, _s="left": spine_side_width(a, _s)
    ),
}

#: 四条显隐开关 + 「全部」的颜色 / 线宽（这两条走模型，应用顺序不影响结果）。
HANDLERS_VISIBILITY: dict[tuple[str, str], tuple] = {
    ("axes", "spine_top"): (_mk_spine_get("top"), _mk_spine_set("top")),
    ("axes", "spine_right"): (_mk_spine_get("right"), _mk_spine_set("right")),
    ("axes", "spine_bottom"): (_mk_spine_get("bottom"), _mk_spine_set("bottom")),
    ("axes", "spine_left"): (_mk_spine_get("left"), _mk_spine_set("left")),
    ("axes", "spine_color"): _mk_spine_handler("all_color", spine_all_color),
    ("axes", "spine_linewidth"): _mk_spine_handler("all_width", spine_all_width),
}

#: 撤销 = 退回未表态（`_mk_spine_restore`），`overrides._RESTORE` 按这张表登记。
RESTORE: dict[tuple[str, str], object] = {
    ("axes", _prop): _mk_spine_restore(_key)
    for _prop, _key in (
        ("spine_color", "all_color"),
        ("spine_linewidth", "all_width"),
        *[
            (f"spine_{_s}_{_n}", f"{_s}_{_k}")
            for _s in _SPINE_SIDES
            for _n, _k in (("color", "color"), ("linewidth", "width"))
        ],
    )
}
