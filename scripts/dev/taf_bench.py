"""TAF Harness Lab · 基准跑分（ADR 0138 Phase 6b，`06-benchmark-plan.md`）。开发侧工具，不进产品。

在 FigureBench 的改图场景上**端到端**跑真 app（Flask 测试客户端）+ 真 AI 桥 + 真 safe worker，只把「理解指令」换成
回放：假 CLI 在候选目录里应用场景登记的编辑（参考解 `reference_solution` 或反例 `negative_variants`）。于是候选、L1、
提交、渲染层验证全部真实运行，跑出的就是基础设施的基线；`--real`（真实模型）在 6c，不在这里。

每个场景：改前渲染一次（基线 manifest）→ `/api/ai/run`（TAF 开关打开）→ 读任务结局 → 再渲染一次（改后 manifest，已
built、不多执行）→ 用场景自己的目标 / 保持断言（`figureverify.verify`）判。报告字段见 `06-benchmark-plan.md` §三。

隔离：数据目录与配置目录在 `--work` 下（`TAVOTTO_DATA_DIR` / `TAVOTTO_CONFIG_DIR` 在 import 产品模块之前设好），
TAF 开关只在本进程里打开，**不碰用户的任何配置**。默认只读 dev 场景；`--holdout` 只出汇总分（不出逐场景明细）。

用法：
    python scripts/dev/taf_bench.py --work /tmp/taf-bench --out report.json [--negatives] [--only FB-S02,FB-S03]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# Windows 上被捕获 / 重定向时中文报告不能静默丢掉：两条流都钉成 UTF-8（tests/test_windows_regressions.py 看护）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "figurebench"
SPLITS = {"dev": FIXTURES / "scenarios.json", "holdout": FIXTURES / "scenarios_holdout.json"}

#: 报告里逐场景的字段（闭集；`--holdout` 只出 `metrics`）
ROW_FIELDS = (
    "id",
    "variant",
    "outcome",
    "figure_verdict",
    "goal",
    "preserve",
    "unintended",
    "rounds",
    "wall_s",
    "error",
    "prompt_sha256",
    "rep",
)
#: `--real` 一次最多跑多少次**实际 Agent CLI 调用**（真实模型要花用户的额度；开跑前按「场景数 × 每任务最大调用数」
#: 拒绝，运行中按真实调用次数封顶，不悄悄截断）
DEFAULT_MAX_REAL_RUNS = 30


def agent_runs_per_task() -> int:
    """一个改图任务最多起几次 Agent CLI：读任务契约（`ai_figure_edit.envelope_for` 的 budget），不在这里写死。"""
    from tavotto.engine import ai_figure_edit

    env = ai_figure_edit.envelope_for(
        agent="codex",
        project_key="bench",
        script_rel="figure.py",
        prompt="bench",
        rid=None,
        base_sha256="0" * 64,
    )
    return env.budget.max_agent_runs


def load_scenarios(split: str) -> list[dict]:
    return json.loads(SPLITS[split].read_text(encoding="utf-8"))["scenarios"]


def benchable(s: dict) -> bool:
    """能回放的改图场景：探针可核验、有参考解。需要真实模型 / 主观判断的不在 6b。"""
    return (
        s.get("task_type") == "figure_edit"
        and s.get("check") == "probe"
        and bool(s.get("reference_solution"))
        and not s.get("needs_real_model")
    )


# --------------------------------------------------------------------------- 防泄漏（Harness² 的 assert_no_leak 同一思路）
class LeakError(AssertionError):
    pass


def holdout_markers(holdout: list[dict]) -> set[str]:
    """holdout 场景里不许出现在优化产物（候选 Pack、经验条目、轨迹）里的字符串：id、指令原文、断言 id 与期望文字。"""
    out: set[str] = set()
    for s in holdout:
        out.add(s["id"])
        out.add(s["instruction"])
        for a in [*s.get("goal", []), *s.get("preserve", [])]:
            out.add(a.get("id", ""))
            exp = a.get("expected")
            if isinstance(exp, str):
                out.add(exp)
    return {m for m in out if len(m) >= 4}


def assert_no_leak(texts, holdout: list[dict]) -> None:
    """`texts` 里任何一段含有 holdout 标记就抛 `LeakError`（列出命中的标记，不打印 holdout 原文之外的东西）。"""
    markers = holdout_markers(holdout)
    hits = sorted({m for t in texts for m in markers if m in t})
    if hits:
        raise LeakError(f"优化产物里出现了 holdout 内容：{hits[:10]}")


# --------------------------------------------------------------------------- 回放用的假 CLI
_REPLAY = r"""
import json, pathlib, sys
cwd = pathlib.Path(sys.argv[1]); edits = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
p = cwd / "figure.py"; src = p.read_text(encoding="utf-8")
for e in edits:
    n = src.count(e["find"])
    if n != e["count"]:
        sys.exit(f"edit anchor {e['find']!r} occurs {n} times, expected {e['count']}")
    src = src.replace(e["find"], e["replace"])
