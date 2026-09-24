"""PDF 后端边界层——契约名只在这里，实现在 `rendercore/facade.py`，**按一条写明的策略选**。

**为什么要有这一层**：把「读页面尺寸 / 栅格化 / 按布局合成」这几件事收敛成下面这组
与实现无关的函数后，换 PDF 库只需新写一个实现模块，HTTP 层（`app.py`）一行不用动。
这层从 PyMuPDF 时代（AGPL-3.0 的那个理由）一直活到 U10 把 PyMuPDF 退役（ADR 0072）：
`app.py` / `artifactcheck` / `tutorial` / MCP bridge / 两个生成脚本只认这里导出的名字，
不认识 pikepdf、PDFium 或任何实现模块。

边界契约（进出全是 dict / Path / 基本类型，不泄漏任何后端对象）：

  probe_asset(path, kind)           → 素材原始尺寸，供图库列表换算物理尺寸
  pdf_fonts(path)                   → PDF 首页真正用到的字体名（最终产物验收的唯一依据）
  render_preview_png(path, w, out)  → 画布显示用的位图预览（带磁盘缓存）
  text_width(s, size_pt, ...)       → 中英混排字符串宽度（pt；`family` 与落笔同族）
  text_plan(s, family, ...)         → 字形归属计划 [(片段, 层)]；层见 glyphplan.GLYPH_LAYERS
  missing_glyphs(s, family, ...)    → 这段文字里画不出来的字符（预检的唯一依据）
  coverage_ranges() / COVERAGE_MAX_CP
                                    → 三层覆盖的区间表，生成 canvas_coverage.json 用
  CANVAS_TEXT_FAMILIES              → 画布文字能选的字体族**闭集**（三个通用族）。
                                       与 `web/src/lib/typography.ts` 严格同源；
                                       换后端实现时这条闭集要跟着新后端画得出
                                       什么走，不能照抄——它是一句能力承诺。
  compare_png(a, b)                 → 两张 PNG 的像素差异指标（写回像素门）
  compose(page_w_mm, page_h_mm, transparent=False)
                                    → 合成画布；place() 逐个落对象，save_*() 出图
  original_pdf(src, out, page_pt)   → 按原图尺寸出 PDF（矢量源整页搬运，不重画；
                                       位图源的页面尺寸由调用方给——密度的解析
                                       只有 engine/originalspec 一处）
  original_png(src, out, ppi, transparent)
                                    → 按原图出 PNG（位图**永远**保源像素网格；
                                       JPEG 源换容器不换像素）
  original_tiff(src, out, ppi, transparent, dpi_meta=)
                                    → 按原图出 TIFF（同一套规则；Deflate 无损；
                                       位图源的分辨率标签只写源文件自己声明过的）
  annotate_asset(pdf, png, objs)    → 把画布标注画进单图文件（写回原图带标注）
  BACKEND_NAME / BACKEND_VERSION    → 后端身份（进渲染缓存键：换实现/换版本
                                       出来的像素可能就不一样了）

唯一的例外是 `compose()` 返回的画布对象本身——它由实现模块定义，
但只通过 place/save_pdf/save_png/save_tiff/close 这几个方法被使用。

## 实现与选择策略（统一实施包 U08 引入开关，U10 切默认并退役旧实现：ADR 0067 / 0072）

| `TAVOTTO_RENDER_BACKEND` | 实现模块 | 状态 |
|---|---|---|
| 未设 / `rendercore` | `rendercore/facade.py`（pikepdf / HarfBuzz / PDFium render child） | **默认，唯一** |
| `pymupdf` | ——（`pdfbackend/pymupdf_backend.py` 已于 U10 删除） | `BackendSelectionError(backend_retired)` |

**选定即定，不静默回退**（06 §1）：实现被选中而它的包 / 批准字体不在，错误就是那个错误
（`CandidatePackagesMissing` / `FontsUnavailable`），绝不换成别的库——「静默回退」会让
「后端能用」这句话在任何机器上都恒真。不认识的取值当场 `BackendSelectionError(backend_unknown)`；
写着 `pymupdf` 的旧配置当场 `backend_retired`——不猜、不退默认，让还指着旧后端的脚本 / 环境
第一次跑就看见（而不是悄悄换了栅格器）。

开关的形状保留（`selected()` / `_impl()` / `BACKENDS` 闭集）是为了下一次换实现仍只改这一处；
今天闭集里只有一项。退役扫描 `scripts/ci/retirement_scan.py` 看护「应用闭包里没有 pymupdf」。
"""

