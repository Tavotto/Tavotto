"""脚本 savefig 的裁切框就是这张图的图幅（ADR 0098）。

`savefig(bbox_inches="tight", pad_inches=0.02)` 的原件是 matplotlib 裁出来的那个框；以前
Tavotto 按 figsize 渲染，紧贴图幅的轴标题在画布、导出与写回里被切掉半截（审计 T14 / T33，
教程 Fig1_kinetics）。这里钉住：

* 每种 tight 形状，Tavotto 零 override 导出的 PDF 页面与脚本**自己**存出的原件同尺寸，
  manifest 的 `size_mm` 与它一致，没有元素伸出图幅；
* 没有 `bbox_inches` 的脚本仍按 figsize（逐字节不变由对拍装置在 PR 里证明，这里钉语义）；
* 分数是图幅里的分数：`pos_frac` 落在写下的位置、`axes.position` 按 manifest 报的值写回去
  一个像素都不动；`size_mm` 改的是图幅的尺寸；
* 热态 == 一次性全量重放（几何）；写回之后原件尺寸不变、轴标题完整；
* `paper_style.save` 捷径执行用户那份 `save`，参数看得见、stem 归属不变。

原件由同一个解释器按 `python fig.py` 真跑出来（本进程不 import matplotlib）；页面尺寸读 PDF
的 MediaBox（本进程的 pymupdf 只读）。
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import figcapture, pool, project_watch

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "src" / "tavotto" / "engine"

HEAD = "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"

#: 九种 tight 形状：stem 与脚本名相同，原件一律 PDF（透明那一种是 PNG，单测）
SHAPES = {
    "labels": """\
fig, ax = plt.subplots(figsize=(2.6, 1.9))
fig.subplots_adjust(left=0.08, right=0.99, bottom=0.08, top=0.97)
ax.plot([0, 1, 2, 3], [1, 3, 2, 4])
ax.set_xlabel("Elapsed time since injection (s)", fontsize=11)
ax.set_ylabel("Signal (a.u.)", fontsize=11)
ax.set_title("A title that overflows the figure", fontsize=12)
fig.savefig("labels.pdf", bbox_inches="tight", pad_inches=0.02)
""",
    "legend_out": """\
fig, ax = plt.subplots(figsize=(3.0, 2.2))
ax.plot([0, 1, 2], [0, 1, 4], label="quadratic growth")
ax.plot([0, 1, 2], [0, 1, 2], label="linear")
ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), borderaxespad=0)
fig.savefig("legend_out.pdf", bbox_inches="tight", pad_inches=0.05)
""",
    "colorbar": """\
fig, ax = plt.subplots(figsize=(3.0, 2.4))
im = ax.imshow([[i * 10 + j for j in range(10)] for i in range(10)])
fig.colorbar(im, ax=ax).set_label("Intensity (counts per second)")
fig.savefig("colorbar.pdf", bbox_inches="tight", pad_inches=0.1)
""",
    "constrained": """\
fig, axs = plt.subplots(1, 2, figsize=(4.0, 2.0), layout="constrained")
for i, ax in enumerate(axs):
    ax.plot([0, 1, 2], [i, 2, 1])
    ax.set_xlabel(f"Axis {i} label")
fig.suptitle("Constrained plus tight")
fig.savefig("constrained.pdf", bbox_inches="tight", pad_inches=0.05)
""",
    "pad_layout": """\
fig, ax = plt.subplots(figsize=(3.0, 2.0), layout="constrained")
ax.plot([0, 1, 2], [2, 0, 1])
ax.set_xlabel("pad_inches = layout")
fig.savefig("pad_layout.pdf", bbox_inches="tight", pad_inches="layout")
""",
    "explicit": """\
from matplotlib.transforms import Bbox
fig, ax = plt.subplots(figsize=(3.0, 2.0))
ax.plot([0, 1, 2], [0, 2, 1])
fig.savefig("explicit.pdf", bbox_inches=Bbox.from_extents(-0.2, -0.3, 2.8, 2.1))
""",
    "rc_tight": """\
matplotlib.rcParams["savefig.bbox"] = "tight"
matplotlib.rcParams["savefig.pad_inches"] = 0.03
fig, ax = plt.subplots(figsize=(2.5, 1.8))
fig.subplots_adjust(bottom=0.05)
ax.plot([1, 2, 3])
ax.set_xlabel("from rcParams")
fig.savefig("rc_tight.pdf")
""",
    "extra_artists": """\
