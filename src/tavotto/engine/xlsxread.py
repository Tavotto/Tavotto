"""XLSX 只读取值（TAF 数据出图 v2）：只用 `zipfile` + `xml.etree`，Flask 父进程 import，纯标准库。

**不执行**任何东西、不写任何文件、不把压缩包里的东西解到磁盘（成员只在内存里按名字读）。输出是「格子里写的是什么」的
字符串表，喂进与 CSV 同一套 `ai_data_figure.parse_cell` 规则，不另造一套数值语义。

取值规则（一处文字定义，下面实现）：

* 共享字符串 / 内联字符串 / 公式字符串 → 文字（富文本取各段拼接，忽略拼音注音）；
* 数值 → 文件里写的原文（`0.1` 就是 `0.1`，不重新格式化）；
* 布尔 → `TRUE` / `FALSE`（文字，不是数）；错误值（`#N/A` 等）→ 原样文字；
* 公式取**缓存值**（Excel 上次保存时算出的），没有缓存值 = 空格子；不重算任何公式；
* 日期：数值格且「数字格式含年或日」（内置日期格式号，或自定义格式去掉引号 / 方括号 / 转义后含 `y` 或 `d`；`[h]` `[m]` `[s]`
  这类经过时长格式不算）→ 整天的值写 ISO `YYYY-MM-DD`，带时间的写 `YYYY-MM-DDTHH:MM:SS`（后者不是 ISO 日期，当文字）。
  纪元看 `workbookPr/@date1904`：1900 系统以 1899-12-30 为 0（序列号 < 60 的那一段按 Excel 的假 1900-02-29 修正：
  序列号 1 = 1900-01-01，60 = 那个不存在的日子，保持原数不转）；1904 系统以 1904-01-01 为 0。负数、0、超出
  `datetime` 范围的保持原数；
* 合并单元格 / 多行表头**不猜**：每个格子照它所在位置原样读（合并区只有左上角有值），交给上层判布局；
* 隐藏工作表照常列出（`state` 带回 `hidden` / `veryHidden`）；图表页（chartsheet）不是表，不列。

防护（解压缩包是整条链里唯一吃不可信二进制的地方）：成员数上限、**声明**的解压后总大小上限、读取时按实际字节数再卡一次
（声明会撒谎）、成员重名 / 加密 / 路径穿越一律拒绝；XML 里出现 `DOCTYPE` / `ENTITY`（外部实体、十亿笑声的前提）直接
拒绝，且只认 UTF-8（UTF-16 会绕过按字节的扫描）；`.xls` / `.xlsm` / `.xlsb` 明确拒绝（`data_excel_unsupported`）。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

#: 与 `ai_data_figure` 的上限同一口径（那边 import 这里，所以数在两处各写一份并由测试对拍相等）
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 64
#: 压缩包成员数 / 声明的解压后总大小 / 单个成员解压上限
MAX_MEMBERS = 1000
MAX_UNZIPPED = 100 * 1024 * 1024
#: Excel 的列上限（XFD）；超了不是合法引用
_MAX_COL = 16384

#: `XlsxError.code` 的稳定码闭集（并进 `ai_data_figure.ERROR_CODES`；前端 `errors:backend.<code>` 有中英文文案）
ERROR_CODES = (
    "data_excel_unsupported",
    "data_xlsx_invalid",
    "data_xlsx_too_large",
    "data_xlsx_unsafe",
    "data_sheet_not_found",
    "data_too_large",
    "data_too_long",
    "data_too_wide",
    "data_unreadable",
)

LEGACY_SUFFIXES = (".xls", ".xlsm", ".xlsb")


class XlsxError(ValueError):
    """XLSX 读不了；`code` 在 `ERROR_CODES` 里（构造时校验）。"""

    def __init__(self, code: str, detail: str = ""):
        if code not in ERROR_CODES:
            raise ValueError(f"未登记的 XlsxError 码：{code}")
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class SheetInfo:
    name: str
    index: int  # 0 起，工作簿里 `<sheets>` 的顺序（含隐藏表）
    state: str  # visible / hidden / veryHidden

    @property
    def hidden(self) -> bool:
        return self.state != "visible"


# --------------------------------------------------------------------------- XML（防护 + 流式）
_DOCTYPE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_XML_DECL_ENC = re.compile(rb"""^<\?xml[^>]*?encoding\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_NS = re.compile(r"^\{[^}]*\}")


def _local(tag: str) -> str:
    return _NS.sub("", tag)


