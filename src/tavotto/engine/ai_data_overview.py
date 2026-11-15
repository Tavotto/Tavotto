"""数据出图 v2 的两个只读出口（ADR 0178「数据概览与来源」一节）。

纯标准库，Flask 父进程 import；**只读**：不写任何文件、不调模型、不执行任何代码、不发遥测。表里的文字与脚本里的注释都是不可信输入，
只显示、只做严格解析。

1. `overview(root, path, sheet=, header_row=)`：一个数据文件（文本表或 XLSX 的一张表）的概览——状态、行列数、每列的名字与类型、
   前 `PREVIEW_ROWS` 行单元格原文、表头行之前被跳过的说明行。**读表就是 propose 读表的那一份**（文本：`read_text_table`，
   XLSX：`inspect_data_source(v2=True)`），所以预览里看到的就是之后出图读到的那张表；`header_row` 与 propose 的 `header_rows` 同一口径。
2. `generated_from(root, script_rel)` / `annotate_generated_from`：某个脚本是不是 v2 生成的、来源是哪个数据文件（项目相对路径，XLSX 带工作表名）。
   只给路径与工作表名，不给配方、不给哈希。
"""

from __future__ import annotations

import os
import posixpath
import re
from decimal import Decimal
from pathlib import Path, PurePosixPath

from . import (
    ai_data_figure as adf,
    ai_data_propose as propose_mod,
    ai_data_template as tpl,
    discover,
    projectenv,
)

#: 每个响应的上界：前多少行、每格 / 每个列名最多多少字符（超出截断加 `…`）。说明行的条数与长度上界在 `adf.LEAD_MAX_LINES` / `adf.PREVIEW_CHARS`
PREVIEW_ROWS = 20
CELL_CHARS = 80
NAME_CHARS = 200

#: 单元格 / 列名里的控制字符（含制表符与换行、C1 控制符）换成空格——它们只显示
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _clip(text: str, limit: int) -> str:
    t = _CONTROL.sub(" ", text)
    return t if len(t) <= limit else t[: limit - 1] + "…"


#: XLSX 里存的浮点数文字（`0.6840000000000001`）：有小数点 / 指数、数字位 > 15 才是「浮点误差的尾巴」，整数与位数不多的原样
_FLOAT_LITERAL = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+(?=[eE]))(?:[eE][+-]?\d+)?$")


def display_number(text: str) -> str:
    """XLSX 数值格的预览文字：与 Excel 自己显示一样取 15 位有效数字（`0.6840000000000001` → `0.684`），**只给人看**，读表 / 类型 / 资格 /
    出图用的仍是原文。规则：整格是带小数点或指数的十进制字面量且有效数字 > 15 位才改（整数、位数不多的、日期 `2026-01-01`、
    `TRUE` / `#N/A` / 普通文字都原样）；改写后去掉多余的尾零；绝对值在 [1e-9, 1e15) 用普通小数写法（不出科学计数法），更大 / 更小的才用
    科学计数法（`1.5e-12`）。无法确定的原样给。"""
    if not _FLOAT_LITERAL.match(text):
        return text
    mantissa = re.split(r"[eE]", text)[0].lstrip("+-").replace(".", "").lstrip("0")
    if len(mantissa) <= 15:
        return text
    try:
        x = float(text)
    except ValueError:
        return text
    if x != x or x in (float("inf"), float("-inf")):
        return text
    sci = f"{x:.14e}"  # 15 位有效数字
    if x == 0 or 1e-9 <= abs(x) < 1e15:
        out = format(Decimal(sci), "f")
        if "." in out:
            out = out.rstrip("0").rstrip(".")
        return out
    m, _, e = sci.partition("e")
    m = m.rstrip("0").rstrip(".")
    return f"{m}e{int(e)}"


def _lines(pairs) -> list[dict]:
    return [{"line": n, "text": t} for n, t in pairs]


def _base(rel: str, status: str, code: str | None = None) -> dict:
    return {
        "path": rel,
        "sheet": None,
        "status": status,
        "code": code,
        "delimiter": None,
        "encoding": None,
        "header_row": None,
        "rows": None,
        "columns": None,
        "layout": None,
        "fields": [],
        "preview_rows": [],
        "truncated": False,
        "lead_lines": [],
        "raw_lines": [],
    }