fig, ax = plt.subplots(figsize=(3.0, 2.0))
ax.plot([0, 1, 2], [0, 1, 0])
note = fig.text(1.05, 0.5, "side note")
fig.savefig("extra_artists.pdf", bbox_inches="tight", pad_inches=0.02, bbox_extra_artists=[note])
""",
}

NOT_TIGHT = """\
fig, ax = plt.subplots(figsize=(3.0, 2.0))
fig.subplots_adjust(bottom=0.05)
ax.plot([1, 2, 3])
ax.set_xlabel("clipped on purpose")
fig.savefig("plain.pdf")
"""


def _mediabox_mm(path: Path) -> tuple[float, float]:
    m = re.search(
        rb"/MediaBox\s*\[\s*([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)", path.read_bytes()
    )
    assert m, f"{path} 里没有 MediaBox"
    x0, y0, x1, y1 = (float(v) for v in m.groups())
    return round((x1 - x0) / 72 * 25.4, 2), round((y1 - y0) / 72 * 25.4, 2)


def _project(tmp_path: Path, name: str, body: str) -> Path:
    """写脚本并按 `python fig.py` 真跑一遍，磁盘上留下脚本自己存的原件。"""
    figs = tmp_path / "figs"
    figs.mkdir(exist_ok=True)
    (figs / f"{name}.py").write_text(HEAD + body, encoding="utf-8")
    proc = subprocess.run(
        [WORKER_PY, f"{name}.py"],
        cwd=figs,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return figs


class _Session:
    def __init__(self, figs: Path, script: str):
        self.w = pool.one_shot(script, str(figs), "__main__")
        self.build = self.w.ensure_built()

    def manifest(self, stem: str, patches: list | None = None) -> dict:
        return self.w.override(stem, patches or [])["manifest"]

    def close(self):
        pool.discard(self.w)


@pytest.fixture
def session():
    made: list[_Session] = []

    def _open(figs: Path, script: str) -> _Session:
        s = _Session(figs, script)
        made.append(s)
        return s

    yield _open
    for s in made:
        s.close()


def _outside(man: dict) -> list[str]:
    out = []
    for el in man["elements"]:
        b = el.get("bbox")
        if b and (b[0] < -1e-3 or b[1] < -1e-3 or b[0] + b[2] > 1 + 1e-3 or b[1] + b[3] > 1 + 1e-3):
            out.append(el["gid"])
    return out


def _el(man: dict, gid: str) -> dict:
    return next(el for el in man["elements"] if el["gid"] == gid)


# ===========================================================================
# 图幅 = 原件的页面
# ===========================================================================
@pytest.mark.parametrize("name", sorted(SHAPES))
def test_export_page_matches_the_scripts_own_original(tmp_path, session, name):
    figs = _project(tmp_path, name, SHAPES[name])
    original = _mediabox_mm(figs / f"{name}.pdf")
    s = session(figs, f"{name}.py")
    man = s.manifest(name)
    assert man["size_mm"] == pytest.approx(original, abs=0.02)
    assert s.build["stems"][name]["size_mm"] == pytest.approx(original, abs=0.02)
    (desc,) = s.build["descriptors"]
    assert desc["size_mm"] == pytest.approx(original, abs=0.02), "描述符与 manifest 同一个尺寸"
    out = tmp_path / "export.pdf"
    s.w.export(name, [], str(out), fmt="pdf", dpi=600)
    assert _mediabox_mm(out) == pytest.approx(original, abs=0.02)
    assert _outside(man) == [], "原件里完整的元素在图幅里也必须完整"
    assert "frame_unavailable" not in man, "算得出图幅的图不报「算不出」"


def test_a_png_original_defines_the_frame_at_its_own_resolution(tmp_path, session):
    """定义图幅的是与原件同格式的那次调用：只存了透明 PNG 的图按 PNG 那次的框。"""
    figs = _project(
        tmp_path,
        "transparent",
        """\
