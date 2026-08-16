"""Worker 池（Flask 父进程侧，纯标准库——.venv 里没有 matplotlib）。

每个脚本一个常驻子进程；Phase 0 为同步阻塞版（fig9 秒级），
Phase 1 再加 LRU / 防抖 / 异步 SSE。
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config

LOG = logging.getLogger("mm.engine")

ENGINE_CACHE = config.data_dir() / "cache" / "engine"
WORKER_PY = Path(__file__).resolve().parent / "worker.py"
MAX_ALIVE = 3  # 同时存活的 worker 会话数（每个都端着整套 Figure 的内存）

_worker_python: str | None = None
_workers: dict[str, "EngineWorker"] = {}
_lock = threading.Lock()


class WorkerError(RuntimeError):
    def __init__(self, message: str, traceback_text: str = ""):
        super().__init__(message)
        self.traceback_text = traceback_text


def _candidate_pythons() -> list[str | None]:
    """按优先级列出可能装了 matplotlib 的解释器（跨平台）。

    sys.executable 排在环境变量之后、系统路径之前：`pip install magplot[worker]`
    这种单环境安装里，跑 Flask 的解释器自己就带科学栈，无需再探测。
    """
    import os
    import sys
    cands: list[str | None] = [os.environ.get("MM_WORKER_PYTHON"), sys.executable]
    if os.name == "nt":
        cands += [shutil.which("python"), shutil.which("python3")]
    else:
        cands += [
            "/opt/homebrew/opt/python@3.13/libexec/bin/python3",  # macOS Homebrew
            "/opt/homebrew/bin/python3",
            shutil.which("python3"),
            "/usr/bin/python3",
        ]
    return cands


def find_worker_python() -> str:
    """找一个装了 matplotlib 的解释器（Flask 自己的 .venv 可能没有）。"""
    global _worker_python
    if _worker_python:
        return _worker_python
    seen: set[str] = set()
    for cand in _candidate_pythons():
        if not cand or cand in seen or not Path(cand).exists():
            continue
        seen.add(cand)   # 同一个解释器不重复探测（每次探测最多 30s）
        probe = subprocess.run([cand, "-c", "import matplotlib"],
                               capture_output=True, timeout=30)
        if probe.returncode == 0:
            _worker_python = cand
            return cand
    raise WorkerError(
        "找不到装有 matplotlib 的 python：请 `pip install magplot[worker]`，"
        "或用环境变量 MM_WORKER_PYTHON 指定一个带科学栈的解释器")


class EngineWorker:
    def __init__(self, script_name: str, figures_dir: str, entry: str):
        self.script_name = script_name
        self.entry = entry
        base = ENGINE_CACHE / Path(script_name).stem
        self.out_dir = base / "out"
        self.sandbox = base / "sandbox"
        self.log_path = base / "worker.log"
        base.mkdir(parents=True, exist_ok=True)
        self.rev = 0                       # 每次 override 递增，用于前端缓存穿透
        self.lock = threading.Lock()
        self.built = False
        self.last_used = time.time()
        self._log = open(self.log_path, "ab", buffering=0)
        LOG.info("worker 启动: %s（entry=%s）", script_name, entry)
        self.proc = subprocess.Popen(
            [find_worker_python(), str(WORKER_PY),
             "--script", str(Path(figures_dir) / script_name),
             "--figures-dir", figures_dir,
             "--out-dir", str(self.out_dir),
             "--sandbox", str(self.sandbox),
             "--entry", entry],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log,
            text=True, bufsize=1,
            # 显式 UTF-8：text=True 默认跟随系统区域编码，Windows 上是 cp1252/
            # cp936，读 worker 回来的中文/µ/⁻¹ 会解码失败。worker 侧同样钉死。
            encoding="utf-8", errors="replace",
        )

    def alive(self) -> bool:
        return self.proc.poll() is None

    def _log_tail(self, n: int = 30) -> str:
        try:
            lines = self.log_path.read_text(errors="replace").splitlines()
            return "\n".join(lines[-n:])
        except OSError:
            return ""

    def request(self, obj: dict) -> dict:
        self.last_used = time.time()
        with self.lock:
            if not self.alive():
                raise WorkerError("worker 进程已退出", self._log_tail())
            self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
        if not line:
            raise WorkerError("worker 进程崩溃（无响应）", self._log_tail())
        resp = json.loads(line)
        if not resp.get("ok"):
            raise WorkerError(resp.get("error", "worker 错误"),
                              resp.get("traceback", ""))
        return resp

    def ensure_built(self) -> dict:
        resp = self.request({"cmd": "build"})
        self.built = True
        return resp

    def override(self, stem: str, patches: list) -> dict:
        if not self.built:
            self.ensure_built()
        resp = self.request({"cmd": "override", "stem": stem, "patches": patches})
        self.rev += 1
        return resp

    def export(self, stem: str, patches: list, path: str,
               fmt: str = "pdf", dpi: int = 600) -> dict:
        if not self.built:
            self.ensure_built()
        return self.request({"cmd": "export", "stem": stem, "patches": patches,
                             "path": path, "format": fmt, "dpi": dpi})

    def svg_path(self, stem: str) -> Path:
        return self.out_dir / f"{stem}.svg"

    def render_png(self, stem: str, width_px: int) -> Path:
        if not self.built:
            self.ensure_built()
        resp = self.request({"cmd": "render_png", "stem": stem, "width": width_px})
        return Path(resp["path"])

    def preview_png(self, stem: str, patches: list, width_px: int, tag: str) -> Path:
        if not self.built:
            self.ensure_built()
        resp = self.request({"cmd": "preview_png", "stem": stem, "patches": patches,
                             "width": width_px, "tag": tag})
        return Path(resp["path"])

    def shutdown(self) -> None:
        try:
            if self.alive():
                self.request({"cmd": "shutdown"})
        except (WorkerError, OSError):
            pass
        finally:
            if self.alive():
                self.proc.kill()
            self._log.close()


def get(script_name: str, figures_dir: str, entry: str) -> EngineWorker:
    """取（或重建）某脚本的 worker；崩溃的自动换新；超出 MAX_ALIVE 按 LRU 淘汰。"""
    with _lock:
        w = _workers.get(script_name)
        if w is not None and not w.alive():
            LOG.warning("worker 已死，重建: %s", script_name)
            w.shutdown()
            w = None
        if w is None:
            w = EngineWorker(script_name, figures_dir, entry)
            _workers[script_name] = w
        w.last_used = time.time()
        alive = [x for x in _workers.values() if x.alive()]
        if len(alive) > MAX_ALIVE:
            for victim in sorted(alive, key=lambda x: x.last_used)[: len(alive) - MAX_ALIVE]:
                if victim is not w:
                    LOG.info("worker LRU 淘汰: %s", victim.script_name)
                    _workers.pop(victim.script_name, None)
                    threading.Thread(target=victim.shutdown, daemon=True).start()
        return w


def invalidate(script_name: str) -> None:
    """脚本文件变更后作废其会话（下次请求自动重建）。"""
    with _lock:
        w = _workers.pop(script_name, None)
    if w is not None:
        threading.Thread(target=w.shutdown, daemon=True).start()


def shutdown_all() -> None:
    """关闭全部 worker（项目切换 / 进程退出前）。异步优雅关停 + 兜底 kill。"""
    with _lock:
        victims = list(_workers.values())
        _workers.clear()
    for w in victims:
        threading.Thread(target=w.shutdown, daemon=True).start()


_watcher_stop: threading.Event | None = None


def stop_watcher() -> None:
    """停掉当前 watcher 线程（项目切换时必须先停旧的，否则旧目录被继续轮询）。"""
    global _watcher_stop
    if _watcher_stop is not None:
        _watcher_stop.set()
        _watcher_stop = None


def start_watcher(figures_dir: str, scripts: list[str], on_change, interval: float = 2.0) -> None:
    """轮询脚本 mtime；变更即作废会话并回调（paper_style 变更作废全部）。
    重复调用自动替换旧 watcher。"""
    global _watcher_stop
    stop_watcher()
    stop = threading.Event()
    _watcher_stop = stop
    fig_dir = Path(figures_dir)
    tracked: dict[str, float | None] = {}
    for s in [*scripts, "paper_style.py"]:
        try:
            tracked[s] = (fig_dir / s).stat().st_mtime
        except OSError:
            tracked[s] = None

    def loop():
        while not stop.wait(interval):
            changed = []
            for s in tracked:
                try:
                    mt = (fig_dir / s).stat().st_mtime
                except OSError:
                    continue
                if tracked[s] is not None and mt != tracked[s]:
                    changed.append(s)
                tracked[s] = mt
            if not changed:
                continue
            LOG.info("脚本变更: %s，作废会话", changed)
            victims = list(_workers) if "paper_style.py" in changed else changed
            for name in victims:
                invalidate(name)
            try:
                on_change(changed)
            except Exception:  # noqa: BLE001 — watcher 不允许因回调挂掉
                pass

    threading.Thread(target=loop, daemon=True, name="mm-script-watcher").start()
