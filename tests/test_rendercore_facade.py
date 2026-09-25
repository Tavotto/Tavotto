"""pdfbackend 契约的实现（`rendercore/facade.py`）与选择开关（统一实施包 U08 引入、U10 切默认：ADR 0067 / 0072）。

三组主语：

1. **开关**（任何机器）：默认 `rendercore`（未设 / 空串同义）；`TAVOTTO_RENDER_BACKEND=pymupdf` 当场
   `BackendSelectionError(backend_retired)`、不认识的取值 `backend_unknown`；实现被选中而它的包 / 字体不在
   → 结构化异常，**绝不**悄悄换成别的库。
2. **对旧后端的批准资产**（`tests/fixtures/legacy_pymupdf/oracle.json`，PyMuPDF 1.28.2 退役前记下的返回值）：
   同一输入返回结构逐键相同、数值在批准的迁移量之内；U08 时这里是两个后端同进程对拍，U10 删掉旧实现后
   参照改成冻结资产（06 §3：历史差分只在批准资产里）。
3. **实现自己的合同**：Canvas 面 PNG / TIFF 同一 Canonical PDF；原图逐字节复制；标注覆盖不重画；
   独立读取器（pypdfium2 / `tests/support/pdfread.py`）检查产物而不只信返回值。
"""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from tavotto import pdfbackend

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import pdfread  # noqa: E402
import tiffcheck  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "foundation" / "pdf_png_assets"
LEGACY = ROOT / "tests" / "fixtures" / "legacy_pymupdf"
HAS_CANDIDATE = all(
    importlib.util.find_spec(m) is not None for m in ("pypdfium2", "pikepdf", "uharfbuzz", "PIL")
)
needs_candidate = pytest.mark.skipif(
    not HAS_CANDIDATE, reason="RenderCore 依赖未装（not_run，不是绿）"
)
#: U10 之前「对拍」要两个后端同在；现在参照是冻结资产，条件与实现自己的合同相同
needs_both = needs_candidate


@pytest.fixture
def candidate(monkeypatch):
    """契约层的默认就是 rendercore（U10）；这里只把开关显式钉住，防止外面误设的变量漏进用例。"""
    monkeypatch.delenv(pdfbackend.BACKEND_ENV, raising=False)
    yield