fig, ax = plt.subplots(figsize=(2.8, 2.0))
ax.plot([0, 1, 2], [1, 0, 1])
ax.set_ylabel("Transparent background")
fig.savefig("transparent.png", bbox_inches="tight", pad_inches=0.2, transparent=True, dpi=200)
""",
    )
    from PIL import Image

    with Image.open(figs / "transparent.png") as im:
        original_px = im.size
    s = session(figs, "transparent.py")
    out = tmp_path / "export.png"
    s.w.export("transparent", [], str(out), fmt="png", dpi=200)
    with Image.open(out) as im:
        assert im.size == original_px


def test_previews_and_probes_are_cropped_to_the_frame(tmp_path, session):
    """画布上的位图（`render_png`，也是写回像素门的探针）、历史预览（`preview_png`）、预览 SVG
    都是图幅那一页：长宽比与 manifest 的 `size_mm` 一致。"""
    from PIL import Image

    figs = _project(tmp_path, "legend_out", SHAPES["legend_out"])
    s = session(figs, "legend_out.py")
    w_mm, h_mm = s.manifest("legend_out")["size_mm"]
    for path in (
        s.w.render_png("legend_out", 800),
        s.w.preview_png("legend_out", [], 800, "probe"),
    ):
        with Image.open(path) as im:
            assert im.size[0] == pytest.approx(800, abs=2)
            assert im.size[0] / im.size[1] == pytest.approx(w_mm / h_mm, rel=0.01)
    svg = s.w.svg_path("legend_out").read_text(encoding="utf-8")
    m = re.search(r'<svg[^>]*width="([\d.]+)pt"[^>]*height="([\d.]+)pt"', svg)
    assert m, "预览 SVG 的页面尺寸读不到"
    assert (float(m.group(1)) / 72 * 25.4, float(m.group(2)) / 72 * 25.4) == pytest.approx(
        (w_mm, h_mm), abs=0.05
    )


MANY_CALLS = """\
fig, ax = plt.subplots(figsize=(2.6, 1.9))
fig.subplots_adjust(left=0.08, right=0.99, bottom=0.08, top=0.97)
ax.plot([0, 1, 2, 3], [1, 3, 2, 4])
ax.set_xlabel("Elapsed time since injection (s)", fontsize=11)
for i in range(12):
    fig.savefig("many_calls.png", dpi=40)
fig.savefig("many_calls.pdf", bbox_inches="tight", pad_inches=0.02)
"""


def test_a_late_call_in_the_originals_format_still_defines_the_frame(tmp_path, session):
    """记账上限（`MAX_SAVEFIG_CALLS`）之后才第一次出现的格式照记：先在循环里存了十几张
    不裁的 png、最后才存 tight 的 pdf 原件，图幅仍按 pdf 那次（以前它被丢掉，退回第一次
    png 调用，按 figsize 出图、切边）。"""
    figs = _project(tmp_path, "many_calls", MANY_CALLS)
    original = _mediabox_mm(figs / "many_calls.pdf")
    s = session(figs, "many_calls.py")
    (desc,) = s.build["descriptors"]
    assert [c["format"] for c in desc["savefig_calls"]][-2:] == ["png", "pdf"]
    assert s.manifest("many_calls")["size_mm"] == pytest.approx(original, abs=0.02)


UNMEASURABLE = """\
from matplotlib.artist import Artist


class Unmeasurable(Artist):
    def draw(self, renderer):
        pass

    def get_tightbbox(self, renderer=None):
        raise ValueError("no extent")


