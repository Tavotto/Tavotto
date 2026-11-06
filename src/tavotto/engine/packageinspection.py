"""已准备 v4 包的完整资源检视；不采用文档、不安装或执行脚本。

容器/INPUT/资源字节仍由 chartpackage 验证；Figure 档案由 svgstore 验证。
这里只核文档与这些已核事实的关系。历史来源与当前登记可以不同；缺失
执行条件以明确缺口返回，不把过去的回执提升为本机运行授权。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .. import pdfbackend
from ..rendercore import svgcheck
from . import (
    config,
    documentformat,
    documentpackage,
    figcapture,
    packageexecution,
    patchspec,
    projectenv,
    registry,
    runtimeasset,
    svgstore,
)

if TYPE_CHECKING:
    from .chartpackage import PreparedChartPackage


def _invalid(reason: str, detail: str) -> None:
    raise documentpackage.PackageError(reason, detail)


def _logical_id(value: str) -> None:
    if not isinstance(value, str):
        _invalid("invalid_manifest", "Resource identity must be a string")
    documentpackage.project_member_key("assets/" + value)


def _figure_facts(plan, *, temporary_root: Path) -> dict:
    prefix = f"{config.PROJECT_STORE_DIRNAME}/{svgstore.STORE_DIRNAME}/"
    store = temporary_root / svgstore.STORE_DIRNAME
    found = False
    for target, item in plan:
        in_store = target.casefold().startswith(prefix.casefold())
        if item.record["kind"] != "figure_snapshot":
            if in_store:
                _invalid("figure_snapshot_namespace", "Figure store namespace is reserved")
            continue
        if not target.startswith(prefix):
            _invalid("figure_snapshot_namespace", "Figure resource has an invalid namespace")
        found = True
        relative = target[len(prefix) :]
        destination = projectenv.contained_path(store, relative)
        if destination is None:
            _invalid("figure_snapshot_namespace", "Figure resource escapes its store")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Only context-owned, already verified numeric stage paths reach here.
        # A changed stage is detected again against the original manifest row.
        shutil.copyfile(item.source, destination)
        digest, size = figcapture.hash_file(destination)
        if (digest, size) != (item.record["sha256"], item.record["bytes"]):
            _invalid("resource_mismatch", "Figure resource changed during inspection")
    if not found:
        return {"snapshots": {}}
    # ZIP resources represent files, not empty managed directories. Marker-only
    # stores and read-only archive copies legitimately omit directory entries.
    for name in ("entries", "snapshots", "sources", "blobs"):
        (store / name).mkdir(parents=True, exist_ok=True)
    try:
        facts = svgstore.inspect_portable_store(storage_root=store)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise documentpackage.PackageError(
            "figure_snapshot_invalid", "Invalid packaged Figure store"
        ) from exc
    # Content-addressed bytes and a re-computable receipt are not proof that the
    # artwork is a readable PDF or safe static SVG. Validate every blob, even
    # an unreferenced historical member. Geometry/replay remains a later gate.
    for name, blob in facts["blobs"].items():
        path = store / "blobs" / name
        if blob["kind"] == "svg":
            with path.open("rb") as source:
                data = source.read(svgcheck.SVGBudget().max_bytes + 1)
            try:
                svgcheck.parse_svg(data)
            except svgcheck.SVGError as exc:
                raise documentpackage.PackageError(
                    "figure_snapshot_invalid", "Invalid packaged Figure SVG"
                ) from exc
        else:
            try:
                info = pdfbackend.probe_asset(path, "pdf", include_pages=True)
            except Exception as exc:
                # Backend availability/timeouts retain their existing product
                # error path. An actual parse failure is a rejected resource.
                if getattr(exc, "code", None) == "render_failed":
                    raise documentpackage.PackageError(
                        "figure_snapshot_invalid", "Unreadable packaged Figure PDF"
                    ) from exc
                raise
            if info.get("pages") != 1:
                _invalid("figure_snapshot_invalid", "Figure PDF must have one page")
    return facts


def inspect_project_package(prepared: PreparedChartPackage) -> dict:
    """核整个活跃 Prepared，返回脱离暂存路径的报告，不证明计算重放已通过。

    原 manifest 声明原样保留；missing/warnings 从已核资源与文档重新计算。
    所有资源目标都返回，不隐藏当前未被面板引用的脚本、数据或历史档案。
    安装方必须在自己的活跃 Prepared 内再次核验，不能使用本报告当写授权。
    """
    plan = prepared.plan_project_resources()
    document, manifest = prepared.document, prepared.manifest
    resources = {
        item.record["id"]: item.record
        for _target, item in plan
        if item.record["kind"] in {"file", "script"}
    }
    missing, warnings = set(), set()

    def warn(reason: str, source_id: str) -> None:
        warnings.add((reason, source_id))

    def file_candidate(value: str) -> bool:
        # A sender's declaration is inventory evidence, never permission to
        # resolve a path on this machine. Preserve non-file/nonportable claims
        # as explicit gaps; an older runtime warning is not a disk filename.
        try:
            _logical_id(value)
        except documentpackage.PackageError:
            warn("declared_resource_unresolved", value)
            return False
        return True

    execution = manifest.get("execution")
    facts, captured_inputs = None, []
    if execution is not None and execution["version"] == packageexecution.CAPTURE_VERSION:
        data_root = config.data_dir()
        data_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="package-inspection-", dir=data_root) as directory:
            facts = _figure_facts(plan, temporary_root=Path(directory))
        captured_inputs = packageexecution.validate_capture_facts(execution, facts)
        for capture in captured_inputs:
            # A fileId can have multiple historical captures. Keep each gap
            # associated with its exact archive, rather than merging by name.
            warn("captured_input_channels_unverified", capture["snapshot_ref"]["snapshotId"])
    captured_scripts = {row["scope"]["script"] for row in captured_inputs}
    current_scripts = {row["script"] for row in execution["contexts"]} if execution else set()

    def script_available(script):
        return script in resources or (script in captured_scripts and script not in current_scripts)

    registered = registry.Registry()
    if execution is not None:
        registered.load_data(execution["registry"], source="<project-package>")
    if captured_inputs:
        captured_refs = {row["snapshot_ref"]["snapshotId"] for row in captured_inputs}
        for canvas in document["canvases"]:
            for obj in canvas.get("objects", []):
                if (
                    not isinstance(obj, dict)
                    or obj.get("type") != "panel"
                    or obj.get("chart") is not None
                ):
                    continue
                reference = obj.get("figureSnapshot") or {}
                if not isinstance(reference, dict):
                    continue  # The reference validator below retains the error contract.
                if reference.get("snapshotId") in captured_refs:
                    continue
                source_id = obj.get("fileId")
                if not isinstance(source_id, str):
                    continue  # The ordinary document validation reports it below.
                resolved = (
                    runtimeasset.resolve(source_id, registered)
                    if runtimeasset.is_runtime_id(source_id)
                    else registered.for_artifact(source_id)
                )
                if resolved is not None:
                    current_scripts.add(resolved["script"])
                owner = reference.get("executionOwner")
                if isinstance(owner, dict) and isinstance(owner.get("script"), str):
                    current_scripts.add(owner["script"])
    declared_scripts = set(manifest.get("scripts", []))
    for descriptor in manifest.get("runtime_assets", []):
        if "script" in descriptor:
            script = descriptor["script"]
            if not isinstance(script, str):
                _invalid("invalid_manifest", "Runtime script declaration must be a string")
            declared_scripts.add(script)
    if execution is not None:
        declared_scripts.update(execution["registry"]["scripts"])
    scripts = {script for script in declared_scripts if file_candidate(script)}
    for script in scripts:
        if not script_available(script):
            missing.add(script)
        if execution is None:
            warn("execution_unrecorded", script)
        elif execution["coverage"]["state"] == "complete":
            # v1 facts do not contain a verifiable full input declaration yet.
            # Preserve the sender's claim without presenting it as verified.
            warn("execution_coverage_unverified", script)
    chart_sources, ordinary_sources = set(), set()
    for canvas in document["canvases"]:
        for obj in canvas.get("objects", []):
            if not isinstance(obj, dict) or obj.get("type") != "panel":
                continue
            source_id = obj.get("fileId")
            if isinstance(source_id, str):
                (chart_sources if obj.get("chart") is not None else ordinary_sources).add(source_id)
    for resource_id in manifest.get("missing_at_pack_time", []):
        if (
            file_candidate(resource_id)
            and resource_id not in resources
            and not (resource_id in captured_scripts and resource_id not in current_scripts)
            and (resource_id not in chart_sources or resource_id in ordinary_sources)
        ):
            missing.add(resource_id)
    runs = {run["id"]: run for run in execution["runs"]} if execution is not None else {}
    captured_ids = {row["snapshot_ref"]["snapshotId"] for row in captured_inputs}
    for capture in captured_inputs:
        scope = capture["scope"]
        if scope["run_config"] is not None:
            run = runs.get(scope["run_config"])
            if run is None or run["script"] != scope["script"]:
                warn("historical_run_config_missing", capture["snapshot_ref"]["snapshotId"])

    data_root = config.data_dir()
    data_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="package-inspection-", dir=data_root) as directory:
        if facts is None:
            facts = _figure_facts(plan, temporary_root=Path(directory))
        snapshots = facts["snapshots"]
        for canvas in document["canvases"]:
            for obj in canvas.get("objects", []):
                if not isinstance(obj, dict) or obj.get("type") != "panel":
                    continue
                source_id = obj.get("fileId")
                if not isinstance(source_id, str) or not source_id.strip() or "\0" in source_id:
                    _invalid("invalid_document", "Panel source identity is missing or invalid")
                if obj.get("chart") is not None:
                    # INPUT can be an older self-contained bundle than the
                    # current panel model. Its bytes/scene, not model equality,
                    # were already checked by the source authority.
                    continue
                is_runtime = runtimeasset.is_runtime_id(source_id)
                if not is_runtime:
                    _logical_id(source_id)
                    if source_id not in resources:
                        missing.add(source_id)
                resolved = (
                    runtimeasset.resolve(source_id, registered)
                    if is_runtime
                    else registered.for_artifact(source_id)
                )
                if is_runtime and resolved is None:
                    warn("runtime_unresolved", source_id)
                if resolved is not None:
                    script = resolved["script"]
                    if not script_available(script):
                        missing.add(script)
                    run_id = resolved.get("run_config")
                    if run_id and run_id not in runs:
                        warn("run_config_missing", source_id)
                reference = obj.get("figureSnapshot")
                if reference is None:
                    if is_runtime or obj.get("overrides"):
                        warn("figure_snapshot_missing", source_id)
                    continue
                try:
                    reference = svgstore.validate_reference(reference)
                except (ValueError, TypeError, RecursionError) as exc:
                    raise documentpackage.PackageError(
                        "figure_snapshot_invalid", "Invalid document Figure reference"
                    ) from exc
                record = snapshots.get(reference["snapshotId"])
                if record is None:
                    _invalid("figure_snapshot_missing", "Referenced Figure archive is missing")
                if (
                    record["reference"] != reference
                    or reference["sourceId"] != source_id
                    or reference["patchHash"] != patchspec.patch_hash(obj.get("overrides") or [])
                ):
                    _invalid(
                        "figure_snapshot_mismatch", "Figure reference and document input disagree"
                    )
                identity = record["identity"]
                static_hash = identity["static_sha256"]
                if static_hash is not None and source_id in resources:
                    if resources[source_id]["sha256"] != static_hash:
                        # An explicit full reference pins the immutable pair,
                        # while the same logical file may now have newer bytes.
                        # Both byte sets were independently checked above. This
                        # does not recover the historical disk original itself.
                        warn("historical_static_source_unavailable", reference["snapshotId"])
                if identity["remap_fingerprint"]:
                    warn("snapshot_remap_requires_relink", source_id)
                owner = record["execution_owner"]
                if owner is not None and (
                    resolved is None or owner["script"] != resolved["script"]
                ):
                    warn("historical_owner_unavailable", source_id)
                if owner is not None and owner["run_config"] is not None:
                    run = runs.get(owner["run_config"])
                    if run is None or run["script"] != owner["script"]:
                        warn(
                            "historical_run_config_missing",
                            reference["snapshotId"]
                            if reference["snapshotId"] in captured_ids
                            else source_id,
                        )
    # Recheck the original numeric stages after semantic inspection. No leased
    # temporary path or previously verified mutable bytes escape this request.
    prepared.plan_project_resources()
    result = {
        "document_format": documentformat.CHART4,
        "doc": document,
        "manifest": manifest,
        "resources": [{"target": target, "record": item.record} for target, item in plan],
        "missing": sorted(missing),
        "warnings": [{"reason": reason, "id": source_id} for reason, source_id in sorted(warnings)],
        "figure_snapshots": len(snapshots),
        "execution_coverage": execution["coverage"] if execution is not None else None,
    }
    if captured_inputs:
        result["captured_inputs"] = captured_inputs
    return result
