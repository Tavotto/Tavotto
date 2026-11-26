"""导出修改后的 .py（ADR 0171）：整图扫描与覆盖断言（夹具图上每个写得进脚本的字段一起改，新脚本要与热态一致）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from support.scriptadjust_harness import (
    _GROUPS,
    _HELD_OUT_STEMS,
    _LIBRARIES,
    _SWEEP,
    HELD_OUT_SHARDS,
    WORKER_PY,
    _library_worker,
    _replays_identically,
    _sweep_only,
    _sweep_patches,
    find_test,
    held_out_replays,
    real_worker,
)
from tavotto.engine import (
    pool,
    scriptadjust,
)

pytest_plugins = ("support.scriptadjust_harness",)


#: 扫描采不到（或刻意不进扫描）的写得进脚本的键 → 证明它等价的专用用例名。
#: 覆盖断言（`test_every_writable_key_is_swept_or_named`）要求 `WRITES` 的每个键二者居一。
_DEDICATED: dict[tuple[str, str], str] = {
    ("text", "pos_frac"): "test_moves_on_a_tight_cropped_figure_land_where_the_editor_shows",
    ("legend", "loc_frac"): "test_moves_on_a_tight_cropped_figure_land_where_the_editor_shows",
    # 创建型修改：不在 manifest 的可编辑字段里，扫描采不到；六类源 × 无 / tight / constrained 布局各一条用例
    ("text", "copy_of"): "test_edited_script_with_a_copy_passes_the_replay_gate",
    ("text", "fontfamily"): "test_font_edits_replay_identically",
    ("legend_text", "fontfamily"): "test_font_edits_replay_identically",
    ("ticks", "fontfamily"): "test_font_edits_replay_identically",
    ("axes", "invert_x"): "test_inverted_axes_replay_identically",
    ("axes", "invert_y"): "test_inverted_axes_replay_identically",
    ("bar_series", "bar_thickness"): "test_horizontal_bar_thickness_replays_identically",
    # 改名不动像素也不动几何（脚本的图例在 savefig 之前就建好了），整图扫描对它是隐形的：变异实测写成常量仍然绿
    **{
        (cls, "label"): "test_renames_reach_the_objects"
        for cls in ("line", "collection", "bar_series", "stem_series")
    },
    ("arrowpatch", "endpoints_frac"): "test_arrow_endpoints_replay_identically",
}


@real_worker
@pytest.mark.parametrize(("lib", "stem"), _SWEEP, ids=[f"{lib}-{s}" for lib, s in _SWEEP])
def test_every_writable_field_replays_identically(tmp_path, lib, stem):
    """夹具里每张图上所有写得进脚本的字段**一起**改，新脚本重放必须与热态几何 + 像素一致。"""
    worker, name = _library_worker(tmp_path, lib)
    try:
        man = worker.override(stem, [])["manifest"]
        patches, _covered = _sweep_patches(man, _sweep_only())
        if not patches:
            pytest.skip("这张图上没有写得进脚本的字段")
        _replays_identically(worker, name, tmp_path, stem, patches)
    finally:
        pool.discard(worker)


@real_worker
@pytest.mark.parametrize(
    "gid",
    ["axes_0.collections_0", "axes_1.colorbar", "axes_2.colorbar"],
    ids=["mesh", "own", "alone"],
)
def test_cmap_of_a_group_with_a_standalone_colorbar_replays_identically(tmp_path, gid):
    """登记网格 + 它的色条 + 共用 norm 的独立 `ScalarMappable` 色条（`InvShared3`）：引擎换其中任一条的 cmap，
    组里另一条色条也跟着换；脚本里 `set_cmap` 只换得了一个对象，所以这一组的 cmap 整体留给重放。
    只改一条（整图扫描里每个键都采同一个值，两条色条一起改，掩盖了这一点）。"""
    worker, name = _library_worker(tmp_path, "inv")
    try:
        patches = [{"gid": gid, "prop": "cmap", "value": "viridis"}]
        _replays_identically(worker, name, tmp_path, "InvShared3", patches, must_write=False)
    finally:
        pool.discard(worker)


@real_worker
def test_standalone_mappable_colorbar_replays_identically(tmp_path):
    """独立 `ScalarMappable` 的 3D 色条（`Inv3D` 整图全改超像素阈值，不在整图扫描里）：只改色条自己的键。
    落在色条轴上的写进脚本，cmap / vmin / vmax 公开 API 够不着 mappable，留作重放，两边合起来要等价。"""
    worker, name = _library_worker(tmp_path, "inv")
    try:
        man = worker.override("Inv3D", [])["manifest"]
        keys = {k for k in scriptadjust.WRITES if k[0] == "colorbar"} - {("colorbar", "visible")}
        patches, covered = _sweep_patches(man, keys, keyed={k: None for k in keys})
        assert {p for c, p in covered if c == "colorbar"} >= {"label", "cmap", "tick_color"}
        assert _replays_identically(worker, name, tmp_path, "Inv3D", patches)
    finally:
        pool.discard(worker)


def _written_keys(
    worker, script: Path, stem: str, man: dict, props, keyed=None
) -> set[tuple[str, str]]:
    """这张图上采到、而且**真写进了脚本**的键（被 `_Unsupported` 挡下留作重放的不算验证过）。"""
    from tavotto import app

    patches, _covered = _sweep_patches(man, props=props, keyed=keyed)
    if not patches:
        return set()
    worker.override(stem, patches)
    hot = app._hot_manifest(worker, stem, patches)
    worker.override(stem, [])
    assert hot is not None, stem
    adjusted = scriptadjust.build(script.read_bytes(), stem, patches, hot, version="9.9")
    residual = {(p["gid"], p["prop"]) for p in adjusted.residual}
    els = {e["gid"]: e for e in man["elements"]}
    return {
        (scriptadjust._cls_of(p["gid"], els.get(p["gid"])), p["prop"])
        for p in patches
        if (p["gid"], p["prop"]) not in residual
    }


#: prop → 它依赖的公开 API（`模块:类.方法`）。引擎同样按 `hasattr` 判（`overrides` 的 hatchcolor 一族：
#: `set_hatchcolor` 3.11 起才有），旧版 matplotlib 上 manifest 不发这条字段，扫描采不到不算漏验。
_NEEDS_API = {"hatchcolor": "matplotlib.patches:Patch.set_hatchcolor"}


def _worker_has(api: str) -> bool:
    mod, attr = api.split(":")
    cls, meth = attr.split(".")
    code = f"import importlib; print(hasattr(getattr(importlib.import_module({mod!r}), {cls!r}), {meth!r}))"
    out = subprocess.run(
        [WORKER_PY, "-c", code], capture_output=True, encoding="utf-8", check=True, timeout=60
    )
    return out.stdout.strip().splitlines()[-1] == "True"


@real_worker
def test_every_writable_key_is_swept_or_named(tmp_path):
    """`WRITES` 的每个键都在某张图上真被采样验证过，或者点名了证明它的专用用例。

    没有这一条，进了 `WRITES` 却在任何夹具上都采不到值的键（结构化字段、夹具里没有的元素）
    会被扫描静默放过，「逐键验证过」就是一句空话。
    """
    covered: set[tuple[str, str]] = set()
    for lib in _LIBRARIES:
        stems = {s for lb, s in _SWEEP + _HELD_OUT_STEMS["scale"] if lb == lib}
        sub = tmp_path / lib
        sub.mkdir()
        worker, _name = _library_worker(sub, lib)
        try:
            for stem in stems:
                man = worker.override(stem, [])["manifest"]
                for props, keyed in [(None, None), *_GROUPS.values()]:
                    covered |= _written_keys(worker, sub / _name, stem, man, props, keyed)
        finally:
            pool.discard(worker)
    missing = set(scriptadjust.WRITES) - covered - set(_DEDICATED)
    # 要较新 matplotlib 才有的 prop：引擎按同一条公开 API 是否存在决定发不发字段，这一档没有就采不到
    missing -= {k for k in missing if k[1] in _NEEDS_API and not _worker_has(_NEEDS_API[k[1]])}
    assert not missing, f"写进了脚本、却既没被扫描采到也没点名专用用例：{sorted(missing)}"
    for key, test in _DEDICATED.items():
        assert find_test(test) is not None, f"{key} 点名的用例 {test} 不存在"
        assert key in scriptadjust.WRITES, f"{key} 已不在 WRITES，从 _DEDICATED 删掉"


@real_worker
def test_arrow_endpoints_replay_identically(tmp_path):
    """`endpoints_frac` 不在 manifest 的可编辑字段里（端点另发 `arrow_endpoints`），扫描采不到：
    这里按 manifest 的端点逐根箭头挪一遍（注释箭头改的是注释的锚点、独立箭头 `set_positions`），
    再与热态比。"""
    worker, name = _library_worker(tmp_path, "adj")
    try:
        man = worker.override("AdjText", [])["manifest"]
        arrows = [e for e in man["elements"] if e.get("arrow_endpoints")]
        assert len(arrows) == 4, [e["gid"] for e in arrows]  # 纯箭头注释两根 + 独立箭头两根
        patches = [
            {
                "gid": e["gid"],
                "prop": "endpoints_frac",
                "value": [
                    min(0.95, a[0] + 0.07 * (k + 1)),
                    max(0.05, a[1] - 0.06),
                    max(0.05, b[0] - 0.05),
                    min(0.95, b[1] + 0.08 * (k + 1)),
                ],
            }
            for k, e in enumerate(arrows)
            for a, b in [e["arrow_endpoints"]]
        ]
        _replays_identically(worker, name, tmp_path, "AdjText", patches)
    finally:
        pool.discard(worker)


@real_worker
@pytest.mark.parametrize("group", HELD_OUT_SHARDS["sweep"])
def test_held_out_fields_replay_identically(tmp_path, group):
    """整图扫描里刻意不放的键（`_HELD_OUT` / `_HELD_OUT_KEYED`）：每张图上这一组一起改，照样要等价。"""
    held_out_replays(tmp_path, group)
