"""WorkBuddy P0 spike：夹在真 WorkBuddy 与真 Tavotto server 之间的**录制代理**。

WorkBuddy 的「自定义连接器」启动的是它；它把 stdin/stdout 原样转给真正的
`codex-plugin/mcp/server.py`，同时把**宿主那一侧的事实**写进一份 JSONL：

* 启动时：cwd、argv、全部环境变量的**键**（值只记非敏感键，含 TOKEN / KEY /
  SECRET / PASS / AUTH / COOKIE / SESSION 的一律打码）；
* 每一帧：方向（c2s = 宿主→server，s2c = server→宿主）、method / id、字节数；
  `initialize` 的 params 与 result、server→宿主的请求（`roots/list` /
  `elicitation/create`）及宿主的回答、错误帧**整帧**记下；
  其余帧的 params 截到 2 KB，result 只记形状（键与体积）。

判据全在这份日志里：宿主声明了哪些 capability、有没有回 roots/list、确认框点了
什么、`resources/read` 在什么时刻被谁调、每次拖动之后有没有一条 apply 到达
server（被拒绝的调用根本到不了这里）。

    python3 tests/workbuddy/record_proxy.py      # 由 WorkBuddy 启动，不要手动跑
    环境变量 WB_RECORD_DIR：日志目录（缺省 ~/tavotto-workbuddy-record）

它不改任何一帧，不参与协议；只是个旁听者。spike 工具，不进产品。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAUNCHER = HERE.parents[1] / "codex-plugin" / "mcp" / "server.py"
SECRET_MARKERS = ("TOKEN", "KEY", "SECRET", "PASS", "AUTH", "COOKIE", "SESSION", "CREDENTIAL")
FULL_METHODS = {"initialize", "roots/list", "elicitation/create", "resources/read"}

log_path: Path | None = None  # main() 里才定：被 import 时不往任何地方写
_lock = threading.Lock()
_t0 = time.monotonic()
_pending: dict[tuple[str, object], str] = {}  # (方向, id) → method：用来认出响应属于哪个请求


def log(entry: dict) -> None:
    entry["t"] = round(time.monotonic() - _t0, 3)
    assert log_path is not None
    with _lock, log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _env_snapshot() -> dict:
    snap = {}
    for k, v in sorted(os.environ.items()):
        secret = any(m in k.upper() for m in SECRET_MARKERS)
        snap[k] = "<redacted>" if secret else v
    return snap


def _shape(obj: object, depth: int = 0) -> object:
    if isinstance(obj, dict):
        if depth >= 2:
            return f"<object {len(obj)} keys>"
        return {k: _shape(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return f"<list {len(obj)}>"
    if isinstance(obj, str) and len(obj) > 120:
        return f"<str {len(obj)}>"
    return obj


def record(direction: str, raw: bytes) -> None:
    try:
        msg = json.loads(raw)
    except ValueError:
        log({"dir": direction, "unparsed_bytes": len(raw)})
        return
    entry: dict = {"dir": direction, "bytes": len(raw)}
    method = msg.get("method")
    mid = msg.get("id")
    if method:
        entry.update(method=method, id=mid)
        if mid is not None:
            _pending[(direction, mid)] = method
        params = msg.get("params")
        if method in FULL_METHODS or method.startswith("notifications/"):
            entry["params"] = params
        elif params is not None:
            text = json.dumps(params, ensure_ascii=False)
            entry["params"] = text[:2048] + ("…" if len(text) > 2048 else "")
    else:
        other = "s2c" if direction == "c2s" else "c2s"
        req = _pending.pop((other, mid), None)
        entry.update(response_to=req, id=mid)
        if "error" in msg:
            entry["error"] = msg["error"]
        elif req in FULL_METHODS and req != "resources/read":
            entry["result"] = msg.get("result")
        else:
            res = msg.get("result")
            entry["result_shape"] = _shape(res)
            if isinstance(res, dict):
                sc = res.get("structuredContent")
                if isinstance(sc, dict):
                    entry["sc"] = {
                        k: sc.get(k)
                        for k in ("ok", "code", "disposition", "session_id", "elided", "canvas_ui")
                        if k in sc
                    }
                if "isError" in res:
                    entry["isError"] = res["isError"]
    log(entry)


def pump(src, dst, direction: str) -> None:
    for raw in iter(src.readline, b""):
        record(direction, raw.strip())
        dst.write(raw)
        dst.flush()
    log({"dir": direction, "eof": True})
    try:
        dst.close()
    except OSError:
        pass


def main() -> int:
    global log_path
    out_dir = Path(os.environ.get("WB_RECORD_DIR") or Path.home() / "tavotto-workbuddy-record")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"record-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.jsonl"
    log(
        {
            "start": True,
            "cwd": os.getcwd(),
            "argv": sys.argv,
            "python": sys.executable,
            "ppid": os.getppid(),
            "env": _env_snapshot(),
        }
    )
    child = subprocess.Popen(
        ["python3", str(LAUNCHER), *sys.argv[1:]],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=os.environ.copy(),
        cwd=os.getcwd(),
    )
    t_in = threading.Thread(target=pump, args=(sys.stdin.buffer, child.stdin, "c2s"), daemon=True)
    t_out = threading.Thread(
        target=pump, args=(child.stdout, sys.stdout.buffer, "s2c"), daemon=True
    )
    t_in.start()
    t_out.start()
    code = child.wait()
    t_out.join(timeout=5)
    log({"exit": code})
    return code


if __name__ == "__main__":
    raise SystemExit(main())
