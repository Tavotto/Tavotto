"""评测：兼容性修复——Tavotto 内置 AI 修复（A）vs 通用编码 agent + Tavotto MCP（B）。

问题（维护者）：同一份「在 Tavotto 里跑不起来 / 不出图」的脚本，谁能修好、卡在哪。
沿用 `scripts/eval/agent_journal/` 的结构：题库存成 JSON（脚本是数据，不落成 .py 进仓库、不被 ruff 改写）、
结果追加到 `results/<cond>.jsonl`、被测 agent 是 `opencode run -m opencode-go/deepseek-v4.1-flash`。

**成功判据（两组一致，只有这一条）**：修复结束后，用真 sidecar 对**原始目标脚本**再建一次准备会话、点运行、等落定，
`phase == completed` 且 `facts.figure_captured is True`；并且项目里原有的每个文件字节不变（对比运行前的哈希）。
新多出来的文件允许，但会记在 `new_files`。

子命令：
  list     列题库
  probe    不修，只跑一遍：记录「未修前的真实结局」（稳定错误码）
  run      跑一个条件：baseline（不修，只判）/ A（内置 AI 修复）/ B（opencode + Tavotto MCP）/ C（预留，Codex CLI，本次不跑）
  report   汇总（调 summarize.py）

环境变量：
  PYTHON               起 sidecar 与 MCP 用的解释器（要装 tavotto 的依赖；缺省当前解释器）
  OPENCODE_GO_API_KEY  A 组：OpenCode Go 密钥。只经环境变量进来、经 `POST /api/ai/connections`（Tavotto 自己的配置机制）
                       存进被测 sidecar 的一次性配置目录；绝不打印、不写进结果 / 日志
  EVAL_MODEL           缺省 opencode-go/deepseek-v4.1-flash
  EVAL_RUNS            运行目录（缺省 /tmp/tavotto-fix-compat-runs）
  TAVOTTO_MCP          B 组的 MCP 启动命令（空格分隔）；缺省 `$PYTHON -m tavotto mcp`（源码树）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CORPUS_JSON = HERE / "corpus.json"
#: B 组项目放在这里（与日志 / 判分状态分开，agent 的工作目录旁边没有评测的私有文件）
SCRATCH = Path(os.environ.get("EVAL_SCRATCH", "/tmp/scratch-projects"))
#: 每一轮可以单独放（`EVAL_RESULTS=…/results/2026-11-19-deepseek-flash`），不和基线混在同一个 jsonl 里
RESULTS = Path(os.environ.get("EVAL_RESULTS") or (HERE / "results"))
RESULTS.mkdir(parents=True, exist_ok=True)
RUNS = Path(os.environ.get("EVAL_RUNS", "/tmp/tavotto-fix-compat-runs"))
MODEL = os.environ.get("EVAL_MODEL", "opencode-go/deepseek-v4.1-flash")
PYTHON = os.environ.get("PYTHON") or sys.executable
#: A 组的连接：`EVAL_MODEL` 写成「Tavotto 连接厂商 id/型号」（`opencode-go/deepseek-v4.1-flash`、`deepseek/deepseek-flash`、
#: `anthropic-api/claude-opus-5-5`、`openai-api/gpt-5`……厂商 id 见 `src/tavotto/engine/ai_catalog.json`）
CONN_VENDOR, _, CONN_MODEL = MODEL.partition("/")
#: A 组的密钥：`EVAL_A_API_KEY`（任意厂商）；旧名 `OPENCODE_GO_API_KEY` 照旧认
A_KEY_VARS = ("EVAL_A_API_KEY", "OPENCODE_GO_API_KEY")
SESSIONS = "/api/engine/preparation-sessions"
SETTLED = (
    "completed",
    "partial",
    "action_required",
    "cancelled",
    "awaiting_configuration",
    "awaiting_confirmation",
)
#: sidecar 的环境：关掉用户环境发现（evals 只用 PYTHON 指定的这个解释器），源码树生效
SIDECAR_ENV = {
    "TAVOTTO_USER_ENV_DISCOVERY": "0",
    "PYTHONPATH": str(ROOT / "src"),
    # sidecar 继承整个环境，worker / 用户脚本也拿得到：密钥变量置空（连接是经 HTTP 存进 Tavotto 配置的）
    "OPENCODE_GO_API_KEY": "",
    "EVAL_A_API_KEY": "",
}

sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(HERE))  # envfaults（环境故障家族的假 wheel 夹具）


# ---------------------------------------------------------------- 题库 / 项目


def load_corpus() -> dict:
    return json.loads(CORPUS_JSON.read_text(encoding="utf-8"))


def materialize(item: dict, dest: Path) -> None:
    """题库条目 → 项目目录 dest（先清空）。"""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for rel, text in item["files"].items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    for rel, text in (
        item.get("outside_files") or {}
    ).items():  # 项目文件夹之外的兄弟文件（不进哈希）
        p = dest.parent / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def hash_tree(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".pyc"):
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# ---------------------------------------------------------------- sidecar


def _wait(predicate, what: str, timeout: float):
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = predicate()
        if got:
            return got
        time.sleep(0.25)
    raise TimeoutError(f"{timeout}s 内没等到 {what}")


def sidecar(project: Path, work: Path, extra_env: dict | None = None):
    """真 `tavotto --desktop-sidecar`（回退与修复只授权给桌面来源）。夹具复用 tests/support/foundation_app.sidecar_app。"""
    from support import foundation_app as fa

    # 夹具用 sys.executable 起服务：这里让它就是 PYTHON（用 PYTHON 重新执行本脚本，见 main）
    return fa.sidecar_app(project, work, env_overrides={**SIDECAR_ENV, **(extra_env or {})})


def report_of(app, sid: str) -> dict:
    return app.call(f"{SESSIONS}/{sid}", timeout=30)[1]


def run_prep(app, script: str, timeout: float = 240.0, argv: list[str] | None = None) -> dict:
    """对 script 建一次准备会话、点运行（有就点）、等落定。返回 {session_id, report, ran, clicked}。"""
    # 与界面「打开并运行」同一种打开方式（`reuse_last_success`）：修复里验证通过的运行配置（参数）会被沿用，
    # 判分不因「没有前端回填参数草稿」而冤枉修好的那一组
    status, rep = app.call(SESSIONS, {"script": script, "reuse_last_success": True}, timeout=120)
    sid = rep["session_id"]
    runs = [a for a in rep.get("actions", []) if a["kind"] == "run"]
    clicked = False
    if runs:
        st, claimed = app.call(
            f"{SESSIONS}/{sid}/actions",
            {"action_id": runs[0]["id"], "expected_config_revision": rep["config_revision"]},
            timeout=60,
        )
        clicked = st == 202 and bool(claimed.get("claimed"))
    if clicked:
        rep = _wait(
            lambda: (r := report_of(app, sid)) and r["phase"] in SETTLED and r,
            "准备会话落定",
            timeout,
        )
    return {"session_id": sid, "report": rep, "clicked": clicked}


def summarize_report(rep: dict) -> dict:
    out = rep.get("outcome") or {}
    return {
        "phase": rep.get("phase"),
        "outcome_code": out.get("code"),
        "figure_captured": (rep.get("facts") or {}).get("figure_captured"),
        "actions": sorted({a["kind"] for a in rep.get("actions", [])}),
        "attempts": [
            {k: h.get(k) for k in ("profile", "outcome", "code", "cause")}
            for h in rep.get("attempt_history", [])
        ],
    }


def judge(app, project: Path, item: dict, before_hash: dict) -> dict:
    """成功判据（只有这一条）：原始脚本再建一次准备会话跑一遍 completed + figure_captured，且原文件字节不变。"""
    try:
        res = run_prep(app, item["script"])
        summ = summarize_report(res["report"])
        summ["clicked_run"] = res["clicked"]
    except Exception as exc:  # noqa: BLE001 - 判分失败也要有记录
        summ = {"phase": "judge_error", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    after = hash_tree(project)
    changed = sorted(k for k, v in before_hash.items() if after.get(k) != v)
    new_files = sorted(k for k in after if k not in before_hash)
    ok = summ.get("phase") == "completed" and summ.get("figure_captured") is True and not changed
    return {
        "success": bool(ok),
        "final": summ,
        "originals_changed": changed,
        "new_files": new_files,
    }


# ---------------------------------------------------------------- 环境故障家族（ADR 0221 §六）


def is_env_item(item: dict) -> bool:
    return item.get("family") == "environment"


def fault_env(item: dict, house: Path) -> dict[str, str] | None:
    """环境题：在 `house`（隔离临时目录）里合成假 wheel，返回要并进 sidecar 的环境变量；非环境题 None。"""
    import envfaults

    if not is_env_item(item):
        return None
    return envfaults.build_fixtures(item["fault"], house, PYTHON)


def install_attempt(app, item: dict, timeout: float = 900.0) -> dict:
    """环境题的「用户点了一键安装」：对缺的那个包形成计划（装进 Tavotto 受管环境，题库点名分发名）、执行、等落定。

    返回稳定错误码（只有码，不带路径；自检阶段 / worker 码只在诊断包的 managed_environment_jobs 里）；与界面「装上并继续」同一条后端路由。
    """
    wheel = item["fault"]["wheels"][0]
    st, body = app.call(
        "/api/engine/dependency/plan",
        {
            "module": wheel["import"],
            "script": item["script"],
            "target": "tavotto_managed",
            "distribution": wheel["dist"],
        },
        timeout=120,
    )
    plan = body.get("plan") or {}
    if not plan.get("plan_id"):
        return {"stage": "plan", "code": body.get("code") or f"http_{st}"}
    app.call(
        "/api/engine/dependency/install",
        {"plan_id": plan["plan_id"], "impact_digest": plan.get("impact_digest", "")},
        timeout=120,
    )
    t0 = time.time()
    state: dict = {}
    while time.time() - t0 < timeout:
        _, state = app.call(f"/api/engine/dependency/state?plan_id={plan['plan_id']}", timeout=30)
        if state.get("state") in ("done", "failed", "cancelled"):
            break
        time.sleep(2)
    return {
        "stage": "install",
        "state": state.get("state"),
        "code": state.get("code") or ("" if state.get("state") == "done" else "no_final_state"),
    }


def env_before(app, item: dict) -> dict:
    """环境题的「未修前」：先开准备会话（缺包），再点一键安装，记真实的失败形状。与 `summarize_report` 同口径加 `install`。"""
    prep = run_prep(app, item["script"])
    out = summarize_report(prep["report"])
    out["install"] = install_attempt(app, item)
    return out


# ---------------------------------------------------------------- 卡点归类


def classify_stuck(rec: dict) -> str:
    """首个卡点的归类（粗粒度，给汇总计数用；证据在 trajectory 里）。成功 = `none`。"""
    if rec.get("success"):
        return "none"
    cond = rec.get("cond")
    if cond == "baseline":
        return "no_fix_attempted"
    tr = rec.get("trajectory") or []
    fx = rec.get("fix") or {}
    if rec.get("agent_errors"):
        return "model_error"
    if cond in ("A", "M"):
        if fx.get("reason") in ("ai_fix_unavailable", "no_connection"):
            return "no_connection"
        for c in rec.get("cards") or []:
            if c.get("answered") == "unanswerable":
                return f"unanswerable_card:{c.get('tool') or c.get('kind')}"
        if rec.get("timed_out"):
            return "timeout"
        oc = fx.get("outcome")
        if oc == "fixed":
            return "claimed_fixed_but_judge_failed"
        if oc == "failed":
            return f"fix_failed:{fx.get('reason')}"
        if oc == "convert":
            return "handed_off_convert"
        if oc == "not_fixed":
            denied = [t for t in tr if t.get("approved_by") == "blocked"]
            if denied:
                return f"tool_denied:{denied[0].get('tool')}"
            return "gave_up_not_fixed"
        return "no_outcome"
    # B / C
    if rec.get("timed_out"):
        return "timeout"
    if rec.get("originals_changed"):
        return "modified_original_files"
    new_py = [f for f in rec.get("new_files") or [] if f.endswith(".py")]
    if new_py and rec.get("final", {}).get("phase") != "completed":
        # 图在 Tavotto 里出来了（新脚本），但原目标脚本照旧跑不起来：绕过去而不是修好
        return "worked_around_with_new_script"
    names = [t.get("tool") for t in tr]
    if not any(str(n).startswith("tavotto_") for n in names):
        return "never_used_tavotto"
    return "agent_done_but_judge_failed"


# ---------------------------------------------------------------- A 组：内置 AI 修复


def connect_builtin(app) -> dict:
    """经 Tavotto 自己的配置入口存一条连接（厂商 / 型号来自 `EVAL_MODEL`；密钥只来自环境变量，不回显）。"""
    key = next(
        (os.environ.get(v, "").strip() for v in A_KEY_VARS if os.environ.get(v, "").strip()), ""
    )
    if not key:
        raise SystemExit(
            f"A 组需要环境变量 {' 或 '.join(A_KEY_VARS)}（{CONN_VENDOR} 的密钥）；没有就不能跑 A"
        )
    st, body = app.call(
        "/api/ai/connections",
        {"vendor": CONN_VENDOR, "api_key": key, "model": CONN_MODEL},
        timeout=120,
    )
    conn = body.get("connection") or {}
    summary = {
        "ok": body.get("ok"),
        "state": conn.get("state"),
        "error": conn.get("error"),
        "model": conn.get("default_model") or conn.get("model"),
        "protocol": conn.get("protocol"),
        "probe": body.get("probe"),
    }
    if not body.get("ok") or conn.get("state") not in ("ready", "ok", "connected"):
        # 连接不可用：不要往下调 /api/ai/fix（没有连接时它会改走本机的 Codex / Claude Code CLI）
        raise SystemExit(f"{CONN_VENDOR} 连接没存成 / 不可用，A 组中止：{summary}")
    return summary


def answer_card(card: dict) -> tuple[str, str | None]:
    """自动回答确认卡：一律同意；答不了的（pick_file 之类）回 skip，由调用方记为卡住。"""
    kind = card.get("kind")
    if kind in ("approve",):
        return "yes", None
    if kind == "send":
        return "sent", None
    if kind == "choice":
        # 选项里挑第一个非 skip 的；没有就跳过
        opts = [o.get("id") for o in card.get("options") or [] if o.get("id") not in (None, "skip")]
        return (opts[0], None) if opts else ("skip", None)
    if kind == "pick":
        return "skip", None
    return "skip", None


def read_audit(work: Path) -> list[dict]:
    """sidecar 数据目录里的修复审计：每次工具调用一行（工具、档、参数摘要、批准人、结果码；内容不落盘）。"""
    p = work / "data" / "ai" / "fix_audit.jsonl"
    out = []
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def run_fix(app, work: Path, prep: dict, item: dict, timeout: float) -> dict:
    """POST /api/ai/fix，轮询、自动回答卡片，等 finished。"""
    t0 = time.time()
    st, started = app.call(
        "/api/ai/fix", {"session_id": prep["session_id"], "script": item["script"]}, timeout=60
    )
    fix: dict = {"start": {k: v for k, v in started.items() if k != "repair_token"}}
    cards: list[dict] = []
    seen_acts: list[dict] = []
    if not started.get("ok"):
        fix["reason"] = started.get("reason")
        return {"fix": fix, "cards": cards, "activity": seen_acts, "timed_out": False}
    fid = started["fix_id"]
    answered: set[str] = set()
    timed_out = False
    state: dict = {}
    while True:
        st, state = app.call(f"/api/ai/fix/{fid}", timeout=30)
        act = state.get("activity")
        if act and (not seen_acts or act != seen_acts[-1]):
            seen_acts.append(act)
        card = state.get("card")
        if state.get("state") == "waiting" and card and card.get("id") not in answered:
            ans, text = answer_card(card)
            answered.add(card.get("id"))
            rec = {
                "kind": card.get("kind"),
                "tool": card.get("tool"),
                "answer": ans,
                "answered": "unanswerable"
                if card.get("kind") == "pick"
                else "auto_first_option"
                if card.get("kind") == "choice"
                else "auto_yes",
            }
            cards.append(rec)
            if rec["answered"] == "unanswerable":
                # 记为卡住：回 skip 让会话继续收尾（不替用户挑文件）
                pass
            try:
                app.call(
                    f"/api/ai/fix/{fid}/answer",
                    {"card_id": card.get("id"), "answer": ans, **({"text": text} if text else {})},
                    timeout=30,
                )
            except Exception as exc:  # noqa: BLE001
                rec["answer_error"] = str(exc)[:120]
        if state.get("state") == "finished":
            break
        if time.time() - t0 > timeout:
            timed_out = True
            try:
                app.call(f"/api/ai/fix/{fid}/cancel", {}, timeout=30)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)
            st, state = app.call(f"/api/ai/fix/{fid}", timeout=30)
            break
        time.sleep(0.4)
    fix.update(
        {
            "fix_id": fid,
            "outcome": state.get("outcome"),
            "reason": state.get("reason"),
            "summary": (state.get("summary") or "")[:400],
            "steps": state.get("steps"),
        }
    )
    return {
        "fix": fix,
        "cards": cards,
        "activity": seen_acts,
        "timed_out": timed_out,
        "wall_s": round(time.time() - t0, 1),
    }


# ---------------------------------------------------------------- B 组：opencode + Tavotto MCP

#: 被测 agent 的权限：可读写项目目录、跑 python；外部目录、联网拒绝。要求「不许修改原有文件」写在任务说明里，
#: 是否真的没改由判据里的哈希比对判定（不用权限替它拦，否则测不出来）
PERMISSION = {
    "edit": "allow",
    "bash": {"*": "allow"},
    "shell": {"*": "allow"},
    "execute": "deny",
    "webfetch": "deny",
    "websearch": "deny",
    "external_directory": "deny",
}

PROMPT_B = """这个项目里的 `{script}` 在 Tavotto 里打不开 / 不出图。请让它在 Tavotto 里能出图。
不许修改项目里原有的任何文件（可以新增文件）。Tavotto 的 MCP 工具可用来打开、检查、修复 matplotlib 图。
做完后回复 DONE。"""
#: B 组提示词版本；改任务说明时加一
PROMPT_B_REV = 1


def mcp_cmd() -> list[str]:
    raw = os.environ.get("TAVOTTO_MCP")
    return raw.split() if raw else [PYTHON, "-m", "tavotto", "mcp"]


def mcp_config(wrapper_dir: Path, work: Path) -> str:
    """B 组 opencode 配置（经 OPENCODE_CONFIG_CONTENT 传，不在项目目录里落文件）。
    MCP 与判分 sidecar 共用同一份 Tavotto 状态（数据 / 配置 / HOME 同一个 work 目录）：agent 在 Tavotto 里记下的
    项目设置（脚本参数、数据重映射、改入口）判分时才作数——与真实用户「一个 Tavotto」一致。
    启动命令包在一个脚本里：路径与环境不写在配置里，免得 agent 一读配置就摸到评测的目录布局。"""
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    sh = wrapper_dir / "mcp.sh"
    env = {
        "TAVOTTO_DATA_DIR": str(work / "data"),
        "TAVOTTO_CONFIG_DIR": str(work / "config"),
        "HOME": str(work / "home"),
        "TAVOTTO_USER_ENV_DISCOVERY": "0",
        "PYTHONPATH": str(ROOT / "src"),
        "TAVOTTO_NO_UPDATE_CHECK": "1",
        "TAVOTTO_NO_TELEMETRY": "1",
    }
    lines = ["#!/bin/sh"] + [f"export {k}='{v}'" for k, v in env.items()]
    lines.append("exec " + " ".join(f"'{c}'" for c in mcp_cmd()))
    sh.write_text("\n".join(lines) + "\n")
    sh.chmod(0o755)
    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "permission": PERMISSION,
        "mcp": {"tavotto": {"type": "local", "command": ["/bin/sh", str(sh)], "enabled": True}},
    }
    return json.dumps(cfg)


def _trim(v, n=200):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"…(+{len(s) - n})"


def parse_opencode(text: str) -> dict:
    """opencode `--format json` 事件流 → 工具调用序列 + token + 错误（退出码 0 也可能是错误，查 Error:）。"""
    steps = tin = tout = 0
    traj: list[dict] = []
    errors: list[str] = []
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            if "Error:" in line:
                errors.append(line[:300])
            continue
        part = ev.get("part") or {}
        if ev.get("type") == "step_finish":
            steps += 1
            tk = part.get("tokens") or {}
            tin += tk.get("input", 0)
            tout += tk.get("output", 0) + tk.get("reasoning", 0)
        if part.get("type") == "tool":
            st = part.get("state") or {}
            inp = st.get("input") or {}
            out = st.get("output")
            traj.append(
                {
                    "tool": part.get("tool", "?"),
                    "args": _trim(inp, 160),
                    "status": st.get("status"),
                    # 结果码：MCP 返回的 JSON 里的 code / ok / error 摘要；拿不到就截一小段原文
                    "result": _result_code(out) if out is not None else _trim(st.get("error"), 160),
                }
            )
        if ev.get("type") == "error":
            errors.append(_trim(ev, 300))
    return {
        "steps": steps,
        "tokens_in": tin,
        "tokens_out": tout,
        "trajectory": traj,
        "agent_errors": errors[:5],
    }


def _result_code(out) -> str | None:
    if out is None:
        return None
    s = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
    m = re.search(r'"(?:code|error)"\s*:\s*"([A-Za-z0-9_.:-]{2,60})"', s)
    if m:
        return m.group(1)
    m = re.search(r'"ok"\s*:\s*(true|false)', s)
    return f"ok={m.group(1)}" if m else _trim(s, 80)


def run_opencode(cwd: Path, prompt: str, timeout: int, config: str, log: Path) -> dict:
    env = {
        **os.environ,
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "PWD": str(cwd),
        "OPENCODE_CONFIG_CONTENT": config,
        "PATH": f"{Path(PYTHON).parent}:{os.environ['PATH']}",
    }
    t0 = time.time()
    timed_out = False
    with log.open("w", encoding="utf-8") as fh:
        try:
            subprocess.run(  # noqa: S603
                ["opencode", "run", "--standalone", "-m", MODEL, "--format", "json", prompt],
                cwd=cwd,
                env=env,
                stdout=fh,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
    text = log.read_text(encoding="utf-8", errors="replace")
    return {
        **parse_opencode(text),
        "wall_s": round(time.time() - t0, 1),
        "timed_out": timed_out,
    }


# ---------------------------------------------------------------- 一次运行


def _fix_and_judge(cond, project, work, item, before, timeout, rec, extra_env=None) -> None:
    """A / M 组的一次修复 + 判分（baseline 只判分）。轨迹只取这一次修复新增的审计行（M 组里 warmup 的不算）。"""
    with sidecar(project, work, extra_env) as app:
        if cond in ("A", "M"):
            rec["connection"] = connect_builtin(app)
        prep = run_prep(app, item["script"])
        if extra_env is not None and cond == "A":
            # 环境题：修复会话从「用户点了一键安装、没装好」之后开始（失败码记进 rec）
            rec["install_attempt"] = install_attempt(app, item)
        if cond in ("A", "M"):
            seen = len(read_audit(work))
            fx = run_fix(app, work, prep, item, timeout)
            rec.update(fx)
            audit = read_audit(work)[seen:]
            rec["trajectory"] = [
                {
                    "tool": a.get("tool"),
                    "args": a.get("args"),
                    "tier": a.get("tier"),
                    "approved_by": a.get("approved_by"),
                    "result": (a.get("result") or {}).get("code")
                    or (a.get("result") or {}).get("ok"),
                    # 已知问题（ADR 0221 §二）：审计结果里有 known_issue 就带上（只有 id）；现在的审计行还没有这个键，缺省不写
                    **(
                        {"known_issue": (a.get("result") or {}).get("known_issue")}
                        if (a.get("result") or {}).get("known_issue")
                        else {}
                    ),
                    # 应用试运行的记录（ADR 0197 第 1 层，只有码）：之前 / 之后的形状、给的提示 id、这之前做过的改动
                    **({"attempt": a["attempt"]} if a.get("attempt") else {}),
                }
                for a in audit
            ]
            rec["steps"] = (rec.get("fix") or {}).get("steps")
        rec.update(judge(app, project, item, before))
        if cond in ("A", "M"):  # 密钥不留在一次性配置目录里
            try:
                app.call(f"/api/ai/connections/{CONN_VENDOR}", method="DELETE", timeout=30)
            except Exception:  # noqa: BLE001
                pass


def _warmup(item: dict, work: Path, scratch: Path, timeout: int) -> dict:
    """M 组的「甲」：在同一个 work 里修一遍，只记结局（成没成、几步），不进结果表。"""
    warm_id = item["warmup"]
    warm = load_corpus()[warm_id]
    project = scratch / "warmup-project"
    shutil.rmtree(project, ignore_errors=True)
    materialize(warm, project)
    rec: dict = {}
    _fix_and_judge("M", project, work, warm, hash_tree(project), timeout, rec)
    return {
        "id": warm_id,
        "success": rec.get("success"),
        "steps": rec.get("steps"),
        "outcome": (rec.get("fix") or {}).get("outcome"),
    }


def _memory_shapes(work: Path) -> list[dict]:
    """修「乙」之前本机记住了什么：只要失败类型与改动种类（值不进结果）。"""
    p = work / "data" / "ai" / "fix_memory.json"
    try:
        rows = json.loads(p.read_text(encoding="utf-8")).get("entries") or []
    except (OSError, ValueError):
        return []
    return [{"shape": r.get("shape"), "kind": r.get("kind")} for r in rows if isinstance(r, dict)]


def one_run(cond: str, pid: str, item: dict, rep: int, timeout: int) -> dict:
    base = RUNS / cond / f"{pid}__{rep}"
    scratch = SCRATCH / f"{cond}-{pid}-{rep}"
    project = scratch / "project"
    shutil.rmtree(scratch, ignore_errors=True)
    work = base / "work"
    shutil.rmtree(base, ignore_errors=True)
    work.mkdir(parents=True)
    materialize(item, project)
    before = hash_tree(project)
    rec: dict = {
        "id": pid,
        "family": item["family"],
        "cond": cond,
        "rep": rep,
        "model": MODEL,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "solvable": item.get("solvable", True),
        # 环境题：这道故障在产品一侧是不是产品缺陷（summarize 的 misjudged_user_fault 用）
        **(
            {"product_defect": bool(item["fault"].get("product_defect"))}
            if is_env_item(item)
            else {}
        ),
    }
    t0 = time.time()
    # 未修前先在独立目录里真跑一遍：本来就出图的（已修家族）不再叫 agent / AI——前提不成立，记 already_ok
    pre_proj, pre_work = base / "pre-project", base / "pre-work"
    pre_work.mkdir(parents=True)
    materialize(item, pre_proj)
    extra = fault_env(item, base / "wheelhouse")  # 环境题：假 wheel 在隔离目录里现造；其它题 None
    with sidecar(pre_proj, pre_work, extra) as app:
        if extra is not None:
            pre = env_before(app, item)
        else:
            pre = summarize_report(run_prep(app, item["script"])["report"])
    rec["before"] = pre
    if cond == "M" and not item.get("warmup"):
        raise SystemExit(f"{pid} 没有 warmup：M 组只跑成对的「乙」题（corpus.json 里带 warmup 的）")
    if cond != "baseline" and pre["phase"] == "completed" and pre["figure_captured"] is True:
        rec.update(success=True, already_ok=True, final=pre, originals_changed=[], new_files=[])
        rec["wall_s_total"] = round(time.time() - t0, 1)
        rec["stuck"] = "none"
        return rec
    if extra is not None and cond in ("B", "M"):
        raise SystemExit(
            f"{pid} 是环境题：B / M 组不支持（MCP 与 warmup 接不上假 wheel 夹具），只跑 baseline / A"
        )
    if cond == "B":
        # B：agent 在项目目录里干活，自己的 Tavotto MCP（独立数据目录）；判分用另一个干净的 sidecar
        for d in ("data", "config", "home"):
            (work / d).mkdir(exist_ok=True)
        cfg = mcp_config(SCRATCH / "_mcp" / f"{cond}-{pid}-{rep}", work)
        agent = run_opencode(
            project, PROMPT_B.format(script=item["script"]), timeout, cfg, base / "agent.log"
        )
        rec.update(agent)
        rec["prompt_rev"] = PROMPT_B_REV
        with sidecar(project, work) as app:
            rec.update(judge(app, project, item, before))
    else:
        if cond == "M":
            # M：同一个数据目录里先修「甲」（warmup），本机记住的做法留在 work/data/ai/fix_memory.json，再修「乙」
            rec["warmup"] = _warmup(item, work, scratch, timeout)
            rec["memory_before"] = _memory_shapes(work)
        _fix_and_judge(cond, project, work, item, before, timeout, rec, extra)
    rec["wall_s_total"] = round(time.time() - t0, 1)
    rec["stuck"] = classify_stuck(rec)
    if rec["cond"] == "B":
        rec["tool_calls"] = len(rec.get("trajectory") or [])
    return rec


def scrub(text: str) -> str:
    """结果里不留本机路径 / 用户名：运行目录、源码树、家目录换成占位符。"""
    pairs = []
    for label, path in (
        ("<SCRATCH>", SCRATCH),
        ("<RUNS>", RUNS),
        ("<REPO>", ROOT),
        ("<PYTHON_BIN>", Path(PYTHON).parent),
        ("<HOME>", Path.home()),
    ):
        for variant in {str(path), str(path.resolve()), "/private" + str(path)}:
            pairs.append((variant, label))
    for old, new in sorted(pairs, key=lambda kv: -len(kv[0])):
        text = text.replace(old, new)
    text = re.sub(r"/Volumes/[^\s\"'\\:,)]+", "<VOLUME_PATH>", text)
    text = re.sub(r"/Users/[^\s\"'\\:,)]+", "<HOME_PATH>", text)
    return text.replace(Path.home().name, "<USER>") if Path.home().name else text


def append(cond: str, rec: dict) -> None:
    RESULTS.mkdir(exist_ok=True)
    with (RESULTS / f"{cond}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(scrub(json.dumps(rec, ensure_ascii=False)) + "\n")


def cmd_reparse(args) -> None:
    """解析规则改了：不重跑 agent，按运行目录里留下的 agent.log 重建 B 组的轨迹 / token，再整份 scrub 一遍。"""
    path = RESULTS / f"{args.cond}.jsonl"
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        log = RUNS / args.cond / f"{rec['id']}__{rec['rep']}" / "agent.log"
        if args.cond != "A" and log.exists():
            rec.update(parse_opencode(log.read_text(encoding="utf-8", errors="replace")))
            rec["tool_calls"] = len(rec["trajectory"])
            rec["stuck"] = classify_stuck(rec)
        out.append(scrub(json.dumps(rec, ensure_ascii=False)))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def cmd_list(args) -> None:
    for pid, it in load_corpus().items():
        print(f"{pid:28} {it['family']:22} {it['title']}")


def cmd_probe(args) -> None:
    """未修前的真实结局：每题起 sidecar、跑一次，打印 phase / outcome code。"""
    corpus = load_corpus()
    ids = args.only or list(corpus)
    for pid in ids:
        it = corpus[pid]
        base = RUNS / "probe" / pid
        shutil.rmtree(base, ignore_errors=True)
        (base / "work").mkdir(parents=True)
        materialize(it, base / "project")
        extra = fault_env(it, base / "wheelhouse")
        with sidecar(base / "project", base / "work", extra) as app:
            if extra is not None:
                summ = env_before(app, it)
            else:
                summ = summarize_report(run_prep(app, it["script"])["report"])
        print(pid, json.dumps(summ, ensure_ascii=False), flush=True)
        if extra is not None:
            # 环境题：失败形状 = 一键安装的稳定错误码，与题库 `fault.expect.code` 对
            want_code = (it["fault"].get("expect") or {}).get("code")
            if (summ.get("install") or {}).get("code") != want_code:
                print(
                    f"  !! corpus.json 的 fault.expect.code 是 {want_code!r}，实测 {(summ.get('install') or {}).get('code')!r}",
                    flush=True,
                )
            continue
        # 题库的 `hint` 要与实测对上（tests/test_fix_compat_corpus_hints.py 按最近一轮 baseline 核对）：对不上就当场说
        want = (it.get("hint") or {}).get("code")
        failing = summ.get("phase") != "completed"
        if failing and summ.get("outcome_code") != want:
            print(
                f"  !! corpus.json 的 hint.code 是 {want!r}，实测 {summ.get('outcome_code')!r}",
                flush=True,
            )
        elif not failing and want:
            print("  !! 实测已经出图（不用 AI），corpus.json 里这题的 hint 该去掉", flush=True)


def cmd_run(args) -> None:
    corpus = load_corpus()
    ids = args.only or [
        k
        for k, v in corpus.items()
        if (args.cond != "M" or v.get("warmup"))  # M 组只跑带 warmup 的「乙」题
        and not (is_env_item(v) and args.cond in ("B", "M"))  # 环境题只跑 baseline / A
    ]
    done = set()
    path = RESULTS / f"{args.cond}.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["id"], r["rep"]))
    for pid in ids:
        for rep in range(1, args.reps + 1):
            if (pid, rep) in done:
                continue
            try:
                rec = one_run(args.cond, pid, corpus[pid], rep, args.timeout)
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001 - 基础设施故障不记成「模型失败」
                print(
                    pid, rep, "INFRA_ERROR", f"{type(exc).__name__}: {str(exc)[:200]}", flush=True
                )
                continue
            errs = rec.get("agent_errors") or []
            if any("usage limit" in e or "go-limit" in e for e in errs):
                print(pid, rep, "额度用完，停", flush=True)
                return
            append(args.cond, rec)
            print(
                pid,
                rep,
                "OK" if rec["success"] else "fail",
                rec["stuck"],
                f"{rec['wall_s_total']}s",
                flush=True,
            )


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("probe")
    p.add_argument("--only", nargs="*")
    r = sub.add_parser("run")
    r.add_argument("cond", choices=["baseline", "A", "B", "C", "M"])
    r.add_argument("--only", nargs="*")
    r.add_argument("--reps", type=int, default=1)
    r.add_argument("--timeout", type=int, default=900)
    sub.add_parser("report")
    rp = sub.add_parser("reparse")
    rp.add_argument("cond", choices=["A", "B"])
    a = ap.parse_args()
    if a.cmd == "run" and a.cond == "C":
        sys.exit("C 组（Codex CLI）只预留接口，本次不跑（Codex 额度要省）")
    if a.cmd == "report":
        import summarize

        summarize.main()
        return
    {"list": cmd_list, "probe": cmd_probe, "run": cmd_run, "reparse": cmd_reparse}[a.cmd](a)


if __name__ == "__main__":
    main()
