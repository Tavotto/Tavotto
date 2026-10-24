"""写进 `PdfPages` 的每一页记成「那份 PDF 路径」的一次 occurrence（带页号），并拒绝把图写回多页 PDF（#1370 PR2）。

两件事，一个主语（多页 PDF 不是任何图的 savefig 原件）：

1. **记页**（ADR 0113 补充条款）：`figcapture.SavefigObservations.record` 认得 `PdfPages`——目的地解析规则与 savefig
   完全相同（调用当刻的 cwd，scope 取 project / execution / unresolved，根外路径不外发），带页号；缓冲区目标不记。
   build 响应的 `pdf_pages` 摘要加 `path` / `scope` / `page_stems` / `complete`（加字段，不升协议版本，ADR 0003 §1）。
   safe worker / 浏览器在这里；native bridge 在 `tests/bridge/test_bridge_pdfpages_pages.py`，两边钉同一份期望。
2. **写回守卫**（`app._own_write_back_targets` / `_refuse_pdf_pages_source`）：这次运行里被 PdfPages 写过的路径，对所有 stem
   都算别人的产物——`src` 是它 → 409 `write_back_multipage_pdf`，在起一次性 worker / 建任何临时文件之前、原件零改动；
   同目录的普通 savefig 原件照常写回。

safe 档的 PdfPages 真写盘（透传）但跑在沙盒里，所以 scope 是 `execution`；记录的是 `intercepted`。
"""

from __future__ import annotations

import json

import pytest

from tavotto import app as m
from tavotto.engine import figcapture, pool
from test_capture_pdfpages import CLOSE_EACH, KEEP_OPEN
from test_compat_capture_parity import browser_load, desktop_build, write

# 三条入口同一份期望：页号 / 路径 / 图序 / 格式。scope 因入口而异（safe 与浏览器在沙盒里 = execution），不比
PAGE_ROWS = [
    {"pdf_page": 1, "path": "all_samples.pdf", "figure_ordinal": 1, "stem": None, "format": "pdf"},
    {"pdf_page": 2, "path": "all_samples.pdf", "figure_ordinal": 2, "stem": None, "format": "pdf"},
    {"pdf_page": 3, "path": "all_samples.pdf", "figure_ordinal": 3, "stem": None, "format": "pdf"},
]


def page_rows(report: dict) -> list[dict]:
    """观察报告里 PdfPages 的那些记录，压成与入口无关的形状（供三条入口对拍）。"""
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


# --------------------------------------------------------------------------- 记页：safe / 浏览器
@pytest.mark.parametrize("source", [CLOSE_EACH, KEEP_OPEN], ids=["close_each", "open"])
def test_safe_worker_records_each_page_with_its_number(tmp_path, source):
    write(tmp_path / "figs", "repro.py", source)
    resp = desktop_build(tmp_path / "figs", "repro.py")
    report = resp["savefig_observations"]
    assert page_rows(report) == PAGE_ROWS
    assert {r["destination"]["scope"] for r in report["records"]} == {"execution"}
    assert {r["result"] for r in report["records"]} == {"intercepted"}
    # 次序号唯一且递增；savefig 自己的预算（complete / observed_count）不被页占用
    assert [r["occurrence"] for r in report["records"]] == [1, 2, 3]
    assert report["complete"] is True and report["observed_count"] == 0
    (summary,) = resp["pdf_pages"]
    assert (summary["path"], summary["scope"], summary["complete"]) == (
        "all_samples.pdf",
        "execution",
        True,
    )
    assert summary["page_stems"] == ["all_samples", "all_samples-2", "all_samples-3"]


def test_browser_records_the_same_pages(tmp_path):
    resp = browser_load(CLOSE_EACH, "repro.py", tmp_path / "ws")
    assert page_rows(resp["savefig_observations"]) == PAGE_ROWS
    (summary,) = resp["pdf_pages"]
    assert (summary["path"], summary["complete"]) == ("all_samples.pdf", True)
    assert summary["page_stems"] == ["all_samples", "all_samples-2", "all_samples-3"]


