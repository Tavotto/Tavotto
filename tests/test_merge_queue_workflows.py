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
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"

#: 模块级读进来的 workflow——少一个，下面 41 条判据全部没有主语。
_NEEDED = ("ci.yml", "codeql.yml")

# 本模块的输入是**仓库级**的 workflow 文件，而 sdist 只带 `tests` /
# `src/tavotto` / `web/src`（`[tool.hatch.build.targets.sdist].include`）。
# 从 sdist 解出来跑时 `.github/` 根本不存在，而这两行是**模块级**语句——
# 不接住就崩在收集期：pytest 报 `ERROR collecting <file>`，栈顶停在 pathlib，
# 不指向任何一条用例，整组判据一起消失（issue #269）。
#
# 「读不到」有两种成因，它们把人送去的方向相反，所以必须分开报：
#   * 整个 `.github/workflows/` 不在 → **这个环境里没有这些输入**（sdist 布局），
#     如实跳过并点名缺的是什么，别去找一个不存在的重命名；
#   * 目录在、单个文件不在 → **路径真的变了**（重命名 / 挪走），当场抛并点名
#     是哪个文件——这种情况不该被跳过糊过去。
#
# 守卫的前提由 `test_the_skip_premise_still_holds` 钉住：哪天 sdist 带上了
# `.github`，这个 skip 就是多余的，而**一个多余的 skip 会在本该跑得动的环境里
# 安静地关掉整组判据**（`tests/test_blame_ignore_revs.py` 的浅克隆 skip 是同族
# 先例：把盲点写在明处不等于补上了）。
if not WF.is_dir():
    pytest.skip(
        f"当前环境里没有 {WF.relative_to(ROOT)}/（本模块要读 {', '.join(_NEEDED)}）"
        "——本模块的判据是仓库级 workflow 契约，只在**源码检出**里有意义"
        "（sdist 只带 tests / src/tavotto / web/src）。这不是「路径变了」，"
        "别去找重命名。",
        allow_module_level=True,
    )

_RENAMED = [name for name in _NEEDED if not (WF / name).is_file()]
assert not _RENAMED, (
    f"{WF.relative_to(ROOT)}/ 在，但里面读不到 {_RENAMED}——这是**路径变了**"
    "（workflow 被重命名或挪走），去找那个新名字。"
    "「这个环境里根本没有 .github」是另一回事，由上面的 skip 守卫接住。"
)

CI = (WF / "ci.yml").read_text(encoding="utf-8")
CODEQL = (WF / "codeql.yml").read_text(encoding="utf-8")

# 判定器与 ruleset 工具：TestHeavyLaneDependencies 要拿真实的 `decide()` 与 `GATE_CONTEXTS`
# 去证明「删边之后 AND 还在」，不复述自己以为的规则。挂法与 tests/test_aggregate_gate.py 相同。
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
import aggregate_gate as AG  # noqa: E402
import merge_queue_ruleset as MQ  # noqa: E402


def _sdist_include() -> list[str]:
    """读 pyproject 的 sdist include 列表；**只认列表项，不认注释里的散文**。"""
    body = re.search(
        r"(?ms)^\[tool\.hatch\.build\.targets\.sdist\]\n(.*?)^\[",
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
    )
    assert body, "pyproject 里切不出 [tool.hatch.build.targets.sdist] 段"
    entries = re.findall(r'(?m)^\s*"([^"]+)",\s*$', body.group(1))
    assert entries, "sdist 段里一个 include 条目都读不出来——列表的写法变了？"
    return entries


def test_the_skip_premise_still_holds():
    """守卫的前提：sdist 确实带 `tests`、确实不带 `.github`。

    前提一变（比如以后把 `.github` 也打进 sdist），这里当场红——那时模块顶上的
    skip 就多余了，而多余的 skip 会在**本该跑得动**的环境里安静地关掉整组判据。
    与 `tests/test_e2e_leg_topology.py` 的同名判据同一形状（issue #269）。
    """
    include = _sdist_include()
    assert "tests" in include, "sdist 不再带 tests——本模块根本不会被解出来，这个守卫也就没有主语了"
    for shipped in (".github",):
        assert not any(e == shipped or e.startswith(shipped + "/") for e in include), (
            f"sdist 现在带上了 {shipped}——模块顶上的 skip 守卫已经多余。"
            "留着它等于在一个本该能跑的环境里安静地关掉整组判据"
        )


#: ruleset 收敛后的三个 required contexts；名字改动 = 仓库锁死，
#: 与 scripts/ci/merge_queue_ruleset.py 的 GATE_CONTEXTS 对拍。
GATES_IN_CI = ("CI fast gate", "CI integration gate")
GATE_IN_CODEQL = "CodeQL gate"

#: `desktop-shell` 的 matrix 里每个 runner → 它是不是 macOS。判据只关心这一个
#: 维度，因为 `main.rs` 的 cfg 分岔就在 `target_os = "macos"` 这一维上
#: （issue #282）。这是**枚举**不是白名单：加一个新 runner 就必须回到这里，
#: 顺便被问一句「它属于哪一类」。
_RUNNER_IS_MACOS = {
    "ubuntu-latest": False,
    "macos-latest": True,
    "windows-latest": False,
}


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


def _matrix_axes(job_block: str) -> dict[str, list[str]]:
    """读 `strategy.matrix` 的**轴**（`key: [a, b]`）；切不出来或写法认不出当场抛。

    backend-fast / backend-platforms 自 CI03a 起用轴而不是 `include`（GitHub 对
    `include` 条目不做笛卡尔积，shard 只能是轴）。`include` 形状在这里就是「认不出」。
    """
    code = _code(job_block)
    m = re.search(r"(?m)^      matrix:\n((?:        \S.*\n)+)", code)
    assert m, "job 里切不出 strategy.matrix 的轴块（是不是还写成 include 了？）"
    axes: dict[str, list[str]] = {}
    for line in m.group(1).splitlines():
        am = re.fullmatch(r"\s+([a-z_-]+): \[([^\]]*)\]", line)
        assert am, f"matrix 轴的写法认不出：{line!r}"
        axes[am.group(1)] = [v.strip().strip("\"'") for v in am.group(2).split(",")]
    assert axes, "matrix 块是空的"
    return axes