def _check_xml_bytes(data: bytes) -> None:
    if b"\x00" in data[:4] or data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise XlsxError("data_xlsx_unsafe", "non-utf8-xml")
    body = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
    m = _XML_DECL_ENC.match(body)
    if m and m.group(1).lower() not in (b"utf-8", b"utf8"):
        raise XlsxError("data_xlsx_unsafe", "non-utf8-xml")
    if _DOCTYPE.search(data):
        raise XlsxError("data_xlsx_unsafe", "doctype")


def _iter_xml(data: bytes):
    """`(event, elem)`（start / end）流；先过 `_check_xml_bytes`；语法错误 → `data_xlsx_invalid`。"""
    _check_xml_bytes(data)
    parser = ET.XMLPullParser(events=("start", "end"))
    try:
        for i in range(0, len(data), 1 << 16):
            parser.feed(data[i : i + (1 << 16)])
            yield from parser.read_events()
        parser.close()
        yield from parser.read_events()
    except ET.ParseError:
        raise XlsxError("data_xlsx_invalid", "xml") from None


def _parse_small(data: bytes) -> ET.Element:
    """小文件（workbook / rels / styles / sharedStrings 之外的整树解析）：同样的防护，整树返回。"""
    _check_xml_bytes(data)
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise XlsxError("data_xlsx_invalid", "xml") from None


# --------------------------------------------------------------------------- 日期
#: 内置数字格式号里含年 / 日的（18–21 / 32–35 / 45–47 是纯时间，不在内）
_BUILTIN_DATE_FMT = frozenset({14, 15, 16, 17, 22, 27, 28, 29, 30, 31, 36}) | frozenset(
    range(50, 59)
)
_FMT_STRIP = re.compile(r'"[^"]*"|\[[^\]]*\]|\\.|_.|\*.')


def _is_date_format(code: str) -> bool:
    c = _FMT_STRIP.sub("", code.split(";")[0])
    if c.lower() == "general":
        return False
    if re.search(r"\[\s*[hms]+\s*\]", code, re.IGNORECASE):
        return False
    return re.search(r"[yd]", c, re.IGNORECASE) is not None


