"""父进程的显式输入选择；本机批准与 worker 的实际捕获事实分开。

本模块不执行脚本；只有匹配真实执行上下文的声明才生成私有 candidate。
当前内容比对复用唯一 freezer，不能产生新的执行事实。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import (
    atomicio,
    config,
    execspec,
    figcapture,
    inputremap,
    layoutsession,
    packagemembers,
    projectcopy,
    projectenv,
    runconfig,
)

VERSION = 1
MAX_DECLARATIONS = 128
MAX_SETTINGS_BYTES = 4 * 1024 * 1024
MAX_TEXT_CHARS = 4096
_REVISION = re.compile(r"[0-9a-f]{32}\Z")
_LOCK = threading.RLock()


class DeclaredInputsError(ValueError):
    """声明不可用；不得把坏配置当作没有声明。"""


class DeclaredInputsConflict(DeclaredInputsError):
    """调用者观察的整份声明修订已经失效。"""


class DeclaredInputsUnavailable(DeclaredInputsError):
    """本机存储不可用，不等同于选择了无效输入。"""


def _text(value, *, empty=False):
    if (
        not isinstance(value, str)
        or (not value and not empty)
        or len(value) > MAX_TEXT_CHARS
        or "\x00" in value
    ):
        raise DeclaredInputsError("Invalid input declaration text")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise DeclaredInputsError("Input declaration is not valid UTF-8") from exc
    return value


def _portable(value):
    value = _text(value)
    try:
        key = packagemembers.project_member_key("assets/" + value)
    except packagemembers.PackageError as exc:
        raise DeclaredInputsError("Input identity must be project relative and portable") from exc
    return value, key


def _scope(script, cwd_mode, run_config):
    script, _ = _portable(script)
    if not isinstance(cwd_mode, str) or cwd_mode not in execspec.CWD_MODES:
        raise DeclaredInputsError("Invalid declared cwd mode")
    if run_config is not None and (
        not isinstance(run_config, str) or not runconfig.ID_RE.fullmatch(run_config)
    ):
        raise DeclaredInputsError("Invalid declared run configuration")
    return {"script": script, "cwd_mode": cwd_mode, "run_config": run_config}


def _scope_key(scope):
    return scope["script"], scope["cwd_mode"], scope["run_config"]


def _row_revision(row):
    return (
        "sha256:" + hashlib.sha256(atomicio.dumps_json(row)).hexdigest()
        if row is not None
        else None
    )


def scope_selection(root, *, script, cwd_mode, run=None, execution="safe"):
    """A private, exact-scope metadata observation, including observed absence."""
    if execution not in (execspec.PROFILE_SAFE, execspec.PROFILE_NATIVE):
        raise DeclaredInputsError("Unknown input declaration execution profile")
    if run is not None and (not isinstance(run, execspec.RunSelection) or run.sensitive):
        return {"scope": None, "revision": None}
    if execution == execspec.PROFILE_NATIVE and cwd_mode == execspec.CWD_SANDBOX:
        cwd_mode = execspec.CWD_PROJECT
    scope = _scope(script, cwd_mode, run.config_id if run is not None else None)
    data, _ = _read(root)
    row = next((item for item in data["declarations"] if item["scope"] == scope), None)
    return {
        "scope": scope,
        "revision": _row_revision(row),
    }


def _strings(raw, *, portable=False, deduplicate=True):
    if not isinstance(raw, (list, tuple)) or len(raw) > projectcopy.MAX_FILES:
        raise DeclaredInputsError("Too many or invalid input selections")
    result, seen = [], {}
    for value in raw:
        value, key = _portable(value) if portable else (_text(value, empty=True), value)
        if key in seen:
            if seen[key] != value or not deduplicate:
                raise DeclaredInputsError(
                    "Input selections contain duplicate or aliased identities"
                )
            continue
        seen[key] = value
        result.append(value)
    return result


def _absolute(value):
    value = _text(value)
    if not os.path.isabs(value):
        raise DeclaredInputsError("Approved input path must be absolute")
    return value


def _stored_remap(raw):
    if not isinstance(raw, dict) or set(raw) != {"kind", "from", "to", "path"}:
        raise DeclaredInputsError("Invalid approved remap record")
    if raw["kind"] not in (figcapture.REMAP_FILE, figcapture.REMAP_PREFIX):
        raise DeclaredInputsError("Invalid approved remap kind")
    return {
        "kind": raw["kind"],
        "from": _text(raw["from"], empty=True),
        "to": _absolute(raw["to"]),
        "path": _absolute(raw["path"]),
    }


def _store(raw):
    if (
        not isinstance(raw, dict)
        or set(raw) != {"version", "declarations"}
        or type(raw["version"]) is not int
        or raw["version"] != VERSION
        or not isinstance(raw["declarations"], list)
        or len(raw["declarations"]) > MAX_DECLARATIONS
    ):
        raise DeclaredInputsError("Unsupported or invalid input declaration settings")
    rows, seen = [], set()
    for row in raw["declarations"]:
        if not isinstance(row, dict) or set(row) != {"scope", "files", "remaps"}:
            raise DeclaredInputsError("Invalid input declaration record")
        scope = row["scope"]
        if not isinstance(scope, dict) or set(scope) != {"script", "cwd_mode", "run_config"}:
            raise DeclaredInputsError("Invalid input declaration scope")
        scope = _scope(**scope)
        key = _scope_key(scope)
        if key in seen:
            raise DeclaredInputsError("Duplicate input declaration scope")
        seen.add(key)
        files = _strings(row["files"], portable=True, deduplicate=False)
        if not isinstance(row["remaps"], list) or len(row["remaps"]) > figcapture.MAX_REMAP_RULES:
            raise DeclaredInputsError("Invalid approved remap selections")
        remaps = [_stored_remap(item) for item in row["remaps"]]
        if len({item["from"] for item in remaps}) != len(remaps):
            raise DeclaredInputsError("Duplicate approved remap request")
        rows.append({"scope": scope, "files": files, "remaps": remaps})
    return {"version": VERSION, "declarations": rows}


def store_path(root) -> Path:
    """本机项目身份与 runconfig 同源；不把批准记录写进可搬迁项目。"""
    identity = runconfig.project_identity(root)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return config.data_path("input-declarations", digest + ".json")


def _read(root):
    try:
        with store_path(root).open("rb") as handle:
            data = handle.read(MAX_SETTINGS_BYTES + 1)
    except FileNotFoundError:
        return {"version": VERSION, "declarations": []}, None
    except OSError as exc:
        raise DeclaredInputsUnavailable("Input declaration settings cannot be read") from exc
    if len(data) > MAX_SETTINGS_BYTES:
        raise DeclaredInputsError("Input declaration settings exceed the metadata budget")
    try:
        raw = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise DeclaredInputsError("Input declaration settings are malformed") from exc
    return _store(raw), atomicio.revision_of(data)


@contextmanager
def _guard(root):
    with _LOCK:
        path = store_path(root)
        try:
            fd = layoutsession.acquire_file_lock(
                path.parent / (path.name + ".lock"),
                time.monotonic() + layoutsession.LOCK_TIMEOUT_S,
            )
        except OSError as exc:
            raise DeclaredInputsUnavailable("Input declaration settings cannot be locked") from exc
        try:
            yield
        finally:
            layoutsession.release_file_lock(fd)


def _view(data, revision):
    return {
        "version": VERSION,
        "revision": revision,
        "declarations": [
            {
                "scope": dict(row["scope"]),
                "files": list(row["files"]),
                "remaps": [item["from"] for item in row["remaps"]],
            }
            for row in data["declarations"]
        ],
    }


def state(root):
    """只读声明元信息；不探测任何脚本、数据、目录或外部 remap 目标。"""
    return _view(*_read(root))


def _cas(actual, expected):
    if expected is not None and (
        not isinstance(expected, str) or not _REVISION.fullmatch(expected)
    ):
        raise DeclaredInputsError("Invalid expected declaration revision")
    if actual != expected:
        raise DeclaredInputsConflict("Input declaration settings changed")


def _write(root, data):
    data = _store(data)
    try:
        encoded = atomicio.dumps_json(data)
        if len(encoded) > MAX_SETTINGS_BYTES:
            raise DeclaredInputsError("Input declaration settings exceed the metadata budget")
        path = store_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        revision = atomicio.write_json(path, data)
    except OSError as exc:
        raise DeclaredInputsUnavailable("Input declaration settings cannot be saved") from exc
    return _view(data, revision)


def _file(root, identity):
    approved = projectenv.contained_path(root, identity)
    if approved is None:
        raise DeclaredInputsError("Declared input leaves the project")
    if not Path(approved).is_file():
        raise DeclaredInputsError("Declared input is not an available regular file")
    return approved


def _approved_remaps(root, requested):
    if not requested:
        return []
    _, rules = inputremap.snapshot(root)
    result = []
    for source in requested:
        matching = [rule for rule in rules if rule["from"] == source]
        if len(matching) != 1:
            raise DeclaredInputsError("Remap request is missing or ambiguous")
        rule = matching[0]
        # The root is this already-approved rule's exact target, never a request path.
        approved = projectenv.contained_path(rule["to"], rule["to"])
        if approved is None:
            raise DeclaredInputsError("Approved remap target is unavailable")
        probe = Path(approved)
        available = probe.is_file() if rule["kind"] == figcapture.REMAP_FILE else probe.is_dir()
        if not available:
            raise DeclaredInputsError("Approved remap target has the wrong type or is missing")
        result.append({**rule, "path": approved})
    return result


def declare(
    root,
    *,
    script,
    cwd_mode,
    run_config=None,
    files: list[str],
    remaps: list[str] = (),
    expected_revision: str | None,
):
    """明确选择输入；None 修订只允许首次创建，不读内容、不运行、不展开目录。"""
    scope = _scope(script, cwd_mode, run_config)
    files = _strings(files, portable=True)
    remaps = _strings(remaps)
    if len(remaps) > figcapture.MAX_REMAP_RULES:
        raise DeclaredInputsError("Too many approved remaps")
    with _guard(root):
        data, revision = _read(root)
        _cas(revision, expected_revision)
        if run_config is not None:
            try:
                selected = runconfig.get(root, run_config, script=script)
            except runconfig.RunConfigError as exc:
                raise DeclaredInputsError("Declared run configuration is unavailable") from exc
            if selected.sensitive:
                raise DeclaredInputsError(
                    "Sensitive run configuration cannot declare portable inputs"
                )
        for identity in files:
            _file(root, identity)
        row = {"scope": scope, "files": files, "remaps": _approved_remaps(root, remaps)}
        remaining = [r for r in data["declarations"] if _scope_key(r["scope"]) != _scope_key(scope)]
        if len(remaining) >= MAX_DECLARATIONS:
            raise DeclaredInputsError("Too many input declarations")
        data["declarations"] = [*remaining, row]
        return _write(root, data)


def remove(root, *, script, cwd_mode, run_config=None, expected_revision: str | None):
    """只移除精确 scope；空 store 仍保留修订，防止删除后的 ABA。"""
    scope = _scope(script, cwd_mode, run_config)
    with _guard(root):
        data, revision = _read(root)
        _cas(revision, expected_revision)
        data["declarations"] = [
            row for row in data["declarations"] if _scope_key(row["scope"]) != _scope_key(scope)
        ]
        return _write(root, data)


def _actual_scope(worker):
    spec = getattr(worker, "spec", None)
    if (
        not isinstance(spec, execspec.ExecutionSpec)
        or spec.profile != execspec.PROFILE_SAFE
        or spec.target_kind != execspec.TARGET_SCRIPT
        or spec.target != getattr(worker, "script_name", None)
        or runconfig.project_identity(spec.project_root)
        != runconfig.project_identity(getattr(worker, "figures_dir", ""))
    ):
        return None
    selected = getattr(worker, "run", None)
    if selected is None:
        if spec.argv or spec.run_config:
            return None
        reference = None
    elif (
        isinstance(selected, execspec.RunSelection)
        and not selected.sensitive
        and selected.config_id == spec.run_config
        and selected.argv == spec.argv
    ):
        reference = selected.config_id
    else:
        return None
    return _scope(spec.target, spec.cwd_mode, reference)


def _remap_identity(root, approved, rule):
    local = projectenv.contained_path(root, approved)
    if local is not None:
        identity = os.path.relpath(local, os.path.realpath(root)).replace(os.sep, "/")
        try:
            return _portable(identity)[0]
        except DeclaredInputsError:
            pass  # The project root itself has no portable resource ID.
    encoded = atomicio.dumps_json([rule["kind"], rule["from"]])
    return "remap:" + hashlib.sha256(encoded).hexdigest()


def payload_for(worker):
    """只有真实 safe spec 的精确声明才进入 build；无声明不触碰资源。"""
    scope = _actual_scope(worker)
    if scope is None:
        return {}
    spec = worker.spec
    root = spec.project_root
    data, _ = _read(root)
    row = next(
        (item for item in data["declarations"] if item["scope"] == scope),
        None,
    )
    if row is None:
        return {}
    if scope["run_config"] is not None:
        try:
            selected = runconfig.get(root, scope["run_config"], script=scope["script"])
        except runconfig.RunConfigError:
            return {}
        if selected.sensitive or selected.argv != spec.argv:
            return {}
    return {
        "input_declaration": _candidate(
            root, scope, row, rules=spec.input_remap, source=spec.script_source
        )
    }


def _candidate(root, scope, row, *, rules=(), source=None, native=False):
    rules = [dict(rule) for rule in rules]
    if figcapture.clean_remap_rules(rules) != rules:
        raise DeclaredInputsError("Worker remap rules are malformed")
    files = [{"id": identity, "path": _file(root, identity)} for identity in row["files"]]
    prefixes, mappings = [], []
    for selected in row["remaps"]:
        rule = {key: selected[key] for key in ("kind", "from", "to")}
        if not native and rules.count(rule) != 1:
            raise DeclaredInputsError("Worker remap no longer matches the approved declaration")
        approved = projectenv.contained_path(rule["to"], rule["to"])
        if approved is None or approved != selected["path"]:
            raise DeclaredInputsError("Approved remap target changed")
        identity = _remap_identity(root, approved, rule)
        item = {"id": identity, "path": approved}
        target = files if rule["kind"] == figcapture.REMAP_FILE else prefixes
        if item not in target:
            target.append(item)
        if not native:
            # Native does not apply safe remapping rules. Its explicitly
            # approved targets are evidence resources, never claimed aliases.
            mappings.append({"kind": rule["kind"], "from": rule["from"], "id": identity})
    if source is None:
        source = projectenv.contained_path(root, scope["script"])
    elif os.path.isabs(source):
        # An owned safe spec may point at an explicitly selected shadow source.
        source = projectenv.contained_path(Path(source).parent, source)
    else:
        source = None
    if source is None or not Path(source).is_file():
        raise DeclaredInputsError("Worker script source is unavailable")
    from . import inputdeclaration

    candidate = {
        "version": inputdeclaration.VERSION,
        "scope": dict(scope),
        "script_path": source,
        "files": files,
        "prefixes": prefixes,
        "mappings": mappings,
        "rules": rules,
        "limits": {"max_files": projectcopy.MAX_FILES, "max_bytes": projectcopy.MAX_BYTES},
    }
    return inputdeclaration.validate_candidate(copy.deepcopy(candidate))


def payload_for_native(spec, *, run=None, cwd_mode=None, expected_selection=None):
    """Approved candidate for actual native cwd and positively known arguments.

    Terminal nonempty argv has no verified portable run reference. Multiple
    compatible declarations need an explicit selection instead of guessing.
    """
    if (
        not isinstance(spec, execspec.ExecutionSpec)
        or spec.profile != execspec.PROFILE_NATIVE
        or spec.target_kind != execspec.TARGET_SCRIPT
    ):
        return {}
    if spec.run_config:
        if spec.owner != execspec.OWNER_DESKTOP:
            return {}
        try:
            selected = runconfig.get(spec.project_root, spec.run_config, script=spec.target)
        except runconfig.RunConfigError:
            return {}
        if selected.sensitive or selected.argv != spec.argv:
            return {}
        if run is not None and (
            not isinstance(run, execspec.RunSelection)
            or run.sensitive
            or run.config_id != spec.run_config
            or run.argv != spec.argv
        ):
            return {}
    elif spec.argv or run is not None:
        return {}
    if expected_selection is not None:
        if not isinstance(expected_selection, dict) or set(expected_selection) != {
            "scope",
            "revision",
        }:
            raise DeclaredInputsError("Invalid input declaration selection")
        expected_scope = expected_selection["scope"]
        if expected_scope is None:
            return {}
        if not isinstance(expected_scope, dict):
            raise DeclaredInputsError("Invalid input declaration scope")
        cwd_mode = expected_scope.get("cwd_mode")
    if cwd_mode == execspec.CWD_SANDBOX:
        cwd_mode = execspec.CWD_PROJECT
    modes = (
        (cwd_mode,) if cwd_mode is not None else (execspec.CWD_PROJECT, execspec.CWD_PROJECT_ROOT)
    )
    scopes = []
    for mode in modes:
        if mode not in (execspec.CWD_PROJECT, execspec.CWD_PROJECT_ROOT):
            continue
        scope = _scope(spec.target, mode, spec.run_config or None)
        expected_cwd = execspec.desktop_cwd(spec.project_root, spec.target, mode)
        if os.path.normcase(os.path.abspath(spec.cwd)) == os.path.normcase(
            os.path.abspath(expected_cwd)
        ):
            scopes.append(scope)
    data, _ = _read(spec.project_root)
    if expected_selection is not None:
        if expected_scope not in scopes:
            raise DeclaredInputsConflict("Native input declaration scope changed")
        row = next((r for r in data["declarations"] if r["scope"] == expected_scope), None)
        revision = _row_revision(row)
        if revision != expected_selection["revision"]:
            raise DeclaredInputsConflict("Native input declaration selection changed")
        rows = [row] if row is not None else []
    else:
        rows = [row for row in data["declarations"] if row["scope"] in scopes]
    if len(rows) != 1:
        return {}
    row = rows[0]
    return {"input_declaration": _candidate(spec.project_root, row["scope"], row, native=True)}


def matches_current_public(root, *, script, cwd_mode, run, captured_inputs, execution="safe"):
    """Compare current approved bytes with a past actual capture's public hash."""
    from . import inputdeclaration

    checked = inputdeclaration.validate_public_fact(captured_inputs)
    if checked is None:
        return False
    try:
        selection = scope_selection(
            root, script=script, cwd_mode=cwd_mode, run=run, execution=execution
        )
        if selection["scope"] is None or selection["revision"] is None:
            return False
        if run is not None:
            actual = runconfig.get(root, run.config_id, script=script)
            if actual.sensitive or actual.argv != run.argv:
                return False
        data, _ = _read(root)
        row = next((r for r in data["declarations"] if r["scope"] == selection["scope"]), None)
        if row is None or _row_revision(row) != selection["revision"]:
            return False
        candidate = _candidate(
            root,
            selection["scope"],
            row,
            rules=inputremap.rules_for(root) if execution == execspec.PROFILE_SAFE else (),
            native=execution == execspec.PROFILE_NATIVE,
        )
        return inputdeclaration.current_public_fact(candidate) == checked
    except (DeclaredInputsError, ValueError, OSError, TypeError):
        return False
