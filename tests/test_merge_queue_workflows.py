"""Merge Queue 兼容层的 workflow 契约（ci.yml / codeql.yml）。

这些判据钉的都是「坏掉之后不会有任何测试红、只会在线上锁死或漏验」的形状：

* required Gate 的 workflow 掉了 merge_group 触发 → 队列候选等一个永远
  不出现的 context，90 分钟超时，谁也合不进去；
* concurrency 组把 merge_group 与 PR 归到一组、或 cancel-in-progress 写成
  全局 true → 队列候选被新 push 取消，同样白等超时；
* Gate 的 `needs` 与 `--required` 漂开 → 新上游 job 的失败 Gate 看不见；
* merge_group payload 里没有 pull_request.draft / labels——不分事件就读，
  条件会安静地算出错误分支。

与 tests/test_release_workflow_contract.py 同一条纪律：**不用 PyYAML**
（它不在 `.venv` 里，importorskip 会让整个模块静默跳过——那正是空门禁），
用只认本仓库缩进形状的字符串判据，解析不出预期形状时当场抛。
"""
from __future__ import annotations

import re
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows"
CI = (WF / "ci.yml").read_text(encoding="utf-8")
CODEQL = (WF / "codeql.yml").read_text(encoding="utf-8")

#: ruleset 收敛后的三个 required contexts；名字改动 = 仓库锁死，
#: 与 scripts/ci/merge_queue_ruleset.py 的 GATE_CONTEXTS 对拍。
GATES_IN_CI = ("CI fast gate", "CI integration gate")
GATE_IN_CODEQL = "CodeQL gate"


def _code(text: str) -> str:
    """剥掉注释行——判据只看会被执行的部分。"""
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.lstrip().startswith("#"))


def _job(text: str, job_id: str) -> str:
    """按缩进切出一个 job 块；切不出来当场抛（安静的空判据比没有更坏）。"""
    m = re.search(rf"(?m)^  {re.escape(job_id)}:\n(.*?)(?=^  [\w-]+:|\Z)",
                  text, re.S)
    assert m, f"ci/codeql 里切不出 job `{job_id}`——缩进形状变了？"
    return m.group(0)


def _needs_of(job_block: str) -> set[str]:
    m = re.search(r"(?m)^\s+needs:\s*\[([^\]]+)\]", job_block)
    assert m, "job 里读不出 needs: [...]"
    return {s.strip() for s in m.group(1).split(",")}


def _required_of(job_block: str) -> set[str]:
    m = re.search(r"--required\s+([\w,\-]+)", job_block)
    assert m, "job 里读不出 --required"
    return set(m.group(1).split(","))


# ============================================================ merge_group 触发
class TestMergeGroupTrigger:
    def test_ci_listens_to_merge_group_checks_requested(self):
        assert re.search(r"(?m)^  merge_group:\n(?:\s*#.*\n)*\s+types: \[checks_requested\]",
                         CI), "ci.yml 没有监听 merge_group.checks_requested"

    def test_codeql_listens_to_merge_group_checks_requested(self):
        assert re.search(r"(?m)^  merge_group:\n(?:\s*#.*\n)*\s+types: \[checks_requested\]",
                         CODEQL), "codeql.yml 没有监听 merge_group.checks_requested"

    def test_gate_workflows_still_listen_to_pull_request(self):
        """Gate 也要在 PR 上产出结论——PR 得先绿才能进队列。"""
        for name, text in (("ci.yml", CI), ("codeql.yml", CODEQL)):
            assert re.search(r"(?m)^  pull_request:", text), \
                f"{name} 掉了 pull_request 触发"

    def test_non_required_workflows_do_not_join_the_queue(self):
        """nightly / release / lab 不产出 required contexts，盲目接进
        merge_group 只会把深度验证和发布链拖进每一次排队。"""
        for name in ("nightly.yml", "release.yml", "lab-ci.yml",
                     "desktop-tauri.yml", "telemetry-metrics.yml",
                     "_lab-qualification.yml", "pr-conflict-domains.yml"):
            text = _code((WF / name).read_text(encoding="utf-8"))
            assert "merge_group" not in text, f"{name} 不该监听 merge_group"