fig, ax = plt.subplots(figsize=(2.6, 1.9))
ax.plot([0, 1, 2], [1, 3, 2])
fig.add_artist(Unmeasurable())
fig.savefig("unmeasurable.pdf", bbox_inches="tight")
"""


def test_a_frame_that_cannot_be_measured_is_reported(tmp_path, session):
    """脚本按 tight 存盘、图幅却算不出来（这里：用户的 artist 在 tight 量包围盒时抛）：按 figsize
    出图，manifest 如实报 `frame_unavailable`——不报的话与「脚本本来就不裁」分不开。
    （脚本不真跑：它自己的 savefig 也会抛；worker 拦截 savefig、不执行它。）"""
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "unmeasurable.py").write_text(HEAD + UNMEASURABLE, encoding="utf-8")
    s = session(figs, "unmeasurable.py")
    man = s.manifest("unmeasurable")
    assert man["size_mm"] == [66.04, 48.26]
    assert "frame" not in man
    assert man.get("frame_unavailable", "").startswith("ValueError"), man.get("frame_unavailable")


def test_a_script_without_bbox_inches_keeps_the_figsize(tmp_path, session):
    """绝大多数脚本：没有 frame，图幅就是 figsize，伸出去的照旧被切（并由预检说出来）。"""
    figs = _project(tmp_path, "plain", NOT_TIGHT)
    s = session(figs, "plain.py")
    man = s.manifest("plain")
    assert man["size_mm"] == [76.2, 50.8]
    assert "frame_unavailable" not in man, "本来就不裁的图不报「算不出」"
    assert _mediabox_mm(figs / "plain.pdf") == (76.2, 50.8)
    assert "axes_0.xlabel" in _outside(man), "前提：这张图的轴标题确实伸出了 figsize"


def test_the_frame_is_not_moved_by_edits(tmp_path, session):
    """编辑不移动图幅：把字号放大到伸出去，页面尺寸不变（ADR 0098 §二，冻结 F）。"""
    figs = _project(tmp_path, "labels", SHAPES["labels"])
    s = session(figs, "labels.py")
    before = s.manifest("labels")["size_mm"]
    after = s.manifest("labels", [{"gid": "axes_0.xlabel", "prop": "fontsize", "value": 24}])
    assert after["size_mm"] == before
    assert "axes_0.xlabel" in _outside(after)


# ===========================================================================
# 分数是图幅里的分数
# ===========================================================================
def test_pos_frac_lands_where_it_was_written(tmp_path, session):
    figs = _project(
        tmp_path,
        "note",
        """\
