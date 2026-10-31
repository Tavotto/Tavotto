"""透明的 MCP stdio 记录器（Windows Lab `hosts-live`；规则 docs/rules/ci/windows-lab.md「AI 宿主分层」）。

    python mcp_recorder.py <记录文件.jsonl> [--host-root <桌面宿主安装目录>] -- <真正的 MCP server 命令…>

宿主（Claude Code / Codex / CodeBuddy …）以为自己在跟 Tavotto MCP server 说话——确实是：本进程把
stdin/stdout 的字节原样转发给子进程，一个字节都不改。旁路只记**服务端事实**，供各宿主同一套判定：

- 宿主发了哪些方法（`initialize` / `tools/list` / `tools/call`）、调的是哪个工具；
- 每个响应是否出错（JSON-RPC `error` 或 MCP `result.isError`）、`tools/list` 列出了哪些 Tavotto 工具；
- 判 L5 要用的几个有界字段：`session_id`、`patch_hash`、`ok`。

参数与结果正文**不记**（路径、图内文字、SVG 都不进记录），只截上面这几个字段。

桌面宿主（`desktop-live`）还要证明「这条流量确实是桌面宿主里的扩展发的」：首个 `initialize` 的记录里多带
`client`（clientInfo 的 name / version）与 `ancestry`（本进程往上的父进程**映像名**，最多 `MAX_ANCESTRY` 级），
给了 `--host-root` 时再带 `host_ancestor`（祖先里最近一个映像落在该目录下的路径，没有就是 null）。
只记映像名与那一条路径，不记命令行参数。
MCP stdio 帧是按行分隔的 JSON-RPC；解析不了的行照样转发、只记一条 `unparsed`。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import threading
import time

# 仓库级约定：入口钉 UTF-8（本文件只写二进制流与 utf-8 记录文件，钉住只是为 stderr 报错）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

#: 父进程链最多往上走几级：够穿过 启动器 / 扩展宿主 / 随包的 claude / python 启动器，又不会无界
MAX_ANCESTRY = 12
#: 写进记录的映像名最多几级（lead 约定：有界）
MAX_RECORDED_NAMES = 8


def _win_process_chain(limit: int = MAX_ANCESTRY) -> list[tuple[str, str]]:
    """本进程往上的祖先 [(映像名, 映像完整路径)]，近的在前。非 Windows 返回空。
    父进程先退出后 pid 可能被复用：祖先的创建时间必须早于孩子的，否则链到此为止。"""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

    class Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    parent_of: dict[int, int] = {}
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return []
    try:
        e = Entry()
        e.dwSize = ctypes.sizeof(e)
        ok = k32.Process32FirstW(wintypes.HANDLE(snap), ctypes.byref(e))
        while ok:
            parent_of[e.th32ProcessID] = e.th32ParentProcessID
            ok = k32.Process32NextW(wintypes.HANDLE(snap), ctypes.byref(e))
    finally:
        k32.CloseHandle(wintypes.HANDLE(snap))

    def info(pid: int) -> tuple[str, int] | None:
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not k32.GetProcessTimes(h, *(ctypes.byref(t) for t in times)):
                return None
            created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            return buf.value, created
        finally:
            k32.CloseHandle(h)

    chain: list[tuple[str, str]] = []
    me = info(os.getpid())
    child_created = me[1] if me else None
    pid = parent_of.get(os.getpid(), 0)
    seen = {os.getpid()}
    while pid and pid not in seen and len(chain) < limit:
        seen.add(pid)
        got = info(pid)
        if got is None or (child_created is not None and got[1] > child_created):
            break
        path, child_created = got
        chain.append((os.path.basename(path), path))
        pid = parent_of.get(pid, 0)
    return chain


def _under(path: str, root: str) -> bool:
    p = os.path.normcase(os.path.normpath(path))
    r = os.path.normcase(os.path.normpath(root)).rstrip("\\/")
    return p == r or p.startswith(r + os.sep)


def provenance(chain: list[tuple[str, str]], host_root: str | None) -> dict:
    """父进程链 → 记录里的 ancestry / host_ancestor（只有映像名与那一条宿主路径）。"""
    out: dict = {"ancestry": [name for name, _ in chain][:MAX_RECORDED_NAMES]}
    if host_root:
        out["host_ancestor"] = next((path for _, path in chain if _under(path, host_root)), None)
    return out


def _client_info(params: object) -> dict:
    info = (params or {}).get("clientInfo") if isinstance(params, dict) else None
    if not isinstance(info, dict):
        return {}
    return {k: str(info.get(k, ""))[:64] for k in ("name", "version") if k in info}


_FIELDS = re.compile(rb'"(session_id|patch_hash|ok)"\s*:\s*("(?:[^"\\]|\\.){0,100}"|true|false)')


def _facts(text: bytes) -> dict:
    out = {}
    for k, v in _FIELDS.findall(text[:200_000]):
        key = k.decode()
        if key not in out:
            out[key] = json.loads(v)
    return out


class Recorder:
    def __init__(self, path: str, host_root: str | None = None, chain=_win_process_chain):
        self._f = open(path, "a", encoding="utf-8")  # noqa: SIM115 — 进程期内一直开着
        self._lock = threading.Lock()
        self._pending: dict = {}
        self._host_root = host_root
        self._chain = chain
        self._initialized = False

    def write(self, rec: dict) -> None:
        rec["t"] = round(time.time(), 3)
        with self._lock:
            self._f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._f.flush()

    def from_host(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            self.write({"dir": "host", "unparsed": True})
            return
        if not isinstance(msg, dict) or "method" not in msg:
            return
        tool = (msg.get("params") or {}).get("name") if msg["method"] == "tools/call" else None
        if "id" in msg:
            self._pending[msg["id"]] = (msg["method"], tool)
        rec = {"dir": "host", "method": msg["method"], "tool": tool}
        if msg["method"] == "initialize" and not self._initialized:
            self._initialized = True
            rec["client"] = _client_info(msg.get("params"))
            try:
                rec.update(provenance(self._chain(), self._host_root))
            except Exception:  # noqa: BLE001 — 取不到进程链只是少一条证据，不许打断通信
                rec["ancestry"] = []
        self.write(rec)

    def from_server(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            self.write({"dir": "server", "unparsed": True})
            return
        if not isinstance(msg, dict) or "id" not in msg or msg["id"] not in self._pending:
            return
        method, tool = self._pending.pop(msg["id"])
        res = msg.get("result") if isinstance(msg.get("result"), dict) else {}
        rec = {
            "dir": "server",
            "method": method,
            "tool": tool,
            "error": "error" in msg or bool(res.get("isError")),
        }
        if method == "tools/list":
            rec["tools"] = sorted(
                str(t.get("name")) for t in res.get("tools") or [] if isinstance(t, dict)
            )
        if method == "tools/call":
            rec["facts"] = _facts(line)
        self.write(rec)


def _pump(src, dst, observe) -> None:
    for line in iter(src.readline, b""):
        try:  # 先登记请求 id 再转发：server 回得快时 from_server 才认得出这条回复
            observe(line)
        except Exception:  # noqa: BLE001 — 记录器出错不许打断宿主与 server 的通信
            pass
        dst.write(line)
        dst.flush()
    try:
        dst.close()
    except OSError:
        pass


def _tail(path: str, child: subprocess.Popen, dst, observe) -> None:
    """server 的 stdout 落在文件里（不开管道：写满 64 KiB 卡死的那类问题根本不存在），这里边写边读、
    按整行原样转给宿主。子进程退出且读到文件末尾才停。"""
    buf = b""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line += b"\n"
                    dst.write(line)
                    dst.flush()
                    try:
                        observe(line)
                    except Exception:  # noqa: BLE001
                        pass
                continue
            if child.poll() is not None:
                if buf:
                    dst.write(buf)
                    dst.flush()
                return
            time.sleep(0.005)


def main(argv: list[str]) -> int:
    host_root = None
    if len(argv) >= 5 and argv[1] == "--host-root":
        host_root, argv = argv[2], [argv[0], *argv[3:]]
    if len(argv) < 3 or argv[1] != "--":
        sys.stderr.write("usage: mcp_recorder.py LOG.jsonl [--host-root DIR] -- COMMAND...\n")
        return 2
    rec = Recorder(argv[0], host_root)
    rec.write({"event": "start"})
    out_path = argv[0] + f".server-stdout.{os.getpid()}"
    with open(out_path, "wb") as out:
        child = subprocess.Popen(argv[2:], stdin=subprocess.PIPE, stdout=out)
    up = threading.Thread(
        target=_pump, args=(sys.stdin.buffer, child.stdin, rec.from_host), daemon=True
    )
    up.start()
    _tail(out_path, child, sys.stdout.buffer, rec.from_server)
    rc = child.wait()
    rec.write({"event": "exit", "code": rc})
    with contextlib.suppress(OSError):
        os.unlink(out_path)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
