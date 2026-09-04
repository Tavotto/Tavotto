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
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _job(text: str, job_id: str) -> str:
    """按缩进切出一个 job 块；切不出来当场抛（安静的空判据比没有更坏）。"""
    m = re.search(rf"(?m)^  {re.escape(job_id)}:\n(.*?)(?=^  [\w-]+:|\Z)", text, re.S)
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


def _if_of(job_block: str) -> str:
    """读单行 `if:`；折叠块（`if: >-`）读不出来当场抛——重型那几档才那么写。"""
    m = re.search(r"(?m)^    if: (.+)$", job_block)
    assert m, "job 里读不出单行 if:"
    return m.group(1).strip()


#: fast 档 job 的**唯一**合法条件。写死在这里是有意的：它与重型档的
#: `if: >-`（merge_group 或 full-ci 标签）是两种东西，而两者在 Gate 的
#: needs 里长得一模一样。
FAST_LANE_CONDITION = "github.event_name == 'pull_request' || github.event_name == 'merge_group'"


# ============================================================ merge_group 触发
class TestMergeGroupTrigger:
    def test_ci_listens_to_merge_group_checks_requested(self):
        assert re.search(r"(?m)^  merge_group:\n(?:\s*#.*\n)*\s+types: \[checks_requested\]", CI), (
            "ci.yml 没有监听 merge_group.checks_requested"
        )

    def test_codeql_listens_to_merge_group_checks_requested(self):
        assert re.search(
            r"(?m)^  merge_group:\n(?:\s*#.*\n)*\s+types: \[checks_requested\]", CODEQL
        ), "codeql.yml 没有监听 merge_group.checks_requested"

    def test_gate_workflows_still_listen_to_pull_request(self):
        """Gate 也要在 PR 上产出结论——PR 得先绿才能进队列。"""
        for name, text in (("ci.yml", CI), ("codeql.yml", CODEQL)):
            assert re.search(r"(?m)^  pull_request:", text), f"{name} 掉了 pull_request 触发"

    def test_non_required_workflows_do_not_join_the_queue(self):
        """nightly / release / lab 不产出 required contexts，盲目接进
        merge_group 只会把深度验证和发布链拖进每一次排队。"""
        for name in (
            "nightly.yml",
            "release.yml",
            "lab-ci.yml",
            "desktop-tauri.yml",
            "telemetry-metrics.yml",
            "_lab-qualification.yml",
            "pr-conflict-domains.yml",
        ):
            text = _code((WF / name).read_text(encoding="utf-8"))
            assert "merge_group" not in text, f"{name} 不该监听 merge_group"


# ============================================================ 并发
class TestConcurrency:
    def _groups(self):
        out = {}
        for name, text in (("ci.yml", CI), ("codeql.yml", CODEQL)):
            m = re.search(
                r"(?m)^concurrency:\n(?:\s*#.*\n)*\s+group: (.+)\n"
                r"(?:\s*#.*\n)*\s+cancel-in-progress: (.+)$",
                text,
            )
            assert m, f"{name} 顶层 concurrency 解析不出来"
            out[name] = (m.group(1), m.group(2))
        return out

    def test_group_distinguishes_events(self):
        """merge_group / PR / push 绝不同组：组名必须带 event_name，且用
        merge_group 的 head SHA 兜底——临时分支 SHA ≠ PR head SHA。"""
        for name, (group, _) in self._groups().items():
            assert "github.event_name" in group, f"{name} 的组名没带 event_name"
            assert "github.event.merge_group.head_sha" in group, (
                f"{name} 的组名没把 merge_group 候选彼此分开"
            )

    def test_cancel_in_progress_only_for_pull_request(self):
        """写成全局 true 的那天：队列候选被取消、main 的唯一验证记录被取消。"""
        for name, (_, cancel) in self._groups().items():
            assert cancel.strip() == "${{ github.event_name == 'pull_request' }}", (
                f"{name} 的 cancel-in-progress 不再只对 PR 开：{cancel}"
            )

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
            for m in re.finditer(r"(?m)^(\s+)(if|group): (>-\n(?:\1  .+\n)+|.*$)", code):
                expr = m.group(3)
                if "github.event.pull_request." not in expr:
                    continue
                guarded = "github.event_name == 'pull_request'" in expr or "||" in expr
                assert guarded, f"{name} 里这段表达式未按事件分支就读 PR 字段：\n{expr}"

    def test_no_bare_head_ref_or_label_event_usage(self):
        for name, text in (("ci.yml", CI), ("codeql.yml", CODEQL)):
            code = _code(text)
            for bad in (
                "github.head_ref",
                "github.base_ref",
                "github.event.label",
                "github.event.action",
            ):
                assert bad not in code, f"{name} 用了 {bad}——merge_group 下没有它"


