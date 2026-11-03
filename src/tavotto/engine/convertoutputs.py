"""Give generated conversion outputs a stable identity without taking an owner's stem.

Only the new candidate is changed, before its real validation. A call-shaped AST
rewrite preserves expressions and evaluation order; the emitted wrapper checks
the actual Matplotlib callable at runtime, including the current capture hook.
Other objects' savefig methods receive their original arguments unchanged.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize


def helper_source(name: str, suffix: str) -> str:
    """Standalone code: the adopted script does not need Tavotto installed.

    Path classification mirrors figcapture.savefig_targets_path (str/bytes/
    PathLike; streams remain streams). Tests exercise that existing contract.
    """
    return f"""# Independent output names for this editable version; original outputs are preserved.
def {name}(_savefig, /, *args, **kwargs):
    import os as _os
    from matplotlib.figure import Figure as _Figure
    import matplotlib.pyplot as _pyplot

    _owner = getattr(_savefig, "__self__", None)
    _is_figure = isinstance(_owner, _Figure) and getattr(_savefig, "__func__", None) is _Figure.savefig
    if _savefig is _pyplot.savefig or _is_figure:
        def _output(_value):
            if not isinstance(_value, (str, bytes, _os.PathLike)):
                return _value
            _path = _os.fsdecode(_value)
            _base, _ext = _os.path.splitext(_path)
            if not _base.endswith({suffix!r}):
                _base += {suffix!r}
            return _base + _ext
        if args:
            args = (_output(args[0]), *args[1:])
        elif "fname" in kwargs:
            kwargs["fname"] = _output(kwargs["fname"])
    return _savefig(*args, **kwargs)

"""


def pdf_helper_source(name: str, suffix: str) -> str:
    return f"""# Keep multi-page PDF output separate from the original script's file.
def {name}(_constructor, /, *args, **kwargs):
    import os as _os
    from matplotlib.backends.backend_pdf import PdfPages as _PdfPages

    if _constructor is _PdfPages:
        def _output(_value):
            if not isinstance(_value, (str, bytes, _os.PathLike)):
                return _value
            _base, _ext = _os.path.splitext(_os.fsdecode(_value))
            if not _base.endswith({suffix!r}):
                _base += {suffix!r}
            return _base + _ext
        if args:
            args = (_output(args[0]), *args[1:])
        elif "filename" in kwargs:
            kwargs["filename"] = _output(kwargs["filename"])
    return _constructor(*args, **kwargs)

"""


def _aliases(tree: ast.AST, method: str, module: str) -> set[str]:
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            aliases.update(a.asname or a.name for a in node.names if a.name == method)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, ast.Attribute) and value.attr == method:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                aliases.update(t.id for t in targets if isinstance(t, ast.Name))
    return aliases


def _save_call(func: ast.AST, aliases: set[str], method: str) -> bool:
    if isinstance(func, ast.Attribute):
        return func.attr == method
    if isinstance(func, ast.Name):
        return func.id in aliases
    return (
        isinstance(func, ast.Call)
        and isinstance(func.func, ast.Name)
        and func.func.id == "getattr"
        and len(func.args) >= 2
        and isinstance(func.args[1], ast.Constant)
        and func.args[1].value == method
    )


def normalize(data: bytes, sid: str) -> bytes:
    """Idempotent source-preserving rewrite; no user project is read or changed."""
    # A stream passed to Figure.savefig has already been opened. Give PdfPages
    # its identity at construction, before it can truncate an original PDF.
    return _normalize(_normalize(data, sid, pdf_pages=True), sid, pdf_pages=False)


def _normalize(data: bytes, sid: str, *, pdf_pages: bool) -> bytes:
    if re.fullmatch(r"[0-9a-f]{12}", sid) is None:
        raise ValueError("invalid_output_identity")
    text = data.decode("utf-8-sig")
    raw = text.encode("utf-8")
    tree = ast.parse(raw)
    kind = "pdfpages" if pdf_pages else "savefig"
    name = f"_tavotto_editable_{kind}_{sid}"
    suffix = f"_matplotlib_{sid}"
    helper = (pdf_helper_source if pdf_pages else helper_source)(name, suffix)
    canonical = ast.parse(helper).body[0]
    existing = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None
    )
    if existing is not None and ast.dump(existing) != ast.dump(canonical):
        raise ValueError("output_helper_changed")
    method = "PdfPages" if pdf_pages else "savefig"
    module = "matplotlib.backends.backend_pdf" if pdf_pages else "matplotlib.pyplot"
    aliases = _aliases(tree, method, module)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and _save_call(n.func, aliases, method)
        and not (existing is not None and existing.lineno <= n.lineno <= existing.end_lineno)
    ]
    if not calls:
        return data
    offsets = [0]
    for line in raw.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))

    def offset(line, column):
        return offsets[line - 1] + column

    edits = []
    for call in calls:
        func = call.func
        start = offset(func.lineno, func.col_offset)
        end = offset(func.end_lineno, func.end_col_offset)
        tail_end = offset(call.end_lineno, call.end_col_offset)
        tail = raw[end:tail_end].decode("utf-8")
        # The real call opening parenthesis, ignoring comments and redundant
        # parentheses around the callable. tokenize positions are characters.
        token = next(
            t
            for t in tokenize.generate_tokens(io.StringIO(tail).readline)
            if t.type == tokenize.OP and t.string == "("
        )
        before = "".join(tail.splitlines(keepends=True)[: token.start[0] - 1])
        before += tail.splitlines(keepends=True)[token.start[0] - 1][: token.start[1] + 1]
        opening_end = end + len(before.encode("utf-8"))
        edits += [(start, end, name.encode()), (opening_end, opening_end, raw[start:end] + b", ")]
    # Nested callable expressions cannot safely share an edited span. Leave a
    # concrete failure for correction instead of guessing the original program.
    ordered = sorted(edits)
    if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
        raise ValueError("overlapping_savefig_calls")
    for start, end, replacement in reversed(ordered):
        raw = raw[:start] + replacement + raw[end:]
    if existing is None:
        at = 0
        for node in tree.body:
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and at == 0
            ):
                at = offsets[node.end_lineno]
            elif isinstance(node, ast.ImportFrom) and node.module == "__future__":
                at = offsets[node.end_lineno]
            else:
                break
        raw = raw[:at] + b"\n" + helper.encode() + raw[at:]
    ast.parse(raw)  # Never send malformed generated syntax to a runner.
    return raw
