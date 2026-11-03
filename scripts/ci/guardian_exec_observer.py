"""CPython exec-event counter for reviewed CompatBench copies only.

This is diagnostic instrumentation, not a sandbox or attestation. It counts actual
module execution starts, including probes/fresh workers; never HTTP/build calls.
Only exact source hashes below the run's temporary root qualify. V2 additionally
has its fixture-owned external log. Bundled/isolated Python may ignore sitecustomize;
the runner then reports an unknown count rather than inventing one.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import sys
from pathlib import Path

#: Versions worth showing in a public report. The full installed set is bound by
#: digest instead of listed: a complete package inventory does not belong in
#: artifacts anyone with read access can download.
KEY_DISTRIBUTIONS = (
    "matplotlib",
    "numpy",
    "pillow",
    "fonttools",
    "pandas",
    "scipy",
    "seaborn",
    "scikit-learn",
    "opencv-python",
    "opencv-python-headless",
    "flask",
    "pikepdf",
    "uharfbuzz",
    "pypdfium2",
)


def actual_environment() -> dict:
    installed = sorted(
        f"{d.metadata['Name']}=={d.version}"
        for d in importlib.metadata.distributions()
        if d.metadata.get("Name")
    )
    lowered = {item.split("==")[0].lower(): item.split("==", 1)[1] for item in installed}
    facts = {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "executable_sha256": hashlib.sha256(
            Path(sys.executable).resolve().read_bytes()
        ).hexdigest(),
        "distribution_count": len(installed),
        "distributions_sha256": hashlib.sha256("\n".join(installed).encode()).hexdigest(),
        "key_distributions": {n: lowered[n] for n in KEY_DISTRIBUTIONS if n in lowered},
        "execution_env": {
            k: os.environ.get(k) for k in ("PYTHONHASHSEED", "MPLBACKEND", "LANG", "LC_ALL")
        },
    }
    canon = json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()
    return {"fingerprint": hashlib.sha256(canon).hexdigest(), "facts": facts}


def install() -> None:
    root = Path(os.environ["CG_EXEC_ROOT"]).resolve()
    allowed = set(json.loads(os.environ["CG_EXEC_HASHES"]))
    fd = os.open(os.environ["CG_EXEC_LOG"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)

    def observe(event, args):
        if event != "exec":
            return
        code = args[0]
        if code.co_name != "<module>":
            return
        try:
            path = Path(code.co_filename).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                return
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest not in allowed:
                return
            row = {
                "pid": os.getpid(),
                "source_sha256": digest,
                "script": path.relative_to(root).as_posix(),
                "environment": actual_environment(),
            }
            os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode())
        except OSError:
            # Missing counter evidence cannot result in a passing Guardian report.
            return

    sys.addaudithook(observe)
