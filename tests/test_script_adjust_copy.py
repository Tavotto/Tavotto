"""ADR 0207 PR 2：图内副本 `copy_of` 进「导出修改后的 .py」（ADR 0171）与「同步到同脚本的其他图」。

判据的主语分三段：

* 生成器（纯字节、纯标准库）：创建语句排在全部属性语句之前、副本 gid 上的修改指向这只副本、
  源的选择器写不出 / 副本上有一条写不进去 → **整件事**留作重放（不生成半截代码）；
* 与引擎的同源对（真 matplotlib，worker 解释器）：脚本里的 `_copy_of` 建出来的 Text 与引擎 `_ensure_copy` 逐项一致；
  排序档、gid 形状与第一期源的闭集两侧对账；
* 端到端（真 worker + `app._verify_edited_script`）：带副本的 .py 验证通过（几何 + 像素）、产物里真有副本的字；
  同步时副本被跳过并带稳定原因码，其余修改照常映射。
"""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pymupdf
import pytest

from support.scriptadjust_harness import WORKER_PY, _replays_identically, real_worker
from tavotto.engine import pool, scriptadjust

pytest_plugins = ("support.scriptadjust_harness",)

CID = "ab12cd34"
HOST = "axes_0"
COPY = f"{HOST}.copy_{CID}"

MAN = {
    "elements": [
        {"gid": "axes_0", "role": "axes", "label": "子图 1", "editable": []},
        {"gid": "axes_0.title", "role": "title", "label": "标题", "editable": []},
        {"gid": "axes_0.ylabel", "role": "axis_label", "label": "y 轴标题", "editable": []},
        {"gid": "axes_0.texts_0", "role": "text", "label": "文字 1", "editable": []},
        {"gid": "fig.texts_0", "role": "text", "label": "图文字 1", "editable": []},
        {"gid": COPY, "role": "title", "label": "标题 的副本", "editable": []},
        {"gid": f"fig.copy_{CID}", "role": "text", "label": "图文字 1 的副本", "editable": []},
    ]
}


def _make(gid=COPY, src="axes_0.title", text="Copy", at=(0.5, 0.7)) -> dict:
    return {"gid": gid, "prop": "copy_of", "value": {"src": src, "text": text, "at": list(at)}}


def _build(patches, man=MAN, src=b"import os\n") -> scriptadjust.Adjusted:
    return scriptadjust.build(src, "Fig1", list(patches), man, version="9.9")


# ---------------------------------------------------------------------- 生成器
def test_copy_is_a_creation_statement_and_later_edits_point_at_it():
    out = _build([_make(), {"gid": COPY, "prop": "fontsize", "value": 14}])
    text = out.data.decode()
    ast.parse(text)
    assert out.written == 2 and not out.residual and out.copies == [COPY]
    assert f"_copy_of(fig, {COPY!r}, _ax(fig, 0), _ax(fig, 0).title, [0.5, 0.7], 'Copy')" in text
    assert f"_copy_ref(fig, {COPY!r}).set_fontsize(14)" in text
    assert text.index(f"_copy_of(fig, {COPY!r}") < text.index("set_fontsize")
    assert text.isascii()


def test_copy_is_created_before_every_property_statement_even_the_sources_own():
    """样式按「脚本原样的源」：改源样式的语句排在创建之后，副本才抄到脚本原样（与引擎 `_RANK_COPY` 先于其余档同一语义）。"""
    patches = [
        {"gid": "axes_0.title", "prop": "color", "value": "#ff0000"},  # 源的修改写在前面
        {"gid": "axes_0", "prop": "xscale", "value": "log"},  # 脚本里排最前的普通档
        _make(),
        {"gid": COPY, "prop": "color", "value": "#00ff00"},
    ]
    text = _build(patches).data.decode()
    made = text.index(f"_copy_of(fig, {COPY!r}")
    assert made < text.index("set_color('#ff0000')")
    assert made < text.index("set_xscale")
    assert made < text.index("set_color('#00ff00')")


def test_copy_rank_is_below_every_other_script_rank():
    others = [
        scriptadjust._rank(p, k)
        for p, k in [
            ("xscale", None),
            ("fontsize", None),
            ("x", "rebuild"),
            ("x", "detach"),
            ("x", "broadcast"),
            ("x", "tick_model"),
            ("x", "tick_text"),
            ("handle_color", None),
            ("label", "rename"),
        ]
    ]
    assert scriptadjust._rank(scriptadjust.COPY_PROP, "copy") < min(others)
    assert scriptadjust._kind("text", "copy_of") == "copy"


