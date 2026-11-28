"""Translate shared plot patches into a native, removable R source adjustment."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Callable
from contextlib import ExitStack, suppress
from dataclasses import dataclass, replace
from pathlib import Path

from . import config, execspec, executionfact, nativeggplot, projectenv, runtime, scriptadjust
from .colorscale import CMAPS, COLORBAR_EXTENSIONS, COLORBAR_ORIENTATIONS, PALETTE_CATALOG_PATH
from .fixtools import cmdsandbox, trial, winappcontainer
from .manifest_fields import AXIS_FRAME_SIDES

_R_AXIS_TRANSFORMS = {
    "linear": "scales::transform_identity()",
    "log": "scales::transform_log10()",
    "symlog": "ggplot_transform_symlog()",
    "logit": "scales::transform_logit()",
}
_R_THEME_RECT_CELLS = frozenset({"panel.background", "panel.border"})
_R_THEME_GRID_CELLS = frozenset(
    {
        "panel.grid.major",
        "panel.grid.major.x",
        "panel.grid.major.y",
        "panel.grid.minor.x",
        "panel.grid.minor.y",
    }
)
_R_THEME_STROKE_FIELDS = frozenset({"colour", "linewidth", "linetype", "linejoin", "inherit.blank"})
_R_THEME_FIELDS = {
    "element_rect": _R_THEME_STROKE_FIELDS | {"fill"},
    "element_line": _R_THEME_STROKE_FIELDS | {"lineend", "arrow", "arrow.fill"},
}
_RGB = re.compile(r"#[0-9a-fA-F]{6}\Z")
_RGBA = re.compile(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\Z")
_FACES = {
    ("normal", "normal"): "plain",
    ("bold", "normal"): "bold",
    ("normal", "italic"): "italic",
    ("bold", "italic"): "bold.italic",
}


@dataclass(frozen=True)
class AdjustedRSource:
    data: bytes
    written: int
    residual: list[dict]
    skipped: list[dict]
    view_written: bool = False
    colour_written: bool = False


@dataclass(frozen=True)
class ReplayedRSource:
    source: AdjustedRSource
    original_bytes: bytes
    manifest: dict
    png: bytes | None
    execution_id: str
    pdf: bytes | None = None
    svg: bytes | None = None
    data_fingerprint: str | None = None


class _SourceProgramFailed(scriptadjust.ScriptAdjustError):
    """The isolated interpreter exited unsuccessfully."""


class _RetryWithoutFontFamily(Exception):
    def __init__(self, original: bytes) -> None:
        super().__init__("The source did not run with the selected concrete fonts.")
        self.original = original


def _coordinate_value(
    element: dict, prop: str, value, planned_scale: str | None
) -> list | bool | str | None:
    if prop in ("xscale", "yscale"):
        return value if isinstance(value, str) and value in _R_AXIS_TRANSFORMS else None
    if prop in ("invert_x", "invert_y"):
        return value if type(value) is bool else None
    if prop not in ("xlim", "ylim") or not isinstance(value, list) or len(value) != 2:
        return None
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
        return None
    lower, upper = value
    if not lower < upper:
        return None
    scale = next(f for f in element["editable"] if f["prop"] == f"{prop[0]}scale")
    replay_scale = planned_scale or scale.get("value_original", scale["value"])
    if (replay_scale == "log" and lower <= 0) or (
        replay_scale == "logit" and not (0 < lower < upper < 1)
    ):
        return None
    return value


def _native_coordinate_writes(
    axes: dict[str, dict], patches: list[dict], *, write_scales: bool = True
) -> tuple[dict, set[tuple[str, str]]]:
    """A stock coord_cartesian change affects every native scale group on its axis."""
    candidates = defaultdict(list)
    for patch in patches:
        gid, prop = patch["gid"], patch["prop"]
        element = axes.get(gid, {})
        scope = element.get("source_props", {}).get(prop)
        if scope and not scope.get("facet"):
            candidates[prop].append((gid, scope, patch["value"]))
    coordinates = {}
    written = set()
    scale_props = ("xscale", "yscale") if write_scales else ()
    for prop in (*scale_props, "xlim", "ylim", "invert_x", "invert_y"):
        values = candidates.get(prop)
        if not values:
            continue
        _, scope, value = values[0]
        groups = {item[1]["group"] for item in values}
        planned_scale = coordinates.get(f"{prop[0]}scale") if prop.endswith("lim") else None
        if len(groups) != scope["total"] or any(
            other["total"] != scope["total"]
            or (validated := _coordinate_value(axes[gid], prop, requested, planned_scale)) is None
            or validated != value
            for gid, other, requested in values
        ):
            continue
        coordinates[prop] = value
        written.update((gid, prop) for gid, _, _ in values)
    return coordinates, written


def _native_facet_coordinate_writes(
    axes: dict[str, dict], patches: list[dict], *, write_scales: bool = True
) -> tuple[dict[tuple[str, str], tuple[str, dict]], set[tuple[str, str]]]:
    groups = defaultdict(lambda: defaultdict(list))
    for patch in patches:
        gid, prop = patch["gid"], patch["prop"]
        target = axes.get(gid, {})
        scope = target.get("source_props", {}).get(prop, {})
        if not scope.get("facet") or (not write_scales and prop.endswith("scale")):
            continue
        axis = prop[-1] if prop.startswith("invert_") else prop[0]
        if axis in ("x", "y"):
            groups[axis, scope["group"]][prop].append((gid, scope, patch["value"]))

    views = {}
    written = set()
    for (axis, group), props in groups.items():
        view: dict = {}
        for prop, field in (
            (f"{axis}scale", "mode"),
            (f"{axis}lim", "range"),
            (f"invert_{axis}", "inverted"),
        ):
            changes = props.get(prop, [])
            if not changes:
                continue
            _, scope, value = changes[0]
            facet = _r_facet_selector(scope["facet"])
            if facet is None or any(
                (validated := _coordinate_value(axes[gid], prop, requested, view.get("mode")))
                is None
                or validated != value
                for gid, _, requested in changes
            ):
                continue
            view[field] = value
            views[axis, group] = (facet, view)
            written.update((gid, prop) for gid, _, _ in changes)
    return views, written


def _r_literal(value) -> str | None:
    if value is None:
        return "NA_character_"
    if type(value) is bool:
        return "TRUE" if value else "FALSE"
    if isinstance(value, str) and "\0" not in value:
        return json.dumps(value, ensure_ascii=False)
    if type(value) in (int, float) and math.isfinite(value):
        return json.dumps(value, allow_nan=False)
    return None


def _r_theme_options(elements: dict) -> list[str]:
    options = []
    for cell, element in elements.items():
        kind, fields = element.get("kind"), element.get("fields")
        if cell in _R_THEME_GRID_CELLS and kind == "element_blank" and fields is None:
            options.append(f"    {cell} = ggplot2::element_blank()")
            continue
        cells = _R_THEME_RECT_CELLS if kind == "element_rect" else _R_THEME_GRID_CELLS
        if (
            cell not in cells
            or kind not in _R_THEME_FIELDS
            or not isinstance(fields, dict)
            or not fields.keys() <= _R_THEME_FIELDS[kind]
        ):
            return []
        values = {key: _r_literal(value) for key, value in fields.items()}
        if any(value is None for value in values.values()):
            return []
        args = ", ".join(f"{key} = {value}" for key, value in values.items())
        options.append(f"    {cell} = ggplot2::{kind}({args})")
    return options


def _native_theme_writes(
    axes: dict[str, dict], patches: list[dict]
) -> tuple[list[str], set[tuple[str, str]]]:
    """A plain R theme applies only when all rendered panels adopt the same style."""
    edits = defaultdict(lambda: defaultdict(dict))
    for patch in patches:
        gid, prop = patch["gid"], patch["prop"]
        for name, source in axes.get(gid, {}).get("source_theme", {}).items():
            if prop in source["edited"]:
                edits[name][gid][prop] = patch["value"]
    options = []
    written = set()
    for name, panels in edits.items():
        baseline = axes[next(iter(panels))]["source_theme"][name]
        if baseline["total"] != len(panels) or any(
            axes[gid]["source_theme"][name] != baseline
            or choices != baseline["edited"]
            or (
                name in ("grid", "background")
                and axes[gid].get("source_panel_theme", {}).get("authored")
            )
            for gid, choices in panels.items()
        ):
            continue
        values = _r_theme_options(baseline["elements"])
        if values:
            options.extend(values)
            written.update((gid, prop) for gid, changes in panels.items() for prop in changes)
    return options, written


def _native_panel_theme_writes(
    axes: dict[str, dict], patches: list[dict], global_writes: set[tuple[str, str]]
) -> tuple[dict[str, list[str]], set[tuple[str, str]]]:
    changes = defaultdict(dict)
    for patch in patches:
        gid, prop = patch["gid"], patch["prop"]
        if (gid, prop) not in global_writes and axes.get(gid, {}).get("source_panel_theme"):
            changes[gid][prop] = patch["value"]
    themes = defaultdict(list)
    written = set()
    for gid, properties in changes.items():
        axis = axes[gid]
        facet = _r_facet_selector(axis["source_panel_theme"]["facet"])
        if facet is None:
            continue
        for kind in ("grid", "background"):
            style = axis.get("source_theme", {}).get(kind)
            if not style or any(
                properties.get(prop) != value for prop, value in style["edited"].items()
            ):
                continue
            if options := _r_theme_options(style["elements"]):
                themes[facet].extend(options)
                written.update((gid, prop) for prop in style["edited"])
    return themes, written


def _r_panel_theme_block(name: str, themes: dict[str, list[str]]) -> str:
    recipes = []
    for facet, options in themes.items():
        fields = ",\n".join("    " + setting for setting in options)
        recipes.append(f"    list(facet = {facet}, theme = ggplot2::theme(\n{fields}\n    ))")
    return "\n".join(
        [
            f"base::assign({name}, (function(plot) {{",
            *_r_helpers(
                "ggplot_objects.R", "ggplot_panel_theme_geometry.R", "ggplot_panel_theme_source.R"
            ),
            "  ggplot_with_panel_themes(plot, list(",
            ",\n".join(recipes),
            "  ))",
            f"}})(base::get({name}, inherits = FALSE)))",
        ]
    )


_FRAME_SIDES = {f"spine_{side}" for side in AXIS_FRAME_SIDES}
_FRAME_COLORS = {"spine_color", *(f"{side}_color" for side in _FRAME_SIDES)}
_FRAME_WIDTHS = {"spine_linewidth", *(f"{side}_linewidth" for side in _FRAME_SIDES)}


def _native_frame_style_value(prop: str, value) -> bool:
    if prop in _FRAME_SIDES:
        return type(value) is bool
    if prop in _FRAME_COLORS:
        return isinstance(value, str) and (value == "none" or _RGB.fullmatch(value) is not None)
    return (
        prop in _FRAME_WIDTHS
        and type(value) in (float, int)
        and math.isfinite(value)
        and 0.1 <= value <= 10
    )


def _native_frame_writes(
    axes: dict[str, dict], patches: list[dict], theme_writes: set[tuple[str, str]]
) -> tuple[dict[str, dict], set[tuple[str, str]]]:
    frames = defaultdict(dict)
    written = set()
    for patch in patches:
        gid, prop, value = patch["gid"], patch["prop"], patch["value"]
        source = axes.get(gid, {}).get("source_frame")
        if not source or (gid, prop) in theme_writes or not _native_frame_style_value(prop, value):
            continue
        facet = _r_facet_selector(source["facet"])
        if facet is not None:
            frames[facet][prop] = value
            written.add((gid, prop))
    return frames, written


def _r_frame_block(name: str, frames: dict[str, dict]) -> str:
    recipes = []
    for facet, changes in frames.items():
        style = ", ".join(f"{prop} = {_r_literal(value)}" for prop, value in changes.items())
        recipes.append(f"    list(facet = {facet}, style = list({style}))")
    return "\n".join(
        [
            f"base::assign({name}, (function(plot) {{",
            *_r_helpers(
                "ggplot_objects.R",
                "ggplot_pigment.R",
                "ggplot_frame_geometry.R",
                "ggplot_frame_source.R",
            ),
            "  plot <- ggplot_with_source_frames(plot, list(",
            ",\n".join(recipes),
            "  ))",
            "  plot",
            f"}})(base::get({name}, inherits = FALSE)))",
        ]
    )


def _layer_aesthetic_value(key: str, value) -> bool:
    if key in ("colour", "fill"):
        return value is None or (isinstance(value, str) and _RGBA.fullmatch(value) is not None)
    if key == "linetype":
        return isinstance(value, str) and value in {"solid", "dashed", "dotted", "dotdash"}
    return (
        key in ("size", "shape", "alpha", "stroke", "linewidth")
        and type(value) in (int, float)
        and math.isfinite(value)
    )


_GEOMETRY_IMPLEMENTATIONS = {
    "bars": (
        ("GeomBar", "GeomCol"),
        "ggplot_bar_geometry.R",
        "ggplot_apply_bar_width",
    ),
    "caps": (
        ("GeomErrorbar", "GeomErrorbarh"),
        "ggplot_interval_geometry.R",
        "ggplot_apply_errorbar_caps",
    ),
    "curves": (
        ("GeomLine", "GeomPath"),
        "ggplot_curve_style.R",
        "ggplot_apply_curve_styles",
    ),
    "ribbons": (
        ("GeomRibbon",),
        "ggplot_ribbon_geometry.R",
        "ggplot_apply_ribbon_styles",
    ),
    "fixed_ribbon": (
        ("GeomRibbon", "GeomArea"),
        "ggplot_ribbon_geometry.R",
        "ggplot_apply_fixed_ribbon_paint",
    ),
    "points": (
        ("GeomPoint",),
        "ggplot_point_style.R",
        "ggplot_apply_point_styles",
    ),
}
_GEOMETRY_PROPS = {"bar_width", "bar_thickness", "capsize", "cap_thickness"}


def _curve_style_value(prop: str, value) -> bool:
    if prop == "visible":
        return type(value) is bool
    if prop == "linestyle":
        return isinstance(value, str) and value in {"-", "--", ":", "-."}
    if type(value) not in (int, float) or not math.isfinite(value):
        return False
    return prop == "alpha" and 0 <= value <= 1 or prop == "linewidth" and 0.1 <= value <= 20


def _point_style_value(prop: str, value) -> bool:
    if prop == "marker":
        return isinstance(value, str) and value in {"o", "s", "^", "D"}
    if prop in {"facecolor", "edgecolor"}:
        return isinstance(value, str) and (value == "none" or _RGB.fullmatch(value) is not None)
    if type(value) not in (int, float) or not math.isfinite(value):
        return False
    ranges = {"size": (1, 400), "alpha": (0, 1), "linewidth": (0, 8)}
    return prop in ranges and ranges[prop][0] <= value <= ranges[prop][1]


def _point_recipe_aesthetics(recipe: dict) -> bool:
    channels = recipe.get("aesthetics")
    return (
        isinstance(channels, list)
        and bool(channels)
        and all(
            isinstance(channel, str) and channel in {"colour", "fill", "shape"}
            for channel in channels
        )
        and len(channels) == len(set(channels))
    )


def _ribbon_style_value(prop: str, value) -> bool:
    if prop == "facecolor":
        return isinstance(value, str) and (value == "none" or _RGB.fullmatch(value) is not None)
    return (
        prop == "alpha" and type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
    )


def _native_layer_geometry(source: dict) -> dict | None:
    recipe = source.get("geometry")
    if not isinstance(recipe, dict) or recipe.get("kind") not in _GEOMETRY_IMPLEMENTATIONS:
        return None
    kind, values, facet = recipe["kind"], recipe["values"], recipe["facet"]
    native_kinds = _GEOMETRY_IMPLEMENTATIONS[kind][0]
    if source["geom"] not in native_kinds or not isinstance(values, dict) or not values:
        return None
    if facet is not None and (
        not isinstance(facet, dict)
        or not facet
        or not all(
            isinstance(k, str)
            and _r_literal(k)
            and (v is None or isinstance(v, str))
            and _r_literal(v)
            for k, v in facet.items()
        )
    ):
        return None
    if "category" in recipe:
        category = recipe["category"]
        if (
            not isinstance(category, dict)
            or set(category) != {"value"}
            or not (category["value"] is None or isinstance(category["value"], str))
            or not _r_literal(category["value"])
        ):
            return None
    if kind in {"curves", "points"}:
        validator = _curve_style_value if kind == "curves" else _point_style_value
        if (
            facet is None
            and isinstance(recipe.get("category", {}).get("value"), str)
            and (kind == "curves" or _point_recipe_aesthetics(recipe))
            and all(
                source["edited"].get(prop) == value and validator(prop, value)
                for prop, value in values.items()
            )
        ):
            return recipe
        return None
    if kind in {"ribbons", "fixed_ribbon"}:
        if kind == "ribbons" and ("category" not in recipe or set(values) != {"alpha"}):
            return None
        if kind == "fixed_ribbon" and "category" in recipe:
            return None
        if all(
            source["edited"].get(prop) == value and _ribbon_style_value(prop, value)
            for prop, value in values.items()
        ):
            return recipe
        return None
    for prop, value in values.items():
        if (
            type(value) not in (float, int)
            or not math.isfinite(value)
            or source["edited"].get(prop) != value
        ):
            return None
        if kind == "bars":
            if prop not in {"bar_width", "bar_thickness"} or len(values) != 1 or value <= 0:
                return None
        elif not (
            prop == "capsize" and 0 <= value <= 15 or prop == "cap_thickness" and 0.1 <= value <= 5
        ):
            return None
    return recipe


def _native_layer_writes(
    hot: dict | None, patches: list[dict]
) -> tuple[dict[int, dict], set[tuple[str, str]]]:
    """Fixed paint follows the entire layer. Draw-time recipes can follow facets or data categories."""
    edits = defaultdict(dict)
    for patch in patches:
        edits[patch["gid"]][patch["prop"]] = patch["value"]
    layers = defaultdict(dict)
    for element in (hot or {}).get("elements", ()):
        gid = element["gid"]
        source = element.get("source_layer")
        if not source or edits.get(gid) != source["edited"]:
            continue
        index, count = source["layer"], source["panels"]
        aesthetics = source["aesthetics"] or {}
        geometry = _native_layer_geometry(source)
        kinds = (
            "GeomPoint",
            "GeomLine",
            "GeomPath",
            "GeomCol",
            "GeomBar",
            "GeomErrorbar",
            "GeomErrorbarh",
            "GeomRibbon",
            "GeomArea",
        )
        if (
            type(index) is int
            and index >= 1
            and type(count) is int
            and count >= 1
            and source["geom"] in kinds
            and (aesthetics or geometry)
            and all(_layer_aesthetic_value(k, v) for k, v in aesthetics.items())
        ):
            layers[index][gid] = {**source, "aesthetics": aesthetics, "geometry": geometry}
    plans = {}
    written = set()
    for index, panels in layers.items():
        plan = next(iter(panels.values()))
        if any((p["geom"], p["panels"]) != (plan["geom"], plan["panels"]) for p in panels.values()):
            continue
        aesthetics = plan["aesthetics"]
        scoped_paint = {}
        paint_gids = set()
        if (
            len(panels) == plan["panels"]
            and (len(panels) > 1 or not plan.get("facet"))
            and not plan.get("scoped_paint")
            and all(panel["aesthetics"] == aesthetics for panel in panels.values())
        ):
            if aesthetics:
                paint_gids.update(panels)
        else:
            aesthetics = {}
            for gid, panel in panels.items():
                if panel.get("facet") and panel["aesthetics"]:
                    facet = _r_facet_selector(panel["facet"])
                    if facet is not None:
                        scoped_paint[facet] = panel["aesthetics"]
                        paint_gids.add(gid)
        geometries = {gid: p["geometry"] for gid, p in panels.items() if p["geometry"]}
        recipes = []
        kind = None
        categorical = False
        if geometries:
            first = next(iter(geometries.values()))
            kind = first["kind"]
            selections = {"category" in recipe for recipe in geometries.values()}
            if selections == {True}:
                categorical = True
                recipes = list(geometries.values())
            elif selections == {False}:
                if len(geometries) == plan["panels"] and all(
                    g["values"] == first["values"] for g in geometries.values()
                ):
                    recipes = [{"facet": None, "values": first["values"]}]
                elif all(g["facet"] for g in geometries.values()):
                    recipes = list(geometries.values())
        if aesthetics or scoped_paint or recipes:
            plans[index] = {
                "geom": plan["geom"],
                "aesthetics": aesthetics,
                "scoped_paint": scoped_paint,
                "kind": kind,
                "recipes": recipes,
                "categorical": categorical,
            }
            if recipes:
                written.update((gid, prop) for gid, g in geometries.items() for prop in g["values"])
            written.update(
                (gid, prop) for gid in paint_gids for prop in edits[gid].keys() - _GEOMETRY_PROPS
            )
    return plans, written


def _r_geometry_recipes(recipes: list[dict]) -> str:
    entries = []
    for recipe in recipes:
        fields = recipe["facet"]
        facet = (
            "list("
            + ", ".join(f"{_r_literal(k)} = {_r_literal(v)}" for k, v in fields.items())
            + ")"
            if fields
            else "NULL"
        )
        values = ", ".join(
            f"{prop} = {_r_literal(value)}" for prop, value in recipe["values"].items()
        )
        category = (
            f", category = {_r_literal(recipe['category']['value'])}"
            if "category" in recipe
            else ""
        )
        entries.append(f"list(facet = {facet}, values = list({values}){category})")
    return "list(" + ", ".join(entries) + ")"


def _r_layer_aesthetics(values: dict) -> str:
    return ", ".join(f"{key} = {_r_literal(value)}" for key, value in values.items())


def _r_layer_block(name: str, layers: dict[int, dict]) -> str:
    edits = []
    shared = []
    guide_styles = []
    sources = set()
    observe = any(plan["categorical"] for plan in layers.values())
    for index, plan in sorted(layers.items()):
        values = plan["aesthetics"]
        geom = json.dumps(plan["geom"])
        slot = f"plot$layers[[{index}L]]"
        params = _r_layer_aesthetics(values)
        geometry = ""
        kind = plan["kind"]
        channels = None
        if recipes := plan["recipes"]:
            _, resource, adjust = _GEOMETRY_IMPLEMENTATIONS[kind]
            sources.add(resource)
            native_recipes = _r_geometry_recipes(recipes)
            geometry = f", {native_recipes}, {adjust}"
            if kind in {"points", "ribbons", "fixed_ribbon"}:
                sources.add("ggplot_pigment.R")
            if kind in {"curves", "points"}:
                aesthetics = recipes[0]["aesthetics"] if kind == "points" else ["colour"]
                channels = "c(" + ", ".join(map(_r_literal, aesthetics)) + ")"
                geometry += f", function(data) ggplot_source_series_categories(capture, {index}L, data, {channels})"
                guide_argument = channels if kind == "points" else geom
                guide_type = "point" if kind == "points" else "curve"
                guide_styles.append(
                    f"ggplot_{guide_type}_guide_recipe({guide_argument}, {native_recipes})"
                )
            elif plan["categorical"]:
                channel = "fill" if kind in {"bars", "ribbons"} else "colour"
                geometry += f', function(data) ggplot_observed_categories(capture, {index}L, data, "{channel}")'
        if scoped_paint := plan["scoped_paint"]:
            sources.add("ggplot_layer_paint.R")
            paints = ", ".join(
                f"list(facet = {facet}, aesthetics = list({_r_layer_aesthetics(aesthetics)}))"
                for facet, aesthetics in scoped_paint.items()
            )
            geometry += f", facet_paint = list({paints})"
        edits.append(f"  {slot} <- styled_layer({slot}, {geom}, list({params}){geometry})")
        if channels:
            edits.append(f"  {slot}$tavotto_series_aesthetics <- {channels}")
    observe_plot = ()
    if observe:
        sources.add("ggplot_aesthetic_observation.R")
        observe_plot = (
            "  capture <- new.env(parent = emptyenv())",
            "  plot <- ggplot_observe_colour_maps(plot, capture)",
        )
    decorate = ()
    if sources:
        shared.extend(_r_helpers("ggplot_objects.R", "ggplot_layer_geometry.R", *sorted(sources)))
        decorate = (
            "    if (!is.null(recipes)) layer <- ggplot_set_layer_geometry(layer, recipes, adjust, lookup)",
        )
    if "ggplot_layer_paint.R" in sources:
        decorate += (
            "    if (!is.null(facet_paint)) layer <- ggplot_set_layer_paint(layer, facet_paint)",
        )
    if guide_styles:
        guides = ", ".join(guide_styles)
        edits.append(f"  plot <- ggplot_source_styled_guides(plot, list({guides}))")
    # ggproto resolves its parent lazily. Each call must bind its own layer.
    return "\n".join(
        [
            f"base::assign({name}, (function(plot) {{",
            *shared,
            *observe_plot,
            "  styled_layer <- function(layer, kind, values, recipes = NULL, adjust = NULL, lookup = NULL,",
            "                           facet_paint = NULL) {",
            '    if (!identical(class(layer$geom)[[1L]], kind)) stop("Tavotto style layer changed.")',
            "    aesthetics <- utils::modifyList(layer$aes_params, values)",
            "    force(layer)",
            *decorate,
            "    source_layer <- layer",
            "    ggplot2::ggproto(NULL, source_layer, aes_params = aesthetics)",
            "  }",
            *edits,
            "  plot",
            f"}})(base::get({name}, inherits = FALSE)))",
        ]
    )


def _native_colour_mapping_writes(
    hot: dict | None, patches: list[dict]
) -> tuple[dict[tuple[str, ...], dict], set[tuple[str, str]]]:
    edits = defaultdict(dict)
    for patch in patches:
        if patch["prop"] in {"vmin", "vmax", "cmap"}:
            edits[patch["gid"]][patch["prop"]] = patch["value"]
    plans = {}
    written = set()
    for element in (hot or {}).get("elements", ()):
        gid = element["gid"]
        source = element.get("source_colour_mapping")
        if not source or not edits.get(gid) or edits[gid] != source["edited"]:
            continue
        aesthetics = tuple(source["aesthetics"])
        limits, palette = source["limits"], source["palette"]
        order = source.get("guide_order")
        if order is not None and (
            not isinstance(order, list)
            or len(order) < 2
            or not all(isinstance(key, str) and key and _r_literal(key) for key in order)
            or len(set(order)) != len(order)
        ):
            continue
        if not aesthetics or len(set(aesthetics)) != len(aesthetics):
            continue
        if not set(aesthetics) <= {"colour", "fill"} or aesthetics in plans:
            continue
        if limits is not None and (
            len(limits) != 2
            or not all(type(value) in (int, float) and math.isfinite(value) for value in limits)
            or limits[0] >= limits[1]
        ):
            continue
        if palette is not None and palette not in CMAPS:
            continue
        plans[aesthetics] = source
        written.update((gid, prop) for prop in edits[gid])
    return plans, written


def _r_colour_mapping_block(name: str, scales: dict[tuple[str, ...], dict]) -> str:
    catalog = json.loads(PALETTE_CATALOG_PATH.read_text(encoding="utf-8"))["palettes"]
    resources = ["ggplot_colour_mapping.R"]
    edits = []
    order = next(iter(scales.values())).get("guide_order")
    if order is not None:
        resources.append("ggplot_guide_order.R")
        keys = ", ".join(_r_literal(key) for key in order)
        edits.append(f"  plot <- ggplot_preserve_guide_order(plot, c({keys}))")
    implementation = "\n".join(
        nativeggplot.GGPLOT_BRIDGE.with_name(resource).read_text(encoding="utf-8")
        for resource in resources
    )
    for aesthetics, plan in scales.items():
        channels = "c(" + ", ".join(json.dumps(aesthetic) for aesthetic in aesthetics) + ")"
        limits = plan["limits"]
        bounds = "NULL" if limits is None else "c(" + ", ".join(map(str, limits)) + ")"
        palette = plan["palette"]
        packed = "NULL"
        if palette is not None:
            rgb = catalog[palette]
            chunks = [json.dumps(rgb[start : start + 72]) for start in range(0, len(rgb), 72)]
            packed = "paste0(\n    " + ",\n    ".join(chunks) + "\n  )"
        edits.append(f"  plot <- adjust_scale(plot, {channels}, {bounds}, {packed})")
    return "\n".join(
        [
            f"base::assign({name}, (function(plot) {{",
            *(f"  {line}" if line else "" for line in implementation.splitlines()),
            "  adjust_scale <- function(plot, aesthetics, limits, packed) {",
            "    channel <- aesthetics[[1L]]",
            "    scale <- plot$scales$get_scales(channel)",
            "    if (is.null(scale)) {",
            '      scale <- if (channel == "colour") ggplot2::scale_colour_continuous() else',
            "        ggplot2::scale_fill_continuous()",
            "    }",
            '    if (!identical(class(scale)[[1L]], "ScaleContinuous") ||',
            "        !setequal(scale$aesthetics, aesthetics)) {",
            '      stop("Tavotto colour scale changed.")',
            "    }",
            "    scale <- ggplot_set_colour_mapping(scale$clone(), limits, packed)",
            "    suppressMessages(plot + scale)",
            "  }",
            *edits,
            "  plot",
            f"}})(base::get({name}, inherits = FALSE)))",
        ]
    )


def _r_tick_locator_style(style: dict) -> str | None:
    fields = []
    for prop, value in sorted(style.items()):
        if prop == "major_values" and isinstance(value, list):
            if any(type(item) not in (int, float) or not math.isfinite(item) for item in value):
                return None
            literal = (
                f"c({', '.join(_r_literal(item) for item in value)})" if value else "numeric()"
            )
        elif prop == "major_step" and type(value) in (int, float) and value > 0:
            literal = _r_literal(value)
        elif prop == "major_mode" and value in ("auto", "step", "fixed"):
            literal = _r_literal(value)
        elif prop == "format" and isinstance(value, str):
            literal = _r_literal(value)
        else:
            return None
        if literal is None:
            return None
        fields.append(f"{prop} = {literal}")
    return f"list({', '.join(fields)})" if fields else None


def _r_minor_locator_style(style: dict) -> str | None:
    fields = []
    for prop, value in sorted(style.items()):
        valid = (
            (prop == "minor_visible" and type(value) is bool)
            or (prop == "minor_mode" and value in ("auto", "step"))
            or (prop == "minor_step" and type(value) in (float, int) and value > 0)
            or (prop == "minor_format" and isinstance(value, str))
        )
        literal = _r_literal(value) if valid else None
        if literal is None:
            return None
        fields.append(f"{prop} = {literal}")
    return f"list({', '.join(fields)})" if fields else None


def _native_tick_locator_writes(
    hot: dict | None, patches: list[dict], *, minor: bool = False
) -> tuple[dict[tuple[str, str], str], set[tuple[str, str]]]:
    field = "source_minor_locator" if minor else "source_tick_locator"
    style_literal = _r_minor_locator_style if minor else _r_tick_locator_style
    targets = {
        e["gid"]: e[field]
        for e in (hot or {}).get("elements", ())
        if e["role"] == "ticks" and e.get(field)
    }
    candidates = defaultdict(set)
    bindings = defaultdict(set)
    for patch in patches:
        gid, prop = patch["gid"], patch["prop"]
        target = targets.get(gid)
        if target is None or target["axis"] not in ("x", "y"):
            continue
        style = target["style"]
        if prop not in style or style[prop] != patch["value"]:
            continue
        recipe = style_literal(style)
        facet = _r_facet_selector(target.get("facet"))
        if recipe is not None and facet is not None:
            key = (target["axis"], facet)
            candidates[key].add(recipe)
            bindings[key].add((gid, prop))
    recipes = {key: next(iter(choices)) for key, choices in candidates.items() if len(choices) == 1}
    return recipes, {binding for key in recipes for binding in bindings[key]}


def _r_tick_locator_block(name: str, recipes: dict[tuple[str, str], str]) -> str:
    global_axes = []
    facet_axes = []
    for (axis, facet), recipe in recipes.items():
        if facet == "NULL":
            global_axes.append(f"    {axis} = {recipe}")
        else:
            facet_axes.append(
                f"    list(axis = {_r_literal(axis)}, facet = {facet}, style = {recipe})"
            )
    statements = []
    if global_axes:
        statements.extend(
            ["  plot <- ggplot_with_tick_locators(plot, list(", ",\n".join(global_axes), "  ))"]
        )
    if facet_axes:
        statements.extend(
            ["  plot <- ggplot_with_facet_locators(plot, list(", ",\n".join(facet_axes), "  ))"]
        )
    return "\n".join(
        [
            f"base::assign({name}, (function(plot) {{",
            *_r_helpers("ggplot_objects.R", "ggplot_tick_locators.R"),
            *statements,
            "  plot",
            f"}})(base::get({name}, inherits = FALSE)))",
        ]
    )


def _r_minor_locator_block(name: str, recipes: dict[tuple[str, str], str]) -> str:
    entries = [
        f"    list(axis = {_r_literal(axis)}, facet = {facet}, style = {recipe})"
        for (axis, facet), recipe in recipes.items()
    ]
    return "\n".join(
        [
            f"base::assign({name}, (function(plot) {{",
            *_r_helpers(
                "ggplot_objects.R",
                "ggplot_tick_locators.R",
                "ggplot_minor_ticks.R",
                "ggplot_minor_source.R",
            ),
            "  ggplot_with_minor_locators(plot, list(",
            ",\n".join(entries),
            "  ))",
            f"}})(base::get({name}, inherits = FALSE)))",
        ]
    )


def _r_facet_selector(facet) -> str | None:
    if facet is None:
        return "NULL"
    if (
        not isinstance(facet, dict)
        or not facet
        or not all(
            isinstance(name, str)
            and name
            and _r_literal(name) is not None
            and (category is None or isinstance(category, str) and _r_literal(category) is not None)
            for name, category in facet.items()
        )
    ):
        return None
    return (
        "list("
        + ", ".join(
            f"{_r_literal(name)} = {_r_literal(category)}" for name, category in facet.items()
        )
        + ")"
    )


def _native_tick_label_writes(
    hot: dict | None, patches: list[dict]
) -> tuple[list[str], set[tuple[str, str]]]:
    targets = {
        element["gid"]: element["source_tick_label"]
        for element in (hot or {}).get("elements", ())
        if element["role"] == "ticklabel" and element.get("source_tick_label")
    }
    recipes = []
    written = set()
    for patch in patches:
        gid = patch["gid"]
        plan = targets.get(gid)
        if patch["prop"] != "text" or not plan or patch["value"] != plan["text"]:
            continue
        position, facet, key, value = (
            plan[field] for field in ("position", "facet", "key", "text")
        )
        if position not in ("top", "bottom", "left", "right"):
            continue
        if not all(isinstance(item, str) and _r_literal(item) is not None for item in (key, value)):
            continue
        owner = _r_facet_selector(facet)
        if owner is None:
            continue
        recipes.append(
            f"list(position = {_r_literal(position)}, facet = {owner}, "
            f"key = {_r_literal(key)}, text = {_r_literal(value)})"
        )
        written.add((gid, "text"))
    return recipes, written


def _native_title_text_writes(hot: dict | None, patches: list[dict]) -> dict[str, tuple[str, str]]:
    slots = {"title", "subtitle", "caption", "xlab-t", "xlab-b", "ylab-l", "ylab-r"}
    targets = {
        element["gid"]: element["source_title_text"]
        for element in (hot or {}).get("elements", ())
        if element.get("source_title_text")
    }
    recipes = {}
    for patch in patches:
        gid, value = patch["gid"], patch["value"]
        plan = targets.get(gid)
        if (
            patch["prop"] == "text"
            and plan
            and plan["slot"] in slots
            and isinstance(value, str)
            and value == plan["text"]
            and _r_literal(value) is not None
        ):
            recipes[gid] = (plan["slot"], value)
    return recipes


def _r_title_text_block(name: str, recipes: dict[str, tuple[str, str]]) -> str:
    fields = ", ".join(
        f"{_r_literal(slot)} = {_r_literal(value)}" for slot, value in recipes.values()
    )
    return "\n".join(
        [
            f"base::assign({name}, (function(plot) {{",
            *_r_helpers("ggplot_objects.R", "ggplot_title_text.R"),
            f"  titles <- list({fields})",
            "  plot <- ggplot_relabel_titles(plot, titles)",
            "  plot$coordinates <- ggplot_relabel_coordinates(plot$coordinates, titles)",
            "  plot",
            f"}})(base::get({name}, inherits = FALSE)))",
        ]
    )


def _r_helpers(*resources: str) -> list[str]:
    return [
        f"  {line}" if line else ""
        for resource in resources
        for line in nativeggplot.GGPLOT_BRIDGE.with_name(resource)
        .read_text(encoding="utf-8")
        .splitlines()
    ]


def _native_colourbar_layout_writes(
    hot: dict | None, patches: list[dict]
) -> tuple[list[str], set[tuple[str, str]]]:
    valid = {"orientation": COLORBAR_ORIENTATIONS, "extend": COLORBAR_EXTENSIONS}
    edits = defaultdict(dict)
    for patch in patches:
        if patch["prop"] in valid:
            edits[patch["gid"]][patch["prop"]] = patch["value"]
    recipes = []
    written = set()
    for element in (hot or {}).get("elements", ()):
        gid = element["gid"]
        plan = element.get("source_colourbar_layout")
        if element["role"] != "colorbar" or not plan or edits[gid] != plan["edited"]:
            continue
        scope = plan["scope"]
        aesthetics, position, title = (scope[key] for key in ("aesthetics", "position", "title"))
        if not aesthetics or any(aesthetic not in ("colour", "fill") for aesthetic in aesthetics):
            continue
        if position not in (None, "top", "bottom", "left", "right", "inside"):
            continue
        if title is not None and (not isinstance(title, str) or _r_literal(title) is None):
            continue
        if not edits[gid] or any(value not in valid[prop] for prop, value in edits[gid].items()):
            continue
        channels = "list(" + ", ".join(_r_literal(aesthetic) for aesthetic in aesthetics) + ")"
        target = (
            f"list(aesthetics = {channels}, position = {_r_literal(position) if position else 'NULL'}, "
            f"title = {_r_literal(title) if title is not None else 'NULL'})"
        )
        fields = ", ".join(f"{prop} = {_r_literal(value)}" for prop, value in edits[gid].items())
        recipes.append(f"list(scope = {target}, {fields})")
        written.update((gid, prop) for prop in edits[gid])
    return recipes, written


def _r_colourbar_layout_block(name: str, recipes: list[str]) -> str:
    return "\n".join(
        [
            f"base::assign({name}, (function(plot) {{",
            *_r_helpers("ggplot_objects.R", "ggplot_colourbar_layout.R"),
            "  ggplot_with_colourbar_layout(plot, list(",
            ",\n".join(f"    {recipe}" for recipe in recipes),
            "  ))",
            f"}})(base::get({name}, inherits = FALSE)))",
        ]
    )


def _r_tick_labels_block(name: str, recipes: list[str]) -> str:
    helpers = _r_helpers("ggplot_objects.R", "ggplot_tick_source.R")
    return "\n".join(
        [
            f"base::assign({name}, (function(plot) {{",
            *helpers,
            "  ggplot_with_tick_labels(plot, list(",
            ",\n".join(f"    {recipe}" for recipe in recipes),
            "  ))",
            f"}})(base::get({name}, inherits = FALSE)))",
        ]
    )


def _r_coordinate_block(name: str, coordinates: dict, *, mirror_raster: bool) -> str:
    edits = []
    resources = []
    if any(coordinates.get(prop) == "symlog" for prop in ("xscale", "yscale")):
        resources.append("ggplot_transforms.R")
    draw = []
    if mirror_raster:
        resources.append("ggplot_raster_geometry.R")
        draw = [
            "  plot$layers <- lapply(plot$layers, ggplot_mirror_raster_layer, coord = plot$coordinates)",
        ]
    if any(prop in coordinates for prop in ("xscale", "yscale")):
        edits.append("  plot$scales <- plot$scales$clone()")
    for axis in ("x", "y"):
        if (mode := coordinates.get(f"{axis}scale")) is not None:
            transform = _R_AXIS_TRANSFORMS[mode]
            edits.extend(
                (
                    f'  scale <- plot$scales$get_scales("{axis}")',
                    "  if (is.null(scale)) {",
                    f"    scale <- ggplot2::scale_{axis}_continuous(transform = {transform})",
                    "  } else {",
                    "    scale <- scale$clone()",
                    f"    scale$trans <- {transform}",
                    "  }",
                    "  suppressMessages(plot$scales$add(scale))",
                )
            )
    for axis, sides in (("x", '"left", "right"'), ("y", '"bottom", "top"')):
        if (limits := coordinates.get(f"{axis}lim")) is not None:
            pair = ", ".join(json.dumps(v, allow_nan=False) for v in limits)
            edits.extend((f"  limits${axis} <- c({pair})", f"  expand[c({sides})] <- FALSE"))
        if (invert := coordinates.get(f"invert_{axis}")) is not None:
            edits.append(f'  reversed["{axis}"] <- {"TRUE" if invert else "FALSE"}')
    return "\n".join(
        [
            f"base::assign({name}, (function(plot) {{",
            *_r_helpers(*resources),
            "  coord <- plot$coordinates",
            '  if (!identical(class(coord)[[1L]], "CoordCartesian")) stop("Tavotto coordinates need coord_cartesian().")',
            '  expand <- stats::setNames(coord$setup_params(list())$expand, c("top", "right", "bottom", "left"))',
            "  limits <- coord$limits",
            '  reversed <- c(x = grepl("x", coord$reverse, fixed = TRUE), y = grepl("y", coord$reverse, fixed = TRUE))',
            *edits,
            '  direction <- paste(names(reversed)[reversed], collapse = "")',
            '  if (!nzchar(direction)) direction <- "none"',
            "  plot$coordinates <- ggplot2::ggproto(",
            "    NULL, coord, limits = limits, expand = expand, reverse = direction",
            "  )",
            *draw,
            "  plot",
            f"}})(base::get({name}, inherits = FALSE)))",
        ]
    )


def _r_facet_coordinate_block(name: str, views: dict, *, mirror_raster: bool) -> str:
    resources = [
        "ggplot_objects.R",
        "ggplot_transforms.R",
        "ggplot_axis_projection.R",
        "ggplot_facet_view_source.R",
    ]
    recipes = []
    for (axis, _group), (facet, view) in views.items():
        values = []
        for field, value in view.items():
            if field == "range":
                literal = "c(" + ", ".join(map(_r_literal, value)) + ")"
            else:
                literal = _r_literal(value)
            values.append(f"{field} = {literal}")
        properties = ", ".join(values)
        recipes.append(f'    list(axis = "{axis}", facet = {facet}, view = list({properties}))')
    draw = []
    if mirror_raster:
        resources.append("ggplot_raster_geometry.R")
        draw.append(
            "  plot$layers <- lapply(plot$layers, ggplot_mirror_raster_layer, coord = plot$coordinates)"
        )
    return "\n".join(
        [
            f"base::assign({name}, (function(plot) {{",
            *_r_helpers(*resources),
            "  plot <- ggplot_with_facet_views(plot, list(",
            ",\n".join(recipes),
            "  ))",
            *draw,
            "  plot",
            f"}})(base::get({name}, inherits = FALSE)))",
        ]
    )


def _rebind_residual(source: AdjustedRSource, hot: dict | None, cold: dict) -> AdjustedRSource:
    if not (source.view_written or source.colour_written) or not hot:
        return source
    bound = {e["gid"]: e for e in hot["elements"]}
    targets = {e["gid"]: e for e in cold["elements"]}
    residual = []
    for patch in source.residual:
        prior, current = bound.get(patch["gid"]), targets.get(patch["gid"])
        identity = patch.get("identity")
        if identity is not None and current and identity != current["identity"]:
            if (
                not prior
                or prior["identity"] != identity
                or not (
                    source.view_written
                    and _same_coordinate_target(prior, current, bound, targets)
                    or source.colour_written
                    and _same_colour_target(prior, current)
                )
            ):
                raise scriptadjust.ScriptAdjustError(
                    "script_export_diverged", "A retained R edit no longer matches its target."
                )
            patch = {**patch, "identity": current["identity"]}
        residual.append(patch)
    return replace(source, residual=residual)


def _same_colour_target(prior: dict, current: dict) -> bool:
    anchor = prior.get("colour_source_anchor")
    return bool(
        anchor
        and prior["role"] == current["role"]
        and anchor == current.get("colour_source_anchor")
    )


def _same_coordinate_target(prior: dict, current: dict, before: dict, after: dict) -> bool:
    if prior["role"] != current["role"]:
        return False
    if prior["role"] == "ticks":
        host = prior.get("tick_host")
        if not host or host != current.get("tick_host"):
            return False
        prior, current = before.get(host["axes_gid"], {}), after.get(host["axes_gid"], {})
    anchor = prior.get("coordinate_source_anchor")
    return anchor is not None and anchor == current.get("coordinate_source_anchor")


def build(
    data: bytes,
    object_name: str,
    patches: list[dict],
    end_line: int,
    end_byte: int,
    *,
    themes: dict[str, str],
    hot: dict | None = None,
    tick_themes: dict[str, str] | None = None,
    write_font_family: bool = True,
) -> AdjustedRSource:
    """Insert the generated R block after the binding located by R's parser."""
    if b"\0" in data:
        raise scriptadjust.ScriptAdjustError(
            "script_export_unreadable", "The R source contains NUL."
        )
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise scriptadjust.ScriptAdjustError(
            "script_export_unreadable", "The R source is not UTF-8."
        ) from exc
    lines = data.splitlines(keepends=True)
    if 1 <= end_line <= len(lines) and 1 <= end_byte <= len(lines[end_line - 1]):
        tail = lines[end_line - 1][end_byte:].lstrip()
    else:
        tail = b"!"
    if tail and not tail.startswith(b"#"):
        raise scriptadjust.ScriptAdjustError(
            "script_export_unreadable", "The ggplot assignment must end on its own line."
        )

    # A second edit follows Tavotto blocks already attached to this binding.
    # Their older values remain the default for properties absent from this edit.
    position = end_line
    begin, end = scriptadjust.BEGIN.encode(), scriptadjust.END.encode()
    while position < len(lines):
        first = position
        while first < len(lines) and not lines[first].strip():
            first += 1
        if first == len(lines) or lines[first].strip() != begin:
            break
        after = next((i + 1 for i in range(first + 1, len(lines)) if lines[i].strip() == end), None)
        if after is None:
            raise scriptadjust.ScriptAdjustError(
                "script_export_unreadable", "An existing Tavotto R adjustment has no end marker."
            )
        position = after

    text_themes = {**themes, **(tick_themes or {})}
    text_fields = {
        item["gid"]: {
            field["prop"]: field
            for field in item.get("editable", ())
            if field.get("prop") in ("weight", "style", "fontfamily")
        }
        for item in (hot or {}).get("elements", ())
        if item["gid"] in text_themes
    }
    elements: dict[str, dict[str, str | float]] = {}
    axes = {e["gid"]: e for e in (hot or {}).get("elements", ()) if e["role"] == "axes"}
    coordinates, coordinate_writes = _native_coordinate_writes(axes, patches)
    facet_views, facet_view_writes = _native_facet_coordinate_writes(axes, patches)
    theme_options, theme_writes = _native_theme_writes(axes, patches)
    panel_themes, panel_writes = _native_panel_theme_writes(axes, patches, theme_writes)
    frames, frame_writes = _native_frame_writes(axes, patches, theme_writes)
    source_layers, layer_writes = _native_layer_writes(hot, patches)
    colour_scales, colour_writes = _native_colour_mapping_writes(hot, patches)
    tick_labels, tick_writes = _native_tick_label_writes(hot, patches)
    locators, locator_writes = _native_tick_locator_writes(hot, patches)
    minor_locators, minor_writes = _native_tick_locator_writes(hot, patches, minor=True)
    bar_layouts, bar_writes = _native_colourbar_layout_writes(hot, patches)
    titles = _native_title_text_writes(hot, patches)
    title_writes = {(gid, "text") for gid in titles}
    written = (
        coordinate_writes
        | facet_view_writes
        | theme_writes
        | frame_writes
        | panel_writes
        | layer_writes
        | colour_writes
        | tick_writes
        | bar_writes
    )
    written.update(title_writes | locator_writes | minor_writes)
    residual = []
    skipped = []
    for patch in patches:
        gid, prop, value = patch["gid"], patch["prop"], patch["value"]
        if (gid, prop) in written:
            continue
        element = text_themes.get(gid)
        if element is not None:
            fields = text_fields.get(gid, {})
            weight = fields.get("weight", {}).get("value")
            style = fields.get("style", {}).get("value")
            face = _FACES.get((weight, style))
            family = fields.get("fontfamily", {})
            resolved = family.get("resolved_family")
            if prop == "color" and isinstance(value, str) and _RGB.fullmatch(value):
                elements.setdefault(element, {})["colour"] = value
            elif prop == "fontsize" and type(value) in (int, float) and 3 <= value <= 200:
                elements.setdefault(element, {})["size"] = value
            elif (
                prop == "fontfamily"
                and write_font_family
                and isinstance(resolved, str)
                and resolved
                and "\0" not in resolved
                and family.get("value") == value
            ):
                elements.setdefault(element, {})["family"] = resolved
            elif (
                prop == "rotation"
                and tick_themes is not None
                and gid in tick_themes
                and type(value) in (int, float)
                and -90 <= value <= 90
            ):
                elements.setdefault(element, {})["angle"] = value
            elif (
                prop in ("weight", "style") and face is not None and fields[prop]["value"] == value
            ):
                elements.setdefault(element, {})["face"] = face
            else:
                element = None
        if element is not None:
            written.add((gid, prop))
            continue
        residual.append(patch)
        skipped.append({"gid": gid, "prop": prop, "reason": "unsupported"})
    # Scale edits change the native baseline position data. Until residual
    # edits can bind against that new baseline, leave the scale in the layout
    # whenever an unrelated override still needs to replay.
    if residual:
        for gid in {patch["gid"] for patch in residual} & titles.keys():
            # Retained style must still belong to the title on its original binding.
            del titles[gid]
            written.discard((gid, "text"))
        if any(prop in coordinates for prop in ("xscale", "yscale")):
            coordinates, fallback_writes = _native_coordinate_writes(
                axes, patches, write_scales=False
            )
            written.difference_update(coordinate_writes - fallback_writes)
        if any("mode" in view for _, view in facet_views.values()):
            facet_views, fallback_writes = _native_facet_coordinate_writes(
                axes, patches, write_scales=False
            )
            written.difference_update(facet_view_writes - fallback_writes)
        # A change of mapping updates guide identities. Carry along the guide
        # edits whose same scale and channel association can be checked on replay.
        anchored = {
            e["gid"] for e in (hot or {}).get("elements", ()) if e.get("colour_source_anchor")
        }
        if any(p["gid"] not in anchored for p in residual):
            colour_scales = {}
            written.difference_update(colour_writes)
        residual = [patch for patch in patches if (patch["gid"], patch["prop"]) not in written]
        skipped = [
            {"gid": patch["gid"], "prop": patch["prop"], "reason": "unsupported"}
            for patch in residual
        ]
    if not (
        elements
        or coordinates
        or facet_views
        or theme_options
        or frames
        or panel_themes
        or source_layers
        or colour_scales
        or tick_labels
        or locators
        or minor_locators
        or bar_layouts
        or titles
    ):
        return AdjustedRSource(data, 0, residual, skipped)

    name = json.dumps(object_name, ensure_ascii=False)
    options = [
        f"    {element} = ggplot2::element_text("
        + ", ".join(
            f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items()
        )
        + ")"
        for element, fields in elements.items()
    ]
    options.extend(theme_options)
    statements = []
    if coordinates:
        mirror_raster = any(
            prop in coordinates and axes[gid]["source_props"][prop].get("raster") is True
            for gid, prop in coordinate_writes
        )
        statements.append(_r_coordinate_block(name, coordinates, mirror_raster=mirror_raster))
    if facet_views:
        mirror_raster = any(
            prop.startswith("invert_") and axes[gid]["source_props"][prop].get("raster") is True
            for gid, prop in facet_view_writes & written
        )
        statements.append(_r_facet_coordinate_block(name, facet_views, mirror_raster=mirror_raster))
    if source_layers:
        statements.append(_r_layer_block(name, source_layers))
    if colour_scales:
        statements.append(_r_colour_mapping_block(name, colour_scales))
    if locators:
        statements.append(_r_tick_locator_block(name, locators))
    if minor_locators:
        statements.append(_r_minor_locator_block(name, minor_locators))
    if tick_labels:
        statements.append(_r_tick_labels_block(name, tick_labels))
    if bar_layouts:
        statements.append(_r_colourbar_layout_block(name, bar_layouts))
    if titles:
        statements.append(_r_title_text_block(name, titles))
    if frames:
        statements.append(_r_frame_block(name, frames))
    if panel_themes:
        statements.append(_r_panel_theme_block(name, panel_themes))
    if options:
        args = ",\n".join(options)
        statements.append(
            f"base::assign({name}, base::get({name}, inherits = FALSE) + ggplot2::theme(\n"
            f"{args}\n))"
        )
    block = "\n".join(("", scriptadjust.BEGIN, *statements, scriptadjust.END, ""))
    newline = b"\r\n" if lines[end_line - 1].endswith(b"\r\n") else b"\n"
    prefix = b"".join(lines[:position])
    if not prefix.endswith(newline):
        prefix += newline
    result = prefix + block.encode().replace(b"\n", newline) + b"".join(lines[position:])
    return AdjustedRSource(
        result,
        len(written),
        residual,
        skipped,
        bool(coordinates or facet_views),
        bool(colour_scales or bar_layouts),
    )


