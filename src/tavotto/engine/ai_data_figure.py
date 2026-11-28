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

import codecs
import csv
import datetime as _dt
import hashlib
import io
import itertools
import json
import math
import re
import struct
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import ai_data_progress as progress, xlsxread

#: 「小表」的界线（ADR 0178 修订·大表）：不超过它就走原来的整份读进内存的路径（脚本、指纹、摘要逐字不变）；
#: v1（CSV 同目录路径）、XLSX、合画永远只认小表。超过它的文本表格是「大表」，走流式读 + 缩减（`read_large_table` / 模板的 `*_reduced`）。
SMALL_BYTES = 5 * 1024 * 1024
SMALL_ROWS = 100_000
#: 大表的硬上限：超出就拒绝（`data_too_large` / `data_too_long`），不截断着给一个看起来完整的摘要
MAX_BYTES = 500 * 1024 * 1024
MAX_ROWS = 60_000_000
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
    # v2 的文本表格（`read_text_table`）：像二进制 / 有文字但没有成列的数据 / 指定的表头行不对
    "data_binary",
    "data_not_table",
    "data_header_row_invalid",
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
class SemicolonCell(str):
    """分号分隔的文本表里带逗号的格子（`table_from_text` 打的标记，内容仍是原文）：分号文件里的逗号多半是欧式小数点，
    `parse_cell` 对它**不按千分位认**（`1,234` 是 1.234 还是 1234 分不清，不猜）——整格是文字，走「数值列混了文字」问用户。
    **运行时类**（与 `parse_cell` 一起进公共模块）。"""


class NumberCell(str):
    """整列写法一致才认的数值格（`tag_columns` 打的标记）：内容是原文（`52.3%`、`1,5`），`number` 是它的数值；`parse_cell` 见到它直接给 `numeric`。
    `strip` 返回自己——取数处都写 `cell.strip()`，标记要留住。**运行时类**（与 `parse_cell` 一起进公共模块）。"""

    def __new__(cls, text, number):
        obj = super().__new__(cls, text)
        obj.number = number
        return obj

    def __getnewargs__(self):
        return (str(self), self.number)

    def strip(self, chars=None):
        return self


def parse_cell(s: str) -> tuple[str, float | None]:
    """`(类别, 值)`：`empty` / `nan`（缺失，值为 NaN）、`numeric`（有限浮点）、`text`（不是数，值为 None）。

    **千分位**（v2）：`1,000` / `-12,345,678.9` 认成数。规则只有一条——整格（去首尾空白）是「首组 1–3 位且不以 0 开头，
    后面每组恰好 3 位」的逗号分组整数，可跟小数点与小数：`1,000` → 1000、`1,000.5` → 1000.5；**不认**的有：`1,2` / `0,5` /
    `12,75`（欧式小数，各组不是 3 位或首组是 0）、`1,000,5` / `1,00` / `1,0000`（乱格式）、`,100`、`1,000,`。
    三位小数的欧式写法（`1,234` = 1.234）与千分位无法区分：**分号分隔的表**里带逗号的格子（`SemicolonCell`）一律不按千分位认、
    也不猜成小数，整格是文字（于是该列是「混了文字」或「没有数值」，由用户决定）；逗号 / 制表 / 空白分隔的表按千分位读。
    同一列里出现过不符合千分位写法的逗号数（`1,5`）时，那一列本来就是混列、先问用户，`1,234` 不会被悄悄认成数。
    **整列判定**（`ColumnTagger` / `tag_columns`；大表先预扫一遍定出同一个判定）：整列每一格都带 `%`（`52.3%`）、或分号 / 制表符分隔的表里整列一致用逗号当小数点（`1,5`）时，
    那一列的格子被打上 `NumberCell`，这里直接认成数（`52.3%` → 52.3，不除以 100；`1,5` → 1.5）；单格看不出来，所以 `parse_cell` 自己仍不认裸的 `52.3%` / `1,5`。
    **这是运行时函数**：父进程与生成的脚本共用同一份源码；v1 的摘要走 `legacy_parse_cell`（不认千分位，v1 逐字不变）。"""
    if isinstance(s, NumberCell):
        return "numeric", s.number
    t = s.strip()
    if not t:
        return "empty", math.nan
    try:
        v = float(t)
    except ValueError:
        if (
            "," in t
            and not isinstance(s, SemicolonCell)
            and re.fullmatch(r"[-+]?[1-9]\d{0,2}(?:,\d{3})+(?:\.\d+)?", t)
        ):
            return "numeric", float(t.replace(",", ""))
        return "text", None
    if math.isnan(v):
        return "nan", math.nan
    if math.isinf(v):
        return "text", None
    return "numeric", v


def legacy_parse_cell(s: str) -> tuple[str, float | None]:
    """v1 的单元格规则（不认千分位）：v1 的摘要、期望数据与脚本里的 `_cell` 是一套，**逐字不变**；只给 v1 用。"""
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
    """v1 的日期规则：只认 ISO `YYYY-MM-DD`（v1 的摘要、期望数据与它自己的脚本是一套，**逐字不变**）；v2 用 `parse_when`。"""
    t = s.strip()
    if not _ISO_DATE.fullmatch(t):
        return None
    try:
        return _dt.date.fromisoformat(t)
    except ValueError:
        return None


def parse_when(s):
    """v2 的日期 / 日期时刻：认三种**年份在最前、不会分不清日月**的写法，别的一律不是日期（`03/04/2026` 是 3 月 4 日还是 4 月 3 日分不清，不猜）：
    ISO `2026-03-01`，可带时刻（`T` 或一个空格分隔，`HH:MM` 或 `HH:MM:SS`，如 `2026-03-01 08:00`、`2026-03-01T08:00:30`）；`2026/3/1`（月、日可以只写一位）；
    `2026年3月1日`。只有日期 → `datetime.date`（与 `parse_date` 对 ISO 日期的结果一样）；带时刻 → `datetime.datetime`（无时区；`Z` / `+08:00` 之类的时区后缀不认）。
    不合法（`2026-02-30`、`25:00`）→ None。**运行时函数**；`date_number` 把它们折成自 1970-01-01 起的天数（带时刻的是小数天）。"""
    t = s.strip()
    m = re.fullmatch(
        r"([0-9]{4})-([0-9]{2})-([0-9]{2})(?:[T ]([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?)?", t
    )
    if m is None:
        m = re.fullmatch(r"([0-9]{4})/([0-9]{1,2})/([0-9]{1,2})()()()", t) or re.fullmatch(
            r"([0-9]{4})年([0-9]{1,2})月([0-9]{1,2})日()()()", t
        )
    if m is None:
        return None
    y, mo, d, hh, mi, ss = m.groups()
    try:
        if hh:
            return _dt.datetime(int(y), int(mo), int(d), int(hh), int(mi), int(ss or 0))
        return _dt.date(int(y), int(mo), int(d))
    except ValueError:
        return None


#: 日期数值 = 自 1970-01-01 起的天数；生成的日期脚本会把 matplotlib 的纪元固定成它（见 `_EPOCH_SETUP`）
_EPOCH = _dt.date(1970, 1, 1)


def date_number(d) -> float:
    """日期 → 自 1970-01-01 起的天数；带时刻（`datetime`）的折成小数天（同一天不同时刻是不同的横坐标，不会被当成重复的 x）。**运行时函数**。"""
    if isinstance(d, _dt.datetime):
        return float((d.date() - _EPOCH).days) + (d.hour * 3600 + d.minute * 60 + d.second) / 86400
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

#: 整数编码的分组列（`knockout` 0 / 1、`treatment` 1 / 2 / 3）：不同取值个数的上限。比文字分组的 12 更严——数值本身有大小意义，
#: 取值一多（剂量 / 时间点 / 计数）它更像横轴或测量值而不是类别；6 以内的整数编码才有「几个组互相比较」的读法（见 `numcat_levels`）。
NUMCAT_MAX_LEVELS = 6

LAYOUTS = ("long", "wide", "matrix", "unknown")


@dataclass
class ColStat:
    """大表扫描时一列的精确统计（只算有限数值格）：个数与最小 / 最大。独立于缩减那一遍的另一次累计，用来核对「缩减读到的行没少」。"""

    numeric: int = 0
    lo: float | None = None
    hi: float | None = None


#: 大表预览（数据概览端点只给前 `PREVIEW_ROWS`）：扫描时留下的前几行
LARGE_PREVIEW_ROWS = 40
#: 结构线索里「去重集合」的上限：超过就不再收（保守结局见 `_structure`）
STRUCTURE_SET_CAP = 200_000