# ============================================================ 并发
class TestConcurrency:
    def _groups(self):
        out = {}
        for name, text in (("ci.yml", CI), ("codeql.yml", CODEQL)):
            m = re.search(r"(?m)^concurrency:\n(?:\s*#.*\n)*\s+group: (.+)\n"
                          r"(?:\s*#.*\n)*\s+cancel-in-progress: (.+)$", text)
            assert m, f"{name} 顶层 concurrency 解析不出来"
            out[name] = (m.group(1), m.group(2))
        return out

    def test_group_distinguishes_events(self):
        """merge_group / PR / push 绝不同组：组名必须带 event_name，且用
        merge_group 的 head SHA 兜底——临时分支 SHA ≠ PR head SHA。"""
        for name, (group, _) in self._groups().items():
            assert "github.event_name" in group, f"{name} 的组名没带 event_name"
            assert "github.event.merge_group.head_sha" in group, \
                f"{name} 的组名没把 merge_group 候选彼此分开"

    def test_cancel_in_progress_only_for_pull_request(self):
        """写成全局 true 的那天：队列候选被取消、main 的唯一验证记录被取消。"""
        for name, (_, cancel) in self._groups().items():
            assert cancel.strip() == "${{ github.event_name == 'pull_request' }}", \
                f"{name} 的 cancel-in-progress 不再只对 PR 开：{cancel}"

    def test_ci_and_codeql_use_distinct_namespaces(self):
        """两个 workflow 的组名都带 github.workflow——名字不同，天然不同组。"""
        for name, (group, _) in self._groups().items():
            assert "github.workflow" in group, f"{name} 的组名没带 workflow 维度"


# ============================================================ 事件字段访问
class TestEventFieldAccess:
    def test_pull_request_fields_are_guarded_by_event_checks(self):
        """`github.event.pull_request.*` 只许出现在两种地方：
        ① 先判过 `github.event_name == 'pull_request'` 的表达式里；
        ② concurrency 组名里带 `||` 兜底的那一处。
        merge_group payload 里没有这些字段，不分事件就读，条件会安静地
        算出错误分支。"""
        for name, text in (("ci.yml", CI), ("codeql.yml", CODEQL)):
            code = _code(text)
            # 按「一段表达式」检查：if 块（>- 折叠）或单行
            for m in re.finditer(r"(?m)^(\s+)(if|group): (>-\n(?:\1  .+\n)+|.*$)",
                                 code):
                expr = m.group(3)
                if "github.event.pull_request." not in expr:
                    continue
                guarded = ("github.event_name == 'pull_request'" in expr
                           or "||" in expr)
                assert guarded, (
                    f"{name} 里这段表达式未按事件分支就读 PR 字段：\n{expr}")

    def test_no_bare_head_ref_or_label_event_usage(self):
        for name, text in (("ci.yml", CI), ("codeql.yml", CODEQL)):
            code = _code(text)
            for bad in ("github.head_ref", "github.base_ref",
                        "github.event.label", "github.event.action"):
                assert bad not in code, f"{name} 用了 {bad}——merge_group 下没有它"


