#!/usr/bin/env python3
"""构建 Tauri 桌面应用（macOS .app/.dmg、Windows NSIS）。

    python scripts/build_desktop.py                # 完整链路
    python scripts/build_desktop.py --skip-tauri   # 只出 sidecar（调试用）

链路（顺序即依赖）：

1. 版本同步：src/magplot/__init__.py 是唯一出处，写进 src-tauri/tauri.conf.json
   与 src-tauri/Cargo.toml（Tauri 的 About/安装包版本号不允许漂移）。
2. 前端：scripts/build_frontend.py → src/magplot/web/（sidecar 从这里出界面）。
3. Rust supervisor：cargo build --release → workerd/target/release/，由
   packaging/magplot.spec 收进 sidecar 的 _internal/。**没有 cargo 就直接中止**
   ——回退到 Python 渲染池是静默的，做出来的包功能一样不缺、只是慢，
   发出去也不会有人发现。
4. 内置渲染 runtime：scripts/build_worker_runtime.py → runtime/，由
   packaging/magplot.spec 收进 sidecar 的 _internal/runtime。**Windows 与 macOS
   都要**——没有它，用户得先自己装 Python，而这正是这条链要消灭的东西。
   已有一份且平台/锁文件都对得上时直接复用（重建一次要下 25 MiB + 装 300 MiB）。
5. sidecar：PyInstaller onedir（packaging/magplot.spec，刻意不含 matplotlib，
   不用 onefile——科学场景的启动解压等不起）→ dist/Magplot/。
6. Tauri：pnpm dlx @tauri-apps/cli build，把 dist/Magplot 作为资源打进壳
   （src-tauri/tauri.conf.json 的 bundle.resources）。

签名/公证不在本脚本内：本地无证书时产物是未签名测试包（macOS 上 Tauri 会
落 adhoc 签名），发行签名走 CI（.github/workflows/desktop-tauri.yml）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
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


WORKERD_NAME = "magplot-workerd.exe" if sys.platform == "win32" else "magplot-workerd"


def build_workerd() -> Path:
    """构建 Rust supervisor，返回二进制路径（magplot.spec 从同一位置取）。

    `MAGPLOT_WORKERD_BIN` 可指到已经构建好的产物（交叉编译 / CI 分步构建时用），
    此时不跑 cargo。两条路都会**确认文件真的在**：桌面产物缺了 workerd 不会
    报错、只会悄悄慢下来，所以这里宁可当场中止也不留下一个「看起来正常」的包。
    """
    prebuilt = os.environ.get("MAGPLOT_WORKERD_BIN")
    if prebuilt:
        exe = Path(prebuilt)
        if not exe.is_file():
            raise SystemExit(f"MAGPLOT_WORKERD_BIN 指向的文件不存在: {exe}")
        print(f"* workerd（沿用现成产物）: {exe}")
        return exe

    if shutil.which("cargo") is None:
        raise SystemExit(
            "找不到 cargo，无法构建 magplot-workerd（Rust supervisor）。\n"
            "  · 装一次 Rust 工具链：https://rustup.rs\n"
            "  · 或者在别处构建好，用 MAGPLOT_WORKERD_BIN=<路径> 指过来\n"
            "桌面产物必须自带 workerd——缺了它渲染静默回退到 Python 池，"
            "队列合并/超时强杀/取消全部失效，而界面上一点异常都看不出来。")
    run(["cargo", "build", "--release",
         "--manifest-path", str(ROOT / "workerd" / "Cargo.toml")])
    exe = ROOT / "workerd" / "target" / "release" / WORKERD_NAME
    if not exe.is_file():
        raise SystemExit(f"cargo 跑完了，但产物不在预期位置: {exe}")
    print(f"* workerd: {exe}（{exe.stat().st_size // 1024} KiB）")
    return exe


RUNTIME_DIR = ROOT / "runtime"
RUNTIME_MANIFEST = RUNTIME_DIR / "runtime-manifest.json"
RUNTIME_LOCK = ROOT / "packaging" / "runtime-lock.json"

sys.path.insert(0, str(ROOT / "scripts"))
from build_worker_runtime import BuildError, check_runtime_dir  # noqa: E402


def _runtime_is_current() -> str:
    """现成的 runtime/ 能不能直接用；能用回空串，不能用回原因。

    判据三条，缺一不可：平台/架构对得上、冒烟真的过了、锁文件没变过。
    前两条与 packaging/magplot.spec 共用 `check_runtime_dir()`（同一把尺）；
    第三条是本地复用特有的——改了锁文件却复用旧产物，等于「以为换了 numpy
    版本，其实一个字节都没动」，而这种错要到用户报「版本不对」时才发现。
    """
    if not RUNTIME_MANIFEST.is_file():
        return "还没构建过"
    try:
        info = check_runtime_dir(RUNTIME_MANIFEST, require_smoke=True)
    except BuildError as exc:
        return str(exc).splitlines()[0]
    try:
        lock_sha = hashlib.sha256(RUNTIME_LOCK.read_bytes()).hexdigest()
    except OSError as exc:
        return f"读不到锁文件（{exc}）"
    if (info.get("build") or {}).get("lock_sha256") != lock_sha:
        return "锁文件变过了"
    return ""


def build_runtime(skip: bool, force: bool) -> None:
    """构建（或复用）内置渲染 runtime。

    `--skip-runtime` 是**开发态**的省时开关：产物照样能跑，只是渲染会回退到
    机器上已有的 Python。发行构建绝不能用它——CI 那边还会加
    `MAGPLOT_REQUIRE_RUNTIME=1`，spec 会当场把没有 runtime 的包拦下来。
    """
    if skip:
        print("* --skip-runtime：不构建内置 runtime"
              "（产物将依赖机器上已有的 Python，**不可发行**）")
        return
    if not force:
        why = _runtime_is_current()
        if not why:
            info = json.loads(RUNTIME_MANIFEST.read_text(encoding="utf-8"))
            print(f"* 内置 runtime（复用现成的）: {info['platform']['os']}/"
                  f"{info['platform']['arch']}  Python {info['python']['version']}")
            return
        print(f"* 内置 runtime 需要重建：{why}")
    run([sys.executable, str(ROOT / "scripts" / "build_worker_runtime.py"),
         "--clean"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-tauri", action="store_true",
                    help="只构建前端与 sidecar，不打 Tauri 壳")
    ap.add_argument("--skip-runtime", action="store_true",
                    help="不构建内置渲染 runtime（开发态省时；产物不可发行）")
    ap.add_argument("--rebuild-runtime", action="store_true",
                    help="强制重建内置 runtime，即使现成的看起来是对的")
    ap.add_argument("--bundles", default="app,dmg" if sys.platform == "darwin"
                    else "nsis", help="Tauri bundler 目标（默认按平台）")
    args = ap.parse_args()

    version = read_version()
    print(f"* Magplot {version}")
    sync_version(version)

    run([sys.executable, str(ROOT / "scripts" / "build_frontend.py")])
    build_workerd()
    build_runtime(args.skip_runtime, args.rebuild_runtime)
    run([sys.executable, "-m", "PyInstaller",
         str(ROOT / "packaging" / "magplot.spec"), "--noconfirm"])

    sidecar = ROOT / "dist" / "Magplot" / \
        ("Magplot.exe" if sys.platform == "win32" else "Magplot")
    if not sidecar.is_file():
        raise SystemExit(f"sidecar 产物缺失: {sidecar}")
    # spec 里那条 binaries 真的落到 _internal/ 了没有——这一步只花一次 stat，
    # 却挡住了「打包器换了版本、落点变了」这种发出去才发现的回归
    packed = sidecar.parent / "_internal" / WORKERD_NAME
    if not packed.is_file():
        raise SystemExit(
            f"sidecar 里没有 workerd: {packed}\n"
            "  packaging/magplot.spec 的 binaries 落点与 "
            "engine/workerd_client.find_workerd() 对不上了。")

    if args.skip_tauri:
        print("* --skip-tauri：到此为止")
        return

    # CLI 版本钉死：src-tauri/windows/installer.nsi 是按这个版本的上游模板
    # 打的品牌补丁，模板与打包器必须同源（升级时两处一起动，见模板头注释）
    cmd = ["pnpm", "dlx", "@tauri-apps/cli@2.11.4", "build",
           "--bundles", args.bundles]
    # tauri.conf.json 里 createUpdaterArtifacts 常开（发行链要它），但打包器
    # 一开它就要用 minisign 私钥签名——本机开发通常没有那把钥匙。没有就地关掉，
    # 而不是让每个开发者为了跑一次构建去配发行密钥。
    if not os.environ.get("TAURI_SIGNING_PRIVATE_KEY"):
        print("* 没有 TAURI_SIGNING_PRIVATE_KEY：本次不产出更新包（安装包照打）")
        cmd += ["--config", json.dumps({"bundle": {"createUpdaterArtifacts": False}})]
    run(cmd)
    out = ROOT / "src-tauri" / "target" / "release" / "bundle"
    print(f"* 产物目录: {out}")


if __name__ == "__main__":
    main()
