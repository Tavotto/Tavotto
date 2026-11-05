#!/usr/bin/env python3
"""证据机准备容器里，从 jsDelivr 补齐 raw.githubusercontent.com 上取不到的文件（ADR 0166）。

大陆到 raw.githubusercontent.com 常常全部被重置，`github.com/.../raw/` 也会跳到那里；jsDelivr 通。
镜像只换来源：每个文件仍按仓库里钉的 sha256 核过才落盘，对不上的绝不发布，所以信任边界不变。
这里取不到不算失败：之后的 fetch_fonts / research fetch 还会走原来源，并由它们决定准备成败。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "scripts" / "ci")]

import compat_research_sources as sources  # noqa: E402
import fetch_fonts  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

#: 只认钉在完整提交上的地址：内容不随分支变，才谈得上和钉住的 sha256 对应。
_RAW = re.compile(
    r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([0-9a-f]{40})/(.+)", re.ASCII
)
_WORKERS = 8
_TIMEOUT_SECONDS = 120


def mirror_url(url: str) -> str | None:
    """raw 地址对应的 jsDelivr 地址；不是钉在完整提交上的 raw 地址就返回 None。"""
    m = _RAW.fullmatch(url)
    if m is None:
        return None
    owner, repo, commit, path = m.groups()
    return f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@{commit}/{path}"


def _download(job: tuple[str, Path, str]) -> bool:
    url, target, expected = job
    try:
        fetch_fonts.download_verified(url, target, expected, timeout=_TIMEOUT_SECONDS)
    except (OSError, ValueError, fetch_fonts.HashMismatch) as exc:
        print(f"mirror: {target.name}: {exc}", file=sys.stderr)
        return False
    return True


def _run(jobs: Iterable[tuple[str, Path, str]]) -> int:
    todo = list(jobs)
    with ThreadPoolExecutor(max_workers=_WORKERS) as executor:
        done = sum(executor.map(_download, todo))
    print(f"mirror: {done}/{len(todo)}")
    return len(todo) - done


def font_jobs(dest: Path, allowlist: dict) -> list[tuple[str, Path, str]]:
    """allowlist 里走 raw、目录里还没有的字体与许可证。"""
    jobs: list[tuple[str, Path, str]] = []
    for spec in allowlist["faces"].values():
        mirrored = mirror_url(spec["source"].get("url", ""))
        if mirrored and not (dest / spec["file"]).is_file():
            jobs.append((mirrored, dest / spec["file"], spec["sha256"]))
    for rel, src in allowlist.get("license_files", {}).items():
        mirrored = mirror_url(src.get("url", ""))
        if mirrored and not (dest / rel).is_file():
            jobs.append((mirrored, dest / rel, src["file_sha256"]))
    return jobs


def research_jobs(project: dict, cache: Path) -> list[tuple[str, Path, str]]:
    """这个科研项目里还没进缓存的上游文件。"""
    directory = sources.project_directory(project, cache)
    upstream = project["upstream"]
    jobs: list[tuple[str, Path, str]] = []
    for upstream_path, logical in project["upstream_path_map"].items():
        target = sources.contained(directory, upstream_path)
        if target.exists():
            continue
        raw = (
            f"https://raw.githubusercontent.com/{upstream['repository']}/{upstream['commit']}/"
            + urllib.parse.quote(upstream_path, safe="/")
        )
        mirrored = mirror_url(raw)
        if mirrored:
            jobs.append((mirrored, target, project["files"][logical]))
    return jobs


def fill_research(case: str) -> int:
    cohort = json.loads((REPO / sources.COHORT_REL).read_text(encoding="utf-8"))
    matches = [p for p in cohort["development"] if p["case_id"] == case]
    if len(matches) != 1:
        print(f"mirror: unknown research case {case}", file=sys.stderr)
        return 1
    return _run(research_jobs(matches[0], sources.cache_root()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="what", required=True)
    sub.add_parser("fonts")
    research = sub.add_parser("research")
    research.add_argument("--case", required=True)
    args = ap.parse_args(argv)
    if args.what == "fonts":
        missing = _run(font_jobs(fetch_fonts.DEFAULT_DEST, fetch_fonts.load_allowlist()))
    else:
        missing = fill_research(args.case)
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
