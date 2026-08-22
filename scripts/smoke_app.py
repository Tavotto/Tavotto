#!/usr/bin/env python3
"""端到端冒烟：把打好的应用真正启动一次，走完一条完整的用户路径。

为什么要有这个脚本（而不是把命令堆在工作流 YAML 里）：
  * 本地能一条命令复现 CI 的失败，不用推一次 commit 等一轮流水线；
  * nightly 的安装测试与 PR 的快速冒烟共用同一条路径，不会两边慢慢跑偏；
  * 失败时统一把 app.log 收上来——Windows 上「双击没反应」的真正原因几乎
    永远在那个日志里，而不在标准输出里。

走的路径（每一步都必须真的发生，不是只看进程还活着）：
    启动 → /api/version → 渲染环境自检 → 打开示例项目 → 列面板
    → 引擎渲染一次 → **再渲染一次（热会话）** → 导出 PDF
    → **再导出一次覆盖同名文件** → 干净退出

第二次渲染与覆盖导出都是刻意的：前者验的是热会话没被第一次渲染搞坏，
后者验的是 Windows 上文件被占用/只读时的表现（与 POSIX 完全不同），
而「再来一次」正是用户最常做的动作。

`--expect-source bundled` 是**两个桌面平台**的核心验收：断言渲染用的是
随安装包附带的内置 runtime，不是运行器上碰巧装着的 Python。没有这条断言，
一台装了 matplotlib 的 CI 机器会让「内置环境根本没打进去」全程绿灯。
加上这个参数时，会话环境里所有可能抢在内置 runtime 前面的东西
（`TAVOTTO_WORKER_PYTHON`、Conda、`PYTHONHOME`/`PYTHONPATH`、活动 venv）
都会被**摘干净**——要验的是「一台干净电脑上装完即可用」。

`--expect-runtime` 再往前一步：断言 `runtime.expected` 与 `runtime.valid`
都为真。`--expect-source bundled` 只说明「这次用的是内置的」，而它回答
「这个安装形态**本来就该**带 runtime，且带的这份是完整的」——两者会在
不同的坏法下分别失败（前者被别的 Python 抢先，后者架构装错/文件损坏）。

`--expect-control-plane workerd` 是同一条思路的另一面：断言渲染真的跑在
Rust supervisor 上。回退到 Python 渲染池是**静默**的降级，包里没打进
tavotto-workerd 时功能一样不缺、只是慢，不断言就永远发现不了。

用法：
    python scripts/smoke_app.py --exe dist/Tavotto/Tavotto.exe
    python scripts/smoke_app.py --python .venv/bin/python      # 源码树/wheel
    python scripts/smoke_app.py --exe dist/Tavotto/Tavotto.exe \
        --figures examples/runtime_check --expect-source bundled \
        --expect-runtime --expect-control-plane workerd \
        --expect-packages numpy,pandas,scipy,seaborn,PIL,matplotlib
    # 中文 + 空格路径（用户目录整个搬过去，不只是项目路径）
    python scripts/smoke_app.py --exe ... --workdir "/tmp/我的 目录/smoke"
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

#: `--expect-source bundled` 时必须从子进程环境里摘掉的变量。
#:
#: 每一条都能让内置 runtime 轮不上，且失败方式各不相同：
#:   TAVOTTO_WORKER_PYTHON           优先级第一，直接顶掉内置的
#:   CONDA_PREFIX / CONDA_*     CI harness 或开发机的 conda 泄进来
#:   VIRTUAL_ENV                激活着的 venv 同理
#:   PYTHONHOME                 最狠的一条：内置解释器被指到别的前缀，起都起不来
#:   PYTHONPATH / PYTHONUSERBASE  import 到别处的 numpy，版本对不上却「能跑」
_HOSTILE_TO_BUNDLED = (
    "TAVOTTO_WORKER_PYTHON",
    "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "CONDA_EXE", "CONDA_PYTHON_EXE",
    "CONDA_PROMPT_MODIFIER", "CONDA_SHLVL",
    "VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT",
    "PYTHONHOME", "PYTHONPATH", "PYTHONUSERBASE", "PYTHONSTARTUP",
    "PYTHONEXECUTABLE",
)


class SmokeError(RuntimeError):
    pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# 会话认证（ADR 0008）：_assert_auth_enforced 验证默认 deny 后从凭据文件
# 装上本机认证头，此后所有请求都带着它
_AUTH: dict[str, str] = {}


def _get(url: str, timeout: float = 30) -> dict:
    req = urllib.request.Request(url, headers=dict(_AUTH))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url: str, payload: dict, timeout: float = 30) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **_AUTH}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _assert_auth_enforced(base: str, data_dir: Path, port: int) -> None:
    """默认 deny 是 1.0 的安全底线：未认证请求必须 401，凭据文件必须在。

    这里同时验证两件事——① 打包产物里认证真的开着（不是只在源码树里开）；
    ② 本机进程的凭据交接（0600 文件 + X-Tavotto-Auth 头）真的能用。
    验完把认证头装上，后续冒烟全部带着它走。
    """
    _AUTH.clear()  # 同进程跑第二轮冒烟时不许带着上一轮的凭据验「默认 deny」
    try:
        _get(f"{base}/api/project", timeout=10)
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise SmokeError(f"未认证请求应 401，实际 {e.code}")
    else:
        raise SmokeError("未认证请求被放行了——会话认证没有生效（P0 回归）")
    secret_file = data_dir / "session" / f"port-{port}.json"
    if not secret_file.is_file():
        raise SmokeError(f"本机会话凭据文件缺失: {secret_file}")
    secret = json.loads(secret_file.read_text(encoding="utf-8"))["secret"]
    _AUTH["X-Tavotto-Auth"] = secret
    _get(f"{base}/api/session/ping", timeout=10)
    print("✓ 会话认证：默认 deny + 本机凭据交接可用")


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


def _leftover_workers(data_dir: Path) -> list[str]:
    """**本次冒烟**留下的 worker 子进程。硬停之后如果还剩，就是僵尸进程。

    不引入 psutil（依赖边界要干净），用各平台自带的进程列表工具；工具本身
    不可用时返回空表——冒烟不该因为环境里没有 ps/tasklist 就红。

    三个条件缺一不可，最后一条是关键：

    * worker.py 的**完整路径**——ps 默认按终端宽度截断命令行，只匹配
      "worker.py" 既可能漏（被截掉）也可能误伤（别的项目的同名文件）；
    * `--figures-dir`——只匹配路径的话，连「正在查找 worker.py」的那条
      shell 命令自己都会被算成残留进程；
    * **本次隔离数据目录**出现在命令行里（worker 的 `--out-dir` 落在它下面）。
      少了这条，开发机上只要**另一个 Tavotto 正开着**（用户自己那份、
      另一个终端里的实例），冒烟就会报「退出后仍有 worker 残留」——
      而那个进程与本次运行毫无关系。假报一次，下次真出问题时这条提示
      就已经被学会无视了。
    """
    marker = os.path.join("tavotto", "engine", "worker.py")
    ours = str(data_dir)
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["wmic", "process", "get", "CommandLine"],
                capture_output=True, text=True, timeout=20,
                encoding="utf-8", errors="replace").stdout
        else:
            # -ww：不按终端宽度截断（截断了就什么都匹配不上）
            out = subprocess.run(["ps", "-eww", "-o", "args="],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln.strip()[:160] for ln in out.splitlines()
            if marker in ln and "--figures-dir" in ln and ours in ln]


def _tail(path: Path, n: int = 120) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return f"（读不到 {path}）"
    return "\n".join(lines[-n:])


def _check_environment(base: str, expect_source: str | None,
                       expect_packages: list[str],
                       expect_runtime: bool = False) -> None:
    """渲染环境自检：解释器来源 + 内置 runtime 完整性 + 科学栈真能 import。

    分三步问是有意的：`source` 回答「用的是谁的 Python」，`runtime` 回答
    「随包那份完不完整」，`imports` 回答「那套 Python 到底能不能用」。
    文件都在但某个扩展模块被杀毒软件隔离（Windows）或没签名被 Gatekeeper
    拦下（macOS）时，只看前两步都会以为一切正常。
    """
    query = "?probe=" + (",".join(expect_packages) if expect_packages else "1")
    env = _get(f"{base}/api/engine/environment{query}", timeout=180)
    src = env.get("source") or "(无)"
    print(f"✓ 渲染环境: python={env.get('python')} 来源={src} "
          f"matplotlib={env.get('matplotlib')}")

    rt = env.get("runtime") or {}
    if rt.get("present"):
        print(f"  内置 runtime: Python {rt.get('python')}，"
              f"{len(rt.get('packages') or {})} 个锁定包，"
              f"expected={rt.get('expected')} valid={rt.get('valid')}")

    if expect_source:
        if not env.get("ok"):
            raise SmokeError(f"渲染环境不可用: {env.get('error') or env}")
        if src != expect_source:
            raise SmokeError(
                f"解释器来源应为 {expect_source}，实际是 {src}"
                f"（python={env.get('python')}）。桌面版这一条不能将就："
                "说明内置 runtime 没进包，或者被机器上别的 Python 抢先了。")

    if expect_runtime:
        if not rt.get("expected"):
            raise SmokeError(
                "runtime.expected 为假——这个产物没有被识别成「本该自带 runtime」"
                "的桌面形态。多半是 engine/runtime.ships_bundled_runtime() 没把"
                "本平台算进去，或者跑的根本不是冻结产物。")
        if not rt.get("valid"):
            raise SmokeError(
                f"runtime.valid 为假：{rt.get('error') or rt.get('code') or rt}。"
                "内置渲染环境缺失/损坏/架构不符——这样的安装包不能发。")
        print("✓ 内置 runtime: expected=True valid=True")

    if expect_packages:
        imports = env.get("imports") or {}
        missing = [n for n in expect_packages if not imports.get(n)]
        if missing:
            raise SmokeError(f"这些包在渲染环境里 import 不到: {missing}"
                             f"（实测结果 {imports}）")
        print("✓ 内置科学栈: " +
              "  ".join(f"{n}={imports[n]}" for n in expect_packages))


def _check_control_plane(base: str, expect: str | None) -> None:
    """渲染控制面自检：**在渲染之后**问，那时池里才有真会话。

    `selected` 回答「产物里有没有 tavotto-workerd」，`sessions` 回答「刚才那次
    渲染到底走了谁」。只看第一个不够：workerd 建会话失败会静默回退到 Python
    渲染池（那是刻意设计的降级），做出来的包功能一样不缺、只是慢，界面上
    一点异常都没有。
    """
    cp = (_get(f"{base}/api/engine/environment").get("control_plane") or {})
    selected, sessions = cp.get("selected"), cp.get("sessions") or []
    print(f"✓ 渲染控制面: selected={selected} sessions={sessions}")
    if not expect:
        return
    if selected != expect:
        raise SmokeError(
            f"渲染控制面应为 {expect}，实际是 {selected}"
            "（产物里没打进 tavotto-workerd？）")
    if expect == "workerd" and "workerd" not in sessions:
        raise SmokeError(
            f"二进制在，但刚才那次渲染走的是 {sessions}——workerd 起不来，"
            "已静默回退到 Python 渲染池（看数据目录 cache/workerd.log）")


def run_smoke(launch: list[str], figures: Path, workdir: Path,
              port: int | None = None, expect_source: str | None = None,
              expect_packages: list[str] | None = None,
              expect_control_plane: str | None = None,
              expect_runtime: bool = False) -> None:
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
        "TAVOTTO_DATA_DIR": str(data_dir),
        "TAVOTTO_CONFIG_DIR": str(config_dir),
        "APPDATA": str(workdir / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(workdir / "AppData" / "Local"),
        "HOME": str(workdir / "home"),
        "USERPROFILE": str(workdir / "home"),
        # 关掉联网检查更新：冒烟不该依赖 GitHub 可达
        "TAVOTTO_NO_UPDATE_CHECK": "1",
        # 匿名用量统计**硬关**。两个理由，缺一不可：① CI 与冒烟绝不能产生
        # 真实的产品事件（那会把「有多少人在用」直接污染掉）；② 冒烟不该
        # 依赖 telemetry.tavotto.com 可达。它与上面那条是两个独立开关，
        # 别指望关了检查更新就顺带关掉这条。
        "TAVOTTO_NO_TELEMETRY": "1",
        # 让 /api/shutdown 可用：冒烟要验证的是**干净退出**，不是硬停
        "TAVOTTO_ALLOW_SHUTDOWN": "1",
    }
    # 冒烟验证的就是「认证默认开着」，外面误设的开发旁路不许泄进来
    env.pop("TAVOTTO_INSECURE_NO_AUTH", None)
    for key in ("APPDATA", "LOCALAPPDATA", "HOME", "USERPROFILE"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)

    if expect_source == "bundled":
        # 要验的是「一台干净电脑上装完即可用」，那台电脑上不会有这些东西。
        # 残留任何一个都会让内置 runtime 根本轮不上（`TAVOTTO_WORKER_PYTHON` 直接
        # 抢第一，Conda/venv 让 CI 的 harness 环境泄进来，PYTHONHOME 更狠——
        # 它会让内置解释器指向别的前缀，连启动都启动不了）。
        # 与其看着断言失败，不如在这里就摘干净并逐条说清楚。
        for key in _HOSTILE_TO_BUNDLED:
            if env.pop(key, None):
                print(f"! 已从子进程环境移除 {key}"
                      "（--expect-source=bundled 要求不借助任何外部解释器）")
        # 配置目录本来就是隔离的新目录，所以「用户在设置里指定的解释器」
        # 天然为空——这里不需要额外处理，但值得说明白，免得下次有人再加一条。

    cmd = [*launch, "--port", str(port), "--no-browser", "--figures", str(figures)]
    print(f"$ {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    log_path = data_dir / "cache" / "app.log"
    # 记几个墙钟数只是为了让排障时有个量级参照（「是不是比上次慢了一个数量级」），
    # **不是性能承诺**：CI runner 的负载天天不一样，真正的基线在
    # docs/perf-baseline.md 里由 scripts/bench_render.py 产出。
    timings: dict[str, float] = {}
    spawned_at = time.time()
    try:
        version = _wait_ready(base, proc, BOOT_TIMEOUT_S)
        timings["app_ready_s"] = time.time() - spawned_at
        print(f"✓ 已启动: version={version.get('version')} "
              f"build={version.get('build')}（{timings['app_ready_s']:.1f}s）")

        _assert_auth_enforced(base, data_dir, port)

        _check_environment(base, expect_source, expect_packages or [],
                           expect_runtime)

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
            t0 = time.time()
            res = _post(f"{base}/api/engine/render",
                        {"id": target["id"], "patches": []},
                        timeout=RENDER_TIMEOUT_S)
            timings["first_render_s"] = time.time() - t0
            if not res.get("manifest"):
                raise SmokeError(f"渲染没回 manifest: {res}")
            print(f"✓ 引擎渲染 {target['id']}: "
                  f"{len(res['manifest'].get('elements', []))} 个元素"
                  f"（{timings['first_render_s']:.1f}s，含冷启动）")

            # 第二次渲染走热会话：验的是「第一次没把会话搞坏」。冷/热两个数
            # 差得很远（冷的要起解释器 + import 整个科学栈），混在一起看不出
            # 任何东西，所以分开记。
            t0 = time.time()
            res2 = _post(f"{base}/api/engine/render",
                         {"id": target["id"], "patches": []},
                         timeout=RENDER_TIMEOUT_S)
            timings["second_render_s"] = time.time() - t0
            if not res2.get("manifest"):
                raise SmokeError(f"第二次渲染没回 manifest: {res2}")
            print(f"✓ 再渲染一次（热会话）"
                  f"（{timings['second_render_s']:.2f}s）")
            _check_control_plane(base, expect_control_plane)
        else:
            print("! 没有可参数化面板，跳过引擎渲染（注册表为空？）")
            if expect_control_plane:
                raise SmokeError(
                    "要求断言控制面，但这个示例项目里没有可参数化面板——"
                    "没渲染就无从判断走的是哪条控制面")

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
        t0 = time.time()
        out = _post(f"{base}/api/export", spec, timeout=RENDER_TIMEOUT_S)
        timings["export_s"] = time.time() - t0
        first = Path(out["export_dir"]) / out["files"][0]["name"]
        if not first.is_file() or first.stat().st_size < 500:
            raise SmokeError(f"导出的 PDF 不对劲: {first}")
        print(f"✓ 导出 {first.name}（{first.stat().st_size} 字节，"
              f"{timings['export_s']:.1f}s）")

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
        if timings:
            print("· 墙钟参考（**不是性能承诺**，基线见 docs/perf-baseline.md）: "
                  + "  ".join(f"{k}={v:.2f}s" for k, v in timings.items()))
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
        leftover = _leftover_workers(data_dir)
        if leftover:
            raise SmokeError(f"退出后仍有 worker 子进程残留: {leftover}")
        if not graceful:
            raise SmokeError("受控退出失败，只能硬停——关窗口时很可能也是这样")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--exe", help="打好的可执行文件（PyInstaller 产物）")
    g.add_argument("--python", help="解释器路径，用 `-m tavotto` 启动")
    ap.add_argument("--figures", default=str(DEFAULT_FIGURES))
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--keep", action="store_true", help="保留临时工作目录便于排查")
    ap.add_argument("--expect-source", default=None,
                    help="断言渲染解释器的来源（bundled / configured / "
                         "managed_venv / system / current_process / env_override）")
    ap.add_argument("--expect-packages", default="",
                    help="逗号分隔的 import 名，断言在渲染环境里都能 import "
                         "（如 numpy,pandas,scipy,seaborn,PIL,matplotlib）")
    ap.add_argument("--expect-control-plane", default=None,
                    choices=["workerd", "python"],
                    help="断言渲染控制面（workerd = Rust supervisor）。"
                         "桌面产物用 workerd：回退是静默的，不断言就发现不了")
    ap.add_argument("--expect-runtime", action="store_true",
                    help="断言 runtime.expected 与 runtime.valid 均为真"
                         "（该带内置 runtime 的形态，且带的这份完整可用）")
    ap.add_argument("--workdir", default=None,
                    help="隔离用户目录的落点（默认系统临时目录）。"
                         "指到中文/带空格的路径可覆盖那一档回归")
    args = ap.parse_args(argv)

    launch = [args.exe] if args.exe else [args.python, "-m", "tavotto"]
    if args.exe and not Path(args.exe).is_file():
        print(f"找不到可执行文件: {args.exe}", file=sys.stderr)
        return 2

    figures = Path(args.figures).resolve()
    if not figures.is_dir():
        print(f"示例项目目录不存在: {figures}", file=sys.stderr)
        return 2

    packages = [p.strip() for p in args.expect_packages.split(",") if p.strip()]
    if args.workdir:
        # 调用方指定的路径可能带中文/空格——那正是要覆盖的一档，别去规避它
        base_dir = Path(args.workdir)
        base_dir.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix="tavotto-smoke-", dir=str(base_dir)))
    else:
        workdir = Path(tempfile.mkdtemp(prefix="tavotto-smoke-"))
    print(f"· 隔离用户目录: {workdir}")
    try:
        run_smoke(launch, figures, workdir, args.port,
                  args.expect_source, packages, args.expect_control_plane,
                  args.expect_runtime)
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
