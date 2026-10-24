"""零编辑导出的 PDF 与脚本自己 savefig 的 PDF 布局一致（usetex + autolayout + bbox tight）。

Celerite `sho.py` 的形状：`font.family` 为单族 sans-serif、`text.usetex`、
`figure.autolayout`、`savefig(bbox_inches="tight")`。字体回退尾巴一旦写进 rcParams，
TeX 管理器退成 serif，刻度文字度量变，tight 布局把三个 axes 的底边抬高约 0.44 pt。
判据是两份 PDF 的**文字坐标**与**坐标轴外框**逐项比对（不看像素）。
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None


def _has_cmbright():
    kpse = shutil.which("kpsewhich")
    if kpse is None:
        return False
    out = subprocess.run(
        [kpse, "cmbright.sty"], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return bool(out.stdout.strip())


pymupdf = pytest.importorskip("pymupdf")
pytestmark = [
    pytest.mark.skipif(WORKER_PY is None, reason="matplotlib worker unavailable"),
    pytest.mark.skipif(
        shutil.which("latex") is None or shutil.which("dvipng") is None or not _has_cmbright(),
        reason="usetex needs a TeX installation",
    ),
]

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams.update({"font.size": 16, "font.family": "sans-serif",
                     "font.sans-serif": ["Computer Modern Sans"], "text.usetex": True,
                     "text.latex.preamble": r"\\usepackage{cmbright}", "figure.autolayout": True})
x = np.linspace(0.1, 12, 200)
fig, axes = plt.subplots(1, 3, figsize=(12, 4))
for i, ax in enumerate(axes):
    ax.plot(x, np.sin(x * (i + 1)) / (i + 1), lw=1.5)
    ax.set_xlabel(r"$\\omega_0\\,t$")
    ax.set_ylabel(r"$k(\\tau) / k(0)$")
fig.savefig("fig.pdf", bbox_inches="tight")
"""


def _facts(path):
    page = pymupdf.open(path)[0]
    words = sorted((w[4], w[0], w[1], w[2], w[3]) for w in page.get_text("words"))
    frames = sorted(
        (d["rect"].x0, d["rect"].y0, d["rect"].x1, d["rect"].y1)
        for d in page.get_drawings()
        if d["rect"].width > 100 and d["rect"].height > 100
    )
    return (page.rect.width, page.rect.height), words, frames


def test_zero_edit_pdf_matches_the_scripts_own_savefig(tmp_path):
    (tmp_path / "fig.py").write_text(SCRIPT, encoding="utf-8")
    subprocess.run(
        [WORKER_PY, "fig.py"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        timeout=180,
        encoding="utf-8",
        errors="replace",
    )
    native = _facts(tmp_path / "fig.pdf")
    (tmp_path / "fig.pdf").rename(tmp_path / "native.pdf")

    worker = pool.one_shot("fig.py", str(tmp_path), "__main__")
    try:
        worker.ensure_built()
        worker.override("fig", [])
        out = tmp_path / "product.pdf"
        worker.export("fig", [], str(out), "pdf", 600)
    finally:
        pool.discard(worker)
    product = _facts(out)

    assert product[0] == pytest.approx(native[0], abs=0.01)
    assert [w[0] for w in product[1]] == [w[0] for w in native[1]]
    for got, want in zip(product[1], native[1]):
        assert got[1:] == pytest.approx(want[1:], abs=0.01), got[0]
    assert product[2] == pytest.approx(native[2], abs=0.01)
    assert native[2], "no axes frame found: the comparison would be hollow"
