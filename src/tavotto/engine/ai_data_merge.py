"""TAF 数据出图 v2「合画」（ADR 0178 2026-11-18 修订）：几份**列名与顺序都相同**的数据（每份文件是一组）拼成一张逻辑表，再走原来的出备选流水。
纯标准库，Flask 父进程 import；**不写任何文件、不执行任何东西**。

定义（一处，下面是实现）：

* 逐份读：每份各按单份的读法（`adf.inspect_data_source(v2=True)`：编码 / 分隔符 / 表头行 / 单位行 / 千分位 / XLSX 工作表）读成自己的摘要；
  用户指定的表头行（`header_rows`）按文件各管各的。各份的列名集合**与顺序**必须一致（`tables_columns_differ`）。
* 拼：**一份实现**——公共模块里的运行时函数 `ai_data_template.merge_tables`（脚本与父进程调同一个）：按给定顺序逐份拼行，表头末尾加一列合成的分组列，
  值是各份的「文件标签」。拼完的摘要用同一个 `adf._summarize` 判（列类型 / 分组 / 布局都按拼好的表判，没有第二套分类器）。
* 夹着文字格的数值列的处理规则（`FigureRecipe.cells`）在拼好之后套：规则逐格独立，所以等于「各份各自套规则再拼」。
* 合成列名：默认 `file`，与现有列重名（不区分大小写）就加数字后缀 `file_2`、`file_3` …（字母后缀会被当成单位后缀，`_id` 会被当成编号列）。
* 文件标签：文件名去扩展名；同名时依次带上工作表名、父文件夹（逐级往上）、完整文件名（含扩展名）区分，最后兜底 `#2`；标签本身是数 / 日期 / `nan`
  会让合成列不再是「分组」，这时整批改用完整文件名（含扩展名）。保证唯一（不区分大小写）。
* 脚本落点：这几份的**最近公共父文件夹**（相对所选数据文件夹；仍在项目内）下的 `tavotto_figures/`；脚本里的数据路径是 `../<相对落点>`。
* 上限：至多 `MAX_TABLES` 份（超了 `tables_too_many`）；合计字节 ≤ `MAX_TOTAL_BYTES`（超了沿用 `data_too_large`）；拼后行数 / 列数沿用单份的上限。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from . import ai_data_figure as adf, ai_data_template as tpl

#: 一次最多合画几份。取 8 = 配方里颜色分组的上限（`ai_data_recipe.MAX_GROUP_LEVELS`）：分组列就是文件，超过它趋势 / 关系图的颜色分组画不了
MAX_TABLES = 8
#: 各份文件字节数之和的上限（每份仍受 `adf.MAX_BYTES`）：候选目录里同一会话放这几份的字节副本，磁盘上界按它算
MAX_TOTAL_BYTES = 4 * adf.MAX_BYTES
COLUMN_BASE = "file"
_LABEL_MAX = 100
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class MergeError(ValueError):
    """合画的结构化失败（`ai_data_propose` 转成 `ProposeError`）：`code` 是 `tables_columns_differ` / `tables_too_many` / `tables_changed`。"""

    def __init__(self, code: str, params: dict | None = None):
        super().__init__(code)
        self.code = code
        self.params = dict(params or {})


@dataclass(frozen=True)
class Source:
    """合画里的一份数据。`path` 是请求里的相对路径（相对所选数据文件夹），`rel` 是相对落点目录的 POSIX 路径（脚本里写 `../<rel>`）。
    `entry` 是这一份**自己的**读法（不带行数据）：脚本里这一份的读表表达式只取自它。"""

    path: str
    rel: str
    sheet: str | None
    label: str
    sha256: str
    size: int
    header_line: int | None
    entry: adf.TableEntry | None = None

    @property
    def data_rel(self) -> str:
        return "../" + self.rel


@dataclass(frozen=True)
class MergeSpec:
    sources: tuple[Source, ...]
    column: str
    landing: str  # 落点目录，相对所选数据文件夹（"" = 数据文件夹本身）

    @property
    def sha256(self) -> str:
        """拼表的「数据身份」：各份 sha256 按顺序连起来再取一次。任何一份变了它就变。"""
        return hashlib.sha256("\n".join(s.sha256 for s in self.sources).encode("ascii")).hexdigest()

    @property
    def size(self) -> int:
        return sum(s.size for s in self.sources)


@dataclass(frozen=True)
class MergedEntry(adf.TableEntry):
    """拼好的那张逻辑表：`path` / `sheet` 是第一份的（`recipe.table` 指它），`summary` 是拼好之后的摘要，`merge` 说明它是怎么拼的。"""

    merge: MergeSpec | None = None


# --------------------------------------------------------------------------- 纯函数：落点、标签、列名
def common_parent(paths: list[str]) -> str:
    """几个相对 POSIX 路径的最近公共父文件夹（相对同一个根；没有公共部分 = 根 = `""`）。"""
    dirs = [PurePosixPath(p).parent.parts for p in paths]
    prefix: list[str] = []
    for parts in zip(*dirs):
        if len(set(parts)) != 1:
            break
        prefix.append(parts[0])
    return "/".join(prefix)


def rebase(path: str, landing: str) -> str:
    """请求相对路径 → 相对落点目录的路径（落点是它的某个祖先）。"""
    return path if not landing else PurePosixPath(path).relative_to(landing).as_posix()


def _clean(text: str) -> str:
    t = _CONTROL.sub(" ", text).strip()
    return t[:_LABEL_MAX]


def _plain_text(label: str) -> bool:
    """标签作为单元格会被当成文字（不是数 / 日期 / nan / 空）。"""
    return bool(label) and adf.parse_cell(label)[0] == "text" and adf.parse_date(label) is None


def unique_labels(items: list[tuple[str, str | None]]) -> list[str]:
    """`[(路径, 工作表)]` → 每份一个**互不相同**（不区分大小写）、且作为单元格是纯文字的标签；规则见模块文档。"""
    paths = [PurePosixPath(p) for p, _ in items]
    shared = {p for p in paths if sum(1 for q in paths if q == p) > 1}  # 同一个工作簿的多张表

    def base(i: int, ext: bool) -> str:
        name = paths[i].name if ext else paths[i].stem
        if paths[i] in shared and items[i][1]:
            name = f"{name} ({items[i][1]})"
        return _clean(name)

    def cand(i: int, k: int, ext: bool) -> str:
        parents = paths[i].parent.parts
        parts = [*(parents[-k:] if k else ()), base(i, ext)]
        return _clean("/".join(parts)) if k else base(i, ext)

    use_ext_first = not all(_plain_text(base(i, False)) for i in range(len(items)))
    depth = max((len(p.parent.parts) for p in paths), default=0)
    labels = [base(i, use_ext_first) for i in range(len(items))]

    def conflicts() -> list[int]:
        count: dict[str, int] = {}
        for lab in labels:
            count[lab.casefold()] = count.get(lab.casefold(), 0) + 1
        return [i for i, lab in enumerate(labels) if count[lab.casefold()] > 1]

    stages = (
        [(k, True) for k in range(1, depth + 1)]
        if use_ext_first
        else [(k, False) for k in range(1, depth + 1)] + [(k, True) for k in range(0, depth + 1)]
    )
    for k, ext in stages:  # 只改还重名的那几份：先带父文件夹，再带完整文件名
        bad = conflicts()
        if not bad:
            break
        for i in bad:
            labels[i] = cand(i, k, ext)
    bad = conflicts()
    if bad:  # 兜底：同一标签的按出现顺序加 #2、#3 …
        seen: dict[str, int] = {}
        for i, lab in enumerate(labels):
            n = seen.get(lab.casefold(), 0) + 1
            seen[lab.casefold()] = n
            if n > 1 and i in bad:
                labels[i] = f"{lab} #{n}"
    return labels


def column_name(columns: list[str]) -> str:
    """合成列名：`file`，重名（不区分大小写）就 `file_2`、`file_3` …"""
    taken = {c.casefold() for c in columns}
    name, n = COLUMN_BASE, 1
    while name.casefold() in taken:
        n += 1
        name = f"{COLUMN_BASE}_{n}"
    return name


# --------------------------------------------------------------------------- 拼
def _entry_of(source: Source, values: adf.DataSummary) -> adf.TableEntry:
    """这一份自己的读法（不带行数据）。`path` 取相对落点的路径（只用它的后缀判 XLSX）。"""
    slim = replace(values, rows=[], row_numbers=[], row_count=len(values.rows))
    return adf.TableEntry(source.rel, values.sheet, False, slim)


def check_same_columns(summaries: list[adf.DataSummary]) -> None:
    first = summaries[0].columns
    for i, s in enumerate(summaries[1:], start=1):
        if s.columns != first:
            raise MergeError("tables_columns_differ", {"index": i})


def build(
    sources: list[Source],
    summaries: list[adf.DataSummary],
    landing: str,
    column: str | None = None,
) -> tuple[MergedEntry, adf.DataSummary]:
    """各份摘要（带行数据）→ `(拼好的条目, 拼好的带行数据摘要)`。表头不一致 `MergeError`；拼后行数 / 列数超限抛 `DataSourceError`（`_summarize` 的原码）。
    `sources` 的 `entry` 在这里按各份摘要补上；`landing` 是落点目录（相对所选数据文件夹）。"""
    check_same_columns(summaries)
    column = column or column_name(summaries[0].columns)
    filled = tuple(
        replace(src, entry=_entry_of(src, vals), sheet=vals.sheet)
        for src, vals in zip(sources, summaries, strict=True)
    )
    header, rows = tpl.merge_tables(
        [(v.columns, v.rows) for v in summaries], [s.label for s in filled], column
    )
    numbers = [0] + [v.row_number(i) for v in summaries for i in range(len(v.rows))]
    spec = MergeSpec(filled, column, landing)
    values = adf._summarize(
        [header, *rows],
        path_name=summaries[0].path_name,
        sha256=hashlib.sha256("\n".join(s.sha256 for s in filled).encode("ascii")).hexdigest(),
        size=sum(s.size for s in filled),
        sheet=None,
        row_numbers=numbers,
        parse=adf.parse_cell,
    )
    values = replace(
        values, row_sources=[s.label for s, v in zip(filled, summaries) for _ in v.rows]
    )
    slim = replace(values, rows=[], row_numbers=[], row_count=len(values.rows), row_sources=[])
    first = filled[0]
    return MergedEntry(first.rel, first.sheet, False, slim, spec), values


def load(landing_dir: Path, spec: MergeSpec) -> tuple[MergedEntry, adf.DataSummary]:
    """提交时重读：每份按出备选时的读法（工作表 / 表头行）从落点目录读，sha256 必须还是出备选时那一份（否则 `MergeError("tables_changed")`），
    再按**同一组标签与列名**拼。纯读。"""
    summaries = []
    for src in spec.sources:
        vals = adf.inspect_data_source(
            landing_dir / PurePosixPath(src.rel), src.sheet, v2=True, header_row=src.header_line
        )
        if vals.sha256 != src.sha256:
            raise MergeError("tables_changed")
        summaries.append(vals)
    return build(list(spec.sources), summaries, spec.landing, spec.column)


__all__ = [
    "COLUMN_BASE",
    "MAX_TABLES",
    "MAX_TOTAL_BYTES",
    "MergeError",
    "MergeSpec",
    "MergedEntry",
    "Source",
    "build",
    "check_same_columns",
    "column_name",
    "common_parent",
    "load",
    "rebase",
    "unique_labels",
]