from __future__ import annotations

import importlib
import os
from types import ModuleType

#: 选择开关的环境变量与它的闭集取值。
BACKEND_ENV = "TAVOTTO_RENDER_BACKEND"
BACKEND_RENDERCORE = "rendercore"
#: 退役的名字：取值仍被**认出**，但答案是一个明确的错误，不是回退（ADR 0072）。
BACKEND_RETIRED = ("pymupdf",)
BACKENDS = (BACKEND_RENDERCORE,)
BACKEND_DEFAULT = BACKEND_RENDERCORE

_IMPL_MODULES = {
    BACKEND_RENDERCORE: "tavotto.rendercore.facade",
}

#: 用户可见的稳定 code（`app.py` 的漏斗把 `BackendSelectionError` 与 `is_backend_unavailable()` 认出来的、实现自己
#: 声明的「不可用」异常（RenderCore：`CandidatePackagesMissing` / `FontsUnavailable`）转成 JSON；两种语言的文案在 `web/src/i18n/locales/*/errors.json`，
#: `tests/test_error_codes.py` 读这个元组）。`backend_unavailable` = 依赖 / 字体不在：安装闭包不完整，明确失败不换库。
ERROR_CODES = ("backend_retired", "backend_unknown", "backend_unavailable")


class BackendSelectionError(RuntimeError):
    """`TAVOTTO_RENDER_BACKEND` 的取值不可用。不猜、不退默认——写错了 / 指着退役后端要当场看见。

    `code`：`backend_unknown`（不在闭集里）或 `backend_retired`（PyMuPDF，U10 起已删除）。
    """

    def __init__(self, value: str, *, retired: bool = False) -> None:
        if retired:
            super().__init__(
                f"{BACKEND_ENV}={value!r}：PyMuPDF 后端已退役（ADR 0072），可选 {BACKENDS}；"
                "把这个变量去掉即用默认后端"
            )
            self.code = "backend_retired"
        else:
            super().__init__(f"{BACKEND_ENV}={value!r} 不认识（可选 {BACKENDS}）")
            self.code = "backend_unknown"
        self.value = value


def is_backend_unavailable(exc: BaseException) -> bool:
    """`exc` 是不是某个**已装载**实现声明的「此刻不可用」（依赖包 / 批准字体不在，`backend_unavailable`）。

    每个实现模块自己声明 `UNAVAILABLE_ERRORS`；调用方（`app.py` 的漏斗）只问这一句，不 import 任何实现的
    异常类——否则换 / 加一个后端时，它的不可用错误就绕过这条路成了 `internal_error`（Codex #539）。只看已经
    装载的实现：判错误时不为了判而装载实现（那本身可能就是抛出这个错误的那一步）。与 `selected()` 一样是
    选择器层的工具，不在 `__all__`（19 项契约名的闭集）。
    """
    mods = list(_IMPLS.values())
    if not mods:
        # WSGI / test client 直接用 `app` 而不经 `main()` 时没人调过 `warm()`，一个实现都没装载——这时认
        # 选中的那个（Codex #539）。实现模块只在函数里 import 原生包，装载它本身不会因为缺包失败；万一装载
        # 失败就照实回 False，不拿一个新错误盖住原来那个。
        try:
            mods = [_impl()]
        except Exception:  # noqa: BLE001 - 判错误的路上不许再抛
            return False
    for mod in mods:
        errors = getattr(mod, "UNAVAILABLE_ERRORS", ())
        if errors and isinstance(exc, errors):
            return True
    return False


def selected() -> str:
    """此刻选中的后端名（`BACKENDS` 之一）。"""
    raw = os.environ.get(BACKEND_ENV)
    if raw is None or raw == "":
        return BACKEND_DEFAULT
    name = raw.strip().lower()
    if name in BACKEND_RETIRED:
        raise BackendSelectionError(raw, retired=True)
    if name not in BACKENDS:
        raise BackendSelectionError(raw)
    return name


_IMPLS: dict[str, ModuleType] = {}