def _fill(out: dict, summ: adf.DataSummary, *, xlsx: bool = False) -> None:
    out["rows"] = summ.row_count if summ.row_count is not None else len(summ.rows)
    out["columns"] = len(summ.columns)
    out["layout"] = summ.layout
    out["fields"] = [
        {
            "name": _clip(name, NAME_CHARS),
            "kind": summ.per_column[name].kind,
            "numeric": summ.per_column[name].numeric,
            "text": summ.per_column[name].text_count,
            "empty": summ.per_column[name].empty,
            "nan": summ.per_column[name].nan,
            "id_like": name in summ.id_like,
        }
        for name in summ.columns
    ]
    out["preview_rows"] = [
        {
            "line": summ.row_number(i),
            "cells": [_clip(display_number(c) if xlsx else c, CELL_CHARS) for c in row],
        }
        for i, row in enumerate(summ.rows[:PREVIEW_ROWS])
    ]
    out["truncated"] = len(summ.rows) > PREVIEW_ROWS


def resolve_file(root: str | Path, path) -> tuple[str, Path]:
    """请求里的数据文件路径 → `(规范的项目相对路径, 绝对路径)`。

    不拿用户给的字符串直接拼路径：先 `normalize_rel`（字符串层）与 `projectenv.contained_path`（realpath 后钉在项目内），再让
    `discover.walk_files` 遍历项目（隐藏 / `PRUNE_DIRS` 剪枝、不进链接目录、不列链接文件、表格后缀）把数据文件列出来，只认其中**逐字相等**的那一项，
    返回的路径取自遍历结果。文件夹外、链接、隐藏项、非表格后缀都落不进来。失败抛 `DataSourceError`（`data_path_outside_project` / `data_not_found`）。"""
    if not isinstance(path, str) or len(path) > 1024:
        raise adf.DataSourceError("data_not_found")
    try:
        rel = propose_mod.normalize_rel(path)
    except ValueError:
        raise adf.DataSourceError("data_path_outside_project") from None
    if not rel:
        raise adf.DataSourceError("data_not_found")
    if projectenv.contained_path(root, rel) is None:
        raise adf.DataSourceError("data_path_outside_project")
    base = projectenv.contained_path(root, "")
    if base is None:
        raise adf.DataSourceError("data_not_found")
    found, _ = discover.walk_files(
        Path(base), adf.TABLE_SUFFIXES + adf.LEGACY_EXCEL_SUFFIXES, scan_max=adf.PROFILE_SCAN_MAX
    )
    hit = next((f[0] for f in found if f[0] == rel and not Path(f[0]).name.startswith("~$")), None)
    if hit is None:
        raise adf.DataSourceError("data_not_found")
    return hit, Path(base) / PurePosixPath(hit)