def test_dragged_copy_moves_like_a_plain_text_even_when_the_source_is_a_title():
    """副本沿用源的角色（标题的副本仍是 `title`），但它是一段独立 Text：不走 `set_title`。"""
    out = _build([_make(), {"gid": COPY, "prop": "pos_frac", "value": [0.4, 0.3]}])
    text = out.data.decode()
    assert not out.residual
    assert f"_text_to(_copy_ref(fig, {COPY!r}), fig, 0.4, 0.3)" in text
    assert "_title_to" not in text


def test_axis_label_source_goes_through_the_label_proxy():
    out = _build([_make(src="axes_0.ylabel", text="Y2")])
    text = out.data.decode()
    assert "_AxisLabel(_ax(fig, 0), 'y')" in text and "src.axis.label" in text


def test_figure_text_copy_lives_on_the_figure_and_leaves_fig_texts_numbering_alone():
    gid = f"fig.copy_{CID}"
    out = _build([_make(gid=gid, src="fig.texts_0", at=(0.5, 0.1))])
    text = out.data.decode()
    assert not out.residual and out.copies == [gid]
    assert f"_copy_of(fig, {gid!r}, fig, _at(fig.texts, 0), [0.5, 0.1], 'Copy')" in text


@pytest.mark.parametrize(
    "patch, why",
    [
        (_make(src="axes_0.legend.texts_0"), "源不是第一期认的那几类"),
        (_make(src="axes_0.xticklabels_0"), "刻度文字不当源"),
        (_make(src="axes_0.lines_0"), "不是文字"),
        (_make(src="axes_1.title"), "源在别的子图"),
        (_make(gid=f"fig.copy_{CID}", src="axes_0.title"), "整图宿主只认 fig.texts"),
        (_make(src="axes_0.texts_9"), "manifest 里没有这个源"),
        ({**_make(), "value": {"src": "axes_0.title", "text": "x", "at": [0.5]}}, "落点形状不对"),
        (
            {**_make(), "value": {"src": "axes_0.title", "text": 3, "at": [0.5, 0.5]}},
            "文字不是字符串",
        ),
        (
            {**_make(), "value": {"src": "axes_0.title", "text": "x", "at": [0.5, float("nan")]}},
            "非有限数",
        ),
    ],
)
def test_unwritable_copy_goes_to_skipped_whole_with_its_edits_and_writes_no_half_code(patch, why):
    """源的选择器写不出：整条副本与它 gid 上的全部修改进 `skipped`；写得进去的别的修改照常写。"""
    edit = {"gid": patch["gid"], "prop": "fontsize", "value": 14}
    other = {"gid": "axes_0", "prop": "facecolor", "value": "#eeeeee"}
    out = _build([patch, edit, other])
    text = out.data.decode()
    assert {(s["gid"], s["prop"]) for s in out.skipped} == {
        (patch["gid"], "copy_of"),
        (patch["gid"], "fontsize"),
    }, why
    assert [(r["gid"], r["prop"]) for r in out.residual] == [
        (patch["gid"], "copy_of"),
        (patch["gid"], "fontsize"),
    ]
    assert "_copy_of(fig" not in text and "_copy_ref(fig" not in text
    assert out.written == 1 and "set_facecolor('#eeeeee')" in text and not out.copies


def test_3d_and_colorbar_hosts_are_not_written():
    man = {
        "elements": [
            {"gid": "axes_0", "role": "axes3d", "editable": []},
            {"gid": "axes_0.title", "role": "title", "editable": []},
            {"gid": "axes_1", "role": "axes", "editable": []},
            {"gid": "axes_1.colorbar", "role": "colorbar", "editable": []},
            {"gid": "axes_1.title", "role": "title", "editable": []},
            {"gid": COPY, "role": "title", "editable": []},
            {"gid": "axes_1.copy_ab12cd34", "role": "title", "editable": []},
        ]
    }
    out = _build([_make(), _make(gid="axes_1.copy_ab12cd34", src="axes_1.title")], man)
    assert out.written == 0 and len(out.residual) == 2


