"""TAF 数据出图（`data_to_figure`，ADR 0138 Phase 5）的确定性内核：读 CSV 受控摘要 → 列映射 → 由固定模板生成绘图脚本 →
算「这份脚本画出来应当是什么数据」的期望指纹。纯标准库，Flask 父进程 import；**不执行**任何东西、不写任何文件。

v1 的硬边界（`02-architecture.md` §四、`04-security-model.md`）：被执行的代码**完全由 Tavotto 决定**——模型（若用）只给
结构化的列映射建议，脚本出自这里的模板；CSV 里的任何文字（列名、单元格）只以 `repr()` 字符串字面量进入脚本，
不会成为代码，也不进注释（注释里的来源信息用 `json.dumps(ensure_ascii=True)`，不可能带换行）。

解析规则只有一份文字定义，两处实现（父进程这里算期望值、生成的脚本里画图），`tests/test_ai_data_figure.py` 用真 worker
对拍两边的数据指纹逐字节相等：

* 空单元格、`nan`（`float()` 认得的 NaN 写法）= **缺失**：折线在那里断开，不填 0、不插值；
* 有限浮点数 = 数值；`inf` 与其它文字 = **不是数**：不画（折线上记作断开、柱图里整行不画），并逐行报告原因；
* x 列全部是 ISO 日期（`YYYY-MM-DD`）= 时间轴，不当成类别字符串；日期数值的纪元固定为 1970-01-01（生成的脚本在画图前
  `set_epoch`，不受用户 matplotlibrc 的 `date.epoch` 影响）；
* v2（`profile_folder`）：同一套规则读 CSV / TSV / XLSX（`xlsxread`），整文件夹给受控摘要，不执行、不写文件。
* 单位只取自列名后缀，且只认闭集 `UNITS`（`time_s` → `time (s)`、`temperature_C` → `temperature (°C)`）；不认识就原样写列名，
  **绝不补单位**。
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import json
import math
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import xlsxread

#: 受控摘要的上限：超出就拒绝（`too_large`），不截断着给一个看起来完整的摘要
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 64
#: 每列最多带回多少个文字样本（按出现顺序、不去重；摘要给人看、给模型看，不是数据本身）
TEXT_SAMPLES = 20

#: 列名后缀 → 轴标题里的单位。闭集：不在表里的后缀不是单位（`my_value` 不会变成「my (value)」）
UNITS: dict[str, str] = {
    **{u: u for u in ("s", "ms", "us", "ns", "min", "h", "d")},
    **{u: u for u in ("V", "mV", "uV", "A", "mA", "uA", "W", "mW", "Ohm")},
    **{u: u for u in ("Hz", "kHz", "MHz", "GHz")},
    **{u: u for u in ("m", "cm", "mm", "um", "nm", "km")},
    **{u: u for u in ("g", "mg", "ug", "kg", "mol", "mM", "uM", "nM", "M")},
    **{u: u for u in ("Pa", "kPa", "MPa", "bar", "K", "J", "kJ", "eV", "dB", "rpm")},
    "C": "°C",
    "degC": "°C",
    "pct": "%",
    "percent": "%",
}

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_UNIT_SUFFIX = re.compile(r"^(?P<name>.+?)_(?P<unit>[A-Za-z]+)$")
_STEM_SAFE = re.compile(r"[^A-Za-z0-9_-]+")

#: 柱图 / 折线的结构化选择（模板只认这两种）
KINDS = ("line", "bar")


#: `DataSourceError` 的稳定码闭集（前端 `errors:backend.<code>` 有中英文文案，`tests/test_error_codes.py` 读这张表）
ERROR_CODES = (
    "data_path_outside_project",
    "data_not_found",
    "data_not_csv",
    "data_too_large",
    "data_unreadable",
    "data_decode_error",
    "data_malformed",
    "data_empty",
    "data_too_wide",
    "data_header_invalid",
    "data_too_long",
    "data_ragged",
    "figure_name_exhausted",
    # v2（数据文件夹 / TSV / XLSX）：不认识的后缀、`.xls` 等旧格式、XLSX 的各种读不了（码表在 `xlsxread.ERROR_CODES`，
    # 其中与上面重名的只登记一次）
    "data_format_unsupported",
)
ERROR_CODES = ERROR_CODES + tuple(c for c in xlsxread.ERROR_CODES if c not in ERROR_CODES)


class DataSourceError(ValueError):
    """CSV 读不成受控摘要 / 放不进项目。`code` 必须在 `ERROR_CODES` 里（构造时校验：新码不登记当场抛，不会悄悄漏掉文案）。"""

    def __init__(self, code: str, detail: str = ""):
        if code not in ERROR_CODES:
            raise ValueError(f"未登记的 DataSourceError 码：{code}")
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


# --------------------------------------------------------------------------- 单元格解析（与模板里的 `_cell` 同一规则）
def parse_cell(s: str) -> tuple[str, float | None]:
    """`(类别, 值)`：`empty` / `nan`（缺失，值为 NaN）、`numeric`（有限浮点）、`text`（不是数，值为 None）。"""
    t = s.strip()
    if not t:
        return "empty", math.nan
    try:
        v = float(t)
    except ValueError:
        return "text", None
    if math.isnan(v):
        return "nan", math.nan
    if math.isinf(v):
        return "text", None
    return "numeric", v


def parse_date(s: str) -> _dt.date | None:
    t = s.strip()
    if not _ISO_DATE.fullmatch(t):
        return None
    try:
        return _dt.date.fromisoformat(t)
    except ValueError:
        return None


#: 日期数值 = 自 1970-01-01 起的天数；生成的日期脚本会把 matplotlib 的纪元固定成它（见 `_EPOCH_SETUP`）
_EPOCH = _dt.date(1970, 1, 1)


def date_number(d: _dt.date) -> float:
    return float((d - _EPOCH).days)


# --------------------------------------------------------------------------- 受控摘要
@dataclass
class ColumnSummary:
    name: str
    numeric: int = 0
    empty: int = 0
    nan: int = 0
    text: list[str] = field(default_factory=list)
    text_count: int = 0
    dates: int = 0

    @property
    def kind(self) -> str:
        """`numeric`（有数、没有文字）/ `date`（非空值全是 ISO 日期）/ `text`（没有数）/ `mixed`（有数也有文字）。"""
        if self.text_count and self.dates == self.text_count and not self.numeric:
            return "date"
        if self.numeric and not self.text_count:
            return "numeric"
        if self.numeric:
            return "mixed"
        return "text"

    def to_dict(self) -> dict:
        return {
            "numeric": self.numeric,
            "empty": self.empty,
            "nan": self.nan,
            "text": list(self.text),
            "text_count": self.text_count,
            "kind": self.kind,
        }


#: 分组列：不同取值个数的上限（>12 个取值的类别列不当分组，多半是标签 / 名字）
MAX_GROUP_LEVELS = 12

LAYOUTS = ("long", "wide", "matrix", "unknown")


@dataclass
class DataSummary:
    """一张表的受控摘要。`layout` / `groups` / `id_like` / `repeats` 的判定规则见 `_structure` 的文档（保守：判不出
    就 `unknown` / 空 / None，不猜）；它们只是给上层（和模型）的线索，不决定任何科学含义。

    `rows` 是行数据（`plan_mapping` / `plotted_data` 要用）；`profile_folder` 的条目为省内存不带行数据（`rows == []`），
    行数记在 `row_count`——要出图再对那个文件 / 工作表 `inspect_data_source` 一次。"""

    path_name: str
    sha256: str
    size: int
    columns: list[str]
    rows: list[list[str]]
    per_column: dict[str, ColumnSummary]
    sheet: str | None = None
    layout: str = "unknown"
    groups: dict[str, int] = field(default_factory=dict)
    id_like: list[str] = field(default_factory=list)
    repeats: bool | None = None
    row_count: int | None = None

    def to_dict(self) -> dict:
        """给前端 / 历史库 / 模型看的那份：**不带行数据**，只带列名、类型与计数（`privacy_policy`）。"""
        return {
            "file": self.path_name,
            "sha256": self.sha256,
            "size": self.size,
            "columns": list(self.columns),
            "rows": self.row_count if self.row_count is not None else len(self.rows),
            "per_column": {c: s.to_dict() for c, s in self.per_column.items()},
            "sheet": self.sheet,
            "layout": self.layout,
            "groups": dict(self.groups),
            "id_like": list(self.id_like),
            "repeats": self.repeats,
        }


#: 表格文件后缀（小写）：CSV / TSV 同一条解析，XLSX 经 `xlsxread`；旧 Excel 格式明确拒绝
CSV_SUFFIXES = (".csv",)
TSV_SUFFIXES = (".tsv", ".tab")
XLSX_SUFFIXES = (".xlsx",)
TABLE_SUFFIXES = CSV_SUFFIXES + TSV_SUFFIXES + XLSX_SUFFIXES
LEGACY_EXCEL_SUFFIXES = xlsxread.LEGACY_SUFFIXES


def _from_xlsx_error(exc: xlsxread.XlsxError) -> DataSourceError:
    return DataSourceError(exc.code, str(exc).partition(": ")[2])


def _read_delimited(p: Path, delimiter: str) -> tuple[list[list[str]], str, int]:
    """`(表, 原始字节的 sha256, 字节数)`：UTF-8 / UTF-8 BOM；上限与错误码是 CSV / TSV 共用的一份。"""
    try:
        size = p.stat().st_size
        if size > MAX_BYTES:
            raise DataSourceError("data_too_large", str(size))
        raw = p.read_bytes()
    except OSError as exc:
        raise DataSourceError("data_unreadable", type(exc).__name__) from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DataSourceError("data_decode_error") from None
    try:
        table = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    except csv.Error as exc:
        raise DataSourceError("data_malformed", str(exc)[:200]) from None
    return table, hashlib.sha256(raw).hexdigest(), size


def inspect_data_source(path: str | Path, sheet: str | int | None = None) -> DataSummary:
    """读一份表的受控摘要。后缀 `.tsv` / `.tab` 按制表符分隔、`.xlsx` 读 `sheet`（表名 / 序号 0 起 / `None` = 第一张可见表）、
    `.xls` / `.xlsm` / `.xlsb` 拒绝（`data_excel_unsupported`）；**其余一律按 CSV 读**（v1 行为不变：后缀的白名单由调用方
    管，如 `ai_data_task` 的 `data_not_csv`）。UTF-8 / UTF-8 BOM。调用方负责路径在项目内（`contained_path`）。"""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in LEGACY_EXCEL_SUFFIXES:
        raise DataSourceError("data_excel_unsupported", suffix)
    if suffix in XLSX_SUFFIXES:
        try:
            with xlsxread.XlsxBook(p) as book:
                return _summarize_book(book, sheet)
        except xlsxread.XlsxError as exc:
            raise _from_xlsx_error(exc) from None
    table, sha, size = _read_delimited(p, "\t" if suffix in TSV_SUFFIXES else ",")
    return _summarize(table, path_name=p.name, sha256=sha, size=size)


def _summarize_book(
    book: xlsxread.XlsxBook, sheet: str | int | None, *, keep_rows: bool = True
) -> DataSummary:
    info = book.resolve(sheet)
    return _summarize(
        book.read(info.index),
        path_name=book.name,
        sha256=book.raw_sha256,
        size=book.size,
        sheet=info.name,
        keep_rows=keep_rows,
    )


def _summarize(
    table: list[list[str]],
    *,
    path_name: str,
    sha256: str,
    size: int,
    sheet: str | None = None,
    keep_rows: bool = True,
) -> DataSummary:
    table = [r for r in table if any(c.strip() for c in r)]  # 纯空行不算数据行
    if not table:
        raise DataSourceError("data_empty")
    header = [h.strip() for h in table[0]]
    if len(header) > MAX_COLUMNS:
        raise DataSourceError("data_too_wide", str(len(header)))
    if any(not h for h in header) or len(set(header)) != len(header):
        raise DataSourceError("data_header_invalid")
    rows = table[1:]
    if len(rows) > MAX_ROWS:
        raise DataSourceError("data_too_long", str(len(rows)))
    if not rows:
        raise DataSourceError("data_empty")
    for r in rows:
        if len(r) != len(header):
            raise DataSourceError("data_ragged")
    per = {h: ColumnSummary(h) for h in header}
    # 取值集合只为「分组列」判定：超过 `MAX_GROUP_LEVELS` 个就不再收（长度 > 上限即「太多」）
    levels: dict[str, set[str]] = {h: set() for h in header}
    for r in rows:
        for h, cell in zip(header, r):
            s = per[h]
            cls, _ = parse_cell(cell)
            if cls == "text":
                s.text_count += 1
                if parse_date(cell) is not None:
                    s.dates += 1
                if len(s.text) < TEXT_SAMPLES:
                    s.text.append(cell.strip())
                lv = levels[h]
                if len(lv) <= MAX_GROUP_LEVELS:
                    lv.add(cell.strip())
            else:
                setattr(s, cls, getattr(s, cls) + 1)
    layout, groups, id_like, repeats = _structure(header, rows, per, levels)
    return DataSummary(
        path_name=path_name,
        sha256=sha256,
        size=size,
        columns=header,
        rows=rows if keep_rows else [],
        per_column=per,
        sheet=sheet,
        layout=layout,
        groups=groups,
        id_like=id_like,
        repeats=repeats,
        row_count=None if keep_rows else len(rows),
    )


# --------------------------------------------------------------------------- 结构线索（布局 / 分组 / 编号）
_ID_WORDS = frozenset(
    {
        "subject",
        "sample",
        "patient",
        "animal",
        "mouse",
        "rat",
        "participant",
        "individual",
        "specimen",
    }
)
_ID_SUFFIX_WORDS = frozenset({"no", "number", "code", "index"})
_ID_CJK = ("编号", "个体", "受试", "被试", "样本", "动物", "小鼠", "大鼠", "患者", "病人")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _id_name(name: str) -> bool:
    """列名像个体编号：词切分（驼峰与非字母数字处切开、转小写）后——最后一个词是 `id`；或只有一个词且在
    `_ID_WORDS`（`subject` / `sample` / `patient` / `animal` / `mouse` / `rat` / `participant` / `individual` / `specimen`）；
    或两个词，前一个在 `_ID_WORDS`、后一个是 `no` / `number` / `code` / `index`；或名字含 `_ID_CJK` 里的中文词。
    `sample_mean`、`rating`、`valid` 这类**不算**（词边界，不做子串匹配；中文词除外）。"""
    tokens = [t for t in re.split(r"[^a-z0-9]+", _CAMEL.sub(" ", name).lower()) if t]
    if tokens and tokens[-1] == "id":
        return True
    if len(tokens) == 1 and tokens[0] in _ID_WORDS:
        return True
    if len(tokens) == 2 and tokens[0] in _ID_WORDS and tokens[1] in _ID_SUFFIX_WORDS:
        return True
    return any(w in name for w in _ID_CJK)


def _structure(
    header: list[str],
    rows: list[list[str]],
    per: dict[str, ColumnSummary],
    levels: dict[str, set[str]],
) -> tuple[str, dict[str, int], list[str], bool | None]:
    """`(layout, groups, id_like, repeats)`。全部保守：拿不准就 `unknown` / 空 / None，不猜。定义（一处，下面是实现）：

    * **groups**：列 kind 是 `text`（没有数、不是日期、没有 `nan`）、不同取值（去首尾空白、区分大小写）个数 `k` 满足
      `2 <= k <= MAX_GROUP_LEVELS`（12），且非空格子数 > `k`（有取值重复出现；全不同的是标签不是分组）。
      值 = `k`。数字编码的分组（`1 2 3`）与混合列不算——不猜。
    * **id_like**：`_id_name` 认的列名（线索一）**并且**这一列的非空值至少两种不同取值、整列是文字或全是整数
      （线索二；日期 / 混合 / 有小数的数不算）。只是线索，不代表真是个体。
    * **repeats**：没有 id_like 列 → `None`；有 → 任一 id_like 列「非空格子数 > 不同取值数」（某个编号出现在不止一行）为
      `True`，否则 `False`。
    * **layout**（按此顺序判，全不满足 = `unknown`）：
      `matrix` = 至少 3 列、第一列是文字且各行取值互不相同（行标签），其余列全是数、且其余列的表头集合 == 行标签集合
      （对称，如相关 / 混淆矩阵）；刻意很窄；
      `long` = 有 groups、没有混合列，且数值列恰有 1 或 2 个（一个值列，可再有一个 x / 时间列）；
      `wide` = 没有 groups、没有混合列，第一列是数 / 日期 / 互不相同的文字，其余**至少 2 列**全是数（行 = 位置，列 = 系列）。
      单个 y 的两列表 `x, y`、分组加多个数值列、带备注文字列的表，一律 `unknown`。"""
    n = len(rows)
    groups: dict[str, int] = {}
    for h in header:
        s = per[h]
        k = len(levels[h])
        if s.kind == "text" and s.nan == 0 and 2 <= k <= MAX_GROUP_LEVELS and s.text_count > k:
            groups[h] = k
    col_of = {h: i for i, h in enumerate(header)}
    id_like: list[str] = []
    repeats: bool | None = None
    for h in header:
        s = per[h]
        if not _id_name(h) or s.kind not in ("text", "numeric") or s.nan:
            continue
        vals = [r[col_of[h]].strip() for r in rows]
        vals = [v for v in vals if v]
        if s.kind == "numeric" and not all(float(v).is_integer() for v in vals):
            continue
        distinct = len(set(vals))
        if distinct < 2:
            continue
        id_like.append(h)
        repeats = bool(repeats) or len(vals) > distinct
    kinds = {h: per[h].kind for h in header}
    numeric = [h for h in header if kinds[h] == "numeric"]
    layout = "unknown"
    if "mixed" not in kinds.values():
        first = header[0]
        first_labels: set[str] | None = None
        if kinds[first] == "text" and per[first].nan == 0 and per[first].text_count == n:
            labs = [r[0].strip() for r in rows]
            if len(set(labs)) == n:
                first_labels = set(labs)
        rest = header[1:]
        if (
            first_labels is not None
            and len(header) >= 3
            and all(kinds[h] == "numeric" for h in rest)
            and set(rest) == first_labels
        ):
            layout = "matrix"
        elif groups and len(numeric) in (1, 2):
            layout = "long"
        elif (
            not groups
            and len(rest) >= 2
            and all(kinds[h] == "numeric" for h in rest)
            and (kinds[first] in ("numeric", "date") or first_labels is not None)
        ):
            layout = "wide"
    return layout, groups, id_like, repeats


# --------------------------------------------------------------------------- 整文件夹摘要
#: 一次最多摘要多少张表（XLSX 每个工作表算一张）/ 最多记多少条跳过 / 最多看多少个目录项；超了标 `truncated`，不静默截断
MAX_PROFILE_TABLES = 20
MAX_PROFILE_SKIPPED = 50
PROFILE_SCAN_MAX = 50_000


@dataclass(frozen=True)
class TableEntry:
    """文件夹里的一张表。`path` 是相对所选文件夹的 POSIX 路径；`summary` 不带行数据（见 `DataSummary`）。"""

    path: str
    sheet: str | None
    hidden: bool
    summary: DataSummary

    def to_dict(self) -> dict:
        return {"path": self.path, "hidden": self.hidden, **self.summary.to_dict()}


@dataclass(frozen=True)
class SkippedEntry:
    """读不了的文件（或 XLSX 里读不了的一张表）：`code` 是 `ERROR_CODES` 里的稳定码。"""

    path: str
    sheet: str | None
    code: str

    def to_dict(self) -> dict:
        return {"path": self.path, "sheet": self.sheet, "code": self.code}


@dataclass(frozen=True)
class FolderProfile:
    """`truncated_reason`：`tables`（表超过 `MAX_PROFILE_TABLES`）/ `skipped`（跳过项超过 `MAX_PROFILE_SKIPPED`）/
    `scan`（目录项超过 `PROFILE_SCAN_MAX`，没看完）；多个同时成立时取先发生的那个（`scan` 只在没有别的原因时才记）。"""

    tables: tuple[TableEntry, ...]
    skipped: tuple[SkippedEntry, ...]
    truncated: bool
    truncated_reason: str | None

    def to_dict(self) -> dict:
        return {
            "tables": [t.to_dict() for t in self.tables],
            "skipped": [k.to_dict() for k in self.skipped],
            "truncated": self.truncated,
            "truncated_reason": self.truncated_reason,
        }


def profile_folder(root: str | Path) -> FolderProfile:
    """一个文件夹里全部表（`.csv` / `.tsv` / `.tab` / `.xlsx`，后缀大小写不敏感）的受控摘要。

    遍历规则就是 `discover.walk_files`（与 `/api/ai/data-files` 同一份：隐藏 / `PRUNE_DIRS` 剪枝、不跟随链接）；按相对
    POSIX 路径排序，XLSX 内按工作簿里的表顺序（隐藏表也列，`hidden=True`）。`.xls` / `.xlsm` / `.xlsb` 进 `skipped`
    （`data_excel_unsupported`）；Excel 的锁文件 `~$*` 不是数据，不列。单个文件 / 工作表读不了只进 `skipped`
    （相对路径 + 稳定码），不让整个文件夹失败。不执行、不写任何东西；`root` 不是目录 → `data_not_found`。"""
    from . import discover

    base = Path(root)
    if not base.is_dir():
        raise DataSourceError("data_not_found")
    found, scan_truncated = discover.walk_files(
        base, TABLE_SUFFIXES + LEGACY_EXCEL_SUFFIXES, scan_max=PROFILE_SCAN_MAX
    )
    found = sorted(f for f in found if not Path(f[0]).name.startswith("~$"))
    tables: list[TableEntry] = []
    skipped: list[SkippedEntry] = []
    reason: str | None = None

    def skip(rel: str, sheet: str | None, code: str) -> bool:
        """记一条跳过；记满了返回 False（调用方停）。"""
        nonlocal reason
        if len(skipped) >= MAX_PROFILE_SKIPPED:
            reason = reason or "skipped"
            return False
        skipped.append(SkippedEntry(rel, sheet, code))
        return True

    for rel, _size in found:
        if len(tables) >= MAX_PROFILE_TABLES:
            reason = "tables"
            break
        p = base / rel
        suffix = p.suffix.lower()
        if suffix in LEGACY_EXCEL_SUFFIXES:
            if not skip(rel, None, "data_excel_unsupported"):
                break
            continue
        if suffix in XLSX_SUFFIXES:
            try:
                book = xlsxread.XlsxBook(p)
            except xlsxread.XlsxError as exc:
                if not skip(rel, None, exc.code):
                    break
                continue
            except Exception:  # noqa: BLE001 - 单个文件的任何意外都不该拖垮整个文件夹
                if not skip(rel, None, "data_unreadable"):
                    break
                continue
            with book:
                for info in book.sheets:
                    if len(tables) >= MAX_PROFILE_TABLES:
                        reason = "tables"
                        break
                    try:
                        summ = _summarize_book(book, info.index, keep_rows=False)
                    except (DataSourceError, xlsxread.XlsxError) as exc:
                        if not skip(rel, info.name, exc.code):
                            break
                        continue
                    except Exception:  # noqa: BLE001
                        if not skip(rel, info.name, "data_unreadable"):
                            break
                        continue
                    tables.append(TableEntry(rel, info.name, info.hidden, summ))
            if reason:
                break
            continue
        try:
            table, sha, size = _read_delimited(p, "\t" if suffix in TSV_SUFFIXES else ",")
            summ = _summarize(table, path_name=p.name, sha256=sha, size=size, keep_rows=False)
        except DataSourceError as exc:
            if not skip(rel, None, exc.code):
                break
            continue
        except Exception:  # noqa: BLE001
            if not skip(rel, None, "data_unreadable"):
                break
            continue
        tables.append(TableEntry(rel, None, False, summ))
    if reason is None and scan_truncated:
        reason = "scan"
    return FolderProfile(tuple(tables), tuple(skipped), reason is not None, reason)


# --------------------------------------------------------------------------- 列映射
@dataclass(frozen=True)
class Mapping:
    x: str
    ys: tuple[str, ...]
    kind: str  # KINDS
    x_is_date: bool = False

    def to_dict(self) -> dict:
        return {"x": self.x, "y": list(self.ys), "kind": self.kind, "x_is_date": self.x_is_date}


@dataclass(frozen=True)
class NeedsInput:
    """决定科学含义的项推不出：不出图、问用户。`preview` 是中性的默认映射（只给用户看，不提交）。"""

    reason: str
    preview: Mapping | None = None


@dataclass(frozen=True)
class Unsupported:
    """v1 模板画不了（不是缺信息，是能力边界）：如实失败，不硬画。"""

    reason: str


def _mentioned(instruction: str, columns: list[str]) -> list[str]:
    """指令里**按词**提到的列名，按出现位置排序（`ab` 不算提到 `a`）。

    收集每个列名的全部出现区间；某个命中区间被**另一个更长列名**的命中区间完全覆盖就丢掉（`温度值` 里的 `温度` 不算
    提到 `温度`）。一个列名只要还有一处未被覆盖的出现就算提到。"""
    spans: dict[str, list[tuple[int, int]]] = {}
    for c in columns:
        for m in re.finditer(rf"(?<![A-Za-z0-9_]){re.escape(c)}(?![A-Za-z0-9_])", instruction):
            spans.setdefault(c, []).append((m.start(), m.end()))
    hits = []
    for c, ss in spans.items():
        for a, b in ss:
            covered = any(
                len(o) > len(c) and oa <= a and b <= ob
                for o, oss in spans.items()
                for oa, ob in oss
            )
            if not covered:
                hits.append((a, c))
                break
    return [c for _, c in sorted(hits)]


def _x_from_phrase(instruction: str, columns: list[str]) -> str | None:
    """「随 X 的变化」「随 X 变化」「vs X」「versus X」「against X」里的 X。"""
    for pat in (r"随\s*(.+?)\s*(?:的)?变化", r"\b(?:vs\.?|versus|against)\s+(\S+)"):
        m = re.search(pat, instruction)
        if m:
            found = _mentioned(m.group(1), columns)
            if found:
                return found[0]
    return None


def _descriptive(name: str) -> bool:
    """列名本身有没有语义：一两个字符的名字（`a` / `b` / `c1`）没有；`x` / `y` / `t` 是约定俗成的轴名，算有。"""
    return len(name) > 2 or name in ("x", "y", "t")


def plan_mapping(summary: DataSummary, instruction: str) -> Mapping | NeedsInput | Unsupported:
    """确定性列映射（不调模型）。规则：

    * x：指令里「随 X 的变化」/「vs X」→ X；否则唯一的日期列；否则第一列。
    * y：指令里提到的列（去掉 x，x 被点名不算 y）都必须可画（numeric / mixed）才画；点名的 y 里有任何一个画不了 →
      `Unsupported("requested_column_not_numeric")`，不打折、不静默换成别的列；一个 y 都没点名 → 其余全部可画列。
      纯文字列（备注）永不自动当 y。
    * 指令一个列都没提、列名又都没有语义（FB-D02 的 a/b/c）→ `needs_input`，带中性预览；
    * x 一个数都没有（纯文字类别）→ 柱图，只支持一个 y（多系列分组柱 v1 不画，`Unsupported`）；
      x 是数 / 日期 → 折线；
    * x 混有数和文字（`mixed`）→ `needs_input`（`x_column_mixed`，带按折线的中性预览）：分不清是数轴里混了坏值，还是
      类别名恰好像数字（`A, 2, C`），不猜。先算 y 并做完 `Unsupported` 判定，再判 mixed x；`x_column_mixed` 与
      `column_mapping_ambiguous` 同时成立时返回前者（它更具体）。
      折线上 x 为空 / NaN 的行仍由 `plotted_data` 与模板一起跳过并报告 `x_not_a_value`。"""
    cols = summary.columns
    per = summary.per_column
    plottable = [c for c in cols if per[c].kind in ("numeric", "mixed")]
    mentioned = _mentioned(instruction, cols)
    x = _x_from_phrase(instruction, cols)
    if x is None:
        dates = [c for c in cols if per[c].kind == "date"]
        x = dates[0] if len(dates) == 1 else cols[0]
    requested = [c for c in mentioned if c != x]
    if requested:
        if any(c not in plottable for c in requested):
            return Unsupported("requested_column_not_numeric")
        ys = requested
    else:
        ys = [c for c in plottable if c != x]
    if not ys:
        return Unsupported("no_numeric_column")
    xk = per[x].kind
    kind = "bar" if xk == "text" else "line"
    mapping = Mapping(x=x, ys=tuple(ys), kind=kind, x_is_date=xk == "date")
    if xk == "mixed" or 0 < per[x].dates < per[x].text_count:
        # 后一种：大部分是日期、夹着几个不是日期的值——当成文字类别画柱图同样会悄悄改掉坐标含义
        return NeedsInput("x_column_mixed", preview=mapping)
    if not mentioned and not any(_descriptive(c) for c in cols):
        return NeedsInput("column_mapping_ambiguous", preview=mapping)
    if kind == "bar" and len(ys) > 1:
        return Unsupported("grouped_bars_unsupported")
    return mapping


def axis_label(column: str) -> str:
    """轴标题：列名 + 闭集单位后缀（`voltage_mV` → `voltage (mV)`）；不认识的后缀不是单位，原样返回列名。"""
    m = _UNIT_SUFFIX.match(column)
    if m and m.group("unit") in UNITS:
        return f"{m.group('name')} ({UNITS[m.group('unit')]})"
    return column


# --------------------------------------------------------------------------- 期望数据（父进程算）
@dataclass(frozen=True)
class Plotted:
    """按映射「应当画出来的」数据与未画的行。`series` 的每项是 `(label, part, values, shape)`——与 manifest 的
    `data_fingerprint.parts[part]` 同一口径（`engine/manifest.py::_fingerprint_parts`）。"""

    series: tuple[tuple[str, str, tuple[float, ...], tuple[int, ...]], ...]
    not_plotted: tuple[dict, ...]
    missing: int


def plotted_data(summary: DataSummary, mapping: Mapping) -> Plotted:
    xi = summary.columns.index(mapping.x)
    not_plotted: list[dict] = []
    missing = 0
    series = []
    if mapping.kind == "line":
        xs: list[float] = []
        cols: dict[str, list[float]] = {y: [] for y in mapping.ys}
        for n, row in enumerate(summary.rows, start=2):  # 行号按文件行（表头是第 1 行）
            if mapping.x_is_date:
                d = parse_date(row[xi])
                xv = date_number(d) if d is not None else None
            else:
                cls, xv = parse_cell(row[xi])
                if cls != "numeric":
                    xv = None
            if xv is None:
                not_plotted.append({"row": n, "column": mapping.x, "reason": "x_not_a_value"})
                continue
            xs.append(xv)
            for y in mapping.ys:
                cls, v = parse_cell(row[summary.columns.index(y)])
                if cls == "text":
                    not_plotted.append({"row": n, "column": y, "reason": "not_a_number"})
                    v = math.nan
                elif cls in ("empty", "nan"):
                    missing += 1
                cols[y].append(v)
        for y in mapping.ys:
            flat = tuple(v for pair in zip(xs, cols[y]) for v in pair)
            series.append((y, "xy", flat, (len(xs), 2)))
    else:  # bar：一个 y；不是数的行整行不画
        (y,) = mapping.ys
        yi = summary.columns.index(y)
        heights: list[float] = []
        for n, row in enumerate(summary.rows, start=2):
            cls, v = parse_cell(row[yi])
            if cls != "numeric":
                not_plotted.append(
                    {
                        "row": n,
                        "column": y,
                        "reason": "not_a_number" if cls == "text" else "missing",
                    }
                )
                continue
            heights.append(v)
        flat = tuple(c for h in heights for c in (0.0, h))
        series.append((y, "bars", flat, (len(heights), 2)))
    return Plotted(series=tuple(series), not_plotted=tuple(not_plotted), missing=missing)


def digest(values, shape: tuple[int, ...]) -> dict:
    """`engine/manifest.py::canonical_digest` 的标准库镜像（同一规则：形状头 + 小端 float64 字节，NaN 统一位型）。"""
    vals = [math.nan if math.isnan(float(v)) else float(v) for v in values]
    head = ("x".join(str(n) for n in shape) + ":").encode()
    return {
        "sha256": hashlib.sha256(head + struct.pack(f"<{len(vals)}d", *vals)).hexdigest(),
        "shape": list(shape),
        "finite": sum(1 for v in vals if math.isfinite(v)),
    }


def expected_fingerprints(plotted: Plotted) -> list[dict]:
    """`[{label, part, digest}]`：改后 manifest 里 label 相同的数据元素，其 `data_fingerprint.parts[part]` 应当等于 digest。"""
    return [
        {"label": label, "part": part, "digest": digest(values, shape)}
        for label, part, values, shape in plotted.series
    ]


# --------------------------------------------------------------------------- 模板
def stem_for(csv_name: str) -> str:
    """CSV 文件名 → 图的 stem（也是脚本名的主干）：只留 `[A-Za-z0-9_-]`，空了就用 `data_figure`。"""
    base = _STEM_SAFE.sub("_", Path(csv_name).stem).strip("_-")
    return f"{base}_figure" if base else "data_figure"


_TEMPLATE = '''\
"""由 Tavotto 根据数据生成的图（data_to_figure v1）。之后就是一份普通的绘图脚本，可以随意修改。"""
# tavotto-source: {source}

import csv
import math
from pathlib import Path

import matplotlib.pyplot as plt

DATA = Path(__file__).resolve().parent / {data_rel!r}
X = {x!r}
YS = {ys!r}


def _cell(s):
    """空 / NaN → 缺失（NaN，折线在这里断开）；有限数 → 数；其余（含 inf、文字）→ None（不画）。"""
    t = s.strip()
    if not t:
        return math.nan
    try:
        v = float(t)
    except ValueError:
        return None
    if math.isinf(v):
        return None
    return v


{x_parser}

def main():
{setup}    with open(DATA, newline="", encoding="utf-8-sig") as fh:
        rows = [r for r in csv.reader(fh) if any(c.strip() for c in r)]
    header = [h.strip() for h in rows[0]]
    body = rows[1:]
    xi = header.index(X)
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
{plot_body}
    ax.set_xlabel({xlabel!r})
    ax.set_ylabel({ylabel!r})
    fig.tight_layout()
    fig.savefig(Path(__file__).resolve().parent / {out!r})
'''

_X_NUMBER = """\
def _x(s):
    v = _cell(s)
    return None if v is None or math.isnan(v) else v
