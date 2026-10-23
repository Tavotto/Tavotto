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


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    """真 render child（rc-venv 里有 pypdfium2）：PNG / TIFF 从 Canonical PDF 栅格（U07，ADR 0066）。"""
    import importlib.util
    import os

    if importlib.util.find_spec("pypdfium2") is None:
        pytest.skip("pypdfium2 未装（not_run）")
    from tavotto.rendercore import renderhost

    env = dict(os.environ)
    root = Path(__file__).resolve().parent.parent
    env["PYTHONPATH"] = os.pathsep.join([str(root / "src"), env.get("PYTHONPATH", "")])
    h = renderhost.RenderHost(
        env=env, default_timeout=60, scratch_dir=tmp_path_factory.mktemp("rgba")
    )
    yield h
    h.close()


def _run(job: exportjob.ExportJob, project: Path, provider, host=None, *, resolver=None) -> dict:
    from tavotto.rendercore import job as rcjob

    def produce(j, tmp_dir):
        return rcjob.produce(
            j,
            tmp_dir,
            sources=resolver if resolver is not None else sources.StaticSourceResolver(project),
            provider=provider,
            host=host,
        )

    return exportjob.run(job, produce)


@pytest.fixture
def paired_source(project, provider):
    """同一 IR 的真实 PDF / SVG；预算只缩小常量，不伪造文件大小或写入器。"""
    from dataclasses import replace

    from tavotto.engine import figcapture
    from tavotto.rendercore import ir, pdfwriter, svgwriter

    shape = ir.Path(
        (("M", 10, 10), ("L", 50, 10), ("L", 50, 40), ("L", 10, 40), ("Z",)),
        fill=ir.Paint((1, 0, 0)),
    )
    page = ir.Page(100, 60, (shape,))
    pdf_path, svg_path = project / "pair.pdf", project / "pair.svg"
    pdfwriter.write_pdf(page, pdf_path, provider)
    svgwriter.write_svg(page, svg_path, provider)

    def freeze(path):
        return sources.FrozenSource(
            artifact=figcapture.source_artifact_from_file(
                path,
                source_id="runtime:plot.py#Fig1",
                origin=figcapture.ORIGIN_EXECUTION,
                receipt_id="sha256:" + "1" * 64,
                receipt_identity="sha256:" + "2" * 64,
                generation=1,
                patch_hash="sha1:abc",
            ),
            path=path,
        )

    return replace(freeze(pdf_path), svg=freeze(svg_path))


def _run_pair(project, provider, host, tmp_path, fs, formats):
    resolver = sources.ExecutionSourceResolver(
        static=sources.StaticSourceResolver(project), execute=lambda _obj: fs
    )
    job = exportjob.prepare(
        _spec(
            [
                {
                    "type": "panel",
                    "id": fs.artifact.source_id,
                    "x_mm": 5,
                    "y_mm": 5,
                    "w_mm": 60,
                    "h_mm": 40,
                }
            ],
            formats=formats,
            ppi=72,
        ),
        tmp_path,
    )
    return _run(job, project, provider, host, resolver=resolver)


def _assert_real_outputs(payload, directory, formats):
    """核最终发布的容器，而不只信 payload.status。"""
    import xml.etree.ElementTree as ET

    import pikepdf
    from PIL import Image

    outputs = {item["format"]: item for item in payload["outputs"]}
    for fmt in formats:
        item = outputs[fmt]
        assert item["status"] == "done", payload
        path = directory / item["name"]
        if fmt == "pdf":
            with pikepdf.open(path) as doc:
                assert len(doc.pages) == 1
                assert b"Do" in doc.pages[0].Contents.read_bytes()
        elif fmt == "svg":
            root = ET.parse(path).getroot()
            assert root.tag == "{http://www.w3.org/2000/svg}svg"
            assert list(root.iter("{http://www.w3.org/2000/svg}path"))
        else:
            with Image.open(path) as image:
                image.load()
                assert image.format == {"png": "PNG", "tiff": "TIFF"}[fmt]
                assert image.size == (340, 170)
                assert image.convert("RGB").getextrema()[1][0] < 255


