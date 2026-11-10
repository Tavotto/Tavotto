"""Source outputs that are certain enough to be checked after conversion (issue #1809).

R / MATLAB sources are never run. This module reads their text only, and keeps just
the writes whose path is a *single string literal* in a plain top-level statement:
no concatenation, variable, helper, loop, branch or function body. Everything else
stays unknown and never gates a conversion. Graphics calls (`ggsave`, `png`,
`saveas` ...) are deliberately absent: the figure capture already covers them.

`written_names` reads what the isolated validation run really wrote (the #1516
redirect area, left empty by a deleted or failed write), and `missing` compares.
Both stay name-level: content and calculation semantics remain unverified.
"""

from __future__ import annotations

import bisect
import os
import re
from pathlib import Path

from .fixtools import readscope

MAX_CALLS = 512
MAX_WRITTEN_FILES = 20000
_MASK = "\x01"

# name -> (index among unnamed args, accepted argument names)
_R_WRITERS: dict[str, tuple[int | None, tuple[str, ...]]] = {
    "write.csv": (1, ("file",)),
    "write.csv2": (1, ("file",)),
    "write.table": (1, ("file",)),
    "write_csv": (1, ("file", "path")),
    "write_csv2": (1, ("file", "path")),
    "write_tsv": (1, ("file", "path")),
    "write_delim": (1, ("file", "path")),
    "write_excel_csv": (1, ("file", "path")),
    "write_excel_csv2": (1, ("file", "path")),
    "write_xlsx": (1, ("path",)),
    "write.xlsx": (1, ("file",)),
    "write.xlsx2": (1, ("file",)),
    "saveWorkbook": (1, ("file",)),
    "saveRDS": (1, ("file",)),
    "write_rds": (1, ("file", "path")),
    "write_json": (1, ("path",)),
    "writeLines": (1, ("con",)),
    "fwrite": (1, ("file",)),
    "write": (1, ("file",)),
    "dput": (1, ("file",)),
    "sink": (0, ("file",)),
    "save.image": (0, ("file",)),
    "save": (None, ("file",)),
    "cat": (None, ("file",)),
}
_R_DATA_FORMALS = frozenset({"x", "object", "text", "wb", "data", "df", "list"})
# name -> (index of the path argument, index of a write-mode argument | None)
_M_WRITERS: dict[str, tuple[int, int | None]] = {
    "writetable": (1, None),
    "writetimetable": (1, None),
    "writematrix": (1, None),
    "writecell": (1, None),
    "writelines": (1, None),
    "writestruct": (1, None),
    "writevars": (0, None),
    "xlswrite": (0, None),
    "csvwrite": (0, None),
    "dlmwrite": (0, None),
    "save": (0, None),
    "diary": (0, None),
    "fopen": (0, 1),
}
_R_BLOCK_WORDS = re.compile(r"\b(?:if|else|for|while|repeat|function)\b|&&|\|\||\?")
_R_BLOCK_TAIL = re.compile(
    r"(?:\bif\s*\(.*\)|\belse|\bfor\s*\(.*\)|\bwhile\s*\(.*\)|\brepeat|\bfunction\s*\(.*\))\s*$"
)
_M_OPEN = re.compile(r"\b(?:if|for|while|switch|try|parfor|spmd)\b")
_M_STOP = re.compile(r"\b(?:function|classdef)\b")
_M_END = re.compile(r"\bend\b")