def test_one_unwritable_edit_on_the_copy_holds_the_whole_copy_and_its_sources_edits():
    """写进脚本的副本只认写进脚本的修改；被留下的副本样式取「脚本原样的源」，源上的修改也要留下（否则重放抄到改过的样子）。"""
    patches = [
        {"gid": "axes_0.title", "prop": "color", "value": "#ff0000"},  # 源上的修改
        _make(),
        {"gid": COPY, "prop": "fontsize", "value": 14},
        {"gid": COPY, "prop": "no_such_prop_yet", "value": 1},  # 引擎认得、脚本写不出的一条
        {"gid": "axes_0", "prop": "facecolor", "value": "#eeeeee"},  # 无关的修改照常写
    ]
    out = _build(patches)
    text = out.data.decode()
    assert [(r["gid"], r["prop"]) for r in out.residual] == [
        ("axes_0.title", "color"),
        (COPY, "copy_of"),
        (COPY, "fontsize"),
        (COPY, "no_such_prop_yet"),
    ]
    assert out.written == 1 and out.copies == []
    assert "_copy_of(fig" not in text and "set_facecolor('#eeeeee')" in text


def test_edit_without_a_creation_record_is_left_to_the_engine_to_report():
    out = _build([{"gid": COPY, "prop": "fontsize", "value": 14}])
    assert out.written == 0 and [(r["gid"], r["prop"]) for r in out.residual] == [
        (COPY, "fontsize")
    ]


def test_held_broadcast_also_holds_the_copy_it_covers():
    """`axes_0` 上同名属性留作重放时，窄的（它的子孙 gid，含副本）一起留下——副本整件事跟着走。"""
    patches = [
        {"gid": "axes_0", "prop": "visible", "value": False},  # axes.visible 不写进脚本
        _make(),
        {"gid": COPY, "prop": "visible", "value": False},
    ]
    out = _build(patches)
    assert out.written == 0 and out.copies == []
    assert {(r["gid"], r["prop"]) for r in out.residual} == {
        ("axes_0", "visible"),
        (COPY, "copy_of"),
        (COPY, "visible"),
    }


def test_two_copies_are_listed_in_creation_order():
    other = f"{HOST}.copy_zz99yy88"
    man = {"elements": [*MAN["elements"], {"gid": other, "role": "text", "editable": []}]}
    out = _build([_make(gid=other, src="axes_0.texts_0"), _make()], man)
    assert out.copies == [other, COPY]
    text = out.data.decode()
    assert text.index(f"_copy_of(fig, {other!r}") < text.index(f"_copy_of(fig, {COPY!r}")


def test_the_block_only_carries_the_copy_helpers_when_a_copy_is_written():
    plain = _build([{"gid": "axes_0.title", "prop": "color", "value": "#123456"}]).data.decode()
    assert "_copy_of" not in plain and "_copy_ref" not in plain
    made = _build([_make(), {"gid": COPY, "prop": "fontsize", "value": 9}]).data.decode()
    assert "def _copy_of" in made and "def _copy_ref" in made and "class _AxisLabel" in made
    assert "tavotto" not in made.lower().replace("# edits made in tavotto", "").replace(
        "_tavotto", ""
    ).replace("tavotto adjustments", "")


