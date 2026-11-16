"""绑定用的表格/CSV 数据入口（ADR 0182，SG06）——类型化错误，绝不静默填零。

普通用户带进来的数据是 CSV 或粘贴的表格。这里只做**解析与定位**：每个
问题（非数值、缺失值、日期列、千分位、列长不齐）都带行列地址，让确认
预览能指到具体格子；解析成功的产品是纯 `float` 列，NaN 记缺失、Infinity
记非有限（与 chartmodel 的读入闸同口径）。单元格判据的唯一出处是
`ai_data_figure.parse_cell` / `parse_date`；本模块只保留分隔符探测、引号
表头、格子地址与诊断探针。
"""

from __future__ import annotations

import csv
import io
import math
import re

from .ai_data_figure import MAX_COLUMNS, MAX_ROWS, parse_cell, parse_date

#: 数据问题的稳定码（前端文案落地时同步 errors.json）。
ISSUE_CODES = (
    "data_not_numeric",  # 该格不是数（含日期/文字列）
    "data_missing",  # 该格为空或 NaN
    "data_non_finite",  # 该格是 Infinity 或溢出成非有限数的字面量
    "data_thousands_separator",  # 千分位（1,234.5）——需要用户清理，不猜
    "data_row_length",  # 该行列数与表头不一致
    "data_too_long",  # 数据行数超过 0178 的上限
    "data_too_wide",  # 列数超过 0178 的上限
)

_DELIMITERS = ("\t", ";", ",")
_DATE_HINT = re.compile(r"^\d{4}[-/]\d{1,2}([-/]\d{1,2})?$|^\d{1,2}/\d{1,2}/\d{2,4}$")


def _table_delimiter(text: str) -> str:
    """优先按表头选一次；平票时用前几条记录的宽度一致性定，不逐行重新选择。"""
    widths = {}
    uniform = {}
    for delimiter in _DELIMITERS:
        rows = []
        clean = True
        try:
            reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
            for index, row in enumerate(reader):
                rows.append(len(row))
                if index >= 7:
                    break
        except csv.Error:
            # 表头之后才炸的候选不是这张表的方言；仍保留表头宽度参与平票。
            clean = False
        if not rows:
            continue
        widths[delimiter] = rows[0]
        uniform[delimiter] = clean and len(set(rows)) == 1
    highest = max(widths.values(), default=0)
    candidates = [delimiter for delimiter, width in widths.items() if width == highest]
    if highest > 1 and len(candidates) == 1:
        return candidates[0]
    if highest > 1:
        # 表头引号里可以合法出现其他分隔符，宽度会平票；正确方言的整段记录宽度一致。
        steady = [delimiter for delimiter in candidates if uniform[delimiter]]
        if len(steady) == 1:
            return steady[0]
        candidates = steady or candidates
    if highest > 1 or len(widths) < len(_DELIMITERS):
        # 仍无法区分时交给标准库方言探测；候选只来自表头宽度平票集。
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


def _text_issue(raw: str, row: int, col: int, column: str | None = None) -> dict:
    """`parse_cell` 判 `text` 之后的诊断；顺序固定，不复制数值正则。

    日期提示优先；`float()` 能认下的 Infinity/溢出记 `data_non_finite`；
    去千分位后能解析的记 `data_thousands_separator`；其余是
    `data_not_numeric`。表列带 `column`，网格不带。
    """
    text = raw.strip()
    issue = {"row": row, "col": col, **({"column": column} if column is not None else {})}
    if parse_date(text) is not None or _DATE_HINT.fullmatch(text):
        detail = (
            f"column {column!r}: date-like value needs conversion"
            if column is not None
            else f"grid cell {raw!r}"
        )
        return {**issue, "code": "data_not_numeric", "detail": detail}
    try:
        probe = float(text)
    except ValueError:
        probe = None
    if probe is not None and math.isinf(probe):
        detail = f"column {column!r}" if column is not None else f"grid cell {raw!r}"
        return {**issue, "code": "data_non_finite", "detail": detail}
    if "," in text:
        try:
            float(text.replace(",", ""))
        except ValueError:
            pass
        else:
            detail = f"column {column!r}: {raw!r}" if column is not None else f"grid cell {raw!r}"
            return {**issue, "code": "data_thousands_separator", "detail": detail}
    detail = f"column {column!r}: {raw!r}" if column is not None else f"grid cell {raw!r}"
    return {**issue, "code": "data_not_numeric", "detail": detail}


