"""项目包的可携执行事实；只校验数据，不恢复机器配置或授予执行权限。

ZIP/资源字节仍由 documentpackage/chartpackage 校验；registry、argv、cwd、
profile 与改指匹配各沿原权威。coverage 记录未兑现的部分，不证明任意 Python
依赖完整；未来声明消费者仍须核 complete 的真实性。
"""

from __future__ import annotations

import json
import re
from pathlib import PureWindowsPath

from . import figcapture, launchctx, packagemembers, registry, runconfig, workdir

VERSION = 1
CAPTURE_VERSION = 2
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
_CAPTURE_COVERAGE_REASONS = COVERAGE_REASONS | {"run_config_unavailable"}
_REGISTRY_REQUIRED = {"entry", "cost", "notes", "stems"}
_REGISTRY_OPTIONAL = {"artifacts", "stem_aliases"}


def _invalid(detail: str = "Invalid portable execution metadata") -> None:
    raise packagemembers.PackageError("invalid_execution", detail)


def _mismatch(detail: str = "Execution metadata and packaged resources disagree") -> None:
    raise packagemembers.PackageError("execution_resource_mismatch", detail)


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
        packagemembers.project_member_key(("scripts/" if script else "assets/") + value)
    except packagemembers.PackageError as exc:
        raise packagemembers.PackageError(
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
    return packagemembers.project_member_key(("scripts/" if script else "assets/") + value)


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
        raise packagemembers.PackageError("invalid_execution", "Invalid portable registry") from exc
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


def _captures(raw) -> list[dict]:
    from . import inputdeclaration, svgstore

    result, seen = [], set()
    for row in _array(raw):
        _fields(row, {"snapshot_ref", "revision"})
        try:
            reference = svgstore.validate_reference(row["snapshot_ref"])
        except (ValueError, TypeError, RecursionError) as exc:
            raise packagemembers.PackageError(
                "invalid_execution", "Invalid captured Figure reference"
            ) from exc
        if (
            inputdeclaration.validate_public_fact(
                {"version": inputdeclaration.VERSION, "revision": row["revision"]}
            )
            is None
        ):
            _invalid("Invalid captured input revision")
        _unique(seen, reference["snapshotId"])
        result.append({"snapshot_ref": reference, "revision": row["revision"]})
    if not result:
        _invalid("Capture metadata requires at least one captured Figure")
    return result


def validate_execution(raw) -> dict:
    """校验显式闭式版本；缺省 v1 不变，v2 另携历史捕获引用。"""
    if not isinstance(raw, dict) or type(raw.get("version")) is not int:
        _invalid("Unsupported execution metadata version")
    version = raw["version"]
    if version not in (VERSION, CAPTURE_VERSION):
        _invalid("Unsupported execution metadata version")
    raw = _fields(raw, _FIELDS | ({"captures"} if version == CAPTURE_VERSION else set()))
    captures = _captures(raw["captures"]) if version == CAPTURE_VERSION else None
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
        reasons = _CAPTURE_COVERAGE_REASONS if version == CAPTURE_VERSION else COVERAGE_REASONS
        if script not in scripts or not isinstance(reason, str) or reason not in reasons:
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
            raise packagemembers.PackageError("invalid_execution", "Invalid portable argv") from exc
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
                for reason in (
                    "run_config_missing",
                    "sensitive_run_config",
                    *(("run_config_unavailable",) if version == CAPTURE_VERSION else ()),
                )
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
        "version": version,
        "registry": registered,
        "runs": runs,
        "contexts": contexts,
        "remaps": remaps,
        "coverage": {"state": coverage["state"], "issues": issues},
    }
    if captures is not None:
        # A closed file declaration does not prove Python's other input channels.
        if value["coverage"]["state"] != "incomplete":
            _invalid("Captured files do not prove complete execution dependency coverage")
        value["captures"] = captures
    # Sort only object keys; original argv/context/basis/remap/issue sequence is retained.
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode(
            "utf-8"
        )
        return json.loads(encoded)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise packagemembers.PackageError("invalid_execution", "Invalid execution JSON") from exc


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
        except packagemembers.PackageError as exc:
            raise packagemembers.PackageError(
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
    captured_scripts = {
        row["snapshot_ref"]["executionOwner"]["script"]
        for row in value.get("captures", [])
        if "executionOwner" in row["snapshot_ref"]
    }
    current_scripts = {row["script"] for row in value["contexts"]}
    issues = {(item["script"], item["reason"]) for item in value["coverage"]["issues"]}
    for script in value["registry"]["scripts"]:
        if (
            script not in resources
            and (script not in captured_scripts or script in current_scripts)
            and (script, "resource_missing") not in issues
        ):
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
    if value["version"] == CAPTURE_VERSION:
        from . import config, svgstore, svgstore_inputs

        prefix = f"assets/{config.PROJECT_STORE_DIRNAME}/{svgstore.STORE_DIRNAME}/"
        by_path = {row.get("path"): row for row in normalized_records}
        for capture in value["captures"]:
            reference, revision = capture["snapshot_ref"], capture["revision"]
            archive = by_path.get(prefix + "snapshots/" + reference["snapshotId"] + ".json")
            graph = by_path.get(prefix + svgstore_inputs._graph_name({"revision": revision}))
            if (
                archive is None
                or archive.get("kind") != "figure_snapshot"
                or graph is None
                or graph.get("kind") != "figure_snapshot"
                or graph.get("sha256") != revision[7:]
            ):
                _mismatch("Captured Figure archive or input graph was not packaged")
            for row in (archive, graph):
                try:
                    _digest(row.get("sha256"))
                except packagemembers.PackageError as exc:
                    raise packagemembers.PackageError(
                        "execution_resource_mismatch", "Invalid captured resource digest"
                    ) from exc
                if type(row.get("bytes")) is not int or row["bytes"] < 1:
                    _mismatch("Invalid captured resource size")


def captures_from_facts(facts) -> list[dict]:
    """Project already checked store facts into the small versioned wire shape."""
    from . import svgstore, svgstore_inputs

    if not isinstance(facts, dict) or not isinstance(facts.get("snapshots"), dict):
        _mismatch("Invalid checked Figure store facts")
    captures = []
    for snapshot_id, row in sorted(facts["snapshots"].items()):
        if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
            _mismatch("Invalid checked Figure archive")
        descriptor = row["payload"].get("input_archive")
        if descriptor is not None:
            try:
                descriptor = svgstore_inputs.validate_descriptor(descriptor)
                reference = svgstore._snapshot_reference(row["payload"])
            except (ValueError, TypeError, KeyError, RecursionError) as exc:
                raise packagemembers.PackageError(
                    "execution_resource_mismatch", "Invalid captured input descriptor"
                ) from exc
            if (
                reference["snapshotId"] != snapshot_id
                or row.get("reference") != reference
                or row.get("execution_owner") != row["payload"].get("execution_owner")
                or row.get("receipt") != row["payload"]["receipt"]
            ):
                _mismatch("Checked Figure identity changed")
            captures.append({"snapshot_ref": reference, "revision": descriptor["revision"]})
    return _captures(captures) if captures else []


def _portable_graph(graph) -> None:
    """Validate input names without resolving them or treating requests as paths.

    A selected shadow script is its own role; its source may differ from a data
    file with the same logical name. The existing script_source execution path
    preserves that distinction. Different captures also have separate views.
    """
    _id(graph["scope"]["script"], script=True)
    reference = graph["scope"]["run_config"]
    if reference is not None and not runconfig.ID_RE.fullmatch(reference):
        _mismatch("Captured run configuration identity is invalid")
    project, opaque = [], set()

    def identity(value):
        if re.fullmatch(r"remap:[0-9a-f]{64}", value):
            _unique(opaque, value)
            return False
        _id(value)
        return True

    def tree(items):
        seen, spellings = {}, {}
        for name, content in items:
            key = _path_key(name)
            parts, originals = key.split("/"), name.split("/")
            for index in range(1, len(parts) + 1):
                ancestor, spelling = "/".join(parts[:index]), "/".join(originals[:index])
                if ancestor in spellings and spellings[ancestor] != spelling:
                    _mismatch("Captured input directory names alias")
                spellings[ancestor] = spelling
            previous = seen.get(key)
            if previous is not None:
                if previous != (name, content):
                    _mismatch("Captured input names alias or disagree on content")
            else:
                seen[key] = (name, content)
        for key, (_name, content) in seen.items():
            parts = key.split("/")
            for index in range(1, len(parts)):
                parent = seen.get("/".join(parts[:index]))
                if parent is not None and parent[1] is not None:
                    _mismatch("Captured input file conflicts with a directory")

    for row in graph["files"]:
        if identity(row["id"]):
            project.append((row["id"], (row["sha256"], row["size_bytes"])))
    for prefix in graph["prefixes"]:
        is_project = identity(prefix["id"])
        # Use a safe, synthetic root for the opaque remap's separate namespace.
        root = prefix["id"] if is_project else "remapped-input"
        items, directories = [], set(prefix["directories"])
        if "" not in directories:
            _mismatch("Captured input prefix root directory is missing")
        for suffix in prefix["directories"]:
            name = root + ("/" + suffix if suffix else "")
            _id(name)
            items.append((name, None))
            if suffix and suffix.rpartition("/")[0] not in directories:
                _mismatch("Captured input prefix directory parent is missing")
        for member in prefix["members"]:
            suffix = member["suffix"]
            name = root + "/" + suffix
            _id(name)
            if suffix.rpartition("/")[0] not in directories:
                _mismatch("Captured input prefix file parent is missing")
            items.append((name, (member["sha256"], member["size_bytes"])))
        tree(items)
        if is_project:
            project.extend(items)
    tree(project)


def validate_capture_facts(execution, facts) -> list[dict]:
    """Cross-check v2 against full store inspection, never current project IO.

    Returned summaries describe closed declared file sets, not execution grants,
    installed targets or proof of unobserved Python input channels.
    """
    from . import inputdeclaration, svgstore

    value = validate_execution(execution)
    if value["version"] != CAPTURE_VERSION:
        return []
    expected = captures_from_facts(facts)
    actual = {row["snapshot_ref"]["snapshotId"]: row for row in value["captures"]}
    if actual != {row["snapshot_ref"]["snapshotId"]: row for row in expected}:
        _mismatch("Execution captures and packaged Figure archives disagree")
    inputs = facts.get("inputs")
    if (
        not isinstance(inputs, dict)
        or not isinstance(inputs.get("graphs"), dict)
        or not isinstance(inputs.get("blobs"), dict)
    ):
        _mismatch("Captured input inventory is missing")
    needed_graphs, needed_blobs, summaries = set(), {}, []
    policy = svgstore.input_store_budget()
    for capture in value["captures"]:
        reference, revision = capture["snapshot_ref"], capture["revision"]
        row = facts["snapshots"][reference["snapshotId"]]
        entry = inputs["graphs"].get(revision[7:])
        if not isinstance(entry, dict) or entry.get("revision") != revision:
            _mismatch("Captured input graph is missing")
        graph = inputdeclaration.validate_graph(entry.get("graph"), expected_revision=revision)
        if graph is None:
            _mismatch("Invalid captured input graph")
        descriptor = row["payload"]["input_archive"]
        if (
            entry.get("graph_size_bytes") != descriptor["graph_size_bytes"]
            or len(inputdeclaration.canonical_graph_bytes(graph)) != descriptor["graph_size_bytes"]
        ):
            _mismatch("Captured input graph size disagrees")
        scope, receipt = graph["scope"], row["receipt"]
        if receipt.get("captured_inputs", {}).get("revision") != revision:
            _mismatch("Captured input receipt disagrees")
        owner = row.get("execution_owner")
        if owner is not None and owner != {key: scope[key] for key in ("script", "run_config")}:
            _mismatch("Captured input execution owner disagrees")
        spec = receipt.get("spec")
        if isinstance(spec, dict):
            if spec.get("target") not in (None, scope["script"]):
                _mismatch("Captured input script scope disagrees")
            if spec.get("run_config") != scope["run_config"]:
                _mismatch("Captured input run scope disagrees")
            if receipt.get("profile") == "safe" and spec.get("cwd_mode") != scope["cwd_mode"]:
                _mismatch("Captured input working directory scope disagrees")
        try:
            _portable_graph(graph)
            contents = inputdeclaration.graph_resources(
                graph,
                limits={
                    "max_files": policy.capture_max_files,
                    "max_bytes": policy.capture_max_bytes,
                },
            )
        except (ValueError, TypeError) as exc:
            raise packagemembers.PackageError(
                "execution_resource_mismatch", "Captured input logical tree is invalid"
            ) from exc
        needed_graphs.add(revision[7:])
        for digest, size in contents.items():
            if digest in needed_blobs and needed_blobs[digest] != size:
                _mismatch("Captured input content identities disagree")
            needed_blobs[digest] = size
        summaries.append(
            {
                **capture,
                "scope": scope,
                "files": len(graph["files"]),
                "prefixes": len(graph["prefixes"]),
            }
        )
    if set(inputs["graphs"]) != needed_graphs or set(inputs["blobs"]) != set(needed_blobs):
        _mismatch("Captured input inventory is not the exact archive closure")
    for digest, size in needed_blobs.items():
        if inputs["blobs"][digest] != {"sha256": digest, "size_bytes": size}:
            _mismatch("Captured input blob inventory disagrees")
    return summaries