@pytest.mark.parametrize("formats", [("pdf",), ("png",), ("tiff",), ("pdf", "png", "tiff")])
@pytest.mark.parametrize("budget", ["canonical", "pair"])
def test_non_svg_formats_budget_and_read_only_the_canonical_source(
    project, provider, host, tmp_path, monkeypatch, paired_source, formats, budget
):
    """主语：produce 消费的字节；非 SVG 在 canonical 峰值恰好够时成功，companion 零读取。"""
    import builtins

    from tavotto.rendercore import job as rcjob

    size = paired_source.artifact.size_bytes
    svg_size = paired_source.svg.artifact.size_bytes
    limit = size * 2 if budget == "canonical" else size + svg_size + max(size, svg_size)
    monkeypatch.setattr(rcjob, "SOURCE_BYTES_BUDGET", limit)
    # 冻结完成以后观察整个 produce：即使绕开 read_frozen，也不能 stat / open companion。
    for owner, name in ((builtins, "open"), (Path, "open"), (Path, "stat")):
        original = getattr(owner, name)

        def guard(path, *args, _original=original, **kwargs):
            if isinstance(path, (str, Path)) and Path(path) == paired_source.svg.path:
                pytest.fail("non-SVG export accessed its unused companion")
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(owner, name, guard)
    calls = []
    real = rcjob.read_frozen

    def counting(fs):
        calls.append(fs.artifact.kind)
        return real(fs)

    monkeypatch.setattr(rcjob, "read_frozen", counting)
    payload = _run_pair(project, provider, host, tmp_path / "out", paired_source, formats)
    assert payload["status"] == "done", payload
    assert calls == ["pdf"]
    _assert_real_outputs(payload, tmp_path / "out", formats)


@pytest.mark.parametrize("formats", [("svg",), ("pdf", "png", "tiff", "svg")])
def test_svg_companion_budget_preserves_peak_boundary_and_other_formats(
    project, provider, host, tmp_path, monkeypatch, paired_source, formats
):
    """SVG 仍计 sum + max；单 SVG 超限零读，混合请求只读/交付 canonical 那一路。"""
    from tavotto.rendercore import job as rcjob

    sizes = [paired_source.artifact.size_bytes, paired_source.svg.artifact.size_bytes]
    peak = sum(sizes) + max(sizes)
    monkeypatch.setattr(rcjob, "SOURCE_BYTES_BUDGET", peak - 1)
    calls = []
    real = rcjob.read_frozen

    def counting(fs):
        calls.append(fs.artifact.kind)
        return real(fs)

    monkeypatch.setattr(rcjob, "read_frozen", counting)
    payload = _run_pair(project, provider, host, tmp_path / "over", paired_source, formats)
    if formats == ("svg",):
        assert payload["status"] == "failed", payload
        error = payload["error"]
        assert error["code"] == "export_render_failed"
        assert calls == []
    else:
        assert payload["status"] == "partial", payload
        error = next(item for item in payload["outputs"] if item["format"] == "svg")["error"]
        assert error["code"] == "format_failed"
        assert calls == ["pdf"]
        _assert_real_outputs(payload, tmp_path / "over", ("pdf", "png", "tiff"))
    assert "source_budget_exceeded" in error["params"]["reason"]
    assert error["params"]["source_bytes"] == sum(sizes)
    assert error["params"]["peak_bytes"] == peak
    assert error["params"]["budget"] == peak - 1
    assert not (tmp_path / "over" / "Fig 1.svg").exists()

    monkeypatch.setattr(rcjob, "SOURCE_BYTES_BUDGET", peak)
    calls.clear()
    payload = _run_pair(project, provider, host, tmp_path / "exact", paired_source, formats)
    assert payload["status"] == "done", payload
    assert calls == ["pdf", "svg"]
    _assert_real_outputs(payload, tmp_path / "exact", formats)


@pytest.mark.parametrize("formats", [("pdf", "png", "tiff"), ("svg",), ("pdf", "svg")])
def test_canonical_source_over_budget_still_fails_every_format_before_reading(
    project, provider, tmp_path, monkeypatch, paired_source, formats
):
    from tavotto.rendercore import job as rcjob

    monkeypatch.setattr(rcjob, "SOURCE_BYTES_BUDGET", paired_source.artifact.size_bytes * 2 - 1)
    monkeypatch.setattr(rcjob, "read_frozen", lambda _fs: pytest.fail("over-budget source read"))
    payload = _run_pair(project, provider, None, tmp_path / "out", paired_source, formats)
    assert payload["status"] == "failed", payload
    assert payload["error"]["code"] == "export_render_failed"
    assert "source_budget_exceeded" in payload["error"]["params"]["reason"]


