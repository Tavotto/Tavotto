"""Native release acceptance, dispatched by the final frozen sidecar before Flask.

Runs the actual bundle resolver/provision transaction, then the actual managed
venv creator. A host Python cannot stand in for this frozen entry point.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from . import config, managedenv, privatepython, privatepython_bundle, runtime


def _tree(app: Path) -> dict:
    return {
        str(p.relative_to(app)): (
            p.lstat().st_mode,
            os.readlink(p)
            if p.is_symlink()
            else hashlib.sha256(p.read_bytes()).hexdigest()
            if p.is_file()
            else "directory",
        )
        for p in app.rglob("*")
    }


def main() -> int:
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        raise RuntimeError("private Python app smoke requires the frozen macOS sidecar")
    if not os.environ.get("TAVOTTO_DATA_DIR") or os.environ.get(runtime.PRIVATE_PYTHON_BUNDLE_ENV):
        raise RuntimeError("smoke requires an explicit empty data directory and no bundle override")
    app = privatepython_bundle.containing_app(Path(sys.executable))
    data = config.data_dir().resolve()
    if data == app or app in data.parents or (data.exists() and any(data.iterdir())):
        raise RuntimeError("smoke data directory must be empty and outside the app")
    before = _tree(app)
    network_attempts = []

    def offline(event, args):
        if event in ("socket.connect", "socket.getaddrinfo"):
            network_attempts.append(event)
            raise RuntimeError("offline private Python smoke attempted network access")

    sys.addaudithook(offline)
    source = privatepython.source_for()
    if source is None or privatepython.archive_origin(source) != privatepython.ORIGIN_BUNDLED:
        raise RuntimeError("final app does not offer its private Python archive")
    base = privatepython.provision(source, required_origin=privatepython.ORIGIN_BUNDLED)
    project, generation = data / "project", "gprivatepythonsmoke"
    project.mkdir(parents=True)
    ok, detail = managedenv.create_generation_venv(project, generation, base)
    if not ok:
        raise RuntimeError(f"signed private Python cannot create a managed venv: {detail}")
    python = managedenv.generation_python(project, generation)
    proc = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "import ensurepip, pip, ssl, sqlite3, zlib, sys; "
            "assert sys.prefix != sys.base_prefix; "
            "print(ensurepip.version())",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env=privatepython._probe_env(),
    )
    ledger = privatepython.read_ledger()["runtimes"][source.id]
    if ledger["origin"] != privatepython.ORIGIN_BUNDLED or network_attempts:
        raise RuntimeError("provision did not stay offline and bundled")
    if list(privatepython.downloads_dir().iterdir()) or before != _tree(app):
        raise RuntimeError("smoke wrote to the bundle or populated a download cache")
    if (Path(sys._MEIPASS).joinpath(*privatepython_bundle.MANIFEST_PARTS)).exists():
        privatepython_bundle.verify_app(app, Path(sys.executable))
    print(
        json.dumps(
            {
                "private_python": "bundled",
                "source_id": source.id,
                "archive_network_attempts": 0,
                "downloads": 0,
                "app_unchanged": True,
                "venv_ensurepip": proc.stdout.strip(),
            }
        )
    )
    return 0
