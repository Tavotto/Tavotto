"""RenderCore 接进 `app.py` 的真实入口（统一实施包 U08 候选、U10 起默认，ADR 0067 / 0072）：HTTP 导出（同步 /
异步 + SSE）、`/api/render`、关停、带 override 的面板经 worker + 回执。

主语是**真的 Flask 端点交回来的东西与磁盘上的文件**——独立读取器（`tests/support/pdfread.py` / PDFium）
检查产物而不只信返回 JSON（05 §5）。这里每一条都是旧契约用例在候选后端下的替代证据（ledger
`candidate_parity.deselected` 里点名的那几条），或候选独有的入口合同（503 背压、关停 reap、回执随源）。

需要 RenderCore 依赖 + 批准字体（U10 起是运行时闭包）；不在的机器 skip 并写明理由。带 override 的那条还要
一个装了 matplotlib 的解释器（与 `test_mcp_normalize.py` 同一判据）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tavotto import app as m, pdfbackend
from tavotto.engine import exportjob, pool as engine_pool, project_watch as engine_watch

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import pdfread  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "foundation" / "pdf_png_assets"
HAS_CANDIDATE = all(
    importlib.util.find_spec(mod) is not None
    for mod in ("pypdfium2", "pikepdf", "uharfbuzz", "PIL")
)
pytestmark = pytest.mark.skipif(not HAS_CANDIDATE, reason="RenderCore 依赖未装（not_run，不是绿）")


def _worker_python():
    try:
        return engine_pool.find_worker_python()
    except engine_pool.WorkerError:
        return None


@pytest.fixture(scope="module", autouse=True)
def _fonts_and_child():
    from tavotto.rendercore import fonts, renderhost

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}；先跑 scripts/fetch_fonts.py")
    yield
    renderhost.shutdown_shared()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv(pdfbackend.BACKEND_ENV, raising=False)  # 默认就是 rendercore（U10）
    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    exportjob.reset_for_tests()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()
    exportjob.reset_for_tests()


def _project(tmp_path: Path) -> Path:
    figs = tmp_path / "figs"
    figs.mkdir(exist_ok=True)
    (figs / "p1.pdf").write_bytes((FIXTURE / "page.pdf").read_bytes())
    (figs / "r1.png").write_bytes((FIXTURE / "original.png").read_bytes())
    m.open_project(str(figs))
    return figs


def _canvas(**over) -> dict:
    spec = {
        "scope": "canvas",
        "filename": "Fig 1",
        "formats": ["pdf", "png"],
        "ppi": 150,
        "overwrite": "replace",
        "canvas": {
            "page_w_mm": 120,
            "page_h_mm": 60,
            "objects": [
                {"type": "panel", "id": "p1.pdf", "x_mm": 5, "y_mm": 5, "w_mm": 67.5, "h_mm": 40},
                {
                    "type": "text",
                    "id": "t1",
                    "text": "Hello 图",
                    "x_mm": 80,
                    "y_mm": 10,
                    "w_mm": 30,
                    "h_mm": 10,
                    "size_pt": 10,
                },
            ],
        },
    }
    spec.update(over)
    return spec


def _out(body: dict, fmt: str) -> dict:
    return next(o for o in body["outputs"] if o["format"] == fmt)


def _png_rgb(path: Path) -> tuple[tuple[int, int], bytes]:
    w, h, bpp, px = pdfread.decode_png_any(path.read_bytes())
    if bpp == 4:
        px = b"".join(px[i : i + 3] for i in range(0, len(px), 4))
    return (w, h), px


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
def test_export_canvas_under_the_candidate_writes_a_searchable_pdf_and_a_png_from_it(
    client, tmp_path
):
    """RC-086：真实端点 → 最终字节。PDF 有文字层与 Form（面板）；PNG 是同一份 PDF 栅格出来的
    （把交付的 PDF 再栅格一次逐字节相同）；回执里 `vector` 与尺寸如实。"""
    from tavotto.rendercore import facade, raster

    _project(tmp_path)
    resp = client.post("/api/export", json=_canvas())
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "done", body
    out_dir = Path(body["export_dir"])
    pdf, png = _out(body, "pdf"), _out(body, "png")
    assert pdf["vector"] is True and png["vector"] is False
    assert pdf["dimensions"]["mm"] == [120.0, 60.0]
    assert png["dimensions"]["px"] == [709, 354]  # round(340.16·150/72), round(170.08·150/72)
    objs = pdfread.objects((out_dir / pdf["name"]).read_bytes())
    _head, content = pdfread.page(objs)
    assert b"TJ" in content and b"Do" in content
    (w, h), _ = _png_rgb(out_dir / png["name"])
    assert (w, h) == (709, 354)
    again = facade.host().render(out_dir / pdf["name"], dpi=150.0, transparent=False)
    assert raster.encode_png(again) == (out_dir / png["name"]).read_bytes()
    assert not list(out_dir.glob(f"{exportjob.TMP_PREFIX}*")), "作业临时目录没清"
    assert body["files"] and {f["name"] for f in body["files"]} == {pdf["name"], png["name"]}


def test_export_canvas_eps_under_the_candidate_reports_the_old_stable_code(client, tmp_path):
    """RC-005 / RC-090：画布合成给不出 EPS，报的是老客户端认识的 `eps_not_for_canvas`，别的格式照常。"""
    _project(tmp_path)
    body = client.post("/api/export", json=_canvas(formats=["pdf", "eps"])).get_json()
    assert body["status"] == "partial", body
    assert _out(body, "pdf")["status"] == "done"
    eps = _out(body, "eps")
    assert eps["status"] == "failed" and eps["error"]["code"] == "eps_not_for_canvas"
    assert not (Path(body["export_dir"]) / "Fig 1.eps").exists()


def test_export_original_under_the_candidate_copies_the_png_byte_for_byte(client, tmp_path):
    figs = _project(tmp_path)
    spec = {
        "scope": "original",
        "filename": "Orig",
        "formats": ["png", "tiff"],
        "ppi": 600,
        "overwrite": "replace",
        "original": {"figure_id": "r1.png", "source_kind": "raster", "px_w": 64, "px_h": 48},
    }
    body = client.post("/api/export", json=spec).get_json()
    assert body["status"] == "done", body
    out_dir = Path(body["export_dir"])
    assert (out_dir / "Orig.png").read_bytes() == (figs / "r1.png").read_bytes()
    assert _out(body, "png")["dimensions"]["px"] == [64, 48]
    assert _out(body, "tiff")["dimensions"]["px"] == [64, 48]


def test_a_child_failure_under_the_candidate_is_partial_and_leaves_nothing_behind(
    client, tmp_path, monkeypatch
):
    """替代 `test_export_pipeline.py::test_nothing_is_left_behind_when_a_format_fails`（那条经 monkeypatch
    `compose` 注错，候选路不经 `compose`）：child 挂了 → PNG `format_failed` 带 `raster_code`、PDF 照常、
    `partial`、临时目录清掉。"""
    from tavotto.rendercore import facade, renderhost

    _project(tmp_path)

    class DeadHost:
        def render(self, *a, **kw):
            raise renderhost.RenderChildError("render_child_died", "注入：child 没了")

        def probe(self, pdf, **kw):
            return renderhost.shared().probe(pdf, **kw)

    monkeypatch.setattr(facade, "host", lambda: DeadHost())
    body = client.post("/api/export", json=_canvas()).get_json()
    assert body["status"] == "partial", body
    assert _out(body, "pdf")["status"] == "done"
    png = _out(body, "png")
    assert png["status"] == "failed" and png["error"]["code"] == "format_failed"
    assert png["error"]["params"]["raster_code"] == "render_child_died"
    out_dir = Path(body["export_dir"])
    assert (out_dir / "Fig 1.pdf").exists() and not (out_dir / "Fig 1.png").exists()
    assert not list(out_dir.glob(f"{exportjob.TMP_PREFIX}*"))


def test_a_writer_failure_under_the_candidate_is_a_500_and_captures_no_telemetry(
    client, tmp_path, monkeypatch, telemetry_sent
):
    """替代 `test_export_endpoint.py::test_failed_export_captures_nothing`：写入器炸了 → 500，遥测一条不记。"""
    from tavotto.engine import telemetry
    from tavotto.rendercore import pdfwriter

    _project(tmp_path)

    def boom(*a, **kw):
        raise OSError("磁盘满了")

    monkeypatch.setattr(pdfwriter, "write_pdf", boom)
    resp = client.post("/api/export", json=_canvas(formats=["pdf"]))
    assert resp.status_code == 500
    assert resp.get_json()["code"] in ("export_failed", "format_failed")
    telemetry.flush(timeout=5)
    assert [p for p in telemetry_sent if "export" in json.dumps(p)] == []


def test_export_start_under_the_candidate_streams_progress_and_finishes(client, tmp_path):
    """异步入口（`/api/export/start` + `/state`）在候选后端下同样跑通；终局字段先于终局 status。"""
    _project(tmp_path)
    body = client.post("/api/export/start", json=_canvas(formats=["pdf"])).get_json()
    job_id = body["job_id"]
    deadline = time.time() + 60
    state = None
    while time.time() < deadline:
        state = client.get(f"/api/export/state?job_id={job_id}").get_json()
        if state["status"] in ("done", "partial", "failed", "cancelled"):
            break
        time.sleep(0.05)
    assert state and state["status"] == "done", state
    assert state["timing"]["elapsed_ms"] is not None
    assert (Path(state["export_dir"]) / "Fig 1.pdf").read_bytes()[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# /api/render
# ---------------------------------------------------------------------------
def test_api_render_under_the_candidate_serves_a_png_of_the_bucket_width_from_the_cache(
    client, tmp_path
):
    from tavotto.rendercore import facade

    _project(tmp_path)
    resp = client.get("/api/render?id=p1.pdf&w=200")
    assert resp.status_code == 200 and resp.mimetype == "image/png"
    data = resp.get_data()
    w, h, _bpp, _px = pdfread.decode_png_any(data)
    assert (w, h) == (200, 119)
    cache = facade.preview_cache()
    renders = cache.renders
    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200
    assert cache.renders == renders, "同键第二次请求不该再渲染"
    files = [p for p in (tmp_path / "_cache").glob("*.png") if not p.name.endswith(".part.png")]
    assert len(files) == 1


def test_api_render_under_the_candidate_reports_backpressure_as_503_and_other_failures_as_500(
    client, tmp_path, monkeypatch
):
    from tavotto.rendercore import preview

    _project(tmp_path)

    def full(self, *a, **kw):
        raise preview.PreviewError("render_queue_full", "队列满了")

    monkeypatch.setattr(preview.PreviewCache, "get", full)
    resp = client.get("/api/render?id=p1.pdf&w=200")
    assert resp.status_code == 503 and resp.headers.get("Retry-After") == "1"
    assert resp.get_json()["code"] == "render_queue_full"

    def dead(self, *a, **kw):
        raise preview.PreviewError("render_child_timeout", "超时")

    monkeypatch.setattr(preview.PreviewCache, "get", dead)
    resp = client.get("/api/render?id=p1.pdf&w=200")
    assert resp.status_code == 500 and resp.get_json()["code"] == "render_child_timeout"


@pytest.mark.skipif(
    importlib.util.find_spec("pymupdf") is None, reason="要两个后端同在（rc-venv）；not_run"
)
def test_switching_the_backend_changes_the_preview_cache_key(client, tmp_path, monkeypatch):
    """ledger `BACKEND_NAME`：同一 PDF 换后端身份后 `/api/render` 必须重画（键含后端名 + build）。
    U10 之前这里真的在 pymupdf 与 rendercore 之间切；旧后端删掉之后（ADR 0072）把「另一个后端」换成
    另一个 build 串——键里那一维的主语没变。旧后端时代留下的 `<sha1>.png` 缓存文件同理不会被命中，
    只占预算、按 mtime 淘汰。"""
    from tavotto.rendercore import facade, preview as rc_preview

    _project(tmp_path)
    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200
    monkeypatch.setattr(rc_preview, "BACKEND_VERSION", "99.9.9")
    facade.reset_for_tests()
    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200
    files = [p for p in (tmp_path / "_cache").glob("*.png") if not p.name.endswith(".part.png")]
    assert len(files) == 2, files


def test_the_retired_backend_name_is_refused_at_the_real_entry_not_swapped(
    client, tmp_path, monkeypatch
):
    """负例（06 §1 / ADR 0072）：环境里还写着 `TAVOTTO_RENDER_BACKEND=pymupdf` 的机器，`/api/render` 与导出
    要明确失败（`backend_retired`），不是悄悄用 rendercore 画——「静默换实现」是退役扫描要挡的那种形状。"""
    _project(tmp_path)
    monkeypatch.setenv(pdfbackend.BACKEND_ENV, "pymupdf")
    resp = client.get("/api/render?id=p1.pdf&w=200")
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["code"] == "backend_retired" and body["params"]["value"] == "pymupdf"
    assert "ADR 0072" in body["error"]
    assert not list((tmp_path / "_cache").glob("*.png"))
    resp = client.post("/api/export", json=_canvas(formats=["pdf"]))
    assert resp.status_code == 500 and resp.get_json()["code"] in (
        "backend_retired",
        "export_failed",
    )
    assert "退役" in json.dumps(resp.get_json(), ensure_ascii=False)


def test_a_missing_runtime_dependency_is_backend_unavailable_not_a_fallback(
    client, tmp_path, monkeypatch
):
    """负例（06 §1 / ADR 0072）：闭包不完整（pypdfium2 没装：pip 装漏 / 冻结产物没收）时 `/api/render` 报
    `backend_unavailable` 并点名缺的包——不是 `render_child_died`（把安装问题伪装成崩溃），更不是换个库画。
    把 pypdfium2 藏起来的办法是让 `find_spec` 回 None：主语是「起 child 之前的检查」，不真卸包。"""
    import importlib.util

    from tavotto.rendercore import facade, renderhost

    _project(tmp_path)
    real = importlib.util.find_spec

    def hidden(name, *a, **k):
        return None if name == "pypdfium2" else real(name, *a, **k)

    renderhost.shutdown_shared()
    facade.reset_for_tests()
    monkeypatch.setattr(importlib.util, "find_spec", hidden)
    resp = client.get("/api/render?id=p1.pdf&w=200")
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["code"] == "backend_unavailable" and "pypdfium2" in body["params"]["reason"]
    assert not list((tmp_path / "_cache").glob("*.png"))
    monkeypatch.undo()
    facade.reset_for_tests()
    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200  # 包回来了就正常


def test_reset_projects_reaps_the_render_child(client, tmp_path):
    """render child 是应用运行时的一部分：关项目 / 关应用时一并收掉并 reap（ADR 0066）。"""
    from tavotto.rendercore import renderhost

    _project(tmp_path)
    assert client.get("/api/render?id=p1.pdf&w=200").status_code == 200
    host = renderhost.shared()
    pid = host.pid
    assert pid is not None
    m.reset_projects(wait=True)
    assert renderhost._SHARED is None
    assert host.last_exit is not None  # 被 wait() 回收过
    with pytest.raises(OSError):
        os.kill(pid, 0)


# ---------------------------------------------------------------------------
# 带 override 的面板：worker 现画 + 回执随源产物进 RenderPlan
# ---------------------------------------------------------------------------
SCRIPT = """\
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent


