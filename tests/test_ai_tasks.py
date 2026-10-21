"""TAF Task Core（`engine/ai_tasks.py`，ADR 0138 Phase 1）。

主语：Task Core 本身——契约、注册表、编排、预算、持久化——用 Fake 适配器 + Fake 执行器驱动，**不碰任何用户文件、
不调任何模型**。会话行写进每个用例自己的临时历史库（`db_path`），与 AI 桥的真实库隔离。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from tavotto.engine import ai_history, ai_tasks
from tavotto.rendercore import inspector

SRC = Path(__file__).resolve().parents[1] / "src"


def _env(**over) -> dict:
    d = {
        "schema_version": 1,
        "task_type": "fake_task",
        "project_ref": {"project_key": "/proj/figs"},
        "user_intent": "把标题字号改成 12",
        "execution_policy": {"executor": "deterministic"},
    }
    d.update(over)
    return d


class FakeAdapter:
    task_type = "fake_task"

    def __init__(
        self, pre="verified", post=(("g1", "goal", "verified"),), needs_input=None, plan_error=False
    ):
        self.pre, self.post, self.needs, self.plan_error = pre, post, needs_input, plan_error
        self.verify_calls: list[str] = []

    def validate(self, envelope):
        return [] if envelope.user_intent else ["empty"]

    def plan(self, envelope):
        if self.plan_error:
            raise RuntimeError("plan boom")
        return ai_tasks.Plan(needs_input=self.needs)

    def verify(self, stage, run, candidate):
        self.verify_calls.append(stage)
        if stage == "precommit":
            if self.pre == "raise":
                raise RuntimeError("verifier boom")
            return [ai_tasks.CheckResult("L1.syntax", "L1", "precommit", "integrity", self.pre)]
        return [
            ai_tasks.CheckResult(cid, "L2", "postcommit", role, v) for cid, role, v in self.post
        ]

    def explain(self, outcome, unmet):
        return {"message_key": f"fake.{outcome}", "params": {"n": len(unmet)}}


class FakeExecutor:
    executor_id = "deterministic"
    kind = "deterministic"
    writes_user_files = False

    def __init__(self, on_execute=None, fail=None):
        self.executed = 0
        self.committed: list = []
        self.discarded: list = []
        self.on_execute, self.fail = on_execute, fail

    def execute(self, run):
        self.executed += 1
        if self.on_execute:
            self.on_execute(run)
        if self.fail:
            raise self.fail
        return {"candidate": run.task_id}

    def commit(self, run, candidate):
        self.committed.append(candidate)

    def discard(self, run, candidate):
        self.discarded.append(candidate)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "hist.sqlite3"


@pytest.fixture
def adapter():
    a = FakeAdapter()
    ai_tasks.register(a)
    yield a
    ai_tasks.unregister("fake_task")


def _run(env_dict, executor, db, **kw):
    return ai_tasks.run_task(ai_tasks.TaskEnvelope.from_dict(env_dict), executor, db_path=db, **kw)


# --------------------------------------------------------------------------- 契约
def test_envelope_round_trips_and_rejects_unknown_fields():
    env = ai_tasks.TaskEnvelope.from_dict(_env(client_request_id="rid-00000001"))
    assert ai_tasks.TaskEnvelope.from_dict(env.to_dict()) == env
    with pytest.raises(ai_tasks.ContractError) as e:
        ai_tasks.TaskEnvelope.from_dict(_env(sandbox="off"))
    assert e.value.code == "contract_unknown_field"
    with pytest.raises(ai_tasks.ContractError) as e:
        ai_tasks.TaskEnvelope.from_dict(
            _env(output_contract={"allowed_writes": [], "max_files": 99})
        )
    assert e.value.code == "contract_unknown_field"


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"schema_version": 2}, "contract_version"),
        ({"task_type": "Bad Type"}, "contract_task_type"),
        ({"user_intent": "   "}, "contract_type"),
        (
            {"user_intent": "x" * (ai_history.INSTRUCTION_MAX_BYTES + 1)},
            "contract_intent_too_large",
        ),
        ({"client_request_id": "short"}, "contract_request_id"),
        (
            {"execution_policy": {"executor": "deterministic", "render": "native"}},
            "contract_render_policy",
        ),
        ({"execution_policy": {"executor": "not-an-agent"}}, "contract_executor"),
        ({"input_refs": [{"kind": "data_file", "path_rel": "/etc/passwd"}]}, "contract_path"),
        ({"input_refs": [{"kind": "data_file", "path_rel": "data/../../x.csv"}]}, "contract_path"),
        ({"input_refs": [{"kind": "data_file", "path_rel": "C:/x.csv"}]}, "contract_path"),
        ({"target_refs": [{"kind": "spreadsheet"}]}, "contract_ref_kind"),
        ({"budget": {"max_agent_runs": 11}}, "contract_budget"),
        ({"budget": {"max_wall_s": 0}}, "contract_budget"),
        ({"budget": {"max_agent_runs": True}}, "contract_budget"),
    ],
)
def test_envelope_rejects_invalid_contracts(over, code):
    with pytest.raises(ai_tasks.ContractError) as e:
        ai_tasks.TaskEnvelope.from_dict(_env(**over))
    assert e.value.code == code


def test_executor_must_be_a_registered_coding_agent_or_deterministic():
    """执行者只认 `AGENT_REGISTRY`：不另立第二份 Agent 名单。"""
    from tavotto.engine import ai_agents

    for agent_id in ai_agents.agent_ids():
        assert (
            ai_tasks.TaskEnvelope.from_dict(_env(execution_policy={"executor": agent_id})).executor
            == agent_id
        )


def test_verdicts_are_the_inspector_vocabulary():
    assert ai_tasks.VERDICTS is inspector.VERDICTS


# --------------------------------------------------------------------------- 判定规则
def test_aggregate_verdict_is_worst_of_and_ignores_not_applicable():
    agg = ai_tasks.aggregate_verdict
    assert agg([]) == "not_applicable"
    assert agg(["not_applicable"]) == "not_applicable"
    assert agg(["verified", "not_applicable"]) == "verified"
    assert agg(["verified", "unknown"]) == "unknown"
    assert agg(["unknown", "failed", "verified"]) == "failed"


def _c(cid, role, verdict, stage="postcommit"):
    return ai_tasks.CheckResult(cid, "L2", stage, role, verdict)


@pytest.mark.parametrize(
    ("committed", "checks", "required", "want"),
    [
        (False, [_c("g", "goal", "verified")], (), "failed"),
        (True, [_c("g", "goal", "verified"), _c("p", "preserve", "failed")], (), "failed"),
        (True, [_c("g1", "goal", "verified"), _c("g2", "goal", "failed")], (), "partial"),
        (True, [_c("g1", "goal", "failed")], (), "failed"),
        (True, [_c("g", "goal", "verified"), _c("p", "preserve", "unknown")], ("p",), "partial"),
        (True, [_c("g", "goal", "verified")], ("p",), "partial"),
        (True, [_c("g", "goal", "verified"), _c("p", "preserve", "verified")], ("p",), "completed"),
        (True, [_c("p", "preserve", "verified")], (), "completed"),
        (True, [_c("g", "goal", "verified"), _c("x", "preserve", "unknown")], (), "partial"),
    ],
)
def test_derive_outcome_never_turns_unknown_into_completed(committed, checks, required, want):
    outcome, unmet = ai_tasks.derive_outcome(committed=committed, checks=checks, required=required)
    assert outcome == want
    if want != "completed":
        assert unmet or not committed


def test_check_result_rejects_vocabulary_outside_the_closed_sets():
    with pytest.raises(ValueError):
        ai_tasks.CheckResult("x", "L2", "postcommit", "goal", "passed")
    with pytest.raises(ValueError):
        ai_tasks.CheckResult("x", "L9", "postcommit", "goal", "verified")


# --------------------------------------------------------------------------- 注册表
def test_production_adapter_registry_is_empty_in_phase_1():
    """生产注册表只放真的能跑起来的适配器：figure_edit / data_to_figure 在各自阶段取得资格后才登记。"""
    assert ai_tasks.ADAPTERS == {}


def test_registry_rejects_duplicates_and_unknown_types(adapter):
    with pytest.raises(ValueError):
        ai_tasks.register(FakeAdapter())
    with pytest.raises(ai_tasks.ContractError) as e:
        ai_tasks.get_adapter("nope_task")
    assert e.value.code == "task_type_unknown"


# --------------------------------------------------------------------------- 编排
def test_fake_task_completes_and_is_persisted_with_a_machine_readable_result(adapter, db):
    ex = FakeExecutor()
    res = _run(_env(), ex, db)
    assert res["outcome"] == "completed" and res["status"] == "done" and res["final"] is True
    assert res["deduped"] is False and res["phase"] is None
    assert ex.executed == 1 and len(ex.committed) == 1 and ex.discarded == []
    assert adapter.verify_calls == ["precommit", "postcommit"]
    v = res["verification"]
    assert v["figure"]["verdict"] == "verified"
    assert v["persistence"]["verdict"] == "not_applicable"
    assert v["explain"] == {"message_key": "fake.completed", "params": {"n": 0}}
    json.dumps(res)
    row = ai_history.get(res["task_id"], db_path=db)
    assert row["task_type"] == "fake_task" and row["outcome"] == "completed"
    assert "user_intent" not in row["envelope"], "原话已在 prompt 列，envelope 不存第二份"
    assert row["prompt"] == "把标题字号改成 12"


def test_same_request_id_executes_once(adapter, db):
    ex = FakeExecutor()
    first = _run(_env(client_request_id="rid-abcdef01"), ex, db)
    again = _run(_env(client_request_id="rid-abcdef01"), ex, db)
    assert ex.executed == 1
    assert again["deduped"] is True and again["task_id"] == first["task_id"]
    assert again["outcome"] == "completed"


def test_concurrent_submissions_with_one_request_id_execute_once(adapter, db):
    gate = threading.Event()
    ex = FakeExecutor(on_execute=lambda run: gate.wait(5))
    results: list[dict] = []

    def go():
        results.append(_run(_env(client_request_id="rid-concurr1"), ex, db))

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(10)
    assert ex.executed == 1
    assert sorted(r["deduped"] for r in results) == [False, True, True, True]
    assert len({r["task_id"] for r in results}) == 1


def test_cancel_during_execution_discards_the_candidate_and_never_commits(adapter, db):
    started = threading.Event()
    release = threading.Event()

    def block(run):
        started.set()
        release.wait(5)

    ex = FakeExecutor(on_execute=block)
    out: list[dict] = []
    t = threading.Thread(target=lambda: out.append(_run(_env(), ex, db)))
    t.start()
    assert started.wait(5)
    (task_id,) = list(ai_tasks._RUNNING)
    assert ai_history.get(task_id, db_path=db)["phase"] == "executing"
    assert ai_tasks.cancel(task_id) is True
    release.set()
    t.join(10)
    res = out[0]
    assert res["outcome"] == "cancelled" and res["status"] == "cancelled"
    assert ex.committed == [] and len(ex.discarded) == 1
    assert ai_tasks.cancel(task_id) is False, "结束了的任务不再可取消"


def test_timeout_is_reported_honestly_and_blocks_commit(adapter, db):
    now = [0.0]

    def slow(run):
        now[0] += 31.0

    ex = FakeExecutor(on_execute=slow)
    res = _run(_env(budget={"max_wall_s": 30}), ex, db, clock=lambda: now[0])
    assert res["status"] == "timeout" and res["outcome"] == "failed"
    assert res["error"] == "timeout"
    assert {"constraint_id": "budget.max_wall_s", "reason_code": "timeout"} in res["verification"][
        "unmet"
    ]
    assert ex.committed == [] and len(ex.discarded) == 1


def test_verifier_crash_is_unknown_and_blocks_commit(db):
    a = FakeAdapter(pre="raise")
    ai_tasks.register(a)
    try:
        ex = FakeExecutor()
        res = _run(_env(), ex, db)
    finally:
        ai_tasks.unregister("fake_task")
    assert res["outcome"] == "failed" and ex.committed == []
    (check,) = res["verification"]["figure"]["checks"]
    assert check["verdict"] == "unknown" and check["reason_code"] == "verifier_error"
    assert res["verification"]["figure"]["verdict"] == "unknown"


def test_precommit_unknown_is_not_a_pass(db):
    ai_tasks.register(FakeAdapter(pre="unknown"))
    try:
        ex = FakeExecutor()
        res = _run(_env(), ex, db)
    finally:
        ai_tasks.unregister("fake_task")
    assert res["outcome"] == "failed" and res["error"] == "precommit_not_verified"
    assert ex.committed == [] and len(ex.discarded) == 1


def test_postcommit_preserve_failure_is_failed_not_partial(db):
    ai_tasks.register(
        FakeAdapter(post=(("g", "goal", "verified"), ("p.data", "preserve", "failed")))
    )
    try:
        res = _run(_env(), FakeExecutor(), db)
    finally:
        ai_tasks.unregister("fake_task")
    assert res["outcome"] == "failed" and res["status"] == "failed"
    assert {"constraint_id": "p.data", "reason_code": "failed"} in res["verification"]["unmet"]


def test_needs_input_does_not_execute(db):
    ai_tasks.register(FakeAdapter(needs_input="axis_meaning_ambiguous"))
    try:
        ex = FakeExecutor()
        res = _run(_env(), ex, db)
    finally:
        ai_tasks.unregister("fake_task")
    assert res["outcome"] == "needs_input" and res["status"] == "done" and res["final"]
    assert ex.executed == 0


def test_agent_budget_is_charged_before_any_execution(db):
    ai_tasks.register(FakeAdapter())
    ex = FakeExecutor()
    ex.executor_id, ex.kind = "codex", "agent"
    try:
        res = _run(
            _env(execution_policy={"executor": "codex"}, budget={"max_agent_runs": 0}), ex, db
        )
    finally:
        ai_tasks.unregister("fake_task")
    assert ex.executed == 0
    assert res["outcome"] == "failed" and res["error"] == "budget_exhausted"
    assert res["budget_used"]["agent_runs"] == 0


def test_executor_failure_and_plan_failure_end_in_a_terminal_row(db):
    ai_tasks.register(FakeAdapter())
    try:
        res = _run(_env(), FakeExecutor(fail=ai_tasks.TaskError("agent_invalid_output")), db)
        assert (
            res["outcome"] == "failed" and res["error"] == "agent_invalid_output" and res["final"]
        )
        res = _run(_env(), FakeExecutor(fail=KeyError("x")), db)
        assert res["outcome"] == "failed" and res["error"] == "executor_error:KeyError"
    finally:
        ai_tasks.unregister("fake_task")
    ai_tasks.register(FakeAdapter(plan_error=True))
    try:
        ex = FakeExecutor()
        res = _run(_env(), ex, db)
    finally:
        ai_tasks.unregister("fake_task")
    assert res["outcome"] == "failed" and res["final"] and ex.executed == 0
    assert res["error"] == "executor_error:RuntimeError"


def test_writing_user_files_is_rejected_before_anything_is_recorded(adapter, db):
    ex = FakeExecutor()
    with pytest.raises(ai_tasks.ContractError) as e:
        _run(_env(output_contract={"allowed_writes": ["fig1.py"]}), ex, db)
    assert e.value.code == "contract_writes_unsupported"
    ex.writes_user_files = True
    with pytest.raises(ai_tasks.ContractError):
        _run(_env(), ex, db)
    assert ex.executed == 0
    assert ai_history.list_sessions("/proj/figs", db_path=db)["total"] == 0


def test_invalid_input_is_rejected_before_anything_is_recorded(adapter, db):
    class Picky(FakeAdapter):
        def validate(self, envelope):
            return ["no_target"]

    ai_tasks.unregister("fake_task")
    ai_tasks.register(Picky())
    with pytest.raises(ai_tasks.ContractError) as e:
        _run(_env(), FakeExecutor(), db)
    assert e.value.code == "task_input_invalid"
    assert ai_history.list_sessions("/proj/figs", db_path=db)["total"] == 0


# --------------------------------------------------------------------------- 重启与旧路径
def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_restart_marks_a_task_whose_owner_died_as_interrupted(db):
    ai_history.record_start(
        {
            "id": "deadbeef0001",
            "project": "/proj/figs",
            "provider": "deterministic",
            "prompt": "x",
            "task_type": "fake_task",
            "phase": "executing",
            "envelope": {},
            "owner_pid": _dead_pid(),
        },
        db_path=db,
    )
    running = ai_tasks.load("deadbeef0001", db_path=db)
    assert (
        running["final"] is False and running["outcome"] is None and running["phase"] == "executing"
    )
    assert ai_history.mark_interrupted_running(db_path=db) == 1
    after = ai_tasks.load("deadbeef0001", db_path=db)
    assert after["status"] == "interrupted" and after["outcome"] == "interrupted" and after["final"]


def test_legacy_sessions_are_not_tasks_and_keep_their_shape(db):
    ai_history.record_start(
        {"id": "legacy000001", "project": "/p", "provider": "codex", "prompt": "x"}, db_path=db
    )
    ai_history.record_end("legacy000001", "done", diff="d", changed=True, db_path=db)
    row = ai_history.get("legacy000001", db_path=db)
    assert row["status"] == "done" and row["changed"] is True
    for col in (
        "task_type",
        "phase",
        "outcome",
        "verification",
        "envelope",
        "budget_used",
        "parent_task_id",
    ):
        assert row[col] is None, col
    assert ai_tasks.load("legacy000001", db_path=db) is None


def test_old_database_gains_the_task_columns_without_losing_rows(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.executescript(ai_history._SCHEMA)
    con.execute(
        "INSERT INTO sessions (id, project, provider, prompt, status, started_ms) VALUES ('old1','/p','codex','x','done',1)"
    )
    con.commit()
    con.close()
    row = ai_history.get("old1", db_path=path)
    assert row["status"] == "done" and row["task_type"] is None and row["outcome"] is None


def test_phase_never_reopens_a_finished_row(db):
    ai_history.record_start(
        {"id": "fin000000001", "project": "/p", "provider": "x", "prompt": "x", "task_type": "t"},
        db_path=db,
    )
    ai_history.update_status("fin000000001", "cancelled", db_path=db)
    assert ai_history.record_task_phase("fin000000001", "committing", db_path=db) is False


# --------------------------------------------------------------------------- 进程边界
def test_task_core_imports_without_the_scientific_stack():
    """Flask 父进程侧：`ai_tasks` 在 numpy / matplotlib / pandas 一个都 import 不了的解释器里照样能 import。"""
    code = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in {'numpy', 'matplotlib', 'pandas', 'scipy', 'pymupdf', 'fitz'}:\n"
        "            raise ImportError('blocked ' + name)\n"
        "sys.meta_path.insert(0, Block())\n"
        f"sys.path.insert(0, {str(SRC)!r})\n"
        "import tavotto.engine.ai_tasks as t\n"
        "print(t.SCHEMA_VERSION)\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip() == "1"


def test_cancel_after_the_candidate_exists_still_blocks_commit(db):
    """候选已经产生、提交前检查进行中被取消：提交之前那一次复查必须挡住它（不是只在执行之后查一次）。"""

    class CancelInVerify(FakeAdapter):
        def verify(self, stage, run, candidate):
            if stage == "precommit":
                ai_tasks.cancel(run.task_id)
            return super().verify(stage, run, candidate)

    ai_tasks.register(CancelInVerify())
    try:
        ex = FakeExecutor()
        res = _run(_env(), ex, db)
    finally:
        ai_tasks.unregister("fake_task")
    assert res["outcome"] == "cancelled" and ex.committed == [] and len(ex.discarded) == 1


def test_postcommit_unknown_on_a_non_required_check_is_partial(db):
    ai_tasks.register(
        FakeAdapter(post=(("g", "goal", "verified"), ("x.extra", "preserve", "unknown")))
    )
    try:
        res = _run(_env(), FakeExecutor(), db)
    finally:
        ai_tasks.unregister("fake_task")
    assert res["outcome"] == "partial"
    assert {"constraint_id": "x.extra", "reason_code": "unknown"} in res["verification"]["unmet"]


def test_postcommit_verifier_crash_is_partial_not_completed(db):
    class Crash(FakeAdapter):
        def verify(self, stage, run, candidate):
            if stage == "postcommit":
                raise RuntimeError("post boom")
            return super().verify(stage, run, candidate)

    ai_tasks.register(Crash())
    try:
        ex = FakeExecutor()
        res = _run(_env(), ex, db)
    finally:
        ai_tasks.unregister("fake_task")
    assert len(ex.committed) == 1 and res["outcome"] == "partial"
    assert {"constraint_id": "postcommit.verifier", "reason_code": "verifier_error"} in res[
        "verification"
    ]["unmet"]


def test_cancel_once_committing_is_refused_and_the_task_is_not_cancelled(adapter, db):
    """提交进行中（另一线程的 cancel 在 commit 里到达）：回 False、任务照常 completed。"""
    seen: list[bool] = []

    class CancelInCommit(FakeExecutor):
        def commit(self, run, candidate):
            seen.append(ai_tasks.cancel(run.task_id))
            super().commit(run, candidate)

    ex = CancelInCommit()
    res = _run(_env(), ex, db)
    assert seen == [False]
    assert res["outcome"] == "completed" and len(ex.committed) == 1 and ex.discarded == []


def test_cancel_before_committing_is_accepted_and_nothing_is_committed(adapter, db):
    seen: list[bool] = []
    ex = FakeExecutor(on_execute=lambda run: seen.append(ai_tasks.cancel(run.task_id)))
    res = _run(_env(), ex, db)
    assert seen == [True]
    assert res["outcome"] == "cancelled" and ex.committed == [] and len(ex.discarded) == 1


def test_wall_budget_spent_during_commit_is_partial_and_the_commit_stands(adapter, db):
    now = [0.0]

    class SlowCommit(FakeExecutor):
        def commit(self, run, candidate):
            now[0] += 31.0
            super().commit(run, candidate)

    ex = SlowCommit()
    res = _run(_env(budget={"max_wall_s": 30}), ex, db, clock=lambda: now[0])
    assert len(ex.committed) == 1 and ex.discarded == []
    assert res["outcome"] == "partial" and res["error"] == "timeout"
    assert res["status"] == "done", "已提交的任务不落成 timeout（那会读成没提交）"
    assert {"constraint_id": "budget.max_wall_s", "reason_code": "timeout"} in res["verification"][
        "unmet"
    ]


def test_interrupted_task_clears_its_stale_phase(db):
    import sqlite3

    ai_history.record_start(
        {
            "id": "deadbeef0002",
            "project": "/proj/figs",
            "provider": "deterministic",
            "prompt": "x",
            "task_type": "fake_task",
            "phase": "executing",
            "envelope": {},
            "owner_pid": _dead_pid(),
        },
        db_path=db,
    )
    assert ai_history.mark_interrupted_running(db_path=db) == 1
    after = ai_tasks.load("deadbeef0002", db_path=db)
    assert after["phase"] is None and after["final"] is True and after["outcome"] == "interrupted"
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT phase FROM sessions WHERE id='deadbeef0002'").fetchone() == (
            None,
        )
    finally:
        con.close()
    # 老行：终态却残留 phase（修复之前写下的）——load 也不能把它当进行中的阶段报出去
    con = sqlite3.connect(db)
    try:
        con.execute("UPDATE sessions SET phase='executing' WHERE id='deadbeef0002'")
        con.commit()
    finally:
        con.close()
    assert ai_tasks.load("deadbeef0002", db_path=db)["phase"] is None


# --------------------------------------------------------------------------- 有界修正（Phase 4）
class CorrectingAdapter(FakeAdapter):
    """提交前每轮按 `rounds` 依次给出 `(检查 id, 结论, 原因)`；`correctable=False` 时适配器判不可修。"""

    def __init__(self, rounds, correctable=True):
        super().__init__()
        self.rounds, self.correctable = list(rounds), correctable
        self.offers: list[list[str]] = []

    def verify(self, stage, run, candidate):
        if stage == "precommit":
            self.verify_calls.append(stage)
            cid, verdict, reason = self.rounds.pop(0)
            return [
                ai_tasks.CheckResult(
                    cid, "L1", "precommit", "integrity", verdict, reason_code=reason
                )
            ]
        return super().verify(stage, run, candidate)

    def correction(self, run, failed):
        self.offers.append([c.id for c in failed])
        return (
            {"prompt": "fix " + ",".join(c.reason_code for c in failed)}
            if self.correctable
            else None
        )


class CorrectingExecutor(FakeExecutor):
    executor_id = "codex"
    kind = "agent"

    def __init__(self, on_correct=None):
        super().__init__()
        self.corrections: list[dict] = []
        self.on_correct = on_correct

    def correct(self, run, candidate, feedback):
        self.corrections.append(feedback)
        if self.on_correct:
            self.on_correct(run)
        return candidate


SYNTAX_FAIL = ("L1.syntax", "failed", "syntax_error")
STEMS_FAIL = ("L1.stems", "failed", "stems_changed")
OK = ("L1.syntax", "verified", None)


def _corr_env(**budget):
    return _env(
        execution_policy={"executor": "codex"},
        budget={"max_agent_runs": 2, "max_corrections": 1, **budget},
    )


@pytest.fixture
def corr():
    made = []

    def make(rounds, **kw):
        a = CorrectingAdapter(rounds, **kw)
        ai_tasks.register(a)
        made.append(a)
        return a

    yield make
    ai_tasks.unregister("fake_task")


def test_a_correctable_precommit_failure_is_fixed_once_then_committed(corr, db):
    a = corr([SYNTAX_FAIL, OK])
    ex = CorrectingExecutor()
    res = _run(_corr_env(), ex, db)
    assert res["outcome"] == "completed" and len(ex.committed) == 1
    assert [f["prompt"] for f in ex.corrections] == ["fix syntax_error"]
    assert res["budget_used"]["agent_runs"] == 2 and res["budget_used"]["corrections"] == 1
    v = res["verification"]
    assert v["attempts"] == [
        {"round": 1, "failed": [["L1.syntax", "syntax_error"]], "next": "correct"}
    ]
    # 结局只按最后一轮的提交前检查判：被修好的那一轮只作为证据留在 attempts
    assert [c["verdict"] for c in v["figure"]["checks"] if c["stage"] == "precommit"] == [
        "verified"
    ]
    assert a.verify_calls == ["precommit", "precommit", "postcommit"]


def test_the_same_failure_after_a_correction_is_not_retried(corr, db):
    corr([SYNTAX_FAIL, SYNTAX_FAIL])
    ex = CorrectingExecutor()
    res = _run(_corr_env(max_corrections=3, max_agent_runs=4), ex, db)
    assert res["outcome"] == "failed" and ex.committed == [] and len(ex.discarded) == 1
    assert len(ex.corrections) == 1, "失败集合没变 = 没有新根因，不再修第二次（0127）"
    assert {"constraint_id": "correction", "reason_code": "no_new_failure"} in res["verification"][
        "unmet"
    ]
    assert [a["next"] for a in res["verification"]["attempts"]] == ["correct", "no_new_failure"]


def test_a_different_failure_may_be_corrected_again_within_budget(corr, db):
    corr([SYNTAX_FAIL, STEMS_FAIL, OK])
    ex = CorrectingExecutor()
    res = _run(_corr_env(max_corrections=2, max_agent_runs=3), ex, db)
    assert res["outcome"] == "completed" and len(ex.corrections) == 2


def test_correction_budget_is_enforced_before_running(corr, db):
    corr([SYNTAX_FAIL, STEMS_FAIL])
    ex = CorrectingExecutor()
    res = _run(_corr_env(), ex, db)  # 修正 ≤ 1
    assert res["outcome"] == "failed" and len(ex.corrections) == 1 and ex.committed == []
    assert {"constraint_id": "budget.corrections", "reason_code": "budget_exhausted"} in res[
        "verification"
    ]["unmet"]
    assert res["budget_used"]["corrections"] == 1, "被拒的修正没执行，不记账"


def test_agent_run_budget_also_bounds_corrections(corr, db):
    corr([SYNTAX_FAIL])
    ex = CorrectingExecutor()
    res = _run(_corr_env(max_agent_runs=1), ex, db)
    assert ex.corrections == [] and res["outcome"] == "failed"
    assert {"constraint_id": "budget.agent_runs", "reason_code": "budget_exhausted"} in res[
        "verification"
    ]["unmet"]


def test_unknown_precommit_results_are_never_corrected(corr, db):
    a = corr([("L1.tree", "unknown", "candidate_over_budget")])
    ex = CorrectingExecutor()
    res = _run(_corr_env(), ex, db)
    assert ex.corrections == [] and a.offers == [], "量不出来就没有能告诉 Agent 的失败条件"
    assert {"constraint_id": "correction", "reason_code": "not_correctable"} in res["verification"][
        "unmet"
    ]


def test_adapter_refusal_means_no_correction(corr, db):
    corr([SYNTAX_FAIL], correctable=False)
    ex = CorrectingExecutor()
    res = _run(_corr_env(), ex, db)
    assert ex.corrections == [] and res["outcome"] == "failed"
    assert res["verification"]["attempts"][0]["next"] == "not_correctable"


def test_without_a_correction_budget_behaviour_is_unchanged(corr, db):
    corr([SYNTAX_FAIL])
    ex = CorrectingExecutor()
    res = _run(_env(execution_policy={"executor": "codex"}), ex, db)  # 默认 max_corrections=0
    assert ex.corrections == [] and res["outcome"] == "failed"
    unmet = res["verification"]["unmet"]
    assert unmet == [{"constraint_id": "L1.syntax", "reason_code": "syntax_error"}], unmet
    assert res["verification"]["attempts"][0]["next"] is None


def test_an_executor_that_cannot_correct_is_not_asked_to(corr, db):
    a = corr([SYNTAX_FAIL])
    ex = CorrectingExecutor()
    ex.correct = None
    res = _run(_corr_env(), ex, db)
    assert res["outcome"] == "failed" and a.offers == []


def test_cancel_during_a_correction_never_commits(corr, db):
    corr([SYNTAX_FAIL, OK])
    started, release = threading.Event(), threading.Event()

    def block(run):
        started.set()
        release.wait(5)

    ex = CorrectingExecutor(on_correct=block)
    out: list[dict] = []
    t = threading.Thread(target=lambda: out.append(_run(_corr_env(), ex, db)))
    t.start()
    assert started.wait(5)
    (task_id,) = list(ai_tasks._RUNNING)
    assert ai_history.get(task_id, db_path=db)["phase"] == "correcting"
    assert ai_tasks.cancel(task_id) is True
    release.set()
    t.join(10)
    assert out[0]["outcome"] == "cancelled" and ex.committed == [] and len(ex.discarded) == 1


def test_a_failing_correction_run_fails_the_task_without_committing(corr, db):
    corr([SYNTAX_FAIL, OK])

    def boom(run):
        raise ai_tasks.TaskError("agent_exit_nonzero", "1")

    ex = CorrectingExecutor(on_correct=boom)
    res = _run(_corr_env(), ex, db)
    assert res["outcome"] == "failed" and ex.committed == [] and len(ex.discarded) == 1
    assert res["error"] == "agent_exit_nonzero"


def test_budget_contract_bounds_corrections():
    with pytest.raises(ai_tasks.ContractError) as e:
        ai_tasks.TaskEnvelope.from_dict(_env(budget={"max_corrections": 11}))
    assert e.value.code == "contract_budget"
    env = ai_tasks.TaskEnvelope.from_dict(_env())
    assert env.budget.max_corrections == 0 and env.to_dict()["budget"]["max_corrections"] == 0