def _tiers_of(job_block: str) -> set[tuple[str, str]]:
    """一个 backend job 实际跑的 (os, python) 档：轴上有就取轴，没有就取写死的那个值。

    os 不在轴上时 `runs-on` 必须是字面量（`${{ matrix.os }}` 却没有 os 轴 = 空档）；
    python 不在轴上时 `python-version` 必须是字面量，同理。
    """
    axes = _matrix_axes(job_block)
    code = _code(job_block)
    if "os" in axes:
        oses = axes["os"]
    else:
        m = re.search(r"(?m)^    runs-on: ([\w-]+)$", code)
        assert m, "os 不在 matrix 轴上，runs-on 又不是字面量"
        oses = [m.group(1)]
    if "python" in axes:
        pys = axes["python"]
    else:
        m = re.search(r'python-version: "([\d.]+)"', code)
        assert m, "python 不在 matrix 轴上，python-version 又不是字面量"
        pys = [m.group(1)]
    return {(o, py) for o in oses for py in pys}


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

    def test_desktop_shell_lints_both_sides_of_the_macos_cfg(self):
        """`#[cfg(target_os = "macos")]` 那一支必须有 clippy 的执行位置（#282）。

        **clippy 只看得见参与编译的那一支。** 只跑一条 Linux 腿时，`main.rs`
        的应用菜单分支（`about_with_text` / `hide_with_text` / …）不进编译单元，
        它的 lint 在**任何**工作流里都没有执行位置——`desktop-tauri.yml` 的
        macOS 腿只跑 `cargo test`，不 deny warnings，编译错误抓得到、lint 抓不到。
        本机实测过这把尺子是活的：同一条 `format!("{}", "x")` 塞进 macos 块里
        clippy 退 101，塞进 not(macos) 块里退 0。

        判据钉的是**两类各要有一条腿**：只钉一侧的门禁，反方向越界时不会响。
        """
        block = _code(_job(CI, "desktop-shell"))
        assert re.search(r"(?m)^    runs-on: \$\{\{ matrix\.os \}\}$", block), (
            "desktop-shell 不再按 matrix.os 分腿——它退回单平台了，"
            "另一侧 cfg 分支的 clippy 又没有执行位置了（issue #282）"
        )
        m = re.search(r"(?m)^        os: \[([^\]]+)\]$", block)
        assert m, "desktop-shell 里读不出 strategy.matrix.os —— 缩进形状变了？"
        oses = [o.strip() for o in m.group(1).split(",")]
        unknown = [o for o in oses if o not in _RUNNER_IS_MACOS]
        assert not unknown, (
            f"desktop-shell 的 matrix 里有没见过的 runner {unknown}——"
            "先在 _RUNNER_IS_MACOS 里说清它属不属于 macOS 那一类，再加腿"
        )
        assert {_RUNNER_IS_MACOS[o] for o in oses} == {True, False}, (
            f"desktop-shell 的腿只覆盖了 {oses}——`main.rs` 里另一侧 cfg 分支"
            "在这些 runner 上不参与编译，它的 clippy 又变成一条永远不执行的"
            "判据了（issue #282）"
        )

    def test_the_rust_gates_run_on_every_desktop_shell_leg(self):
        """三条 cargo 命令不许被 `if:` 收窄到某一条腿上。

        收窄任意一条，它守的那一侧就重新变成「登记了但不执行」——而 matrix
        还在、Gate 照绿，上面那条按 runner 数腿的判据也照样过。这是同一个
        缺陷的第二个消费点。
        """
        block = _code(_job(CI, "desktop-shell"))
        steps = re.split(r"(?m)^      - ", block)[1:]
        cargo = [st for st in steps if re.search(r"(?m)^\s*run: cargo ", st)]
        assert len(cargo) == 3, f"desktop-shell 里的 cargo 步骤有 {len(cargo)} 条，预期 3 条"
        for st in cargo:
            cmd = re.search(r"(?m)^\s*run: (cargo .*)$", st).group(1)
            assert not re.search(r"(?m)^\s*if:", st), (
                f"`{cmd}` 被 `if:` 收窄到了某一条腿上——它守的那一侧就没有执行"
                "位置了（issue #282）。要按平台分岔就分在 cargo 那一侧，别关掉整步"
            )

    def test_integration_gate_covers_the_heavy_layer(self):
        assert {"package", "windows-exe-smoke", "macos-app-smoke"} <= _needs_of(
            _job(CI, "ci-integration-gate")
        )

    def test_codeql_gate_depends_on_analyze(self):
        block = _job(CODEQL, "codeql-gate")
        assert _needs_of(block) == {"analyze"}
        assert _required_of(block) == {"analyze"}

    def test_codeql_skips_the_sarif_upload_only_on_merge_group(self):
        """合并组里 SARIF 上传没有消费者，却是 `CodeQL gate` 唯一的外部依赖
        （2026-09-13 #336 三次被踢全在这一步）。PR / push 仍要上传：PR 的 diff
        告警与 main 的告警账本都靠它。判据钉在 analyze 步骤自己的 `with:` 里——
        写到别的步骤上、或把 PR 也一起关掉，这里都红。"""
        block = _code(_job(CODEQL, "analyze"))
        step = re.search(
            r"uses: github/codeql-action/analyze@v\d+\n(.*?)(?=\n      - |\Z)", block, re.S
        )
        assert step, "codeql.yml 里找不到 analyze 步骤"
        m = re.search(r"(?m)^\s+upload:\s*(.+)$", step.group(1))
        assert m, "analyze 步骤没有 upload: 输入——合并组会重新依赖 SARIF 上传"
        expr = m.group(1).strip()
        assert "github.event_name == 'merge_group' && 'never'" in expr, expr
        assert expr.endswith("|| 'always' }}"), f"非 merge_group 事件必须照常上传：{expr}"

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

    def test_backend_split_keeps_all_five_tiers(self):
        """backend-fast + backend-platforms 合起来必须逐档等于：
        Linux 3.10 / Linux 3.13 / Linux 3.14 / macOS 3.13 / Windows 3.13。
        merge_group 上五档全跑（fast 与 platforms 都在），一档都不许少。
        Linux 3.14 是 issue #33 放开上界时加的——上界那档在不在矩阵里，
        由 tests/test_support_matrix.py 对着 support-matrix 的 tested 再钉一次。

        CI03a 起 matrix 是轴（python × shard / os × shard），os 或 python 不在轴上时
        取 runs-on / python-version 的字面量——`_tiers_of` 两种形状都读，读不出当场抛。
        分片轴不改变档：每档只是拆成两片跑，五档仍逐档存在。
        """
        fast = _job(CI, "backend-fast")
        platforms = _job(CI, "backend-platforms")
        tiers = _tiers_of(fast) | _tiers_of(platforms)
        assert tiers == {
            ("ubuntu-latest", "3.10"),
            ("ubuntu-latest", "3.13"),
            ("ubuntu-latest", "3.14"),
            ("macos-latest", "3.13"),
            ("windows-latest", "3.13"),
        }, f"backend 覆盖漂了：{sorted(tiers)}"
        assert "python -m pytest" in _code(fast) and "python -m pytest" in _code(platforms)

    @pytest.mark.parametrize("job_id", ["backend-fast", "backend-platforms"])
    def test_pytest_shards_agree_between_the_matrix_and_the_command(self, job_id):
        """`--shard K/N` 的 N 与 matrix.shard 的片数必须是**同一个数**，且轴恰好是 1..N。

        每个 shard 进程只能自验「我算出的 N 片并集 == 全集」，它看不见别的 job 有没有
        跑：轴写成 `[1, 1]`、或轴是 `[1, 2]` 而命令写 `/3`，每个进程都绿，第 2 / 第 3 片
        却没人跑。这一位只有静态合同钉得住（CI03A_PYTEST_SHARDS.md「漏片兜底链」第二层）。
        """
        block = _job(CI, job_id)
        axes = _matrix_axes(block)
        assert "shard" in axes, f"{job_id} 的 matrix 没有 shard 轴"
        n = len(axes["shard"])
        assert axes["shard"] == [str(i) for i in range(1, n + 1)], (
            f"{job_id} 的 shard 轴必须恰好是 1..N，收到 {axes['shard']}"
        )
        assert n == 2, f"{job_id} 现在定的是 2 片；改片数要同时改这里与文档里的实测"
        code = _code(block)
        # `--shard=K/N` 与 `--shard-manifest=PATH` **必须是 `=` 形式**：这两个选项在
        # tests/conftest.py 里注册，pytest 预解析时把未知选项的下一个 token 当路径去找
        # conftest；`--shard-manifest PATH` 在 PATH 已存在时只加载 PATH 所在目录的 conftest，
        # tests/conftest.py 没加载，整条命令 rc 4「unrecognized arguments」。托管 runner 的
        # RUNNER_TEMP 每次都是新的，CI 自己永远不会撞上——所以这一位只能静态钉。
        m = re.search(r"python -m pytest --shard=\$\{\{ matrix\.shard \}\}/(\d+)", code)
        assert m, (
            f"{job_id} 的 pytest 命令里没有 `--shard=${{{{ matrix.shard }}}}/N`（要 `=` 形式）"
        )
        assert int(m.group(1)) == n, (
            f"{job_id}：命令里的 N={m.group(1)} 与 matrix.shard 的片数 {n} 不是同一个数"
        )
        # 证据链：manifest + junit 上传成按片命名的 artifact（只作证据，不作判定输入）
        assert "--shard-manifest=" in code and "--junitxml" in code
        assert "--shard-manifest " not in code, f"{job_id}：--shard-manifest 要写成 `=` 形式"
        assert re.search(
            r"name: pytest-" + re.escape(job_id) + r"-.*shard\$\{\{ matrix\.shard \}\}", code
        ), f"{job_id} 的分片证据 artifact 名字里没有片号——两片会互相覆盖"

    def test_unsharded_pytest_lanes_stay_unsharded(self):
        """lab（含 release 的资格，同一份 `_lab-qualification.yml`）、nightly、desktop-tauri
        的 pytest 命令**不带** `--shard`：不带时钩子是 no-op，它们跑的仍是全集。

        前提先钉住（否则「不含」是恒真）：每个文件至少有一条 `-m pytest` 的可执行行，
        release.yml 的资格确实经由 `_lab-qualification.yml`。
        """
        release = _code((WF / "release.yml").read_text(encoding="utf-8"))
        assert "uses: ./.github/workflows/_lab-qualification.yml" in release, (
            "release 的资格不再走 _lab-qualification.yml——本判据对 release 的覆盖失效"
        )
        for name in ("_lab-qualification.yml", "nightly.yml", "desktop-tauri.yml"):
            code = _code((WF / name).read_text(encoding="utf-8"))
            lines = [ln for ln in code.splitlines() if "-m pytest" in ln]
            assert lines, f"{name} 里一条 pytest 命令都没有——判据没有主语"
            assert not [ln for ln in lines if "--shard" in ln], f"{name} 的 pytest 命令带了 --shard"

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


