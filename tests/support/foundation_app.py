"""首开场景（FirstOpenBench）经**真实公共入口**跑的装置：`python -m tavotto` + 会话认证。

U01-S1（`tests/test_foundation_harness.py`）把这套装置写在用例里；U03 起多条场景共用，
收成一个模块。纪律一个字不变（05_TEST_STRATEGY §4）：

* **harness 不替产品选环境、不装包、不预设 cwd**：服务子进程的环境里
  `TAVOTTO_WORKER_PYTHON` / `MM_WORKER_PYTHON` **一律摘掉**（除非用例明确要测显式选择），
  用户配置目录是空的；产品自己发现、体检、记住；
* **凭据交接只有一份实现**：`scripts/smoke_app.adopt_session_credentials`；
* 默认拒绝（401）是每条场景都先验一次的前提，不是可选步骤。

用法：

    with running_app(project_dir, work_dir) as app:
        app.call("/api/panels")                # GET，带凭据
        app.call("/api/engine/preparation", {"id": ...})   # POST
        app.prepare(panel_id)                  # 起准备并等终局
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TERMINAL = ("ready", "error", "cancelled", "static_source_available", "needs_input")


def smoke_module():
    spec = importlib.util.spec_from_file_location(
        "_foundation_smoke_app", ROOT / "scripts" / "smoke_app.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class HttpError(Exception):
    def __init__(self, status: int, body: dict):
        super().__init__(f"HTTP {status}: {body.get('code') or body.get('error')}")
        self.status = status
        self.body = body


class RunningApp:
    def __init__(self, base: str, auth: dict, proc: subprocess.Popen, log_path: Path, work: Path):
        self.base = base
        self.auth = auth
        self.proc = proc
        self.log_path = log_path
        self.work = work

    # ---- HTTP ----
    def call(
        self,
        path: str,
        payload: dict | None = None,
        *,
        method: str | None = None,
        timeout: float = 60.0,
    ):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json", **self.auth} if data else dict(self.auth)
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except ValueError:
                body = {}
            raise HttpError(exc.code, body) from None

    def prepare(self, panel_id: str, *, timeout: float = 300.0) -> dict:
        """POST 准备 → 轮询到终局，回 `{plan, result}`。"""
        status, prep = self.call("/api/engine/preparation", {"id": panel_id}, timeout=30)
        assert status == 202, prep
        plan_id = prep["plan"]["plan_id"]
        deadline = time.time() + timeout
        while True:
            _, state = self.call(f"/api/engine/preparation/{plan_id}", timeout=30)
            if state["result"]["status"] in TERMINAL:
                return state
            assert time.time() < deadline, state
            time.sleep(0.2)

    def render(self, panel_id: str, patches: list | None = None, *, timeout: float = 300.0) -> dict:
        _, body = self.call(
            "/api/engine/render", {"id": panel_id, "patches": patches or []}, timeout=timeout
        )
        return body

    def server_log(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


@contextlib.contextmanager
def running_app(
    project: Path, work: Path, *, env_overrides: dict | None = None, ready_timeout: float = 120.0
):
    """起一个真实服务（会话认证默认开），交接凭据，退出时干净关掉。

    `env_overrides` 只给「明确要测显式选择」的用例用（例如 FO15 里点名一条坏的
    `TAVOTTO_WORKER_PYTHON`）；默认把两个渲染解释器变量都摘掉。
    """
    smoke = smoke_module()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    data_dir, config_dir, home = work / "data", work / "config", work / "home"
    for d in (data_dir, config_dir, home):
        d.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "TAVOTTO_DATA_DIR": str(data_dir),
        "TAVOTTO_CONFIG_DIR": str(config_dir),
        "HOME": str(home),
        "TAVOTTO_NO_UPDATE_CHECK": "1",
        "TAVOTTO_NO_TELEMETRY": "1",
        "TAVOTTO_ALLOW_SHUTDOWN": "1",
    }
    for name in ("TAVOTTO_INSECURE_NO_AUTH", "TAVOTTO_WORKER_PYTHON", "MM_WORKER_PYTHON"):
        env.pop(name, None)
    env.update(env_overrides or {})
    log_path = work / "server.log"
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tavotto",
            "--port",
            str(port),
            "--no-browser",
            "--figures",
            str(project),
        ],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=str(work),
    )
    app: RunningApp | None = None
    try:
        deadline = time.time() + ready_timeout
        while True:
            assert proc.poll() is None, f"服务在就绪前退出: {log_path.read_text('utf-8')[-2000:]}"
            try:
                urllib.request.urlopen(f"{base}/api/version", timeout=5).read()
                break
            except (urllib.error.URLError, OSError):
                assert time.time() < deadline, f"{ready_timeout} s 内 /api/version 仍不可访问"
                time.sleep(0.3)
        # 默认拒绝（ADR 0008）→ 凭据文件交接（唯一实现在 smoke_app）
        try:
            urllib.request.urlopen(f"{base}/api/panels", timeout=10).read()
        except urllib.error.HTTPError as denied:
            assert denied.code == 401, denied.code
        else:
            raise AssertionError("未认证的 /api/panels 没有被拒绝")
        assert smoke.adopt_session_credentials(data_dir, port), "本机会话凭据文件缺失"
        app = RunningApp(base, dict(smoke._AUTH), proc, log_path, work)
        yield app
        with contextlib.suppress(Exception):
            app.call("/api/shutdown", {}, timeout=30)
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)
        log.close()


def independent_identity(python: str) -> dict:
    """独立探针：目标解释器自报 version / prefix / executable（不经产品代码）。"""
    out = subprocess.run(
        [
            python,
            "-c",
            "import sys, json, platform; print(json.dumps({'prefix': sys.prefix, 'executable': sys.executable, "
            "'version': platform.python_version(), 'base_prefix': sys.base_prefix}))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])
