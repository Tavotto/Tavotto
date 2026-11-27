"""评测：Agent 矢量编辑（ADR 0212 §五）——桌面改图助手（A）vs 通用编码 agent + Tavotto MCP 排版工具（B）。

结构、跑法、结果格式、密钥纪律、断点续跑、INFRA_ERROR / 额度停下的约定沿用 `scripts/eval/fix_compat/`。题库是 `corpus.json`
（由 `build_corpus.py` 生成），每题一个合成小项目 + 一句中文指令 + 对结果画布的可机判判据（`predicates.py`）。

子命令：
  list               列题库
  run A              桌面改图助手：真 sidecar + 一条 OpenCode Go 连接，`POST /api/vector-edit/plan` 指令模式 → 轮询 → 落到画布 → 判
  run Aref           接线自检（**不叫模型**）：同一条链路，`{canvas, ops}` 模式，补丁用题库标准答案（记 `simulated: true`）
  run B              插件 agent：`opencode run --standalone` + Tavotto MCP（排版场景 / 编辑 / 标注工具），读项目里的排版文件判
  report             汇总（`summarize.py`）

环境变量：
  PYTHON               起 sidecar / MCP 用的解释器（要有 matplotlib；几何题要 wasmtime）；缺省当前解释器。源码树经 PYTHONPATH 生效
  EVAL_A_API_KEY / OPENCODE_GO_API_KEY   A 组密钥：只经环境变量进来、经 `POST /api/ai/connections` 存进一次性配置目录；
                       绝不打印、不写进结果 / 日志 / 轨迹（sidecar 环境里置空，A 跑完删连接）
  EVAL_MODEL           缺省 opencode-go/deepseek-v4.1-flash（厂商 id/模型）
  EVAL_RESULTS         结果目录（缺省 results/）；EVAL_RUNS 运行目录；EVAL_SCRATCH B 组项目目录

**A 组落位口径**：后端回的 `ops` 是已过干跑的补丁；前端落位（TS）在 Python 里无法原样复现，所以这里用
`vectoredit.apply_ops(canvas.objects, plan.ops, panels, page)` 的结果判（与前端「应用」调用的是同一个补丁语义，核对与方案卡勾选不在内）。
A 组**按设计不能新增对象**：加对象题（0208 类）在 A 里的期望是进 `unhandled`、画布不动，不进 A 的分母。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
#: 被测的 Tavotto 源码树（缺省 = 本仓库）；评测另一个 worktree 上的修复时设 `EVAL_TREE=<路径>`，MCP 与 sidecar 用它的 src / 插件
TREE = Path(os.environ.get("EVAL_TREE") or ROOT).resolve()
sys.path.insert(0, str(TREE / "src"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(HERE))

import predicates  # noqa: E402
from opencode_runner import run_agent  # noqa: E402

CORPUS_JSON = HERE / "corpus.json"
RESULTS = Path(os.environ.get("EVAL_RESULTS") or (HERE / "results"))
RUNS = Path(os.environ.get("EVAL_RUNS", "/tmp/tavotto-vector-edit-runs"))
SCRATCH = Path(os.environ.get("EVAL_SCRATCH", "/tmp/tavotto-vector-edit-scratch"))
PYTHON = os.environ.get("PYTHON") or sys.executable
MODEL = os.environ.get("EVAL_MODEL", "opencode-go/deepseek-v4.1-flash")
CONN_VENDOR, _, CONN_MODEL = MODEL.partition("/")
A_KEY_VARS = ("EVAL_A_API_KEY", "OPENCODE_GO_API_KEY")
LAYOUT = "组图"
CANVAS_ID = "c_main0001"
SIDECAR_ENV = {
    "TAVOTTO_USER_ENV_DISCOVERY": "0",
    "PYTHONPATH": str(TREE / "src"),
    "OPENCODE_GO_API_KEY": "",
    "EVAL_A_API_KEY": "",
}
_LOCK = threading.Lock()


def load_corpus() -> dict:
    return json.loads(CORPUS_JSON.read_text(encoding="utf-8"))


def tasks_of(corpus: dict, only: list[str] | None) -> list[dict]:
    ts = corpus["tasks"]
    return [t for t in ts if not only or t["id"] in only]


# ---------------------------------------------------------------- 画布 / 落位


def _canvas_of(corpus: dict, task: dict) -> dict:
    c = task["canvas"]
    return {"id": CANVAS_ID, "name": "图版 1", "page": c["page"], "objects": c["objects"]}


def land_ops(canvas: dict, ops: list) -> tuple[list[dict], str | None]:
    """补丁落到画布副本上 → (改后的全部对象, 失败码)。没有补丁 = 原样。"""
    import copy

    from tavotto.engine import layoutannotate as la, vectoredit as ve

    objs = copy.deepcopy(canvas["objects"])
    if not ops:
        return objs, None
    try:
        panels, _refs, _un = la.measure_panels(canvas, None, ve.unmeasured_panel)
        res = ve.apply_ops(objs, ops, panels, la._page(canvas))  # noqa: SLF001
    except ve.EditError as exc:
        return objs, f"apply_failed:{exc.code}"
    return res["objects"], None


# ---------------------------------------------------------------- 判分与归类


def a_expectation(task: dict) -> str:
    """A 组对这题的期望：`change`（编辑题，按判据）/ `unhandled`（对照题与加对象题：画布不动、进 unhandled）。"""
    return "change" if task["kind"] == "edit" else "unhandled"


def judge_a(task: dict, before: list[dict], after: list[dict], rec: dict) -> dict:
    if a_expectation(task) == "change":
        got = predicates.evaluate(task, before, after)
    else:
        unchanged = before == after
        said = bool(rec.get("unhandled"))
        got = {
            "ok": unchanged and said,
            "checks": [
                {
                    "t": "unchanged",
                    "ok": unchanged,
                    "detail": "画布没动" if unchanged else "画布被动了",
                },
                {
                    "t": "unhandled_said",
                    "ok": said,
                    "detail": f"unhandled {len(rec.get('unhandled') or [])} 条",
                },
            ],
        }
    return {"success": got["ok"], "predicate_detail": got["checks"]}


_ID_CODES = ("unknown_object",)
_VALUE_CODES = ("bad_field", "bad_value", "out_of_range", "duplicate_target")


def classify_a(rec: dict, task: dict) -> str:
    if rec.get("success"):
        return "none"
    if rec.get("timed_out"):
        return "超时"
    if rec.get("state") == "failed":
        return f"工具报错:{rec.get('code')}"
    if rec.get("land_error"):
        return f"工具报错:{rec['land_error']}"
    ops, unh = rec.get("ops") or [], rec.get("unhandled") or []
    codes = [u.get("code") for u in unh if isinstance(u, dict) and u.get("code")]
    if a_expectation(task) == "unhandled":
        return "该进 unhandled 没进（动了画布）" if ops else "没有进 unhandled（没说原因）"
    if not ops:
        if any(c in _ID_CODES for c in codes):
            return "模型给错 id"
        if any(c in _VALUE_CODES for c in codes):
            return "模型给错值/字段"
        if "kernel_unavailable" in codes:
            return "工具报错:kernel_unavailable"
        return "该做的进了 unhandled" if unh else "没给任何补丁"
    return "用错操作或值不对（判据未过）"


def b_completed(rec: dict) -> bool:
    """agent 完整跑完：没超时、没有 agent 报错。没跑完的运行不能给任何题记成功（对照题「画布没动」尤其会被白送）。"""
    return not rec.get("timed_out") and not rec.get("agent_errors")


def classify_b(rec: dict, task: dict) -> str:
    # 先看是否跑完，再看成败：超时 / 报错的运行不会因为画布碰巧没动或判据碰巧过而变成「成功」
    if rec.get("timed_out"):
        return "超时"
    if rec.get("agent_errors"):
        return "模型/运行报错"
    if rec.get("success"):
        return "none"
    traj = rec.get("trajectory") or []
    names = [t["tool"] for t in traj]
    layout_calls = [n for n in names if "tavotto_layout" in n]
    applied = [n for n in names if n.endswith(("_edit_apply", "_annotate_apply"))]
    changed = bool(rec.get("layout_changed"))
    if task["kind"] in ("control",):
        return "该不动却动了" if changed else "判据未过"
    if not layout_calls:
        return "绕过工具直接改文件" if changed else "没用 Tavotto 排版工具"
    errs = [t for t in traj if "tavotto_layout" in t["tool"] and t.get("error")]
    stale = sum("layout_changed" in (t.get("result") or "") for t in traj)
    if not applied:
        if stale >= 2:
            return "拿不到 base_revision（工具文本里没有，反复 layout_changed）"
        return "工具报错（没写入成功）" if errs else "只出方案没写入"
    if any("unknown_object" in (t.get("result") or "") for t in traj):
        return "模型给错 id"
    return "用错操作或值不对（判据未过）"


# ---------------------------------------------------------------- A 组


def connect_builtin(app) -> dict:
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
        "id": conn.get("id") or CONN_VENDOR,
        "model": conn.get("default_model") or conn.get("model"),
    }
    if not body.get("ok") or conn.get("state") not in ("ready", "ok", "connected"):
        raise SystemExit(f"{CONN_VENDOR} 连接没存成 / 不可用，A 组中止：{summary}")
    return summary


def run_a_task(
    app, corpus: dict, task: dict, rep: int, mode: str, conn: dict | None, timeout: float
) -> dict:
    """A / Aref 的一次运行。mode = `instruction`（真模型）/ `ops`（题库标准答案，模拟）。"""
    canvas = _canvas_of(corpus, task)
    before = canvas["objects"]
    rec: dict = {
        "id": task["id"],
        "kind": task["kind"],
        "family": task["family"],
        "cond": "A" if mode == "instruction" else "Aref",
        "rep": rep,
        "model": MODEL if mode == "instruction" else None,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "expect": a_expectation(task),
        "in_denominator": a_expectation(task) == "change",
        "simulated": mode != "instruction",
    }
    t0 = time.time()
    if mode == "ops":
        ref = task["reference"]
        if ref["kind"] != "ops":  # 没有编辑补丁形态的标准答案：Aref 只验空补丁路径（对照）
            rec.update(state="skipped", ops=[], unhandled=[{"reason": "模拟：无补丁"}])
            ops, unhandled = [], rec["unhandled"]
        else:
            body = {"canvas": canvas, "ops": ref["ops"]}
            st, plan = app.call("/api/vector-edit/plan", body, timeout=120)
            rec.update(
                state="ready",
                http=st,
                plan_ok=plan.get("ok"),
                errors=plan.get("errors"),
                echo=plan.get("echo"),
            )
            ops, unhandled = ref["ops"] if plan.get("ok") else [], []
            rec.update(ops=ops, unhandled=unhandled)
    else:
        body = {
            "canvas": canvas,
            "instruction": task["instruction"],
            "selection": task.get("selection") or [],
            "locale": "zh-CN",
            "connection": conn["id"],
            "model": CONN_MODEL,
        }
        st, started = app.call("/api/vector-edit/plan", body, timeout=60)
        if st not in (200, 202) or "plan_id" not in started:
            rec.update(
                state="failed",
                code=started.get("code") or started.get("error") or f"http_{st}",
                ops=[],
                unhandled=[],
            )
        else:
            pid = started["plan_id"]
            state: dict = {}
            while time.time() - t0 < timeout:
                st, state = app.call(f"/api/vector-edit/plan/{pid}", timeout=30)
                if state.get("state") in ("ready", "failed"):
                    break
                time.sleep(0.5)
            else:
                rec["timed_out"] = True
            res = state.get("plan") or {}
            rec.update(
                state=state.get("state", "running"),
                code=state.get("code"),
                detail=str(state.get("detail") or "")[:200],
                plan_ok=res.get("ok"),
                errors=res.get("errors"),
                ops=res.get("ops") or [],
                unhandled=res.get("unhandled") or [],
                echo=res.get("echo"),
                checks=[
                    {
                        k: c.get(k)
                        for k in ("kind", "level", "code")
                        if isinstance(c, dict) and k in c
                    }
                    for c in (res.get("checks") or [])
                ][:10]
                if isinstance(res.get("checks"), list)
                else None,
            )
    rec["wall_s"] = round(time.time() - t0, 1)
    after, err = land_ops(canvas, rec.get("ops") or [])
    rec["land_error"] = err
    rec.update(judge_a(task, before, after, rec))
    rec["stuck"] = classify_a(rec, task)
    return rec


def cmd_run_a(args, mode: str) -> None:
    from support import foundation_app as fa

    corpus = load_corpus()
    tasks = tasks_of(corpus, args.only)
    cond = "A" if mode == "instruction" else "Aref"
    done = _done(cond)
    work = RUNS / cond / "work"
    shutil.rmtree(work.parent, ignore_errors=True)
    project = RUNS / cond / "project"
    project.mkdir(parents=True)
    work.mkdir(parents=True)
    with fa.sidecar_app(project, work, env_overrides=SIDECAR_ENV) as app:
        conn = connect_builtin(app) if mode == "instruction" else None
        try:
            for task in tasks:
                for rep in range(1, args.reps + 1):
                    if (task["id"], rep) in done:
                        continue
                    try:
                        rec = run_a_task(app, corpus, task, rep, mode, conn, args.timeout)
                    except Exception as exc:  # noqa: BLE001 - 基础设施故障不记成「模型失败」
                        print(
                            task["id"],
                            rep,
                            "INFRA_ERROR",
                            f"{type(exc).__name__}: {str(exc)[:200]}",
                            flush=True,
                        )
                        continue
                    if rec.get("code") in ("change_plan_model_quota",):
                        print(task["id"], rep, "额度用完，停", flush=True)
                        return
                    append(cond, rec)
                    print(
                        task["id"],
                        rep,
                        "OK" if rec["success"] else "fail",
                        rec["stuck"],
                        f"{rec['wall_s']}s",
                        flush=True,
                    )
        finally:
            if conn:
                try:
                    app.call(f"/api/ai/connections/{CONN_VENDOR}", method="DELETE", timeout=30)
                except Exception:  # noqa: BLE001
                    pass


# ---------------------------------------------------------------- B 组

#: agent 只能经 Tavotto MCP 改排版：不给 shell / 编辑权限（否则 agent 直接改 JSON，测不到工具面）；只读项目文件与联网仍拒
PERMISSION = {
    "edit": "deny",
    "bash": "deny",
    "shell": "deny",
    "execute": "deny",
    "webfetch": "deny",
    "websearch": "deny",
    "external_directory": "deny",
    "opencode_session_move": "deny",
    "task": "deny",
}
PROMPT_B = """{instruction}

