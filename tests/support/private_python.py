"""私有 Python（U05）用例的装置：本地供应服务 + 假归档。**不联公网、不 mock 产品代码**。

* `LoopbackServer`：`127.0.0.1` 上的 HTTP 服务，按路径回文件；可让它**截断**（少发一段）、
  **篡改**（翻转字节）、**404**、**限速**（给取消 / 并发去重留出时间）。每个请求记一条日志——
  「有没有联网」的判据是这份日志，不是猜。
* `fake_archive()`：与 pbs install_only **同形状**的 tar.gz（顶层 `python/`、`bin/python3`），
  解释器是一个**能真跑**的替身：POSIX 上是 `exec` 宿主解释器的 sh 脚本，Windows 上是
  `venvlauncher` 副本 + `pyvenv.cfg`（与 `venv` 模块建 venv 时的做法相同）。它能 `-m venv`、
  能 `-m pip`，所以 U04 的事务在它上面真跑得通；每次被起来都往 `launches.log` 追加一行——
  「坏 hash 那条路上解释器执行计数为 0」量的就是这个文件。
  `sha256` 由归档真实字节算出，锁里钉的就是它；篡改 / 截断 / 错期望值各自另造。
"""

from __future__ import annotations

import hashlib
import http.server
import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

from tavotto.engine import privatepython

ARCHIVE_NAME = "cpython-test-install_only.tar.gz"


