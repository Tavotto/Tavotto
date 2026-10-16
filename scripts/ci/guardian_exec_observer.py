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


def actual_environment() -> dict:
    facts = {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "executable_sha256": hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest(),
        "distributions": sorted(
            f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()
            if d.metadata.get("Name")
        ),
        "execution_env": {k: os.environ.get(k) for k in ("PYTHONHASHSEED", "MPLBACKEND", "LANG", "LC_ALL")},
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
            row = {"pid": os.getpid(), "source_sha256": digest,
                   "script": str(path.relative_to(root)), "environment": actual_environment()}
            os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode())
        except OSError:
            # Missing counter evidence cannot result in a passing Guardian report.
            return

    sys.addaudithook(observe)
