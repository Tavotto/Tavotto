"""convert-live 的单次转换驱动（Windows Lab 上由 agent.py 起子进程运行；随 deploy-agent 一同部署）。

用 Flask test client 调 Tavotto 真实的 `POST /api/ai/convert`（agent=codex、source_language=r），真 `CodexAgent.build_command`
起真 Codex，等到会话结束，把结果写成 JSON。只有两处 monkeypatch，都不改产品行为：
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
import time
from pathlib import Path

#: 产品对单次 AI 任务的上限是 900 s（ai_bridge.TIMEOUT_S）；多等一分钟看收尾
DEFAULT_TIMEOUT_S = 960
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--script", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    ns = ap.parse_args(argv)
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
    client = m.app.test_client()

    t0 = time.time()
    r = client.post(
        "/api/ai/convert", json={"script": ns.script, "source_language": "r", "agent": "codex"}
    )
    body = r.get_json(silent=True) or {}
    result["start_http"] = r.status_code
    result["start_body"] = {k: body.get(k) for k in ("session", "code", "error", "deduped")}
    sid = body.get("session")
    if not sid:
        result["elapsed_s"] = round(time.time() - t0, 1)
        return
    result["session"] = sid
    final = None
    while time.time() - t0 < ns.timeout:
        g = client.get(f"/api/ai/sessions/{sid}").get_json(silent=True) or {}
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
    st = client.get(f"/api/ai/convert/{sid}").get_json(silent=True) or {}
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