def _run(command: list[str], cwd: Path, environment: dict[str, str]) -> None:
    with subprocess.Popen(  # noqa: S603 - the interpreter belongs to the selected native session
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=runtime.CREATE_NO_WINDOW,
        start_new_session=True,
    ) as process:
        try:
            status = process.wait(timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise scriptadjust.ScriptAdjustError(
                "script_export_run_failed", "The isolated R replay timed out."
            ) from exc
        finally:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        if status:
            raise _SourceProgramFailed(
                "script_export_run_failed", "The selected plot could not be replayed from R source."
            )


def available() -> bool:
    """Is the protected source replay available on this machine?"""
    if os.name == "nt":
        return winappcontainer.available()
    return cmdsandbox.sandbox_kind(offline=True) is not None


def _copy_windows_runtime_folder(source: Path, destination: Path) -> None:
    def redirect(path):
        info = path.lstat()
        return stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )

    def reject_redirects(directory, entries):
        for name in entries:
            if redirect(Path(directory) / name):
                raise winappcontainer.Unavailable(
                    "The R runtime contains an external path redirect."
                )
        return ()

    if redirect(source):
        raise winappcontainer.Unavailable("The R runtime contains an external path redirect.")
    shutil.copytree(source, destination, ignore=reject_redirects)


