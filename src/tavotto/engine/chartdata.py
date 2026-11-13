"""绑定用的表格/CSV 数据入口（ADR-XXXX，SG06）——类型化错误，绝不静默填零。

普通用户带进来的数据是 CSV 或粘贴的表格。这里只做**解析与定位**：每个
问题（非数值、缺失值、日期列、千分位、列长不齐）都带行列地址，让确认
预览能指到具体格子；解析成功的产品是纯 `float` 列，NaN/Infinity 在格级
就死（与 chartmodel 的读入闸同口径）。
"""

from __future__ import annotations

import csv
import io
import math
import re

#: 数据问题的稳定码（前端文案落地时同步 errors.json）。
ISSUE_CODES = (
    "data_not_numeric",  # 该格不是数（含日期/文字列）
    "data_missing",  # 该格为空
    "data_non_finite",  # 该格是 NaN/Infinity 字面量
    "data_thousands_separator",  # 千分位（1,234.5）——需要用户清理，不猜
    "data_row_length",  # 该行列数与表头不一致
)

_DELIMITERS = ("\t", ";", ",")
_NUM_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")
_DATE_HINT = re.compile(r"^\d{4}[-/]\d{1,2}([-/]\d{1,2})?$|^\d{1,2}/\d{1,2}/\d{2,4}$")


def _table_delimiter(text: str) -> str:
    """优先按表头选一次；歧义候选交给标准库，不逐行重新选择。"""
    widths = {}
    for delimiter in _DELIMITERS:
        try:
            reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
            widths[delimiter] = len(next(reader, []))
        except csv.Error:
            continue
    highest = max(widths.values(), default=0)
    candidates = [delimiter for delimiter, width in widths.items() if width == highest]
    if highest > 1 and len(candidates) == 1:
        return candidates[0]
    if highest > 1 or len(widths) < len(_DELIMITERS):
        # 表头本身可含其他合法分隔符；平票时由 CSV 方言探测核对整表。
        # 仅有一列时，异常候选仍可能是分隔符：不能把畸形CSV降成合法的一列TSV。
        options = "".join(candidates if highest > 1 else _DELIMITERS)
        for sample in (text, io.StringIO(text, newline="").readline()):
            try:
                return csv.Sniffer().sniff(sample, delimiters=options).delimiter
            except csv.Error:
                continue
    return candidates[0] if candidates else _DELIMITERS[0]


def _header_names(values: list[str], record: str) -> list[str]:
    """未引用表头沿用旧的去外围空白；引用表头保留精确列名。

    值已经由 csv.reader 解析，按它们的编码长度定位外层引号；这里不再拆分
    或反转义记录。默认 CSV 的字段内引号每个双写一次，故编码长度可直接求得。
    """
    offset = 0
    names = []
    for value in values:
        if record[offset : offset + 1] == '"':
            names.append(value)
            offset += len(value) + value.count('"') + 2
        else:
            names.append(value.strip())
            offset += len(value)
        offset += 1  # 下一个字段前恰有一个已由 csv.reader 验证过的分隔符。
    return names


