"""把一版预览 SVG 走**真链路**渲出来落到指定路径——给浏览器侧探针当输入。

`preview_hybrid_probe.py` 量的是引擎这一侧（字节数、`<path>` 数、还原、计时），
它的产物落在 `TemporaryDirectory` 里、读完就没了。浏览器那一侧要的是**那份文件
本身**：`browser_dom_probe.mjs` 得把它挂进真 Chromium 才能问「DOM 有多少节点」。
所以这里只做一件事：走 `LiveFigureSession` 出一版预览，把 SVG 复制出来。

**走的是真的那条路**（`instrument_all()` 冷 build + `do_render()` 热 render），
不是直接 `savefig`：#181 的用户第一次打开图走的恰恰是冷 build，而 hybrid 的
接线点在 `figsession.render()` 一处，两条路必须落到同一条策略上。

`--vector` 把六个闸全抬走，出纯矢量对照——**它不是「今天会发生的事」**，是
「如果闸不存在会是什么样」。拿它量出来的数只能当形状对照，不能当裁决证据。

用法：

    python tests/support/large_preview_svg.py /tmp/hybrid.svg --n 470
    python tests/support/large_preview_svg.py /tmp/vector.svg --n 162 --vector

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

import figcapture  # noqa: E402
import figsession  # noqa: E402
import previewbudget  # noqa: E402

#: 抬闸时给的值。用一个大到不可能被越过的数，而不是 `None`——判据吃的是
#: 整数比较，塞 `None` 会变成一个 TypeError 而不是「闸不生效」。
_NO_LIMIT = 1 << 62

#: 会被 `--vector` 抬走的六个闸。**列表写在这里而不是散在调用点**：漏掉一个
#: 就会得到一份「半抬闸」的产物，而它看起来和纯矢量一模一样。
_BUDGET_NAMES = (
    "MESH_CELL_BUDGET",
    "SCATTER_INSTANCE_BUDGET",
    "COLLECTION_VERTEX_BUDGET",
    "TOTAL_VECTOR_PRIMITIVE_BUDGET",
    "EDITOR_SVG_SOFT_LIMIT_BYTES",
    "EDITOR_SVG_HARD_LIMIT_BYTES",
)


@contextlib.contextmanager
def budgets_off():
    """六个闸全抬走，出去时逐个还回原值。"""
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

    浏览器的 `Nodes` 是另一把（同一批产物上约 2.0–2.5 倍，且比值不是常数）。
    两把尺子各自报各自的，绝不互相相除。
    """
    import re

    return len(re.findall(r"<([A-Za-z][\w:-]*)", path.read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out", help="预览 SVG 复制到哪里")
    ap.add_argument("--n", type=int, default=470, help="fixture 的 mesh 边长")
    ap.add_argument(
        "--vector",
        action="store_true",
        help="把六个闸全抬走出纯矢量对照（形状对照用，不是今天会发生的事）",
    )
    ap.add_argument("--preview-dpi", type=int, default=200)
    args = ap.parse_args(argv)

    fig = fx.build(args.n)
    with tempfile.TemporaryDirectory(prefix="large-preview-") as tmp:
        sess = figsession.LiveFigureSession(tmp, preview_dpi=args.preview_dpi)
        sess.add_figure(fx.STEM, fig, figcapture.SOURCE_SAVEFIG)

        timings: dict = {}
        preview: dict = {}
        t0 = time.perf_counter()
        with budgets_off() if args.vector else contextlib.nullcontext():
            sess.instrument_all()  # ← 冷 build 走的就是这一句
            sess.do_render(fx.STEM, [], timings=timings, inline_svg=False, preview=preview)
        wall_ms = round((time.perf_counter() - t0) * 1000.0, 1)

        svg = Path(tmp) / f"{fx.STEM}.svg"
        manifest = json.loads((Path(tmp) / f"{fx.STEM}.json").read_text(encoding="utf-8"))
        payload = {
            "n": args.n,
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