def _impl() -> ModuleType:
    name = selected()
    mod = _IMPLS.get(name)
    if mod is None:
        mod = importlib.import_module(_IMPL_MODULES[name])
        _IMPLS[name] = mod
    return mod


def warm() -> str:
    """服务启动时把选中的后端装载好，回它的名字。

    契约层按名字**惰性**装载实现（第一次调用才 import）：旧 facade 是在进程 import 时就把
    PyMuPDF 带进来的，改成惰性之后第一次 `probe_asset` 多了 ~100 ms 的 import——`/api/tutorial`
    的第一次探测就慢了这一截，项目选择器的状态探测与用户的点击撞在一起（U08 e2e 抓到）。
    `main()` 起服务前调一次，第一次请求的延迟回到从前；顺带让「选了一个装不上的后端」在
    启动时就报出来（`BackendSelectionError`：退役 / 不认识；`CandidatePackagesMissing`：依赖不在），
    而不是在用户第一次打开文件时。不静默回退：装不上就抛。与 `selected()` 一样是选择器层的工具，
    **不在** `__all__`（那是 19 项契约名的闭集）。U10 起装的是 `rendercore/facade.py`（ADR 0072）。

    实现模块若有 `prewarm()`（RenderCore 有：render child / 字体注册表 / CJK 脸三件冷启动 ~220 ms），在这里交给它
    **后台**去做——不挡服务启动，也不改变「装不上就在这里抛」：import 已经在上一行成功了。
    """
    impl = _impl()
    prewarm = getattr(impl, "prewarm", None)
    if callable(prewarm):
        prewarm()
    return selected()


# --------------------------------------------------------------------------
# 契约函数：显式委托（可 grep、可 monkeypatch、codegraph 看得见）
# --------------------------------------------------------------------------
def probe_asset(path, kind):
    return _impl().probe_asset(path, kind)


def pdf_fonts(path):
    return _impl().pdf_fonts(path)


def render_preview_png(path, width_px, out):
    return _impl().render_preview_png(path, width_px, out)


def text_width(s, size_pt, bold=False, italic=False, family="serif"):
    return _impl().text_width(s, size_pt, bold, italic, family)


def text_plan(s, family="serif", bold=False, italic=False):
    return _impl().text_plan(s, family, bold, italic)


def missing_glyphs(s, family="serif", bold=False, italic=False):
    return _impl().missing_glyphs(s, family, bold, italic)


def coverage_ranges():
    return _impl().coverage_ranges()


def compare_png(baseline, candidate):
    return _impl().compare_png(baseline, candidate)


def compose(page_w_mm, page_h_mm, transparent=False):
    return _impl().compose(page_w_mm, page_h_mm, transparent)


def original_pdf(src, out, page_pt=None):
    return _impl().original_pdf(src, out, page_pt)


def original_png(src, out, ppi, transparent=False):
    return _impl().original_png(src, out, ppi, transparent)


def original_tiff(src, out, ppi, transparent=False, *, dpi_meta=None):
    return _impl().original_tiff(src, out, ppi, transparent, dpi_meta=dpi_meta)


def annotate_asset(pdf_path, png_path, objects, dpi=600):
    return _impl().annotate_asset(pdf_path, png_path, objects, dpi)


def mm2pt(mm):
    return _impl().mm2pt(mm)


def hex2rgb(s):
    return _impl().hex2rgb(s)


#: 契约常量按选中的实现取（PEP 562）：`BACKEND_NAME` / `BACKEND_VERSION` 进渲染缓存键，
#: `CANVAS_TEXT_FAMILIES` / `COVERAGE_MAX_CP` 进覆盖表。
_CONSTANTS = frozenset(
    {"BACKEND_NAME", "BACKEND_VERSION", "CANVAS_TEXT_FAMILIES", "COVERAGE_MAX_CP"}
)


def __getattr__(name: str):
    if name in _CONSTANTS:
        return getattr(_impl(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BACKEND_NAME",
    "BACKEND_VERSION",
    "CANVAS_TEXT_FAMILIES",
    "COVERAGE_MAX_CP",
    "annotate_asset",
    "compare_png",
    "compose",
    "coverage_ranges",
    "hex2rgb",
    "missing_glyphs",
    "mm2pt",
    "original_pdf",
    "original_png",
    "original_tiff",
    "pdf_fonts",
    "probe_asset",
    "render_preview_png",
    "text_plan",
    "text_width",
]