@pytest.mark.parametrize("change", ["hash", "missing"])
def test_unusable_canonical_source_still_fails_the_whole_mixed_request(
    project, provider, tmp_path, monkeypatch, paired_source, change
):
    from tavotto.rendercore import job as rcjob

    path = paired_source.path
    if change == "hash":
        data = path.read_bytes()
        path.write_bytes(data[:-1] + b" ")
    else:
        path.unlink()
    calls = []
    real = rcjob.read_frozen

    def read(fs):
        calls.append(fs.artifact.kind)
        return real(fs)

    monkeypatch.setattr(rcjob, "read_frozen", read)
    payload = _run_pair(
        project, provider, None, tmp_path / "out", paired_source, ("pdf", "png", "tiff", "svg")
    )
    assert payload["status"] == "failed", payload
    assert payload["error"]["code"] == "export_render_failed"
    code = "source_changed" if change == "hash" else "source_missing"
    assert code in payload["error"]["params"]["reason"]
    assert calls == ["pdf"]
    assert not any((tmp_path / "out").glob("Fig 1.*"))


@pytest.mark.parametrize(
    "formats", [("svg",), ("pdf", "png", "tiff"), ("pdf", "png", "tiff", "svg")]
)
@pytest.mark.parametrize("change", ["hash", "missing"])
def test_unusable_svg_companion_only_blocks_requested_svg(
    project, provider, host, tmp_path, monkeypatch, paired_source, formats, change
):
    from tavotto.rendercore import job as rcjob

    path = paired_source.svg.path
    if change == "hash":
        data = path.read_bytes()
        path.write_bytes(data[:-1] + b" ")  # 同长改写：一定核 hash，不能只核 size。
        assert path.stat().st_size == paired_source.svg.artifact.size_bytes
    else:
        path.unlink()
    calls = []
    real = rcjob.read_frozen

    def counting(fs):
        calls.append(fs.artifact.kind)
        return real(fs)

    monkeypatch.setattr(rcjob, "read_frozen", counting)
    payload = _run_pair(project, provider, host, tmp_path / "out", paired_source, formats)
    if "svg" not in formats:
        assert payload["status"] == "done", payload
        assert calls == ["pdf"]
        _assert_real_outputs(payload, tmp_path / "out", formats)
        return
    assert calls == ["pdf", "svg"]
    if formats == ("svg",):
        assert payload["status"] == "failed", payload
        error = payload["error"]
        assert error["code"] == "export_render_failed"
    else:
        assert payload["status"] == "partial", payload
        error = next(item for item in payload["outputs"] if item["format"] == "svg")["error"]
        assert error["code"] == "format_failed"
        _assert_real_outputs(payload, tmp_path / "out", ("pdf", "png", "tiff"))
    code = "source_changed" if change == "hash" else "source_missing"
    assert code in error["params"]["reason"]
    assert error["params"]["id"] == paired_source.artifact.source_id
    assert not (tmp_path / "out" / "Fig 1.svg").exists()


