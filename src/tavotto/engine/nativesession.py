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

## 进程所有权的两类（P06，ADR 0125）

会话的进程归谁，决定「放手」与「结束」是不是同一件事：

* **terminal 拥有**（默认，`tavotto run`）：进程是 CLI 的子进程。`shutdown()` / `force_kill()` /
  `detach()` 一律只是**放手**，脚本照常跑完；Tavotto 不 kill 它（`cancel_owned()` 拒绝）。
* **desktop 拥有**（`desktopproc.start`）：进程是 sidecar 自己 spawn 的，`self.owned` 是它的
  所有者对象。**断开不是结束**：WebView / socket 断开、`shutdown()` 只关控制连接，租约一直
  持到所有者**确认进程（组 / Job）真的退出**才放；要结束它走 `cancel_owned()`。

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
import copy
import hashlib
import json
import os
import socket
import threading
import time
from pathlib import Path, PurePosixPath

from . import envlease, executionfact, figcapture, nativerelay, patchspec, pool, runcodes
from .runcodes import RunError

#: 带外事件的键——**与 `bridge_runner.EVENT_KEY` 严格同源**。
EVENT_KEY = "bridge_event"
#: 桌面拥有的进程里脚本发起一次提问 / 桌面把答复交回去——**与 `bridge_runner` 严格同源**（P07，ADR 0126）。
INPUT_REQUEST_EVENT = "input_request"
INPUT_REPLY_KEY = "input_reply"
#: 提问的闭集种类（未知种类按 `input` 处理，不信对端）；`getpass` 是口令，答复永不留存
INPUT_KINDS = ("input", "readline", "read", "getpass")
INPUT_PROMPT_LIMIT = 1000
#: 一次答复的字节上限（`read` 可能是整份数据；超限当场拒绝，脚本继续等）
INPUT_ANSWER_LIMIT = 4 * 1024 * 1024

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
#: 进入这些状态时，脚本不可能还停在一次提问上（到了屏障 / 放行 / 终态）。
_INPUT_CLEARING_STATES = frozenset({BARRIER, CONTINUING, ENDED, DETACHED, FAILED})

#: 单条请求的上限（与 `pool.REQUEST_TIMEOUT` 同口径）。
REQUEST_TIMEOUT = pool.REQUEST_TIMEOUT
BUILD_TIMEOUT = pool.BUILD_TIMEOUT
EXPORT_TIMEOUT = pool.EXPORT_TIMEOUT

#: 每条会话留多少条事件（给 UI 重连后补看）。有界——一个跑了两小时、
#: `show()` 上千次的脚本不该把 sidecar 的内存吃光。
EVENT_LOG_MAX = 200


def _canonical_execution_script(value) -> str:
    if not isinstance(value, str) or not value or len(value) > 16384 or "\0" in value:
        raise ValueError("invalid native execution script")
    path = PurePosixPath(figcapture.normalize_relative_script(value))
    if path.as_posix() != value or not path.parts or ".." in path.parts:
        raise ValueError("native execution script must be a canonical project path")
    return value


def interpreter_fingerprint(python: str) -> str:
    """解释器的稳定指纹（`realpath`）。

    **按 realpath 而不是按用户敲的路径**：`.venv/bin/python` 与
    `/opt/…/python3.13` 可能是同一个解释器，重新 attach 时该认出来是同一个。
    """
    return hashlib.sha256(os.path.realpath(str(python)).encode("utf-8")).hexdigest()[:16]


class _RequestOutcomeUnknown(RunError):
    """Local transport evidence: bytes may have reached the runner, no valid reply.

    This does not assert that user code resumed. It only prevents reusing a
    pre-release Figure receipt after a possibly dispatched continue command.
    The distinction stays local; error codes and wire payloads are unchanged.
    """


class NativeTransport:
    """一条已认证的控制连接：**一个**常驻 reader + 按 request_id 配对。"""

    def __init__(self, sock: socket.socket, *, on_event=None, on_close=None):
        self.sock = sock
        self._rfile = sock.makefile("rb")
        self._on_event = on_event
        self._on_close = on_close
        self._lock = threading.Lock()
        #: 发送串行化：请求 / 单向命令 / 输入答复各自从不同线程发，一行 JSON 不许被另一行劈开
        self._send_lock = threading.Lock()
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

    def join_reader(self, timeout: float) -> None:
        """等常驻 reader 处理完缓冲里的帧并退出（对端关闭后它会读到 EOF）；超时不抛。"""
        t = self._reader
        if t is not None and t is not threading.current_thread():
            t.join(timeout)

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
        data = (json.dumps(env, ensure_ascii=False) + "\n").encode("utf-8")
        slot = {"event": threading.Event(), "frame": None}
        with self._send_lock:
            with self._lock:
                # Recheck after waiting for the sender lock. A known closed or
                # poisoned channel has not dispatched this request at all.
                if self.poison is not None:
                    raise RunError(runcodes.NATIVE_RELAY_FAILED)
                if self._closed:
                    raise RunError(runcodes.NATIVE_SESSION_DISCONNECTED)
                self._waiters[rid] = slot
            try:
                self.sock.sendall(data)
            except OSError as exc:
                with self._lock:
                    self._waiters.pop(rid, None)
                    if self.poison is None:
                        self.poison = {
                            "code": runcodes.NATIVE_SESSION_DISCONNECTED,
                            "detail": "请求发送失败，执行结果未知",
                        }
                # sendall may write part or all of a frame before failing.
                raise _RequestOutcomeUnknown(runcodes.NATIVE_SESSION_DISCONNECTED) from exc
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
            raise _RequestOutcomeUnknown(runcodes.NATIVE_RELAY_FAILED)
        frame = slot["frame"]
        if frame is None:
            raise _RequestOutcomeUnknown(runcodes.NATIVE_SESSION_DISCONNECTED)
        got, ver = frame.get("request_id"), frame.get("protocol_version")
        if got != rid or ver != pool.PROTOCOL_VERSION:
            with self._lock:
                self.poison = {
                    "code": runcodes.NATIVE_RELAY_FAILED,
                    "detail": f"回显对不上: request_id={got!r} protocol_version={ver!r}",
                }
            raise _RequestOutcomeUnknown(runcodes.NATIVE_RELAY_FAILED)
        if not isinstance(frame.get("ok"), bool) or (
            frame.get("ok") is False and not isinstance(frame.get("error") or {}, dict)
        ):
            with self._lock:
                self.poison = {
                    "code": runcodes.NATIVE_RELAY_FAILED,
                    "detail": "响应缺少有效的成功或失败结果",
                }
            raise _RequestOutcomeUnknown(runcodes.NATIVE_RELAY_FAILED)
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
            with self._send_lock:
                self.sock.sendall((json.dumps(env, ensure_ascii=False) + "\n").encode("utf-8"))

    def send_line(self, obj: dict) -> None:
        """发一行**不是请求**的控制帧（输入答复）。发不出去抛 `NATIVE_SESSION_DISCONNECTED`。"""
        if self._closed:
            raise RunError(runcodes.NATIVE_SESSION_DISCONNECTED)
        try:
            with self._send_lock:
                self.sock.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError as exc:
            raise RunError(runcodes.NATIVE_SESSION_DISCONNECTED) from exc