def test_same_figure_on_many_pages_repeats_its_stem(tmp_path):
    src = """\
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

fig, ax = plt.subplots(figsize=(3.0, 2.0))
with PdfPages("one.pdf") as pdf:
    for _ in range(3):
        pdf.savefig(fig)
"""
    write(tmp_path / "figs", "repro.py", src)
    resp = desktop_build(tmp_path / "figs", "repro.py")
    rows = page_rows(resp["savefig_observations"])
    assert [(r["pdf_page"], r["figure_ordinal"]) for r in rows] == [(1, 1), (2, 1), (3, 1)]
    assert resp["pdf_pages"][0]["page_stems"] == ["one", "one", "one"]


def test_two_pdf_files_number_their_pages_independently(tmp_path):
    src = """\
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

a, b = plt.figure(figsize=(2, 2)), plt.figure(figsize=(2, 2))
with PdfPages("a.pdf") as pa, PdfPages("sub_b.pdf") as pb:
    pa.savefig(a)
    pb.savefig(b)
    pa.savefig(b)
"""
    write(tmp_path / "figs", "repro.py", src)
    resp = desktop_build(tmp_path / "figs", "repro.py")
    rows = [
        (r["pdf_page"], r["destination"]["path"]) for r in resp["savefig_observations"]["records"]
    ]
    assert rows == [(1, "a.pdf"), (1, "sub_b.pdf"), (2, "a.pdf")]


def test_page_is_interleaved_with_savefig_in_one_occurrence_sequence(tmp_path):
    src = """\
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

fig = plt.figure(figsize=(2, 2))
fig.savefig("single.png")
with PdfPages("deck.pdf") as pdf:
    pdf.savefig(fig)
fig.savefig("single.pdf")
"""
    write(tmp_path / "figs", "repro.py", src)
    resp = desktop_build(tmp_path / "figs", "repro.py")
    rows = resp["savefig_observations"]["records"]
    assert [(r["occurrence"], r["destination"]["path"], r.get("pdf_page")) for r in rows] == [
        (1, "single.png", None),
        (2, "deck.pdf", 1),
        (3, "single.pdf", None),
    ]
    # 同一张图：被 savefig 认领的 stem 就是它在 PDF 里那一页的 stem
    (summary,) = resp["pdf_pages"]
    assert (summary["captured"], summary["page_stems"]) == (0, ["single"])


def test_buffer_target_is_not_recorded(tmp_path):
    src = """\
import io
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

with PdfPages(io.BytesIO()) as pdf:
    for _ in range(2):
        fig = plt.figure(figsize=(2, 2))
        pdf.savefig(fig)
        plt.close(fig)
"""
    write(tmp_path / "figs", "repro.py", src)
    resp = desktop_build(tmp_path / "figs", "repro.py")
    assert page_rows(resp["savefig_observations"]) == []
    (summary,) = resp["pdf_pages"]
    assert (summary["name"], summary["path"], summary["scope"]) == (None, None, "unresolved")
    assert summary["page_stems"] == ["repro", "repro-2"] and summary["complete"] is True


def test_destination_outside_every_root_is_unresolved_and_never_leaked(tmp_path):
    outside = tmp_path / "elsewhere_secret_dir"
    outside.mkdir()
    src = f"""\
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

with PdfPages({str(outside / "out.pdf")!r}) as pdf:
    fig = plt.figure(figsize=(2, 2))
    pdf.savefig(fig)
"""
    write(tmp_path / "figs", "repro.py", src)
    resp = desktop_build(tmp_path / "figs", "repro.py")
    (row,) = [r for r in resp["savefig_observations"]["records"] if "pdf_page" in r]
    assert row["destination"] == {"scope": "unresolved", "path": None}
    (summary,) = resp["pdf_pages"]
    assert (summary["path"], summary["scope"]) == (None, "unresolved")
    public = json.dumps([resp["savefig_observations"], resp["pdf_pages"]])
    assert "elsewhere_secret_dir" not in public and str(tmp_path) not in public


