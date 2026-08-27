"""桌面 sidecar 模式：Tauri 壳的受控后端（`tavotto --desktop-sidecar`）。

与浏览器模式（`app.run` + `webbrowser.open`）的差异全部收在这个模块里：

- 只绑 127.0.0.1、端口 0（操作系统分配——先绑定后读端口，没有「先查再绑」竞态）。
- 用 werkzeug 的 `make_server`（Flask 自带依赖，PyInstaller 反正要打它）：
  拿得到真实端口，支持从别的线程优雅 `shutdown()`。
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
import sys
import threading
import time
from pathlib import Path

from werkzeug.serving import make_server

from . import security
from .engine import ai_bridge as engine_ai, pool as engine_pool

LOG = logging.getLogger("tavotto.desktop")

# 兼容再导出：smoke_desktop / 测试用这些名字
COOKIE_NAME = security.COOKIE_NAME
BOOTSTRAP_PATH = security.LEGACY_BOOTSTRAP_PATH
DesktopState = security.SessionState


# ---------------------------------------------------------------------------
# 握手文件
# ---------------------------------------------------------------------------
def write_handshake(path: Path | None, *, ready: bool, port: int | None = None,
                    error: str | None = None) -> None:
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
def read_launch_credentials(stdin=None, environ=None) -> tuple[str | None, int | None, object]:
    """取启动 nonce 与父 PID。

    返回 (nonce, parent_pid, stdin_stream)：stdin_stream 是已经读掉首行、
    留给父进程监视继续 read 的流（可能为 None）。

    优先级：环境变量（读到即从 environ 摘除，调试用）→ stdin 首行 JSON。
    stdin 是 tty（用户在终端里手敲 --desktop-sidecar）时不读——那会无限等
    键盘输入；这种场景让上层报「缺启动凭据」退出。
    """
    env = environ if environ is not None else os.environ
    stream = stdin if stdin is not None else sys.stdin
    nonce = env.pop("TAVOTTO_DESKTOP_NONCE", None)
    parent_raw = env.pop("TAVOTTO_DESKTOP_PARENT_PID", None)
    parent_pid = int(parent_raw) if parent_raw and parent_raw.isdigit() else None

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
        except (OSError, ValueError):
            pass
    return nonce, parent_pid, stream


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
class SidecarServer:
    """绑定 127.0.0.1:0 的受控 server；shutdown 幂等且线程安全。"""

    def __init__(self, flask_app, state: DesktopState,
                 handshake: Path | None = None) -> None:
        self._app = flask_app
        self._handshake = handshake
        # threaded=True：SSE 长连接 + 渲染请求并存；werkzeug 的线程 server
        # daemon_threads=True，shutdown 后残余长连接不阻塞进程退出
        self._srv = make_server("127.0.0.1", 0, flask_app, threaded=True)
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
        threading.Thread(target=self._srv.shutdown, daemon=True,
                         name="mm-sidecar-shutdown").start()

    def wait_stopped(self, timeout: float | None = None) -> bool:
        return self._stopped.wait(timeout)

    def _cleanup(self) -> None:
        """serve 循环退出后：停 watcher → 同步关 worker → 中断 AI → 清握手。"""
        try:
            engine_pool.stop_watcher()          # None = 停掉全部项目的 watcher
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
    nonce, parent_pid, stdin_stream = read_launch_credentials()
    handshake_raw = os.environ.pop("TAVOTTO_DESKTOP_HANDSHAKE", None)
    handshake = Path(handshake_raw) if handshake_raw else None

    if not nonce:
        # 没有凭据坚决不起「无认证的桌面模式」——宁可失败清楚，
        # 也不给任何本地页面一个不设防的全功能后端。
        msg = ("desktop sidecar 需要启动凭据：由 Tavotto 桌面应用启动，"
               "或调试时设置 TAVOTTO_DESKTOP_NONCE")
        LOG.error(msg)
        write_handshake(handshake, ready=False, error=msg)
        return 2

    state = DesktopState(nonce)
    try:
        srv = SidecarServer(flask_app, state, handshake)
    except OSError as exc:
        write_handshake(handshake, ready=False,
                        error=f"无法绑定 127.0.0.1 端口: {exc}")
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
    LOG.info("desktop sidecar 就绪: 127.0.0.1:%d (pid %d)", srv.port, os.getpid())
    srv.serve_forever()
    LOG.info("desktop sidecar 已退出")
    return 0