"""

_X_DATE = """\
def _x(s):
    import datetime
    import re

    t = s.strip()
    if not re.fullmatch(r"\\d{4}-\\d{2}-\\d{2}", t):
        return None
    try:
        return datetime.date.fromisoformat(t)
    except ValueError:
        return None
"""

_EPOCH_SETUP = """\
    import matplotlib.dates

    try:  # 日期数值按 1970 纪元算（与 Tavotto 的期望值一致）；本进程已用过别的纪元时 matplotlib 会拒绝，保持原状
        matplotlib.dates.set_epoch("1970-01-01T00:00:00")
    except RuntimeError:
        pass
"""

_LINE_BODY = """\
    xs = []
    cols = {y: [] for y in YS}
    for r in body:
        xv = _x(r[xi])
        if xv is None:
            continue
        xs.append(xv)
        for y in YS:
            v = _cell(r[header.index(y)])
            cols[y].append(math.nan if v is None else v)
    for y in YS:
        ax.plot(xs, cols[y], marker="o", label=y)
    if len(YS) > 1:
        ax.legend()"""

_BAR_BODY = """\
    (y,) = YS
    yi = header.index(y)
    cats, heights = [], []
    for r in body:
        v = _cell(r[yi])
        if v is None or math.isnan(v):
            continue
        cats.append(r[xi].strip())
        heights.append(v)
    ax.bar(range(len(heights)), heights, tick_label=cats, label=y)  # 按行位置画：同名类别不叠在一起"""


