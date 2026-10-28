#!/usr/bin/env python3
"""生成 / 校验 `src/tavotto/resources/managed_cli_lock.json`（受控 CLI 的锁，`engine/managedcli.py` 读它）。

    python scripts/managed_cli_lock.py --check                  # 离线：锁文件形状与规范化（CI 跑这个）
    python scripts/managed_cli_lock.py --check --online         # 联网：与 GitHub release / npmmirror 的元数据对账（不下载大文件）
    python scripts/managed_cli_lock.py --update 0.166.0 [--cache DIR]   # 维护者：下载全部平台、两来源逐文件比对后写锁

## 更新流程（升版本）

1. `--update <版本>`：对每个平台下载 GitHub `codex-package-<triple>.tar.gz` 与 npmmirror
   `@openai/codex@<版本>-<平台>` 的 tgz；核 npm 元数据里的 sha512 integrity；两边各自解开，**整棵树摘要必须相同**
   （不同 = 来源不一致，拒绝写锁）；写入每个来源自己的 size / sha256，以及共用的 tree_sha256 / unpacked_size。
2. `--check --online` 再对一次账；`pytest tests/test_managed_cli.py`。
3. 发版前在 mac / Windows 真机各点一次「安装」（真探测 `--version`）。
`--cache DIR`：已下载过的归档按 URL 文件名放在 DIR 里就不重下（大小对得上才用）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tavotto.engine import managedcli as mc  # noqa: E402

LOCK = ROOT / "src" / "tavotto" / "resources" / "managed_cli_lock.json"

PLATFORMS = {
    "darwin-arm64": ("aarch64-apple-darwin", "bin/codex"),
    "darwin-x64": ("x86_64-apple-darwin", "bin/codex"),
    "win32-x64": ("x86_64-pc-windows-msvc", "bin/codex.exe"),
    "linux-x64": ("x86_64-unknown-linux-musl", "bin/codex"),
}
TEMPLATES = {
    "github_url": mc.GITHUB_PREFIX + "rust-v{version}/codex-package-{triple}.tar.gz",
    "mirror_url": mc.MIRROR_PREFIX + "codex-{version}-{npm}.tgz",
    "mirror_strip": "package/vendor/{triple}/",
}


COMMENT = "受控 CLI 的锁；不要手改。更新流程见 scripts/managed_cli_lock.py 文件头：--update <版本> 生成，--check [--online] 校验。"


def dumps(lock: dict) -> str:
    return json.dumps(lock, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "tavotto-lock/1"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 — 固定主机
        return json.load(r)


def _download(url: str, cache: Path | None, size: int | None) -> Path:
    name = url.rsplit("/", 1)[-1]
    if cache is not None:
        cand = cache / name
        if cand.is_file() and (size is None or cand.stat().st_size == size):
            return cand
    dest = (cache or Path(tempfile.mkdtemp())) / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "tavotto-lock/1"})
    with urllib.request.urlopen(req, timeout=120) as r, dest.open("wb") as f:  # noqa: S310
        while chunk := r.read(1 << 20):
            f.write(chunk)
    return dest


def _sri512(path: Path) -> str:
    import base64

    h = hashlib.sha512()
    with path.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return "sha512-" + base64.b64encode(h.digest()).decode()


def _tree(archive: Path, strip: str, version: str, entry: str) -> tuple[str, int, int]:
    cli = mc.ManagedCli("codex", version, "x", "x", entry, "0" * 64, 1 << 40, 0, ())
    src = mc.ArchiveSource("https://github.com/x", 1, "0" * 64, strip)
    with tempfile.TemporaryDirectory() as td:
        stage = Path(td) / "t"
        stage.mkdir()
        # 复用产品的解包器（同一套成员校验）；文件数与总量检查在这里放开
        try:
            mc._extract(archive, stage, src, cli, mc._Job(None, None))  # noqa: SLF001
        except mc.ManagedCliError as exc:
            if exc.code != mc.ERROR_INVALID_ARCHIVE or "文件数" not in str(exc):
                raise
        return mc.tree_digest(stage)


def update(version: str, cache: Path | None) -> dict:
    tag = f"rust-v{version}"
    rel = _get_json(f"https://api.github.com/repos/openai/codex/releases/tags/{tag}")
    assets = {a["name"]: a for a in rel["assets"]}
    plats: dict = {}
    for key, (triple, entry) in PLATFORMS.items():
        npm = key
        a = assets[f"codex-package-{triple}.tar.gz"]
        gh_sha = str(a["digest"]).removeprefix("sha256:")
        meta = _get_json(f"https://registry.npmmirror.com/@openai/codex/{version}-{npm}")
        dist = meta["dist"]
        gh_url = TEMPLATES["github_url"].format(version=version, triple=triple)
        mi_url = TEMPLATES["mirror_url"].format(version=version, npm=npm)
        gh_file = _download(gh_url, cache, a["size"])
        mi_file = _download(mi_url, cache, dist["size"])
        assert mc._sha256_file(gh_file) == gh_sha, f"{key}: GitHub 归档与 release 摘要不符"  # noqa: SLF001
        assert _sri512(mi_file) == dist["integrity"], f"{key}: npm 归档与 registry integrity 不符"
        t1 = _tree(gh_file, "", version, entry)
        t2 = _tree(mi_file, TEMPLATES["mirror_strip"].format(triple=triple), version, entry)
        if t1 != t2:
            raise SystemExit(f"{key}: 两个来源解开的树不同，拒绝写锁")
        plats[key] = {
            "triple": triple,
            "npm": npm,
            "entrypoint": entry,
            "tree_sha256": t1[0],
            "file_count": t1[1],
            "unpacked_size": t1[2],
            "github": {"size": a["size"], "sha256": gh_sha},
            "mirror": {
                "size": dist["size"],
                "sha256": mc._sha256_file(mi_file),  # noqa: SLF001
                "integrity": dist["integrity"],
            },
        }
        print(f"{key}: ok tree={t1[0][:12]} files={t1[1]}", file=sys.stderr)
    return {
        "_comment": COMMENT,
        "schema": mc.LOCK_SCHEMA,
        "clis": {"codex": {"version": version, **TEMPLATES, "platforms": plats}},
    }


def check_online(lock: dict) -> list[str]:
    problems: list[str] = []
    for name, cli in lock["clis"].items():
        v = cli["version"]
        rel = _get_json(f"https://api.github.com/repos/openai/codex/releases/tags/rust-v{v}")
        assets = {a["name"]: a for a in rel["assets"]}
        for key, p in cli["platforms"].items():
            a = assets.get(f"codex-package-{p['triple']}.tar.gz")
            if (
                a is None
                or str(a["digest"]) != f"sha256:{p['github']['sha256']}"
                or a["size"] != p["github"]["size"]
            ):
                problems.append(f"{name}/{key}: GitHub 资产与锁不符")
            dist = _get_json(f"https://registry.npmmirror.com/@openai/codex/{v}-{p['npm']}")["dist"]
            if dist["integrity"] != p["mirror"]["integrity"] or dist["size"] != p["mirror"]["size"]:
                problems.append(f"{name}/{key}: npmmirror 元数据与锁不符")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--update", metavar="VERSION")
    ap.add_argument("--cache", type=Path)
    ap.add_argument("--lock", type=Path, default=LOCK)
    args = ap.parse_args(argv)
    if args.update:
        args.lock.write_text(dumps(update(args.update, args.cache)), encoding="utf-8")
        print(f"已写 {args.lock}")
        return 0
    text = args.lock.read_text(encoding="utf-8")
    lock = json.loads(text)
    try:
        mc.validate_lock(lock)
    except ValueError as exc:
        print(f"锁文件不合格: {exc}", file=sys.stderr)
        return 1
    if text != dumps(lock):
        print("锁文件不是规范格式（请用 --update 生成，不要手改）", file=sys.stderr)
        return 1
    if args.online:
        problems = check_online(lock)
        for p in problems:
            print(p, file=sys.stderr)
        if problems:
            return 1
    print("managed_cli_lock.json OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
