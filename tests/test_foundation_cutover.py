"""U10.cutover：RenderCore 是默认渲染后端之后的 harness 实例 `U08-R1`（enforced，pr lane，ADR 0072）。

主语是**产品的公开 HTTP 入口交回来的字节**（进程内 Flask 客户端，与 U04 / U05 的 `http-inprocess` 同一形态）：
开项目 → `/api/render` 预览 → `/api/export`（画布 PDF + PNG，standard 政策）→ 独立读取器核文件（`tests/support/pdfread.py`
纯标准库读字体对象与文字层、PDFium 字符级文字层、标准库 PNG 解码）→ manifest 的 `backend` / `identity` 说的是 rendercore
→ 结果记录写进 `TAVOTTO_FOUNDATION_RESULTS`（校验器据此判「预期集合 == 已提交有效结果集合」）。

负例（ADR 0072 §4）：环境里写着 `TAVOTTO_RENDER_BACKEND=pymupdf` 时同一条入口明确失败（`backend_retired`），
不是悄悄用 rendercore 画——那条在 `tests/test_rendercore_app.py`；这里只记正例的结果记录。

需要 RenderCore 依赖 + 批准字体（U10 起是运行时闭包；CI 每条腿都取字体）。skip 不是绿：这条 skip 了，
harness 校验步就红。
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

from tavotto import app as m, pdfbackend
from tavotto.engine import exportjob

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import foundation_harness as fh  # noqa: E402
import pdfread  # noqa: E402
import pdftext  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "foundation" / "pdf_png_assets"
CASE_ID = "U08-R1"
HAS = all(
    importlib.util.find_spec(mod) is not None
    for mod in ("pypdfium2", "pikepdf", "uharfbuzz", "PIL")
)
pytestmark = pytest.mark.skipif(not HAS, reason="RenderCore 依赖未装（not_run，不是绿）")


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
    from tavotto.rendercore import facade

    monkeypatch.delenv(pdfbackend.BACKEND_ENV, raising=False)  # 默认就是 rendercore（U10）
    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    facade.reset_for_tests()
    exportjob.reset_for_tests()
    yield m.app.test_client()
    m.reset_projects()
    facade.reset_for_tests()
    exportjob.reset_for_tests()


def _canvas() -> dict:
    return {
        "scope": "canvas",
        "filename": "u10-r1",
        "formats": ["pdf", "png"],
        "ppi": 150,
        "overwrite": "replace",
        "canvas": {
            "page_w_mm": 120,
            "page_h_mm": 60,
            "objects": [
                {"type": "panel", "id": "page.pdf", "x_mm": 5, "y_mm": 5, "w_mm": 67.5, "h_mm": 40},
                {
                    "type": "text",
                    "id": "t1",
                    "text": "Hello 图 ×10⁵",
                    "x_mm": 80,
                    "y_mm": 10,
                    "w_mm": 35,
                    "h_mm": 10,
                    "size_pt": 10,
                },
            ],
        },
    }


def test_u08_r1_default_export_through_the_public_entry_is_rendercore(client, tmp_path):
    ledger = fh.load_ledger()
    case = next(c for c in ledger["cases"] if c["case_id"] == CASE_ID)
    binding = fh.binding_from_environment(
        ledger=ledger, entry=case["entry"], fixture=case["fixture"]
    )
    out_dir = fh.results_dir() or (tmp_path / "results")
    observed: dict = {
        "backend": pdfbackend.selected(),
        "backend_version": pdfbackend.BACKEND_VERSION,
    }
    evidence: list[str] = []
    assert observed["backend"] == "rendercore" == pdfbackend.BACKEND_DEFAULT

    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "page.pdf").write_bytes((FIXTURE / "page.pdf").read_bytes())
    m.open_project(str(figs))

    # ---- 预览：PreviewCache 交回桶宽的 PNG（不是空图）
    t0 = time.time()
    resp = client.get("/api/render?id=page.pdf&w=400")
    assert resp.status_code == 200, resp.get_json()
    w, h, _bpp, px = pdfread.decode_png_any(resp.get_data())
    assert w == 400 and h > 0 and any(b != 255 for b in px[: 4 * w * h : 7])
    observed["render_s"] = round(time.time() - t0, 2)
    evidence.append(f"/api/render: PNG {w}x{h} from the render child")

    # ---- 画布导出：PDF + PNG 出自同一份 Canonical PDF；产物核验（standard）
    t0 = time.time()
    body = client.post("/api/export", json=_canvas()).get_json()
    observed["export_s"] = round(time.time() - t0, 2)
    assert body["status"] == "done", body
    out_pdf = next(o for o in body["outputs"] if o["format"] == "pdf")
    out_png = next(o for o in body["outputs"] if o["format"] == "png")
    export_dir = Path(body["export_dir"])
    pdf_path, png_path = export_dir / out_pdf["name"], export_dir / out_png["name"]
    assert pdf_path.is_file() and png_path.is_file()
    # 独立读取器：面板是 Form XObject、文字层带用户原文、字体是嵌入的批准子集
    objs = pdfread.objects(pdf_path.read_bytes())
    head, content = pdfread.page(objs)
    assert b"Do" in content and b"TJ" in content
    fonts = pdfread.fonts(objs, head)
    assert fonts and all(f["program"] for f in fonts.values()), fonts
    assert {f["base"] for f in fonts.values()} >= {"LiberationSerif", "NotoSansSC-Regular"}
    text = pdftext.text(pdf_path)
    assert "Hello 图 ×10⁵" in text, text
    pw, ph, _bpp, _px = pdfread.decode_png_any(png_path.read_bytes())
    assert (pw, ph) == (out_png["dimensions"]["px"][0], out_png["dimensions"]["px"][1])
    assert (pw, ph) == (round(120 / 25.4 * 150), round(60 / 25.4 * 150))
    # manifest：后端 / 身份 / 核验结论
    mf = out_pdf["manifest"]
    assert mf["backend"] == "rendercore"
    assert mf["verdict"] == "accepted", mf  # standard 政策：必需项全过才发布（ADR 0068）
    assert mf["checks"]["text_layer"] == "verified", mf["checks"]
    # 夹具页自己用的是未嵌入的 base-14 Helvetica（truth.json：font_embedded=false）——检查器如实报
    # 可选项 `fonts_embedded` failed，那是外来页的性质不是写入器的；画布文字的脸都真嵌了（上面按对象核过）
    assert mf["checks"]["fonts_embedded"] == "failed" and "Helvetica" in mf["fonts_used"], mf
    assert mf["identity"]["render"] and mf["identity"]["artifact"]
    observed["export"] = {
        "outputs": [{k: o[k] for k in ("format", "name", "status")} for o in body["outputs"]],
        "pdf_manifest": {
            "verdict": mf["verdict"],
            "checks": mf["checks"],
            "identity": mf["identity"],
        },
        "png_px": [pw, ph],
        "fonts": sorted(f["base"] for f in fonts.values()),
    }
    evidence.append(
        f"export: {out_pdf['name']} ({len(fonts)} embedded faces, text layer verified), {out_png['name']} {pw}x{ph}"
    )

    record = fh.ResultRecord(
        case_id=CASE_ID,
        binding=binding,
        product_outcome="automatic",
        test_verdict="pass",
        observed=observed,
        evidence=tuple(evidence),
    )
    path = fh.write_result(record, out_dir)
    assert path.is_file()