# ---------------------------------------------------------------------- 同步
def test_sync_skips_copies_with_a_stable_reason_and_maps_the_rest(monkeypatch):
    """副本不跨图同步（源在目标图上未必存在）：`copy_of` 与副本 gid 上的修改进 `skipped` 带原因码；其余照常映射。"""
    from support.registrystub import stem_registry
    from tavotto import app as m

    manifest = {
        "elements": [
            {"gid": "axes_0", "bbox": [0.1, 0.1, 0.8, 0.8]},
            {"gid": "axes_0.title", "editable": []},
            {"gid": COPY, "editable": []},
        ]
    }
    registry = stem_registry({"script": "flow.py", "entry": "main"})
    monkeypatch.setattr(m, "safe_resolve", Path)
    monkeypatch.setattr(m, "require_project", lambda: Path("."))
    monkeypatch.setattr(m, "current_registry", lambda: registry)
    monkeypatch.setattr(m, "_safe_worker", lambda *args: object())
    monkeypatch.setattr(m, "_manifest_of", lambda worker, stem: manifest)
    color = {"gid": "axes_0.title", "prop": "color", "value": "#123456"}
    patches = [
        color,
        _make(),
        {"gid": COPY, "prop": "fontsize", "value": 14},
        {"gid": f"fig.copy_{CID}", "prop": "text", "value": "fig copy"},
    ]
    with m.app.test_request_context(
        "/api/engine/sync_overrides",
        method="POST",
        json={"from_id": "Flow.pdf", "to_id": "Flow_detail.pdf", "patches": patches},
    ):
        response = m.api_engine_sync_overrides()
    assert response.status_code == 200
    result = response.get_json()
    assert result["mapped"] == [color]
    assert result["unmatched"] == []
    assert [(p["gid"], p["prop"], p["skip_reason"]) for p in result["skipped"]] == [
        (COPY, "copy_of", m.SYNC_SKIP_COPY),
        (COPY, "fontsize", m.SYNC_SKIP_COPY),
        (f"fig.copy_{CID}", "text", m.SYNC_SKIP_COPY),
    ]
    assert m.SYNC_SKIP_COPY == "copy_not_synced"


def test_copies_do_not_change_how_axes_are_paired_between_figures():
    from tavotto import app as m

    base = [
        {"gid": "axes_0", "bbox": [0, 0, 1, 1], "editable": []},
        {"gid": "axes_0.title", "editable": [{"prop": "text", "value": "T"}]},
    ]
    with_copy = [*base, {"gid": COPY, "editable": [{"prop": "text", "value": "Copy"}]}]
    assert m._axes_info({"elements": with_copy}) == m._axes_info({"elements": base})


# ---------------------------------------------------------------------- 验证时的 gid 对应
def test_baked_copy_gids_are_matched_to_the_new_scripts_serial_names_in_creation_order():
    from tavotto import app as m

    other = f"{HOST}.copy_zz99yy88"
    hot = {
        "elements": [
            {"gid": "axes_0.texts_0", "bbox": [0, 0, 1, 1]},
            {"gid": COPY, "bbox": [0.1, 0.1, 0.2, 0.2]},
            {"gid": other, "bbox": [0.3, 0.3, 0.4, 0.4]},
            {"gid": f"fig.copy_{CID}", "bbox": [0.5, 0.5, 0.6, 0.6]},
        ]
    }
    fresh = {
        "elements": [
            {"gid": "axes_0.texts_0", "bbox": [0, 0, 1, 1]},
            {"gid": "axes_0.texts_1", "bbox": [0.1, 0.1, 0.2, 0.2]},
            {"gid": "axes_0.texts_2", "bbox": [0.3, 0.3, 0.4, 0.4]},
            {"gid": "fig.artists_0", "bbox": [0.5, 0.5, 0.6, 0.6]},
        ]
    }
    aliased = m._alias_baked_copies(hot, fresh, [COPY, other, f"fig.copy_{CID}"])
    assert [e["gid"] for e in aliased["elements"]] == [
        "axes_0.texts_0",
        "axes_0.texts_1",
        "axes_0.texts_2",
        "fig.artists_0",
    ]
    assert m._compare_manifests(aliased, fresh)[0] == []
    # 不放松：换了名字之后几何照比——位置不一致仍是分歧
    fresh["elements"][1]["bbox"] = [0.6, 0.1, 0.2, 0.2]
    assert [d["field"] for d in m._compare_manifests(aliased, fresh)[0]] == ["bbox"]


def test_a_baked_copy_missing_from_the_new_script_stays_a_divergence():
    from tavotto import app as m

    hot = {
        "elements": [
            {"gid": "axes_0.texts_0", "bbox": [0, 0, 1, 1]},
            {"gid": COPY, "bbox": [0, 0, 1, 1]},
        ]
    }
    fresh = {"elements": [{"gid": "axes_0.texts_0", "bbox": [0, 0, 1, 1]}]}
    aliased = m._alias_baked_copies(hot, fresh, [COPY])
    assert [d["field"] for d in m._compare_manifests(aliased, fresh)[0]] == ["missing_in_replay"]
    assert m._alias_baked_copies(hot, fresh, []) is hot


