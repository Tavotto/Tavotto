"""Content check of a conversion's non-figure outputs against the project's old results (issue #1809, layer 2).

For every certain source write the candidate really wrote, compare it with the same-named file that already
exists in the project (the old result of the source script). Nothing here runs R / MATLAB and nothing is
adopted: the candidate files are read inside the validation window, before the redirect area / sandbox goes.

Result per file: `{"status": match | differs | incomparable, "reason"?, "diff_count", "diffs"}`.
`diffs` are at most `MAX_DIFFS` coordinates, never values: `{"sheet", "cell"}` (xlsx), `{"row", "col"}` (csv / tsv),
`{"line"}` (txt / log) or `{"kind": "shape" | "sheets"}`. Reasons are a closed set (`REASONS`).
Raw bytes are never compared, so unrelated metadata (zip timestamps, styles) cannot cause a difference.
The xlsx reader is stdlib-only and ignores the declared `<dimension>` entirely, reading the cells that exist.
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree

MATCH, DIFFERS, INCOMPARABLE = "match", "differs", "incomparable"
REASONS = frozenset(
    {"no_previous", "stale", "format", "too_large", "inputs_unknown", "compare_failed"}
)
MAX_DIFFS = 5
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED = 64 * 1024 * 1024
MAX_CELLS = 400_000
TOTAL_BUDGET_S = 8.0
REL_TOL, ABS_TOL = 1e-9, 1e-12
_TEXT = frozenset({".txt", ".log"})
_DELIMITED = {".csv": ",", ".tsv": "\t"}


class _TooLarge(Exception):
    pass


class _Format(Exception):
    pass


def _result(status: str, reason: str | None = None, diffs: list | None = None, count: int = 0):
    out: dict = {"status": status, "diff_count": count, "diffs": (diffs or [])[:MAX_DIFFS]}
    if reason is not None:
        out["reason"] = reason
    return out


def _incomparable(reason: str) -> dict:
    return _result(INCOMPARABLE, reason)


def _check_time(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise _TooLarge


def _same_number(a: float, b: float) -> bool:
    if math.isnan(a) and math.isnan(b):
        return True
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def _number(text: str) -> float | None:
    t = text.strip()
    if not t or t.lower() in {"inf", "-inf", "+inf", "infinity", "-infinity"}:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _cells_equal(a, b) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        x, y = _number(a), _number(b)
        if x is not None and y is not None:
            return _same_number(x, y)
        return a == b
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, float) and isinstance(b, float):
        return _same_number(a, b)
    return False


def _read_text(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise _TooLarge
    try:
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _Format from exc


def _compare_delimited(old: Path, new: Path, delimiter: str, deadline: float) -> dict:
    grids = []
    for path in (old, new):
        try:
            rows = list(csv.reader(io.StringIO(_read_text(path), newline=""), delimiter=delimiter))
        except csv.Error as exc:
            raise _Format from exc
        if sum(len(r) for r in rows) > MAX_CELLS:
            raise _TooLarge
        grids.append(rows)
    a, b = grids
    width = (max((len(r) for r in a), default=0), max((len(r) for r in b), default=0))
    if len(a) != len(b) or width[0] != width[1]:
        return _result(DIFFERS, diffs=[{"kind": "shape"}], count=1)
    diffs: list[dict] = []
    count = 0
    for i, (ra, rb) in enumerate(zip(a, b, strict=True), 1):
        _check_time(deadline)
        for j in range(width[0]):
            x = ra[j] if j < len(ra) else ""
            y = rb[j] if j < len(rb) else ""
            if not _cells_equal(x, y):
                count += 1
                if len(diffs) < MAX_DIFFS:
                    diffs.append({"row": i, "col": j + 1})
    return _result(DIFFERS, diffs=diffs, count=count) if count else _result(MATCH)


def _compare_lines(old: Path, new: Path, deadline: float) -> dict:
    a = _read_text(old).splitlines()
    b = _read_text(new).splitlines()
    if len(a) != len(b):
        return _result(DIFFERS, diffs=[{"kind": "shape"}], count=1)
    diffs: list[dict] = []
    count = 0
    for i, (x, y) in enumerate(zip(a, b, strict=True), 1):
        _check_time(deadline)
        if x != y:
            count += 1
            if len(diffs) < MAX_DIFFS:
                diffs.append({"line": i})
    return _result(DIFFERS, diffs=diffs, count=count) if count else _result(MATCH)


# ---------------------------------------------------------------- xlsx (stdlib, never trusts <dimension>)
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _column_number(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


_CELL_REF = re.compile(r"^([A-Z]+)([0-9]+)$")


def _zip_member(zf: zipfile.ZipFile, name: str) -> bytes:
    info = zf.getinfo(name)
    if info.file_size > MAX_XLSX_UNCOMPRESSED:
        raise _TooLarge
    return zf.read(name)


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        data = _zip_member(zf, "xl/sharedStrings.xml")
    except KeyError:
        return []
    out: list[str] = []
    for _event, el in ElementTree.iterparse(io.BytesIO(data), events=("end",)):
        if _local(el.tag) != "si":
            continue
        parts = []
        for child in el:  # `t` or rich runs `r/t`; phonetic `rPh` text is not the cell's text
            if _local(child.tag) == "t":
                parts.append(child.text or "")
            elif _local(child.tag) == "r":
                parts.extend((n.text or "") for n in child if _local(n.tag) == "t")
        out.append("".join(parts))
        el.clear()
    return out


def _sheet_targets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    wb = ElementTree.fromstring(_zip_member(zf, "xl/workbook.xml"))
    rels = {}
    try:
        for rel in ElementTree.fromstring(_zip_member(zf, "xl/_rels/workbook.xml.rels")):
            rels[rel.get("Id")] = rel.get("Target") or ""
    except KeyError as exc:
        raise _Format from exc
    sheets = []
    for el in wb.iter():
        if _local(el.tag) != "sheet":
            continue
        rid = next((v for k, v in el.attrib.items() if _local(k) == "id"), None)
        target = rels.get(rid or "", "")
        if not target:
            raise _Format
        path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        sheets.append((el.get("name") or "", os.path.normpath(path).replace(os.sep, "/")))
    return sheets


def _sheet_cells(zf: zipfile.ZipFile, path: str, shared: list[str], deadline: float) -> dict:
    cells: dict[tuple[int, int], object] = {}
    data = _zip_member(zf, path)
    count = 0
    for _event, el in ElementTree.iterparse(io.BytesIO(data), events=("end",)):
        if _local(el.tag) != "c":
            continue
        count += 1
        if count > MAX_CELLS:
            raise _TooLarge
        if count % 4096 == 0:
            _check_time(deadline)
        m = _CELL_REF.match(el.get("r") or "")
        if m is None:
            raise _Format
        kind = el.get("t") or "n"
        value: object = None
        v = next((c for c in el if _local(c.tag) == "v"), None)
        if kind == "inlineStr":
            value = "".join((n.text or "") for n in el.iter() if _local(n.tag) == "t")
        elif v is not None and v.text is not None:
            if kind == "s":
                idx = int(v.text)
                if not 0 <= idx < len(shared):
                    raise _Format
                value = shared[idx]
            elif kind == "b":
                value = v.text.strip() in {"1", "true"}
            elif kind in {"str", "e", "d"}:
                value = v.text
            else:
                value = float(v.text)
        el.clear()
        if value is None or value == "":
            continue
        cells[(int(m.group(2)), _column_number(m.group(1)))] = value
    return cells


def _column_letters(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _compare_xlsx(old: Path, new: Path, deadline: float) -> dict:
    books = []
    for path in (old, new):
        if path.stat().st_size > MAX_FILE_BYTES:
            raise _TooLarge
        try:
            with zipfile.ZipFile(path) as zf:
                shared = _shared_strings(zf)
                books.append(
                    [
                        (name, _sheet_cells(zf, target, shared, deadline))
                        for name, target in _sheet_targets(zf)
                    ]
                )
        except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, ValueError) as exc:
            raise _Format from exc
    a, b = books
    if [n for n, _ in a] != [n for n, _ in b]:
        return _result(DIFFERS, diffs=[{"kind": "sheets"}], count=1)
    diffs: list[dict] = []
    count = 0
    for (name, ca), (_n, cb) in zip(a, b, strict=True):
        _check_time(deadline)
        for key in sorted(ca.keys() | cb.keys()):
            x, y = ca.get(key), cb.get(key)
            if x is None or y is None or not _cells_equal(x, y):
                count += 1
                if len(diffs) < MAX_DIFFS:
                    diffs.append({"sheet": name[:31], "cell": f"{_column_letters(key[1])}{key[0]}"})
    return _result(DIFFERS, diffs=diffs, count=count) if count else _result(MATCH)


def compare_files(old: Path, new: Path, deadline: float) -> dict:
    """Compare two files by content. Never raises: any trouble is an `incomparable` result."""
    suffix = old.suffix.lower()
    try:
        if suffix != new.suffix.lower():
            return _incomparable("format")
        if suffix in _DELIMITED:
            return _compare_delimited(old, new, _DELIMITED[suffix], deadline)
        if suffix in _TEXT:
            return _compare_lines(old, new, deadline)
        if suffix == ".xlsx":
            return _compare_xlsx(old, new, deadline)
        return _incomparable("format")
    except _TooLarge:
        return _incomparable("too_large")
    except _Format:
        return _incomparable("format")
    except Exception:  # noqa: BLE001 - a comparator fault is never a conversion failure
        return _incomparable("compare_failed")


SUPPORTED_SUFFIXES = frozenset({*_DELIMITED, *_TEXT, ".xlsx"})


# ---------------------------------------------------------------- orchestration
def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def old_result(project_root: Path, script_rel: str, literal: str) -> Path | None:
    """The project's existing file for a source write: beside the script first, then the project root."""
    script_dir = Path(script_rel).parent
    for base in (project_root / script_dir, project_root):
        path = base / literal
        try:
            if path.is_file() and not path.is_symlink() and _inside(project_root, path):
                return path
        except OSError:
            continue
    return None