def _stage_windows_runtime(interpreter: str, scratch: Path, environment: dict[str, str]) -> Path:
    launcher = Path(interpreter).resolve(strict=True)
    bin_dir = launcher.parent
    root = bin_dir.parent if bin_dir.name.lower() == "bin" else bin_dir.parent.parent
    choices = (bin_dir / "Rterm.exe", root / "bin" / "x64" / "Rterm.exe")
    terminal = next((path.resolve() for path in choices if path.is_file()), None)
    if (
        terminal is None
        or not terminal.is_relative_to(root)
        or not (terminal.parent / "R.dll").is_file()
        or not (root / "library" / "base").is_dir()
        or not (root / "etc").is_dir()
    ):
        raise winappcontainer.Unavailable("The selected R installation could not be staged.")

    # Only the already selected R binary and base functions run here. No
    # user source or startup profiles are loaded during library discovery.
    discovery_env = {key: value for key, value in environment.items() if key.upper() != "R_TESTS"}
    discovery_env.update(
        R_DEFAULT_PACKAGES="NULL", R_SCRIPT_DEFAULT_PACKAGES="NULL", R_ENABLE_JIT="0"
    )
    listing = subprocess.run(  # noqa: S603 - the native session attested the selected interpreter
        [
            interpreter,
            "--vanilla",
            "--default-packages=NULL",
            "-e",
            "writeLines(enc2utf8(.libPaths()), useBytes = TRUE)",
        ],
        cwd=bin_dir,
        env=discovery_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=runtime.CREATE_NO_WINDOW,
        timeout=30,
        check=True,
        encoding="utf-8",
    )
    staged = scratch / "r-runtime"
    staged.mkdir()
    for name in ("bin", "etc", "library", "modules", "share"):
        folder = root / name
        if folder.is_dir():
            _copy_windows_runtime_folder(folder, staged / name)
    libraries = []
    paths = dict.fromkeys(
        Path(row).resolve() for row in listing.stdout.splitlines() if Path(row).is_absolute()
    )
    for index, path in enumerate(paths):
        if path == root / "library":
            continue
        packages = [package for package in path.iterdir() if (package / "DESCRIPTION").is_file()]
        if not packages:
            continue
        destination = scratch / f"r-library-{index}"
        destination.mkdir()
        for package in packages:
            if package.resolve() != package or getattr(package, "is_junction", lambda: False)():
                raise winappcontainer.Unavailable(
                    "The R library contains an external path redirect."
                )
            _copy_windows_runtime_folder(package, destination / package.name)
        libraries.append(str(destination))
    home = scratch / "r-home"
    home.mkdir()
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        environment[key] = str(home)
    for key in ("R_LIBS", "R_LIBS_SITE", "R_ENVIRON_USER", "R_PROFILE_USER", "R_TESTS"):
        environment.pop(key, None)
    executable = staged / terminal.relative_to(root)
    environment.update(
        R_HOME=str(staged),
        R_USER=str(home),
        R_LIBS_USER=os.pathsep.join(libraries),
        TAVOTTO_R_REPLAY_ROOT=str(scratch.resolve(strict=True)),
        PATH=os.pathsep.join(
            (str(executable.parent), str(Path(os.environ["SystemRoot"]) / "System32"))
        ),
    )
    return executable


