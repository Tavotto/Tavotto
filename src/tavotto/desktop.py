"""桌面 sidecar 模式：Tauri 壳的受控后端（`tavotto --desktop-sidecar`）。

与浏览器模式（`localserver.serve_browser` + `webbrowser.open`）的差异全部收在这个模块里：

- 只绑 127.0.0.1。端口优先用壳经 stdin 首行建议的 `preferred_port`（壳记在
  `app_config_dir()/desktop-port`，ADR 0108）：窗口的 origin 跨重启不变，前端
  localStorage 里的崩溃兜底副本与偏好才活得下来（issue #715）。直接 bind，占不到就在
  `PREFERRED_PORT_RETRY_SECONDS` 内重试（应用内更新重启时旧 sidecar 要等 stdin EOF 才退），
  再占不到退回端口 0（操作系统分配）——都是先占后读，没有「先查再绑」竞态。
- 用 werkzeug 的线程 server（经 `localserver.LocalWSGIServer`：bind 与 listen
  之间不反查主机名）：拿得到真实端口，支持从别的线程优雅 `shutdown()`。
- 一次性启动 nonce → 短生命周期 HttpOnly 会话 cookie 的桌面认证：
  nonce 优先经 **stdin 首行** 传入（环境变量对同用户进程可见——macOS 上
  `ps eww` 就能看到别的进程的 env，管道不行），`TAVOTTO_DESKTOP_NONCE`
  只作为调试用的回退，读到后立即从 `os.environ` 摘除，绝不落盘、绝不进日志。
- 握手文件（`TAVOTTO_DESKTOP_HANDSHAKE` 指定路径，tmp+replace 原子写）：
  ready / port / pid / error，**不含任何认证材料**；退出时清理。
- 父进程监视：stdin EOF（首选，跨平台）+ 父 PID 轮询（兜底）。Tauri 异常
  退出时 sidecar 也必须跟着退，不能留孤儿。
- 桌面模式下 Python updater 完全停用（升级归 Tauri 层），浏览器/CLI 模式不变。

认证模型（一次性 bootstrap；实现已泛化到 tavotto/security.py，浏览器模式
共用同一道边界，见 ADR 0008）：

    Tauri 生成 nonce ──stdin──▶ sidecar 持有
    Tauri 让首个页面带 URL fragment（fragment 不进 HTTP 日志）
    页面 POST /api/session/bootstrap {nonce} ──▶ 验证后当场作废 nonce，
        Set-Cookie: HttpOnly + SameSite=Strict 的会话 cookie
    此后 /api、/exports、渲染图片、SSE 一律凭 cookie；Host/Origin 同时校验。

桌面与浏览器模式的差别只剩参数：桌面的 cookie 是会话级（窗口即进程）、
没有磁盘上的本机凭据文件（nonce 走 stdin，实例复用由壳的单实例转发负责）。
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

from . import localserver, security
from .engine import ai_bridge as engine_ai, pool as engine_pool, project_watch as engine_watch

LOG = logging.getLogger("tavotto.desktop")

# 兼容再导出：smoke_desktop / 测试用这些名字
COOKIE_NAME = security.COOKIE_NAME
BOOTSTRAP_PATH = security.LEGACY_BOOTSTRAP_PATH
DesktopState = security.SessionState

#: stdin 首行 JSON 里壳建议的端口字段与合法范围——与生产方 `src-tauri/src/sidecar/port_memory.rs`
#: 严格同源：两侧各读 `tests/golden/desktop_preferred_port.json`，不读对方源码。
PREFERRED_PORT_FIELD = "preferred_port"
PREFERRED_PORT_MIN = 1024
PREFERRED_PORT_MAX = 65535
#: 建议端口占不到时重试多久。应用内更新装完走 `process::exit` 重启：旧壳没走 `RunEvent::Exit`，
#: 旧 sidecar 要等 stdin EOF 才开始关，端口可能还没放出来。等太久用户看到的是 splash 多停几秒，
#: 所以只给两三秒，再占不到就本次换端口（壳那边记一次落空，ADR 0108）。
PREFERRED_PORT_RETRY_SECONDS = 2.5
PREFERRED_PORT_RETRY_INTERVAL = 0.1


# ---------------------------------------------------------------------------
# 握手文件
# ---------------------------------------------------------------------------
def write_handshake(
    path: Path | None, *, ready: bool, port: int | None = None, error: str | None = None
) -> None:
    """原子写握手数据（tmp + replace）。只有状态，绝无认证材料。"""
    if path is None:
        return
    payload: dict = {"ready": ready, "pid": os.getpid()}
    if port is not None:
        payload["port"] = port
    if error:
        payload["error"] = error
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        LOG.error("握手文件写入失败 %s: %s", path, exc)


# ---------------------------------------------------------------------------
# 启动凭据（nonce）与父进程监视
# ---------------------------------------------------------------------------
def parse_preferred_port(value: object) -> int | None:
    """stdin 里的建议端口 → 端口；不是 `[PREFERRED_PORT_MIN, PREFERRED_PORT_MAX]` 里的整数一律 None。

    非法值只是被忽略、退回系统分配：建议端口是「尽量稳定」的优化，不是启动的前提。
    （JSON 的 `true` / `false` 在 Python 里是 int 1 / 0，落在范围下限之外，同样被忽略。）
    """
    if not isinstance(value, int):
        return None
    return value if PREFERRED_PORT_MIN <= value <= PREFERRED_PORT_MAX else None


def read_launch_credentials(
    stdin=None, environ=None
) -> tuple[str | None, int | None, int | None, object]:
    """取启动 nonce、父 PID 与建议端口。

    返回 (nonce, parent_pid, preferred_port, stdin_stream)：stdin_stream 是已经读掉首行、
    留给父进程监视继续 read 的流（可能为 None）。建议端口只从 stdin 首行来
    （`PREFERRED_PORT_FIELD`），缺席或非法都是 None。

    优先级：环境变量（读到即从 environ 摘除，调试用）→ stdin 首行 JSON。
    stdin 是 tty（用户在终端里手敲 --desktop-sidecar）时不读——那会无限等
    键盘输入；这种场景让上层报「缺启动凭据」退出。
    """
    env = environ if environ is not None else os.environ
    stream = stdin if stdin is not None else sys.stdin
    nonce = env.pop("TAVOTTO_DESKTOP_NONCE", None)
    parent_raw = env.pop("TAVOTTO_DESKTOP_PARENT_PID", None)
    parent_pid = int(parent_raw) if parent_raw and parent_raw.isdigit() else None
    preferred_port = None

    if nonce is None and stream is not None:
        try:
            if not stream.isatty():
                line = stream.readline()
                msg = json.loads(line) if line.strip() else {}
                if isinstance(msg, dict):
                    n = msg.get("nonce")
                    if isinstance(n, str) and n:
                        nonce = n
                    pp = msg.get("parent_pid")
                    if parent_pid is None and isinstance(pp, int):
                        parent_pid = pp
                    preferred_port = parse_preferred_port(msg.get(PREFERRED_PORT_FIELD))
        except (OSError, ValueError):
            pass
    return nonce, parent_pid, preferred_port, stream


def watch_stdin_eof(stream, on_gone) -> None:
    """stdin 读到 EOF = 父进程（Tauri）没了 → 回调。跨平台、无轮询。"""

    def run():
        try:
            buf = getattr(stream, "buffer", stream)
            while True:
                chunk = buf.read(4096)
                if not chunk:
                    break
        except (OSError, ValueError):
            pass
        LOG.info("stdin EOF：父进程已退出，sidecar 跟随关闭")
        on_gone()

    threading.Thread(target=run, daemon=True, name="mm-parent-stdin").start()


def watch_parent_pid(parent_pid: int, on_gone, interval: float = 1.0) -> None:
    """按 PID 轮询父进程存活（stdin 不可用时的兜底）。

    POSIX：sidecar 是 Tauri 的直接子进程，父亡则被 reparent——getppid 变化
    即为信号。Windows：拿 SYNCHRONIZE 句柄 WaitForSingleObject。
    """

    def run_posix():
        while True:
            if os.getppid() != parent_pid:
                break
            time.sleep(interval)
        LOG.info("父进程 %d 已退出（getppid 变化），sidecar 跟随关闭", parent_pid)
        on_gone()

    def run_windows():
        import ctypes

        SYNCHRONIZE = 0x00100000
        h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
        if not h:
            return  # 父进程已经没了或拿不到句柄：交给 stdin EOF 那条路
        ctypes.windll.kernel32.WaitForSingleObject(h, 0xFFFFFFFF)
        ctypes.windll.kernel32.CloseHandle(h)
        LOG.info("父进程 %d 已退出（句柄发信号），sidecar 跟随关闭", parent_pid)
        on_gone()

    target = run_windows if os.name == "nt" else run_posix
    threading.Thread(target=target, daemon=True, name="mm-parent-pid").start()


# ---------------------------------------------------------------------------
# 受控 WSGI server
# ---------------------------------------------------------------------------
def claim_listener(
    preferred_port: int | None,
    *,
    retry_seconds: float = PREFERRED_PORT_RETRY_SECONDS,
    interval: float = PREFERRED_PORT_RETRY_INTERVAL,
) -> socket.socket:
    """占下 sidecar 的监听 socket：先建议端口（限时重试），再退回端口 0。

    占法与浏览器模式同一份（`localserver.claim`：bind + listen，选项走
    `apply_bind_options(exclusive=True)`——POSIX 上 `SO_REUSEADDR` 让上一个进程留下的 TIME_WAIT
    挡不住同端口重启；Windows 上 `SO_EXCLUSIVEADDRUSE`，别的程序即使带 `SO_REUSEADDR` 也抢不走）。
    不用「先探后绑」：探到空闲与真 bind 之间谁都可能插进来。
    """
    host = "127.0.0.1"
    if preferred_port is not None:
        deadline = time.monotonic() + retry_seconds
        while True:
            try:
                return localserver.claim(host, preferred_port)
            except OSError as exc:
                if time.monotonic() >= deadline:
                    LOG.warning(
                        "建议端口 %d 在 %.1f s 内都占不到（%s），本次改用系统分配的端口",
                        preferred_port,
                        retry_seconds,
                        exc,
                    )
                    break
            time.sleep(interval)
    return localserver.claim(host, 0)


class SidecarServer:
    """绑定 127.0.0.1（建议端口，退回端口 0）的受控 server；shutdown 幂等且线程安全。"""

    def __init__(
        self,
        flask_app,
        state: DesktopState,
        handshake: Path | None = None,
        *,
        preferred_port: int | None = None,
        retry_seconds: float = PREFERRED_PORT_RETRY_SECONDS,
    ) -> None:
        self._app = flask_app
        self._handshake = handshake
        # 线程 server（SSE 长连接 + 渲染请求并存；daemon_threads=True，shutdown
        # 后残余长连接不阻塞进程退出），且 bind → listen 之间不反查主机名——
        # 否则反向 DNS 无回音的机器上握手文件要等 30–60 s 才写得出来（localserver.py）。
        # 监听 socket 由 `claim_listener` 先占好，werkzeug 经 `fd=` 接管（与浏览器模式
        # `serve_browser` 同一条路）：werkzeug 自己 bind 失败是 `sys.exit(1)`，接不住，也就没法重试 / 回退。
        listener = claim_listener(preferred_port, retry_seconds=retry_seconds)
        try:
            self._srv = localserver.LocalWSGIServer(
                "127.0.0.1", listener.getsockname()[1], flask_app, fd=listener.fileno()
            )
        finally:
            listener.close()  # werkzeug 从 fd dup 了一份，原件关掉
        # fd 路径不走 server_bind：把 HTTPServer 会填的两个属性照 `server_bind` 那样填上
        self._srv.server_name = "127.0.0.1"
        self._srv.server_port = self._srv.port
        # Host / Origin 校验钉的是这里——**实际**拿到的端口，不是建议的那个
        state.port = self._srv.server_port
        flask_app.config["TAVOTTO_DESKTOP_MODE"] = True
        flask_app.config[security.STATE_KEY] = state
        self._shutting_down = threading.Event()
        self._stopped = threading.Event()

    @property
    def port(self) -> int:
        return self._srv.server_port

    def announce_ready(self) -> None:
        write_handshake(self._handshake, ready=True, port=self.port)

    def serve_forever(self) -> None:
        try:
            self._srv.serve_forever()
        finally:
            self._cleanup()
            self._stopped.set()

    def shutdown(self) -> None:
        """从任意线程（含信号处理器）安全调用；重复调用无副作用。

        socketserver 的 shutdown() 要等 serve_forever 的循环退出——若从
        serve_forever 所在线程（信号处理器就是）直接调会自锁死，所以一律
        丢到独立线程执行。
        """
        if self._shutting_down.is_set():
            return
        self._shutting_down.set()
        threading.Thread(target=self._srv.shutdown, daemon=True, name="mm-sidecar-shutdown").start()

    def wait_stopped(self, timeout: float | None = None) -> bool:
        return self._stopped.wait(timeout)

    def _cleanup(self) -> None:
        """serve 循环退出后：停 watcher → 同步关 worker → 中断 AI → 清握手。

        监听 socket 此时已经关了（werkzeug 的 `serve_forever` 在 finally 里 `server_close()`）：
        同步关 worker 最多要等好几秒，端口不必陪着等——紧接着启动的下一个 sidecar（应用内更新
        后的重启）才拿得到建议端口（`test_a_stopped_sidecar_releases_its_port_at_once` 看护）。
        """
        try:
            engine_watch.stop()  # None = 停掉全部项目的 watcher
            engine_pool.shutdown_all(wait=True)  # 同步等 worker 真的退了再走
            engine_ai.interrupt_all()
        except Exception:  # noqa: BLE001 — 清理路径绝不能把退出堵死
            LOG.exception("sidecar 清理异常（继续退出）")
        if self._handshake is not None:
            try:
                self._handshake.unlink(missing_ok=True)
            except OSError:
                pass
        self._app.config.pop(security.STATE_KEY, None)
        self._app.config.pop("TAVOTTO_DESKTOP_MODE", None)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def run(flask_app) -> int:
    """`tavotto --desktop-sidecar` 的主体。项目打开逻辑仍在 app.main（与浏览器
    模式同一套），这里只负责认证、server 生命周期与父进程跟随。"""
    nonce, parent_pid, preferred_port, stdin_stream = read_launch_credentials()
    handshake_raw = os.environ.pop("TAVOTTO_DESKTOP_HANDSHAKE", None)
    handshake = Path(handshake_raw) if handshake_raw else None

    if not nonce:
        # 没有凭据坚决不起「无认证的桌面模式」——宁可失败清楚，
        # 也不给任何本地页面一个不设防的全功能后端。
        msg = (
            "desktop sidecar 需要启动凭据：由 Tavotto 桌面应用启动，"
            "或调试时设置 TAVOTTO_DESKTOP_NONCE"
        )
        LOG.error("desktop sidecar 缺启动凭据，拒绝以无认证方式启动")
        write_handshake(handshake, ready=False, error=msg)
        return 2

    state = DesktopState(nonce)
    try:
        srv = SidecarServer(flask_app, state, handshake, preferred_port=preferred_port)
    except OSError as exc:
        write_handshake(handshake, ready=False, error=f"无法绑定 127.0.0.1 端口: {exc}")
        LOG.error("sidecar 绑定失败: %s", exc)
        return 1

    if stdin_stream is not None and not stdin_stream.isatty():
        watch_stdin_eof(stdin_stream, srv.shutdown)
    if parent_pid is not None:
        watch_parent_pid(parent_pid, srv.shutdown)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: srv.shutdown())
        except (ValueError, OSError):  # 非主线程 / 平台不支持
            pass

    srv.announce_ready()
    LOG.info(
        "desktop sidecar 就绪: 127.0.0.1:%d (pid %d，建议端口 %s)",
        srv.port,
        os.getpid(),
        preferred_port,
    )
    srv.serve_forever()
    LOG.info("desktop sidecar 已退出")
    return 0
