"""#874 第二步：纹理颜色 `hatchcolor` 可编辑（Patch 族 / 柱 / 柱系列）。

字段**按真实 setter 判，不按版本号**：`set_hatchcolor` 是 matplotlib 3.11 才有的；≤3.10 的花纹色是边色的
副产品，不发这条字段，旧版本重放新存的 override 是安静的空操作。同一份用例在 3.10 / 3.11 上各自判
「该发的发了、不该发的没发」（解释器由 `TAVOTTO_WORKER_PYTHON` 切）。

判据的主语（每条断言说谁的、哪个时刻）：
  * 字段表：manifest 里该元素的 editable（worker 此刻的真实状态）；
  * 能力真实：同一张图上改前改后的像素；
  * 热态 == 重放：热 worker 增量应用的结果 vs 只见当前列表的全新 worker，**每一步**都比 manifest 与像素；
  * 还原：撤空后的像素 == 脚本原样（含 `rc_context` 里建的图：模式是「未设」、花纹色是创建时解析的值）；
  * 顺序无关：hatchcolor 与 alpha / fill 的两种应用顺序得到同一张图。
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from support import rc_hatch_figure as rc
from tavotto.engine import patchspec, pool, project_watch

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_hatchcolor.py"
ENTRY = "main"
STEM = "HatchColor"

#: A 组带斜线 + 显式描边，B 组带斜线无描边（花纹跟随的是默认边色），外加独立形状（带花纹）。
LIBRARY = """\
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.6))
    x = np.arange(3)
    ax.bar(x - 0.2, [3, 5, 4], 0.4, label="A", facecolor="#47749e", edgecolor="#008000",
           linewidth=1, hatch="//")
    ax.bar(x + 0.2, [2, 4, 6], 0.4, label="B", facecolor="#c0562a", hatch="xx")
    ax.add_patch(Rectangle((3.0, 0.3), 0.6, 2.0, facecolor="#2A6F3C", edgecolor="#222222",
                           hatch="++"))
    ax.set_xlim(-0.7, 3.8)
    ax.set_ylim(0, 7)
    ax.legend(loc="upper left")
    fig.savefig("HatchColor.png")