def main():
    fig, ax = plt.subplots(figsize=(80 / 25.4, 50 / 25.4))
    ax.plot([0, 1, 2], [1, 3, 2], lw=1.0, label="series-a")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Signal (V)")
    ax.legend(frameon=False)
    fig.savefig(OUT / "Fig1.pdf")


if __name__ == "__main__":
    main()
"""


@pytest.mark.skipif(_worker_python() is None, reason="没有带科学栈的解释器（not_run）")
def test_a_panel_with_overrides_is_rendered_by_the_worker_and_carries_a_receipt(
    client, tmp_path, monkeypatch
):
    """RC-017 / ADR 0053：带 override 的面板由当次权威 worker 现画（不拿磁盘原件冒充），源产物带
    `origin=execution` + `receipt_id` / `receipt_identity` / `patch_hash`；中间文件在作业私有目录里，
    作业结束后不留。"""
    from tavotto.rendercore import job as rc_job

    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "fig1.py").write_text(SCRIPT, encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(
        json.dumps({"scripts": {"fig1.py": {"entry": "main", "cost": "light", "stems": ["Fig1"]}}}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [_worker_python(), str(figs / "fig1.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(figs),
    )
    assert proc.returncode == 0, proc.stderr
    m.open_project(str(figs))

    seen: dict = {}
    real_produce = rc_job.produce

    def spy(job, tmp_dir, *, sources, provider, host=None):
        class Spy:
            def resolve(self, obj):
                fs = sources.resolve(obj)
                seen[str(obj.get("id"))] = fs
                return fs

        return real_produce(job, tmp_dir, sources=Spy(), provider=provider, host=host)

    monkeypatch.setattr(rc_job, "produce", spy)
    spec = _canvas(formats=["pdf"])
    spec["canvas"]["objects"][0] = {
        "type": "panel",
        "id": "Fig1.pdf",
        "x_mm": 5,
        "y_mm": 5,
        "w_mm": 80,
        "h_mm": 50,
        "overrides": [{"gid": "axes_0.xlabel", "prop": "text", "value": "Overridden (s)"}],
    }
    resp = client.post("/api/export", json=spec)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "done", body
    # override 真的写进去了：worker 一条 warning 都没报（编译期的 cjk_face 事实是画布文字的，不是它的）
    assert [w for w in body["warnings"] if w.startswith("Fig1.pdf")] == [], body["warnings"]
    fs = seen["Fig1.pdf"]
    art = fs.artifact
    assert art.origin == "execution" and art.receipt_id and art.receipt_identity
    assert art.patch_hash and art.source_id == "Fig1.pdf"
    assert exportjob.TMP_PREFIX in str(fs.path) and not Path(fs.path).exists()
    # 导出的 PDF 里画的是 override 之后的图：文字层里有新的 x 轴标题（独立读取器 PDFium）
    import pypdfium2 as pdfium

    out = Path(body["export_dir"]) / "Fig 1.pdf"
    doc = pdfium.PdfDocument(str(out))
    try:
        page = doc[0]
        tp = page.get_textpage()
        text = tp.get_text_range()
        tp.close()
        page.close()
    finally:
        doc.close()
    assert "Overridden" in text and "Time (s)" not in text


# ---------------------------------------------------------------------------
# 有限产物验证（ADR 0068）在候选下：计划半张来自 RenderPlan，文字层 / 字体 / 载体真的被核
# ---------------------------------------------------------------------------
def _text_only(**over) -> dict:
    spec = _canvas(**over)
    spec["canvas"]["objects"] = [o for o in spec["canvas"]["objects"] if o["type"] == "text"]
    return spec


def test_export_under_the_candidate_verifies_text_layer_fonts_and_carrier_from_the_plan(
    client, tmp_path
):
    _project(tmp_path)
    body = client.post("/api/export", json=_text_only()).get_json()
    assert body["status"] == "done", body
    mf = _out(body, "pdf")["manifest"]
    assert mf["backend"] == "rendercore" and mf["plan_identity"].startswith("sha256:")
    assert mf["checks"]["text_layer"] == "verified"  # 期望的行「Hello 图」从 ToUnicode 抽回来了
    assert mf["checks"]["fonts_embedded"] == "verified" and mf["checks"]["carrier"] == "verified"
    assert mf["fonts_used"] == ["LiberationSerif", "NotoSansSC-Regular"]
    png = _out(body, "png")["manifest"]
    assert png["checks"]["dpi_tag"] == "verified"  # 候选的 PNG 写真实 ppi 的 pHYs
    # 面板里的源页用的是没嵌入的 Helvetica（夹具）：候选如实报 fonts_embedded failed（可选项，standard 照样交付）
    body = client.post("/api/export", json=_canvas(filename="Panel")).get_json()
    assert body["status"] == "done", body
    mf = _out(body, "pdf")["manifest"]
    assert mf["checks"]["fonts_embedded"] == "failed" and mf["verdict"] == "accepted"
    assert "Helvetica" in mf["fonts_used"]


def test_a_child_that_renders_the_wrong_pixel_size_is_caught_by_the_size_check(
    client, tmp_path, monkeypatch
):
    """RC-064 经候选路：计划里的期望像素按页面 × dpi 算、**不抄 child 回来的尺寸**——child 把 dpi 算错
    （这里模拟成按一半 dpi 出图）时，检查器量到的像素与计划不符 → PNG `artifact_rejected`、不发布；PDF 照常。
    两边同源的话（计划抄 buf.width / buf.height）这条永远绿（Codex #476 第二轮 P2）。"""
    from tavotto.rendercore import facade

    real_host = facade.host()

    class HalfDpiHost:
        def render(self, pdf_path, *, dpi, transparent, page_size_pt=None, **kw):
            return real_host.render(
                pdf_path, dpi=dpi / 2.0, transparent=transparent, page_size_pt=page_size_pt, **kw
            )

        def __getattr__(self, name):
            return getattr(real_host, name)

    monkeypatch.setattr(facade, "host", lambda: HalfDpiHost())
    _project(tmp_path)
    body = client.post("/api/export", json=_canvas(filename="HalfDpi")).get_json()
    assert body["status"] == "partial", body
    png = _out(body, "png")
    assert png["status"] == "failed" and png["error"]["code"] == "artifact_rejected"
    assert png["manifest"]["checks"]["size"] == "failed"
    # 检查器量到的是 child 真写出的像素（一半），计划里的是页面 × 150 ppi
    assert png["manifest"]["px"] == [round(120 / 25.4 * 75), round(60 / 25.4 * 75)]
    assert _out(body, "pdf")["status"] == "done"
    assert not list(Path(body["export_dir"]).glob("HalfDpi*.png")), (
        "不合格的 PNG 不许出现在导出目录"
    )


