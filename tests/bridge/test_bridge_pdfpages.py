"""native bridge 也认领写进 `PdfPages` 的图（#1370 PR1，三条入口同源）。

规则唯一实现 `figcapture.PdfPagesLedger` / `adopt_pdf_pages`；safe worker 与浏览器的对应用例在
`tests/test_capture_pdfpages.py`。native 与 safe 相反：PDF 真写盘（透传），所以这里还钉着
「磁盘上的多页 PDF 不是这些图的原件」。
"""

from __future__ import annotations

import json
import re

import pytest

from support.bridgekit import run_runner, write
from tavotto.engine import bridge

pytestmark = pytest.mark.usefixtures("clean_env")

HEAD = "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
PDF = "from matplotlib.backends.backend_pdf import PdfPages\n"

CLOSE_EACH = PDF + (
    "with PdfPages('all_samples.pdf') as pdf:\n"
    "    for i in range(3):\n"
    "        fig = plt.figure(figsize=(3, 2))\n"
    "        pdf.savefig(fig)\n"
    "        plt.close(fig)\n"
)
CLOSE_ALL_END = PDF + (
    "with PdfPages('all_samples.pdf') as pdf:\n"
    "    for i in range(3):\n"
    "        plt.figure(figsize=(3, 2))\n"
    "        pdf.savefig()\n"
    "plt.close('all')\n"
)
KEEP_OPEN = PDF + (
    "with PdfPages('all_samples.pdf') as pdf:\n"
    "    for i in range(3):\n"
    "        fig = plt.figure(figsize=(3, 2))\n"
    "        pdf.savefig(fig)\n"
)
# 纯 OO（脚本从不 import pyplot）也能用 PdfPages：认领不能放在「pyplot 已载入」的分支里
OO_ONLY = (
    "import matplotlib\nmatplotlib.use('Agg')\n"
    "from matplotlib.figure import Figure\n" + PDF + "with PdfPages('oo.pdf') as pdf:\n"
    "    for i in range(2):\n"
    "        fig = Figure(figsize=(3, 2))\n"
    "        fig.add_subplot(1, 1, 1)\n"
    "        pdf.savefig(fig)\n"
)


def _run(user_python, tmp_path, source: str, name="fig.py"):
    proj = tmp_path / "proj"
    write(proj / name, source)
    report = tmp_path / "report.json"
    run_runner(
        user_python,
        bridge.RUNNER_PY,
        target=proj / name,
        cwd=str(proj),
        report=report,
        out_dir=tmp_path / "out",
        argv=(),
    )
    return json.loads(report.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "body", [CLOSE_EACH, CLOSE_ALL_END, KEEP_OPEN], ids=["each", "all_end", "open"]
)
def test_native_captures_one_stem_per_page_named_after_the_pdf(user_python, tmp_path, body):
    data = _run(user_python, tmp_path, HEAD + body)
    assert [f["stem"] for f in data["figures"]] == ["all_samples", "all_samples-2", "all_samples-3"]
    # 来源 pyplot：磁盘上那份多页 PDF 不是这些图的原件（写回不得指向它）
    assert {f["capture_source"] for f in data["figures"]} == {"pyplot"}
    (summary,) = data["pdf_pages"]
    assert (summary["name"], summary["pages"], summary["captured"]) == ("all_samples.pdf", 3, 3)


def test_native_oo_script_without_pyplot_is_captured(user_python, tmp_path):
    data = _run(user_python, tmp_path, OO_ONLY)
    assert [f["stem"] for f in data["figures"]] == ["oo", "oo-2"]
    assert {f["capture_source"] for f in data["figures"]} == {"pyplot"}


def test_native_real_pdf_is_still_written_untouched(user_python, tmp_path):
    _run(user_python, tmp_path, HEAD + CLOSE_EACH)
    pdf = tmp_path / "proj" / "all_samples.pdf"
    assert len(re.findall(rb"/Type /Page(?!s)", pdf.read_bytes())) == 3
