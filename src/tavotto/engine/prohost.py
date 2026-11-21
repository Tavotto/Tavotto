"""高级版闭源组件的宿主（ADR 0198）：找到组件、拉起独立进程、按 pro 协议 v1 收发。

本模块只做**进程与协议**，不做权益判断（`license.has` 只在各高级功能的入口模块里调用，
`tests/test_license_callsites.py` 看护）。

本版只实现两种来源：

* 开发 / 验收：`TAVOTTO_PRO_DIR=<本地目录>`，**只在设了 `TAVOTTO_LICENSE_ENDPOINT` 时认**（正式安装里设了也无效）。
  目录里有 `tavotto_pro/__main__.py` 就用当前解释器 `-m tavotto_pro` 跑；有 `tavotto-pro`（Windows `tavotto-pro.exe`）
  可执行文件就直接跑。
* 已安装：`config.data_dir()/pro/current.json` 指向的版本目录（下载、验签、安装、回退由后续 PR 实现，ADR 0198 §五–§七）。

失败一律折成 `ProUnavailable`，调用方把它当作「这个高级功能暂时不可用」，不把组件的原话给用户看。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import config, license as engine_license, runtime

LOG = logging.getLogger("tavotto.prohost")

ENV_PRO_DIR = "TAVOTTO_PRO_DIR"
PROTOCOL_VERSION = 1
#: 宿主认得的 pro 协议版本（ADR 0198 §三）。
SUPPORTED_PRO_API = frozenset({1})
#: 协议失败码闭集（ADR 0198 §三）。
FAILURES = frozenset({"bad_request", "unsupported", "license", "timeout", "internal"})

HELLO_TIMEOUT_S = 10.0
REQUEST_TIMEOUT_S = 30.0
IDLE_EXIT_S = 600.0
MAX_CRASHES = 3
MAX_LINE_BYTES = 8 * 1024 * 1024
DIR_NAME = "pro"


class ProUnavailable(Exception):
    """组件不存在 / 起不来 / 协议不对 / 超时 / 回了失败。`code` ∈ `FAILURES` ∪ {"missing", "crashed"}。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def _dev_dir() -> Path | None:
    raw = os.environ.get(ENV_PRO_DIR, "").strip()
    if not raw:
        return None
    # 只在开发 / 验收模式（设了合规的授权测试端点）认，ADR 0198 §八
    if not os.environ.get(engine_license.ENV_ENDPOINT, "").strip():
        return None
    if engine_license.resolve_target() is None:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def _installed_dir() -> Path | None:
    try:
        rec = json.loads(config.data_path(DIR_NAME, "current.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = rec.get("current") if isinstance(rec, dict) else None
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    p = config.data_path(DIR_NAME, name)
    return p if p.is_dir() else None


def _command(root: Path) -> tuple[list[str], dict] | None:
    exe = root / ("tavotto-pro.exe" if sys.platform == "win32" else "tavotto-pro")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TAVOTTO_DATA_DIR"] = str(config.data_dir())
    if exe.is_file():
        return [str(exe)], env
    if (root / "tavotto_pro" / "__main__.py").is_file():
        env["PYTHONPATH"] = str(root)
        return [sys.executable, "-m", "tavotto_pro"], env
    return None


def locate() -> Path | None:
    """此刻要用的组件目录（开发目录优先）；没有 = None。只读本机文件，不联网。"""
    return _dev_dir() or _installed_dir()


class _Sidecar:
    def __init__(self, root: Path) -> None:
        cmd = _command(root)
        if cmd is None:
            raise ProUnavailable("missing", "no entry point")
        argv, env = cmd
        try:
            self.proc = subprocess.Popen(  # noqa: S603 — argv 来自本机组件目录，不经 shell
                argv,
                cwd=str(root),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # 桌面版后台起的隐藏子进程：Windows 上不弹黑框
                creationflags=runtime.CREATE_NO_WINDOW,
            )
        except OSError as exc:
            raise ProUnavailable("missing", str(exc)) from None
        self.root = root
        self.last_used = time.monotonic()
        self._stderr = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr.start()
        hello = self.request("hello", {}, timeout=HELLO_TIMEOUT_S)
        api = hello.get("pro_api")
        if api not in SUPPORTED_PRO_API:
            self.close()
            raise ProUnavailable("unsupported", f"pro_api {api!r}")
        feats = hello.get("features")
        self.features = (
            frozenset(f for f in feats if isinstance(f, str))
            if isinstance(feats, list)
            else frozenset()
        )
        self.version = str(hello.get("pro_version") or "")

    def _drain_stderr(self) -> None:
        assert self.proc.stderr is not None
        for raw in self.proc.stderr:
            LOG.debug("pro: %s", raw[:500].decode("utf-8", "replace").rstrip())

    def alive(self) -> bool:
        return self.proc.poll() is None

    def request(self, cmd: str, payload: dict, *, timeout: float = REQUEST_TIMEOUT_S) -> dict:
        rid = uuid.uuid4().hex
        line = json.dumps(
            {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": rid,
                "cmd": cmd,
                "payload": payload,
            },
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        assert self.proc.stdin is not None and self.proc.stdout is not None
        box: dict = {}

        # 写入 + 读回整个往返共用一个截止时间：子进程不读 stdin 时 write/flush 会堵在管道上，
        # 所以写也放进带超时的线程里，到点 close() 杀掉并回收子进程（同时解除堵塞的写）。
        def exchange() -> None:
            try:
                self.proc.stdin.write(line + b"\n")  # type: ignore[union-attr]
                self.proc.stdin.flush()  # type: ignore[union-attr]
            except (OSError, ValueError):
                box["stdin_closed"] = True
                return
            try:
                box["raw"] = self.proc.stdout.readline(MAX_LINE_BYTES)  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                box["err"] = exc

        t = threading.Thread(target=exchange, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            # 先杀并回收子进程：堵在 write 上的线程持着 stdin 的缓冲锁，先 close() 会跟它一起死锁
            self.proc.kill()
            self.proc.wait()
            t.join(2)
            self.close()
            raise ProUnavailable("timeout", cmd)
        if box.get("stdin_closed"):
            raise ProUnavailable("crashed", "stdin closed")
        raw = box.get("raw") or b""
        if not raw:
            raise ProUnavailable("crashed", "no response")
        try:
            resp = json.loads(raw.decode("utf-8"))
        except ValueError:
            self.close()
            raise ProUnavailable("internal", "bad json") from None
        if not isinstance(resp, dict) or resp.get("request_id") != rid:
            self.close()
            raise ProUnavailable("internal", "request_id mismatch")
        self.last_used = time.monotonic()
        if resp.get("ok") is not True:
            code = resp.get("code")
            raise ProUnavailable(
                code if code in FAILURES else "internal", str(resp.get("message") or "")[:200]
            )
        result = resp.get("result")
        if not isinstance(result, dict):
            raise ProUnavailable("internal", "result not an object")
        return result

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()  # 只结束自己拉起的这个 PID


_LOCK = threading.Lock()
_current: _Sidecar | None = None
_crashes = 0


def call(cmd: str, payload: dict, *, feature: str, timeout: float = REQUEST_TIMEOUT_S) -> dict:
    """向组件发一条命令（必要时拉起）。调用方必须**已经**用 `license.has(feature)` 判过。"""
    global _current, _crashes
    with _LOCK:
        if _crashes >= MAX_CRASHES:
            raise ProUnavailable("crashed", "disabled for this session")
        if _current is not None and (
            not _current.alive() or time.monotonic() - _current.last_used > IDLE_EXIT_S
        ):
            _current.close()
            _current = None
        if _current is None:
            root = locate()
            if root is None:
                raise ProUnavailable("missing")
            try:
                _current = _Sidecar(root)
            except ProUnavailable as exc:
                if exc.code in ("crashed", "timeout", "internal"):
                    _crashes += 1
                raise
        if feature not in _current.features:
            raise ProUnavailable("unsupported", feature)
        try:
            return _current.request(cmd, payload, timeout=timeout)
        except ProUnavailable as exc:
            if exc.code in ("crashed", "timeout", "internal"):
                _crashes += 1
                _current.close()
                _current = None
            raise


def available(feature: str) -> bool:
    """本机有没有提供这个功能的组件（只看文件，不拉起进程）。"""
    return locate() is not None


def shutdown() -> None:
    global _current
    with _LOCK:
        if _current is not None:
            _current.close()
            _current = None
