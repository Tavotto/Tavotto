"""Launch arguments for an owned R/ggplot2 capture."""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import execspec, executionfact, figcapture, projectenv, runcodes

_RESOURCES = Path(__file__).resolve().parents[1] / "resources" / "native"
_LEGEND_ENTRY_IDENTITY_PREFIX = "r2:"


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
        from . import artifactinspect, pool

        stem = self.stem(session.spec.target)
        manifest, raw = response.get("manifest"), response.get("svg")
        if (
            not isinstance(manifest, dict)
            or manifest.get("stem") != stem
            or not isinstance(raw, str)
        ):
            raise pool.WorkerError("The R capture returned no matching SVG and manifest.")
        for element in manifest["elements"]:
            legend_title = element.pop("legend_title", None)
            if legend_title is not None:
                # Identical entry labels can belong to different guides at the same slot.
                context = json.dumps([element["gid"], legend_title, element["label"]])
                element["identity"] = (
                    _LEGEND_ENTRY_IDENTITY_PREFIX
                    + hashlib.sha256(context.encode("utf-8")).hexdigest()[:16]
                )
                continue
            label = element["label"].encode("utf-8")
            # Keep plot-title identities already stored in saved overrides.
            if element["gid"] != "plot.title":
                label = element["gid"].encode("utf-8") + b"\0" + label
            element["identity"] = "r1:" + hashlib.sha256(label).hexdigest()[:16]
        try:
            with tempfile.TemporaryDirectory(dir=session.out_dir) as temporary:
                svg = Path(temporary) / "figure.svg"
                manifest_part = Path(temporary) / "figure.json"
                svg.write_bytes(raw.encode("utf-8"))
                artifactinspect.normalize_worker_svg(svg)
                data = svg.read_bytes()
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
            requires_identity = expected is not None and expected.startswith(
                _LEGEND_ENTRY_IDENTITY_PREFIX
            )
            if (identity is not None or requires_identity) and (
                expected is None or identity != expected
            ):
                raise pool.WorkerError("The native R text target changed. Select it again.")

    @staticmethod
    def acknowledge_export(response: dict, svg_path: str | None) -> dict:
        from . import artifactinspect

        raw = response.pop("svg_companion", None)
        if svg_path is not None and isinstance(raw, str):
            svg = Path(svg_path)
            try:
                svg.write_bytes(raw.encode("utf-8"))
                artifactinspect.normalize_worker_svg(svg)
                data = svg.read_bytes()
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