fig, ax = plt.subplots(figsize=(3.0, 2.0))
fig.subplots_adjust(bottom=0.02, left=0.02)
ax.plot([0, 1], [0, 1])
ax.set_xlabel("pushes the frame down")
ax.set_ylabel("pushes the frame left")
fig.text(0.5, 0.5, "note", ha="center", va="center")
fig.savefig("note.pdf", bbox_inches="tight", pad_inches=0.1)
""",
    )
    s = session(figs, "note.py")
    base = s.manifest("note")
    gid = next(
        el["gid"]
        for el in base["elements"]
        for f in el.get("editable", [])
        if f["prop"] == "text" and f["value"] == "note"
    )
    man = s.manifest("note", [{"gid": gid, "prop": "pos_frac", "value": [0.25, 0.75]}])
    x, y, w, h = _el(man, gid)["bbox"]
    assert (x + w / 2, y + h / 2) == pytest.approx((0.25, 0.75), abs=0.005)


def test_writing_back_the_reported_axes_position_moves_nothing(tmp_path, session):
    """manifest 报的 `axes.position` 是图幅里的分数；原样写回去一个像素都不许动。"""
    figs = _project(tmp_path, "labels", SHAPES["labels"])
    s = session(figs, "labels.py")
    base = s.manifest("labels")
    pos = next(f["value"] for f in _el(base, "axes_0")["editable"] if f["prop"] == "position")
    man = s.manifest("labels", [{"gid": "axes_0", "prop": "position", "value": pos}])
    assert _el(man, "axes_0")["bbox"] == pytest.approx(_el(base, "axes_0")["bbox"], abs=1e-4)


def test_size_mm_is_the_frame_size(tmp_path, session):
    figs = _project(tmp_path, "labels", SHAPES["labels"])
    s = session(figs, "labels.py")
    original = s.manifest("labels")["size_mm"]
    man = s.manifest("labels", [{"gid": "figure", "prop": "size_mm", "value": [90.0, 70.0]}])
    assert man["size_mm"] == pytest.approx([90.0, 70.0], abs=0.01)
    size_field = next(f for f in _el(man, "figure")["editable"] if f["prop"] == "size_mm")
    assert size_field["value"] == pytest.approx([90.0, 70.0], abs=0.1)
    out = tmp_path / "resized.pdf"
    s.w.export("labels", [{"gid": "figure", "prop": "size_mm", "value": [90.0, 70.0]}], str(out))
    assert _mediabox_mm(out) == pytest.approx((90.0, 70.0), abs=0.02)
    assert s.manifest("labels", [])["size_mm"] == original, "撤掉 override 回到脚本的图幅"


def test_hot_session_equals_a_fresh_replay(tmp_path, session):
    """增量应用（先拖、再改图幅、再挪子图）与一次性全量重放落到同一张图上。"""
    from tavotto import app as m

    figs = _project(tmp_path, "labels", SHAPES["labels"])
    hot = session(figs, "labels.py")
    patches = [
        {"gid": "axes_0.title", "prop": "pos_frac", "value": [0.4, 0.05]},
        {"gid": "figure", "prop": "size_mm", "value": [85.0, 66.0]},
        {"gid": "axes_0", "prop": "position", "value": [0.2, 0.25, 0.7, 0.6]},
    ]
    for i in range(1, len(patches) + 1):
        hot_man = hot.manifest("labels", patches[:i])
    fresh = session(figs, "labels.py").manifest("labels", patches)
    diffs, compared = m._compare_manifests(hot_man, fresh)
    assert compared > 0
    assert diffs == []


# ===========================================================================
# figure.frame：升级前的版面按 figsize（ADR 0098 §三，用户 2026-09-26 选 C）
# ===========================================================================
LEGACY = {"gid": "figure", "prop": "frame", "value": "figsize"}


def test_the_legacy_frame_override_renders_the_figsize_as_before(tmp_path, session):
    figs = _project(tmp_path, "labels", SHAPES["labels"])
    s = session(figs, "labels.py")
    tight = s.manifest("labels")
    assert tight["frame"]["active"] is True
    legacy = s.manifest("labels", [LEGACY])
    assert legacy["size_mm"] == [66.04, 48.26], "带着 figsize 那条 override：与升级前一样按 figsize"
    assert legacy["frame"]["active"] is False
    assert legacy["frame"]["figsize_mm"] == [66.04, 48.26]
    x, y, w, h = legacy["frame"]["savefig_mm"]
    assert (w, h) == pytest.approx(tight["size_mm"], abs=0.01)
    assert x < 0 and y < 0, "这张图的原件向左、向上都伸出了 figsize"
    out = tmp_path / "legacy.pdf"
    s.w.export("labels", [LEGACY], str(out))
    assert _mediabox_mm(out) == pytest.approx((66.04, 48.26), abs=0.02)


def test_a_figure_without_a_savefig_frame_reports_none(tmp_path, session):
    figs = _project(tmp_path, "plain", NOT_TIGHT)
    s = session(figs, "plain.py")
    assert "frame" not in s.manifest("plain"), "没有 frame 的图 manifest 一个键都不多"
    assert s.manifest("plain", [LEGACY])["size_mm"] == [76.2, 50.8]


def test_switching_the_frame_mid_session_equals_a_fresh_replay(tmp_path, session):
    """热会话里先带着 figsize 编辑、再切到脚本的图幅：与全新重放同一张图。"""
    from tavotto import app as m

    figs = _project(tmp_path, "labels", SHAPES["labels"])
    edits = [
        {"gid": "figure", "prop": "size_mm", "value": [80.0, 60.0]},
        {"gid": "axes_0", "prop": "position", "value": [0.2, 0.25, 0.7, 0.6]},
        {"gid": "axes_0.title", "prop": "pos_frac", "value": [0.4, 0.05]},
    ]
    hot = session(figs, "labels.py")
    hot.manifest("labels", [LEGACY, *edits])
    hot_man = hot.manifest("labels", edits)
    fresh = session(figs, "labels.py").manifest("labels", edits)
    diffs, compared = m._compare_manifests(hot_man, fresh)
    assert compared > 0
    assert diffs == []
    back = hot.manifest("labels", [LEGACY, *edits])
    again = session(figs, "labels.py").manifest("labels", [LEGACY, *edits])
    diffs, _ = m._compare_manifests(back, again)
    assert diffs == []


@pytest.mark.parametrize(
    "frame", ["tight", "explicit", "tight-layout", "constrained-layout", "figsize", "none"]
)
@pytest.mark.parametrize("history", ["add-change-undo-redo", "remove-on-open"])
def test_unchanged_axes_position_replays_after_frame_resize(tmp_path, frame, history):
    """Same frame-relative axes position, changed size: hot == full replay.

    The existing hot/fresh test adds position *after* size, so it never exercises
    a cached F→G conversion. Removing size must also re-evaluate that conversion:
    returning to the size where position was first applied can hide this bug.
    """
    from PIL import Image

    from tavotto import app as m, pdfbackend

    name = "resize"
    layout = frame.removesuffix("-layout") if frame.endswith("-layout") else None
    bbox = (
        "None"
        if frame == "none"
        else "Bbox.from_extents(-.2, -.3, 4.8, 3.4)"
        if frame == "explicit"
        else "'tight'"
    )
    # 5 × 3.2 inches round-trips exactly through the existing size_mm restore.
    # A 3-inch source has a separate baseline roundoff (3 → 2.9999999999999996),
    # which can move a tick glyph across Agg's pixel-snapping boundary on undo.
    body = f"""\
