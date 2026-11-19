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
import unicodedata
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


def parse_cell(s: str) -> tuple[str, float | None]:
    """`(类别, 值)`：`empty` / `nan`（缺失，值为 NaN）、`numeric`（有限浮点）、`text`（不是数，值为 None）。

    **千分位**（v2）：`1,000` / `-12,345,678.9` 认成数。规则只有一条——整格（去首尾空白）是「首组 1–3 位且不以 0 开头，
    后面每组恰好 3 位」的逗号分组整数，可跟小数点与小数：`1,000` → 1000、`1,000.5` → 1000.5；**不认**的有：`1,2` / `0,5` /
    `12,75`（欧式小数，各组不是 3 位或首组是 0）、`1,000,5` / `1,00` / `1,0000`（乱格式）、`,100`、`1,000,`。
    三位小数的欧式写法（`1,234` = 1.234）与千分位无法区分：**分号分隔的表**里带逗号的格子（`SemicolonCell`）一律不按千分位认、
    也不猜成小数，整格是文字（于是该列是「混了文字」或「没有数值」，由用户决定）；逗号 / 制表 / 空白分隔的表按千分位读。
    同一列里出现过不符合千分位写法的逗号数（`1,5`）时，那一列本来就是混列、先问用户，`1,234` 不会被悄悄认成数。
    **这是运行时函数**：父进程与生成的脚本共用同一份源码；v1 的摘要走 `legacy_parse_cell`（不认千分位，v1 逐字不变）。"""
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
    #: 文本表格实际用的编码（`TEXT_ENCODINGS`；父进程定下、脚本照抄 codec 名，不自己探测）；XLSX / v1 是 None
    encoding: str | None = None
    #: 合画（几份列名相同的表拼成一张）时与 `rows` 逐项对应的「这一行来自哪一份」（该份的分组值）；单份 = 空。`row_numbers` 是各份自己的文件行号
    row_sources: list[str] = field(default_factory=list)

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
TEXT_SUFFIXES = (".txt", ".dat", ".asc")
#: GROMACS / Grace 的 `.xvg`：`#` 注释、`@` 指令、空白分隔的数值列；没有表头行，列名在指令里（`xvg_text` 把它改写成带表头行的制表符文本再走同一条读表流水）
XVG_SUFFIXES = (".xvg",)
XLSX_SUFFIXES = (".xlsx",)
TABLE_SUFFIXES = CSV_SUFFIXES + TSV_SUFFIXES + TEXT_SUFFIXES + XVG_SUFFIXES + XLSX_SUFFIXES
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
def delimited_records(text, delimiter):
    """全文 → `[(起始物理行号, 单元格列表)]`；全空记录（空行、只有分隔符的行）丢掉。`ws` = 每行按连续空白切；其余按 `csv` 规则。
    **这是运行时函数**（生成的脚本里也是它）。"""
    out = []
    if delimiter == "ws":
        for n, line in enumerate(re.split(r"\r\n|\r|\n", text), start=1):
            cells = line.split()
            if cells:
                out.append((n, cells))
        return out
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    prev = 0
    for row in reader:
        if any(c.strip() for c in row):
            out.append((prev + 1, row))
        prev = reader.line_num
    return out


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