def _stage_windows_fonts(paths: list[str], scratch: Path) -> list[str]:
    from ..rendercore.userfonts import FONT_SUFFIXES, MAX_FONT_BYTES, system_font_dirs

    roots = tuple(path.absolute() for path in system_font_dirs())
    copied = []
    for path in dict.fromkeys(paths):
        source = Path(path)
        if source.suffix.lower() not in FONT_SUFFIXES or not any(
            source.is_relative_to(root) for root in roots
        ):
            continue
        source = source.resolve(strict=True)
        if not any(source.is_relative_to(root) for root in roots) or not source.is_file():
            continue
        if source.stat().st_size > MAX_FONT_BYTES:
            continue
        destination = scratch / "fonts" / str(len(copied)) / source.name
        destination.parent.mkdir(parents=True)
        shutil.copyfile(source, destination)
        copied.append(destination.as_posix())
    return copied


def replay(
    spec: execspec.ExecutionSpec,
    target: nativeggplot.GgplotTarget,
    stem: str,
    patches: list[dict],
    width: int,
    *,
    hot: dict | None = None,
    vectors: bool = False,
    fonts: list[str] | None = None,
    check_cancelled: Callable[[], None] | None = None,
    include_data_fingerprint: bool = False,
) -> ReplayedRSource:
    """Run the candidate in a protected copy using the recorded native interpreter."""
    try:
        return _replay(
            spec,
            target,
            stem,
            patches,
            width,
            hot=hot,
            vectors=vectors,
            fonts=fonts,
            include_data_fingerprint=include_data_fingerprint,
        )
    except _RetryWithoutFontFamily as exc:
        # The source's own PDF device can reject a name supported by Tavotto's
        # devices. Keep the font edits in the document and replay from a fresh
        # copy so the original program still runs and the pixel gate still applies.
        if check_cancelled is not None:
            check_cancelled()
        return _replay(
            spec,
            target,
            stem,
            patches,
            width,
            hot=hot,
            vectors=vectors,
            fonts=fonts,
            include_data_fingerprint=include_data_fingerprint,
            original_for_retry=exc.original,
        )


