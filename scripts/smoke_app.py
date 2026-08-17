#!/usr/bin/env python3
"""端到端冒烟：把打好的应用真正启动一次，走完一条完整的用户路径。

为什么要有这个脚本（而不是把命令堆在工作流 YAML 里）：
  * 本地能一条命令复现 CI 的失败，不用推一次 commit 等一轮流水线；
  * nightly 的安装测试与 PR 的快速冒烟共用同一条路径，不会两边慢慢跑偏；
  * 失败时统一把 app.log 收上来——Windows 上「双击没反应」的真正原因几乎
    永远在那个日志里，而不在标准输出里。

走的路径（每一步都必须真的发生，不是只看进程还活着）：
    启动 → /api/version → 渲染环境自检 → 打开示例项目 → 列面板
    → 引擎渲染一次 → 导出 PDF → **再导出一次覆盖同名文件** → 干净退出

覆盖导出那一步是刻意的：Windows 上文件被占用/只读时的表现与 POSIX 完全
不同，而「导出第二次」正是用户最常做的动作。

`--expect-source bundled` 是 Windows 桌面版的核心验收：断言渲染用的是
**随安装包附带的内置 runtime**，不是运行器上碰巧装着的 Python。没有这条
断言，一台装了 matplotlib 的 CI 机器会让「内置环境根本没打进去」全程绿灯。

用法：
    python scripts/smoke_app.py --exe dist/Magplot/Magplot.exe
    python scripts/smoke_app.py --python .venv/bin/python      # 源码树/wheel
    python scripts/smoke_app.py --exe dist/Magplot/Magplot.exe \
        --figures examples/runtime_check --expect-source bundled \
        --expect-packages numpy,pandas,scipy,seaborn,PIL,matplotlib
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# Windows 上 stdout 被重定向成管道时会退回系统区域编码（cp1252/cp936），
# 打印带中文或 ✓ 的进度就会 UnicodeEncodeError——冒烟明明通过却以非零退出。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
DEFAULT_FIGURES = REPO / "examples" / "figures"
BOOT_TIMEOUT_S = 120      # 冷启动 + 首次 import 在 Windows runner 上可能很慢
RENDER_TIMEOUT_S = 300    # 冷启动一个 matplotlib 会话


class SmokeError(RuntimeError):
    pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get(url: str, timeout: float = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url: str, payload: dict, timeout: float = 30) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _wait_ready(base: str, proc: subprocess.Popen, timeout: float) -> dict:
    """等 /api/version 可访问；进程中途退出就立刻失败（别干等到超时）。"""
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SmokeError(f"进程在就绪前退出，returncode={proc.returncode}")
        try:
            return _get(f"{base}/api/version", timeout=5)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last = exc
            time.sleep(1)
    raise SmokeError(f"{timeout:.0f}s 内 /api/version 仍不可访问: {last}")


def _leftover_workers() -> list[str]:
    """还在跑的 worker 子进程。硬停之后如果还剩，就是用户机器上的僵尸进程。

    不引入 psutil（依赖边界要干净），用各平台自带的进程列表工具；工具本身
    不可用时返回空表——冒烟不该因为环境里没有 ps/tasklist 就红。
    """
    # 认的是 worker.py 的完整路径：ps 默认会按终端宽度截断命令行，
    # 只匹配 "worker.py" 既可能漏（被截掉）也可能误伤（别的项目的同名文件）
    marker = os.path.join("magplot", "engine", "worker.py")
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["wmic", "process", "get", "CommandLine"],
                capture_output=True, text=True, timeout=20,
                encoding="utf-8", errors="replace").stdout
        else:
            # -ww：不按终端宽度截断（截断了就什么都匹配不上）
            out = subprocess.run(["ps", "-eww", "-o", "args="],
                                 capture_output=True, text=True,
                                 timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    # 再要求带上 worker 独有的参数：只匹配路径的话，连「正在查找 worker.py」
    # 的那条 shell 命令自己都会被算成残留进程
    return [ln.strip()[:160] for ln in out.splitlines()
            if marker in ln and "--figures-dir" in ln]


def _tail(path: Path, n: int = 120) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return f"（读不到 {path}）"
    return "\n".join(lines[-n:])


def _check_environment(base: str, expect_source: str | None,
                       expect_packages: list[str]) -> None:
    """渲染环境自检：解释器来源 + 内置科学栈真能 import。

    分两步问是有意的：`source` 回答「用的是谁的 Python」，`imports` 回答
    「那套 Python 到底能不能用」。文件都在但某个 .pyd 被杀毒软件隔离时，
    只看第一步会以为一切正常。
    """
    query = "?probe=" + (",".join(expect_packages) if expect_packages else "1")
    env = _get(f"{base}/api/engine/environment{query}", timeout=180)
    src = env.get("source") or "(无)"
    print(f"✓ 渲染环境: python={env.get('python')} 来源={src} "
          f"matplotlib={env.get('matplotlib')}")

    rt = env.get("runtime") or {}
    if rt.get("present"):
        print(f"  内置 runtime: Python {rt.get('python')}，"
              f"{len(rt.get('packages') or {})} 个锁定包，valid={rt.get('valid')}")

    if expect_source:
        if not env.get("ok"):
            raise SmokeError(f"渲染环境不可用: {env.get('error') or env}")
        if src != expect_source:
            raise SmokeError(
                f"解释器来源应为 {expect_source}，实际是 {src}"
                f"（python={env.get('python')}）。桌面版这一条不能将就："
                "说明内置 runtime 没进包，或者被机器上别的 Python 抢先了。")

    if expect_packages:
        imports = env.get("imports") or {}
        missing = [n for n in expect_packages if not imports.get(n)]
        if missing:
            raise SmokeError(f"这些包在渲染环境里 import 不到: {missing}"
                             f"（实测结果 {imports}）")
        print("✓ 内置科学栈: " +
              "  ".join(f"{n}={imports[n]}" for n in expect_packages))


def run_smoke(launch: list[str], figures: Path, workdir: Path,
              port: int | None = None, expect_source: str | None = None,
              expect_packages: list[str] | None = None) -> None:
    port = port or _free_port()
    base = f"http://127.0.0.1:{port}"
    data_dir = workdir / "data"
    config_dir = workdir / "config"
    export_dir = workdir / "exports"
    for d in (data_dir, config_dir, export_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 隔离用户目录：绝不污染跑测试的机器，也保证「用户目录为空」这个
    # 首次启动场景每次都真的从零开始
    env = {
        **os.environ,
        "MAGPLOT_DATA_DIR": str(data_dir),
        "MAGPLOT_CONFIG_DIR": str(config_dir),
        "APPDATA": str(workdir / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(workdir / "AppData" / "Local"),
        "HOME": str(workdir / "home"),
        "USERPROFILE": str(workdir / "home"),
        # 关掉联网检查更新：冒烟不该依赖 GitHub 可达
        "MAGPLOT_NO_UPDATE_CHECK": "1",
        # 让 /api/shutdown 可用：冒烟要验证的是**干净退出**，不是硬停
        "MAGPLOT_ALLOW_SHUTDOWN": "1",
    }
    for key in ("APPDATA", "LOCALAPPDATA", "HOME", "USERPROFILE"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)

    if expect_source == "bundled":
        # 要验的是「干净的 Windows 电脑上装完即可用」，那台电脑上不会有
        # MM_WORKER_PYTHON。上一步残留的这个变量会让内置 runtime 根本轮不上，
        # 断言随即失败——与其看着它失败，不如在这里就摘干净并说清楚。
        for key in ("MM_WORKER_PYTHON",):
            if env.pop(key, None):
                print(f"! 已从子进程环境移除 {key}（--expect-source=bundled "
                      "要求不借助任何外部解释器）")

    cmd = [*launch, "--port", str(port), "--no-browser", "--figures", str(figures)]
    print(f"$ {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    log_path = data_dir / "cache" / "app.log"
    try:
        version = _wait_ready(base, proc, BOOT_TIMEOUT_S)
        print(f"✓ 已启动: version={version.get('version')} build={version.get('build')}")

        _check_environment(base, expect_source, expect_packages or [])

        project = _get(f"{base}/api/project")
        if not project.get("open"):
            raise SmokeError(f"项目没打开: {project}")
        print(f"✓ 项目: {project['figures_dir']}（{project.get('scripts')} 个脚本）")

        panels = _get(f"{base}/api/panels")["panels"]
        if not panels:
            raise SmokeError("示例项目里一个面板都没扫到")
        scripted = [p for p in panels if p.get("script")]
        print(f"✓ 面板 {len(panels)} 个，其中可参数化 {len(scripted)} 个")

        if scripted:
            target = scripted[0]
            res = _post(f"{base}/api/engine/render",
                        {"id": target["id"], "patches": []},
                        timeout=RENDER_TIMEOUT_S)
            if not res.get("manifest"):
                raise SmokeError(f"渲染没回 manifest: {res}")
            print(f"✓ 引擎渲染 {target['id']}: "
                  f"{len(res['manifest'].get('elements', []))} 个元素")
        else:
            print("! 没有可参数化面板，跳过引擎渲染（注册表为空？）")

        spec = {
            "page_w_mm": 80, "page_h_mm": 40, "formats": ["pdf"], "stem": "smoke",
            "objects": [
                {"type": "text", "text": "Smoke cm^{-1}", "x_mm": 5, "y_mm": 5,
                 "w_mm": 70, "h_mm": 8, "size_pt": 10, "bold": False,
                 "color": "#000000", "align": "left"},
                {"type": "panel", "id": panels[0]["id"], "x_mm": 5, "y_mm": 15,
                 "w_mm": 40, "h_mm": 20},
            ],
        }
        out = _post(f"{base}/api/export", spec, timeout=RENDER_TIMEOUT_S)
        first = Path(out["export_dir"]) / out["files"][0]["name"]
        if not first.is_file() or first.stat().st_size < 500:
            raise SmokeError(f"导出的 PDF 不对劲: {first}")
        print(f"✓ 导出 {first.name}（{first.stat().st_size} 字节）")

        # 覆盖导出：Windows 上文件占用/只读的表现与 POSIX 完全不同，
        # 而「再导出一次」正是用户最常做的动作
        out2 = _post(f"{base}/api/export", {**spec, "overwrite": True},
                     timeout=RENDER_TIMEOUT_S)
        second = Path(out2["export_dir"]) / out2["files"][0]["name"]
        if not second.is_file():
            raise SmokeError("第二次导出没有产出文件")
        print(f"✓ 覆盖导出 {second.name}")

        diag = _get(f"{base}/api/diagnostics")["checks"]
        bad = [c for c in diag if not c["ok"]]
        print(f"✓ 诊断 {len(diag)} 项，其中未通过 {len(bad)}: "
              f"{[c['id'] for c in bad]}")
    except Exception:
        print("--- app.log ---", flush=True)
        print(_tail(log_path), flush=True)
        # worker 侧的 traceback 只在这些文件里；「渲染失败」十次有九次要看它
        for wlog in sorted((data_dir / "cache" / "engine").glob("*/worker.log")):
            print(f"--- {wlog.parent.name}/worker.log ---", flush=True)
            print(_tail(wlog, 60), flush=True)
        # 内置 runtime 的清单：装了哪个 Python、哪些包、构建时冒烟过没有。
        # 只看固定的几个落点，不 rglob 整个 dist（那是几万个文件）。
        exe_dir = Path(launch[0]).resolve().parent
        for mf in (exe_dir / "_internal" / "runtime" / "runtime-manifest.json",
                   exe_dir / "runtime" / "runtime-manifest.json"):
            if mf.is_file():
                print(f"--- {mf} ---", flush=True)
                print(_tail(mf, 80), flush=True)
                break
        raise
    finally:
        graceful = False
        if proc.poll() is None:
            # 先走受控退出，验证它真的会自己收尾（worker 子进程一起收掉）；
            # 只有这条路走不通才硬停——硬停测不出「关掉窗口留下僵尸进程」
            try:
                _post(f"{base}/api/shutdown", {}, timeout=10)
                proc.wait(timeout=30)
                graceful = proc.returncode == 0
            except (urllib.error.URLError, OSError, TimeoutError,
                    subprocess.TimeoutExpired):
                pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        out_text = proc.stdout.read() if proc.stdout else ""
        if out_text.strip():
            print("--- 进程输出 ---")
            print(out_text[-4000:])
        print(f"{'✓ 干净退出' if graceful else '! 强制停止'}，退出码 {proc.returncode}")
        # 无论怎么退出，都不能在用户机器上留下僵尸 worker
        leftover = _leftover_workers()
        if leftover:
            raise SmokeError(f"退出后仍有 worker 子进程残留: {leftover}")
        if not graceful:
            raise SmokeError("受控退出失败，只能硬停——关窗口时很可能也是这样")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--exe", help="打好的可执行文件（PyInstaller 产物）")
    g.add_argument("--python", help="解释器路径，用 `-m magplot` 启动")
    ap.add_argument("--figures", default=str(DEFAULT_FIGURES))
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--keep", action="store_true", help="保留临时工作目录便于排查")
    ap.add_argument("--expect-source", default=None,
                    help="断言渲染解释器的来源（bundled / configured / "
                         "managed_venv / system / current_process / env_override）")
    ap.add_argument("--expect-packages", default="",
                    help="逗号分隔的 import 名，断言在渲染环境里都能 import "
                         "（如 numpy,pandas,scipy,seaborn,PIL,matplotlib）")
    args = ap.parse_args(argv)

    launch = [args.exe] if args.exe else [args.python, "-m", "magplot"]
    if args.exe and not Path(args.exe).is_file():
        print(f"找不到可执行文件: {args.exe}", file=sys.stderr)
        return 2

    figures = Path(args.figures).resolve()
    if not figures.is_dir():
        print(f"示例项目目录不存在: {figures}", file=sys.stderr)
        return 2

    packages = [p.strip() for p in args.expect_packages.split(",") if p.strip()]
    workdir = Path(tempfile.mkdtemp(prefix="magplot-smoke-"))
    try:
        run_smoke(launch, figures, workdir, args.port,
                  args.expect_source, packages)
    except Exception as exc:  # noqa: BLE001 — 冒烟脚本要给人看结论
        print(f"::error::冒烟失败: {exc}", file=sys.stderr)
        return 1
    finally:
        if args.keep:
            print(f"工作目录保留在 {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
    print("冒烟通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