p.write_text(src, encoding="utf-8")
"""


def _metrics(rows: list[dict], aggregate_only: bool = False) -> dict:
    """`06-benchmark-plan.md` §三：分母只算真的跑了的（not_run 单列，不进分母）。

    `aggregate_only`（holdout）：`not_run` 只给数量，不出 `id/variant` 列表——汇总里不许带逐场景信息。"""
    ran = [r for r in rows if r["outcome"] is not None]
    ref = [r for r in ran if r["variant"] == "ref"]
    neg = [r for r in ran if r["variant"] != "ref"]

    def rate(items, pred):
        return round(sum(1 for r in items if pred(r)) / len(items), 4) if items else None

    def goal_ok(r):
        # 没有目标断言的场景（只有保持类，例如 FB-V01）目标记 not_applicable，算满足
        return r["goal"] in ("verified", "not_applicable")

    def final_ok(r):
        return r["outcome"] == "completed" and r["figure_verdict"] == "verified" and goal_ok(r)

    not_run = [f"{r['id']}/{r['variant']}" for r in rows if r["outcome"] is None]
    walls = sorted(r["wall_s"] for r in ran if r["wall_s"] is not None)
    return {
        "scenarios_run": len(ran),
        **({"not_run_count": len(not_run)} if aggregate_only else {"not_run": not_run}),
        # 首发 = 真正的第一轮提交前检查就过（rounds == 1）；修正过再成功的算最终通过、不算首发
        "first_attempt_verified_rate": rate(ref, lambda r: r["rounds"] == 1 and final_ok(r)),
        "final_verified_completion_rate": rate(ref, final_ok),
        "scientific_preservation_rate": rate(ref, lambda r: r["preserve"] == "verified"),
        # `unintended` 行字段只表示用户文件比较；§三口径是「非目标文件变化或保持断言 failed」，这里合并
        "unintended_modification_rate": rate(
            ref, lambda r: r["unintended"] or r["preserve"] == "failed"
        ),
        "negatives_caught_rate": rate(
            neg, lambda r: r["goal"] == "failed" or r["preserve"] == "failed"
        ),
        # TAF 自己判 completed、基准却判反例违反了目标 / 保持断言：任务结局对用户「说多了」的比例。提交后验证目前只核
        # 数据指纹（`ai_figure_edit._render_checks`），文字 / 单位 / 配色 / 刻度类改动它看不见——这是 Harness Lab 要压低的数
        "taf_overclaim_rate": rate(
            [r for r in neg if r["goal"] == "failed" or r["preserve"] == "failed"],
            lambda r: r["outcome"] == "completed",
        ),
        "wall_s_median": walls[len(walls) // 2] if walls else None,
        "wall_s_p95": walls[min(len(walls) - 1, int(len(walls) * 0.95))] if walls else None,
    }


def _agent_resolution(agent: str):
    """与 `ai_bridge._cmd` 同一份解析结果（`ai_agents.resolve`，带缓存）：实际启动的可执行与版本以它为准，
    不再自己在 PATH 里 `which`（自定义路径 / 常见位置回退 / 内置都会让两者分叉）。"""
    from tavotto.engine import ai_agents

    definition = ai_agents.get_agent(agent)
    if definition is None:
        return None
    try:
        return ai_agents.resolve(definition)
    except Exception:  # noqa: BLE001 —— 探测失败只是报告里少一项
        return None


def _endpoint_identity(endpoint: dict | None) -> dict:
    """接口的**非敏感**身份：id / label / base_url 的 host / `{id, base_url}` 的 sha256 前 12 位。
    绝不带 api_key、token、完整 URL（路径与 query 可能含凭据）。没有接口 = CLI 默认账号。"""
    if not endpoint:
        return {"kind": "cli_default"}
    from urllib.parse import urlsplit

    base_url = str(endpoint.get("base_url") or "")
    try:
        host = urlsplit(base_url).hostname
    except ValueError:
        host = None
    ident = json.dumps({"id": endpoint.get("id"), "base_url": base_url}, sort_keys=True)
    return {
        "kind": "provider",
        "id": endpoint.get("id"),
        "label": endpoint.get("label"),
        "host": host,
        "fingerprint": hashlib.sha256(ident.encode("utf-8")).hexdigest()[:12],
    }


def _environment(app_module, executor: str = "replay") -> dict:
    from tavotto.engine import ai_prompt_pack, pool

    def git_sha():
        try:
            return subprocess.run(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    try:
        py = pool.find_worker_python()
        mpl = subprocess.run(
            [py, "-c", "import matplotlib; print(matplotlib.__version__)"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 —— 环境探测失败只是报告里少一项
        mpl = None
    return {
        "git_sha": git_sha(),
        "pack": {"id": ai_prompt_pack.DEFAULT.pack_id, "version": ai_prompt_pack.DEFAULT.version},
        "executor": executor,
        "os": sys.platform,
        "python": sys.version.split()[0],
        "matplotlib": mpl,
    }


# --------------------------------------------------------------------------- 一次运行
def run(
    scenarios: list[dict],
    work: Path,
    *,
    negatives: bool = False,
    real_agent: str | None = None,
    pack: dict | None = None,
    model: str | None = None,
    effort: str | None = None,
    max_runs: int | None = None,
    repeat: int = 1,
) -> dict:
    """在 `work` 下逐场景跑，回 `{environment, rows, metrics}`。调用方负责 `TAVOTTO_DATA_DIR` 等已指向隔离目录。

    `real_agent`（6c）：不回放，交给真实的编码 Agent CLI（产品同一条 `/api/ai/run` 路径、同一套 sandbox 参数），只跑参考解
    那一档（反例是回放专用）。`pack`：本次运行改用这份候选 Prompt Pack（`ai_prompt_pack.load` 校验；只在本进程里替换
    `DEFAULT`，跑完恢复——产品默认值不由工具改）。
    `model` / `effort`：透传进 `/api/ai/run`，两臂钉住同一个。`max_runs`：实际 Agent CLI 调用次数上限（含修正那次），
    到了之后再来的调用不 spawn，该次任务记 `max_runs_reached`。"""
    from tavotto import app as m
    from tavotto.engine import (
        ai_bridge,
        ai_figure_edit,
        ai_tasks,
        config,
        discover,
        figureverify,
        pool,
        project_watch,
    )

    if real_agent:
        # 回放留下的参考解（`<base>/_replay`）不能被同一 base 下的真实运行读到：先删掉本工具建的目录，
        # 再兜底——base 下任何残留的 `*.edits.json` 都拒绝运行（此时还没调任何 CLI）
        shutil.rmtree(work.parent / "_replay", ignore_errors=True)
        if work.parent.is_dir():
            leftovers = sorted(work.parent.rglob("*.edits.json"))
            if leftovers:
                raise RuntimeError(
                    f"{work.parent} 下有残留的参考解文件（{leftovers[0]} 等），真实模式拒绝运行；请换一个干净的 --work"
                )

    real_settings, real_cmd = config.ai_settings, ai_bridge._cmd
    real_usable, real_resolve, real_env = (
        ai_bridge.require_usable,
        ai_bridge.ai_providers.resolve,
        ai_bridge._spawn_env,
    )
    from tavotto.engine import ai_prompt_pack

    real_pack = ai_prompt_pack.DEFAULT
    edits_box: dict = {}

    calls = {"n": 0, "executable": None, "endpoint": None}

    def count_call():
        if max_runs is not None and calls["n"] >= max_runs:
            edits_box["blocked"] = True
            raise ai_bridge.AgentError("max_runs_reached")
        calls["n"] += 1

    def replay_cmd(agent_id, prompt, cwd, model=None, effort=None, endpoint=None):
        count_call()
        edits_box["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return [sys.executable, "-c", _REPLAY, cwd, edits_box["path"]], {}

    def real_cmd_recording(agent_id, prompt, cwd, model=None, effort=None, endpoint=None):
        count_call()
        edits_box["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        cmd, env = real_cmd(agent_id, prompt, cwd, model=model, effort=effort, endpoint=endpoint)
        calls["executable"] = cmd[0]  # 实际 spawn 的那一个
        calls["endpoint"] = _endpoint_identity(endpoint)
        return cmd, env

    def overlay_settings():
        """叠加而不是替换：用户配置的第三方接口等其余设置原样保留，只在本进程里把 TAF 打开（不写配置）。"""
        base = real_settings() or {}
        return {**base, "taf": {**(base.get("taf") or {}), "enabled": True}}

    config.ai_settings = overlay_settings
    if pack is not None:
        ai_prompt_pack.DEFAULT = ai_prompt_pack.load(pack)
    replay = real_agent is None
    if replay:
        ai_bridge._cmd = replay_cmd
        ai_bridge.require_usable = lambda agent: {}
        ai_bridge.ai_providers.resolve = lambda agent, endpoint_id: None
        ai_bridge._spawn_env = lambda cmd0, extra: dict(os.environ)
    else:
        # 真实 CLI：Agent 可用性、接口选择、spawn 环境全走产品路径；只旁路记一下提示词的 sha256（不落原文）
        ai_bridge._cmd = real_cmd_recording
        negatives = False
    agent = real_agent or "codex"
    m.app.config["TESTING"] = True
    client = m.app.test_client()
    rows: list[dict] = []
    try:
        for s in scenarios:
            # 真实模式不把参考解带进来（`_one` 也不会为它落盘）
            jobs = [("ref", None if real_agent else s["reference_solution"])]
            if negatives:
                jobs += [(n["id"], n["edits"]) for n in s.get("negative_variants", [])]
            for (variant, edits), rep in [(j, k) for j in jobs for k in range(1, repeat + 1)]:
                row = _one(
                    m,
                    client,
                    s,
                    variant,
                    edits,
                    work,
                    edits_box,
                    discover,
                    figureverify,
                    agent,
                    replay=replay,
                    model=model,
                    effort=effort,
                    rep=rep,
                )
                row["rep"] = rep
                if edits_box.pop("blocked", False):
                    row["error"] = "max_runs_reached"
                rows.append(row)
                m.reset_projects()
                pool.shutdown_all(wait=True)
                project_watch.stop()
    finally:
        ai_prompt_pack.DEFAULT = real_pack
        config.ai_settings, ai_bridge._cmd = real_settings, real_cmd
        ai_bridge.require_usable = real_usable
        ai_bridge.ai_providers.resolve = real_resolve
        ai_bridge._spawn_env = real_env
        ai_tasks.unregister(ai_figure_edit.TASK_TYPE)
    res = _agent_resolution(agent) if real_agent else None
    version = res.version if res is not None else None
    env = _environment(m, f"real:{agent} {version or '?'}" if real_agent else "replay")
    env.update(
        {
            "executable": calls["executable"] if real_agent else None,
            "version": version,
            "endpoint": calls["endpoint"] or ({"kind": "cli_default"} if real_agent else None),
            "model": model,
            "effort": effort,
            "agent_calls": calls["n"],
        }
    )
    if pack is not None:
        env["pack"] = {"id": pack["pack_id"], "version": pack["version"], "source": pack["source"]}
    return {"environment": env, "rows": rows, "metrics": _metrics(rows)}


def _row(s, variant, **kw) -> dict:
    row = dict.fromkeys(ROW_FIELDS)
    row.update({"id": s["id"], "variant": variant, "unintended": False, **kw})
    return row


def _one(
    m,
    client,
    s,
    variant,
    edits,
    work,
    edits_box,
    discover,
    figureverify,
    agent="codex",
    *,
    replay=True,
    model=None,
    effort=None,
    rep=1,
) -> dict:
    # 重复跑（`--repeat`）各用自己的项目目录；rep=1 时目录名与以前相同
    proj = work / (f"{s['id']}__{variant}" if rep == 1 else f"{s['id']}__{variant}__r{rep}")
    if proj.exists():
        shutil.rmtree(proj)  # 本工具在 work 下建的目录；同一 --work 重跑时先清掉上次的
    shutil.copytree(FIXTURES / s["fixture"], proj)
    (proj / "truth.json").unlink(missing_ok=True)  # 真值不进项目（6c 的真实模型会读到它）
    (proj / "notes.txt").write_text("用户自己的文件：任务期间不许变。\n", encoding="utf-8")
    stems = discover.inspect_script(proj / "figure.py", proj, glob_disk=False)["info"]["stems"]
    (proj / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "figure.py": {"entry": "main", "cost": "light", "notes": "", "stems": stems}
                },
            }
        ),
        encoding="utf-8",
    )
    if replay:
        # 参考解只在回放时落盘，且放在 work 的父目录下的 `_replay/`——不在任何项目目录里、也不在项目目录的父目录里
        # （真实模式根本不生成这个文件：Agent 翻周边目录也读不到标准答案）
        replay_dir = work.parent / "_replay"
        replay_dir.mkdir(parents=True, exist_ok=True)
        edits_path = replay_dir / f"{proj.name}.edits.json"
        edits_path.write_text(json.dumps(edits), encoding="utf-8")
        edits_box["path"] = str(edits_path)
    import pikepdf  # 开发侧依赖：面板要先在磁盘上有个文件，渲染接口才认（与 tests/test_ai_figure_edit_app.py 同法）

    for st in stems:
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(200, 100))
        pdf.save(proj / f"{st}.pdf")

    def user_files() -> dict[str, bytes]:
        """非目标的用户文件：去掉目标脚本与 Tavotto 自己管理的东西（注册表、图的产物、项目元数据目录）。"""
        managed = {"figure.py", "tavotto_registry.json"} | {f"{st}.pdf" for st in stems}
        return {
            p.relative_to(proj).as_posix(): p.read_bytes()
            for p in proj.rglob("*")
            if p.is_file()
            and p.name not in managed
            and "tavottofile" not in p.relative_to(proj).parts
        }

    m.open_project(str(proj))
    stem = stems[0]

    def render():
        r = client.post("/api/engine/render", json={"id": f"{stem}.pdf", "patches": []})
        if r.status_code != 200:
            raise RuntimeError(f"render {r.status_code}: {r.get_json()}")
        return r.get_json()["manifest"]

    t0 = time.monotonic()
    try:
        before = render()
        data_before = user_files()  # 改前渲染之后、任务之前：任务期间任何用户文件变化都算非预期修改
        body = {"agent": agent, "prompt": s["instruction"], "id": f"{stem}.pdf"}
        if model:
            body["model"] = model
        if effort:
            body["effort"] = effort
        r = client.post("/api/ai/run", json=body)
        if r.status_code != 200:
            return _row(s, variant, error=f"run {r.status_code}")
        sid = r.get_json()["session"]
        pump = next((t for t in threading.enumerate() if t.name == f"ai-{sid}"), None)
        if pump is not None:
            pump.join(1200)  # 真实 CLI 最长也在桥自己的超时（TIMEOUT_S）里收尾
        from tavotto.engine import ai_bridge, ai_tasks

        sess = ai_bridge.SESSIONS[sid]
        task = ai_tasks.load(sid) or {}
        after = render()
    except Exception as exc:  # noqa: BLE001 —— 一个场景跑不起来记成 not_run，不让整批中断
        return _row(s, variant, error=f"{type(exc).__name__}: {exc}"[:300])
    wall = round(time.monotonic() - t0, 3)
    checks = figureverify.verify(s.get("goal", []), s.get("preserve", []), after, before)
    from tavotto.engine.ai_tasks import aggregate_verdict

    goal = aggregate_verdict(c.verdict for c in checks if c.role == "goal")
    preserve = aggregate_verdict(c.verdict for c in checks if c.role == "preserve")
    unintended = user_files() != data_before
    attempts = (task.get("verification") or {}).get("attempts") or []
    return _row(
        s,
        variant,
        outcome=task.get("outcome"),
        figure_verdict=((task.get("verification") or {}).get("figure") or {}).get("verdict"),
        goal=goal,
        preserve=preserve,
        unintended=unintended,
        rounds=len(attempts) + 1,
        prompt_sha256=edits_box.get("prompt_sha256"),
        wall_s=wall,
        error=sess.get("error"),
    )


def compare(a: dict, b: dict) -> str:
    """两份报告 → 可审查的 Markdown：环境、指标逐项差、逐场景结局变化。只比两边都跑了的场景。"""
    ea, eb = a.get("environment", {}), b.get("environment", {})
    lines = []
    keys = ("model", "effort", "executable", "version", "endpoint")
    if any(ea.get(k) != eb.get(k) for k in keys):
        lines += ["⚠ 两臂执行配置不同，差值不可归因于 Pack", ""]
    lines += [
        "| | A | B |",
        "| --- | --- | --- |",
        f"| Pack | {ea.get('pack')} | {eb.get('pack')} |",
        f"| 执行器 | {ea.get('executor')} | {eb.get('executor')} |",
        f"| git | {ea.get('git_sha')} | {eb.get('git_sha')} |",
        f"| 模型 / effort | {ea.get('model')} / {ea.get('effort')} | {eb.get('model')} / {eb.get('effort')} |",
        f"| 可执行 / 版本 | {ea.get('executable')} {ea.get('version')} | {eb.get('executable')} {eb.get('version')} |",
        "",
        "| 指标 | A | B | B − A |",
        "| --- | --- | --- | --- |",
    ]
    ma, mb = a.get("metrics", {}), b.get("metrics", {})
    for k in sorted(ma.keys() | mb.keys()):
        va, vb = ma.get(k), mb.get(k)
        if isinstance(va, list) or isinstance(vb, list):
            continue
        diff = (
            round(vb - va, 4)
            if isinstance(va, (int, float)) and isinstance(vb, (int, float))
            else ""
        )
        lines.append(f"| {k} | {va} | {vb} | {diff} |")
    ra = {(r["id"], r["variant"], r.get("rep") or 1): r for r in a.get("rows", [])}
    rb = {(r["id"], r["variant"], r.get("rep") or 1): r for r in b.get("rows", [])}
    changed = [
        (key, ra[key], rb[key])
        for key in sorted(ra.keys() & rb.keys())
        if (ra[key]["outcome"], ra[key]["goal"], ra[key]["preserve"])
        != (rb[key]["outcome"], rb[key]["goal"], rb[key]["preserve"])
    ]
    lines += ["", f"逐场景变化（{len(changed)} 个，结局 / 目标 / 保持）："]
    for (sid, var, rep), x, y in changed:
        lines.append(
            f"- {sid}/{var}{'' if rep == 1 else f'#{rep}'}: {x['outcome']}/{x['goal']}/{x['preserve']} → {y['outcome']}/{y['goal']}/{y['preserve']}"
        )
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--work", type=Path, default=None, help="工作目录（默认临时目录）；数据 / 配置目录也在这里"
    )
    ap.add_argument("--out", type=Path, default=None, help="报告 JSON 写到这里（默认打印）")
    ap.add_argument("--negatives", action="store_true", help="同时回放每个场景的反例")
    ap.add_argument("--only", default="", help="逗号分隔的场景 id")
    ap.add_argument("--holdout", action="store_true", help="跑 holdout，只出汇总分")
    ap.add_argument(
        "--real",
        metavar="AGENT",
        default=None,
        help="用真实编码 Agent CLI（如 codex）代替回放；花用户的额度",
    )
    ap.add_argument(
        "--pack",
        type=Path,
        default=None,
        help="本次改用这份候选 Prompt Pack（JSON）；先过 holdout 防泄漏守卫",
    )
    ap.add_argument(
        "--repeat", type=int, default=1, help="每个场景跑几次（估方差；真实模式按次数计入上限）"
    )
    ap.add_argument(
        "--max-runs",
        type=int,
        default=DEFAULT_MAX_REAL_RUNS,
        help="--real 时最多起多少次**实际 Agent CLI 调用**（含修正那次）；开跑前按 场景数 × 每任务最大调用数 检查",
    )
    ap.add_argument("--model", default=None, help="钉住模型（--real 必填；两臂必须相同）")
    ap.add_argument("--effort", default=None, help="钉住推理强度（可选；两臂必须相同）")
    ap.add_argument(
        "--compare", nargs=2, type=Path, metavar=("A", "B"), help="比较两份报告，输出 Markdown"
    )
    args = ap.parse_args(argv)
    if args.compare:
        a, b = (json.loads(p.read_text(encoding="utf-8")) for p in args.compare)
        text = compare(a, b)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        else:
            print(text)
        return 0
    if args.real and not args.model:
        print(
            "--real 必须用 --model 钉住模型（两臂用同一个，否则差值不可归因于 Pack）",
            file=sys.stderr,
        )
        return 2
    pack = None
    if args.pack:
        raw = args.pack.read_text(encoding="utf-8")
        try:
            assert_no_leak([raw], load_scenarios("holdout"))
        except LeakError as exc:
            print(f"候选 Pack 泄漏守卫未通过：{exc}", file=sys.stderr)
            return 2
        pack = json.loads(raw)
    work = args.work or Path(tempfile.mkdtemp(prefix="taf-bench-"))
    work.mkdir(parents=True, exist_ok=True)
    # 必须在 import 任何产品模块之前：历史库、候选、快照等路径在 import 时就按数据目录算好
    os.environ["TAVOTTO_DATA_DIR"] = str(work / "data")
    os.environ["TAVOTTO_CONFIG_DIR"] = str(work / "config")
    sys.path.insert(0, str(ROOT / "src"))
    split = "holdout" if args.holdout else "dev"
    scenarios = [s for s in load_scenarios(split) if benchable(s)]
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s["id"] in wanted]
    if args.real:
        per_task = agent_runs_per_task()
        need = len(scenarios) * args.repeat * per_task
        if need > args.max_runs:
            print(
                f"--real 最多要起 {need} 次 Agent CLI 调用（{len(scenarios)} 个场景 × 重复 {args.repeat} × 每任务最多 {per_task} 次），"
                f"超过上限 {args.max_runs}；用 --only 缩小或显式调大 --max-runs",
                file=sys.stderr,
            )
            return 2
    try:
        report = run(
            scenarios,
            work / "projects",
            negatives=args.negatives,
            real_agent=args.real,
            pack=pack,
            model=args.model,
            effort=args.effort,
            max_runs=args.max_runs if args.real else None,
            repeat=args.repeat,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    rows = report["rows"]
    report["split"] = split
    if args.holdout:
        report.pop("rows")  # holdout 只出汇总分
        report["metrics"] = _metrics(rows, aggregate_only=True)
        # 兜底：汇总里不许出现任何 holdout 场景 id / 指令 / 断言
        try:
            assert_no_leak([json.dumps(report, ensure_ascii=False)], load_scenarios("holdout"))
        except LeakError as exc:
            print(f"holdout 汇总泄漏守卫未通过：{exc}", file=sys.stderr)
            return 2
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
