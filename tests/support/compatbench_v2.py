"""CompatBench V2 基线：兼容优先执行（Compatibility-first Execution）的「before」度量装置。

只观测，不改产品行为，不加遥测。复用的东西：

* 真服务子进程 + 会话认证 + 凭据交接：`support.foundation_app.running_app`（FirstOpenBench 的同一套）；
* 公共入口：`POST /api/project/scan` -> `POST /api/engine/preparation-sessions` -> 认领后端生成的 `run` 动作
  （与 `tests/test_foundation_script_first_run.py` 的 T11 同一条路）；
* 指标口径沿用 `05_TEST_STRATEGY` §4/§5：**真实执行次数只认脚本自己写在项目外的日志**，回执 / 回显 / toast 不算。

三把尺各管各的（不合并）：CompatBench（`scripts/ci/compat_matrix.py`）问「引擎层原生 vs Tavotto」，FirstOpenBench
（`foundation_harness`）问「首开到首图」，这里补的是它们都没有的**会话层多场景基线**，结果文件是独立的 JSON
（`TAVOTTO_COMPAT_V2_RESULTS` 指定目录），**不写进** `TAVOTTO_FOUNDATION_RESULTS`（那个目录是闭集校验，混进去会
被当成「未登记的实例」）。

`null` 的含义：**未观测**（现在拿不到，不是 0）。`UNOBSERVED` 里逐项写明缺什么。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in (None, ""):  # `python tests/support/compatbench_v2.py summarize ...` 直接当脚本跑
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from support import foundation_app as fa  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "compat_v2"
SESSIONS = "/api/engine/preparation-sessions"
RESULTS_ENV = "TAVOTTO_COMPAT_V2_RESULTS"
SCHEMA = 1

# 现在拿不到的指标：字段留 null，原因写这里（不伪造）。
UNOBSERVED = {
    "fallback_rate": "main 上没有「回退」概念：准备会话只有一次 attempt、没有 execution/fallback_reason 字段（audit-E §2.2）",
    "intervention_count": "产品内没有干预计数器；这里只数测试驱动认领的动作个数（actions_claimed），那不是用户干预",
    "e2e_first_figure_ms_p50_p95": "每个场景只跑一次，没有分布；端到端（点击到可编辑图）需要前端；这里只有后端会话分段",
}


@dataclass(frozen=True)
class Scenario:
    id: str
    scenes: tuple[str, ...]  # 场景总表编号
    title: str
    failure_mode: str  # 原始失败模式
    expected_strategy: str  # 预期策略（D1–D5）
    ideal_exec_count: int  # 理想真实执行次数（路由直达）
    ideal_exec_count_max: int  # 可接受上限（含一次回退）
    ideal_outcome: str  # figure | stop_no_fallback | partial_figure | dependency_plan
    # main 上实测的形状：只写会随行为修复而改变的判据。None = 对照组（main 上就该达标）。
    main_baseline: dict | None = None
    # 夹具特殊处理
    readonly_project: bool = False
    needs_site_packages: tuple[str, ...] = ()  # 想验证的包名（跑前核本机确实缺 / 有）
    notes: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Observation:
    scenario: str
    final_phase: str = ""
    phases: list[str] = field(default_factory=list)
    exec_count: int = 0
    side_effect_count: int | None = None
    captured_stems: list[str] = field(default_factory=list)
    figures_valid: int = 0
    failed_figures: list[str] = field(default_factory=list)
    problem_codes: list[str] = field(default_factory=list)
    attempts: int | None = None
    actions_claimed: list[str] = field(default_factory=list)
    timings_s: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)
    ran: bool = False  # 是否有可认领的 run 动作且已认领
    error_type: str | None = None
    outcome_kind: str | None = None
    titles: list[str] = field(
        default_factory=list
    )  # 捕获的图里读出的标题（语义真值，不只是「有图」）

    @property
    def first_valid_figure(self) -> bool:
        """第一次尝试就得到有效 Figure（captured 非空且终局是 completed / partial）。"""
        return self.figures_valid > 0 and self.final_phase in ("completed", "partial")

    @property
    def one_shot_success(self) -> bool:
        """一次成功：首次有效出图，且真实执行恰好一次，没有任何额外动作（除了认领 run）。"""
        return (
            self.first_valid_figure
            and self.exec_count == 1
            and set(self.actions_claimed) <= {"run"}
        )

    def to_json(self) -> dict:
        return {
            "scenario": self.scenario,
            "final_phase": self.final_phase,
            "phases": self.phases,
            "exec_count": self.exec_count,
            "side_effect_count": self.side_effect_count,
            "captured_stems": self.captured_stems,
            "figures_valid": self.figures_valid,
            "failed_figures": self.failed_figures,
            "problem_codes": self.problem_codes,
            "attempts": self.attempts,
            "actions_claimed": self.actions_claimed,
            "timings_s": self.timings_s,
            "facts": self.facts,
            "first_valid_figure": self.first_valid_figure,
            "one_shot_success": self.one_shot_success,
            "ran": self.ran,
            "error_type": self.error_type,
            "outcome_kind": self.outcome_kind,
            "titles": self.titles,
        }


def exec_log(parent: Path) -> list[dict]:
    path = parent / "exec_log.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


def side_effects(parent: Path) -> int | None:
    path = parent / "side.sqlite"
    if not path.exists():
        return None
    db = sqlite3.connect(str(path))
    try:
        return int(db.execute("SELECT COUNT(*) FROM effects").fetchone()[0])
    finally:
        db.close()


def stage(scenario_id: str, tmp: Path) -> Path:
    """把夹具复制进隔离临时目录；返回项目根（日志 / 副作用库在它的父目录，项目外）。"""
    proj = tmp / "proj"
    shutil.copytree(FIXTURES / scenario_id, proj)
    return proj


def _wait(app, sid: str, phases, *, timeout: float = 240.0) -> dict:
    deadline = time.time() + timeout
    while True:
        status, report = app.call(f"{SESSIONS}/{sid}", timeout=30)
        assert status == 200, report
        if report["phase"] in phases:
            return report
        assert time.time() < deadline, json.dumps(report, ensure_ascii=False)[:1500]
        time.sleep(0.1)


SETTLED = (
    "ready_to_run",
    "awaiting_confirmation",
    "awaiting_configuration",
    "completed",
    "partial",
    "action_required",
    "failed",
    "cancelled",
)
DONE = ("completed", "partial", "action_required", "failed", "cancelled")


def _problem_codes(report: dict) -> list[str]:
    """终局的原因码：`outcome.code`（会话投影）与 `result.error.code`（引擎）。只取码，不取消息（消息带用户路径）。"""
    codes: list[str] = []
    outcome = report.get("outcome")
    if isinstance(outcome, dict) and outcome.get("code"):
        codes.append(str(outcome["code"]))
    error = (report.get("result") or {}).get("error")
    if isinstance(error, dict) and error.get("code") and str(error["code"]) not in codes:
        codes.append(str(error["code"]))
    return codes


def _dependency_plan(report: dict) -> tuple[list[str], dict[str, str]]:
    """依赖计划里「要装哪些发行包」与「import 名 -> 发行包名」（只读会话报告，不另起解析器）。"""
    installs: list[str] = []
    missing: dict[str, str] = {}
    for req in report.get("requirements") or []:
        payload = req.get("payload") if isinstance(req, dict) else None
        if not isinstance(payload, dict):
            continue
        installs.extend(str(x) for x in (payload.get("impact") or {}).get("installs") or [])
        for m in (payload.get("plan") or {}).get("missing") or []:
            missing[str(m.get("import_name"))] = str(m.get("distribution"))
    return sorted(set(installs)), missing


def _error_type(report: dict) -> str | None:
    """脚本异常的类名（traceback 末行冒号前）；用来区分「真 bug」与「从 savefig 衍生的 FileNotFoundError」。"""
    error = (report.get("result") or {}).get("error")
    tb = error.get("traceback") if isinstance(error, dict) else None
    if not isinstance(tb, str) or not tb.strip():
        return None
    last = tb.strip().splitlines()[-1]
    head = last.split(":", 1)[0].strip()
    return head if head.replace(".", "").replace("_", "").isalnum() else None


def observe(scenario: Scenario, tmp: Path, *, env_overrides: dict | None = None) -> Observation:
    """经真实公共入口跑一遍场景并记录；不替产品选环境、不预热、不预装。"""
    proj = stage(scenario.id, tmp)
    work = tmp / "work"
    obs = Observation(scenario=scenario.id)
    env = {"TAVOTTO_USER_ENV_DISCOVERY": "0", "PYTHONPATH": str(ROOT / "src")}
    env.update(env_overrides or {})
    t_total = time.monotonic()
    ro_restore: list[tuple[Path, int]] = []
    try:
        with fa.running_app(proj, work, env_overrides=env) as app:
            t0 = time.monotonic()
            status, scan = app.call("/api/project/scan", {"reason": "claim"}, timeout=60)
            assert status in (200, 202), scan
            deadline = time.time() + 120
            while scan["state"] == "running":
                assert time.time() < deadline, scan
                time.sleep(0.1)
                _, scan = app.call("/api/project/scan", timeout=30)
            obs.timings_s["scan"] = round(time.monotonic() - t0, 3)
            listed = [t for t in scan["targets"] if t["script"] == "script.py"]
            # 扫描没把它当绘图目标（例如脚本里没有任何绘图证据）时，用户仍可手动选脚本：同一个请求体形状
            session_target = (
                dict(listed[0]["session_target"]) if listed else {"script": "script.py"}
            )
            obs.facts["scan_listed_target"] = bool(listed)

            t0 = time.monotonic()
            status, report = app.call(SESSIONS, session_target, timeout=120)
            assert status == 201, report
            obs.timings_s["check"] = round(time.monotonic() - t0, 3)
            obs.phases.append(report["phase"])
            report = _wait(app, report["session_id"], SETTLED, timeout=120)
            if obs.phases[-1] != report["phase"]:
                obs.phases.append(report["phase"])
            # 检查阶段不得执行用户代码（C23）
            obs.facts["exec_count_after_check"] = len(exec_log(tmp))

            kinds = [a["kind"] for a in report.get("actions", [])]
            obs.facts["actions_offered_after_check"] = kinds
            if report["phase"] == "awaiting_configuration":
                # 运行目录待办：选项目根（与界面同一个端点），之后重新检查
                app.call("/api/engine/workdir", {"mode": "project_root"}, method="PATCH")
                (act,) = [a for a in report["actions"] if a["kind"] == "recheck"]
                app.call(
                    f"{SESSIONS}/{report['session_id']}/actions",
                    {"action_id": act["id"], "expected_config_revision": report["config_revision"]},
                    timeout=60,
                )
                obs.actions_claimed.append("recheck")
                report = _wait(app, report["session_id"], SETTLED, timeout=120)
                obs.phases.append(report["phase"])
                kinds = [a["kind"] for a in report.get("actions", [])]

            if "run" in kinds:
                if scenario.readonly_project:
                    ro_restore.append((proj, proj.stat().st_mode))
                    proj.chmod(0o555)
                (act,) = [a for a in report["actions"] if a["kind"] == "run"]
                t0 = time.monotonic()
                status, claimed = app.call(
                    f"{SESSIONS}/{report['session_id']}/actions",
                    {"action_id": act["id"], "expected_config_revision": report["config_revision"]},
                    timeout=60,
                )
                obs.actions_claimed.append("run")
                obs.ran = status == 202
                report = _wait(app, report["session_id"], DONE, timeout=240)
                obs.timings_s["run_to_terminal"] = round(time.monotonic() - t0, 3)
                obs.phases.append(report["phase"])

            obs.final_phase = report["phase"]
            captured = report.get("captured") or []
            obs.captured_stems = [c.get("stem", "") for c in captured]
            obs.figures_valid = len(captured)
            obs.failed_figures = [
                str(f.get("stem"))
                for f in (report.get("failed_figures") or [])
                if isinstance(f, dict)
            ]
            for cap in captured:
                with contextlib.suppress(Exception):
                    render = app.render(cap["asset_id"])
                    for el in render["manifest"]["elements"]:
                        if el["role"] == "title":
                            obs.titles.extend(
                                str(f["value"]) for f in el["editable"] if f["prop"] == "text"
                            )
            obs.problem_codes = _problem_codes(report)
            obs.error_type = _error_type(report)
            obs.outcome_kind = (report.get("outcome") or {}).get("kind")
            obs.facts["facts"] = report.get("facts")
            provider = (report.get("report") or {}).get("provider") or {}
            obs.attempts = provider.get("attempts")
            obs.facts["dependency_installs"], obs.facts["dependency_missing"] = _dependency_plan(
                report
            )
            obs.facts["actions_offered_final"] = [a["kind"] for a in report.get("actions", [])]
            obs.facts["headline_code"] = (
                (report.get("headline") or {}).get("code")
                if isinstance(report.get("headline"), dict)
                else None
            )
            obs.facts["report_keys"] = sorted(report)
    finally:
        for path, mode in ro_restore:
            with contextlib.suppress(OSError):
                path.chmod(mode)
        obs.timings_s["total"] = round(time.monotonic() - t_total, 3)
        obs.exec_count = len(exec_log(tmp))
        obs.side_effect_count = side_effects(tmp)
    return obs


# ---------------------------------------------------------------- 汇总（只算已观测的）


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(sorted(values), n=100, method="inclusive")[int(q) - 1]


def summarize(rows: list[dict]) -> dict:
    """汇总：分母 = 本次跑的场景；缺数据的字段 null（05_TEST_STRATEGY §7）。"""
    n = len(rows)
    figure_scenarios = [r for r in rows if r["ideal_outcome"] in ("figure", "partial_figure")]
    fv = [r for r in figure_scenarios if r["observed"]["first_valid_figure"]]
    one = [r for r in figure_scenarios if r["observed"]["one_shot_success"]]
    execs = [r["observed"]["exec_count"] for r in rows]
    totals = [r["observed"]["timings_s"].get("total") for r in rows]
    return {
        "scenarios": n,
        "figure_scenarios": len(figure_scenarios),
        "first_valid_figure_rate": round(len(fv) / len(figure_scenarios), 4)
        if figure_scenarios
        else None,
        "one_shot_success_rate": round(len(one) / len(figure_scenarios), 4)
        if figure_scenarios
        else None,
        "exec_count_total": sum(execs),
        "exec_count_distribution": {str(k): execs.count(k) for k in sorted(set(execs))},
        "ideal_met": sum(1 for r in rows if r["ideal_met"]),
        "diverges_from_ideal": [r["scenario"] for r in rows if not r["ideal_met"]],
        "backend_session_total_s": {
            "p50": _pct([t for t in totals if t is not None], 50),
            "p95": _pct([t for t in totals if t is not None], 95),
            "note": "后端会话分段合计（含起服务、扫描），每场景一次，不是端到端；样本少，仅作量级参考",
        },
        "fallback_rate": None,
        "intervention_count": None,
        "e2e_first_figure_ms_p50_p95": None,
        "unobserved": UNOBSERVED,
    }


def results_dir() -> Path | None:
    raw = os.environ.get(RESULTS_ENV, "").strip()
    return Path(raw) if raw else None


def record(
    obs: Observation, scenario: Scenario, ideal_missing: list[str], baseline_ok: bool | None
) -> dict:
    """一条结果记录（独立 JSON；`null` = 未观测）。"""
    from support import foundation_harness as fh  # 只借 source_sha，不进它的闭集校验

    return {
        "schema": SCHEMA,
        "scenario": scenario.id,
        "scenes": list(scenario.scenes),
        "title": scenario.title,
        "failure_mode": scenario.failure_mode,
        "expected_strategy": scenario.expected_strategy,
        "ideal_exec_count": scenario.ideal_exec_count,
        "ideal_exec_count_max": scenario.ideal_exec_count_max,
        "ideal_outcome": scenario.ideal_outcome,
        "ideal_met": not ideal_missing,
        "ideal_missing": ideal_missing,
        "main_baseline": scenario.main_baseline,
        "matches_registered_baseline": baseline_ok,
        "observed": obs.to_json(),
        "source_sha": fh.source_sha(ROOT),
        "recorded_at": time.time(),
        "unobserved": UNOBSERVED,
    }


def write_record(rec: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{rec['scenario']}.json"
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return path


def load_records(directory: Path) -> list[dict]:
    return [
        json.loads(p.read_text("utf-8"))
        for p in sorted(directory.glob("*.json"))
        if p.name != "summary.json"
    ]


def main(argv: list[str] | None = None) -> int:
    """`python tests/support/compatbench_v2.py summarize DIR [--out FILE]`：把逐场景记录汇成一份机器可读基线。"""
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sm = sub.add_parser("summarize")
    sm.add_argument("dir", type=Path)
    sm.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    rows = load_records(args.dir)
    if not rows:
        print(f"{args.dir} 里没有记录——空集合不是通过")
        return 2
    report = {
        "schema": SCHEMA,
        "kind": "compatbench_v2_session_baseline",
        "source_sha": rows[0]["source_sha"],
        "summary": summarize(rows),
        "scenarios": rows,
    }
    text = json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