# ============================================================ Gate 结构
class TestGates:
    def test_gate_jobs_exist_with_fixed_names(self):
        for gate in GATES_IN_CI:
            assert f"name: {gate}\n" in CI, f"ci.yml 里没有固定名字「{gate}」"
        assert f"name: {GATE_IN_CODEQL}\n" in CODEQL

    def test_gates_run_on_always(self):
        for job_id, text in (
            ("ci-fast-gate", CI),
            ("ci-integration-gate", CI),
            ("codeql-gate", CODEQL),
        ):
            block = _code(_job(text, job_id))
            assert re.search(r"(?m)^\s+if:.*always\(\)", block), (
                f"{job_id} 不是 always()——上游失败时它不会跑，required check 没结论"
            )

    def test_gates_run_the_trusted_copy_of_the_verdict(self):
        """switch-to-gates 之后 Gate 是唯一的 required check，判定逻辑必须
        来自**默认分支**而不是被判定的那个 revision（#119 评审 P1：PR 里塞
        一个 scripts/ci/json.py，`import json` 时 SystemExit(0)，全红的
        needs 就被判成绿）。`python3 -I` 是第二道：不挂脚本目录进 sys.path、
        无视 PYTHONPATH。bootstrap 回退只许在默认分支缺这份脚本时走。"""
        for job_id, text in (
            ("ci-fast-gate", CI),
            ("ci-integration-gate", CI),
            ("codeql-gate", CODEQL),
        ):
            block = _code(_job(text, job_id))
            assert "?ref=${{ github.event.repository.default_branch }}" in block, (
                f"{job_id} 不再从默认分支取判定器"
            )
            assert 'python3 -I "$RUNNER_TEMP/trusted-gate/aggregate_gate.py"' in block, (
                f"{job_id} 没有用 -I 执行可信副本"
            )
            assert "python3 scripts/ci/aggregate_gate.py" not in block, (
                f"{job_id} 还在执行 checkout 里（PR 可改写）的判定器"
            )

    def test_fast_gate_needs_matches_required_closed_set(self):
        block = _job(CI, "ci-fast-gate")
        assert _needs_of(block) == _required_of(block), (
            "fast gate 的 needs 与 --required 漂开了——新 job 的失败 Gate 看不见"
        )

    def test_integration_gate_needs_matches_required_closed_set(self):
        block = _job(CI, "ci-integration-gate")
        assert _needs_of(block) == _required_of(block)

    def test_fast_gate_covers_the_fast_layer(self):
        assert {
            "python-lint",
            "invariants",
            "frontend",
            "workerd",
            "desktop-shell",
            "compat-smoke",
        } <= _needs_of(_job(CI, "ci-fast-gate"))
        assert _needs_of(_job(CI, "ci-fast-gate")) & {"backend", "backend-fast"}, (
            "fast gate 必须聚合 backend 快线"
        )

    def test_every_fast_lane_job_actually_runs_on_a_plain_pull_request(self):
        """fast 档的每个 job 都必须在**普通 PR** 上产出结论。

        「接进了 Gate 的闭集」与「在 PR 上真的跑」是两件事，而它们在
        `needs:` 那一行长得一模一样。重型那几档正是接在 integration gate 里、
        普通 PR 上整体 skipped——`--allow-deferred` 判 deferred，Gate 照样绿。
        把一个 fast 档的 job 悄悄改成同样的条件，Gate 依旧全绿，而它守的东西
        合并前一次都不验：**issue #275 就是这个形状**（`src-tauri` 的 Rust 判据
        只登记在发行链上，改了壳的 PR 一路绿）。

        这里逐个比死条件，而不是「含 pull_request 就算过」：重型档的折叠条件
        里也含 `pull_request`，只是后面还跟着 `full-ci` 标签。
        """
        for job_id in sorted(_needs_of(_job(CI, "ci-fast-gate"))):
            assert _if_of(_job(CI, job_id)) == FAST_LANE_CONDITION, (
                f"fast 档的 `{job_id}` 不是在每个 PR 上都跑——它在 Gate 里，"
                "但普通 PR 上没有结论，等于一道登记了却不执行的门禁"
            )

    def test_the_desktop_shell_rust_gates_have_an_execution_slot(self):
        """`src-tauri` 的 fmt / clippy / 单测必须在 PR 档有执行位置（#275）。

        改造前它们只跑在 `desktop-tauri.yml`（打 tag / workflow_dispatch）的
        **build 矩阵的 macOS 那条腿**上：登记在发行链里，合并前从不执行。
        而 `main.rs` 里其中一条判据守的是「关不掉的窗口」，它只可能写成
        **行为**判据（源码里搜 token 的写法会放行 `if false { … }`）——
        行为判据只有真的跑起来才算数。

        `mkdir -p dist/Tavotto` 那一步同样是判据的一部分：`tauri.conf.json` 的
        `bundle.resources` 指向它，缺了 tauri-build 直接失败。它也是这一格
        **不必**挂在完整打包之后的原因。
        """
        block = _code(_job(CI, "desktop-shell"))
        assert "mkdir -p dist/Tavotto" in block, "少了那个空 sidecar 目录，tauri-build 起不来"
        for cmd in ("cargo fmt --check", "cargo clippy --all-targets -- -D warnings", "cargo test"):
            assert re.search(
                rf"(?m)^      - working-directory: src-tauri\n\s+run: {re.escape(cmd)}$",
                block,
            ), f"desktop-shell 里没有在 src-tauri 下跑 `{cmd}`"
        assert "desktop-shell" in _needs_of(_job(CI, "ci-fast-gate")), (
            "跑了但没接进 Gate：它红了没人看得见"
        )

    def test_integration_gate_covers_the_heavy_layer(self):
        assert {"package", "windows-exe-smoke", "macos-app-smoke"} <= _needs_of(
            _job(CI, "ci-integration-gate")
        )

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
        assert re.search(r"types: \[[^\]]*labeled", CI), (
            "pull_request types 里掉了 labeled——加标签不会触发新 run"
        )

    HEAVY = ("backend-platforms", "package", "windows-exe-smoke", "macos-app-smoke", "posix-e2e")

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
            assert "github.event_name == 'merge_group'" in cond, (
                f"{job_id} 在 merge_group 上不跑——队列候选会白等超时"
            )
            assert "github.event_name != 'pull_request'" not in cond, (
                f"{job_id} 用了过宽的否定条件——未来事件会误入重型路径"
            )

    def test_heavy_jobs_do_not_run_on_plain_prs_or_push(self):
        """PR 2 定版：重型资格只在 merge_group 或 full-ci PR 上跑——
        普通 PR 不再等它，push main 不再重复它。"""
        for job_id in self.HEAVY:
            cond = self._heavy_cond(job_id)
            assert "'full-ci'" in cond, f"{job_id} 掉了 full-ci 提前跑的入口"
            assert "== 'push'" not in cond, f"{job_id} 还在 push main 上重复制造同一批产物"
            assert "draft" not in cond, (
                f"{job_id} 还在按草稿与否分层——那套信号已被 merge_group 取代"
            )

    def test_fast_jobs_cover_pr_and_merge_group_but_not_push(self):
        """快线在 PR 与 merge_group 上都要跑（PR 先绿才能进队列，组合提交
        还要再验一遍）；push main 只留 landing audit。"""
        for job_id in (
            "python-lint",
            "invariants",
            "backend-fast",
            "frontend",
            "workerd",
            "compat-smoke",
        ):
            block = _code(_job(CI, job_id))
            m = re.search(r"(?m)^\s+if: (.+)$", block)
            assert m, f"{job_id} 没有事件条件"
            cond = m.group(1)
            assert (
                "github.event_name == 'pull_request'" in cond
                and "github.event_name == 'merge_group'" in cond
            ), f"{job_id} 的事件条件不对：{cond}"

    def test_backend_split_keeps_all_four_tiers(self):
        """backend-fast + backend-platforms 合起来必须与从前的四腿矩阵逐档
        相同：Linux 3.10 / Linux 3.13 / macOS 3.13 / Windows 3.13。
        merge_group 上四档全跑（fast 与 platforms 都在），一档都不许少。"""
        fast = _job(CI, "backend-fast")
        platforms = _job(CI, "backend-platforms")
        tiers = set(re.findall(r"\{ os: ([\w-]+),\s*python: \"([\d.]+)\" \}", fast + platforms))
        assert tiers == {
            ("ubuntu-latest", "3.10"),
            ("ubuntu-latest", "3.13"),
            ("macos-latest", "3.13"),
            ("windows-latest", "3.13"),
        }, f"backend 覆盖漂了：{sorted(tiers)}"
        assert "python -m pytest" in _code(fast) and "python -m pytest" in _code(platforms)

    def test_integration_gate_includes_backend_platforms(self):
        assert "backend-platforms" in _needs_of(_job(CI, "ci-integration-gate"))

    def test_python_lint_failure_cannot_be_invisible_to_the_gate(self):
        """Ruff 红了 fast gate 必须跟着红。

        这条与上面两条合起来才是完整的：`needs` 里有它（gate 看得见）、
        `--required` 里有它（闭集校验数得到它）、事件条件与快线一致
        （PR 与 merge_group 都真的跑）。缺任何一环，python-lint 就是一格
        「看起来在检查、实际挡不住任何东西」的空门禁。
        """
        block = _job(CI, "ci-fast-gate")
        assert "python-lint" in _needs_of(block)
        assert "python-lint" in _required_of(block)


