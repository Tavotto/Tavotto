"""CI 基线采集器（scripts/ci/ci_baseline.py）的看护。

它算出来的数字会直接进 docs/implementation/ci-foundation/CI_BASELINE.*，然后被
CI01–CI04 拿去决定砍哪条边、分几片。数字算错的两个方向都贵：把 runner 排队算成
DAG 等待会去扩容量、把 DAG 等待算成排队会去删不该删的边。所以这里钉的是**口径**
（04_BUILD_TEST_CACHE.md §6 的四类时间），用真实 API 样例的裁剪版
（tests/fixtures/ci_baseline/，取自 run 34970490865 / 34762723238 / 34977366199）：

* 分页：两页首尾相接的 `{total_count, jobs}` 要摊平，总数对不上要红；
* attempt：`filter=all` 会把两次 attempt 的 job 一起回，必须按 run_attempt 过滤，
  被抄进新 attempt 的旧结果要标 carried_over 而不是算成本次的时长；
* 四类时间：dependency_wait 按 needs 的 completed_at 算，runner_wait 按
  created→started 算，两者不许互相冒充；
* 负例：空集合 / 缺 completed_at / 错 SHA / 没分类的边 / 空 evidence 目录一律非零。

**分解历史 run 用的 workflow 是那些 run 真正执行时的那份**：
`tests/fixtures/ci_baseline/ci_8b95256c.yml`（`git show 8b95256c:.github/workflows/ci.yml`
逐字节），不是 HEAD 的 `.github/workflows/ci.yml`。dependency_wait / 关键路径都是从
`needs` 边算出来的，而 CI01 起 HEAD 的 ci.yml 已经删掉了 backend-fast → 重型 的四条边——
拿新 DAG 去分解旧 run，2120s 的 DAG 等待会被算成 256s，那不是测量，是把模型冒充成事实
（「夹具默认跟着产品版本走」的那一族）。HEAD 的 ci.yml 只在明确要问「现在的 workflow」
的用例里读（`live_workflow`）。

纯标准库，不联网（`gh` 只在 fetch-* 子命令里被调用，这里不碰）。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI_DIR = ROOT / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import ci_baseline as CB  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "ci_baseline"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
#: 夹具 run 执行时的 ci.yml（8b95256c）——分解它们必须用这一份，见模块 docstring。
CI_YML_AT_BASELINE = FIXTURES / "ci_8b95256c.yml"

pytestmark = pytest.mark.skipif(
    not FIXTURES.is_dir() or not CI_YML.is_file(),
    reason="本模块要读 tests/fixtures/ci_baseline/ 与 .github/workflows/ci.yml（sdist 里没有）",
)


def _ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def workflow() -> dict[str, dict]:
    """夹具 run（34970490865 / 34762723238 / 34977366199）执行时的 workflow。"""
    return CB.extract_workflow_jobs(CI_YML_AT_BASELINE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def live_workflow() -> dict[str, dict]:
    """HEAD 的 ci.yml——只给明确要问「现在的 workflow 长什么样」的用例。"""
    return CB.extract_workflow_jobs(CI_YML.read_text(encoding="utf-8"))


def _load(run_id: int, attempt: int | None = None):
    run = json.loads((FIXTURES / f"run_{run_id}.json").read_text(encoding="utf-8"))
    pages = CB.load_pages((FIXTURES / f"jobs_{run_id}.raw.json").read_text(encoding="utf-8"))
    return run, CB.jobs_from_pages(pages, attempt=attempt)


# ---------------------------------------------------------------- 分页


def test_load_pages_reads_concatenated_objects_and_merged_arrays():
    pages = CB.load_pages('{"a": 1}\n{"a": 2}\n')
    assert [p["a"] for p in pages] == [1, 2]
    pages = CB.load_pages('[{"a": 1}, {"a": 2}]')
    assert [p["a"] for p in pages] == [1, 2]


@pytest.mark.parametrize("text", ["", "   \n", '{"a": 1}\n{not json'])
def test_load_pages_rejects_empty_or_broken_output(text):
    with pytest.raises(CB.BaselineError):
        CB.load_pages(text)


def test_jobs_from_pages_flattens_two_pages_and_checks_total_count():
    text = (FIXTURES / "jobs_34970490865.raw.json").read_text(encoding="utf-8")
    pages = CB.load_pages(text)
    assert len(pages) == 2, "样例刻意切成两页，少了一页分页判据就没有主语"
    jobs = CB.jobs_from_pages(pages)
    assert len(jobs) == pages[0]["total_count"] == 8


def test_jobs_from_pages_rejects_a_missing_page():
    text = (FIXTURES / "jobs_34970490865.raw.json").read_text(encoding="utf-8")
    only_first = CB.load_pages(text)[:1]
    with pytest.raises(CB.BaselineError, match="分页没取全"):
        CB.jobs_from_pages(only_first)


def test_jobs_from_pages_rejects_an_empty_set():
    with pytest.raises(CB.BaselineError, match="为空"):
        CB.jobs_from_pages([{"total_count": 0, "jobs": []}])
    _, jobs = _load(34762723238)
    with pytest.raises(CB.BaselineError, match="为空"):
        CB.jobs_from_pages([{"total_count": len(jobs), "jobs": jobs}], attempt=3)


def test_jobs_from_pages_filters_by_attempt():
    _, all_jobs = _load(34762723238)
    assert {j["run_attempt"] for j in all_jobs} == {1, 2}, "filter=all 会把两次 attempt 一起回"
    _, second = _load(34762723238, attempt=2)
    assert {j["run_attempt"] for j in second} == {2}
    assert len(second) == len(all_jobs) // 2


# ---------------------------------------------------------------- 名字与 workflow


def test_display_names_map_back_to_workflow_job_ids(workflow):
    assert CB.display_to_job_id("backend-fast (ubuntu-latest, 3.10)", workflow) == "backend-fast"
    assert CB.display_to_job_id("package", workflow) == "package"  # skipped 时 matrix 没展开
    assert CB.display_to_job_id("Python quality (Ruff)", workflow) == "python-lint"
    assert CB.display_to_job_id("CI fast gate", workflow) == "ci-fast-gate"
    with pytest.raises(CB.BaselineError, match="对不上"):
        CB.display_to_job_id("backend (ubuntu-latest)", workflow)


def test_the_real_ci_yml_yields_the_two_gates_and_their_closed_sets(live_workflow):
    """解析器对 HEAD 的 ci.yml 仍然读得出两个 Gate 与它们的闭集（解析器活着的证据）。

    重型 job 的 needs 形状**不在这里钉**：那是 CI01 的决定，由
    tests/test_merge_queue_workflows.py::TestHeavyLaneDependencies 看住。
    """
    fast = live_workflow["ci-fast-gate"]
    heavy = live_workflow["ci-integration-gate"]
    assert fast["name"] == "CI fast gate" and heavy["name"] == "CI integration gate"
    assert "backend-fast" in fast["needs"] and "backend-fast" not in heavy["needs"]
    assert live_workflow["backend-fast"]["timeout_minutes"] == 60  # 2026-09-25 从 40 抬到 60 止血（#636）
    # CI03a 起 matrix 是轴（python × shard），解析器回的是 {轴: 值}，不再是 include 列表
    assert live_workflow["backend-fast"]["matrix"] == {
        "python": ["3.10", "3.13", "3.14"],
        "shard": ["1", "2"],
    }
    assert live_workflow["backend-platforms"]["matrix"] == {
        "os": ["macos-latest", "windows-latest"],
        "shard": ["1", "2"],
    }


def test_sharded_display_names_map_back_to_the_job_id(live_workflow):
    """分片后 API 的显示名是 `backend-fast (3.10, 1)` / `backend-platforms (windows-latest, 2)`
    ——矩阵的值按轴顺序排进括号，os 不在轴上就不出现。仍要映射回 job id，否则下一轮
    基线采集会把这两个 job 的时间算丢。"""
    assert CB.display_to_job_id("backend-fast (3.10, 1)", live_workflow) == "backend-fast"
    assert CB.display_to_job_id("backend-fast (3.14, 2)", live_workflow) == "backend-fast"
    assert CB.display_to_job_id("backend-platforms (windows-latest, 2)", live_workflow) == (
        "backend-platforms"
    )
    assert CB.display_to_job_id("backend-fast", live_workflow) == "backend-fast"  # 整格 skip


def test_expression_display_names_map_back_to_the_job_id(live_workflow):
    """CI03c 起 windows-exe-smoke 的 `name:` 是 `windows-exe-smoke (${{ matrix.shard }})`
    （include 形状的 matrix 不加 name 会把四个字段全排进显示名）。API 的显示名是
    `windows-exe-smoke (1)` / `(2)`；整格 skip 时表达式展开成空、显示成 `windows-exe-smoke ()`；
    三种都要映射回 job id，否则下一轮基线采集会把这条腿的时间算丢。别的名字照样拒。"""
    assert live_workflow["windows-exe-smoke"]["name"] == "windows-exe-smoke (${{ matrix.shard }})"
    for shown in (
        "windows-exe-smoke (1)",
        "windows-exe-smoke (2)",
        "windows-exe-smoke ()",
        "windows-exe-smoke",
    ):
        assert CB.display_to_job_id(shown, live_workflow) == "windows-exe-smoke", shown
    with pytest.raises(CB.BaselineError):
        CB.display_to_job_id("windows-exe-smoke-shard1", live_workflow)
    with pytest.raises(CB.BaselineError):
        CB.display_to_job_id("windows-exe-smoke (1) (2)", live_workflow)


def test_every_named_step_in_the_live_workflow_has_an_execution_category(live_workflow):
    """`execution` 是按 step 名字分类相加的；一个新步骤名对不上任何规则就落进 `other`，
    那一段秒数从 test / build / install / artifact 的账上消失、却不报错。CI03a 的
    「分片证据」（upload-artifact）在 CI05 分解 after run 时就是这样静默漏到 `other` 里的。
    主语是 HEAD 的 ci.yml 里**每一个带 `name:` 的步骤**：全部要落进一个真实类别。
    没有 `name:` 的步骤 API 显示成 `Run <uses>` / `Run <run 首行>`，按同一张表判。"""
    unclassified = []
    for jid, info in live_workflow.items():
        for step in info["steps"]:
            shown = step.get("name") or "Run " + (step.get("uses") or step.get("run") or "")
            if CB.classify_step(shown) == "other":
                unclassified.append((jid, shown))
    assert unclassified == [], unclassified
    # 表本身没有被放宽成「什么都算 artifact」：一个陌生名字仍是 other
    assert CB.classify_step("某个没登记过的步骤") == "other"


#: `cache-seed`（push main 的缓存种子）三个步骤名 → 类别。它们是「让消费者的准备步骤真跑一次」，
#: 账记在消费者同一类下：pnpm install → install；cargo 编译 / runtime 构建 → build（它们的产出是
#: target/ 与 build/runtime-cache，不是结论）。**枚举**：种子加一步就要回到这里登记一次。
_CACHE_SEED_STEPS = {
    "种 pnpm store（pnpm install 真跑一次）": "install",
    "让消费者那组 cargo 命令真跑一次（种 target/）": "build",
    "种 CPython 归档（构建内置渲染 runtime 真跑一次）": "build",
}


def test_cache_seed_steps_are_booked_under_install_and_build(live_workflow):
    """主语是 HEAD 的 ci.yml 里 `cache-seed` **带名字的每一步**：与上面的枚举键集合相等（少一步 =
    种子形状变了、多一步 = 没登记），且各自分到枚举说的那一类——不是「不是 other 就行」：分错类
    （比如 cargo 那一步落进 test）时 build 的账少一段、test 多一段，同样没有红灯。"""
    named = {s["name"] for s in live_workflow["cache-seed"]["steps"] if s.get("name")}
    assert set(_CACHE_SEED_STEPS) <= named, sorted(set(_CACHE_SEED_STEPS) - named)
    # 其余带名字的步骤（缓存 CPython / 装 Tauri / 摆 sidecar）与消费者同名，走消费者那几条规则
    for extra in sorted(named - set(_CACHE_SEED_STEPS)):
        assert CB.classify_step(extra) in {"setup", "install", "build"}, (
            extra,
            CB.classify_step(extra),
        )
    for name, cat in _CACHE_SEED_STEPS.items():
        assert CB.classify_step(name) == cat, (name, CB.classify_step(name))


def test_the_snapshot_is_the_workflow_the_fixture_runs_executed_under():
    """快照必须是 8b95256c 那份、且带着 backend-fast → 重型 的边。

    下面 2120s 的 dependency_wait、`backend-fast → windows-exe-smoke → gate` 的关键路径
    都以这四条边**存在**为前提；CI01 把它们从 HEAD 删掉之后，这个前提只在快照里成立。
    快照被人「顺手同步到最新」的那天这里红——同步了它，上面那些数字就全变成模型。
    """
    snapshot = CB.extract_workflow_jobs(CI_YML_AT_BASELINE.read_text(encoding="utf-8"))
    for heavy in ("package", "windows-exe-smoke", "macos-app-smoke", "posix-e2e"):
        assert set(snapshot[heavy]["needs"]) == {"backend-fast", "frontend"}, heavy
    # 与 HEAD 的差别正是 CI01 那一刀：HEAD 上四条边已经没了
    live = CB.extract_workflow_jobs(CI_YML.read_text(encoding="utf-8"))
    assert all("backend-fast" not in live[h]["needs"] for h in ("package", "windows-exe-smoke"))


def test_extract_rejects_a_block_needs_that_the_parser_cannot_read():
    text = "jobs:\n  a:\n    runs-on: x\n  b:\n    needs:\n      - a\n    runs-on: x\n"
    with pytest.raises(CB.BaselineError, match="needs"):
        CB.extract_workflow_jobs(text)
    with pytest.raises(CB.BaselineError, match="jobs"):
        CB.extract_workflow_jobs("name: x\non: push\n")


# ---------------------------------------------------------------- 四类时间


def test_dependency_wait_and_runner_wait_are_different_axes(workflow):
    run, jobs = _load(34970490865)
    report = CB.analyze_run(run, jobs, workflow)
    by = {d["name"]: d for d in report["jobs"]}
    win = by["windows-exe-smoke"]
    # needs 里最晚的是 backend-fast 3.10（13:18:19），run 建于 12:42:59 → 2120s
    assert win["dependency_wait"] == 2120
    # 它自己 13:18:20 建、13:18:22 起 → 领 runner 只等了 2s
    assert win["runner_wait"] == 2
    assert win["job_seconds"] == 1481
    assert win["execution"]["test"] >= 1124, "Playwright 那一步 1124s 必须落在 test 类"
    bf = by["backend-fast (ubuntu-latest, 3.10)"]
    assert bf["dependency_wait"] == 0 and bf["runner_wait"] == 2 and bf["job_seconds"] == 2117
    assert bf["execution"]["test"] == 2096 and bf["execution"]["install"] == 16


def test_feedback_qualification_and_critical_path(workflow):
    run, jobs = _load(34970490865)
    report = CB.analyze_run(run, jobs, workflow)
    assert report["feedback_seconds"] == 2129
    assert report["qualification_seconds"] == 3615
    assert [n["job_id"] for n in report["critical_path"]] == [
        "backend-fast",
        "windows-exe-smoke",
        "ci-integration-gate",
    ]
    assert report["job_states"] == {
        "executed": 7,
        "skipped": 1,
        "never_started": 0,
        "carried_over": 0,
    }
    assert report["runner_minutes_by_label"]["windows-latest"] == pytest.approx(
        (2507 + 1481) / 60, abs=0.01
    )


def test_qualification_never_uses_updated_at(workflow):
    run, jobs = _load(34970490865)
    run = dict(run, updated_at="2030-01-01T00:00:00Z")
    report = CB.analyze_run(run, jobs, workflow)
    assert report["qualification_seconds"] == 3615


def test_model_moves_heavy_jobs_to_start_after_frontend(workflow):
    run, jobs = _load(34970490865)
    report = CB.analyze_run(run, jobs, workflow)
    m = report["model_without_backend_fast_edges"]
    assert m["observed_seconds"] == 3615
    # 去掉 backend-fast 边后 windows-exe-smoke 从 frontend 完成（256s）起算：
    # 256 + gap 1 + runner_wait 2 + 1481 = 1740；此时最晚的是 backend-platforms
    # (windows) 1 + 2 + 2507 = 2510，再加 gate 自己的 gap 0 + runner_wait 3 + 8 → 2521
    assert m["modelled_seconds"] == 2510 + 0 + 3 + 8
    assert m["delta_seconds"] < 0


# ---------------------------------------------------------------- attempt 与取消


def test_second_attempt_marks_copied_jobs_as_carried_over(workflow):
    run, jobs = _load(34762723238, attempt=2)
    report = CB.analyze_run(run, jobs, workflow)
    states = {d["name"]: d["state"] for d in report["jobs"]}
    assert states["windows-exe-smoke"] == "executed"
    assert states["frontend"] == "carried_over"
    assert states["backend-fast (ubuntu-latest, 3.10)"] == "carried_over"
    assert report["feedback_seconds"] == "carried_over", "fast gate 没重跑，不能报一个数"
    # 从 run_started_at（15:37:09）到 integration gate 完成（16:01:16）
    assert report["qualification_seconds"] == 1447
    assert [n["job_id"] for n in report["critical_path"]] == [
        "windows-exe-smoke",
        "ci-integration-gate",
    ]


def test_never_started_jobs_do_not_get_an_execution_time(workflow):
    run, jobs = _load(34977366199)
    report = CB.analyze_run(run, jobs, workflow)
    by = {d["name"]: d for d in report["jobs"]}
    cla = by["Contributor licence (CLA)"]
    assert cla["state"] == "never_started" and cla["job_seconds"] is None
    assert cla["queued_until_cancel_seconds"] == 1874
    inv = by["invariants"]
    assert inv["state"] == "executed" and inv["runner_wait"] == 1681
    assert inv["died_at_step"]["name"] == "结构性不变式"
    assert by["macos-app-smoke"]["state"] == "never_started"


# ---------------------------------------------------------------- 负例：形状不对就红


def test_a_job_with_a_foreign_sha_is_refused(workflow):
    run, jobs = _load(34970490865)
    jobs[0] = dict(jobs[0], head_sha="0" * 40)
    with pytest.raises(CB.BaselineError, match="head_sha"):
        CB.analyze_run(run, jobs, workflow)


def test_a_completed_job_without_completed_at_is_refused(workflow):
    run, jobs = _load(34970490865)
    idx = next(i for i, j in enumerate(jobs) if j["name"] == "windows-exe-smoke")
    jobs[idx] = dict(jobs[idx], completed_at=None)
    # 判的是「缺失」这一条，不是碰巧被「completed 早于 started」那条兜住
    with pytest.raises(CB.BaselineError, match="completed_at：时刻缺失"):
        CB.analyze_run(run, jobs, workflow)


def test_every_edge_must_be_classified(workflow):
    with pytest.raises(CB.BaselineError, match="没有分类"):
        CB.build_dag(workflow, {})
    kinds = {
        f"{n}->{j}": {"kind": "verdict-only", "evidence": "x"}
        for j, i in workflow.items()
        for n in i["needs"]
    }
    dag = CB.build_dag(workflow, kinds)
    assert all(e["kind"] == "verdict-only" for e in dag["edges"])
    kinds["frontend->plugin-candidate"] = {"kind": "made-up", "evidence": "x"}
    with pytest.raises(CB.BaselineError, match="不合法"):
        CB.build_dag(workflow, kinds)


def test_analyze_cli_refuses_an_empty_evidence_dir(tmp_path):
    (tmp_path / "runs").mkdir()
    (tmp_path / "jobs").mkdir()
    rc = CB.main(
        [
            "analyze",
            "--workflow",
            str(CI_YML),
            "--evidence",
            str(tmp_path),
            "--out",
            str(tmp_path / "out.json"),
        ]
    )
    assert rc == 2
    assert not (tmp_path / "out.json").exists(), "解析失败不许留下一份看起来成功的空报告"


def test_analyze_cli_writes_a_report_for_the_fixture(tmp_path):
    (tmp_path / "runs").mkdir()
    (tmp_path / "jobs").mkdir()
    for name in ("run_34970490865.json", "jobs_34970490865.raw.json"):
        dst = "runs" if name.startswith("run_") else "jobs"
        (tmp_path / dst / name).write_bytes((FIXTURES / name).read_bytes())
    out = tmp_path / "out.json"
    rc = CB.main(
        [
            "analyze",
            "--workflow",
            str(CI_YML_AT_BASELINE),
            "--evidence",
            str(tmp_path),
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["run_count"] == 1
    assert report["runs"][0]["qualification_seconds"] == 3615
