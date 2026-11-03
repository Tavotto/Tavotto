"""绑定用的表格/CSV 数据入口（ADR 0166，SG06）——类型化错误，绝不静默填零。

普通用户带进来的数据是 CSV 或粘贴的表格。这里只做**解析与定位**：每个
问题（非数值、缺失值、日期列、千分位、列长不齐）都带行列地址，让确认
预览能指到具体格子；解析成功的产品是纯 `float` 列，NaN/Infinity 在格级
就死（与 chartmodel 的读入闸同口径）。
"""

from __future__ import annotations

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


def parse_table(text: str) -> dict:
    """解析表格文本 → ``{"columns": {...}, "issues": [...]}``。

    列名取第一行；分隔符按出现次数最多的候选定（tab / 分号 / 逗号）。
    返回的 columns 只含**全数值**的列；含问题的列缺席并记 issues。
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or not any(ln.strip() for ln in lines):
        return {
            "columns": {},
            "issues": [{"code": "data_missing", "row": 1, "col": 1, "detail": "empty table"}],
        }

    def split_row(line: str) -> list[str]:
        best = None
        for delim in _DELIMITERS:
            count = line.count(delim)
            if best is None or count > best[1]:
                best = (delim, count)
        delim = best[0] if best and best[1] > 0 else "\t"
        return [cell.strip() for cell in line.split(delim)]

    header = split_row(lines[0])
    width = len(header)
    rows: list[list[str]] = []
    issues: list[dict] = []
    for i, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        cells = split_row(line)
        if len(cells) != width:
            issues.append(
                {
                    "code": "data_row_length",
                    "row": i,
                    "col": len(cells) + 1,
                    "detail": f"expected {width} cells, got {len(cells)}",
                }
            )
            continue
        rows.append(cells)

    columns: dict[str, list[float]] = {}
    text_columns: dict[str, list[str]] = {}
    for c, name in enumerate(header):
        col_name = name or f"col{c + 1}"
        values: list[float] = []
        texts: list[str] = []
        text_ok = True
        column_issues: list[dict] = []
        for r, cells in enumerate(rows, start=2):
            raw = cells[c] if c < len(cells) else ""
            if raw == "":
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
            if _NUM_RE.fullmatch(raw):
                values.append(float(raw))
                texts.append(raw)
                continue
            texts.append(raw)  # 非数值格：数值列的 issue；文本列候选同步积累
            if _DATE_HINT.fullmatch(raw):
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
            cleaned = raw.replace(",", "")
            if "," in raw and _NUM_RE.fullmatch(cleaned):
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
    # NaN/Infinity 已被 _NUM_RE 挡下（字面量不匹配）；显式再核一遍防御浮点噪声
    for name, values in columns.items():
        for j, v in enumerate(values):
            if not math.isfinite(v):
                issues.append(
                    {
                        "code": "data_non_finite",
                        "row": j + 2,
                        "col": 1,
                        "detail": f"column {name!r}",
                    }
                )
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