def test_a_file_named_without_extension_is_unresolved_like_savefig(tmp_path):
    """PdfPages 恒写 pdf，但文件名没后缀时目的地判据与 savefig 一样不猜（同一个 `_destination`）。"""
    src = """\
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

with PdfPages("noext") as pdf:
    pdf.savefig(plt.figure(figsize=(2, 2)))
"""
    write(tmp_path / "figs", "repro.py", src)
    resp = desktop_build(tmp_path / "figs", "repro.py")
    assert resp["pdf_pages"][0]["scope"] == "execution"  # 显式 format=pdf，路径是沙盒里的 noext


def test_page_records_have_their_own_budget_and_say_so():
    """页的观察上限（= 账本上限）与 savefig 的 128 分开：多出来的页让这份 PDF 的 `complete` 为假，不让 savefig 的观察变不完整。"""
    import io

    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.figure import Figure

    obs = figcapture.SavefigObservations(metadata=lambda fig: ([2.0, 2.0], 100.0, 100.0))
    pdf = PdfPages(io.BytesIO())
    pdf._filename = "big.pdf"  # 给缓冲区的 PdfPages 配个文件名（只为走记页分支，不写盘）
    ledger = figcapture.PdfPagesLedger()
    fig = Figure()
    n = figcapture.MAX_PDF_PAGES_LEDGER + 3
    for _ in range(n):
        obs.record(fig, pdf, "", None)
        ledger.note(pdf, fig)
    report = obs.report()
    assert len(report["records"]) == figcapture.MAX_PDF_PAGES_LEDGER
    assert report["complete"] is True and report["observed_count"] == 0
    facts = obs.pdf_file_facts(pdf)
    assert facts["complete"] is False and facts["recorded"] == figcapture.MAX_PDF_PAGES_LEDGER
    capture: dict = {}
    figcapture.adopt_pdf_pages(capture, {}, ledger, "repro")
    (summary,) = ledger.summary(obs)
    assert summary["pages"] == n and summary["complete"] is False
    pdf.close()


def test_pdf_page_records_are_never_a_saved_original(tmp_path):
    """`saved_originals`（只读降级依据）不认 PdfPages 的页：多页 PDF 不是任何一张图的原件。"""
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.figure import Figure

    out = tmp_path / "x.pdf"
    obs = figcapture.SavefigObservations(
        project_root=tmp_path, metadata=lambda fig: ([2.0, 2.0], 100.0, 100.0)
    )
    fig = Figure()
    with PdfPages(out) as pdf:
        index = obs.record(fig, pdf, "", None)
        fig.savefig(pdf, format="pdf")
        obs.complete(index, "saved")
    assert obs.report()["records"][0]["pdf_page"] == 1
    assert obs.saved_originals(fig) == []


# --------------------------------------------------------------------------- 写回守卫：真 worker 的摘要
SCRIPT_PNG_PLUS_PAGES = """\
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

fig, ax = plt.subplots(figsize=(2.4, 1.6))
ax.plot([0, 1], [0, 1])
fig.savefig("Fig.png", dpi=100)
with PdfPages("Fig.pdf") as pdf:
    for i in range(3):
        pdf.savefig(fig)
plt.close(fig)
"""


def test_real_worker_summary_drives_the_guard(tmp_path):
    figs = tmp_path / "figs"
    write(figs, "repro.py", SCRIPT_PNG_PLUS_PAGES)
    (figs / "Fig.png").write_bytes(b"\x89PNG")
    (figs / "Fig.pdf").write_bytes(b"%PDF-multipage")
    w = pool.one_shot("repro.py", str(figs), "__main__")
    try:
        w.ensure_built()
        assert [(p["path"], p["pages"], p["scope"]) for p in w.last_build_pdf_pages] == [
            ("Fig.pdf", 3, "execution")
        ]
        png, pdf = figs / "Fig.png", figs / "Fig.pdf"
        # 自己的 PNG 原件照写；同目录的多页 PDF 不是写回目标
        assert m._own_write_back_targets(w, "Fig", png, [png, pdf]) == [png]
        # src 是多页 PDF：对所有 stem 一视同仁地拒绝（包括认领了它某一页的 stem）
        for stem in ("Fig", "Fig-2"):
            with pytest.raises(m.WriteBackMultipagePdfError):
                m._own_write_back_targets(w, stem, pdf, [png, pdf])
    finally:
        pool.discard(w)