def test_strict_export_under_the_candidate_accepts_clean_text_and_rejects_low_ppi_panels(
    client, tmp_path
):
    """D08：严格政策下，纯文字画布全部必需项 verified → 交付；位图面板的有效 ppi（64 px 铺 67.5 mm ≈ 24 ppi）
    低于规范 → `image_ppi` failed → 不发布。阈值来自出版规范，不是这里写的数。"""
    _project(tmp_path)
    body = client.post(
        "/api/export",
        json=_text_only(filename="StrictText", ppi=600, inspection={"mode": "strict"}),
    ).get_json()
    assert body["status"] == "done", body
    mf = _out(body, "pdf")["manifest"]
    assert mf["policy"] == "strict" and mf["verdict"] == "accepted"
    png = _out(body, "png")["manifest"]
    assert png["verdict"] == "accepted" and png["checks"]["raster_density"] == "verified"
    # 同一张画布 150 ppi 就过不了规范的 300：PNG 那一项被拦、PDF 照常（partial）
    body = client.post(
        "/api/export",
        json=_text_only(filename="StrictLow", ppi=150, inspection={"mode": "strict"}),
    ).get_json()
    assert body["status"] == "partial", body
    assert _out(body, "png")["error"]["params"]["failed"] == "raster_density"
    assert not (Path(body["export_dir"]) / "StrictLow.png").exists()
    assert all(
        mf["checks"][k] == "verified"
        for k in ("integrity", "size", "carrier", "fonts_embedded", "text_layer")
    )
    assert mf["checks"]["image_ppi"] == "not_applicable"
    spec = _text_only(filename="StrictRaster", formats=["pdf"], inspection={"mode": "strict"})
    spec["canvas"]["objects"].append(
        {"type": "panel", "id": "r1.png", "x_mm": 5, "y_mm": 5, "w_mm": 67.5, "h_mm": 40}
    )
    body = client.post("/api/export", json=spec).get_json()
    assert body["status"] == "failed", body
    o = _out(body, "pdf")
    assert (
        o["error"]["code"] == "artifact_rejected" and "image_ppi" in o["error"]["params"]["failed"]
    )
    assert o["manifest"]["checks"]["image_ppi"] == "failed" and o["manifest"]["carrier"] == "mixed"
    assert not (Path(body["export_dir"]) / "StrictRaster.pdf").exists()


