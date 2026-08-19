"""Flask ↔ `magplot-workerd` 的客户端（**纯标准库**，Flask 父进程 import）。

supervisor 协议是一条 stdio JSON 行协议，与 worker 协议 v1（ADR 0003）**是两套
东西**：worker 那条严格串行、一次一请求；这条要被 Flask 的多个线程共用，靠
`request_id` 多路复用。完整契约见 `docs/adr/0004-workerd-supervisor.md`。

三条与「别再犯老毛病」直接相关的实现纪律：

* **一条 reader 线程 + 按 request_id 分发**：调用线程只等自己那个 `Event`，
  绝不去读管道。多个线程各自 readline 会把彼此的响应吃掉。
* **写端持锁串行**：一行必须原子地写出去，交错半行会让 workerd 当场解析失败。
* **workerd 崩了所有 pending 立刻失败**：不许让调用线程挂在一个永远不会有人
  回应的 Event 上——那就是 Python 池里「会话死锁」的同一种病。

策略仍全在 Python：解释器探测、内置 runtime 的 env/args、超时档位、会话上限，
都由 `pool.py` 算好装进 spawn 规格交过去。workerd 只负责生命周期与可靠性。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time

from . import config, runtime

LOG = logging.getLogger("mm.engine")

#: supervisor 协议版本（与 `workerd/src/protocol.rs` 的常量同源）。
SUPERVISOR_PROTOCOL_VERSION = 1

#: 可执行文件名（Windows 上带 .exe）。
EXE_NAME = "magplot-workerd.exe" if os.name == "nt" else "magplot-workerd"

#: 调用线程在 workerd 自己的期限之外再多等这么久。workerd 会**按请求携带的
#: timeout 自己超时并回一条错误**，所以这里等到的永远该是一条响应；多给的这段
#: 只是防「workerd 整个卡住」，不参与业务超时判断。
_SLACK_SECONDS = 30.0

#: 连续「起来就崩」多少次之后放弃 workerd（本进程内回退到 Python 池）。
#: 不设上限的话一个坏产物会变成无限重启循环，每次渲染都白等一轮。
_MAX_RESTARTS = 3
#: 起来之后活过这么久就算这次重启成功，重启计数清零。
_MIN_UPTIME = 5.0


class WorkerdError(RuntimeError):
    """supervisor 层的结构化错误（`pool` 再转成 `WorkerError`）。"""

    def __init__(self, message: str, code: str = "", retryable: bool = False,
                 traceback_text: str = "", extra: dict | None = None,
                 session_id: str = ""):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.traceback_text = traceback_text
        self.extra = extra or {}
        #: 失败响应里带回来的会话 id（workerd 的 `.with_session()` 在**成功与
        #: 失败两条路上都会附**）。open 失败时它就是唯一的线索：不认领的话，
        #: supervisor 的 sessions / by_hash 里那条记录谁也够不着——refs 停在
        #: 1，只能等超出 max_sessions 时被淘汰，而被挤掉的往往是真正在用的
        #: 那条。
        self.session_id = session_id


def _dev_tree_candidates() -> list[str]:
    """源码树里的构建产物（`cargo build` / `cargo build --release`）。

    路径一律用 `os.path` 拼字符串：`pathlib.Path` 按 `os.name` 分派实现，
    在非目标平台上构造另一半会直接抛 UnsupportedOperation（与 `runtime.py`
    同一条纪律）。
    """
    here = os.path.dirname(os.path.abspath(__file__))       # engine/
    pkg = os.path.dirname(here)                             # magplot/
    src = os.path.dirname(pkg)                              # src/
    root = os.path.dirname(src)                             # 仓库根
    base = os.path.join(root, "workerd", "target")
    # release 优先：开发机上两个都可能在，跑得快的那个才是想要的
    return [os.path.join(base, "release", EXE_NAME),
            os.path.join(base, "debug", EXE_NAME)]


def find_workerd() -> str | None:
    """workerd 可执行文件的路径；显式禁用或找不到回 `None`。

    `MAGPLOT_WORKERD` 是唯一的手动开关：
      * `0` / `off` / `false` / `no`（大小写不敏感）= **禁用**，一律走 Python 池；
      * 其余非空值 = 指定路径（指到不存在的文件同样回 None，并留一条警告——
        用户明确指了路径却静默回退是最难排查的一种失灵）。
    """
    override = (os.environ.get("MAGPLOT_WORKERD") or "").strip()
    if override.lower() in ("0", "off", "false", "no"):
        return None
    if override:
        if os.path.isfile(override):
            return override
        LOG.warning("MAGPLOT_WORKERD 指向的文件不存在，回退到 Python 渲染池: %s",
                    override)
        return None

    candidates: list[str] = []
    if runtime.is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, EXE_NAME))
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(exe_dir, EXE_NAME))
        candidates.append(os.path.join(exe_dir, "_internal", EXE_NAME))
    else:
        candidates += _dev_tree_candidates()
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


class WorkerdClient:
    """一个 workerd 子进程 + 一条按 request_id 分发的收件簿。"""

    def __init__(self, exe: str):
        self.exe = exe
        self._proc: subprocess.Popen | None = None
        # 三把锁，职责不许混：
        #   _lock       —— 只保护 _proc / _pending / _seq 这几个字段，**绝不跨越
        #                  任何阻塞等待**（跨越就会和 reader 线程锁死：reader 要
        #                  拿同一把锁才能把响应投递进来）
        #   _start_lock —— 保证同一时刻只有一个线程在起 / 重启 workerd
        #   _wlock      —— 写端串行，一行必须原子地出去
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._wlock = threading.Lock()
        self._pending: dict[str, dict] = {}
        self._seq = 0
        self._restarts = 0
        self._started_at = 0.0
        self.disabled = False                  # 连续崩溃后本进程放弃 workerd
        # **握过手才算「起来了」**：只看「进程对象还在」会把一个正在退出的
        # 进程当成就绪的 workerd（见 ensure_started 里的注释）
        self._ready = False
        self.hello: dict = {}
        self._log = None

    # ---------------------------------------------------------------- 生命周期
    def _log_path(self) -> str:
        base = config.data_dir() / "cache"
        base.mkdir(parents=True, exist_ok=True)
        return str(base / "workerd.log")

    def ensure_started(self, max_sessions: int = 3, max_queue: int = 32) -> None:
        """确保有一条活着的 workerd；崩过的自动重启（有上限）。

        hello 协商**在 `_lock` 之外**发：它是一次要等响应的往返，而 reader 线程
        投递响应时要拿同一把 `_lock`——持着它去等响应就是一次必然的死锁。
        """
        with self._start_lock:
            with self._lock:
                proc = self._proc
            # 就绪 = **hello 握过手**，不是「进程对象还在」。差别在 Windows 上
            # 是致命的：那儿关一个进程比 POSIX 慢得多，hello 已经失败（写管道
            # 报 EINVAL）而 `poll()` 还是 None，于是下一次 ensure_started 直接
            # 当成「已就绪」返回——重启计数一次都不加，`_MAX_RESTARTS` 永远到
            # 不了，一个起来就崩的二进制就成了无限重启：每次渲染白等一轮
            # spawn + 握手，还永远退不到 Python 池（CI 的 windows-latest 上
            # 6 次尝试后 disabled 仍是 False，tests/test_windows_regressions.py
            # 已把这一幕钉死）。
            if proc is not None and proc.poll() is None and self._ready:
                return
            if self.disabled:
                raise WorkerdError("workerd 已在本次进程内禁用",
                                   code="workerd_unavailable")
            if proc is not None:
                # 半启动的（还活着但没握上手）先收掉：不收就是每重启一次泄漏
                # 一个子进程，而它还占着日志文件与管道
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except OSError:
                        pass
                # 上一条已经不能用了：起得来但活不过 _MIN_UPTIME 就是「起来就崩」
                if time.time() - self._started_at < _MIN_UPTIME:
                    self._restarts += 1
                else:
                    self._restarts = 1
                if self._restarts > _MAX_RESTARTS:
                    self.disabled = True
                    LOG.error("workerd 连续 %d 次起来就崩，本次进程改用 Python "
                              "渲染池（日志见 %s）", self._restarts, self._log_path())
                    raise WorkerdError("workerd 反复崩溃，已停用",
                                       code="workerd_unavailable")
                LOG.warning("workerd 已退出（第 %d 次），重启", self._restarts)

            self._ready = False
            try:
                self._log = open(self._log_path(), "ab", buffering=0)
            except OSError:
                self._log = None
            proc = subprocess.Popen(
                [self.exe],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._log if self._log is not None else subprocess.DEVNULL,
                text=True, bufsize=1,
                # 与 worker 管道同一条纪律：显式 UTF-8。Windows 的默认 stdio 编码
                # 跟随系统区域（cp936），中文 stem / µ / ⁻¹ 一出现就解码失败。
                encoding="utf-8", errors="replace",
                creationflags=runtime.CREATE_NO_WINDOW,
            )
            self._started_at = time.time()
            with self._lock:
                self._proc = proc
            threading.Thread(target=self._read_loop, args=(proc,),
                             daemon=True, name="mm-workerd-read").start()
            LOG.info("workerd 启动: %s（pid=%s）", self.exe, proc.pid)

            self.hello = self._call_on(
                proc, "hello", None, None,
                {"max_sessions": max_sessions, "max_queue": max_queue}, 15.0)
            got = self.hello.get("supervisor_protocol_version")
            if got != SUPERVISOR_PROTOCOL_VERSION:
                raise WorkerdError(
                    f"workerd 说的是 supervisor 协议 v{got}，本版 Magplot 说 "
                    f"v{SUPERVISOR_PROTOCOL_VERSION}",
                    code="protocol_mismatch")
            self._ready = True

    def close(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        self._ready = False
        if proc is None:
            return
        try:
            if proc.poll() is None:
                self._write(proc, {
                    "supervisor_protocol_version": SUPERVISOR_PROTOCOL_VERSION,
                    "request_id": "c-shutdown", "op": "shutdown", "payload": {}})
                proc.wait(timeout=5)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        finally:
            if proc.poll() is None:
                proc.kill()
            if self._log is not None:
                try:
                    self._log.close()
                except OSError:
                    pass
                self._log = None

    # ---------------------------------------------------------------- 读 / 写
    def _read_loop(self, proc: subprocess.Popen) -> None:
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    resp = json.loads(line)
                except ValueError:
                    LOG.warning("workerd 写了非 JSON 的一行，丢弃: %.200s", line)
                    continue
                rid = resp.get("request_id")
                with self._lock:
                    slot = self._pending.pop(rid, None)
                if slot is None:
                    # 已经被放弃的请求（调用方超时走了）迟到了——丢弃即可，
                    # 但要留痕：它同时也是「两边对不上号」的第一手线索。
                    LOG.debug("workerd 的迟到响应，丢弃: %s", rid)
                    continue
                slot["resp"] = resp
                slot["event"].set()
        except (OSError, ValueError):
            pass
        finally:
            self._fail_all("workerd 进程已退出（渲染会话需要重建）")

    def _fail_all(self, message: str) -> None:
        """workerd 没了：**所有等着的调用线程立刻失败**，一个都不许挂死。"""
        with self._lock:
            pending, self._pending = self._pending, {}
        for slot in pending.values():
            slot["resp"] = {
                "ok": False,
                "error": {"code": "workerd_dead", "retryable": True,
                          "message": message, "traceback": ""},
            }
            slot["event"].set()

    def _write(self, proc: subprocess.Popen, req: dict) -> None:
        line = json.dumps(req, ensure_ascii=False) + "\n"
        with self._wlock:
            proc.stdin.write(line)
            proc.stdin.flush()

    def _next_id(self) -> str:
        with self._lock:
            self._seq += 1
            return f"c-{self._seq}"

    # ---------------------------------------------------------------- 调用
    def call(self, op: str, *, session_id: str | None = None,
             stem: str | None = None, payload: dict | None = None,
             timeout: float | None = None, slack: float | None = None) -> dict:
        """发一条请求并等它的响应；失败抛 `WorkerdError`。

        `slack` 是「workerd 自己都卡住了」的兜底余量，退出路径要把它调小：
        进程收尾时多等 30 秒等于让用户盯着一个关不掉的窗口。
        """
        self.ensure_started()
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            raise WorkerdError("workerd 进程不可用", code="workerd_unavailable",
                               retryable=True)
        return self._call_on(proc, op, session_id, stem, payload, timeout, slack)

    def _call_on(self, proc, op, session_id, stem, payload, timeout,
                 slack: float | None = None) -> dict:
        rid = self._next_id()
        req = {
            "supervisor_protocol_version": SUPERVISOR_PROTOCOL_VERSION,
            "request_id": rid,
            "op": op,
            "payload": payload or {},
        }
        if session_id:
            req["session_id"] = session_id
        if stem:
            req["stem"] = stem
        if timeout:
            req["timeout_ms"] = int(timeout * 1000)

        slot = {"event": threading.Event(), "resp": None}
        # 先登记再写：反过来的话响应可能先于登记到达，reader 找不到收件人就把
        # 它当成迟到响应丢了，调用线程于是白等到超时。
        with self._lock:
            self._pending[rid] = slot
        try:
            self._write(proc, req)
        except (OSError, ValueError) as exc:
            with self._lock:
                self._pending.pop(rid, None)
            raise WorkerdError(f"写 workerd 失败: {exc}",
                               code="workerd_unavailable", retryable=True) from exc

        budget = (timeout or 60.0) + (_SLACK_SECONDS if slack is None else slack)
        if not slot["event"].wait(budget):
            with self._lock:
                self._pending.pop(rid, None)
            # workerd 本该自己超时并回一条错误。走到这里说明 workerd 整个卡住了，
            # 与「渲染超时」不是一回事，code 必须分开。
            raise WorkerdError(
                f"workerd 在 {int(budget)} 秒内没有回应（op={op}）",
                code="workerd_unavailable", retryable=True)

        resp = slot["resp"] or {}
        if resp.get("ok"):
            return resp
        err = resp.get("error") or {}
        raise WorkerdError(
            err.get("message") or "workerd 错误",
            code=err.get("code", ""),
            retryable=bool(err.get("retryable")),
            traceback_text=err.get("traceback", ""),
            extra={k: v for k, v in err.items()
                   if k not in ("code", "retryable", "message", "traceback")},
            # **顶层字段**，不在 error 里面：以前这里只拆 `resp["error"]`，
            # 于是 open 失败时调用方永远学不到这条会话的 id。
            session_id=str(resp.get("session_id") or ""),
        )


_client: WorkerdClient | None = None
_client_lock = threading.Lock()


def client() -> WorkerdClient | None:
    """进程内唯一的 workerd 客户端；不可用回 `None`（调用方回退 Python 池）。"""
    global _client
    with _client_lock:
        if _client is not None:
            return None if _client.disabled else _client
        exe = find_workerd()
        if not exe:
            return None
        _client = WorkerdClient(exe)
        return _client


def reset_client() -> None:
    """丢弃当前客户端（测试与「改了设置」用；会把 workerd 一起关掉）。"""
    global _client
    with _client_lock:
        old, _client = _client, None
    if old is not None:
        old.close()