# ---------------------------------------------------------------------- 与引擎的同源对
_DRIVER = """\
import json, sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import overrides, manifest

runtime = open(sys.argv[2], encoding="utf-8").read()


def figure():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1])
    ax.set_title("Title", color="#336699", fontstyle="italic", rotation=7, alpha=0.8, zorder=7)
    ax.title.set_path_effects([pe.withStroke(linewidth=2, foreground="w")])
    ax.title.set_wrap(True)
    ax.set_ylabel("Signal", fontsize=13, fontweight="bold", labelpad=9)
    ax.text(0.2, 0.8, r"$\\alpha$ boxed", fontsize=9, linespacing=1.7, parse_math=False, rotation_mode="anchor",
            rotation=20, ha="left", va="top", bbox=dict(boxstyle="round,pad=0.4", fc="#ffeeaa", ec="k", lw=1.5, alpha=0.6))
    ax.annotate("note", xy=(0.5, 0.5), xytext=(0.6, 0.2), arrowprops=dict(arrowstyle="->"))
    fig.text(0.5, 0.02, "fig text", color="C2", ha="center")
    return fig, ax


CASES = [
    ("axes_0.copy_ab12cd34", "axes_0.title", "axes_0"),
    ("axes_0.copy_ab12cd35", "axes_0.ylabel", "axes_0"),
    ("axes_0.copy_ab12cd36", "axes_0.texts_0", "axes_0"),
    ("axes_0.copy_ab12cd37", "axes_0.texts_1", "axes_0"),
    ("fig.copy_ab12cd38", "fig.texts_0", "fig"),
]
NAMES = ["text", "color", "rotation", "rotation_mode", "horizontalalignment", "verticalalignment", "zorder",
         "wrap", "parse_math", "label", "alpha", "visible", "in_layout", "clip_on", "linespacing", "fontsize",
         "fontweight", "fontstyle", "fontfamily", "antialiased", "usetex", "position",
         "math_fontfamily", "snap", "rasterized", "sketch_params", "animated", "url"]


def dump(t, host, fig):
    out = {n: repr(getattr(t, "get_" + n)()) for n in NAMES}
    simple = (str, int, float, bool, type(None))
    # 登记留下的痕迹（gid / picker / 私有标记）不是样式；其余凡是标量属性一律逐项比，引擎以后补抄一项而脚本没跟，这里就红
    out["props"] = {k: repr(v) for k, v in t.properties().items()
                    if isinstance(v, simple) and k not in ("gid", "picker")}
    out["font"] = str(t.get_fontproperties())
    out["multialignment"] = repr(t._multialignment)
    out["effects"] = [type(e).__name__ + repr(sorted((k, repr(v)) for k, v in vars(e).items())) for e in t.get_path_effects()]
    bp = t.get_bbox_patch()
    out["bbox"] = None if bp is None else {
        "style": type(bp.get_boxstyle()).__name__ + repr(vars(bp.get_boxstyle())),
        "fc": repr(bp.get_facecolor()), "ec": repr(bp.get_edgecolor()), "lw": bp.get_linewidth(),
        "ls": repr(bp.get_linestyle()), "alpha": bp.get_alpha()}
    out["transform_is_host"] = bool(t.get_transform() == (fig.transFigure if host is fig else host.transAxes))
    out["parent"] = "fig.artists" if t in fig.artists else ("ax.texts" if t in host.texts else "?")
    out["clip_path"] = t.get_clip_path() is None
    out["mm_copy"] = bool(getattr(t, "_mm_copy", None))
    return out


def engine_side(case):
    gid, src, host_gid = case
    fig, ax = figure()
    state = overrides.FigState(fig)
    manifest.instrument(state)
    w = overrides.apply(state, [{"gid": gid, "prop": "copy_of", "value": {"src": src, "text": "Copy", "at": [0.4, 0.55]}}])
    assert not w, w
    t = state.index[gid]
    fig.canvas.draw()
    return dump(t, fig if host_gid == "fig" else ax, fig), [round(v, 6) for v in t.get_window_extent().extents]


def script_side(case):
    gid, src, host_gid = case
    fig, ax = figure()
    ns = {}
    exec(runtime, ns)
    host = fig if host_gid == "fig" else ax
    if src == "axes_0.title":
        s = ax.title
    elif src == "axes_0.ylabel":
        s = ns["_AxisLabel"](ax, "y")
    elif src.startswith("axes_0.texts_"):
        s = ax.texts[int(src.rsplit("_", 1)[1])]
    else:
        s = fig.texts[0]
    ns["_copy_of"](fig, gid, host, s, [0.4, 0.55], "Copy")
    t = ns["_copy_ref"](fig, gid)
    fig.canvas.draw()
    return dump(t, host, fig), [round(v, 6) for v in t.get_window_extent().extents]


res = {}
for case in CASES:
    e, s = engine_side(case), script_side(case)
    res[case[0]] = {"engine": e, "script": s}
print(json.dumps({
    "cases": res,
    "engine_consts": {"prop": overrides.COPY_PROP, "gid": overrides._COPY_GID.pattern,
                      "rank": [overrides._apply_rank("copy_of", None, "axes_0.copy_ab12cd34"),
                               overrides._apply_rank("fontsize", None, "axes_0.title"),
                               overrides._apply_rank("xscale", plt.figure().add_subplot(), "axes_0")]},
    "supported": {s: [overrides._copy_source_supported(h, s) for h in ("axes_0", "fig")]
                  for s in ["axes_0.title", "axes_0.title_left", "axes_0.title_right", "axes_0.xlabel", "axes_0.ylabel",
                            "axes_0.texts_3", "axes_1.title", "axes_0.legend.texts_0", "axes_0.xticklabels_0",
                            "axes_0.lines_0", "fig.texts_2", "fig.texts_x", "axes_0.texts_"]},
}))
"""


