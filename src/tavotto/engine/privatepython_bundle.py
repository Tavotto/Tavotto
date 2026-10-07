"""macOS signed transport for the upstream-locked private Python (ADR 0111).

The generated manifest is a sealed app resource, never a second source lock.
No cached trust: every derived-digest acceptance verifies the installed app and
executing sidecar with the system verifier and an Apple Developer ID requirement.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import runtime

MANIFEST_PARTS = ("tavotto", "resources", "private_python_bundle.json")
# Leading = means inline requirement source, not a filename (Apple TN3127).
DEVELOPER_ID_REQUIREMENT = (
    "=anchor apple generic and "
    "certificate 1[field.1.2.840.113635.100.6.2.6] exists and "
    "certificate leaf[field.1.2.840.113635.100.6.1.13] exists"
)


def source_identity(source) -> dict:
    return {
        "schema": 1,
        "target": source.target,
        "version": source.version,
        "release": source.release,
        "archive_root": source.archive_root,
        "python_rel": source.python_rel,
        "source_id": source.id,
        "upstream_sha256": source.sha256,
        "upstream_size": source.size,
        "archive_name": source.archive_name,
    }


def containing_app(path: Path) -> Path:
    return next(p for p in path.resolve(strict=True).parents if p.suffix == ".app")


def _codesign(*args: str) -> str:
    proc = subprocess.run(
        ["/usr/bin/codesign", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=True,
        creationflags=runtime.CREATE_NO_WINDOW,
    )
    return proc.stdout + proc.stderr


def verify_app(app: Path, executable: Path) -> None:
    """Verify both certificate chains, seals, and the same certificate Team ID.

    TeamIdentifier is read only after validating the sidecar. Also test its
    certificate OU explicitly; a string in a receipt or Authority is not trust.
    """
    _codesign("--verify", "--strict", "-R", DEVELOPER_ID_REQUIREMENT, str(executable))
    detail = _codesign("-d", "--verbose=4", str(executable))
    match = re.search(r"^TeamIdentifier=([A-Z0-9]{10})$", detail, re.MULTILINE)
    if not match:
        raise ValueError("signed sidecar has no valid TeamIdentifier")
    requirement = DEVELOPER_ID_REQUIREMENT + f' and certificate leaf[subject.OU] = "{match[1]}"'
    _codesign("--verify", "--strict", "-R", requirement, str(executable))
    _codesign("--verify", "--deep", "--strict", "-R", requirement, str(app))


def accepts(source, archive: Path, digest: str, size: int) -> bool:
    """Only canonical frozen macOS app resources may use a transformed digest."""
    if (
        sys.platform != "darwin"
        or not getattr(sys, "frozen", False)
        or os.environ.get(runtime.PRIVATE_PYTHON_BUNDLE_ENV)
        or not source.target.startswith("macos-")
    ):
        return False
    try:
        executable = Path(sys.executable).resolve(strict=True)
        app = containing_app(executable)
        base = Path(sys._MEIPASS).resolve(strict=True)
        canonical = (base / runtime.PRIVATE_PYTHON_BUNDLE_DIR_NAME / source.archive_name).resolve(
            strict=True
        )
        manifest = base.joinpath(*MANIFEST_PARTS).resolve(strict=True)
        for path in (base, canonical, manifest):
            path.relative_to(app)
        if archive.resolve(strict=True) != canonical:
            return False
        contents = manifest.read_bytes()
        data = json.loads(contents)
        if not isinstance(data, dict) or any(
            data.get(k) != v for k, v in source_identity(source).items()
        ):
            return False
        if data.get("sha256") != digest or data.get("size") != size:
            return False
        verify_app(app, executable)
        # Do not use a receipt changed during verification. The consumed archive
        # is separately snapshotted and hashed immediately before extraction.
        return manifest.read_bytes() == contents
    except (AttributeError, OSError, ValueError, StopIteration, subprocess.SubprocessError):
        return False
