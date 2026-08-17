#!/usr/bin/env python3
"""构建 Tauri 桌面应用（macOS .app/.dmg、Windows NSIS）。

    python scripts/build_desktop.py                # 完整链路
    python scripts/build_desktop.py --skip-tauri   # 只出 sidecar（调试用）

链路（顺序即依赖）：

1. 版本同步：src/magplot/__init__.py 是唯一出处，写进 src-tauri/tauri.conf.json
   与 src-tauri/Cargo.toml（Tauri 的 About/安装包版本号不允许漂移）。
2. 前端：scripts/build_frontend.py → src/magplot/web/（sidecar 从这里出界面）。
3. sidecar：PyInstaller onedir（packaging/magplot.spec，刻意不含 matplotlib，
   不用 onefile——科学场景的启动解压等不起）→ dist/Magplot/。
4. Tauri：pnpm dlx @tauri-apps/cli build，把 dist/Magplot 作为资源打进壳
   （src-tauri/tauri.conf.json 的 bundle.resources）。

签名/公证不在本脚本内：本地无证书时产物是未签名测试包（macOS 上 Tauri 会
落 adhoc 签名），发行签名走 CI（.github/workflows/desktop-tauri.yml）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Windows 上 stdout 被重定向成管道时会退回系统区域编码（cp1252/cp936），
# 打印带中文的进度就会 UnicodeEncodeError——构建明明成功了却以非零退出。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], **kw) -> None:
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=str(ROOT), **kw)


def read_version() -> str:
    text = (ROOT / "src" / "magplot" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("src/magplot/__init__.py 里找不到 __version__")
    return m.group(1)


def sync_version(version: str) -> None:
    conf_path = ROOT / "src-tauri" / "tauri.conf.json"
    conf = json.loads(conf_path.read_text(encoding="utf-8"))
    if conf.get("version") != version:
        conf["version"] = version
        conf_path.write_text(json.dumps(conf, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        print(f"* tauri.conf.json 版本 → {version}")
    cargo_path = ROOT / "src-tauri" / "Cargo.toml"
    cargo = cargo_path.read_text(encoding="utf-8")
    new = re.sub(r'^version = "[^"]+"', f'version = "{version}"', cargo,
                 count=1, flags=re.M)
    if new != cargo:
        cargo_path.write_text(new, encoding="utf-8")
        print(f"* Cargo.toml 版本 → {version}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-tauri", action="store_true",
                    help="只构建前端与 sidecar，不打 Tauri 壳")
    ap.add_argument("--bundles", default="app,dmg" if sys.platform == "darwin"
                    else "nsis", help="Tauri bundler 目标（默认按平台）")
    args = ap.parse_args()

    version = read_version()
    print(f"* Magplot {version}")
    sync_version(version)

    run([sys.executable, str(ROOT / "scripts" / "build_frontend.py")])
    run([sys.executable, "-m", "PyInstaller",
         str(ROOT / "packaging" / "magplot.spec"), "--noconfirm"])

    sidecar = ROOT / "dist" / "Magplot" / \
        ("Magplot.exe" if sys.platform == "win32" else "Magplot")
    if not sidecar.is_file():
        raise SystemExit(f"sidecar 产物缺失: {sidecar}")

    if args.skip_tauri:
        print("* --skip-tauri：到此为止")
        return

    run(["pnpm", "dlx", "@tauri-apps/cli@latest", "build",
         "--bundles", args.bundles])
    out = ROOT / "src-tauri" / "target" / "release" / "bundle"
    print(f"* 产物目录: {out}")


if __name__ == "__main__":
    main()
