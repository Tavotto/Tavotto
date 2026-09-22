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

import functools
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

import pytest

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
    # 3.13 起 venv 的启动器叫 venvlauncher.exe（3.12 及之前是 scripts/nt/python.exe）——两个名字都找
    for root in (base, Path(sys.base_prefix)):
        for name in ("venvlauncher.exe", "python.exe"):
            cand = root / "Lib" / "venv" / "scripts" / "nt" / name
            if cand.is_file():
                launcher = cand
                break
        if launcher is not None:
            break
    if launcher is None:
        raise FileNotFoundError(
            "找不到 venvlauncher（Lib/venv/scripts/nt/venvlauncher.exe 或 python.exe）"
        )
    return launcher.read_bytes(), _windows_pyvenv_cfg(base)


def _windows_pyvenv_cfg(home: Path | str) -> bytes:
    """替身的 pyvenv.cfg。**`include-system-site-packages = true`**：POSIX 的替身是 exec 宿主解释器的 sh 包装，
    宿主的 site-packages（matplotlib 在那里）天然可见；Windows 的替身是 venvlauncher 副本，它自己是个「venv」——
    不带这一行时宿主的包一个都看不见，`offline_managed_env` 问替身要 site-packages 挂进新代 → 挂到的是替身自己
    不存在的目录 → 新代 import 不到 matplotlib（#475 d0d331f7 Windows FO24 / FO26 的真根因；此前 14 条
    `managed_env_create_failed` 同源）。带上这一行，两种替身对「宿主的包可见」这件事同一种语义。"""
    return (
        f"home = {home}\ninclude-system-site-packages = true\nversion = {platform.python_version()}\n"
    ).encode("utf-8")


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


@functools.lru_cache(maxsize=1)
def windows_standin_base_probe() -> dict:
    """Windows 上的替身（venvlauncher 副本 + pyvenv.cfg）能不能当建 venv 的 base——**按真依赖的那一步量**：
    `-m venv` 退出 0 且目录齐全还不算，建出来的 venv 里 `Scripts\\python.exe` 得起得来、`-m pip --version` 得退出 0
    （#475 db5f994a：目标腿 runner 上 `-m venv` 退出 0、Include / Lib / Scripts 都在，backend-platforms 上真建受管代
    那步却 `managed_env_create_failed`——目录能建 ≠ 里面的解释器能启动）。回 {"ok", "steps"}，每步带 rc / 输出。"""
    import tempfile

    from tavotto.engine import pool as engine_pool

    # 与用例里的替身同一个宿主（`support.dependency_repair.WORKER_PY` 的取法），否则探的不是同一件事
    try:
        host = engine_pool.find_worker_python() or sys.executable
    except engine_pool.WorkerError:
        host = sys.executable
    steps: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="tavotto-standin-probe-") as tmp:
        root = Path(tmp)
        try:
            exe, cfg = _windows_launcher(host)
        except FileNotFoundError as exc:
            return {"ok": False, "steps": [{"step": "launcher", "error": str(exc)}]}
        (root / "python.exe").write_bytes(exe)
        (root / "pyvenv.cfg").write_bytes(cfg)
        venv = root / "venv"

        def _step(name: str, argv: list[str]) -> bool:
            try:
                out = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=300,
                    stdin=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                steps.append({"step": name, "error": str(exc)})
                return False
            steps.append(
                {"step": name, "rc": out.returncode, "out": (out.stdout + out.stderr)[-600:]}
            )
            return out.returncode == 0

        ok = _step("venv", [str(root / "python.exe"), "-m", "venv", str(venv)])
        vpy = venv / "Scripts" / "python.exe"
        ok = ok and vpy.is_file()
        ok = ok and _step("launch", [str(vpy), "-I", "-c", "import sys; print(sys.prefix)"])
        ok = ok and _step("pip", [str(vpy), "-m", "pip", "--version"])
    return {"ok": ok, "steps": steps}


#: Windows 上的替身是 venvlauncher 副本 + pyvenv.cfg：能被真起（`-I -c` 自报版本）、能被校验；能不能当**建 venv 的
#: base** 因机器而异，而且「目录建得出来」不等于「里面的解释器起得来」——所以这里**按真依赖的那一步探一次**
#: （建 venv → 起它的 python → pip），探不过的机器上「用供应出来的解释器建受管代」的用例 skip-with-reason，探得过就
#: 真跑。#475 那批 Windows 红（db5f994a 的 12 + 2 条 `managed_env_create_failed`）最后查明**不是**替身当不了 base，
#: 是替身的 pyvenv.cfg 没开 `include-system-site-packages`（见 `_windows_pyvenv_cfg`）——探测过了、用例也就真跑了；
#: 探测留着是为了不再把「建不出 venv」和「建出来的用不了」混成一句。探测与真实建代的一致性由
#: `tests/test_private_python.py::test_the_windows_standin_base_probe_matches_reality` 看住。


def standin_base_probe() -> dict:
    """POSIX 上替身是 sh 包装、恒能当 base；Windows 上首次调用才真探（模块 import 不起子进程）。"""
    return {"ok": True, "steps": []} if os.name != "nt" else windows_standin_base_probe()


def standin_can_be_base() -> bool:
    return bool(standin_base_probe()["ok"])


def needs_real_base(fn):
    """装饰器：在**用到时**才探（装饰测试函数那一刻，即测试模块收集期），模块 import 本身不起子进程、
    也不碰 config / pool——#475 ccfad63e 在模块顶层求值，Windows 两片收集期就挂，POSIX 走不到那一支所以本机看不见。"""
    return pytest.mark.skipif(
        not standin_can_be_base(),
        reason="这台 Windows 上的替身（venvlauncher 副本）建出的 venv 起不来，当不了 base；Windows 真链在 private-python-targets 腿（真 pbs）",
    )(fn)
