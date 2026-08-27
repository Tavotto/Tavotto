#!/usr/bin/env python3
"""构建浏览器 playground（网站 `/try`）的静态产物。

    python scripts/build_browser_playground.py                # 构建 web/dist-playground/
    python scripts/build_browser_playground.py --fingerprint  # 只打印源码指纹
    python scripts/build_browser_playground.py --check        # 校验现有产物是否与源码同步

产物（`web/dist-playground/`，不进本仓库 git）：

    index.html                  页面（vite 产物 playground.html 改名）
    assets/…                    带内容哈希的 JS/CSS/Worker
    engine.zip                  Tavotto 引擎的 Python 模块（Pyodide 里解到 /engine）
    playground-manifest.json    指纹 + 版本 + 逐文件 sha256

网站仓库（Tavotto_website）用 `pnpm sync-playground` 把这套拷进 `public/try/`
并提交；`pnpm check-playground` 拿本地产品仓库重算指纹，比对已提交产物——
**改了编辑器/引擎却没重新构建同步**在那边是红灯，不是悄悄发旧画布
（与 build_mcp_widget 同一条纪律：过期但还能跑的产物比构建失败更坏）。

engine.zip 是**确定性**产物：条目按名排序、时间戳钉死、权限钉死——
同一份源码在任何机器上产出逐字节相同的 zip。

指纹逻辑直接 import `build_mcp_widget.digest`（路径/换行/顺序的规范化
只有一份实现）。纯标准库。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_mcp_widget import _pnpm, digest  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DIST = WEB / "dist-playground"
ENGINE = ROOT / "src" / "tavotto" / "engine"
RUNTIME_LOCK = ROOT / "packaging" / "playground-runtime.json"

#: 进 engine.zip 的引擎模块——manifest/overrides/pathgeom/patchspec 与桌面
#: worker 是同一份文件（语义只有一份实现，ADR 0007）；browser* 是浏览器适配层。
ENGINE_FILES = [
    "browser.py",
    "browser_imports.py",
    "figcapture.py",
    "manifest.py",
    "overrides.py",
    "patchspec.py",
    "pathgeom.py",
]

MANIFEST_NAME = "playground-manifest.json"


def source_fingerprint() -> str:
    """playground 行为的源码指纹。

    集合 = 前端源码（web/src 全部 ts/tsx/css——画布、stores、inspector 都在
    bundle 里，任何一处都可能改变 playground 行为）+ 案例源码与封面
    （examples/*.py 经 ?raw 进 bundle，generated/ 的封面与 manifest 是
    卡片资产——两者任何一个变了都是另一个 playground）+ playground 入口
    与构建配置 + 进 engine.zip 的每个 Python 模块 + 运行时锁 + 规范文件。
    """
    files: list[Path] = []
    for p in (WEB / "src").rglob("*"):
        if p.is_file() and p.suffix in (".ts", ".tsx", ".css") and ".test." not in p.name:
            files.append(p)
    files += sorted((WEB / "src" / "playground" / "examples").glob("*.py"))
    files += sorted((WEB / "src" / "playground" / "generated").glob("*"))
    files += [
        WEB / "playground.html",
        WEB / "vite.playground.config.ts",
        WEB / "package.json",
        RUNTIME_LOCK,
        ROOT / "src" / "tavotto" / "profiles" / "publication.json",
    ]
    files += [ENGINE / name for name in ENGINE_FILES]
    return digest((p.relative_to(ROOT), p.read_bytes()) for p in files if p.is_file())


def build_engine_zip(out: Path) -> None:
    """确定性 zip：排序条目 + 1980 时间戳 + 固定权限。"""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(ENGINE_FILES):
            data = (ENGINE / name).read_bytes().replace(b"\r\n", b"\n")
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)


def _git_commit() -> str:
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                              capture_output=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, text=True,
                               capture_output=True, check=True).stdout.strip()
        return head + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build() -> dict:
    cmd = [*_pnpm(), "exec", "vite", "build", "--config", "vite.playground.config.ts"] \
        if _pnpm()[0].endswith("pnpm") else [*_pnpm(), "build", "--config", "vite.playground.config.ts"]
    proc = subprocess.run(cmd, cwd=WEB, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"vite build 失败（退出码 {proc.returncode}）")

    # 页面要以 /try/ 的 index.html 形态被静态托管
    (DIST / "playground.html").replace(DIST / "index.html")
    build_engine_zip(DIST / "engine.zip")

    lock = json.loads(RUNTIME_LOCK.read_text(encoding="utf-8"))
    entries = {}
    for p in sorted(DIST.rglob("*")):
        if p.is_file() and p.name != MANIFEST_NAME:
            entries[p.relative_to(DIST).as_posix()] = \
                hashlib.sha256(p.read_bytes()).hexdigest()

    manifest = {
        "schema": 1,
        "fingerprint": source_fingerprint(),
        "product_commit": _git_commit(),
        "pyodide_version": lock["pyodide_version"],
        "python": lock["python"],
        "packages": lock["packages"],
        "engine_files": sorted(ENGINE_FILES),
        "files": entries,
    }
    (DIST / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return manifest


def check() -> int:
    path = DIST / MANIFEST_NAME
    want = source_fingerprint()
    if not path.is_file():
        print(f"没有产物（{path} 不存在）。先跑 python scripts/build_browser_playground.py",
              file=sys.stderr)
        return 1
    have = json.loads(path.read_text(encoding="utf-8")).get("fingerprint")
    if have != want:
        print(f"playground 产物过期：源码指纹 {want}，产物里是 {have}。"
              f"重跑 python scripts/build_browser_playground.py", file=sys.stderr)
        return 1
    print(f"playground 产物与源码一致（{want}）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fingerprint", action="store_true",
                    help="只打印源码指纹（网站仓库的 check-playground 调它比对）")
    ap.add_argument("--check", action="store_true",
                    help="校验 dist-playground 是否与源码同步，不构建")
    args = ap.parse_args(argv)

    if args.fingerprint:
        print(source_fingerprint())
        return 0
    if args.check:
        return check()

    manifest = build()
    total = sum((DIST / f).stat().st_size for f in manifest["files"])
    print(f"已写入 {DIST}（{len(manifest['files'])} 个文件，"
          f"{total / 1024:.0f} KiB，指纹 {manifest['fingerprint']}）")
    print("下一步：cd ../Tavotto_website && pnpm sync-playground")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("NODE_ENV", "production")
    raise SystemExit(main())