class NativeSession:
    """一条 native 会话。**Worker-like**——上层不该知道自己拿的是哪一种。"""

    #: 回执 / 准备路径按它认控制面（`pool.control_plane_of` 读这个属性；值与 `receipt.CONTROL_PLANE_NATIVE` 同）
    control_plane = "native"

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
        #: 桌面拥有的进程所有者（`desktopproc.DesktopProcess`，鸭子类型：`finished` / `cancel()` /
        #: `pid`）。None = 进程是终端的——本模块绝不去结束它。
        self.owned = None
        #: 桌面拥有的会话的**不可变启动身份**（`execspec.ExecutionSpec`，owner=desktop）。含 argv / env 原文，
        #: 所以是私有的：不进 `public_state()` / `diagnostics()`，回执只取它的公开投影。终端拥有的为 None。
        self.spec = None
        #: 桌面拥有的会话：这次运行的运行配置引用 / 回执关联键（公开，不含参数值）
        self.run_config = str(meta.get("run_config") or "")
        self.launch_id = str(meta.get("launch_id") or "")
        #: 这条会话是被所有者取消结束的（与脚本自己退出、崩溃分开记）
        self.cancelled = False
        #: 所属计划的登记序号（桌面拥有的会话由 `desktopproc.start` 盖；终端拥有的恒 None）
        self.launch_order: int | None = None
        #: 脚本此刻正停在的那一次提问（桌面拥有的才有；事实在后端，不属于某个窗口）。键：`request_id` / `kind` /
        #: `prompt` / `masked` / `ordinal` / `at`。**答复从不存在这里**。
        self._pending_input: dict | None = None

        self.state = PENDING_CONFIRMATION
        self.barrier_reason = ""
        self.process_pid: int | None = None
        self.descriptors: list = []
        self.stems: list[str] = []
        #: 逐图的实际完成事实（P08a）：活图 / 失败图 / 只读原件各自声明的能力与时刻。`stems` 只列活图。
        self.figure_facts: list[dict] = []
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
        # Receipt identity advances when user code leaves or reaches a barrier.
        # Wire generation remains unchanged; repeated build/render requests at
        # the same barrier must keep a stable Figure snapshot identity.
        self.snapshot_epoch = 0
        # The receipt epoch at entry to this barrier. EOF and a failed release
        # waiter can revoke it concurrently; both must count the same loss once.
        self._barrier_snapshot_epoch: int | None = None
        # Set only at the authenticated runner's compiled target exec boundary
        # or successful barrier release; never hello, spawn or failed preflight.
        self.execution_id: str | None = None
        self.execution_script: str | None = None
        self.execution_started_ns = 0
        # Application-owned, public invalidation projection; no launch spec/argv.
        self.figure_snapshot_invalidation: dict | None = None
        self.rev = 0
        #: 与 `pool.EngineWorker.built` **同名同义**——「脚本跑过了没有」。
        #: `app.py` 的渲染端点用 `not worker.built` 判冷启动（发 render.started
        #: 的 `cold`），它不知道自己手里是哪一种，也不该知道。
        self.built = False
        self.last_build: dict | None = None
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._on_change = None
        #: 本会话占着的环境租约有没有被释放过（只释放一次）。
        self._lease_released = False
        #: 「与文档不一致」的图：stem → 让它停在这一档的那份全量列表的 canonical hash。
        #: 引擎报 `unrestored > 0`（撤掉的改动还原不回去）时记下，下一次 render 报 0 时解除。
        #: 这期间只放行**同一份列表**的重渲染（它会重试还原），换列表的编辑与导出一律
        #: `native_figure_inconsistent`——与 offline 同一条 409 路。会话是用户的进程，
        #: 不杀不断开；重新运行原命令 = 新会话、新 Figure，这张表自然是空的（Codex #549 第八轮）
        self.inconsistent: dict[str, str] = {}
        #: 「查 `inconsistent` → 发请求 → 按响应更新它」必须是一整段（Codex #549 第九轮）。
        #: 传输支持多个等待者并发，两个 Flask 请求重叠时都会在第一个响应记下标记之前通过
        #: 检查，第二个（换了列表的渲染 / 导出 / continue）就在不一致的 Figure 上执行了，
        #: 晚到的响应还会把标记改回旧的那一次。bridge 本来就串行执行，这把锁只是让检查与
        #: 执行同序。会话粒度（不是 stem）：continue / detach 看的是**所有** stem。
        self._figure_lock = threading.Lock()
        #: 每张图最近一次渲染的那份全量列表（canonical hash）——即会话此刻的列表。export /
        #: preview 临时套用别的列表、还原回它时失败，被标「不一致」的是**它**：之后放行的
        #: 重渲染是这一份（重试还原）。没渲染过就记空串，任何渲染都不放行（只能重跑）
        self._session_list: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 状态机
    # ------------------------------------------------------------------
    def _set_state(
        self, state: str, *, _expected_snapshot_epoch: int | None = None, **fields
    ) -> None:
        if state not in STATES:
            raise ValueError(f"未知的 native 会话状态: {state!r}")
        with self._changed:
            if (
                _expected_snapshot_epoch is not None
                and self.snapshot_epoch != _expected_snapshot_epoch
            ):
                return  # A newer barrier/event has already superseded this response.
            if self.state in TERMINAL_STATES and state not in TERMINAL_STATES:
                # 终态不回头。回头的具体形状是：脚本已经退出了，UI 却因为一条
                # 迟到的事件又显示成"正在运行"。
                return
            self.state = state
            if state in _INPUT_CLEARING_STATES:
                # 脚本已经到了屏障 / 结束：不可能还停在提问上（取消 / 退出 / 断线都走这里）
                self._pending_input = None
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
        if name == "execution_started":
            execution_id = frame.get("execution_id")
            if not executionfact.valid_id(execution_id):
                return
            try:
                script = (
                    _canonical_execution_script(frame["script"])
                    if frame.get("script") is not None
                    else None
                )
            except ValueError:
                return
            with self._lock:
                if self.execution_id is not None or self.state not in {
                    STARTING_PYTHON,
                    WAITING_FOR_FIGURE,
                    RUNNING_SCRIPT,
                }:
                    return
                self.execution_id = execution_id
                self.execution_started_ns = executionfact.started_ns(execution_id)
                self.execution_script = script
            self._set_state(RUNNING_SCRIPT, execution_started=True)
        elif name == "barrier":
            with self._lock:
                if self.state != BARRIER:
                    self.snapshot_epoch += 1
                    self._barrier_snapshot_epoch = self.snapshot_epoch
            stems = frame.get("stems") or []
            facts = frame.get("figure_facts")
            if isinstance(facts, list):
                # 不进事件日志（里头有路径）：先于状态落定，读到 BARRIER 的人一定看到这一个 barrier 的事实
                with self._lock:
                    self.figure_facts = list(facts)
            self._set_state(
                BARRIER,
                barrier_reason=str(frame.get("reason") or ""),
                stems=list(stems),
                script_error=frame.get("script_error") or None,
            )
        elif name == "released":
            with self._lock:
                if "execution_id" in frame:
                    if self.state not in {BARRIER, CONTINUING} or not self._accept_release_fact(
                        frame
                    ):
                        return
                if self.state == BARRIER:
                    self.snapshot_epoch += 1
                self._barrier_snapshot_epoch = None  # The runner confirms release.
            self._set_state(RUNNING_SCRIPT, barrier_reason="")
        elif name == INPUT_REQUEST_EVENT:
            self._on_input_request(frame)
        elif name == "exit":
            with self._lock:
                self._barrier_snapshot_epoch = None
            self._release_lease()
            self._set_state(
                ENDED,
                exit_code=_as_int(frame.get("code")),
                figures_captured=_as_int(frame.get("figures")) or 0,
            )
        elif name == "shutdown":
            with self._lock:
                self._barrier_snapshot_epoch = None
            self._release_lease()
            self._set_state(ENDED)

    def _accept_release_fact(self, frame: dict) -> bool:
        """Accept one trusted release fact, under ``_lock``; both channels agree.

        The runner generates the fact only after release_barrier succeeds.
        HTTP success or a legacy response with no fact cannot create one here.
        """
        execution_id = frame.get("execution_id")
        if (
            not executionfact.valid_id(execution_id)
            or self.execution_id is None
            or frame.get("script") != self.execution_script
        ):
            return False
        if execution_id == self.execution_id:
            return True
        started_ns = executionfact.started_ns(execution_id)
        if started_ns <= self.execution_started_ns:
            return False
        self.execution_id = execution_id
        self.execution_started_ns = started_ns
        return True

    def _revoke_pending_barrier(self) -> bool:
        """Revoke one unconfirmed barrier, with ``_lock`` held.

        Local terminal states (notably one-way terminate) are not runner
        acknowledgements. Only trusted release/end frames or a successful
        continue response clear this pending barrier without uncertainty.
        """
        if self._barrier_snapshot_epoch is not None:
            changed = self.snapshot_epoch == self._barrier_snapshot_epoch
            if changed:
                self.snapshot_epoch += 1
            self._barrier_snapshot_epoch = None
            return changed
        return False

    def _on_transport_closed(self, reason: dict | None) -> None:
        owned = self.owned
        if reason is not None and owned is not None and owned.cancelled:
            # 所有者主动取消（SIGTERM / TerminateJobObject）：对端被杀，Windows 上 reader 读到的是
            # `ConnectionResetError` 而不是干净 EOF——那是取消的后果，不是「连接故障」。取消的终态是 ENDED
            reason = None
        with self._lock:
            # EOF releases a runner barrier even without a continue request.
            # Its Figure may change after contact is lost; do not assert exec.
            revoked = self._revoke_pending_barrier()
        self._release_lease()
        if self.state in TERMINAL_STATES:
            if revoked:
                self._publish()  # Local optimistic ENDED is not a runner acknowledgement.
            return
        if reason is None:
            self._set_state(ENDED)
        else:
            self._set_state(FAILED, terminal_error=reason)

    def note_hello(self, hello: dict) -> None:
        """收到 Bridge Runner 转发过来的握手帧——用户的 Python 起来了。"""
        self._set_state(WAITING_FOR_FIGURE, process_pid=_as_int(hello.get("pid")))

    # ------------------------------------------------------------------
    # 脚本的提问（P07，ADR 0126）：桌面拥有的进程里 input / stdin / getpass
    # ------------------------------------------------------------------
    @property
    def pending_input(self) -> dict | None:
        """脚本此刻停在的那一次提问的公开形状（拷贝）；没有在等的 = None。"""
        with self._lock:
            return dict(self._pending_input) if self._pending_input else None

    def _publish(self, **fields) -> None:
        """状态不变、但对界面有意义的变化（提问出现 / 被答 / 被取消）：序号 +1、记事件、推变更。

        事件里只有 `input` 与 `request_id` / `input_kind`——**没有提示词与答案**（SSE 是给所有窗口的）。"""
        with self._changed:
            self.sequence += 1
            self.last_event_at = time.time()
            entry = {"seq": self.sequence, "state": self.state, "at": self.last_event_at, **fields}
            self.events.append(entry)
            self._changed.notify_all()
        if self._on_change is not None:
            self._on_change(self, entry)

    def _on_input_request(self, frame: dict) -> None:
        """reader 线程里跑。终端拥有的会话从不装输入桥，收到也不信（没有人能答）。"""
        if self.owned is None:
            return
        rid = frame.get("request_id")
        if not isinstance(rid, str) or not rid or len(rid) > 128:
            return
        kind = frame.get("kind")
        kind = kind if kind in INPUT_KINDS else "input"
        prompt = frame.get("prompt")
        pending = {
            "request_id": rid,
            "kind": kind,
            "prompt": prompt[:INPUT_PROMPT_LIMIT] if isinstance(prompt, str) else "",
            "masked": kind == "getpass",  # 由种类推出，不信对端自报
            "ordinal": _as_int(frame.get("ordinal")) or 0,
            "at": time.time(),
        }
        with self._lock:
            # 终态判定与装 pending 在同一把锁里:`_set_state` 进终态也持这把锁（并清 pending）,所以终态之后才到的
            # 提问一律丢弃,不会在已结束的会话上复活一个答不了的框
            if self.state in TERMINAL_STATES:
                return
            self._pending_input = pending
        self._publish(input="requested", request_id=rid, input_kind=kind)

    def answer_input(self, request_id: str, *, text: str | None = None, eof: bool = False) -> dict:
        """把用户的答复交给停在提问上的脚本——**同一个进程、同一批对象续跑**。

        先到先得：答复先校验（类型 / 长度 / 能否编码）、认领（清掉 pending）是**最后一步校验**，之后才发；第二个窗口的答复、旧提问的答复、
        没有提问时的答复一律 `native_input_not_pending`，不排队、不喂给之后的提问。`eof=True` =
        「没有输入」（`input` 抛 `EOFError`、读回空串；之后同一个进程里 stdin 家族不再提问）。
        口令的值只经这条通道发一次，不进事件 / 日志 / 状态。
        """
        if not eof:
            if not isinstance(text, str):
                raise RunError(runcodes.NATIVE_INPUT_INVALID, reason="答复必须是文本")
            try:
                size = len(
                    text.encode("utf-8")
                )  # 严格编码:控制通道两侧钉 UTF-8,编不出来的答复到不了脚本
            except UnicodeEncodeError:
                raise RunError(
                    runcodes.NATIVE_INPUT_INVALID, reason="答复含有无法编码的字符"
                ) from None
            if size > INPUT_ANSWER_LIMIT:
                raise RunError(runcodes.NATIVE_INPUT_INVALID, reason="答复太长")
        with self._lock:
            pending = self._pending_input
            if self.owned is None or pending is None or pending["request_id"] != request_id:
                raise RunError(runcodes.NATIVE_INPUT_NOT_PENDING)
            transport = self.transport
            self._pending_input = None  # 认领：此后的任何答复都会被拒
        if transport is None or transport.closed:
            raise RunError(runcodes.NATIVE_SESSION_DISCONNECTED)
        reply = {"request_id": request_id, **({"eof": True} if eof else {"text": text})}
        transport.send_line({INPUT_REPLY_KEY: reply})
        self._publish(input="answered", request_id=request_id, input_kind=pending["kind"])
        return {"answered": True, "request_id": request_id}

    def _release_lease(self) -> None:
        owned = self.owned
        if owned is not None and not owned.confirmed:
            # 桌面拥有的进程还没被所有者确认结束：控制连接断了、状态进了终态都不放租约——
            # 还活着的脚本正在用这个环境，放了就等于放安装器进来改它（AC-16）。
            # 所有者确认结束时回调 `_on_owner_exit()`，那里再放。
            return
        with self._lock:
            if self._lease_released or not self.interpreter:
                return
            self._lease_released = True
        envlease.release_native(self.interpreter, self.session_id)

    def _on_owner_exit(self, code: int | None, *, cancelled: bool) -> None:
        """所有者（`desktopproc`）确认进程已退出并 reap 之后调用——**租约在这里才放**。

        `code` 是进程的真实退出码（POSIX 被信号杀 = 128+signo，与 `tavotto run` 同口径）。
        脚本自己的非零退出码原样保留；取消只额外记 `cancelled`，不改写码。
        """
        transport = self.transport
        if transport is not None:
            # 所有者的等待线程与传输 reader 是两条线程：脚本正常退出时 exit 帧可能还在缓冲里。
            # 先等 reader 把最后一帧处理完（它会落 ENDED），再判「没通知就走了」
            transport.join_reader(2.0)
        with self._changed:
            self.cancelled = self.cancelled or cancelled
            if code is not None:
                self.exit_code = code
        if self.state not in TERMINAL_STATES:
            if cancelled:
                self._set_state(ENDED, exit_code=code, terminal_error=None)
            else:
                self._set_state(
                    FAILED,
                    exit_code=code,
                    terminal_error={
                        "code": runcodes.BRIDGE_CHILD_EXITED,
                        "detail": "进程在没有通知控制通道的情况下退出",
                    },
                )
        self._release_lease()

    def cancel_owned(self) -> dict:
        """结束**桌面拥有的**脚本进程（含它的进程组 / Job）。终端拥有的会话拒绝（`NATIVE_PROCESS_NOT_OWNED`）。

        这是 `force_kill` / `shutdown`（放手）之外**唯一**会结束进程的入口，所以名字不叫 kill：
        `force_kill` 对终端会话是 detach 的别名，把它改成 kill 会误杀用户终端里的进程。
        返回所有者的取消结果（`confirmed` = 进程与进程组都已确认退出）。
        """
        owned = self.owned
        if owned is None:
            raise RunError(runcodes.NATIVE_PROCESS_NOT_OWNED)
        return owned.cancel()

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
        try:
            return self._require_barrier().request(
                obj, generation=self.generation, revision=self.rev, timeout=timeout
            )
        except pool.WorkerError as exc:
            if exc.code == pool.figcapture.PREVIEW_POISONED_CODE:
                # 预览产物对读方不可信：读方进程回「无预览」，传输标 poison（既有的
                # 「会话不再可信」机制），之后每条请求都 NATIVE_RELAY_FAILED
                pool.figcapture.poison_preview_dir(self.out_dir)
                transport = self.transport
                if transport is not None and transport.poison is None:
                    transport.poison = {"code": runcodes.NATIVE_RELAY_FAILED, "detail": str(exc)}
            raise

    @property
    def last_build_descriptors(self) -> list:
        """`pool.EngineWorker` 上这批描述符叫这个名字，`app.py` 的 runtime
        cache 物化只认这个名字。**存储仍然只有 `descriptors` 一份**——别名
        只读，两份各自更新迟早会分叉。
        """
        return self.descriptors

    @property
    def last_build_runtime(self) -> dict | None:
        """与 `pool.EngineWorker.last_build_runtime` 同名同义（ADR 0053）：bridge 的
        build 响应里 `runtime` 那一段——用户自己那个解释器的 prefix / cwd 自报。
        存储仍只有 `last_build` 一份，这里是只读投影。"""
        rt = (self.last_build or {}).get("runtime") if isinstance(self.last_build, dict) else None
        return dict(rt) if isinstance(rt, dict) else None

    @property
    def observed_inputs(self) -> dict | None:
        """桌面拥有的运行在脚本**跑完之后**的 build 才带的输入观察（`figcapture.InputObserver.report()`，#1047）：观察到的
        项目内数据文件与本地模块（项目相对路径 + sha256）。`show()` 屏障上的 build 没有它（脚本还没跑完）= None。
        含项目相对路径：只给启动记录用，不进 `public_state()` / 事件 / 诊断。存储仍只有 `last_build` 一份。"""
        ins = (
            (self.last_build or {}).get("observed_inputs")
            if isinstance(self.last_build, dict)
            else None
        )
        return dict(ins) if isinstance(ins, dict) else None

    @property
    def child_pid(self) -> int | None:
        """与 `pool.EngineWorker.child_pid` 同名同义（ADR 0070）：握手帧里用户进程自报的 pid——回执核
        「自报来自这一个进程」用；没握过手就是 None（那一维不核）。存储仍只有 `process_pid` 一份。"""
        return self.process_pid

    @property
    def export_dir(self) -> Path:
        """导出临时件的落点（画布导出取 `export_dir / f"{stem}.pdf"`）。

        与 safe worker 一样**单独一层子目录**，不直接用 `out_dir`：那是会话
        自己的产出面，runner 往里写 `{stem}.svg` / `{stem}.json` / 预览 PNG，
        `runcli._figures_written()` 还会扫它来数这次跑出了几张图。导出的临时
        件属于另一层，混进去就是让两件事共用一个目录。
        """
        d = self.out_dir / "export"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def ensure_built(self) -> dict:
        resp = self._request({"cmd": "build"}, BUILD_TIMEOUT)
        self.built = True
        self.last_build = resp
        self.descriptors = list(resp.get("descriptors") or [])
        self.figure_facts = list(resp.get("figure_facts") or [])
        self.stems = list((resp.get("stems") or {}).keys())
        self.script_error = resp.get("script_error") or self.script_error
        return resp

    def publish_built(self) -> None:
        """build 出来的描述符已经绑上活路由：把带描述符的新快照推给界面（状态不变，序号 +1）。

        桌面拥有的会话由准备 runner 自己 build，屏障事件在 build 之前就发出了——界面手里这条会话的 `descriptors`
        还是空的，面板按描述符认领会话，认不到就把一张活着的图报成「会话已结束」。`GET /api/native/sessions`
        的快照与屏障事件同序号，`refresh()` 的「序号更大才覆盖」也补不上，所以要由后端发一条序号更大的。
        终端拥有的会话不走这里：界面在屏障处自己 build，再多一条事件只会让它再 build 一次。"""
        self._publish(built="descriptors")

    def override(
        self, stem: str, patches: list, preview_dpi: int | None = None, inline_svg: bool = False
    ) -> dict:
        """**签名与 `pool.EngineWorker.override()` 逐参对齐。**

        `app.py` 的渲染端点发的是 `wk.override(st, patches, preview_dpi,
        inline_svg=...)`——第三个是**位置**参数。原来的 `**kw` 收不下它，
        native 面板每一次渲染都是 TypeError。
        """
        want = patchspec.patch_hash(patches)
        payload = {"cmd": "override", "stem": stem, "patches": patches}
        # 不给就**一个字段都不加**：信封形状与 safe worker 一字不差
        if preview_dpi:
            payload["preview_dpi"] = int(preview_dpi)
        if inline_svg:
            payload["inline_svg"] = True
        with self._figure_lock:
            owed = self.inconsistent.get(stem)
            if owed is not None and owed != want:
                raise RunError(runcodes.NATIVE_FIGURE_INCONSISTENT)
            self.rev += 1
            resp = self._request(payload, REQUEST_TIMEOUT)
            self._session_list[stem] = want
            if self._owes(resp):
                self.inconsistent[stem] = want
            else:
                self.inconsistent.pop(stem, None)
            return resp

    @staticmethod
    def _owes(resp: dict) -> bool:
        """结果说这张图还欠着还原（v1 的 `unrestored`；老 bridge 不给 = 0）。"""
        n = resp.get("unrestored")
        return isinstance(n, int) and n > 0

    def _note_temporary(self, stem: str, resp: dict) -> None:
        """export / preview 临时套用、还原回会话列表之后的账（Codex #549 第九轮 P1）：还原
        失败就标「不一致」，锚在会话列表上。调用方持有 `_figure_lock`。"""
        if self._owes(resp):
            self.inconsistent[stem] = self._session_list.get(stem, "")

    def _require_consistent(self, stem: str | None = None) -> None:
        """导出 / 历史预览拿的是 live Figure：图与文档不一致时不许拿它交差。
        `stem=None` 问的是整条会话（continue / detach：脚本会看到**每一张**图）。
        调用方必须持有 `_figure_lock`。"""
        if self.inconsistent if stem is None else stem in self.inconsistent:
            raise RunError(runcodes.NATIVE_FIGURE_INCONSISTENT)

    def export(
        self,
        stem: str,
        patches: list,
        path: str,
        fmt: str = "pdf",
        dpi: int = 600,
        *,
        svg_path: str | None = None,
        require_built: bool = False,
        snapshot_only: bool = False,
        svg_fonttype: str | None = None,
        svg_text_path: str | None = None,
    ) -> dict:
        with self._figure_lock:
            self._require_consistent(stem)
            resp = self._request(
                {
                    "cmd": "export",
                    "stem": stem,
                    "patches": patches,
                    "path": path,
                    "format": fmt,
                    "dpi": dpi,
                    **({"svg_path": svg_path} if svg_path is not None else {}),
                    **({"require_built": True} if require_built else {}),
                    **({"snapshot_only": True} if snapshot_only else {}),
                    **({"svg_fonttype": svg_fonttype} if svg_fonttype is not None else {}),
                    **({"svg_text_path": svg_text_path} if svg_text_path is not None else {}),
                },
                EXPORT_TIMEOUT,
            )
            self._note_temporary(stem, resp)
            return resp

    def render_png(self, stem: str, width_px: int) -> Path:
        self._request({"cmd": "render_png", "stem": stem, "width": int(width_px)}, REQUEST_TIMEOUT)
        return self.out_dir / f"{stem}_w{int(width_px)}.png"

    def preview_png(self, stem: str, patches: list, width_px: int, tag: str) -> Path:
        with self._figure_lock:
            self._require_consistent(stem)
            resp = self._request(
                {
                    "cmd": "preview_png",
                    "stem": stem,
                    "patches": patches,
                    "width": int(width_px),
                    "tag": tag,
                },
                REQUEST_TIMEOUT,
            )
            self._note_temporary(stem, resp)
        return self.out_dir / f"{stem}__{tag}.png"

    def preview_png_snapshot(self, stem: str, patches: list, width_px: int) -> dict:
        with self._figure_lock:
            self._require_consistent(stem)
            resp = self._request(
                {
                    "cmd": "preview_png",
                    "stem": stem,
                    "patches": patches,
                    "width": int(width_px),
                    "with_manifest": True,
                },
                REQUEST_TIMEOUT,
            )
            self._note_temporary(stem, resp)
            return resp

    def svg_path(self, stem: str) -> Path:
        return Path(pool.figcapture.usable_preview_path(self.out_dir, stem, ".svg"))

    def resume(self) -> dict:
        """放开屏障，让用户脚本接着往下跑。

        **runner 侧会先把 Figure 恢复成脚本原样**（ADR 0021 §8）——那一步在
        用户的进程里做，这里只是发一条 `continue`。
        """
        return self._release(CONTINUING)

    def detach(self) -> dict:
        """放手：脚本继续正常跑完，Tavotto 不再控制它。

        与 `resume` 的区别只在**之后**：连接关掉、会话进终态 `detached`。
        屏障的恢复语义两者相同（runner 侧的 `release_barrier`），所以脚本
        无论如何都看不到 Tavotto 的 override。
        """
        resp = self._release(DETACHED)
        if self.transport is not None:
            self.transport.close()
        return resp

    def _release(self, then: str) -> dict:
        """continue / detach 共用的放行：**任何一张图与文档不一致就不放**（Codex #549 第九轮）。

        放行之前 runner 要把 Figure 恢复成脚本原样（ADR 0021 §8.1），而欠着还原的图恰恰
        恢复不回去——放行等于让脚本带着 Tavotto 半改的 Figure 往下跑。这里先挡一层；
        runner 侧 `release_barrier()` 还原失败时同样拒绝（它回 `native_figure_inconsistent`，
        这里原样翻成同一个码），不只靠这一层。`terminate` 不走这里：脚本不会再往下跑。

        状态切到 `then`（CONTINUING / DETACHED）**也在锁里**（Codex #549 第九轮 P1）：runner 收到
        continue 就离开了控制循环，锁一放、状态还停在 BARRIER 的那一瞬，等锁的渲染会通过
        `_require_barrier()` 把帧发给一个不再读它的 runner。
        """
        with self._figure_lock:
            self._require_consistent()
            previous_epoch = self.snapshot_epoch
            try:
                resp = self._request({"cmd": "continue"}, REQUEST_TIMEOUT)
            except _RequestOutcomeUnknown:
                with self._lock:
                    # The release event may already have revoked this epoch.
                    # Never manufacture execution_id: resumption is unknown.
                    if self.snapshot_epoch == previous_epoch:
                        self.snapshot_epoch += 1
                self._publish()
                raise
            except pool.WorkerError as exc:
                if getattr(exc, "code", "") == runcodes.NATIVE_FIGURE_INCONSISTENT:
                    raise RunError(runcodes.NATIVE_FIGURE_INCONSISTENT) from exc
                raise
            with self._lock:
                if resp.get("released") is True:
                    self._accept_release_fact(resp)
                # The released event may already have advanced it. Either way,
                # successful continuation revokes the old Figure immediately,
                # including when the script ends without another barrier.
                if self.snapshot_epoch == previous_epoch:
                    self.snapshot_epoch += 1
                if self._barrier_snapshot_epoch == previous_epoch:
                    self._barrier_snapshot_epoch = None
                if then == DETACHED:
                    # The next barrier can arrive before this response. Detach
                    # closes that barrier too, while resume must preserve it.
                    self._revoke_pending_barrier()
            self._set_state(
                then,
                _expected_snapshot_epoch=previous_epoch + 1 if then == CONTINUING else None,
            )
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
        with self._lock:
            # Controller shutdown is detach-and-continue, unlike a trusted
            # runner "shutdown" event. Revoke before publishing DETACHED.
            revoked = self._revoke_pending_barrier()
        self._release_lease()
        # **先标状态再关连接**：关连接会让 reader 线程走到 `_on_transport_closed`，
        # 那条路把会话记成 `ended`（对端正常收摊）。反过来做的话，我们自己
        # 主动放手的这一次会被记成"脚本结束了"——而脚本其实还在跑，UI 于是
        # 显示"会话已结束 退出码 None"，用户以为他的脚本被杀了。
        if self.state not in TERMINAL_STATES:
            self._set_state(DETACHED)
        elif revoked:
            self._publish()
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
            "owner": "desktop" if self.owned is not None else "terminal",
            #: 桌面拥有的会话属于哪个准备计划（计划号，不透明）：界面据此把「停止运行」对到这次运行；终端拥有的为空串
            "launch_id": self.launch_id,
            "state": self.state,
            "barrier_reason": self.barrier_reason,
            "process_pid": self.process_pid,
            "stems": list(self.stems),
            "descriptors": list(self.descriptors),
            "figure_facts": list(self.figure_facts),
            "script_error": self.script_error,
            "terminal_error": self.terminal_error,
            "exit_code": self.exit_code,
            "cancelled": self.cancelled,
            "figures_captured": self.figures_captured,
            "started_at": self.started_at,
            "last_event_at": self.last_event_at,
            "sequence": self.sequence,
            "editable": self.state == BARRIER,
            "pending_input": self.pending_input,
            **(
                {"figure_snapshot_invalidation": copy.deepcopy(self.figure_snapshot_invalidation)}
                if self.figure_snapshot_invalidation is not None
                else {}
            ),
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


