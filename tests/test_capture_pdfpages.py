"""写进 `PdfPages` 的图也要捕获（#1370 PR1，Compatibility Guardian 的 PdfPages 家族）。

`pdf.savefig(fig)` 最终调的是 `fig.savefig(<PdfPages>, format="pdf", backend="pdf")`，第一个参数不是路径，
stem 取不出来；逐页 `plt.close(fig)` 的脚本跑完一张不剩，以前 safe worker 报 `script_no_figure`。
现在保存那一刻记进有界账本（`figcapture.PdfPagesLedger`），脚本结束先于 pyplot 兜底认领：
stem 取 PDF 文件名（`all_samples`、`all_samples-2`…），来源 pyplot——**多页 PDF 不是任何一张图的原件**。

safe worker 真起子进程（`pool.one_shot`）；浏览器侧 spawn 同一个解释器跑 `engine/browser.py`
（与 `test_compat_capture_parity.py` 同一条纪律）；native 在 `tests/bridge/test_bridge_pdfpages.py`。
"""

from __future__ import annotations

import io
import sys

import pytest

from tavotto.engine import figcapture
from test_compat_capture_parity import browser_load, desktop_build, write

CLOSE_EACH = """\
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

with PdfPages("all_samples.pdf") as pdf:
    for i in range(3):
        fig, ax = plt.subplots(figsize=(3.0, 2.0))
        ax.plot([0, 1, 2], [i, i + 1, i * 2])
        ax.set_title(f"sample {i}")
        pdf.savefig(fig)
        plt.close(fig)
"""

CLOSE_ALL_END = """\
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

with PdfPages("all_samples.pdf") as pdf:
    for i in range(3):
        fig, ax = plt.subplots(figsize=(3.0, 2.0))
        ax.set_title(f"sample {i}")
        pdf.savefig()
plt.close("all")
"""

KEEP_OPEN = """\
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

with PdfPages("all_samples.pdf") as pdf:
    for i in range(3):
        fig, ax = plt.subplots(figsize=(3.0, 2.0))
        ax.set_title(f"sample {i}")
        pdf.savefig(fig)
"""


def stems_of(resp: dict) -> list[str]:
    return list(resp["stems"])


@pytest.mark.parametrize(
    "source", [CLOSE_EACH, CLOSE_ALL_END, KEEP_OPEN], ids=["each", "all_end", "open"]
)
def test_all_three_writing_styles_capture_one_stem_per_page(tmp_path, source):
    write(tmp_path / "figs", "repro.py", source)
    resp = desktop_build(tmp_path / "figs", "repro.py")
    assert stems_of(resp) == ["all_samples", "all_samples-2", "all_samples-3"]
    assert {v["source"] for v in resp["stems"].values()} == {"pyplot"}
    (summary,) = resp["pdf_pages"]
    assert summary == {
        "name": "all_samples.pdf",
        "pages": 3,
        "captured": 3,
        "stems": ["all_samples", "all_samples-2", "all_samples-3"],
    }


def test_pages_keep_page_order_titles(tmp_path):
    write(tmp_path / "figs", "repro.py", CLOSE_EACH)
    resp = desktop_build(tmp_path / "figs", "repro.py")
    titles = [d["stem"] for d in resp["descriptors"]]
    assert titles == ["all_samples", "all_samples-2", "all_samples-3"]


def test_same_figure_written_to_many_pages_is_claimed_once(tmp_path):
    src = """\
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

fig, ax = plt.subplots(figsize=(3.0, 2.0))
with PdfPages("one.pdf") as pdf:
    for _ in range(4):
        pdf.savefig(fig)
plt.close(fig)
"""
    write(tmp_path / "figs", "repro.py", src)
    resp = desktop_build(tmp_path / "figs", "repro.py")
    assert stems_of(resp) == ["one"]
    assert resp["pdf_pages"] == [{"name": "one.pdf", "pages": 4, "captured": 1, "stems": ["one"]}]


