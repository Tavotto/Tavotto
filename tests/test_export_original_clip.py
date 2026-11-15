"""ADR 0188：`original.clip`——单图编辑里剪切蒙版裁这张图。

缺席 = 请求与输出与从前逐字节相同；非空 = 走「原图 + 标注」那条 RenderCore 合成路径（没有标注也走），图外面包
`Group(clip=…)`：PDF / PNG 里蒙版外是纸；EPS 由 `epsoverlay.compose(figure_clip=…)` 把内嵌原图裁住，仍是矢量。
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import epsoverlay, exportjob, exportreq

PT = 25.4 / 72
W_MM, H_MM = 200 * PT, 150 * PT


@pytest.fixture
def env(tmp_path, monkeypatch):
    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=150)
    page.draw_rect(pymupdf.Rect(0, 0, 200, 150), color=None, fill=(0.1, 0.2, 0.8))
    doc.save(figs / "p1.pdf")
    doc.close()
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    m.open_project(str(figs))
    exportjob.reset_for_tests()
    m.app.config["TESTING"] = True
    return m.app.test_client(), figs


# 左半边（页面 mm）
LEFT = {
    "type": "path",
    "x_mm": 0,
    "y_mm": 0,
    "w_mm": W_MM / 2,
    "h_mm": H_MM,
    "subpaths": [
        {"closed": True, "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}, {"p": [0, 1]}]}
    ],
}


def _spec(formats, clip=None, **over):
    original = {"figure_id": "p1.pdf", "w_mm": W_MM, "h_mm": H_MM, "source_kind": "vector"}
    if clip is not None:
        original["clip"] = clip
    return {
        "scope": "original",
        "filename": "Fig",
        "formats": formats,
        "original": original,
        **over,
    }


def _post(client, spec):
    resp = client.post("/api/export", json=spec)
    return resp.status_code, resp.get_json()


def _out(body, fmt):
    return next(o for o in body["outputs"] if o["format"] == fmt)


def _path(body, fmt) -> Path:
    return Path(body["export_dir"]) / _out(body, fmt)["name"]


def test_clip_parses_and_changes_plan_identity_only_when_present():
    plain = exportreq.normalize(_spec(["pdf"]))
    assert plain.original.clip == ()
    assert exportreq.normalize(_spec(["pdf"], clip=[])).original.clip == ()
    clipped = exportreq.normalize(_spec(["pdf"], clip=[LEFT]))
    assert clipped.original.clip == (LEFT,)
    ref = exportreq.render_plan_ref(plain, [])
    assert exportreq.render_plan_ref(exportreq.normalize(_spec(["pdf"], clip=[])), []) == ref
    assert exportreq.render_plan_ref(clipped, []) != ref


@pytest.mark.parametrize("bad", [{"type": "path"}, [{"type": "shape"}], ["x"]])
def test_bad_clip_is_a_structured_request_error(bad):
    with pytest.raises(exportreq.ExportRequestError) as e:
        exportreq._original_source({"figure_id": "p1.pdf", "w_mm": 10, "h_mm": 5, "clip": bad})
    assert e.value.code == "bad_objects"


def test_clip_needs_the_figure_size():
    with pytest.raises(exportreq.ExportRequestError) as e:
        exportreq._original_source({"figure_id": "p1.pdf", "clip": [LEFT]})
    assert e.value.code == "bad_objects"


def test_pdf_and_png_are_clipped_to_the_mask(env):
    client, _ = env
    status, body = _post(client, _spec(["pdf", "png"], [LEFT], ppi=72))
    assert status == 200, body
    assert _out(body, "pdf")["status"] == "done", body
    with pymupdf.open(_path(body, "pdf")) as d:
        pix = d[0].get_pixmap(alpha=False)
        assert pix.pixel(50, 75)[2] > 150  # 左半边：图（蓝）
        assert pix.pixel(150, 75) == (255, 255, 255)  # 右半边：裁掉了
        assert "W n" in d[0].read_contents().decode("latin-1")
    png = pymupdf.Pixmap(str(_path(body, "png")))
    assert png.pixel(50, 75)[2] > 150
    assert png.pixel(150, 75)[:3] == (255, 255, 255)


def test_without_clip_the_output_is_unchanged(env):
    client, _ = env
    _, a = _post(client, _spec(["pdf"], filename="a"))
    _, b = _post(client, _spec(["pdf"], [], filename="b"))
    assert _path(a, "pdf").read_bytes() == _path(b, "pdf").read_bytes()


@pytest.fixture
def inner_eps(env, tmp_path, monkeypatch):
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    fig = Figure(figsize=(200 / 72, 150 / 72))
    fig.add_axes([0.1, 0.1, 0.5, 0.5]).plot([0, 1], [0, 1])
    path = tmp_path / "inner.eps"
    fig.savefig(path, format="eps")

    def fake(job, src, dpi, tmp_dir, **kw):
        out = tmp_dir / "inner_copy.eps"
        out.write_bytes(path.read_bytes())
        return exportjob.Produced(
            format="eps", tmp_path=out, width_mm=W_MM, height_mm=H_MM, vector=True
        )

    monkeypatch.setattr(m, "_produce_original_eps", fake)
    return path


def test_eps_clips_the_embedded_figure(env, inner_eps):
    client, _ = env
    status, body = _post(client, _spec(["eps"], [LEFT]))
    assert status == 200, body
    assert _out(body, "eps")["status"] == "done", body
    data = _path(body, "eps").read_bytes()
    begin = data.index(b"%%BeginDocument")
    head = data[:begin].decode("latin-1")
    # 嵌入段之前有一条裁剪：到右边界 100 pt（左半边）
    assert re.search(r"gsave\nnewpath\n0 \S+ moveto\n100 \S+ lineto", head), head[-400:]
    assert "\nclip\nnewpath\n" in head
    # 嵌入段之后多一个 grestore 收掉裁剪
    tail = data[data.index(b"%%EndDocument") :]
    assert tail.count(b"grestore") >= 2


def test_eps_compose_without_figure_clip_is_byte_identical(inner_eps):
    inner = inner_eps.read_bytes()
    a = epsoverlay.compose(inner, "ov\n", 200.0, 150.0, underlay="un\n")
    b = epsoverlay.compose(inner, "ov\n", 200.0, 150.0, underlay="un\n", figure_clip="")
    assert a == b
