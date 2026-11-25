"""Launch arguments for an owned R/ggplot2 capture."""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import execspec, executionfact, figcapture, projectenv, runcodes

_RESOURCES = Path(__file__).resolve().parents[1] / "resources" / "native"
_CONTEXTUAL_TEXT_IDENTITY_PREFIX = "r2:"
_LEGEND_IDENTITY_PREFIX = "r3:"
_LINE_IDENTITY_PREFIX = "r4:"


def _write_ggplot_svg(path: Path, raw: str) -> bytes:
    from ..rendercore import svgcheck
    from . import artifactinspect

    path.write_bytes(raw.encode("utf-8"))
    artifactinspect.normalize_worker_svg(path)
    data = path.read_bytes()
    if "<use id='legend." not in raw:
        return data

    # svglite supplies a device-local definition for each marked guide box.
    # Move its exclusive contents to the draw site so repeated panels can
    # preview their own boxes in the same browser document.
    root = svgcheck.parse_svg(data)
    ns = f"{{{svgcheck.SVG_NS}}}"
    defs = {
        group.get("id"): (parent, group)
        for parent in root.iter(ns + "defs")
        for group in parent
        if group.tag == ns + "g"
    }
    uses = list(root.iter(ns + "use"))
    references = Counter(node.get("href") for node in uses)
    for use in uses:
        gid = use.get("id", "")
        if not gid.startswith("legend."):
            continue
        href = use.get("href", "")
        if references[href] != 1 or not href.startswith("#") or href[1:] not in defs:
            raise ValueError("The native SVG legend has no exclusive drawing group.")
        definition, group = defs[href[1:]]
        definition.remove(group)
        del group.attrib["id"]
        use.tag = ns + "g"
        del use.attrib["href"]
        use.append(group)
    ET.register_namespace("", svgcheck.SVG_NS)
    ET.register_namespace("xlink", svgcheck.XLINK_NS)
    data = ET.tostring(root, encoding="utf-8")
    svgcheck.parse_svg(data)
    path.write_bytes(data)
    return data


