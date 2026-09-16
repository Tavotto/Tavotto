#!/usr/bin/env python3
"""CI05 前后对照：把 `timing/timing_<wf>.json`（after，`ci_baseline.py analyze` 按 run 所在
分支的 ci.yml 分解）与 CI00 的 `../actions/timing_decomposition.json`（before，29 个 merge_group
success 的中位）并排，写成 `comparison.json`。

三条纪律：

* **只有中位 / min / max，不算 p95**（after 每档 n = 1–2）。
* **争抢样本单列**：一个 run 只要关键路径上任一 job 的 `runner_wait` ≥ 60s，就标 `contended`，
  不混进「可比」列。给它算一个 `qualification_model_zero_runner_wait`——把每个 job 的
  runner_wait 当 0、其余（dispatch_gap / job_seconds / needs）照实测重放 DAG 得出的资格时长。
  **这是口径不是测量**：它回答「如果 runner 秒领，这条 DAG 会在几秒出结论」，不回答「实际几秒」。
* 每个数字回指 run id / attempt / job 显示名；step 级秒数直接从 `actions/jobs/` 的裁剪 JSON 读
  （名字逐字匹配 ci.yml 的 `name:`）。

纯标准库；不联网。
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[4] / "scripts" / "ci"))
from ci_baseline import display_to_job_id, extract_workflow_jobs, load_pages  # noqa: E402

BEFORE = HERE.parent / "actions" / "timing_decomposition.json"
CONTENTION_THRESHOLD_SECONDS = 60

#: 每个 after run 的身份：PR、含哪些改动、分解用的 ci.yml 快照、备注。
RUNS = {
    34993304390: {
        "pr": 372,
        "label": "CI00（对照：与 before 同 DAG）",
        "changes": [],
        "workflow": "8b95256c",
        "kind": "after-control",
        "full_ci": True,
    },
    34994534095: {
        "pr": 373,
        "label": "CI01 删边",
        "changes": ["CI01"],
        "workflow": "79c5aa38",
        "kind": "after",
        "full_ci": True,
        "note": "attempt 1 的 windows-exe-smoke 挂 60 min 被 job 级超时硬杀（Gate failure）；attempt 2 是 re-run failed jobs，"
        "只重跑了 windows-exe-smoke + integration gate（其余 21 个 job carried_over）——所以 attempt 1 给 DAG 等待的样本，"
        "attempt 2 只给那一个 job 的时长，两者都不是完整的资格样本",
    },
    35004450721: {
        "pr": 374,
        "label": "CI01 + CI03a（首跑，Windows 片 2 红：新用例编码问题）",
        "changes": ["CI01", "CI03a"],
        "workflow": "d9e8dd72",
        "kind": "after-red",
        "full_ci": True,
    },
    35007730894: {
        "pr": 374,
        "label": "CI01 + CI03a",
        "changes": ["CI01", "CI03a"],
        "workflow": "d9e8dd72",
        "kind": "after",
        "full_ci": True,
    },
    35011613925: {
        "pr": 375,
        "label": "+ CI03c",
        "changes": ["CI01", "CI03a", "CI03c"],
        "workflow": "35b912a0",
        "kind": "after",
        "full_ci": True,
    },
    35024490379: {
        "pr": 376,
        "label": "+ CI03b（首跑，macOS 片 2 红 10 条：getfqdn）",
        "changes": ["CI01", "CI03a", "CI03c", "CI03b"],
        "workflow": "1f7f13e8",
        "kind": "after-red",
        "full_ci": True,
    },
    35028309531: {
        "pr": 376,
        "label": "+ CI03b（诊断跑，红 5）",
        "changes": ["CI01", "CI03a", "CI03c", "CI03b"],
        "workflow": "1f7f13e8",
        "kind": "after-red",
        "full_ci": True,
    },
    35031461863: {
        "pr": 376,
        "label": "+ CI03b",
        "changes": ["CI01", "CI03a", "CI03c", "CI03b"],
        "workflow": "162f54c6",
        "kind": "after",
        "full_ci": True,
    },
    35031790918: {
        "pr": 377,
        "label": "+ CI02（Windows 去 --with-deps）",
        "changes": ["CI01", "CI03a", "CI03c", "CI03b", "CI02"],
        "workflow": "bb27bdaf",
        "kind": "after",
        "full_ci": True,
        "note": "与 #376 / #378 的 run 同时跑（22:32–23:20）",
    },
    35031800904: {
        "pr": 378,
        "label": "+ CI04（无 yml 行为改动；无 full-ci 标签）",
        "changes": ["CI01", "CI03a", "CI03c", "CI03b", "CI02", "CI04"],
        "workflow": "bb27bdaf",
        "kind": "after-fast-only",
        "full_ci": False,
        "note": "重型 5 个 skipped，integration gate 是 deferred（绿）——不是资格样本，只有快线",
    },
    # merge_group 对照（别人的 PR，ci.yml 仍是 8b95256c 那份）
    34957615294: {
        "pr": 358,
        "label": "merge_group 对照",
        "changes": [],
        "workflow": "8b95256c",
        "kind": "control-mg",
    },
    34970490865: {
        "pr": 359,
        "label": "merge_group 对照（CI00 样本 run）",
        "changes": [],
        "workflow": "8b95256c",
        "kind": "control-mg",
    },
    34990070055: {
        "pr": 360,
        "label": "merge_group 对照",
        "changes": [],
        "workflow": "8b95256c",
        "kind": "control-mg",
    },
    35002647792: {
        "pr": 361,
        "label": "merge_group 对照",
        "changes": [],
        "workflow": "8b95256c",
        "kind": "control-mg",
    },
    35015416419: {
        "pr": 362,
        "label": "merge_group 对照",
        "changes": [],
        "workflow": "8b95256c",
        "kind": "control-mg",
    },
    35027644355: {
        "pr": 357,
        "label": "merge_group 对照（与 lab nightly 35028176892 同时）",
        "changes": [],
        "workflow": "8b95256c",
        "kind": "control-mg",
    },
}

#: 分项贡献要看的 step（前缀逐字取自各版 ci.yml 的 `name:`；括号后缀按版本不同）
STEPS_OF_INTEREST = {
    "windows-exe-smoke": [
        "装 web 依赖与本片的浏览器",  # CI03c 起
        "Playwright 黄金路径",
        "Playwright 分片自验",
    ],
    "package": ["起服务并请求首页", "装进干净环境并冒烟"],
    "backend-fast": ["pytest"],
    "backend-platforms": ["pytest"],
}


def _ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _med(xs):
    xs = [x for x in xs if isinstance(x, int | float)]
    return statistics.median(xs) if xs else None


def zero_runner_wait_model(run: dict, workflow_jobs: dict, target: str) -> int | None:
    """runner_wait := 0，其余照实测重放 DAG；target 没执行就 None。"""
    by_id: dict[str, list[dict]] = {}
    for j in run["jobs"]:
        if j["state"] == "executed":
            by_id.setdefault(j["job_id"], []).append(j)
    if target not in by_id:
        return None
    finish: dict[str, int] = {}

    def done(jid: str) -> int:
        if jid in finish:
            return finish[jid]
        needs = [n for n in workflow_jobs[jid]["needs"] if n in by_id]
        ready = max((done(n) for n in needs), default=0)
        finish[jid] = max(ready + d["dispatch_gap"] + d["job_seconds"] for d in by_id[jid])
        return finish[jid]

    return done(target)


def step_seconds(jobs_file: Path, attempt: int, wanted: dict[str, list[str]], wf: dict) -> dict:
    """{显示名: {step 名: 秒}}——只取 executed 的 step。"""
    out: dict[str, dict[str, int | None]] = {}
    for page in load_pages(jobs_file.read_text("utf-8")):
        for j in page["jobs"]:
            if j.get("run_attempt") != attempt:
                continue
            jid = display_to_job_id(j["name"], wf)
            if jid not in wanted:
                continue
            row: dict[str, int | None] = {}
            for s in j.get("steps", []):
                # ci.yml 的 step 名带括号后缀（`Playwright 黄金路径（…本片 --project=chromium）`），按前缀配
                if any(str(s.get("name", "")).startswith(w) for w in wanted[jid]):
                    if (
                        s.get("started_at")
                        and s.get("completed_at")
                        and s.get("conclusion") != "skipped"
                    ):
                        row[s["name"]] = int(
                            (_ts(s["completed_at"]) - _ts(s["started_at"])).total_seconds()
                        )
                    else:
                        row[s["name"]] = None
            if row:
                out[j["name"]] = row
    return out


def main() -> int:
    before = json.loads(BEFORE.read_text("utf-8"))
    mg = [
        r
        for r in before["runs"]
        if r["event"] == "merge_group" and r["conclusion"] == "success" and r["attempt"] == 1
    ]
    assert len(mg) == 29, len(mg)
    before_jobs: dict[str, list[dict]] = {}
    for r in mg:
        for j in r["jobs"]:
            if j["state"] == "executed":
                before_jobs.setdefault(j["name"], []).append(j)
    before_summary = {
        "n": len(mg),
        "feedback_median": _med([r["feedback_seconds"] for r in mg]),
        "qualification_median": _med([r["qualification_seconds"] for r in mg]),
        "qualification_min_max": [
            min(r["qualification_seconds"] for r in mg),
            max(r["qualification_seconds"] for r in mg),
        ],
        "jobs": {
            name: {
                "n": len(js),
                "job_seconds_median": _med([j["job_seconds"] for j in js]),
                "dependency_wait_median": _med([j["dependency_wait"] for j in js]),
                "runner_wait_median": _med([j["runner_wait"] for j in js]),
                "test_median": _med([j["execution"].get("test", 0) for j in js]),
            }
            for name, js in sorted(before_jobs.items())
        },
    }

    after_runs = []
    for tf in sorted((HERE / "timing").glob("timing_*.json")):
        rep = json.loads(tf.read_text("utf-8"))
        wf_path = HERE.parents[4] / rep["workflow"]
        wf = extract_workflow_jobs(wf_path.read_text("utf-8"))
        for r in rep["runs"]:
            meta = RUNS[r["run_id"]]
            crit_rw = [
                x["runner_wait"] for x in r["critical_path"] if isinstance(x["runner_wait"], int)
            ]
            all_rw = [j["runner_wait"] for j in r["jobs"] if j["state"] == "executed"]
            contended = max(crit_rw, default=0) >= CONTENTION_THRESHOLD_SECONDS
            jobs_file = (
                HERE
                / "actions"
                / "jobs"
                / (
                    f"jobs_{r['run_id']}.attempt{r['attempt']}.raw.json"
                    if (
                        HERE
                        / "actions"
                        / "jobs"
                        / f"jobs_{r['run_id']}.attempt{r['attempt']}.raw.json"
                    ).exists()
                    else f"jobs_{r['run_id']}.raw.json"
                )
            )
            steps = step_seconds(jobs_file, r["attempt"], STEPS_OF_INTEREST, wf)
            heavy_dw = {
                j["name"]: j["dependency_wait"]
                for j in r["jobs"]
                if j["job_id"] in ("package", "windows-exe-smoke", "macos-app-smoke", "posix-e2e")
                and j["state"] == "executed"
            }
            after_runs.append(
                {
                    "run_id": r["run_id"],
                    "attempt": r["attempt"],
                    "pr": meta["pr"],
                    "label": meta["label"],
                    "changes": meta["changes"],
                    "kind": meta["kind"],
                    "note": meta.get("note"),
                    "event": r["event"],
                    "conclusion": r["conclusion"],
                    "head_sha": r["head_sha"],
                    "workflow_snapshot": rep["workflow"],
                    "t0": r["t0"],
                    "job_states": r["job_states"],
                    "feedback_seconds": r["feedback_seconds"],
                    "qualification_seconds": r["qualification_seconds"],
                    "qualification_is_a_real_verdict": meta.get("full_ci", True)
                    and r["event"] in ("merge_group", "pull_request")
                    and r["job_states"]["executed"] > 2,
                    "critical_path": [
                        {
                            "job": x["name"],
                            "job_seconds": x["job_seconds"],
                            "runner_wait": x["runner_wait"],
                        }
                        for x in r["critical_path"]
                    ],
                    "max_runner_wait_on_critical_path": max(crit_rw, default=0),
                    "max_runner_wait_any_job": max(all_rw, default=0),
                    "contended": contended,
                    "qualification_model_zero_runner_wait": zero_runner_wait_model(
                        r, wf, "ci-integration-gate"
                    ),
                    "feedback_model_zero_runner_wait": zero_runner_wait_model(
                        r, wf, "ci-fast-gate"
                    ),
                    "runner_minutes_by_label": r["runner_minutes_by_label"],
                    "heavy_dependency_wait": heavy_dw,
                    "jobs": [
                        {
                            "name": j["name"],
                            "job_id": j["job_id"],
                            "state": j["state"],
                            "conclusion": j["conclusion"],
                            "dependency_wait": j["dependency_wait"],
                            "dispatch_gap": j["dispatch_gap"],
                            "runner_wait": j["runner_wait"],
                            "job_seconds": j["job_seconds"],
                            "execution": j["execution"],
                            "died_at_step": j.get("died_at_step"),
                        }
                        for j in sorted(r["jobs"], key=lambda j: j["name"])
                    ],
                    "steps_of_interest": steps,
                }
            )
    after_runs.sort(key=lambda r: (r["t0"], r["attempt"]))
    out = {
        "kind": "ci05_before_after_comparison",
        "generated_by": "docs/implementation/ci-foundation/evidence/ci05/compare.py",
        "rules": {
            "no_percentiles": "after 每档 n = 1–2，只给单值 / 中位 / min–max",
            "contended": f"关键路径上任一 job 的 runner_wait ≥ {CONTENTION_THRESHOLD_SECONDS}s 即标 contended，单列不混入可比列",
            "qualification_model_zero_runner_wait": "runner_wait := 0 重放 DAG 的口径（不是测量）：dispatch_gap / job_seconds / needs 照实测",
        },
        "before": before_summary,
        "after": after_runs,
    }
    (HERE / "comparison.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=None) + "\n", "utf-8"
    )
    print(
        f"before n={before_summary['n']} feedback={before_summary['feedback_median']} qualification={before_summary['qualification_median']}"
    )
    for r in after_runs:
        print(
            f"{r['run_id']} a{r['attempt']} PR#{r['pr']:<4} {r['kind']:<15} fb={r['feedback_seconds']!s:>12} "
            f"q={r['qualification_seconds']!s:>5} q0={r['qualification_model_zero_runner_wait']!s:>5} "
            f"contended={r['contended']!s:<5} maxrw_cp={r['max_runner_wait_on_critical_path']:>4} {r['label']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
