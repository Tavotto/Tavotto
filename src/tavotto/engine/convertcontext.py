"""Bounded, read-only dependency evidence for conversion (ADR 0143).

This is a nomination pass, not a proof that every runtime input was found. Python
module resolution belongs to importscan; sensitive paths/content to readscope.
No project walk, imports, foreign-language execution, or links are used to copy
resources. The same manifest governs the snapshot and later source checks.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from . import ai_figure_edit, importscan, scanbudget
from .fixtools import readscope

DIRECTORY = "project_context"
MANIFEST = "context.json"
MAX_FILES = 64
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_BYTES = 16 * 1024 * 1024
MAX_DEPTH = 4
MAX_REFERENCES = 512
MAX_SELECTED_FILES = 16
_SOURCE_EXT = frozenset({".py", ".r", ".m"})
_STRINGS = re.compile(r"'((?:[^'\n]|'')*)'|\"((?:[^\"\n]|\"\")*)\"")
_CALL = re.compile(r"\b([A-Za-z]\w*)\s*\(")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path(root: Path, path: Path) -> tuple[Path | None, str]:
    """Reject redirects before any operation which could follow one (also Windows junctions)."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None, "outside_project"
    current = root
    for part in rel.parts:
        current = current.parent if part == ".." else current / part
        try:
            current.relative_to(root)
        except ValueError:
            return None, "outside_project"
        if scanbudget.redirected_component(root, current):
            return None, "redirect_or_parent_path"
    path = current
    rel = path.relative_to(root)
    if any(part.startswith(".") for part in rel.parts):
        return None, "hidden_path"
    for parent in [path.parent, *path.parent.parents]:
        if parent == root:
            break
        if ai_figure_edit.is_python_env_dir(parent):
            return None, "environment"
    return path, ""


def _read(root: Path, path: Path, scope: readscope.ReadScope) -> tuple[bytes | None, str]:
    path, reason = _path(root, path)
    if path is None:
        return None, reason
    if not scope.check(str(path)).ok:
        return None, "sensitive_path"
    try:
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or scanbudget.is_placeholder(st):
            return None, "not_local_regular_file"
        if st.st_size > MAX_FILE_BYTES:
            return None, "file_budget"
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                # Windows stat reports creation time while fstat can report
                # change time (CPython #157671). Compare that field only within
                # the same API below; file identity, size and mtime still agree.
                or _identity(opened, change_time=os.name != "nt")
                != _identity(st, change_time=os.name != "nt")
                or scanbudget.redirected_component(root, path)
            ):
                return None, "not_local_regular_file"
            data = handle.read(MAX_FILE_BYTES + 1)
            if (
                _identity(os.fstat(handle.fileno())) != _identity(opened)
                or scanbudget.redirected_component(root, path)
                or _identity(path.lstat()) != _identity(st)
            ):
                return None, "resource_changed"
        if len(data) > MAX_FILE_BYTES:
            return None, "file_budget"
    except OSError:
        return None, "unreadable"
    return data, ""


def _identity(st: os.stat_result, *, change_time: bool = True) -> tuple:
    stable = st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns
    return (*stable, st.st_ctime_ns) if change_time else stable


def _disclosed(data: bytes) -> bytes:
    """Reuse the existing model-text privacy policy without discarding a whole helper.

    A scrubbed comment (for example an author's email) does not make all of the
    surrounding source unavailable. The manifest distinguishes the original
    bytes from this read-only evidence; it is never run or adopted as a helper.
    """
    try:
        return readscope.scrub(data.decode("utf-8")).encode("utf-8")
    except UnicodeError:
        return data


