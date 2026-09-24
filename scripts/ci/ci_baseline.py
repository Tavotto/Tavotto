#!/usr/bin/env python3
"""CI 基线采集与时间分解（CI00 · docs/implementation/ci-foundation）。

    python scripts/ci/ci_baseline.py fetch-runs --out DIR [--pages 2]
    python scripts/ci/ci_baseline.py fetch-jobs --out DIR --run-id ID [--run-id ID …]
    python scripts/ci/ci_baseline.py dag --workflow .github/workflows/ci.yml --out dag.json
    python scripts/ci/ci_baseline.py analyze --workflow .github/workflows/ci.yml \\
        --evidence DIR --edge-kinds dag_edge_kinds.json --out CI_BASELINE.json

三条口径（04_BUILD_TEST_CACHE.md §6），每个数字都能回指到 `--evidence` 目录里的
原始 API JSON：

* `dependency_wait`：按 DAG 算——该 job 全部 `needs` 里**最晚**的 `completed_at`
  减 run 的 `created_at`；没有 `needs` 的记 0。**不是** `job.started_at −
  job.created_at`（那是 runner_wait）。
* `runner_wait`：`job.created_at → job.started_at`。GitHub 在 `needs` 满足后才
  创建下游 job，所以这一段只含调度与领取，不含等上游。缺任一时刻标 `unknown`。
* `execution`：按 step 分类（checkout / setup / install / test / build / artifact /
  overhead / post / other）逐段相加；step 的时刻缺失就归 `unknown_seconds`。
* `feedback` / `qualification`：run `created_at` → `ci-fast-gate` /
  `ci-integration-gate` 的 `completed_at`。**绝不用 `updated_at` 冒充完成时刻。**

解析纪律：任何一处形状不对（空 jobs、缺 `completed_at` 的已完成 job、job 名对不上
workflow、attempt 过滤后为空、edge 没分类、SHA 对不上）都抛 `BaselineError`
并以非零退出——**不产生「成功的空报告」**。token 只经 `gh api`（keyring）走，
脚本自己不碰凭据。纯标准库。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import use_utf8_streams  # noqa: E402

use_utf8_streams()

DEFAULT_REPO = "Tavotto/Tavotto"
FAST_GATE = "ci-fast-gate"
INTEGRATION_GATE = "ci-integration-gate"
EDGE_KINDS = ("artifact/data", "短预筛", "verdict-only")


class BaselineError(RuntimeError):
    """解析 / 形状错误。任何一条都意味着报告**没有**写出来。"""


# ---------------------------------------------------------------- gh 访问层


def gh_api(path: str, *, paginate: bool = False) -> str:
    """经本机 `gh api` 取原文（凭据在 gh 的 keyring 里，脚本不碰 token）。"""
    cmd = ["gh", "api"]
    if paginate:
        cmd.append("--paginate")
    cmd.append(path)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise BaselineError(f"gh api {path} 失败：{proc.stderr.strip()[:500]}")
    if not proc.stdout.strip():
        raise BaselineError(f"gh api {path} 回了空正文")
    return proc.stdout


# ---------------------------------------------------------------- 解析


def load_pages(text: str) -> list[dict]:
    """把 `gh api --paginate` 的输出解析成页列表。

    gh 对**对象**端点（`/jobs` 回 `{total_count, jobs}`）按页输出多个 JSON 对象
    首尾相接；对数组端点某些版本会合并成一个数组。两种都吃；空输入当场抛。
    """
    dec = json.JSONDecoder()
    i, n, out = 0, len(text), []
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        try:
            obj, i = dec.raw_decode(text, i)
        except json.JSONDecodeError as exc:
            raise BaselineError(f"分页输出第 {len(out) + 1} 段不是合法 JSON：{exc}") from exc
        if isinstance(obj, list):
            out.extend(o for o in obj if isinstance(o, dict))
        else:
            out.append(obj)
    if not out:
        raise BaselineError("分页输出里一页都没有")
    return out


def jobs_from_pages(pages: list[dict], *, attempt: int | None = None) -> list[dict]:
    """把各页的 `jobs` 摊平；`attempt` 给了就只留那一次 attempt 的 job。

    单次 attempt 的 `filter=all` 端点会把**所有** attempt 的 job 一起回（attempt 2
    的 run 拿到 44 条而不是 22 条），不按 `run_attempt` 过滤会把两次跑的时刻混在
    一起——那正是要防的「跨 attempt 错误复用」。摊平后为空一律抛。
    """
    jobs: list[dict] = []
    declared: int | None = None
    for page in pages:
        if "jobs" not in page or not isinstance(page["jobs"], list):
            raise BaselineError("分页里有一页没有 `jobs` 数组")
        if declared is None and isinstance(page.get("total_count"), int):
            declared = page["total_count"]
        jobs.extend(page["jobs"])
    if declared is not None and declared != len(jobs):
        raise BaselineError(f"total_count={declared} 与实际取到的 {len(jobs)} 条不符——分页没取全")
    if attempt is not None:
        jobs = [j for j in jobs if j.get("run_attempt") == attempt]
    if not jobs:
        raise BaselineError(f"jobs 集合为空（attempt={attempt}）——不出空报告")
    return jobs


def parse_ts(value: object, *, what: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise BaselineError(f"{what}：时刻缺失（{value!r}）")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise BaselineError(f"{what}：时刻格式认不出（{value!r}）") from exc


def _seconds(a: datetime, b: datetime) -> int:
    return int((b - a).total_seconds())


# ---------------------------------------------------------------- workflow 形状

_JOB_HEADER = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
_KEY = re.compile(r"^    ([a-z-]+):\s*(.*)$")


def extract_workflow_jobs(text: str) -> dict[str, dict]:
    """从 ci.yml 抽 job 表：name / needs / if / runs-on / timeout-minutes / matrix。

    与 tests/test_merge_queue_workflows.py 同一条纪律：**不用 PyYAML**（它不在
    .venv 里），只认本仓库的缩进形状——job 在 `jobs:` 下缩进 2，字段缩进 4。
    形状认不出来当场抛，不猜。
    """
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.rstrip() == "jobs:")
    except StopIteration as exc:
        raise BaselineError("workflow 里没有顶层 `jobs:`") from exc
    jobs: dict[str, dict] = {}
    current: str | None = None
    block: list[str] = []

    def flush() -> None:
        if current is not None:
            jobs[current] = _parse_job_block(current, block)

    for ln in lines[start + 1 :]:
        if ln and not ln.startswith(" ") and not ln.startswith("#"):
            break  # 下一个顶层键
        m = _JOB_HEADER.match(ln)
        if m:
            flush()
            current, block = m.group(1), []
            continue
        if current is not None:
            block.append(ln)
    flush()
    if not jobs:
        raise BaselineError("`jobs:` 下一个 job 都没解析出来")
    return jobs


def _parse_job_block(job_id: str, block: list[str]) -> dict:
    info: dict = {
        "id": job_id,
        "name": job_id,
        "needs": [],
        "if": None,
        "runs_on": None,
        "timeout_minutes": None,
        "matrix": None,
        "steps": [],
    }
    i = 0
    while i < len(block):
        ln = block[i]
        m = _KEY.match(ln)
        if not m:
            i += 1
            continue
        key, rest = m.group(1), m.group(2).strip()
        if key == "name":
            info["name"] = rest.strip("'\"")
        elif key == "needs":
            lm = re.fullmatch(r"\[(.*)\]", rest)
            if not lm:
                raise BaselineError(f"{job_id}: needs 不是 `[a, b]` 的流式写法：{rest!r}")
            info["needs"] = [s.strip() for s in lm.group(1).split(",") if s.strip()]
        elif key == "if":
            if rest in (">-", ">", "|", "|-"):
                buf = []
                j = i + 1
                while j < len(block) and (block[j].startswith("      ") or not block[j].strip()):
                    buf.append(block[j].strip())
                    j += 1
                info["if"] = " ".join(b for b in buf if b)
                i = j
                continue
            info["if"] = rest.strip()
            if info["if"].startswith("${{") and info["if"].endswith("}}"):
                info["if"] = info["if"][3:-2].strip()
        elif key == "runs-on":
            info["runs_on"] = re.split(r"\s+#", rest, maxsplit=1)[0].strip()
        elif key == "timeout-minutes":
            tm = re.match(r"(\d+)", rest)
            if not tm:
                raise BaselineError(f"{job_id}: timeout-minutes 不是整数：{rest!r}")
            info["timeout_minutes"] = int(tm.group(1))
        elif key == "strategy":
            info["matrix"] = _parse_matrix(job_id, block, i + 1)
        elif key == "steps":
            info["steps"] = _parse_steps(block, i + 1)
        i += 1
    return info


def _parse_matrix(job_id: str, block: list[str], start: int) -> list[dict] | dict:
    """`strategy.matrix` 只认两种形状：`include:` 下的 `- { k: v, … }` 行，或
    `key: [a, b]` 的单维列表。"""
    include: list[dict] = []
    axes: dict[str, list[str]] = {}
    j = start
    while j < len(block) and (block[j].startswith("      ") or not block[j].strip()):
        s = block[j].strip()
        fm = re.fullmatch(r"-\s*\{(.*)\}", s)
        if fm:
            entry = {}
            for part in fm.group(1).split(","):
                k, _, v = part.partition(":")
                entry[k.strip()] = v.strip().strip("'\"")
            include.append(entry)
        else:
            am = re.fullmatch(r"([a-z_-]+):\s*\[(.*)\]", s)
            if am and am.group(1) not in ("include",):
                axes[am.group(1)] = [x.strip().strip("'\"") for x in am.group(2).split(",")]
        j += 1
    if include:
        return include
    if axes:
        return axes
    raise BaselineError(f"{job_id}: strategy.matrix 的形状认不出")


def _parse_steps(block: list[str], start: int) -> list[dict]:
    steps: list[dict] = []
    j = start
    while j < len(block):
        s = block[j]
        if s.strip() and not s.startswith("      "):
            break
        m = re.match(r"^      - (name|uses|run|working-directory|if):\s*(.*)$", s)
        if m:
            steps.append({m.group(1): m.group(2).strip()})
        elif steps and re.match(r"^        (name|uses|run):\s*(.*)$", s):
            k, v = re.match(r"^        (name|uses|run):\s*(.*)$", s).groups()
            steps[-1].setdefault(k, v.strip())
        j += 1
    return steps


_EXPR = re.compile(r"\$\{\{.*?\}\}")


def display_to_job_id(name: str, workflow_jobs: dict[str, dict]) -> str:
    """把 API 的 job 显示名映射回 workflow 的 job id。

    matrix job 显示成 `backend-fast (ubuntu-latest, 3.10)`；整格被 skip 时 matrix
    没展开，显示名就是裸 id `package`；自定义 `name:` 的显示名逐字相同。`name:` 里带
    表达式的（CI03c 起 `windows-exe-smoke (${{ matrix.shard }})`，为的是 include 形状的
    matrix 不把四个字段全排进显示名）按模式配：表达式处配任意串（整格 skip 时表达式
    展开成空，显示成 `windows-exe-smoke ()`），其余逐字。对不上一律抛——猜一个 id 会把
    时间算到别的 job 头上。
    """
    exact = {info["name"]: jid for jid, info in workflow_jobs.items()}
    if name in exact:
        return exact[name]
    for jid, info in workflow_jobs.items():
        if name.startswith(info["name"] + " (") or name == jid:
            return jid
        if "${{" in info["name"]:
            # 表达式处配一个 matrix 值：不含括号（值里真有括号时这里会抛，不会错配到别人）
            pattern = "[^()]*".join(re.escape(part) for part in _EXPR.split(info["name"]))
            if re.fullmatch(pattern, name):
                return jid
    raise BaselineError(f"job 显示名 {name!r} 对不上 workflow 里的任何 job")


# ---------------------------------------------------------------- step 分类

_STEP_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("overhead", re.compile(r"^(Set up job|Complete job)$")),
    ("post", re.compile(r"^Post ")),
    ("checkout", re.compile(r"^Run actions/checkout@")),
    (
        "setup",
        re.compile(
            r"^Run (actions/setup-python@|actions/setup-node@|pnpm/action-setup@|"
            r"dtolnay/rust-toolchain@|Swatinem/rust-cache@|actions/cache@)|^缓存 CPython|^缓存字体"
        ),
    ),
    (
        "artifact",
        re.compile(
            r"^Run actions/(upload|download)-artifact@|^上传|^失败时收集|^失败诊断|^取回候选|"
            r"^分片证据|^首开 / 输出 harness 证据"
        ),
    ),
    (
        "install",
        re.compile(
            r"^Run pnpm install|^安装|^装 |^装打包依赖|^装进干净环境|^准备渲染环境|^装 Tauri|"
            r"^种 pnpm store|^批准字体"
        ),
    ),
    (
        "build",
        re.compile(
            r"^Run pnpm build|^构建|^打 wheel|^PyInstaller|^组装并验证|^浏览器 playground 构建|"
            r"^摆一个空的 sidecar|^让消费者那组 cargo 命令|^种 CPython 归档"
        ),
    ),
    (
        "test",
        re.compile(
            r"^pytest$|^Run pnpm (test|lint|i18n:check)|^Run cargo |^Ruff |^CompatBench|"
            r"^Playwright|^冒烟|^不变式|^结构性不变式|^断言|^校验|^判定$|^聚合判定|"
            r"^依赖真实插件|^核心验收|^解包并验证候选|^发行生成物|^大图预览|^架构核对|"
            r"^起服务并请求首页|^取默认分支|^收集这个 PR|^落地信息|^workflow YAML|^CI 结构契约|"
            # U01（ADR 0053）：预期集合 + enforced 用例、闭集校验——都是判定；证据上传在 artifact 那条
            r"^首开 / 输出 harness|"
            # U10（ADR 0072）：退役扫描是对产物的判定
            r"^退役扫描"
        ),
    ),
)


def classify_step(name: str) -> str:
    for cat, rx in _STEP_RULES:
        if rx.search(name):
            return cat
    return "other"


# ---------------------------------------------------------------- 时间分解


def job_state(job: dict, t0: datetime) -> str:
    """一个 job 在**这次 attempt** 里的状态：

    * `skipped`：`if:` 没成立，没有执行；
    * `never_started`：排到了队里但取消前没领到 runner（`steps` 为空、
      `started_at == created_at`、没有 runner_name）——它的「时长」是排队时长，
      不是执行时长，绝不能算进 job_seconds；
    * `carried_over`：re-run failed jobs 时没被重跑的 job——GitHub 把它抄进新
      attempt，`created_at` 是重跑时刻而 `started_at` 还是上一次的（早于 t0），
      它的时刻属于上一次 attempt；
    * `executed`：真在这次 attempt 里跑过。
    """
    if job.get("conclusion") == "skipped":
        return "skipped"
    if not job.get("steps") and not job.get("runner_name"):
        return "never_started"
    started = job.get("started_at")
    if isinstance(started, str) and started and parse_ts(started, what="started_at") < t0:
        return "carried_over"
    return "executed"


def decompose_job(t0: datetime, job: dict, needs_completed: list[datetime]) -> dict:
    """一个 job 的四类时间（execution 按 step 类别分列）。`t0` = 本次 attempt 的起点
    （run 的 `run_started_at`；attempt 1 时它等于 `created_at`）。"""
    name = job.get("name", "?")
    conclusion = job.get("conclusion")
    state = job_state(job, t0)
    out: dict = {
        "name": name,
        "state": state,
        "conclusion": conclusion,
        "status": job.get("status"),
        "runner_labels": job.get("labels"),
        "runner_name": job.get("runner_name"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
    }
    if state != "executed":
        note = {
            "skipped": "skipped：没有执行，时刻无意义",
            "never_started": "取消前没领到 runner：created→completed 是排队时长，不是执行",
            "carried_over": "上一次 attempt 的结果被抄进来，时刻属于上一次",
        }[state]
        queued = None
        if state == "never_started" and job.get("created_at") and job.get("completed_at"):
            queued = _seconds(
                parse_ts(job["created_at"], what=f"{name}.created_at"),
                parse_ts(job["completed_at"], what=f"{name}.completed_at"),
            )
        out.update(
            {
                "dependency_wait": "unknown",
                "runner_wait": "unknown",
                "dispatch_gap": "unknown",
                "job_seconds": None,
                "queued_until_cancel_seconds": queued,
                "execution": {},
                "execution_unknown_steps": 0,
                "died_at_step": None,
                "note": note,
            }
        )
        return out
    if job.get("status") != "completed":
        raise BaselineError(f"{name}: status={job.get('status')}，未完成的 job 不进基线")
    created = parse_ts(job.get("created_at"), what=f"{name}.created_at")
    started = parse_ts(job.get("started_at"), what=f"{name}.started_at")
    completed = parse_ts(job.get("completed_at"), what=f"{name}.completed_at")
    if completed < started:
        raise BaselineError(f"{name}: completed_at 早于 started_at")
    if needs_completed:
        ready = max(needs_completed)
        out["dependency_wait"] = _seconds(t0, ready)
        out["dispatch_gap"] = _seconds(ready, created)
    else:
        out["dependency_wait"] = 0
        out["dispatch_gap"] = _seconds(t0, created)
    out["runner_wait"] = _seconds(created, started)
    out["job_seconds"] = _seconds(started, completed)

    execution: dict[str, int] = {}
    unknown = 0
    died = None
    for step in job.get("steps", []):
        sname = step.get("name", "?")
        cat = classify_step(sname)
        if step.get("conclusion") in ("failure", "cancelled") and died is None:
            died = {"number": step.get("number"), "name": sname, "conclusion": step["conclusion"]}
        if step.get("conclusion") == "skipped":
            continue
        if not step.get("started_at") or not step.get("completed_at"):
            unknown += 1
            continue
        secs = _seconds(
            parse_ts(step["started_at"], what=f"{name}/{sname}.started_at"),
            parse_ts(step["completed_at"], what=f"{name}/{sname}.completed_at"),
        )
        execution[cat] = execution.get(cat, 0) + secs
    out["execution"] = dict(sorted(execution.items()))
    out["execution_unknown_steps"] = unknown
    out["died_at_step"] = died
    out["steps"] = [
        {
            "number": s.get("number"),
            "name": s.get("name"),
            "category": classify_step(s.get("name", "")),
            "conclusion": s.get("conclusion"),
            "seconds": (
                _seconds(
                    parse_ts(s["started_at"], what="step"),
                    parse_ts(s["completed_at"], what="step"),
                )
                if s.get("started_at") and s.get("completed_at")
                else None
            ),
        }
        for s in job.get("steps", [])
    ]
    return out


def analyze_run(run: dict, jobs: list[dict], workflow_jobs: dict[str, dict]) -> dict:
    """一个 run（一次 attempt）的分解：每个 job 四类时间 + 关键路径 + 两个 Gate 时刻。"""
    run_created = parse_ts(run.get("created_at"), what="run.created_at")
    t0 = parse_ts(run.get("run_started_at") or run.get("created_at"), what="run.run_started_at")
    attempt = run.get("run_attempt")
    if any(j.get("run_attempt") != attempt for j in jobs):
        raise BaselineError(f"run {run.get('id')}: jobs 里混进了别的 attempt")
    if any(j.get("head_sha") != run.get("head_sha") for j in jobs):
        raise BaselineError(f"run {run.get('id')}: jobs 的 head_sha 与 run 不一致")

    by_id: dict[str, list[dict]] = {}
    for j in jobs:
        by_id.setdefault(display_to_job_id(j["name"], workflow_jobs), []).append(j)

    def completed_of(jid: str, *, executed_only: bool) -> list[datetime]:
        out = []
        for j in by_id.get(jid, []):
            st = job_state(j, t0)
            if st == "skipped" or st == "never_started" or (executed_only and st != "executed"):
                continue
            out.append(parse_ts(j.get("completed_at"), what=f"{j['name']}.completed_at"))
        return out

    decomposed: list[dict] = []
    for jid, group in by_id.items():
        needs = workflow_jobs[jid]["needs"]
        # 上游被抄进来的（carried_over）结果对下游来说在 t0 之前就已经「完成」——
        # 它对本次 attempt 的 dependency_wait 贡献为 0，所以按 t0 计
        needs_done = [max(t, t0) for n in needs for t in completed_of(n, executed_only=False)]
        for j in group:
            d = decompose_job(t0, j, needs_done)
            d["job_id"] = jid
            d["needs"] = needs
            d["api_job_id"] = j.get("id")
            decomposed.append(d)

    def gate_seconds(jid: str) -> int | str:
        done = completed_of(jid, executed_only=True)
        if not done:
            carried = completed_of(jid, executed_only=False)
            return "carried_over" if carried else "unknown"
        return _seconds(t0, max(done))

    return {
        "run_id": run.get("id"),
        "attempt": attempt,
        "event": run.get("event"),
        "conclusion": run.get("conclusion"),
        "head_sha": run.get("head_sha"),
        "head_branch": run.get("head_branch"),
        "created_at": run.get("created_at"),
        "run_started_at": run.get("run_started_at"),
        "t0": t0.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_created_to_t0_seconds": _seconds(run_created, t0),
        "job_count": len(jobs),
        "job_states": {
            st: sum(1 for d in decomposed if d["state"] == st)
            for st in ("executed", "skipped", "never_started", "carried_over")
        },
        "feedback_seconds": gate_seconds(FAST_GATE),
        "qualification_seconds": gate_seconds(INTEGRATION_GATE),
        "critical_path": critical_path(decomposed, workflow_jobs, INTEGRATION_GATE),
        "runner_minutes_by_label": runner_minutes_by_label(decomposed),
        "model_without_backend_fast_edges": (
            model_without_edges(
                decomposed,
                workflow_jobs,
                {
                    ("backend-fast", "package"),
                    ("backend-fast", "windows-exe-smoke"),
                    ("backend-fast", "macos-app-smoke"),
                    ("backend-fast", "posix-e2e"),
                },
                INTEGRATION_GATE,
                t0,
            )
            if any(d["job_id"] == INTEGRATION_GATE and d["state"] == "executed" for d in decomposed)
            and any(
                d["job_id"] == "windows-exe-smoke" and d["state"] == "executed" for d in decomposed
            )
            else None
        ),
        "jobs": sorted(decomposed, key=lambda d: (d["created_at"] or "", d["name"])),
    }


def critical_path(
    decomposed: list[dict], workflow_jobs: dict[str, dict], target: str
) -> list[dict]:
    """从 target 往回走：每一步挑「完成最晚」的那条 needs，直到没有 needs。

    这就是决定 target 完成时刻的那条链；链上每个节点记它的 job 时长与完成时刻。
    target 没跑（skipped）就返回空表。
    """
    latest: dict[str, dict] = {}
    for d in decomposed:
        if d["state"] != "executed":
            continue
        cur = latest.get(d["job_id"])
        if cur is None or d["completed_at"] > cur["completed_at"]:
            latest[d["job_id"]] = d
    if target not in latest:
        return []
    path: list[dict] = []
    node = target
    seen: set[str] = set()
    while node and node not in seen:
        seen.add(node)
        d = latest[node]
        path.append(
            {
                "job_id": node,
                "name": d["name"],
                "job_seconds": d["job_seconds"],
                "completed_at": d["completed_at"],
                "runner_wait": d["runner_wait"],
            }
        )
        needs = [n for n in workflow_jobs[node]["needs"] if n in latest]
        node = max(needs, key=lambda n: latest[n]["completed_at"]) if needs else None
    path.reverse()
    return path


# ---------------------------------------------------------------- 反事实模型


def runner_minutes_by_label(decomposed: list[dict]) -> dict[str, float]:
    """按 runner 标签汇总真正执行了的 job 分钟数（job_seconds，不含排队）。"""
    out: dict[str, float] = {}
    for d in decomposed:
        if d["state"] != "executed" or not d.get("runner_labels"):
            continue
        label = d["runner_labels"][0]
        out[label] = round(out.get(label, 0.0) + d["job_seconds"] / 60.0, 2)
    return dict(sorted(out.items()))


def model_without_edges(
    decomposed: list[dict],
    workflow_jobs: dict[str, dict],
    removed: set[tuple[str, str]],
    target: str,
    t0: datetime,
) -> dict:
    """**模型，不是测量**：假设删掉 `removed` 里的 needs 边，其它一切不变，
    target 会在什么时候完成。

    假设写在明处：每个 job 的 `job_seconds`、`dispatch_gap`、`runner_wait` 与实测
    相同（并发提前不会让托管 runner 变慢、也不会更难领到）；没有 needs 的 job 从
    t0 起算；有 needs 的从「剩余 needs 全部完成」起算。只对 executed 的 job 建模。
    返回的 `target_seconds` 是相对 t0 的完成秒数；`observed_seconds` 是实测值。
    """
    by_id: dict[str, list[dict]] = {}
    for d in decomposed:
        if d["state"] == "executed":
            by_id.setdefault(d["job_id"], []).append(d)
    if target not in by_id:
        raise BaselineError(f"{target} 在这个 run 里没有执行，模型没有主语")
    # 实测的 target 完成时刻（相对 t0）
    observed = max(
        _seconds(t0, parse_ts(d["completed_at"], what="completed_at")) for d in by_id[target]
    )
    finish: dict[str, int] = {}

    def done(jid: str) -> int:
        if jid in finish:
            return finish[jid]
        needs = [n for n in workflow_jobs[jid]["needs"] if (n, jid) not in removed and n in by_id]
        ready = max((done(n) for n in needs), default=0)
        latest = 0
        for d in by_id[jid]:
            latest = max(latest, ready + d["dispatch_gap"] + d["runner_wait"] + d["job_seconds"])
        finish[jid] = latest
        return latest

    modelled = done(target)
    return {
        "removed_edges": sorted(f"{a}->{b}" for a, b in removed),
        "observed_seconds": observed,
        "modelled_seconds": modelled,
        "delta_seconds": modelled - observed,
        "assumption": "job_seconds / runner_wait / dispatch_gap 与实测相同；仅改变起点",
    }


# ---------------------------------------------------------------- DAG


def build_dag(workflow_jobs: dict[str, dict], edge_kinds: dict | None) -> dict:
    """job 表 + 边表。`edge_kinds` 给了就逐条要求分类与证据行，缺一条抛。"""
    edges = []
    for jid, info in workflow_jobs.items():
        for n in info["needs"]:
            if n not in workflow_jobs:
                raise BaselineError(f"{jid} needs {n}，但 workflow 里没有这个 job")
            e = {"from": n, "to": jid, "kind": None, "evidence": None}
            if edge_kinds is not None:
                key = f"{n}->{jid}"
                if key not in edge_kinds:
                    raise BaselineError(f"边 {key} 没有分类——每条 needs 都要标 kind 与证据行")
                k = edge_kinds[key]
                if k.get("kind") not in EDGE_KINDS or not k.get("evidence"):
                    raise BaselineError(f"边 {key} 的 kind/evidence 不合法：{k}")
                e.update({"kind": k["kind"], "evidence": k["evidence"], "note": k.get("note")})
            edges.append(e)
    return {
        "jobs": {
            jid: {
                "name": i["name"],
                "needs": i["needs"],
                "if": i["if"],
                "runs_on": i["runs_on"],
                "timeout_minutes": i["timeout_minutes"],
                "matrix": i["matrix"],
                "yaml_step_count": len(i["steps"]),
            }
            for jid, i in workflow_jobs.items()
        },
        "edges": edges,
    }


# ---------------------------------------------------------------- 汇总


def _dist(values: list) -> dict | None:
    xs = sorted(v for v in values if isinstance(v, int) and not isinstance(v, bool))
    if not xs:
        return None
    mid = len(xs) // 2
    median = xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2
    return {"n": len(xs), "min": xs[0], "median": median, "max": xs[-1]}


def summarize_runs(runs: list[dict]) -> dict:
    """把 analyze 的逐 run 结果压成分组统计。只给 min / median / max 与计数——
    样本不够就不编 p95（04_BUILD_TEST_CACHE.md §6）。"""
    groups: dict[str, list[dict]] = {}
    for r in runs:
        if r["attempt"] != 1:
            groups.setdefault("attempt_gt_1", []).append(r)
            continue
        heavy = [j for j in r["jobs"] if j["job_id"] in HEAVY_JOBS]
        if r["event"] == "merge_group":
            key = f"merge_group/{r['conclusion']}"
        elif r["event"] == "pull_request":
            ran_heavy = any(j["state"] == "executed" for j in heavy)
            key = f"pull_request/{'full-ci' if ran_heavy else 'plain'}/{r['conclusion']}"
        else:
            key = f"{r['event']}/{r['conclusion']}"
        groups.setdefault(key, []).append(r)

    out: dict = {"groups": {}}
    for key, rs in sorted(groups.items()):
        g: dict = {
            "run_ids": [r["run_id"] for r in rs],
            "feedback_seconds": _dist([r["feedback_seconds"] for r in rs]),
            "qualification_seconds": _dist([r["qualification_seconds"] for r in rs]),
            "critical_paths": {},
            "jobs": {},
            "runner_wait_by_label": {},
            "runner_minutes_by_label": {},
        }
        cps: dict[str, int] = {}
        for r in rs:
            k = "->".join(n["job_id"] for n in r["critical_path"]) or "(none)"
            cps[k] = cps.get(k, 0) + 1
        g["critical_paths"] = dict(sorted(cps.items(), key=lambda kv: -kv[1]))
        per: dict[str, dict[str, list]] = {}
        waits: dict[str, list] = {}
        mins: dict[str, list] = {}
        for r in rs:
            for lbl, m in r["runner_minutes_by_label"].items():
                mins.setdefault(lbl, []).append(m)
            for j in r["jobs"]:
                if j["state"] != "executed":
                    continue
                d = per.setdefault(
                    j["name"],
                    {
                        "job_seconds": [],
                        "runner_wait": [],
                        "dependency_wait": [],
                        "test": [],
                        "build": [],
                        "install": [],
                    },
                )
                d["job_seconds"].append(j["job_seconds"])
                d["runner_wait"].append(j["runner_wait"])
                d["dependency_wait"].append(j["dependency_wait"])
                for cat in ("test", "build", "install"):
                    if cat in j["execution"]:
                        d[cat].append(j["execution"][cat])
                if j.get("runner_labels"):
                    waits.setdefault(j["runner_labels"][0], []).append(j["runner_wait"])
        g["jobs"] = {
            name: {k: _dist(v) for k, v in d.items() if _dist(v)} for name, d in sorted(per.items())
        }
        g["runner_wait_by_label"] = {lbl: _dist(v) for lbl, v in sorted(waits.items())}
        g["runner_minutes_by_label"] = {
            lbl: {"n": len(v), "median": sorted(v)[len(v) // 2], "max": max(v)}
            for lbl, v in sorted(mins.items())
        }
        models = [
            r["model_without_backend_fast_edges"]
            for r in rs
            if r.get("model_without_backend_fast_edges")
        ]
        if models:
            g["model_without_backend_fast_edges"] = {
                "observed_seconds": _dist([m["observed_seconds"] for m in models]),
                "modelled_seconds": _dist([m["modelled_seconds"] for m in models]),
                "delta_seconds": _dist([m["delta_seconds"] for m in models]),
                "assumption": models[0]["assumption"],
            }
        out["groups"][key] = g
    return out


HEAVY_JOBS = ("backend-platforms", "package", "windows-exe-smoke", "macos-app-smoke", "posix-e2e")


def cmd_summarize(args: argparse.Namespace) -> int:
    analysis = _load_json(Path(args.analysis))
    if not isinstance(analysis, dict) or not analysis.get("runs"):
        raise BaselineError("analysis 里没有 runs——不出空汇总")
    summary = summarize_runs(analysis["runs"])
    doc: dict = {}
    if args.extra:
        extra = _load_json(Path(args.extra))
        if not isinstance(extra, dict):
            raise BaselineError("--extra 不是对象")
        doc.update(extra)
    doc["timing"] = {
        "source_analysis": args.analysis,
        "run_count": analysis["run_count"],
        **summary,
    }
    doc["dag"] = analysis["dag"]
    if args.include_runs:
        want = set(args.include_runs)
        doc["sample_runs"] = [r for r in analysis["runs"] if r["run_id"] in want]
        missing = want - {r["run_id"] for r in doc["sample_runs"]}
        if missing:
            raise BaselineError(f"--include-runs 里这些 run 不在 analysis 里：{sorted(missing)}")
    # 紧凑序列化：这是脚本产物，不是给人逐行读的（看的时候 `python -m json.tool`）
    Path(args.out).write_text(
        json.dumps(doc, ensure_ascii=False, indent=None) + "\n", encoding="utf-8"
    )
    print(f"汇总 {analysis['run_count']} 个 run → {args.out}")
    return 0


# ---------------------------------------------------------------- 裁剪（进仓库的证据）

#: 进仓库的证据只留分解要用的字段。裁剪的是 URL / node_id / 大段冗余，**不裁时刻
#: 与结论**——裁掉的字段清单就是这三张表，evidence/actions/README.md 引用它们。
RUN_FIELDS = (
    "id",
    "run_attempt",
    "event",
    "status",
    "conclusion",
    "head_sha",
    "head_branch",
    "created_at",
    "run_started_at",
    "updated_at",
    "html_url",
)
JOB_FIELDS = (
    "id",
    "run_id",
    "run_attempt",
    "head_sha",
    "name",
    "status",
    "conclusion",
    "created_at",
    "started_at",
    "completed_at",
    "runner_name",
    "runner_group_name",
    "labels",
)
STEP_FIELDS = ("number", "name", "status", "conclusion", "started_at", "completed_at")


def trim_run(run: dict) -> dict:
    return {k: run.get(k) for k in RUN_FIELDS}


def trim_jobs_pages(pages: list[dict]) -> list[dict]:
    """保留页结构与 total_count，只裁字段；空页 / 缺 jobs 一律抛。"""
    out = []
    for page in pages:
        if "jobs" not in page:
            raise BaselineError("裁剪：有一页没有 jobs")
        out.append(
            {
                "total_count": page.get("total_count"),
                "jobs": [
                    {
                        **{k: j.get(k) for k in JOB_FIELDS},
                        "steps": [{k: s.get(k) for k in STEP_FIELDS} for s in j.get("steps", [])],
                    }
                    for j in page["jobs"]
                ],
            }
        )
    return out


def cmd_trim(args: argparse.Namespace) -> int:
    src, dst = Path(args.src), Path(args.dst)
    (dst / "runs").mkdir(parents=True, exist_ok=True)
    (dst / "jobs").mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted((src / "runs").glob("run_*.json")):
        run = _load_json(f)
        (dst / "runs" / f.name).write_text(
            json.dumps(trim_run(run), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        n += 1
    for f in sorted((src / "jobs").glob("jobs_*.raw.json")):
        pages = trim_jobs_pages(load_pages(f.read_text(encoding="utf-8")))
        jobs_from_pages(pages)  # 裁完还得是合法输入
        (dst / "jobs" / f.name).write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in pages) + "\n", encoding="utf-8"
        )
    if n == 0:
        raise BaselineError(f"{src}/runs 下没有 run_*.json")
    print(f"裁剪 {n} 个 run → {dst}")
    return 0


# ---------------------------------------------------------------- 子命令


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError(f"读不了 {path}：{exc}") from exc


def cmd_fetch_runs(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    total = 0
    for page in range(1, args.pages + 1):
        text = gh_api(
            f"repos/{args.repo}/actions/workflows/{args.workflow}/runs?per_page=100&page={page}"
        )
        data = load_pages(text)[0]
        runs = data.get("workflow_runs")
        if not isinstance(runs, list) or not runs:
            raise BaselineError(f"第 {page} 页没有 workflow_runs")
        (out / f"{Path(args.workflow).stem}_runs_p{page}.json").write_text(text, encoding="utf-8")
        total += len(runs)
    print(f"取到 {total} 个 run → {out}")
    return 0


def cmd_fetch_jobs(args: argparse.Namespace) -> int:
    out = Path(args.out)
    (out / "runs").mkdir(parents=True, exist_ok=True)
    (out / "jobs").mkdir(parents=True, exist_ok=True)
    for rid in args.run_id:
        run_text = gh_api(f"repos/{args.repo}/actions/runs/{rid}")
        run = load_pages(run_text)[0]
        (out / "runs" / f"run_{rid}.json").write_text(run_text, encoding="utf-8")
        jobs_text = gh_api(
            f"repos/{args.repo}/actions/runs/{rid}/jobs?filter=all&per_page=100", paginate=True
        )
        jobs_from_pages(load_pages(jobs_text))  # 形状核验
        (out / "jobs" / f"jobs_{rid}.raw.json").write_text(jobs_text, encoding="utf-8")
        attempts = int(run.get("run_attempt") or 1)
        for a in range(1, attempts):
            at = gh_api(
                f"repos/{args.repo}/actions/runs/{rid}/attempts/{a}/jobs?per_page=100",
                paginate=True,
            )
            jobs_from_pages(load_pages(at), attempt=a)
            (out / "jobs" / f"jobs_{rid}.attempt{a}.raw.json").write_text(at, encoding="utf-8")
            (out / "runs" / f"run_{rid}.attempt{a}.json").write_text(
                gh_api(f"repos/{args.repo}/actions/runs/{rid}/attempts/{a}"), encoding="utf-8"
            )
        print(f"run {rid}: attempt {attempts}（含之前 {attempts - 1} 次）已存")
    return 0


def cmd_dag(args: argparse.Namespace) -> int:
    wf = extract_workflow_jobs(Path(args.workflow).read_text(encoding="utf-8"))
    kinds = _load_json(Path(args.edge_kinds)) if args.edge_kinds else None
    dag = build_dag(wf, kinds)
    Path(args.out).write_text(
        json.dumps(dag, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{len(dag['jobs'])} 个 job，{len(dag['edges'])} 条边 → {args.out}")
    return 0


def analyze_evidence(evidence: Path, workflow_jobs: dict[str, dict]) -> list[dict]:
    runs_dir, jobs_dir = evidence / "runs", evidence / "jobs"
    if not runs_dir.is_dir() or not jobs_dir.is_dir():
        raise BaselineError(f"{evidence} 下要有 runs/ 与 jobs/")
    results: list[dict] = []
    for run_file in sorted(runs_dir.glob("run_*.json")):
        run = _load_json(run_file)
        m = re.fullmatch(r"run_(\d+)(?:\.attempt(\d+))?\.json", run_file.name)
        if not m:
            raise BaselineError(f"run 文件名认不出：{run_file.name}")
        rid, att = m.group(1), m.group(2)
        jobs_file = jobs_dir / (
            f"jobs_{rid}.attempt{att}.raw.json" if att else f"jobs_{rid}.raw.json"
        )
        if not jobs_file.is_file():
            raise BaselineError(f"{run_file.name} 没有对应的 {jobs_file.name}")
        attempt = int(run.get("run_attempt") or 0)
        if att and attempt != int(att):
            raise BaselineError(f"{run_file.name}: run_attempt={attempt} 与文件名不符")
        if int(run.get("id") or 0) != int(rid):
            raise BaselineError(f"{run_file.name}: run.id={run.get('id')} 与文件名不符")
        jobs = jobs_from_pages(load_pages(jobs_file.read_text(encoding="utf-8")), attempt=attempt)
        results.append(analyze_run(run, jobs, workflow_jobs))
    if not results:
        raise BaselineError("evidence 里一个 run 都没有——不出空报告")
    return results


def cmd_analyze(args: argparse.Namespace) -> int:
    wf = extract_workflow_jobs(Path(args.workflow).read_text(encoding="utf-8"))
    kinds = _load_json(Path(args.edge_kinds)) if args.edge_kinds else None
    dag = build_dag(wf, kinds)
    runs = analyze_evidence(Path(args.evidence), wf)
    if args.compact:
        for r in runs:
            for j in r["jobs"]:
                j.pop("steps", None)
    report = {
        "schema_version": 1,
        "kind": "ci_baseline_timing_decomposition",
        "generated_by": "scripts/ci/ci_baseline.py analyze",
        "workflow": args.workflow,
        "evidence_dir": args.evidence,
        "run_count": len(runs),
        "dag": dag,
        "runs": runs,
    }
    # 紧凑序列化：同 summarize，脚本产物不按行读
    Path(args.out).write_text(
        json.dumps(report, ensure_ascii=False, indent=None) + "\n", encoding="utf-8"
    )
    print(f"{len(runs)} 个 run 已分解 → {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch-runs", help="列最近的 workflow run（每页 100）")
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--workflow", default="ci.yml")
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_fetch_runs)

    p = sub.add_parser("fetch-jobs", help="取 run 元数据 + 全部 attempt 的 jobs（含 steps）")
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--run-id", action="append", required=True, type=int)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_fetch_jobs)

    p = sub.add_parser("summarize", help="把 analyze 的输出压成分组统计，可并入手写段落")
    p.add_argument("--analysis", required=True)
    p.add_argument("--extra", help="手写段落（JSON 对象），原样并入")
    p.add_argument(
        "--include-runs", type=int, action="append", help="把这些 run 的逐 job 分解也带上"
    )
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_summarize)

    p = sub.add_parser(
        "trim", help="把 fetch-jobs 的原始 JSON 裁成进仓库的证据（只裁字段不裁时刻）"
    )
    p.add_argument("--src", required=True)
    p.add_argument("--dst", required=True)
    p.set_defaults(fn=cmd_trim)

    p = sub.add_parser("dag", help="从 workflow 抽 job 表与 needs 边")
    p.add_argument("--workflow", required=True)
    p.add_argument("--edge-kinds", help="人工分类表（{'a->b': {kind, evidence}}）")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_dag)

    p = sub.add_parser("analyze", help="对 evidence 目录里的每个 run 做四类时间分解")
    p.add_argument("--workflow", required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--edge-kinds")
    p.add_argument("--compact", action="store_true", help="不写每个 step 的明细，只留按类别汇总")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_analyze)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except BaselineError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
