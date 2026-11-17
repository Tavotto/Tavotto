"""`tests/test_script_adjust*.py` 共用的夹具与辅助（拆文件时从 `test_script_adjust.py` 原样搬来）。

导出修改后的 .py（ADR 0171）的用例按耗时分在几个文件里（CI 按文件分片）：
`test_script_adjust.py`（生成器单测 + 走真 `/api/export` 的用例）、`test_script_adjust_sweep.py`（整图扫描与覆盖断言）、
`test_script_adjust_heldout_a.py` / `_b.py`（整图扫描放不下的键，按组单独扫）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from support.invariant_figures import (
    ENTRY as INV_ENTRY,
    LIBRARY as INV_LIBRARY,
    SCRIPT_NAME as INV_SCRIPT,
    STEMS,
)
from support.overridesample import _ENABLERS, _SAMPLE_OVERRIDE, _sample_value
from support.scriptadjust_figures import (
    ENTRY as ADJ_ENTRY,
    LIBRARY as ADJ_LIBRARY,
    SCRIPT_NAME as ADJ_SCRIPT,
    STEMS as ADJ_STEMS,
)
from tavotto.engine import (
    pool,
    scriptadjust,
)

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None


real_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


#: `visible` 关掉之后别的字段量不到；翻转坐标轴 + 改图像 + 改色条标签 va 三者同时才会偏（真出现时是
#: `script_export_diverged`，不交付），翻转单独一条用例（`test_inverted_axes_replay_identically`）。
#: 对数轴 + 改刻度字体同理：`tick_params` 没有公式字体的键，`10^4` 这种标签的字体只盖得住已有的那几条。
_NOT_IN_THE_SWEEP = {"visible", "invert_x", "invert_y", "xscale", "yscale"}


#: 3D 图逐字段单独都等价，所有字段一次全改才超像素阈值（max 22 / 255），同样由验证拦下。
_FLAT_STEMS = [s for s in STEMS if not s.startswith("Inv3D")]


#: 扫描的图：(图库, stem)。`invariant_figures` 是全套不变式共用的那份（不往里加图，别的用例写死了编号），
#: 导出脚本自己要的图（箭头、带框文字、多列图例……）加在 `scriptadjust_figures` 里。
_LIBRARIES = {
    "inv": (INV_LIBRARY, INV_SCRIPT, INV_ENTRY),
    "adj": (ADJ_LIBRARY, ADJ_SCRIPT, ADJ_ENTRY),
}


#: 色条的三张图（`invariant_figures` 里有、但不在它的 `STEMS` 里：别的用例按编号写死，不往那里加）：
#: 同一个 mappable 两条色条 + 独立 `ScalarMappable` 的色条、映射的线组、两块网格共用 norm。
_CBAR_STEMS = ("InvCbar2", "InvCbarLC", "InvShared", "InvShared3")


_SWEEP = (
    [("inv", s) for s in _FLAT_STEMS]
    + [("inv", s) for s in _CBAR_STEMS]
    + [("adj", s) for s in ADJ_STEMS]
)


#: 结构化字段（pair / order / number_list）`_sample_value` 挑不出值，在这里按 prop 给一个明显不同的值。
#: 写进 `WRITES` 的结构化键要么在这里有采样，要么在 `_DEDICATED` 里点名专用用例。
def _widen(field):
    lo, hi = (float(v) for v in field["value"])
    return [lo - (hi - lo) * 0.25, hi + (hi - lo) * 0.1]


_STRUCTURED: dict[str, object] = {
    "xlim": _widen,
    "ylim": _widen,
    # 锚点：图例的右上角钉在子图里的一个点上（预设 loc 是 upper right / left，两种都看得出挪动）
    "loc_anchor": [0.8, 0.7],
    # 网格透明度：默认 1.0，`_sample_value` 只挑 0.9，细网格线上低于像素阈值，看不出写没写
    "grid_alpha": 0.25,
    # 线型默认 "-" 与 `_sample_value` 挑的第一个选项相同，给一个看得出的
    "grid_linestyle": "--",
}


#: 按 (cls, prop) 给的采样值（同名 prop 在不同类里要不同的值才看得出）。层次（zorder）默认采 `原值 + 5.5`，
#: 整张图的元素都同样往上挪，相对次序几乎不变，箭头 / 锚定框的层次改了也不动一个像素：箭头抬到最上层、
#: 锚定框沉到最下层，夹具里二者叠在一起（`AdjText` 左下角），次序一翻像素就变（各自单独扫，见 `_HELD_OUT`）。
_SAMPLE_BY_KEY: dict[tuple[str, str], object] = {
    # 箭头样式默认采第一个不同的选项 "-"（无头）：线型改成虚线时头保持实线这件事就没有东西可看
    ("arrowpatch", "arrowstyle"): "<|-|>",
    # 箭头帽大小：默认采 原值 + 3.9，帽子只大两成，像素差在阈值边上（变异实测可以溜过去）
    ("arrowpatch", "mutation_scale"): 36.0,
}


#: **颜色采样不能全用同一个值**：`_sample_value` 对每个颜色字段都给 `#123456`，于是图底、子图底和所有前景
#: 一个颜色，前景色译错了（甚至写死成一个常量）像素比对也看不出来。底色给两个浅色，前景按序号轮一组
#: 彼此分得开的饱和色，并且避开字段当前值。
_BACKGROUND = {("figure", "facecolor"): "#f6f2dc", ("axes", "facecolor"): "#e6eefb"}


_FOREGROUND = (
    "#d62728",
    "#1f77b4",
    "#2ca02c",
    "#9467bd",
    "#ff7f0e",
    "#17becf",
    "#e377c2",
    "#8c564b",
)


def _sample_color(key, field, k: int) -> str:
    if key in _BACKGROUND:
        return _BACKGROUND[key]
    cur = str(field.get("value")).lower()
    for i in range(len(_FOREGROUND)):
        c = _FOREGROUND[(k + i) % len(_FOREGROUND)]
        if c != cur:
            return c
    return "#000000"


#: 不进整图扫描的键（`_NOT_IN_THE_SWEEP`），按组单独扫：同组一起改，组与组之间不叠。
#: 坐标范围（pair）放进整图扫描会被 scale 的副作用搅乱，所以也自成一组。
_HELD_OUT = {
    "visible": {"visible"},
    "scale": {"xscale", "yscale"},
    "lim": {"xlim", "ylim"},
    # 边框颜色 / 线宽：整图扫描里四条边同时被关掉、画不出来，所以单独扫；「全部」与四边各自分开两组，
    # 否则四边各自的设定把「全部」整个盖住，广播那条等于没验
    "spine_all": {"spine_color", "spine_linewidth"},
    "spine_sides": {
        f"spine_{s}_{k}" for s in ("top", "right", "bottom", "left") for k in ("color", "linewidth")
    },
}


_HELD_OUT_PROPS = set().union(*_HELD_OUT.values())


def _every_other(field):
    """固定刻度：现有主刻度隔一个取一个（视区内的位置，少几条、看得出变了）。"""
    vals = [float(v) for v in field["value"]]
    return vals[::2] if len(vals) > 2 else None


#: 按 **(cls, prop)** 划的组，整图扫描里放不下：值是这一组里的采样值（None = 照常采，函数吃 manifest 字段）。
#: - 单条刻度文字冻结整条轴，序号按定位**之后**的刻度认——与 `major_*` 同扫，序号会越界；
#: - `major_values` 只在 `major_mode=fixed` 时生效，整图扫描里 mode 采到的是 step，它在那里是空转。
_HELD_OUT_KEYED: dict[str, dict[tuple[str, str], object]] = {
    "tick_text": {("ticklabel", "text"): None},
    "tick_fixed": {("ticks", "major_mode"): "fixed", ("ticks", "major_values"): _every_other},
    # 色条（ADR 0171 修订 ④）。整图扫描里它们都是隐形的，各自拆出来：
    # · 标签：色条轴的 ylabel 元素（`text`）采到同一个值，两条写的是同一个 Text，谁错了看不出来；
    # · 刻度字号 / 颜色是广播，被色条轴刻度组自己的窄条目（`ticks.fontsize` / `ticks.color`）盖住；
    # · vmin / vmax 是广播，被图像自己的窄条目盖住；cmap 与图像的 cmap 互相广播，两条都采第一个选项；
    # · 轮廓关掉之后轮廓线宽看不见。
    "cb_label": {("colorbar", "label"): None},
    "cb_ticks": {("colorbar", "tick_fontsize"): None, ("colorbar", "tick_color"): None},
    "cb_clim": {("colorbar", "vmin"): None, ("colorbar", "vmax"): None},
    "cb_cmap": {("colorbar", "cmap"): None},
    "cb_outline": {("colorbar", "outline_visible"): None},
    # 柱系列（广播）与单根柱（窄条目，ADR 0171 修订 ⑤）：同扫的话每根柱自己的设定把系列的同名键整个盖住，广播那条等于没验；
    # 显隐再各拆一组（隐藏时两边一样什么都画不出来）
    "bar_series": {("bar_series", p): None for p in scriptadjust._BAR_BROADCAST if p != "visible"},
    "bar_each": {("bar", p): None for p in scriptadjust._BAR_BROADCAST if p != "visible"},
    "bar_series_visible": {("bar_series", "visible"): None},
    "bar_each_visible": {("bar", "visible"): None},
    # 层次（zorder，ADR 0171 修订 ⑥）：箭头抬到最上、锚定框沉到最下，夹具 `AdjText` 里二者叠在一起；同扫时谁的翻译是
    # 常量，另一个的变化已经把像素带到同一个结果，所以各自单独扫
    "arrow_zorder": {("arrowpatch", "zorder"): 30.0},
    "artist_zorder": {("artist", "zorder"): -3.0},
}


_HELD_OUT_KEYS = {("ticklabel", "text"), ("ticks", "major_values")} | {
    k
    for g, d in _HELD_OUT_KEYED.items()
    if g.startswith(("cb_", "bar_", "arrow_", "artist_"))
    for k in d
}


_GROUPS: dict[str, tuple[set[str] | None, dict | None]] = {
    **{g: (props, None) for g, props in _HELD_OUT.items()},
    **{g: (None, keyed) for g, keyed in _HELD_OUT_KEYED.items()},
}


#: 整图扫描里采样值要换的 prop：次刻度格式默认的下一档是 auto（= 脚本原样，空转），换成真改格式的那一档；
#: 次刻度显隐钉成 True：脚本原样已有次刻度的图上「取反」会把它关掉，随后的次刻度模式 / 步长就是空转。
_LOCAL_SAMPLES = {"minor_format": "%.1f", "minor_visible": True}


def _sweep_patches(
    man: dict,
    only: set[tuple[str, str]] | None = None,
    props: set[str] | None = None,
    keyed: dict | None = None,
):
    """(patches, 采到的 (cls, prop))：图上每个写得进脚本的字段挑一个不同的值，带上它的使能项。

    `only` 只采这几个键（定位用：`TAVOTTO_SWEEP_ONLY=legend.ncol,axes.grid_color`）；
    `props` 给了就只采这些 prop（`_HELD_OUT` 的一组），`keyed` 给了就只采这些 (cls, prop)
    并用它给的采样值（`_HELD_OUT_KEYED` 的一组），都不给就采整图扫描的全部。
    """
    patches, covered = [], set()
    for el in man["elements"]:
        cls = scriptadjust._cls_of(el["gid"], el)
        advertised = {f["prop"] for f in el.get("editable") or []}
        for field in el.get("editable") or []:
            key = (cls, field["prop"])
            if key not in scriptadjust.WRITES:
                continue
            if keyed is not None:
                if key not in keyed:
                    continue
            elif props is None:
                if field["prop"] in (_NOT_IN_THE_SWEEP | _HELD_OUT_PROPS) or key in _HELD_OUT_KEYS:
                    continue
            elif field["prop"] not in props:
                continue
            if only is not None and key not in only:
                continue
            prop = field["prop"]
            if keyed is not None and keyed[key] is not None:
                value = keyed[key]
                value = value(field) if callable(value) else value
            elif key in _SAMPLE_BY_KEY:
                value = _SAMPLE_BY_KEY[key]
            elif prop in _STRUCTURED:
                value = _STRUCTURED[prop]
                value = value(field) if callable(value) else value
            elif field.get("type") == "color":
                value = _sample_color(key, field, len(patches))
            else:
                value = _LOCAL_SAMPLES.get(prop, _SAMPLE_OVERRIDE.get(prop, _sample_value(field)))
            if value is None:
                continue
            for ep, ev in _ENABLERS.get(prop, ()):
                if ep != prop and ep in advertised:
                    patches.append({"gid": el["gid"], "prop": ep, "value": ev})
            patches.append({"gid": el["gid"], "prop": prop, "value": value})
            covered.add(key)
    return patches, covered


def _sweep_only() -> set[tuple[str, str]] | None:
    raw = os.environ.get("TAVOTTO_SWEEP_ONLY", "").strip()
    if not raw:
        return None
    return {tuple(k.strip().split(".", 1)) for k in raw.split(",") if k.strip()}


def _library_worker(tmp_path: Path, lib: str):
    text, name, entry = _LIBRARIES[lib]
    (tmp_path / name).write_text(text, encoding="utf-8")
    worker = pool.one_shot(name, str(tmp_path), entry)
    worker.ensure_built()
    return worker, name


def _replays_identically(
    worker, name: str, tmp_path: Path, stem: str, patches: list[dict], *, must_write: bool = True
) -> bool:
    """新脚本重放与热态一致。`must_write=False`：这张图上这组键全被挡下留作重放时不算错，回 False。"""
    from tavotto import app

    assert not (worker.override(stem, patches).get("warnings") or [])
    hot = app._hot_manifest(worker, stem, patches)
    src = (tmp_path / name).read_bytes()
    adjusted = scriptadjust.build(src, stem, patches, hot, version="9.9")
    if not adjusted.written and not must_write:
        worker.override(stem, [])
        return False
    assert adjusted.written
    stage = tmp_path / f"_stage_{stem}"
    stage.mkdir()
    target = stage / "edited.py"
    target.write_bytes(adjusted.data)
    assert app._verify_edited_script(worker, stem, target, adjusted, hot, stage) == [], stem
    worker.override(stem, [])
    return True


#: 对数轴要严格为正的数据，那张图不在整图扫描里（其余几张的 y 轴跨 0，log 会被夹住）。
_HELD_OUT_STEMS = {"scale": [("inv", "InvScale")]}


def held_out_replays(tmp_path, group):
    """整图扫描里刻意不放的键（`_HELD_OUT` / `_HELD_OUT_KEYED`）：每张图上这一组一起改，照样要等价。"""
    props, keyed = _GROUPS[group]
    seen = False
    for lib in _LIBRARIES:
        stems = [s for lb, s in _SWEEP + _HELD_OUT_STEMS.get(group, []) if lb == lib]
        if not stems:
            continue
        sub = tmp_path / lib
        sub.mkdir()
        worker, name = _library_worker(sub, lib)
        try:
            for stem in stems:
                man = worker.override(stem, [])["manifest"]
                patches, _covered = _sweep_patches(man, _sweep_only(), props, keyed)
                # 这张图上整组都被挡下（比如 3D 轴的单条刻度文字）不算错，但至少要有一张图真写进去并验过
                if patches and _replays_identically(
                    worker, name, sub, stem, patches, must_write=False
                ):
                    seen = True
        finally:
            pool.discard(worker)
    assert seen, f"{group} 这一组在任何图上都没真写进脚本"


#: 整图扫描放不下的键（`_GROUPS`）按组单独扫，各组分给几个文件跑（`test_held_out_fields_replay_identically`）。
HELD_OUT_SHARDS: dict[str, list[str]] = {
    "sweep": ["scale", "visible"],
    "a": ["cb_ticks", "tick_fixed", "spine_all", "spine_sides", "tick_text"],
    "b": [
        "lim",
        "cb_label",
        "cb_clim",
        "cb_outline",
        "cb_cmap",
        "bar_series_visible",
        "bar_each_visible",
        "bar_series",
        "bar_each",
        "arrow_zorder",
        "artist_zorder",
    ],
}
assert sorted(g for gs in HELD_OUT_SHARDS.values() for g in gs) == sorted(_GROUPS), (
    "有一组没分给任何文件"
)

#: 覆盖断言按名字找用例：用例分在这几个文件里
TEST_MODULES = (
    "test_script_adjust",
    "test_script_adjust_sweep",
    "test_script_adjust_heldout_a",
    "test_script_adjust_heldout_b",
)


def find_test(name: str):
    import importlib

    for mod in TEST_MODULES:
        fn = getattr(importlib.import_module(mod), name, None)
        if callable(fn):
            return fn
    return None