def _references(text: str, suffix: str) -> tuple[list[str], list[str]]:
    if suffix == ".py":
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            return [], []
        literals = [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        # Reuse importscan's __file__/dirname/Path/join expression grammar. These
        # remain nominations, not another import or filesystem resolver.
        for node in ast.walk(tree):
            parts = importscan._literal_dir(node)
            if parts:
                literals.append(str(Path(*parts)))
        return literals, []
    # Foreign source is never executed; these are only literal-file/helper nominations.
    literals = [a.replace("''", "'") or b.replace('""', '"') for a, b in _STRINGS.findall(text)]
    calls = _CALL.findall(_STRINGS.sub(" ", text)) if suffix == ".m" else []
    return literals, calls


def selected_paths(root: Path, value: object) -> tuple[str, ...]:
    """Validate explicit project references through the same bounded read policy.

    An explicit selection must be available in full; silently omitting it would
    send the agent back into the same missing-evidence loop.
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > MAX_SELECTED_FILES:
        raise ValueError("convert_reference_invalid")
    root = root.absolute()
    scope = readscope.ReadScope(home=Path.home(), project_root=root)
    paths: list[str] = []
    total = 0
    for item in value:
        if (
            not isinstance(item, str)
            or not item.strip()
            or len(item) > 1024
            or any(ord(c) < 32 for c in item)
            or Path(item).is_absolute()
        ):
            raise ValueError("convert_reference_invalid")
        path, _ = _path(root, root / item)
        if path is None:
            raise ValueError("convert_reference_invalid")
        rel = path.relative_to(root).as_posix()
        if rel in paths:
            continue
        data, _ = _read(root, path, scope)
        if data is None:
            raise ValueError("convert_reference_invalid")
        total += max(len(data), len(_disclosed(data)))
        if total > MAX_BYTES:
            raise ValueError("convert_reference_invalid")
        paths.append(rel)
    return tuple(paths)


def snapshot(
    root: Path,
    script_rel: str,
    source: bytes,
    destination: Path,
    *,
    reference_files: tuple[str, ...] = (),
) -> dict:
    from . import inputremap

    root = root.absolute()
    selected = selected_paths(root, reference_files)
    scope = readscope.ReadScope(home=Path.home(), project_root=root)
    entry = Path(script_rel)
    # The model must know why a runtime input can be readable even though it is
    # absent from this project-only snapshot. Do not disclose mapping targets or
    # copy external files. Bind the fact to the same rules used by the worker.
    with inputremap.project_mutex(root):
        mapping_fingerprint = inputremap.fingerprint(root)
    manifest: dict = {
        "version": 1,
        "source": entry.as_posix(),
        "directory": DIRECTORY,
        "coverage": "static_partial",
        "input_remap_fingerprint": mapping_fingerprint,
        "input_read_policy": {
            "project_mappings_configured": bool(mapping_fingerprint),
            "mapping_applies_to": "missing_read_open",
            "filesystem_probes_remapped": False,
            "target_availability": "verify_by_reading_in_application",
        },
        "user_selected": list(selected),
        "files": [],
        "omitted": [],
        "limitations": [
            "Dynamic paths, directory enumeration and external resources are not resolved."
        ],
    }
    pending: list[tuple[Path, int]] = []
    seen = {entry.as_posix()}
    total = 0
    references = 0

    def nominate(path: Path, depth: int) -> None:
        nonlocal references
        if references >= MAX_REFERENCES:
            return
        references += 1
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            return  # Never expose an external path in the resource manifest.
        if rel not in seen:
            seen.add(rel)
            pending.append((path, depth))

    def scan(path: Path, data: bytes, depth: int) -> None:
        if path.suffix.lower() not in _SOURCE_EXT:
            return
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            return
        literals, calls = _references(text, path.suffix.lower())
        for literal in literals:
            if not literal or "\x00" in literal or "\n" in literal or len(literal) > 1024:
                continue
            p = Path(literal)
            for base in (path.parent, root):
                proposed = p if p.is_absolute() else base / p
                safe, _ = _path(root, proposed)
                # Only an existing file is a nomination; arbitrary plot labels are not paths.
                if safe is not None and safe.is_file():
                    nominate(safe, depth + 1)
                elif safe is None and (p.suffix or "/" in literal or "\\" in literal):
                    nominate(proposed, depth + 1)
        for name in calls:
            proposed = path.parent / (name + ".m")
            safe, _ = _path(root, proposed)
            if safe is not None and safe.is_file():
                nominate(proposed, depth + 1)

    # Explicit references have priority over inferred dependencies within the
    # shared budget. All still go through _read and _disclosed below.
    for rel in selected:
        nominate(root / rel, 0)
    if entry.suffix.lower() == ".py":
        result = importscan.scan(root, script_rel, no_follow=True)
        for rel in result.files:
            nominate(root / rel, 1)
        if not result.search_complete or result.truncated:
            manifest["limitations"].append("Python import discovery was incomplete.")
    scan(root / entry, source, 0)
    while pending:
        path, depth = pending.pop(0)
        rel = path.relative_to(root).as_posix()
        if depth > MAX_DEPTH or len(manifest["files"]) >= MAX_FILES or total >= MAX_BYTES:
            if rel in selected:
                raise ValueError("convert_reference_invalid")
            manifest["omitted"].append({"path": rel, "reason": "context_budget"})
            continue
        data, reason = _read(root, path, scope)
        if data is not None and total + len(data) > MAX_BYTES:
            data, reason = None, "context_budget"
        if data is None:
            if rel in selected:
                raise ValueError("convert_reference_invalid")
            manifest["omitted"].append({"path": rel, "reason": reason})
            continue
        disclosed = _disclosed(data)
        if len(disclosed) > MAX_FILE_BYTES or total + len(disclosed) > MAX_BYTES:
            if rel in selected:
                raise ValueError("convert_reference_invalid")
            manifest["omitted"].append({"path": rel, "reason": "context_budget"})
            continue
        copy = destination / DIRECTORY / rel
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(disclosed)
        manifest["files"].append(
            {
                "path": rel,
                "sha256": _sha(data),
                "bytes": len(data),
                "snapshot_sha256": _sha(disclosed),
                "snapshot_bytes": len(disclosed),
                "redacted": disclosed != data,
            }
        )
        total += max(len(data), len(disclosed))
        scan(path, data, depth)
    if references >= MAX_REFERENCES:
        manifest["limitations"].append("Reference nomination budget reached.")
    (destination / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def unchanged(root: Path, manifest: dict, *, snapshot_dir: Path | None = None) -> bool:
    """Hash every nominated resource, including large binaries; never accept metadata as a hash."""
    from . import inputremap

    if manifest.get("version") != 1 or not isinstance(manifest.get("files"), list):
        return False
    if (
        "input_remap_fingerprint" in manifest
        and inputremap.fingerprint(root) != manifest["input_remap_fingerprint"]
    ):
        return False
    scope = readscope.ReadScope(home=Path.home(), project_root=root)
    for row in manifest["files"]:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            return False
        data, _ = _read(root.absolute(), root.absolute() / row["path"], scope)
        if data is None or _sha(data) != row.get("sha256"):
            return False
        if snapshot_dir is not None:
            copy_root = snapshot_dir / DIRECTORY
            if _path(snapshot_dir, copy_root)[0] is None:
                return False
            copy_scope = readscope.ReadScope(home=Path.home(), project_root=copy_root)
            data, _ = _read(copy_root, copy_root / row["path"], copy_scope)
            if data is None or _sha(data) != row.get("snapshot_sha256", row["sha256"]):
                return False
    return True


def transient_reference(tree: ast.AST, directory: Path) -> bool:
    """Prevent literal references to the disposable snapshot from surviving adoption."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.replace("\\", "/")
            if str(directory).replace("\\", "/") in value:
                return True
    return False
