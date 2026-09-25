# ruff: noqa: F811 — pytest 夹具按名注入，参数名与导入的夹具同名是有意的
"""SCI-02 附带探针：外观 override 之后，worker 重新序列化的 PDF 里轴标签的文字层是否完整。

复用 tests/test_appearance_edits_keep_science.py 的夹具与 patch，逐个 patch 单独施加，打印 PyMuPDF 抽出的
文字，并把页面栅格化到 scratch 目录供肉眼复核。用法（worktree 根目录）：
    bash docs/qa/2026-09-24/sci/repro/pytest.sh docs/qa/2026-09-24/sci/repro/sci02_textlayer_probe.py -s
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "tests"))

from test_appearance_edits_keep_science import (  # noqa: E402
    _gid,
    _manifest,
    _materialize,
    _original_pdf,
    project,  # noqa: F401
)


def test_probe_text_layer_per_patch(project):
    _m, client, _figs = project
    base = _manifest(client, [])
    _materialize(client)
    line = _gid(base, "label", "signal")
    title = _gid(base, "text", "Raw")
    variants = {
        "none": [],
        "color": [{"gid": line, "prop": "color", "value": "#d62728"}],
        "linewidth": [{"gid": line, "prop": "linewidth", "value": 3.0}],
        "title_fontsize": [{"gid": title, "prop": "fontsize", "value": 15.0}],
        "legend_loc": [{"gid": "axes_0.legend", "prop": "loc_frac", "value": [0.55, 0.10]}],
        "size_mm": [{"gid": "figure", "prop": "size_mm", "value": [120.0, 70.0]}],
    }
    out = Path(os.environ.get("SCI_SCRATCH", "/tmp")) / "sci02_textlayer"
    out.mkdir(parents=True, exist_ok=True)
    for name, patches in variants.items():
        pdf = _original_pdf(client, f"probe_{name}", patches)
        with pymupdf.open(pdf) as doc:
            text = doc[0].get_text().replace("\n", " | ")
            fonts = sorted({f[3] + "/" + f[2] for f in doc[0].get_fonts()})
            doc[0].get_pixmap(dpi=150).save(out / f"{name}.png")
        print(f"[{name}] fonts={fonts}\n    text={text}")