def candidate_file(area: Path, name: str, literal: str) -> Path | None:
    """The file the candidate wrote under the redirect area (project cwd) or its kept sandbox copy."""
    wanted = name.casefold()
    suffix = "/" + literal.replace("\\", "/").casefold()
    hits: list[tuple[bool, str, Path]] = []
    try:
        for dirpath, _dirs, files in os.walk(area):
            for f in files:
                if f.casefold() == wanted:
                    path = Path(dirpath) / f
                    rel = "/" + path.relative_to(area).as_posix().casefold()
                    hits.append((not rel.endswith(suffix), rel, path))
    except OSError:
        return None
    return min(hits)[2] if hits else None


def inputs_state(inputs_read: object) -> int | None:
    """Newest mtime (ns) among the inputs the candidate read, None when the record cannot be trusted."""
    if not isinstance(inputs_read, dict) or inputs_read.get("truncated"):
        return None
    files = inputs_read.get("files")
    if not isinstance(files, list) or not files:
        return None
    stamps = [f.get("mtime_ns") for f in files if isinstance(f, dict)]
    if len(stamps) != len(files) or not all(isinstance(s, int) for s in stamps):
        return None
    return max(stamps)


def check_outputs(
    writes: list[dict],
    *,
    project_root: Path,
    script_rel: str,
    area: Path,
    written: set[str],
    inputs_read: object,
) -> dict[str, dict]:
    """`{name: result}` for the certain writes the candidate wrote. `writes` carry `name`, `path` (literal)."""
    deadline = time.monotonic() + TOTAL_BUDGET_S
    newest_input = inputs_state(inputs_read)
    out: dict[str, dict] = {}
    for w in writes:
        name = w["name"]
        if name.casefold() not in written:
            continue
        try:
            old = old_result(project_root, script_rel, w.get("path") or name)
            if old is None:
                out[name] = _incomparable("no_previous")
                continue
            if old.suffix.lower() not in SUPPORTED_SUFFIXES:
                out[name] = _incomparable("format")
                continue
            if newest_input is None:
                out[name] = _incomparable("inputs_unknown")
                continue
            if old.stat().st_mtime_ns <= newest_input:
                out[name] = _incomparable("stale")
                continue
            new = candidate_file(area, name, w.get("path") or name)
            if new is None or time.monotonic() > deadline:
                out[name] = _incomparable("too_large")
                continue
            out[name] = compare_files(old, new, deadline)
        except Exception:  # noqa: BLE001
            out[name] = _incomparable("compare_failed")
    return out
