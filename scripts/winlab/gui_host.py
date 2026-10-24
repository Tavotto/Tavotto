"""Windows Lab 交互会话执行器（桌面一侧；规则全文 docs/rules/ci/windows-lab.md「交互会话」）。

SSH 会话在 session 0，没有用户桌面：真窗口、WebView2 渲染、窗口消息都要在**已登录的交互会话**里跑。
`tavotto-lab` 开机自动登录控制台（`winlab.py admin gui-setup`），它名下有一个「只在交互会话运行」的计划任务
`TavottoLab-GUI`，动作就是本文件。`agent.py` 在 session 0 里把一份请求写进 `state\\gui\\request.json`、
触发任务、等结果；本文件在交互会话里读请求、用同一套 Job Object（`agent.ProcessTree`）跑那条命令、
把结果写回 `state\\gui\\<run_id>.result.json`。

本文件不决定跑什么：请求只能由 agent 写（`state\\` 只有 tavotto-lab 与管理员可写），而 agent 只写
登记任务的 argv。没有请求、请求过期、run_id 不合形状一律直接退出。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent as A  # noqa: E402 — 同目录部署：Job Object、run id 形状、路径约定的唯一出处

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

#: 请求写下后多久内必须被拾取：更旧的请求是上一次没跑起来的残留，不执行
REQUEST_TTL_S = 120


def session_id() -> int | None:
    if os.name != "nt":
        return None
    import ctypes

    sid = ctypes.c_ulong()
    if ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid)):
        return int(sid.value)
    return None


def serve(lab: A.Lab) -> int:
    gui = lab.state / "gui"
    req_path = gui / "request.json"
    try:
        req = json.loads(req_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    run_id = str(req.get("run_id", ""))
    if (
        not A.RUN_ID_RE.fullmatch(run_id)
        or time.time() - float(req.get("written", 0)) > REQUEST_TTL_S
    ):
        return 0
    req_path.unlink(missing_ok=True)
    sid = session_id()
    (gui / f"{run_id}.started").write_text(
        json.dumps({"session_id": sid, "pid": os.getpid()}), encoding="utf-8"
    )
    prefix = Path(req["out_prefix"])
    prefix.parent.mkdir(parents=True, exist_ok=True)
    stop_flag = gui / f"{run_id}.stop"
    t0 = time.monotonic()
    with open(f"{prefix}.stdout.log", "wb") as out, open(f"{prefix}.stderr.log", "wb") as err:
        try:
            tree = A.ProcessTree(
                list(req["argv"]),
                cwd=Path(req["cwd"]),
                env=dict(req["env"]),
                stdout=out,
                stderr=err,
            )
        except OSError as exc:
            err.write(f"{type(exc).__name__}: {exc}\n".encode("utf-8", "replace"))
            result = {"exit_code": None, "timed_out": False, "spawn_error": type(exc).__name__}
        else:
            deadline = t0 + float(req["timeout"])
            rc, timed_out = None, False
            while True:
                rc = tree.proc.poll()
                if rc is not None:
                    break
                if stop_flag.exists() or time.monotonic() > deadline:
                    tree.kill_all()
                    tree.proc.wait()
                    rc, timed_out = None, True
                    break
                time.sleep(0.5)
            tree.close()
            result = {"exit_code": rc, "timed_out": timed_out}
    result.update(duration_s=round(time.monotonic() - t0, 3), session_id=sid)
    tmp = gui / f"{run_id}.result.json.tmp"
    tmp.write_text(json.dumps(result), encoding="utf-8")
    os.replace(tmp, gui / f"{run_id}.result.json")
    stop_flag.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(serve(A.Lab()))