def _mask_r(text: str) -> str:
    """Blank comments and string contents, keeping offsets and quotes."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "#":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
        elif ch in "'\"":
            quote, i = ch, i + 1
            while i < n and text[i] != quote:
                if text[i] == "\\" and i + 1 < n:
                    out[i] = _MASK
                    i += 1
                if text[i] != "\n":
                    out[i] = _MASK
                i += 1
            i += 1
        else:
            i += 1
    return "".join(out)


def _blank_block_comments(text: str) -> str:
    """`%{` ... `%}` on their own lines: blank them, keeping offsets."""
    out, inside = [], False
    for line in text.split("\n"):
        mark = line.strip()
        if mark == "%{":
            inside = True
        keep = inside or mark == "%}"
        out.append(" " * len(line) if keep else line)
        if mark == "%}":
            inside = False
    return "\n".join(out)


def _mask_matlab(text: str) -> str:
    text = _blank_block_comments(text)
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "%" or text.startswith("...", i):
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        prev = text[:i].rstrip(" \t")[-1:] if i else ""
        if ch == '"' or (ch == "'" and not (prev.isalnum() or prev in "_)]}'.")):
            quote, i = ch, i + 1
            while i < n and text[i] != "\n":
                if text[i] == quote:
                    if i + 1 < n and text[i + 1] == quote:
                        out[i] = out[i + 1] = _MASK
                        i += 2
                        continue
                    break
                out[i] = _MASK
                i += 1
            i += 1
            continue
        i += 1
    return "".join(out)


def _split_args(masked: str, open_at: int) -> list[tuple[int, int]] | None:
    """Spans of the top-level arguments of the call whose `(` is at `open_at`."""
    depth, start, spans = 0, open_at + 1, []
    for i in range(open_at, len(masked)):
        ch = masked[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                if masked[start:i].strip():
                    spans.append((start, i))
                return spans
        elif ch == "," and depth == 1:
            spans.append((start, i))
            start = i + 1
    return None


def _literal(text: str, masked: str, span: tuple[int, int]) -> str | None:
    """The value of an argument that is exactly one string literal, else None."""
    lo, hi = span
    while lo < hi and masked[lo].isspace():
        lo += 1
    while hi > lo and masked[hi - 1].isspace():
        hi -= 1
    if hi - lo < 2 or masked[lo] not in "'\"" or masked[hi - 1] != masked[lo]:
        return None
    if masked[lo] in masked[lo + 1 : hi - 1]:
        return None
    return text[lo + 1 : hi - 1]


def _usable(value: str) -> str | None:
    """A relative, in-project literal whose basename may be disclosed, else None."""
    if not value or "\x00" in value or "\n" in value or len(value) > 1024:
        return None
    flat = value.replace("\\", "/")
    if flat.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", flat):
        return None
    if ".." in flat.split("/") or any(c in flat for c in "*?<>|$%{}"):
        return None
    name = flat.rsplit("/", 1)[-1]
    if not name or len(name) > 128 or any(ord(ch) < 32 for ch in name):
        return None
    if "." not in name.strip("."):
        return None
    clean = readscope.scrub(name)
    return clean if clean == name else None


def _rel(literal: str) -> str:
    flat = literal.replace("\\", "/")
    while flat.startswith("./"):
        flat = flat[2:]
    return flat


def _named(masked: str, span: tuple[int, int]) -> tuple[str | None, tuple[int, int]]:
    m = re.match(r"\s*([A-Za-z_.][\w.]*)\s*=(?!=)", masked[span[0] : span[1]])
    if m is None:
        return None, span
    return m.group(1), (span[0] + m.end(), span[1])


def _r_writes(text: str) -> list[tuple[str, int, str]]:
    masked = _mask_r(text)
    found: list[tuple[str, int, str]] = []
    names = "|".join(re.escape(n) for n in sorted(_R_WRITERS, key=len, reverse=True))
    pattern = re.compile(rf"(?<![\w.$@])(?:[A-Za-z][\w.]*::)?({names})\s*\(")
    for count, m in enumerate(pattern.finditer(masked)):
        if count >= MAX_CALLS:
            break
        start = m.start()
        before = masked[:start]
        if before.count("{") != before.count("}") or before.count("(") != before.count(")"):
            continue
        line_start = before.rfind("\n") + 1
        if _R_BLOCK_WORDS.search(before[line_start:]):
            continue
        earlier = before[:line_start].rstrip()
        if _R_BLOCK_TAIL.search(earlier[earlier.rfind("\n") + 1 :]):
            continue
        spans = _split_args(masked, m.end() - 1)
        if spans is None:
            continue
        index, keys = _R_WRITERS[m.group(1)]
        value: tuple[int, int] | None = None
        unnamed: list[tuple[int, int]] = []
        shift = 0
        for span in spans:
            key, rest = _named(masked, span)
            if key in keys:
                value = rest
                break
            if key is None:
                unnamed.append(span)
            elif key in _R_DATA_FORMALS:
                shift += 1
        if value is None and index is not None:
            if before.rstrip().endswith(("%>%", "|>")):
                shift += 1
            at = index - shift
            if 0 <= at < len(unnamed):
                value = unnamed[at]
        if value is None:
            continue
        literal = _literal(text, masked, value)
        name = _usable(literal) if literal is not None else None
        if name is not None:
            found.append((name, text.count("\n", 0, start) + 1, _rel(literal)))
    return found


def _matlab_flat(masked: str) -> tuple[str, list[int]]:
    """Blank bracket contents (keeping offsets) and record bracket depth per char."""
    out, depth, depths = list(masked), 0, []
    for i, ch in enumerate(masked):
        if ch in "([{":
            depths.append(depth)
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
            depths.append(depth)
        else:
            depths.append(depth)
            if depth and ch != "\n":
                out[i] = " "
    return "".join(out), depths


def _matlab_writes(text: str) -> list[tuple[str, int, str]]:
    masked = _mask_matlab(text)
    flat, depths = _matlab_flat(masked)
    stop = _M_STOP.search(flat)
    limit = stop.start() if stop else len(flat)
    deltas = sorted(
        [(m.start(), 1) for m in _M_OPEN.finditer(flat, 0, limit)]
        + [(m.start(), -1) for m in _M_END.finditer(flat, 0, limit)]
    )
    positions = [p for p, _ in deltas]
    running, total = [], 0
    for _, d in deltas:
        total += d
        running.append(total)
    names = "|".join(sorted(_M_WRITERS, key=len, reverse=True))
    pattern = re.compile(rf"(?<![\w.@$])({names})\s*\(")
    found: list[tuple[str, int, str]] = []
    for count, m in enumerate(pattern.finditer(masked, 0, limit)):
        if count >= MAX_CALLS:
            break
        start = m.start()
        k = bisect.bisect_left(positions, start)
        if depths[start] or (running[k - 1] if k else 0):
            continue
        spans = _split_args(masked, m.end() - 1)
        if spans is None:
            continue
        path_at, mode_at = _M_WRITERS[m.group(1)]
        if path_at >= len(spans):
            continue
        if mode_at is not None:
            mode = _literal(text, masked, spans[mode_at]) if mode_at < len(spans) else None
            if mode is None or not re.match(r"^[waWA]", mode):
                continue
        literal = _literal(text, masked, spans[path_at])
        literal = literal.replace("''", "'") if literal is not None else None
        name = _usable(literal) if literal is not None else None
        if name is not None:
            found.append((name, text.count("\n", 0, start) + 1, _rel(literal)))
    return found


def certain_writes(source: bytes, language: str) -> list[dict]:
    """`[{"name": basename, "line": n, "path": relative literal}]` (`path` stays internal): literal-path write calls in plain top-level statements.

    Only R / MATLAB sources are read; any other language, an undecodable file or a
    dynamic path yields nothing, which means *unknown* (never "no outputs")."""
    if language not in ("r", "matlab"):
        return []
    text = source.decode("utf-8", errors="replace")
    triples = _r_writes(text) if language == "r" else _matlab_writes(text)
    first: dict[str, tuple[int, str]] = {}
    for name, line, rel in triples:
        first.setdefault(name, (line, rel))
    return [
        {"name": n, "line": first[n][0], "path": first[n][1]}
        for n in sorted(first, key=lambda n: (first[n][0], n))
    ]


def written_names(area: Path) -> set[str] | None:
    """Casefolded basenames of files that really exist under the redirect area.

    None when the area cannot be listed completely (then nothing is judged)."""
    names: set[str] = set()
    count = 0
    try:
        for _dir, _dirs, files in os.walk(area):
            for name in files:
                count += 1
                if count > MAX_WRITTEN_FILES:
                    return None
                names.add(name.casefold())
    except OSError:
        return None
    return names


def missing(writes: list[dict], written: set[str]) -> list[dict]:
    return [w for w in writes if w["name"].casefold() not in written]
