"""柱形 / 形状的**纹理三项**可编辑：图案 `hatch`、颜色 `hatchcolor`、线宽 `hatch_linewidth`。

背景：真实 Windows beta 用户的分组柱状图——柱子带斜线纹理，选中「柱 7」后填充色 / 描边色 / 线宽 /
不透明度都能改，唯独「纹理」一栏写着「此元素由脚本生成」。`_bar_fields` / `_bar_series_fields`
根本没发 `hatch` 字段（`Patch` 族的 `_patch_fields` 早就有），引擎侧的 `("bar", "hatch")` setter
却一直在（能力层 `_PATCH_CAPS` 注册给了 `bar`），系列级则整条缺席。

两条**按版本发**的字段，判据是真实 getter 实况、不是版本号字符串：

  * `hatchcolor`：`Patch.set_hatchcolor` 是 matplotlib 3.11 才有的。之前花纹颜色就是边色，没有
    独立属性（用户改「描边色」就是改花纹颜色）——旧版本不发字段，而不是发一条改了不生效的。
  * `hatch_linewidth`：`Patch.set_hatch_linewidth` 是 3.10 才有的。之前只有 rcParams 全局值。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里，那个解释器有没有这两个
setter 由子进程探一次（`CAPS`），断言按能力集写——所以同一份用例在 3.8 / 3.10 / 3.11 上各自判
「该发的发了、不该发的没发」（nightly 的版本矩阵跑这个文件）。

判据的主语（每条断言说谁的、哪个时刻）：
  * 字段表：**manifest 里该元素的 editable**（worker 此刻的真实状态），不是前端的 profile；
  * 能力真实：**同一张图上改前改后的像素**（`preview_png` 状态中立）；
  * 还原：撤销之后的 **manifest 与像素**回到脚本原样；
  * 热态 == 重放：热 worker 增量应用的结果 vs **只见最终列表的全新 worker**。
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess

import pytest

from tavotto.engine import patchspec, pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_hatch.py"
ENTRY = "main"
STEM = "Hatch"

#: 分组柱状图（用户的图）：A 组脚本带斜线纹理 + 显式描边，B 组没有纹理；外加一个独立形状。
LIBRARY = """\
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.6))
    x = np.arange(3)
    ax.bar(x - 0.2, [3, 5, 4], 0.4, label="A", facecolor="#47749e", edgecolor="#000000",
           linewidth=1, hatch="//")
    ax.bar(x + 0.2, [2, 4, 6], 0.4, label="B", facecolor="#c0562a")
    ax.add_patch(Rectangle((3.0, 0.3), 0.6, 2.0, facecolor="#2A6F3C", edgecolor="#222222"))
    ax.set_xlim(-0.7, 3.8)
    ax.set_ylim(0, 7)
    ax.legend(loc="upper left")
    fig.savefig("Hatch.png")
