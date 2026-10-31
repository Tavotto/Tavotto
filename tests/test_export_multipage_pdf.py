"""把若干张图合成一个多页 PDF（#1370 PR3）：`merge_pdf` 请求、`pdfbackend.concat_pdf`、`/api/export` 端到端。

主语：**导出出来的那个文件**——页数、页序、每页的尺寸与文字（用 pymupdf 读回），不是请求的形状。
每一页先走现有的单页 PDF 导出路径，所以这里不重测单页导出本身（`test_export_pipeline.py`），
只钉合并带来的东西：页序跟请求走、重复页、非法组合、失败不交付半成品、单页导出不变。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m, pdfbackend
from tavotto.engine import exportjob, exportreq

# 每张图尺寸、文字都不同：页序错了、少了一页、页被复制成别的页，下面的读回都会不一样
PAGES = {
    "s0.pdf": (200.0, 150.0, "SampleZero"),
    "s1.pdf": (180.0, 120.0, "SampleOne"),
    "s2.pdf": (160.0, 90.0, "SampleTwo"),
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    figs = tmp_path / "figs"
    figs.mkdir()
    for name, (w, h, text) in PAGES.items():
        doc = pymupdf.open()
        page = doc.new_page(width=w, height=h)
        page.insert_text((20, 60), text, fontsize=11)
        doc.save(figs / name)
        doc.close()
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    m.open_project(str(figs))
    exportjob.reset_for_tests()
    m.app.config["TESTING"] = True
    return m.app.test_client(), figs


def _spec(ids, **over):
    spec = {
        "scope": "original",
        "filename": "all_samples",
        "formats": ["pdf"],
        "merge_pdf": {"pages": [{"figure_id": i, "source_kind": "vector"} for i in ids]},
    }
    spec.update(over)
    return spec


def _post(client, spec):
    resp = client.post("/api/export", json=spec)
    return resp.status_code, resp.get_json()


def _read(body):
    """读回导出的 PDF：每页 (宽, 高, 文字)。"""
    (out,) = [o for o in body["outputs"] if o["format"] == "pdf"]
    with pymupdf.open(Path(body["export_dir"]) / out["name"]) as doc:
        return [(round(p.rect.width), round(p.rect.height), p.get_text().strip()) for p in doc]


def test_three_figures_become_three_pages_in_request_order(env):
    client, _ = env
    status, body = _post(client, _spec(["s0.pdf", "s1.pdf", "s2.pdf"]))
    assert status == 200, body
    assert body["status"] == "done"
    assert [o["name"] for o in body["outputs"]] == ["all_samples.pdf"]
    assert _read(body) == [
        (200, 150, "SampleZero"),
        (180, 120, "SampleOne"),
        (160, 90, "SampleTwo"),
    ]
    # 回执写明页数与页序
    assert body["request"]["merge_pdf"] == {
        "page_count": 3,
        "figure_ids": ["s0.pdf", "s1.pdf", "s2.pdf"],
    }
    (out,) = body["outputs"]
    assert out["vector"] is True
    assert out["dimensions"]["mm"] is not None  # 第一页的尺寸


def test_page_order_is_the_request_order_not_the_filename_order(env):
    client, _ = env
    _, body = _post(client, _spec(["s2.pdf", "s0.pdf", "s1.pdf"]))
    assert [t for _, _, t in _read(body)] == ["SampleTwo", "SampleZero", "SampleOne"]


def test_the_same_figure_on_two_pages_is_placed_twice_in_page_order(env):
    client, _ = env
    _, body = _post(client, _spec(["s1.pdf", "s0.pdf", "s1.pdf"]))
    assert body["status"] == "done", body
    assert [t for _, _, t in _read(body)] == ["SampleOne", "SampleZero", "SampleOne"]
    assert body["request"]["merge_pdf"]["page_count"] == 3


def test_a_missing_figure_fails_the_whole_file_instead_of_shipping_a_short_one(env):
    client, _ = env
    _, body = _post(client, _spec(["s0.pdf", "nope.pdf", "s2.pdf"]))
    assert body["status"] == "failed"
    assert not list(Path(body["export_dir"]).glob("all_samples*.pdf"))


def test_a_single_page_request_is_unchanged_by_the_new_field(env):
    """没有 `merge_pdf` 的请求：与从前逐字相同——一页、没有 `merge_pdf` 回执。"""
    client, _ = env
    status, body = _post(
        client,
        {
            "scope": "original",
            "filename": "one",
            "formats": ["pdf"],
            "original": {"figure_id": "s1.pdf", "source_kind": "vector"},
        },
    )
    assert status == 200, body
    assert "merge_pdf" not in body["request"]
    assert _read(body) == [(180, 120, "SampleOne")]


def test_background_job_merges_too(env):
    client, _ = env
    resp = client.post("/api/export/start", json=_spec(["s0.pdf", "s2.pdf"]))
    job_id = resp.get_json()["job_id"]
    job = exportjob.get(job_id)
    for _ in range(200):
        if job.status in exportjob._TERMINAL_STATUSES:
            break
        import time

        time.sleep(0.05)
    body = job.to_payload()
    assert body["status"] == "done", body
    assert [t for _, _, t in _read(body)] == ["SampleZero", "SampleTwo"]


# ------------------------------ 逐页检查（Codex #1395 P1）------------------------------
def test_a_bad_second_page_rejects_the_whole_file_before_publish(env, monkeypatch):
    """产物检查器只读首页：第一页合规、第二页的事实对不上文件（报告 120 × 60 mm、实际 180 × 120 pt）——
    只检查合并后的文件会放行；逐页检查要拦下，整份不发布，错误码指出是第几页。"""
    client, _ = env
    real = m._export_produce_original
    seen = []

    def tamper(job, tmp_dir, **kw):
        produced = real(job, tmp_dir, **kw)
        seen.append(job.request.original.figure_id)
        if job.request.original.figure_id == "s1.pdf":
            produced[0].width_mm, produced[0].height_mm = 120.0, 60.0
        return produced

    monkeypatch.setattr(m, "_export_produce_original", tamper)
    status, body = _post(client, _spec(["s0.pdf", "s1.pdf", "s2.pdf"]))
    assert seen == ["s0.pdf", "s1.pdf", "s2.pdf"]
    (out,) = body["outputs"]
    assert out["status"] == "failed" and out["error"]["code"] == "artifact_rejected", body
    assert out["error"]["params"]["failed"] == "page 2: size"
    assert not list(Path(body["export_dir"]).glob("all_samples*.pdf"))


def test_all_pages_good_is_still_published_with_a_manifest(env):
    client, _ = env
    _, body = _post(client, _spec(["s0.pdf", "s1.pdf"]))
    (out,) = body["outputs"]
    assert out["status"] == "done" and out["manifest"]["verdict"] != "rejected"


# ------------------------------- 非法请求 ---------------------------------------
@pytest.mark.parametrize(
    "over",
    [
        {"formats": ["pdf", "png"]},  # 位图没有「多页」
        {"formats": ["svg"]},
        {"formats": ["pdf", "svg"]},  # SVG 没有多页，混在一起同名异物
        {"original": {"figure_id": "s0.pdf"}},  # 第一页的来源只能在 pages 里给
        {"merge_pdf": {"pages": []}},
        {"merge_pdf": {"pages": "s0.pdf"}},
        {"merge_pdf": ["s0.pdf"]},
        {"merge_pdf": {"pages": [{"figure_id": "s0.pdf"}] * (exportreq.MERGE_PAGES_MAX + 1)}},
    ],
    ids=["png", "svg", "pdf+svg", "original", "empty", "pages-not-list", "not-dict", "too-many"],
)
def test_illegal_combinations_are_refused_before_any_file_is_written(env, over):
    client, _ = env
    status, body = _post(client, _spec(["s0.pdf"], **over))
    assert status == 400 and body["code"] == "bad_merge", body
    assert not list(Path(m.EXPORT_DIR).glob("**/*.pdf"))


def test_canvas_scope_is_refused(env):
    client, _ = env
    spec = _spec(["s0.pdf"], scope="canvas")
    spec["canvas"] = {"page_w_mm": 100, "page_h_mm": 50, "objects": []}
    status, body = _post(client, spec)
    assert status == 400 and body["code"] == "bad_merge"


def test_a_page_without_a_figure_reports_the_original_codes(env):
    client, _ = env
    status, body = _post(client, _spec(["s0.pdf"], merge_pdf={"pages": [{"w_mm": 3}]}))
    assert status == 400 and body["code"] == "missing_figure"


# ------------------------------- concat_pdf 本身 ----------------------------------
def _single(path: Path, text: str, w=100, h=50):
    doc = pymupdf.open()
    doc.new_page(width=w, height=h).insert_text((10, 30), text)
    doc.save(path)
    doc.close()
    return path


def test_merge_keeps_order_and_repeats(tmp_path):
    a = _single(tmp_path / "a.pdf", "AAA", 100, 50)
    b = _single(tmp_path / "b.pdf", "BBB", 120, 60)
    out = tmp_path / "out" / "m.pdf"
    assert pdfbackend.concat_pdf([a, b, a], out) == 3
    with pymupdf.open(out) as doc:
        assert [p.get_text().strip() for p in doc] == ["AAA", "BBB", "AAA"]
        assert [round(p.rect.width) for p in doc] == [100, 120, 100]


def test_merge_rejects_a_multipage_or_missing_input(tmp_path):
    a = _single(tmp_path / "a.pdf", "AAA")
    two = tmp_path / "two.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    doc.save(two)
    doc.close()
    with pytest.raises(pdfbackend.PdfConcatError):
        pdfbackend.concat_pdf([a, two], tmp_path / "o.pdf")
    with pytest.raises(pdfbackend.PdfConcatError):
        pdfbackend.concat_pdf([tmp_path / "missing.pdf"], tmp_path / "o.pdf")
    with pytest.raises(pdfbackend.PdfConcatError):
        pdfbackend.concat_pdf([], tmp_path / "o.pdf")


# ------------------- 端到端：脚本用 PdfPages 写三页 → probe → 经 HTTP 合成 -------------------
from tavotto.engine import pool as engine_pool  # noqa: E402

try:
    _WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    _WORKER_PY = None

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


@pytest.mark.skipif(
    _WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)
def test_pdfpages_script_exports_as_one_three_page_pdf_over_http(tmp_path, monkeypatch):
    """pdfprobe 的 close_each：probe 得到 `pdf_pages.page_stems` 与每张图的 asset_id，按页序经 HTTP 合成，
    读回的第 i 页与「第 i 张图单独导出」的那一页逐像素相同——页序真的跟脚本走，不是三份同一页。"""
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "repro.py").write_text(CLOSE_EACH, encoding="utf-8")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    m.app.config["TESTING"] = True
    m.reset_projects()
    client = m.app.test_client()
    try:
        client.post("/api/projects/open", json={"path": str(figs)})
        probe = client.post("/api/registry/probe", json={"script": "repro.py"}).get_json()
        assert probe["error"] is None, probe
        (summary,) = probe["pdf_pages"]
        assert summary["complete"] is True and summary["path"] == "all_samples.pdf"
        by_stem = {d["stem"]: d["asset_id"] for d in probe["descriptors"]}
        ids = [by_stem[s] for s in summary["page_stems"]]
        assert len(ids) == 3

        def export(spec_over):
            spec = {"scope": "original", "formats": ["pdf"], **spec_over}
            resp = client.post("/api/export", json=spec)
            assert resp.status_code == 200, resp.get_json()
            body = resp.get_json()
            assert body["status"] == "done", body
            return body

        merged = export(
            {
                "filename": "all_samples",
                "merge_pdf": {"pages": [{"figure_id": i, "source_kind": "figure"} for i in ids]},
            }
        )
        assert merged["request"]["merge_pdf"]["page_count"] == 3
        with pymupdf.open(Path(merged["export_dir"]) / "all_samples.pdf") as doc:
            assert doc.page_count == 3
            merged_px = [p.get_pixmap(dpi=50).samples for p in doc]
        assert len(set(merged_px)) == 3  # 三页互不相同
        for n, asset_id in enumerate(ids):
            single = export(
                {
                    "filename": f"single_{n}",
                    "original": {"figure_id": asset_id, "source_kind": "figure"},
                }
            )
            with pymupdf.open(Path(single["export_dir"]) / f"single_{n}.pdf") as one:
                assert one.page_count == 1
                assert one[0].get_pixmap(dpi=50).samples == merged_px[n]
    finally:
        engine_pool.shutdown_all(str(figs), wait=True)
        m.reset_projects()