# ============================================================ 重型档的 needs（CI01）
class TestHeavyLaneDependencies:
    """package / windows-exe-smoke / macos-app-smoke / posix-e2e 的 `needs`（CI01，2026-09-16）。

    CI00 把 ci.yml 全部 23 条 needs 边逐条分了类（docs/implementation/ci-foundation/
    evidence/dag_edge_kinds.json）：只有 frontend → plugin-candidate 一条真的消费字节；
    backend-fast / frontend → 四个重型 job 的 8 条全是 verdict-only——下游没有一步
    download-artifact，前端各自重建。而 29 个合并组里 28 个的关键路径是
    backend-fast（中位 31 分钟）→ windows-exe-smoke → integration gate。

    CI01 的两条决定，各由一条用例钉住：
      1. 删 backend-fast → 重型 的四条边（合并资格模型中位 57 → 41 分钟）；
      2. 保留 frontend → 重型 的四条边作短预筛（4 分钟、不在关键路径上，
         前端坏时省下每个候选约 45 runner 分钟）。
    第三条用例是「真实 artifact/data 依赖继续成立」的正面判据，第四条用真实判定器证明
    删边没有松掉 AND。回退 = 把四行 needs 改回 `[backend-fast, frontend]`，别的不动。
    """

    #: 四个不再等 backend-fast 的重型 job（按 job **id** 点名——显示名带矩阵后缀）。
    #: backend-platforms 也是重型档，但它本来就没有 needs，不在这一刀里。
    HEAVY_CONSUMERS = ("package", "windows-exe-smoke", "macos-app-smoke", "posix-e2e")

    #: 只产出结论、不产出任何字节的上游——重型 job 等它们只能是 verdict-only。
    VERDICT_ONLY_UPSTREAMS = frozenset(
        {"backend-fast", "backend-platforms", "invariants", "ci-fast-gate", "ci-integration-gate"}
    )

    @staticmethod
    def _job_ids() -> list[str]:
        return re.findall(r"(?m)^  ([\w-]+):\n", CI.split("\njobs:\n", 1)[1])

    @staticmethod
    def _optional_needs(block: str) -> set[str]:
        """`needs` 可以没有（快线 job 都没有）；有就必须是 `[a, b]` 的单行形状。"""
        code = _code(block)
        if not re.search(r"(?m)^    needs:", code):
            return set()
        return _needs_of(code)

    @staticmethod
    def _artifact_names(block: str, action: str) -> list[str]:
        """一个 job 里所有 `actions/<action>-artifact` 步骤的 `with.name`。

        只认 `with:` 块里的 `name:`——步骤自己的显示名 `- name: 上传…` 也叫 name，
        判据要是抓到它，「上传了什么」就会被读成「这一步叫什么」。
        """
        names: list[str] = []
        for step in re.split(r"(?m)^      - ", _code(block))[1:]:
            m = re.search(rf"(?m)^\s*uses: actions/{action}-artifact@v\d+\n", step)
            if not m:
                continue
            with_ = re.search(r"(?m)^        with:\n", step[m.end() :])
            assert with_, f"{action}-artifact 步骤没有 with: 块——形状变了？\n{step}"
            name = re.search(r"(?m)^          name: (.+)$", step[m.end() + with_.end() :])
            assert name, f"{action}-artifact 的 with: 里读不出 name:\n{step}"
            names.append(name.group(1).strip())
        return names

    def _needs_closure(self, job_id: str) -> set[str]:
        """`needs` 的传递闭包（不含自己）。"""
        seen: set[str] = set()
        todo = [job_id]
        while todo:
            for n in self._optional_needs(_job(CI, todo.pop())):
                if n not in seen:
                    seen.add(n)
                    todo.append(n)
        return seen

    def test_heavy_jobs_do_not_wait_for_the_backend_fast_verdict(self):
        """四个重型 job 的 needs 里不许再有 backend-fast（也不许有任何只产结论的上游）。

        这条边是 verdict-only 的证据就在同一个文件里：四个 job 没有一步
        download-artifact（这里顺手断言，作为「删边是安全的」的前提），前端由各自的
        `build_frontend.py` 自建。它们等 backend-fast 只是等一句「pytest 过了」，而那句话
        由 `CI fast gate` 的闭集（`test_fast_gate_needs_matches_required_closed_set`）与
        ruleset 的三个 context 一起保证，不需要在 DAG 里再串一遍。

        回退：把四行 `needs: [frontend]` 改回 `needs: [backend-fast, frontend]`，
        并把这条用例与下一条一起改掉——别的（判定器、Gate 闭集、timeout、concurrency、
        if）都不用动。
        """
        for job_id in self.HEAVY_CONSUMERS:
            block = _job(CI, job_id)
            needs = self._optional_needs(block)
            assert needs, f"{job_id} 没有 needs 了——短预筛也被删了？看下一条用例"
            waiting_for = needs & self.VERDICT_ONLY_UPSTREAMS
            assert not waiting_for, (
                f"{job_id} 又在等 {sorted(waiting_for)}——它们只产结论不产字节，"
                "等它们把关键路径拉回 backend-fast → 重型 → gate（CI00 §5.1）"
            )
            assert self._artifact_names(block, "download") == [], (
                f"{job_id} 开始 download-artifact 了——它对上游的依赖不再是 verdict-only，"
                "先按 test_heavy_consumers_that_download_an_artifact_must_need_its_producer 补数据边"
            )

    def test_heavy_jobs_keep_the_frontend_prescreen(self):
        """四个重型 job 的 needs **恰好**是 `[frontend]`——短预筛保留，别的一条不加。

        这是一个明确的成本 / 延迟决定（02 §2）：frontend 中位 4 分钟且删边后不在
        关键路径上（关键路径变成 backend-platforms (windows) 41 分钟），留着它对资格
        时长零成本，却能在前端坏掉时省下每个候选约 45 runner 分钟。谁想把它也删掉、
        或把长边加回来，都得改这条用例并在 PR 里说理由。
        """
        for job_id in self.HEAVY_CONSUMERS:
            assert self._optional_needs(_job(CI, job_id)) == {"frontend"}, (
                f"{job_id} 的 needs 不再恰好是 [frontend]——短预筛的决定被改了，"
                "先改这条用例并写明理由"
            )

    def test_heavy_consumers_that_download_an_artifact_must_need_its_producer(self):
        """每个 download-artifact 的 name，都必须由它 needs 闭包里的某个 job 上传过。

        这是「真实 artifact/data 依赖继续成立」（CIP-005）的**正面**判据：删 verdict-only
        边的同时，数据边一条都不许掉——artifact 没出来，consumer 不能假定它存在。
        现在全图只有 plugin-candidate ← frontend（codex-plugin-candidate）一条数据边；
        将来谁在重型 job 里加 download-artifact，这里会要求他同时把生产者加进 needs。
        """
        uploads: dict[str, set[str]] = {}
        downloads: list[tuple[str, str]] = []
        for job_id in self._job_ids():
            block = _job(CI, job_id)
            for name in self._artifact_names(block, "upload"):
                uploads.setdefault(name, set()).add(job_id)
            for name in self._artifact_names(block, "download"):
                downloads.append((job_id, name))
        # 非空前提：一个 download 都解析不出时，下面的循环什么都没证明
        assert ("plugin-candidate", "codex-plugin-candidate") in downloads, (
            f"全图唯一的数据边（plugin-candidate ← codex-plugin-candidate）没解析出来：{downloads}"
        )
        assert uploads.get("codex-plugin-candidate") == {"frontend"}, uploads
        for consumer, name in downloads:
            producers = uploads.get(name, set())
            assert producers, f"{consumer} download 的 `{name}` 没有任何 job 上传过"
            closure = self._needs_closure(consumer)
            assert producers & closure, (
                f"{consumer} download `{name}`，但它的生产者 {sorted(producers)} 不在其 needs "
                f"闭包 {sorted(closure)} 里——artifact 可能还没出来它就开始跑了"
            )

    def test_a_red_backend_fast_still_blocks_the_merge_even_when_every_heavy_job_is_green(self):
        """「测试失败但构建成功」的反例（phases/CI01 第 2 条）：用真实判定器跑一遍 merge_group。

        删边之后 backend-fast 红、四个重型 job 全绿是可能同时发生的（它们并行了）。
        这时候：fast gate 按 ci.yml 里真实的 `--required` 闭集判 → failure；
        integration gate 五个全 success → success；而 ruleset 的 required contexts 是
        `GATE_CONTEXTS` 三个**全部**（`build_switch_to_gates` 写进 ruleset 的正是这张表，
        GitHub 的 required_status_checks 语义是每一个 context 都要过）——一个 Gate 红，
        候选就进不了 main。这里断言的是判定器 + ruleset 变换的实际输出，不是复述规则。
        """
        fast_required = sorted(_required_of(_job(CI, "ci-fast-gate")))
        assert "backend-fast" in fast_required, "前提：backend-fast 还在 fast gate 的闭集里"
        results = {j: "success" for j in fast_required}
        results["backend-fast"] = "failure"
        fast = AG.decide("fast", "merge_group", fast_required, results)
        assert fast["status"] == "failure", fast
        assert "backend-fast: failure" in fast["problems"], fast

        heavy_required = sorted(_required_of(_job(CI, "ci-integration-gate")))
        assert "backend-fast" not in heavy_required, (
            "integration gate 的闭集本来就不含 backend-fast"
        )
        heavy = AG.decide(
            "integration",
            "merge_group",
            heavy_required,
            {j: "success" for j in heavy_required},
            require_heavy=True,
        )
        assert heavy["status"] == "success", heavy

        # ruleset 侧：switch-to-gates 写进去的 required contexts 就是三个 Gate，一个不少
        current = {
            "name": MQ.DEFAULT_RULESET_NAME,
            "target": "branch",
            "rules": [
                {"type": "merge_queue", "parameters": dict(MQ.MERGE_QUEUE_PARAMS)},
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": False,
                        "required_status_checks": [{"context": "anything-old"}],
                    },
                },
            ],
        }
        rsc = [
            r
            for r in MQ.build_switch_to_gates(current)["rules"]
            if r["type"] == "required_status_checks"
        ][0]["parameters"]["required_status_checks"]
        contexts = [c["context"] for c in rsc]
        assert contexts == MQ.GATE_CONTEXTS
        assert {"CI fast gate", "CI integration gate"} <= set(contexts)
        # 每条都是无条件的 {context} 条目：没有哪一个被标成可选 / 只在某些事件下要求
        assert all(set(c) == {"context"} for c in rsc), rsc
        # 两个 CI Gate 的名字与 ci.yml 里的 `name:` 逐字相同——ruleset 要求的正是这两个 job
        for gate in ("CI fast gate", "CI integration gate"):
            assert f"name: {gate}\n" in CI


