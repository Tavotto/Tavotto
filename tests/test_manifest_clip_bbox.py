"""manifest 的**裁剪框**（`clip_bbox`）：预检「元素超出图幅」的第二个维度。

`bbox` 回答「这个元素的内容到哪儿」，对曲线 / 散点 / 填充这类元素那是**未裁剪
的整个数据范围**——`xlim=(0, 1)` 配一个 x=1e3 的离群点，包围盒会被撑到图幅的
几百倍宽。matplotlib 在 axes patch 处就把它切掉了，图幅边界处一点内容都没丢，
可 `element-outside-figure` 照样报出几万毫米的**阻断级**问题（评审 P1）。

`clip_bbox` 就是补上的那一维：matplotlib 真会在哪儿把它切掉。钉四件事：

1. **两个维度缺一不可** —— `clip_on` 与「有没有裁剪框」是两回事，只看一个都会
   判错（`Axes.text()` 是 clip_on=False 却带着子图框；标题 / 图例 / 刻度是
   clip_on=True 却一个框都没有）；
2. **坐标约定与 bbox 同一套** —— figure 分数、y 向下；
3. **不裁的元素不发这个键** —— 缺席就是「不裁」，消费者据此退回原判据；
4. **端到端** —— 同一份 manifest 交给预检，离群散点不报、图幅外的标注照旧报。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

import pytest

from tavotto.engine import pool, preflight, profiles

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_clip.py"
ENTRY = "main"
STEM = "ClipFig"

# figsize 4×3 in / 100 dpi = 101.6 × 76.2 mm，默认边距下子图 display [50,33]–[360,264]
LIBRARY = """\
import matplotlib.pyplot as plt
from matplotlib.patches import Circle


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0), dpi=100)

    # scatter_0 / lines_0：数据远超显式 xlim —— 未裁剪的 bbox 会撑到图幅几百倍宽，
    # 而 matplotlib 在 axes patch 处切掉，图幅边界处什么都没丢
    ax.scatter([0.1, 0.2, 0.5, 1e3], [0.1, 0.2, 0.5, 0.4], label="pts")
    ax.plot([0.1, 0.2, 1e3], [0.2, 0.3, 0.9], label="raw")
    # lines_1：显式关掉裁剪 —— clip_box 还在（子图框），但它真的会画到框外
    ax.plot([0.1, 1e3], [0.5, 0.6], clip_on=False)
    # lines_2：非矩形裁剪 —— clip_path 留着一条真 path，取它的包围盒
    (clipped_by_circle,) = ax.plot([0.1, 1e3], [0.4, 0.7])
    clipped_by_circle.set_clip_path(Circle((0.5, 0.5), 0.25, transform=ax.transData))
    # lines_3：裁到**整幅图** —— 等于什么都没裁掉，不该发这个键
    (clipped_by_figure,) = ax.plot([0.1, 1e3], [0.3, 0.35])
    clipped_by_figure.set_clip_box(fig.bbox)

    # texts_0：Axes.text() 默认 clip_on=False —— 它真的画到图幅上方去了
    ax.text(0.5, 1.6, "outside", transform=ax.transAxes)

    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(0.0, 1.0)
    ax.set_title("title")
    ax.set_xlabel("x")
    ax.legend()
    fig.savefig("ClipFig.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("clip-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


@pytest.fixture(scope="module")
def manifest(library):
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    try:
        w.ensure_built()
        resp = w.override(STEM, [])
        assert not resp.get("warnings"), resp["warnings"]
        return resp["manifest"]
    finally:
        pool.discard(w)


def _el(man, gid):
    return next(e for e in man["elements"] if e["gid"] == gid)


def _axes_rect(man):
    return _el(man, "axes_0")["bbox"]


def test_clipped_artists_carry_the_axes_rect_in_bbox_coordinates(manifest):
    """裁到子图里的元素带 `clip_bbox`，值就是子图框，坐标与 bbox 同一套。"""
    axes = _axes_rect(manifest)
    for gid in ("axes_0.scatter_0", "axes_0.lines_0"):
        el = _el(manifest, gid)
        clip = el.get("clip_bbox")
        assert clip is not None, f"{gid} 没带裁剪框——预检会把离群数据判成超出图幅"
        assert clip == pytest.approx(axes, abs=2e-3), f"{gid} 的裁剪框不是子图框"
        # 前提：不裁的话它真的会被判成超出图幅（否则下面的「不报」什么都没证明）
        assert el["bbox"][0] + el["bbox"][2] > 100.0, "夹具没造出离群点"


def test_clip_on_false_gets_no_clip_bbox_even_though_the_box_is_still_there(manifest):
    """**两个维度缺一不可。** `clip_on=False` 的 artist 身上 `get_clip_box()` 照样
    是子图框（`Axes.text()` 默认就是这个组合）——只看框会把真画到图幅外的标注
    当成被裁住了。这两条就是那个维度本身。"""
    assert "clip_bbox" not in _el(manifest, "axes_0.lines_1"), "clip_on=False 被当成裁住了"
    assert "clip_bbox" not in _el(manifest, "axes_0.texts_0"), "clip_on=False 被当成裁住了"


def test_artists_with_no_clip_box_get_no_clip_bbox(manifest):
    """反过来：标题 / 轴标题 / 图例 / 刻度 / 子图自己 `clip_on` 都是 True，框却是
    None（`Artist._clipon` 默认 True，`_clipbox` 默认 None）。只看开关会把它们
    当成裁进了子图里，于是**真的探出图幅的轴标题不再报**——那正是这条规则的本行。"""
    for gid in ("axes_0", "axes_0.title", "axes_0.xlabel", "axes_0.legend", "axes_0.xticks"):
        assert "clip_bbox" not in _el(manifest, gid), f"{gid} 凭空多了裁剪框"


def test_a_clip_box_that_covers_the_whole_figure_is_not_reported(manifest):
    """裁到**整幅图** = 什么都没裁掉。发出去只是噪音，而且会让「不裁」这个语义
    多出一种写法——消费者从此得判两遍。"""
    assert "clip_bbox" not in _el(manifest, "axes_0.lines_3")


def test_non_rectangular_clip_path_falls_back_to_its_bounding_box(manifest):
    """圆形裁剪：matplotlib 留下一条真 clip_path，取它的**包围盒**（保守方向，
    框大于真实可见区，宁可多报也不漏报）。半径 0.25 的圆在 4×3 in 的图上占
    x[127.5, 282.5] / y[90.8, 206.2] display px。"""
    clip = _el(manifest, "axes_0.lines_2").get("clip_bbox")
    assert clip is not None
    x, y, w, h = clip
    assert (x, w) == pytest.approx((127.5 / 400.0, 155.0 / 400.0), abs=2e-3)
    assert (y, h) == pytest.approx((1.0 - 206.2 / 300.0, 115.4 / 300.0), abs=2e-3)
    axes = _axes_rect(manifest)
    assert w < axes[2] and h < axes[3], "圆的包围盒必须小于子图框，否则这条什么都没量"


def test_preflight_stops_blaming_axes_clipped_outliers_but_still_reports_real_overflow(manifest):
    """端到端：同一份真 manifest 交给预检——离群散点 / 曲线不再报，
    `clip_on=False` 的元素照旧报，而且是阻断级。

    末尾把 `clip_bbox` 一个键摘掉再跑一遍：同一份输入，那两条必须回来。
    否则「不报」也可能是这条规则整个不响了。
    """
    profile = profiles.load("lab-publication-v1")

    def _reported(man):
        issues = preflight.run(preflight.spec_from_manifest(man), profile)
        hit = next((i for i in issues if i["id"] == "element-outside-figure"), None)
        if hit is None:
            return None, set()
        return hit["severity"], set(hit["gids"])

    severity, gids = _reported(manifest)
    assert severity == "error", "图幅外的元素必须是阻断级"
    assert not ({"axes_0.scatter_0", "axes_0.lines_0"} & gids), (
        f"被子图裁住的离群数据又被报成超出图幅了：{sorted(gids)}"
    )
    assert {"axes_0.texts_0", "axes_0.lines_1"} <= gids, (
        f"clip_on=False 的元素真会画到图幅外，必须报：{sorted(gids)}"
    )

    stripped = {
        **manifest,
        "elements": [
            {k: v for k, v in e.items() if k != "clip_bbox"} for e in manifest["elements"]
        ],
    }
    _, without = _reported(stripped)
    assert {"axes_0.scatter_0", "axes_0.lines_0"} <= without, (
        "摘掉 clip_bbox 之后那两条没回来——上面的「不报」不是这个键起的作用"
    )
