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

认证模型（一次性 bootstrap）：

    Tauri 生成 nonce ──stdin──▶ sidecar 持有
    Tauri 让首个页面带 URL fragment（fragment 不进 HTTP 日志）
    页面 POST /api/desktop/bootstrap {nonce} ──▶ 验证后当场作废 nonce，
        Set-Cookie: HttpOnly + SameSite=Strict 的会话 cookie
    此后 /api、/exports、渲染图片、SSE 一律凭 cookie；Host/Origin 同时校验。

浏览器模式下这些钩子全部旁路（`TAVOTTO_DESKTOP_STATE` 未设置即直接放行），
bootstrap 端点回 404——普通 `tavotto` CLI 的行为一个字节都不变。
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import signal
import sys
import threading
import time
from pathlib import Path

from flask import jsonify, request
from werkzeug.serving import make_server

from .engine import ai_bridge as engine_ai
from .engine import pool as engine_pool

LOG = logging.getLogger("tavotto.desktop")

COOKIE_NAME = "tavotto_desktop"
BOOTSTRAP_PATH = "/api/desktop/bootstrap"

# 无会话即可访问的路径：首屏 HTML 与静态构建产物（不含任何用户数据），
# 以及 bootstrap 本身。其余（/api、/exports、/api/render 图片、SSE）一律凭 cookie。
_PUBLIC_PATHS = {"/", "/favicon.ico", BOOTSTRAP_PATH}
_PUBLIC_PREFIXES = ("/assets/",)


class DesktopState:
    """一次性 nonce 与进程内会话 token。全部只存内存，进程退出即失效。"""

    def __init__(self, nonce: str) -> None:
        self._nonce: str | None = nonce
        self._token: str | None = None
        self._lock = threading.Lock()
        self.port: int | None = None

    def redeem(self, submitted: str) -> str | None:
        """核对并作废 nonce，签发会话 token；错误的猜测不作废（否则任何本地
        进程都能抢在真页面前面用错误 nonce 把应用 DoS 掉；256 位随机值也
        没有可行的爆破空间）。"""
        with self._lock:
            if not self._nonce or not submitted:
                return None
            if not hmac.compare_digest(self._nonce, submitted):
                return None
            self._nonce = None
            self._token = secrets.token_urlsafe(32)
            return self._token

    def valid_cookie(self, token: str | None) -> bool:
        with self._lock:
            return bool(self._token) and bool(token) \
                and hmac.compare_digest(self._token, token)


def install(flask_app) -> None:
    """在 app 创建期挂上桌面认证钩子与 bootstrap 端点。

    必须在处理第一个请求前调用（app.py import 期）；浏览器模式下
    `TAVOTTO_DESKTOP_STATE` 不存在，钩子直接放行、端点 404，行为零变化。
    """

    @flask_app.post(BOOTSTRAP_PATH)
    def _desktop_bootstrap():
        state: DesktopState | None = flask_app.config.get("TAVOTTO_DESKTOP_STATE")
        if state is None:
            return jsonify({"error": "非桌面模式"}), 404
        body = request.get_json(force=True, silent=True) or {}
        token = state.redeem(str(body.get("nonce") or ""))
        if token is None:
            return jsonify({"error": "启动凭据无效或已使用",
                            "code": "bad_nonce"}), 403
        resp = jsonify({"ok": True})
        # 会话 cookie（无 Max-Age）：窗口关闭即消失；token 反正只在本进程内有效
        resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="Strict",
                        secure=False, path="/")
        return resp

    @flask_app.before_request
    def _desktop_guard():
        state: DesktopState | None = flask_app.config.get("TAVOTTO_DESKTOP_STATE")
        if state is None:
            return None  # 浏览器 / CLI 模式：不做任何桌面校验
        # Host / Origin：sidecar 只被 127.0.0.1:<port> 上的本应用访问。
        # DNS rebinding、localhost 花式写法、别的本地页面发起的跨源请求全拒。
        if state.port is not None:
            if request.headers.get("Host", "") != f"127.0.0.1:{state.port}":
                return jsonify({"error": "拒绝的 Host", "code": "bad_host"}), 403
            origin = request.headers.get("Origin")
            if origin and origin != f"http://127.0.0.1:{state.port}":
                return jsonify({"error": "拒绝的来源", "code": "bad_origin"}), 403
        p = request.path
        if p in _PUBLIC_PATHS or p.startswith(_PUBLIC_PREFIXES):
            return None
        if state.valid_cookie(request.cookies.get(COOKIE_NAME)):
            return None
        return jsonify({"error": "桌面会话未建立或已失效",
                        "code": "desktop_auth_required"}), 401


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
        flask_app.config["TAVOTTO_DESKTOP_STATE"] = state
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
        self._app.config.pop("TAVOTTO_DESKTOP_STATE", None)
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