这是一个 Tavotto 项目：目录 {project}（就是当前目录），排版名「{layout}」（第 1 页，画布 id {canvas}）。{selection}请在 Tavotto 里改这个排版，做完回复 DONE。"""
PROMPT_B_REV = 1


def make_project(corpus: dict, task: dict, dest: Path) -> Path:
    """B 组项目：合成子图脚本 + 登记表 + 生成的 FigM.pdf + 排版文件（画布 = 题目的画布）。"""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    (dest / "figm.py").write_text(corpus["figure_script"], encoding="utf-8")
    (dest / "tavotto_registry.json").write_text(json.dumps(corpus["registry"]), encoding="utf-8")
    proc = subprocess.run(  # noqa: S603
        [PYTHON, str(dest / "figm.py")], cwd=dest, capture_output=True, text=True, timeout=120
    )
    if proc.returncode or not (dest / "FigM.pdf").is_file():
        raise RuntimeError(f"子图脚本没跑成：{proc.stderr[-300:]}")
    doc = {
        "schema": 3,
        "project": {"id": "p_test0001", "name": "t"},
        "canvases": [
            {
                "id": CANVAS_ID,
                "name": "图版 1",
                "page": task["canvas"]["page"],
                "objects": task["canvas"]["objects"],
                "guides": [],
            }
        ],
        "activeCanvasId": CANVAS_ID,
        "createdAt": 0,
        "updatedAt": 0,
    }
    store = dest / "tavottofile"
    store.mkdir()
    (store / f"{LAYOUT}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return dest


def read_layout_objects(project: Path) -> list[dict]:
    from tavotto.engine import layoutannotate as la

    return la.open_layout(project, LAYOUT).doc["canvases"][0]["objects"]


def mcp_config(wrapper_dir: Path, project: Path, work: Path) -> str:
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    sh = wrapper_dir / "mcp.sh"
    env = {
        "TAVOTTO_MCP_ROOTS": str(project),
        "TAVOTTO_DATA_DIR": str(work / "data"),
        "TAVOTTO_CONFIG_DIR": str(work / "config"),
        "HOME": str(work / "home"),
        "TAVOTTO_USER_ENV_DISCOVERY": "0",
        "PYTHONPATH": str(TREE / "src"),
        "TAVOTTO_NO_UPDATE_CHECK": "1",
        "TAVOTTO_NO_TELEMETRY": "1",
    }
    lines = ["#!/bin/sh"] + [f"export {k}='{v}'" for k, v in env.items()]
    server = f"'{PYTHON}' '{TREE / 'codex-plugin' / 'mcp' / 'server.py'}'"
    lines.append(f"exec {server}")
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


_RESULT_CODE = re.compile(r'"(?:code|error)"\s*:\s*"([A-Za-z0-9_.:-]{2,60})"')


def parse_opencode(text: str) -> dict:
    """opencode `--format json` 事件流 → 工具调用序列 + token + 错误。`tavotto_layout_*` 的参数记全（ops / items 是失败分析的主证据）。"""
    steps = tin = tout = 0
    traj: list[dict] = []
    errors: list[str] = []
    last_text = ""
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
        if part.get("type") == "text" and part.get("text"):
            last_text = part["text"]
        if part.get("type") == "tool":
            st = part.get("state") or {}
            name = part.get("tool", "?")
            inp = st.get("input") or {}
            out = st.get("output")
            outs = (
                out
                if isinstance(out, str)
                else json.dumps(out, ensure_ascii=False)
                if out is not None
                else ""
            )
            row = {
                "tool": name,
                "args": _trim(inp, 4000 if "layout" in name else 160),
                "status": st.get("status"),
                "result": _trim(outs, 400)
                if "layout" in name
                else (_RESULT_CODE.search(outs) or [None, _trim(outs, 80)])[1]
                if outs
                else _trim(st.get("error"), 160),
            }
            if st.get("status") == "error" or re.search(
                r'"isError"\s*:\s*true|"ok"\s*:\s*false|"errors"\s*:\s*\[\s*\{', outs
            ):
                row["error"] = True
            traj.append(row)
        if ev.get("type") == "error":
            errors.append(_trim(ev, 300))
    return {
        "steps": steps,
        "tokens_in": tin,
        "tokens_out": tout,
        "trajectory": traj,
        "agent_errors": errors[:5],
        "final_text": _trim(last_text, 400),
    }


def run_opencode(cwd: Path, prompt: str, timeout: int, config: str, log: Path) -> dict:
    env = {
        **os.environ,
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "PWD": str(cwd),
        "OPENCODE_CONFIG_CONTENT": config,
        "PATH": f"{Path(PYTHON).parent}:{os.environ['PATH']}",
    }
    for v in A_KEY_VARS:
        env.pop(v, None)
    argv = ["opencode", "run", "--standalone", "-m", MODEL, "--format", "json", prompt]
    timed_out, wall_s = run_agent(cwd, argv, env, timeout, log)
    text = log.read_text(encoding="utf-8", errors="replace")
    return {**parse_opencode(text), "wall_s": wall_s, "timed_out": timed_out}


def run_b_task(corpus: dict, task: dict, rep: int, timeout: int, cond: str = "B") -> dict:
    base = RUNS / cond / f"{task['id']}__{rep}"
    shutil.rmtree(base, ignore_errors=True)
    work = base / "work"
    for d in ("data", "config", "home"):
        (work / d).mkdir(parents=True)
    project = make_project(corpus, task, SCRATCH / f"{cond}-{task['id']}-{rep}" / "project")
    before = read_layout_objects(project)
    before_bytes = (project / "tavottofile" / f"{LAYOUT}.json").read_bytes()
    sel = task.get("selection")
    prompt = PROMPT_B.format(
        instruction=task["instruction"],
        project=project,
        layout=LAYOUT,
        canvas=CANVAS_ID,
        selection=(f"我当前选中的对象 id：{'、'.join(sel)}。" if sel else ""),
    )
    cfg = mcp_config(SCRATCH / "_mcp" / f"{cond}-{task['id']}-{rep}", project, work)
    rec: dict = {
        "id": task["id"],
        "kind": task["kind"],
        "family": task["family"],
        "cond": cond,
        "rep": rep,
        "model": MODEL,
        "prompt_rev": PROMPT_B_REV,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "simulated": False,
        "in_denominator": True,
    }
    rec.update(run_opencode(project, prompt, timeout, cfg, base / "agent.log"))
    after = read_layout_objects(project)
    rec["layout_changed"] = (
        project / "tavottofile" / f"{LAYOUT}.json"
    ).read_bytes() != before_bytes
    got = predicates.evaluate(task, before, after)
    rec["completed"] = b_completed(rec)
    rec["success"], rec["predicate_detail"] = got["ok"] and rec["completed"], got["checks"]
    rec["tool_calls"] = len(rec["trajectory"])
    rec["stuck"] = classify_b(rec, task)
    return rec


def cmd_run_b(args) -> None:
    corpus = load_corpus()
    tasks = tasks_of(corpus, args.only)
    cond = args.cond
    done = _done(cond)
    todo = [(t, r) for t in tasks for r in range(1, args.reps + 1) if (t["id"], r) not in done]
    stop = threading.Event()

    def one(item):
        task, rep = item
        if stop.is_set():
            return
        try:
            rec = run_b_task(corpus, task, rep, args.timeout, cond)
        except Exception as exc:  # noqa: BLE001
            print(
                task["id"],
                rep,
                "INFRA_ERROR",
                f"{type(exc).__name__}: {str(exc)[:200]}",
                flush=True,
            )
            return
        errs = (rec.get("agent_errors") or []) + [rec.get("final_text") or ""]
        if any("usage limit" in e or "go-limit" in e for e in errs):
            print(task["id"], rep, "额度用完，停", flush=True)
            stop.set()
            return
        if not rec["completed"] and not args.keep_incomplete:
            # 超时 / agent 报错：不是「这题没做成」的证据（可能只是慢、断连），不落结果，重跑即可；要留档用 --keep-incomplete
            print(task["id"], rep, "INCOMPLETE（不记结果，可重跑）", rec["stuck"], flush=True)
            return
        append(cond, rec)
        print(
            task["id"],
            rep,
            "OK" if rec["success"] else "fail",
            rec["stuck"],
            f"{rec['wall_s']}s",
            flush=True,
        )

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        list(ex.map(one, todo))


# ---------------------------------------------------------------- 结果文件


def scrub(text: str) -> str:
    """结果里不留本机路径 / 用户名。"""
    pairs = []
    for label, path in (
        ("<SCRATCH>", SCRATCH),
        ("<RUNS>", RUNS),
        ("<REPO>", ROOT),
        ("<TREE>", TREE),
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
    RESULTS.mkdir(parents=True, exist_ok=True)
    with _LOCK, (RESULTS / f"{cond}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(scrub(json.dumps(rec, ensure_ascii=False)) + "\n")


def _done(cond: str) -> set:
    path = RESULTS / f"{cond}.jsonl"
    out = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            out.add((r["id"], r["rep"]))
    return out


def cmd_rejudge(args) -> None:
    """判据改了：不重跑 agent，按运行后留在 `EVAL_SCRATCH/<组>-<题>-<rep>/project` 的排版文件重新判分，原地改写 `<组>.jsonl`。"""
    corpus = load_corpus()
    tasks = {t["id"]: t for t in corpus["tasks"]}
    path = RESULTS / f"{args.cond}.jsonl"
    out, missing = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        task = tasks[rec["id"]]
        proj = SCRATCH / f"{args.cond}-{rec['id']}-{rec['rep']}" / "project"
        if not (proj / "tavottofile").is_dir():
            missing.append(rec["id"])
            out.append(line)
            continue
        got = predicates.evaluate(task, task["canvas"]["objects"], read_layout_objects(proj))
        rec["completed"] = b_completed(rec)
        rec["rejudged"] = True
        rec["success"], rec["predicate_detail"] = got["ok"] and rec["completed"], got["checks"]
        rec["stuck"] = classify_b(rec, task)
        out.append(scrub(json.dumps(rec, ensure_ascii=False)))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"重判 {len(out) - len(missing)} 行；找不到运行目录没重判：{missing or '无'}")


def cmd_list(args) -> None:
    for t in load_corpus()["tasks"]:
        print(f"{t['id']:24} {t['kind']:8} {t['family']:12} {t['instruction']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("run")
    r.add_argument("cond", choices=["A", "Aref", "B"])
    r.add_argument("--only", nargs="*")
    r.add_argument("--reps", type=int, default=1)
    r.add_argument("--timeout", type=int, default=600)
    r.add_argument(
        "--keep-incomplete", action="store_true", help="B 组：超时 / agent 报错的运行也记成失败结果"
    )
    r.add_argument("--jobs", type=int, default=1, help="B 组并发数")
    rj = sub.add_parser("rejudge")
    rj.add_argument("cond", choices=["B", "B2"])  # B2 只留着重判已有结果
    sub.add_parser("report")
    a = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    if a.cmd == "report":
        import summarize

        summarize.main()
        return
    if a.cmd == "list":
        cmd_list(a)
    elif a.cmd == "rejudge":
        cmd_rejudge(a)
    elif a.cond == "B":
        cmd_run_b(a)
    else:
        cmd_run_a(a, "instruction" if a.cond == "A" else "ops")


if __name__ == "__main__":
    main()