# ============================================================ Playwright 按 project 分片（CI03c）
def _matrix_include(job_block: str) -> list[dict[str, str]]:
    """读 `strategy.matrix.include` 的条目（本仓库只写 `- { k: v, … }` 单行流式）；切不出来当场抛。

    值里不能有逗号——`scripts/ci/ci_baseline.py::_parse_matrix` 也按逗号切，两边同一个前提。
    """
    code = _code(job_block)
    m = re.search(r"(?m)^        include:\n((?:          - \{.*\}\n)+)", code)
    assert m, "job 里切不出 strategy.matrix.include 的 `- { … }` 行（形状变了？）"
    entries: list[dict[str, str]] = []
    for line in m.group(1).splitlines():
        body = re.fullmatch(r"\s+- \{(.*)\}", line)
        assert body, line
        entry: dict[str, str] = {}
        for part in body.group(1).split(","):
            k, sep, v = part.partition(":")
            assert sep, f"include 条目里这一段不是 `k: v`：{part!r}"
            entry[k.strip()] = v.strip().strip("\"'")
        entries.append(entry)
    assert entries, "include 是空的"
    return entries


def _steps(job_block: str) -> list[str]:
    """一个 job 的 `steps:` 逐条切开（去掉注释行之后）；每条以 `- ` 起头那一行的正文开始。"""
    code = _code(job_block)
    m = re.search(r"(?m)^    steps:\n", code)
    assert m, "job 里没有 steps:"
    parts = re.split(r"(?m)^      - ", code[m.end() :])[1:]
    assert parts, "steps: 下一条都切不出来"
    return parts