def table_from_text(text, delimiter, header_row=None, units_row=False):
    """`(表头, 数据行, 数据行的物理行号)`。`header_row` 为 None：第一条非空记录是表头，其余都是数据（老读法，干净的 CSV / TSV 与它逐字相同）。
    给了行号：从该行起（第一条起始行号 ≥ 它的记录）是表头，之前的行都是说明、不读；表头之后以 `#` 开头**且列数与表头不同**的行是
    注释、丢掉（列数相同的 `#` 行是数据）。`units_row` 真：表头之后的第一条记录是单位行，不是数据，切掉（嗅探认出来才由调用方给；
    切掉后数据行的物理行号不变）。**运行时函数**：父进程与生成的脚本读出来的必然是同一张表。"""
    records = delimited_records(text, delimiter)
    if header_row is not None:
        records = [r for r in records if r[0] >= header_row]
    if not records:
        return [], [], []
    first_line, first = records[0]
    rest = records[1:]
    if delimiter == ";":
        rest = [(n, [SemicolonCell(c) if "," in c else c for c in cells]) for n, cells in rest]
    if units_row:
        rest = rest[1:]
    if header_row is None:
        return [h.strip() for h in first], [c for _, c in rest], [n for n, _ in rest]
    probe = next((c for _, c in rest if not c[0].lstrip().startswith("#")), None)
    if probe is None and rest:
        probe = rest[0][1]
    raw = re.split(r"\r\n|\r|\n", text)[first_line - 1] if delimiter == "ws" else ""
    header = resolve_header(first, raw, delimiter, len(probe) if probe is not None else len(first))
    body = [(n, c) for n, c in rest if len(c) == len(header) or not c[0].lstrip().startswith("#")]
    return header, [c for _, c in body], [n for n, _ in body]


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
        k = 1
        if numeric:
            k = next(
                (i for i, (_n, r) in enumerate(block) if any(_numlike(r[j]) for j in numeric)),
                0,
            )
        if k >= 1:
            cands = [r for r in block[:k] if not _is_hash(r[1])] or block[
                :k
            ]  # 与表头同列数的 `#` 行是注释
            # 默认取第一条像名字的（老读法）。例外：候选里有**单位行**（`nm  a.u.`）时，列名是紧挨在它前面的那一行——更前面同列数的说明行
            # （`operator: lab`）也「像名字」，不能因它排在前面被选中。没有单位行时不往后挪：`group,value` 后面紧跟一条夹着文字标记的观测
            # （`control,<0.1`）也「像名字」，那是数据不是表头。
            u = next((i for i, r in enumerate(cands) if _is_units_row(r[1], width)), None)
            if u is not None and u >= 1 and _name_like(cands[u - 1][1]):
                pick = cands[u - 1]
            else:
                pick = next((r for r in cands if _name_like(r[1])), cands[0])
            return TextLayout(d, pick[0], len(cands) == 1, _units_follow(block, pick, k, width))
        first_line = block[0][0]
        prev = next((r for r in reversed(recs) if r[0] < first_line), None)
        if prev is not None:
            raw = re.split(r"\r\n|\r|\n", sample)[prev[0] - 1] if d == "ws" else ""
            hdr = resolve_header(prev[1], raw, d, width)
            if (_is_hash(prev[1]) or d == "ws") and len(hdr) == width and all(hdr):
                return TextLayout(d, prev[0], True)
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
        return {
            "status": self.status,
            "code": self.code,
            "delimiter": DELIMITER_NAMES.get(self.delimiter) if self.delimiter else None,
            "encoding": self.encoding,
            "header_row": self.header_line,
            "columns": self.columns,
            "rows": self.rows,
            "preview": [{"line": n, "text": t} for n, t in self.preview],
        }


#: 文本表格认的编码（稳定名，给界面显示；不外露 Python 的 codec 名）。判定规则只有这一条、确定且可解释：
#: 先看 BOM（UTF-8 BOM / UTF-16 LE·BE），没有 BOM 就严格按 UTF-8 解，失败再严格按 GB18030 解（覆盖 GBK / GB2312），都失败 = 读不了。
#: 不探测、不打分、不引第三方库；**不加** Big5 / Shift-JIS / Latin-1 / Windows-1252：它们的字节大多也是合法的 GB18030，
#: 先后顺序只会把一种编码的文字悄悄解成另一种的乱码（Latin-1 更是什么字节都解得开），科研数据被静默读错比说「读不了」严重。
TEXT_ENCODINGS = ("utf-8", "utf-8-bom", "utf-16le", "utf-16be", "gb18030")
#: 生成的脚本里 `.decode(...)` 用的 codec 名；UTF-8 / UTF-8 BOM 不在表里（脚本沿用 `read_delimited` 的 `utf-8-sig`，逐字不变）
_SCRIPT_CODECS = {"utf-16le": "utf-16", "utf-16be": "utf-16", "gb18030": "gb18030"}


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