def test_ledger_limit_counts_the_rest_and_says_so():
    class Page:
        pass

    ledger = figcapture.PdfPagesLedger(limit=2)
    pdf = object()
    ledger._group(pdf)  # 名字取不到（不是 PdfPages）→ 退回脚本名
    figs = [Page() for _ in range(5)]
    for fig in figs:
        ledger.note(pdf, fig)
    capture: dict = {}
    source: dict = {}
    added = figcapture.adopt_pdf_pages(capture, source, ledger, "repro")
    assert added == ["repro", "repro-2"]
    assert source == {"repro": "pyplot", "repro-2": "pyplot"}
    assert ledger.omitted == 3
    (summary,) = ledger.summary()
    assert (summary["pages"], summary["captured"], summary["omitted"]) == (5, 2, 3)


def test_over_the_default_limit_in_a_real_worker(tmp_path):
    n = figcapture.MAX_PDF_PAGES_LEDGER + 6
    src = f"""\
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

with PdfPages("big.pdf") as pdf:
    for i in range({n}):
        fig = plt.figure(figsize=(1.0, 1.0))
        pdf.savefig(fig)
        plt.close(fig)
"""
    write(tmp_path / "figs", "repro.py", src)
    resp = desktop_build(tmp_path / "figs", "repro.py")
    assert len(resp["stems"]) == figcapture.MAX_PDF_PAGES_LEDGER
    (summary,) = resp["pdf_pages"]
    assert (summary["pages"], summary["captured"], summary["omitted"]) == (
        n,
        figcapture.MAX_PDF_PAGES_LEDGER,
        6,
    )


def test_bytesio_target_falls_back_to_script_stem_and_has_no_file_name(tmp_path):
    src = """\
import io
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

buf = io.BytesIO()
with PdfPages(buf) as pdf:
    for i in range(2):
        fig = plt.figure(figsize=(2.0, 2.0))
        pdf.savefig(fig)
        plt.close(fig)
"""
    write(tmp_path / "figs", "repro.py", src)
    resp = desktop_build(tmp_path / "figs", "repro.py")
    assert stems_of(resp) == ["repro", "repro-2"]
    (summary,) = resp["pdf_pages"]
    assert summary["name"] is None and summary["captured"] == 2


def test_two_pdfs_with_the_same_name_continue_numbering(tmp_path):
    src = """\
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

for _ in range(2):
    with PdfPages("same.pdf") as pdf:
        for i in range(2):
            fig = plt.figure(figsize=(2.0, 2.0))
            pdf.savefig(fig)
            plt.close(fig)
"""
    write(tmp_path / "figs", "repro.py", src)
    resp = desktop_build(tmp_path / "figs", "repro.py")
    assert stems_of(resp) == ["same", "same-2", "same-3", "same-4"]


def test_savefig_claimed_figure_is_not_claimed_again_by_the_pdf(tmp_path):
    src = """\
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

fig = plt.figure(figsize=(2.0, 2.0))
fig.savefig("single.png")
with PdfPages("deck.pdf") as pdf:
    pdf.savefig(fig)
plt.close(fig)
"""
    write(tmp_path / "figs", "repro.py", src)
    resp = desktop_build(tmp_path / "figs", "repro.py")
    assert stems_of(resp) == ["single"]
    assert resp["stems"]["single"]["source"] == "savefig"
    assert "pdf_pages" not in resp


def test_failed_page_write_is_not_recorded(tmp_path):
    src = """\
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

good = plt.figure(figsize=(2.0, 2.0))
with PdfPages("p.pdf") as pdf:
    pdf.savefig(good)
    try:
        pdf.savefig(good, bbox_inches="not-a-valid-value")
    except Exception:
        pass
"""
    write(tmp_path / "figs", "repro.py", src)
    resp = desktop_build(tmp_path / "figs", "repro.py")
    assert stems_of(resp) == ["p"]
    assert resp["pdf_pages"][0]["pages"] == 1


def test_pdf_pages_never_become_the_writeback_original(tmp_path):
    """多页 PDF 就躺在项目目录里，也不是这些图的原件：来源 pyplot、`original_artifact` 恒 None。"""
    figs = tmp_path / "figs"
    write(figs, "repro.py", CLOSE_EACH)
    (figs / "all_samples.pdf").write_bytes(b"%PDF-1.4 decoy")
    for name in ("all_samples", "all_samples-2", "all_samples-3"):
        (figs / f"{name}.png").write_bytes(b"decoy")
    resp = desktop_build(figs, "repro.py")
    assert {v["source"] for v in resp["stems"].values()} == {"pyplot"}
    descriptors = resp["descriptors"]
    assert len(descriptors) == 3
    assert {d["capture_source"] for d in descriptors} == {"pyplot"}
    assert [d["original_artifact"] for d in descriptors] == [None, None, None]
    assert all(not d.get("artifact_files") for d in descriptors)


