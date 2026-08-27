#!/usr/bin/env python3
"""合成 Tauri 更新器要的 `latest.json`。

桌面版的「软件内直接更新」靠它：壳按 tauri.conf.json 里的 endpoint 拉这份
清单，比版本号，然后下载对应平台的包、用内置公钥校验签名、就地安装。

**为什么要单独一个 job / 单独一个脚本**：两个平台在各自的 matrix 腿里构建，
谁都只知道自己那一半；清单必须等两条腿都跑完才拼得出来。而拼接本身有真实
的判断（哪个文件对应哪个平台、签名文件在不在、版本号对不对），塞进 YAML
里就没法写用例了。

签名文件（`.sig`）由 Tauri 的 minisign 私钥产生，私钥只在 CI 的 secret 里。
**这里只搬运，不生成签名**——没有 .sig 的平台直接报错，绝不产出一份「看着
完整、装到一半发现签名对不上」的清单。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 文件名 → Tauri 的平台标识。macOS 只发 arm64（sidecar 由 arm64 runner 上的
# PyInstaller 打出来，本来就跑不了 Intel），所以不给 darwin-x86_64 ——
# 给了等于把一个装不上的包推给 Intel 用户。
PLATFORM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("darwin-aarch64", re.compile(r"\.app\.tar\.gz$")),
    ("windows-x86_64", re.compile(r"(?:x64|x86_64)[-_].*setup\.nsis\.zip$|setup\.nsis\.zip$")),
]

RELEASE_URL = "https://github.com/{owner}/{repo}/releases/download/{tag}/{name}"


def platform_of(name: str) -> str | None:
    for key, pat in PLATFORM_PATTERNS:
        if pat.search(name):
            return key
    return None


def collect(root: Path) -> dict[str, tuple[Path, Path]]:
    """扫出 {平台: (包, 签名)}。包在、签名不在 = 硬错误。"""
    found: dict[str, tuple[Path, Path]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.endswith(".sig"):
            continue
        key = platform_of(path.name)
        if key is None:
            continue
        sig = path.with_name(path.name + ".sig")
        if not sig.exists():
            raise SystemExit(
                f"{path.name} 没有配套的 .sig —— 构建时没拿到更新器私钥？"
                "宁可不发更新清单，也不能发一份装不上的"
            )
        if key in found:
            raise SystemExit(f"{key} 匹配到多个包：{found[key][0].name} 与 {path.name}")
        found[key] = (path, sig)
    return found


def build_manifest(
    root: Path,
    version: str,
    tag: str,
    owner: str,
    repo: str,
    notes: str,
    require: list[str] | None = None,
) -> dict:
    found = collect(root)
    if not found:
        raise SystemExit("一个更新包都没找到——检查 createUpdaterArtifacts 与私钥配置")
    # 「一个都没有」会被上面拦下，「只有一半」以前不会——那才是真正发生过的那次：
    # macOS 的产物没被传成 artifact，清单只剩 windows-x86_64，全链路绿灯，
    # 而 dmg 那批用户从此查不到新版本。缺哪个平台就报哪个。
    if require:
        missing = [key for key in require if key not in found]
        if missing:
            raise SystemExit(
                f"清单缺平台：{', '.join(missing)}（只找到 {', '.join(sorted(found))}）"
                "——那个平台的用户会永远收不到更新。检查该腿有没有把 out/ 传成"
                " desktop-tauri-* artifact"
            )
    return {
        "version": version,
        "notes": notes,
        "platforms": {
            key: {
                # 签名文件里是 base64 的 minisign 签名，原样塞进去
                "signature": sig.read_text(encoding="utf-8").strip(),
                "url": RELEASE_URL.format(owner=owner, repo=repo, tag=tag, name=pkg.name),
            }
            for key, (pkg, sig) in sorted(found.items())
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--artifacts", required=True, type=Path, help="收集到的产物目录（递归扫描）")
    ap.add_argument("--tag", required=True, help="Release tag，如 v0.6.1")
    ap.add_argument("--owner", default="Tavotto")
    ap.add_argument("--repo", default="tavotto")
    ap.add_argument("--notes", default="", help="更新说明（原样写进清单）")
    ap.add_argument("--require", default="", help="必须齐全的平台（逗号分隔），缺一个即失败")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    version = args.tag[1:] if args.tag.startswith("v") else args.tag
    require = [k.strip() for k in args.require.split(",") if k.strip()]
    manifest = build_manifest(
        args.artifacts, version, args.tag, args.owner, args.repo, args.notes, require
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"✓ {args.out}（{', '.join(manifest['platforms'])}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
