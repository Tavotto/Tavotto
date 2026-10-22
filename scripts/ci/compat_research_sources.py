"""Fetch reviewed research inputs by upstream commit and SHA-256, without executing them.

The cache is deliberately outside the checkout. Reading the registry never fetches;
only the explicit research preparation command downloads public upstream files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath, PureWindowsPath

REPO = Path(__file__).resolve().parents[2]
COHORT_REL = "tests/compat/research-cohort.json"
MANIFEST_REL = "tests/compat/research-manifest.json"
CACHE_ENV = "TAVOTTO_RESEARCH_CACHE"
MAX_FILE_BYTES = 64 * 1024 * 1024


class SourceError(ValueError):
    """An upstream input cannot be tied to its reviewed identity."""


def relative_path(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).drive
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise SourceError("source path must be canonical and project-relative")


def contained(root: Path, relative: str) -> Path:
    relative_path(relative)
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise SourceError("source path escapes its cache root")
    return path


def validate_project(project: dict) -> None:
    upstream = project["upstream"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", upstream["repository"]):
        raise SourceError("upstream must be a GitHub owner/repository")
    if any(part in (".", "..") for part in upstream["repository"].split("/")):
        raise SourceError("invalid upstream repository")
    if not re.fullmatch(r"[0-9a-f]{40}", upstream["commit"]):
        raise SourceError("upstream commit must be an immutable full SHA")
    mapping, files = project["upstream_path_map"], project["files"]
    if (
        not files
        or len(set(mapping.values())) != len(mapping)
        or set(mapping.values()) != set(files)
    ):
        raise SourceError("source mapping must cover the frozen inventory exactly once")
    for upstream_path, logical_path in mapping.items():
        relative_path(upstream_path)
        relative_path(logical_path)
        if not re.fullmatch(r"[0-9a-f]{64}", files[logical_path]):
            raise SourceError("each source requires a SHA-256 commitment")
    if not project.get("license_files") or not set(project["license_files"]) <= files.keys():
        raise SourceError("license files must be in the source inventory")


def cache_root(cache: Path | None = None, *, root: Path = REPO) -> Path:
    value = cache or os.environ.get(CACHE_ENV)
    if not value:
        raise SourceError(f"set {CACHE_ENV} to an external directory and run fetch first")
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or path.resolve().is_relative_to(root.resolve()):
        raise SourceError("research cache must be an absolute directory outside the checkout")
    return path.resolve()


def project_directory(project: dict, cache: Path) -> Path:
    validate_project(project)
    upstream = project["upstream"]
    return contained(cache, f"{upstream['repository']}/{upstream['commit']}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_path(project: dict, logical: str, cache: Path) -> Path:
    mapping = {value: key for key, value in project["upstream_path_map"].items()}
    if logical not in mapping:
        raise SourceError("source is not in the frozen project inventory")
    return contained(project_directory(project, cache), mapping[logical])


def verified_bytes(project: dict, logical: str, cache: Path) -> bytes:
    path = input_path(project, logical, cache)
    if not path.is_file():
        raise SourceError(f"missing or changed upstream input: {logical}")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != project["files"][logical]:
        raise SourceError(f"missing or changed upstream input: {logical}")
    return data


def verified_file(project: dict, logical: str, cache: Path) -> Path:
    verified_bytes(project, logical, cache)
    return input_path(project, logical, cache)


def fetch_project(project: dict, cache: Path, *, root: Path = REPO) -> dict:
    cache = cache_root(cache, root=root)
    directory = project_directory(project, cache)
    directory.mkdir(parents=True, exist_ok=True)
    upstream = project["upstream"]

    def fetch_one(item):
        upstream_path, logical = item
        target = contained(directory, upstream_path)
        if target.exists():
            verified_file(project, logical, cache)
            return
        url = (
            f"https://raw.githubusercontent.com/{upstream['repository']}/{upstream['commit']}/"
            + urllib.parse.quote(upstream_path, safe="/")
        )
        request = urllib.request.Request(url, headers={"User-Agent": "Tavotto-CompatBench"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(MAX_FILE_BYTES + 1)
        if (
            len(data) > MAX_FILE_BYTES
            or hashlib.sha256(data).hexdigest() != project["files"][logical]
        ):
            raise SourceError(f"upstream bytes differ from reviewed SHA-256: {logical}")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Publish only a complete verified file; an interrupted fetch is never executable input.
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
            temporary.write(data)
            temporary_path = Path(temporary.name)
        try:
            temporary_path.replace(contained(directory, upstream_path))
        finally:
            temporary_path.unlink(missing_ok=True)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(fetch_one, project["upstream_path_map"].items()))
    for logical in project["files"]:
        verified_file(project, logical, cache)
    return {
        "project": project["project_id"],
        "commit": upstream["commit"],
        "verified_files": len(project["files"]),
    }


def project_for_case(case: dict, *, root: Path = REPO) -> dict:
    cohort = json.loads((root / COHORT_REL).read_text(encoding="utf-8"))
    matches = [p for p in cohort["development"] if p["case_id"] == case["id"]]
    if len(matches) != 1 or matches[0]["project_id"] != case.get("research_project_id"):
        raise SourceError("case is not a registered research project")
    return matches[0]


def source_file(case: dict, relative: str, *, root: Path = REPO) -> Path:
    project = project_for_case(case, root=root)
    return verified_file(project, relative, cache_root(root=root))


def source_bytes(case: dict, relative: str, *, root: Path = REPO) -> bytes:
    return verified_bytes(project_for_case(case, root=root), relative, cache_root(root=root))
