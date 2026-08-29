"""在 **worker 解释器**里跑的探针：hybrid 预览到底把什么改了、又还原了没有。

本进程（Flask 侧的 `.venv`）没有 matplotlib，而 hybrid 改的就是 matplotlib 的
artist——所以判据留在 `tests/test_preview_hybrid.py`，这里只负责**如实报事实**。

走的是**真的那条路**（`figsession.LiveFigureSession`）：`instrument_all()` 是
冷 build，`do_render()` 是热 render，`do_export()` 是出版导出。抄一条简化的
调用链进来就等于测一个我们自己写的假流程——而 #181 的用户第一次打开图走的
恰恰是冷 build 那条。

伪 artist 只出现在**故障注入**那一格（`--case contextmanager`）：真的
matplotlib artist 的 `set_rasterized` 是一句赋值，没有办法让它失败，而
「还原到一半炸了」正是最需要有人守着的那条路。同一件事在真 artist 上的读数
在 `lifecycle` 那一格里（[[simulated-input-shape-lies]]：伪造的输入形状只用来
造那个真输入造不出的**故障**，不用来替代真输入）。

用法：

    python tests/support/preview_hybrid_probe.py                 # 全部
    python tests/support/preview_hybrid_probe.py --n 200
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

# 与 `engine/worker.py` 同一条 sys.path 纪律：engine 目录进 path，模块平铺 import。
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_REPO, "src", "tavotto", "engine"))
sys.path.insert(0, os.path.join(_REPO, "tests", "fixtures", "large_figures"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import issue_181_large_pcolormesh as fx  # noqa: E402  (fixtures/large_figures 在 path 上)
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

import figcapture  # noqa: E402
import figsession  # noqa: E402
import preview_complexity as pc  # noqa: E402
import preview_hybrid as ph  # noqa: E402
import previewbudget  # noqa: E402

#: 探针默认的 mesh 边长：40 000 cell/格，是 `MESH_CELL_BUDGET` 的两倍——够越线，
#: 又比基线那个 470 快一个数量级（用例不该为了越线跑十几秒）。
DEFAULT_N = 200

_REAL_READ_TEXT = Path.read_text
READS: list[str] = []


def _recording_read_text(self, *a, **kw):
    READS.append(str(self))
    return _REAL_READ_TEXT(self, *a, **kw)


#: matplotlib 往 SVG 的 RDF 元数据里写一行**微秒精度的时间戳**——同一张图连存
#: 两次也不会逐字节相同。比字节之前把它抹掉，比的才是画出来的那份。
_DC_DATE = re.compile(r"<dc:date>[^<]*</dc:date>")


def _undated(text: str) -> str:
    return _DC_DATE.sub("<dc:date/>", text)


def _svg_stats(text: str) -> dict:
    return {
        "bytes": len(text.encode("utf-8")),
        "paths": text.count("<path"),
        "images": text.count("<image"),
    }


def _meshes(fig):
    """三块用户 mesh（色条那块 `solids` 不算——它是 matplotlib 每次重画的内部件）。"""
    return [a for ax in fig.axes[:3] for a in ax.collections]


def _gids(man: dict, role: str) -> list[str]:
    return [e["gid"] for e in man["elements"] if e.get("role") == role]


@contextlib.contextmanager
def _budgets_off():
    """把**六个**闸全抬走 = 纯矢量对照组。

    只抬 `MESH_CELL_BUDGET` 是不够的：同一块 mesh 还会撞上顶点预算、图级预算，
    而 22.9 MB 的产物又会撞上软闸（触发升档）与硬闸（不给 SVG）。漏一个的
    表现是「对照组自己也变成了 hybrid」——那时 A/B 两侧一模一样，比值恒等于 1，
    尺子量不到它要量的那一维（[[crosscheck-ruler-must-see-the-dimension]]）。
    """
    names = (
        "MESH_CELL_BUDGET",
        "SCATTER_INSTANCE_BUDGET",
        "COLLECTION_VERTEX_BUDGET",
        "TOTAL_VECTOR_PRIMITIVE_BUDGET",
        "EDITOR_SVG_SOFT_LIMIT_BYTES",
        "EDITOR_SVG_HARD_LIMIT_BYTES",
    )
    saved = {n: getattr(previewbudget, n) for n in names}
    for n in names:
        setattr(previewbudget, n, 10**15)
    try:
        yield
    finally:
        for n, v in saved.items():
            setattr(previewbudget, n, v)


def _session(tmp: str, fig, dpi: int = 200):
    sess = figsession.LiveFigureSession(tmp, preview_dpi=dpi)
    sess.add_figure(fx.STEM, fig, figcapture.SOURCE_SAVEFIG)
    return sess


# ------------------------------------------------------------------ lifecycle
def case_lifecycle(n: int) -> dict:
    """冷 build → 热 render → 导出 → 还原，一条真链路上的全部读数。"""
    out: dict = {}
    with tempfile.TemporaryDirectory(prefix="hybrid-probe-") as tmp:
        fig = fx.build(n)
        meshes = _meshes(fig)
        out["rasterized_before"] = [m.get_rasterized() for m in meshes]
        sess = _session(tmp, fig)

        t0 = time.perf_counter()
        sess.instrument_all()  # ← 冷 build 走的就是这一句
        out["cold_build_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        svg_path = Path(tmp) / f"{fx.STEM}.svg"
        out["cold"] = _svg_stats(svg_path.read_text(encoding="utf-8"))
        out["rasterized_after_cold"] = [m.get_rasterized() for m in meshes]

        timings: dict = {}
        preview: dict = {}
        res = sess.do_render(fx.STEM, [], timings=timings, inline_svg=True, preview=preview)
        out["preview"] = preview
        out["timings"] = timings
        out["hot"] = _svg_stats(res["svg"])
        out["rasterized_after_hot"] = [m.get_rasterized() for m in meshes]
        out["has_svg_in_response"] = "svg" in res

        man = res["manifest"]
        out["manifest_elements"] = len(man["elements"])
        mesh_gids = _gids(man, "collection")
        vector_gids = _gids(man, "line") + _gids(man, "legend") + _gids(man, "axes")[:1]
        out["mesh_gids"] = mesh_gids
        # gid 丢失是**允许的**（ADR 0022 §7 / Session 03 §9）：rasterize 掉的
        # artist 在 SVG DOM 里没有自己的节点了，假实时预览安静退出、覆盖层接管。
        out["mesh_gids_in_svg"] = [g for g in mesh_gids if g in res["svg"]]
        out["vector_gids"] = vector_gids
        out["vector_gids_in_svg"] = [g for g in vector_gids if g in res["svg"]]

        # 写回不受影响：这是表示法策略，不是用户改动
        out["snapshot_after_hybrid"] = sess.snapshot(fx.STEM)

        # 同一张图、同一个会话、**紧挨着的两次**：把预算抬到天上再渲一次 =
        # 纯矢量对照组。A/B 在同一进程同一次运行里交替，不是两次跑的两个样本
        # （[[interleaved-ab-not-sequential]]）。
        with _budgets_off():
            vpreview: dict = {}
            vres = sess.do_render(fx.STEM, [], inline_svg=True, preview=vpreview)
            out["vector_preview"] = vpreview
            out["vector"] = _svg_stats(vres["svg"])
            # 不变量 1：表示法换了，**语义 manifest 逐字节不变**
            out["manifest_identical"] = json.dumps(
                vres["manifest"], sort_keys=True, ensure_ascii=False, default=str
            ) == json.dumps(man, sort_keys=True, ensure_ascii=False, default=str)

        # 导出保真（不变量 2）：常驻 Figure 上一个 rasterized 都没留下
        exp = Path(tmp) / "export.svg"
        sess.do_export(fx.STEM, [], str(exp), fmt="svg")
        out["export"] = _svg_stats(exp.read_text(encoding="utf-8"))
        out["rasterized_after_export"] = [m.get_rasterized() for m in meshes]
        plt.close(fig)
    return out


def case_user_rasterized(n: int) -> dict:
    """用户自己 `set_rasterized(True)` 的那块 mesh：预览前后都必须还是 True，
    导出也必须还是位图。**「还原」是还回原值，不是还回 False。**"""
    out: dict = {}
    with tempfile.TemporaryDirectory(prefix="hybrid-probe-") as tmp:
        fig = fx.build(n)
        meshes = _meshes(fig)
        meshes[0].set_rasterized(True)
        out["before"] = [m.get_rasterized() for m in meshes]
        sess = _session(tmp, fig)
        sess.instrument_all()
        out["after_cold"] = [m.get_rasterized() for m in meshes]
        sess.do_render(fx.STEM, [], inline_svg=True, preview={})
        out["after_hot"] = [m.get_rasterized() for m in meshes]
        exp = Path(tmp) / "export.svg"
        sess.do_export(fx.STEM, [], str(exp), fmt="svg")
        out["export"] = _svg_stats(exp.read_text(encoding="utf-8"))
        out["after_export"] = [m.get_rasterized() for m in meshes]
        plt.close(fig)
    return out


def case_savefig_raises(n: int) -> dict:
    """`savefig` 中途抛了：`finally` 必须把每一个都还回去。

    没有它的话，下一次 `do_export` 会把预览用的位图化当成用户的选择写进论文——
    而中间没有任何一步会报错。
    """
    out: dict = {}
    with tempfile.TemporaryDirectory(prefix="hybrid-probe-") as tmp:
        fig = fx.build(n)
        meshes = _meshes(fig)
        sess = _session(tmp, fig)
        sess.instrument_all()
        out["before"] = [m.get_rasterized() for m in meshes]
        rasterized_inside: list = []
        real = fig.savefig

        def boom(*a, **kw):
            rasterized_inside.append([m.get_rasterized() for m in meshes])
            raise RuntimeError("savefig 炸了")

        fig.savefig = boom
        try:
            sess.render(fx.STEM)
        except RuntimeError as exc:
            out["raised"] = str(exc)
        finally:
            fig.savefig = real
        # **窗口里确实设上了**：不设也能「还原正确」，那是一条恒真的判据
        out["rasterized_inside_window"] = rasterized_inside
        out["after"] = [m.get_rasterized() for m in meshes]
        plt.close(fig)
    return out


# ------------------------------------------------------- 故障注入（伪 artist）
class FakeArtist:
    """只为**制造 `set_rasterized` 失败**而存在——真 artist 上那句是赋值，炸不了。"""

    def __init__(self, initial=False, boom: str | None = None):
        self.value = initial
        self.boom = boom
        self.calls: list = []

    def get_rasterized(self):
        return self.value

    def set_rasterized(self, v):
        self.calls.append(v)
        if self.boom == "enter" and v is True:
            raise RuntimeError("进窗口时炸")
        if self.boom == "exit" and v is not True:
            raise RuntimeError("还原时炸")
        self.value = v

    def __repr__(self):
        return f"<FakeArtist {id(self):x}>"


def case_contextmanager() -> dict:
    out: dict = {}

    # ① 空名单是真的 no-op：一个 getter 一个 setter 都不许调
    class Landmine:
        def __getattr__(self, name):
            raise AssertionError(f"空名单不该碰任何 artist，却调了 {name}")

    with ph.preview_rasterization([]):
        pass
    out["empty_is_noop"] = True

    # ② 正常一轮：初值 True 的还回 True，初值 False 的还回 False
    a, b = FakeArtist(False), FakeArtist(True)
    with ph.preview_rasterization([a, b]):
        out["inside"] = [a.value, b.value]
    out["restored"] = [a.value, b.value]

    # ③ 进窗口时第二个炸：已经设过的那个必须被还回去
    x, y, z = FakeArtist(False), FakeArtist(False, boom="enter"), FakeArtist(False)
    try:
        with ph.preview_rasterization([x, y, z]):
            out["enter_boom_body_ran"] = True
    except RuntimeError as exc:
        out["enter_boom"] = str(exc)
    out["enter_boom_values"] = [x.value, y.value, z.value]
    out["enter_boom_third_untouched"] = z.calls == []

    # ④ 还原时第二个炸：**其余两个照样要还原**，且必须吵出来
    x, y, z = FakeArtist(False), FakeArtist(False, boom="exit"), FakeArtist(True)
    try:
        with ph.preview_rasterization([x, y, z]):
            pass
    except ph.RestoreFailed as exc:
        out["exit_boom"] = str(exc)[:80]
    out["exit_boom_values"] = [x.value, y.value, z.value]

    # ⑤ 窗口体自己抛：异常照旧传出去，状态照旧还原
    a, b = FakeArtist(False), FakeArtist(True)
    try:
        with ph.preview_rasterization([a, b]):
            raise ValueError("body")
    except ValueError as exc:
        out["body_raise"] = str(exc)
    out["body_raise_values"] = [a.value, b.value]
    assert Landmine  # 只是让它别被 linter 判成没用
    return out


# ------------------------------------------------------------------ 软闸升档
def _fig_small_meshes():
    """三块**都在逐族预算之内**的 mesh：第一遍分析器判 vector，全靠字节闸兜。"""
    fig, axes = plt.subplots(2, 2, figsize=(7.0, 5.6), constrained_layout=True)
    rng = np.random.default_rng(3)
    edges = np.linspace(0, 1, 61)
    for ax in axes.flat[:3]:
        ax.pcolormesh(edges, edges, rng.standard_normal((60, 60)), shading="flat")
    axes.flat[3].plot(np.linspace(0, 1, 100), np.linspace(0, 1, 100), label="l")
    axes.flat[3].legend()
    return fig


def _fig_many_vector_curves():
    """六十条 3000 点的曲线：3 MB 的产物，但**按契约一条都不许 rasterize**。

    数据取随机游走而不是 `sin`：matplotlib 的 `path.simplify` 会把光滑曲线压
    掉九成，那样图小得根本越不过软闸——用例就在测「没越线所以没升档」，而不是
    「越了线也升不动」。
    """
    fig, ax = plt.subplots(figsize=(7.0, 5.6))
    rng = np.random.default_rng(7)
    t = np.linspace(0, 1, 3000)
    for _ in range(60):
        ax.plot(t, rng.standard_normal(3000).cumsum(), lw=0.4)
    return fig


def _escalation_case(fig, soft: int) -> dict:
    """同一张图上的 A/B：软闸抬走（不升档）→ 软闸压到 `soft`（该升就升）。"""
    out: dict = {}
    calls: list[int] = []
    with tempfile.TemporaryDirectory(prefix="hybrid-probe-") as tmp:
        sess = _session(tmp, fig)
        real = fig.savefig

        def counting(*a, **kw):
            calls.append(len(calls))
            return real(*a, **kw)

        sess.instrument_all()  # 冷 build（不计入下面的账）
        try:
            fig.savefig = counting
            with _budgets_off():
                base: dict = {}
                bres = sess.do_render(fx.STEM, [], inline_svg=True, preview=base)
                out["baseline_savefig_calls"] = len(calls)
                out["baseline_preview"] = base
                out["baseline_svg"] = _svg_stats(bres["svg"])
            calls.clear()
            saved = previewbudget.EDITOR_SVG_SOFT_LIMIT_BYTES
            previewbudget.EDITOR_SVG_SOFT_LIMIT_BYTES = soft
            try:
                preview: dict = {}
                res = sess.do_render(fx.STEM, [], inline_svg=True, preview=preview)
                out["soft_limit"] = soft
                out["savefig_calls"] = len(calls)
                out["preview"] = preview
                out["svg"] = _svg_stats(res["svg"]) if "svg" in res else None
            finally:
                previewbudget.EDITOR_SVG_SOFT_LIMIT_BYTES = saved
        finally:
            fig.savefig = real
    plt.close(fig)
    return out


def case_escalation() -> dict:
    """两把尺子里的第二把：分析器判 vector，产物却越过软闸 ⇒ 再画一遍。"""
    return _escalation_case(_fig_small_meshes(), soft=200_000)


def case_no_escalation_possible() -> dict:
    """越过软闸，但**一个可 rasterize 的层都没有**：只画一遍，老实报 vector。

    再画一遍不会变小（普通曲线按契约不许收），那一次 `savefig` 就是白付的钱。
    """
    return _escalation_case(_fig_many_vector_curves(), soft=200_000)


def case_hard_guard(n: int) -> dict:
    """hybrid 产物照样可能超硬闸——**那时它仍然不许被读**（不变量 3 不打折）。"""
    out: dict = {}
    saved = previewbudget.EDITOR_SVG_HARD_LIMIT_BYTES
    previewbudget.EDITOR_SVG_HARD_LIMIT_BYTES = 100_000
    Path.read_text = _recording_read_text
    try:
        with tempfile.TemporaryDirectory(prefix="hybrid-probe-") as tmp:
            fig = fx.build(n)
            sess = _session(tmp, fig)
            sess.instrument_all()
            READS.clear()
            preview: dict = {}
            res = sess.do_render(fx.STEM, [], inline_svg=True, preview=preview)
            svg_path = str(Path(tmp) / f"{fx.STEM}.svg")
            out["preview"] = preview
            out["has_svg_in_response"] = "svg" in res
            out["svg_read_text_calls"] = READS.count(svg_path)
            out["manifest_elements"] = len(res["manifest"]["elements"])
            plt.close(fig)
    finally:
        Path.read_text = _REAL_READ_TEXT
        previewbudget.EDITOR_SVG_HARD_LIMIT_BYTES = saved
    return out


def case_normal_figure() -> dict:
    """普通科研图：名单是空的，产物与「根本没有 hybrid 这回事」**逐字节相同**。

    `svg.hashsalt` 要钉死：matplotlib 默认拿一个**每次 savefig 现取的
    `uuid4()`** 当哈希盐，于是同一张图两次导出的 `clip-path` / marker id 全都
    不一样——不钉住的话这条比对恒为假，而它恒为假的理由与被测的东西无关。
    """
    out: dict = {}
    with tempfile.TemporaryDirectory(prefix="hybrid-probe-") as tmp:
        matplotlib.rcParams["svg.hashsalt"] = "tavotto-preview-hybrid-probe"
        fig, ax = plt.subplots(figsize=(4.0, 3.0))
        t = np.linspace(0, 10, 400)
        ax.plot(t, np.exp(-t / 4), label="decay")
        ax.set_title("normal")
        ax.legend()
        sess = _session(tmp, fig)
        sess.instrument_all()
        preview: dict = {}
        res = sess.do_render(fx.STEM, [], inline_svg=True, preview=preview)
        out["preview"] = preview
        state = sess.states[fx.STEM]
        out["plan_empty"] = pc.plan_for_state(state).rasterized_artist_count == 0
        import io

        buf = io.StringIO()
        fig.savefig(buf, format="svg", dpi=200)
        out["identical_to_plain_savefig"] = _undated(buf.getvalue()) == _undated(res["svg"])
        out["hashsalt"] = matplotlib.rcParams["svg.hashsalt"]
        matplotlib.rcParams["svg.hashsalt"] = None
        plt.close(fig)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n", type=int, default=DEFAULT_N)
    args = ap.parse_args(argv)
    payload = {
        "matplotlib": matplotlib.__version__,
        "n": args.n,
        "limits": {
            "soft": previewbudget.EDITOR_SVG_SOFT_LIMIT_BYTES,
            "hard": previewbudget.EDITOR_SVG_HARD_LIMIT_BYTES,
        },
        "lifecycle": case_lifecycle(args.n),
        "user_rasterized": case_user_rasterized(args.n),
        "savefig_raises": case_savefig_raises(args.n),
        "contextmanager": case_contextmanager(),
        "escalation": case_escalation(),
        "no_escalation_possible": case_no_escalation_possible(),
        "hard_guard": case_hard_guard(args.n),
        "normal_figure": case_normal_figure(),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