def _serial_to_text(raw: str, date1904: bool) -> str:
    """日期格里的数值 → ISO 文本；转不了（负数、0、假 1900-02-29、越界、非数）保持原文。"""
    try:
        v = float(raw)
    except ValueError:
        return raw
    if v < 1 or v >= 3_000_000:
        return raw
    days = int(v)
    frac = v - days
    try:
        if date1904:
            d = _dt.date(1904, 1, 1) + _dt.timedelta(days=days)
        elif days < 60:
            d = _dt.date(1899, 12, 31) + _dt.timedelta(days=days)
        elif days == 60:
            return raw
        else:
            d = _dt.date(1899, 12, 30) + _dt.timedelta(days=days)
    except OverflowError:
        return raw
    if frac < 1e-9:
        return d.isoformat()
    secs = round(frac * 86400)
    if secs >= 86400:  # 23:59:59.9999 进位
        try:
            d += _dt.timedelta(days=1)
        except OverflowError:
            return raw
        secs = 0
    t = _dt.time(secs // 3600, secs % 3600 // 60, secs % 60)
    return f"{d.isoformat()}T{t.isoformat()}"


# --------------------------------------------------------------------------- 工作簿
def _int_or_none(text: str | None) -> int | None:
    try:
        return int(text) if text else None
    except ValueError:
        return None


def _col_index(ref: str) -> int | None:
    m = re.match(r"^([A-Za-z]{1,3})(\d+)$", ref)
    if not m:
        return None
    n = 0
    for ch in m.group(1).upper():
        n = n * 26 + ord(ch) - 64
    return n - 1 if 1 <= n <= _MAX_COL else None


def _row_index(ref: str) -> int | None:
    m = re.match(r"^[A-Za-z]{1,3}(\d+)$", ref)
    return int(m.group(1)) if m else None


def _text_of(si: ET.Element) -> str:
    """`<si>` / `<is>`：直接的 `<t>` + 各 `<r><t>`；不取 `<rPh>`（拼音注音）。"""
    out = []
    for ch in si:
        tag = _local(ch.tag)
        if tag == "t":
            out.append(ch.text or "")
        elif tag == "r":
            out.extend(t.text or "" for t in ch if _local(t.tag) == "t")
    return "".join(out)


class XlsxBook:
    """打开一个 xlsx：校验压缩包、读工作表清单；共享字符串 / 样式第一次取值时才读。`close()` 或 `with` 用完关。"""

    def __init__(self, path: str | Path):
        p = Path(path)
        try:
            size = p.stat().st_size
            if size > MAX_BYTES:
                raise XlsxError("data_too_large", str(size))
            self.raw_sha256, self.size = _sha256_file(p), size
        except OSError as exc:
            raise XlsxError("data_unreadable", type(exc).__name__) from None
        self.name = p.name
        try:
            self._zf = zipfile.ZipFile(p)
        except (zipfile.BadZipFile, OSError, zlib.error, EOFError, NotImplementedError):
            raise XlsxError("data_xlsx_invalid", "zip") from None
        try:
            self._audit_zip()
            self._load_workbook()
        except BaseException:
            self._zf.close()
            raise
        self._sst: list[str] | None = None
        self._date_xf: list[bool] | None = None

    def __enter__(self) -> "XlsxBook":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._zf.close()

    # ---- 压缩包
    def _audit_zip(self) -> None:
        infos = self._zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise XlsxError("data_xlsx_too_large", f"members={len(infos)}")
        names = [i.filename for i in infos]
        if len(set(names)) != len(names):
            raise XlsxError("data_xlsx_invalid", "duplicate-member")
        if sum(i.file_size for i in infos) > MAX_UNZIPPED:
            raise XlsxError("data_xlsx_too_large", "unzipped")
        if any(i.flag_bits & 0x1 for i in infos):
            raise XlsxError("data_xlsx_invalid", "encrypted")

    def _read(self, member: str, *, required: bool = True) -> bytes | None:
        """读一个成员（只在内存里）；按**实际**读到的字节卡上限，不信声明的 `file_size`。"""
        try:
            info = self._zf.getinfo(member)
        except KeyError:
            if required:
                raise XlsxError("data_xlsx_invalid", f"missing:{member}") from None
            return None
        cap = min(info.file_size, MAX_UNZIPPED)
        try:
            with self._zf.open(info) as fh:
                data = fh.read(cap + 1)
        except (
            zipfile.BadZipFile,
            OSError,
            zlib.error,
            EOFError,
            NotImplementedError,
            RuntimeError,
        ):
            raise XlsxError("data_xlsx_invalid", "member") from None
        if len(data) > cap:
            raise XlsxError("data_xlsx_too_large", "member")
        return data

    # ---- 工作簿清单
    def _load_workbook(self) -> None:
        wb = _parse_small(self._read("xl/workbook.xml"))
        rels = _parse_small(self._read("xl/_rels/workbook.xml.rels"))
        targets: dict[str, tuple[str, str]] = {}
        for r in rels:
            if _local(r.tag) == "Relationship" and r.get("Mode") != "External":
                targets[r.get("Id", "")] = (r.get("Type", ""), r.get("Target", ""))
        self.date1904 = False
        sheets: list[SheetInfo] = []
        self._sheet_member: dict[int, str] = {}
        for el in wb.iter():
            tag = _local(el.tag)
            if tag == "workbookPr":
                self.date1904 = (el.get("date1904") or "").lower() in ("1", "true")
            elif tag == "sheet":
                rid = next((v for k, v in el.attrib.items() if _local(k) == "id"), "")
                typ, tgt = targets.get(rid, ("", ""))
                if not typ.endswith("/worksheet") or not tgt:
                    continue  # 图表页 / 宏表 / 悬空引用：不是表
                member = posixpath.normpath(tgt.lstrip("/") if tgt.startswith("/") else f"xl/{tgt}")
                if member.startswith("..") or member.startswith("/"):
                    raise XlsxError("data_xlsx_invalid", "path")
                state = el.get("state") or "visible"
                if state not in ("visible", "hidden", "veryHidden"):
                    state = "visible"
                idx = len(sheets)
                sheets.append(SheetInfo(el.get("name", ""), idx, state))
                self._sheet_member[idx] = member
        if not sheets:
            raise XlsxError("data_xlsx_invalid", "no-sheet")
        self.sheets: list[SheetInfo] = sheets

    def resolve(self, sheet: str | int | None) -> SheetInfo:
        """`None` = 第一张可见表（没有可见的取第一张）；`int` = 序号（0 起）；`str` = 表名（区分大小写）。"""
        if sheet is None:
            return next((s for s in self.sheets if not s.hidden), self.sheets[0])
        if isinstance(sheet, bool):
            raise XlsxError("data_sheet_not_found")
        if isinstance(sheet, int):
            if 0 <= sheet < len(self.sheets):
                return self.sheets[sheet]
            raise XlsxError("data_sheet_not_found", str(sheet))
        for s in self.sheets:
            if s.name == sheet:
                return s
        raise XlsxError("data_sheet_not_found")

    # ---- 共享字符串 / 样式
    def _shared_strings(self) -> list[str]:
        if self._sst is None:
            out: list[str] = []
            data = self._read("xl/sharedStrings.xml", required=False)
            if data:
                for ev, el in _iter_xml(data):
                    if ev == "end" and _local(el.tag) == "si":
                        out.append(_text_of(el))
                        el.clear()
            self._sst = out
        return self._sst

    def _date_styles(self) -> list[bool]:
        """`cellXfs` 每一项（样式号）是不是日期格式。"""
        if self._date_xf is None:
            flags: list[bool] = []
            data = self._read("xl/styles.xml", required=False)
            if data:
                root = _parse_small(data)
                custom: dict[int, str] = {}
                for el in root.iter():
                    if _local(el.tag) == "numFmt":
                        try:
                            custom[int(el.get("numFmtId", ""))] = el.get("formatCode", "")
                        except ValueError:
                            pass
                for xfs in root:
                    if _local(xfs.tag) != "cellXfs":
                        continue
                    for xf in xfs:
                        try:
                            fid = int(xf.get("numFmtId", "0"))
                        except ValueError:
                            fid = 0
                        flags.append(
                            _is_date_format(custom[fid])
                            if fid in custom
                            else fid in _BUILTIN_DATE_FMT
                        )
            self._date_xf = flags
        return self._date_xf

    # ---- 取值
    def read(self, sheet: str | int | None = None) -> list[list[str]]:
        """一张表的格子 → 行列表（全空行丢掉；每行补齐到「最右一个非空格」的宽度）。行数超 `MAX_ROWS + 1`（含表头）
        → `data_too_long`；非空格子落在第 `MAX_COLUMNS` 列之后 → `data_too_wide`（**在补齐之前**判：稀疏表里一个远在
        XFD 列的格子会把每一行撑到 16384 列，几十 KB 的文件就能吃光内存）。"""
        info = self.resolve(sheet)
        data = self._read(self._sheet_member[info.index])
        sst = self._shared_strings()
        date_xf = self._date_styles()
        rows: list[dict[int, str]] = []
        numbers: list[int] = []
        cur: dict[int, str] | None = None
        cur_no = 0
        last_no = 0
        next_col = 0
        for ev, el in _iter_xml(data):
            tag = _local(el.tag)
            if ev == "start":
                if tag == "row":
                    cur, next_col = {}, 0
                    ref_no = _int_or_none(el.get("r"))
                    cur_no = ref_no if ref_no is not None else last_no + 1
                    last_no = cur_no
                continue
            if tag == "c" and cur is not None:
                ref = el.get("r") or ""
                col = _col_index(ref) if ref else next_col
                if col is None:
                    raise XlsxError("data_xlsx_invalid", "cell-ref")
                next_col = col + 1
                val = self._cell_text(el, sst, date_xf)
                if val != "":
                    if col >= MAX_COLUMNS:
                        raise XlsxError("data_too_wide", str(col + 1))
                    cur[col] = val
                el.clear()
            elif tag == "row" and cur is not None:
                if cur:
                    rows.append(cur)
                    numbers.append(cur_no)
                    if len(rows) > MAX_ROWS + 1:
                        raise XlsxError("data_too_long", str(len(rows)))
                cur = None
                el.clear()
        width = max((max(r) + 1 for r in rows), default=0)
        #: 与返回的行逐项对应的工作表行号（人看到的行号：全空行被丢掉，所以不是下标 + 1）
        self.row_numbers = numbers
        return [[r.get(c, "") for c in range(width)] for r in rows]

    def _cell_text(self, c: ET.Element, sst: list[str], date_xf: list[bool]) -> str:
        t = c.get("t") or "n"
        v = None
        inline = None
        for ch in c:
            tag = _local(ch.tag)
            if tag == "v":
                v = ch.text or ""
            elif tag == "is":
                inline = _text_of(ch)
        if t == "inlineStr":
            return inline if inline is not None else (v or "")
        if v is None:
            return ""  # 没有缓存值的公式 / 空格子
        if t == "s":
            try:
                return sst[int(v)]
            except (ValueError, IndexError):
                raise XlsxError("data_xlsx_invalid", "shared-string-index") from None
        if t == "b":
            return "TRUE" if v.strip() == "1" else "FALSE"
        if t in ("str", "e", "d"):
            return v
        v = v.strip()
        try:
            s_idx = int(c.get("s") or "0")
        except ValueError:
            s_idx = 0
        if 0 <= s_idx < len(date_xf) and date_xf[s_idx]:
            return _serial_to_text(v, self.date1904)
        return v


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def list_xlsx_sheets(path: str | Path) -> list[SheetInfo]:
    """工作表清单（含隐藏表，工作簿里的顺序）。"""
    with XlsxBook(path) as book:
        return list(book.sheets)


def read_xlsx_values(path: str | Path, sheet: str | int | None = None) -> list[list[str]]:
    """一张工作表的格子值（字符串表，规则见模块文档）。`sheet`：表名 / 序号（0 起）/ `None` = 第一张可见表。"""
    with XlsxBook(path) as book:
        return book.read(sheet)