def test_single_page_pdf_pages_is_refused_too(tmp_path):
    """只写了 1 页的 PdfPages 同样不是任何图的 savefig 原件：覆盖它抹掉的是脚本的整份输出。"""
    figs = tmp_path / "figs"
    write(
        figs,
        "repro.py",
        SCRIPT_PNG_PLUS_PAGES.replace("range(3)", "range(1)"),
    )
    w = pool.one_shot("repro.py", str(figs), "__main__")
    try:
        w.ensure_built()
        assert [p["pages"] for p in w.last_build_pdf_pages] == [1]
        with pytest.raises(m.WriteBackMultipagePdfError):
            m._own_write_back_targets(w, "Fig", figs / "Fig.pdf", [figs / "Fig.pdf"])
    finally:
        pool.discard(w)


def test_unresolved_pdf_path_gives_the_guard_nothing_to_match(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    figs = tmp_path / "figs"
    write(
        figs, "repro.py", SCRIPT_PNG_PLUS_PAGES.replace('"Fig.pdf"', repr(str(outside / "Fig.pdf")))
    )
    (figs / "Fig.pdf").write_bytes(b"%PDF-user")
    w = pool.one_shot("repro.py", str(figs), "__main__")
    try:
        w.ensure_built()
        (item,) = w.last_build_pdf_pages
        assert (item["scope"], item["path"]) == ("unresolved", None)
        pdf = figs / "Fig.pdf"
        # 根外的文件不是项目里的同名位置：守卫不认（也不会因此误拦项目里的 Fig.pdf）
        assert m._own_write_back_targets(w, "Fig", figs / "Fig.png", [figs / "Fig.png", pdf]) == [
            figs / "Fig.png",
            pdf,
        ]
    finally:
        pool.discard(w)


# --------------------------------------------------------------------------- 写回守卫：端到端 HTTP（假 worker）
@pytest.fixture
def client(tmp_path, monkeypatch):
    from tavotto.engine import project_watch

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    yield m.app.test_client()
    m.reset_projects()
    project_watch.stop()


MULTI = [
    {
        "name": "Fig1.pdf",
        "pages": 3,
        "captured": 3,
        "stems": [],
        "path": "Fig1.pdf",
        "scope": "execution",
    }
]


def _pair(tmp_path, monkeypatch, *, hot_pages=None, fresh_pages=None):
    import test_write_back as wb

    figs = wb._figs(tmp_path)
    hot, fresh = wb._pair(figs, tmp_path)
    hot.last_build_pdf_pages = list(hot_pages or [])
    fresh.last_build_pdf_pages = list(fresh_pages or [])
    discarded = wb._use(monkeypatch, hot, fresh)
    return figs, hot, fresh, discarded, wb


def test_write_back_to_a_multipage_pdf_is_refused_before_any_replay(client, tmp_path, monkeypatch):
    """热会话 build 已经知道它是 PdfPages 写的：连一次性 worker 都不起（不必把脚本全量重放一遍），零临时文件。"""
    figs, hot, fresh, discarded, wb = _pair(tmp_path, monkeypatch, hot_pages=MULTI)
    before = {p: (figs / p).read_bytes() for p in ("Fig1.pdf", "Fig1.png")}
    resp = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 409, resp.get_json()
    body = resp.get_json()
    assert body["code"] == "write_back_multipage_pdf" and body["params"] == {"file": "Fig1.pdf"}
    assert {p: (figs / p).read_bytes() for p in before} == before
    assert wb._leftovers(figs) == []
    assert discarded == [] and not fresh.calls, "重放没起，一个导出都没有"


def test_write_back_to_a_multipage_pdf_is_refused_after_replay_without_temp_files(
    client, tmp_path, monkeypatch
):
    """热会话没有摘要（老会话）时第二道：重放后的描述符 / 摘要一到，仍在导出任何临时文件之前拒绝。"""
    figs, hot, fresh, discarded, wb = _pair(tmp_path, monkeypatch, fresh_pages=MULTI)
    before = {p: (figs / p).read_bytes() for p in ("Fig1.pdf", "Fig1.png")}
    resp = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 409, resp.get_json()
    assert resp.get_json()["code"] == "write_back_multipage_pdf"
    assert {p: (figs / p).read_bytes() for p in before} == before
    assert wb._leftovers(figs) == []
    assert discarded == [fresh]
    assert "pdf" not in fresh.calls and "png" not in fresh.calls, "一个临时文件都不该导出"


def test_write_back_of_a_plain_savefig_original_next_to_a_multipage_pdf_still_works(
    client, tmp_path, monkeypatch
):
    figs, hot, fresh, _, wb = _pair(tmp_path, monkeypatch, hot_pages=MULTI, fresh_pages=MULTI)
    before_pdf = (figs / "Fig1.pdf").read_bytes()
    resp = client.post("/api/engine/update_source", json={"id": "Fig1.png", "patches": []})
    assert resp.status_code == 200, resp.get_json()
    assert (figs / "Fig1.png").read_bytes() == b"NEW-png"
    assert (figs / "Fig1.pdf").read_bytes() == before_pdf, "多页 PDF 不许被覆盖"
    assert "pdf" not in fresh.calls


def test_write_back_unaffected_when_the_script_wrote_no_pdf_pages(client, tmp_path, monkeypatch):
    figs, hot, fresh, _, wb = _pair(tmp_path, monkeypatch)
    resp = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 200, resp.get_json()
    assert (figs / "Fig1.pdf").read_bytes() == b"NEW-pdf"


ANNOTATION = [{"type": "text", "text": "a", "x_mm": 1, "y_mm": 1, "size_pt": 8, "color": "#000000"}]


def test_annotated_png_write_back_beside_a_multipage_pdf_is_a_controlled_409(
    client, tmp_path, monkeypatch
):
    """Codex #1394 P2：PNG 是自己的原件、同 stem 的 PDF 是 PdfPages 写的。带标注写回要先画在 PDF 上，
    守卫把 PDF 去掉后没有载体——以前走到 `assert pdf_tmp is not None` 成 500；现在是受控 409，不建任何临时文件，
    PNG / PDF 都零改动（不能只写 PNG 而丢标注：前端写回成功后会把标注对象从画布摘掉）。"""
    figs, hot, fresh, discarded, wb = _pair(tmp_path, monkeypatch, fresh_pages=MULTI)
    before = {p: (figs / p).read_bytes() for p in ("Fig1.pdf", "Fig1.png")}
    resp = client.post(
        "/api/engine/update_source",
        json={"id": "Fig1.png", "patches": [], "annotations": ANNOTATION},
    )
    assert resp.status_code == 409, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["code"] == "write_back_multipage_pdf" and body["params"] == {"file": "Fig1.pdf"}
    assert {p: (figs / p).read_bytes() for p in before} == before
    assert wb._leftovers(figs) == []
    assert "pdf" not in fresh.calls and "png" not in fresh.calls, "一个临时文件都不该导出"
    assert discarded == [fresh]


def test_annotated_png_write_back_beside_another_figures_pdf_is_a_controlled_409_too(
    client, tmp_path, monkeypatch
):
    """同一处断言的另一条路：PDF 是同脚本里另一张图的原件（`write_back_foreign_file`），以前同样 500。"""
    import test_write_back as wb

    figs = wb._figs(tmp_path)
    hot, fresh = wb._pair(figs, tmp_path)
    fresh.last_build_descriptors = [
        {"stem": "Fig1", "original_artifact": "Fig1.png", "savefig_calls": [{"format": "png"}]},
        {"stem": "Fig1-2", "original_artifact": "Fig1.pdf", "savefig_calls": [{"format": "pdf"}]},
    ]
    wb._use(monkeypatch, hot, fresh)
    resp = client.post(
        "/api/engine/update_source",
        json={"id": "Fig1.png", "patches": [], "annotations": ANNOTATION},
    )
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert resp.get_json()["code"] == "write_back_foreign_file"
    assert wb._leftovers(figs) == []
    assert "png" not in fresh.calls
