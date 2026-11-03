"""CompatBench 语料上的快照保真度（ADR 0139 §九.4）。

每个 case 的 main() 跑完，对每张还开着的 Figure：拍快照 -> 还原 -> 同 dpi 渲染对比。
手工脚本，不进 pytest：用要验的 matplotlib 版本的解释器执行 `python tests/support/save_moment_corpus.py`。
打印 `identical / unpicklable / restore_failed / pixel_diff ...` 计数；`pixel_diff` 必须为 0。
"""

from __future__ import annotations

import collections
import glob
import importlib.util
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tavotto", "engine"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from PIL import Image  # noqa: E402

import figcapture  # noqa: E402


def png(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    return np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGBA")).astype(int)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    counts: collections.Counter = collections.Counter()
    detail = []
    os.chdir(tempfile.mkdtemp())
    for path in sorted(glob.glob(os.path.join(ROOT, "tests", "compat", "cases", "*", "*.py"))):
        name = os.path.basename(path)
        if name.startswith("_") or "shadow" in path:
            continue
        plt.close("all")
        try:
            spec = importlib.util.spec_from_file_location("c_" + name[:-3], path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            if hasattr(mod, "main"):
                mod.main()
        except BaseException as exc:  # noqa: BLE001 —— 语料里有依赖外部文件 / 包的 case，跳过并计数
            counts["script_error"] += 1
            detail.append((name, "script_error", type(exc).__name__))
            continue
        for num in plt.get_fignums():
            fig = plt.figure(num)
            ledger = figcapture.SaveMomentLedger()
            ledger.take(fig, 1)
            restored, reason = ledger.restore(fig)
            if restored is None:
                counts[reason] += 1
                detail.append((name, reason, ""))
                continue
            FigureCanvasAgg(restored)
            a, b = png(fig), png(restored)
            if a.shape != b.shape:
                counts["pixel_diff"] += 1
                detail.append((name, "pixel_diff", "shape"))
            elif int(np.abs(a - b).max()) == 0:
                counts["identical"] += 1
            else:
                counts["pixel_diff"] += 1
                detail.append((name, "pixel_diff", f"maxdiff={int(np.abs(a - b).max())}"))
    print("mpl", matplotlib.__version__, dict(counts))
    for row in detail:
        print(" ", *row)


if __name__ == "__main__":
    main()
