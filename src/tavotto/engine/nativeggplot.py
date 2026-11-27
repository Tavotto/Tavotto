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
from .manifest_fields import (
    axis_aspect_field,
    axis_facecolor_field,
    axis_frame_fields,
    axis_grid_fields,
    axis_invert_field,
    axis_limits_field,
    axis_scale_field,
    axis_tick_side_fields,
    bar_paint_fields,
    bar_width_field,
    errorbar_cap_fields,
    errorbar_stroke_fields,
    fill_color_field,
    marker_field,
    opacity_field,
    point_size_field,
)

_RESOURCES = Path(__file__).resolve().parents[1] / "resources" / "native"
GGPLOT_BRIDGE = _RESOURCES / "ggplot_bridge.R"
_CONTEXTUAL_TEXT_IDENTITY_PREFIX = "r2:"
_LEGEND_IDENTITY_PREFIX = "r3:"
_LINE_IDENTITY_PREFIX = "r4:"
_COLOR_SCALE_IDENTITY_PREFIX = "r5:"
_POINT_SERIES_IDENTITY_PREFIX = "r6:"
_BAR_SERIES_IDENTITY_PREFIX = "r7:"
_ERRORBAR_IDENTITY_PREFIX = "r8:"
_RIBBON_IDENTITY_PREFIX = "r9:"
_AXES_IDENTITY_PREFIX = "r10:"