#: `shutdown_all` 等在途启动线程收尾的总上限（秒）。
_START_WAIT_S = 30.0


class StartTicket:
    """一次桌面拥有的启动在注册表里的「在途」登记：**spawn 之前**就领，Popen 之后才挂上所有者。

    `shutdown_all` 的快照里要么看得到它（并标 `cancelled`，之后挂所有者的一步会被拒），要么注册表已 closing
    根本领不到——两者之间没有「进程已起、谁也看不见」的窗口。"""

    __slots__ = (
        "launch_id",
        "project_root",
        "script",
        "order",
        "cancel_check",
        "owner",
        "cancelled",
        "done",
    )

    def __init__(
        self, launch_id: str, project_root: str = "", script: str = "", order: int | None = None
    ):
        self.launch_id = launch_id
        #: 这次启动属于哪个项目(项目关闭据此取消在途启动);空 = 不属于任何项目
        self.project_root = project_root
        #: 启动的脚本(`spec.target`,项目相对);同一脚本的重新运行据此取消在途启动
        self.script = script
        #: 所属计划的登记序号（`PreparationPlan.launch_order`）；None = 未知
        self.order = order
        #: 启动方的「我是不是已被取消 / 取代」判定（`PreparationService.is_cancelled`）；None = 不核。`check_start`
        #: 在 Popen 之前最后再问一次，与 `reserve` / `begin_start` 是同一个判定
        self.cancel_check = None
        self.owner = None
        self.cancelled = False
        #: 启动线程走完（成功或失败收尾都做完）时置位：关停据此等「在途的启动线程」，哪怕它还没 Popen
        self.done = threading.Event()