# ============================================================ Gate 结构
class TestGates:
    def test_gate_jobs_exist_with_fixed_names(self):
        for gate in GATES_IN_CI:
            assert f"name: {gate}\n" in CI, f"ci.yml 里没有固定名字「{gate}」"
        assert f"name: {GATE_IN_CODEQL}\n" in CODEQL

    def test_gates_run_on_always(self):
        for job_id, text in (("ci-fast-gate", CI), ("ci-integration-gate", CI),
                             ("codeql-gate", CODEQL)):
            block = _code(_job(text, job_id))
            assert re.search(r"(?m)^\s+if:.*always\(\)", block), \
                f"{job_id} 不是 always()——上游失败时它不会跑，required check 没结论"

    def test_fast_gate_needs_matches_required_closed_set(self):
        block = _job(CI, "ci-fast-gate")
        assert _needs_of(block) == _required_of(block), \
            "fast gate 的 needs 与 --required 漂开了——新 job 的失败 Gate 看不见"

    def test_integration_gate_needs_matches_required_closed_set(self):
        block = _job(CI, "ci-integration-gate")
        assert _needs_of(block) == _required_of(block)

    def test_fast_gate_covers_the_fast_layer(self):
        assert {"invariants", "frontend", "workerd",
                "compat-smoke"} <= _needs_of(_job(CI, "ci-fast-gate"))
        assert _needs_of(_job(CI, "ci-fast-gate")) \
            & {"backend", "backend-fast"}, "fast gate 必须聚合 backend 快线"

    def test_integration_gate_covers_the_heavy_layer(self):
        assert {"package", "windows-exe-smoke",
                "macos-app-smoke"} <= _needs_of(_job(CI, "ci-integration-gate"))

    def test_codeql_gate_depends_on_analyze(self):
        block = _job(CODEQL, "codeql-gate")
        assert _needs_of(block) == {"analyze"}
        assert _required_of(block) == {"analyze"}

    def test_every_gate_needs_is_a_real_job(self):
        """needs 指向的 job 必须存在——改名后 Gate 会在 workflow 解析期炸，
        但那时已经推上去了；这里在本地就红。"""
        job_ids = set(re.findall(r"(?m)^  ([\w-]+):\n", CI.split("\njobs:\n", 1)[1]))
        for gate in ("ci-fast-gate", "ci-integration-gate"):
            for need in _needs_of(_job(CI, gate)):
                assert need in job_ids, f"{gate} 的 needs 指向不存在的 job {need}"

    def test_integration_gate_defers_only_on_plain_pull_requests(self):
        """merge_group / push 一律 --require-heavy；full-ci 走 --require-heavy
        且把 --full-ci 交给脚本复核。判定散在 Bash 里的部分只有这个三分支，
        真正的规则在 aggregate_gate.py（有自己的单测）。"""
        block = _code(_job(CI, "ci-integration-gate"))
        assert '"$GATE_EVENT" != "pull_request"' in block
        assert "--require-heavy" in block and "--allow-deferred" in block
        assert "--full-ci" in block

    def test_full_ci_label_still_triggers_the_heavy_layer(self):
        code = _code(CI)
        assert "contains(github.event.pull_request.labels.*.name, 'full-ci')" in code
        assert re.search(r"types: \[[^\]]*labeled", CI), \
            "pull_request types 里掉了 labeled——加标签不会触发新 run"

    HEAVY = ("backend-platforms", "package", "windows-exe-smoke",
             "macos-app-smoke")

    def _heavy_cond(self, job_id: str) -> str:
        """折叠块的行模式写成 ` {6,}\\S.*`（缩进全部交给 ` {6,}`、正文以 \\S
        起头）：`(?:\\s+.+\\n)+` 那种 `\\s` 与 `.` 重叠的嵌套量词是 CodeQL
        py/redos 实打实报过的（#119），恶意构造的输入能让它指数回溯。"""
        block = _code(_job(CI, job_id))
        m = re.search(r"(?m)^    if: >-\n((?: {6,}\S.*\n)+)", block)
        assert m, f"{job_id} 的 if 条件解析不出来"
        return m.group(1)

    def test_heavy_jobs_run_on_merge_group(self):
        """重型资格在 merge_group 上必须产出结论，否则队列候选白等超时。"""
        for job_id in self.HEAVY:
            cond = self._heavy_cond(job_id)
            assert "github.event_name == 'merge_group'" in cond, \
                f"{job_id} 在 merge_group 上不跑——队列候选会白等超时"
            assert "github.event_name != 'pull_request'" not in cond, \
                f"{job_id} 用了过宽的否定条件——未来事件会误入重型路径"

    def test_heavy_jobs_do_not_run_on_plain_prs_or_push(self):
        """PR 2 定版：重型资格只在 merge_group 或 full-ci PR 上跑——
        普通 PR 不再等它，push main 不再重复它。"""
        for job_id in self.HEAVY:
            cond = self._heavy_cond(job_id)
            assert "'full-ci'" in cond, f"{job_id} 掉了 full-ci 提前跑的入口"
            assert "== 'push'" not in cond, \
                f"{job_id} 还在 push main 上重复制造同一批产物"
            assert "draft" not in cond, \
                f"{job_id} 还在按草稿与否分层——那套信号已被 merge_group 取代"

    def test_fast_jobs_cover_pr_and_merge_group_but_not_push(self):
        """快线在 PR 与 merge_group 上都要跑（PR 先绿才能进队列，组合提交
        还要再验一遍）；push main 只留 landing audit。"""
        for job_id in ("invariants", "backend-fast", "frontend", "workerd",
                       "compat-smoke"):
            block = _code(_job(CI, job_id))
            m = re.search(r"(?m)^\s+if: (.+)$", block)
            assert m, f"{job_id} 没有事件条件"
            cond = m.group(1)
            assert "github.event_name == 'pull_request'" in cond and \
                "github.event_name == 'merge_group'" in cond, \
                f"{job_id} 的事件条件不对：{cond}"

    def test_backend_split_keeps_all_four_tiers(self):
        """backend-fast + backend-platforms 合起来必须与从前的四腿矩阵逐档
        相同：Linux 3.10 / Linux 3.13 / macOS 3.13 / Windows 3.13。
        merge_group 上四档全跑（fast 与 platforms 都在），一档都不许少。"""
        fast = _job(CI, "backend-fast")
        platforms = _job(CI, "backend-platforms")
        tiers = set(re.findall(r"\{ os: ([\w-]+),\s*python: \"([\d.]+)\" \}",
                               fast + platforms))
        assert tiers == {("ubuntu-latest", "3.10"), ("ubuntu-latest", "3.13"),
                         ("macos-latest", "3.13"), ("windows-latest", "3.13")}, \
            f"backend 覆盖漂了：{sorted(tiers)}"
        assert "python -m pytest" in _code(fast) and \
            "python -m pytest" in _code(platforms)

    def test_integration_gate_includes_backend_platforms(self):
        assert "backend-platforms" in _needs_of(_job(CI, "ci-integration-gate"))

    def test_main_push_runs_only_the_landing_audit(self):
        """push main 的落地审计存在、只在 push 上跑、且真的轻——不装科学栈、
        不打包、不跑冒烟。"""
        block = _code(_job(CI, "main-landing-audit"))
        assert re.search(r"(?m)^\s+if: github\.event_name == 'push'$", block)
        assert "build_mcp_widget.py --check" in block, "受管生成物一致性掉了"
        assert "pytest" in block, "结构契约那一步掉了"
        for heavy_marker in ("pyinstaller", "smoke_app.py", "python -m build",
                             "matplotlib"):
            assert heavy_marker not in block, \
                f"landing audit 里混进了重活：{heavy_marker}"

    def test_landing_audit_structural_tests_exist(self):
        """audit 里点名的测试文件必须真实存在——点一个不存在的文件，pytest
        当场红，main 每次落地都红。"""
        block = _code(_job(CI, "main-landing-audit"))
        root = WF.parents[1]
        for rel in re.findall(r"tests/[\w/]+\.py", block):
            assert (root / rel).is_file(), f"landing audit 引用的 {rel} 不存在"