def parse_table(text: str) -> dict:
    """解析表格文本 → ``{"columns": {...}, "issues": [...]}``。

    列名取第一个 CSV 记录；分隔符只按表头引号外的候选定一次。
    引号、双写引号和跨行单元格由标准库解析；格地址使用记录的物理起始行。
    返回的 columns 只含**全数值**的列；含问题的列缺席并记 issues。
    """
    if not text.strip():
        return {
            "columns": {},
            "issues": [{"code": "data_missing", "row": 1, "col": 1, "detail": "empty table"}],
        }

    stream = io.StringIO(text, newline="")
    reader = csv.reader(stream, delimiter=_table_delimiter(text), strict=True)
    header: list[str] | None = None
    rows: list[tuple[int, list[str]]] = []
    issues: list[dict] = []
    while True:
        start_row, start_offset = reader.line_num + 1, stream.tell()
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            issues.append(
                {
                    "code": "data_row_length",
                    "row": start_row,
                    "col": 1,
                    "detail": f"invalid delimited record: {exc}",
                }
            )
            break  # 引号破损时无法证明后续物理行是新记录，不猜测恢复点。
        record = text[start_offset : stream.tell()]
        if header is None:
            header = _header_names(cells or [""], record)
            continue
        if not record.strip():
            continue
        if len(cells) != len(header):
            issues.append(
                {
                    "code": "data_row_length",
                    "row": start_row,
                    "col": len(cells) + 1,
                    "detail": f"expected {len(header)} cells, got {len(cells)}",
                }
            )
            continue
        rows.append((start_row, cells))

    columns: dict[str, list[float]] = {}
    text_columns: dict[str, list[str]] = {}
    for c, name in enumerate(header or []):
        col_name = name or f"col{c + 1}"
        values: list[float] = []
        texts: list[str] = []
        text_ok = True
        column_issues: list[dict] = []
        for r, cells in rows:
            raw = cells[c]
            numeric = raw.strip()
            if numeric == "":
                column_issues.append(
                    {
                        "code": "data_missing",
                        "row": r,
                        "col": c + 1,
                        "column": col_name,
                        "detail": f"column {col_name!r}",
                    }
                )
                text_ok = False
                continue
            if _NUM_RE.fullmatch(numeric):
                value = float(numeric)
                if not math.isfinite(value):
                    column_issues.append(
                        {
                            "code": "data_non_finite",
                            "row": r,
                            "col": c + 1,
                            "column": col_name,
                            "detail": f"column {col_name!r}",
                        }
                    )
                    text_ok = False
                    continue
                values.append(value)
                texts.append(raw)
                continue
            texts.append(raw)  # 非数值格：数值列的 issue；文本列候选同步积累
            if _DATE_HINT.fullmatch(numeric):
                column_issues.append(
                    {
                        "code": "data_not_numeric",
                        "row": r,
                        "col": c + 1,
                        "column": col_name,
                        "detail": f"column {col_name!r}: date-like value needs conversion",
                    }
                )
                continue
            cleaned = numeric.replace(",", "")
            if "," in numeric and _NUM_RE.fullmatch(cleaned):
                column_issues.append(
                    {
                        "code": "data_thousands_separator",
                        "row": r,
                        "col": c + 1,
                        "column": col_name,
                        "detail": f"column {col_name!r}: {raw!r}",
                    }
                )
                continue
            column_issues.append(
                {
                    "code": "data_not_numeric",
                    "row": r,
                    "col": c + 1,
                    "column": col_name,
                    "detail": f"column {col_name!r}: {raw!r}",
                }
            )
        if not column_issues:
            columns[col_name] = values
        else:
            issues.extend(column_issues)
            # 无缺失、全非空的文本列保留（类别/标签的**显式指认**来源，绝不自动当数值）
            if text_ok and texts and all(t for t in texts):
                text_columns[col_name] = texts
    return {"columns": columns, "text_columns": text_columns, "issues": issues}


def parse_grid(text: str) -> dict:
    """解析数值矩阵（空白/逗号分隔，无表头）→ ``{"values": [[...]], "issues": []}``。

    网格数据（热图/标量场）不带类别表头；任何非数值格都是带行列地址的问题，
    绝不静默填零。
    """
    values: list[list[float]] = []
    issues: list[dict] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    width = None
    for r, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        cells = [c.strip() for c in re.split(r"[,\s]+", line.strip()) if c != ""]
        if width is None:
            width = len(cells)
        elif len(cells) != width:
            issues.append(
                {
                    "code": "data_row_length",
                    "row": r,
                    "col": len(cells) + 1,
                    "detail": f"expected {width} cells, got {len(cells)}",
                }
            )
            continue
        row: list[float] = []
        for c, raw in enumerate(cells, start=1):
            if _NUM_RE.fullmatch(raw):
                row.append(float(raw))
            else:
                issues.append(
                    {"code": "data_not_numeric", "row": r, "col": c, "detail": f"grid cell {raw!r}"}
                )
        if len(row) == len(cells):
            values.append(row)
    return {"values": values, "issues": issues}