def _oracle() -> dict:
    import json

    return json.loads((LEGACY / "oracle.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fonts_ready():
    if not HAS_CANDIDATE:
        pytest.skip("候选包未装（not_run）")
    from tavotto.rendercore import fonts

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}；先跑 scripts/fetch_fonts.py")


@pytest.fixture(scope="module", autouse=True)
def _reap_child():
    yield
    from tavotto.rendercore import renderhost

    renderhost.shutdown_shared()


# ---------------------------------------------------------------------------
# 1. 开关
# ---------------------------------------------------------------------------
def test_the_default_backend_is_rendercore_and_the_contract_names_follow_the_selection(monkeypatch):
    monkeypatch.delenv(pdfbackend.BACKEND_ENV, raising=False)
    assert pdfbackend.selected() == "rendercore" == pdfbackend.BACKEND_DEFAULT
    monkeypatch.setenv(pdfbackend.BACKEND_ENV, "")
    assert pdfbackend.selected() == "rendercore"
    monkeypatch.setenv(pdfbackend.BACKEND_ENV, " RenderCore ")
    assert pdfbackend.selected() == "rendercore"
    # 常量按选中的实现取：名字不同于旧后端 → /api/render 的旧缓存键天然失效（ledger：BACKEND_NAME）
    assert pdfbackend.BACKEND_NAME == "rendercore"
    from tavotto import rendercore

    assert pdfbackend.BACKEND_VERSION == rendercore.BACKEND_VERSION
    assert pdfbackend.BACKENDS == ("rendercore",)


def test_an_unknown_backend_name_is_an_error_not_a_default(monkeypatch):
    """写错了要当场看见，不猜、不退默认——退默认会让 `TAVOTTO_RENDER_BACKEND=rendercoer` 的机器一直以为
    自己在跑另一个后端。"""
    monkeypatch.setenv(pdfbackend.BACKEND_ENV, "rendercoer")
    with pytest.raises(pdfbackend.BackendSelectionError) as exc:
        pdfbackend.selected()
    assert exc.value.code == "backend_unknown"
    with pytest.raises(pdfbackend.BackendSelectionError):
        pdfbackend.probe_asset(FIXTURE / "page.pdf", "pdf")


def test_the_retired_pymupdf_name_is_a_loud_error_not_a_fallback(monkeypatch):
    """U10（ADR 0072）：还写着 `pymupdf` 的旧配置 / 脚本第一次跑就要看见「已退役」，不许静默换成 rendercore
    （那会让「我还在用旧栅格器」这句话在退役之后继续为真）。"""
    monkeypatch.setenv(pdfbackend.BACKEND_ENV, "pymupdf")
    with pytest.raises(pdfbackend.BackendSelectionError) as exc:
        pdfbackend.selected()
    assert exc.value.code == "backend_retired" and "ADR 0072" in str(exc.value)
    with pytest.raises(pdfbackend.BackendSelectionError):
        pdfbackend.text_width("x", 10.0)


def test_warm_loads_the_selected_backend_up_front_and_refuses_a_retired_or_unknown_one(monkeypatch):
    """契约层惰性装载：第一次 `probe_asset` 才 import 实现，旧 facade 却是进程 import 时就带进 PyMuPDF——
    多出来的那 ~100 ms 让 `/api/tutorial` 的第一次探测撞上用户的点击（U08 e2e）。`main()` 起服务前
    `warm()` 一次：选中的实现装载完毕、回它的名字；选错了当场抛，不退默认。U10 起装的是 rendercore（ADR 0072）。"""
    import sys

    monkeypatch.delenv(pdfbackend.BACKEND_ENV, raising=False)
    pdfbackend._IMPLS.clear()
    sys.modules.pop("tavotto.rendercore.facade", None)
    assert "tavotto.rendercore.facade" not in sys.modules
    assert pdfbackend.warm() == "rendercore"
    assert "tavotto.rendercore.facade" in sys.modules, "warm() 之后实现模块必须已经装载"
    assert "warm" not in pdfbackend.__all__
    monkeypatch.setenv(pdfbackend.BACKEND_ENV, "pymupdf")
    with pytest.raises(pdfbackend.BackendSelectionError) as exc:
        pdfbackend.warm()
    assert exc.value.code == "backend_retired"
    monkeypatch.setenv(pdfbackend.BACKEND_ENV, "rendercoer")
    with pytest.raises(pdfbackend.BackendSelectionError) as exc:
        pdfbackend.warm()
    assert exc.value.code == "backend_unknown"


def test_warm_hands_the_selected_implementation_its_background_prewarm(monkeypatch):
    """`warm()` 仍然先把实现装载好（装不上就在启动时抛）；实现若有 `prewarm()`，在这里交给它——候选靠它把
    child / 字体注册表 / CJK 脸三件冷启动挪出用户的第一次操作。没有 `prewarm` 的实现照旧只装载。"""
    import types

    calls = []
    monkeypatch.delenv(pdfbackend.BACKEND_ENV, raising=False)
    monkeypatch.setitem(
        pdfbackend._IMPLS, "rendercore", types.SimpleNamespace(prewarm=lambda: calls.append(1))
    )
    assert pdfbackend.warm() == "rendercore" and calls == [1]
    monkeypatch.setitem(pdfbackend._IMPLS, "rendercore", types.SimpleNamespace())
    assert pdfbackend.warm() == "rendercore" and calls == [1]


def test_the_candidate_prewarm_starts_the_child_and_loads_the_faces(candidate, fonts_ready):
    from tavotto.rendercore import facade

    facade.reset_for_tests()
    facade.prewarm().join(timeout=60)
    assert facade.host().pid is not None, "预热之后 render child 应当已经在跑"
    assert facade._PROVIDER is not None, "预热之后字体注册表应当已经建好"


def _stub_prewarm(monkeypatch, *, ping, faces):
    """把预热的两段换成可控的桩：`ping`（标准库那段）与 `_faces`（原生那段的入口）；回登记的 atexit 回调。"""
    import types

    from tavotto.rendercore import facade

    registered: list[tuple] = []
    monkeypatch.setattr(facade.atexit, "register", lambda fn, *a: registered.append((fn, a)))
    monkeypatch.setattr(facade, "host", lambda: types.SimpleNamespace(ping=ping))
    monkeypatch.setattr(facade, "_faces", faces)
    monkeypatch.setattr(facade, "typography", types.SimpleNamespace(text_width=lambda *a: None))
    t = facade.prewarm()
    assert len(registered) == 1 and registered[0][0] is facade._stop_prewarm, "预热必须登记退出收尾"
    fn, args = registered[0]
    return t, (lambda: fn(*args))


def test_prewarm_never_enters_native_code_after_the_exit_hook_returns(monkeypatch):
    """预热线程是 daemon，原生那一段里有第一次 import pikepdf（nanobind）/ uharfbuzz。解释器收尾时 daemon 线程
    被强制退出、穿过 C++ 栈帧——py3.10 x86_64 上 abort / SIGSEGV（#641）。退出回调返回时线程还卡在 ping 里
    （child 慢，ping 可以等到 60 s），之后走出 ping 也**绝不能**再进原生那一段（Codex #644：有界 join 挡不住）。"""
    import threading

    in_ping, release_ping = threading.Event(), threading.Event()
    native_calls: list[str] = []

    def ping():
        in_ping.set()
        release_ping.wait()

    t, stop = _stub_prewarm(monkeypatch, ping=ping, faces=lambda *a: native_calls.append("faces"))
    assert in_ping.wait(10), "预热线程应当已经在 ping 里"
    stop()  # 解释器收尾前的 atexit：线程还卡在 ping 里（超过任何有界等待）
    release_ping.set()  # 之后 child 回话了
    t.join(10)
    assert not t.is_alive()
    assert native_calls == [], "退出回调返回之后预热线程进了原生那一段"


def test_the_exit_hook_waits_out_a_native_section_already_in_progress(monkeypatch):
    """线程已经在原生那一段里（正在 import / 建脸）：退出回调要等它做完再返回，不能留它在收尾时还在 C++ 里。"""
    import threading

    in_native, release_native = threading.Event(), threading.Event()
    order: list[str] = []

    def faces(*a):
        in_native.set()
        release_native.wait()
        order.append("native_done")

    t, stop = _stub_prewarm(monkeypatch, ping=lambda: None, faces=faces)
    assert in_native.wait(10), "预热线程应当已经在原生那一段里"
    hook = threading.Thread(target=lambda: (stop(), order.append("hook_returned")))
    hook.start()
    hook.join(0.2)
    assert hook.is_alive(), "原生那一段还没做完，退出回调就返回了"
    release_native.set()
    hook.join(10)
    t.join(10)
    assert order == ["native_done", "hook_returned"]


def test_probe_asset_reuses_its_answer_until_the_file_changes(candidate, monkeypatch, tmp_path):
    """素材库每次 `/api/panels` 都把每个素材探一遍（`app.scan_panels`，没有别的缓存）：文件没动就不再问 child
    （`host.requests` 不涨）；换成另一份文件（inode 变了）就重新探；调用方改了回来的 dict 也不污染下一次。
    位图那一档同样（`header_info` 不再读文件）。"""
    if not HAS_CANDIDATE:
        pytest.skip("候选包未装（not_run）")
    from tavotto.rendercore import facade, sources

    monkeypatch.setattr(sources, "RACY_WINDOW_NS", 0)
    monkeypatch.setattr(sources, "FINGERPRINT_TRUSTED", True)
    facade.reset_for_tests()
    src = tmp_path / "fig.pdf"
    shutil.copy(FIXTURE / "page.pdf", src)
    h = facade.host()
    first = pdfbackend.probe_asset(src, "pdf")
    n = h.requests
    again = pdfbackend.probe_asset(src, "pdf")
    assert again == first and h.requests == n
    snapshot = dict(first)
    again["w_pt"] = first["w_pt"] = -1.0  # 调用方改了自己手里的 dict
    assert pdfbackend.probe_asset(src, "pdf") == snapshot
    other = tmp_path / "other.pdf"
    shutil.copy(ROOT / "docs/implementation/tavotto-foundation/evidence/u06/u06.pdf", other)
    other.replace(src)
    changed = pdfbackend.probe_asset(src, "pdf")
    assert changed != snapshot and h.requests == n + 1

    png = tmp_path / "fig.png"
    shutil.copy(FIXTURE / "original.png", png)
    reads = []
    real = facade.rasterio.header_info
    monkeypatch.setattr(
        facade.rasterio, "header_info", lambda *a, **k: reads.append(1) or real(*a, **k)
    )
    assert pdfbackend.probe_asset(png, "raster") == pdfbackend.probe_asset(png, "raster")
    assert reads == [1]


def test_the_candidate_never_falls_back_to_pymupdf_when_its_fonts_are_missing(
    monkeypatch, tmp_path, candidate
):
    """无静默回退政策（06 §1 / ADR 0067）：候选被选中而字体（或候选包）不在，错误就是那个错误，
    `text_width` 不许回一个 PyMuPDF 量出来的数。"""
    from tavotto.rendercore import facade

    facade.reset_for_tests()
    monkeypatch.setenv("TAVOTTO_FONTS_DIR", str(tmp_path / "no-fonts"))
    try:
        with pytest.raises(RuntimeError) as exc:
            pdfbackend.text_width("Hello", 10.0)
        code = getattr(exc.value, "code", "") or type(exc.value).__name__
        assert code in ("face_missing", "CandidatePackagesMissing"), exc.value
        assert pdfbackend.selected() == "rendercore"  # 出错之后仍是候选，没有换回去
    finally:
        facade.reset_for_tests()


def test_the_contract_names_in_all_are_the_ledger_nineteen():
    assert len(pdfbackend.__all__) == 19
    from tavotto.rendercore import facade

    for name in pdfbackend.__all__:
        assert hasattr(facade, name), f"实现缺契约项 {name}"


# ---------------------------------------------------------------------------
# 2. 对旧后端的批准资产（退役前记下的返回值；U08 时是同进程对拍）
# ---------------------------------------------------------------------------
@needs_both
def test_probe_asset_returns_the_same_structure_and_values_as_the_retired_backend(
    candidate, fonts_ready
):
    want = _oracle()["probe_asset"]
    for f in ("original.png", "original_nophys.png"):
        assert pdfbackend.probe_asset(FIXTURE / f, "raster") == want[f]
    new, was = pdfbackend.probe_asset(FIXTURE / "page.pdf", "pdf"), want["page.pdf"]
    assert set(new) == set(was) == {"kind", "w_pt", "h_pt"}
    assert new["kind"] == "pdf" and abs(new["w_pt"] - was["w_pt"]) < 1e-6
    assert abs(new["h_pt"] - was["h_pt"]) < 1e-6


@needs_both
def test_probe_pdf_applies_userunit_exactly_once_like_the_old_backend(
    candidate, fonts_ready, tmp_path
):
    """`/UserUnit 2` 的页报两倍（RC-039：恰好应用一次）。U07 交接把它记成「有意差异」——实测
    **不成立**：PyMuPDF 1.28.2 的 `page.rect` 同样乘了 /UserUnit（540 × 320），忽略它的只是 PDFium 的
    `get_size()`，而 child 的 probe 已经补上那一次。量过之后这一条从差异表里划掉（measure, don't trust the comment）。"""
    import pikepdf

    src = tmp_path / "uu.pdf"
    with pikepdf.open(str(FIXTURE / "page.pdf")) as pdf:
        pdf.pages[0].obj["/UserUnit"] = 2
        pdf.save(str(src))
    new = pdfbackend.probe_asset(src, "pdf")
    assert abs(new["w_pt"] - 540.0) < 1e-6 and abs(new["h_pt"] - 320.0) < 1e-6


@needs_both
def test_pdf_fonts_matches_the_retired_backend_including_fonts_inside_form_xobjects(
    candidate, fonts_ready
):
    """首页的字体名（去子集前缀、去重、保序）——含 Form XObject 里的：旧后端合成的画布把源页放成
    XObject（冻结资产 `calibration/panel_plain.pdf`），那里面的字体也得看见。"""
    want = _oracle()["pdf_fonts"]
    assert pdfbackend.pdf_fonts(FIXTURE / "page.pdf") == want["page.pdf"]
    got = pdfbackend.pdf_fonts(LEGACY / "calibration" / "panel_plain.pdf")
    assert got == want["calibration/panel_plain.pdf"] and "Helvetica" in got


@needs_both
def test_compare_png_is_the_shared_pixelmetrics_ruler_over_the_decoded_buffers(
    candidate, fonts_ready, tmp_path
):
    """尺子只有 `pixelmetrics` 一份：`compare_png` 只换解码器。拿 MuPDF 栅格的冻结 PNG（`preview_300.png`）
    与 PDFium 现渲的比——两个栅格器真的有差异——三指标必须逐值等于直接对解码 buffer 跑 `rgba_metrics`。"""
    from tavotto import pixelmetrics
    from tavotto.rendercore import facade

    a, b = LEGACY / "preview_300.png", tmp_path / "b.png"
    pdfbackend.render_preview_png(FIXTURE / "page.pdf", 300, b)
    got = pdfbackend.compare_png(a, b)
    (ra, size_a), (rb, size_b) = facade._rgba_of(a), facade._rgba_of(b)
    assert size_a == size_b and size_a[0] == 300
    assert got == pixelmetrics.rgba_metrics(ra, rb, size_a, size_b)
    assert got["ok"] and got["changed_pixel_ratio"] > 0  # 判据没量在「两张一样的图」上
    same = pdfbackend.compare_png(a, a)
    assert same["max_abs_diff"] == 0 and same["changed_pixel_ratio"] == 0
    with pytest.raises(Exception):  # noqa: B017 —— 主语是「不返回一个指标」
        pdfbackend.compare_png(a, tmp_path / "missing.png")


@needs_both
def test_text_metrics_agree_with_the_retired_backend_within_the_approved_migration(
    candidate, fonts_ready
):
    """ADR 0060 §4（D07）：advance 兼容（Liberation 与 base-14 同度量）；层归属只在批准的那几类字符上不同
    （fallback 层恒空）。参照是退役前记下的数。"""
    o = _oracle()
    for text in ("Sample", "H2O", "Fig. 1 (a)"):
        assert abs(pdfbackend.text_width(text, 12.0) - o["text_width_12pt"][text]) < 0.2, text
        assert [list(r) for r in pdfbackend.text_plan(text)] == o["text_plan"][text], text
        assert pdfbackend.missing_glyphs(text) == [], text
    assert list(pdfbackend.CANVAS_TEXT_FAMILIES) == o["CANVAS_TEXT_FAMILIES"]
    assert pdfbackend.COVERAGE_MAX_CP == o["COVERAGE_MAX_CP"]
    ranges = pdfbackend.coverage_ranges()
    assert set(ranges) == {"primary", "cjk", "fallback"}
    assert ranges["fallback"] == []  # 能力边界（ADR 0060 §1），不是漏写


# ---------------------------------------------------------------------------
# 3. 候选自己的合同
# ---------------------------------------------------------------------------
def _png_size(data: bytes) -> tuple[int, int]:
    w, h, _bpp, _px = pdfread.decode_png_any(data)
    return w, h


@needs_candidate
def test_original_png_copies_a_png_source_byte_for_byte_and_transcodes_jpeg(
    candidate, fonts_ready, tmp_path
):
    src = FIXTURE / "original.png"
    facts = pdfbackend.original_png(src, tmp_path / "o.png", 600)
    assert (tmp_path / "o.png").read_bytes() == src.read_bytes()  # 含 pHYs 等元数据（RC-054）
    assert facts == {"px_w": 64, "px_h": 48, "resampled": False, "transcoded": False}
    from PIL import Image

    im = Image.new("RGB", (30, 20), (200, 30, 30))
    im.save(tmp_path / "j.jpg", quality=90)
    facts = pdfbackend.original_png(tmp_path / "j.jpg", tmp_path / "j.png", 600)
    data = (tmp_path / "j.png").read_bytes()
    assert data[:8] == pdfread.PNG_SIGNATURE if hasattr(pdfread, "PNG_SIGNATURE") else True
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert _png_size(data) == (30, 20) and facts["px_w"] == 30 and facts["transcoded"] is True
    assert facts["resampled"] is False  # 600 ppi 的请求不改像素网格（RC-055）


@needs_candidate
def test_original_pdf_moves_only_the_first_page_and_does_not_redraw_it(
    candidate, fonts_ready, tmp_path
):
    """矢量源整页搬运：多页源只出第一页；文字层、对象数与源页一致（独立读取器 PDFium）；`vector: True`。"""
    import pikepdf
    import pypdfium2 as pdfium

    src = tmp_path / "two.pdf"
    with pikepdf.open(str(FIXTURE / "page.pdf")) as pdf:
        pdf.pages.append(pdf.pages[0])
        pdf.pages[1].obj["/MediaBox"] = [0, 0, 100, 100]
        pdf.save(str(src))
    facts = pdfbackend.original_pdf(src, tmp_path / "o.pdf")
    assert facts["vector"] is True and facts["pages"] == 2
    assert facts["px_w"] is None and facts["px_h"] is None

    def census(path: Path):
        doc = pdfium.PdfDocument(str(path))
        try:
            page = doc[0]
            kinds = sorted(o.type for o in page.get_objects())
            tp = page.get_textpage()
            text = tp.get_text_range()
            tp.close()
            page.close()
            return len(doc), kinds, text, page and None
        finally:
            doc.close()

    n, kinds, text, _ = census(tmp_path / "o.pdf")
    _n0, kinds0, text0, _ = census(FIXTURE / "page.pdf")
    assert n == 1 and kinds == kinds0 and text == text0
    assert abs(facts["w_pt"] - 270.0) < 1e-6 and abs(facts["h_pt"] - 160.0) < 1e-6


@needs_candidate
def test_original_pdf_of_a_raster_source_fills_the_given_page_and_is_not_vector(
    candidate, fonts_ready, tmp_path
):
    import pypdfium2 as pdfium

    facts = pdfbackend.original_pdf(FIXTURE / "original.png", tmp_path / "r.pdf", (200.0, 150.0))
    assert facts == {
        "w_pt": 200.0,
        "h_pt": 150.0,
        "px_w": 64,
        "px_h": 48,
        "vector": False,
        "pages": 1,
    }
    doc = pdfium.PdfDocument(str(tmp_path / "r.pdf"))
    try:
        page = doc[0]
        assert page.get_size() == (200.0, 150.0)
        assert [o.type for o in page.get_objects()] == [3]  # 一个 image 对象
        page.close()
    finally:
        doc.close()
    with pytest.raises(ValueError):
        pdfbackend.original_pdf(FIXTURE / "original.png", tmp_path / "x.pdf", None)


@needs_candidate
def test_original_tiff_writes_only_the_declared_density_and_keeps_the_pixel_grid(
    candidate, fonts_ready, tmp_path
):
    """RC-057：没声明就写「没有绝对单位」，声明了才写；位图源的像素网格不变；矢量源标签 = ppi。"""
    facts = pdfbackend.original_tiff(FIXTURE / "original_nophys.png", tmp_path / "n.tiff", 600)
    tags = tiffcheck.read_tags(tmp_path / "n.tiff")
    assert facts["px_w"] == 64 and facts["px_h"] == 48 and facts["resampled"] is False
    assert tags["resolution_unit"] == 1 and tags["x_resolution"] == 1  # 无绝对单位（不编一个数）
    assert (tags["width"], tags["height"]) == (64, 48)
    pdfbackend.original_tiff(FIXTURE / "original.png", tmp_path / "d.tiff", 600, dpi_meta=300.0)
    tags = tiffcheck.read_tags(tmp_path / "d.tiff")
    assert tags["resolution_unit"] == 2 and abs(float(tags["x_resolution"]) - 300.0) < 1e-6
    facts = pdfbackend.original_tiff(FIXTURE / "page.pdf", tmp_path / "v.tiff", 150)
    tags = tiffcheck.read_tags(tmp_path / "v.tiff")
    assert (facts["px_w"], facts["px_h"]) == (562, 333)  # round(270·150/72), round(160·150/72)
    assert tags["resolution_unit"] == 2 and abs(float(tags["x_resolution"]) - 150.0) < 1e-6
    # 同一矢量源、同一 ppi 的 PNG 与 TIFF 像素逐个相同（03 §6 必须精确）
    pdfbackend.original_png(FIXTURE / "page.pdf", tmp_path / "v.png", 150)
    _, png_px = _decode_png_rgb(tmp_path / "v.png")
    _, tiff_px = tiffcheck.decode_samples(tmp_path / "v.tiff")
    assert png_px == tiff_px


def _decode_png_rgb(path: Path) -> tuple[tuple[int, int], bytes]:
    w, h, bpp, px = pdfread.decode_png_any(path.read_bytes())
    if bpp == 4:
        px = b"".join(px[i : i + 3] for i in range(0, len(px), 4))
    return (w, h), px


@needs_candidate
def test_the_canvas_face_saves_png_and_tiff_from_one_canonical_pdf(
    candidate, fonts_ready, tmp_path
):
    """RC-002：同实例多格式保存出自同一份 Canonical PDF；`save_png` 不重新解析另一份来源——把交付的
    PDF 再栅格一次，与交付的 PNG 逐字节相同；重复 close 幂等。"""
    from tavotto.rendercore import facade, raster

    canvas = pdfbackend.compose(120, 60)
    canvas.place(
        {"type": "panel", "id": "figs/p.pdf", "x_mm": 5, "y_mm": 5, "w_mm": 67.5, "h_mm": 40},
        150,
        lambda o, d: FIXTURE / "page.pdf",
    )
    canvas.place(
        {
            "type": "text",
            "id": "t",
            "text": "Hello 图",
            "x_mm": 80,
            "y_mm": 10,
            "w_mm": 30,
            "h_mm": 10,
            "size_pt": 10,
        },
        150,
        lambda o, d: None,
    )
    assert canvas.size_pt == (pytest.approx(340.157, abs=1e-3), pytest.approx(170.079, abs=1e-3))
    canvas.save_pdf(tmp_path / "c.pdf")
    canvas.save_png(tmp_path / "c.png", 150)
    tiff_facts = canvas.save_tiff(tmp_path / "c.tiff", 150)
    canvas.close()
    canvas.close()  # 幂等
    assert tiff_facts["px_w"] == 709 and tiff_facts["px_h"] == 354
    (w, h), png_px = _decode_png_rgb(tmp_path / "c.png")
    assert (w, h) == (709, 354)
    _, tiff_px = tiffcheck.decode_samples(tmp_path / "c.tiff")
    assert png_px == tiff_px
    again = facade.host().render(tmp_path / "c.pdf", dpi=150.0, transparent=False)
    assert raster.encode_png(again) == (tmp_path / "c.png").read_bytes()
    # 文字层在、面板是 Form（独立读取器）
    objs = pdfread.objects((tmp_path / "c.pdf").read_bytes())
    _head, content = pdfread.page(objs)
    assert b"Do" in content and b"TJ" in content


@needs_candidate
def test_annotate_asset_overlays_vector_annotations_without_redrawing_the_source(
    candidate, fonts_ready, tmp_path
):
    """写回携带标注：源页内容流不动（文字、对象都在），标注作为覆盖层叠上；PNG 由注好的 PDF 栅格，
    尺寸 = round(pt·dpi/72)，像素与再栅格一次逐字节相同。"""
    import pypdfium2 as pdfium

    from tavotto.rendercore import facade, raster

    pdf = tmp_path / "ann.pdf"
    shutil.copy(FIXTURE / "page.pdf", pdf)
    before = hashlib.sha256(pdf.read_bytes()).hexdigest()
    pdfbackend.annotate_asset(
        pdf,
        tmp_path / "ann.png",
        [
            {
                "type": "text",
                "id": "a",
                "text": "note",
                "x_mm": 5,
                "y_mm": 5,
                "w_mm": 30,
                "h_mm": 8,
                "size_pt": 9,
            },
            {
                "type": "arrow",
                "id": "b",
                "x_mm": 10,
                "y_mm": 20,
                "w_mm": 30,
                "h_mm": 10,
                "color": "#ff0000",
                "stroke_pt": 1,
                "start": {"rx": 0, "ry": 1},
                "end": {"rx": 1, "ry": 0},
                "head_end": "triangle",
            },
        ],
        dpi=150,
    )
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() != before
    doc = pdfium.PdfDocument(str(pdf))
    try:
        page = doc[0]
        assert page.get_size() == (270.0, 160.0)
        kinds = [o.type for o in page.get_objects()]
        tp = page.get_textpage()
        text = tp.get_text_range()
        tp.close()
        page.close()
    finally:
        doc.close()
    assert "U00 fixture" in text and "note" in text  # 源文字层在、标注文字层在
    assert 5 in kinds  # 覆盖层是一个 Form XObject，源对象照旧
    assert set(pdfbackend.pdf_fonts(pdf)) >= {"Helvetica", "LiberationSerif"}
    (w, h), png_px = _decode_png_rgb(tmp_path / "ann.png")
    assert (w, h) == (562, 333)
    again = facade.host().render(pdf, dpi=150.0, transparent=False)
    assert raster.encode_png(again) == (tmp_path / "ann.png").read_bytes()


@needs_candidate
def test_annotate_asset_refuses_panel_objects(candidate, fonts_ready, tmp_path):
    from tavotto.rendercore import plan

    pdf = tmp_path / "ann.pdf"
    shutil.copy(FIXTURE / "page.pdf", pdf)
    before = pdf.read_bytes()
    with pytest.raises(plan.PlanError):
        pdfbackend.annotate_asset(
            pdf,
            None,
            [
                {
                    "type": "text",
                    "id": "a",
                    "text": "x",
                    "x_mm": 1,
                    "y_mm": 1,
                    "w_mm": 5,
                    "h_mm": 5,
                },
                {"type": "panel", "id": "p", "x_mm": 1, "y_mm": 1, "w_mm": 5, "h_mm": 5},
            ],
        )
    assert pdf.read_bytes() == before  # 拒绝时原件零改动
    # 不认识的对象类型同样拒（不是静默跳过）
    with pytest.raises(plan.PlanError):
        pdfbackend.annotate_asset(pdf, None, [{"type": "widget", "id": "w"}])
    assert pdf.read_bytes() == before


@needs_candidate
def test_render_preview_png_renders_the_first_page_at_the_bucket_width(
    candidate, fonts_ready, tmp_path
):
    pdfbackend.render_preview_png(FIXTURE / "page.pdf", 400, tmp_path / "p" / "prev.png")
    (w, h), _ = _decode_png_rgb(tmp_path / "p" / "prev.png")
    assert (w, h) == (400, 237)  # round(160·400/270)


@needs_candidate
def test_pdf_fonts_has_a_budget_for_self_referencing_forms(candidate, fonts_ready, tmp_path):
    """恶意自引用的 Form 资源树不许让 `pdf_fonts` 无限递归（RC-072 同族）。"""
    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(100, 100))
    form = pikepdf.Stream(pdf, b"")
    form["/Type"] = pikepdf.Name.XObject
    form["/Subtype"] = pikepdf.Name.Form
    form["/BBox"] = [0, 0, 100, 100]
    form = pdf.make_indirect(form)
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont="/ABCDEF+Inner"
        )
    )
    form["/Resources"] = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(X1=form), Font=pikepdf.Dictionary(F9=font)
    )
    page.obj["/Resources"] = pikepdf.Dictionary(XObject=pikepdf.Dictionary(X0=form))
    pdf.save(str(tmp_path / "loop.pdf"))
    assert pdfbackend.pdf_fonts(tmp_path / "loop.pdf") == ["Inner"]
    # 共享子树的扇出：每层 16 个引用指向同一个下层 form、8 层深——不按身份去重就是 16^8 次访问
    # （深度上限挡不住指数级的重复走访），去重后只走 8 个 form。判据：有界时间内走完并找到最深处的字体
    import threading

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(100, 100))
    deep_font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont="/Deep")
    )
    child = None
    for level in range(8):
        form = pikepdf.Stream(pdf, b"")
        form["/Type"] = pikepdf.Name.XObject
        form["/Subtype"] = pikepdf.Name.Form
        form["/BBox"] = [0, 0, 100, 100]
        res = pikepdf.Dictionary()
        if child is None:
            res["/Font"] = pikepdf.Dictionary(F0=deep_font)
        else:
            res["/XObject"] = pikepdf.Dictionary({f"/X{i}": child for i in range(16)})
        form["/Resources"] = res
        child = pdf.make_indirect(form)
    page.obj["/Resources"] = pikepdf.Dictionary(XObject=pikepdf.Dictionary(X0=child))
    pdf.save(str(tmp_path / "fanout.pdf"))
    box: list = []
    t = threading.Thread(target=lambda: box.append(pdfbackend.pdf_fonts(tmp_path / "fanout.pdf")))
    t.daemon = True
    t.start()
    t.join(timeout=20)
    assert not t.is_alive(), "pdf_fonts 在共享子树上走了指数级的路（没有按身份去重）"
    assert box == [["Deep"]]


