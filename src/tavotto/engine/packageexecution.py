"""项目包的可携执行事实；只校验数据，不恢复机器配置或授予执行权限。

ZIP/资源字节仍由 documentpackage/chartpackage 校验；registry、argv、cwd、
profile 与改指匹配各沿原权威。coverage 记录未兑现的部分，不证明任意 Python
依赖完整；未来声明消费者仍须核 complete 的真实性。
"""

from __future__ import annotations

import json
import re
from pathlib import PureWindowsPath

from . import documentpackage, figcapture, launchctx, registry, runconfig, workdir

VERSION = 1
COVERAGE_REASONS = frozenset(
    {
        "dependency_set_undeclared",
        "source_basis_missing",
        "source_basis_stale",
        "run_config_missing",
        "run_config_unresolved",
        "sensitive_run_config",
        "external_input_requires_relink",
        "prefix_members_undeclared",
        "unsupported_absolute_request",
        "resource_missing",
    }
)
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = {"version", "registry", "runs", "contexts", "remaps", "coverage"}
_REGISTRY_REQUIRED = {"entry", "cost", "notes", "stems"}
_REGISTRY_OPTIONAL = {"artifacts", "stem_aliases"}


def _invalid(detail: str = "Invalid portable execution metadata") -> None:
    raise documentpackage.PackageError("invalid_execution", detail)


def _mismatch(detail: str = "Execution metadata and packaged resources disagree") -> None:
    raise documentpackage.PackageError("execution_resource_mismatch", detail)