class NativeSessionRegistry:
    """进程内的所有 native 会话 + logical asset → live route 的映射。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: dict[str, NativeSession] = {}
        #: (project_root, script, stem) → session_id。**live route，不是身份**
        #: （ADR 0021 §9）：会话结束即失效，绝不进文档。
        self._routes: dict[tuple, str] = {}
        self._reserved: set[str] = set()
        #: 已关闭的项目(realpath):其后任何新启动在 `reserve` / `begin_start` 当场被拒,直到项目重新打开
        self._closed_projects: set[str] = set()
        #: 项目的「关闭代次」:每次关闭 +1。启动方带着自己登记时看到的代次来:代次对不上 = 它是关闭**之前**的旧准备,
        #: 项目即使已重新打开也不许起进程（重新打开只清「已关闭」标记,不让旧计划借新状态复活）
        self._project_epoch: dict[str, int] = {}
        #: launch_id → 已 spawn、还在握手 / 登记中的所有者。不在 `_sessions` 里，但进程已经在跑：
        #: `shutdown_all` 必须看得见它，否则 sidecar 关停时漏掉成孤儿。
        self._starting: dict[str, StartTicket] = {}
        #: sidecar 退出中 / 已退出：`shutdown_all()` 第一步在锁内置位。此后 `reserve` / `begin_start` /
        #: `attach_owner` / `adopt` 一律原子拒绝（`native_registry_closing`），在途的启动在下一个检查点
        #: 自行收掉子进程。只有 `reopen()`（测试 / 同进程重启）能清。
        self._closing = False
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

    def project_epoch(self, project_root: str) -> int:
        """这个项目当前的关闭代次（登记计划时取一份带着走）。"""
        with self._lock:
            return self._project_epoch.get(os.path.realpath(project_root), 0)

    def _refuse_if_closed(
        self, project_root: str, epoch: int | None = None, cancelled=None
    ) -> None:
        """调用方持锁。注册表 closing、这个项目已关闭、或启动方的代次已过期 → 当场拒绝(一行用户代码都没跑)。"""
        if self._closing:
            raise RunError(runcodes.NATIVE_REGISTRY_CLOSING)
        if cancelled is not None and cancelled():
            # 启动方的计划已被取消 / 被更新的同脚本计划取代：一行用户代码都不跑（取消落在「还没领票据」的窗口里，
            # 重跑的 `cancel_project` 看不见它）
            raise RunError(runcodes.NATIVE_REGISTRY_CLOSING)
        if not project_root:
            return
        root = os.path.realpath(project_root)
        if root in self._closed_projects or (
            epoch is not None and epoch != self._project_epoch.get(root, 0)
        ):
            raise RunError(runcodes.NATIVE_REGISTRY_CLOSING)

    def reserve(
        self,
        launch_id: str,
        project_root: str = "",
        epoch: int | None = None,
        cancelled=None,
    ) -> None:
        """原子占住一个桌面拥有的 launch_id（取租约 / spawn 之前）；已被占、或同 id 的上一任还没被所有者确认
        结束（`owned.finished`）就抛冲突。检查与占位在同一把锁里。

        占位由所有者的完成回调释放（`desktopproc.start`）——握手 / 登记失败后清组超时（取消未确认）也不例外。
        租约键另有一层保险：每次启动的会话 id 都带唯一后缀，不同所有者互相删不掉对方的租约。"""
        with self._lock:
            self._refuse_if_closed(project_root, epoch, cancelled)
            busy = any(
                s.launch_id == launch_id
                and (s.alive() or (s.owned is not None and not s.owned.finished))
                for s in self._sessions.values()
            )
            if busy or launch_id in self._reserved:
                raise RunError(runcodes.NATIVE_SESSION_CONFLICT)
            self._reserved.add(launch_id)

    def begin_start(
        self,
        launch_id: str,
        project_root: str = "",
        script: str = "",
        epoch: int | None = None,
        order: int | None = None,
        cancelled=None,
    ) -> StartTicket:
        """spawn **之前**领在途登记；closing 时拒绝（一行用户代码都没跑）。"""
        with self._lock:
            self._refuse_if_closed(project_root, epoch, cancelled)
            ticket = StartTicket(
                launch_id, os.path.realpath(project_root) if project_root else "", script, order
            )
            ticket.cancel_check = cancelled
            self._starting[launch_id] = ticket
            return ticket

    def attach_owner(self, ticket: StartTicket, owner) -> None:
        """Popen 之后把所有者挂到登记上。关停已经开始（标了 cancelled / closing）就拒绝——调用方自己收子进程。"""
        with self._lock:
            if self._closing or ticket.cancelled:
                raise RunError(runcodes.NATIVE_REGISTRY_CLOSING)
            ticket.owner = owner

    def check_start(self, ticket: StartTicket) -> None:
        """Popen 之前的检查点：关停已开始就拒绝，**不再 spawn**。"""
        with self._lock:
            if (
                self._closing
                or ticket.cancelled
                or (ticket.cancel_check is not None and ticket.cancel_check())
            ):
                raise RunError(runcodes.NATIVE_REGISTRY_CLOSING)

    def end_start(self, ticket: StartTicket) -> None:
        """按登记**身份**移除：同 id 重试（ABA）时先前那次的收尾删不掉后来者的。"""
        with self._lock:
            if self._starting.get(ticket.launch_id) is ticket:
                del self._starting[ticket.launch_id]
        ticket.done.set()

    def reopen(self) -> None:
        """清 closing（仅测试隔离 / 同进程重启用）。"""
        with self._lock:
            self._closing = False
            self._closed_projects.clear()
            self._project_epoch.clear()

    def close_project(self, project_root: str) -> int:
        """项目关闭:**先**标记(此后这个项目的新启动在 `reserve` / `begin_start` 当场被拒,覆盖「准备线程还没走到
        登记」的所有窗口),**再**结束已登记的会话与在途票据。返回结束的数量。项目重新打开时 `reopen_project`。"""
        self.mark_project_closed(project_root)
        return self.cancel_project(project_root)

    def mark_project_closed(self, project_root: str) -> None:
        """只做标记(此后这个项目的新启动当场被拒)。关项目分步做时先标记、再取消准备、再 `close_project` 结束会话。"""
        root = os.path.realpath(project_root)
        with self._lock:
            if root not in self._closed_projects:  # 已在关闭中（分步关项目会标记两次）不重复升代次
                self._closed_projects.add(root)
                self._project_epoch[root] = self._project_epoch.get(root, 0) + 1

    def reopen_project(self, project_root: str) -> None:
        with self._lock:
            self._closed_projects.discard(os.path.realpath(project_root))

    def unreserve(self, launch_id: str) -> None:
        with self._lock:
            self._reserved.discard(launch_id)

    def adopt(self, session: NativeSession) -> None:
        """登记一条**桌面拥有**的会话（`desktopproc.start` 在握手成功、传输起好之后调）。

        与 `attach` 的区别只在会话怎么来的（没有 relay / descriptor）；之后的注册表语义
        （live route、终态丢路由、`on_change`）是同一套。
        """
        with self._lock:
            if self._closing:
                raise RunError(runcodes.NATIVE_REGISTRY_CLOSING)
            prev = self._sessions.get(session.session_id)
            if prev is not None and (
                prev.alive() or (prev.owned is not None and not prev.owned.finished)
            ):
                raise RunError(runcodes.NATIVE_SESSION_CONFLICT)
            session._on_change = self._changed  # noqa: SLF001 — 注册表持有会话
            self._sessions[session.session_id] = session
        # 传输 reader 在登记之前就已经在跑:脚本一起来就 `input()` / 到屏障时,那条事件发出时回调还没装上,
        # 会被丢掉而界面再也收不到(准备一直等着)。登记完成后把当前最新一条补发出去(序号单调,重复无害)
        with session._lock:  # noqa: SLF001
            last = session.events[-1] if session.events else None
        if last is not None:
            self._changed(session, last)

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
        claims: list[tuple] = []
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
                claims.append(key)
            # 全有或全无:任一张被占着,这一轮 build 的图一张都不绑——否则界面 / 缓存里留着一张「没有活路由」的图,
            # 而 runner 对同一结果判的是失败（`/build` 端点与准备 runner 共用这一个判定）
            if not rejected:
                for key in claims:
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

    def script_route_conflict(
        self, project_root: str, script: str, *, exclude_session_id: str = ""
    ) -> bool:
        """Whether another live session already owns any route for this script.

        A second terminal invocation may execute, but must not use its start
        fact to revoke the first invocation's established source ownership.
        This is a read-only eligibility check, not a claim or route takeover.
        """
        wanted = self.route_key(project_root, _canonical_execution_script(script), "")[:2]
        with self._lock:
            return any(
                key[:2] == wanted
                and session_id != exclude_session_id
                and (session := self._sessions.get(session_id)) is not None
                and session.alive()
                for key, session_id in self._routes.items()
            )

    def _drop_routes(self, session_id: str) -> None:
        with self._lock:
            for key in [k for k, v in self._routes.items() if v == session_id]:
                self._routes.pop(key, None)

    # ---------------- 收尾 ----------------
    def forget(self, session_id: str) -> None:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is not None and s.owned is not None and not s.owned.finished:
                return  # 桌面拥有的进程还没确认结束：不能从注册表消失，否则 shutdown_all 看不见它
            self._sessions.pop(session_id, None)
        self._drop_routes(session_id)

    def prune(self, *, keep: int = 20) -> int:
        """终态会话留最近 `keep` 条给 UI 看，多的丢掉。"""
        with self._lock:
            done = sorted(
                (
                    s
                    for s in self._sessions.values()
                    if s.state in TERMINAL_STATES and (s.owned is None or s.owned.finished)
                ),
                key=lambda s: s.last_event_at,
            )
            victims = done[: max(0, len(done) - keep)]
            for s in victims:
                self._sessions.pop(s.session_id, None)
        for s in victims:
            self._drop_routes(s.session_id)
        return len(victims)

    def cancel_project(
        self, project_root: str, *, script: str = "", older_than: int | None = None
    ) -> int:
        """结束这个项目里**桌面拥有的**还活着的会话（项目关闭 / 同一脚本重新运行）；返回结束了几条。

        终端拥有的一条不碰（用户的进程，关项目不该带走它，ADR 0021 §8.1）。`script` 非空 = 只结束这个脚本的
        （`spec.target` 是项目相对 POSIX 路径）。每条走 `cancel_owned()`：等到所有者确认进程组 / Job 空、租约放掉。"""
        # 还没登记的在途启动(`begin_start` 与 `adopt` 之间):标 cancelled,挂所有者 / 登记的下一步会被拒;
        # 已挂所有者的当场同步结束(与 `shutdown_all` 同一套)。重新运行(`script` 非空)只取消**同一脚本**的在途启动:
        # 调用方自己的启动此刻还没领票据,所以不会误伤;两条重叠的重跑,后来的取消先来的(旧的先于新的起进程之前收掉)
        root = os.path.realpath(project_root)
        with self._lock:
            flying = [
                t
                for t in self._starting.values()
                if t.project_root == root
                and (not script or t.script == script)
                and _is_older(t.order, older_than)
            ]
            for t in flying:
                t.cancelled = True
        stoppers = [t.owner.terminate_sync for t in flying if t.owner is not None]
        victims = [
            s
            for s in self.list(project_root)
            if s.owned is not None
            and not s.owned.finished
            and (not script or (s.spec is not None and s.spec.target == script))
            and _is_older(s.launch_order, older_than)
        ]
        threads = [
            threading.Thread(target=_run_quiet, args=(fn,), daemon=True)
            for fn in [*stoppers, *(s.cancel_owned for s in victims)]
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join(60)
        return len(victims) + len(flying)

    def shutdown_all(self) -> int:
        """sidecar 退出：终端拥有的**只放手，不杀**（ADR 0021 §8.1 / §10.2）。

        桌面拥有的进程是 sidecar 自己起的，退出时不能留成没人管的孤儿（还占着环境）：
        先断开，再按所有者的有界策略结束（ADR 0125 §五）。"""
        with self._lock:
            self._closing = True  # 第一步：此后任何新启动 / 登记都被原子拒绝
            sessions = list(self._sessions.values())
            tickets = list(self._starting.values())
            for t in tickets:
                t.cancelled = True  # 还没挂所有者的，挂的那一步会被拒
        for s in sessions:
            with _suppress_all():
                s.shutdown()
        # 在途启动的所有者 + 已登记会话的所有者：并发收、**同步等到进程确认消失**（有界）再返回，
        # 否则 sidecar 比子进程先退，就成了孤儿
        # 已登记的走 `cancel_owned()`（等到 finished：终态与租约都落定）；在途的所有者还握着启动闸门，
        # 走 `terminate_sync()`（等进程与进程组确认消失，不等闸门）
        targets = [t.owner.terminate_sync for t in tickets if t.owner is not None]
        targets += [s.cancel_owned for s in sessions if s.owned is not None]
        threads = [
            threading.Thread(target=_run_quiet, args=(fn,), daemon=True, name="tavotto-owned-stop")
            for fn in targets
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join(60)
        # 每张在途票据都等到它的启动线程收尾（含还没 Popen / 还没挂所有者的）：否则这里先返回、sidecar 先退出，
        # 启动线程随后才 Popen，子进程就成了孤儿。有界（spawn 预算 + 失败收尾）
        deadline = time.monotonic() + _START_WAIT_S
        for t in tickets:
            t.done.wait(max(0.0, deadline - time.monotonic()))
        return len(sessions) + len(tickets)


def _is_older(order: int | None, older_than: int | None) -> bool:
    """重跑的先后判定:没给 `older_than`（关项目等）全部命中；给了就只命中**序号更小**的，序号未知的（终端拥有的、
    不经准备计划的）不碰——顺序的唯一出处是 `PreparationService` 的登记序号。"""
    return older_than is None or (order is not None and order < older_than)


def _run_quiet(fn) -> None:
    with _suppress_all():
        fn()


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