@needs_candidate
def test_three_families_reach_three_different_embedded_faces(candidate, fonts_ready, tmp_path):
    """替代 `test_typography_families.py::test_the_family_reaches_the_pdf_font_resources`（那条断言 base-14
    名字，D07：实现细节）。用户合同：族要走到**产物**里——三个族的文字在 PDF 字体资源表里是三张不同的、
    批准集合里的脸；没写 font_family 的那条走衬线。"""
    out = tmp_path / "families.pdf"

    def text(y, **kw):
        return {
            "type": "text",
            "id": f"t{y}",
            "text": "Sample",
            "x_mm": 5,
            "y_mm": y,
            "w_mm": 60,
            "h_mm": 10,
            "size_pt": 10,
            **kw,
        }

    with pdfbackend.compose(80, 60) as canvas:
        canvas.place(text(5.0), dpi=300, resolve_panel=lambda o, d: None)
        canvas.place(text(20.0, font_family="sans-serif"), dpi=300, resolve_panel=lambda o, d: None)
        canvas.place(text(35.0, font_family="monospace"), dpi=300, resolve_panel=lambda o, d: None)
        canvas.save_pdf(out)
    names = pdfbackend.pdf_fonts(out)
    assert names == ["LiberationSerif", "LiberationSans", "LiberationMono"], names
    objs = pdfread.objects(out.read_bytes())
    head, _content = pdfread.page(objs)
    fonts = pdfread.fonts(objs, head)
    assert len(fonts) == 3 and all(f["program"] for f in fonts.values()), fonts  # 三张脸都真嵌了