def parse_table(text: str) -> dict:
    """解析表格文本 → ``{"columns": {...}, "issues": [...]}``。

    列名取第一个 CSV 记录；分隔符只按表头引号外的候选定一次。
    引号、双写引号和跨行单元格由标准库解析；格地址使用记录的物理起始行。
    返回的 columns 只含**全数值**的列；含问题的列缺席并记 issues。
    超过 0178 的列/行上限时整表拒绝，不返回部分列。
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
    data_rows = 0
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
            if len(header) > MAX_COLUMNS:
                return {
                    "columns": {},
                    "text_columns": {},
                    "issues": [
                        {
                            "code": "data_too_wide",
                            "row": 1,
                            "col": 1,
                            "detail": f"table has {len(header)} columns (max {MAX_COLUMNS})",
                        }
                    ],
                }
            continue
        if not record.strip():
            continue
        data_rows += 1
        if data_rows > MAX_ROWS:
            return {
                "columns": {},
                "text_columns": {},
                "issues": [
                    {
                        "code": "data_too_long",
                        "row": 1,
                        "col": 1,
                        "detail": f"table has more than {MAX_ROWS} rows",
                    }
                ],
            }
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
            kind, value = parse_cell(raw)
            if kind in ("empty", "nan"):
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
            if kind == "numeric":
                if "," in raw:
                    # 0178 的 parse_cell 把合法千分位当数值；chartdata 的合同仍是
                    # 「报告、不猜」，所以含逗号的数值格回落到 _text_issue
                    # （data_thousands_separator），不把值收进数值列。
                    texts.append(raw)
                    column_issues.append(_text_issue(raw, r, c + 1, col_name))
                    continue
                values.append(float(value))
                texts.append(raw)
                continue
            texts.append(raw)  # 非数值格：数值列的 issue；文本列候选同步积累
            issue = _text_issue(raw, r, c + 1, col_name)
            column_issues.append(issue)
            if issue["code"] == "data_non_finite":
                text_ok = False
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
    绝不静默填零。NaN 记缺失、Infinity/溢出记非有限；超过 0178 的列/行上限
    时整表拒绝。
    """
    values: list[list[float]] = []
    issues: list[dict] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    width = None
    data_rows = 0
    for r, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        data_rows += 1
        if data_rows > MAX_ROWS:
            return {
                "values": [],
                "issues": [
                    {
                        "code": "data_too_long",
                        "row": 1,
                        "col": 1,
                        "detail": f"grid has more than {MAX_ROWS} rows",
                    }
                ],
            }
        cells = [c.strip() for c in re.split(r"[,\s]+", line.strip()) if c != ""]
        if width is None:
            width = len(cells)
            if width > MAX_COLUMNS:
                return {
                    "values": [],
                    "issues": [
                        {
                            "code": "data_too_wide",
                            "row": 1,
                            "col": 1,
                            "detail": f"grid has {width} columns (max {MAX_COLUMNS})",
                        }
                    ],
                }
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
            kind, value = parse_cell(raw)
            if kind in ("empty", "nan"):
                issues.append(
                    {"code": "data_missing", "row": r, "col": c, "detail": f"grid cell {raw!r}"}
                )
            elif kind == "numeric":
                row.append(float(value))
            else:
                issues.append(_text_issue(raw, r, c))
        if len(row) == len(cells):
            values.append(row)
    return {"values": values, "issues": issues}