def _clip_facet_curve(element: dict, size_mm: list[float]) -> None:
    """Project panel-local clips onto the shared figure's visible paths."""
    geometry = element.get("geometry")
    if geometry is None:
        return
    paths = geometry["paths"]
    if not any("clip" in path for path in paths):
        return

    from ..rendercore.geometry import clip_polyline

    visible = []
    for path in paths:
        points = [tuple(point) for point in path["points"]]
        clip = path.get("clip")
        if clip is None:
            runs = [points]
        else:
            x, y, w, h = clip
            runs = clip_polyline(points, (x, y, x + w, y + h))
        visible.extend({"points": run, "closed": False} for run in runs)
    geometry["paths"] = visible
    if not visible:
        element["bbox"] = [0.0, 0.0, 0.0, 0.0]
        return

    xs, ys = zip(*(point for path in visible for point in path["points"]), strict=True)
    width_mm = geometry["stroke_pt"] * 25.4 / 72
    px, py = (width_mm / (2 * side) for side in size_mm)
    element["bbox"] = [
        min(xs) - px,
        min(ys) - py,
        max(xs) - min(xs) + 2 * px,
        max(ys) - min(ys) + 2 * py,
    ]


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
    library: str | None = None

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
        if self.library is not None and (
            not isinstance(self.library, str)
            or "\0" in self.library
            or not Path(self.library).is_absolute()
            or not Path(self.library).is_dir()
        ):
            raise ValueError("Select an existing R package library folder.")

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
            str(GGPLOT_BRIDGE),
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
            *([self.library] if self.library is not None else []),
        ]

    def publish_preview(self, session, response: dict, *, build: bool) -> dict:
        from . import pool

        stem = self.stem(session.target_display)
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
                script=session.target_display,
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
            axes = element.pop("native_axes", None)
            if axes is not None:
                GgplotTarget.annotate_axes(element, axes)
                continue
            ribbon = element.pop("ribbon", None)
            if ribbon is not None:
                current = ribbon["current"]
                fields = [fill_color_field(current["facecolor"])]
                if "alpha" in current:
                    fields.append(opacity_field(current["alpha"]))
                for field in fields:
                    prop = field["prop"]
                    if prop in ribbon["edited"]:
                        field["value_original"] = ribbon["original"][prop]
                element["editable"] = fields
                element["identity"] = _RIBBON_IDENTITY_PREFIX + ribbon["context"]
                continue
            errorbar_series = element.pop("errorbar_series", None)
            if errorbar_series is not None:
                GgplotTarget.annotate_errorbar_series(element, errorbar_series)
                continue
            bar_series = element.pop("bar_series", None)
            if bar_series is not None:
                GgplotTarget.annotate_bar_series(element, bar_series)
                continue
            point_series = element.pop("point_series", None)
            if point_series is not None:
                GgplotTarget.annotate_point_series(element, point_series)
                continue
            color_scale = element.pop("color_scale", None)
            if color_scale is not None:
                GgplotTarget.annotate_color_scale(element, color_scale)
                continue
            line_context = element.pop("line_context", None)
            if line_context is not None:
                _clip_facet_curve(element, manifest["size_mm"])
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
    def annotate_axes(element: dict, axes: dict) -> None:
        fields = []
        for axis, original in (axes["original"] or {}).items():
            current = axes["current"][axis]
            limits = axis_limits_field(axis, current["range"])
            scale = axis_scale_field(
                axis, current["mode"], original["options"], strict_options=True
            )
            candidates = [(limits, original["range"]), (scale, original["mode"])]
            if (inverted := current["inverted"]) is not None:
                candidates.append((axis_invert_field(axis, inverted), original["inverted"]))
            links = (axes.get("links") or {}).get(axis)
            for field, source in candidates:
                prop = field["prop"]
                if prop in axes["edited"]:
                    field["value_original"] = source
                if links:
                    element.setdefault("alias_covers", {})[prop] = {prop: links}
                    element.setdefault("alias_pure", []).append(prop)
                fields.append(field)
        if (aspect := axes.get("aspect")) is not None:
            field = axis_aspect_field(aspect["current"])
            if "aspect" in axes["edited"]:
                field["value_original"] = aspect["original"]
            fields.append(field)
        for name, field_factory in (
            ("tick_sides", axis_tick_side_fields),
            ("grid", axis_grid_fields),
            ("frame", axis_frame_fields),
        ):
            group = axes.get(name)
            if group is None:
                continue
            edited = group["edited"] or {}
            actual = field_factory(group.get("current", group["original"] | edited))
            original_values = {f["prop"]: f["value"] for f in field_factory(group["original"])}
            for field in actual:
                prop = field["prop"]
                if prop in edited:
                    field["value_original"] = original_values[prop]
                fields.append(field)
        if (background := axes.get("background")) is not None:
            field = axis_facecolor_field(background["current"])
            if "facecolor" in axes["edited"]:
                field["value_original"] = background["original"]
            fields.append(field)
        element["editable"] = fields
        element["identity"] = _AXES_IDENTITY_PREFIX + axes["context"]

    @staticmethod
    def annotate_errorbar_series(element: dict, series: dict) -> None:
        def stroke(values: dict) -> list[dict]:
            fields = errorbar_stroke_fields(values.get("color"), values["linewidth"])
            if "capsize" in values:
                fields.extend(errorbar_cap_fields(values["capsize"], values["cap_thickness"]))
            if "alpha" in values:
                fields.append(opacity_field(values["alpha"]))
            return fields

        fields = stroke(series["current"])
        original = {field["prop"]: field["value"] for field in stroke(series["original"])}
        for field in fields:
            prop = field["prop"]
            if prop in series["edited"]:
                field["value_original"] = original[prop]
        element["editable"] = fields
        element["identity"] = _ERRORBAR_IDENTITY_PREFIX + series["context"]

    @staticmethod
    def annotate_bar_series(element: dict, series: dict) -> None:
        def bar_fields(values: dict) -> list[dict]:
            fields = bar_paint_fields(values)
            if "bar_width" in values:
                fields.append(bar_width_field(values["bar_width"]))
            if "bar_thickness" in values:
                fields.append(bar_width_field(values["bar_thickness"], horizontal=True))
            if "alpha" in values:
                fields.append(opacity_field(values["alpha"]))
            return fields

        fields = bar_fields(series["current"])
        original_fields = {
            field["prop"]: field["value"] for field in bar_fields(series["original"])
        }
        for field in fields:
            prop = field["prop"]
            if prop in series["edited"]:
                field["value_original"] = original_fields[prop]
        element["editable"] = fields
        element["identity"] = _BAR_SERIES_IDENTITY_PREFIX + series["context"]

    @staticmethod
    def annotate_point_series(element: dict, series: dict) -> None:
        current = series["current"]
        original = series["original"]
        fields = []
        if "size" in current:
            fields.append(point_size_field(current["size"]))
        if "marker" in current:
            edited = "marker" in series["edited"]
            marker = current["marker"] if edited else "original"
            shape = {
                "kind": "named",
                "name": original["marker"] if marker == "original" else marker,
            }
            fields.append(
                marker_field(
                    "marker",
                    marker,
                    ["original", *series["markers"]],
                    shape,
                    {"kind": "named", "name": original["marker"]} if edited else None,
                    strict_options=True,
                )
            )
        if "alpha" in current:
            fields.append(opacity_field(current["alpha"]))
        for field in fields:
            prop = field["prop"]
            if prop in series["edited"]:
                field["value_original"] = "original" if prop == "marker" else original[prop]
        element["editable"] = fields
        element["identity"] = _POINT_SERIES_IDENTITY_PREFIX + series["context"]

    @staticmethod
    def annotate_color_scale(element: dict, scale: dict) -> None:
        from .colorscale import colorbar_layout_fields, colorbar_outline_fields

        fields = colorbar_layout_fields(scale["orientation"], scale["extend"])
        for field in fields:
            field["strict_options"] = True
            if field["prop"] in scale["layout_edited"]:
                field["value_original"] = scale["layout_original"][field["prop"]]
        if scale["limits"] is not None:
            fields.extend(GgplotTarget.color_mapping_fields(scale))
        if (outline := scale.get("outline")) is not None:
            original = outline["original"]
            edited = outline["edited"] or {}
            values = original | edited
            for field in colorbar_outline_fields(
                values["outline_visible"], values["outline_width"]
            ):
                if (prop := field["prop"]) in edited:
                    field["value_original"] = original[prop]
                fields.append(field)
        element["editable"] = fields
        element["colorbar_reversed"] = scale["reversed"]
        element["identity"] = _COLOR_SCALE_IDENTITY_PREFIX + scale["context"]

    @staticmethod
    def color_mapping_fields(scale: dict) -> list[dict]:
        from .colorscale import CMAPS, color_scale_fields

        original = {
            "name": "custom",
            "custom": True,
            "discrete": False,
            "stops": scale["stops"],
        }
        choice = scale["cmap"]
        cmap = {
            "prop": "cmap",
            "type": "enum",
            "value": choice or original["name"],
            "options": CMAPS,
            "group": "颜色映射",
            "strict_options": True,
        }
        if choice is None:
            cmap["cmap_current"] = {**original, "stops": scale["current_stops"]}
        else:
            cmap["cmap_current"] = {
                "name": choice,
                "custom": False,
                "discrete": False,
                "stops": scale["current_stops"],
            }
            cmap["cmap_original"] = original
            cmap["value_original"] = original["name"]
        fields = color_scale_fields(*scale["limits"], cmap, precision=None)
        if scale["range_editable"]:
            for field, value in zip(fields[1:], scale["original_limits"], strict=True):
                if field["value"] != value:
                    field["value_original"] = value
        else:
            fields = [cmap]
        return fields

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
            if expected is not None and expected.startswith(_COLOR_SCALE_IDENTITY_PREFIX):
                if identity != expected:
                    raise pool.WorkerError("The native R colour scale changed. Select it again.")
                continue
            if expected is not None and expected.startswith(_POINT_SERIES_IDENTITY_PREFIX):
                if identity != expected:
                    raise pool.WorkerError("The native R point series changed. Select it again.")
                continue
            if expected is not None and expected.startswith(_BAR_SERIES_IDENTITY_PREFIX):
                if identity != expected:
                    raise pool.WorkerError("The native R bar series changed. Select it again.")
                continue
            if expected is not None and expected.startswith(_ERRORBAR_IDENTITY_PREFIX):
                if identity != expected:
                    raise pool.WorkerError("The native R error bars changed. Select them again.")
                continue
            if expected is not None and expected.startswith(_RIBBON_IDENTITY_PREFIX):
                if identity != expected:
                    raise pool.WorkerError("The native R range fill changed. Select it again.")
                continue
            if expected is not None and expected.startswith(_AXES_IDENTITY_PREFIX):
                if identity != expected:
                    raise pool.WorkerError("The native R axes changed. Select them again.")
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
