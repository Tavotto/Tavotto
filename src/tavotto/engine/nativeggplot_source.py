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
from .fixtools import cmdsandbox, trial, winappcontainer

_RGB = re.compile(r"#[0-9a-fA-F]{6}\Z")
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


@dataclass(frozen=True)
class ReplayedRSource:
    source: AdjustedRSource
    original_bytes: bytes
    manifest: dict
    png: bytes | None
    execution_id: str
    pdf: bytes | None = None
    svg: bytes | None = None


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
        return value if isinstance(value, str) and value in ("linear", "log") else None
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
        if scope:
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


def _r_coordinate_block(name: str, coordinates: dict) -> str:
    edits = []
    transforms = {
        "linear": "scales::transform_identity()",
        "log": "scales::transform_log10()",
    }
    if any(prop in coordinates for prop in ("xscale", "yscale")):
        edits.append("  plot$scales <- plot$scales$clone()")
    for axis in ("x", "y"):
        if (mode := coordinates.get(f"{axis}scale")) is not None:
            transform = transforms[mode]
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
            "  plot",
            f"}})(base::get({name}, inherits = FALSE)))",
        ]
    )


def _rebind_coordinate_residual(
    source: AdjustedRSource, hot: dict | None, cold: dict
) -> AdjustedRSource:
    if not source.view_written or not hot:
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
                or not _same_coordinate_target(prior, current, bound, targets)
            ):
                raise scriptadjust.ScriptAdjustError(
                    "script_export_diverged", "A retained R edit no longer matches its target."
                )
            patch = {**patch, "identity": current["identity"]}
        residual.append(patch)
    return replace(source, residual=residual)


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
    written = coordinate_writes.copy()
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
    if residual and any(prop in coordinates for prop in ("xscale", "yscale")):
        coordinates, fallback_writes = _native_coordinate_writes(axes, patches, write_scales=False)
        written.difference_update(coordinate_writes - fallback_writes)
        residual = [patch for patch in patches if (patch["gid"], patch["prop"]) not in written]
        skipped = [
            {"gid": patch["gid"], "prop": patch["prop"], "reason": "unsupported"}
            for patch in residual
        ]
    if not elements and not coordinates:
        return AdjustedRSource(data, 0, residual, skipped)

    name = json.dumps(object_name, ensure_ascii=False)
    options = ",\n".join(
        f"    {element} = ggplot2::element_text("
        + ", ".join(
            f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items()
        )
        + ")"
        for element, fields in elements.items()
    )
    statements = []
    if coordinates:
        statements.append(_r_coordinate_block(name, coordinates))
    if elements:
        statements.append(
            f"base::assign({name}, base::get({name}, inherits = FALSE) + ggplot2::theme(\n"
            f"{options}\n))"
        )
    block = "\n".join(("", scriptadjust.BEGIN, *statements, scriptadjust.END, ""))
    newline = b"\r\n" if lines[end_line - 1].endswith(b"\r\n") else b"\n"
    prefix = b"".join(lines[:position])
    if not prefix.endswith(newline):
        prefix += newline
    result = prefix + block.encode().replace(b"\n", newline) + b"".join(lines[position:])
    return AdjustedRSource(result, len(written), residual, skipped, bool(coordinates))


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
) -> ReplayedRSource:
    """Run the candidate in a protected copy using the recorded native interpreter."""
    try:
        return _replay(spec, target, stem, patches, width, hot=hot, vectors=vectors, fonts=fonts)
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
        target.annotate_manifest(manifest)
        adjusted = _rebind_coordinate_residual(adjusted, hot, manifest)
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
        )