# ---------------------------------------------------------------------------
# 写回携带标注（RC-058 / RC-059 / RC-060）在候选下：事务是 app 的既有权威，标注经契约层的 annotate_asset
# ---------------------------------------------------------------------------
def _writeback_env(client, tmp_path, monkeypatch, *, staged_pdf: bytes):
    """复用 `test_write_back.py` 的假 worker（写回事务与真实渲染无关），只把重放侧导出的 PDF 换成给定字节。"""
    import test_write_back as wb

    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    figs = wb._figs(tmp_path)
    (figs / "Fig1.pdf").write_bytes((FIXTURE / "page.pdf").read_bytes())
    hot, fresh = wb._pair(figs, tmp_path)

    def export(stem, patches, path, fmt="pdf", dpi=600):
        fresh.calls.append(fmt)
        Path(path).write_bytes(staged_pdf if fmt == "pdf" else b"\x89PNG\r\n\x1a\nplaceholder")
        return {"ok": True, "path": path, "warnings": []}

    fresh.export = export
    wb._use(monkeypatch, hot, fresh)
    return figs


_ANNOTATIONS = [
    {
        "type": "text",
        "id": "n",
        "text": "note",
        "x_mm": 5,
        "y_mm": 5,
        "w_mm": 30,
        "h_mm": 8,
        "size_pt": 9,
    }
]