@dataclass(frozen=True)
class GgplotTarget:
    object_name: str
    frame_pt: tuple[float, float]

    def __post_init__(self):
        if (
            not isinstance(self.object_name, str)
            or not self.object_name.strip()
            or "\0" in self.object_name
        ):
            raise ValueError("Specify the name of a ggplot object.")
        if len(self.frame_pt) != 2 or any(
            type(value) not in (float, int) or not math.isfinite(value) or value <= 0
            for value in self.frame_pt
        ):
            raise ValueError("The SVG frame needs two positive dimensions in points.")

    def stem(self, script: str) -> str:
        identity = json.dumps([script, self.object_name], ensure_ascii=False).encode("utf-8")
        return "ggplot2-" + hashlib.sha256(identity).hexdigest()[:24]

    def command(
        self, spec: execspec.ExecutionSpec, *, host: str, port: int, out_dir: str
    ) -> list[str]:
        script = projectenv.contained_path(spec.project_root, spec.target)
        if (
            spec.target_kind != execspec.TARGET_SCRIPT
            or spec.argv
            or spec.entry is not None
            or script is None
            or Path(script).suffix.lower() != ".r"
            or not Path(script).is_file()
        ):
            raise runcodes.RunError(
                runcodes.DESKTOP_RUN_LAUNCH_INVALID,
                reason="Specify a project R script that creates the named plot.",
            )
        return [
            spec.interpreter,
            "--vanilla",
            str(_RESOURCES / "ggplot_bridge.R"),
            script,
            self.object_name,
            *(str(value) for value in self.frame_pt),
            host,
            str(port),
            str(_RESOURCES / "ggplot2.R"),
            self.stem(spec.target),
            executionfact.new_id(),
            spec.target,
            out_dir,
        ]

    def publish_preview(self, session, response: dict, *, build: bool) -> dict:
        from . import pool

        stem = self.stem(session.spec.target)
        manifest, raw = response.get("manifest"), response.get("svg")
        if (
            not isinstance(manifest, dict)
            or manifest.get("stem") != stem
            or not isinstance(raw, str)
        ):
            raise pool.WorkerError("The R capture returned no matching SVG and manifest.")
        self.annotate_manifest(manifest)
        try:
            with tempfile.TemporaryDirectory(dir=session.out_dir) as temporary:
                svg = Path(temporary) / "figure.svg"
                manifest_part = Path(temporary) / "figure.json"
                data = _write_ggplot_svg(svg, raw)
                manifest_part.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                figcapture.publish_preview(session.out_dir, stem, svg, manifest_part, len(data))
        except (OSError, ValueError) as exc:
            raise pool.WorkerError("The R capture could not publish its preview.") from exc
        response = {**response, "svg": data.decode("utf-8")}
        if build:
            desc = figcapture.build_descriptor(
                script=session.spec.target,
                entry="__main__",
                stem=stem,
                capture_source=figcapture.SOURCE_GGPLOT2,
                execution_profile=figcapture.PROFILE_NATIVE,
                size_mm=manifest["size_mm"],
                source_fingerprint=hashlib.sha256(data).hexdigest(),
            )
            response["descriptors"] = [desc.to_payload()]
        return response

    @staticmethod
    def annotate_manifest(manifest: dict) -> None:
        for element in manifest["elements"]:
            line_context = element.pop("line_context", None)
            if line_context is not None:
                element["identity"] = _LINE_IDENTITY_PREFIX + line_context
                continue
            legend_context = element.pop("legend_context", None)
            if legend_context is not None:
                context = [element["gid"], legend_context]
                element["identity"] = (
                    _LEGEND_IDENTITY_PREFIX
                    + hashlib.sha256(json.dumps(context).encode("utf-8")).hexdigest()[:16]
                )
                continue
            legend_title = element.pop("legend_title", None)
            tick_labels = element.pop("tick_labels", None)
            if legend_title is not None or tick_labels is not None:
                # Use the owning guide or the observed labels as part of the target identity.
                context = [element["gid"]]
                if legend_title is not None:
                    context.append(legend_title)
                context.append(tick_labels if tick_labels is not None else element["label"])
                element["identity"] = (
                    _CONTEXTUAL_TEXT_IDENTITY_PREFIX
                    + hashlib.sha256(json.dumps(context).encode("utf-8")).hexdigest()[:16]
                )
                continue
            label = element["label"].encode("utf-8")
            # Keep plot-title identities already stored in saved overrides.
            if element["gid"] != "plot.title":
                label = element["gid"].encode("utf-8") + b"\0" + label
            element["identity"] = "r1:" + hashlib.sha256(label).hexdigest()[:16]

    @staticmethod
    def validate_edit_targets(session, patches: list) -> None:
        from . import pool

        elements = (session.last_build or {}).get("manifest", {}).get("elements", [])
        identities = {element["gid"]: element["identity"] for element in elements}
        for patch in patches:
            if not isinstance(patch, dict):
                continue
            identity = patch.get("identity")
            gid = patch.get("gid")
            expected = identities.get(gid) if isinstance(gid, str) else None
            if expected is not None and expected.startswith(_LEGEND_IDENTITY_PREFIX):
                if identity != expected:
                    raise pool.WorkerError("The native R legend changed. Select it again.")
                continue
            if expected is not None and expected.startswith(_LINE_IDENTITY_PREFIX):
                if identity != expected:
                    raise pool.WorkerError("The native R curve changed. Select it again.")
                continue
            requires_identity = expected is not None and expected.startswith(
                _CONTEXTUAL_TEXT_IDENTITY_PREFIX
            )
            if (identity is not None or requires_identity) and (
                expected is None or identity != expected
            ):
                raise pool.WorkerError("The native R text target changed. Select it again.")

    @staticmethod
    def acknowledge_export(response: dict, svg_path: str | None) -> dict:
        raw = response.pop("svg_companion", None)
        if svg_path is not None and isinstance(raw, str):
            svg = Path(svg_path)
            try:
                data = _write_ggplot_svg(svg, raw)
            except (OSError, ValueError) as exc:
                svg.unlink(missing_ok=True)
                response["svg_error"] = {"message": str(exc)}
            else:
                response["svg"] = {
                    "path": svg_path,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                }
        return response
