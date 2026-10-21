"""worker 直出位图的像素预算（`figcapture.check_export_raster` / `figsession.do_export`）。

`do_export` → `Figure.savefig` 对位图格式（png / tiff / jpg…）一次分配整幅 Agg RGBA。RenderCore 最终导出的
条带栅格与预算是另一条路径（PDFium 条带，整页不落内存），**不能**证明这条路安全。合同：

1. 超预算 → worker 回结构化错误 `export_raster_too_large`（extra：`width` / `height` / `max_pixels`），
   **不分配那块内存、不创建目标目录、不写任何文件**；父进程 `pool` 转成带 code 的 `WorkerError`，
   `recovery_params` / `_worker_error_payload` 把三个整数交给界面；
2. 失败是状态中立的：热会话照常可用、patches 已还原；
3. 预算内 → 原尺寸（像素 = figsize×dpi，不降 dpi、不缩小）；矢量格式不受影响；
4. 判据的主语是**即将分配的栅格**：`bbox_inches="tight"` / frame 生效后的真实尺寸，由
   `figsession._export_raster_px` 量出，与 `savefig` 真正写出的像素逐一相等（含 tight 与 rcParam 边距）。

夹具是原创的：超预算用「figsize 小、dpi 极高」——检查发生在分配之前，所以用例本身不分配大内存。
matplotlib 只在科学栈子进程里（与真 worker 同）；本进程不 import 它。
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import figcapture, pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="找不到装有 matplotlib 的解释器")

ENGINE = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine"
SCRIPT_NAME = "fig_pixel_budget.py"
STEM = "Budget"
CODE = "export_raster_too_large"

#: figsize 3×2 英寸、轴占满：名义尺寸 = 3·dpi × 2·dpi。
LIBRARY = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    fig = plt.figure(figsize=(3, 2), dpi=100)
    ax = fig.add_axes([0.1, 0.1, 0.8, 0.8])
    ax.plot([0, 1, 2], [0, 1, 0], color="#1f77b4")
    ax.set_title("pixel budget")
    fig.savefig("Budget.png")
"""


@pytest.fixture
def hot(tmp_path):
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    w = pool.one_shot(SCRIPT_NAME, str(figs), "main")
    w.ensure_built()
    yield w
    pool.discard(w)


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _manifest_elements(worker) -> list:
    return worker.override(STEM, [])["manifest"]["elements"]


# --------------------------- 真 worker：超预算 / 预算内 -----------------------
def test_over_budget_png_is_refused_before_anything_is_written(hot, tmp_path):
    """150 M 像素（15000×10000）> 上限：结构化错误，目标目录都不创建，宽高上限原样带回。"""
    out = tmp_path / "never_created" / "big.png"
    with pytest.raises(pool.WorkerError) as err:
        hot.export(STEM, [], str(out), "png", 5000)
    exc = err.value
    assert exc.code == CODE
    assert exc.extra["width"] == 15000 and exc.extra["height"] == 10000
    assert exc.extra["max_pixels"] == figcapture.MAX_EXPORT_RASTER_PIXELS
    assert pool.recovery_params(exc) == {
        "width": 15000,
        "height": 10000,
        "max_pixels": figcapture.MAX_EXPORT_RASTER_PIXELS,
    }
    assert not out.parent.exists(), "超预算必须在 mkdir / 任何写入之前拒绝"
    assert not list(tmp_path.glob("**/*.png"))


def test_refusal_is_state_neutral_and_the_hot_session_keeps_working(hot, tmp_path):
    """带 patch 的超预算导出失败后：patch 已还原（manifest 不变）、会话没死、下一次导出照常。"""
    before = _manifest_elements(hot)
    title = next(e for e in before if e.get("role") == "title")
    patch = [{"gid": title["gid"], "prop": "text", "value": "changed"}]
    with pytest.raises(pool.WorkerError) as err:
        hot.export(STEM, patch, str(tmp_path / "big.png"), "png", 5000)
    assert err.value.code == CODE
    assert _manifest_elements(hot) == before, "失败的导出不得把它的 patches 留在热会话上"

    ok = tmp_path / "ok.png"
    hot.export(STEM, [], str(ok), "png", 100)
    assert _png_size(ok) == (300, 200)


def test_within_budget_keeps_the_exact_requested_size(hot, tmp_path):
    """预算内一律原尺寸：figsize×dpi 精确相等，不降 dpi、不缩小。"""
    for dpi in (100, 250, 600):
        out = tmp_path / f"d{dpi}.png"
        hot.export(STEM, [], str(out), "png", dpi)
        assert _png_size(out) == (3 * dpi, 2 * dpi)


def test_vector_formats_are_not_subject_to_the_raster_budget(hot, tmp_path):
    """pdf 不分配整幅栅格：同样 5000 dpi 的 PDF 照常交付（预算只管位图格式）。"""
    out = tmp_path / "v.pdf"
    hot.export(STEM, [], str(out), "pdf", 5000)
    assert out.read_bytes().startswith(b"%PDF")


def test_the_error_reaches_the_user_with_params(hot, tmp_path):
    """app 的错误响应体带 code 与 params（写回 verify 段用同一个漏斗，加 stage）。"""
    with pytest.raises(pool.WorkerError) as err:
        hot.export(STEM, [], str(tmp_path / "x.png"), "png", 5000)
    body = m._worker_error_payload(err.value, stage="verify")
    assert body["code"] == CODE and body["stage"] == "verify"
    assert body["params"] == {
        "width": 15000,
        "height": 10000,
        "max_pixels": figcapture.MAX_EXPORT_RASTER_PIXELS,
    }