def test_writeback_with_annotations_under_the_candidate_overlays_the_staged_pdf_then_commits(
    client, tmp_path, monkeypatch
):
    from tavotto.rendercore import inspector

    figs = _writeback_env(
        client, tmp_path, monkeypatch, staged_pdf=(FIXTURE / "page.pdf").read_bytes()
    )
    resp = client.post(
        "/api/engine/update_source",
        json={"id": "Fig1.pdf", "patches": [], "annotations": _ANNOTATIONS},
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert sorted(body["updated"]) == ["Fig1.pdf", "Fig1.png"]
    o = inspector.observe_pdf(figs / "Fig1.pdf")
    assert o["integrity"] == "verified" and "note" in o["text_lines"]
    assert "U00 fixture: y = 3x + 1" in o["text_lines"]  # 源内容流没动
    assert o["census"]["forms"] == 1  # 覆盖层是一个 Form
    w, h, _bpp, _px = pdfread.decode_png_any((figs / "Fig1.png").read_bytes())
    assert (w, h) == (
        2250,
        1333,
    )  # 600 dpi 由同一份注好的 PDF 栅格：round(270·600/72), round(160·600/72)
    assert not [p for p in figs.iterdir() if p.name.endswith(".updating")]


@pytest.mark.parametrize("kind", ["broken", "encrypted"])
def test_writeback_with_annotations_fails_closed_when_the_staged_pdf_is_unusable(
    client, tmp_path, monkeypatch, kind
):
    """RC-059 / RC-060：staging PDF 是坏的 / 加密的 → annotate 结构化失败 → 409、原件零改动、临时文件清干净；
    不承诺「加密文件也能注」。"""
    import pikepdf

    if kind == "broken":
        staged = b"%PDF-1.4 not really"
    else:
        with pikepdf.open(str(FIXTURE / "page.pdf")) as pdf:
            pdf.save(str(tmp_path / "enc.pdf"), encryption=pikepdf.Encryption(owner="o", user="u"))
        staged = (tmp_path / "enc.pdf").read_bytes()
    figs = _writeback_env(client, tmp_path, monkeypatch, staged_pdf=staged)
    before_pdf, before_png = (figs / "Fig1.pdf").read_bytes(), (figs / "Fig1.png").read_bytes()
    resp = client.post(
        "/api/engine/update_source",
        json={"id": "Fig1.pdf", "patches": [], "annotations": _ANNOTATIONS},
    )
    assert resp.status_code != 200, resp.get_json()
    assert (figs / "Fig1.pdf").read_bytes() == before_pdf
    assert (figs / "Fig1.png").read_bytes() == before_png
    assert not [p for p in figs.iterdir() if p.name.endswith(".updating")]