def _step_name(step: str) -> str:
    m = re.search(r"(?m)^\s*name: (.+)$", step)
    return m.group(1).strip() if m else ""


#: `devices['Desktop X']` → `playwright install` 里的引擎名。枚举不是白名单：配置里换了设备
#: 家族就得回到这里，顺便被问一句「那一片要装哪个浏览器」。
_DEVICE_ENGINE = {
    "Chrome": "chromium",
    "Edge": "chromium",
    "Safari": "webkit",
    "Firefox": "firefox",
}


def _playwright_projects() -> dict[str, str]:
    """`web/playwright.config.ts` 的 `projects[].name` → 引擎。只看代码行，不看注释。

    不引 TS 解析器：本仓库的 projects 块形状固定（`{ name: '…', use: { ...devices['Desktop …'] … } }`），
    切不出来当场抛，别静默回空集——空集会让「并集 == 配置」恒真。
    """
    text = (ROOT / "web" / "playwright.config.ts").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("//"))
    body = re.search(r"(?ms)^  projects: \[\n(.*?)^  \],", code)
    assert body, "playwright.config.ts 里切不出 projects: [ … ]"
    projects: dict[str, str] = {}
    for block in re.split(r"(?m)^    \{\n", body.group(1))[1:]:
        name = re.search(r"name: '([^']+)'", block)
        device = re.search(r"devices\['Desktop (\w+)'\]", block)
        assert name and device, f"project 块里读不出 name / devices：{block!r}"
        assert name.group(1) not in projects, f"project 名重复：{name.group(1)}"
        assert device.group(1) in _DEVICE_ENGINE, f"没登记的设备家族：{device.group(1)}"
        projects[name.group(1)] = _DEVICE_ENGINE[device.group(1)]
    assert len(projects) >= 2 and "webkit" in projects.values(), (
        f"配置里的 project 集是 {projects}——分片的问题不再成立，先重估本组判据"
    )
    return projects


