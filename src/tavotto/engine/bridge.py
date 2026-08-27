"""native bridge 的**父进程侧**：起用户的 Python、认证、说同一套 worker v1。

技术验证阶段（ADR 0020）。**不是产品 CLI**——`tavotto run` 的产品化在
Session 9，本模块只服务架构决策与 spike 用例。

## 传输：为什么不能照搬 stdin/stdout

safe worker 的协议跑在 stdin/stdout 上，那没问题——它的 stdout 本来就没有
别的用途（用户脚本的 print 被重定向到 stderr）。native bridge 里这条路是
**封死的**：

    print("hello")                 # 用户的 stdout，是他程序的一部分
    input()                        # 用户的 stdin
    print('{"cmd":"render"}')      # 用户完全可以打印出一行合法 JSON

把 stdout 当协议管道 = 用户的每一行输出都是一帧可能被误解的协议数据，
而且用户再也看不到自己的输出（`tavotto run` 的承诺恰恰是"与你自己在终端里
跑这条命令完全等同"）。所以控制通道走 **127.0.0.1 loopback + 一次性 token**，
子进程的 stdout/stderr 原样继承到用户的终端。

**协议语义零改动**：信封由 `pool.build_envelope()` 产出（与 stdin/stdout 那条
是同一个函数），执行侧由 `wireproto` 分派（与 worker.py 是同一个类）。
换的只有字节走哪条管子。

## 认证

* 只 bind `127.0.0.1`（**绝不** 0.0.0.0）；端口 0 让内核分配；
* token = `secrets.token_urlsafe(32)`（256 位），**一次会话一枚**；
* token 走**环境变量**不走 argv——同机上 `ps` 能看到别人的命令行，
  而环境在 macOS/Linux 上默认只有属主读得到；子进程一起来就把它
  从 `os.environ` 摘掉，用户脚本与它起的子进程都看不到；
* 握手帧 token 不符**立刻断开**，并且**继续 accept**——认证失败就关掉监听
  等于给了任何本机进程一个 DoS（抢先连一下，真正的子进程就永远连不上）；
* 握手成功后关闭监听 socket：一次会话只有一条连接。
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import threading
from pathlib import Path

from . import execspec, pool, runtime

HERE = Path(__file__).resolve().parent
RUNNER_PY = HERE / "bridge_runner.py"

#: 与 `bridge_runner` 共享的三个键名（同源：改一处必须改两处，
#: 看护 `tests/bridge/test_bridge_transport.py::test_wire_key_names_match`）。
TOKEN_ENV = "TAVOTTO_BRIDGE_TOKEN"
HELLO_KEY = "bridge_hello"
EVENT_KEY = "bridge_event"

#: 等子进程连回来的上限。用户的 Python 冷启动 + import 科学栈可能很慢，
#: 但 60s 还连不上就不是"慢"而是"起不来"了（多半是解释器路径错了）。
CONNECT_TIMEOUT = 60.0
#: 单条请求的上限（与 pool 的 REQUEST_TIMEOUT 同口径）。
REQUEST_TIMEOUT = pool.REQUEST_TIMEOUT
#: 等屏障（= 用户脚本从头跑到 show() 或跑完）的上限，与 build 同口径。
BARRIER_TIMEOUT = pool.BUILD_TIMEOUT


class BridgeError(RuntimeError):
    def __init__(self, message: str, code: str = "", traceback_text: str = ""):
        super().__init__(message)
        self.code = code
        self.traceback_text = traceback_text


class BridgeSession:
    """一次 native 运行：起进程 → 认证 → 屏障 → v1 请求 → 收尾。

    **不进池**（对照 safe 的 `pool.get()`）：native 会话绑着用户的一次具体
    invocation（cwd/argv/env 都可能不同），复用条件比 safe 复杂得多，而这
    是 spike——先证明单次跑得通。是否进池、键怎么算，留给 Session 9。
    """

    def __init__(
        self,
        spec: execspec.ExecutionSpec,
        *,
        out_dir: str | os.PathLike,
        preview_dpi: int = 200,
        runner_py: str | os.PathLike = RUNNER_PY,
        report: str = "",
        stdout=None,
        stderr=None,
    ):
        if spec.profile != execspec.PROFILE_NATIVE:
            raise ValueError("BridgeSession 只服务 native profile")
        self.spec = spec
        self.out_dir = Path(out_dir)
        self.preview_dpi = preview_dpi
        self.runner_py = runner_py
        self.report = report
        self._stdout = stdout
        self._stderr = stderr
        self.proc: subprocess.Popen | None = None
        self.sock: socket.socket | None = None
        self.rfile = None
        self.rev = 0
        self.generation = 1
        self.events: list[dict] = []
        self.last_build: dict | None = None
        self._closed = False

    # ---------------- 生命周期 ----------------
    def start(self) -> dict:
        """起进程并完成认证；返回握手帧。"""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))  # **只** loopback
        listener.listen(4)
        port = listener.getsockname()[1]
        token = secrets.token_urlsafe(32)

        argv = execspec.bridge_argv(
            self.spec,
            runner_py=self.runner_py,
            out_dir=self.out_dir,
            preview_dpi=self.preview_dpi,
            control_host="127.0.0.1",
            control_port=port,
            report=self.report,
        )
        # 环境：**用户的原样** + 一个 token。native 的定义就是"你自己的
        # 环境就是环境"——不重建 conda/poetry/uv、不清洗 PATH、不动
        # LD_LIBRARY_PATH。token 是 bridge 唯一注入的变量，逐个列名写进
        # ADR 0020 §9，且子进程一起来就摘掉。
        env = {**os.environ, TOKEN_ENV: token}
        self.out_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.proc = subprocess.Popen(
                argv,
                cwd=self.spec.cwd,
                env=env,
                # stdin/stdout/stderr **原样继承**（或由调用方指定）：
                # 用户的 print / input 是他程序的语义，协议不碰它们。
                stdin=None,
                stdout=self._stdout,
                stderr=self._stderr,
                creationflags=runtime.CREATE_NO_WINDOW,
            )
            hello = self._accept(listener, token)
        finally:
            with _suppress_os():
                listener.close()
        return hello

    def _accept(self, listener: socket.socket, token: str) -> dict:
        """接受连接直到**认证通过**的那一条。

        认证失败不停止 accept：本机任何进程都能连上 loopback，一次失败就
        收摊等于把 DoS 送出去。同时盯着子进程——它要是直接死了（解释器路径
        错、runner 报错），我们不该白等满 60 秒。
        """
        deadline = _now() + CONNECT_TIMEOUT
        rejected = 0
        while True:
            remaining = deadline - _now()
            if remaining <= 0:
                raise BridgeError(
                    f"用户的 Python 在 {CONNECT_TIMEOUT:.0f}s 内没有连回控制通道"
                    f"（拒绝了 {rejected} 条未认证连接）",
                    code="bridge_connect_timeout",
                )
            listener.settimeout(min(0.5, remaining))
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                if self.proc is not None and self.proc.poll() is not None:
                    raise BridgeError(
                        f"用户的 Python 还没连上控制通道就退出了"
                        f"（退出码 {self.proc.returncode}）",
                        code="bridge_child_exited",
                    ) from None
                continue
            conn.settimeout(10.0)
            rfile = conn.makefile("r", encoding="utf-8", newline="\n")
            try:
                line = rfile.readline()
                hello = json.loads(line) if line else {}
            except (OSError, ValueError):
                hello = {}
            claimed = hello.get("token") if isinstance(hello, dict) else None
            # `compare_digest`：token 比对不给计时旁路留缝。本机 loopback 上
            # 这条威胁很弱，但"认证比对用常数时间"是没有理由不遵守的默认。
            if not isinstance(claimed, str) or not secrets.compare_digest(claimed, token):
                rejected += 1
                with _suppress_os():
                    conn.sendall(b'{"ok":false,"code":"bad_token"}\n')
                with _suppress_os():
                    rfile.close()
                with _suppress_os():
                    conn.close()
                continue
            conn.sendall(b'{"' + HELLO_KEY.encode() + b'":1,"ok":true}\n')
            conn.settimeout(None)
            self.sock = conn
            self.rfile = rfile
            hello.pop("token", None)  # token 不进任何可能被打日志的结构
            return hello

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with _suppress_os():
            if self.rfile is not None:
                self.rfile.close()
        with _suppress_os():
            if self.sock is not None:
                self.sock.close()
        if self.proc is not None and self.proc.poll() is None:
            with _suppress_os():
                self.proc.terminate()
            try:
                self.proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                with _suppress_os():
                    self.proc.kill()
                with _suppress_os():
                    self.proc.wait(timeout=5.0)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ---------------- 收发 ----------------
    def _readline(self, timeout: float) -> str:
        """带超时读一行。用「读线程 + join」而不是 select：与 pool 的
        `_readline` 同一条纪律（Windows 的 select 对管道不成立，两条路径
        的超时语义必须一致）。超时不杀进程——native 的进程是**用户的**，
        杀它等于杀他的脚本；抛错让调用方决定。"""
        box: list[str] = []

        def read() -> None:
            try:
                box.append(self.rfile.readline())
            except (OSError, ValueError):
                box.append("")

        t = threading.Thread(target=read, daemon=True, name="tavotto-bridge-read")
        t.start()
        t.join(timeout)
        if t.is_alive():
            raise BridgeError(
                f"等 native bridge 响应超时（{int(timeout)}s）", code="bridge_timeout"
            )
        return box[0] if box else ""

    def _next_frame(self, timeout: float) -> dict:
        line = self._readline(timeout)
        if not line:
            code = None if self.proc is None else self.proc.poll()
            raise BridgeError(
                f"native bridge 连接已关闭（子进程退出码 {code}）", code="bridge_closed"
            )
        return json.loads(line)

    def wait_event(self, name: str, timeout: float = BARRIER_TIMEOUT) -> dict:
        """等一个带外事件（barrier / released / exit / shutdown）。

        路上遇到的响应帧是不该出现的（我们串行发请求），当场报错而不是
        丢掉——静默丢帧会让下一条请求对上错误的响应。
        """
        for ev in self.events:
            if ev.get(EVENT_KEY) == name:
                self.events.remove(ev)
                return ev
        while True:
            frame = self._next_frame(timeout)
            if EVENT_KEY not in frame:
                raise BridgeError(
                    f"等事件 {name} 时收到一条响应帧: {frame.get('request_id')!r}",
                    code="bridge_desync",
                )
            if frame.get(EVENT_KEY) == name:
                return frame
            self.events.append(frame)

    def request(self, obj: dict, timeout: float = REQUEST_TIMEOUT) -> dict:
        """发一条 v1 请求，返回响应体。

        信封由 `pool.build_envelope()` 产出——**与 stdin/stdout 那条控制面
        是同一个函数**。回显校验也是同一条纪律：对不上就说明会话错位了，
        继续用下去用户会看到 A 图的 manifest 落在 B 图上。
        """
        if self.sock is None:
            raise BridgeError("控制通道还没建立（先 start()）", code="bridge_not_started")
        env = pool.build_envelope(obj, generation=self.generation, revision=self.rev)
        rid = env["request_id"]
        self.sock.sendall((json.dumps(env, ensure_ascii=False) + "\n").encode("utf-8"))
        while True:
            frame = self._next_frame(timeout)
            if EVENT_KEY in frame:
                self.events.append(frame)
                continue
            break
        got, ver = frame.get("request_id"), frame.get("protocol_version")
        if got != rid or ver != pool.PROTOCOL_VERSION:
            raise BridgeError(
                f"native bridge 协议错乱（request_id={got!r} 期待 {rid!r}，"
                f"protocol_version={ver!r}）",
                code="protocol_mismatch",
            )
        if not frame.get("ok"):
            err = frame.get("error") or {}
            raise BridgeError(
                err.get("message") or "native bridge 错误",
                code=err.get("code", ""),
                traceback_text=err.get("traceback", ""),
            )
        return frame

    # ---------------- 高层动作（与 EngineWorker 同名同义） ----------------
    def ensure_built(self) -> dict:
        resp = self.request({"cmd": "build"}, BARRIER_TIMEOUT)
        self.last_build = resp
        return resp

    def override(self, stem: str, patches: list, **kw) -> dict:
        self.rev += 1
        return self.request({"cmd": "override", "stem": stem, "patches": patches, **kw})

    def export(self, stem: str, patches: list, path: str, fmt: str = "pdf", dpi: int = 600) -> dict:
        return self.request(
            {
                "cmd": "export",
                "stem": stem,
                "patches": patches,
                "path": path,
                "format": fmt,
                "dpi": dpi,
            },
            BARRIER_TIMEOUT,
        )

    def render_png(self, stem: str, width_px: int) -> dict:
        return self.request({"cmd": "render_png", "stem": stem, "width": int(width_px)})

    def resume(self) -> dict:
        """放开屏障，让用户脚本接着往下跑（native 独有的那一条命令）。"""
        return self.request({"cmd": "continue"})

    def shutdown(self) -> None:
        with _suppress_os():
            if self.sock is not None:
                env = pool.build_envelope({"cmd": "shutdown"})
                self.sock.sendall((json.dumps(env) + "\n").encode("utf-8"))
        self.close()

    def svg_path(self, stem: str) -> Path:
        return self.out_dir / f"{stem}.svg"


class _suppress_os:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, (OSError, ValueError))


def _now() -> float:
    import time

    return time.monotonic()


def resolve_interpreter(explicit: str = "") -> str:
    """native 用哪个解释器：**用户 invocation 里那一个**，绝不静默替换。

    没有显式给出时用**当前 shell 里的 `python`**——`conda activate paper`
    之后 `tavotto run python fig.py` 里那个 `python` 就该是 paper 环境的
    那一个。Tavotto **不重建** conda / poetry / uv 的激活（ADR 0020 §9）：
    重建等于第二份环境解析实现，而它必然与真正的激活有出入。

    找不到就抛——**绝不回退到 Tavotto 自己的解释器**。静默换一个解释器是
    native 档最不该有的行为：用户看到的是"跑起来了但缺包 / 结果不对"。
    """
    import shutil

    if explicit:
        found = shutil.which(explicit) or (explicit if os.path.isfile(explicit) else "")
        if not found:
            raise BridgeError(f"找不到解释器: {explicit}", code="interpreter_not_found")
        return os.path.abspath(found)
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return os.path.abspath(found)
    raise BridgeError(
        "当前 shell 里找不到 python / python3。native 模式绝不替你挑一个解释器"
        "——请先激活你的环境，或显式给出解释器路径。",
        code="interpreter_not_found",
    )


def spec_for(
    raw_target: str,
    *,
    interpreter: str,
    argv: tuple[str, ...] = (),
    target_kind: str = execspec.TARGET_SCRIPT,
    cwd: str = "",
    project_root: str = "",
) -> execspec.ExecutionSpec:
    """把一次调用化成 native ExecutionSpec（默认值只写在这里）。

    * `cwd` 缺省 = **当前 cwd**（继承，不是沙盒）；
    * `project_root` 缺省：script 取脚本所在目录、module 取 cwd。它只决定
      描述符里那条项目相对路径，不影响执行语义。
    """
    cwd = cwd or os.getcwd()
    if not project_root:
        project_root = (
            os.path.dirname(os.path.abspath(raw_target))
            if target_kind == execspec.TARGET_SCRIPT
            else cwd
        )
    return execspec.native_spec(
        raw_target,
        interpreter=interpreter,
        cwd=cwd,
        project_root=project_root,
        target_kind=target_kind,
        argv=argv,
    )
