"""PDF 原件的字体事实（`artifactinspect.original_font_facts` + `/api/panel-font-facts`，ADR 0139 修订 2026-11-24 补充）。

画布在用户单选一张有脚本、没动过的 PDF 原件时问这一句，据此给「用脚本重画」角标。尺子是 matplotlib 自己存的 PDF：

* `pdf.fonttype=3`（matplotlib 默认）→ `type3`；`pdf.fonttype=42` → 不是；
* 只有西文字体的图里写中文：matplotlib ≥ 3.11 退到 LastResort → `missing_glyphs`；
* 读不出的文件不凭空给出「有问题」；同一份文件只走一遍内容流，文件一变就重新看。
"""

from __future__ import annotations

import os

import pytest

from tavotto.engine import artifactinspect

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")

from matplotlib import pyplot as plt  # noqa: E402


def _figure_pdf(path, *, fonttype: int, text: str = "Concentration") -> None:
    with matplotlib.rc_context({"pdf.fonttype": fonttype, "font.family": ["DejaVu Sans"]}):
        fig, ax = plt.subplots(figsize=(2, 1.5))
        ax.plot([0, 1], [0, 1])
        ax.set_ylabel(text)
        fig.savefig(path)
        plt.close(fig)


@pytest.fixture(autouse=True)
def _fresh_memo():
    artifactinspect._FONT_FACTS.clear()
    yield
    artifactinspect._FONT_FACTS.clear()


def test_type3_is_reported_and_type42_is_not(tmp_path):
    _figure_pdf(tmp_path / "t3.pdf", fonttype=3)
    _figure_pdf(tmp_path / "t42.pdf", fonttype=42)
    assert artifactinspect.original_font_facts(tmp_path / "t3.pdf") == {
        "readable": True,
        "type3": True,
        "missing_glyphs": False,
    }
    assert artifactinspect.original_font_facts(tmp_path / "t42.pdf") == {
        "readable": True,
        "type3": False,
        "missing_glyphs": False,
    }


def test_chinese_without_a_cjk_face_is_reported_as_missing_glyphs(tmp_path):
    if tuple(int(x) for x in matplotlib.__version__.split(".")[:2]) < (3, 11):
        pytest.skip(
            "matplotlib < 3.11 不退到 LastResort（Type 42 画成 .notdef，要检查器认得出才报）"
        )
    _figure_pdf(tmp_path / "cjk.pdf", fonttype=42, text="浓度")
    facts = artifactinspect.original_font_facts(tmp_path / "cjk.pdf")
    assert facts["readable"] and facts["missing_glyphs"], facts


def test_last_resort_that_never_paints_is_not_missing_glyphs(tmp_path, monkeypatch):
    from tavotto.rendercore import inspector

    pdf = tmp_path / "layer.pdf"
    _figure_pdf(pdf, fonttype=42)
    font = {"name": "LastResort", "type3": False, "last_resort": True, "notdef": False}
    for painted, expected in ((False, False), (True, True)):
        artifactinspect._FONT_FACTS.clear()
        seen = {"integrity": inspector.VERIFIED, "fonts_used": [{**font, "painted": painted}]}
        monkeypatch.setattr(inspector, "observe_pdf", lambda p, **kw: seen)
        assert artifactinspect.original_font_facts(pdf)["missing_glyphs"] is expected


def test_unreadable_files_are_not_called_problematic(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 not really")
    assert artifactinspect.original_font_facts(bad) == {
        "readable": False,
        "type3": False,
        "missing_glyphs": False,
    }
    assert artifactinspect.original_font_facts(tmp_path / "gone.pdf")["readable"] is False


def test_the_same_file_is_walked_once_and_a_changed_file_again(tmp_path, monkeypatch):
    from tavotto.rendercore import inspector

    pdf = tmp_path / "fig.pdf"
    _figure_pdf(pdf, fonttype=3)
    calls = []
    real = inspector.observe_pdf
    monkeypatch.setattr(inspector, "observe_pdf", lambda p, **kw: calls.append(p) or real(p, **kw))
    assert artifactinspect.original_font_facts(pdf)["type3"] is True
    assert artifactinspect.original_font_facts(pdf)["type3"] is True
    assert len(calls) == 1
    _figure_pdf(pdf, fonttype=42)
    st = pdf.stat()
    os.utime(pdf, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert artifactinspect.original_font_facts(pdf)["type3"] is False
    assert len(calls) == 2


# --------------------------------------------------------------------------- HTTP
@pytest.fixture
def client(tmp_path, monkeypatch):
    from tavotto import app as m
    from tavotto.engine import project_watch

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    figs = tmp_path / "figs"
    figs.mkdir()
    _figure_pdf(figs / "Fig1.pdf", fonttype=3)
    (figs / "Fig1.png").write_bytes(b"\x89PNG\r\n\x1a\nORIGINAL")
    m.open_project(str(figs))
    yield m.app.test_client()
    m.reset_projects()
    project_watch.stop()


def test_endpoint_reports_the_pdf_and_refuses_other_kinds(client):
    resp = client.get("/api/panel-font-facts?id=Fig1.pdf")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body == {"id": "Fig1.pdf", "readable": True, "type3": True, "missing_glyphs": False}
    assert resp.headers["Cache-Control"] == "no-store"
    png = client.get("/api/panel-font-facts?id=Fig1.png")
    assert png.status_code == 400 and png.get_json()["code"] == "not_pdf"
    assert client.get("/api/panel-font-facts?id=../x.pdf").status_code in (403, 404)