@needs_candidate
def test_placing_after_a_save_invalidates_the_canonical_pdf_not_just_the_compiled_page(
    candidate, fonts_ready, tmp_path
):
    """Codex #476 P2：save → 再 place → 再 save，第二份 PDF 必须含新对象（不能复用上一次的 Canonical PDF 文件）。"""
    canvas = pdfbackend.compose(80, 40)
    text = {
        "type": "text",
        "id": "a",
        "text": "first",
        "x_mm": 5,
        "y_mm": 5,
        "w_mm": 60,
        "h_mm": 8,
        "size_pt": 9,
    }
    canvas.place(text, 150, lambda o, d: None)
    canvas.save_pdf(tmp_path / "one.pdf")
    canvas.save_png(tmp_path / "one.png", 150)
    canvas.place({**text, "id": "b", "text": "second", "y_mm": 20}, 150, lambda o, d: None)
    canvas.save_pdf(tmp_path / "two.pdf")
    canvas.save_png(tmp_path / "two.png", 150)
    canvas.close()
    objs1 = pdfread.objects((tmp_path / "one.pdf").read_bytes())
    objs2 = pdfread.objects((tmp_path / "two.pdf").read_bytes())
    _h1, c1 = pdfread.page(objs1)
    _h2, c2 = pdfread.page(objs2)
    assert c1.count(b"TJ") == 1 and c2.count(b"TJ") == 2
    assert (tmp_path / "one.png").read_bytes() != (tmp_path / "two.png").read_bytes()