def render_script(summary: DataSummary, mapping: Mapping, *, data_rel: str, stem: str) -> str:
    """由映射生成脚本文本。CSV 里来的字符串（列名、文件名）只经 `repr()` 进字符串字面量；来源行是 ASCII JSON。

    `data_rel` 是数据文件相对**脚本所在目录**的 POSIX 路径（调用方保证不出项目）；`stem` 决定输出 `<stem>.pdf`。"""
    if mapping.kind not in KINDS:
        raise ValueError(mapping.kind)
    source = json.dumps(
        {
            "data": data_rel,
            "sha256": summary.sha256,
            "mapping": mapping.to_dict(),
            "generator": "data_to_figure/v1",
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    # 多系列：y 轴标题列出各列（单位各自取自列名），图例给每条线的 label；不编一个「value」出来
    ylabel = ", ".join(axis_label(y) for y in mapping.ys)
    return _TEMPLATE.format(
        source=source,
        data_rel=data_rel,
        x=mapping.x,
        ys=list(mapping.ys),
        x_parser=_X_DATE if mapping.x_is_date else _X_NUMBER,
        setup=_EPOCH_SETUP if mapping.x_is_date else "",
        plot_body=_LINE_BODY if mapping.kind == "line" else _BAR_BODY,
        xlabel=axis_label(mapping.x),
        ylabel=ylabel,
        out=f"{stem}.pdf",
    )
