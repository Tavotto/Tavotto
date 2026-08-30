"""把一版预览 SVG 走**真链路**渲出来落到指定路径——给浏览器侧探针当输入。

`preview_hybrid_probe.py` 量的是引擎这一侧（字节数、`<path>` 数、还原、计时），
它的产物落在 `TemporaryDirectory` 里、读完就没了。浏览器那一侧要的是**那份文件
本身**：`browser_dom_probe.mjs` 得把它挂进真 Chromium 才能问「DOM 有多少节点」。
所以这里只做一件事：走 `LiveFigureSession` 出一版预览，把 SVG 复制出来。

**走的是真的那条路**（`instrument_all()` 冷 build + `do_render()` 热 render），
不是直接 `savefig`：#181 的用户第一次打开图走的恰恰是冷 build，而 hybrid 的
接线点在 `figsession.render()` 一处，两条路必须落到同一条策略上。

`--vector` 把每一条闸都抬走，出纯矢量对照——**它不是「今天会发生的事」**，是
「如果闸不存在会是什么样」。拿它量出来的数只能当形状对照，不能当裁决证据。

`--shape` 选的是**哪一类图**。`docs/perf-baseline.md` 的「字节闸看不见节点数」
那张表每一行都对应这里的一个 shape——少了它，那张表的结论就只能靠相信，
而不是靠重跑：

    mesh   #181 的 fixture（QuadMesh 大头 + 一格普通曲线），`--n` = mesh 边长
    lines  n 条 3 点折线。**节点多、字节少**，是「无闸触发的可达 freeze 路径」
    dense  n 条 2000 点密集曲线。**字节多、节点少**，用来否掉「字节数是节点
           数的代理」这个说法
    paths  n 个逐点大小的 scatter marker。落在 `draw_path_collection` 的慢路上
           （每个 marker 各自内联），是小 path 海的形状对照

用法：

    python tests/support/large_preview_svg.py /tmp/hybrid.svg --n 470
    python tests/support/large_preview_svg.py /tmp/vector.svg --n 162 --vector
    python tests/support/large_preview_svg.py /tmp/lines.svg --shape lines --n 40000

在 **worker 解释器**里跑（本进程要 matplotlib）。stdout 最后一行是 JSON。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
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
import issue_181_large_pcolormesh as fx  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

import figcapture  # noqa: E402
import figsession  # noqa: E402
import previewbudget  # noqa: E402

# Windows 上 stdout 被重定向成管道时会退回系统区域编码（cp1252/cp936），而这个
# 脚本把**带中文的 JSON** 打给调用方（CI 的 windows-exe-smoke 就是这么调的）
# ——第一次 print 就 UnicodeEncodeError，退出码变成 1。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

#: 抬闸时给的值。用一个大到不可能被越过的数，而不是 `None`——判据吃的是
#: 整数比较，塞 `None` 会变成一个 TypeError 而不是「闸不生效」。
_NO_LIMIT = 1 << 62

#: 会被 `--vector` 抬走的闸，**从 `previewbudget` 枚举出来而不是手写**：
#: 手写的名单漏掉一条就会给出一份「半抬闸」的产物，而它看起来和纯矢量
#: 一模一样。`TOTAL_VECTOR_NODE_BUDGET` 加进去时，探针里那份手写名单就漏了。
_BUDGET_NAMES = tuple(
    n for n in previewbudget.__all__ if n.endswith("_BUDGET") or n.endswith("_LIMIT_BYTES")
)


@contextlib.contextmanager
def budgets_off():
    """每一条闸都抬走，出去时逐个还回原值。"""
    saved = {k: getattr(previewbudget, k) for k in _BUDGET_NAMES}
    for k in saved:
        setattr(previewbudget, k, _NO_LIMIT)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(previewbudget, k, v)


def svg_stats(path: Path, chunk_bytes: int = 1 << 20) -> dict:
    """按块数标签，不把 126 MB 读进内存。

    **重叠区里数得完整的那些要减掉**——上一块已经数过了。少了这一句，每有一个
    needle 恰好整个落在重叠区里就多报一个（`scripts/bench_render.py` 当年就是
    这么把 662 772 报成 662 773 的）。判据与
    `preview_hybrid_probe._svg_file_stats` 是同一条。
    """
    needles = {"paths": b"<path", "images": b"<image", "uses": b"<use", "groups": b"<g "}
    overlap = max(len(v) for v in needles.values()) - 1
    stats = {k: 0 for k in needles}
    stats["bytes"] = 0
    tail = b""
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_bytes):
            stats["bytes"] += len(chunk)
            window = tail + chunk
            for key, needle in needles.items():
                stats[key] += window.count(needle) - tail.count(needle)
            tail = window[-overlap:]
    return stats


def element_total(path: Path) -> int:
    """SVG 元素总数——**与 before 基线那个 663 533 同一把尺子**。

    浏览器的 `Nodes` 是另一把（同一批产物上 2.0–3.5 倍，且比值不是常数）。
    两把尺子各自报各自的，绝不互相相除。
    """
    import re

    return len(re.findall(r"<([A-Za-z][\w:-]*)", path.read_text(encoding="utf-8")))


#: 每个 shape 的默认规模。`mesh` 与 fixture 的 `DEFAULT_MESH_N` 对齐，其余
#: 三个取的是 `docs/perf-baseline.md` 那张表里实际跑出来的规模。
_SHAPE_DEFAULT_N = {"mesh": 470, "lines": 40_000, "dense": 560, "paths": 40_000}


def build_shape(shape: str, n: int):
    """按 shape 造一张图。**数据全部由 `rng(181)` 现生成**，同一个 (shape, n)
    在任何机器上是同一张图——基线数字才对得上。"""
    if shape == "mesh":
        return fx.build(n)

    rng = np.random.default_rng(fx.SEED)
    fig, ax = plt.subplots(figsize=(8, 6))
    if shape == "lines":
        # 每条 3 个点：**节点多、字节少**。`line` 按契约不在
        # `RASTERIZABLE_FAMILIES` 里，所以复杂度闸对它无能为力——这正是
        # Session 06 找到的那条路。
        ys = rng.random((n, 3))
        for row in ys:
            ax.plot([0.0, 0.5, 1.0], row, linewidth=0.5)
    elif shape == "dense":
        # 每条 2000 个点：**字节多、节点少**。字节全在 path 的 `d` 属性里。
        #
        # 数据用随机游走而不是正弦：`path.simplify` 默认开着，光滑曲线会被它
        # 抽掉大半个点，量出来的字节数就不是「2000 个点该有多少」了（实测差
        # 五倍）。**要量的那一维必须活到产物里**，否则这一行证不了它想证的事。
        x = np.linspace(0.0, 1.0, 2000)
        for row in np.cumsum(rng.standard_normal((n, 2000)), axis=1):
            ax.plot(x, row, linewidth=0.4)
    elif shape == "paths":
        # **逐点大小**让它掉出 `do_single_path_optimization` 那条快路：
        # 每个 marker 各自内联，而不是 `<defs>` 里一份 + n 个 `<use>`。
        # 这一格是 `_is_single_path_fast_path` 建模的反面样本。
        ax.scatter(
            rng.random(n), rng.random(n), s=rng.uniform(2.0, 20.0, n), c=rng.random(n), lw=0.0
        )
    else:  # pragma: no cover - argparse 已经挡住了
        raise ValueError(f"未知 shape: {shape}")
    return fig


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out", help="预览 SVG 复制到哪里")
    ap.add_argument(
        "--shape",
        choices=sorted(_SHAPE_DEFAULT_N),
        default="mesh",
        help="画哪一类图（见模块文档；对应 perf-baseline 那张表的每一行）",
    )
    ap.add_argument(
        "--n",
        type=int,
        default=None,
        help="规模。mesh 是边长，其余三个是条数/点数；缺省按 shape 取（见 _SHAPE_DEFAULT_N）",
    )
    ap.add_argument(
        "--vector",
        action="store_true",
        help="把每一条闸都抬走出纯矢量对照（形状对照用，不是今天会发生的事）",
    )
    ap.add_argument("--preview-dpi", type=int, default=200)
    args = ap.parse_args(argv)

    n = args.n if args.n is not None else _SHAPE_DEFAULT_N[args.shape]
    fig = build_shape(args.shape, n)
    with tempfile.TemporaryDirectory(prefix="large-preview-") as tmp:
        sess = figsession.LiveFigureSession(tmp, preview_dpi=args.preview_dpi)
        stem = fx.STEM if args.shape == "mesh" else f"perfbaseline_{args.shape}"
        sess.add_figure(stem, fig, figcapture.SOURCE_SAVEFIG)

        timings: dict = {}
        preview: dict = {}
        t0 = time.perf_counter()
        with budgets_off() if args.vector else contextlib.nullcontext():
            sess.instrument_all()  # ← 冷 build 走的就是这一句
            sess.do_render(stem, [], timings=timings, inline_svg=False, preview=preview)
        wall_ms = round((time.perf_counter() - t0) * 1000.0, 1)

        svg = Path(tmp) / f"{stem}.svg"
        manifest = json.loads((Path(tmp) / f"{stem}.json").read_text(encoding="utf-8"))
        payload = {
            "shape": args.shape,
            "n": n,
            "vector_control": args.vector,
            "matplotlib": matplotlib.__version__,
            "preview": preview,
            "timings": timings,
            "wall_ms": wall_ms,
            "svg": svg_stats(svg),
            "svg_element_total": element_total(svg),
            "manifest_elements": len(manifest.get("elements", [])),
            "out": args.out,
        }
        shutil.copyfile(svg, args.out)
    plt.close(fig)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
