#!/usr/bin/env python3
"""按 allowlist 的 sha256 取批准字体到包内 `src/tavotto/resources/fonts/`（统一实施包 U06，ADR 0060）。

    python scripts/fetch_fonts.py                 # 取到 src/tavotto/resources/fonts/（不进 git）
    python scripts/fetch_fonts.py --check         # 只核对：目录里每张脸的 sha256 与 allowlist 一致
    python scripts/fetch_fonts.py --dest DIR      # 取到别处（CI / 冒烟用；运行时用 TAVOTTO_FONTS_DIR 指过去）

**字体文件不进 git**（`tests/test_font_provenance.py`）；它们随 wheel（pyproject 的
`[tool.hatch.build] artifacts`）与 PyInstaller datas（`packaging/tavotto.spec` 的 `resources/`）分发，
许可证全文（OFL 1.1）与字体放在一起。唯一的输入是 `src/tavotto/rendercore/fonts_allowlist.json`：
来源 URL、tarball 成员名、**每个文件自己的 sha256**——下载到 `.part`，校验通过才改名成正式名，
hash 不符当场删掉、不重试（与 `scripts/build_worker_runtime.py` 的 `download()` 同一条纪律）。

纯标准库；不 import 产品模块（它要在 `pip install -e` 之前就能跑）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = ROOT / "src" / "tavotto" / "rendercore" / "fonts_allowlist.json"
DEFAULT_DEST = ROOT / "src" / "tavotto" / "resources" / "fonts"
#: 下载缓存**不在**字体目录里：字体目录整个进 wheel artifacts / PyInstaller datas，tarball 不该跟着走。
DEFAULT_CACHE = ROOT / "build" / "fonts-cache"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


class HashMismatch(Exception):
    def __init__(self, path: Path, expected: str, got: str) -> None:
        super().__init__(f"SHA-256 不符：{path}\n  期望 {expected}\n  实得 {got}")
        self.path, self.expected, self.got = path, expected, got


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(path: Path, expected: str) -> Path:
    expected = expected.strip().lower()
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError(f"登记的不是一个 SHA-256 十六进制串：{expected!r}")
    got = sha256_file(path)
    if got != expected:
        raise HashMismatch(Path(path), expected, got)
    return Path(path)


def download_verified(
    url: str, dest: Path, expected_sha256: str, *, timeout: float = 300, attempts: int = 3
) -> Path:
    """下载 → 校验 → 才 rename 成 `dest`。传输层失败有界重试；**hash 不符绝不重试**。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        try:
            return verify_sha256(dest, expected_sha256)
        except HashMismatch:
            dest.unlink()
    part = dest.with_name(dest.name + ".part")
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp, part.open("wb") as fh:
                shutil.copyfileobj(resp, fh)
        except (urllib.error.URLError, OSError) as exc:
            part.unlink(missing_ok=True)
            last = exc
            if attempt < attempts:
                time.sleep(2.0 * attempt)
            continue
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        try:
            verify_sha256(part, expected_sha256)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        os.replace(part, dest)
        return dest
    assert last is not None
    raise last


def _extract_member(archive: Path, member: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        info = tar.getmember(member)
        if not info.isfile():
            raise ValueError(f"{member} 不是普通文件")
        src = tar.extractfile(info)
        assert src is not None
        tmp = dest.with_name(dest.name + ".part")
        with tmp.open("wb") as out:
            out.write(src.read())
        os.replace(tmp, dest)
    return dest


def load_allowlist(path: Path = ALLOWLIST) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not data.get("faces"):
        raise SystemExit(f"{path}: 不是 schema 1 的 allowlist")
    return data


def fetch(
    dest: Path, *, cache: Path | None = None, allowlist: dict | None = None
) -> dict[str, Path]:
    """把 allowlist 里的字体与许可证全文落到 `dest`；每个字节都经过 sha256（tarball 整体一次、
    单文件各一次）。已在且校验通过的直接复用（幂等）。返回 face_id → 路径。"""
    data = allowlist or load_allowlist()
    dest = Path(dest)
    cache = Path(cache) if cache else DEFAULT_CACHE
    archives: dict[str, Path] = {}

    def archive_for(src: dict) -> Path:
        if src["url"] not in archives:
            archives[src["url"]] = download_verified(
                src["url"], cache / Path(src["url"]).name, src["sha256"]
            )
        return archives[src["url"]]

    out: dict[str, Path] = {}
    for face_id, spec in data["faces"].items():
        target = dest / spec["file"]
        src = spec["source"]
        if target.is_file():
            try:
                verify_sha256(target, spec["sha256"])
            except HashMismatch:
                target.unlink()
        if not target.is_file():
            if src["kind"] == "tarball-member":
                _extract_member(archive_for(src), src["member"], target)
            elif src["kind"] == "file":
                download_verified(src["url"], target, src["sha256"])
            else:
                raise SystemExit(f"{face_id}: 不认识的来源 {src['kind']!r}")
        verify_sha256(target, spec["sha256"])
        out[face_id] = target
    for rel, src in data.get("license_files", {}).items():
        # 许可证全文与字体同一条纪律：落盘的那份按 `file_sha256` 核（tarball 成员只核 tarball 不够——
        # 复用的构建目录里被截断 / 替换的 LICENSE 会原样进 wheel）
        target = dest / rel
        if target.is_file():
            try:
                verify_sha256(target, src["file_sha256"])
            except HashMismatch:
                target.unlink()
        if not target.is_file():
            if src["kind"] == "tarball-member":
                _extract_member(archive_for(src), src["member"], target)
            else:
                download_verified(src["url"], target, src["sha256"])
        verify_sha256(target, src["file_sha256"])
    return out


def check(dest: Path, allowlist: dict | None = None) -> list[str]:
    """目录里每张脸的 sha256 与 allowlist 逐个核；许可证全文也要在。返回问题清单（空 = 通过）。"""
    data = allowlist or load_allowlist()
    dest = Path(dest)
    problems: list[str] = []
    for face_id, spec in data["faces"].items():
        path = dest / spec["file"]
        if not path.is_file():
            problems.append(f"缺 {face_id}（{spec['file']}）")
            continue
        try:
            verify_sha256(path, spec["sha256"])
        except HashMismatch as exc:
            problems.append(f"{face_id}: {exc}")
    for rel, src in data.get("license_files", {}).items():
        path = dest / rel
        if not path.is_file():
            problems.append(f"缺许可证全文 {rel}")
            continue
        try:
            verify_sha256(path, src["file_sha256"])
        except HashMismatch as exc:
            problems.append(f"{rel}: {exc}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="字体落盘目录")
    ap.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="下载缓存目录（默认 build/fonts-cache，不在字体目录里）",
    )
    ap.add_argument("--check", action="store_true", help="只核对，不下载")
    args = ap.parse_args(argv)
    if args.check:
        problems = check(args.dest)
        for p in problems:
            print(p, file=sys.stderr)
        if problems:
            return 1
        print(f"{args.dest}：{len(load_allowlist()['faces'])} 张脸 sha256 全部一致")
        return 0
    paths = fetch(args.dest, cache=args.cache)
    for face_id, path in paths.items():
        print(f"{sha256_file(path)}  {path}")
    problems = check(args.dest)
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