def overview(root: str | Path, path, *, sheet=None, header_row=None) -> dict:
    """一个数据文件（文本表或 XLSX 的一张表）的概览（形状见 ADR 0178「数据概览」）。

    `header_row`：调用方指定的「每列名字在第几行」（1 起的物理行号，与 propose 的 `header_rows` 同一判据）；只对文本表有意义，
    XLSX 的表头永远是第一条非空行，给了也不用（与 propose 一致）。`sheet`：XLSX 的工作表名，缺省 = 第一张可见表。
    读不了不抛：`status == "unreadable"` + 稳定码；请求本身不对（路径出界 / 文件不在 / 工作表不存在 / 参数形状）抛 `DataSourceError` /
    `ProposeError`。只读，用户文件一个字节不动。"""
    if sheet is not None and (
        not isinstance(sheet, str) or not 0 < len(sheet) <= 255 or "\x00" in sheet
    ):
        raise propose_mod.ProposeError("propose_input_invalid", "sheet")
    hrow = None if header_row is None else propose_mod.parse_header_row(header_row)
    rel, file = resolve_file(root, path)
    suffix = file.suffix.lower()
    if suffix in adf.LEGACY_EXCEL_SUFFIXES:
        return _base(rel, "unreadable", "data_excel_unsupported")
    if suffix in adf.XLSX_SUFFIXES:
        out = _base(rel, "ok")
        try:
            summ = adf.inspect_data_source(file, sheet, v2=True)
        except adf.DataSourceError as exc:
            if exc.code == "data_sheet_not_found":
                raise
            out.update(status="unreadable", code=exc.code)
            return out
        except Exception:  # noqa: BLE001 —— 单个文件的任何意外只是这一个读不了
            out.update(status="unreadable", code="data_unreadable")
            return out
        out["sheet"] = summ.sheet
        _fill(out, summ, xlsx=True)
        return out
    try:
        info = adf.read_text_table(file, header_row=hrow)
    except Exception:  # noqa: BLE001
        return _base(rel, "unreadable", "data_unreadable")
    out = _base(rel, info.status, info.code)
    # 读不了但可以靠指定表头行补救的（`data_ragged`、指定的行读不成表…）：`read_text_table` 带着原文行与猜的 / 用的行号，原样带出去；
    # 真二进制 / 散文 / 编码错这类没有 preview，`raw_lines` 为空 = 没法点一行补救（前端据 `raw_lines` 非空决定要不要让用户点一行）
    out.update(
        delimiter=adf.DELIMITER_NAMES.get(info.delimiter) if info.delimiter else None,
        encoding=info.encoding,
        header_row=info.header_line,
        rows=info.rows,
        columns=info.columns,
        lead_lines=_lines(info.lead),
        raw_lines=_lines(info.preview),
    )
    if info.summary is not None:
        _fill(out, info.summary)
    return out


# --------------------------------------------------------------------------- 脚本的来源
#: 一次最多读多少个脚本的开头（大项目里一次请求不至于读遍所有脚本；超出的不带来源，不假装没有）
GENERATED_FROM_MAX = 2000


def _data_path(script_rel: str, data: str) -> str | None:
    """来源注释里的数据路径（相对脚本所在目录）→ 项目相对 POSIX 路径；绝对路径 / 盘符 / 逃出项目 / 不是表格后缀都回 None。纯字符串，不碰文件系统。"""
    if data.startswith("/") or re.match(r"^[A-Za-z]:", data):
        return None
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(script_rel), data))
    if joined in (".", "") or joined == ".." or joined.startswith("../") or joined.startswith("/"):
        return None
    if not joined.lower().endswith(adf.TABLE_SUFFIXES):
        return None
    return joined


def generated_from(root: str | Path, script_rel: str) -> dict | None:
    """脚本（项目相对路径）是 Tavotto v2 从数据生成的吗：是 → `{"data": 数据文件的项目相对路径, "sheet": 工作表名 | None}`，否则 None。

    只读脚本开头 `tpl.SOURCE_SCAN_CHARS` 字符，解析走 `ai_data_template.parse_source_comment`（严格，格式不对 = 没有）；路径钉在项目内、
    不跟随链接；**不核对数据文件还在不在**（数据被挪走时这张图仍是「从那份数据生成的」）。"""
    if not isinstance(script_rel, str) or not script_rel.endswith(".py"):
        return None
    real = projectenv.contained_path(root, script_rel)
    if real is None:
        return None
    try:
        if os.path.islink(os.path.join(os.path.realpath(root), *script_rel.split("/"))):
            return None
        with open(real, "rb") as f:
            head = f.read(tpl.SOURCE_SCAN_CHARS)
    except OSError:
        return None
    meta = tpl.parse_source_comment(head.decode("utf-8", errors="replace"))
    if meta is None:
        return None
    data = _data_path(script_rel, meta["data"])
    if data is None:
        return None
    return {"data": data, "sheet": meta["sheet"]}


def annotate_generated_from(root: str | Path, inventory: list[dict]) -> None:
    """给脚本清单（`probe.script_inventory` 的条目）原地加 `generated_from`——**只给认出来源的脚本加**，其余条目一个字节不变。
    基础设施脚本（公共模块 `tavotto_plot_*`）不读。"""
    read = 0
    for item in inventory:
        script = item.get("script")
        if not isinstance(script, str) or discover.is_infrastructure_name(Path(script).name):
            continue
        if read >= GENERATED_FROM_MAX:
            break
        read += 1
        src = generated_from(root, script)
        if src is not None:
            item["generated_from"] = src