from matplotlib.transforms import Bbox
fig, ax = plt.subplots(figsize=(5.0, 3.2), layout={layout!r})
ax.plot([0, 1, 2], [2, 0, 3])
ax.set(xlabel="Time", ylabel="Response", title="Frame-relative axes")
fig.savefig("{name}.pdf", bbox_inches={bbox}, pad_inches=0.04)
"""
    figs = _project(tmp_path, name, body)
    original = {p.name: p.read_bytes() for p in figs.iterdir()}
    position = {"gid": "axes_0", "prop": "position", "value": [0.18, 0.22, 0.67, 0.64]}
    size = {"gid": "figure", "prop": "size_mm", "value": [140.0, 95.0]}
    size2 = {**size, "value": [160.0, 105.0]}
    steps = (
        [[position], [position, size], [position, size2], [position], [position, size]]
        if history == "add-change-undo-redo"
        else [[position, size], [position]]
    )
    prefix = [LEGACY] if frame == "figsize" else []
    hot = _Session(figs, f"{name}.py")
    try:
        for step, patches in enumerate(steps):
            patches = prefix + patches
            fresh = _Session(figs, f"{name}.py")
            try:
                hot_result = hot.w.override(name, patches)
                fresh_result = fresh.w.override(name, patches)
                assert hot_result["warnings"] == fresh_result["warnings"] == []
                hot_man, fresh_man = hot_result["manifest"], fresh_result["manifest"]
                if frame == "none":
                    assert "frame" not in hot_man
                else:
                    assert hot_man["frame"]["active"] is (frame != "figsize")
                hot_position = next(
                    field["value"]
                    for field in _el(hot_man, "axes_0")["editable"]
                    if field["prop"] == "position"
                )
                # Read the actual geometry, not state.applied or the request list.
                assert hot_position == pytest.approx(position["value"], abs=1e-9), step
                diffs, compared = m._compare_manifests(hot_man, fresh_man)
                assert compared > 0
                assert diffs == [], (step, diffs)
                for label, worker in (("hot", hot.w), ("fresh", fresh.w)):
                    worker.export(name, patches, str(tmp_path / f"{label}.png"), "png", 100)
                with Image.open(tmp_path / "hot.png") as h, Image.open(tmp_path / "fresh.png") as f:
                    assert h.size == f.size
                    assert h.convert("RGBA").tobytes() == f.convert("RGBA").tobytes(), step
                # The two-step primary regression also checks real PDF pixels.
                if frame == "tight" and history == "add-change-undo-redo" and step == 1:
                    for label, worker in (("hot", hot.w), ("fresh", fresh.w)):
                        pdf = tmp_path / f"{label}.pdf"
                        worker.export(name, patches, str(pdf), "pdf", 100)
                        assert _mediabox_mm(pdf) == pytest.approx(size["value"], abs=0.02)
                        pdfbackend.render_preview_png(pdf, 600, tmp_path / f"{label}-pdf.png")
                    with (
                        Image.open(tmp_path / "hot-pdf.png") as h,
                        Image.open(tmp_path / "fresh-pdf.png") as f,
                    ):
                        assert h.size == f.size
                        assert h.convert("RGBA").tobytes() == f.convert("RGBA").tobytes()
            finally:
                fresh.close()
    finally:
        hot.close()
    assert {p.name: p.read_bytes() for p in figs.iterdir()} == original


# ===========================================================================
# paper_style.save 捷径（ADR 0098 §四 = ADR 0094 §五.4 的前置修正）
# ===========================================================================
PAPER_STYLE = """\
import matplotlib.pyplot as plt
def save(fig, stem, outdir=None):
    fig.savefig(f"{stem}_final.pdf", bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return "saved"
"""
USES_PAPER_STYLE = """\
from paper_style import save
fig, ax = plt.subplots(figsize=(2.6, 1.9))
fig.subplots_adjust(bottom=0.05)
ax.plot([1, 2])
ax.set_xlabel("saved through paper_style")
print(save(fig, "Fig9"))
"""


def test_paper_style_save_runs_and_its_savefig_is_filed_under_the_stem(tmp_path, session):
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "paper_style.py").write_text(PAPER_STYLE, encoding="utf-8")
    figs = _project(tmp_path, "uses", USES_PAPER_STYLE)
    s = session(figs, "uses.py")
    assert list(s.build["stems"]) == ["Fig9"], "stem 按 save(fig, stem) 的 stem，不按文件名"
    (desc,) = s.build["descriptors"]
    (call,) = desc["savefig_calls"]
    assert call["bbox_inches"] == "tight" and call["pad_inches"] == 0.02
    original = _mediabox_mm(figs / "Fig9_final.pdf")
    assert s.manifest("Fig9")["size_mm"] == pytest.approx(original, abs=0.02)


def test_a_paper_style_without_a_callable_save_still_registers(tmp_path, session):
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "paper_style.py").write_text("save = None\n", encoding="utf-8")
    (figs / "uses.py").write_text(HEAD + USES_PAPER_STYLE, encoding="utf-8")
    s = session(figs, "uses.py")
    assert list(s.build["stems"]) == ["Fig9"]


# ===========================================================================
# 写回：原件尺寸不变、轴标题完整
# ===========================================================================
def test_write_back_keeps_the_original_page_and_the_axis_label(tmp_path, monkeypatch):
    import pymupdf

    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    figs = _project(tmp_path, "labels", SHAPES["labels"])
    (figs / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "labels.py": {
                        "entry": "__main__",
                        "cost": "light",
                        "notes": "",
                        "stems": ["labels"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    original = _mediabox_mm(figs / "labels.pdf")
    m.open_project(str(figs))
    client = m.app.test_client()
    try:
        patches = [{"gid": "axes_0.title", "prop": "text", "value": "Rewritten"}]
        r = client.post("/api/engine/render", json={"id": "labels.pdf", "patches": patches})
        assert r.status_code == 200, r.get_json()
        resp = client.post(
            "/api/engine/update_source",
            json={
                "id": "labels.pdf",
                "patches": patches,
                "expected_mtime": int((figs / "labels.pdf").stat().st_mtime),
            },
        )
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["verification"]["replay"] == "ok"
        assert body["verification"]["pixels"] == "ok"
        assert "post_check" not in body
        assert _mediabox_mm(figs / "labels.pdf") == pytest.approx(original, abs=0.02)
        with pymupdf.open(figs / "labels.pdf") as doc:
            page = doc[0]
            words = {w[4]: w for w in page.get_text("words")}
            assert "Rewritten" in words
            injection = words["injection"]
            assert injection[3] <= page.rect.height + 0.01, "轴标题整行都在页面里"
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()


def test_the_frame_is_defined_by_the_call_in_the_originals_format():
    """与原件同格式的那一次定义图幅（写回覆盖的正是那份文件）；没有原件取第一次。"""
    calls = [{"format": "png", "bbox_inches": None}, {"format": "pdf", "bbox_inches": "tight"}]
    assert figcapture.frame_call(calls, "Fig1.pdf") is calls[1]
    assert figcapture.frame_call(calls, "Fig1.png") is calls[0]
    assert figcapture.frame_call(calls, None) is calls[0]
    assert figcapture.frame_call([{"format": "tiff"}], "Fig1.tif") == {"format": "tiff"}
    assert figcapture.frame_call(None, "Fig1.pdf") is None
    assert figcapture.frame_call([], "Fig1.pdf") is None


# ===========================================================================
# 同源字面量
# ===========================================================================
def test_the_frame_attribute_is_one_literal_on_both_sides():
    """figcapture（纯标准库）读、pathgeom 写同一个属性名；两边各写一份字面量，这里钉住相等。"""
    tree = ast.parse((ENGINE / "pathgeom.py").read_text(encoding="utf-8"))
    literal = next(
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "FRAME_ATTR" for t in node.targets)
    )
    assert literal == figcapture.FRAME_ATTR
