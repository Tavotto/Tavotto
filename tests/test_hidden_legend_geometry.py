"""隐藏图例的文字几何只取决于当前状态，不取决于上一次是谁、在什么 dpi 下画的（#413）。

机制：图例文字的像素位置在 `Legend.draw → OffsetBox.draw → TextArea.set_offset` 里写死；
图例隐藏后 `Legend.draw` 直接返回，这组像素就冻结在上一次画它那回。`preview_png` /
`export` 在别的 dpi 上 savefig 恰好会画一回——之后再藏图例，manifest 量到的六个文字
bbox 就是那次 dpi 的坐标除以文档像素。修法在 `manifest._layout_undrawn_legends`：
draw 跳过的图例在一次性 renderer 上按文档 dpi 补排一次版再量。

判据：**同一份 override 列表下，经历过预览 / 导出的热 worker 与从没预览过的对照 worker
manifest 逐字相等**——对照就是「当前状态在文档 dpi 上的几何」这个契约的可观测形式。
本进程不 import matplotlib：一切经 `pool.one_shot()` 起的 worker。
"""

from __future__ import annotations

import pytest

from support.invariant_figures import ENTRY, LIBRARY, SCRIPT_NAME
from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

STEM = "InvMix"
HIDE = [{"gid": "axes_0.legend", "prop": "visible", "value": False}]
LEGEND_TEXT_GIDS = tuple(f"axes_0.legend.texts_{j}" for j in range(6))


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("hidden-legend-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


@pytest.fixture
def worker(library):
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    w.ensure_built()
    yield w
    pool.discard(w)


@pytest.fixture
def control(library):
    """对照：只做 `override([])` → `override(HIDE)`，中间没有任何别的 dpi 的 draw。"""
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    w.ensure_built()
    try:
        _apply(w, [])
        yield _apply(w, HIDE)
    finally:
        pool.discard(w)


def _apply(w, patches) -> dict:
    resp = w.override(STEM, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp["manifest"]


def _text_boxes(man: dict) -> dict[str, list[float]]:
    boxes = {e["gid"]: e["bbox"] for e in man["elements"] if e["gid"] in LEGEND_TEXT_GIDS}
    assert len(boxes) == len(LEGEND_TEXT_GIDS), sorted(boxes)  # 六个都还在元素表里
    return boxes


def test_preview_then_hide_reports_document_dpi_geometry(worker, control):
    """#413 的最小复现：预览（380 px）之后藏图例，文字 bbox 必须与从没预览过的对照相同。"""
    _apply(worker, [])
    worker.preview_png(STEM, [], 380, "before-hide")
    hot = _apply(worker, HIDE)
    assert _text_boxes(hot) == _text_boxes(control)
    assert hot == control  # 不只是那六个字段：整份 manifest 逐字相等


def test_preview_that_showed_the_legend_while_the_session_hides_it(worker, control):
    """补一次文档 dpi 的 draw 盖不住的那一格：会话里图例已隐藏，预览的历史版本里它可见。
    预览那次 draw 画了图例（别的 dpi），还原后的 draw 又跳过它——几何要仍是文档 dpi 的。"""
    _apply(worker, [])
    _apply(worker, HIDE)
    worker.preview_png(STEM, [], 380, "legend-visible-in-preview")
    hot = _apply(worker, HIDE)
    assert _text_boxes(hot) == _text_boxes(control)


def test_export_then_hide_reports_document_dpi_geometry(worker, control, tmp_path):
    """export 与 preview_png 同一条纪律，600 dpi 的那次 savefig 同样不许留在几何里。"""
    _apply(worker, [])
    worker.export(STEM, [], str(tmp_path / "out.pdf"), "pdf", 600)
    hot = _apply(worker, HIDE)
    assert _text_boxes(hot) == _text_boxes(control)


def test_render_png_then_hide_reports_document_dpi_geometry(worker, control):
    """第三条别的 dpi 的 draw：imshow 类面板的 `render_png`（按目标像素宽出位图）。图例可见时
    出一张 900 px 的，再藏——藏着的时候出图不会碰图例，所以要在可见时出才量得到。"""
    _apply(worker, [])
    worker.render_png(STEM, 900)
    hot = _apply(worker, HIDE)
    assert _text_boxes(hot) == _text_boxes(control)


def test_visible_legend_geometry_is_unchanged_by_the_fix(worker, library):
    """可见图例走的还是 draw 那条路：预览前后 manifest 逐字相等（修法只碰 draw 跳过的图例）。"""
    before = _apply(worker, [])
    worker.preview_png(STEM, [], 380, "visible")
    after = _apply(worker, [])
    assert before == after


def test_hidden_geometry_is_where_the_legend_would_be_if_shown(worker):
    """契约的另一半：隐藏图例文字的 bbox 就是它显示时的 bbox（同一状态、同一 dpi）——
    「藏起来」只改可见性，不改「它在哪」。"""
    shown = _text_boxes(_apply(worker, []))
    hidden = _text_boxes(_apply(worker, HIDE))
    assert hidden == shown


HIDE_AXES = [{"gid": "axes_0", "prop": "visible", "value": False}]


def test_legend_inside_a_hidden_axes_is_laid_out_too(worker, library):
    """axes 不可见时 `Axes.draw` 整个返回，图例同样没被画——「draw 会跳过的图例」不只有
    自己隐藏这一种。对照是从没预览过、只藏 axes 的 worker。"""
    ctrl = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    ctrl.ensure_built()
    try:
        _apply(ctrl, [])
        control = _apply(ctrl, HIDE_AXES)
    finally:
        pool.discard(ctrl)
    _apply(worker, [])
    worker.preview_png(STEM, [], 380, "before-hiding-axes")
    hot = _apply(worker, HIDE_AXES)
    assert _text_boxes(hot) == _text_boxes(control)
