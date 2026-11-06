"""只收集已批准的可携执行事实；不运行脚本、不复制本机执行授权。"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from . import (
    chartpackage,
    databinding,
    documentpackage,
    figcapture,
    inputremap,
    launchctx,
    packageexecution,
    projectenv,
    runconfig,
    workdir,
)

_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class CollectedExecution:
    """execution 为独立 JSON；resources 含主脚本，missing 仅项目相对资源 ID。"""

    execution: dict
    resources: tuple[chartpackage.ResourceFile, ...]
    missing: tuple[str, ...]


def _invalid(detail: str) -> None:
    raise documentpackage.PackageError("invalid_execution", detail)


def _mismatch(detail: str) -> None:
    raise documentpackage.PackageError("execution_resource_mismatch", detail)


def collect_execution(
    project_root: str | Path,
    registry,
    source_uses: Iterable[Mapping],
    *,
    resource: Callable[[str, str], chartpackage.ResourceFile | None],
    capture_facts=None,
) -> CollectedExecution:
    """Keep v1 current-source collection separate from checked historical captures.

    ``capture_facts`` is the actual staging svgstore inspection, never client
    metadata. Captured resources stay in that existing Figure store; this
    collector does not re-read their original paths or mint current execution.
    """
    if capture_facts is None:
        return _collect_execution_v1(project_root, registry, source_uses, resource=resource)
    captures = packageexecution.captures_from_facts(capture_facts)
    if not captures:
        return _collect_execution_v1(project_root, registry, source_uses, resource=resource)
    current = _collect_execution_v1(project_root, registry, source_uses, resource=resource)
    execution = current.execution
    execution["version"] = packageexecution.CAPTURE_VERSION
    execution["captures"] = captures
    _capture_run_suggestions(project_root, registry, execution)
    execution = packageexecution.validate_execution(execution)
    packageexecution.validate_capture_facts(execution, capture_facts)
    return CollectedExecution(execution, current.resources, current.missing)


def _capture_run_suggestions(project_root, registry, execution):
    """Carry available original argv as advice, without reconstructing history."""
    entries = registry.entries()
    scripts = execution["registry"]["scripts"]
    run_ids = {row["id"] for row in execution["runs"]}
    issues = {(row["script"], row["reason"]) for row in execution["coverage"]["issues"]}
    addresses = set()

    def issue(script, reason):
        if (script, reason) not in issues:
            issues.add((script, reason))
            execution["coverage"]["issues"].append({"script": script, "reason": reason})

    for capture in execution["captures"]:
        owner = capture["snapshot_ref"].get("executionOwner")
        if owner is None:
            continue
        script, reference = owner["script"], owner["run_config"]
        address = (script, reference)
        if address in addresses or script not in entries:
            continue
        addresses.add(address)
        scripts.setdefault(script, copy.deepcopy(entries[script]))
        if reference is None or reference in run_ids:
            continue
        try:
            cfg = runconfig.get(project_root, reference, script=script)
        except runconfig.RunConfigSecretMissing:
            issue(script, "sensitive_run_config")
        except runconfig.RunConfigMissing:
            issue(script, "run_config_missing")
        except (runconfig.RunConfigError, OSError):
            issue(script, "run_config_unavailable")
        else:
            if cfg.sensitive:
                issue(script, "sensitive_run_config")
            else:
                run_ids.add(cfg.id)
                execution["runs"].append({"id": cfg.id, "script": script, "argv": list(cfg.argv)})


def _collect_execution_v1(
    project_root: str | Path,
    registry,
    source_uses: Iterable[Mapping],
    *,
    resource: Callable[[str, str], chartpackage.ResourceFile | None],
) -> CollectedExecution:
    """收集 resolver 已确认的 script/配置；文件批准与封口复用调用方 resource。

    source_uses 每项恰有 script、run_config、run_config_known；不选当前 default
    冒充产物历史。cwd/profile 是当前运行建议，不是旧快照的执行证明。resource
    对主脚本须返回项目内 canonical Path，以便静态绑定与旧源码依据读取同一来源。
    """
    root = Path(project_root).resolve()
    entries = registry.entries()
    scripts, uses, addresses = {}, [], set()
    for raw in source_uses:
        if not isinstance(raw, Mapping) or set(raw) != {
            "script",
            "run_config",
            "run_config_known",
        }:
            _invalid("Invalid resolved execution source")
        script, reference, known = raw["script"], raw["run_config"], raw["run_config_known"]
        if (
            not isinstance(script, str)
            or script not in entries
            or type(known) is not bool
            or (
                reference is not None
                and (not isinstance(reference, str) or not runconfig.ID_RE.fullmatch(reference))
            )
            or (not known and reference is not None)
        ):
            _invalid("Invalid resolved execution identity")
        address = (script, reference, known)
        if address in addresses:
            continue
        addresses.add(address)
        scripts[script] = copy.deepcopy(entries[script])
        uses.append((script, reference, known))

    issues, issue_keys = [], set()

    def issue(script: str, reason: str) -> None:
        if (script, reason) not in issue_keys:
            issue_keys.add((script, reason))
            issues.append({"script": script, "reason": reason})

    for script in scripts:
        issue(script, "dependency_set_undeclared")

    execution = {
        "version": packageexecution.VERSION,
        "registry": {"scripts": scripts},
        "runs": [],
        "contexts": [],
        "remaps": [],
        "coverage": {"state": "incomplete", "issues": issues},
    }
    # 在文件访问之前消费同一 ID/登记判据；不能给任意注册表键拼磁盘路径。
    packageexecution.validate_execution(execution)
    resources: dict[tuple[str, str], chartpackage.ResourceFile] = {}
    attempted: dict[tuple[str, str], chartpackage.ResourceFile | None] = {}
    missing: set[str] = set()

    def take(kind: str, resource_id: str, *, owner: str) -> chartpackage.ResourceFile | None:
        approved = projectenv.contained_path(root, root / resource_id)
        if approved is None:
            issue(owner, "external_input_requires_relink")
            return None
        key = (kind, resource_id)
        if key not in attempted:
            item = resource(kind, resource_id)
            if item is not None:
                if (
                    not isinstance(item, chartpackage.ResourceFile)
                    or item.record.get("kind") != kind
                    or item.record.get("id") != resource_id
                ):
                    _mismatch("Approved resource identity changed")
                if isinstance(item.source, Path):
                    canonical = projectenv.contained_path(root, item.source)
                    if canonical is None:
                        _mismatch("Approved resource escaped the project")
                    if canonical != approved:
                        _mismatch("Approved resource points to another project file")
                    item = chartpackage.ResourceFile(copy.deepcopy(item.record), Path(canonical))
                else:
                    item = chartpackage.ResourceFile(copy.deepcopy(item.record), item.source)
            attempted[key] = item
        item = attempted[key]
        if item is None:
            missing.add(resource_id)
            issue(owner, "resource_missing")
        return item

    def keep(item: chartpackage.ResourceFile) -> None:
        row = item.record
        for (_, resource_id), existing in resources.items():
            if resource_id == row["id"] and existing.record["sha256"] != row["sha256"]:
                _mismatch("Approved resource identities disagree on bytes")
        resources[(row["kind"], row["id"])] = item

    def take_and_keep(kind: str, resource_id: str, *, owner: str):
        item = take(kind, resource_id, owner=owner)
        if item is not None:
            keep(item)
        return item

    rules = inputremap.rules_for(root) if scripts else []
    for rule in rules:
        portable = True
        parsed = figcapture.remap_parts(rule["from"])
        if rule["kind"] != figcapture.REMAP_FILE:
            for script in scripts:
                issue(script, "prefix_members_undeclared")
            portable = False
        if parsed is None or parsed[0] or not parsed[1] or PureWindowsPath(rule["from"]).drive:
            for script in scripts:
                issue(script, "unsupported_absolute_request")
            portable = False
        target = projectenv.contained_path(root, rule["to"])
        if target is None:
            for script in scripts:
                issue(script, "external_input_requires_relink")
            portable = False
        if not portable:
            continue
        target_id = Path(target).relative_to(root.resolve()).as_posix()
        item = None
        for script in scripts:
            item = take_and_keep("file", target_id, owner=script)
        if item is not None:
            execution["remaps"].append(
                {"kind": figcapture.REMAP_FILE, "from": rule["from"], "to_resource": target_id}
            )

    main_sources: dict[str, tuple[Path, str] | None] = {}
    for script in scripts:
        item = take_and_keep("script", script, owner=script)
        if item is None:
            main_sources[script] = None
            continue
        if not isinstance(item.source, Path):
            _mismatch("Main script requires an approved canonical file")
        sha1, sha256, size = hashlib.sha1(), hashlib.sha256(), 0
        with item.source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(64 * 1024), b""):
                sha1.update(chunk)
                sha256.update(chunk)
                size += len(chunk)
        if sha256.hexdigest() != item.record["sha256"] or size != item.record["bytes"]:
            _mismatch("Main script changed after resource approval")
        main_sources[script] = (item.source, sha1.hexdigest())

    run_ids = set()
    for script, reference, known in uses:
        if not known:
            issue(script, "run_config_unresolved")
        elif reference is not None:
            try:
                cfg = runconfig.get(root, reference, script=script)
            except runconfig.RunConfigSecretMissing:
                issue(script, "sensitive_run_config")
            except runconfig.RunConfigMissing:
                issue(script, "run_config_missing")
            else:
                if cfg.sensitive:
                    issue(script, "sensitive_run_config")
                elif cfg.id not in run_ids:
                    run_ids.add(cfg.id)
                    execution["runs"].append(
                        {"id": cfg.id, "script": script, "argv": list(cfg.argv)}
                    )
        mode = workdir.resolve_mode(root, script)
        context = {
            "script": script,
            "run_config": reference,
            "run_config_known": known,
            "cwd_mode": mode,
            "profile": None,
            "basis": [],
        }
        execution["contexts"].append(context)
        main = main_sources[script]
        if main is None:
            issue(script, "source_basis_missing")
            continue
        approved_script, script_sha1 = main
        binding = databinding.binding_for(approved_script, root, mode)
        for resource_id in binding["expected"]:
            take_and_keep("file", resource_id, owner=script)
        if binding["outside"]:
            issue(script, "external_input_requires_relink")
        base = root if mode == workdir.MODE_PROJECT_ROOT else approved_script.parent
        for literal in binding["missing"]:
            remapped = figcapture.remap_target(rules, literal)
            candidate = remapped if remapped is not None else base / literal
            approved = projectenv.contained_path(root, candidate)
            if approved is None:
                issue(script, "external_input_requires_relink")
                continue
            resource_id = Path(approved).relative_to(root.resolve()).as_posix()
            take_and_keep("file", resource_id, owner=script)
        try:
            record = runconfig.launch_record(root, script) if known else None
        except runconfig.RunConfigError:
            record = None
        source = record.get("source") if isinstance(record, dict) else None
        if (
            not isinstance(record, dict)
            or record.get("version") not in launchctx.READABLE_VERSIONS
            or record.get("run_config") != reference
            or not isinstance(source, dict)
            or source.get("basis_observed") is False
            or not isinstance(source.get("basis"), dict)
            or len(source["basis"]) > launchctx.MAX_BASIS
            or not isinstance(source.get("script_sha1"), str)
            or not _SHA1.fullmatch(source["script_sha1"])
        ):
            issue(script, "source_basis_missing")
            continue
        if source["script_sha1"] != script_sha1:
            issue(script, "source_basis_stale")
            continue
        for resource_id, digest in source["basis"].items():
            if (
                not isinstance(resource_id, str)
                or not isinstance(digest, str)
                or not _SHA256.fullmatch(digest)
            ):
                issue(script, "source_basis_stale")
                continue
            if projectenv.contained_path(root, root / resource_id) is None:
                issue(script, "external_input_requires_relink")
                continue
            # basis 也是包引用；先消费原 validator 的路径规则再交给资源回调。
            try:
                documentpackage.project_member_key("assets/" + resource_id)
            except documentpackage.PackageError:
                issue(script, "source_basis_stale")
                continue
            kind = (
                "script"
                if resource_id.rsplit(".", 1)[-1].lower() in figcapture.CODE_SUFFIXES
                else "file"
            )
            item = take(kind, resource_id, owner=script)
            if item is None:
                continue
            if item.record["sha256"] != digest:
                issue(script, "source_basis_stale")
                continue
            keep(item)
            context["basis"].append({"id": resource_id, "sha256": digest})

    execution = packageexecution.validate_execution(execution)
    packageexecution.validate_resources(execution, [item.record for item in resources.values()])
    return CollectedExecution(execution, tuple(resources.values()), tuple(sorted(missing)))