"""

SERIES_A = "axes_0.barseries_0"
SERIES_B = "axes_0.barseries_1"
BAR_A0 = "axes_0.barseries_0.bar_0"
BAR_B1 = "axes_0.barseries_1.bar_1"

PROBE = (
    "import matplotlib.patches as p; r = p.Rectangle((0, 0), 1, 1); "
    "print(int(hasattr(r, 'set_hatchcolor')))"
)


def _caps() -> bool:
    if WORKER_PY is None:  # 没有 worker：模块整体 skip，收集阶段不得起子进程
        return False
    out = subprocess.run(  # noqa: S603 — 测试里探 worker 解释器的能力
        [WORKER_PY, "-c", PROBE], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.split()
    return out[0] == "1"


HAS_HATCHCOLOR = _caps()
needs_hatchcolor = pytest.mark.skipif(
    not HAS_HATCHCOLOR, reason="该 matplotlib 没有 set_hatchcolor（≤3.10）"
)


def _p(gid, prop, value):
    return {"gid": gid, "prop": prop, "value": value}


def _hc(gid, value="#0000ff"):
    return _p(gid, "hatchcolor", value)


def _alpha(gid, value=0.4):
    return _p(gid, "alpha", value)


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("hatchcolor-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _worker(library, stem_lib=SCRIPT_NAME):
    w = pool.one_shot(stem_lib, str(library), ENTRY)
    w.ensure_built()
    return w


@pytest.fixture
def worker(library):
    w = _worker(library)
    yield w
    pool.discard(w)


def _apply(w, patches, stem=STEM) -> dict:
    resp = w.override(stem, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp["manifest"]


def _png(w, patches, tag, stem=STEM) -> str:
    tag = re.sub(r"[^A-Za-z0-9_.-]", "_", tag)
    return hashlib.sha1(w.preview_png(stem, list(patches), 420, tag).read_bytes()).hexdigest()


def _fresh(library, patches, tag, script=SCRIPT_NAME, stem=STEM):
    w = _worker(library, script)
    try:
        return _apply(w, patches, stem), _png(w, patches, tag, stem)
    finally:
        pool.discard(w)


def _fields(man, gid) -> dict:
    hits = [e for e in man["elements"] if e["gid"] == gid]
    assert hits, f"{gid} 不在 manifest 里：{sorted(e['gid'] for e in man['elements'])}"
    return {f["prop"]: f for f in hits[0]["editable"]}


def _patch_gid(man) -> str:
    return next(e["gid"] for e in man["elements"] if e["role"] == "patch")


def _resolve(gid, man):
    return _patch_gid(man) if gid == "PATCH" else gid


GIDS = [SERIES_A, SERIES_B, BAR_A0, BAR_B1, "PATCH"]


# ---------------------------------------------------------------------------
# 字段：按真实 setter 发，形状 = 解析后的 hex + 是否跟随边框
# ---------------------------------------------------------------------------
def test_hatchcolor_field_is_advertised_only_where_matplotlib_has_the_setter(worker):
    man = _apply(worker, [])
    for gid in [SERIES_A, SERIES_B, BAR_A0, BAR_B1, _patch_gid(man)]:
        fields = _fields(man, gid)
        if not HAS_HATCHCOLOR:
            assert "hatchcolor" not in fields, f"{gid}：本版本没有 set_hatchcolor，却发了字段"
            continue
        f = fields["hatchcolor"]
        assert f["type"] == "color"
        assert re.fullmatch(r"#[0-9a-f]{6}", str(f["value"]).lower()), f
        assert isinstance(f["follows_edge"], bool), f


@needs_hatchcolor
def test_default_hatchcolor_follows_the_edge_and_reports_the_edge_colour(worker):
    """脚本没设花纹色：3.11 默认 rc 是 `'edge'`，跟随边框；`value` 是边色的实况。"""
    man = _apply(worker, [])
    a = _fields(man, SERIES_A)["hatchcolor"]
    assert a["follows_edge"] is True
    assert a["value"].lower() == "#008000"  # A 组显式描边 green
    assert _fields(man, _patch_gid(man))["hatchcolor"]["value"].lower() == "#222222"


@needs_hatchcolor
def test_explicit_colour_and_edge_are_reported_after_the_edit(worker):
    man = _apply(worker, [_hc(SERIES_B, "#0000ff")])
    f = _fields(man, SERIES_B)["hatchcolor"]
    assert (f["value"].lower(), f["follows_edge"]) == ("#0000ff", False)
    assert "value_original" in f  # 有已应用的 override：补脚本原样（hex 口径）
    man = _apply(worker, [_hc(SERIES_A, "#0000ff"), _hc(BAR_A0, "edge")])
    assert _fields(man, BAR_A0)["hatchcolor"]["follows_edge"] is True
    assert _fields(man, BAR_A0)["hatchcolor"]["value"].lower() == "#008000"
    assert _fields(man, BAR_A0.replace("bar_0", "bar_1"))["hatchcolor"]["follows_edge"] is False


def test_manifest_gate_is_the_real_setter_not_the_version_string():
    """判据的主语是**这个 artist 有没有 `set_hatchcolor`**：同一个解释器里有 / 没有 setter 的两个替身各给各的答案，
    与 matplotlib 版本号无关（版本号判据在真实 3.11 上与 hasattr 恒等，只有替身分得开；探针在 worker 解释器里跑）。"""
    probe = Path(__file__).parent / "support" / "hatchcolor_gate_probe.py"
    out = subprocess.run(  # noqa: S603 — 测试里起 worker 解释器跑探针
        [WORKER_PY, str(probe)], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    rep = json.loads(out.strip().splitlines()[-1])
    assert rep["with_setter"] == {
        "prop": "hatchcolor",
        "type": "color",
        "value": "#008000",
        "follows_edge": True,
    }
    assert rep["without_setter"] is None, (
        "没有 set_hatchcolor 的 artist 不该发字段（按版本号判会错）"
    )
    assert rep["explicit_follows_edge"] is False


# ---------------------------------------------------------------------------
# 能力真实：改了，像素真的变；'edge' 与显式色画得不同
# ---------------------------------------------------------------------------
@needs_hatchcolor
@pytest.mark.parametrize("gid", GIDS)
def test_hatchcolor_changes_the_pixels(worker, gid):
    gid = _resolve(gid, _apply(worker, []))
    base = _png(worker, [], "base")
    blue = _png(worker, [_hc(gid)], f"{gid}-blue")
    assert blue != base, f"{gid}：改花纹色没有动像素"
    assert _png(worker, [_hc(gid, "#ff00ff")], f"{gid}-magenta") != blue


# ---------------------------------------------------------------------------
# setter / restore 往返：撤销后 manifest 与像素逐位回到脚本原样（含 'edge'）
# ---------------------------------------------------------------------------
@needs_hatchcolor
@pytest.mark.parametrize("value", ["#0000ff", "edge"])
@pytest.mark.parametrize("gid", GIDS)
def test_set_then_undo_returns_to_the_script_look(worker, gid, value):
    base_man = _apply(worker, [])
    gid = _resolve(gid, base_man)
    base_png = _png(worker, [], "base")
    _apply(worker, [_hc(gid, value)])
    assert _apply(worker, []) == base_man, f"{gid}={value}：撤销后 manifest 没回到原样"
    assert _png(worker, [], f"{gid}-{value}-undo") == base_png


# ---------------------------------------------------------------------------
# 热态 == 全量重放；与 alpha / fill 的顺序无关
# ---------------------------------------------------------------------------
SEQUENCES = {
    # 设 → 改色 → 撤
    "set-change-undo": [
        [_hc(BAR_B1, "#0000ff")],
        [_hc(BAR_B1, "#ff00ff")],
        [],
    ],
    # 'edge' 往返
    "edge-roundtrip": [
        [_hc(SERIES_A, "#0000ff")],
        [_hc(SERIES_A, "edge")],
        [_hc(SERIES_A, "#0000ff")],
        [],
    ],
    # hatchcolor 与 alpha 两种顺序
    "hatchcolor-then-alpha": [
        [_hc(SERIES_B)],
        [_hc(SERIES_B), _alpha(SERIES_B)],
        [_alpha(SERIES_B)],
        [],
    ],
    "alpha-then-hatchcolor": [
        [_alpha(SERIES_B)],
        [_alpha(SERIES_B), _hc(SERIES_B)],
        [_hc(SERIES_B)],
        [],
    ],
    # hatchcolor → alpha → 撤 hatchcolor → 撤 alpha
    "hc-alpha-undo-hc-undo-alpha": [
        [_hc(SERIES_B)],
        [_hc(SERIES_B), _alpha(SERIES_B)],
        [_alpha(SERIES_B)],
        [],
    ],
    "alpha-hc-undo-alpha-undo-hc": [
        [_alpha(SERIES_B)],
        [_alpha(SERIES_B), _hc(SERIES_B)],
        [_hc(SERIES_B)],
        [],
    ],
    # 系列广播 + 单柱叠加撤销
    "series-then-single": [
        [_hc(SERIES_B, "#0000ff")],
        [_hc(SERIES_B, "#0000ff"), _hc(BAR_B1, "#ff00ff")],
        [_hc(BAR_B1, "#ff00ff")],
        [],
    ],
    # 单柱在前、系列在后，再全撤（#968，已修）
    "single-then-series": [
        [_hc(BAR_B1, "#ff00ff")],
        [_hc(BAR_B1, "#ff00ff"), _hc(SERIES_B, "edge")],
        [_hc(SERIES_B, "edge")],
        [],
    ],
    "series-then-single-with-alpha": [
        [_hc(SERIES_A), _alpha(SERIES_A)],
        [_hc(SERIES_A), _alpha(SERIES_A), _hc(BAR_A0, "edge")],
        [_alpha(SERIES_A), _hc(BAR_A0, "edge")],
        [],
    ],
}


@needs_hatchcolor
@pytest.mark.parametrize("name", sorted(SEQUENCES))
def test_hot_session_equals_a_fresh_replay_at_every_step(worker, library, name):
    base_png = _png(worker, [], "base")
    for i, patches in enumerate(SEQUENCES[name]):
        hot_man = _apply(worker, patches)
        hot_png = _png(worker, patches, f"{name}-{i}-hot")
        fresh_man, fresh_png = _fresh(library, patches, f"{name}-{i}-fresh")
        assert hot_man == fresh_man, f"{name} 第 {i} 步：热态 manifest ≠ 全量重放"
        assert hot_png == fresh_png, f"{name} 第 {i} 步：manifest 一样但画出来不一样"
    assert _png(worker, [], f"{name}-end") == base_png, f"{name}：撤空后没有回到脚本原样"


BAR_B0 = "axes_0.barseries_1.bar_0"  # 系列字段的读数取自第一根柱，单柱落在它身上才碰得到 `value_original`


@needs_hatchcolor
@pytest.mark.parametrize("bar_gid", [BAR_B1, BAR_B0])
@pytest.mark.parametrize(
    "prop,v1,v2",
    [("hatch_linewidth", 3.0, 2.0), ("hatchcolor", "#ff00ff", "edge"), ("hatch", "///", "|")],
)
@pytest.mark.parametrize(
    "order",
    [
        "single,both,series,empty",  # 单柱先改、系列后改，撤单柱再撤系列（#968）
        "series,both,single,empty",  # 系列先、单柱后，撤系列再撤单柱
        "single,both,empty",  # 一次全撤
        "series,both,empty",
    ],
)
def test_single_and_series_undo_orders_match_replay_and_return_to_the_script(
    worker, library, bar_gid, prop, v1, v2, order
):
    base_png = _png(worker, [], "base")
    bar, series = _p(bar_gid, prop, v1), _p(SERIES_B, prop, v2)
    table = {"single": [bar], "series": [series], "both": [bar, series], "empty": []}
    for i, step in enumerate(order.split(",")):
        patches = table[step]
        hot_man = _apply(worker, patches)
        hot_png = _png(worker, patches, f"{prop}-{order}-{i}")
        fresh_man, fresh_png = _fresh(library, patches, f"{prop}-{order}-{i}-f")
        assert hot_man == fresh_man, f"{order} 第 {i} 步：热态 manifest ≠ 重放"
        assert hot_png == fresh_png, f"{order} 第 {i} 步：像素 ≠ 重放"
        for gid in (
            SERIES_B,
            bar_gid,
        ):  # value_original 也要与重放一致（整份 manifest 已比，这里点名两个读数）
            hot_f, fresh_f = _fields(hot_man, gid)[prop], _fields(fresh_man, gid)[prop]
            assert hot_f.get("value_original") == fresh_f.get("value_original"), (
                f"{order} 第 {i} 步 {gid}：value_original 热态 ≠ 重放"
            )
    assert _png(worker, [], "end") == base_png


@needs_hatchcolor
def test_edit_draws_what_the_script_would_draw_including_the_legend(worker, tmp_path):
    """真值参照：脚本里直接写 `hatchcolor=` 的同一张图。图例示意块跟随源的花纹色，不需要另外同步。"""
    script = LIBRARY.replace(
        'label="B", facecolor="#c0562a", hatch="xx"',
        'label="B", facecolor="#c0562a", hatch="xx", hatchcolor="#0000ff"',
    )
    assert script != LIBRARY
    (tmp_path / SCRIPT_NAME).write_text(script, encoding="utf-8")
    w = _worker(tmp_path)
    try:
        want = _png(w, [], "script-hatchcolor")
    finally:
        pool.discard(w)
    assert _png(worker, [_hc(SERIES_B, "#0000ff")], "edit-hatchcolor") == want


@needs_hatchcolor
def test_alpha_and_hatchcolor_commute(worker):
    """两种应用顺序画出同一张图（列表顺序就是应用顺序，所以直接比两个顺序的列表）。"""
    a = [_hc(SERIES_B), _alpha(SERIES_B)]
    b = [_alpha(SERIES_B), _hc(SERIES_B)]
    assert _png(worker, a, "hc-a") == _png(worker, b, "a-hc")
    assert _apply(worker, a) == _apply(worker, b)


@needs_hatchcolor
def test_fill_and_hatchcolor_commute_on_a_standalone_patch(worker):
    gid = _patch_gid(_apply(worker, []))
    a = [_hc(gid), _p(gid, "fill", False)]
    b = [_p(gid, "fill", False), _hc(gid)]
    assert _png(worker, a, "hc-fill") == _png(worker, b, "fill-hc")


# ---------------------------------------------------------------------------
# rc_context 里建的图（#874 现存缺陷的形状）：hatchcolor 也要热态 == 重放、撤空 == 脚本原样
# ---------------------------------------------------------------------------
RC_SEQUENCES = {
    "bar": [
        [_hc(rc.BAR)],
        [_hc(rc.BAR), _alpha(rc.BAR, 0.5)],
        [_alpha(rc.BAR, 0.5)],
        [],
    ],
    "series-alpha-first": [
        [_alpha(rc.SERIES, 0.5)],
        [_alpha(rc.SERIES, 0.5), _hc(rc.SERIES, "edge")],
        [_hc(rc.SERIES, "edge")],
        [],
    ],
    "rect": [
        [_hc(rc.RECT), _p(rc.RECT, "fill", False)],
        [_hc(rc.RECT), _p(rc.RECT, "fill", False), _alpha(rc.RECT, 0.5)],
        [_p(rc.RECT, "fill", False)],
        [],
    ],
}


@pytest.fixture(scope="module")
def rc_library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("hatchcolor-rc-figures")
    (figs / rc.SCRIPT_NAME).write_text(rc.LIBRARY, encoding="utf-8")
    return figs


@needs_hatchcolor
@pytest.mark.parametrize("name", sorted(RC_SEQUENCES))
def test_rc_context_figure_hot_equals_replay_and_undo_returns_to_the_script(rc_library, name):
    w = _worker(rc_library, rc.SCRIPT_NAME)
    try:
        base_png = _png(w, [], "base", rc.STEM)
        for i, patches in enumerate(RC_SEQUENCES[name]):
            hot_man = _apply(w, patches, rc.STEM)
            hot_png = _png(w, patches, f"rc-{name}-{i}-hot", rc.STEM)
            fresh_man, fresh_png = _fresh(
                rc_library, patches, f"rc-{name}-{i}-fresh", rc.SCRIPT_NAME, rc.STEM
            )
            assert hot_man == fresh_man, f"{name} 第 {i} 步：热态 manifest ≠ 全量重放"
            assert hot_png == fresh_png, f"{name} 第 {i} 步：manifest 一样但像素不同"
        assert _png(w, [], f"rc-{name}-end", rc.STEM) == base_png, f"{name}：撤空 ≠ 脚本原样"
    finally:
        pool.discard(w)


# ---------------------------------------------------------------------------
# 序列化往返：override 过 JSON / patchspec 再应用，等于直接应用
# ---------------------------------------------------------------------------
@needs_hatchcolor
def test_patches_survive_json_and_patchspec_roundtrip(worker, library):
    patches = [_hc(SERIES_B), _hc(BAR_A0, "edge"), _alpha(SERIES_A)]
    assert len(patchspec.canonicalize(patches)) == len(patches)
    again = json.loads(json.dumps(patches, ensure_ascii=False))
    assert again == patches
    assert patchspec.patch_hash(again) == patchspec.patch_hash(patches)
    direct_man = _apply(worker, patches)
    _apply(worker, [])
    assert _apply(worker, again) == direct_man
    assert _fresh(library, again, "json-fresh")[1] == _png(worker, patches, "direct")


# ---------------------------------------------------------------------------
# ≤3.10：字段不发，旧版本重放新存的 override 是安静的空操作（warning 会阻断写回）
# ---------------------------------------------------------------------------
@pytest.mark.skipif(HAS_HATCHCOLOR, reason="本版本有 set_hatchcolor，没有可降级的")
def test_hatchcolor_override_on_a_matplotlib_without_the_setter_is_a_quiet_noop(worker):
    base_png = _png(worker, [], "base")
    for gid in (SERIES_B, BAR_B1):
        _apply(worker, [_hc(gid)])  # _apply 断言没有 warning
        assert _png(worker, [_hc(gid)], f"noop-{gid}") == base_png, "旧版本上凭空改了画面"
        assert "hatchcolor" not in _fields(_apply(worker, []), gid)


# ---------------------------------------------------------------------------
# 写回事务：编辑 alpha + hatchcolor → 写回 → 重放像素一致（走真 worker + Flask 全链路）
# ---------------------------------------------------------------------------
_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc0f01f0005000201"
    "a5f6d2a30000000049454e44ae426082"
)


@pytest.fixture
def project(tmp_path, monkeypatch, request):
    import pikepdf

    from tavotto import app as m

    script, lib, stem = getattr(
        request,
        "param",
        (SCRIPT_NAME, LIBRARY.replace('"HatchColor.png"', '"HatchColor.pdf"'), STEM),
    )
    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / script).write_text(lib, encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    script: {"entry": ENTRY, "cost": "light", "notes": "", "stems": [stem]}
                },
            }
        ),
        encoding="utf-8",
    )
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 100))
    pdf.save(figs / f"{stem}.pdf")
    (figs / f"{stem}.png").write_bytes(_PNG_1X1)
    m.open_project(str(figs))
    try:
        yield m, m.app.test_client(), figs
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()


def _render(client, patches, stem):
    r = client.post("/api/engine/render", json={"id": f"{stem}.pdf", "patches": patches})
    assert r.status_code == 200, r.get_json()
    assert not r.get_json().get("warnings"), r.get_json()["warnings"]
    return r.get_json()["manifest"]


@needs_hatchcolor
def test_write_back_alpha_and_hatchcolor_verifies_hot_equals_file_equals_replay(project):
    m, client, figs = project
    patches = [_alpha(SERIES_B), _hc(SERIES_B), _hc(BAR_A0, "edge"), _hc(BAR_B1, "#ff00ff")]
    _render(client, patches, STEM)
    plain = client.post("/api/engine/update_source", json={"id": f"{STEM}.pdf", "patches": []})
    assert plain.status_code == 200, plain.get_json()
    plain_bytes = (figs / f"{STEM}.pdf").read_bytes()
    resp = client.post("/api/engine/update_source", json={"id": f"{STEM}.pdf", "patches": patches})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["verification"]["replay"] == "ok", body["verification"]
    assert body["verification"]["pixels"] == "ok", body["verification"]
    assert body["warnings"] == []
    assert body["patch_hash"] == patchspec.patch_hash(patches)
    assert (figs / f"{STEM}.pdf").read_bytes() != plain_bytes, "写回的文件里没有花纹色的改动"
    # 脚本原件一个字节不动：编辑只存在于 override 里
    assert (figs / SCRIPT_NAME).read_text(encoding="utf-8") == LIBRARY.replace(
        '"HatchColor.png"', '"HatchColor.pdf"'
    )


@needs_hatchcolor
@pytest.mark.parametrize("project", [(rc.SCRIPT_NAME, rc.LIBRARY, rc.STEM)], indirect=True)
def test_write_back_after_hatchcolor_and_alpha_undone_is_not_a_replay_divergence(project):
    """rc_context 里建的图：改 hatchcolor + alpha，再全撤，写回不被 `replay_divergence` 拦。"""
    m, client, figs = project
    _render(client, [], rc.STEM)
    _render(client, [_hc(rc.BAR), _alpha(rc.BAR, 0.5)], rc.STEM)
    _render(client, [], rc.STEM)
    r = client.post("/api/engine/update_source", json={"id": f"{rc.STEM}.pdf", "patches": []})
    body = r.get_json()
    assert r.status_code == 200, f"全撤后写回被拦：{body.get('code')} {body.get('diffs')}"
    assert (body.get("verification") or {}).get("pixels") == "ok", body
