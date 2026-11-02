"""convert-live 的单次转换驱动（Windows Lab 上由 agent.py 起子进程运行；随 deploy-agent 一同部署）。

在本进程里用 werkzeug 把同一个 app 起在 127.0.0.1:<空闲端口>（后台线程），再经真实 HTTP 调 Tavotto 真实的
`POST /api/ai/convert`（agent=codex、source_language=r）：请求的 SERVER_PORT 是真实端口，转换会话经 MCP 工具回调
`POST /api/ai/convert/<sid>/target`（#1628 的写入工具）才连得上。真 `CodexAgent.build_command` 起真 Codex，等到会话结束，把结果写成 JSON。只有两处 monkeypatch，都不改产品行为：
- `ai_bridge.require_usable` / `ai_providers.resolve` / `config.ai_settings`：让产品不去读用户的 Codex 登录态与第三方接口记录
  （密钥因此不进任何配置文件——它只在本进程的环境变量里，由 CODEX_HOME 里 config.toml 的 `env_key` 引用）。
- 记录钩子：把 Codex 的 stdout 每行原样追加进 codex_stdout.jsonl，并在 `CliRunFacts`（#1587，所 prepare 的代码里有才取）出快照时落盘。

用法（没有任何密钥参数；密钥只在环境变量 WINLAB_MODEL_KEY 里）：
    convert_driver.py --project DIR --script REL --out DIR --result FILE [--timeout S]
环境：TAVOTTO_DATA_DIR / TAVOTTO_CONFIG_DIR / CODEX_HOME 由调用方设好（必须在 import tavotto 之前就已生效）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# Windows 上流被捕获 / 重定向时退回系统区域编码，中文报文会静默丢掉——两条流都钉成 UTF-8（见 scripts/build_frontend.py）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

#: 轮询截止 = 产品按脚本行数给的转换时限（ai_convert.convert_timeout_s）+ 完成余量；
#: 所 prepare 的代码没有这个函数就退回基线 900 s + 余量（与 agent.CONVERT_TIMEOUT_* 一致，测试核对）
FALLBACK_BASE_S = 900
MARGIN_S = 120
DEFAULT_TIMEOUT_S = FALLBACK_BASE_S + MARGIN_S
MAX_LISTED_FILES = 200


def long_path(p: Path) -> Path:
    r"""Windows 上 `long_paths_enabled=0` 时，超过 MAX_PATH 的路径要 `\\?\` 前缀才能列目录——驱动自己要能看见产品看不见的东西。"""
    if os.name == "nt" and not str(p).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(p))
    return p


def list_workspace(ws: Path) -> list[dict]:
    """候选工作区里的文件：相对名 + 大小 + 绝对路径长度。拿不到就空表（调用方另有 `listing_error`）。"""
    out: list[dict] = []
    root = long_path(ws)
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = Path(dirpath) / name
            try:
                size = full.stat().st_size
            except OSError:
                size = -1
            rel = full.relative_to(root).as_posix()
            plain = str(ws / rel)
            out.append({"name": rel, "bytes": size, "path_len": len(plain)})
            if len(out) >= MAX_LISTED_FILES:
                return out
    return sorted(out, key=lambda d: d["name"])


def timeout_for(data: bytes, ai_convert) -> int:
    """一份源脚本（字节）的轮询截止秒数：产品同一个公式 + 余量；缺函数时 900 + 余量。"""
    fn = getattr(ai_convert, "convert_timeout_s", None)
    if not callable(fn):
        return FALLBACK_BASE_S + MARGIN_S
    count = getattr(ai_convert, "source_line_count", None)
    lines = count(data) if callable(count) else data.count(b"\n")
    return int(fn(lines)) + MARGIN_S


class Api:
    """对本进程里真实回环服务的最小 HTTP 客户端（urllib；非 2xx 也返回 JSON 体与状态码）。"""

    def __init__(self, port: int) -> None:
        self.base = f"http://127.0.0.1:{port}"

    def call(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 固定 http://127.0.0.1
                code, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            code, raw = exc.code, exc.read()
        try:
            body = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            body = {}
        return code, body if isinstance(body, dict) else {}


def serve_app(flask_app):
    """把 app 起在 127.0.0.1:<空闲端口>（端口交给内核分配，没有探测再绑定的竞态）；返回 (server, port)。"""
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 0, flask_app, threaded=True)
    threading.Thread(target=server.serve_forever, name="convert-driver-http", daemon=True).start()
    return server, server.server_port


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project")
    ap.add_argument("--script")
    ap.add_argument("--out")
    ap.add_argument("--result")
    ap.add_argument("--timeout", type=int, default=None, help="缺省：按脚本行数由产品公式求")
    ap.add_argument("--print-timeout", action="store_true", help="只打印这个脚本的时限（秒）后退出")
    ap.add_argument("--script-file", help="--print-timeout 用：脚本的绝对路径")
    ns = ap.parse_args(argv)
    if ns.print_timeout:
        try:
            from tavotto.engine import ai_convert as _ac
        except ImportError:
            _ac = None
        data = Path(ns.script_file).read_bytes() if ns.script_file else b""
        print(timeout_for(data, _ac) if _ac else DEFAULT_TIMEOUT_S)
        sys.stdout.flush()
        os._exit(0)
    if not (ns.project and ns.script and ns.out and ns.result):
        ap.error("--project --script --out --result are required")
    if ns.timeout is None:
        ns.timeout = DEFAULT_TIMEOUT_S
    out = Path(ns.out)
    out.mkdir(parents=True, exist_ok=True)
    result: dict = {"started": time.strftime("%Y-%m-%dT%H:%M:%S")}

    def save() -> None:
        Path(ns.result).write_text(
            json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )

    try:
        run(ns, out, result)
    except BaseException as exc:  # 驱动自己的缺陷也要落成一份结果，而不是没有结果
        result["driver_error"] = f"{type(exc).__name__}: {exc}"[:400]
    save()
    sys.stdout.flush()
    os._exit(0)  # 引擎后台线程不拦退出


def run(ns, out: Path, result: dict) -> None:
    from tavotto import app as m
    from tavotto.engine import ai_agents, ai_bridge, ai_convert, ai_history, config

    jl = open(out / "codex_stdout.jsonl", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    orig_classify = ai_agents.CodexAgent.classify_event

    def classify(self, line, state):
        jl.write(line.rstrip("\n") + "\n")
        return orig_classify(self, line, state)

    ai_agents.CodexAgent.classify_event = classify

    facts_cls = getattr(
        ai_agents, "CliRunFacts", None
    )  # #1587：所 prepare 的代码里没有就只记产品已有的诊断
    result["cli_run_facts_available"] = facts_cls is not None
    if facts_cls is not None:
        orig_snap = facts_cls.snapshot

        def snap(self, *a, **k):
            d = orig_snap(self, *a, **k)
            (out / "facts.json").write_text(
                json.dumps(d, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
            )
            return d

        facts_cls.snapshot = snap

    orig_cmd = ai_bridge._cmd

    def cmd(*a, **k):
        c, e = orig_cmd(*a, **k)
        # argv 里的长文本（提示词）只记长度，另存 prompt.txt；环境变量只记名字
        (out / "argv.json").write_text(
            json.dumps(
                {
                    "argv": [x if len(x) < 300 else f"<{len(x)} chars>" for x in c],
                    "prompt_chars": len(c[-1]),
                    "env_keys": sorted(e),
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        (out / "prompt.txt").write_text(c[-1], encoding="utf-8")
        return c, e

    ai_bridge._cmd = cmd

    class H(logging.Handler):
        def emit(self, r):
            with open(out / "log.txt", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {r.levelname} {r.getMessage()}\n")

    logging.getLogger().addHandler(H())
    logging.getLogger().setLevel(logging.INFO)

    data = Path(os.environ["TAVOTTO_DATA_DIR"])
    m.app.config["TESTING"] = True
    m.reset_projects()
    m.BAKED_DIR = data / "_baked"
    m.BAKED_PATH = data / "_legacy_baked.json"
    m.CACHE_DIR = data / "cache"
    m.EXPORT_DIR = data / "exports"
    ai_bridge.SNAP_DIR = data / "snap"
    ai_convert.CONVERT_DIR = data / "cache" / "ai_convert"
    ai_history.DB_PATH = data / "hist.sqlite3"
    try:
        from tavotto.engine import ai_figure_edit

        ai_figure_edit.CANDIDATE_DIR = data / "cand"
    except (ImportError, AttributeError):
        pass
    ai_bridge.require_usable = lambda agent: {}
    ai_bridge.ai_providers.resolve = lambda agent, eid: None
    config.ai_settings = lambda: {}
    m.open_project(ns.project)
    # 真实回环服务：之后所有 API 调用都走 HTTP（会话认证状态不装载——本进程只服务自己，见 agent.py 的旁路说明）
    server, port = serve_app(m.app)
    result["server_port"] = port
    try:
        drive(Api(port), ns, out, result, ai_convert)
    finally:
        server.shutdown()


def drive(api: Api, ns, out: Path, result: dict, ai_convert) -> None:
    t0 = time.time()
    status, body = api.call(
        "POST", "/api/ai/convert", {"script": ns.script, "source_language": "r", "agent": "codex"}
    )
    result["start_http"] = status
    result["start_body"] = {k: body.get(k) for k in ("session", "code", "error", "deduped")}
    sid = body.get("session")
    if not sid:
        result["elapsed_s"] = round(time.time() - t0, 1)
        return
    result["session"] = sid
    final = None
    while time.time() - t0 < ns.timeout:
        g = api.call("GET", f"/api/ai/sessions/{sid}")[1]
        if g.get("final"):
            final = g
            break
        time.sleep(1)
    result["elapsed_s"] = round(time.time() - t0, 1)
    result["finished_in_time"] = final is not None
    task = (final or {}).get("task") or {}
    result["status"] = (final or {}).get("status")
    result["code"] = (final or {}).get("code") or (final or {}).get("error")
    result["outcome"] = task.get("outcome")
    if final is not None:
        (out / "session.json").write_text(
            json.dumps(final, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
    st = api.call("GET", f"/api/ai/convert/{sid}")[1]
    result["convert_state"] = st.get("state")
    result["convert_failure"] = st.get("failure")
    ws = ai_convert.CONVERT_DIR / sid
    result["workspace_path_len"] = len(str(ws))
    try:
        result["workspace_files"] = list_workspace(ws)
    except OSError as exc:
        result["workspace_files"] = []
        result["listing_error"] = f"{type(exc).__name__}: {exc}"[:200]


if __name__ == "__main__":
    sys.exit(main())