def test_matplotlib_still_has_the_private_filename_we_read(tmp_path):
    """文件名取法依赖 `PdfPages._filename`（3.8–3.11 都有）；CI 的每个 matplotlib 版本上它仍在。"""
    from matplotlib.backends.backend_pdf import PdfPages

    path = tmp_path / "named.pdf"
    pdf = PdfPages(str(path))
    try:
        assert figcapture.pdf_pages_filename(pdf) == str(path)
        assert figcapture.pdf_pages_filename(PdfPages(path)) == str(path)  # PathLike
    finally:
        pdf.close()
    buf = PdfPages(io.BytesIO())
    assert figcapture.pdf_pages_filename(buf) is None
    assert figcapture.pdf_pages_filename(object()) is None


def test_is_pdf_pages_does_not_import_matplotlib():
    """账本判型只看已载入的模块：没载入过 backend_pdf 就不可能有 PdfPages 实例。"""
    saved = sys.modules.pop("matplotlib.backends.backend_pdf", None)
    try:
        assert figcapture.is_pdf_pages(object()) is False
        assert "matplotlib.backends.backend_pdf" not in sys.modules
    finally:
        if saved is not None:
            sys.modules["matplotlib.backends.backend_pdf"] = saved


def test_descriptor_version_was_bumped_for_the_new_stem_rule():
    assert figcapture.DESCRIPTOR_VERSION >= 2


class TestBrowserSameAnswer:
    """playground 与 safe worker 对同一份脚本给同一组 stem（三条入口同源）。"""

    @pytest.mark.parametrize(
        "source", [CLOSE_EACH, CLOSE_ALL_END, KEEP_OPEN], ids=["each", "all_end", "open"]
    )
    def test_browser_captures_the_same_stems(self, tmp_path, source):
        resp = browser_load(source, "repro.py", tmp_path / "ws")
        assert [f["stem"] for f in resp["figures"]] == [
            "all_samples",
            "all_samples-2",
            "all_samples-3",
        ]
        assert {d["capture_source"] for d in resp["descriptors"]} == {"pyplot"}
        assert [d["original_artifact"] for d in resp["descriptors"]] == [None] * 3


def test_more_pdfs_than_the_file_limit_report_the_omitted_ones():
    """第 33 份单页 PDF 没有组可挂：只捕获 32 张，省略数必须是 1（不能报 0）。"""
    ledger = figcapture.PdfPagesLedger()
    pdfs = [object() for _ in range(figcapture.MAX_PDF_PAGES_FILES + 1)]
    for pdf in pdfs:
        ledger.note(pdf, object())
    capture: dict = {}
    added = figcapture.adopt_pdf_pages(capture, {}, ledger, "repro")
    assert len(added) == figcapture.MAX_PDF_PAGES_FILES == len(capture)
    assert ledger.omitted == 1
    summary = ledger.summary()
    assert sum(item.get("omitted", 0) for item in summary) == 1
    assert summary[-1] == {"name": None, "pages": 1, "captured": 0, "stems": [], "omitted": 1}


def test_probe_result_carries_pdf_pages(tmp_path):
    """worker → `probe.probe()` 的成功响应是逐字段构造的，`pdf_pages` 必须被带出来（前端胶囊靠它）。"""
    from tavotto.engine import probe as engine_probe

    figs = tmp_path / "figs"
    write(figs, "repro.py", CLOSE_EACH)
    from tavotto.engine import pool as engine_pool

    try:
        result = engine_probe.probe(str(figs), "repro.py")
        plain = None
        write(figs, "plain.py", "import matplotlib.pyplot as plt\nplt.plot([1])\nplt.show()\n")
        plain = engine_probe.probe(str(figs), "plain.py")
    finally:
        engine_pool.shutdown_all(str(figs), wait=True)
    assert result["error"] is None
    assert result["pdf_pages"] == [
        {
            "name": "all_samples.pdf",
            "pages": 3,
            "captured": 3,
            "stems": ["all_samples", "all_samples-2", "all_samples-3"],
        }
    ]
    # 没写多页 PDF 的脚本：字段不出现
    assert "pdf_pages" not in plain