@pytest.mark.parametrize("cancel", [False, True])
def test_mixed_export_stops_companion_reads_after_failure_and_propagates_cancellation(
    project, provider, host, tmp_path, monkeypatch, paired_source, cancel
):
    from dataclasses import replace

    from tavotto.rendercore import job as rcjob

    pairs = {}
    for n in range(3):
        sid = f"runtime:plot.py#Fig{n}"
        pairs[sid] = replace(
            paired_source,
            artifact=replace(paired_source.artifact, source_id=sid),
            svg=replace(
                paired_source.svg, artifact=replace(paired_source.svg.artifact, source_id=sid)
            ),
        )
    calls = []
    real = rcjob.read_frozen

    def read(fs):
        sid, kind = fs.artifact.source_id, fs.artifact.kind
        calls.append((sid, kind))
        if (sid, kind) == ("runtime:plot.py#Fig1", "svg"):
            if cancel:
                raise exportjob.Cancelled()
            # 第一份 SVG 已缓存；第二份读取时同长篡改，第三份绝不继续读。
            data = fs.path.read_bytes()
            fs.path.write_bytes(data[:-1] + b" ")
        return real(fs)

    monkeypatch.setattr(rcjob, "read_frozen", read)
    resolver = sources.ExecutionSourceResolver(
        static=sources.StaticSourceResolver(project), execute=lambda obj: pairs[obj["id"]]
    )
    job = exportjob.prepare(
        _spec(
            [
                {"type": "panel", "id": sid, "x_mm": 5, "y_mm": 5, "w_mm": 60, "h_mm": 40}
                for sid in pairs
            ],
            formats=("pdf", "png", "tiff", "svg"),
            ppi=72,
        ),
        tmp_path / "out",
    )
    payload = _run(job, project, provider, host, resolver=resolver)
    assert calls == [(sid, "pdf") for sid in pairs] + [
        ("runtime:plot.py#Fig0", "svg"),
        ("runtime:plot.py#Fig1", "svg"),
    ]
    if cancel:
        assert payload["status"] == "cancelled", payload
        assert not any((tmp_path / "out").glob("Fig 1.*"))
    else:
        assert payload["status"] == "partial", payload
        _assert_real_outputs(payload, tmp_path / "out", ("pdf", "png", "tiff"))
        error = next(item for item in payload["outputs"] if item["format"] == "svg")["error"]
        assert "source_changed" in error["params"]["reason"]
        assert not (tmp_path / "out" / "Fig 1.svg").exists()