@needs_candidate
def test_dpi_renders_of_a_userunit_page_have_as_many_pixels_as_the_probe_says(
    candidate, fonts_ready, tmp_path
):
    """Codex #476 P2：`/UserUnit 2` 的页按 150 ppi 出图，像素数要与 probe 报的物理尺寸一致（540 × 320 pt →
    1125 × 667），不是未乘 UserUnit 的 562 × 333。"""
    import pikepdf

    src = tmp_path / "uu.pdf"
    with pikepdf.open(str(FIXTURE / "page.pdf")) as pdf:
        pdf.pages[0].obj["/UserUnit"] = 2
        pdf.save(str(src))
    probe = pdfbackend.probe_asset(src, "pdf")
    facts = pdfbackend.original_png(src, tmp_path / "uu.png", 150)
    want = (round(probe["w_pt"] * 150 / 72), round(probe["h_pt"] * 150 / 72))
    assert (facts["px_w"], facts["px_h"]) == want == (1125, 667)
    assert _png_size((tmp_path / "uu.png").read_bytes()) == want
    tiff = pdfbackend.original_tiff(src, tmp_path / "uu.tiff", 150)
    assert (tiff["px_w"], tiff["px_h"]) == want
    # 按宽定尺寸的预览不受影响（比例不变）
    pdfbackend.render_preview_png(src, 400, tmp_path / "prev.png")
    assert _png_size((tmp_path / "prev.png").read_bytes()) == (400, 237)
