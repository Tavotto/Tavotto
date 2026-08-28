"""在 **worker 解释器**里跑的探针：超限 SVG 到底有没有被 `read_text()`。

ADR 0021 不变量 3 的判据不是「响应里没有 svg」——那句话在「先读 126 MB
再删掉它」的实现下也成立，而那正是 issue #181 的成因（读一次加两次 JSON
编解码就能让服务进程峰值 RSS 到 1.2 GB，SVG 一个字节都还没到浏览器）。
真正要证的是**那次读根本没有发生**。

所以这里把 `pathlib.Path.read_text` 换成一个**记账的**实现（不是抛异常的：
两边都要跑，同一根探针在「阈值之上」与「阈值之下」各答一次，才是对照而不
是两个样本），然后如实报事实。判定归调用方 `tests/test_preview_budget.py`。

阈值由 `--hard-limit` 现场改写，图**保持很小**：这里验的是**机制**（判定在
读之前发生），不是那个数字——数字由常量用例单独钉住。真实规模下的验收在
`docs/perf-baseline.md` 的大图基线里。

用法：

    python tests/support/preview_guard_probe.py --hard-limit 1000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# 与 `engine/worker.py` 同一条 sys.path 纪律：engine 目录进 path，模块平铺 import。
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "src", "tavotto", "engine"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import figcapture  # noqa: E402
import figsession  # noqa: E402
import previewbudget  # noqa: E402

STEM = "GuardProbe"

#: `read_text` 的调用账本：(路径, 是不是那份预览 SVG)。
READS: list[str] = []
_REAL_READ_TEXT = Path.read_text


def _recording_read_text(self, *a, **kw):
    READS.append(str(self))
    return _REAL_READ_TEXT(self, *a, **kw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hard-limit", type=int, required=True, help="现场改写的硬闸字节数")
    args = ap.parse_args(argv)

    previewbudget.EDITOR_SVG_HARD_LIMIT_BYTES = args.hard_limit
    Path.read_text = _recording_read_text

    with tempfile.TemporaryDirectory(prefix="guard-probe-") as tmp:
        session = figsession.LiveFigureSession(tmp)
        fig, ax = plt.subplots(figsize=(3.0, 2.0))
        ax.plot([0, 1, 2], [0, 1, 0], label="line")
        ax.set_title("guard probe")
        ax.legend()
        session.add_figure(STEM, fig, figcapture.SOURCE_SAVEFIG)
        session.instrument_all()

        READS.clear()  # instrument 期间的读不算数，量的是这一次 do_render
        result = session.do_render(STEM, [], inline_svg=True)
        svg_path = str(Path(tmp) / f"{STEM}.svg")

        print(
            json.dumps(
                {
                    "hard_limit": args.hard_limit,
                    "svg_bytes_on_disk": Path(svg_path).stat().st_size,
                    "has_svg_in_response": "svg" in result,
                    "preview": result.get("preview"),
                    "manifest_elements": len(result["manifest"]["elements"]),
                    "has_warnings_key": "warnings" in result,
                    # **这一条才是不变量 3 的判据**
                    "svg_read_text_calls": READS.count(svg_path),
                    "all_read_text_calls": READS,
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