def read_text_table(
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
        if size > MAX_BYTES:
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
    if text.count("\n") + text.count("\r") > 2 * MAX_ROWS + 1000:
        return _unreadable("data_too_long")
    sha = hashlib.sha256(raw).hexdigest()
    try:
        layout = _detect(text, suffix)
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
    try:
        header, rows, row_lines = (
            table_from_text(text, delim, script_header, True)
            if units
            else table_from_text(text, delim, script_header)
        )
    except csv.Error:
        return _unreadable("data_malformed")
    if not header:
        if header_row is not None:
            return _tt(
                "unreadable", code="data_header_row_invalid", delimiter=delim,
                header_line=line, preview=_preview(text, line),
            )  # fmt: skip
        return _unreadable("data_empty")
    guessed = not layout.sure and header_row is None
    try:
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


def inspect_data_source(
    path: str | Path,
    sheet: str | int | None = None,
    *,
    v2: bool = False,
    header_row: int | None = None,
) -> DataSummary:
    """读一份表的受控摘要。后缀 `.tsv` / `.tab` 按制表符分隔、`.xlsx` 读 `sheet`（表名 / 序号 0 起 / `None` = 第一张可见表）、
    `.xls` / `.xlsm` / `.xlsb` 拒绝（`data_excel_unsupported`）；**其余一律按 CSV 读**（v1 行为不变：后缀的白名单由调用方
    管，如 `ai_data_task` 的 `data_not_csv`）。UTF-8 / UTF-8 BOM。调用方负责路径在项目内（`contained_path`）。

    `v2=True`（数据出图 v2 的读法，v1 不传）：文本格式走 `read_text_table`（嗅探分隔符、跳过说明行、找表头行，`.txt` / `.dat` / `.asc` 也认；
    `header_row` 是调用方指定的表头物理行号；编码认 UTF-8 / UTF-8 BOM / 有 BOM 的 UTF-16 / GB18030，见 `decode_text`），单元格规则认千分位（`1,000`）；读不了抛 `DataSourceError`（稳定码）。XLSX 的读法不变，
    只是同样认千分位的文字格。"""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in LEGACY_EXCEL_SUFFIXES:
        raise DataSourceError("data_excel_unsupported", suffix)
    if suffix in XLSX_SUFFIXES:
        try:
            with xlsxread.XlsxBook(p) as book:
                return _summarize_book(book, sheet, parse=parse_cell if v2 else None)
        except xlsxread.XlsxError as exc:
            raise _from_xlsx_error(exc) from None
    if v2:
        info = read_text_table(p, header_row=header_row)
        if info.summary is None:
            raise DataSourceError(info.code or "data_header_invalid")
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
    book: xlsxread.XlsxBook, sheet: str | int | None, *, keep_rows: bool = True, parse=None
) -> DataSummary:
    info = book.resolve(sheet)
    table = book.read(info.index)
    return _summarize(
        table,
        row_numbers=list(getattr(book, "row_numbers", [])),
        path_name=book.name,
        sha256=book.raw_sha256,
        size=book.size,
        sheet=info.name,
        keep_rows=keep_rows,
        parse=parse,
    )


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
) -> DataSummary:
    #: 单元格规则：默认 v1 的（不认千分位）；v2 传 `parse_cell`
    parse = parse or legacy_parse_cell
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
            cls, _ = parse(cell)
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
    layout, groups, id_like, repeats = _structure(header, rows, per, levels, parse)
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
        row_numbers=list(row_numbers[1:]) if row_numbers and keep_rows else [],
        delimiter=delimiter,
        header_row=header_row,
        header_line=header_line,
        encoding=encoding,
        units_row=units_row,
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
    parse=None,
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
        if s.kind == "numeric" and not all(parse(v)[1].is_integer() for v in vals):
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
            info = read_text_table(p, header_row=(header_rows or {}).get(rel), keep_rows=False)
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