def _project_flags(arg: str, label: str) -> set[str]:
    """`"--project=a --project=b"` → `{a, b}`：每个 token 都必须是这个形状，空串是空片。

    与 scripts/ci/playwright_shard_check.py 的 `parse_projects` **刻意不同源**（对拍要两侧独立）。
    """
    names = []
    for tok in arg.split():
        m = re.fullmatch(r"--project=(\S+)", tok)
        assert m, f"{label}：`{tok}` 不是 `--project=NAME`"
        names.append(m.group(1))
    assert names, f"{label}：空片（一个 --project= 都没有）"
    assert len(set(names)) == len(names), f"{label}：project 重复 {names}"
    return set(names)


class TestPlaywrightShards:
    """`windows-exe-smoke` 的 Playwright 按 project 分两片（CI03c，2026-09-16）。

    分片漏掉一个 project 的两个方向都**不会有任何用例红**：漏片时两片各自全绿，重叠时只是慢。
    漏片兜底三层里这里是第一层（源码层）：matrix include 的 `--project=` 集合与
    `web/playwright.config.ts` 的 project 集合比——并集相等、两两不交、无空片、`others` 是
    其余片之并、浏览器按片装的正是本片 project 要的引擎。第二层是每片 e2e 之前的自验
    （scripts/ci/playwright_shard_check.py，对着真 `--list`），第三层是 matrix 语义 + Gate 闭集。
    另外钉住：artifact 名按片唯一（upload-artifact v4 同名会失败）、必需步骤没有 `if:`、
    两条 Playwright 步都有 step 级 timeout（job 级硬杀时 step 停在 in_progress、收集步骤
    不跑、日志与 artifact 都没有——PR #373 attempt 1）。
    设计、本机实测、负例：docs/implementation/ci-foundation/CI03C_PLAYWRIGHT_SHARDS.md。
    回退 = 删 strategy 与 name、`pnpm e2e` 去掉 `${{ matrix.projects }}`、artifact 名去掉片号。
    """

    JOB = "windows-exe-smoke"

    def _entries(self) -> list[dict[str, str]]:
        entries = _matrix_include(_job(CI, self.JOB))
        assert [e.get("shard") for e in entries] == [str(i) for i in range(1, len(entries) + 1)], (
            f"{self.JOB} 的 include 条目的 shard 必须恰好是 1..N：{entries}"
        )
        assert len(entries) == 2, "现在定的是 2 片；改片数要同时改这里与文档里的实测"
        for e in entries:
            assert set(e) == {"shard", "browsers", "projects", "others"}, (
                f"include 条目的字段变了：{sorted(e)}——四个字段各有消费者（自验脚本 / e2e 命令 / 装浏览器）"
            )
        return entries

    def test_the_shards_partition_exactly_the_configured_projects(self):
        """并集 == 配置的 project 集、两两不交、无空片；`others` == 其余片之并。"""
        entries = self._entries()
        configured = set(_playwright_projects())
        sets = [_project_flags(e["projects"], f"片 {e['shard']} projects") for e in entries]
        for i, a in enumerate(sets):
            for b in sets[i + 1 :]:
                assert not (a & b), f"两片都要跑 {sorted(a & b)}——同一片内容跑两遍、判定却只算一次"
        union = set().union(*sets)
        assert union == configured, (
            f"两片的 project 并集 {sorted(union)} ≠ 配置里的 {sorted(configured)}："
            f"漏 {sorted(configured - union)} / 多 {sorted(union - configured)}"
        )
        for i, e in enumerate(entries):
            others = _project_flags(e["others"], f"片 {e['shard']} others")
            rest = set().union(*(s for j, s in enumerate(sets) if j != i))
            assert others == rest, (
                f"片 {e['shard']} 的 others {sorted(others)} 不是其余片之并 {sorted(rest)}——"
                "自验脚本会拿它去算「本片 ∪ 另一片 == 全集」"
            )

    def test_each_shard_installs_exactly_the_engines_its_projects_need(self):
        """片 1 只装 chromium，片 2 chromium + webkit——按 project 的设备家族推，不按名字猜。"""
        engine_of = _playwright_projects()
        for e in self._entries():
            need = {engine_of[p] for p in _project_flags(e["projects"], "projects")}
            installed = set(e["browsers"].split())
            assert installed == need, (
                f"片 {e['shard']} 装的是 {sorted(installed)}，它的 project 要的是 {sorted(need)}"
            )

    def test_the_e2e_command_and_the_install_take_their_arguments_from_the_matrix(self):
        """`pnpm e2e` 与 `playwright install` 都从 matrix 取参数——别处不再写死 project 名。"""
        code = _code(_job(CI, self.JOB))
        e2e = re.findall(r"(?m)^\s+pnpm e2e(.*)$", code)
        assert e2e == [" ${{ matrix.projects }}"], f"windows-exe-smoke 的 pnpm e2e 行：{e2e}"
        assert re.search(
            r"(?m)^\s+pnpm exec playwright install --with-deps \$\{\{ matrix\.browsers \}\}\s*$",
            code,
        ), "浏览器安装没有从 matrix.browsers 取"

    def test_the_self_check_runs_before_e2e_with_the_matrix_arguments(self):
        """自验步骤在 e2e 步之前、拿的是 matrix 的三个字段、且不带 `if:`。"""
        steps = _steps(_job(CI, self.JOB))
        names = [_step_name(s) for s in steps]
        check = [i for i, s in enumerate(steps) if "scripts/ci/playwright_shard_check.py" in s]
        e2e = [i for i, s in enumerate(steps) if re.search(r"(?m)^\s+pnpm e2e\b", s)]
        assert len(check) == 1 and len(e2e) == 1, (names, check, e2e)
        assert check[0] < e2e[0], "分片自验必须在 e2e 之前——它要让 job 红在跑 e2e 之前"
        step = steps[check[0]]
        for needle in (
            "--shard ${{ matrix.shard }}",
            '--projects="${{ matrix.projects }}"',
            '--others="${{ matrix.others }}"',
            "--web web",
        ):
            assert needle in step, f"自验步骤里没有 {needle}：\n{step}"
        assert not re.search(r"(?m)^\s+if:", step), "自验步骤不许带 if:"

    def test_required_steps_are_not_conditionally_skipped_on_any_shard(self):
        """两片各自完整地构建产物并都跑三条断言与冒烟①②③——带 `if:` 的只能是 artifact 上传。

        前提先钉住（否则「没有 if」恒真）：三条断言、三条冒烟、PyInstaller 都在。
        """
        steps = _steps(_job(CI, self.JOB))
        names = [_step_name(s) for s in steps]
        for prefix, n in (("断言", 3), ("冒烟", 3), ("PyInstaller", 1)):
            assert sum(nm.startswith(prefix) for nm in names) == n, (prefix, names)
        conditional = [_step_name(s) for s in steps if re.search(r"(?m)^        if:", s)]
        uploads = [_step_name(s) for s in steps if "uses: actions/upload-artifact@" in s]
        assert conditional and set(conditional) <= set(uploads), (
            f"这些步骤带了 if:（只有 artifact 上传可以）：{sorted(set(conditional) - set(uploads))}"
        )

    def test_every_artifact_of_the_sharded_job_is_named_per_shard(self):
        """upload-artifact v4 同名会失败：同一 job id 下的 artifact 名在矩阵展开后不能相同。"""
        names = TestHeavyLaneDependencies._artifact_names(_job(CI, self.JOB), "upload")
        assert len(names) >= 3, f"前提：这条腿至少三个 upload-artifact：{names}"
        for n in names:
            assert "${{ matrix.shard }}" in n, f"artifact `{n}` 的名字里没有片号——两片会撞名"
        expanded = {n.replace("${{ matrix.shard }}", k) for n in names for k in ("1", "2")}
        assert len(expanded) == 2 * len(names)

    @pytest.mark.parametrize(
        "job_id, step_minutes, job_minutes",
        [("windows-exe-smoke", 30, 60), ("posix-e2e", 20, 45)],
    )
    def test_the_playwright_step_has_a_step_level_timeout(self, job_id, step_minutes, job_minutes):
        """主语是 **step** 的 `timeout-minutes`（缩进 8），不是 job 的（缩进 4）——job 级的不动。"""
        block = _job(CI, job_id)
        pw = [s for s in _steps(block) if _step_name(s).startswith("Playwright 黄金路径")]
        assert len(pw) == 1, [_step_name(s) for s in _steps(block)]
        m = re.search(r"(?m)^        timeout-minutes: (\d+)\s*$", pw[0])
        assert m, f"{job_id} 的 Playwright 步没有 step 级 timeout-minutes"
        assert int(m.group(1)) == step_minutes, (job_id, m.group(1))
        jm = re.search(r"(?m)^    timeout-minutes: (\d+)", _code(block))
        assert jm and int(jm.group(1)) == job_minutes, f"{job_id} 的 job 级 timeout 变了"

    def test_the_job_id_and_the_gate_closed_set_are_unchanged(self):
        """job id 仍是 `windows-exe-smoke`（Gate 的 needs / --required 读的是 id）；显示名由
        `name: windows-exe-smoke (${{ matrix.shard }})` 给出——不写 name 时 include 形状的
        matrix 会把四个字段全排进显示名；scripts/ci/ci_baseline.py 按这个形状映射回 id。"""
        block = _job(CI, self.JOB)
        assert re.search(
            r"(?m)^    name: windows-exe-smoke \(\$\{\{ matrix\.shard \}\}\)\s*$", block
        )
        assert re.search(r"(?m)^    runs-on: windows-latest\s*$", _code(block))
        gate = _job(CI, "ci-integration-gate")
        assert self.JOB in _needs_of(gate) and self.JOB in _required_of(gate)

    def test_posix_e2e_stays_a_single_job_on_configured_projects(self):
        """posix-e2e 不分片（7 分钟，不在关键路径上）；它写死的 project 名必须仍在配置里。"""
        block = _job(CI, "posix-e2e")
        assert not re.search(r"(?m)^    strategy:", _code(block)), (
            "posix-e2e 分片了——先改文档里的决定"
        )
        e2e = re.findall(r"(?m)^\s+pnpm e2e(.*)$", _code(block))
        assert len(e2e) == 1, e2e
        assert _project_flags(e2e[0], "posix-e2e") <= set(_playwright_projects())


