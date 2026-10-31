#!/usr/bin/env python3
"""生成 / 校验 `src/tavotto/resources/managed_cli_lock.json`（受控 CLI 的锁，`engine/managedcli.py` 读它）。

    python scripts/managed_cli_lock.py --check                  # 离线：锁文件形状与规范化（CI 跑这个）
    python scripts/managed_cli_lock.py --check --online         # 联网：与 GitHub release / npmmirror 的元数据对账（不下载大文件）
    python scripts/managed_cli_lock.py --update 0.166.0 [--cli codex] [--cache DIR]   # 维护者：下载全部平台、两来源逐文件比对后写锁
    python scripts/managed_cli_lock.py --update 2.1.308 --cli claude [--cache DIR]    # 同上，Claude Code（只改这一个 CLI 的条目）

## 更新流程（升版本）

1. `--update <版本>`：对每个平台下载 GitHub `codex-package-<triple>.tar.gz` 与 npmmirror
   `@openai/codex@<版本>-<平台>` 的 tgz；核 npm 元数据里的 sha512 integrity；两边各自解开，**整棵树摘要必须相同**
   （不同 = 来源不一致，拒绝写锁）；写入每个来源自己的 size / sha256，以及共用的 tree_sha256 / unpacked_size。
2. `--check --online` 再对一次账；`pytest tests/test_managed_cli.py`。
3. 发版前在 mac / Windows 真机各点一次「安装」（真探测 `--version`）。
`--cache DIR`：已下载过的归档按 URL 文件名放在 DIR 里就不重下（大小对得上才用）。

Claude Code：npmmirror 平台子包 `@anthropic-ai/claude-code-<平台>@<版本>`（tgz，只取 `package/<二进制>`）与官方
`https://downloads.claude.ai/claude-code-releases/<版本>/<平台>/<二进制>` 裸文件；后者的 sha256 必须等于同目录
`manifest.json` 里该平台的 checksum，前者必须等于 registry 元数据的 sha512 integrity，**两边解出的二进制字节必须逐字节相同**。
（manifest 另有 GPG 签名，这里不验——锁里钉的是我们自己算的 sha256，运行时只认锁。）
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

CODEX_PLATFORMS = {
    "darwin-arm64": ("aarch64-apple-darwin", "bin/codex"),
    "darwin-x64": ("x86_64-apple-darwin", "bin/codex"),
    "win32-x64": ("x86_64-pc-windows-msvc", "bin/codex.exe"),
    "linux-x64": ("x86_64-unknown-linux-musl", "bin/codex"),
}
CODEX_SOURCES = [
    {"key": "github", "url": mc.GITHUB_PREFIX + "rust-v{version}/codex-package-{triple}.tar.gz"},
    {
        "key": "mirror",
        "url": mc.MIRROR_PREFIX + "codex-{version}-{npm}.tgz",
        "strip": "package/vendor/{triple}/",
    },
]
#: Claude Code：平台键同时是 npm 子包后缀与 GCS 目录名；入口是原生二进制（Windows 带 .exe）
CLAUDE_PLATFORMS = {
    "darwin-arm64": ("darwin-arm64", "claude"),
    "darwin-x64": ("darwin-x64", "claude"),
    "win32-x64": ("win32-x64", "claude.exe"),
    "linux-x64": ("linux-x64", "claude"),
}
CLAUDE_SOURCES = [
    {
        "key": "mirror",
        "url": mc.CLAUDE_MIRROR_PREFIX + "{npm}/-/claude-code-{npm}-{version}.tgz",
        "strip": "package/",
        "only": "{entry}",
    },
    {"key": "gcs", "url": mc.CLAUDE_GCS_PREFIX + "{version}/{npm}/{entry}", "raw": True},
]
GCS_MANIFEST = mc.CLAUDE_GCS_PREFIX + "{version}/manifest.json"
NPM_META = {
    "codex": "https://registry.npmmirror.com/@openai/codex/{version}-{npm}",
    "claude": "https://registry.npmmirror.com/@anthropic-ai/claude-code-{npm}/{version}",
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


def _tree(
    name: str, archive: Path, version: str, entry: str, strip: str = "", only: str = "", raw=False
) -> tuple[str, int, int]:
    cli = mc.ManagedCli(name, version, "x", "x", entry, "0" * 64, 1 << 40, 0, ())
    src = mc.ArchiveSource("https://github.com/x", 1, "0" * 64, strip, only, raw)
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


def _plat_common(key: str, triple: str, entry: str, tree: tuple[str, int, int]) -> dict:
    return {
        "triple": triple,
        "npm": key,
        "entrypoint": entry,
        "tree_sha256": tree[0],
        "file_count": tree[1],
        "unpacked_size": tree[2],
    }


def _update_codex(version: str, cache: Path | None) -> dict:
    tag = f"rust-v{version}"
    rel = _get_json(f"https://api.github.com/repos/openai/codex/releases/tags/{tag}")
    assets = {a["name"]: a for a in rel["assets"]}
    plats: dict = {}
    gh_src, mi_src = CODEX_SOURCES
    for key, (triple, entry) in CODEX_PLATFORMS.items():
        a = assets[f"codex-package-{triple}.tar.gz"]
        gh_sha = str(a["digest"]).removeprefix("sha256:")
        dist = _get_json(NPM_META["codex"].format(version=version, npm=key))["dist"]
        gh_file = _download(gh_src["url"].format(version=version, triple=triple), cache, a["size"])
        mi_file = _download(mi_src["url"].format(version=version, npm=key), cache, dist["size"])
        assert mc._sha256_file(gh_file) == gh_sha, f"{key}: GitHub 归档与 release 摘要不符"  # noqa: SLF001
        assert _sri512(mi_file) == dist["integrity"], f"{key}: npm 归档与 registry integrity 不符"
        t1 = _tree("codex", gh_file, version, entry)
        t2 = _tree("codex", mi_file, version, entry, mi_src["strip"].format(triple=triple))
        if t1 != t2:
            raise SystemExit(f"{key}: 两个来源解开的树不同，拒绝写锁")
        plats[key] = {
            **_plat_common(key, triple, entry, t1),
            "github": {"size": a["size"], "sha256": gh_sha},
            "mirror": {
                "size": dist["size"],
                "sha256": mc._sha256_file(mi_file),  # noqa: SLF001
                "integrity": dist["integrity"],
            },
        }
        print(f"{key}: ok tree={t1[0][:12]} files={t1[1]}", file=sys.stderr)
    return {"version": version, "sources": CODEX_SOURCES, "platforms": plats}


def _update_claude(version: str, cache: Path | None) -> dict:
    manifest = _get_json(GCS_MANIFEST.format(version=version))
    if manifest.get("version") != version:
        raise SystemExit(f"manifest 的版本 {manifest.get('version')!r} 与要锁的 {version} 不符")
    mi_src, gcs_src = CLAUDE_SOURCES
    plats: dict = {}
    for key, (triple, entry) in CLAUDE_PLATFORMS.items():
        m = manifest["platforms"][key]
        if m["binary"] != entry:
            raise SystemExit(f"{key}: manifest 的二进制名 {m['binary']!r} 不是 {entry!r}")
        dist = _get_json(NPM_META["claude"].format(version=version, npm=key))["dist"]
        kw = {"version": version, "npm": key, "entry": entry}
        # 缓存文件名取 URL 末段；两个平台的 GCS 文件都叫 claude，所以按平台分目录
        sub = (cache / key) if cache is not None else None
        mi_file = _download(mi_src["url"].format(**kw), sub, dist["size"])
        raw_file = _download(gcs_src["url"].format(**kw), sub, m["size"])
        assert mc._sha256_file(raw_file) == m["checksum"], f"{key}: GCS 文件与 manifest 摘要不符"  # noqa: SLF001
        assert _sri512(mi_file) == dist["integrity"], f"{key}: npm 归档与 registry integrity 不符"
        t1 = _tree("claude", mi_file, version, entry, mi_src["strip"], mi_src["only"].format(**kw))
        t2 = _tree("claude", raw_file, version, entry, raw=True)
        if t1 != t2:
            raise SystemExit(f"{key}: 两个来源的二进制不是同一份字节，拒绝写锁")
        plats[key] = {
            **_plat_common(key, triple, entry, t1),
            "mirror": {
                "size": dist["size"],
                "sha256": mc._sha256_file(mi_file),  # noqa: SLF001
                "integrity": dist["integrity"],
            },
            "gcs": {"size": m["size"], "sha256": m["checksum"]},
        }
        print(f"{key}: ok tree={t1[0][:12]} files={t1[1]}", file=sys.stderr)
    return {"version": version, "sources": CLAUDE_SOURCES, "platforms": plats}


UPDATERS = {"codex": _update_codex, "claude": _update_claude}


def update(name: str, version: str, cache: Path | None, base: dict | None = None) -> dict:
    """只重写 `name` 这一个 CLI 的条目，其它 CLI 原样保留。"""
    clis = dict((base or {}).get("clis") or {})
    clis[name] = UPDATERS[name](version, cache)
    return {"_comment": COMMENT, "schema": mc.LOCK_SCHEMA, "clis": clis}


def check_online(lock: dict) -> list[str]:
    problems: list[str] = []
    for name, cli in lock["clis"].items():
        v = cli["version"]
        if name == "codex":
            rel = _get_json(f"https://api.github.com/repos/openai/codex/releases/tags/rust-v{v}")
            assets = {a["name"]: a for a in rel["assets"]}
        else:
            manifest = _get_json(GCS_MANIFEST.format(version=v))
        for key, p in cli["platforms"].items():
            if name == "codex":
                a = assets.get(f"codex-package-{p['triple']}.tar.gz")
                if (
                    a is None
                    or str(a["digest"]) != f"sha256:{p['github']['sha256']}"
                    or a["size"] != p["github"]["size"]
                ):
                    problems.append(f"{name}/{key}: GitHub 资产与锁不符")
            else:
                m = manifest["platforms"].get(key)
                if (
                    m is None
                    or m["checksum"] != p["gcs"]["sha256"]
                    or m["size"] != p["gcs"]["size"]
                ):
                    problems.append(f"{name}/{key}: 官方 manifest 与锁不符")
            dist = _get_json(NPM_META[name].format(version=v, npm=p["npm"]))["dist"]
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
    ap.add_argument(
        "--cli", choices=sorted(UPDATERS), default="codex", help="--update 针对哪个 CLI"
    )
    ap.add_argument("--cache", type=Path)
    ap.add_argument("--lock", type=Path, default=LOCK)
    args = ap.parse_args(argv)
    if args.update:
        base = json.loads(args.lock.read_text(encoding="utf-8")) if args.lock.is_file() else None
        args.lock.write_text(
            dumps(update(args.cli, args.update, args.cache, base)), encoding="utf-8"
        )
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
