"""RenderCore 接进**原 ExportJob**（统一实施包 U06，ADR 0059）：`exportjob.prepare → run(job, produce)` 真跑
一遍——临时目录 / 原子发布 / partial / 取消 / 终局字段顺序全是 `exportjob` 的既有语义，本模块只提供
`produce` 那一只手。

主语是 **`job.to_payload()` 里读者看到的东西**与**导出目录里的文件**：PDF 真的原子落在最终名字上、
`vector: True`、给不出的格式逐项 `format_failed` 且理由结构化、编译期的事实进 `warnings`、冻结源
在写入前核 hash（RC-014）、带 override 的面板不起子进程（RC-019）。

要候选包 + 批准字体（缺则 skip 并说明理由）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tavotto.engine import exportjob
from tavotto.rendercore import fonts, sources

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "foundation" / "pdf_png_assets"


@pytest.fixture(scope="module")
def provider():
    from tavotto.rendercore.hbshaper import CandidatePackagesMissing, HbFaceProvider, require

    try:
        require("pikepdf", "fontTools", "uharfbuzz")
    except CandidatePackagesMissing as exc:
        pytest.skip(f"候选包未装（not_run）：{exc}")
    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}；先跑 scripts/fetch_fonts.py")
    return HbFaceProvider(reg)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "figs").mkdir(parents=True)
    shutil.copy(FIXTURE / "page.pdf", root / "figs" / "Fig1.pdf")
    return root


@pytest.fixture(autouse=True)
def _reset_jobs():
    exportjob.reset_for_tests()
    yield
    exportjob.reset_for_tests()


def _spec(objects: list[dict], formats=("pdf", "png"), **extra) -> dict:
    return {
        "scope": "canvas",
        "formats": list(formats),
        "filename": "Fig 1",
        "overwrite": "replace",
        "canvas": {"page_w_mm": 120, "page_h_mm": 60, "objects": objects},
        **extra,
    }


def _text(text="Hello 图 ∇", **kw) -> dict:
    return {
        "type": "text",
        "id": kw.pop("id", "t1"),
        "text": text,
        "x_mm": 5,
        "y_mm": 5,
        "w_mm": 100,
        "h_mm": 10,
        "size_pt": 10,
        **kw,
    }


def _run(job: exportjob.ExportJob, project: Path, provider) -> dict:
    from tavotto.rendercore import job as rcjob

    def produce(j, tmp_dir):
        return rcjob.produce(
            j, tmp_dir, sources=sources.StaticSourceResolver(project), provider=provider
        )

    return exportjob.run(job, produce)


def test_a_text_only_canvas_exports_a_vector_pdf_and_reports_png_as_not_yet_supported(
    project, provider, tmp_path
):
    export_dir = tmp_path / "out"
    job = exportjob.prepare(
        _spec(
            [
                _text(),
                {
                    "type": "text",
                    "id": "h",
                    "text": "x",
                    "x_mm": 1,
                    "y_mm": 1,
                    "w_mm": 5,
                    "h_mm": 5,
                    "hidden": True,
                },
            ]
        ),
        export_dir,
    )
    payload = _run(job, project, provider)
    assert payload["status"] == "partial", payload
    by_fmt = {o["format"]: o for o in payload["outputs"]}
    pdf = by_fmt["pdf"]
    assert pdf["status"] == "done" and pdf["vector"] is True and pdf["name"] == "Fig 1.pdf"
    assert (export_dir / "Fig 1.pdf").read_bytes()[:5] == b"%PDF-"
    assert pdf["dimensions"]["mm"] == [120.0, 60.0]
    png = by_fmt["png"]
    assert png["status"] == "failed" and png["error"]["code"] == "format_failed"
    assert any(g["operation"] == "text" for g in png["error"]["params"]["unsupported"])
    # 编译期事实进 warnings：图 / ∇ 由 CJK 脸画、hidden 对象被丢；没有静默
    assert "t1: cjk_face 图∇" in payload["warnings"] and "h: hidden" in payload["warnings"]
    assert not any(p.name.startswith(exportjob.TMP_PREFIX) for p in export_dir.iterdir())


def test_a_panel_canvas_exports_a_vector_pdf_with_the_source_page_inside(
    project, provider, tmp_path
):
    """页上有面板（ImportedPage，U07 起 native）：PDF 那一项 `done`、`vector: True`，源页的文字层随
    Form XObject 一起在产物里；作业里读的是冻结那一份字节（`files` 经 `read_frozen()` 交给写入器）。"""
    export_dir = tmp_path / "out"
    job = exportjob.prepare(
        _spec(
            [
                {
                    "type": "panel",
                    "id": "figs/Fig1.pdf",
                    "x_mm": 5,
                    "y_mm": 5,
                    "w_mm": 60,
                    "h_mm": 40,
                    "rotation": 90,
                    "opacity": 0.5,
                }
            ],
            formats=("pdf",),
        ),
        export_dir,
    )
    payload = _run(job, project, provider)
    assert payload["status"] == "done", payload
    out = payload["outputs"][0]
    assert out["status"] == "done" and out["vector"] is True
    data = (export_dir / "Fig 1.pdf").read_bytes()
    assert data[:5] == b"%PDF-" and b"/Subtype /Form" in data and b"/Transparency" in data
    pdfium = pytest.importorskip("pypdfium2", reason="pypdfium2 未装（not_run）")
    text = pdfium.PdfDocument(str(export_dir / "Fig 1.pdf"))[0].get_textpage().get_text_range()
    assert "U00 fixture: y = 3x + 1" in text


def test_a_frozen_source_that_changes_before_writing_fails_the_job(
    project, provider, tmp_path, monkeypatch
):
    """RC-014：编译冻结了 Fig1.pdf 的 hash；写入之前文件被改写 → `export_render_failed` 带 source_changed，
    不读新字节。用 monkeypatch 在冻结与核对之间改文件（真实竞态的最小复现）。"""
    from tavotto.rendercore import job as rcjob

    real_read = rcjob.read_frozen
    calls = {"n": 0}

    def tamper_then_read(fs):
        if calls["n"] == 0:
            Path(fs.path).write_bytes(b"%PDF-1.4\n% tampered after freeze\n")
        calls["n"] += 1
        return real_read(fs)

    monkeypatch.setattr(rcjob, "read_frozen", tamper_then_read)
    job = exportjob.prepare(
        _spec(
            [
                {
                    "type": "panel",
                    "id": "figs/Fig1.pdf",
                    "x_mm": 5,
                    "y_mm": 5,
                    "w_mm": 60,
                    "h_mm": 40,
                }
            ],
            formats=("pdf",),
        ),
        tmp_path / "out",
    )
    payload = _run(job, project, provider)
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "export_render_failed"
    assert "source_changed" in payload["error"]["params"]["reason"]
    assert calls["n"] == 1


def test_a_panel_with_overrides_is_refused_without_running_anything(project, provider, tmp_path):
    job = exportjob.prepare(
        _spec(
            [
                {
                    "type": "panel",
                    "id": "figs/Fig1.pdf",
                    "x_mm": 5,
                    "y_mm": 5,
                    "w_mm": 60,
                    "h_mm": 40,
                    "overrides": [{"gid": "a", "prop": "xlim"}],
                }
            ],
            formats=("pdf",),
        ),
        tmp_path / "out",
    )
    payload = _run(job, project, provider)
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "export_render_failed"
    assert "source_needs_execution" in payload["error"]["params"]["reason"]


def test_a_page_with_no_operations_still_refuses_non_pdf_formats(project, provider, tmp_path):
    """全 hidden 的透明页：能力表上没有任何操作可判、缺口为空，但 PNG 仍然只有 U07 才给得出——
    不能把 PDF 字节写进 .png 报成功（Codex #460 P2）。PDF 那一项照常（空页是合法产物）。"""
    export_dir = tmp_path / "out"
    job = exportjob.prepare(
        _spec([_text(hidden=True)], formats=("pdf", "png", "tiff"), background="transparent"),
        export_dir,
    )
    payload = _run(job, project, provider)
    assert payload["status"] == "partial", payload
    by_fmt = {o["format"]: o for o in payload["outputs"]}
    assert by_fmt["pdf"]["status"] == "done" and by_fmt["pdf"]["vector"] is True
    for fmt in ("png", "tiff"):
        assert by_fmt[fmt]["status"] == "failed", fmt
        assert by_fmt[fmt]["error"]["code"] == "format_failed"
        assert by_fmt[fmt]["error"]["params"]["unsupported"][0]["operation"] == "format"
        assert not (export_dir / f"Fig 1.{fmt}").exists()
    assert (export_dir / "Fig 1.pdf").read_bytes()[:5] == b"%PDF-"


def test_cancel_before_writing_leaves_nothing_behind(project, provider, tmp_path):
    export_dir = tmp_path / "out"
    job = exportjob.prepare(_spec([_text()], formats=("pdf",)), export_dir)
    assert exportjob.cancel(job.id)
    payload = _run(job, project, provider)
    assert payload["status"] == "cancelled" and payload["outputs"] == []
    assert not (export_dir / "Fig 1.pdf").exists()


def test_the_same_request_twice_gives_the_same_plan_identity_and_bytes(project, provider, tmp_path):
    a = exportjob.prepare(_spec([_text()], formats=("pdf",)), tmp_path / "a")
    b = exportjob.prepare(_spec([_text()], formats=("pdf",)), tmp_path / "b")
    pa, pb = _run(a, project, provider), _run(b, project, provider)
    assert pa["status"] == pb["status"] == "done"
    assert (tmp_path / "a" / "Fig 1.pdf").read_bytes() == (
        tmp_path / "b" / "Fig 1.pdf"
    ).read_bytes()