def host_python_version(python: str | None = None) -> str:
    """替身自报的版本 = 它 exec 的那个宿主解释器的版本（锁里就钉这个）。"""
    if python is None or python == sys.executable:
        return platform.python_version()
    out = subprocess.run(
        [python, "-c", "import platform; print(platform.python_version())"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    )
    return out.stdout.strip().splitlines()[-1]


def _launcher_script(host_python: str, launches_log: Path, *, exit_code: int = 0) -> bytes:
    if os.name == "nt":
        raise NotImplementedError("Windows 上用 venvlauncher 副本，见 fake_archive()")
    body = "#!/bin/sh\n"
    body += f"printf 'x\\n' >> '{launches_log}'\n"
    if exit_code:
        body += f"exit {exit_code}\n"
    else:
        body += f'exec "{host_python}" "$@"\n'
    return body.encode("utf-8")


def _windows_launcher(host_python: str) -> tuple[bytes, bytes]:
    """(python.exe 的字节, pyvenv.cfg 的字节)：venv 模块在 Windows 上就是这么造 venv 里的 python.exe 的。"""
    base = Path(host_python).resolve().parent
    launcher = None
    for cand in (
        base / "Lib" / "venv" / "scripts" / "nt" / "python.exe",
        Path(sys.base_prefix) / "Lib" / "venv" / "scripts" / "nt" / "python.exe",
    ):
        if cand.is_file():
            launcher = cand
            break
    if launcher is None:
        raise FileNotFoundError("找不到 venvlauncher（Lib/venv/scripts/nt/python.exe）")
    cfg = f"home = {base}\ninclude-system-site-packages = false\nversion = {platform.python_version()}\n"
    return launcher.read_bytes(), cfg.encode("utf-8")


def fake_archive(
    dest_dir: Path,
    *,
    host_python: str,
    launches_log: Path,
    root: str = "python",
    exit_code: int = 0,
    exec_bit: bool = True,
    extra_members: list[tuple[str, bytes | None, str | None]] | None = None,
) -> tuple[Path, str, str]:
    """造一份假归档，回 (路径, sha256, python_rel)。

    `extra_members` 每项 (名字, 内容或 None, 软链接目标或 None)——给 zip-slip 负例造越界成员。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / ARCHIVE_NAME
    now = int(time.time())
    if os.name == "nt":
        python_rel = "python.exe"
        exe, cfg = _windows_launcher(host_python)
        files: list[tuple[str, bytes, int]] = [
            (f"{root}/python.exe", exe, 0o755),
            (f"{root}/pyvenv.cfg", cfg, 0o644),
        ]
        # Windows 上的执行计数由 sitecustomize 记不了（-I 关掉 site），改由用例读 ledger 与目录判断
    else:
        python_rel = "bin/python3"
        files = [
            (
                f"{root}/bin/python3",
                _launcher_script(host_python, launches_log, exit_code=exit_code),
                0o755 if exec_bit else 0o644,
            ),
            (f"{root}/lib/README", b"fake runtime\n", 0o644),
        ]
    # 让归档大于服务端的一段（64 KiB）：限速 / hold / 截断都要「发了一段、还没发完」这个中间态；
    # 随机字节不被 gzip 压掉
    files.append((f"{root}/lib/padding.bin", os.urandom(256 * 1024), 0o644))
    with tarfile.open(path, "w:gz") as tar:
        for dirname in (root, f"{root}/bin", f"{root}/lib"):
            info = tarfile.TarInfo(dirname)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.mtime = now
            tar.addfile(info)
        for name, data, mode in files:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = mode
            info.mtime = now
            tar.addfile(info, io.BytesIO(data))
        for name, data, link in extra_members or []:
            info = tarfile.TarInfo(name)
            info.mtime = now
            if link is not None:
                info.type = tarfile.SYMTYPE
                info.linkname = link
                tar.addfile(info)
            else:
                info.size = len(data or b"")
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data or b""))
    return path, hashlib.sha256(path.read_bytes()).hexdigest(), python_rel


def source_from(
    archive: Path,
    sha256: str,
    python_rel: str,
    *,
    url: str,
    version: str,
    target: str = "test-host",
    enabled: bool = False,
) -> privatepython.PythonSource:
    return privatepython.PythonSource(
        target=target,
        version=version,
        release="test",
        triple="test-host",
        url=url,
        sha256=sha256,
        size=archive.stat().st_size,
        archive_root="python",
        python_rel=python_rel,
        enabled=enabled,
    )


class LoopbackServer:
    """本地供应服务。`mode`：`ok` / `truncate` / `corrupt` / `missing`；`throttle_bps` 限速。"""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.requests: list[str] = []
        self.mode = "ok"
        self.throttle_bps = 0
        self.truncate_bytes = 4096
        self._server = None
        self._thread = None
        self.gate = threading.Event()  # `mode == "hold"` 时：发完第一段就等它
        self.gate.set()

    @property
    def base(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def url(self, name: str) -> str:
        return f"{self.base}/{name}"

    def __enter__(self):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):  # 静音
                pass

            def do_GET(self):  # noqa: N802
                outer.requests.append(self.path)
                name = self.path.lstrip("/")
                path = outer.directory / name
                if outer.mode == "missing" or not path.is_file():
                    self.send_response(404)
                    self.end_headers()
                    return
                data = path.read_bytes()
                if outer.mode == "corrupt":
                    mid = len(data) // 2
                    data = data[:mid] + bytes([data[mid] ^ 0xFF]) + data[mid + 1 :]
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if outer.mode == "truncate":
                    self.wfile.write(data[: max(0, len(data) - outer.truncate_bytes)])
                    self.wfile.flush()
                    return  # 提前关连接：读侧拿到的是不完整的字节
                chunk = 64 * 1024
                for i in range(0, len(data), chunk):
                    try:
                        self.wfile.write(data[i : i + chunk])
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return
                    if outer.throttle_bps:
                        time.sleep(chunk / outer.throttle_bps)
                    if outer.mode == "hold" and i == 0:
                        outer.gate.wait(60)

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        assert self._server is not None
        self.gate.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=10)


def closed_port_url(name: str = ARCHIVE_NAME) -> str:
    """一个**没人听**的回环端口：连它 = 连接被拒（离线的最小真实形态，不是 mock）。"""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}/{name}"


def snapshot_tree(root: Path) -> set[str]:
    out: set[str] = set()
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        for n in dirnames + filenames:
            out.add((rel / n).as_posix())
    return out


def rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
