"""SCI-01 对照：A 腿（worker 零 override 重序列化 vs 原生）的差异是不是来自 Tavotto 明示的 `pdf.fonttype=42`
（`engine/figsession.py`：导出 PDF/PS 时字体按 TrueType 嵌入，保留文字层）。

做法：原生再跑一次、只加 `rcParams["pdf.fonttype"] = 42`，拿它与 Tavotto 重序列化的 PDF 比。若差异消失，
A 腿的偏差归类为「明示支持范围内的差异」，不是缺陷。
前置：先以 SCI01_KEEP=<目录> 跑过 sci01_final_export_fidelity.py（留下 *.tavotto.pdf）。
用法：WORKER_PY=... "$PY" docs/qa/2026-09-24/sci/repro/sci01_fonttype42_control.py <keep 目录>
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[5]
CASES = {
    "art_colorbar": "tests/compat/cases/core_artists/ca_legend_colorbar.py",
    "sci_mathtext": "tests/compat/cases/scientific_stack/sci_typography.py",
}


def _pix(pdf: Path):
    with pymupdf.open(pdf) as doc:
        p = doc[0].get_pixmap(dpi=150, alpha=False)
        return bytes(p.samples), doc[0].get_text()


def main() -> int:
    keep = Path(sys.argv[1])
    ctl = keep / "native_fonttype42"
    ctl.mkdir(parents=True, exist_ok=True)
    ok = True
    for stem, rel in CASES.items():
        src = ROOT / rel
        shutil.copy2(src, ctl / src.name)
        subprocess.run(
            [
                os.environ.get("WORKER_PY", sys.executable),
                "-c",
                "import matplotlib as mpl; mpl.rcParams['pdf.fonttype'] = 42\n"
                f"import {src.stem} as s; s.main()",
            ],
            cwd=ctl,
            env={**os.environ, "MPLBACKEND": "Agg", "PYTHONPATH": str(ctl)},
            check=True,
            capture_output=True,
        )
        a, ta = _pix(ctl / f"{stem}.pdf")
        b, tb = _pix(keep / f"{stem}.tavotto.pdf")
        n = len(a) // 3
        changed = sum(
            1
            for i in range(0, len(a), 3)
            if max(abs(a[i] - b[i]), abs(a[i + 1] - b[i + 1]), abs(a[i + 2] - b[i + 2])) > 16
        )
        print(f"{stem}: changed_pixel_ratio vs native(fonttype42) = {changed / n:.6f}; text_equal = {ta == tb}")
        ok &= changed / n <= 0.004 and ta == tb
    print("fonttype42 control:", "差异归因于 fonttype 42" if ok else "仍有 fonttype 之外的差异")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