# ============================================================ package 冒烟的实例隔离（CI03b）
class TestPackageSmokeIsolation:
    """`package` job 的冒烟按实例隔离（CI03b，2026-09-16）。

    原来那两步有三件事在同一台机器上跑两个实例时会互相撞、而托管 VM 用完即毁所以从没暴露：
    venv 固定在 `/tmp/smoke`、固定端口 5199 + `sleep 8`、`&` 起的服务从不终止（04 §4）。
    这里钉的是 ci.yml 那一侧的合同，**正面形式优先**（根 AGENTS.md：否定断言会被解释它的那句
    话咬到，所以判据只看 `_code()` 剥掉注释之后的 run 脚本）：venv 与 workdir 都在
    `${{ runner.temp }}` 下、冒烟步骤调的是 `scripts/ci/package_smoke.py` 且带 `--python "$BIN/python"`
    与 `--workdir`、step 级 timeout、失败日志 artifact 名按矩阵唯一、data / config 不再由 yml 另设
    （脚本放在 workdir 下——`tests/test_package_smoke.py` 证明子进程真的拿到那个目录）、job id /
    needs / if / 四条腿 / Gate 闭集不变。脚本自己的判据（租约 + 竞争、就绪 = 我们的进程在应答、
    终止 = 进程不存在）归 `tests/test_package_smoke.py`。
    设计、本机实测、负例：docs/implementation/ci-foundation/CI03B_PACKAGE_SMOKE_ISOLATION.md。
    回退 = 恢复两步原文。
    """

    JOB = "package"
    VENV = '"${{ runner.temp }}/smoke-venv"'
    WORKDIR = '"${{ runner.temp }}/smoke-run"'

    def _steps(self) -> tuple[list[str], str, str]:
        """(全部步骤, 装 wheel 那一步, 起服务那一步)——两步都切得出来，切不出当场抛。"""
        steps = _steps(_job(CI, self.JOB))
        install = [s for s in steps if _step_name(s) == "装进干净环境并冒烟"]
        smoke = [s for s in steps if _step_name(s) == "起服务并请求首页"]
        assert len(install) == 1 and len(smoke) == 1, [_step_name(s) for s in steps]
        return steps, install[0], smoke[0]

    @staticmethod
    def _run_script(step: str) -> str:
        m = re.search(r"(?m)^        run: \|\n((?:          .*\n?)+)", step)
        assert m, f"步骤里切不出 run: | 块：\n{step}"
        return m.group(1)

    def test_the_venv_lives_under_runner_temp_and_both_steps_share_it(self):
        """venv 路径按 job 隔离，两步用同一个变量拼 `$BIN`（探 bin / Scripts 那套照旧）。"""
        _, install, smoke = self._steps()
        for step in (install, smoke):
            run = self._run_script(step)
            assert f"VENV={self.VENV}" in run, run
            assert 'BIN="$VENV/bin"; [ -d "$BIN" ] || BIN="$VENV/Scripts"' in run, run
        assert 'python -m venv "$VENV"' in self._run_script(install)
        assert '"$BIN/python" -m pip install --quiet dist/*.whl' in self._run_script(install)

    def test_the_smoke_step_runs_the_isolated_script_on_the_venv_python(self):
        """冒烟步骤：setup-python 的 `python` 跑脚本，被测解释器是 `$BIN/python`，workdir 在 runner.temp 下。"""
        _, _, smoke = self._steps()
        run = self._run_script(smoke)
        m = re.search(r"(?s)python scripts/ci/package_smoke\.py (.+?)$", run.strip())
        assert m, f"冒烟步骤没有调用 scripts/ci/package_smoke.py：\n{run}"
        args = m.group(1).replace("\\\n", " ")
        assert '--python "$BIN/python"' in args, args
        assert f"--workdir {self.WORKDIR}" in args, args
        assert (ROOT / "scripts" / "ci" / "package_smoke.py").is_file()

    def test_no_run_script_of_the_job_uses_a_shared_path_a_fixed_port_or_a_sleep(self):
        """否定形式作兜底——只看 run 脚本的代码行（注释已剥掉），三样都不许回来。"""
        steps, _, _ = self._steps()
        runs = "\n".join(
            self._run_script(s) for s in steps if re.search(r"(?m)^        run: \|", s)
        )
        assert "python -m venv" in runs, "前提：装 wheel 那一步还在"
        for needle in ("/tmp/", "5199", "sleep"):
            assert not re.search(rf"(?m)^\s*[^#]*{re.escape(needle)}", runs), (
                f"package 的 run 脚本里又出现了 `{needle}`——固定路径 / 固定端口 / 盲等三样都不许回来"
            )

    def test_the_smoke_step_has_a_step_level_timeout_and_the_job_level_is_unchanged(self):
        """主语是 **step** 的 `timeout-minutes`（缩进 8）；job 级 60 不动。"""
        _, _, smoke = self._steps()
        m = re.search(r"(?m)^        timeout-minutes: (\d+)\s*$", smoke)
        assert m and int(m.group(1)) == 5, smoke
        jm = re.search(r"(?m)^    timeout-minutes: (\d+)", _code(_job(CI, self.JOB)))
        assert jm and int(jm.group(1)) == 60

    def test_failure_logs_are_uploaded_under_a_name_unique_per_leg(self):
        """`if: failure()` 的 upload-artifact：名字带 os 与 python（四条腿互异）；路径**只收**
        `result.json` 与 `attempt-*/server.log`——正面形式列全，`attempt-*/data`（会话凭据
        `port-<P>.json`，ADR 0008）与 `config` 永远不在里面。写成 `smoke-run/**` 就把凭据传出去了。
        """
        block = _job(CI, self.JOB)
        names = TestHeavyLaneDependencies._artifact_names(block, "upload")
        assert names == ["package-smoke-logs-${{ matrix.os }}-${{ matrix.python }}"], names
        upload = [s for s in _steps(block) if "uses: actions/upload-artifact@" in s]
        assert len(upload) == 1 and re.search(r"(?m)^        if: failure\(\)\s*$", upload[0])
        m = re.search(r"(?ms)^          path: \|\n((?:^            \S.*\n)+)", upload[0])
        assert m, "path 必须是块标量（`path: |` + 逐行），不是单个 glob"
        paths = [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]
        assert paths == [
            "${{ runner.temp }}/smoke-run/result.json",
            "${{ runner.temp }}/smoke-run/attempt-*/server.log",
        ], paths
        entries = _matrix_include(block)
        expanded = {
            names[0]
            .replace("${{ matrix.os }}", e["os"])
            .replace("${{ matrix.python }}", e["python"])
            for e in entries
        }
        assert len(expanded) == len(entries) == 4, expanded

    def test_isolation_dirs_are_owned_by_the_script_not_by_the_yml(self):
        """yml 不再另设 TAVOTTO_DATA_DIR / TAVOTTO_CONFIG_DIR（脚本会覆盖，留着是假隔离）；
        脚本把它们放在 workdir 下——`package_smoke.child_env` 直接问。"""
        block = _code(_job(CI, self.JOB))
        assert "TAVOTTO_DATA_DIR" not in block and "TAVOTTO_CONFIG_DIR" not in block, (
            "package job 的 yml 又设了 TAVOTTO_*_DIR——脚本会覆盖它，隔离不是它做的"
        )
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_package_smoke_probe", ROOT / "scripts" / "ci" / "package_smoke.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        env = mod.child_env(Path("/w/attempt-1/data"), Path("/w/attempt-1/config"))
        assert env["TAVOTTO_DATA_DIR"] == str(Path("/w/attempt-1/data"))
        assert env["TAVOTTO_CONFIG_DIR"] == str(Path("/w/attempt-1/config"))

    def test_the_job_shape_and_the_gate_closed_set_are_unchanged(self):
        """job id、needs、if、四条腿、runs-on 与 Gate 闭集一个都没动。"""
        block = _job(CI, self.JOB)
        assert _needs_of(block) == {"frontend"}
        code = _code(block)
        assert "github.event_name == 'merge_group'" in code and "'full-ci'" in code
        assert re.search(r"(?m)^    runs-on: \$\{\{ matrix\.os \}\}\s*$", code)
        legs = {(e["os"], e["python"]) for e in _matrix_include(block)}
        assert legs == {
            ("ubuntu-latest", "3.13"),
            ("ubuntu-latest", "3.14"),
            ("macos-latest", "3.13"),
            ("windows-latest", "3.13"),
        }, legs
        gate = _job(CI, "ci-integration-gate")
        assert self.JOB in _needs_of(gate) and self.JOB in _required_of(gate)


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
        # ADR 0043：画布不再入库，指纹对比退休；换成「发行生成物不许进索引」
        assert "build_mcp_widget.py --check" not in block, "画布不入库了，这条 --check 会恒红"
        assert "check_generated_untracked.py" in block, "「发行生成物不许进索引」那一步掉了"
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
