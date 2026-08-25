#!/usr/bin/env python3
"""打 Windows 更新包（`*-setup.nsis.zip`）——条目必须是 STORED（不压缩）。

为什么专门一个脚本、为什么不能用 Compress-Archive / 任何默认压缩：
tauri-plugin-updater 对 Windows 的 zip 依赖是 **`default-features = false`**
（上游 Cargo.toml 的 `[target.'cfg(target_os = "windows")'.dependencies.zip]`），
deflate 解压 feature 被关掉，只解得开 STORED（方法 0）的条目。
Compress-Archive 默认 deflate（方法 8），于是装出去的每一次 Windows 应用内
更新都在下载完成后死在 "Compression method not supported"——用户看到的是
进度条走满、然后「无法安装更新，去 Releases 手动下载」。这一步从 v0.7.0
引入应用内更新起就是坏的（2026-08-25 确定性复现：同版本 zip crate 4.6.1 +
default-features=false 对线上 v0.10.0 更新包 EXTRACT FAILED，重打成
STORED 后 EXTRACT OK；macOS 走 tar.gz + flate2，不经这条路）。

tauri-bundler 自己打的 nsis.zip 用的正是 STORED——它知道自己的更新器解不了
deflate。我们必须重打（要装进 SignPath 签过名的最终安装包），重打就得遵守
同一个约定。exe 本来就是 NSIS 压过的，STORED 几乎不多占体积。

更新器解包后在**顶层**找第一个 `.exe` 执行，所以条目名取 exe 的
basename、不带任何目录前缀。

    python scripts/make_updater_zip.py --exe out/Tavotto-X.Y.Z-Windows-Setup.exe \
        --out out/Tavotto_X.Y.Z_x64-setup.nsis.zip
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path


def build(exe: Path, out: Path) -> None:
    if not exe.is_file():
        raise SystemExit(f"找不到最终安装包: {exe}")
    if exe.suffix.lower() != ".exe":
        raise SystemExit(f"更新包里只该装安装器 exe，拿到的是: {exe.name}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as z:
        z.write(exe, arcname=exe.name)
    # 自检：读回确认每个条目都是 STORED。写错压缩方式的更新包**宁可不发**——
    # 发出去的表现是「每个用户的应用内更新都失败」，而 CI 全绿。
    with zipfile.ZipFile(out) as z:
        bad = [i.filename for i in z.infolist() if i.compress_type != zipfile.ZIP_STORED]
    if bad:
        raise SystemExit(f"条目不是 STORED: {bad}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exe", required=True, type=Path, help="签过名的最终安装包")
    ap.add_argument("--out", required=True, type=Path, help="更新包输出路径（*.nsis.zip）")
    args = ap.parse_args(argv)
    build(args.exe, args.out)
    print(f"✓ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
