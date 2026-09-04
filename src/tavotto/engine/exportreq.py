"""统一导出请求 —— 「这一次导出到底要什么」全产品只有这一份定义。

改造前，「导出什么」这句话散在四个地方各说一遍：`ExportDialog.tsx` 拼一份
载荷、`app.api_export()` 从 `spec` 里逐个 `get()` 出自己的默认值、
`codex-plugin` 的 bridge 又一份、打包端点再一份。四份的**默认值并不一样**
（dpi 的兜底、stem 的清洗、格式的顺序），于是"同样一张图、同样的设置"在两条
入口下能出来两个不同的文件。

这个模块是那个唯一定义。它只做三件事，都不碰磁盘：

1. **规范化**：任意 JSON → `ExportRequest`（缺省值只有这里一份）；
2. **校验**：不合法的请求在**碰磁盘之前**变成结构化错误（`code` 是稳定枚举，
   不要去解析 message——那是给人看的）；
3. **命名**：文件名的跨平台合法性、扩展名补齐与去重、覆盖策略的枚举。

### 文件名规则是**严格同源对**

`web/src/lib/exportName.ts` 是同一套规则的 TS 实现（浏览器里输入的那一刻就要
给出就地提示，等一次网络往返再说"这个名字不行"太晚了）。两份实现由
`tests/golden/filename_vectors.json` 对齐，pytest 与 vitest 各跑一遍同一份向量
——与 `preflight` ↔ `preflight.ts`、`patchspec` ↔ Rust 完全一样的纪律。

**判据按最严的平台写**（Windows），不按当前运行平台写：项目会被拷到另一台
电脑上，一个在 macOS 上导出成功的 `Fig?1.pdf` 到了 Windows 上根本创建不出来。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ------------------------------- 枚举 ---------------------------------------

#: 导出范围。**两个取值，不许有第三个**——"按原图" 与 "按画布" 是两套不同的
#: 尺寸语义（ADR 0028 / ADR 0031），中间没有插值。
SCOPE_ORIGINAL = "original"
SCOPE_CANVAS = "canvas"
SCOPES = (SCOPE_ORIGINAL, SCOPE_CANVAS)

#: 输出格式。`pdf` 是矢量载体，`png` 是位图载体。
#: **不许在这里加一个当前管线给不出真矢量的格式**（共享规则 §8：不得伪称矢量）。
FORMAT_PDF = "pdf"
FORMAT_PNG = "png"
FORMAT_SVG = "svg"
FORMATS = (FORMAT_PDF, FORMAT_PNG)

#: 引擎**直接序列化**那条路（codex-plugin 的 `tavotto_export`）额外认的格式。
#:
#: 它**不在 `FORMATS` 里**，理由不是"svg 不够矢量"——matplotlib 序列化出来的
#: SVG 与它的 PDF 同源，是真矢量——而是画布合成走 PyMuPDF，那条路**给不出**
#: SVG。把它放进全局枚举，导出面板就会摆出一个合成管线兑现不了的选项。
#: 所以「这次认哪几种格式」是**消费点自己带的参数**
#: （`normalize(allowed_formats=…)`），而规则（清洗、扩展名、去重、PPI 语义、
#: 顺序）仍然只有这一份。
ENGINE_FORMATS = (FORMAT_PDF, FORMAT_PNG, FORMAT_SVG)

#: 哪些格式是位图 —— 「PPI 只在位图输出时有意义」这句话的唯一判据。
RASTER_FORMATS = frozenset({FORMAT_PNG})
VECTOR_FORMATS = frozenset({FORMAT_PDF, FORMAT_SVG})

#: 覆盖已有文件的策略。**默认 `ask`**：静默覆盖用户上一次的成果是不可逆的。
OVERWRITE_ASK = "ask"
OVERWRITE_REPLACE = "replace"
OVERWRITE_RENAME = "rename"
OVERWRITE_POLICIES = (OVERWRITE_ASK, OVERWRITE_REPLACE, OVERWRITE_RENAME)

#: 背景。`transparent` 只对位图有意义；PDF 的"透明"是不画白底矩形。
BACKGROUND_WHITE = "white"
BACKGROUND_TRANSPARENT = "transparent"
BACKGROUNDS = (BACKGROUND_WHITE, BACKGROUND_TRANSPARENT)

#: 检查策略。`block_on_error` = 有 error 就不导；`acknowledged` = 用户在界面上
#: 明确点过头，那次确认连同规则码一起进样式检查报告。
VALIDATION_BLOCK = "block_on_error"
VALIDATION_ACKNOWLEDGED = "acknowledged"
VALIDATION_POLICIES = (VALIDATION_BLOCK, VALIDATION_ACKNOWLEDGED)

#: PPI 的合法区间。上限不是审美偏好：一张 180mm 宽的图在 2400 ppi 下是
#: 17000 px，占内存以 GB 计——那台机器会在合成中途被系统杀掉，而用户看到的
#: 是「导出没反应」。
PPI_MIN = 36
PPI_MAX = 1200
PPI_DEFAULT = 600

#: 文件名长度上限。Windows 的 `MAX_PATH` 是 260 **含整条路径**；给目录、
#: 扩展名和 `` (2)`` 这类去重后缀留出余量后，基名限 120。
FILENAME_MAX = 120

#: Windows 保留设备名（不区分大小写，带扩展名也一样被拒）。
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

#: Windows 上非法的字符 + 路径分隔符。`/` 在 POSIX 上也是分隔符，
#: 两个平台的并集才是"拷到哪台机器都建得出来"。
_ILLEGAL_CHARS = '<>:"/\\|?*'

#: 输入里允许被识别并剥掉的扩展名。**只剥我们自己会产出的那几个** ——
#: 用户把图叫做 `v1.2` 时不能把 `.2` 当扩展名剥掉。
_STRIPPABLE_EXTS = ("pdf", "png", "svg", "tif", "tiff", "jpg", "jpeg", "eps")


#: 本模块会发出的**全部**错误码。
#:
#: 它不是文档的复制品，是**门禁的扫描面**：`tests/test_error_codes.py` 直接读
#: 这个元组（不是正则扫源码——`_one_of()` 的 code 是个变量，正则看不见它，
#: 而看不见的门禁等于没有门禁）。新增一个 code 不加进这里，两种语言的文案
#: 就不会被要求补齐，英文界面上会冷不丁蹦出一句中文。
ERROR_CODES = (
    "bad_request",
    "bad_scope",
    "bad_overwrite",
    "bad_background",
    "bad_policy",
    "bad_formats",
    "no_format",
    "unsupported_format",
    "bad_ppi",
    "ppi_out_of_range",
    "bad_geometry",
    "bad_filename",
    "bad_validation",
    "bad_objects",
    "missing_original",
    "missing_figure",
    "bad_overrides",
    "name_exhausted",
)


class ExportRequestError(ValueError):
    """请求不合法。`code` 是稳定枚举，`params` 供界面拼本地化文案。"""

    def __init__(self, code: str, message: str, params: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.params = params or {}


# ---------------------------- 文件名（同源对） --------------------------------


#: 首尾空白的判定集合。**写死一份，不用 `str.strip()`**：Python 的 `strip()`
#: 与 JavaScript 的 `trim()` 认的字符集**不一样**（`\ufeff` 只有 JS 认，
#: `\x1c`–`\x1f` 只有 Python 认）。靠各自的内建函数，两侧对
#: `"\ufeffFig"` 会给出不同的答案，而那正是 golden 向量存在的意义。
_EDGE_WHITESPACE = frozenset(" \t\n\r\v\f\u00a0\u1680\u2028\u2029\u202f\u205f\u3000\ufeff") | {
    chr(c) for c in range(0x2000, 0x200B)
}


def check_filename(name: str) -> str | None:
    """文件名基名合不合法。合法回 `None`，否则回**稳定错误码**。

    回码不回文案：文案要按界面语言本地化，而判据要能进 golden 向量逐字比对。
    顺序是判据的一部分（同一个名字可能同时犯两条，两侧必须报同一条）。
    """
    if not name:
        return "empty"
    if name[0] in _EDGE_WHITESPACE or name[-1] in _EDGE_WHITESPACE:
        # 首尾空白单独一档：用户看不见它，而"这个名字已经存在"之类的后续判断
        # 会因为它而给出看起来自相矛盾的结论
        return "whitespace_edge"
    if len(name) > FILENAME_MAX:
        return "too_long"
    for ch in name:
        if ord(ch) < 32 or ord(ch) == 127:
            return "control_char"
    for ch in name:
        if ch in _ILLEGAL_CHARS:
            return "illegal_char"
    if set(name) <= {"."}:
        # `.` 与 `..` 先于尾点判：它们是目录引用，说"名字不能以点结尾"
        # 只会让人再试一次 `..`。两条规则都要够得着，否则其中一条是死的
        return "dot_only"
    if name.endswith("."):
        # Windows 会**静默**把尾点吃掉：用户存的 `Fig1.` 变成 `Fig1`，
        # 下一次同名导出于是"莫名其妙"撞车
        return "trailing_dot"
    stem = name.split(".", 1)[0].upper()
    if stem in _RESERVED_NAMES:
        return "reserved_name"
    return None


def strip_output_extension(name: str, formats: tuple[str, ...] = FORMATS) -> str:
    """把用户顺手打上的输出扩展名剥掉，避免 `Fig 1.pdf.pdf`。

    只剥**我们自己会产出**的扩展名，且一次只剥一层：`Fig.pdf.pdf` → `Fig.pdf`
    是错的（那才是用户真的想要的名字里带个 `.pdf` 的极少数情况？不——一次
    只剥一层的理由是可预期：剥到底的话 `data.tar.gz.png` 会变成 `data.tar`）。
    """
    lowered = name.lower()
    known = {e.lower() for e in _STRIPPABLE_EXTS} | {f.lower() for f in formats}
    for ext in known:
        suffix = "." + ext
        if lowered.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def output_name(base: str, fmt: str) -> str:
    """基名 + 格式 → 文件名。**补扩展名只有这一处**。"""
    return f"{base}.{fmt}"


def dedupe_name(base: str, fmt: str, taken) -> str:
    """`rename` 策略下的去重名：`Fig 1 (2).pdf`、`Fig 1 (3).pdf`…

    `taken` 是一个 `name -> bool` 的判定（磁盘存在性或本批已占用），由调用方
    给——这个模块不碰磁盘。
    """
    name = output_name(base, fmt)
    if not taken(name):
        return name
    n = 2
    while True:
        candidate = output_name(f"{base} ({n})", fmt)
        if not taken(candidate):
            return candidate
        n += 1
        if n > 9999:  # 不可能发生；但无界循环在一个写盘路径上不可接受
            raise ExportRequestError("name_exhausted", "无法为这个文件名找到可用的编号", {})


#: 旧契约（`stem`）沿用的清洗：非字/非连字符/非中日韩一律折成下划线。
#: **只给旧路径用**——新路径不偷偷改用户输入的名字，而是当场告诉他哪里不行。
_LEGACY_STEM = re.compile(r"[^\w\-一-鿿]+")


def legacy_stem(raw: str | None) -> str:
    return _LEGACY_STEM.sub("_", raw or "composed") or "composed"


# ------------------------------- 请求 ---------------------------------------


@dataclass(frozen=True)
class CanvasSource:
    """`scope=canvas`：页面尺寸 + z 序对象列表（底 → 顶）。"""

    page_w_mm: float
    page_h_mm: float
    objects: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class OriginalSource:
    """`scope=original`：一张图自己。

    **没有 x/y/w/h，也没有页面尺寸** —— 那正是这个 scope 的定义：图幅由
    `OriginalOutputSpec`（ADR 0028）说了算，画布上的落位与缩放一个字不算数。
    `ignored` 是被忽略的变换清单，原样带进样式检查报告：**忽略而不说等于骗人**。
    """

    figure_id: str
    overrides: list[dict] = field(default_factory=list)
    w_mm: float | None = None
    h_mm: float | None = None
    px_w: int | None = None
    px_h: int | None = None
    #: 源的形态：`figure`（引擎可重渲染）/ `vector` / `raster` / `unknown`
    source_kind: str = "unknown"
    ignored: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExportRequest:
    scope: str
    formats: tuple[str, ...]
    filename: str
    #: **只在有位图格式时是数字**，否则是 `None`。`None` 与 `600` 是两个不同的
    #: 答案：前者说的是"这次导出里 PPI 没有意义"，把它压成一个默认数字，界面
    #: 就会去显示一个不影响任何东西的设置（T-49 同款）。
    ppi: int | None
    background: str
    overwrite: str
    validation_policy: str
    acknowledged: tuple[str, ...]
    include_report: bool
    document_id: str | None
    document_revision: str | None
    canvas: CanvasSource | None = None
    original: OriginalSource | None = None
    #: 旧契约（`stem` + 时间戳后缀）。新界面一律 `False`。
    legacy_naming: bool = False

    @property
    def has_raster(self) -> bool:
        return any(f in RASTER_FORMATS for f in self.formats)

    @property
    def has_vector(self) -> bool:
        return any(f in VECTOR_FORMATS for f in self.formats)

    def to_payload(self) -> dict:
        """回给界面的形态（结果里要回显这次用的到底是什么设置）。"""
        return {
            "scope": self.scope,
            "formats": list(self.formats),
            "filename": self.filename,
            "ppi": self.ppi,
            "background": self.background,
            "overwrite": self.overwrite,
            "validation_policy": self.validation_policy,
            "acknowledged": list(self.acknowledged),
            "include_report": self.include_report,
            "document_id": self.document_id,
            "document_revision": self.document_revision,
            "figure_id": self.original.figure_id if self.original else None,
        }


def _one_of(value: Any, allowed: tuple[str, ...], default: str, code: str) -> str:
    if value is None:
        return default
    text = str(value)
    if text not in allowed:
        # params 只放 `value`：文案里列不下全部合法取值，而
        # `test_placeholders_match_the_params_the_backend_sends` 要求
        # 占位符与 params **逐个对上**——多给一个用不上的键就是一条假承诺
        raise ExportRequestError(
            code, f"不认识的取值：{text}（合法值：{allowed}）", {"value": text}
        )
    return text


def _formats(raw: Any, allowed: tuple[str, ...]) -> tuple[str, ...]:
    if raw is None:
        raise ExportRequestError("no_format", "至少要选一种输出格式", {})
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ExportRequestError("bad_formats", "格式必须是一个列表", {})
    seen: list[str] = []
    for f in raw:
        text = str(f).lower()
        if text not in allowed:
            raise ExportRequestError(
                "unsupported_format", f"不支持的格式：{text}", {"format": text}
            )
        if text not in seen:
            seen.append(text)
    if not seen:
        raise ExportRequestError("no_format", "至少要选一种输出格式", {})
    # 顺序固定成枚举的顺序：结果里的 outputs[] 与界面上的清单要能逐项对上，
    # 而请求里的顺序取决于用户点勾选框的先后——那不是一个稳定的身份
    return tuple(f for f in allowed if f in seen)


def _ppi(raw: Any, *, has_raster: bool) -> int | None:
    if not has_raster:
        # 只出 PDF 时 PPI 无意义。**回 None 而不是回默认值**，
        # 界面据此把整行藏起来（§五：PPI 只在位图输出时出现）
        return None
    if raw is None:
        return PPI_DEFAULT
    try:
        value = int(float(raw))
    except (TypeError, ValueError) as exc:
        raise ExportRequestError(
            "bad_ppi", f"分辨率不是一个数：{raw!r}", {"value": str(raw)}
        ) from exc
    if value < PPI_MIN or value > PPI_MAX:
        raise ExportRequestError(
            "ppi_out_of_range",
            f"分辨率要在 {PPI_MIN}–{PPI_MAX} 之间",
            {"value": value, "min": PPI_MIN, "max": PPI_MAX},
        )
    return value


def _float(raw: Any, name: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ExportRequestError("bad_geometry", f"{name} 不是一个数", {"field": name}) from exc
    if value <= 0 or value != value or value in (float("inf"), float("-inf")):
        raise ExportRequestError("bad_geometry", f"{name} 必须是正的有限数", {"field": name})
    return value


def normalize(spec: dict, *, allowed_formats: tuple[str, ...] = FORMATS) -> ExportRequest:
    """任意 JSON → `ExportRequest`。**缺省值只有这一处**。

    旧契约（`stem` / `items[]` + `texts[]` / 没有 `scope`）在这里被抬成新形状，
    `legacy_naming=True` 让它继续拿到带时间戳的文件名——老标签页与 CI 脚本
    的行为一个字节不变，新界面则拿到用户自己起的名字。

    `allowed_formats` 是**这个消费点**认的格式集合（缺省 `FORMATS`；引擎直接
    序列化那条路传 `ENGINE_FORMATS`）。它只决定"认不认"与顺序，其余规则一份。
    """
    if not isinstance(spec, dict):
        raise ExportRequestError("bad_request", "请求体不是一个对象", {})

    legacy = "filename" not in spec
    scope = _one_of(spec.get("scope"), SCOPES, SCOPE_CANVAS, "bad_scope")
    formats = _formats(spec.get("formats"), allowed_formats)
    has_raster = any(f in RASTER_FORMATS for f in formats)
    ppi = _ppi(spec.get("ppi", spec.get("dpi")), has_raster=has_raster)

    if legacy:
        filename = legacy_stem(spec.get("stem"))
    else:
        raw_name = str(spec.get("filename") or "")
        filename = strip_output_extension(raw_name, formats)
        bad = check_filename(filename)
        if bad is not None:
            raise ExportRequestError("bad_filename", f"文件名不合法（{bad}）", {"reason": bad})

    overwrite_raw = spec.get("overwrite")
    if isinstance(overwrite_raw, bool):
        # 旧契约里 `overwrite` 是个布尔（`scripts/smoke_app.py` 至今这么发）
        overwrite_raw = OVERWRITE_REPLACE if overwrite_raw else None
    overwrite = _one_of(
        overwrite_raw,
        OVERWRITE_POLICIES,
        # 旧路径带时间戳，天生撞不了车；新路径默认问一句
        OVERWRITE_REPLACE if legacy else OVERWRITE_ASK,
        "bad_overwrite",
    )

    background = _one_of(spec.get("background"), BACKGROUNDS, BACKGROUND_WHITE, "bad_background")
    validation = spec.get("validation") or {}
    if not isinstance(validation, dict):
        raise ExportRequestError("bad_validation", "validation 必须是一个对象", {})
    policy = _one_of(validation.get("policy"), VALIDATION_POLICIES, VALIDATION_BLOCK, "bad_policy")
    acknowledged = tuple(
        str(c) for c in (validation.get("acknowledged") or []) if isinstance(c, (str, int))
    )

    canvas = original = None
    if scope == SCOPE_CANVAS:
        source = spec.get("canvas") if isinstance(spec.get("canvas"), dict) else spec
        objects = source.get("objects")
        if objects is None:  # 更老的契约：items[] + texts[]（文字恒在最上）
            objects = [{"type": "panel", **it} for it in (spec.get("items") or [])] + [
                {"type": "text", **t} for t in (spec.get("texts") or [])
            ]
        if not isinstance(objects, list):
            raise ExportRequestError("bad_objects", "objects 必须是一个列表", {})
        canvas = CanvasSource(
            page_w_mm=_float(source.get("page_w_mm"), "page_w_mm"),
            page_h_mm=_float(source.get("page_h_mm"), "page_h_mm"),
            objects=[o for o in objects if isinstance(o, dict)],
        )
    else:
        source = spec.get("original")
        if not isinstance(source, dict):
            raise ExportRequestError("missing_original", "按原图导出缺少 original 段", {})
        figure_id = str(source.get("figure_id") or "")
        if not figure_id:
            raise ExportRequestError("missing_figure", "按原图导出没有说是哪一张图", {})
        overrides = source.get("overrides") or []
        if not isinstance(overrides, list):
            raise ExportRequestError("bad_overrides", "overrides 必须是一个列表", {})
        original = OriginalSource(
            figure_id=figure_id,
            overrides=[o for o in overrides if isinstance(o, dict)],
            w_mm=float(source["w_mm"]) if source.get("w_mm") else None,
            h_mm=float(source["h_mm"]) if source.get("h_mm") else None,
            px_w=int(source["px_w"]) if source.get("px_w") else None,
            px_h=int(source["px_h"]) if source.get("px_h") else None,
            source_kind=str(source.get("source_kind") or "unknown"),
            ignored=tuple(str(x) for x in (source.get("ignored") or [])),
        )

    return ExportRequest(
        scope=scope,
        formats=formats,
        filename=filename,
        ppi=ppi,
        background=background,
        overwrite=overwrite,
        validation_policy=policy,
        acknowledged=acknowledged,
        include_report=bool(spec.get("include_style_check_report") or spec.get("proof")),
        document_id=str(spec["document_id"]) if spec.get("document_id") else None,
        document_revision=(
            str(spec["document_revision"]) if spec.get("document_revision") else None
        ),
        canvas=canvas,
        original=original,
        legacy_naming=legacy,
    )