@pytest.fixture(scope="module")
def parity(tmp_path_factory):
    if WORKER_PY is None:
        pytest.skip("找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）")
    d = tmp_path_factory.mktemp("copy-parity")
    runtime = d / "runtime.py"
    runtime.write_text(scriptadjust._needed("_copy_of _copy_ref _AxisLabel"), encoding="utf-8")
    engine_dir = Path(scriptadjust.__file__).resolve().parent
    out = subprocess.run(  # noqa: S603 — 测试里起 worker 解释器跑探针
        [WORKER_PY, "-c", _DRIVER, str(engine_dir), str(runtime)],
        capture_output=True,
        encoding="utf-8",
        timeout=180,
    )
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


_CASE_IDS = ["title", "ylabel", "text-boxed", "annotate", "figtext"]


@real_worker
@pytest.mark.parametrize("k", range(5), ids=_CASE_IDS)
def test_script_copy_equals_the_engines_copy_property_by_property(parity, k):
    """同源对：`scriptadjust._copy_of`（脚本里的）↔ `overrides._ensure_copy`（引擎里的）。

    两边各自从同一张图建副本，逐项比字体 / 颜色 / 对齐 / 转角 / 转角模式 / 层次 / 折行 / 数学解析 / 描边 / 底框 /
    变换 / 布局归属 / 裁剪，再比画出来的外框。任何一侧多抄或少抄一项（`update_from` 管不到的那几项）这里就红。"""
    cases = list(parity["cases"].values())
    (engine, ext_e), (script, ext_s) = cases[k]["engine"], cases[k]["script"]
    engine = {**engine, "mm_copy": None}  # 私有标记只有引擎的副本带，不算样式
    script = {**script, "mm_copy": None}
    assert engine == script
    assert ext_e == ext_s


@real_worker
def test_the_closed_sets_and_names_agree_with_the_engine(parity):
    c = parity["engine_consts"]
    assert c["prop"] == scriptadjust.COPY_PROP
    assert c["gid"] == scriptadjust._COPY_GID.pattern
    # 第一期源的闭集：脚本侧（宿主前缀 + `_COPY_SRC_AX`）与引擎 `_copy_source_supported` 逐个对
    import re

    for src, (ax_ok, fig_ok) in parity["supported"].items():
        mine_ax = re.fullmatch(re.escape("axes_0") + scriptadjust._COPY_SRC_AX, src) is not None
        mine_fig = re.fullmatch(r"fig\.texts_\d+", src) is not None
        assert (mine_ax, mine_fig) == (ax_ok, fig_ok), src