def test_a_text_only_canvas_exports_a_vector_pdf_and_a_png_from_the_same_canonical_pdf(
    project, provider, host, tmp_path
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
    payload = _run(job, project, provider, host)
    assert payload["status"] == "done", payload
    by_fmt = {o["format"]: o for o in payload["outputs"]}
    pdf = by_fmt["pdf"]
    assert pdf["status"] == "done" and pdf["vector"] is True and pdf["name"] == "Fig 1.pdf"
    assert (export_dir / "Fig 1.pdf").read_bytes()[:5] == b"%PDF-"
    assert pdf["dimensions"]["mm"] == [120.0, 60.0]
    png = by_fmt["png"]
    assert png["status"] == "done" and png["vector"] is False and png["name"] == "Fig 1.png"
    # 600 ppi（缺省）：120×60 mm → round(2834.6) × round(1417.3)
    assert png["dimensions"]["px"] == [2835, 1417] and png["dimensions"]["mm"] == [120.0, 60.0]
    assert (export_dir / "Fig 1.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
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


def test_the_aggregate_frozen_source_bytes_budget_fails_before_any_byte_is_read(
    project, provider, tmp_path, monkeypatch
):
    """Codex #463 第四轮 P2：像素预算管不住「几份大 PDF」。两份不同的源合计超过预算 → `export_render_failed` 带
    `source_budget_exceeded`，且 `read_frozen` 一次都没被调（一个字节不读）。"""
    import shutil

    from tavotto.rendercore import job as rcjob

    shutil.copy(FIXTURE / "page.pdf", project / "figs" / "Fig1b.pdf")
    size = (FIXTURE / "page.pdf").stat().st_size
    monkeypatch.setattr(rcjob, "SOURCE_BYTES_BUDGET", size * 3 - 1)  # 记账 = 两份之和 + 最大一份
    calls = {"n": 0}
    real = rcjob.read_frozen

    def counting(fs):
        calls["n"] += 1
        return real(fs)

    monkeypatch.setattr(rcjob, "read_frozen", counting)
    job = exportjob.prepare(
        _spec(
            [
                {
                    "type": "panel",
                    "id": "figs/Fig1.pdf",
                    "x_mm": 5,
                    "y_mm": 5,
                    "w_mm": 40,
                    "h_mm": 25,
                },
                {
                    "type": "panel",
                    "id": "figs/Fig1b.pdf",
                    "x_mm": 60,
                    "y_mm": 5,
                    "w_mm": 40,
                    "h_mm": 25,
                },
            ],
            formats=("pdf",),
        ),
        tmp_path / "out",
    )
    payload = _run(job, project, provider)
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "export_render_failed"
    assert "source_budget_exceeded" in payload["error"]["params"]["reason"]
    assert payload["error"]["params"]["source_bytes"] == size * 2
    assert payload["error"]["params"]["peak_bytes"] == size * 3
    assert calls["n"] == 0
    # 预算够时照常（同一份文件两次只算一份资源）
    monkeypatch.setattr(rcjob, "SOURCE_BYTES_BUDGET", size * 3)
    job = exportjob.prepare(
        _spec(
            [
                {
                    "type": "panel",
                    "id": "figs/Fig1.pdf",
                    "x_mm": 5,
                    "y_mm": 5,
                    "w_mm": 40,
                    "h_mm": 25,
                },
                {
                    "type": "panel",
                    "id": "figs/Fig1b.pdf",
                    "x_mm": 60,
                    "y_mm": 5,
                    "w_mm": 40,
                    "h_mm": 25,
                },
            ],
            formats=("pdf",),
        ),
        tmp_path / "out2",
    )
    assert _run(job, project, provider)["status"] == "done"


def test_a_source_replaced_by_a_bigger_file_is_refused_after_reading_at_most_one_extra_byte(
    project, provider, tmp_path, monkeypatch
):
    """Codex #463 第五轮 P2：冻结后文件被换成一个大得多的替身——`read_frozen` 分块、有界：多读一个字节就停、
    报 `source_changed`，不把整个替身读进内存。用一个记账的 `open()` 数它到底读了多少。"""
    import builtins

    from tavotto.rendercore import sources as rcsources

    fs = rcsources.StaticSourceResolver(project).resolve({"id": "figs/Fig1.pdf"})
    frozen = int(fs.artifact.size_bytes)
    Path(fs.path).write_bytes(b"%PDF-1.4\n" + b"x" * (frozen * 8))  # 8 倍大的替身
    seen = {"n": 0}
    real_open = builtins.open

    class Counting:
        def __init__(self, fh):
            self.fh = fh

        def read(self, n=-1):
            data = self.fh.read(n)
            seen["n"] += len(data)
            return data

        def readinto(self, view):
            n = self.fh.readinto(view)
            seen["n"] += n or 0
            return n

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return self.fh.__exit__(*a)

    def counting_open(path, *a, **k):
        fh = real_open(path, *a, **k)
        return Counting(fh) if str(path) == str(fs.path) else fh

    monkeypatch.setattr(rcsources, "open", counting_open, raising=False)
    with pytest.raises(rcsources.SourceError) as ei:
        rcsources.read_frozen(fs)
    assert ei.value.code == "source_changed" and ei.value.params["frozen_bytes"] == frozen
    assert seen["n"] <= frozen + rcsources.READ_CHUNK, seen  # 有界：最多多读一块，不是整个替身
    assert seen["n"] < frozen * 8


def test_read_frozen_holds_one_buffer_not_a_chunk_list_plus_join(project, tmp_path, monkeypatch):
    """Codex #463 第六轮 P2：512 MiB 的源不该在 `b"".join(chunks)` 那一刻变成 1 GiB。判据：读的是 `readinto`
    进预分配缓冲（一份），`read()` 一次不叫（没有 chunk 列表可 join）。"""
    import builtins

    from tavotto.rendercore import sources as rcsources

    fs = rcsources.StaticSourceResolver(project).resolve({"id": "figs/Fig1.pdf"})
    real_open = builtins.open
    calls = {"readinto": 0, "read": 0}

    class Only:
        def __init__(self, fh):
            self.fh = fh

        def readinto(self, view):
            calls["readinto"] += 1
            return self.fh.readinto(view)

        def read(self, n=-1):
            calls["read"] += 1
            return self.fh.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return self.fh.__exit__(*a)

    monkeypatch.setattr(
        rcsources,
        "open",
        lambda path, *a, **k: (
            Only(real_open(path, *a, **k))
            if str(path) == str(fs.path)
            else real_open(path, *a, **k)
        ),
        raising=False,
    )
    data = rcsources.read_frozen(fs)
    assert data == (FIXTURE / "page.pdf").read_bytes()
    assert calls["readinto"] >= 1 and calls["read"] == 0


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


def test_an_empty_panel_source_fails_with_a_structured_source_unreadable(project, tmp_path):
    """#517：面板指向 0 字节的 PDF → `export_render_failed`，reason 带 `source_unreadable`、`why=empty`，
    而不是 `export_failed` 带 `SourceArtifact` 的 ValueError 原文。编译期就失败，用不到字体（provider=None）。"""
    (project / "figs" / "Empty.pdf").write_bytes(b"")
    job = exportjob.prepare(
        _spec(
            [
                {
                    "type": "panel",
                    "id": "figs/Empty.pdf",
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
    payload = _run(job, project, None)
    assert payload["status"] == "failed"
    err = payload["error"]
    assert err["code"] == "export_render_failed", err
    assert err["params"]["id"] == "figs/Empty.pdf"
    assert err["params"]["reason"].startswith("source:source_unreadable:")
    assert err["params"]["why"] == "empty"


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


def test_a_page_with_no_operations_rasterizes_to_blank_bitmaps_and_refuses_eps(
    project, provider, host, tmp_path
):
    """全 hidden 的透明页：能力表上没有任何操作可判、缺口为空——U06 时 PNG 只能 format_failed（Codex #460 P2：
    不把 PDF 字节写进 .png 报成功），U07 起 PNG / TIFF 是真的从 Canonical PDF 栅格出来的**空**位图（合法产物，
    RGBA 全 0）；EPS 仍没有写入器，逐项 format_failed 且理由是结构化的。PDF 那一项照常。"""
    export_dir = tmp_path / "out"
    job = exportjob.prepare(
        _spec(
            [_text(hidden=True)], formats=("pdf", "png", "tiff", "eps"), background="transparent"
        ),
        export_dir,
    )
    payload = _run(job, project, provider, host)
    assert payload["status"] == "partial", payload
    by_fmt = {o["format"]: o for o in payload["outputs"]}
    assert by_fmt["pdf"]["status"] == "done" and by_fmt["pdf"]["vector"] is True
    for fmt in ("png", "tiff"):
        assert by_fmt[fmt]["status"] == "done" and by_fmt[fmt]["vector"] is False, fmt
        assert (export_dir / f"Fig 1.{fmt}").stat().st_size > 0
    assert by_fmt["eps"]["status"] == "failed"
    assert by_fmt["eps"]["error"]["code"] == "eps_not_for_canvas"  # 与旧路同一个稳定码（U08）
    assert by_fmt["eps"]["error"]["params"]["unsupported"][0]["operation"] == "format"
    assert not (export_dir / "Fig 1.eps").exists()
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


def _svg_gap_plan(origins: dict[str, str]):
    """每个 object_id 一块 `imported_page`，来源 = `origins[object_id]`；SVG 都没有同 Figure 表示。"""
    from types import SimpleNamespace

    from tavotto.rendercore import ir
    from tavotto.rendercore.ir import unsupported_for

    resources, children = {}, []
    for oid, origin in origins.items():
        key = f"file:{oid}"
        resources[key] = ir.FileResource(
            source_id=f"{origin}:{oid}",
            kind="pdf",
            sha256="0" * 64,
            origin=origin,
            semantic_identity="sha256:" + "0" * 64,
        )
        children.append(ir.ImportedPage(resource=key, rect=(0, 0, 10, 10), object_id=oid))
    page = ir.Page(100, 100, tuple(children), resources)
    return SimpleNamespace(page=page), unsupported_for(page, "svg")


def test_svg_panel_gap_names_the_right_code_per_source_kind():
    """#1348 Codex P2：脚本图缺 SVG 与静态 PDF 缺 SVG 同画布时，静态 PDF 不得被标成「脚本里的自定义函数」。"""
    from tavotto.rendercore.job import _svg_panel_gap

    rp, gaps = _svg_gap_plan({"a": "execution"})
    assert _svg_panel_gap(rp, gaps, ["execution:a: SVG unavailable: custom canvas"])[0] == (
        "svg_figure_unavailable"
    )
    rp, gaps = _svg_gap_plan({"s": "static"})
    assert _svg_panel_gap(rp, gaps, [])[0] == "svg_source_unsupported"
    rp, gaps = _svg_gap_plan({"a": "execution", "s": "static"})
    code, params = _svg_panel_gap(rp, gaps, [])
    assert code == "svg_source_unsupported"
    assert "execution:a" in params["id"] and "static:s" in params["id"]
