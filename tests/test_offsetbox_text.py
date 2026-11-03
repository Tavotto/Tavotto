"""框里的文字可编辑（用户反馈 2026-10-26）。

用户脚本用 `AnnotationBbox(HPacker([TextArea, TextArea]))` 在柱上标「98.8% (79/80)」：
右键只有「隐藏此元素 / 打开全部属性」，文字改不了——引擎只把外面那个框登记成通用对象，
`TextArea` 里的文字根本不在元素表里。`AnchoredText` 同理。

这里用真 worker 钉住：

* 框里的每段文字都是一个 `text` 元素，挂在框的 gid 下；不宣称可拖，也不开旋转 / 对齐 / 层级；
* 改文字、字号、颜色真的画出来，框按新文字现量（变宽）；
* 外框照旧可拖、撤销逐位回到基线。
"""

from __future__ import annotations

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_offsetbox_text.py"

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.offsetbox import AnchoredText, AnnotationBbox, HPacker, TextArea


def main():
    fig, ax = plt.subplots()
    ax.barh([0, 1], [80, 90])
    for y, v in ((0, 80), (1, 90)):
        main_ta = TextArea(f"{v:.1f}%", textprops=dict(color="white", fontsize=10.2, fontweight="bold"))
        sub_ta = TextArea(f"({v}/100)", textprops=dict(color="white", fontsize=7.8))
        packed = HPacker(children=[main_ta, sub_ta], align="baseline", pad=0, sep=3.5)
        ax.add_artist(AnnotationBbox(packed, (v - 2, y), box_alignment=(1.0, 0.5), frameon=False, pad=0))
    ax.add_artist(AnchoredText("(a)", loc="upper left"))
    fig.savefig("Box.pdf")
    plt.close(fig)

    # 同一类问题的其他形状：图级的框、DrawingArea 里直接放的 Text、比例尺的标签
    from matplotlib.text import Text
    from mpl_toolkits.axes_grid1.anchored_artists import AnchoredDrawingArea, AnchoredSizeBar

    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    fig.add_artist(AnnotationBbox(TextArea("fig note"), (0.5, 0.95), xycoords="figure fraction"))
    fig.add_artist(AnchoredText("(b)", loc="lower right", bbox_to_anchor=(1, 0), bbox_transform=fig.transFigure))
    ada = AnchoredDrawingArea(40, 20, 0, 0, loc="lower left", frameon=False)
    ada.da.add_artist(Text(2, 2, "in da", fontsize=9))
    ax.add_artist(ada)
    ax.add_artist(AnchoredSizeBar(ax.transData, 0.2, "0.2 s", loc="lower right"))
    fig.savefig("More.pdf")
    plt.close(fig)