# --------------------------- 判据的主语：真实栅格尺寸 ------------------------
PROBE = textwrap.dedent(
    """\
    import io, json, sys
    sys.path.insert(0, {engine!r})
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.transforms import Bbox
    import figcapture, figsession

    def png_size(fig, **kw):
        buf = io.BytesIO()
        fig.savefig(buf, format="png", **kw)
        data = buf.getvalue()
        return [int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")]

    def wide():
        fig = plt.figure(figsize=(2, 1.5), dpi=100)
        ax = fig.add_axes([0.2, 0.2, 0.5, 0.5])
        # 文字远伸出画布：tight 的真实尺寸远大于 figsize
        ax.text(1.4, 0.5, "a long annotation sticking out of the figure", transform=ax.transAxes)
        return fig

    out = {{}}
    for dpi in (72, 100, 150):
        plain = plt.figure(figsize=(3.3, 2.2), dpi=100)
        out[f"plain{{dpi}}"] = [list(figsession._export_raster_px(plain, dpi, {{}})), png_size(plain, dpi=dpi)]
        frame = Bbox.from_bounds(0.25, 0.5, 2.0, 1.1)
        out[f"frame{{dpi}}"] = [
            list(figsession._export_raster_px(plain, dpi, {{"bbox_inches": frame}})),
            png_size(plain, dpi=dpi, bbox_inches=frame),
        ]
        fig = wide()
        out[f"tight{{dpi}}"] = [
            list(figsession._export_raster_px(fig, dpi, {{"bbox_inches": "tight"}})),
            png_size(fig, dpi=dpi, bbox_inches="tight"),
        ]
        # 脚本把 tight 设成 rcParam（不经 kwargs）：do_export 没有显式 bbox_inches 时走这条
        with matplotlib.rc_context({{"savefig.bbox": "tight", "savefig.pad_inches": 0.3}}):
            out[f"rc{{dpi}}"] = [
                list(figsession._export_raster_px(fig, dpi, {{}})),
                png_size(fig, dpi=dpi),
            ]
    out["tight_exceeds_figsize"] = out["tight100"][0][0] > 200 and out["tight100"][0][1] > 0

    # 约束布局 + tight：get_tightbbox 自己先跑一遍布局引擎，量出来的要与写出来的一致
    con = plt.figure(figsize=(2, 1.5), dpi=100, layout="constrained")
    con.add_subplot().plot([0, 1])
    con.axes[0].set_xlabel("a rather long x label that widens the tight box")
    out["constrained_tight"] = [
        list(figsession._export_raster_px(con, 100, {{"bbox_inches": "tight"}})),
        png_size(con, dpi=100, bbox_inches="tight"),
    ]

    # 名义画布（figsize×dpi）：tight / 布局引擎要先 draw，那一遍就要分配它；frame 裁切且无布局引擎则不分配
    figcapture.MAX_EXPORT_RASTER_PIXELS = 50_000
    small = plt.figure(figsize=(3, 2), dpi=100)  # 名义 300×200 = 60000 > 50000
    small.add_axes([0.4, 0.4, 0.1, 0.1])
    try:
        figsession._check_export_raster_budget(small, "png", 100, {{"bbox_inches": "tight"}})
        out["tight_nominal"] = "ok"
    except figcapture.ExportRasterTooLarge as exc:
        out["tight_nominal"] = exc.params
    cropped = Bbox.from_bounds(0.5, 0.5, 1.0, 1.0)  # 最终 100×100，名义画布不会被分配
    figsession._check_export_raster_budget(small, "png", 100, {{"bbox_inches": cropped}})
    out["frame_ok"] = True
    laid = plt.figure(figsize=(3, 2), dpi=100, layout="constrained")
    try:
        figsession._check_export_raster_budget(laid, "png", 100, {{"bbox_inches": cropped}})
        out["layout_nominal"] = "ok"
    except figcapture.ExportRasterTooLarge as exc:
        out["layout_nominal"] = exc.params
    figcapture.MAX_EXPORT_RASTER_PIXELS = 1000

    # 预算在调用时读模块常量；矢量格式不检查
    figcapture.MAX_EXPORT_RASTER_PIXELS = 1000
    try:
        figcapture.check_export_raster("png", 40, 25)
        out["at_limit"] = "ok"
    except Exception as exc:  # noqa: BLE001
        out["at_limit"] = repr(exc)
    try:
        figcapture.check_export_raster("TIFF", 41, 25)
    except figcapture.ExportRasterTooLarge as exc:
        out["over"] = exc.params
    figcapture.check_export_raster("pdf", 10**6, 10**6)
    figcapture.check_export_raster("svg", 10**6, 10**6)
    print(json.dumps(out))
    """
)


def test_the_measured_raster_equals_what_savefig_really_writes(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE.format(engine=str(ENGINE)), encoding="utf-8")
    run = subprocess.run(
        [WORKER_PY or sys.executable, str(probe)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert out.pop("tight_exceeds_figsize"), "夹具失效：tight 的真实尺寸本该大于 figsize"
    assert out.pop("at_limit") == "ok", "宽×高恰等于上限不算超"
    assert out.pop("tight_nominal") == {"width": 300, "height": 200, "max_pixels": 50_000}
    assert out.pop("layout_nominal") == {"width": 300, "height": 200, "max_pixels": 50_000}
    assert out.pop("frame_ok") is True
    assert out.pop("over") == {"width": 41, "height": 25, "max_pixels": 1000}
    for name, (measured, written) in out.items():
        assert measured == written, f"{name}: 量出 {measured} != savefig 实际写出 {written}"