@dataclass
class LargeInfo:
    rows: int
    stats: dict[str, ColStat]
    preview: list[list[str]]
    preview_numbers: list[int]
    #: 扫描的文件（绝对路径；父进程内部用，不进任何响应）：缩减那一遍从这里再读一次
    path: str | None = None


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
    #: 整数编码的类别列（`numcat_levels`）：列名 → 不同取值个数。**不是 `groups`**：规则从不把它当分组，只有用户 / 模型点名、配方带上
    #: `FigureRecipe.numcat` 时才按类别用；不进 `to_dict`（对外响应与历史库逐字不变）。大表与合画是空的（这两条路暂不支持）。
    numcat: dict[str, int] = field(default_factory=dict)
    id_like: list[str] = field(default_factory=list)
    #: 数值列取值结构的「像条件 / 顺序」线索（`design_kinds`）：列名 → `block`（连续块内按行不减、块间重置：重复测量里的时间）/ `monotone_levels`（少量取值且按行排好序）/
    #: `levels`（2–`DESIGN_MAX_LEVELS` 个取值、每个都重复出现：剂量）。没有的列是「像测量」。只看小表 v2 的行数据；大表 / 合画 / v1 是空（保持现有行为）；不进 `to_dict`。
    design: dict[str, str] = field(default_factory=dict)
    #: 行号列（`row_index_columns` / `_row_index_name`）：名字在小闭集里、且非空取值恰是从 0 或 1 起逐行 +1 的整数。**不是 `id_like`**（个体编号可以重复、
    #: 会触发「同一对象重复测量吗」；行号每行一个，不可能是个体）。规则默认不拿它当横轴 / 纵轴，用户仍可在下拉里自己选；不进 `to_dict`（对外响应逐字不变）。
    row_index: list[str] = field(default_factory=list)
    repeats: bool | None = None
    row_count: int | None = None
    #: 与 `rows` 逐项对应的文件行号（人看到的行号，表头算第 1 行；空行被丢掉所以不是下标 + 2）；空 = 不知道，按下标 + 2
    row_numbers: list[int] = field(default_factory=list)
    #: 文本表格的读法（v2；`read_text_table` 给，生成的脚本照抄同一组参数）：`delimiter` 是 `,` / `\t` / `;` / `ws`（连续空白）；
    #: `header_row` 是传给 `read_delimited` 的显式表头行（1 起的物理行号），`None` = 第一条非空记录、没有说明行（老读法）；
    #: `header_line` 是这次实际用的表头物理行号（与预览同一口径）。XLSX 与 v1 都是 None。
    delimiter: str | None = None
    header_row: int | None = None
    header_line: int | None = None
    #: 读法的一部分：表头后紧跟的一行单位已被切掉（嗅探认出来的；只在读法与嗅探的表头行一致时才切）。脚本照抄；False = 读法不变
    units_row: bool = False
    #: 读法的一部分：这张表没有表头行，列名是 `col1`…、第一行是数据（嗅探认出来的；`header_line` 是第一条数据行的物理行号）。脚本照抄；False = 读法不变
    no_header: bool = False
    #: 大表读法的一部分：整列判定（`ColumnTagger.tags()`：`{列下标: 真 = 整列带 % / 假 = 小数逗号}`）。小表是读完整份再判（`tag_columns`），大表先预扫一遍定下它、
    #: 再按它逐行读；脚本照抄。空 = 没有这样的列（读法不变）
    col_tags: dict = field(default_factory=dict)
    #: 文本表格实际用的编码（`TEXT_ENCODINGS`；父进程定下、脚本照抄 codec 名，不自己探测）；XLSX / v1 是 None
    encoding: str | None = None
    #: 合画（几份列名相同的表拼成一张）时与 `rows` 逐项对应的「这一行来自哪一份」（该份的分组值）；单份 = 空。`row_numbers` 是各份自己的文件行号
    row_sources: list[str] = field(default_factory=list)
    #: 大表（超过 `SMALL_BYTES` / `SMALL_ROWS` 的文本表格）才有：这时 `rows` 是空的（行数据不进内存），行数在 `row_count`，
    #: 精确的整表统计与前几行预览在这里。**`large is not None` 的摘要上凡是要「逐行取值」的规则都得有流式的答案或拒绝**
    #: （`ai_data_recipe` 的大表资格：只放行有流式答案的画法）；绝不把「没有行」当成「规则判过了」。
    large: LargeInfo | None = None

    def preview(self, limit: int) -> tuple[list[tuple[int, list[str]]], bool]:
        """前 `limit` 行（文件行号, 单元格）与「后面还有」：小表取 `rows`，大表取扫描时留下的前几行。"""
        if self.large is not None:
            got = list(zip(self.large.preview_numbers, self.large.preview))
            return got[:limit], self.large.rows > limit
        return [(self.row_number(i), r) for i, r in enumerate(self.rows[:limit])], len(
            self.rows
        ) > limit

    def row_source(self, i: int) -> str | None:
        return self.row_sources[i] if i < len(self.row_sources) else None

    def row_number(self, i: int) -> int:
        return self.row_numbers[i] if i < len(self.row_numbers) else i + 2

    def to_dict(self) -> dict:
        """给前端 / 历史库 / 模型看的那份：**不带行数据**，只带列名、类型与计数（`privacy_policy`）。"""
        out = {
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
        if self.header_line is not None:  # 只有 v2 的文本表格才有；v1 / XLSX 的输出逐字不变
            out["header_row"] = self.header_line
        return out


#: 表格文件后缀（小写）：CSV / TSV 同一条解析，XLSX 经 `xlsxread`；旧 Excel 格式明确拒绝
CSV_SUFFIXES = (".csv",)
TSV_SUFFIXES = (".tsv", ".tab")
#: 纯文本的数据导出（仪器 / 数值软件常见）：分隔符与表头行靠嗅探（`read_text_table`）；`.dat` 是二进制时归「读不了」
TEXT_SUFFIXES = (".txt", ".dat", ".asc", ".prn", ".xy")
#: GROMACS / Grace 的 `.xvg`：`#` 注释、`@` 指令、空白分隔的数值列；没有表头行，列名在指令里（`xvg_text` 把它改写成带表头行的制表符文本再走同一条读表流水）
XVG_SUFFIXES = (".xvg",)
#: JSON Lines（每行一个扁平对象）：`jsonl_text` 把它改写成带表头行的 CSV 文本再走同一条读表流水（与 `.xvg` 同一个办法）
JSONL_SUFFIXES = (".jsonl", ".ndjson")
XLSX_SUFFIXES = (".xlsx",)
TABLE_SUFFIXES = (
    CSV_SUFFIXES + TSV_SUFFIXES + TEXT_SUFFIXES + XVG_SUFFIXES + JSONL_SUFFIXES + XLSX_SUFFIXES
)
LEGACY_EXCEL_SUFFIXES = xlsxread.LEGACY_SUFFIXES


def _from_xlsx_error(exc: xlsxread.XlsxError) -> DataSourceError:
    return DataSourceError(exc.code, str(exc).partition(": ")[2])


# --------------------------------------------------------------------------- 文本表格（v2）：嗅探 + 按读法解析
# 两层，各一份实现：
#   * 运行时函数（`delimited_records` / `resolve_header` / `table_from_text`）：给定**读法**（分隔符 + 表头物理行号）把文本
#     解析成表。父进程算摘要 / 期望指纹与生成的脚本（`read_delimited`）调用的是同一份源码（`inspect.getsource` 内嵌）；
#   * 嗅探（`_detect` / `read_text_table`）：只在父进程，从文件内容定出读法。脚本里**不嗅探**，读法以字面量写进脚本。
# 读法 = `(delimiter, header_row)`：`delimiter` 是 `,` / `\t` / `;` / `ws`（连续空白）；`header_row` 是 1 起的**物理行号**
# （与预览里给用户看的行号同一口径；带引号的跨行字段只占一条记录，行号仍按物理行）。
def iter_records(lines, delimiter):
    """行迭代器（文件对象以 `newline=""` 打开，或 `io.StringIO(text, newline="")`）→ 逐条产出 `(起始物理行号, 单元格列表, 原行文字 | None)`；
    全空记录（空行、只有分隔符的行）丢掉。`ws` = 每行按连续空白切（原行文字给 `resolve_header` 用）；其余按 `csv` 规则（原行文字是 None）。
    **流式**：不把整份文本留在内存里。**运行时函数**（生成的脚本里也是它）。"""
    if delimiter == "ws":
        for n, line in enumerate(lines, start=1):
            cells = line.split()
            if cells:
                yield n, cells, line.rstrip("\r\n")
        return
    reader = csv.reader(lines, delimiter=delimiter)
    prev = 0
    for row in reader:
        if any(c.strip() for c in row):
            yield prev + 1, row, None
        prev = reader.line_num


def delimited_records(text, delimiter):
    """全文 → `[(起始物理行号, 单元格列表)]`；全空记录（空行、只有分隔符的行）丢掉。`ws` = 每行按连续空白切；其余按 `csv` 规则。
    `iter_records` 的整份文本版（同一份实现）。**这是运行时函数**（生成的脚本里也是它）。"""
    return [(n, cells) for n, cells, _raw in iter_records(io.StringIO(text, newline=""), delimiter)]


def resolve_header(cells, raw, delimiter, width):
    """显式表头行 → 列名。去掉行首的 `#` 说明标记（`# time voltage`）；`ws` 切出来的个数对不上数据列数时，改按「两个以上空白或制表符」
    切（`Time (s)   Voltage (V)` 这种列名里带空格的）。**运行时函数**。"""
    if delimiter == "ws":
        t = raw.strip()
        if t.startswith("#"):
            t = t[1:].strip()
        out = t.split()
        if len(out) != width:
            wide = [c.strip() for c in re.split(r"\s{2,}|\t", t)]
            if len(wide) == width:
                out = wide
        return out
    out = [c.strip() for c in cells]
    if out and out[0].startswith("#"):
        out[0] = out[0][1:].strip()
    return out


def xvg_label(text):
    """Grace 标签里的转义 → 纯文字（保守）：去掉上 / 下标与回正常的标记（`\\S` `\\s` `\\N`）和字体切换（`\\f{...}`），`\\x` + 一个字母按 Symbol 字体
    换成对应的希腊字母（`\\xl` → λ）；**认不出的原样保留**，不猜。上 / 下标的文字留着（`nm\\S2\\N` → `nm2`）。**运行时函数**。"""
    greek = dict(
        zip(
            "abcdefghiklmnopqrstuwxyzDFGLPQSUWXY",
            "αβχδεφγηικλμνοπθρστυωξψζΔΦΓΛΠΘΣΥΩΞΨ",
        )
    )

    def one(m):
        tok = m.group(0)
        if tok.startswith("\\x"):
            return greek.get(tok[2:], tok)
        return ""

    out = re.sub(r"\\x[A-Za-z]|\\[SsN]|\\f\{[^}]*\}", one, text)
    return " ".join(out.split())


def xvg_text(text):
    """`.xvg`（GROMACS / Grace）全文 → 制表符分隔、带表头行的文本，**行数与原文一致**（物理行号照旧）：
    只读第一个数据集（数据集在一行 `&` 处、或数据之后再出现 `@target` / `@type` 处结束，其后的行清空）；`#` 注释行与 `@` 指令行读完即清空（留着的话，列数碰巧与数据相同的注释会被嗅探当成表头）；
    数据行按空白切开改用制表符连。列名取自指令：`xaxis label` → 第 1 列，`s<N> legend`（N 从 0 起）→ 第 N+2 列，只有一条数据列且没有 legend 时用
    `yaxis label`（经 `xvg_label` 清洗）；缺的列叫 `col<N>`；一个名字都没有就不写表头行（交给既有的「没有表头」流程）。表头写在第一条数据行的**前一行**
    （原来那一行指令 / 注释被它替换；数据从第 1 行起就没有地方放，同样不写表头）。**运行时函数**。"""
    lines = re.split(r"\r\n|\r|\n", text)
    first = None
    end = len(lines)
    labels = {}
    for i, line in enumerate(lines):
        t = line.strip()
        if not t or t[0] == "#":
            continue
        if t[0] == "&":
            if first is not None:
                end = i
                break
            continue
        if t[0] == "@":
            if first is not None and re.match(r"@\s*(target|type)\b", t, re.I):
                end = i
                break
            m = re.match(r'@\s*(xaxis|yaxis)\s+label\s+"(.*)"\s*$', t, re.I)
            if m:
                labels.setdefault(m.group(1).lower(), m.group(2))
            m = re.match(r'@\s*s(\d+)\s+legend\s+"(.*)"\s*$', t, re.I)
            if m:
                labels.setdefault("s" + m.group(1), m.group(2))
            continue
        if first is None:
            first = i
    out = []
    for i, line in enumerate(lines):
        t = line.strip()
        if i >= end or (first is not None and i >= first and t[:1] in ("@", "&")):
            out.append("")
        elif first is not None and i >= first and t and t[0] != "#":
            out.append("\t".join(t.split()))
        elif t[:1] in ("@", "#"):
            out.append("")
        else:
            out.append(line)
    if first is not None and first >= 1:
        width = len(lines[first].split())
        names = [labels.get("xaxis")] + [labels.get(f"s{k}") for k in range(width - 1)]
        if width == 2 and names[1] is None:
            names[1] = labels.get("yaxis")
        if any(n is not None for n in names):
            cells = []
            for k, n in enumerate(names):
                n = xvg_label(n) if n is not None else ""
                cells.append(n.replace('"', "'") or f"col{k + 1}")
            out[first - 1] = "\t".join(cells)
    return "\n".join(out)


class JsonlError(ValueError):
    """`jsonl_text` 读不成表：`code` 是 `ERROR_CODES` 里的稳定码（`data_malformed` / `data_too_wide` / `data_empty`）。**运行时类**。"""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def jsonl_text(text, max_columns=64):
    """JSON Lines（`.jsonl` / `.ndjson`）全文 → 逗号分隔、带表头行的 CSV 文本（下一步照旧按 `table_from_text` 读）。**运行时函数**。

    每个非空行是一个**扁平对象**，值只能是数字 / 字符串 / 布尔 / null；列 = 各行的键按首次出现顺序取并集，缺键与 null 都是空格子。
    数字照原文写法（`1e5`、大整数不经 float 往返）；布尔写成 `true` / `false`；字符串原样。**不猜、一律拒绝**（`JsonlError`，码 `data_malformed`）：
    不是 JSON、不是对象、值是嵌套对象 / 数组、同一行重复键、`NaN` / `Infinity` 这类非标准记号、含 NUL / 孤立代理字符的文字、
    第一格以 `#` 开头（读表时会被当成注释行整行丢掉，不静默丢）；列数超 `max_columns` → `data_too_wide`；没有任何记录 → `data_empty`。
    物理行号：表头占第 1 行，原文第 n 行的记录在第 n + 1 行（空行保留为空行）。"""

    def bad(_name):
        raise ValueError("nonstandard constant")

    def pairs(items):
        if len({k for k, _ in items}) != len(items):
            raise ValueError("duplicate key")
        return dict(items)

    keys = {}
    records = []
    for line in re.split(r"\r\n|\r|\n", text):
        t = line.strip()
        if not t:
            records.append(None)
            continue
        try:
            obj = json.loads(
                t, object_pairs_hook=pairs, parse_float=str, parse_int=str, parse_constant=bad
            )
        except (ValueError, RecursionError):
            raise JsonlError("data_malformed") from None
        if not isinstance(obj, dict):
            raise JsonlError("data_malformed")
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                raise JsonlError("data_malformed")
            for piece in (k, v):
                if isinstance(piece, str):
                    if "\x00" in piece:
                        raise JsonlError("data_malformed")
                    try:
                        piece.encode("utf-8")
                    except UnicodeEncodeError:
                        raise JsonlError("data_malformed") from None
            keys.setdefault(k, None)
            if len(keys) > max_columns:
                raise JsonlError("data_too_wide")
        records.append(obj)
    if not keys:
        raise JsonlError("data_empty")

    def cell(v):
        if v is None:
            return ""
        if v is True:
            return "true"
        if v is False:
            return "false"
        return v

    names = list(keys)
    if names[0].lstrip().startswith("#"):
        raise JsonlError("data_malformed")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(names)
    for r in records:
        if r is None:
            out.write("\n")
            continue
        row = [cell(r.get(k)) for k in names]
        if row[0].lstrip().startswith("#"):
            raise JsonlError("data_malformed")
        writer.writerow(row)
    return out.getvalue()


def fill_header(header):
    """表头里**第一列**的名字是空的（pandas `to_csv()` 把行索引写成第一列、表头留空；R `write.csv`、Excel 的 A1 同理）→ 把它叫 `col1`，
    与 `xvg_text` 的缺名列同一个叫法（`col<N>`，N 从 1 数）。**只放宽第一列**：其余位置的空名字、重复名字（含与 `col1` 撞名）原样留着，
    由调用方照旧判 `data_header_invalid`——说明读到的不是表头行（标题行 `实验一,,,`），不替用户猜。返回新列表。**运行时函数**。"""
    out = list(header)
    if len(out) >= 2 and not out[0].strip():
        out[0] = "col1"
    return out


class NamelessTailData(ValueError):
    """表头末尾「没有名字」的列里有数据（`cut_tail` 抛）：那一列不是 Excel 另存时多出来的空列，读表的调用方一律按 `data_header_invalid` 处理。
    **运行时类**（与 `cut_tail` 一起进公共模块；生成的脚本里它只会在父进程已核过的表上不出现）。"""


def nameless_tail(header):
    """表头末尾**连续**名字为空的列有几个（至少留一列）。Excel / WPS 另存 CSV 时常在每行末尾多出几个空格子（`a,b,c,,`），表头里它们也是空名字。
    只数尾部：中间的空名字（`a,,c`）不算，仍是 `data_header_invalid`。**运行时函数**。"""
    k = 0
    while k < len(header) - 1 and not header[-1 - k].strip():
        k += 1
    return k


def cut_tail(cells, keep, total):
    """一行数据去掉表头里没有名字的尾部列：只保留前 `keep` 格。这一行的宽度不是表头原来的宽度 `total`、或被去掉的格子里有一格不是空的，就抛
    `NamelessTailData`——前者不是「每行末尾都多了几个空格子」的表，后者是名字空、整列却有数据；两种都与此前一样按 `data_header_invalid`
    处理（表头里有空名字时，表头判定先于行宽判定），不替用户丢数据、也不改报错的先后。**运行时函数**：整份读（`iter_table` / `_summarize` /
    `read_xlsx`）与流式读是同一个判据。"""
    if len(cells) != total or any(c.strip() for c in cells[keep:]):
        raise NamelessTailData("data_header_invalid")
    return cells[:keep]


def _trim_tail(header, body):
    """`(表头, 数据行迭代器)` → 去掉表头末尾没有名字的列（`nameless_tail`）与各行对应的格子（`cut_tail`）；没有这样的列原样返回。**运行时函数**。"""
    k = nameless_tail(header)
    if not k:
        return header, body
    total = len(header)
    keep = total - k
    return header[:keep], ((n, cut_tail(c, keep, total)) for n, c in body)


def _tagged(body, tags):
    """数据行迭代器按 `tags` 逐行 `apply_tags`；没有 `tags` 原样返回。**运行时函数**。"""
    if not tags:
        return body
    return ((n, apply_tags(c, tags)) for n, c in body)


def _tagged_table(result, tags):
    """`(表头, 数据行迭代器)` 的数据行套 `_tagged`。**运行时函数**。"""
    header, body = result
    return header, _tagged(body, tags)


def iter_table(lines, delimiter, header_row=None, units_row=False, no_header=False, tags=None):
    """`(表头, 数据行迭代器)`；迭代器逐条产出 `(物理行号, 单元格列表)`。读法与 `table_from_text` 完全一样（这就是它的实现）：
    `header_row` 为 None：第一条非空记录是表头，其余都是数据（老读法，干净的 CSV / TSV 与它逐字相同）。给了行号：从该行起（第一条起始行号 ≥ 它的记录）
    是表头，之前的行都是说明、不读；表头之后以 `#` 开头**且列数与表头不同**的行是注释、丢掉（列数相同的 `#` 行是数据）。`units_row` 真：表头之后的
    第一条记录是单位行，不是数据，切掉（嗅探认出来才由调用方给；切掉后数据行的物理行号不变）。`no_header` 真：这张表没有表头行（嗅探认出来才由调用方给：第一条记录整行都是数值、
    下面也是数值），列名叫 `col1`、`col2`…（与 `xvg_text` / `fill_header` 同一个叫法），第一条记录（从 `header_row` 起算）是数据；表头之后的 `#` 行规则同显式表头行。`tags`（`ColumnTagger.tags()`，大表预扫出来的整列判定）给了就逐行 `apply_tags`。没有任何记录 → `([], 空迭代器)`。
    **流式、运行时函数**：父进程与生成的脚本读出来的必然是同一张表；大文件一遍扫过，内存只与表头和（显式表头行时）开头的注释行有关。"""
    records = iter_records(lines, delimiter)
    if header_row is not None:
        records = (r for r in records if r[0] >= header_row)
    first = next(records, None)
    if first is None:
        return [], iter(())
    first_line, first_cells, first_raw = first
    rest = ((n, c) for n, c, _raw in records)
    if no_header:
        rest = itertools.chain([(first_line, first_cells)], rest)  # 第一条记录也是数据
    if delimiter == ";":
        rest = ((n, [SemicolonCell(c) if "," in c else c for c in cells]) for n, cells in rest)
    if units_row:
        next(rest, None)
    if no_header:
        width = len(first_cells)

        def data():
            for n, c in rest:
                if len(c) == width or not c[0].lstrip().startswith("#"):
                    yield n, c

        return ["col" + str(k + 1) for k in range(width)], _tagged(data(), tags)
    if header_row is None:
        return _tagged_table(_trim_tail(fill_header([h.strip() for h in first_cells]), rest), tags)
    # 列数以表头之后第一条非 `#` 记录为准（没有就取紧跟的第一条）；探测时读过的记录接回去，不丢
    held = []
    probe = None
    for n, c in rest:
        held.append((n, c))
        if not c[0].lstrip().startswith("#"):
            probe = c
            break
    if probe is None and held:
        probe = held[0][1]
    header = fill_header(
        resolve_header(
            first_cells,
            first_raw or "",
            delimiter,
            len(probe) if probe is not None else len(first_cells),
        )
    )

    def body():
        for n, c in itertools.chain(held, rest):
            if len(c) == len(header) or not c[0].lstrip().startswith("#"):
                yield n, c

    return _tagged_table(_trim_tail(header, body()), tags)


class ColumnTagger:
    """整列判定的累加器（`tag_columns` 与大表的预扫描**共用这一个实现**，小表整份读与大表流式读由此得到同一个判定）：逐行 `feed`，最后 `tags()` 给出
    `{列下标: 真 = 百分号 / 假 = 小数逗号}`。判据（列级，整列每个非空格都要符合，有一格不符整列照旧）：
    * **百分号**：每格都是 `数字%`（`52.3%`，允许正负号、小数、`%` 前的空白）→ 数值是 `%` 前面的数（**不除以 100**）；
    * **小数逗号**：只在分隔符是分号 / 制表符时；每格都是 `数字` 或 `数字,数字`、至少一格带逗号、且**没有任何一格长得像千分位**（`1,234`：首组 1–3 位不以 0 开头、
      其后每组恰好 3 位）→ 逗号是小数点（`1,5` → 1.5）。有一格像千分位就分不清是 1.234 还是 1234，整列照旧（分号文件里是文字，制表符文件里按千分位认）；逗号分隔、空白分隔的表里绝不这样读。
    **运行时类**。"""

    def __init__(self, width, delimiter):
        self.pct = [True] * width
        self.dec = [delimiter in (";", "\t")] * width
        self.comma = [False] * width
        self.seen = [False] * width

    def feed(self, cells):
        for j, cell in enumerate(cells[: len(self.seen)]):
            t = cell.strip()
            if not t:
                continue
            self.seen[j] = True
            if self.pct[j] and not re.fullmatch(r"[-+]?[0-9]+(?:\.[0-9]+)?\s*%", t):
                self.pct[j] = False
            if self.dec[j]:
                if not re.fullmatch(r"[-+]?[0-9]+(?:,[0-9]+)?", t) or re.fullmatch(
                    r"[-+]?[1-9][0-9]{0,2}(?:,[0-9]{3})+", t
                ):
                    self.dec[j] = False
                elif "," in t:
                    self.comma[j] = True

    def tags(self):
        out = {}
        for j, seen in enumerate(self.seen):
            if not seen:
                continue
            if self.pct[j]:
                out[j] = True
            elif self.dec[j] and self.comma[j]:
                out[j] = False
        return out


def apply_tags(cells, tags):
    """一行的格子按 `tags`（`ColumnTagger.tags()`）把判定为数值的列换成 `NumberCell`（原文 + 数值），别的格原样；没有 `tags` 原样返回。**运行时函数**。"""
    if not tags:
        return cells
    out = list(cells)
    for j, percent in tags.items():
        if j < len(out) and out[j].strip():
            t = out[j].strip()
            out[j] = NumberCell(t, float(t[:-1]) if percent else float(t.replace(",", ".")))
    return out


def tag_columns(header, rows, delimiter):
    """整份读的文本表里「整列写法一致」才认的两种数值（判据见 `ColumnTagger`）：整列符合的每格换成 `NumberCell`，别的列原样；返回新的行列表
    （没有符合的列时就是传进来的那个）。只改内存里的副本，不碰文件。大表（流式）由 `read_text_table` 先预扫一遍定出同一个 `tags`、读表时按它逐行 `apply_tags`。**运行时函数**。"""
    tagger = ColumnTagger(len(header), delimiter)
    for r in rows:
        tagger.feed(r)
    tags = tagger.tags()
    return [apply_tags(r, tags) for r in rows] if tags else rows


def table_from_text(text, delimiter, header_row=None, units_row=False, no_header=False):
    """`(表头, 数据行, 数据行的物理行号)`：`iter_table` 的整份文本版（同一份读法，见它的文档），多一步整列判定 `tag_columns`（整列带 `%` / 整列小数逗号）。**运行时函数**。"""
    header, body = iter_table(
        io.StringIO(text, newline=""), delimiter, header_row, units_row, no_header
    )
    pairs = list(body)
    return header, tag_columns(header, [c for _, c in pairs], delimiter), [n for n, _ in pairs]


# --- 嗅探（只在父进程）
#: 嗅探只看开头这么多字符（再往后只会让同一个读法重复得到验证）；整份文件仍按 `MAX_BYTES` / `MAX_ROWS` 限制读
SNIFF_CHARS = 256 * 1024
#: 嗅探候选的优先顺序（得分相同时靠前的赢）
_SNIFF_ORDER = ("\t", ";", ",", "ws")
#: 没认准时给前端的原文预览：默认前 30 行，猜的表头更靠后就多给到它后 5 行；最多 100 行、每行最多 200 字符
PREVIEW_LINES = 30
PREVIEW_MAX_LINES = 100
PREVIEW_CHARS = 200
#: 界面 / 接口里的分隔符名字（`,` 等字符本身不外露）
DELIMITER_NAMES = {",": "comma", "\t": "tab", ";": "semicolon", "ws": "space"}
_UNITS_CELL = re.compile(r"^[\[(（【].*[\])）】]$")
_DECIMAL_COMMA = re.compile(r"[-+]?\d+,\d+")


def _is_hash(cells) -> bool:
    return cells[0].lstrip().startswith("#")


def _numlike(cell: str) -> bool:
    """嗅探用的「像数」：数、NaN，或欧式小数（`1,5`）——后者只用于挑分隔符，读表时仍是文字。"""
    t = cell.strip()
    return parse_cell(t)[0] in ("numeric", "nan") or bool(_DECIMAL_COMMA.fullmatch(t))


#: 仪器导出的单位行里常见、但不属于 `UNITS`（`UNITS` 管列名后缀的显示，加了会改变列名的写法）的写法：只用来认「这一行是单位行」
_UNITS_ROW_EXTRA = frozenset(
    {"a.u.", "a.u", "au", "arb.u.", "arb.", "cps", "counts", "%", "deg", "rad"}
)


def _units_like(cells) -> bool:
    cells = [c.strip() for c in cells if c.strip()]
    return bool(cells) and all(
        _UNITS_CELL.match(c) or c in UNITS or c.lower() in _UNITS_ROW_EXTRA for c in cells
    )


def _block(records):
    """数据块：`(列数 W, 块内记录, 非注释行数)` 或 None。W = 非 `#` 记录里最常见的列数（≥ 2；并列取更宽的）；块 = 文件末尾连续
    列数为 W 的那一段（列数不同的 `#` 行当注释跳过）。块里至少要有 2 条记录（表头 + 一行数据）。"""
    plain = [r for r in records if not _is_hash(r[1])]
    pool = [r for r in (plain or records) if len(r[1]) >= 2]
    if not pool:
        return None
    counts: dict[int, int] = {}
    for _, c in pool:
        counts[len(c)] = counts.get(len(c), 0) + 1
    width = max(counts, key=lambda w: (counts[w], w))
    kept = [r for r in records if not _is_hash(r[1]) or len(r[1]) == width]
    start = len(kept)
    while start > 0 and len(kept[start - 1][1]) == width:
        start -= 1
    block = kept[start:]
    return (width, block, len(plain)) if len(block) >= 2 else None


def _numeric_columns(block, width) -> list[int]:
    """数值列：块里（不含第一条与 `#` 行）该列非空格子 ≥ 50% 像数。"""
    tail = [r for r in block[1:] if not _is_hash(r[1])][-50:]
    out = []
    for j in range(width):
        vals = [r[1][j].strip() for r in tail if r[1][j].strip()]
        if vals and sum(_numlike(v) for v in vals) >= 0.5 * len(vals):
            out.append(j)
    return out


def _name_like(cells) -> bool:
    return (
        all(c.strip() for c in cells)
        and not any(_numlike(c) for c in cells)
        and not _units_like(cells)
    )


@dataclass(frozen=True)
class TextLayout:
    """嗅探结果：读法 + 把握。`sure` 假 = 表头行是猜的（`header_line` 是猜的那一行）。"""

    delimiter: str
    header_line: int
    sure: bool
    #: 表头行后面紧跟一行单位（`nm  a.u.`）、再往后才是数据：读表时把这一行切掉（不是数据，也不是第二个表头）
    units: bool = False
    #: 这张表没有表头行（第一条记录整行都是数值、下面也是数值）：`header_line` 是第一条数据的物理行号，列名叫 `col1`…；读表时第一条记录算数据
    no_header: bool = False


@dataclass(frozen=True)
class RaggedLayout:
    """像一张表、但有行的列数对不上：**不当成别的表读**（不丢表头和前面的观测），报 `data_ragged`。`line` 是猜的表头行（用户可点另一行覆盖）。"""

    delimiter: str
    line: int


def _ragged_hint(sample: str) -> RaggedLayout | None:
    """所有候选的数据块都认不出来时（例如最后一行列数不符，尾部连续等宽的那一段不存在）：看有没有「至少 3 条等宽记录且有数值列」的分隔符，
    有就是畸形的表而不是散文。"""
    best = None
    for pref, d in enumerate(_SNIFF_ORDER):
        recs = [r for r in delimited_records(sample, d) if not _is_hash(r[1])]
        counts: dict[int, int] = {}
        for _, c in recs:
            if len(c) >= 2:
                counts[len(c)] = counts.get(len(c), 0) + 1
        if not counts:
            continue
        width = max(counts, key=lambda w: (counts[w], w))
        same = [r for r in recs if len(r[1]) == width]
        if len(same) < 3 or not _numeric_columns(same, width):
            continue
        key = (len(same), -pref)
        if best is None or key > best[0]:
            best = (key, RaggedLayout(d, same[0][0]))
    return best[1] if best else None


def _headerless(block) -> bool:
    """块的第一条记录**每一格都是数值**（`parse_cell` 判 `numeric`，空格 / NaN / 文字都不算）、而且紧随其后的记录（最多 50 条非 `#` 行，至少 1 条）里每个非空格子也是
    数值 → 这张表没有表头行：第一行是数据（`10.00  116` 这种两列纯数字的仪器导出）。**只看整行**：表头本来就是数字、第一列却是文字的宽表（`year,2019,2020`）
    第一格是文字，不算；数字表头压在整张纯数字的表上（`2019 2020 2021` 下面全是数）分不开，按没有表头读（嗅探仍标「猜的」，`unsure`）。"""
    rows = [r for _n, r in block if not _is_hash(r)]
    if len(rows) < 2 or any(parse_cell(c)[0] != "numeric" for c in rows[0]):
        return False
    return all(parse_cell(c)[0] in ("numeric", "nan") for r in rows[1:51] for c in r if c.strip())


def _header_end(block, numeric) -> int:
    """块里第一条「有数值列的格子是数」的记录的下标 = 它之前有几条记录是表头候选（0 条：没有候选，表头在块外或没有表头）；没有数值列时按 1（只有第一条）。"""
    if not numeric:
        return 1
    return next(
        (i for i, (_n, r) in enumerate(block) if any(_numlike(r[j]) for j in numeric)),
        0,
    )


def _pick_header(d: str, block, width: int, k: int) -> TextLayout:
    """块里前 `k`（≥ 1）条记录是表头候选：挑哪一条是列名。**文本表（`_detect`）与 XLSX（`xlsx_header_guess`）共用这一个判据**。"""
    cands = [r for r in block[:k] if not _is_hash(r[1])] or block[:k]  # 与表头同列数的 `#` 行是注释
    # 默认取第一条像名字的（老读法）。例外：候选里有**单位行**（`nm  a.u.`）时，列名是紧挨在它前面的那一行——更前面同列数的说明行
    # （`operator: lab`）也「像名字」，不能因它排在前面被选中。没有单位行时不往后挪：`group,value` 后面紧跟一条夹着文字标记的观测
    # （`control,<0.1`）也「像名字」，那是数据不是表头。
    u = next((i for i, r in enumerate(cands) if _is_units_row(r[1], width)), None)
    if u is not None and u >= 1 and _name_like(cands[u - 1][1]):
        pick = cands[u - 1]
    else:
        pick = next((r for r in cands if _name_like(r[1])), cands[0])
    return TextLayout(d, pick[0], len(cands) == 1, _units_follow(block, pick, k, width))


def xlsx_header_guess(table: list[list[str]], numbers: list[int]) -> int | None:
    """XLSX 的表头行是哪一行（工作表行号）——**只在第一条非空行读不成表头时**才问（`_summarize_book`）：开头有标题行（`酶标仪导出 2026-11-20`）、
    列名在后面几行。判据与文本表是同一个（`_block` / `_numeric_columns` / `_header_end` / `_pick_header`；XLSX 的每一行已经是切好的格子，所以没有分隔符那一层）。
    给不出（没有成块的数据、没有候选、猜的就是第一行、表头后紧跟单位行——XLSX 不切单位行）= None，调用方照旧报原来的错。"""
    recs = [(n, list(r)) for n, r in zip(numbers, table) if any(c.strip() for c in r)]
    blk = _block(recs)
    if blk is None:
        return None
    width, block, _plain = blk
    k = _header_end(block, _numeric_columns(block, width))
    if k < 1:
        return None
    layout = _pick_header("xlsx", block, width, k)
    if layout.units or layout.header_line == recs[0][0]:
        return None
    return layout.header_line


def _detect(text: str, suffix: str) -> TextLayout | RaggedLayout | None:
    """从内容定读法；不是表（没有成列的数据）→ None；像表但有列数不符的行 → `RaggedLayout`。

    **畸形行规则**（表头定下之后，数据区里列数不符的行一律不收）：数据块是文件末尾连续等宽的一段；块**之前**若还有等宽的非注释记录，
    说明块不是整张表（中间 / 开头有一行畸形），报 `RaggedLayout` 而不是把后缀当成新表；块之前只有列数不同的说明行 = 仪器导出的说明行，正常。
    尾部：空行忽略、`#` 开头的行忽略（注释）；其它列数不符的尾行（footer 也一样）一律算畸形，不静默丢弃。

    * 候选分隔符：`.csv` / `.tsv` / `.tab` 先只试自己的（干净的老文件逐字不变），不成才试其它（分号 CSV）；其余后缀四种都试。
    * 打分：数据块的记录数大的赢；相同则「像数」的格子占比高的赢（欧式小数 `1,5` 也算像数，所以 `1,5;2,5` 判分号、`1,5 2,5` 判空白）；
      再相同按 制表 / 分号 / 逗号 / 空白。`.txt` / `.dat` / `.asc` 的块里必须有数值列——散文、清单不是表。
    * 表头：块里第一条「有数值列的格子是数」的记录之前的记录是表头候选（0 条：表头可能是块外紧挨着的 `#` 行或带空格的列名行；
      还没有 = 没有表头，猜第一行）。候选 1 条 = 认得准；≥ 2 条（标题 + 列名、列名 + 单位）= 猜第一条「像名字」的，没认准。"""
    primary = {".csv": ",", ".tsv": "\t", ".tab": "\t"}.get(suffix)
    sample = text
    if len(sample) > SNIFF_CHARS:
        cut = sample.rfind("\n", 0, SNIFF_CHARS)
        sample = sample[: cut if cut > 0 else SNIFF_CHARS]
    # `.csv` / `.tsv` / `.tab`：先只认自己的分隔符，且数据块要覆盖全部非注释行（干净的老文件）；有说明行 / 列数对不上就和别的分隔符
    # 一起比（自己的排在并列时的最前）。否则分号 CSV 里带逗号的数会被当成逗号分隔的表。
    stages = (
        [(primary,), (primary, *(d for d in _SNIFF_ORDER if d != primary))]
        if primary
        else [_SNIFF_ORDER]
    )
    for stage_no, stage in enumerate(stages):
        best = None
        for pref, d in enumerate(stage):
            recs = delimited_records(sample, d)
            blk = _block(recs)
            if blk is None:
                continue
            width, block, plain = blk
            if primary and stage_no == 0 and len(block) != plain:
                continue
            numeric = _numeric_columns(block, width)
            if (primary is None or stage_no == 1) and not numeric and d != primary:
                continue
            cells = [c.strip() for _, r in block[1:] for c in r if c.strip()]
            frac = sum(_numlike(c) for c in cells) / len(cells) if cells else 0.0
            key = (len(block), round(frac, 3), -pref)
            if best is None or key > best[0]:
                best = (key, d, width, block, numeric, recs)
        if best is None:
            continue
        _key, d, width, block, numeric, recs = best
        if any(r[0] < block[0][0] and not _is_hash(r[1]) and len(r[1]) == width for r in recs):
            first = next(r for r in recs if not _is_hash(r[1]) and len(r[1]) == width)
            return RaggedLayout(d, first[0])
        # 表头候选
        k = _header_end(block, numeric)
        if k >= 1:
            return _pick_header(d, block, width, k)
        first_line = block[0][0]
        prev = next((r for r in reversed(recs) if r[0] < first_line), None)
        if prev is not None:
            raw = re.split(r"\r\n|\r|\n", sample)[prev[0] - 1] if d == "ws" else ""
            hdr = resolve_header(prev[1], raw, d, width)
            if (_is_hash(prev[1]) or d == "ws") and len(hdr) == width and all(hdr):
                return TextLayout(d, prev[0], True)
        if _headerless(block):
            return TextLayout(d, first_line, False, no_header=True)
        return TextLayout(d, first_line, False)
    return _ragged_hint(sample)


def _is_units_row(cells, width: int) -> bool:
    """整行都是裸写的单位（`nm  a.u.`）：括号包着的（`(a) (b)`、`[mV] [s]`）既可能是单位也可能就是列名，仍交给用户在两行里点（`unsure`），不当单位行。"""
    return (
        len(cells) == width
        and _units_like(cells)
        and not any(_numlike(c) or _UNITS_CELL.match(c.strip()) for c in cells)
    )


def _units_follow(block, pick, k: int, width: int) -> bool:
    """`pick`（表头记录）的**下一条**记录是单位行，且它正好是第一条数据行（块里下标 `k`）之前的最后一条：`Wavelength  Intensity` / `nm  a.u.` / 数据。
    只认「整行都是单位写法、没有一格像数」的行（`_units_like`）；表头与数据之间还有别的记录、或下一条就是数据，都不是。"""
    i = next((n for n, r in enumerate(block) if r is pick), None)
    if i is None or i + 1 != k - 1:
        return False
    return _is_units_row(block[i + 1][1], width)


def _clip_line(t: str) -> str:
    """一行原文给人看：控制字符换空格（保留制表符），超长截断。表里的文字是不可信输入，只显示。"""
    t = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", t)
    return t if len(t) <= PREVIEW_CHARS else t[: PREVIEW_CHARS - 1] + "…"


#: 表头行之前被跳过的说明行，最多给多少行
LEAD_MAX_LINES = 20


def _lead(lines: list[str], header_line: int) -> tuple[tuple[int, str], ...]:
    """表头行之前的原文行（说明行）：`((物理行号, 原文),…)`，空行不给，最多 `LEAD_MAX_LINES` 行，每行长度有上界。"""
    out = []
    for n in range(1, min(header_line, len(lines) + 1)):
        if lines[n - 1].strip():
            out.append((n, _clip_line(lines[n - 1])))
            if len(out) >= LEAD_MAX_LINES:
                break
    return tuple(out)


def _preview(text: str, guess: int) -> tuple[tuple[int, str], ...]:
    """没认准时给用户点的那几行原文：`((物理行号, 原文),…)`。行数与每行长度都有上界；控制字符换成空格（表里的文字是不可信输入，只显示）。"""
    lines = re.split(r"\r\n|\r|\n", text, maxsplit=PREVIEW_MAX_LINES)[:PREVIEW_MAX_LINES]
    last = min(len(lines), max(PREVIEW_LINES, guess + 5))
    out = []
    for n in range(1, last + 1):
        out.append((n, _clip_line(lines[n - 1])))
    return tuple(out)


@dataclass(frozen=True)
class TextTable:
    """`read_text_table` 的结果。`status`：`ok`（认得准）/ `unsure`（能读成列但表头行是猜的）/ `unreadable`（读不了，`code` 是 `ERROR_CODES` 里的稳定码）。
    `preview` 只在 `unsure` 时有（原文行，有上界）；`summary` 在 `unreadable` 时为 None，`unsure` 且猜的表头读不出合格的表（空 / 重复列名）时也为 None。"""

    status: str
    code: str | None = None
    delimiter: str | None = None
    header_line: int | None = None
    columns: int | None = None
    rows: int | None = None
    preview: tuple[tuple[int, str], ...] = ()
    summary: DataSummary | None = None
    #: 表头行之前被跳过的说明行（`_lead`）；只给数据概览端点用，不进 `to_dict`（`/api/ai/data-files` 的响应不变）
    lead: tuple[tuple[int, str], ...] = ()
    #: 实际用的编码（`TEXT_ENCODINGS`）；解码之前就读不了的（太大 / 二进制 / 解不开）是 None
    encoding: str | None = None

    def to_dict(self) -> dict:
        out = {
            "status": self.status,
            "code": self.code,
            "delimiter": DELIMITER_NAMES.get(self.delimiter) if self.delimiter else None,
            "encoding": self.encoding,
            "header_row": self.header_line,
            "columns": self.columns,
            "rows": self.rows,
            "preview": [{"line": n, "text": t} for n, t in self.preview],
        }
        if self.summary is not None and self.summary.large is not None:
            out["large"] = True  # 大表（读它要几十秒）；小表的响应没有这一项
        return out


#: 文本表格认的编码（稳定名，给界面显示；不外露 Python 的 codec 名）。判定规则只有这一条、确定且可解释：
#: 先看 BOM（UTF-8 BOM / UTF-16 LE·BE），没有 BOM 就严格按 UTF-8 解，失败再严格按 GB18030 解（覆盖 GBK / GB2312），都失败 = 读不了。
#: 不探测、不打分、不引第三方库；**不加** Big5 / Shift-JIS / Latin-1 / Windows-1252：它们的字节大多也是合法的 GB18030，
#: 先后顺序只会把一种编码的文字悄悄解成另一种的乱码（Latin-1 更是什么字节都解得开），科研数据被静默读错比说「读不了」严重。
TEXT_ENCODINGS = ("utf-8", "utf-8-bom", "utf-16le", "utf-16be", "gb18030")
#: 生成的脚本里 `.decode(...)` 用的 codec 名；UTF-8 / UTF-8 BOM 不在表里（脚本沿用 `read_delimited` 的 `utf-8-sig`，逐字不变）
_SCRIPT_CODECS = {"utf-16le": "utf-16", "utf-16be": "utf-16", "gb18030": "gb18030"}


#: 大表脚本里总是显式写出 codec（读法的一部分）：编码名 → Python codec 名
_LARGE_CODECS = {
    "utf-8": "utf-8",
    "utf-8-bom": "utf-8-sig",
    "utf-16le": "utf-16",
    "utf-16be": "utf-16",
    "gb18030": "gb18030",
}


def large_codec(encoding: str | None) -> str:
    return _LARGE_CODECS.get(encoding or "", "utf-8")


def script_codec(encoding: str | None) -> str | None:
    """脚本必须显式写出的 codec 名；`None` = 用 `read_delimited`（UTF-8 / UTF-8 BOM，与此前生成的脚本逐字相同）。"""
    return _SCRIPT_CODECS.get(encoding or "")


def _plausible_cjk(text: str) -> bool:
    """GB18030 严格解码通过之后的合理性检查：映射到私用区 / 未分配码位的文字多半是别的编码（Big5 / Shift-JIS）被硬解，拒绝。"""
    return not any(
        unicodedata.category(ch) in ("Co", "Cn", "Cs") for ch in set(text) if ch > "\x7f"
    )


def decode_text(raw: bytes) -> tuple[str, str] | None:
    """`(文本, 编码名)`；认不出 = `None`（调用方报 `data_decode_error`）。父进程唯一的解码入口，生成的脚本用同一个 codec 名
    `bytes.decode` 同一份字节（`script_codec`），不经平台换行转换，读出的就是父进程读的那段文本。"""
    try:
        if raw.startswith(b"\xef\xbb\xbf"):
            return raw.decode("utf-8-sig"), "utf-8-bom"
        if raw.startswith(b"\xff\xfe\x00\x00") or raw.startswith(b"\x00\x00\xfe\xff"):
            return None  # UTF-32：不支持
        if raw.startswith(b"\xff\xfe"):
            return raw.decode("utf-16"), "utf-16le"
        if raw.startswith(b"\xfe\xff"):
            return raw.decode("utf-16"), "utf-16be"
    except UnicodeDecodeError:
        return None
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        text = raw.decode("gb18030")
    except UnicodeDecodeError:
        return None
    return (text, "gb18030") if _plausible_cjk(text) else None


def _unreadable(code: str) -> TextTable:
    return TextTable("unreadable", code=code)


def _read_small_text_table(
    path: str | Path, *, header_row: int | None = None, keep_rows: bool = True
) -> TextTable:
    """读一个文本表格（`.csv` / `.tsv` / `.tab` / `.txt` / `.dat` / `.asc`）：嗅探读法 → 按读法解析 → 受控摘要。**不抛异常**，读不了就是
    `status == "unreadable"`。`header_row`（1 起的物理行号）是调用方指定的「每列名字在第几行」，不给就用嗅探的。

    上限沿用 `MAX_BYTES` / `MAX_ROWS` / `MAX_COLUMNS`。只读：数据文件不被改写。后缀是 `.csv` / `.tsv` / `.tab` 而嗅探不出读法时，
    退回老读法（第一行是表头、按后缀分隔），错误码与 v1 逐字相同。"""
    p = Path(path)
    suffix = p.suffix.lower()
    try:
        size = p.stat().st_size
        if size > SMALL_BYTES:
            return _unreadable("data_too_large")
        raw = p.read_bytes()
    except OSError:
        return _unreadable("data_unreadable")
    if b"\x00" in raw and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return _unreadable("data_binary")  # 有 BOM 的 UTF-16 本来就带 NUL 字节，解码后再查
    decoded = decode_text(raw)
    if decoded is None:
        return _unreadable("data_decode_error")
    text, encoding = decoded

    def _tt(*a, **kw):
        return TextTable(*a, encoding=encoding, **kw)

    if "\x00" in text:
        return _unreadable("data_binary")
    if sum(1 for ch in text if ch < " " and ch not in "\t\r\n\f") > max(8, len(text) // 50):
        return _unreadable("data_binary")  # 控制字符占 2% 以上：不是文本
    if not text.strip():
        return _unreadable("data_empty")
    if suffix in XVG_SUFFIXES:
        text = xvg_text(text)  # 脚本里调用的是同一个函数（见 `ai_data_template._reader_expr`）
    if suffix in JSONL_SUFFIXES:
        if encoding not in ("utf-8", "utf-8-bom"):
            return _unreadable(
                "data_decode_error"
            )  # JSON Lines 只认 UTF-8（可带 BOM）：脚本里写死 `utf-8-sig`
        try:
            text = jsonl_text(text, MAX_COLUMNS)  # 脚本里调用的是同一个函数
        except JsonlError as exc:
            return _unreadable(exc.code)
        header_row = None  # 表头永远是改写后的第 1 行：调用方指定的行号对它没有意义
    if text.count("\n") + text.count("\r") > 2 * SMALL_ROWS + 1000:
        return _unreadable("data_too_long")
    sha = hashlib.sha256(raw).hexdigest()
    try:
        layout = TextLayout(",", 1, True) if suffix in JSONL_SUFFIXES else _detect(text, suffix)
    except csv.Error:
        return _unreadable("data_malformed")
    if isinstance(layout, RaggedLayout):
        if header_row is None:
            return _tt(
                "unreadable",
                code="data_ragged",
                delimiter=layout.delimiter,
                header_line=layout.line,
                preview=_preview(text, layout.line),
            )  # 猜的表头行与原文预览一并给出：用户点对表头行后按它读（指定行之后的畸形行同样拒绝）
        layout = TextLayout(layout.delimiter, layout.line, True)
    if layout is None:
        if suffix not in CSV_SUFFIXES + TSV_SUFFIXES:
            return _unreadable("data_not_table")
        delim = "\t" if suffix in TSV_SUFFIXES else ","
        layout = TextLayout(delim, 0, True)  # 老读法：行号 0 = 「第一条非空记录」
    delim = layout.delimiter
    lines = re.split(r"\r\n|\r|\n", text)
    if header_row is not None and not 1 <= header_row <= len(lines):
        return _tt(
            "unreadable", code="data_header_row_invalid", preview=_preview(text, 1)
        )  # 指定的行不在文件里：原文照给，用户可以重新点一行
    if layout.header_line == 0 and header_row is None:
        # 老读法（嗅探不出）：错误码与 v1 一致，行号按记录序号（与 v1 一致）
        try:
            table = list(csv.reader(io.StringIO(text, newline=""), delimiter=delim))
        except csv.Error:
            return _unreadable("data_malformed")
        if table:  # 整列判定与脚本里的 `table_from_text` 同一个（`tag_columns`）
            table = [table[0], *tag_columns(table[0], table[1:], delim)]
        try:
            summ = _summarize(
                table,
                path_name=p.name,
                sha256=sha,
                size=size,
                keep_rows=keep_rows,
                row_numbers=list(range(1, len(table) + 1)),
                parse=parse_cell,
                delimiter=delim,
                header_line=1,
                encoding=encoding,
            )
        except DataSourceError as exc:
            return _unreadable(exc.code)
        return _tt(
            "ok", delimiter=delim, header_line=1, columns=len(summ.columns),
            rows=summ.row_count if summ.row_count is not None else len(summ.rows), summary=summ,
        )  # fmt: skip
    line = header_row if header_row is not None else layout.header_line
    # 读法里的 header_row：干净的老文件（首行表头、非空白分隔、没有 `#` 行）仍写 None，脚本与 v1 / 老版同一行；其余一律显式
    first_physical = next((i + 1 for i, ln in enumerate(lines) if ln.strip()), 1)
    script_header: int | None = line
    if delim != "ws" and line == first_physical and not re.search(r"(?m)^\s*#", text):
        script_header = None
    # 单位行只在读的就是嗅探认出的那一行表头时才切：用户另指一行（`header_rows`）= 他在说「读法由我定」，不替他猜单位行
    units = bool(layout.units and line == layout.header_line)
    # 没有表头行同理：只在读的就是嗅探认出的那一行时才成立（用户另指一行 = 读法由他定；摘要的 `header_line` 传回来重读时正是这一行，读出同一张表）
    no_header = bool(layout.no_header and line == layout.header_line)
    tail_data = False
    try:
        header, rows, row_lines = (
            table_from_text(text, delim, script_header, True)
            if units
            else table_from_text(text, delim, script_header, False, no_header)
        )
    except csv.Error:
        return _unreadable("data_malformed")
    except NamelessTailData:  # 表头末尾没有名字的列里有数据：与名字空的列一样是 `data_header_invalid`（下面 `_summarize` 前统一处理）
        header, rows, row_lines, tail_data = [], [], [], True
    if not header and not tail_data:
        if header_row is not None:
            return _tt(
                "unreadable", code="data_header_row_invalid", delimiter=delim,
                header_line=line, preview=_preview(text, line),
            )  # fmt: skip
        return _unreadable("data_empty")
    guessed = not layout.sure and header_row is None
    try:
        if tail_data:
            raise DataSourceError("data_header_invalid")
        summ = _summarize(
            [header, *rows],
            path_name=p.name,
            sha256=sha,
            size=size,
            keep_rows=keep_rows,
            row_numbers=[line, *row_lines],
            parse=parse_cell,
            delimiter=delim,
            header_row=script_header,
            header_line=line,
            encoding=encoding,
            units_row=units,
            no_header=no_header,
        )
    except DataSourceError as exc:
        if guessed and exc.code == "data_header_invalid":
            return _tt(
                "unsure", delimiter=delim, header_line=line, columns=len(header),
                preview=_preview(text, line), lead=_lead(lines, line),
            )  # fmt: skip
        if (
            header_row is not None
        ):  # 用户指定的行读不成表（畸形行 / 列名空或重复…）：原文照给，用户可以再点一行
            return _tt(
                "unreadable", code=exc.code, delimiter=delim, header_line=line,
                preview=_preview(text, line),
            )  # fmt: skip
        return _unreadable(exc.code)
    return _tt(
        "unsure" if guessed else "ok",
        delimiter=delim,
        header_line=line,
        columns=len(summ.columns),
        rows=summ.row_count if summ.row_count is not None else len(summ.rows),
        preview=_preview(text, line) if guessed else (),
        summary=summ,
        lead=_lead(lines, line),
    )


# --------------------------------------------------------------------------- 大表（流式读）
#: 大表读法：一遍校验（sha256 / 编码 / 二进制 / 行数）+ 一遍解析统计，内存只与列数和有界的集合有关；嗅探、预览、说明行取开头这一段
LARGE_HEAD_BYTES = 1024 * 1024
_CHUNK_BYTES = 4 * 1024 * 1024
_CTRL_DELETE = {c: None for c in range(32) if chr(c) not in "\t\r\n\f"}
_LARGE_CACHE: dict = {}
_LARGE_CACHE_MAX = 6
_LARGE_LOCK = threading.Lock()


def _stream_check(p: Path) -> tuple[str, str, str, int] | str:
    """大表的一遍校验：`(sha256, 编码名, 打开用的 codec, 行结束符个数)`，或稳定错误码。判定规则与 `decode_text` / `_read_small_text_table` 的
    整份读逐条一致（BOM → UTF-8 BOM / UTF-16；否则严格 UTF-8，再严格 GB18030 + 合理性；NUL / 控制字符占 2% 以上 = 二进制；全空白 = 空），
    只是分块增量解码，不把整份文件读进内存。"""
    sha = hashlib.sha256()
    total = 2 * p.stat().st_size  # 两遍：sha256 / NUL，再增量解码；进度按两遍合计
    read = 0
    with p.open("rb") as fh:
        head = fh.read(4)
        fh.seek(0)
        nul = False
        for chunk in iter(lambda: fh.read(_CHUNK_BYTES), b""):
            sha.update(chunk)
            read += len(chunk)
            progress.tick("validate", read, total)
            if not nul and b"\x00" in chunk:
                nul = True
    digest = sha.hexdigest()
    if head.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return "data_decode_error"
    if head.startswith(b"\xef\xbb\xbf"):
        attempts = [("utf-8-sig", "utf-8-bom")]
    elif head.startswith((b"\xff\xfe", b"\xfe\xff")):
        attempts = [("utf-16", "utf-16le" if head.startswith(b"\xff\xfe") else "utf-16be")]
    else:
        if nul:
            return "data_binary"
        attempts = [("utf-8", "utf-8"), ("gb18030", "gb18030")]
    for codec, label in attempts:
        dec = codecs.getincrementaldecoder(codec)(errors="strict")
        chars = ctrl = lines = 0
        content = False
        exotic: set[str] = set()
        try:
            with p.open("rb") as fh:
                last = False
                while not last:
                    chunk = fh.read(_CHUNK_BYTES)
                    last = not chunk
                    read += len(chunk)
                    progress.tick("validate", min(read, total), total)
                    text = dec.decode(chunk, final=last)
                    if "\x00" in text:
                        return "data_binary"
                    chars += len(text)
                    ctrl += len(text) - len(text.translate(_CTRL_DELETE))
                    lines += text.count("\n") + text.count("\r")
                    content = content or (bool(text) and not text.isspace())
                    if codec == "gb18030":
                        exotic.update(ch for ch in set(text) if ch > "\x7f")
        except UnicodeDecodeError:
            continue
        if codec == "gb18030" and not _plausible_cjk("".join(exotic)):
            return "data_decode_error"
        if ctrl > max(8, chars // 50):
            return "data_binary"
        if not content:
            return "data_empty"
        return digest, label, codec, lines
    return "data_decode_error"


def _head_text(p: Path, codec: str) -> str:
    """文件开头一段（到最后一个换行为止）解码后的文字：嗅探 / 预览 / 说明行用。"""
    with p.open("rb") as fh:
        raw = fh.read(LARGE_HEAD_BYTES)
    text = codecs.getincrementaldecoder(codec)(errors="strict").decode(raw, final=False)
    cut = max(text.rfind("\n"), text.rfind("\r"))
    return text[: cut + 1] if cut > 0 and len(raw) >= LARGE_HEAD_BYTES else text


def _header_invalid_on_tail(body):
    """把流式读里 `cut_tail` 抛的 `NamelessTailData` 换成 `DataSourceError("data_header_invalid")`（逐行透传，其余原样）。"""
    try:
        yield from body
    except NamelessTailData:
        raise DataSourceError("data_header_invalid") from None


def _scan_summary(
    header_cells,
    body,
    *,
    path_name: str,
    sha256: str,
    size: int,
    parse,
    delimiter: str | None,
    header_row: int | None,
    header_line: int,
    encoding: str | None,
    units_row: bool,
    path: str | None = None,
    pos=None,
    no_header: bool = False,
    col_tags: dict | None = None,
) -> DataSummary:
    """大表的一遍扫描：`_summarize` 的流式版（同样的列统计、同样的错误码），行不进内存。结构线索用有界集合（`_Facts`）。
    除受控摘要外留下精确的整表统计（行数、每列有限数值的个数 / 最小 / 最大）与前 `LARGE_PREVIEW_ROWS` 行。"""
    header, body = _trim_tail(fill_header([h.strip() for h in header_cells]), body)
    if len(header) > MAX_COLUMNS:
        raise DataSourceError("data_too_wide", str(len(header)))
    if any(not h for h in header) or len(set(header)) != len(header):
        raise DataSourceError("data_header_invalid")
    ncols = len(header)
    per = {h: ColumnSummary(h) for h in header}
    cols = [per[h] for h in header]
    levels = {h: set() for h in header}
    lvls = [levels[h] for h in header]
    stats = {h: ColStat() for h in header}
    sts = [stats[h] for h in header]
    id_cols = [j for j, h in enumerate(header) if _id_name(h)]
    id_sets = {j: set() for j in id_cols}
    id_n = dict.fromkeys(id_cols, 0)
    id_nonint = dict.fromkeys(id_cols, False)
    id_over: set[int] = set()
    seq = {j: _SeqTrack() for j, h in enumerate(header) if _row_index_name(h)}
    first_set: set[str] = set()
    first_ok = True
    count = 0
    preview: list[list[str]] = []
    pnums: list[int] = []
    for n, r in _header_invalid_on_tail(body):
        if len(r) != ncols:
            raise DataSourceError("data_ragged")
        count += 1
        if count > MAX_ROWS:
            raise DataSourceError("data_too_long", str(count))
        if not count & 0xFFFF and pos is not None:  # 每 65536 行：记已读字节、查取消
            progress.tick("scan", pos(), size)
        if count <= LARGE_PREVIEW_ROWS:
            preview.append(r)
            pnums.append(n)
        for j in range(ncols):
            cell = r[j]
            cls, v = parse(cell)
            s = cols[j]
            if cls == "numeric":
                s.numeric += 1
                st = sts[j]
                st.numeric += 1
                if st.lo is None:
                    st.lo = st.hi = v
                elif v < st.lo:
                    st.lo = v
                elif v > st.hi:
                    st.hi = v
            elif cls == "text":
                s.text_count += 1
                if parse_when(cell) is not None:
                    s.dates += 1
                if len(s.text) < TEXT_SAMPLES:
                    s.text.append(cell.strip())
                lv = lvls[j]
                if len(lv) <= MAX_GROUP_LEVELS:
                    lv.add(cell.strip())
            elif cls == "empty":
                s.empty += 1
            else:
                s.nan += 1
        for j, tr in seq.items():
            if tr.ok:
                tr.feed(*parse(r[j]))
        if first_ok:
            lab = r[0].strip()
            if lab in first_set or len(first_set) >= STRUCTURE_SET_CAP:
                first_ok = False
                first_set = set()
            else:
                first_set.add(lab)
        for j in id_cols:
            c = r[j].strip()
            if not c:
                continue
            id_n[j] += 1
            if j not in id_over:
                id_sets[j].add(c)
                if len(id_sets[j]) > STRUCTURE_SET_CAP:
                    id_over.add(j)
            if not id_nonint[j]:
                kind, num = parse(c)
                if kind == "numeric" and not num.is_integer():
                    id_nonint[j] = True
    if count == 0:
        raise DataSourceError("data_empty")
    first = header[0]
    first_labels = (
        first_set
        if first_ok
        and per[first].kind == "text"
        and per[first].nan == 0
        and per[first].text_count == count
        else None
    )
    ids: dict[str, tuple[int, int, bool]] = {}
    capped = set()
    for j in id_cols:
        h = header[j]
        k = per[h]
        if k.kind not in ("text", "numeric") or k.nan:
            continue
        ids[h] = (id_n[j], len(id_sets[j]), not (k.kind == "numeric" and id_nonint[j]))
        if j in id_over:
            capped.add(h)
    layout, groups, id_like, repeats = _structure_from(
        header, per, levels, count, _Facts(first_labels, ids, frozenset(capped))
    )
    return DataSummary(
        path_name=path_name,
        sha256=sha256,
        size=size,
        columns=header,
        rows=[],
        per_column=per,
        layout=layout,
        groups=groups,
        id_like=id_like,
        row_index=[
            h
            for j, h in enumerate(header)
            if j in seq and seq[j].holds() and seq[j].n == count and h not in id_like
        ],
        repeats=repeats,
        row_count=count,
        delimiter=delimiter,
        header_row=header_row,
        header_line=header_line,
        encoding=encoding,
        units_row=units_row,
        no_header=no_header,
        col_tags=dict(col_tags or {}),
        large=LargeInfo(count, stats, preview, pnums, path),
    )


def _old_reader_stream(fh, delimiter):
    """老读法（嗅探不出读法的 `.csv` / `.tsv`）：第一条非空记录是表头，行号按记录序号（含空行；与 v1 一致）。"""
    reader = csv.reader(fh, delimiter=delimiter)
    for n, row in enumerate(reader, start=1):
        if any(c.strip() for c in row):
            yield n, row


#: 大表整列判定的预扫：先只看开头这么多行，没有哪一列还可能符合就不再读第二遍（绝大多数大表没有带 `%` / 小数逗号的列）
TAG_HEAD_ROWS = 2000


def _large_tags(p, codec, delim, script_header, units, no_header, size) -> dict:
    """大表的整列判定 `{列下标: 真 = 百分号 / 假 = 小数逗号}`（判据与小表整份读的 `tag_columns` 是同一个 `ColumnTagger`）：开头 `TAG_HEAD_ROWS` 行里
    没有任何一列还可能符合就是空；否则再整份读一遍（流式，可取消）定下来。表头读不出来 / 尾部没有名字的列有数据等错误这里不管，留给紧接着的扫描按原样报。"""
    try:
        with io.TextIOWrapper(p.open("rb"), encoding=codec, newline="") as fh:
            header, body = iter_table(fh, delim, script_header, units, no_header)
            if not header:
                return {}
            tagger = ColumnTagger(len(header), delim)
            for _n, cells in itertools.islice(body, TAG_HEAD_ROWS):
                tagger.feed(cells)
            # 「开头没有符合的列」不等于「后面不会有」：小数逗号列可以以整数开头（`1`、`2`……到第 2001 行才出现 `1,5`），百分号列可以以空格开头。
            # 只有每一列都已经被开头的某一格否掉（既不可能整列带 %，也不可能整列小数逗号）才省掉第二遍（Codex #2346 P2）
            if not (any(tagger.pct) or any(tagger.dec)):
                return {}
            for count, (_n, cells) in enumerate(body, start=1):
                tagger.feed(cells)
                if not count & 0xFFFF:
                    progress.tick("scan", fh.buffer.tell(), size)
            return tagger.tags()
    except NamelessTailData:
        return {}


def _read_large_text_table(p: Path, *, header_row: int | None, size: int) -> TextTable:
    """大表（超过 `SMALL_BYTES` / `SMALL_ROWS`）的 `read_text_table`：判定与小表同一套（同一个 `_detect`、同一组错误码与「认得准 / 猜的」口径），
    只是流式读。`.xvg` 要整份改写文本，大表不支持（`data_too_large`）。嗅探 / 预览 / 说明行只看开头 `LARGE_HEAD_BYTES`；
    读法里的 `header_row` 在大表里总是显式的行号（不去判断「整份没有 `#` 行」）。"""
    suffix = p.suffix.lower()
    if suffix in XVG_SUFFIXES + JSONL_SUFFIXES:
        return _unreadable("data_too_large")  # 要整份改写文本，大表不支持
    try:
        checked = _stream_check(p)
    except OSError:
        return _unreadable("data_unreadable")
    if isinstance(checked, str):
        return _unreadable(checked)
    sha, encoding, codec, newlines = checked
    if newlines > 2 * MAX_ROWS + 1000:
        return _unreadable("data_too_long")

    def _tt(*a, **kw):
        return TextTable(*a, encoding=encoding, **kw)

    try:
        text = _head_text(p, codec)
        layout = _detect(text, suffix)
    except csv.Error:
        return _unreadable("data_malformed")
    except (OSError, UnicodeDecodeError):
        return _unreadable("data_unreadable")
    if isinstance(layout, RaggedLayout):
        if header_row is None:
            return _tt(
                "unreadable", code="data_ragged", delimiter=layout.delimiter,
                header_line=layout.line, preview=_preview(text, layout.line),
            )  # fmt: skip
        layout = TextLayout(layout.delimiter, layout.line, True)
    if layout is None:
        if suffix not in CSV_SUFFIXES + TSV_SUFFIXES:
            return _unreadable("data_not_table")
        layout = TextLayout("\t" if suffix in TSV_SUFFIXES else ",", 0, True)
    delim = layout.delimiter
    lines = re.split(r"\r\n|\r|\n", text)
    if header_row is not None and not 1 <= header_row <= max(len(lines), newlines + 1):
        return _tt("unreadable", code="data_header_row_invalid", preview=_preview(text, 1))
    old_reader = layout.header_line == 0 and header_row is None
    line = 1 if old_reader else (header_row if header_row is not None else layout.header_line)
    units = bool(not old_reader and layout.units and line == layout.header_line)
    no_header = bool(not old_reader and layout.no_header and line == layout.header_line)
    script_header = None if old_reader else line
    guessed = not old_reader and not layout.sure and header_row is None
    summ = None
    ncols = None
    try:
        with io.TextIOWrapper(p.open("rb"), encoding=codec, newline="") as fh:
            if old_reader:
                body = _old_reader_stream(fh, delim)
                first = next(body, None)
                if first is not None:
                    ncols = len(first[1])
                    summ = _scan_summary(
                        first[1], body, path_name=p.name, sha256=sha, size=size, parse=parse_cell,
                        delimiter=delim, header_row=None, header_line=1, encoding=encoding,
                        units_row=False, path=str(p), pos=fh.buffer.tell,
                    )  # fmt: skip
            else:
                col_tags = _large_tags(p, codec, delim, script_header, units, no_header, size)
                head_cells, body = iter_table(
                    fh, delim, script_header, units, no_header, col_tags or None
                )
                if head_cells:
                    ncols = len(head_cells)
                    summ = _scan_summary(
                        head_cells, body, path_name=p.name, sha256=sha, size=size, parse=parse_cell,
                        delimiter=delim, header_row=script_header, header_line=line,
                        encoding=encoding, units_row=units, no_header=no_header, path=str(p),
                        pos=fh.buffer.tell, col_tags=col_tags,
                    )  # fmt: skip
    except csv.Error:
        return _unreadable("data_malformed")
    except (OSError, UnicodeDecodeError):
        return _unreadable("data_unreadable")
    except DataSourceError as exc:
        if guessed and exc.code == "data_header_invalid":
            return _tt(
                "unsure", delimiter=delim, header_line=line, columns=ncols,
                preview=_preview(text, line), lead=_lead(lines, line),
            )  # fmt: skip
        if header_row is not None:
            return _tt(
                "unreadable", code=exc.code, delimiter=delim, header_line=line,
                preview=_preview(text, line),
            )  # fmt: skip
        return _unreadable(exc.code)
    if summ is None:
        if header_row is not None:
            return _tt(
                "unreadable", code="data_header_row_invalid", delimiter=delim,
                header_line=line, preview=_preview(text, line),
            )  # fmt: skip
        return _unreadable("data_empty")
    return _tt(
        "unsure" if guessed else "ok",
        delimiter=delim,
        header_line=line,
        columns=len(summ.columns),
        rows=summ.row_count,
        preview=_preview(text, line) if guessed else (),
        summary=summ,
        lead=() if old_reader else _lead(lines, line),
    )


def read_text_table(
    path: str | Path,
    *,
    header_row: int | None = None,
    keep_rows: bool = True,
    use_cache: bool = False,
) -> TextTable:
    """读一个文本表格（`.csv` / `.tsv` / `.tab` / `.txt` / `.dat` / `.asc` / `.xvg`）：不超过 `SMALL_BYTES` / `SMALL_ROWS` 的**小表**走原来的整份读法
    （`_read_small_text_table`，摘要带行数据）；更大的**大表**走流式读（`_read_large_text_table`，摘要 `rows == []`、`large` 有整表统计），
    上限 `MAX_BYTES` / `MAX_ROWS`，超出 `data_too_large` / `data_too_long`。**不抛异常**。`use_cache`（只对大表有意义）：同一个文件（路径、字节数、
    mtime、inode）同一个表头行的扫描结果复用，省掉文件夹概览 / 出备选反复整份扫一遍；提交前核对 sha256 的读取**不许**用缓存。"""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return _unreadable("data_unreadable")
    if st.st_size > MAX_BYTES:
        return _unreadable("data_too_large")
    if st.st_size <= SMALL_BYTES:
        out = _read_small_text_table(p, header_row=header_row, keep_rows=keep_rows)
        if not (out.status == "unreadable" and out.code == "data_too_long"):
            return out
    key = (str(p), st.st_size, st.st_mtime_ns, st.st_ino, header_row)
    if use_cache:
        with _LARGE_LOCK:
            hit = _LARGE_CACHE.get(key)
            if hit is not None:
                _LARGE_CACHE[key] = _LARGE_CACHE.pop(key)  # 最近用过的排到最后
                return hit
    out = _read_large_text_table(p, header_row=header_row, size=st.st_size)
    if use_cache:
        with _LARGE_LOCK:
            _LARGE_CACHE[key] = out
            while len(_LARGE_CACHE) > _LARGE_CACHE_MAX:
                _LARGE_CACHE.pop(next(iter(_LARGE_CACHE)))
    return out


def _read_delimited(p: Path, delimiter: str) -> tuple[list[list[str]], str, int]:
    """`(表, 原始字节的 sha256, 字节数)`：UTF-8 / UTF-8 BOM；上限与错误码是 CSV / TSV 共用的一份。"""
    try:
        size = p.stat().st_size
        if size > SMALL_BYTES:
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


def inspect_data_source(
    path: str | Path,
    sheet: str | int | None = None,
    *,
    v2: bool = False,
    header_row: int | None = None,
    allow_large: bool = False,
) -> DataSummary:
    """读一份表的受控摘要。后缀 `.tsv` / `.tab` 按制表符分隔、`.xlsx` 读 `sheet`（表名 / 序号 0 起 / `None` = 第一张可见表）、
    `.xls` / `.xlsm` / `.xlsb` 拒绝（`data_excel_unsupported`）；**其余一律按 CSV 读**（v1 行为不变：后缀的白名单由调用方
    管，如 `ai_data_task` 的 `data_not_csv`）。UTF-8 / UTF-8 BOM。调用方负责路径在项目内（`contained_path`）。

    `allow_large`：认大表（`read_text_table` 的流式读，摘要不带行数据，见 `DataSummary.large`）的调用方才传；不传时大表抛 `data_too_large`。
    `v2=True`（数据出图 v2 的读法，v1 不传）：文本格式走 `read_text_table`（嗅探分隔符、跳过说明行、找表头行，`.txt` / `.dat` / `.asc` 也认；
    `header_row` 是调用方指定的表头物理行号；编码认 UTF-8 / UTF-8 BOM / 有 BOM 的 UTF-16 / GB18030，见 `decode_text`），单元格规则认千分位（`1,000`）；读不了抛 `DataSourceError`（稳定码）。XLSX 的读法：`header_row`（工作表行号）给了就跳过它之前的行，没给时第一条非空行读不成表头才找标题行后面的列名行（`_summarize_book`）；
    同样认千分位的文字格。"""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in LEGACY_EXCEL_SUFFIXES:
        raise DataSourceError("data_excel_unsupported", suffix)
    if suffix in XLSX_SUFFIXES:
        try:
            with xlsxread.XlsxBook(p) as book:
                return _summarize_book(
                    book,
                    sheet,
                    parse=parse_cell if v2 else None,
                    header_row=header_row if v2 else None,
                )
        except xlsxread.XlsxError as exc:
            raise _from_xlsx_error(exc) from None
    if v2:
        info = read_text_table(p, header_row=header_row)
        if info.summary is None:
            raise DataSourceError(info.code or "data_header_invalid")
        if info.summary.large is not None and not allow_large:
            # 大表的摘要没有行数据：不认大表的调用方（合画、v1 之外的老入口）一律拒，绝不拿一张空行的表当成读过了
            raise DataSourceError("data_too_large")
        return info.summary
    table, sha, size = _read_delimited(p, "\t" if suffix in TSV_SUFFIXES else ",")
    return _summarize(
        table,
        path_name=p.name,
        sha256=sha,
        size=size,
        row_numbers=list(range(1, len(table) + 1)),
    )


def _summarize_book(
    book: xlsxread.XlsxBook,
    sheet: str | int | None,
    *,
    keep_rows: bool = True,
    parse=None,
    header_row: int | None = None,
) -> DataSummary:
    """XLSX 一张表的摘要。`header_row`（工作表里的行号，与文本表同一口径）给了 = 它之前的行是说明、不读。v2（`parse` 给了）且没指定行时：第一条非空行
    读不成表头（`data_header_invalid`）就用 `xlsx_header_guess` 找标题行后面的列名行（与文本表猜表头行同一套判据）；读不成就仍报原来的错。
    **真的跳过了行**才把行号记进摘要（`header_row` / `header_line`），其余（常态）两者都是 None，生成的脚本与此前逐字相同。v1（`parse` 没给）逐字不变。"""
    info = book.resolve(sheet)
    table = book.read(info.index)
    numbers = list(getattr(book, "row_numbers", []))
    v2 = parse is not None

    def one(rows, nums, hrow):
        return _summarize(
            rows,
            row_numbers=nums,
            path_name=book.name,
            sha256=book.raw_sha256,
            size=book.size,
            sheet=info.name,
            keep_rows=keep_rows,
            parse=parse,
            header_row=hrow,
            header_line=hrow,
        )

    def skip(first: int):
        pairs = [(n, r) for n, r in zip(numbers, table) if n >= first]
        return [r for _, r in pairs], [n for n, _ in pairs]

    if v2 and header_row is not None and len(numbers) == len(table):
        rows, nums = skip(header_row)
        if not rows:
            raise DataSourceError("data_header_row_invalid")
        return one(rows, nums, nums[0] if len(rows) < len(table) else None)
    try:
        return one(table, numbers, None)
    except DataSourceError as exc:
        if not v2 or exc.code != "data_header_invalid" or len(numbers) != len(table):
            raise
        guess = xlsx_header_guess(table, numbers)
        if guess is None:
            raise
        rows, nums = skip(guess)
        try:
            return one(rows, nums, nums[0])
        except DataSourceError:
            raise exc from None


def _summarize(
    table: list[list[str]],
    *,
    path_name: str,
    sha256: str,
    size: int,
    sheet: str | None = None,
    keep_rows: bool = True,
    row_numbers: list[int] | None = None,
    parse=None,
    delimiter: str | None = None,
    header_row: int | None = None,
    header_line: int | None = None,
    encoding: str | None = None,
    units_row: bool = False,
    no_header: bool = False,
) -> DataSummary:
    #: 单元格规则：默认 v1 的（不认千分位）；v2 传 `parse_cell`
    v2 = parse is not None
    parse = parse or legacy_parse_cell
    date_of = parse_when if v2 else parse_date  # v1 的日期规则逐字不变（只认 ISO 日期）
    numbered = (
        list(zip(row_numbers, table)) if row_numbers and len(row_numbers) == len(table) else None
    )
    if numbered is not None:
        numbered = [(n, r) for n, r in numbered if any(c.strip() for c in r)]
        table = [r for _, r in numbered]
        row_numbers = [n for n, _ in numbered]
    else:
        row_numbers = None
    table = [r for r in table if any(c.strip() for c in r)]  # 纯空行不算数据行
    if not table:
        raise DataSourceError("data_empty")
    header = fill_header([h.strip() for h in table[0]])
    rows = table[1:]
    if v2:  # v1 逐字不变：尾部没有名字的空列只在 v2 的读法里丢
        try:
            header, body = _trim_tail(header, ((0, r) for r in rows))
            rows = [r for _, r in body]
        except NamelessTailData:
            raise DataSourceError("data_header_invalid") from None
    if len(header) > MAX_COLUMNS:
        raise DataSourceError("data_too_wide", str(len(header)))
    if any(not h for h in header) or len(set(header)) != len(header):
        raise DataSourceError("data_header_invalid")
    if len(rows) > SMALL_ROWS:
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
            cls, _ = parse(cell)
            if cls == "text":
                s.text_count += 1
                if date_of(cell) is not None:
                    s.dates += 1
                if len(s.text) < TEXT_SAMPLES:
                    s.text.append(cell.strip())
                lv = levels[h]
                if len(lv) <= MAX_GROUP_LEVELS:
                    lv.add(cell.strip())
            else:
                setattr(s, cls, getattr(s, cls) + 1)
    layout, groups, id_like, repeats = _structure(header, rows, per, levels, parse)
    row_index = row_index_columns(header, rows, parse, id_like)
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
        numcat=numcat_levels(header, rows, per, id_like) if parse is parse_cell else {},
        id_like=id_like,
        row_index=row_index,
        design=design_kinds(header, rows, per, id_like, row_index) if parse is parse_cell else {},
        repeats=repeats,
        row_count=None if keep_rows else len(rows),
        row_numbers=list(row_numbers[1:]) if row_numbers and keep_rows else [],
        delimiter=delimiter,
        header_row=header_row,
        header_line=header_line,
        encoding=encoding,
        units_row=units_row,
        no_header=no_header,
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


#: 行号列的列名闭集（`_row_index_name` 规范化之后：小写、去掉空白 / `_` / `.` / `-` / `:`）：R 的 `rownames`、pandas 的 `Unnamed: 0`、`index`、`No.`、`序号`……
#: `id` / `*_id` 另有 `_id_name`（个体编号线索）管，这里不重复收；只凭名字不够，见 `row_index_columns`。
_ROW_INDEX_NAMES = frozenset(
    {
        "rownames",
        "rowname",
        "rowid",
        "rownumber",
        "rownum",
        "rowno",
        "row",
        "index",
        "idx",
        "no",
        "sn",
        "#",
        "序号",
        "行号",
    }
)
_UNNAMED_INDEX = re.compile(r"unnamed:\s*0(\.\d+)?")


def _row_index_name(name: str) -> bool:
    """列名像行号：规范化后在 `_ROW_INDEX_NAMES` 里，或是 pandas 存出来的 `Unnamed: 0`（`.1` 后缀也算）。"""
    low = name.strip().lower()
    if _UNNAMED_INDEX.fullmatch(low):
        return True
    return re.sub(r"[\s_.\-:]+", "", low) in _ROW_INDEX_NAMES


#: 行号列至少要这么多行才认（两三行的 1, 2 太容易是真的取值）
ROW_INDEX_MIN_ROWS = 3


class _SeqTrack:
    """逐行核「从 0 或 1 起、逐行恰好 +1 的整数」：小表与大表扫描共用的唯一实现（`row_index_columns` 与 `_scan_summary` 各喂一遍）。"""

    __slots__ = ("n", "ok", "start")

    def __init__(self) -> None:
        self.n = 0
        self.ok = True
        self.start = 0.0

    def feed(self, cls: str, v: float) -> None:
        if not self.ok:
            return
        if cls != "numeric" or not float(v).is_integer():
            self.ok = False
            return
        if self.n == 0:
            if v not in (0, 1):
                self.ok = False
                return
            self.start = v
        elif v != self.start + self.n:
            self.ok = False
            return
        self.n += 1

    def holds(self) -> bool:
        return self.ok and self.n >= ROW_INDEX_MIN_ROWS


def row_index_columns(header, rows, parse, id_like) -> list[str]:
    """行号列（保守，**两条同时满足**）：① 列名是 `_row_index_name`；② 每一行（一格不缺、没有文字 / nan）都是整数，第一行是 0 或 1、之后逐行恰好 +1（不是 `id_like` 列，
    至少 `ROW_INDEX_MIN_ROWS` 行）。只凭取值不认：一列 1..n 也可能是真的「第几天」；只凭名字也不认：`index` 可能是别的量。"""
    out: list[str] = []
    for j, h in enumerate(header):
        if h in id_like or not _row_index_name(h):
            continue
        tr = _SeqTrack()
        for r in rows:
            cls, v = parse(r[j])
            tr.feed(cls, v)
            if not tr.ok:
                break
        if tr.holds() and tr.n == len(rows):
            out.append(h)
    return out


#: 列名里「这是误差量」的词 → 它是哪一种（`unknown` = 是误差量、但名字没说是哪种）。判法见 `error_kind`
_ERROR_WORDS = {
    "sd": "sd",
    "stdev": "sd",
    "stddev": "sd",
    "std": "sd",
    "sem": "sem",
    "stderr": "sem",
    "se": "sem",
    "err": "unknown",
    "error": "unknown",
    "ci": "ci",
    "ci95": "ci",
}
#: 整个列名就是这一个词时，单凭名字就算（`sem` / `se` / `std` / `ci` 不在里面：扫描电镜、硒、标准品、别的缩写）
_ERROR_ALONE = frozenset({"sd", "stdev", "stddev", "stderr", "err", "error"})
_ERROR_CJK = (("标准差", "sd"), ("标准误", "sem"), ("置信区间", "ci"), ("误差", "unknown"))
_MEAN_WORDS = frozenset({"mean", "avg", "average"})
#: 中文里「这一列是均值」的词（长的在前：先去掉「平均值」再看「平均」）。只用于给中文误差列找搭档（`error_match`），不凭它认任何角色
_MEAN_CJK = ("平均值", "均值", "平均数", "均数", "平均")
#: 中文列名里词与词之间常见的连接符 / 括号：比对「去掉误差词之后剩下什么」时不算数
_CJK_GLUE = re.compile(r"[\s_\-—·、，,：:()（）\[\]【】的]+")


def _name_tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", _CAMEL.sub(" ", name).lower()) if t]


def error_match(name: str, others: list[str]) -> tuple[str | None, list[str]]:
    """`error_kind` 的判法本体，另外带回「它修饰的是哪几列」：`(种类 | None, 搭档列)`。搭档列 = `others` 里让名字里的误差词成立的那些列
    （去掉误差词后剩下的词与某一列去掉 `mean` / `avg` / `average` 之后的词相同；去掉后什么都不剩时是名字里带 `mean` / `avg` / `average` 的列），
    可能为空（整个名字就是 `sd` / 中文词、而表里没有 `mean` 列）或多个（歧义）。**判「是不是误差列」的口径只有这一处**——误差列配对
    （`ai_data_recipe.paired_error`）读同一个结果，不另写一份名字规则。"""
    kind: str | None = None
    for word, k in _ERROR_CJK:
        if word in name:
            kind = k
            break
    tokens = _name_tokens(name)
    alone = len(tokens) == 1 and tokens[0] in _ERROR_ALONE
    if kind is None and alone:
        kind = _ERROR_WORDS[tokens[0]]
    partners: list[str] = []
    word_kind: str | None = None
    for t in tokens:
        if t in _ERROR_WORDS:
            rest = [x for x in tokens if x != t]
            for other in others:
                ot = _name_tokens(other)
                core = [x for x in ot if x not in _MEAN_WORDS]
                if ((rest and core == rest) or (not rest and _MEAN_WORDS & set(ot))) and (
                    other not in partners
                ):
                    partners.append(other)
                    word_kind = word_kind or _ERROR_WORDS[t]
    if not partners:
        partners = _cjk_partners(name, others)
    return (kind or word_kind), partners


def _cjk_core(name: str, words: tuple[str, ...]) -> str | None:
    """`name` 里含 `words` 之一 → 去掉它（只去第一处）与连接符之后剩下的核心（可能是空串）；不含 = None。"""
    for w in words:
        if w in name:
            return _CJK_GLUE.sub("", name.replace(w, "", 1)).casefold()
    return None


def _cjk_partners(name: str, others: list[str]) -> list[str]:
    """中文误差列的搭档（与英文那条同一个思路，判据只换成中文的词）：`标准差` ↔ `均值`、`产量标准差` ↔ `产量` / `产量均值` / `平均产量`。
    去掉误差词后**什么都不剩**时，搭档是名字里带均值词的列（中文的，或英文的 `mean` / `avg` / `average`）；剩下核心时，搭档是去掉均值词后
    核心相同的列。名字里只有「误差」这种说不清种类的词也照此找搭档（种类仍是 `unknown`）。"""
    rest = _cjk_core(name, tuple(w for w, _ in _ERROR_CJK))
    if rest is None:
        return []
    out: list[str] = []
    for other in others:
        if other == name or _cjk_core(other, tuple(w for w, _ in _ERROR_CJK)) is not None:
            continue
        mean_core = _cjk_core(other, _MEAN_CJK)
        if rest:
            plain = _CJK_GLUE.sub("", other).casefold()
            ok = (mean_core is not None and mean_core == rest) or plain == rest
        else:
            ok = mean_core is not None or bool(_MEAN_WORDS & set(_name_tokens(other)))
        if ok and other not in out:
            out.append(other)
    return out


def error_kind(name: str, others: list[str]) -> str | None:
    """这一列的名字是不是在说「我是误差量」（`sd` / `sem` / `ci` / `unknown`；不是 = None）。只看列名的**词**（驼峰与非字母数字处切开，
    不做子串匹配：`residue`、`used`、`semester` 不算），宁可认不出、不可认错（认错 = 把一列真的测量值悄悄踢出候选）：

    * 含中文词（标准差 / 标准误 / 置信区间 / 误差）→ 算；
    * 整个名字就是一个词，且在 `sd` / `stdev` / `stddev` / `stderr` / `err` / `error` 里 → 算；
    * 其它含误差词的名字（`mass_sd`、`SEM_depth`、单独的 `sem` / `se` / `std` / `ci`）→ 只有表里另有**它修饰的那一列**时才算：
      去掉这个词后剩下的词与某一列去掉 `mean` / `avg` / `average` 之后的词相同（`mass_sd` ↔ `mass`、`response_sem` ↔ `mean_response`）；
      去掉后什么都不剩时，要有一列的名字里带 `mean` / `avg` / `average`（`mean, sem`）。`SEM_depth_nm`（扫描电镜测的深度）、`Se`（硒）
      没有这样的搭档，不算。

    这只是名字上的线索：它让规则**不把这一列当测量值去画**，不替用户断定误差线的含义（名字只说 `err` 的记 `unknown`，绝不当成 SD）。"""
    return error_match(name, others)[0]


def _structure(
    header: list[str],
    rows: list[list[str]],
    per: dict[str, ColumnSummary],
    levels: dict[str, set[str]],
    parse=None,
    facts=None,
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
    parse = parse or legacy_parse_cell
    n = len(rows)
    if facts is None:
        facts = _row_facts(header, rows, per, parse)
    return _structure_from(header, per, levels, n, facts)


@dataclass
class _Facts:
    """`_structure` 要逐行才能知道的两类事实（小表从行数据算，大表扫描时边读边累计）：
    `first_labels`：第一列是否「每行都是文字、互不相同」——是则给出标签集合（大表超过 `STRUCTURE_SET_CAP` 个不同取值就不再收，按「不是」处理）；
    `ids[列名]`：名字像编号的列的 `(非空格子数, 不同取值数, 数值是否全是整数)`（大表不同取值超过上限时 `capped`：不同取值数按上限算，
    **不据此断言「有重复」**——拿不准的 `repeats` 不报 True）。"""

    first_labels: set[str] | None
    ids: dict[str, tuple[int, int, bool]]
    capped: frozenset[str] = frozenset()


def _row_facts(header, rows, per, parse) -> _Facts:
    n = len(rows)
    first_labels: set[str] | None = None
    first = header[0]
    if per[first].kind == "text" and per[first].nan == 0 and per[first].text_count == n:
        labs = [r[0].strip() for r in rows]
        if len(set(labs)) == n:
            first_labels = set(labs)
    col_of = {h: i for i, h in enumerate(header)}
    ids: dict[str, tuple[int, int, bool]] = {}
    for h in header:
        s = per[h]
        if not _id_name(h) or s.kind not in ("text", "numeric") or s.nan:
            continue
        vals = [r[col_of[h]].strip() for r in rows]
        vals = [v for v in vals if v]
        all_int = not (s.kind == "numeric" and not all(parse(v)[1].is_integer() for v in vals))
        ids[h] = (len(vals), len(set(vals)), all_int)
    return _Facts(first_labels, ids)


def _structure_from(header, per, levels, n, facts: _Facts):
    groups: dict[str, int] = {}
    for h in header:
        s = per[h]
        k = len(levels[h])
        if s.kind == "text" and s.nan == 0 and 2 <= k <= MAX_GROUP_LEVELS and s.text_count > k:
            groups[h] = k
    id_like: list[str] = []
    repeats: bool | None = None
    for h in header:
        if h not in facts.ids:
            continue
        nonempty, distinct, all_int = facts.ids[h]
        if not all_int or distinct < 2:
            continue
        id_like.append(h)
        repeats = bool(repeats) or (nonempty > distinct and h not in facts.capped)
    kinds = {h: per[h].kind for h in header}
    numeric = [h for h in header if kinds[h] == "numeric"]
    layout = "unknown"
    if "mixed" not in kinds.values():
        first = header[0]
        first_labels = facts.first_labels
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


#: 一个工作簿最多逐表数行列的工作表个数（超出的只给名字，数字是 None）
SHEET_COUNT_MAX = 20


def xlsx_sheets(
    path: str | Path, *, counts: bool = False, max_counted: int | None = None
) -> list[dict] | None:
    """一个 XLSX 的工作表清单（工作簿里的顺序，隐藏表也列）；读不了回 None。每项 `{name, hidden, rows, columns, status, code}`。

    `counts=False`（默认）：只读工作簿目录、不读单元格，`rows` / `columns` / `status` / `code` 都是 None（= 没读）。
    `counts=True`：前 `max_counted`（缺省 `SHEET_COUNT_MAX`）张表各读一次，口径与整文件夹摘要（`profile_folder`）和 `propose` 读到的那张表同一份
    （`_summarize_book` + `parse_cell`）：`rows` 是数据行数（不含表头、全空行不算）、`columns` 是列数、`status` 是 `ok` / `unreadable`
    （`code` 是稳定错误码，此时行列数是 None）；超出 `max_counted` 的表四项仍是 None。
    `/api/ai/data-files` 给每个 XLSX 带上它。"""
    limit = SHEET_COUNT_MAX if max_counted is None else max_counted
    try:
        with xlsxread.XlsxBook(Path(path)) as book:
            out = []
            for n, info in enumerate(book.sheets):
                item = {
                    "name": info.name,
                    "hidden": bool(info.hidden),
                    "rows": None,
                    "columns": None,
                    "status": None,
                    "code": None,
                }
                if counts and n < limit:
                    try:
                        summ = _summarize_book(book, info.index, keep_rows=False, parse=parse_cell)
                        item.update(rows=summ.row_count, columns=len(summ.columns), status="ok")
                    except (DataSourceError, xlsxread.XlsxError) as exc:
                        item.update(status="unreadable", code=exc.code)
                    except Exception:  # noqa: BLE001 —— 单张表的任何意外只是这一张读不了
                        item.update(status="unreadable", code="data_unreadable")
                out.append(item)
            return out
    except Exception:  # noqa: BLE001 —— 单个文件读不了只是不带清单，不拖垮列表
        return None


# --------------------------------------------------------------------------- 整文件夹摘要
#: 一次最多摘要多少张表（XLSX 每个工作表算一张）/ 最多记多少条跳过 / 最多看多少个目录项；超了标 `truncated`，不静默截断
MAX_PROFILE_TABLES = 20
MAX_PROFILE_SKIPPED = 50
#: 一次文件夹摘要里**大表**（> `SMALL_BYTES`）最多整份扫描多少字节（每份约 5 s / 100 MB）：超出的大表进 `skipped`（`data_too_large`，「这一轮没读」），
#: 用户显式选中的表不受限（`ai_data_propose.with_explicit_table`）。已缓存的扫描不另算时间，但仍计入，保持结果与缓存状态无关。
PROFILE_LARGE_BYTES = 1536 * 1024 * 1024
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


def profile_folder(root: str | Path, header_rows: dict[str, int] | None = None) -> FolderProfile:
    """一个文件夹里全部表（`.csv` / `.tsv` / `.tab` / `.xlsx`，后缀大小写不敏感）的受控摘要。

    `header_rows`：`{相对路径: 表头物理行号}`，调用方按文件指定「每列名字在第几行」；键只和遍历结果逐字比对（不拼路径）。
    文本格式（含 `.txt` / `.dat` / `.asc`）一律经 `read_text_table`；单元格规则认千分位。

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

    large_read = 0
    for rel, _size in found:
        if len(tables) >= MAX_PROFILE_TABLES:
            reason = "tables"
            break
        p = base / rel
        suffix = p.suffix.lower()
        if suffix not in XLSX_SUFFIXES + LEGACY_EXCEL_SUFFIXES and _size > SMALL_BYTES:
            large_read += _size
            if large_read > PROFILE_LARGE_BYTES:
                if not skip(rel, None, "data_too_large"):
                    break
                continue
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
                        summ = _summarize_book(book, info.index, keep_rows=False, parse=parse_cell)
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
            info = read_text_table(
                p, header_row=(header_rows or {}).get(rel), keep_rows=False, use_cache=True
            )
        except Exception:  # noqa: BLE001 - 单个文件的任何意外都不该拖垮整个文件夹
            info = TextTable("unreadable", code="data_unreadable")
        if info.summary is None:
            if info.code == "data_not_table":
                continue  # README / 清单这类散文不是数据，不当成「读不了的数据」占名额
            if not skip(rel, None, info.code or "data_header_invalid"):
                break
            continue
        tables.append(TableEntry(rel, None, False, info.summary))
    if reason is None and scan_truncated:
        reason = "scan"
    return FolderProfile(tuple(tables), tuple(skipped), reason is not None, reason)


# --------------------------------------------------------------------------- 数值列里夹着的文字格：读表时套的规则
def apply_cells(header, rows, rules):
    """数值列里夹着的文字格（`<0.1`、`n/a`、`12.5*`）按用户选定的规则处理，**只改这次读到的内存里的副本，不碰文件**。
    **这是运行时函数**：父进程算期望值与生成的脚本调用同一份源码（`inspect.getsource` 内嵌进公共模块）。

    `rules`：`[(列名, 方式, 映射, 其余)]`。方式 `skip`：文字格当缺失（该行这一格不画、不计 n，折线在那里断开，同空格）；
    `extract`：取格子里**第一个**数（`12.5*` → 12.5、`<0.5` → 0.5、`3.2 mg` → 3.2、`<1,000` → 1000，千分位规则同 `parse_cell`；
    `1,2` / `1,000,5` 这类不是千分位写法的，仍只取逗号前那个数 1），取不出就当缺失；`map`：原文（去首尾空白后精确匹配）
    → 数或 None（缺失），不在映射里的按「其余」（`skip` / `extract`）。非文字格（数、空、NaN）一律原样。
    返回 `(新行, 说明, 统计)`：说明是图下方写的几句英文（每列一句，没有可说的不写），统计是 `[{列, 方式, 忽略, 取数, 映射}]`。"""
    idx = {h: i for i, h in enumerate(header)}
    out = [list(r) for r in rows]
    notes = []
    stats = []
    for col, mode, mapping, rest in rules:
        i = idx[col]
        ignored = 0
        extracted = 0
        mapped = {}
        for r in out:
            if i >= len(r) or parse_cell(r[i])[0] != "text":
                continue
            text = r[i].strip()
            if mode == "map" and text in mapping:
                v = mapping[text]
                r[i] = "" if v is None else repr(float(v))
                mapped[text] = mapped.get(text, 0) + 1
                continue
            how = rest if mode == "map" else mode
            m = (
                re.search(
                    r"[-+]?(?:[1-9]\d{0,2}(?:,\d{3})+(?:\.\d+)?(?![\d,])|\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?",
                    text,
                )
                if how == "extract"
                else None
            )
            if m is not None:
                r[i] = repr(float(m.group(0).replace(",", "")))
                extracted += 1
            else:
                r[i] = ""
                ignored += 1
        stats.append(
            {
                "column": col,
                "mode": mode,
                "ignored": ignored,
                "extracted": extracted,
                "mapped": mapped,
            }
        )
        parts = []
        for text, n in mapped.items():
            v = mapping[text]
            parts.append(f"{text} = {v:g}" if v is not None else f"{text} treated as missing")
        if extracted:
            parts.append(f"number taken from {extracted} text cell{'s' if extracted != 1 else ''}")
        if ignored:
            parts.append(f"{ignored} non-numeric cell{'s' if ignored != 1 else ''} ignored")
        if parts:
            notes.append(f"{col}: " + ", ".join(parts))
    return out, notes, stats


# --------------------------------------------------------------------------- 整数编码的类别列
def numcat_levels(header, rows, per, id_like) -> dict[str, int]:
    """整数编码、可当类别用的列 → 不同取值个数（**客观条件的唯一实现**：摘要、配方校验、列角色挑选、模型采纳都读 `DataSummary.numcat`）。

    条件（全部满足才算）：列 kind 是 `numeric`（有数、没有文字格；没有 `nan`；日期 / 混合列不算）、非空值**全是整数**（`3.0` 算、`1.5` 不算；
    负数可以：`-1 / 0 / 1` 的编码是常见的）、不同取值（按整数值，`1` / `1.0` / `01` 同一个）满足 `2 <= k <= NUMCAT_MAX_LEVELS`、
    非空格子数 > `k`（至少一个取值重复出现——每行都不同的是编号 / 测量值，不是类别）、不是 `id_like` 列（名字 / 结构像编号的整数列——批次、样本号——
    与文字编号一样不当分组）。空格子不算取值，那一行画图时跳过（同文字分组的空格子）。只看小表的行数据；大表没有行数据，得到空。"""
    out: dict[str, int] = {}
    if not rows:
        return out
    for j, h in enumerate(header):
        s = per[h]
        if s.kind != "numeric" or s.nan or h in id_like:
            continue
        seen: set[int] = set()
        n = 0
        ok = True
        for r in rows:
            cell = r[j]
            if not cell.strip():
                continue
            kind, v = parse_cell(cell)
            if kind != "numeric" or not v.is_integer() or abs(v) >= 1e15:
                ok = False
                break
            seen.add(int(v))
            n += 1
        if ok and 2 <= len(seen) <= NUMCAT_MAX_LEVELS and n > len(seen):
            out[h] = len(seen)
    return out


# --------------------------------------------------------------------------- 数值列：像条件 / 顺序，还是像测量
#: `levels`：不同取值个数的上限（剂量 / 时间点这样落在离散网格上的取值；再多更像测量值）、每个取值至少出现几次，以及平均每个取值至少对应几行
#: （`取值数 * DESIGN_LEVEL_AVG_REPEATS <= 行数`：连续测量值偶尔有重复，不会每个取值都反复出现）
DESIGN_MAX_LEVELS = 50
DESIGN_LEVEL_MIN_REPEATS = 2
DESIGN_LEVEL_AVG_REPEATS = 4
#: 缺格（空 / `nan`）占整列的比例超过它就不判（与 `numeric_like` 的文字格阈值同一个数）
DESIGN_MAX_MISSING = 0.2
#: `block`：够长的块（至少这么多行）里的行要占整列的这个比例以上；短块在随机数据里到处都是，个别个体少测一两次（ChickWeight 里有的小鸡只有 2 行）不该让整列失格
DESIGN_BLOCK_MIN_LEN = 3
DESIGN_BLOCK_LONG_SHARE = 0.9
#: `block`：够长的块里至少这么多个不同取值（块内按行不减；同一个时间点可以有几个重复测量）
DESIGN_BLOCK_MIN_DISTINCT = 3
#: `block`：够长的块里，从同一个值起头的块要占这个比例以上（每个个体都从时间 0 起；一个测量值（体重）逐个体随时间增长，起点各不相同）
DESIGN_BLOCK_SAME_START = 0.8


def _same_start(blocks: list[list[float]]) -> bool:
    starts: dict[float, int] = {}
    for b in blocks:
        starts[b[0]] = starts.get(b[0], 0) + 1
    return max(starts.values()) >= DESIGN_BLOCK_SAME_START * len(blocks)


def design_kind(values: list[float]) -> str | None:
    """一列数值（按行序、全部有限）的取值结构 → `block` / `monotone_levels` / `levels` / None（像测量）。**只看取值，不看列名**：

    * `block`：把整列按「下降处」切成块（相邻两行后一个更小就另起一块，所以块内按行不减、允许同一个取值重复几行）：至少 2 块、够长的块（`DESIGN_BLOCK_MIN_LEN` 行以上）里的行占整列
      `DESIGN_BLOCK_LONG_SHARE` 以上、每个够长的块至少 `DESIGN_BLOCK_MIN_DISTINCT` 个不同取值、**同一个起点值**的块占够长的块的 `DESIGN_BLOCK_SAME_START` 以上
      （个体各自从时间 0 开始；测量值随时间增长，起点各不相同）、整列不同取值个数 < 行数（块与块之间取值重复出现——各个个体的同一套时间点）。
    * `levels`：不同取值 2–`DESIGN_MAX_LEVELS` 个、平均每个取值至少对应 `DESIGN_LEVEL_AVG_REPEATS` 行、每个取值至少出现 `DESIGN_LEVEL_MIN_REPEATS` 次（几个剂量 / 时间点各做若干次）。
    * `monotone_levels`：满足 `levels` 且整列按行不减（按组排好序的编号 / 剂量）。

    **没有「整列单调就是条件」这一条**（2026-11-27 撤掉）：递增的测量值（累积量、按产率排过序的读数）与真正的递增横轴、递减的真横轴（红外波数）与随剂量下降的响应，
    单列上结构相同、分不开，宁可不判。"""
    n = len(values)
    if n < 2 * DESIGN_BLOCK_MIN_LEN:
        return None
    distinct = set(values)
    levels_ok = False
    if 2 <= len(distinct) <= DESIGN_MAX_LEVELS and len(distinct) * DESIGN_LEVEL_AVG_REPEATS <= n:
        counts: dict[float, int] = {}
        for v in values:
            counts[v] = counts.get(v, 0) + 1
        levels_ok = min(counts.values()) >= DESIGN_LEVEL_MIN_REPEATS
    kind = _ordered_kind(values)
    if kind == "block":
        return kind
    if kind == "monotone" and levels_ok:
        return "monotone_levels"  # 按组排好序的编号 / 剂量（每个取值连着出现好几行）
    return "levels" if levels_ok else None


def _ordered_kind(values: list[float]) -> str | None:
    """`design_kind` 里「按行递增」的两种（`block` / `monotone`）。"""
    n = len(values)
    distinct = set(values)
    cuts = [i for i in range(1, n) if values[i] < values[i - 1]]
    if not cuts:
        return "monotone" if len(distinct) >= 3 else None
    edges = [0, *cuts, n]
    blocks = [values[a:b] for a, b in zip(edges, edges[1:], strict=False)]
    long_blocks = [b for b in blocks if len(b) >= DESIGN_BLOCK_MIN_LEN]
    if (
        len(blocks) >= 2
        and len(distinct) < n
        and sum(len(b) for b in long_blocks) >= DESIGN_BLOCK_LONG_SHARE * n
        and all(len(set(b)) >= DESIGN_BLOCK_MIN_DISTINCT for b in long_blocks)
        and _same_start(long_blocks)
    ):
        return "block"
    return None


def design_kinds(header, rows, per, id_like, row_index) -> dict[str, str]:
    """每个「纯数值、不是编号 / 行号」的列的 `design_kind`（只收有结果的）。缺格 / `nan` 不参与（缺的那几行跳过，按剩下的行序判），缺得多（超过 `DESIGN_MAX_MISSING`）的列不判
    ——横轴上缺一格不该让整列失去「像条件」的资格（那一行本来就画不了）。只用小表 v2 的行数据；没有行数据得到空。"""
    out: dict[str, str] = {}
    if not rows:
        return out
    for j, h in enumerate(header):
        s = per[h]
        if s.kind != "numeric" or h in id_like or h in row_index:
            continue
        if (s.empty + s.nan) > DESIGN_MAX_MISSING * len(rows):
            continue
        vals = [v for kind, v in (parse_cell(r[j]) for r in rows) if kind == "numeric"]
        k = design_kind(vals)
        if k is not None:
            out[h] = k
    return out


class NumCat(str):
    """整数编码的类别格子（`apply_numcat` 打的标记）：内容是**规范整数写法**（`0`、`1`、`-1`，没有 `0.0` / `01` / 空白），`number` 是它的数值。
    运行时的 `distinct` 看见一列全是它，就按数值升序排类别（而不是首次出现的顺序）。`strip` 返回自己——取数处都写 `cell.strip()`，标记要留住。
    **运行时类**（进公共模块）。"""

    def __new__(cls, text, number):
        obj = super().__new__(cls, text)
        obj.number = number
        return obj

    def strip(self, chars=None):
        return self


def apply_numcat(header, rows, cols):
    """整数编码的类别列读表后的处理：这些列的每个数值格换成 `NumCat`（规范整数写法 + 数值）；空格 / 不是数的格原样（那一行当缺失，同文字分组）。
    只改这次读到的内存里的副本，不碰文件。**这是运行时函数**：父进程算期望值与生成的脚本调用同一份源码。"""
    idx = {h: i for i, h in enumerate(header)}
    out = [list(r) for r in rows]
    for col in cols:
        i = idx[col]
        for r in out:
            kind, v = parse_cell(r[i])
            if kind == "numeric":
                r[i] = NumCat(str(int(v)) if v.is_integer() else r[i].strip(), v)
    return out


def apply_stack(header, rows, cols, value_name, cat_name):
    """「把这几列当分组」读表后的转换（宽表 → 长表，只在内存里的副本上做，不碰文件）：返回 `([value_name, cat_name], 行)`——一列「值」、一列「类别」（= 原来的列名原文）。
    **按列逐列**取（先 `cols[0]` 的全部、再 `cols[1]` 的……），所以类别的首次出现顺序恒等于 `cols` 的顺序，与哪一行先有值无关；只取数值格（空格子 / `nan` 不是观测，
    那一格不产生行），值保留去首尾空白的原文（之后与别的路径一样由 `parse_cell` 解析）。其余列不进结果。**这是运行时函数**：父进程算期望值与生成的脚本调用同一份源码。"""
    idx = {h: i for i, h in enumerate(header)}
    out = []
    for col in cols:
        i = idx[col]
        for r in rows:
            if parse_cell(r[i])[0] == "numeric":
                out.append([r[i].strip(), col])
    return [value_name, cat_name], out


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
                cls, xv = legacy_parse_cell(row[xi])
                if cls != "numeric":
                    xv = None
            if xv is None:
                not_plotted.append({"row": n, "column": mapping.x, "reason": "x_not_a_value"})
                continue
            xs.append(xv)
            for y in mapping.ys:
                cls, v = legacy_parse_cell(row[summary.columns.index(y)])
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
            cls, v = legacy_parse_cell(row[yi])
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