@real_worker
def test_copy_rank_agrees_with_the_engine(parity):
    """引擎：几何组之后、其余之前（`_RANK_COPY`）；脚本：先于全部属性语句。脚本写得进的几何组键只有 `xscale`，
    它与落在 transAxes / transFigure 上的副本无关，所以「副本先于每一条写进脚本的属性语句」两边等价。"""
    copy_rank, rest_rank, scale_rank = parity["engine_consts"]["rank"]
    assert scale_rank < copy_rank < rest_rank
    assert scriptadjust._rank("copy_of", "copy") < scriptadjust._rank("fontsize", None)


# ---------------------------------------------------------------------- 端到端
SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    kw = {"layout": LAYOUT} if LAYOUT else {}
    fig, ax = plt.subplots(figsize=(4, 3), **kw)
    ax.plot([0, 1], [0, 1], label="line")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_title("Title")
    ax.set_ylabel("Signal (a.u.)")
    ax.text(0.2, 0.8, "data text", fontsize=9, color="C3")
    ax.annotate("note", xy=(0.5, 0.5), xytext=(0.6, 0.2), arrowprops=dict(arrowstyle="->"))
    ax.text(0.7, 0.9, "boxed", bbox=dict(boxstyle="round", fc="w", ec="k"))
    ax.legend()
    fig.text(0.5, 0.02, "fig text")
    fig.savefig("Dup.pdf"SAVE)
