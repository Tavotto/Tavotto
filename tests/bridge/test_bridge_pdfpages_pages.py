"""native bridge 也把写进 `PdfPages` 的每一页记成那份 PDF 路径的一次 occurrence（#1370 PR2，三条入口同源）。

规则唯一实现 `figcapture.SavefigObservations.record` 的 PdfPages 分支；safe worker / 浏览器的对应用例与写回守卫在
`tests/test_pdfpages_record_pages.py`。native 与 safe 相反：PDF 真写盘（透传），记录补的是 `saved`
（写失败补 `failed`），而不是 `intercepted`。
"""

from __future__ import annotations

import json

import pytest

from support.bridgekit import run_runner, write
from tavotto.engine import bridge

pytestmark = pytest.mark.usefixtures("clean_env")

HEAD = "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
PDF = "from matplotlib.backends.backend_pdf import PdfPages\n"
CLOSE_EACH = (
    HEAD
    + PDF
    + (
        "with PdfPages('all_samples.pdf') as pdf:\n"
        "    for i in range(3):\n"
        "        fig = plt.figure(figsize=(3, 2))\n"
        "        pdf.savefig(fig)\n"
        "        plt.close(fig)\n"
    )
)

# 与 tests/test_pdfpages_record_pages.py 的 PAGE_ROWS 逐字相同（三条入口同源）
PAGE_ROWS = [
    {"pdf_page": 1, "path": "all_samples.pdf", "figure_ordinal": 1, "stem": None, "format": "pdf"},
    {"pdf_page": 2, "path": "all_samples.pdf", "figure_ordinal": 2, "stem": None, "format": "pdf"},
    {"pdf_page": 3, "path": "all_samples.pdf", "figure_ordinal": 3, "stem": None, "format": "pdf"},
]


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


def _rows(report: dict) -> list[dict]:
    return [
        {
            "pdf_page": r["pdf_page"],
            "path": r["destination"]["path"],
            "figure_ordinal": r["figure_ordinal"],
            "stem": r["stem"],
            "format": r["options"]["format"],
        }
        for r in report["records"]
        if "pdf_page" in r
    ]


def test_native_records_each_page_with_its_number(user_python, tmp_path):
    data = _run(user_python, tmp_path, CLOSE_EACH)
    obs = data["savefig_observations"]
    assert _rows(obs) == PAGE_ROWS
    assert {r["result"] for r in obs["records"]} == {"saved"}
    assert {r["destination"]["scope"] for r in obs["records"]} <= {"project", "execution"}
    assert obs["complete"] is True and obs["observed_count"] == 0
    (summary,) = data["pdf_pages"]
    assert (summary["path"], summary["complete"]) == ("all_samples.pdf", True)
    assert summary["scope"] in ("project", "execution")  # native 的 cwd 就是执行目录
    assert summary["page_stems"] == ["all_samples", "all_samples-2", "all_samples-3"]


def test_native_failed_page_is_recorded_failed_and_the_exception_passes_through(
    user_python, tmp_path
):
    src = (
        HEAD
        + PDF
        + (
            "fig = plt.figure(figsize=(2, 2))\n"
            "with PdfPages('p.pdf') as pdf:\n"
            "    pdf.savefig(fig)\n"
            "    try:\n"
            "        pdf.savefig(fig, bbox_inches='not-a-valid-value')\n"
            "    except Exception as exc:\n"
            "        print('raised', type(exc).__name__)\n"
        )
    )
    data = _run(user_python, tmp_path, src)
    records = [r for r in data["savefig_observations"]["records"] if "pdf_page" in r]
    assert [(r["pdf_page"], r["result"]) for r in records] == [(1, "saved"), (2, "failed")]
    (summary,) = data["pdf_pages"]
    # 写失败的那次尝试没有这一页：摘要的页数是成功的，观察多出一次尝试 = 不完整，如实说
    assert summary["pages"] == 1 and summary["complete"] is False


def test_native_unresolved_destination_is_not_leaked(user_python, tmp_path):
    outside = tmp_path / "secret_outside_dir"
    outside.mkdir()
    src = (
        HEAD
        + PDF
        + (f"with PdfPages({str(outside / 'o.pdf')!r}) as pdf:\n    pdf.savefig(plt.figure())\n")
    )
    data = _run(user_python, tmp_path, src)
    (row,) = [r for r in data["savefig_observations"]["records"] if "pdf_page" in r]
    assert row["destination"] == {"scope": "unresolved", "path": None}
    assert "secret_outside_dir" not in json.dumps([data["savefig_observations"], data["pdf_pages"]])
