"""Translate shared plot patches into a native, removable R source adjustment."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
from contextlib import ExitStack, suppress
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ReplayedRSource:
    source: AdjustedRSource
    original_bytes: bytes
    manifest: dict
    png: bytes
    execution_id: str
    pdf: bytes | None = None
    svg: bytes | None = None


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

    face_of = {
        item["gid"]: {
            field["prop"]: field["value"]
            for field in item.get("editable", ())
            if field.get("prop") in ("weight", "style")
        }
        for item in (hot or {}).get("elements", ())
        if item["gid"] in themes
    }
    elements: dict[str, dict[str, str | float]] = {}
    written: set[tuple[str, str]] = set()
    residual = []
    skipped = []
    for patch in patches:
        gid, prop, value = patch["gid"], patch["prop"], patch["value"]
        element = themes.get(gid)
        if element is None and prop == "color" and tick_themes is not None:
            element = tick_themes.get(gid)
        if element is not None:
            face_fields = face_of.get(gid, {})
            face = _FACES.get((face_fields.get("weight"), face_fields.get("style")))
            if prop == "color" and isinstance(value, str) and _RGB.fullmatch(value):
                elements.setdefault(element, {})["colour"] = value
            elif prop == "fontsize" and type(value) in (int, float) and 3 <= value <= 200:
                elements.setdefault(element, {})["size"] = value
            elif (
                prop in ("weight", "style") and face is not None and face_fields.get(prop) == value
            ):
                elements.setdefault(element, {})["face"] = face
            else:
                element = None
        if element is not None:
            written.add((gid, prop))
            continue
        residual.append(patch)
        skipped.append({"gid": gid, "prop": prop, "reason": "unsupported"})
    if not elements:
        return AdjustedRSource(data, 0, residual, skipped)

    name = json.dumps(object_name, ensure_ascii=False)
    options = ",\n".join(
        f"    {element} = ggplot2::element_text("
        + ", ".join(f"{key} = {json.dumps(value)}" for key, value in fields.items())
        + ")"
        for element, fields in elements.items()
    )
    block = (
        f"\n{scriptadjust.BEGIN}\n"
        f"base::assign({name}, base::get({name}, inherits = FALSE) + ggplot2::theme(\n"
        f"{options}\n))\n{scriptadjust.END}\n"
    )
    newline = b"\r\n" if lines[end_line - 1].endswith(b"\r\n") else b"\n"
    prefix = b"".join(lines[:position])
    if not prefix.endswith(newline):
        prefix += newline
    result = prefix + block.encode().replace(b"\n", newline) + b"".join(lines[position:])
    return AdjustedRSource(result, len(written), residual, skipped)


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
            raise scriptadjust.ScriptAdjustError(
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
    listing = subprocess.run(  # noqa: S603 - the native session attested the selected interpreter
        [
            interpreter,
            "--vanilla",
            "--default-packages=NULL",
            "-e",
            "writeLines(enc2utf8(.libPaths()), useBytes = TRUE)",
        ],
        cwd=bin_dir,
        env=environment,
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
    for key in ("R_LIBS", "R_LIBS_SITE", "R_ENVIRON_USER", "R_PROFILE_USER"):
        environment.pop(key, None)
    executable = staged / terminal.relative_to(root)
    environment.update(
        R_HOME=str(staged),
        R_USER=str(home),
        R_LIBS_USER=os.pathsep.join(libraries),
        PATH=os.pathsep.join(
            (str(executable.parent), str(Path(os.environ["SystemRoot"]) / "System32"))
        ),
    )
    return executable


def replay(
    spec: execspec.ExecutionSpec,
    target: nativeggplot.GgplotTarget,
    stem: str,
    patches: list[dict],
    width: int,
    *,
    hot: dict | None = None,
    vectors: bool = False,
) -> ReplayedRSource:
    """Run the candidate in a protected copy using the recorded native interpreter."""
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
        if target.library:
            env_library = environment.get("R_LIBS_USER")
            environment["R_LIBS_USER"] = (
                os.pathsep.join((target.library, env_library)) if env_library else target.library
            )
        if windows:
            environment.update(TMPDIR=str(r_temp), TEMP=str(r_temp), TMP=str(r_temp))
            try:
                executable = _stage_windows_runtime(spec.interpreter, scratch, environment)
                bundle = scratch / "tavotto-native"
                bundle.mkdir()
                bridge, driver, adapter, palettes = (
                    Path(shutil.copyfile(path, bundle / path.name))
                    for path in (bridge, driver, adapter, palettes)
                )
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
                    result = execute(command, cwd, timeout=120)
                except winappcontainer.Unavailable as exc:
                    raise scriptadjust.ScriptAdjustError(
                        "script_export_r_source_unavailable",
                        "Could not start protected R replay on Windows.",
                    ) from exc
                if result:
                    raise scriptadjust.ScriptAdjustError(
                        "script_export_run_failed",
                        "The selected plot could not be replayed from R source.",
                    )
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
        )
        staged.write_bytes(adjusted.data)
        replay_request = {
            **common,
            "operation": "replay",
            "palettes": palettes.as_posix(),
            "frame_pt": target.frame_pt,
            "stem": stem,
            "png": png.as_posix(),
            "width": width,
            "residual": adjusted.residual,
            **({"pdf": pdf.as_posix(), "svg": svg.as_posix()} if vectors else {}),
        }
        request.write_text(json.dumps(replay_request), encoding="utf-8")
        execution_id = executionfact.new_id()
        run_stage()
        manifest = json.loads(result.read_text(encoding="utf-8"))
        target.annotate_manifest(manifest)
        if script.read_bytes() != original:
            raise scriptadjust.ScriptAdjustError(
                "script_export_script_changed", "The R source changed during replay."
            )
        return ReplayedRSource(
            adjusted,
            original,
            manifest,
            png.read_bytes(),
            execution_id,
            pdf.read_bytes() if vectors else None,
            nativeggplot._write_ggplot_svg(svg, svg.read_text(encoding="utf-8"))
            if vectors
            else None,
        )