"""

SOURCES = {
    "title": ("axes_0", "axes_0.title", [0.5, 0.7]),
    "ylabel": ("axes_0", "axes_0.ylabel", [-0.14, 0.4]),
    "datatext": ("axes_0", "axes_0.texts_0", [0.3, 0.6]),
    "annotate": ("axes_0", "axes_0.texts_1", [0.7, 0.3]),
    "boxed": ("axes_0", "axes_0.texts_2", [0.5, 0.5]),
    "figtext": ("fig", "fig.texts_0", [0.5, 0.1]),
}
STEM = "Dup"
NAME = "dup.py"


def _worker(tmp_path: Path, layout, save=""):
    (tmp_path / NAME).write_text(
        SCRIPT.replace("LAYOUT", repr(layout)).replace("SAVE", save), encoding="utf-8"
    )
    w = pool.one_shot(NAME, str(tmp_path), "main")
    w.ensure_built()
    return w


def _copy_patches(src_key: str, *, cid: str = CID) -> tuple[list[dict], str]:
    host, src, at = SOURCES[src_key]
    gid = f"{host}.copy_{cid}"
    return [
        _make(gid=gid, src=src, text=f"Copied {src_key}", at=at),
        {"gid": gid, "prop": "fontsize", "value": 15},
        {"gid": gid, "prop": "color", "value": "#cc2200"},
    ], gid


@real_worker
@pytest.mark.parametrize("src_key", list(SOURCES))
def test_edited_script_with_a_copy_passes_the_replay_gate(tmp_path, src_key):
    """导出的 .py 带副本：新脚本 + 没写进去的修改，几何 + 像素与热态一致（`_verify_edited_script` 同一把尺）。"""
    worker = _worker(tmp_path, None)
    try:
        patches, _gid = _copy_patches(src_key)
        # 源自己的样式修改在最前面：副本取脚本原样，不取改过的。改的是副本自己没覆盖的属性（字重 / 斜体），
        # 创建语句要是排到它后面，副本就带着粗体 / 斜体，像素门会红
        src = SOURCES[src_key][1]
        patches = [
            {"gid": src, "prop": "weight", "value": "bold"},
            {"gid": src, "prop": "style", "value": "italic"},
            *patches,
        ]
        _replays_identically(worker, NAME, tmp_path, STEM, patches)
    finally:
        pool.discard(worker)


@real_worker
@pytest.mark.parametrize("layout", ["tight", "constrained"])
@pytest.mark.parametrize("key", ["title", "ylabel"])
def test_copy_under_layout_engines_replays_identically(tmp_path, layout, key):
    worker = _worker(tmp_path, layout)
    try:
        patches, _gid = _copy_patches(key)
        _replays_identically(worker, NAME, tmp_path, STEM, patches)
    finally:
        pool.discard(worker)


@real_worker
def test_dragged_copy_on_a_tight_cropped_figure_lands_where_the_editor_shows(tmp_path):
    """L-156：按图分数换算位置的导出逻辑，夹具带 `bbox_inches="tight"`，副本也要拖一次。"""
    worker = _worker(tmp_path, None, save=', bbox_inches="tight"')
    try:
        patches, gid = _copy_patches("title")
        patches.append({"gid": gid, "prop": "pos_frac", "value": [0.42, 0.18]})
        assert _replays_identically(worker, NAME, tmp_path, STEM, patches)
    finally:
        pool.discard(worker)


@real_worker
def test_a_copy_held_for_replay_still_replays_identically(tmp_path):
    """副本上有一条写不进去的（改图幅之后的拖动留作重放）→ 整件事留作重放，新脚本里没有副本、由引擎重放建出来。"""
    worker = _worker(tmp_path, None)
    try:
        patches, gid = _copy_patches("title")
        patches = [
            {"gid": "axes_0", "prop": "facecolor", "value": "#f4f4f4"},  # 无关、写得进
            {
                "gid": "figure",
                "prop": "size_mm",
                "value": [120.0, 80.0],
            },  # 留作重放，副本的拖动也跟着留下
            *patches,
            {"gid": gid, "prop": "pos_frac", "value": [0.4, 0.2]},
        ]
        from tavotto import app

        assert not worker.override(STEM, patches).get("warnings")
        hot = app._hot_manifest(worker, STEM, patches)
        out = scriptadjust.build((tmp_path / NAME).read_bytes(), STEM, patches, hot, version="9.9")
        assert out.copies == [] and gid in {r["gid"] for r in out.residual}
        assert "_copy_of" not in out.data.decode()
        worker.override(STEM, [])
        assert _replays_identically(worker, NAME, tmp_path, STEM, patches)
    finally:
        pool.discard(worker)


@real_worker
def test_reopening_the_exported_script_shows_the_copy_as_an_ordinary_text_and_exports_again(
    tmp_path,
):
    """L-148：导出 -> 重开 -> 再编辑 -> 再导出。导出版里的副本是脚本自己的 `ax.text`，重开后是普通文字（序号名），
    能再改、再导出，第二轮新脚本重放同样与热态一致。"""
    from tavotto import app

    first = tmp_path / "first"
    first.mkdir()
    worker = _worker(first, None)
    try:
        patches, _gid = _copy_patches("title")
        assert not worker.override(STEM, patches).get("warnings")
        hot = app._hot_manifest(worker, STEM, patches)
        out = scriptadjust.build((first / NAME).read_bytes(), STEM, patches, hot, version="9.9")
        assert out.copies and not out.residual
    finally:
        pool.discard(worker)
    second = tmp_path / "second"
    second.mkdir()
    (second / NAME).write_bytes(out.data)
    worker = pool.one_shot(NAME, str(second), "main")
    try:
        worker.ensure_built()
        els = worker.override(STEM, [])["manifest"]["elements"]
        copied = [e for e in els if any(f.get("value") == "Copied title" for f in e["editable"])]
        assert len(copied) == 1 and ".copy_" not in copied[0]["gid"], [e["gid"] for e in els]
        again = [{"gid": copied[0]["gid"], "prop": "fontsize", "value": 19}]
        _replays_identically(worker, NAME, second, STEM, again)
    finally:
        pool.discard(worker)


@real_worker
def test_exported_script_runs_without_tavotto_and_draws_the_copy(tmp_path):
    """用户拿到的那个文件：纯 matplotlib 子进程跑它，PDF 里有副本的字（改过字号之后），原标题仍在。"""
    from tavotto import app

    worker = _worker(tmp_path, None)
    try:
        patches, _gid = _copy_patches("title")
        assert not worker.override(STEM, patches).get("warnings")
        hot = app._hot_manifest(worker, STEM, patches)
        out = scriptadjust.build((tmp_path / NAME).read_bytes(), STEM, patches, hot, version="9.9")
        assert out.written == 3 and not out.residual and out.copies
        run = tmp_path / "run"
        run.mkdir()
        edited = run / "edited.py"
        edited.write_bytes(out.data + b"\nmain()\n")
        done = subprocess.run(  # noqa: S603
            [WORKER_PY, "-I", str(edited)],
            cwd=run,
            capture_output=True,
            encoding="utf-8",
            timeout=120,
        )
        assert done.returncode == 0, done.stderr[-2000:]
        with pymupdf.open(run / "Dup.pdf") as doc:
            text = doc[0].get_text()
        assert "Copied title" in text and "Title" in text
    finally:
        pool.discard(worker)
