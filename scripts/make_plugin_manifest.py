#!/usr/bin/env python3
"""生成 Codex 插件的版本清单 `codex-plugin.json`（+ 可选的插件 zip）。

    python scripts/make_plugin_manifest.py --tag v0.7.1 --out out/codex-plugin.json
    python scripts/make_plugin_manifest.py --tag v0.7.1 --out out/codex-plugin.json \
        --zip out/codex-plugin-0.7.1.zip

发布时把这两份挂到 GitHub Release，插件下次被调用就会看到新版本
（`codex-plugin/skills/tavotto-figure/scripts/update_check.py` 拉的
`releases/latest/download/codex-plugin.json` 就是它）。

**为什么不放在 desktop-tauri.yml 的 updater-manifest 里**：那个 job 依赖桌面
构建产物和 minisign 私钥，没配私钥时整个跳过。插件清单跟这两样都没关系——
挂在那儿等于「哪天没配签名密钥，插件的更新通道就悄悄停了」，而且全绿。
所以它跟 wheel 一起走 release.yml，每个 tag 都发。

版本号**只从 `.codex-plugin/plugin.json` 读**，并与 tag 核对：对不上直接失败。
发一份说自己是 0.7.1、里面装着 0.7.0 的清单，用户会永远看到「有新版本」，
更新完还是看到——而且没有任何报错。
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "codex-plugin"
PLUGIN_JSON = PLUGIN_DIR / ".codex-plugin" / "plugin.json"

#: 清单 schema。改字段语义要 +1，读的一方（update_check.SCHEMA）同步。
SCHEMA = 1
#: 这个插件最低要求的 Tavotto 版本 = **第一个带 `tavotto open` 的版本**
#: （v0.7.0，见 git log src/tavotto/engine/handoff.py）。没有它交接根本无从谈起。
#: 往上调之前想清楚：这个值会让老用户看到「去升级 Tavotto」的提示。
MIN_TAVOTTO_VERSION = "0.7.0"
CHANNEL = "stable"
REPO = "Tavotto/Tavotto"

#: 打包时跳过的东西（缓存与本机产物，跟插件本身无关）
ZIP_SKIP = {"__pycache__", ".DS_Store", ".pytest_cache"}


def plugin_version(path: Path = PLUGIN_JSON) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise SystemExit(f"{path} 里没有可用的 version")
    return version.strip()


def check_tag(tag: str, version: str) -> None:
    """tag 与插件版本必须一致（tag 允许带 v 前缀）。"""
    if tag.lstrip("vV") != version:
        raise SystemExit(
            f"tag {tag} 与插件版本 {version} 对不上。\n"
            f"  发版前先改 {PLUGIN_JSON.relative_to(ROOT)} 的 version——"
            "清单说自己是新版、里面却是旧版，用户会永远看到「有新版本」。"
        )


def build_manifest(tag: str, version: str, *, published_at: str | None = None) -> dict:
    base = f"https://github.com/{REPO}/releases"
    out = {
        "schema": SCHEMA,
        "plugin": "tavotto",
        "channel": CHANNEL,
        "latest_version": version,
        "download_url": f"{base}/download/{tag}/codex-plugin-{version}.zip",
        "release_notes_url": f"{base}/tag/{tag}",
        "min_tavotto_version": MIN_TAVOTTO_VERSION,
    }
    if published_at:
        out["published_at"] = published_at
    return out


def build_zip(target: Path, source: Path = PLUGIN_DIR) -> Path:
    """把插件目录打成 zip（顶层目录固定叫 codex-plugin）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in source.rglob("*") if p.is_file())
    kept = [p for p in files if not ZIP_SKIP & set(p.relative_to(source).parts)]
    if not kept:
        raise SystemExit(f"{source} 里没有文件可打包")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in kept:
            zf.write(path, str(Path("codex-plugin") / path.relative_to(source)))
    return target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", required=True, help="发布的 tag（如 v0.7.1）")
    ap.add_argument("--out", required=True, help="清单写到哪儿")
    ap.add_argument("--zip", default=None, help="同时打一个插件 zip 到这儿")
    ap.add_argument("--published-at", default=None, help="ISO8601 发布时间（CI 传 date -u）")
    args = ap.parse_args(argv)

    version = plugin_version()
    check_tag(args.tag, version)
    manifest = build_manifest(args.tag, version, published_at=args.published_at)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"* 插件清单: {out}（{version}）")
    if args.zip:
        made = build_zip(Path(args.zip))
        print(f"* 插件包: {made}（{made.stat().st_size // 1024} KiB）")
        if made.name != f"codex-plugin-{version}.zip":
            print(
                f"::warning::zip 名字与清单里的 download_url 对不上: {made.name}", file=sys.stderr
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
