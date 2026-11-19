"""Launch arguments for an owned R/ggplot2 capture."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from . import execspec, projectenv, runcodes

_RESOURCES = Path(__file__).resolve().parents[1] / "resources" / "native"


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

    def command(self, spec: execspec.ExecutionSpec, *, host: str, port: int) -> list[str]:
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
        ]