def _replay(
    spec: execspec.ExecutionSpec,
    target: nativeggplot.GgplotTarget,
    stem: str,
    patches: list[dict],
    width: int,
    *,
    hot: dict | None,
    vectors: bool,
    fonts: list[str] | None,
    include_data_fingerprint: bool,
    original_for_retry: bytes | None = None,
) -> ReplayedRSource:
    windows = os.name == "nt"
    sandbox = None if windows else cmdsandbox.sandbox_kind(offline=True)
    if not (winappcontainer.available() if windows else sandbox is not None):
        raise scriptadjust.ScriptAdjustError(
            "script_export_r_source_unavailable", "Protected R source replay is unavailable."
        )
    project = Path(spec.project_root).resolve()
    source_path = projectenv.contained_path(project, spec.target)
    if source_path is None:
        raise scriptadjust.ScriptAdjustError("script_export_no_script", "The R source disappeared.")
    script = Path(source_path)
    original = script.read_bytes()
    if original_for_retry is not None and original != original_for_retry:
        raise scriptadjust.ScriptAdjustError(
            "script_export_script_changed", "The R source changed before replay."
        )
    pending = config.data_path("cache", "ggplot-source")
    pending.mkdir(parents=True, exist_ok=True)
    if pending.resolve().is_relative_to(project):
        raise scriptadjust.ScriptAdjustError(
            "script_export_run_failed", "The isolated R workspace must be outside the project."
        )
    bridge = nativeggplot.GGPLOT_BRIDGE
    driver = bridge.with_name("ggplot_source_replay.R")
    adapter = bridge.with_name("ggplot2.R")
    palettes = bridge.parent.parent / "color_palettes.json"
    with (
        tempfile.TemporaryDirectory(prefix="replay-", dir=pending) as temp,
        ExitStack() as resources,
    ):
        scratch = Path(temp)
        copy = scratch / "project"
        _, complete = trial._copy_project(project, script, copy)  # noqa: SLF001 - shared trial copy
        if not complete:
            raise scriptadjust.ScriptAdjustError(
                "script_export_run_failed", "The R project could not be copied for replay."
            )
        staged = copy / script.relative_to(project)
        cwd = copy / Path(spec.cwd).resolve().relative_to(project)
        if staged.read_bytes() != original:
            raise scriptadjust.ScriptAdjustError(
                "script_export_script_changed", "The R source changed before replay."
            )
        request = scratch / "request.json"
        result = scratch / "result.json"
        png = scratch / "candidate.png"
        pdf = scratch / "candidate.pdf"
        svg = scratch / "candidate.svg"
        environment = cmdsandbox.clean_env({**os.environ, **(spec.env or {})})
        r_temp = scratch / "r-tmp"
        r_temp.mkdir()
        environment["TMPDIR"] = os.path.relpath(r_temp, cwd)
        staged_fonts = []
        if target.library:
            env_library = environment.get("R_LIBS_USER")
            environment["R_LIBS_USER"] = (
                os.pathsep.join((target.library, env_library)) if env_library else target.library
            )
        if windows:
            # R rejects whitespace in its session temp path when no short NTFS
            # name exists. The relative path resolves in the isolated cwd.
            absolute_temp = str(r_temp)
            environment.update(TEMP=absolute_temp, TMP=absolute_temp)
            if not any(char.isspace() for char in absolute_temp):
                environment["TMPDIR"] = absolute_temp
            try:
                executable = _stage_windows_runtime(spec.interpreter, scratch, environment)
                bundle = scratch / "tavotto-native"
                shutil.copytree(bridge.parent, bundle)
                bridge, driver, adapter = (bundle / path.name for path in (bridge, driver, adapter))
                palettes = Path(shutil.copyfile(palettes, bundle / palettes.name))
                profile = scratch / "r-runtime" / "library" / "base" / "R" / "Rprofile"
                compatibility = (bundle / "ggplot_windows_profile.R").read_bytes()
                profile.write_bytes(compatibility + b"\n" + profile.read_bytes())
                if fonts is not None:
                    staged_fonts = _stage_windows_fonts(fonts, scratch)
                execute = resources.enter_context(winappcontainer.offline(scratch, environment))
            except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
                raise scriptadjust.ScriptAdjustError(
                    "script_export_r_source_unavailable",
                    "Could not prepare isolated R replay on Windows.",
                ) from exc
            command = [
                str(executable),
                "--vanilla",
                "--slave",
                f"--file={driver}",
                "--args",
                str(request),
            ]

            def run_stage():
                try:
                    execute(command, cwd, timeout=120)
                except winappcontainer.ReplayFailed as exc:
                    raise _SourceProgramFailed("script_export_run_failed", str(exc)) from exc
                except winappcontainer.Unavailable as exc:
                    raise scriptadjust.ScriptAdjustError(
                        "script_export_r_source_unavailable",
                        "Could not start protected R replay on Windows.",
                    ) from exc
        else:
            prefix = cmdsandbox.prefix(*sandbox, scratch, project=str(project), offline=True)
            command = [*prefix, spec.interpreter, "--vanilla", str(driver), str(request)]

            def run_stage():
                _run(command, cwd, environment)

        common = {
            "script": staged.as_posix(),
            "object": target.object_name,
            "result": result.as_posix(),
            "adapter": adapter.as_posix(),
            "bridge": bridge.as_posix(),
        }
        request.write_text(json.dumps({**common, "operation": "anchor"}), encoding="utf-8")
        run_stage()
        anchor = json.loads(result.read_text(encoding="utf-8"))
        adjusted = build(
            original,
            target.object_name,
            patches,
            anchor["line"],
            anchor["byte"],
            themes=anchor["themes"],
            hot=hot,
            tick_themes=anchor["tick_themes"],
            write_font_family=original_for_retry is None,
        )
        staged.write_bytes(adjusted.data)
        replay_request = {
            **common,
            "operation": "replay",
            "palettes": palettes.as_posix(),
            "frame_pt": target.frame_pt,
            "stem": stem,
            "width": width,
            "residual": adjusted.residual,
            "include_data_fingerprint": include_data_fingerprint,
        }
        if fonts is None:
            replay_request["png"] = png.as_posix()
            if vectors:
                replay_request.update(pdf=pdf.as_posix(), svg=svg.as_posix())
        else:
            replay_request.update(fonts=staged_fonts, svg=svg.as_posix())
        request.write_text(json.dumps(replay_request), encoding="utf-8")
        execution_id = executionfact.new_id()
        try:
            run_stage()
        except _SourceProgramFailed as exc:
            if any(
                patch["prop"] == "fontfamily" and patch not in adjusted.residual
                for patch in patches
            ):
                raise _RetryWithoutFontFamily(original) from exc
            raise
        manifest = json.loads(result.read_text(encoding="utf-8"))
        data_fingerprint = manifest.pop("data_fingerprint", None)
        target.annotate_manifest(manifest)
        adjusted = _rebind_residual(adjusted, hot, manifest)
        if script.read_bytes() != original:
            raise scriptadjust.ScriptAdjustError(
                "script_export_script_changed", "The R source changed during replay."
            )
        return ReplayedRSource(
            adjusted,
            original,
            manifest,
            png.read_bytes() if fonts is None else None,
            execution_id,
            pdf.read_bytes() if vectors else None,
            nativeggplot._write_ggplot_svg(svg, svg.read_text(encoding="utf-8"))
            if vectors or fonts is not None
            else None,
            data_fingerprint,
        )