class TestPythonLint:
    """Ruff 那一格的形状。它的价值全在「便宜且真的跑」，两头都要钉住。"""

    def test_the_job_exists_with_a_name_that_says_what_broke(self):
        block = _job(CI, "python-lint")
        assert "name: Python quality (Ruff)" in block, (
            "红灯上得看得出是 lint 挂了，而不是一个叫 checks2 的东西"
        )

    def test_ruff_version_is_read_from_pyproject_not_hardcoded(self):
        """本地与 CI 的 ruff 版本一旦漂开，「本地绿、CI 红」变成常态，
        而那是让人不再信任 lint 门禁最快的方式。所以 workflow 里**不许**
        出现版本字面量——它必须从 pyproject 的 dev extra 里读。"""
        block = _code(_job(CI, "python-lint"))
        assert "optional-dependencies" in block and "tomllib" in block, (
            "python-lint 不再从 pyproject 取 ruff 版本"
        )
        assert not re.search(r"(?m)pip install\s+[\"']?ruff[=><~]", block), (
            "workflow 里抄了一份 ruff 版本字面量——它会和 pyproject 漂开"
        )

    def test_pyproject_declares_exactly_one_ruff_constraint(self):
        """workflow 里那段提取逻辑要求 dev extra 里恰好一条 ruff 约束；
        这里在本地就把那个前提钉住，而不是等 CI 上 SystemExit。

        **不用 tomllib 解析**：它是 3.11+ 才进标准库的，而本仓库承诺的下界是
        3.10（backend-fast 有一条 Linux 3.10 腿，这条用例第一次跑就死在那）。
        与本模块开头「不用 PyYAML」同一条纪律：解析器不在场时，判据要么整个
        红、要么被 importorskip 静默跳过——后者正是空门禁。
        workflow 里那段可以用 tomllib，因为 python-lint 明确钉了 3.13。
        """
        text = (WF.parents[1] / "pyproject.toml").read_text(encoding="utf-8")

        m = re.search(r"(?m)^dev = \[(.+?)\]", text, re.S)
        assert m, "pyproject 里读不出 dev extra 的形状——解析不出预期形状就当场抛"
        got = re.findall(r'"(ruff[^"]*)"', m.group(1))
        assert len(got) == 1, f"dev extra 里的 ruff 约束应当恰好一条：{got}"

        m = re.search(r"(?m)^dependencies = \[(.*?)\]", text, re.S)
        assert m, "pyproject 里读不出运行时 dependencies 的形状"
        assert "ruff" not in m.group(1), (
            "ruff 混进了运行时依赖——普通用户不该因为装 Tavotto 拿到 lint 工具"
        )

    def test_the_job_stays_cheap(self):
        """这一格存在的理由就是**十几秒回来**。一旦有人往里加科学栈、
        前端构建或 `pip install -e .`，它就退化成又一个慢检查，
        「先跑 Ruff 再跑 pytest」的习惯也就没人守了。"""
        block = _code(_job(CI, "python-lint"))
        for heavy in (
            "matplotlib",
            "numpy",
            "pnpm",
            "cargo",
            "pytest",
            "pip install -e",
            "runtime_pins",
        ):
            assert heavy not in block, f"python-lint 里混进了重活：{heavy}"

    def test_rule_selection_lives_in_pyproject_only(self):
        """命令行上再写一份 --select/--ignore，本地跑的就不是 CI 跑的那一套。"""
        block = _code(_job(CI, "python-lint"))
        assert re.search(r"(?m)^\s+run: ruff check .*\.$", block), "读不出 ruff check 那一步"
        for flag in (
            "--select",
            "--ignore",
            "--extend-select",
            "--fix",
            "--line-length",
            "--config",
        ):
            assert flag not in block, f"python-lint 在命令行上覆盖了规则集：{flag}"

    def test_formatter_is_also_gated(self):
        """`ruff format` 的迁移只有配上 --check 才算落地。

        少了这一步，仓库会**慢慢漂回**未格式化状态：谁本地没跑 format 就提交，
        没有任何东西会说话，直到下一个人跑一次 `ruff format .` 撞出几百行与他
        的改动无关的 diff。这正是「格式化过一次」与「保持被格式化」的区别。
        """
        block = _code(_job(CI, "python-lint"))
        assert re.search(r"(?m)^\s+run: ruff format --check \.$", block), (
            "python-lint 里没有 `ruff format --check .`"
        )

    def test_lint_and_format_report_independently(self):
        """format 那一步要有 `if: always()`。

        没有它时 lint 先红就看不到格式问题：开发者修完 lint 重新 push，才发现
        还有一堆格式要改——一次 CI 往返只换回一半信息。
        """
        block = _code(_job(CI, "python-lint"))
        i = block.index("- name: Ruff format --check")
        assert "if: always()" in block[i:], "format 那一步没有 always()——lint 先红就看不到它了"

    def test_format_and_lint_exclusions_stay_in_step(self):
        """三处「代码即内容」的目录必须**同时**出现在 lint 的 per-file-ignores
        与 formatter 的 exclude 里，且 lint 侧豁免的确实是 I001。

        漏掉一处的表现很别扭：`ruff check` 放过而 `ruff format --check` 报红
        （或反过来），而两条门禁说的是同一件事——那些目录里的排版不归我们管。
        """
        text = (WF.parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        # 用**行首锚定**的正则切段落。按字面量 split 会切错：表名在解释性注释里
        # 也出现过，于是两次都在同一张表里找，怎么改都绿。
        heads = {m.group(1): m.start() for m in re.finditer(r"(?m)^\[(tool\.ruff[-.\w]*)\]$", text)}
        for need in ("tool.ruff.lint.per-file-ignores", "tool.ruff.format"):
            assert need in heads, f"pyproject 里切不出 [{need}] 这一节"
        starts = sorted(heads.values())

        def _section(name: str) -> str:
            i = heads[name]
            after = [s for s in starts if s > i]
            return text[i : after[0]] if after else text[i:]

        lint = _code(_section("tool.ruff.lint.per-file-ignores"))
        fmt = _code(_section("tool.ruff.format"))
        # **只看真正的条目，不看散文**：上一版用 `d in section` 在原文里找，
        # 匹配到的是注释里的 "examples/**"，把整条豁免删掉判据照样绿。
        lint_rules = dict(re.findall(r'(?m)^"([^"]+)"\s*=\s*\[([^\]]*)\]', lint))
        fmt_globs = set(re.findall(r'(?m)^\s+"([^"]+)",', fmt))
        assert lint_rules, "per-file-ignores 里一条条目都没解析出来——形状变了？"
        assert fmt_globs, "formatter exclude 里一条条目都没解析出来——形状变了？"

        for d in ("examples/", "web/src/playground/examples/", "tests/compat/cases/"):
            covering = [g for g in lint_rules if g.startswith(d)]
            assert covering, f"lint 的 per-file-ignores 里没有覆盖 {d} 的条目"
            assert all("I001" in lint_rules[g] for g in covering), (
                f"{d} 在表里，但豁免的规则里没有 I001"
            )
            assert any(g.startswith(d) for g in fmt_globs), (
                f"formatter 的 exclude 里没有覆盖 {d} 的条目：{sorted(fmt_globs)}"
            )
        assert "*.md" in fmt_globs, (
            "formatter 的 exclude 里掉了 *.md——ruff format 会去重排文档里的 "
            "```python 代码块，而 ruff check 根本不把 .md 当 Python"
        )

    def test_docstring_code_formatting_stays_off(self):
        """显式关着。开了它，docstring 里的代码片段会在某次 ruff 升版后触发
        第二轮全仓迁移，而那应该是一个单独评估过的决定。"""
        text = (WF.parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        assert re.search(r"(?m)^docstring-code-format = false$", text), (
            "pyproject 里没有显式的 docstring-code-format = false"
        )

    def test_blame_ignore_revs_existence_is_gated_in_ci(self):
        """`.git-blame-ignore-revs` 的存在性必须在 **CI 里**真的执行一次。

        `tests/test_blame_ignore_revs.py` 那条存在性判据在浅克隆上 skip，而 CI 的
        `actions/checkout` 默认 `fetch-depth: 1`——也就是说它**在 CI 里从没执行
        过**，一个不存在的 40 位 SHA 能通过全部门禁。补法是 workflow 里按 SHA 做
        定向 fetch。这条判据盯着那一步别被删掉，也盯着它的「一条都没解析出来」
        护栏还在（没有那个护栏，文件被清空之后它就是个永远绿的空循环）。
        """
        block = _code(_job(CI, "python-lint"))
        # **要求它是循环的输入，不是随便出现在哪**：上一版只写
        # `".git-blame-ignore-revs" in block`，而那个串在报错文案里也有——
        # 把循环的输入换成 `echo`（读不到任何 SHA）判据照样绿。
        assert re.search(r"done < <\(grep .*\.git-blame-ignore-revs\)", block), (
            "python-lint 里那一步没有把 .git-blame-ignore-revs 当成循环的输入"
        )
        assert re.search(r"git fetch .*--depth=1 origin \"\$sha\"", block), (
            "没有按 SHA 定向 fetch——浅克隆上就查不出 SHA 存不存在"
        )
        assert re.search(r'git cat-file -e "\$\{sha\}\^\{commit\}"', block), (
            "fetch 之后没有确认它是一个 commit"
        )
        assert re.search(r'if \[ "\$n" -eq 0 \]; then', block), (
            "少了「一条都没解析出来就红」的护栏——文件清空后这一步会变成空循环"
        )

    def test_ci_never_rewrites_the_tree(self):
        """CI 只检查不修改：`--fix` 在门禁里意味着「它替你把红的改绿了」。"""
        block = _code(_job(CI, "python-lint"))
        assert "--fix" not in block
        assert not re.search(r"(?m)^\s+run: ruff format \.$", block), (
            "CI 在写回格式化结果，而不是检查"
        )


class TestLandingAudit:
    def test_main_push_runs_only_the_landing_audit(self):
        """push main 的落地审计存在、只在 push 上跑、且真的轻——不装科学栈、
        不打包、不跑冒烟。"""
        block = _code(_job(CI, "main-landing-audit"))
        assert re.search(r"(?m)^\s+if: github\.event_name == 'push'$", block)
        assert "build_mcp_widget.py --check" in block, "受管生成物一致性掉了"
        assert "pytest" in block, "结构契约那一步掉了"
        for heavy_marker in ("pyinstaller", "smoke_app.py", "python -m build", "matplotlib"):
            assert heavy_marker not in block, f"landing audit 里混进了重活：{heavy_marker}"

    def test_landing_audit_structural_tests_exist(self):
        """audit 里点名的测试文件必须真实存在——点一个不存在的文件，pytest
        当场红，main 每次落地都红。"""
        block = _code(_job(CI, "main-landing-audit"))
        root = WF.parents[1]
        for rel in re.findall(r"tests/[\w/]+\.py", block):
            assert (root / rel).is_file(), f"landing audit 引用的 {rel} 不存在"
