"""Opt-in Celerite diagnostics: observe arrays without changing the source or RNG.

Installed by the existing execution observer only for a hash-verified script.
The profile hook reads script globals at seed/return and Figure data after draw;
it never replaces NumPy, a Matplotlib setter, or a scientific computation.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from pathlib import Path


def array_identity(np, value) -> dict:
    array = np.asarray(value)
    if array.dtype.hasobject:
        raise ValueError("object arrays cannot provide a stable numeric identity")
    return {
        "shape": list(array.shape),
        "dtype": array.dtype.str,
        "sha256": hashlib.sha256(array.tobytes(order="C")).hexdigest(),
    }


def rng_identity(np) -> str:
    name, state, position, has_gauss, cached_gauss = np.random.get_state()
    payload = [name, array_identity(np, state), position, has_gauss, cached_gauss]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def figure_state(np, figure) -> dict:
    return {
        "size_inches": figure.get_size_inches().tolist(),
        "dpi": figure.dpi,
        "axes": [
            {
                # Public getters can apply aspect/autoscale; observe stored state only.
                "position": list(ax._position.bounds),
                "xlim": ax._viewLim.intervalx.tolist(),
                "ylim": ax._viewLim.intervaly.tolist(),
                "lines": [
                    {
                        "x": array_identity(np, line.get_xdata(orig=True)),
                        "y": array_identity(np, line.get_ydata(orig=True)),
                        "color": str(line.get_color()),
                        "linewidth": line.get_linewidth(),
                    }
                    for line in ax.lines
                ],
            }
            for ax in figure.axes
        ],
    }


def attach(code, source: Path, root: Path, fd: int) -> None:
    def write(row):
        row.update(pid=os.getpid(), leg=Path(os.environ["CG_EXEC_LOG"]).name)
        os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode())

    if sys.getprofile() is not None:
        write({"phase": "diagnostic_error", "error": "another profiler is already installed"})
        return
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    seed_lines = {
        node.lineno
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "seed"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "random"
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "np"
    }
    script_globals = {}
    script_frame = None
    complete = False
    seed_reported = False

    def profile(frame, event, arg):
        nonlocal script_globals, script_frame, complete, seed_reported
        is_script = frame.f_code is code
        if is_script:
            script_globals = frame.f_globals
            script_frame = frame
        np = script_globals.get("np")
        # The pinned script's next statement calls setup(). Observe before that
        # call's body, after seed has returned. Do not replace the product tracer.
        if (
            not seed_reported
            and np is not None
            and script_frame is not None
            and seed_lines
            and script_frame.f_lineno > max(seed_lines)
        ):
            write(
                {
                    "phase": "seed_return",
                    "source_sha256": source_sha,
                    "rng_sha256": rng_identity(np),
                }
            )
            seed_reported = True
        script_return = is_script and event == "return"
        draw_return = (
            event == "return"
            and frame.f_code.co_name == "draw"
            and frame.f_globals.get("__name__") == "matplotlib.figure"
        )
        if not (script_return or draw_return):
            return
        np = script_globals.get("np")
        if np is None:
            return
        try:
            row = {
                "phase": "script_return" if script_return else "draw_return",
                "source_sha256": source_sha,
                "rng_sha256": rng_identity(np),
                "after_script": complete,
            }
            figure = frame.f_locals.get("self") if draw_return else script_globals.get("fig")
            if figure is not None and hasattr(figure, "axes"):
                row["figure"] = figure_state(np, figure)
            if script_return:
                complete = True
                row["numpy"] = np.__version__
                row["argv"] = [value.replace(str(root), "<work>") for value in sys.argv]
                row["cwd"] = os.getcwd().replace(str(root), "<work>")
                row["thread_environment"] = {
                    key: os.environ.get(key)
                    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")
                }
                row["last_covariance"] = array_identity(np, script_globals["K"])
                try:
                    import threadpoolctl

                    row["threadpools"] = [
                        {
                            key: pool.get(key)
                            for key in ("internal_api", "num_threads", "version", "architecture")
                        }
                        for pool in threadpoolctl.threadpool_info()
                    ]
                except ImportError:
                    row["threadpools"] = None
            write(row)
        except Exception as exc:  # Diagnostic failure is evidence, never a changed script result.
            write({"phase": "diagnostic_error", "error": f"{type(exc).__name__}: {exc}"})

    sys.setprofile(profile)
