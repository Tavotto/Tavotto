"""sidecar 侧的 **native 会话注册表**与单 reader 传输（ADR 0021 §5）。

```text
tavotto run CLI  ──relay──  这里（sidecar）  ──HTTP/SSE──  WebView UI
       │
   用户的 Python（Figure 住在那儿，永不离开）
```

## 为什么不进 safe 池

`pool` 的每一条语义对 native 都是错的：

| 池的语义 | 对 native 会话意味着 |
|---|---|
| 超出 `MAX_ALIVE` 按 LRU 淘汰 → `shutdown()` | **杀掉用户正在跑的脚本** |
| `unknown_session → reopen`（workerd） | 重跑用户的命令——那是一次具体 invocation，不可透明重建 |
| key = (figures_dir, script) | native 的身份还含 cwd / argv / env / 解释器 |
| 进程属主 = sidecar | native 的进程属主是 **CLI** |

所以另起一张表。**但对上层必须是 Worker-like 的**（`ensure_built` /
`override` / `export` / `render_png` / `preview_png` / `svg_path` /
`resume` / `detach` / `terminate` / `shutdown`），否则每个端点都要写第二套
native 分支——那正是"两个入口两个答案"的形状。

## 为什么只能有一个 reader

ADR 0020 的父进程侧是"每次请求开一个读线程 + `join(timeout)`"。spike 够用，
产品化不行：超时之后那个线程**还卡在 socket 上**，下一条请求再开一个，两个
reader 抢同一条流。此后没有任何东西能证明"这条响应是那条请求的"——而错配的
表现是 A 图的 manifest 落在 B 图上，前端按 gid 索引一切。

所以：**一个常驻 reader 线程**，按 `request_id` 配对，事件独立分发。
超时不是"再试一次"，是把这条会话标成 poisoned——继续用一条已经证明自己
不同步的通道，比当场失败坏得多。

reader 线程**不碰 Figure**：Figure 只在用户进程的主线程里动（ADR 0020 §7）。
这里的 reader 只更新状态机与队列。

## 只有 barrier 才能编辑

脚本在跑的时候，用户进程的主线程在执行用户代码，**没有人读 socket**。这时
发请求不会失败，只会"没有响应"，然后在几分钟后的下一个屏障被执行——用户
早就忘了他点过什么。所以状态不是 `barrier` 时**当场拒绝**
（`native_session_not_at_barrier`），不排队。

纯标准库 + `pool.build_envelope`（信封的唯一出处）。**不 import matplotlib。**
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import socket
import threading
import time
from pathlib import Path

from . import envlease, nativerelay, pool, runcodes
from .runcodes import RunError

#: 带外事件的键——**与 `bridge_runner.EVENT_KEY` 严格同源**。
EVENT_KEY = "bridge_event"

# ---------------------------------------------------------------------------
# 状态闭集（ADR 0021 §5.1）。**不用多个互相矛盾的 boolean**：
# `is_running` + `has_barrier` + `is_dead` 三个布尔有 8 种组合，其中 5 种没有
# 意义，而没有意义的那几种迟早会出现在某条分支上。
# ---------------------------------------------------------------------------
PENDING_CONFIRMATION = "pending_confirmation"
WAITING_FOR_CLI = "waiting_for_cli"
STARTING_PYTHON = "starting_python"
RUNNING_SCRIPT = "running_script"
WAITING_FOR_FIGURE = "waiting_for_figure"
BARRIER = "barrier"
CONTINUING = "continuing"
ENDED = "ended"
DETACHED = "detached"
FAILED = "failed"

STATES = (
    PENDING_CONFIRMATION,
    WAITING_FOR_CLI,
    STARTING_PYTHON,
    RUNNING_SCRIPT,
    WAITING_FOR_FIGURE,
    BARRIER,
    CONTINUING,
    ENDED,
    DETACHED,
    FAILED,
)
#: 终态：进了就不再出来（`detached` 也是——Tavotto 已经放手了）。
TERMINAL_STATES = frozenset({ENDED, DETACHED, FAILED})

#: 单条请求的上限（与 `pool.REQUEST_TIMEOUT` 同口径）。
REQUEST_TIMEOUT = pool.REQUEST_TIMEOUT
BUILD_TIMEOUT = pool.BUILD_TIMEOUT
EXPORT_TIMEOUT = pool.EXPORT_TIMEOUT

#: 每条会话留多少条事件（给 UI 重连后补看）。有界——一个跑了两小时、
#: `show()` 上千次的脚本不该把 sidecar 的内存吃光。
EVENT_LOG_MAX = 200


def interpreter_fingerprint(python: str) -> str:
    """解释器的稳定指纹（`realpath`）。

    **按 realpath 而不是按用户敲的路径**：`.venv/bin/python` 与
    `/opt/…/python3.13` 可能是同一个解释器，重新 attach 时该认出来是同一个。
    """
    return hashlib.sha256(os.path.realpath(str(python)).encode("utf-8")).hexdigest()[:16]


class NativeTransport:
    """一条已认证的控制连接：**一个**常驻 reader + 按 request_id 配对。"""

    def __init__(self, sock: socket.socket, *, on_event=None, on_close=None):
        self.sock = sock
        self._rfile = sock.makefile("rb")
        self._on_event = on_event
        self._on_close = on_close
        self._lock = threading.Lock()
        self._waiters: dict[str, dict] = {}
        self._reader: threading.Thread | None = None
        self._closed = False
        #: 非空 = 这条通道已经证明自己不可信，任何请求当场失败。
        self.poison: dict | None = None
        self.orphan_responses = 0

    # ---------------- 生命周期 ----------------
    def start(self) -> None:
        if self._reader is not None:
            return
        self._reader = threading.Thread(
            target=self._read_loop, daemon=True, name="tavotto-native-reader"
        )
        self._reader.start()

    def _read_loop(self) -> None:
        """**唯一**读这条 socket 的地方。"""
        try:
            while True:
                line = self._rfile.readline()
                if not line:
                    break
                text = line.strip()
                if not text:
                    continue
                try:
                    frame = json.loads(text.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    # 畸形帧 = framing 已经没有证明了。**不跳过、不猜**：
                    # 跳过一帧之后，下一条响应会被配到上一条请求上。
                    self._die({"code": runcodes.NATIVE_RELAY_FAILED, "detail": f"坏帧: {exc}"})
                    return
                if not isinstance(frame, dict):
                    self._die({"code": runcodes.NATIVE_RELAY_FAILED, "detail": "帧不是 JSON 对象"})
                    return
                self._dispatch(frame)
        except OSError as exc:
            self._die({"code": runcodes.NATIVE_SESSION_DISCONNECTED, "detail": str(exc)})
            return
        self._die(None)  # 干净的 EOF：对端正常收摊

    def _dispatch(self, frame: dict) -> None:
        # 握手帧与带外事件都不是"某条请求的响应"——它们没有 `request_id`，
        # 按响应去配对只会把它们当成孤儿丢掉（第一版就是这样：会话永远停在
        # `starting_python`，因为那条转发过来的 hello 谁都没接住）。
        if EVENT_KEY in frame or nativerelay.HELLO_KEY in frame:
            if self._on_event is not None:
                self._on_event(frame)
            return
        rid = frame.get("request_id")
        with self._lock:
            slot = self._waiters.pop(rid, None) if isinstance(rid, str) else None
        if slot is None:
            # 超时之后迟到的响应。**framing 仍然是对的**（单 reader 保证），
            # 所以丢掉它是安全的；但会话已经因为那次超时被 poison 了。
            self.orphan_responses += 1
            return
        slot["frame"] = frame
        slot["event"].set()

    def _die(self, reason: dict | None) -> None:
        """通道结束：**唤醒所有等待者**，绝不让谁永久挂着。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if reason is not None and self.poison is None:
                self.poison = reason
            waiters = list(self._waiters.values())
            self._waiters.clear()
        for slot in waiters:
            slot["frame"] = None
            slot["event"].set()
        if self._on_close is not None:
            self._on_close(reason)

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        with _suppress():
            self.sock.shutdown(socket.SHUT_RDWR)
        with _suppress():
            self.sock.close()
        self._die(None)

    # ---------------- 请求 ----------------
    def request(self, obj: dict, *, generation: int, revision: int, timeout: float) -> dict:
        """发一条 v1 请求并等它的响应。

        信封由 `pool.build_envelope()` 产出——**与 stdin/stdout 那条控制面是
        同一个函数**（ADR 0020 §6）。
        """
        if self.poison is not None:
            raise RunError(runcodes.NATIVE_RELAY_FAILED)
        if self._closed:
            raise RunError(runcodes.NATIVE_SESSION_DISCONNECTED)
        env = pool.build_envelope(obj, generation=generation, revision=revision)
        rid = env["request_id"]
        slot = {"event": threading.Event(), "frame": None}
        with self._lock:
            self._waiters[rid] = slot
        try:
            self.sock.sendall((json.dumps(env, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError as exc:
            with self._lock:
                self._waiters.pop(rid, None)
            raise RunError(runcodes.NATIVE_SESSION_DISCONNECTED) from exc
        if not slot["event"].wait(timeout):
            with self._lock:
                self._waiters.pop(rid, None)
                # **超时 = 这条会话不再可信。** 不"再试一次"：我们不知道
                # 那条请求有没有被执行，而下一条请求的响应可能是它的。
                if self.poison is None:
                    self.poison = {
                        "code": runcodes.NATIVE_RELAY_FAILED,
                        "detail": f"请求超时（{int(timeout)}s）",
                    }
            raise RunError(runcodes.NATIVE_RELAY_FAILED)
        frame = slot["frame"]
        if frame is None:
            raise RunError(runcodes.NATIVE_SESSION_DISCONNECTED)
        got, ver = frame.get("request_id"), frame.get("protocol_version")
        if got != rid or ver != pool.PROTOCOL_VERSION:
            with self._lock:
                self.poison = {
                    "code": runcodes.NATIVE_RELAY_FAILED,
                    "detail": f"回显对不上: request_id={got!r} protocol_version={ver!r}",
                }
            raise RunError(runcodes.NATIVE_RELAY_FAILED)
        if not frame.get("ok"):
            err = frame.get("error") or {}
            raise pool.WorkerError(
                err.get("message") or "native bridge 错误",
                traceback_text=err.get("traceback", ""),
                code=err.get("code", ""),
            )
        return frame

    def send_oneway(self, obj: dict, *, generation: int, revision: int) -> None:
        """发一条不等响应的请求（`shutdown` 那一条——对端会直接走掉）。"""
        env = pool.build_envelope(obj, generation=generation, revision=revision)
        with _suppress():
            self.sock.sendall((json.dumps(env, ensure_ascii=False) + "\n").encode("utf-8"))


class NativeSession:
    """一条 native 会话。**Worker-like**——上层不该知道自己拿的是哪一种。"""

    def __init__(self, *, session_id: str, descriptor: dict):
        meta = descriptor.get("metadata") or {}
        self.session_id = session_id
        self.native_id = descriptor.get("native_id", "")
        self.project_root = str(meta.get("project_root") or "")
        self.interpreter = str(meta.get("interpreter") or "")
        self.interpreter_fingerprint = interpreter_fingerprint(self.interpreter)
        self.target_kind = str(meta.get("target_kind") or "")
        self.target_display = str(meta.get("target_display") or "")
        self.cwd = str(meta.get("cwd") or "")
        self.arg_count = int(meta.get("arg_count") or 0)
        self.command_fingerprint = str(meta.get("command_fingerprint") or "")
        self.permission_key = str(meta.get("permission_key") or "")
        self.python_version = str(meta.get("python_version") or "")
        self.out_dir = Path(str(descriptor.get("out_dir") or ""))

        self.state = PENDING_CONFIRMATION
        self.barrier_reason = ""
        self.process_pid: int | None = None
        self.descriptors: list = []
        self.stems: list[str] = []
        self.script_error: dict | None = None
        self.terminal_error: dict | None = None
        self.exit_code: int | None = None
        self.figures_captured = 0
        self.started_at = time.time()
        self.last_event_at = self.started_at
        self.sequence = 0
        self.events: collections.deque = collections.deque(maxlen=EVENT_LOG_MAX)
        self.transport: NativeTransport | None = None
        self.generation = 1
        self.rev = 0
        self.last_build: dict | None = None
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._on_change = None
        #: 本会话占着的环境租约有没有被释放过（只释放一次）。
        self._lease_released = False

    # ------------------------------------------------------------------
    # 状态机
    # ------------------------------------------------------------------
    def _set_state(self, state: str, **fields) -> None:
        if state not in STATES:
            raise ValueError(f"未知的 native 会话状态: {state!r}")
        with self._changed:
            if self.state in TERMINAL_STATES and state not in TERMINAL_STATES:
                # 终态不回头。回头的具体形状是：脚本已经退出了，UI 却因为一条
                # 迟到的事件又显示成"正在运行"。
                return
            self.state = state
            self.sequence += 1
            self.last_event_at = time.time()
            for k, v in fields.items():
                setattr(self, k, v)
            entry = {"seq": self.sequence, "state": state, "at": self.last_event_at, **fields}
            self.events.append(entry)
            self._changed.notify_all()
        if self._on_change is not None:
            self._on_change(self, entry)

    def wait_for_state(self, states, timeout: float) -> str:
        """阻塞等状态落进 `states`（或任意终态）。**给 CLI 集成与用例用。**

        这不是"排队请求"：它等的是对端**自己**报上来的事件，一个字节都没发
        出去（ADR 0021 §9.3 禁的是把用户的操作攒着后放）。
        """
        want = set(states)
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                if self.state in want or self.state in TERMINAL_STATES:
                    return self.state
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self.state
                self._changed.wait(remaining)

    def _on_frame(self, frame: dict) -> None:
        """reader 线程里跑。**不碰 Figure，只更新状态机。**"""
        if nativerelay.HELLO_KEY in frame:
            # relay 转发过来的子进程握手：用户的 Python 起来了（ADR 0021 §3）。
            self.note_hello(frame)
            return
        name = frame.get(EVENT_KEY)
        if name == "barrier":
            stems = frame.get("stems") or []
            self._set_state(
                BARRIER,
                barrier_reason=str(frame.get("reason") or ""),
                stems=list(stems),
                script_error=frame.get("script_error") or None,
            )
        elif name == "released":
            self._set_state(RUNNING_SCRIPT, barrier_reason="")
        elif name == "exit":
            self._release_lease()
            self._set_state(
                ENDED,
                exit_code=_as_int(frame.get("code")),
                figures_captured=_as_int(frame.get("figures")) or 0,
            )
        elif name == "shutdown":
            self._release_lease()
            self._set_state(ENDED)

    def _on_transport_closed(self, reason: dict | None) -> None:
        self._release_lease()
        if self.state in TERMINAL_STATES:
            return
        if reason is None:
            self._set_state(ENDED)
        else:
            self._set_state(FAILED, terminal_error=reason)

    def note_hello(self, hello: dict) -> None:
        """收到 Bridge Runner 转发过来的握手帧——用户的 Python 起来了。"""
        self._set_state(WAITING_FOR_FIGURE, process_pid=_as_int(hello.get("pid")))

    def _release_lease(self) -> None:
        with self._lock:
            if self._lease_released or not self.interpreter:
                return
            self._lease_released = True
        envlease.release_native(self.interpreter, self.session_id)

    # ------------------------------------------------------------------
    # Worker-like 接口
    # ------------------------------------------------------------------
    def alive(self) -> bool:
        return self.state not in TERMINAL_STATES and self.transport is not None

    def _require_barrier(self) -> NativeTransport:
        if self.state in TERMINAL_STATES:
            raise RunError(runcodes.NATIVE_SESSION_ENDED)
        if self.transport is None:
            raise RunError(runcodes.NATIVE_SESSION_DISCONNECTED)
        if self.state != BARRIER:
            # **当场拒绝，不排队。** 排队的表现是用户几分钟前点的那次改动
            # 在他早就忘了的时候突然生效。
            raise RunError(runcodes.NATIVE_SESSION_NOT_AT_BARRIER)
        return self.transport

    def _request(self, obj: dict, timeout: float) -> dict:
        return self._require_barrier().request(
            obj, generation=self.generation, revision=self.rev, timeout=timeout
        )

    def ensure_built(self) -> dict:
        resp = self._request({"cmd": "build"}, BUILD_TIMEOUT)
        self.last_build = resp
        self.descriptors = list(resp.get("descriptors") or [])
        self.stems = list((resp.get("stems") or {}).keys())
        self.script_error = resp.get("script_error") or self.script_error
        return resp

    def override(self, stem: str, patches: list, **kw) -> dict:
        self.rev += 1
        return self._request(
            {"cmd": "override", "stem": stem, "patches": patches, **kw}, REQUEST_TIMEOUT
        )

    def export(self, stem: str, patches: list, path: str, fmt: str = "pdf", dpi: int = 600) -> dict:
        return self._request(
            {
                "cmd": "export",
                "stem": stem,
                "patches": patches,
                "path": path,
                "format": fmt,
                "dpi": dpi,
            },
            EXPORT_TIMEOUT,
        )

    def render_png(self, stem: str, width_px: int) -> Path:
        self._request({"cmd": "render_png", "stem": stem, "width": int(width_px)}, REQUEST_TIMEOUT)
        return self.out_dir / f"{stem}_w{int(width_px)}.png"

    def preview_png(self, stem: str, patches: list, width_px: int, tag: str) -> Path:
        self._request(
            {
                "cmd": "preview_png",
                "stem": stem,
                "patches": patches,
                "width": int(width_px),
                "tag": tag,
            },
            REQUEST_TIMEOUT,
        )
        return self.out_dir / f"{stem}__{tag}.png"

    def svg_path(self, stem: str) -> Path:
        return self.out_dir / f"{stem}.svg"

    def resume(self) -> dict:
        """放开屏障，让用户脚本接着往下跑。

        **runner 侧会先把 Figure 恢复成脚本原样**（ADR 0021 §8）——那一步在
        用户的进程里做，这里只是发一条 `continue`。
        """
        resp = self._request({"cmd": "continue"}, REQUEST_TIMEOUT)
        self._set_state(CONTINUING)
        return resp

    def detach(self) -> dict:
        """放手：脚本继续正常跑完，Tavotto 不再控制它。

        与 `resume` 的区别只在**之后**：连接关掉、会话进终态 `detached`。
        屏障的恢复语义两者相同（runner 侧的 `release_barrier`），所以脚本
        无论如何都看不到 Tavotto 的 override。
        """
        resp = self._request({"cmd": "continue"}, REQUEST_TIMEOUT)
        self._set_state(DETACHED)
        if self.transport is not None:
            self.transport.close()
        return resp

    def terminate(self) -> dict:
        """结束用户脚本——**明确的危险操作，不伪装成 continue**。

        只在屏障处可用。脚本正在跑的时候没有人读 socket，而这时真正该做的是
        用户在自己的终端里按 Ctrl+C：那个进程是他的，信号也是他的
        （ADR 0021 §10.2）。Tavotto 不从 GUI 里去杀一个别的进程的子进程。
        """
        transport = self._require_barrier()
        transport.send_oneway({"cmd": "terminate"}, generation=self.generation, revision=self.rev)
        self._set_state(ENDED, exit_code=runcodes.EXIT_TERMINATED, terminal_error=None)
        return {"terminated": True}

    def shutdown(self) -> None:
        """收摊（sidecar 退出 / 项目关闭）。**不杀用户的脚本。**

        只是把控制连接关掉。runner 那侧看到 EOF 会**先恢复 baseline 再**
        放开屏障（ADR 0021 §8.1），脚本照常跑完——这正是"关掉 App 默认
        detach and continue"的机制。
        """
        self._release_lease()
        # **先标状态再关连接**：关连接会让 reader 线程走到 `_on_transport_closed`，
        # 那条路把会话记成 `ended`（对端正常收摊）。反过来做的话，我们自己
        # 主动放手的这一次会被记成"脚本结束了"——而脚本其实还在跑，UI 于是
        # 显示"会话已结束 退出码 None"，用户以为他的脚本被杀了。
        if self.state not in TERMINAL_STATES:
            self._set_state(DETACHED)
        if self.transport is not None:
            self.transport.close()

    # 与 `EngineWorker` 同名：上层的 `_engine_attempt` 之类不必分支。
    force_kill = shutdown

    # ------------------------------------------------------------------
    def public_state(self) -> dict:
        """给 API / UI 的一份。**没有 token、没有端口、没有 argv 的值。**"""
        return {
            "session_id": self.session_id,
            "project_root": self.project_root,
            "interpreter": self.interpreter,
            "interpreter_fingerprint": self.interpreter_fingerprint,
            "target_kind": self.target_kind,
            "target_display": self.target_display,
            "cwd": self.cwd,
            "arg_count": self.arg_count,
            "python_version": self.python_version,
            "state": self.state,
            "barrier_reason": self.barrier_reason,
            "process_pid": self.process_pid,
            "stems": list(self.stems),
            "descriptors": list(self.descriptors),
            "script_error": self.script_error,
            "terminal_error": self.terminal_error,
            "exit_code": self.exit_code,
            "figures_captured": self.figures_captured,
            "started_at": self.started_at,
            "last_event_at": self.last_event_at,
            "sequence": self.sequence,
            "editable": self.state == BARRIER,
        }

    def diagnostics(self) -> dict:
        """诊断包里的那一份——**脱敏**（ADR 0021 §15）。

        没有路径原文、没有 argv 的值、没有 token、没有 Figure 里的文字。
        """
        return {
            "state": self.state,
            "execution_profile": "native",
            "python_version": self.python_version,
            "target_kind": self.target_kind,
            "arg_count": self.arg_count,
            "cwd_hash": hashlib.sha256(self.cwd.encode("utf-8")).hexdigest()[:12],
            "interpreter_fingerprint": self.interpreter_fingerprint,
            "barrier_reason": self.barrier_reason,
            "figures": len(self.stems),
            "exit_code": self.exit_code,
            "last_event_sequence": self.sequence,
            "terminal_error_code": (self.terminal_error or {}).get("code"),
            "transport_poisoned": bool(self.transport and self.transport.poison),
        }


class NativeSessionRegistry:
    """进程内的所有 native 会话 + logical asset → live route 的映射。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: dict[str, NativeSession] = {}
        #: (project_root, script, stem) → session_id。**live route，不是身份**
        #: （ADR 0021 §9）：会话结束即失效，绝不进文档。
        self._routes: dict[tuple, str] = {}
        self.on_change = None

    # ---------------- attach ----------------
    def attach(self, descriptor: dict, *, connect=None) -> NativeSession:
        """连上 CLI 的 relay 并认证；返回一条已经开始收事件的会话。

        `descriptor` 是 `nativehandoff.consume()` 出来的那一份（**含 token**）。
        token 从这里之后不再出现在任何结构里。
        """
        meta = descriptor.get("metadata") or {}
        interpreter = str(meta.get("interpreter") or "")
        session_id = f"native-{descriptor.get('native_id', '')}"
        with self._lock:
            if session_id in self._sessions and self._sessions[session_id].alive():
                raise RunError(runcodes.NATIVE_SESSION_CONFLICT)
        # **先拿环境租约再连**：装依赖正在改这个环境时不该开始跑用户脚本
        # （envlease 会抛 `environment_mutating`）。
        envlease.acquire_native(interpreter, session_id)
        try:
            relay = descriptor.get("relay") or {}
            sock = (connect or _connect)(
                str(relay.get("host") or "127.0.0.1"),
                int(relay.get("attach_port") or 0),
                str(descriptor.get("attach_token") or ""),
            )
        except BaseException:
            envlease.release_native(interpreter, session_id)
            raise
        session = NativeSession(session_id=session_id, descriptor=descriptor)
        session._on_change = self._changed  # noqa: SLF001 — 注册表持有会话
        transport = NativeTransport(
            sock,
            on_event=session._on_frame,
            on_close=session._on_transport_closed,  # noqa: SLF001
        )
        session.transport = transport
        session._set_state(STARTING_PYTHON)  # noqa: SLF001
        transport.start()
        with self._lock:
            self._sessions[session_id] = session
        return session

    def _changed(self, session: NativeSession, entry: dict) -> None:
        if session.state in TERMINAL_STATES:
            self._drop_routes(session.session_id)
        if self.on_change is not None:
            self.on_change(session, entry)

    # ---------------- 查询 ----------------
    def get(self, session_id: str) -> NativeSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise RunError(runcodes.NATIVE_SESSION_UNKNOWN)
        return session

    def list(self, project_root: str = "") -> list[NativeSession]:
        with self._lock:
            out = list(self._sessions.values())
        if project_root:
            root = os.path.realpath(project_root)
            out = [s for s in out if os.path.realpath(s.project_root) == root]
        return sorted(out, key=lambda s: s.started_at)

    # ---------------- logical asset → live route ----------------
    @staticmethod
    def route_key(project_root: str, script: str, stem: str) -> tuple:
        return (os.path.realpath(project_root), str(script), str(stem))

    def bind_assets(self, session: NativeSession) -> list[str]:
        """把这条会话 build 出来的每张图绑成 live route；返回**没绑上**的。

        冲突时**不静默覆盖**（ADR 0021 §9.2）：用户可能在两个终端跑同一个
        脚本，后来的那条把面板抢过去 = 他在界面上看到的图突然换成了另一次
        运行的，而界面什么都没说。第二条会话照常跑完自己的脚本，只是不占
        现有面板。
        """
        rejected: list[str] = []
        with self._lock:
            for desc in session.descriptors:
                if not isinstance(desc, dict):
                    continue
                key = self.route_key(
                    session.project_root, desc.get("script", ""), desc.get("stem", "")
                )
                holder = self._routes.get(key)
                if holder is not None and holder != session.session_id:
                    other = self._sessions.get(holder)
                    if other is not None and other.alive():
                        rejected.append(str(desc.get("stem", "")))
                        continue
                self._routes[key] = session.session_id
        return rejected

    def route_for(self, project_root: str, script: str, stem: str) -> NativeSession | None:
        """logical asset → **还活着的**那条会话；没有就 None（= offline）。

        `None` 的调用方**绝不许**退回 safe worker（ADR 0021 §9.1）：那会让
        用户看到另一个环境生成的图，而界面上什么都没说。
        """
        with self._lock:
            sid = self._routes.get(self.route_key(project_root, script, stem))
            session = self._sessions.get(sid) if sid else None
        if session is None or not session.alive():
            return None
        return session

    def _drop_routes(self, session_id: str) -> None:
        with self._lock:
            for key in [k for k, v in self._routes.items() if v == session_id]:
                self._routes.pop(key, None)

    # ---------------- 收尾 ----------------
    def forget(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
        self._drop_routes(session_id)

    def prune(self, *, keep: int = 20) -> int:
        """终态会话留最近 `keep` 条给 UI 看，多的丢掉。"""
        with self._lock:
            done = sorted(
                (s for s in self._sessions.values() if s.state in TERMINAL_STATES),
                key=lambda s: s.last_event_at,
            )
            victims = done[: max(0, len(done) - keep)]
            for s in victims:
                self._sessions.pop(s.session_id, None)
        for s in victims:
            self._drop_routes(s.session_id)
        return len(victims)

    def shutdown_all(self) -> int:
        """sidecar 退出：**只放手，不杀**（ADR 0021 §8.1 / §10.2）。"""
        with self._lock:
            sessions = list(self._sessions.values())
        for s in sessions:
            with _suppress_all():
                s.shutdown()
        return len(sessions)


def _connect(host: str, port: int, token: str, *, timeout: float = 15.0) -> socket.socket:
    """连上 relay 的 attach 侧并完成握手。"""
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout)
    except OSError as exc:
        raise RunError(runcodes.NATIVE_ATTACH_FAILED) from exc
    sock.settimeout(timeout)
    try:
        sock.sendall(
            json.dumps({nativerelay.ATTACH_KEY: 1, "token": token}).encode("utf-8") + b"\n"
        )
        line = _read_line(sock)
        resp = json.loads(line) if line else {}
    except (OSError, ValueError) as exc:
        with _suppress():
            sock.close()
        raise RunError(runcodes.NATIVE_ATTACH_FAILED) from exc
    if not isinstance(resp, dict) or not resp.get("ok"):
        with _suppress():
            sock.close()
        raise RunError(runcodes.NATIVE_AUTH_FAILED)
    sock.settimeout(None)
    return sock


def _read_line(sock: socket.socket, limit: int = 8192) -> str:
    """逐字节读一行——与 `nativerelay._read_hello` 同一个理由：**绝不多读**
    （此后这条 socket 交给 `NativeTransport` 的常驻 reader，多读的字节会丢）。"""
    buf = bytearray()
    while len(buf) < limit:
        b = sock.recv(1)
        if not b or b == b"\n":
            break
        buf += b
    return buf.decode("utf-8", "replace")


def _as_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


class _suppress:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, (OSError, ValueError))


class _suppress_all:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, Exception)


#: 进程内唯一的注册表（与 `pool._workers` 平级，但**是另一张表**）。
REGISTRY = NativeSessionRegistry()