def _fields(value, fields: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        _invalid("Invalid execution object fields")
    return value


def _array(value) -> list:
    if not isinstance(value, list):
        _invalid("Execution collections must be JSON arrays")
    return value


def _id(value, *, script: bool = False) -> str:
    if not isinstance(value, str):
        _invalid("Execution IDs must be project-relative paths")
    try:
        documentpackage.project_member_key(("scripts/" if script else "assets/") + value)
    except documentpackage.PackageError as exc:
        raise documentpackage.PackageError(
            "invalid_execution", "Unsafe execution resource ID"
        ) from exc
    return value


def _digest(value) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        _invalid("Invalid execution resource digest")
    return value


def _unique(seen: set, key) -> None:
    if key in seen:
        _invalid("Duplicate or aliased execution identity")
    seen.add(key)


def _path_key(value: str, *, script: bool = False) -> str:
    return documentpackage.project_member_key(("scripts/" if script else "assets/") + value)


def _registry(value) -> dict:
    scripts = _fields(value, {"scripts"})["scripts"]
    if not isinstance(scripts, dict):
        _invalid("Execution registry must contain a script table")
    expected, script_keys, stem_keys = {}, set(), set()
    for script, raw in scripts.items():
        _id(script, script=True)
        _unique(script_keys, _path_key(script, script=True))
        if (
            not isinstance(raw, dict)
            or not set(raw) <= _REGISTRY_REQUIRED | _REGISTRY_OPTIONAL
            or any(not isinstance(raw.get(key, ""), str) for key in ("entry", "cost", "notes"))
        ):
            _invalid("Invalid portable registry fields")
        stems = _array(raw.get("stems", []))
        for stem in stems:
            _id(stem)
            _unique(stem_keys, _path_key(stem))
        item = {
            "entry": raw.get("entry", "main"),
            "cost": raw.get("cost", "medium"),
            "notes": raw.get("notes", ""),
            "stems": list(stems),
        }
        for field in _REGISTRY_OPTIONAL & raw.keys():
            mapping = raw[field]
            if not isinstance(mapping, dict):
                _invalid("Invalid portable registry mapping")
            seen = set()
            for source, target in mapping.items():
                _id(source)
                _id(target)
                _unique(seen, _path_key(source))
            item[field] = dict(mapping)
        expected[script] = item
    validator = registry.Registry()
    try:
        validator.load_data({"scripts": expected}, source="<package>")
    except (RuntimeError, TypeError, ValueError) as exc:
        raise documentpackage.PackageError(
            "invalid_execution", "Invalid portable registry"
        ) from exc
    cleaned = validator.entries()
    for script, item in expected.items():
        actual = cleaned.get(script)
        if actual is None:
            _invalid("Registry normalization dropped a script")
        for field, declared in item.items():
            # Empty optional maps have no entries to lose; keep their explicit field.
            if actual.get(field, {} if field in _REGISTRY_OPTIONAL else None) != declared:
                _invalid("Registry normalization changed or discarded execution metadata")
    return {"scripts": expected}


def validate_execution(raw) -> dict:
    """校验闭式 v1 数据并返回独立 canonical JSON dict；不触碰项目或配置。"""
    raw = _fields(raw, _FIELDS)
    if type(raw["version"]) is not int or raw["version"] != VERSION:
        _invalid("Unsupported execution metadata version")
    registered = _registry(raw["registry"])
    scripts = registered["scripts"]
    coverage = _fields(raw["coverage"], {"state", "issues"})
    if coverage["state"] not in ("incomplete", "complete"):
        _invalid("Invalid execution coverage state")
    issues, issue_keys = [], set()
    for issue in _array(coverage["issues"]):
        _fields(issue, {"script", "reason"})
        script = _id(issue["script"], script=True)
        reason = issue["reason"]
        if script not in scripts or not isinstance(reason, str) or reason not in COVERAGE_REASONS:
            _invalid("Unknown execution coverage issue")
        _unique(issue_keys, (script, reason))
        issues.append({"script": script, "reason": reason})
    if coverage["state"] == "complete" and issues:
        _invalid("Complete execution coverage cannot contain unresolved issues")
    runs, by_run = [], {}
    for item in _array(raw["runs"]):
        _fields(item, {"id", "script", "argv"})
        reference = item["id"]
        script = _id(item["script"], script=True)
        if (
            not isinstance(reference, str)
            or not runconfig.ID_RE.fullmatch(reference)
            or reference in by_run
            or script not in scripts
        ):
            _invalid("Invalid or duplicate execution run reference")
        argv = _array(item["argv"])
        if not argv:
            _invalid("A portable run reference requires nonempty arguments")
        try:
            tokens = runconfig.validate_argv(argv)
        except runconfig.InvalidArgv as exc:
            raise documentpackage.PackageError(
                "invalid_execution", "Invalid portable argv"
            ) from exc
        row = {"id": reference, "script": script, "argv": list(tokens)}
        by_run[reference] = row
        runs.append(row)
    contexts, addresses = [], set()
    for item in _array(raw["contexts"]):
        _fields(item, {"script", "run_config", "run_config_known", "cwd_mode", "profile", "basis"})
        script = _id(item["script"], script=True)
        reference, known = item["run_config"], item["run_config_known"]
        if (
            script not in scripts
            or type(known) is not bool
            or item["cwd_mode"] not in workdir.MODES
            or item["profile"] not in (*launchctx.PROFILES, None)
            or (
                reference is not None
                and (not isinstance(reference, str) or not runconfig.ID_RE.fullmatch(reference))
            )
        ):
            _invalid("Invalid execution context")
        _unique(addresses, (script, reference, known))
        if not known:
            if reference is not None or (script, "run_config_unresolved") not in issue_keys:
                _invalid("Unresolved run configuration requires an explicit coverage issue")
        elif reference is not None:
            if reference in by_run:
                if by_run[reference]["script"] != script:
                    _invalid("Execution run belongs to another script")
            elif not any(
                (script, reason) in issue_keys
                for reason in ("run_config_missing", "sensitive_run_config")
            ):
                _invalid("Missing portable run configuration requires an accurate coverage issue")
        basis, basis_keys = [], set()
        for entry in _array(item["basis"]):
            _fields(entry, {"id", "sha256"})
            resource_id = _id(entry["id"])
            _unique(basis_keys, _path_key(resource_id))
            basis.append({"id": resource_id, "sha256": _digest(entry["sha256"])})
        contexts.append({**item, "basis": basis})
    remaps, requests = [], set()
    for item in _array(raw["remaps"]):
        _fields(item, {"kind", "from", "to_resource"})
        source = item["from"]
        parsed = figcapture.remap_parts(source)
        if (
            item["kind"] != figcapture.REMAP_FILE
            or parsed is None
            or parsed[0]
            or not parsed[1]
            or PureWindowsPath(source).drive
        ):
            _invalid("Only explicit relative file remaps are portable")
        _unique(requests, parsed[1])
        remaps.append(
            {"kind": figcapture.REMAP_FILE, "from": source, "to_resource": _id(item["to_resource"])}
        )
    value = {
        "version": VERSION,
        "registry": registered,
        "runs": runs,
        "contexts": contexts,
        "remaps": remaps,
        "coverage": {"state": coverage["state"], "issues": issues},
    }
    # Sort only object keys; original argv/context/basis/remap/issue sequence is retained.
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode(
            "utf-8"
        )
        return json.loads(encoded)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise documentpackage.PackageError("invalid_execution", "Invalid execution JSON") from exc


def validate_resources(execution, normalized_records) -> None:
    """核执行引用与已归一资源行；不读文件、不接受 coverage 掩盖摘要矛盾。"""
    value = validate_execution(execution)
    if not isinstance(normalized_records, (list, tuple)):
        _mismatch("Normalized package resources must be a sequence")
    resources, aliases, rows = {}, {}, set()
    for row in normalized_records:
        if not isinstance(row, dict):
            _mismatch("Invalid normalized resource row")
        if row.get("kind") not in ("file", "script"):
            continue
        resource_id, digest = row.get("id"), row.get("sha256")
        try:
            _id(resource_id, script=row["kind"] == "script")
            _digest(digest)
            key = _path_key(resource_id, script=row["kind"] == "script")
        except documentpackage.PackageError as exc:
            raise documentpackage.PackageError(
                "execution_resource_mismatch", "Invalid execution resource row"
            ) from exc
        if (row["kind"], resource_id) in rows:
            _mismatch("Duplicate execution resource row")
        rows.add((row["kind"], resource_id))
        if key in aliases and aliases[key] != resource_id:
            _mismatch("Resource identities alias")
        if resource_id in resources and resources[resource_id] != digest:
            _mismatch("Resource namespaces disagree on bytes")
        aliases[key], resources[resource_id] = resource_id, digest
    issues = {(item["script"], item["reason"]) for item in value["coverage"]["issues"]}
    for script in value["registry"]["scripts"]:
        if script not in resources and (script, "resource_missing") not in issues:
            _mismatch("Registered script was not packaged")
    for context in value["contexts"]:
        for entry in context["basis"]:
            if entry["id"] not in resources:
                if (context["script"], "resource_missing") not in issues:
                    _mismatch("Execution basis resource was not packaged")
            elif resources[entry["id"]] != entry["sha256"]:
                _mismatch("Execution basis digest does not match packaged bytes")
    for remap in value["remaps"]:
        if remap["to_resource"] not in resources:
            _mismatch("Portable file remap target was not packaged")