"""

BOX = "axes_0.artists_0"
MAIN = f"{BOX}.texts_0"
SUB = f"{BOX}.texts_1"
ANCHORED_TEXT = "axes_0.artists_2.texts_0"


@pytest.fixture(scope="module")
def hot(tmp_path_factory):
    d = tmp_path_factory.mktemp("offsetbox-text")
    (d / SCRIPT_NAME).write_text(SCRIPT, encoding="utf-8")
    w = pool.one_shot(SCRIPT_NAME, str(d), "main")
    w.ensure_built()
    try:
        yield w
    finally:
        pool.discard(w)


def _man(worker, patches=()):
    resp = worker.override("Box", list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return {e["gid"]: e for e in resp["manifest"]["elements"]}


def _field(el, prop):
    return next(f["value"] for f in el["editable"] if f["prop"] == prop)


def _props(el):
    return {f["prop"] for f in el["editable"]}


def test_texts_inside_boxes_are_editable_text_elements(hot):
    man = _man(hot)
    for gid, want in ((MAIN, "80.0%"), (SUB, "(80/100)"), (ANCHORED_TEXT, "(a)")):
        el = man[gid]
        assert el["role"] == "text", gid
        assert _field(el, "text") == want
        assert {"text", "fontsize", "color", "weight", "fontfamily"} <= _props(el), gid
        # 落位归打包器：不可拖，旋转 / 对齐 / 层级不开放；按住它拖起来时前端改拖外框（`drag_host`）
        assert el["draggable"] is False, gid
        assert el["drag_host"] == gid.rpartition(".")[0], gid
        assert not ({"rotation", "ha", "va", "zorder"} & _props(el)), gid
    assert _field(man[MAIN], "fontsize") == pytest.approx(10.2)
    assert _field(man[SUB], "fontsize") == pytest.approx(7.8)


def test_editing_a_text_inside_the_box_is_drawn_and_the_box_remeasures(hot):
    base = _man(hot)
    patches = [
        {"gid": MAIN, "prop": "text", "value": "98.8%"},
        {"gid": SUB, "prop": "text", "value": "(79/80) checked"},
        {"gid": SUB, "prop": "fontsize", "value": 9.0},
        {"gid": SUB, "prop": "color", "value": "#ff0000"},
    ]
    got = _man(hot, patches)
    assert _field(got[MAIN], "text") == "98.8%"
    assert _field(got[SUB], "text") == "(79/80) checked"
    assert _field(got[SUB], "fontsize") == pytest.approx(9.0)
    assert _field(got[SUB], "color") == "#ff0000"
    # 框按新文字现量：更长、更大的字让框变宽（bbox = [x, y, w, h]，figure 分数）
    assert got[BOX]["bbox"][2] > base[BOX]["bbox"][2] + 1e-3
    assert got[SUB]["bbox"][2] > base[SUB]["bbox"][2] + 1e-3
    # 另一个框一个字都没动
    assert _field(got["axes_0.artists_1.texts_0"], "text") == "90.0%"
    # 撤销：逐位回到基线
    back = _man(hot)
    assert _field(back[SUB], "text") == "(80/100)"
    assert back[BOX]["bbox"] == pytest.approx(base[BOX]["bbox"], abs=1e-9)


def test_the_box_itself_still_drags_with_its_texts(hot):
    base = _man(hot)
    el = base[BOX]
    assert el["draggable"] is True and el["drag_prop"] == "pos_frac", el
    target = [el["anchor"][0] - 0.05, el["anchor"][1] + 0.02]
    got = _man(
        hot,
        [
            {"gid": BOX, "prop": "pos_frac", "value": target},
            {"gid": MAIN, "prop": "text", "value": "X"},
        ],
    )
    assert got[BOX]["anchor"] == pytest.approx(target, abs=1e-6)
    assert _field(got[MAIN], "text") == "X"
    # 文字跟着框走：框左移 0.05，框里的字也左移
    assert got[MAIN]["bbox"][0] < base[MAIN]["bbox"][0] - 0.01


def test_hiding_the_box_drops_its_texts_from_the_manifest_and_undo_brings_them_back(hot):
    got = _man(
        hot,
        [
            {"gid": BOX, "prop": "visible", "value": False},
            {"gid": MAIN, "prop": "text", "value": "hidden edit"},
        ],
    )
    # 看不见的字不该可点、不该被预检量；外框仍在元素树里可以取消隐藏
    assert MAIN not in got and SUB not in got
    assert _field(got[BOX], "visible") is False
    # 另一个框的文字不受影响
    assert "axes_0.artists_1.texts_0" in got
    back = _man(hot)
    assert _field(back[MAIN], "text") == "80.0%"
    assert _field(back[SUB], "text") == "(80/100)"


def _more(worker, patches=()):
    resp = worker.override("More", list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return {e["gid"]: e for e in resp["manifest"]["elements"]}


#: `More` 图里的框：图级两个（`fig.artists`），轴上的 DrawingArea 与比例尺
FIG_AB = "fig.artists_0"
FIG_ANCHORED = "fig.artists_1"
DRAWING_AREA = "axes_0.artists_0"
SIZE_BAR = "axes_0.artists_1"


def test_other_box_shapes_expose_their_texts_too(hot):
    """图级的框（`fig.add_artist`）、`DrawingArea` 里直接放的 `Text`、比例尺的标签：同一类「框里的字改不了」。"""
    man = _more(hot)
    for box, want in (
        (FIG_AB, "fig note"),
        (FIG_ANCHORED, "(b)"),
        (DRAWING_AREA, "in da"),
        (SIZE_BAR, "0.2 s"),
    ):
        assert man[box]["role"] == "artist", box
        el = man[f"{box}.texts_0"]
        assert el["role"] == "text" and _field(el, "text") == want, box
        assert el["draggable"] is False and el["drag_host"] == box, box
        assert not ({"rotation", "ha", "va", "zorder"} & _props(el)), box
    # 图级的框和轴上的一样可拖（setter 按 figure 分数算）
    assert man[FIG_AB]["draggable"] is True and man[FIG_ANCHORED]["draggable"] is True
    got = _more(
        hot,
        [
            {"gid": f"{FIG_AB}.texts_0", "prop": "text", "value": "figure-level note"},
            {"gid": f"{DRAWING_AREA}.texts_0", "prop": "text", "value": "edited"},
            {"gid": f"{SIZE_BAR}.texts_0", "prop": "text", "value": "200 ms"},
        ],
    )
    assert _field(got[f"{FIG_AB}.texts_0"], "text") == "figure-level note"
    assert _field(got[f"{DRAWING_AREA}.texts_0"], "text") == "edited"
    assert _field(got[f"{SIZE_BAR}.texts_0"], "text") == "200 ms"
    assert got[FIG_AB]["bbox"][2] > man[FIG_AB]["bbox"][2] + 1e-3


def test_figure_level_box_drags_to_the_written_anchor(hot):
    el = _more(hot)[FIG_AB]
    target = [el["anchor"][0] + 0.03, el["anchor"][1] + 0.02]
    got = _more(hot, [{"gid": FIG_AB, "prop": "pos_frac", "value": target}])
    assert got[FIG_AB]["anchor"] == pytest.approx(target, abs=1e-6)
    back = _more(hot)
    assert back[FIG_AB]["anchor"] == pytest.approx(el["anchor"], abs=1e-9)