"""

SERIES_A = "axes_0.barseries_0"
SERIES_B = "axes_0.barseries_1"
BAR_A0 = "axes_0.barseries_0.bar_0"
BAR_B1 = "axes_0.barseries_1.bar_1"

PROBE = (
    "import matplotlib.patches as p; r = p.Rectangle((0, 0), 1, 1); "
    "print(int(hasattr(r, 'set_hatchcolor')), int(hasattr(r, 'set_hatch_linewidth')))"
)


def _caps() -> tuple[bool, bool]:
    out = subprocess.run(  # noqa: S603 — 测试里探 worker 解释器的能力
        [WORKER_PY, "-c", PROBE], capture_output=True, text=True, check=True
    ).stdout.split()
    return out[0] == "1", out[1] == "1"


HAS_COLOR, HAS_WIDTH = _caps()

#: 该版本上应当发出的两条字段
EXPECTED_EXTRA = [p for p, ok in (("hatchcolor", HAS_COLOR), ("hatch_linewidth", HAS_WIDTH)) if ok]
#: 不该发的
MISSING_EXTRA = [p for p in ("hatchcolor", "hatch_linewidth") if p not in EXPECTED_EXTRA]

VALUES = {"hatch": "xx", "hatchcolor": "#d62728", "hatch_linewidth": 3.0}


def _p(gid, prop, value):
    return {"gid": gid, "prop": prop, "value": value}


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("hatch-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _worker(library):
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    w.ensure_built()
    return w


@pytest.fixture
def worker(library):
    w = _worker(library)
    yield w
    pool.discard(w)


def _apply(w, patches) -> dict:
    resp = w.override(STEM, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp["manifest"]


def _png(w, patches, tag) -> str:
    tag = re.sub(r"[^A-Za-z0-9_.-]", "_", tag)  # tag 进文件名：花纹代码里有 / 和 \\
    return hashlib.sha1(w.preview_png(STEM, list(patches), 420, tag).read_bytes()).hexdigest()


def _fresh(library, patches, tag):
    w = _worker(library)
    try:
        return _apply(w, patches), _png(w, patches, tag)
    finally:
        pool.discard(w)


def _fields(man, gid) -> dict:
    hits = [e for e in man["elements"] if e["gid"] == gid]
    assert hits, f"{gid} 不在 manifest 里：{sorted(e['gid'] for e in man['elements'])}"
    return {f["prop"]: f for f in hits[0]["editable"]}


def _patch_gid(man) -> str:
    return next(e["gid"] for e in man["elements"] if e["role"] == "patch")


#: 带纹理的前提：B 组与独立形状脚本里没有花纹，先打开再量颜色 / 线宽
def _with_hatch(gid):
    return [_p(gid, "hatch", "/")]


# ---------------------------------------------------------------------------
# 字段：系列 / 单柱 / 形状口径一致，按版本发
# ---------------------------------------------------------------------------
def test_fixture_exposes_series_bars_and_a_standalone_patch(worker):
    man = _apply(worker, [])
    gids = {e["gid"] for e in man["elements"]}
    assert {SERIES_A, SERIES_B, BAR_A0, BAR_B1}.issubset(gids), sorted(gids)
    assert _patch_gid(man)


def test_hatch_fields_are_advertised_for_series_single_bar_and_patch(worker):
    """用户的截图：选中「柱 7」，纹理一栏不该缺席。系列、单柱、形状三处同口径。"""
    man = _apply(worker, [])
    for gid in (SERIES_A, SERIES_B, BAR_A0, BAR_B1, _patch_gid(man)):
        fields = _fields(man, gid)
        assert "hatch" in fields, f"{gid} 没发 hatch 字段（柱形纹理曾经就缺在这里）"
        for must in EXPECTED_EXTRA:
            assert must in fields, f"{gid} 缺 {must}（本版本的 Patch 有对应 setter）"
        for gone in MISSING_EXTRA:
            assert gone not in fields, f"{gid} 发了 {gone}，而本版本 matplotlib 没有这条属性"
    hatch = _fields(man, SERIES_A)["hatch"]
    assert hatch["value"] == "//"
    # 预设 + 密度档位（重复字符 = 更密）
    for code in ("", "/", "\\", "|", "-", "+", "x", "o", "O", ".", "*", "//", "///"):
        assert code in hatch["options"], f"选项里没有 {code!r}"
    assert _fields(man, SERIES_B)["hatch"]["value"] == ""


@pytest.mark.skipif(not HAS_COLOR, reason="花纹颜色是 matplotlib 3.11 起的独立属性")
def test_hatchcolor_reports_the_colour_the_hatch_is_drawn_in(worker):
    """脚本给了黑色描边 → 花纹跟边色（`'edge'` 模式）→ 字段值是解析后的黑；改边色，字段跟着走。"""
    man = _apply(worker, [])
    assert _fields(man, SERIES_A)["hatchcolor"]["value"] == "#000000"
    man = _apply(worker, [_p(SERIES_A, "edgecolor", "#ff00ff")])
    assert _fields(man, SERIES_A)["hatchcolor"]["value"] == "#ff00ff"


# ---------------------------------------------------------------------------
# 能力真实：改了，像素真的变
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("gid", [SERIES_A, SERIES_B, BAR_A0, BAR_B1, "PATCH"])
def test_each_hatch_prop_changes_the_pixels(worker, gid):
    man = _apply(worker, [])
    gid = _patch_gid(man) if gid == "PATCH" else gid
    base = _png(worker, [], "base")
    assert _png(worker, _with_hatch(gid), f"{gid}-hatch") != base, f"{gid}：打开花纹没有动像素"
    on = _with_hatch(gid)
    on_png = _png(worker, on, f"{gid}-on")
    for prop in EXPECTED_EXTRA:
        changed = on + [_p(gid, prop, VALUES[prop])]
        assert _png(worker, changed, f"{gid}-{prop}") != on_png, f"{gid}.{prop} 改了画面没变"
    # 换图案 / 加密度也要看得见
    for code in ("xx", "///"):
        assert _png(worker, [_p(gid, "hatch", code)], f"{gid}-{code}") != _png(
            worker, _with_hatch(gid), f"{gid}-slash"
        ), f"{gid}：花纹 {code!r} 与 '/' 画出来一样"


# ---------------------------------------------------------------------------
# setter / restore 往返：撤销后 manifest 与像素逐位回到脚本原样
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("gid", [SERIES_A, SERIES_B, BAR_A0, BAR_B1, "PATCH"])
def test_setter_restore_roundtrip_is_exact(worker, gid):
    base_man = _apply(worker, [])
    gid = _patch_gid(base_man) if gid == "PATCH" else gid
    base_png = _png(worker, [], "base")
    for prop in ("hatch", *EXPECTED_EXTRA):
        patches = ([] if prop == "hatch" else _with_hatch(gid)) + [_p(gid, prop, VALUES[prop])]
        _apply(worker, patches)
        assert _apply(worker, []) == base_man, f"{gid}.{prop}：撤销后 manifest 没回到原样"
        assert _png(worker, [], f"{gid}-{prop}-undo") == base_png, f"{gid}.{prop}：撤销后像素不一样"
    # 一次性全改再撤
    allp = [_p(gid, p, v) for p, v in VALUES.items() if p == "hatch" or p in EXPECTED_EXTRA]
    _apply(worker, allp)
    assert _apply(worker, []) == base_man
    assert _png(worker, [], f"{gid}-all-undo") == base_png


@pytest.mark.skipif(not HAS_COLOR, reason="花纹颜色是 matplotlib 3.11 起的独立属性")
def test_undoing_hatchcolor_restores_the_follow_edge_mode_not_a_frozen_colour(worker, library):
    """原样是「跟边色」这个**模式**，不是一个值：撤掉花纹颜色之后再改边色，花纹要跟着走。

    getter 若回解析后的 RGBA，还原就把模式换成死颜色——manifest 这一刻看着一样，下一次改边色才
    分岔（与 `_PatchEdge` / `_PatchFace` 同一个坑）。判据取撤销**之后**再改边色的结果。
    """
    _apply(worker, [_p(SERIES_A, "hatchcolor", "#00aa00")])
    _apply(worker, [])
    man = _apply(worker, [_p(SERIES_A, "edgecolor", "#ff00ff")])
    assert _fields(man, SERIES_A)["hatchcolor"]["value"] == "#ff00ff", "花纹颜色没有回到跟边色"
    fresh_man, fresh_png = _fresh(library, [_p(SERIES_A, "edgecolor", "#ff00ff")], "follow-fresh")
    assert man == fresh_man
    assert _png(worker, [_p(SERIES_A, "edgecolor", "#ff00ff")], "follow-hot") == fresh_png


# ---------------------------------------------------------------------------
# 热态 == 全量重放（含系列 ↔ 单柱的广播次序）
# ---------------------------------------------------------------------------
SEQUENCES = {
    "series-then-single": [
        [_p(SERIES_B, "hatch", "//")],
        [_p(SERIES_B, "hatch", "//"), _p(BAR_B1, "hatch", "xx")],
        [_p(BAR_B1, "hatch", "xx")],
    ],
    "single-then-series": [
        [_p(BAR_B1, "hatch", "xx")],
        [_p(BAR_B1, "hatch", "xx"), _p(SERIES_B, "hatch", "///")],
        [_p(SERIES_B, "hatch", "///")],
    ],
    # 不撤边色：≤3.10 上「撤边色 + 不透明度还在」本来就热态 ≠ 重放（`_PatchEdge` 快照的花纹色
    # 不带 alpha，见回报；与本文件的三条属性无关，不在这里钉）
    "colour-width-alpha": [
        [_p(SERIES_A, "edgecolor", "#ff00ff")],
        [_p(SERIES_A, "edgecolor", "#ff00ff"), _p(SERIES_A, "alpha", 0.5)],
        [_p(SERIES_A, "edgecolor", "#ff00ff"), _p(SERIES_A, "alpha", 0.5)],
        [_p(SERIES_A, "edgecolor", "#ff00ff"), _p(SERIES_A, "linewidth", 2.0)],
    ],
}


def _extras(gid):
    return [_p(gid, p, VALUES[p]) for p in EXPECTED_EXTRA]


@pytest.mark.parametrize("name", sorted(SEQUENCES))
def test_hot_session_equals_a_fresh_replay_at_every_step(worker, library, name):
    steps = [
        s + (_extras(SERIES_A) if name == "colour-width-alpha" else []) for s in SEQUENCES[name]
    ]
    for i, patches in enumerate(steps):
        hot_man = _apply(worker, patches)
        hot_png = _png(worker, patches, f"{name}-{i}-hot")
        fresh_man, fresh_png = _fresh(library, patches, f"{name}-{i}-fresh")
        assert hot_man == fresh_man, f"{name} 第 {i} 步：热态 manifest ≠ 全量重放"
        assert hot_png == fresh_png, f"{name} 第 {i} 步：manifest 一样但画出来不一样"
    assert _apply(worker, []) == _apply(worker, [])


def test_series_then_single_then_undo_returns_to_the_script_look(worker):
    """系列广播与单柱同名 prop 叠加：先改整体再改单条，单条记下的「原样」已被整体改过；
    撤销要回到脚本原样（`ALIAS_GROUPS` 的 bar_series 一行必须点名纹理三项）。"""
    base_man = _apply(worker, [])
    base_png = _png(worker, [], "base")
    for prop in ("hatch", *EXPECTED_EXTRA):
        v = VALUES[prop]
        pre = [] if prop == "hatch" else [_p(SERIES_B, "hatch", "/"), _p(BAR_B1, "hatch", "/")]
        _apply(worker, pre + [_p(SERIES_B, prop, v)])
        _apply(worker, pre + [_p(SERIES_B, prop, v), _p(BAR_B1, prop, v)])
        _apply(worker, [])
        assert _png(worker, [], f"{prop}-undo") == base_png, f"{prop}：撤销没有回到脚本原样"
        assert _apply(worker, []) == base_man


# ---------------------------------------------------------------------------
# 序列化往返：override 过 JSON（baked / 文档）再应用，等于直接应用
# ---------------------------------------------------------------------------
def test_patches_survive_json_and_patchspec_roundtrip(worker, library):
    patches = [
        _p(SERIES_B, "hatch", "\\\\\\"),  # 三个反斜杠：转义最容易出错的一档
        *_extras(SERIES_B),
        _p(BAR_A0, "hatch", "xxx"),
    ]
    canon = patchspec.canonicalize(patches)
    assert len(canon) == len(patches)
    again = json.loads(json.dumps(patches, ensure_ascii=False))
    assert again == patches
    assert patchspec.patch_hash(again) == patchspec.patch_hash(patches)
    direct_man = _apply(worker, patches)
    _apply(worker, [])
    via_json_man = _apply(worker, again)
    assert via_json_man == direct_man
    assert _fields(via_json_man, SERIES_B)["hatch"]["value"] == "\\\\\\"
    assert _png(worker, again, "json") == _png(worker, patches, "direct")
    assert _fresh(library, again, "json-fresh")[1] == _png(worker, patches, "direct2")


# ---------------------------------------------------------------------------
# 降级：旧版本上重放新存的 override——不炸、不 warning（warning 会阻断写回），也不假装生效
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not MISSING_EXTRA, reason="本版本的 matplotlib 两条属性都有，没有可降级的")
def test_overrides_for_properties_this_matplotlib_lacks_replay_as_a_quiet_noop(worker):
    on = _with_hatch(SERIES_B)
    on_png = _png(worker, on, "on")
    for prop in MISSING_EXTRA:
        patches = on + [_p(SERIES_B, prop, VALUES[prop])]
        _apply(worker, patches)  # _apply 断言没有 warning
        assert _png(worker, patches, f"noop-{prop}") == on_png, f"{prop}：旧版本上凭空改了画面"
        man = _apply(worker, [])
        assert prop not in _fields(man, SERIES_B)


# ---------------------------------------------------------------------------
# 写回事务：热态 == 写进文件 == 重开重放（走真 worker + Flask 全链路）
# ---------------------------------------------------------------------------
@pytest.fixture
def project(tmp_path, monkeypatch):
    import pikepdf

    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / SCRIPT_NAME).write_text(LIBRARY.replace('"Hatch.png"', '"Hatch.pdf"'), encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    SCRIPT_NAME: {"entry": ENTRY, "cost": "light", "notes": "", "stems": [STEM]}
                },
            }
        ),
        encoding="utf-8",
    )
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 100))
    pdf.save(figs / "Hatch.pdf")
    m.open_project(str(figs))
    try:
        yield m, m.app.test_client(), figs
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        from tavotto.engine import project_watch

        project_watch.stop()


def test_write_back_verifies_hatch_edits_hot_equals_file_equals_replay(project):
    """纹理三项走写回：prepare → 一次性 worker 全量重放 → 几何 + 像素门，全过才落盘。

    像素门是这条的牙：颜色 / 线宽 / 图案都不动任何包围盒，只有逐像素比量得到热态与重放的分歧。
    """
    m, client, figs = project
    patches = [
        _p(SERIES_B, "hatch", "///"),
        *_extras(SERIES_B),
        _p(BAR_A0, "hatch", "xx"),
    ]
    r = client.post("/api/engine/render", json={"id": "Hatch.pdf", "patches": patches})
    assert r.status_code == 200, r.get_json()
    assert not r.get_json().get("warnings"), r.get_json()["warnings"]
    plain = client.post("/api/engine/update_source", json={"id": "Hatch.pdf", "patches": []})
    assert plain.status_code == 200, plain.get_json()
    plain_bytes = (figs / "Hatch.pdf").read_bytes()
    resp = client.post("/api/engine/update_source", json={"id": "Hatch.pdf", "patches": patches})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["verification"]["replay"] == "ok", body["verification"]
    assert body["verification"]["pixels"] == "ok", body["verification"]
    assert body["warnings"] == []
    assert body["patch_hash"] == patchspec.patch_hash(patches)
    assert (figs / "Hatch.pdf").read_bytes() != plain_bytes, "写回的文件里没有纹理的改动"
    # 脚本原件一个字节不动：编辑只存在于 override 里（最小侵入）
    assert (figs / SCRIPT_NAME).read_text(encoding="utf-8").count("hatch") == 1
