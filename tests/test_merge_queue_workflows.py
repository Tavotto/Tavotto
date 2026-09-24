"""Merge Queue 兼容层的 workflow 契约（ci.yml / codeql.yml）。

这些判据钉的都是「坏掉之后不会有任何测试红、只会在线上锁死或漏验」的形状：

* required Gate 的 workflow 掉了 merge_group 触发 → 队列候选等一个永远
  不出现的 context，90 分钟超时，谁也合不进去；
* concurrency 组把 merge_group 与 PR 归到一组、或 cancel-in-progress 写成
  全局 true → 队列候选被新 push 取消，同样白等超时；
* Gate 的 `needs` 与 `--required` 漂开 → 新上游 job 的失败 Gate 看不见；
* merge_group payload 里没有 pull_request.draft / labels——不分事件就读，
  条件会安静地算出错误分支；
* 一个监听 pull_request / merge_group 的 workflow 把某个 job 派到 self-hosted
  （`TestRunnerTrustZones`）→ 不可信代码跑在常驻的实验室真机上，而 Gate 全绿；
* 某个 PR 级 workflow 掉了 `on.pull_request.branches: [main]`（`TestPullRequestBaseFilter`）
  → 叠栈 PR 每次 push 又起整套 CI，合并队列里的候选被挤到 90 分钟超时——而 PR 上
  什么都不红，只是慢。

与 tests/test_release_workflow_contract.py 同一条纪律：**不用 PyYAML**
（它不在 `.venv` 里，importorskip 会让整个模块静默跳过——那正是空门禁），
用只认本仓库缩进形状的字符串判据，解析不出预期形状时当场抛。
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
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

# GitHub 表达式的极小求值器（tests/support/gh_expr.py）：TestPullRequestEventTypes 用它把
# ci.yml 里真实的 if / GATE_FULL_CI / concurrency 对着合成的事件上下文算成真值表。
from support import gh_expr as G  # noqa: E402


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


def _lab_step(code: str, name_prefix: str) -> str:
    """按 `- name: <前缀>` 切出 lab 文件里的一个 step 块（到下一个 `- name:` 为止）；
    切不出来当场抛。"""
    m = re.search(
        rf"(?m)^      - name: {re.escape(name_prefix)}.*?\n(.*?)(?=^      - name: |\Z)", code, re.S
    )
    assert m, f"_lab-qualification.yml 里切不出 step `{name_prefix}`——名字或缩进变了？"
    return m.group(0)


def _lab_step_script(name_prefix: str) -> str:
    """把 lab 文件里一个 step 的 `run: |` 脚本按原文抽出来（去掉 10 格缩进），给真跑用。"""
    raw = (WF / "_lab-qualification.yml").read_text(encoding="utf-8")
    m = re.search(
        rf"(?m)^      - name: {re.escape(name_prefix)}.*?\n.*?^        run: \|\n((?:          .*\n|\n)+?)(?=^      - name: |^      #|\Z)",
        raw,
        re.S,
    )
    assert m, f"_lab-qualification.yml 里切不出 step `{name_prefix}` 的 run 脚本"
    return (
        "\n".join(ln[10:] if ln.startswith(" " * 10) else ln for ln in m.group(1).splitlines())
        + "\n"
    )


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


def _condition_of(job_block: str) -> str | None:
    """job 级条件的全文：单行 `if:` 原样，折叠 `if: >-` 把各行并成一行；没有 `if:` 回 None
    （没有条件 = 每个事件都跑，调用方要把它当成「也在 push 上跑」，不能当成空串放过）。"""
    code = _code(job_block)
    folded = re.search(r"(?m)^    if: >-\n((?: {6,}\S.*\n)+)", code)
    if folded:
        return " ".join(ln.strip() for ln in folded.group(1).splitlines())
    single = re.search(r"(?m)^    if: (.+)$", code)
    return single.group(1).strip() if single else None


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
            "release-publish.yml",
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
        """`github.head_ref` / `github.base_ref` 一处都不许有（merge_group 下没有它们）。

        `github.event.label` / `github.event.action` 也是只在 `pull_request` 事件里才有的
        字段——merge_group 下读到的是 null，`== 'unlabeled'` 安静地算成 false。它们**只许**
        出现在一处：`ci-integration-gate` 的 `GATE_FULL_CI`（CI01 §4 ①，摘掉 full-ci 的那个
        run 要按 full-ci 判），而且那段表达式必须先按 `github.event_name == 'pull_request'`
        分支。这是枚举不是白名单：再多一处用它们，就回到这里说清楚为什么。
        """
        for name, text in (("ci.yml", CI), ("codeql.yml", CODEQL)):
            code = _code(text)
            for bad in ("github.head_ref", "github.base_ref"):
                assert bad not in code, f"{name} 用了 {bad}——merge_group 下没有它"

        codeql_code = _code(CODEQL)
        for field in ("github.event.label", "github.event.action"):
            assert field not in codeql_code, f"codeql.yml 用了 {field}——merge_group 下没有它"

        ci_code = _code(CI)
        gate_env = _gate_full_ci_expression()
        allowed_lines = {ln for ln in ci_code.splitlines() if "GATE_FULL_CI:" in ln}
        assert len(allowed_lines) == 1, allowed_lines
        for field in ("github.event.label", "github.event.action"):
            where = [ln for ln in ci_code.splitlines() if field in ln]
            assert where == list(allowed_lines), (
                f"ci.yml 里 {field} 只许出现在 ci-integration-gate 的 GATE_FULL_CI 那一行：\n"
                + "\n".join(where)
            )
            assert field in gate_env, (
                f"GATE_FULL_CI 里读不到 {field}——摘掉 full-ci 的 run 又会判成 deferred"
            )
        assert gate_env.lstrip("${ ").startswith("github.event_name == 'pull_request' &&"), (
            f"GATE_FULL_CI 没有先按事件分支：{gate_env}"
        )


def _gate_full_ci_expression() -> str:
    """`ci-integration-gate` 里 `GATE_FULL_CI:` 的整段 `${{ … }}`（单行；读不出当场抛）。"""
    block = _code(_job(CI, "ci-integration-gate"))
    m = re.search(r"(?m)^          GATE_FULL_CI: (.+)$", block)
    assert m, "ci-integration-gate 里读不出 GATE_FULL_CI 那一行"
    return m.group(1).strip()


def _folded_if(job_id: str) -> str:
    """重型档的 `if: >-` 折叠块正文（行模式 ` {6,}\\S.*`，理由见 TestGates._heavy_cond）。"""
    block = _code(_job(CI, job_id))
    m = re.search(r"(?m)^    if: >-\n((?: {6,}\S.*\n)+)", block)
    assert m, f"{job_id} 的 if 条件解析不出来"
    return m.group(1)


def _pull_request_block(text: str) -> str:
    """`on.pull_request:` 之下缩进四格的行（注释已剥）；`on:` 里没有这个键当场抛。

    值为空（`pull_request:` 后面什么都没写 = 全默认）时回空串——那是合法形状，
    交给调用方按「没写」处理，不在这里抛。
    """
    lines = _top_level_block(text, "on").splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.rstrip() == "  pull_request:")
    except StopIteration:
        raise AssertionError("`on:` 块里没有 `pull_request:` 键") from None
    body: list[str] = []
    for ln in lines[start + 1 :]:
        if ln.strip() and not ln.startswith("    "):
            break
        body.append(ln)
    return "\n".join(body)


def _pull_request_filter(text: str, key: str) -> list[str] | None:
    """`on.pull_request.<key>` 的行内列表（保留顺序与重复——集合化交给判据自己做）。

    键不在回 None；**None ≠ 空列表**：前者是「没写 = GitHub 的默认」（`types` 默认三个、
    `branches` 默认不过滤），后者是写了一个空列表。只认 `key: [a, b]` 一种写法——多行
    `- x` 的形状本仓库不用，认不出就抛，不猜。
    """
    block = _pull_request_block(text)
    hits = re.findall(rf"(?m)^    {re.escape(key)}: \[([^\]]*)\]$", block)
    assert len(hits) <= 1, f"on.pull_request 里 `{key}:` 出现了 {len(hits)} 次"
    if not hits:
        assert not re.search(rf"(?m)^    {re.escape(key)}:", block), (
            f"on.pull_request.{key} 不是 `{key}: [a, b]` 的行内写法（多行 `- x`？）——这里认不出"
        )
        return None
    return [t.strip().strip("\"'") for t in hits[0].split(",") if t.strip()]


def _pull_request_types() -> list[str]:
    """ci.yml 的 `on.pull_request.types`；没写当场抛（本仓库靠显式闭集，不靠默认三个）。"""
    types = _pull_request_filter(CI, "types")
    assert types is not None, "ci.yml 的 on.pull_request 里读不出 types: [...]"
    return types


# ============================================================ PR 事件类型与标签事件（CI01 §4 ① ③）
class TestPullRequestEventTypes:
    """`on.pull_request.types` 与「标签事件 → 哪条线跑 / Gate 用哪一档」的真值表。

    这里不用子串判「表达式里写了什么」，而是把 ci.yml 里真实的 `if:` / `GATE_FULL_CI` /
    `concurrency` 对着合成的 `github` 上下文**算一遍**（tests/support/gh_expr.py），再把
    算出来的档位交给真实的 `aggregate_gate.decide()`——判的是事件表的**结论**，不是措辞。
    """

    #: 六个 type 的闭集，与 ci.yml 里 `types:` 旁边那段注释逐条对应（每个为什么在）。
    #: 少一个 = 那类事件从此没有 run；多一个 = 又多一种会重跑整条快线并取消同 PR 运行中
    #: run 的事件（`edited` / `assigned` / `review_requested` 都会这样）。`edited` 同时也是
    #: 「改 base」的事件——它不在这里，所以叠栈 PR retarget 到 main 本身不产生 run，
    #: 要等下一次 push（`TestPullRequestBaseFilter` 的 docstring 写了顺序纪律）。
    EXPECTED_TYPES = frozenset(
        {
            "opened",  # PR 出现
            "synchronize",  # 每次 push——资格的主入口
            "reopened",  # 关了再开，head SHA 上可能已没有结论
            "ready_for_review",  # 将来 T1/T2 分层的接入点；今天只是多一个 run（02 §3）
            "labeled",  # `full-ci` 的入口
            "unlabeled",  # 摘掉 `full-ci` 要让同 SHA 上的重型结论正确失效（§4 ①）
        }
    )

    HEAVY = ("backend-platforms", "package", "windows-exe-smoke", "macos-app-smoke", "posix-e2e")
    FAST = ("python-lint", "invariants", "backend-fast", "frontend", "workerd", "compat-smoke")

    def test_pull_request_types_are_exactly_the_six_we_rely_on(self):
        """集合相等，正面列全：少一个多一个都红。

        `ready_for_review` 是这里最容易被「反正草稿也跑同一套」的理由删掉的一个——
        删掉之后今天没有任何行为变化，等到做 T1/T2 分层那天才是「作者点 Ready 之后
        重活永远不跑」的洞，而那天没有别的测试会红（CI01 §4 ③）。
        """
        raw = _pull_request_types()
        assert len(raw) == len(set(raw)), f"types 里有重复：{raw}"
        assert set(raw) == self.EXPECTED_TYPES, (
            f"on.pull_request.types 不是预期的闭集：\n  多了 {sorted(set(raw) - self.EXPECTED_TYPES)}"
            f"\n  少了 {sorted(self.EXPECTED_TYPES - set(raw))}"
        )

    # ---- 合成的 github 上下文 ----
    @staticmethod
    def _pr(action: str, labels_after: tuple[str, ...], label: str | None = None) -> dict:
        """`pull_request` 事件：`labels` 是**事件发生之后**的状态（unlabeled 时已不含被摘的那个）；
        `label` 只在 labeled / unlabeled 的 payload 里有（github/docs 的 webhook 数据
        `src/webhooks/data/fpt/pull_request.json`：两种 action 各有 `label` object）。"""
        event: dict = {
            "action": action,
            "pull_request": {"number": 381, "labels": [{"name": n} for n in labels_after]},
        }
        if label is not None:
            event["label"] = {"name": label}
        return {
            "event_name": "pull_request",
            "workflow": "CI",
            "ref": "refs/pull/381/merge",
            "event": event,
        }

    @staticmethod
    def _merge_group() -> dict:
        # merge_group payload 里没有 pull_request / label / action——读它们得到 null
        return {
            "event_name": "merge_group",
            "workflow": "CI",
            "ref": "refs/heads/gh-readonly-queue/main/pr-381-abc",
            "event": {"merge_group": {"head_sha": "abc123"}},
        }

    @staticmethod
    def _push_main() -> dict:
        return {"event_name": "push", "workflow": "CI", "ref": "refs/heads/main", "event": {}}

    # ---- 把 ci.yml 里真实的表达式算出来 ----
    def _fast_runs(self, github: dict) -> bool:
        conds = {_if_of(_code(_job(CI, j))) for j in self.FAST}
        assert conds == {FAST_LANE_CONDITION}, conds
        return G.truthy(G.evaluate(FAST_LANE_CONDITION, github))

    def _heavy_runs(self, github: dict) -> bool:
        verdicts = {j: G.truthy(G.evaluate(_folded_if(j), github)) for j in self.HEAVY}
        assert len(set(verdicts.values())) == 1, f"五个重型 job 的条件算出了不同结果：{verdicts}"
        return next(iter(verdicts.values()))

    @staticmethod
    def _gate_step_script() -> str:
        """`ci-integration-gate` 里「聚合判定」那一步的 `run: |` 原文——要执行的就是它，不复刻。"""
        block = _code(_job(CI, "ci-integration-gate"))
        m = re.search(
            r"(?ms)^      - name: 聚合判定\n.*?^        run: \|\n((?:          .*\n)+)", block
        )
        assert m, "ci-integration-gate 里读不出「聚合判定」的 run: | 块"
        return "\n".join(ln[10:] for ln in m.group(1).splitlines()) + "\n"

    def _gate_verdict(self, github: dict, heavy_results: dict[str, str], tmp_path: Path) -> dict:
        """把 ci-integration-gate 那一步**原样**跑一遍：env 由 ci.yml 里真实的表达式渲染，
        `run:` 是那一步的 Bash 原文，判定器是 `scripts/ci/aggregate_gate.py` 的副本（放在
        `$RUNNER_TEMP/trusted-gate/`，与那一步「取默认分支上的可信判定器」落的位置相同）。
        返回判定器 stdout 那一行机器可读 JSON，并核对退出码与结论一致。

        那一步在 ci.yml 里 `runs-on: ubuntu-latest`，Bash 是它唯一的执行环境；本机没有 bash
        的平台（Windows 腿）如实 skip，而不是换一套复刻的 Python 分支去「代跑」。
        """
        import shutil
        import subprocess

        assert re.search(
            r"(?m)^    runs-on: ubuntu-latest$", _code(_job(CI, "ci-integration-gate"))
        )
        bash = shutil.which("bash")
        if bash is None or sys.platform == "win32":
            pytest.skip("Gate 那一步只在 ubuntu-latest 的 bash 里执行；本机没有可用的 bash")

        work = Path(tempfile.mkdtemp(dir=tmp_path))  # 同一个用例里会跑多行，各自一套目录
        runner_temp = work / "runner-temp"
        (runner_temp / "trusted-gate").mkdir(parents=True)
        shutil.copy(ROOT / "scripts" / "ci" / "aggregate_gate.py", runner_temp / "trusted-gate")
        shim = work / "bin"
        shim.mkdir()
        (shim / "python3").symlink_to(sys.executable)
        env = {
            "PATH": f"{shim}:{os.environ.get('PATH', '')}",
            "RUNNER_TEMP": str(runner_temp),
            "NEEDS_JSON": json.dumps({j: {"result": r} for j, r in heavy_results.items()}),
            "GATE_EVENT": G.render("${{ github.event_name }}", github),
            "GATE_FULL_CI": G.render(_gate_full_ci_expression(), github),
        }
        proc = subprocess.run(
            [bash, "-c", self._gate_step_script()],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=work,
        )
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
        assert len(lines) == 1, (
            f"判定器没有恒输出一行 JSON：\nstdout={proc.stdout}\nstderr={proc.stderr}"
        )
        verdict = json.loads(lines[0])
        expected_rc = 0 if verdict["status"] in ("success", "deferred") else 1
        assert proc.returncode == expected_rc, (proc.returncode, verdict, proc.stderr)
        return verdict

    def _heavy_results(self, ran: bool, *, real: str = "success") -> dict[str, str]:
        required = sorted(_required_of(_job(CI, "ci-integration-gate")))
        return {j: (real if ran else "skipped") for j in required}

    def test_the_event_table_for_pull_request_and_label_events(self, tmp_path):
        """CI01 §2 事件表的 PR 行 + §4 ① 的两半，按「快线跑不跑 / 重型跑不跑 / Gate 结论」逐行算。

        每行：(场景, 合成的 github 上下文, 快线跑不跑, 重型跑不跑, Gate 结论)。Gate 那一格喂的 needs
        是「重型跑了就全 success、没跑就全 skipped」——重型真跑时会红的情形归 test_aggregate_gate.py，
        这里问的只是**事件 → 档位**。
        """
        P = self._pr  # (action, 事件之后的 labels, payload 里的 label)
        rows = [
            # 场景（+full-ci = 事件之后标签里仍有 full-ci）, 上下文, 快线, 重型, Gate
            ("opened，无标签", P("opened", ()), True, False, "deferred"),
            ("synchronize，只有 docs", P("synchronize", ("docs",)), True, False, "deferred"),
            ("ready_for_review，无标签", P("ready_for_review", ()), True, False, "deferred"),
            ("labeled full-ci", P("labeled", ("full-ci",), "full-ci"), True, True, "success"),
            ("synchronize，带 full-ci", P("synchronize", ("full-ci",)), True, True, "success"),
            # §4 ① 接受的那一半：无关标签照样触发一个 run（快线全跑），Gate 结论不变
            ("labeled docs，无 full-ci", P("labeled", ("docs",), "docs"), True, False, "deferred"),
            (
                "labeled docs +full-ci",
                P("labeled", ("full-ci", "docs"), "docs"),
                True,
                True,
                "success",
            ),
            (
                "unlabeled docs +full-ci",
                P("unlabeled", ("full-ci",), "docs"),
                True,
                True,
                "success",
            ),
            ("unlabeled docs，无 full-ci", P("unlabeled", (), "docs"), True, False, "deferred"),
            # §4 ① 修的那一半：摘掉 full-ci 的 run 里重型不跑，但 Gate **按 full-ci 判** → 红
            ("unlabeled full-ci", P("unlabeled", (), "full-ci"), True, False, "failure"),
            ("merge_group", self._merge_group(), True, True, "success"),
        ]
        for name, github, fast, heavy, verdict in rows:
            assert self._fast_runs(github) is fast, f"[{name}] 快线跑不跑算错"
            assert self._heavy_runs(github) is heavy, f"[{name}] 重型跑不跑算错"
            got = self._gate_verdict(github, self._heavy_results(heavy), tmp_path)
            assert got["status"] == verdict, f"[{name}] Gate 结论应为 {verdict}：{got}"

        # push main：快线与重型都不跑，两个 Gate 的 if 也把它挡在外面
        push = self._push_main()
        assert self._fast_runs(push) is False and self._heavy_runs(push) is False
        for gate in ("ci-fast-gate", "ci-integration-gate"):
            cond = _if_of(_code(_job(CI, gate))).replace("always()", "true")
            assert G.truthy(G.evaluate(cond, push)) is False, f"{gate} 在 push main 上不该跑"

    def test_removing_the_full_ci_label_is_judged_as_full_ci_not_as_a_plain_pr(self, tmp_path):
        """§4 ① 的核心：`unlabeled(full-ci)` 那个 run 同一 head SHA 上不许产出 deferred（绿）的
        integration gate 去盖掉此前那个真实结论。

        payload 里 `pull_request.labels` 已不含 full-ci → 五个重型 job skipped；Gate 若按普通 PR
        走 --allow-deferred 就是 deferred。修法是让 GATE_FULL_CI 在 `action == 'unlabeled'
        && label.name == 'full-ci'` 时仍为 true → --require-heavy --full-ci → 全 skipped 判
        failure（reason upstream_not_success），把「重型结论不再适用于本 SHA」红出来。
        代价（刻意接受）：摘标签后要再 push 一次或重新打标签才能进队列。
        """
        github = self._pr("unlabeled", (), "full-ci")
        assert self._heavy_runs(github) is False, "前提：摘标签的 run 里重型 job 不跑"
        assert G.render(_gate_full_ci_expression(), github) == "true"
        skipped = self._heavy_results(ran=False)
        got = self._gate_verdict(github, skipped, tmp_path)
        assert got["status"] == "failure", got
        assert got["reason"] == "upstream_not_success", got
        assert all(p.endswith(": skipped") for p in got["problems"]), got
        # 对照：同一份 needs 按普通 PR 判就是 deferred——这正是修之前发生的事
        required = sorted(_required_of(_job(CI, "ci-integration-gate")))
        before = AG.decide("integration", "pull_request", required, skipped, allow_deferred=True)
        assert before["status"] == "deferred", before

    def test_label_events_share_the_pull_request_concurrency_slot(self):
        """§4 ① 接受的那一半，写成合同：任意标签事件与同 PR 的 synchronize 同一个组、
        cancel-in-progress 为 true——加一个 docs 标签会取消同 PR 运行中的 run。
        这是「GitHub 不支持按标签名过滤事件」之下三种修法都不可接受时的既定代价，
        哪天改了它（比如换 (a)(b)(c) 之一），先回 CI01 §4 ① 把代价重新算一遍。"""
        m = re.search(
            r"(?m)^concurrency:\n(?:\s*#.*\n)*\s+group: (.+)\n(?:\s*#.*\n)*\s+cancel-in-progress: (.+)$",
            CI,
        )
        assert m, "ci.yml 顶层 concurrency 解析不出来"
        group, cancel = m.group(1), m.group(2)
        sync = self._pr("synchronize", ("docs",))
        docs = self._pr("labeled", ("docs",), "docs")
        unlabeled_full_ci = self._pr("unlabeled", (), "full-ci")
        assert G.render(group, sync) == G.render(group, docs) == G.render(group, unlabeled_full_ci)
        assert G.render(group, sync) == "ci-CI-pull_request-381"
        for github in (sync, docs, unlabeled_full_ci):
            assert G.render(cancel, github) == "true"
        # merge_group / push 各自一组、不取消
        assert G.render(group, self._merge_group()) == "ci-CI-merge_group-abc123"
        assert G.render(cancel, self._merge_group()) == "false"
        assert G.render(group, self._push_main()) == "ci-CI-push-refs/heads/main"
        assert G.render(cancel, self._push_main()) == "false"


# ============================================================ PR 的 base 过滤（2026-09-21）
class TestPullRequestBaseFilter:
    """`on.pull_request.branches: [main]`：base ≠ main 的 PR 不触发任何 PR 级 workflow。

    主语：**本次 checkout 里**每个监听 `pull_request` 的 workflow 的 `on.pull_request.branches`
    ——不是 GitHub 会不会触发（那由 PR head 上的 yml 决定），也不是 merge_group（队列候选
    的 base 永远是 main，`merge_group` 事件不带这个过滤）。

    为什么（2026-09-21 用户拍板）：统一实施包的 14 层叠栈 PR（base 是上一层分支）每层 push
    都起一整套 PR 级 CI，逐级 rebase 一次 ≈180 个 ubuntu job，合并队列里的候选（#462 / #455
    两次）被挤到 90 分钟 `checks_timed_out` 踢出。结构上把 base ≠ main 的 PR 关掉：叠栈 PR
    靠 Codex 评审 + 本地验证，retarget 到 main 成为链头之后再跑全套——那时它才有资格进队列。
    三个 Gate 的 required 语义（缺失 = 失败）在 base ≠ main 的 PR 上意味着它们合不进 main，
    本来就该如此。

    **retarget 本身不产生 run**：改 base 是 `pull_request.edited`（GitHub webhook 文档），它不在
    ci.yml 的 types 闭集里、也不在其余几个的默认三个（opened / synchronize / reopened）里。顺序纪律：**先
    retarget、后 push**（下层合入后仓库的 delete_branch_on_merge 让 GitHub 自动把上层 base
    改到 main，随后 `rebase --onto` 的 push 才是那个带着 base = main 的 `synchronize`）；先
    push 后 retarget 会两头落空，补救是再推一个空提交。实证：PR #371 在 #370 合入（09:34:29Z）
    后 09:34:30Z `automatic_base_change_succeeded`，没有任何 run，09:35:34Z 的 push 才起了三个。

    判据的对象是**算出来的**集合：目录里每一个监听 `pull_request` 的 workflow（不是一张要人来
    登记的名单——名单是共享序列的读改写，两条并行的叠栈链各带一个新证据 workflow 时，谁后合
    谁的合并组就红，而它们的过滤明明都在），每一个的 `branches` == `[main]`（掉了、写成
    `main*`、多一个分支、写成 `branches-ignore` 都红）。先证明观测有效：三个常驻的 PR 级
    workflow 必须在算出来的集合里，否则是判据量错了对象，不是「没有人监听」。
    """

    #: 常驻的三个 PR 级 workflow——**判据的主语的下界**，不是闭集：叠栈轨道的证据 workflow
    #: （`private-python-targets.yml` 等）也监听 `pull_request`，它们进出 main 不需要来这里改名单，
    #: 只要带着同一条过滤。
    ALWAYS_LISTENING = frozenset({"ci.yml", "codeql.yml", "pr-conflict-domains.yml"})

    @classmethod
    def _listening(cls) -> dict[str, str]:
        """目录里每一个监听 `pull_request` 的 workflow → 文本；三个常驻的不在里面就是量错了对象。"""
        texts = _workflow_texts()
        listening = {n: t for n, t in texts.items() if "pull_request" in _events_of(t)}
        missing = cls.ALWAYS_LISTENING - set(listening)
        assert not missing, f"{sorted(missing)} 居然不监听 pull_request——判据量错了对象"
        return listening

    def test_the_subject_is_every_workflow_that_listens_to_pull_request(self):
        """主语是算出来的：目录里每个监听 `pull_request` 的文件都在集合里，`.yaml` 也算。"""
        listening = self._listening()
        assert self.ALWAYS_LISTENING <= set(listening)
        assert "private-python-targets.yml" in listening, (
            "U05 的目标验证 workflow（#475）监听 pull_request 却不在集合里——集合是怎么算的？"
        )

    def test_every_pull_request_workflow_only_triggers_on_a_main_base(self):
        for name, text in sorted(self._listening().items()):
            branches = _pull_request_filter(text, "branches")
            assert branches == ["main"], (
                f"{name} 的 on.pull_request.branches 是 {branches!r}，要恰好是 [main]——"
                "None = 没写过滤（base ≠ main 的叠栈 PR 每次 push 又会起整套 CI）；"
                "别的值 = 过滤对象不是「base 是 main」"
            )
            assert _pull_request_filter(text, "branches-ignore") is None, (
                f"{name} 同时写了 branches-ignore——GitHub 不许两者并存，且它不是这条规则的形状"
            )

    def test_retargeting_alone_does_not_produce_a_run(self):
        """`edited` 不在任何一个 PR workflow 的 types 里：retarget 只改 base，不起 run。

        这条钉的是顺序纪律的前提。哪天有人为了「retarget 就自动跑」把 `edited` 加进来，
        代价是每次改标题 / 正文都重跑整条快线——那要先回到 TestPullRequestEventTypes 的闭集
        和 CI01 §4 ① 重新算账，而不是顺手加。
        """
        for name, text in sorted(self._listening().items()):
            types = _pull_request_filter(text, "types")
            effective = set(types) if types is not None else {"opened", "synchronize", "reopened"}
            assert "edited" not in effective, f"{name} 监听了 pull_request.edited"
            assert "synchronize" in effective, (
                f"{name} 不监听 synchronize——retarget 之后的 push 就不会在 base = main 上产生结论"
            )


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
        还要再验一遍）；push main 只有 landing audit 与非门禁的 cache-seed
        （`TestLandingAudit` 钉那个集合）。"""
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
        """nightly、desktop-tauri 的 pytest 命令**不带** `--shard`：不带时钩子是 no-op，
        它们跑的仍是全集。lab 的常规套件 2026-09-19 起在同一台机器上跑全部 N 片
        （见 `test_lab_pytest_runs_every_shard_in_one_step`），不再在这里。

        前提先钉住（否则「不含」是恒真）：每个文件至少有一条 `-m pytest` 的可执行行。
        """
        for name in ("nightly.yml", "desktop-tauri.yml"):
            code = _code((WF / name).read_text(encoding="utf-8"))
            assert "-m pytest" in code, f"{name} 里一条 pytest 命令都没有——判据没有主语"
            # 看整个文件的可执行部分，不只看 `-m pytest` 那一行：`--shard=` 写在续行上
            # 是合法的 YAML 形状（lab 的常规套件就是这么写的），只查一行等于没查。
            assert "--shard" not in code, f"{name} 的可执行部分出现了 --shard"

    #: lab 里同机 N 片并行的 step：(step 名前缀, 片号 token, 只许出现在该 step 里的分片记号)。
    #: 常规套件按文件分（CI03a 的 `--shard`），操作序列 harness 按种子取模分（`TAVOTTO_SEQ_SHARD`）。
    PARALLEL_STEPS = (
        ("常规测试套件", '--shard="$k/$N"', "--shard"),
        ("操作序列 harness", 'TAVOTTO_SEQ_SHARD="$k/$N"', "TAVOTTO_SEQ_SHARD"),
    )

    @pytest.mark.parametrize(
        "prefix,token,marker", PARALLEL_STEPS, ids=[s[0] for s in PARALLEL_STEPS]
    )
    def test_lab_pytest_runs_every_shard_in_one_step(self, prefix, token, marker):
        """lab（含 release 的资格，同一份 `_lab-qualification.yml`）的常规套件与操作序列 harness
        是**同机 N 片并行**：覆盖面与从前的单进程全集相同，被切开的只有时间。静态钉四件事——

        1. 带 `--shard=` 的 pytest 命令只有那一条，片号写成 `"$k/$N"`（变量，不是字面量：
           字面量 `1/4` 意味着有人把循环拆成了手抄的几行，漏一行就漏一片）；
        2. 同一个 step 里 `for k in $(seq 1 "$N")` 起片、`wait` 收片——N 片全在这一步；
        3. 起片循环里 `pids+=("$!")` 记下每一片，收片循环按 `"${!pids[@]}"` 逐个 `wait`，
           每片的退出码进控制流（`|| rc=$?` 后 `exit "$fail"`）——少了记 pid 那一句，收片
           循环一次都不跑、整步 0 退出而片还在跑（Codex 2026-09-20 P2）；
        4. 起片之前 `set -m`：无作业控制的 shell 给 `&` 后台命令把 SIGINT 置成 SIG_IGN 并
           跨 exec 继承，Ctrl-C 类用例（`test_ctrl_c_reaches_the_script_and_leaves_no_orphan`）
           会 90 秒超时——本机双向验过，`set -m` 之后每片自成进程组、处置回默认；
        5. 常规套件那个 step 之外，整个文件的可执行部分不再出现 `--shard`——按 step 块查，
           不按 `-m pytest` 那一行查：`--shard=` 写在续行上是合法形状（常规套件自己就
           这么写），只查一行的判据会放过「slow / harness 单起一片、没有循环补齐其余片」
           （Codex 2026-09-20 P2）。

        进程内那一层（并集 == 全集、漏片 rc 4）由 tests/support/shard.py 自己负责；这里
        只保证 N 个进程都被起了、都被等了、都能把整步打红。前提先钉住：release.yml 的
        资格确实按 `mode=release` 派发到 ci-infra（那边 `uses` 的仍是这份文件）。
        """
        release = _code((WF / "release.yml").read_text(encoding="utf-8"))
        assert _DISPATCH_CMD in release and "-f mode=release" in release, (
            "release 的资格不再按 release 档派发 ci-infra——本判据对 release 的覆盖失效"
        )
        code = _code((WF / "_lab-qualification.yml").read_text(encoding="utf-8"))
        pytest_lines = [ln for ln in code.splitlines() if "-m pytest" in ln]
        assert len(pytest_lines) >= 3, "lab 里的 pytest 命令少于三条——判据没有主语"
        sharded = [ln for ln in pytest_lines if "--shard" in ln]
        assert len(sharded) == 0, (
            "`--shard=` 该在续行上、不在 `-m pytest` 那一行——形状变了先来改本判据"
        )
        step = _lab_step(code, prefix)
        assert token in step, f"{prefix} 的片号必须是变量 `{token}`"
        assert 'for k in $(seq 1 "$N")' in step, f"{prefix} 没有按 1..N 起片的循环"
        n_def = re.search(r"^\s*N=(\d+)\s*$", step, re.M)
        assert n_def, f"{prefix} 没有一处 `N=<数字>` 的定义"
        assert int(n_def.group(1)) >= 2, (
            "N 必须 ≥ 2：N=0 时 `seq 1 0` 一片都不起、整步 0 退出（Codex 2026-09-20 P2）；N=1 就不叫并行"
        )
        launch = re.search(r'for k in \$\(seq 1 "\$N"\); do\n(.*?)^\s*done\s*$', step, re.S | re.M)
        assert launch, f"{prefix} 的起片循环切不出来（`for … do` 到 `done`）"
        assert 'pids+=("$!")' in launch.group(1), (
            '起片循环里没有 `pids+=("$!")`——没记下的片 `wait` 循环一次都不跑，整步 0 退出而片还在跑'
            "（Codex 2026-09-20 P2）"
        )
        assert 'for i in "${!pids[@]}"; do' in step, "收片循环没有按 pids 逐个遍历"
        assert 'wait "${pids[$i]}" || rc=$?' in step, "每片的 wait 结果没有进 rc"
        assert 'exit "$fail"' in step, "整步没有按 fail 退出"
        body = _code(step)
        assert body.index("set -m") < body.index('for k in $(seq 1 "$N")'), (
            "起片之前没有 `set -m`——`&` 起的片会继承 SIG_IGN 的 SIGINT，Ctrl-C 类用例假红"
        )
        rest = code.replace(step, "")
        assert "-m pytest" in rest, f"{prefix} 之外没有别的 pytest 命令了——第 5 条判据没有主语"
        assert marker not in rest, f"{prefix} 之外出现了 {marker}（含续行）：只该有那一步用这种分片"

    _STUB = """#!/usr/bin/env bash
# 假 python：认 --shard=K/N（常规套件）或环境变量 TAVOTTO_SEQ_SHARD=K/N（操作序列 harness），
# 记一笔「第 K 片跑过」，按 STUB_FAIL 里的片号决定退出码
shard="${TAVOTTO_SEQ_SHARD:-}"
for a in "$@"; do case "$a" in --shard=*) shard="${a#--shard=}";; esac; done
k="${shard%%/*}"
: "${k:?stub 没收到 --shard=K/N 也没收到 TAVOTTO_SEQ_SHARD}"
touch "$STUB_DIR/ran-$k"
echo "stub shard=$shard"
case ",${STUB_FAIL:-}," in
  *",$k,"*) echo "1 failed, 9 passed in 0.01s"; exit 1 ;;
  *) echo "10 passed, 1 skipped in 0.01s"; exit 0 ;;
esac
"""

    @pytest.mark.skipif(
        sys.platform == "win32", reason="bash 脚本按 POSIX 作业控制跑，Windows 不在这一格"
    )
    @pytest.mark.parametrize("failing", ["", "3", "1,4"])
    @pytest.mark.parametrize(
        "prefix,log_prefix,error_text",
        [
            ("常规测试套件", "pytest-shard-", "常规测试套件片"),
            ("操作序列 harness", "seq-shard-", "操作序列 harness 片"),
        ],
        ids=["regular", "seq"],
    )
    def test_lab_regular_suite_step_exits_nonzero_iff_a_shard_fails(
        self, tmp_path, failing, prefix, log_prefix, error_text
    ):
        """把常规套件那个 step 的脚本**原样**抽出来真跑一遍，python 换成一个只认 `--shard=K/N`
        的假程序：N 片都被起了（每片留下一枚 `ran-K`），全绿时整步 0 退出并逐片打「绿」，
        任一片非零时整步非零并打出 `::error::…片 K/N`。静态那条钉形状，这一条钉**行为**——
        `fail=1` 挪走、`wait` 结果不进 rc、`exit "$fail"` 改成 `exit 0`，这里都会红，不必再
        一句一句钉字符串（Codex 2026-09-20 第三轮 P2 的解法）。
        """
        import shutil
        import subprocess

        bash = shutil.which("bash")
        assert bash, "找不到 bash——这一格的判据没法执行"
        script = _lab_step_script(prefix)
        assert "${{ steps.venv.outputs.python }}" in script, (
            "step 脚本里没有 python 占位——抽错了 step？"
        )
        stub = tmp_path / "fake-python"
        stub.write_text(self._STUB, encoding="utf-8")
        stub.chmod(0o755)
        runner_temp = tmp_path / "runner-temp"
        runner_temp.mkdir()
        stub_dir = tmp_path / "stub"
        stub_dir.mkdir()
        # self-hosted 的 RUNNER_TEMP 跨 run 复用：摆一份「上一轮」留下的同名产物，跑完必须没了
        # （Codex 2026-09-20 P2：某片在写文件前死掉，旧文件会被 always() 的证据上传当成这一轮的）
        stale = runner_temp / f"{log_prefix}9-junit.xml"
        stale.write_text("<stale/>", encoding="utf-8")
        (tmp_path / "step.sh").write_text(
            script.replace("${{ steps.venv.outputs.python }}", str(stub)), encoding="utf-8"
        )
        env = dict(
            os.environ, RUNNER_TEMP=str(runner_temp), STUB_DIR=str(stub_dir), STUB_FAIL=failing
        )
        r = subprocess.run(
            [bash, str(tmp_path / "step.sh")],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        n = int(re.search(r"(?m)^N=(\d+)$", script).group(1))
        assert n >= 2, f"N={n}：下面的「每一片都起了」在 N=0 时恒真（Codex 2026-09-20 P2）"
        assert not stale.exists(), "上一轮留在 RUNNER_TEMP 里的同名产物没被清掉"
        ran = sorted(int(p.name.split("-")[1]) for p in stub_dir.glob("ran-*"))
        assert ran == list(range(1, n + 1)), (
            f"不是每一片都被起了：{ran} / N={n}\n{r.stdout}\n{r.stderr}"
        )
        expected_fail = {int(x) for x in failing.split(",") if x}
        if not expected_fail:
            assert r.returncode == 0, f"全绿却非零退出：rc={r.returncode}\n{r.stdout}\n{r.stderr}"
            assert r.stdout.count("绿：") == n, r.stdout
            assert "::error::" not in r.stdout, r.stdout
        else:
            assert r.returncode != 0, f"有片红了整步却 0 退出\n{r.stdout}\n{r.stderr}"
            for k in expected_fail:
                assert f"::error::{error_text} {k}/{n}" in r.stdout, r.stdout
            assert r.stdout.count("绿：") == n - len(expected_fail), r.stdout

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
        # `--with-deps` 不在这里钉：CI02 把它从 Windows 两片去掉了（实验，由 full-ci run 判），
        # 带不带由 TestBuildReuseAndCaches::test_windows_installs_browsers_without_with_deps_and_posix_keeps_it 管。
        assert re.search(
            r"(?m)^\s+pnpm exec playwright install (?:--with-deps )?\$\{\{ matrix\.browsers \}\}\s*$",
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
        # 就绪上限 120：真产品在 GitHub macOS runner 上 bind → listen 之间卡 getfqdn ~36 s
        # （#376 首跑 ready_seconds 35.78 s），60 贴边——收回默认值会让 macOS 腿偶发假红
        assert re.search(r"--timeout 120(\s|$)", args), args
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
        m = re.search(r"(?m)^          path: \|\n((?:^            \S.*\n)+)", upload[0])
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


# ============================================================ 产物复用与缓存（CI02）
def _jsonc(path: Path) -> dict:
    """读 tsconfig 那种带 `/* … */` / `// …` 注释的 JSON；本仓库的几份里没有带注释符号的字符串。"""
    raw = path.read_text(encoding="utf-8")
    raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    raw = re.sub(r"(?m)^\s*//.*$", "", raw)
    return json.loads(raw)


def _with_block(step: str) -> dict[str, str]:
    """一个 `uses:` 步骤的 `with:` 块 → {键: 值}（单行 `k: v`，或 `with: { k: v, … }` 流式）。"""
    flow = re.search(r"(?m)^\s*with: \{(.*)\}\s*$", step)
    if flow:
        out: dict[str, str] = {}
        for part in flow.group(1).split(","):
            k, sep, v = part.partition(":")
            assert sep, f"with 流式块里这一段不是 `k: v`：{part!r}"
            out[k.strip()] = v.strip().strip("\"'")
        return out
    m = re.search(r"(?m)^\s*with:\n((?:^          \S.*\n?)+)", step)
    if not m:
        return {}
    out = {}
    for ln in m.group(1).splitlines():
        k, sep, v = ln.strip().partition(":")
        if sep and not k.startswith("#"):
            out[k.strip()] = v.strip()
    return out


def _uses_steps(action_prefix: str) -> list[tuple[str, str]]:
    """全部 job 里 `uses: <action_prefix>@…` 的步骤：[(job id, 步骤正文)]。"""
    found: list[tuple[str, str]] = []
    for job_id in TestHeavyLaneDependencies._job_ids():
        for step in _steps(_job(CI, job_id)):
            if re.search(rf"(?m)^\s*uses: {re.escape(action_prefix)}@", step):
                found.append((job_id, step))
    return found


class TestBuildReuseAndCaches:
    """CI02（2026-09-16）：产物复用与工具准备的**决定**，以及缓存的形状。

    决定本身（web 应用 / MCP 画布 / playground / wheel / workerd / runtime / PyInstaller 全部
    **同 job 重建、不跨 job 抽取**；Playwright 浏览器**不缓存**）与它的数字在
    docs/implementation/ci-foundation/CI02_BUILD_REUSE.md。这里钉的是决定落地后的形状，
    改动任一条都得先回到那份文档改数字：
      * TypeScript 的类型检查只有一个执行位置——`pnpm build` 的第一条命令 `tsc -b`，而
        `tsc -b` 检查的是 `web/tsconfig.json` 的 references **集合**（e2e 在里面；本机反证：
        把它从 references 里拿掉，e2e 里的类型错误 `pnpm build` 退 0）；
      * `actions/cache` 的清单是**枚举**（三个 step，全是 CPython 归档：两处消费者 + push main 上
        cache-seed 的种子步，后者展开成 windows / macos 两条腿），key 含 os / arch / 锁 hash；
        setup-node 的 pnpm 缓存按锁文件；rust-cache 各自点名 workspace，并带一个
        **(workspace, profile) 命名的 `shared-key`**——同键的只有「种子腿」与「跑同一组 cargo 命令
        的消费者」，无关 job 之间仍不共享可写 target（种子与消费者的对拍在 `TestCacheSeed`）；
        没有任何缓存 path 指向 venv / site-packages / 用户目录 / 测试结果 / 浏览器目录；
      * 唯一的数据边 frontend → plugin-candidate：消费者用 **HEAD 的 SHA** 与**清单里的
        content_digest** 两把尺子核候选（脚本侧的负例在 tests/test_plugin_stage.py）。
    """

    WEB = ROOT / "web"

    # ── C：TypeScript 真检查引用 ─────────────────────────────────────────────
    def test_the_web_build_script_starts_with_a_real_project_build(self):
        """主语是 `build` 脚本的**第一条**命令：`tsc -b`，不是 `tsc --noEmit`（方案文件下恒绿）、
        不是直接 `vite build`（vite 不做类型检查）。后面接的是产物构建与扫描面门禁。"""
        pkg = json.loads((self.WEB / "package.json").read_text(encoding="utf-8"))
        parts = [p.strip() for p in pkg["scripts"]["build"].split("&&")]
        assert parts[0] == "tsc -b", f"web 的 build 脚本第一条命令是 {parts[0]!r}，不是 `tsc -b`"
        assert "vite build" in parts, parts

    def test_the_project_references_cover_every_typescript_root(self):
        """`tsc -b` 检查的是 references 的**集合**：app / node / e2e 三份都在，方案文件自己不编任何
        东西（`files: []`），三份的 include 合起来恰好是仓库里的四个 TS 根。少一份引用不会有任何
        红灯——那一份里的类型错误只是不再有执行位置（本机反证 CI02 文档 §3）。"""
        root = _jsonc(self.WEB / "tsconfig.json")
        assert root.get("files") == [], (
            "根 tsconfig 必须是 `files: []` 的方案文件（否则 -b 的语义就变了）"
        )
        refs = {r["path"] for r in root["references"]}
        assert refs == {"./tsconfig.app.json", "./tsconfig.node.json", "./tsconfig.e2e.json"}, refs
        roots: set[str] = set()
        for ref in sorted(refs):
            cfg = _jsonc(self.WEB / ref)
            assert cfg["compilerOptions"].get("noEmit") is True, (
                f"{ref} 不是 noEmit——-b 会往树里写 .js"
            )
            include = cfg.get("include")
            assert include, f"{ref} 没有 include"
            roots |= set(include)
        assert roots == {"src", "vite.config.ts", "e2e", "playwright.config.ts"}, (
            f"三份 tsconfig 的 include 并集是 {sorted(roots)}——新增 / 挪走 TS 根要回来改这张枚举"
        )
        for r in roots:
            assert (self.WEB / r).exists(), f"include 里的 {r} 在 web/ 下不存在"

    def test_the_frontend_job_type_checks_through_pnpm_build_without_a_safety_net(self):
        """frontend job 的类型检查就是 `- run: pnpm build` 那一步：恰好一条、不带 if / continue-on-error，
        没有第二条 tsc 步骤（曾经的 `pnpm tsc --noEmit` 恒绿），生成物索引检查排在它之后。"""
        steps = _steps(_job(CI, "frontend"))
        build = [i for i, s in enumerate(steps) if re.search(r"(?m)^\s*run: pnpm build\s*$", s)]
        assert len(build) == 1, [_step_name(s) or s.splitlines()[0] for s in steps]
        step = steps[build[0]]
        assert not re.search(r"(?m)^\s*(if|continue-on-error):", step), step
        assert not [s for s in steps if re.search(r"\btsc\b", s)], (
            "frontend 里出现了 pnpm build 之外的 tsc 步骤"
        )
        gen = [i for i, s in enumerate(steps) if "scripts/ci/check_generated_untracked.py" in s]
        assert len(gen) == 1 and gen[0] > build[0], "生成物不进索引的检查要排在 pnpm build 之后"

    # ── D：缓存四类 ─────────────────────────────────────────────────────────
    #: `actions/cache` 的完整清单（job, path, key）——枚举不是白名单：多一条就红，作者得先回
    #: CI02 文档把新缓存归到 04 §5 的四类里、写上 key 的维度与命中作用域，再来改这里。
    #: 三个 step 都是同一把 CPython key：两处消费者，加 push main 上 cache-seed 的种子步
    #: （CI02 §4.1 (a)，2026-09-16；它在 matrix 里展开成 windows / macos 两条腿——与消费者的
    #: os 集合逐个对拍在 `TestCacheSeed`）。
    CPYTHON_KEY = "cpython-${{ runner.os }}-${{ runner.arch }}-${{ hashFiles('packaging/runtime-lock.json') }}"
    ACTIONS_CACHE = {
        ("windows-exe-smoke", "build/runtime-cache", CPYTHON_KEY),
        ("macos-app-smoke", "build/runtime-cache", CPYTHON_KEY),
        ("cache-seed", "build/runtime-cache", CPYTHON_KEY),
    }

    def test_actions_cache_steps_are_exactly_the_cpython_archive_downloads(self):
        """第一类（下载 bytes）只有两处：path 是脚本的下载缓存目录，key 含 runner.os / runner.arch /
        锁文件 hash，恢复步在「构建内置渲染 runtime」之前。path 里不许出现 venv / site-packages /
        用户目录 / 测试结果 / 浏览器目录（第四类不能当缓存；浏览器不缓存是 CI02 的决定）。"""
        found = set()
        for job_id, step in _uses_steps("actions/cache"):
            w = _with_block(step)
            assert set(w) >= {"path", "key"}, (job_id, w)
            found.add((job_id, w["path"], w["key"]))
            steps = _steps(_job(CI, job_id))
            me = next(i for i, s in enumerate(steps) if s == step)
            use = [i for i, s in enumerate(steps) if "scripts/build_worker_runtime.py" in s]
            assert len(use) == 1 and me < use[0], f"{job_id}：缓存恢复步不在 runtime 构建之前"
        assert found == self.ACTIONS_CACHE, f"actions/cache 的清单变了：{sorted(found)}"
        for _job_id, path, key in found:
            for dim in (
                "${{ runner.os }}",
                "${{ runner.arch }}",
                "hashFiles('packaging/runtime-lock.json')",
            ):
                assert dim in key, (key, dim)
            for bad in (
                "venv",
                "site-packages",
                ".tavotto",
                "test-results",
                "playwright-report",
                "ms-playwright",
            ):
                assert bad not in path, (path, bad)

    #: setup-node 的 pnpm 缓存：开了的 job 必须按 web/pnpm-lock.yaml 取 key；`package` 四条腿没开
    #: （CI02 决定不加：合并组上每个候选 ref 都冷，加了只是多四份 57 MiB 的 save——文档 §4；
    #: 种子落地后可回来开，那是另一个 PR）。`cache-seed` 是 push main 上的种子（每个 os 一条腿
    #: 真跑一次 `pnpm install`），消费者与种子的 os 集合对拍在 `TestCacheSeed`。
    PNPM_CACHED = {
        "frontend",
        "plugin-candidate",
        "windows-exe-smoke",
        "macos-app-smoke",
        "posix-e2e",
        "cache-seed",
    }
    PNPM_UNCACHED = {"package"}

    def test_every_setup_node_pnpm_cache_is_keyed_by_the_lockfile(self):
        cached, uncached = set(), set()
        for job_id, step in _uses_steps("actions/setup-node"):
            w = _with_block(step)
            assert w.get("node-version") == "22", (job_id, w)
            if w.get("cache") == "pnpm":
                assert w.get("cache-dependency-path") == "web/pnpm-lock.yaml", (job_id, w)
                cached.add(job_id)
            else:
                assert "cache" not in w, (job_id, w)
                uncached.add(job_id)
        assert cached == self.PNPM_CACHED, cached
        assert uncached == self.PNPM_UNCACHED, uncached

    #: rust-cache（第二类，编译缓存）：每处点名自己的 workspace，并带 `shared-key`（CI02 §4.1 (a)，
    #: 2026-09-16）。**为什么从「不许 shared-key」翻成「必须 shared-key」**：action 的自动键含 job id
    #: （实测 v0-rust-<key>-<job>-Linux-x64-<env>-<lock>），push main 上的种子 job 与消费者 id 不同，
    #: 种了也命不中；shared-key 代替的正是 job id 那一段，os / arch / rustc / CARGO*·RUST* 环境变量 /
    #: Cargo.toml + Cargo.lock 仍由 action 并入。04 §5「无关 job 不共享可写 target」仍成立——键名是
    #: (workspace, profile)：dev 与 release 的 target/ 不是一份，所以 `workerd` ≠ `workerd-release`；
    #: 同键的只有种子腿与跑同一组 cargo 命令的消费者（对拍在 `TestCacheSeed`）。没有 cache-on-failure，
    #: 也没有额外的 `key`（desktop-shell 原先那条 `key: ${{ matrix.os }}` 与自动键里的 os 重复）。
    RUST_CACHE = {
        ("workerd", "workerd", "workerd"),
        ("desktop-shell", "src-tauri", "desktop-shell"),
        ("windows-exe-smoke", "workerd", "workerd-release"),
        ("macos-app-smoke", "workerd", "workerd-release"),
        ("cache-seed", "${{ matrix.workspace }}", "${{ matrix.rust }}"),
    }

    def test_every_rust_cache_names_its_own_workspace_and_a_shared_key(self):
        found = set()
        for job_id, step in _uses_steps("Swatinem/rust-cache"):
            w = _with_block(step)
            assert {"workspaces", "shared-key"} <= set(w), (job_id, w)
            assert "cache-on-failure" not in w and "key" not in w, (job_id, w)
            found.add((job_id, w["workspaces"], w["shared-key"]))
        assert found == self.RUST_CACHE, sorted(found)

    #: 两条 Playwright 腿各恰好一条 `playwright install`——真跑（幂等），不从缓存恢复浏览器目录
    #: （上面的枚举已保证没有那样的 actions/cache）。**Windows 不带 `--with-deps`**（CI02 实验：
    #: 它在 windows-latest 上只做一件事——装 Media Foundation，226s / 204s，占了这一步的 90%，且这条腿
    #: 是合并资格的关键路径；Chromium 是否需要它由本 PR 的 full-ci run 判，红则加回）；**posix 带**
    #: （apt 装 chromium 的真依赖与字体，23s）。主语是整条命令，不是「含不含某个 flag」。
    PLAYWRIGHT_INSTALL = {
        "windows-exe-smoke": "pnpm exec playwright install ${{ matrix.browsers }}",
        "posix-e2e": "pnpm exec playwright install --with-deps chromium",
    }

    def test_windows_installs_browsers_without_with_deps_and_posix_keeps_it(self):
        for job_id, want in self.PLAYWRIGHT_INSTALL.items():
            code = _code(_job(CI, job_id))
            lines = re.findall(r"(?m)^\s+(pnpm exec playwright install\b.*)$", code)
            assert lines == [want], (job_id, lines)

    # ── E：唯一数据边的身份 ──────────────────────────────────────────────────
    def test_the_plugin_candidate_consumer_verifies_head_sha_and_manifest_digest(self):
        """消费者不信任 artifact 的名字：SHA 取**本次 checkout 的 HEAD**、digest 取**artifact 里的清单**，
        两把尺子一起交给 `plugin_stage.py verify`（外加 `--serve` 真起 server 读画布）。生产者那侧同形：
        stage 的 `--source-sha` 也是 HEAD（脚本自己会与 HEAD 对拍），zip 再按 `digest` 重算验一次。"""
        consumer = [
            s for s in _steps(_job(CI, "plugin-candidate")) if "plugin_stage.py unpack" in s
        ]
        assert len(consumer) == 1, "plugin-candidate 里切不出解包步骤"
        run = consumer[0]
        assert 'SHA="$(git rev-parse HEAD)"' in run, run
        assert '[\'content_digest\'])" "$C/plugin-build.json")"' in run, run
        verify = re.search(
            r"(?m)^\s+python3 scripts/plugin_stage\.py verify \"\$PLUGIN\" (.+)$", run
        )
        assert verify, run
        for needle in ('--source-sha "$SHA"', '--content-digest "$DIGEST"', "--serve"):
            assert needle in verify.group(1), (needle, verify.group(1))
        producer = [s for s in _steps(_job(CI, "frontend")) if "plugin_stage.py stage" in s]
        assert len(producer) == 1
        prun = producer[0]
        assert 'SHA="$(git rev-parse HEAD)"' in prun and '--source-sha "$SHA"' in prun, prun
        assert '--content-digest "$(python3 scripts/plugin_stage.py digest "$STAGE")"' in prun, prun


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
    #: push main 上跑的 job 集合——**枚举**：落地审计（产出结论）+ 缓存种子（非门禁，只暖缓存，
    #: CI02 §4.1 (a)）。多一个 job 出现在 push 上就红：push main 不重复打包、不重复冒烟是 CI01
    #: 定下的合同，种子是它唯一的例外，而且它不产生任何结论（`TestCacheSeed`）。
    PUSH_MAIN_JOBS = {"main-landing-audit", "cache-seed"}

    def test_main_push_runs_only_the_landing_audit_and_the_cache_seed(self):
        """push main 上恰好两个 job：落地审计 + cache-seed；落地审计只在 push 上跑、且真的轻
        ——不装科学栈、不打包、不跑冒烟。判据的主语是「条件里含 `== 'push'` 的 job 集合」，
        对每个 job 都读（单行 if 与折叠 `if: >-` 都认），不是只看那两个自己。"""
        on_push = set()
        for job_id in TestHeavyLaneDependencies._job_ids():
            cond = _condition_of(_job(CI, job_id))
            if cond is None or "== 'push'" in cond or "!= 'pull_request'" in cond:
                on_push.add(job_id)
        assert on_push == self.PUSH_MAIN_JOBS, sorted(on_push)
        assert _if_of(_job(CI, "main-landing-audit")) == "github.event_name == 'push'"
        assert _condition_of(_job(CI, "cache-seed")) == TestCacheSeed.CONDITION
        block = _code(_job(CI, "main-landing-audit"))
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


# ============================================================ 首开 / 输出 harness（U01，ADR 0053 §五）
class TestFoundationHarnessStep:
    """统一实施包 U01 把 case enrollment 的校验接成 `invariants` job 的三步：预期集合 →
    enforced 用例 → 闭集校验 → 证据上传。判据的主语是**这三步的顺序与它们共用的目录**：
    预期集合必须在用例之前生成（在执行前产生，03 §5）；用例写记录的目录必须就是校验读的
    目录（否则校验永远看到空目录——而空目录是红，于是这条 job 会恒红；反过来若把校验改成
    读别的目录也恒红，两个方向都只会红不会假绿，这里钉的是它们**同一个**）；校验步的
    退出码是结论；enforced case 的 lane 与 `--lane` 一致（否则预期集合恒空 → 恒红）。"""

    JOB = "invariants"
    RESULTS = "${{ runner.temp }}/foundation/results"

    def _three(self) -> tuple[str, str, str]:
        steps = _steps(_job(CI, self.JOB))
        names = [_step_name(s) for s in steps]
        run = [s for s in steps if _step_name(s).startswith("首开 / 输出 harness（")]
        check = [s for s in steps if _step_name(s).startswith("首开 / 输出 harness 合同校验")]
        upload = [s for s in steps if _step_name(s).startswith("首开 / 输出 harness 证据")]
        assert len(run) == 1 and len(check) == 1 and len(upload) == 1, names
        assert (
            names.index(_step_name(run[0]))
            < names.index(_step_name(check[0]))
            < names.index(_step_name(upload[0]))
        ), names
        return run[0], check[0], upload[0]

    def test_expected_set_is_generated_before_the_cases_run(self):
        run, _, _ = self._three()
        assert "if:" not in run, "跑用例那一步不许带 if:（必需步骤）"
        script = _code(run)
        assert script.index("foundation_harness.py expected") < script.index("python -m pytest"), (
            "预期实例集合必须在执行**前**生成"
        )
        assert "--lane pr" in script
        assert "tests/test_foundation_harness.py" in script, "enforced 用例所在文件没进这一步"
        assert f"TAVOTTO_FOUNDATION_RESULTS: {self.RESULTS}" in run, (
            "用例写记录的目录必须由这个环境变量指定，且落在 runner.temp 下"
        )

    def test_the_check_reads_the_same_directory_the_cases_wrote_and_has_its_own_verdict(self):
        run, check, _ = self._three()
        assert _if_of_step(check) == "always()", "校验步红时也要留下报告；结论靠它自己的退出码"
        script = _code(check)
        assert "foundation_harness.py validate" in script
        # 同一个目录：env 里的 `${{ runner.temp }}/foundation/results` ↔ 脚本里的 `$RUNNER_TEMP/foundation/results`
        assert '--results "$RUNNER_TEMP/foundation/results"' in script
        assert '--out "$RUNNER_TEMP/foundation/expected.json"' in _code(run)
        assert '--expected "$RUNNER_TEMP/foundation/expected.json"' in script
        assert "|| true" not in script and "continue-on-error" not in check, (
            "校验步的退出码不许被吞"
        )

    def test_the_evidence_upload_carries_expected_results_and_report(self):
        _, _, upload = self._three()
        assert _if_of_step(upload) == "always()"
        assert "uses: actions/upload-artifact@" in upload
        with_ = _with_block(upload)
        assert with_["name"].startswith("foundation-harness-pr-"), with_
        for part in ("foundation/expected.json", "foundation/results", "foundation/report"):
            assert part in upload, f"证据里少了 {part}"

    def test_the_lane_matches_the_enforced_cases_in_the_ledger(self):
        """`--lane pr` 不是随手写的：台账里 enforced 的 case 都在这条 lane 上，否则预期集合恒空。"""
        ledger = json.loads(
            (
                WF.parents[1] / "docs" / "implementation" / "tavotto-foundation" / "enrollment.json"
            ).read_text(encoding="utf-8")
        )
        enforced = [c for c in ledger["cases"] if c["enrollment"] == "enforced"]
        assert enforced, "台账里一个 enforced 的 case 都没有——这条 CI 步骤会恒红（空集合不是通过）"
        assert {c["lane"] for c in enforced} == {"pr"}
        assert "invariants" in _needs_of(_job(CI, "ci-fast-gate")), (
            "harness 的落点必须在 fast gate 闭集里"
        )


def _if_of_step(step: str) -> str:
    m = re.search(r"(?m)^\s*if: (.+)$", step)
    return m.group(1).strip() if m else ""


# ============================================================ 缓存种子（CI02 §4.1 (a)）
#: 消费者 job 的 `runs-on` 里的 `runner.os` 值 → matrix 里的 runner 标签。种子 job 里 Linux 专属
#: 的步骤（装 Tauri 系统依赖）用 `runner.os == 'Linux'` 判，而 matrix 用的是标签；两套名字的对应
#: 关系是**枚举**，加一类 runner 要回到这里登记。
_RUNNER_OS_LABEL = {"Linux": "ubuntu-latest", "macOS": "macos-latest", "Windows": "windows-latest"}


def _job_oses(job_id: str) -> set[str]:
    """一个 job 会跑在哪些托管 runner 上（矩阵展开）——消费者那一侧的 os 集合。"""
    oses = _runs_on_atoms(_code(_job(CI, job_id)), job_id)
    assert oses <= _HOSTED_RUNNERS, (job_id, oses)
    return oses


def _cargo_commands(job_block: str) -> set[str]:
    """一个 job 的 `run:` 行里所有会写 target/ 的 cargo 命令（build / clippy / test / check），
    去掉 `--manifest-path <ws>/Cargo.toml`（冒烟腿从仓库根带它进 workspace，种子腿在
    workspace 里跑，target/ 是同一份）；`cargo fmt` 不编译，不算。"""
    cmds: set[str] = set()
    for line in _code(job_block).splitlines():
        for m in re.finditer(r"\bcargo (?:build|clippy|test|check)\b[^;&|]*", line):
            cmd = re.sub(r"\s+--manifest-path \S+/Cargo\.toml", "", m.group(0)).strip()
            cmds.add(cmd)
    return cmds


class TestCacheSeed:
    """push main 上的缓存种子（CI02 §4.1 (a)，2026-09-16 用户拍板；ci.yml `cache-seed`）。

    GitHub 缓存的作用域是「当前 ref + 默认分支」：合并组候选 ref 与 PR 首跑都读不到别人的缓存，
    而 push main 上原先没有任何产缓存的 job → 合并资格这条唯一的常规执行点上三类缓存 0% 命中、
    每个候选各 save ≈ 1.8 GB（CI02 §4.1 的数字）。种子在 main 上把三类缓存各种一份。

    这里钉的是**种子与消费者同键、同 os、同一组命令**——键对不上就是「种了也命不中」，而那不会
    有任何红灯，只是合并组日志里永远没有 `Restored from cache key`。主语逐条写明：
      * rust-cache：`shared-key` 的**值** × os 的集合，消费者侧 == 种子侧（集合相等）；同一把键的
        消费者 cargo 命令 ⊆ 种子腿那个 profile 的命令（种子跑的就是消费者那条）；
      * CPython 归档：`actions/cache` 的 `path` / `key` **字符串相等**，且种子腿的 os 集合 == 消费者的；
      * pnpm store：开了 `cache: pnpm` 的消费者的 os 集合 == 种子里 `pnpm: true` 的腿的 os 集合；
      * 种子只在 push 上跑、不出现在任何 Gate 的 needs / --required 里、不加 continue-on-error。
    步骤级 `if:` 一律读出来与 matrix 字段比死——按 matrix 字段算出来的 os 集合，只有在步骤真按
    那个字段开关时才是真的。
    """

    SEED = "cache-seed"
    #: 种子的事件条件——**恰好**这一句：push main 是种子；带 `full-ci` 的 PR 上是首验（PR 作用域
    #: 的缓存 main 读不到，但五条腿的每一步先在 PR 自己的 run 上跑过，第一次执行不落在合入那一刻）。
    #: 不含 merge_group（候选 ref 上的种子谁也读不到）；不是 FAST_LANE_CONDITION，也不是重型档的
    #: 条件——它既不是快线也不是重型，那两组枚举都不该把它数进去。
    CONDITION = (
        "github.event_name == 'push' || (github.event_name == 'pull_request' "
        "&& contains(github.event.pull_request.labels.*.name, 'full-ci'))"
    )
    #: 每把 shared-key 对应**一种** profile：dev = workerd / desktop-shell 两个 job 的 clippy + test，
    #: release = 两条冒烟腿的 build --release。同键不同 profile 的 target/ 会互相覆盖。
    PROFILES = {"workerd": "dev", "desktop-shell": "dev", "workerd-release": "release"}

    @classmethod
    def _legs(cls) -> list[dict[str, str]]:
        legs = _matrix_include(_job(CI, cls.SEED))
        for leg in legs:
            assert set(leg) == {"os", "rust", "workspace", "profile", "pnpm", "cpython"}, leg
            assert leg["os"] in _HOSTED_RUNNERS, leg
            assert leg["pnpm"] in {"true", "false"} and leg["cpython"] in {"true", "false"}, leg
            assert cls.PROFILES.get(leg["rust"]) == leg["profile"], (
                f"种子腿 {leg} 的 profile 与 PROFILES 枚举不一致——同一把 shared-key 只能对应一种"
            )
        assert len({(leg["os"], leg["rust"]) for leg in legs}) == len(legs), "有两条腿种同一把键"
        return legs

    @classmethod
    def _consumer_rust_caches(cls) -> list[tuple[str, dict[str, str]]]:
        found = [
            (j, _with_block(st)) for j, st in _uses_steps("Swatinem/rust-cache") if j != cls.SEED
        ]
        assert len(found) >= 4, found
        return found

    def test_the_seed_runs_on_push_and_full_ci_prs_only_and_is_not_a_gate_input(self):
        """条件恰好是 push ∪ (pull_request ∧ full-ci)（字符串相等，折叠块并成一行比）；不在两个
        Gate 的 needs / --required 闭集里（不是门禁）；不在快线枚举也不在重型枚举里（`HEAVY` /
        `HEAVY_CONSUMERS` / fast gate needs 三处都读不到它，那两组按枚举跑的用例不会误判它）；
        没有 needs（不等任何 job）；没有 continue-on-error（红了就红着可见）；有超时。"""
        block = _code(_job(CI, self.SEED))
        assert _condition_of(block) == self.CONDITION, _condition_of(block)
        assert "!= '" not in self.CONDITION and "merge_group" not in self.CONDITION
        for gate in ("ci-fast-gate", "ci-integration-gate"):
            g = _job(CI, gate)
            assert self.SEED not in _needs_of(g) | _required_of(g), f"{gate} 把种子当成了输入"
        assert self.SEED not in TestGates.HEAVY and self.SEED not in _needs_of(
            _job(CI, "ci-fast-gate")
        )
        assert not re.search(r"(?m)^    needs:", block), "种子不该等任何 job"
        assert "continue-on-error" not in block, "种子红了要看得见，不许 continue-on-error"
        assert re.search(r"(?m)^    timeout-minutes: \d+$", block), "种子没有超时上限"
        assert re.search(r"(?m)^      fail-fast: false$", block), "一条腿红不许掐掉别的腿"
        assert self.SEED not in TestHeavyLaneDependencies.HEAVY_CONSUMERS
        assert list(MQ.GATE_CONTEXTS) == ["CI fast gate", "CI integration gate", "CodeQL gate"], (
            "required contexts 变了——种子不该成为其中之一"
        )

    def test_every_rust_shared_key_is_seeded_on_main_for_exactly_the_consumers_oses(self):
        """主语：(shared-key 的值, workspace, os) 三元组的集合，消费者侧 == 种子侧。
        种子的 rust-cache 步骤必须把 matrix 字段原样交给 action（否则 matrix 只是装饰）。"""
        consumers: set[tuple[str, str, str]] = set()
        for job_id, w in self._consumer_rust_caches():
            for os_ in _job_oses(job_id):
                consumers.add((w["shared-key"], w["workspaces"], os_))
        seeds = {(leg["rust"], leg["workspace"], leg["os"]) for leg in self._legs()}
        assert consumers == seeds, (
            f"消费者 − 种子 = {sorted(consumers - seeds)}；种子 − 消费者 = {sorted(seeds - consumers)}"
        )
        seed_steps = [
            w
            for j, st in _uses_steps("Swatinem/rust-cache")
            if j == self.SEED
            for w in [_with_block(st)]
        ]
        assert seed_steps == [
            {"workspaces": "${{ matrix.workspace }}", "shared-key": "${{ matrix.rust }}"}
        ], seed_steps

    def test_each_seed_leg_runs_the_cargo_commands_of_the_consumers_sharing_its_key(self):
        """同一把 shared-key 的 target/ 是共写的：消费者的每条编译命令都必须出现在种子腿对应
        profile 的分支里（同 profile、同 `--all-targets`），而消费者用不用 `--release` 必须与
        PROFILES 说的一致——profile 对不上是「命中了也要重编」的头号成因。"""
        seed = _code(_job(CI, self.SEED))
        branches = dict(re.findall(r"(?m)^\s+(dev|release)\)\s+(.+?)\s+;;$", seed))
        assert set(branches) == {"dev", "release"}, branches
        seed_cmds = {prof: _cargo_commands(cmd) for prof, cmd in branches.items()}
        assert seed_cmds["dev"] == {"cargo clippy --all-targets -- -D warnings", "cargo test"}, (
            seed_cmds
        )
        assert seed_cmds["release"] == {"cargo build --release"}, seed_cmds
        for job_id, w in self._consumer_rust_caches():
            cmds = _cargo_commands(_job(CI, job_id))
            assert cmds, f"{job_id} 用了 rust-cache 却没有一条 cargo 编译命令"
            profile = "release" if any("--release" in c for c in cmds) else "dev"
            assert self.PROFILES[w["shared-key"]] == profile, (job_id, w["shared-key"], cmds)
            assert cmds <= seed_cmds[profile], (
                f"{job_id} 的 {sorted(cmds - seed_cmds[profile])} 不在种子 {profile} 分支里——种子存的 target/ 缺它要的那一半"
            )
        assert "${{ matrix.profile }}" in seed, "case 没有按 matrix.profile 分支"
        assert re.search(r"(?m)^\s+working-directory: \$\{\{ matrix\.workspace \}\}$", seed), (
            "cargo 那一步不在 matrix.workspace 里跑——target/ 会落到别处"
        )

    def test_the_cpython_seed_uses_the_consumers_exact_path_and_key_on_exactly_their_oses(self):
        """`actions/cache` 的 path / key 逐字相同（主语是字符串相等，不是「都含 runner.os」）；
        `cpython: true` 的腿的 os 集合 == 两条冒烟腿的 os 集合；三步都由同一个字段开关。"""
        seed_steps = _steps(_job(CI, self.SEED))
        cache = [st for st in seed_steps if re.search(r"(?m)^\s*uses: actions/cache@", st)]
        assert len(cache) == 1, "种子里应恰好一步 actions/cache"
        want = {("build/runtime-cache", TestBuildReuseAndCaches.CPYTHON_KEY)}
        consumers = {
            (w["path"], w["key"])
            for j, st in _uses_steps("actions/cache")
            if j != self.SEED
            for w in [_with_block(st)]
        }
        assert consumers == want, consumers
        w = _with_block(cache[0])
        assert (w["path"], w["key"]) in want, w
        gated = [st for st in seed_steps if re.search(r"(?m)^\s*if: matrix\.cpython == true$", st)]
        assert len(gated) == 3 and cache[0] in gated, [
            _step_name(s) or s.splitlines()[0] for s in gated
        ]
        assert any("scripts/build_worker_runtime.py --clean" in st for st in gated), (
            "种子没有真跑一次 runtime 构建"
        )
        assert any("actions/setup-python@" in st for st in gated), (
            "runtime 构建要用与消费者同版的 setup-python"
        )
        consumer_oses = set().union(
            *(_job_oses(j) for j, _ in _uses_steps("actions/cache") if j != self.SEED)
        )
        seed_oses = {leg["os"] for leg in self._legs() if leg["cpython"] == "true"}
        assert consumer_oses == seed_oses == {"windows-latest", "macos-latest"}, (
            consumer_oses,
            seed_oses,
        )

    def test_the_pnpm_seed_covers_exactly_the_oses_that_have_a_pnpm_consumer(self):
        """开了 `cache: pnpm` 的消费者跑在哪些 os 上，种子就在哪些 os 上真跑一次 `pnpm install`
        （setup-node 的 key 只含 os / arch / 锁 hash，同 os 即同键）；三步同一个字段开关。"""
        consumer_oses: set[str] = set()
        for job_id in TestBuildReuseAndCaches.PNPM_CACHED - {self.SEED}:
            consumer_oses |= _job_oses(job_id)
        seed_oses = {leg["os"] for leg in self._legs() if leg["pnpm"] == "true"}
        # 三个 OS 各一档（不是 _HOSTED_RUNNERS：Intel 镜像只在发行链上用，ci.yml 里没有它）
        assert consumer_oses == seed_oses == set(_RUNNER_OS_LABEL.values()), (
            consumer_oses,
            seed_oses,
        )
        seed_steps = _steps(_job(CI, self.SEED))
        gated = [st for st in seed_steps if re.search(r"(?m)^\s*if: matrix\.pnpm == true$", st)]
        assert len(gated) == 3, [_step_name(s) or s.splitlines()[0] for s in gated]
        assert any("pnpm/action-setup@" in st for st in gated)
        node = [st for st in gated if "actions/setup-node@" in st]
        assert len(node) == 1 and _with_block(node[0]).get("cache") == "pnpm", node
        install = [
            st for st in gated if re.search(r"(?m)^\s*run: pnpm install --frozen-lockfile$", st)
        ]
        assert len(install) == 1 and "working-directory: web" in install[0], install
        assert seed_steps.index(node[0]) < seed_steps.index(install[0]), (
            "setup-node 要在 pnpm install 之前"
        )

    def test_the_linux_only_tauri_prerequisite_is_gated_like_the_consumer(self):
        """desktop-shell 的 Linux 腿要先 apt 装 WebKit/GTK 才编得过；种子的对应步骤条件是
        「src-tauri 且 Linux」，包名与消费者那一步逐字相同（枯了两处的其中一处就是白种）。"""
        seed = _steps(_job(CI, self.SEED))
        apt = [st for st in seed if "apt-get install" in st]
        assert len(apt) == 1, "种子里应恰好一步 apt"
        assert re.search(
            r"(?m)^\s*if: matrix\.workspace == 'src-tauri' && runner\.os == 'Linux'$", apt[0]
        ), apt[0]
        assert "Linux" in _RUNNER_OS_LABEL and _RUNNER_OS_LABEL["Linux"] in {
            leg["os"] for leg in self._legs() if leg["workspace"] == "src-tauri"
        }, "没有一条 src-tauri 的 Linux 种子腿，这一步永远不执行"
        consumer = [st for st in _steps(_job(CI, "desktop-shell")) if "apt-get install" in st]
        assert len(consumer) == 1

        def pkgs(step: str) -> list[str]:
            m = re.search(r"apt-get install[^\n]*\\\n((?:.*\\\n)*.*)$", step, re.M)
            assert m, step
            return sorted(re.sub(r"\\\n", " ", m.group(1)).split())

        assert pkgs(apt[0]) == pkgs(consumer[0]), (pkgs(apt[0]), pkgs(consumer[0]))


# ============================================================ runner 信任区（CI04）
#: GitHub 托管 runner 的名字——**枚举**，不是「不含 self-hosted 就算托管」的否定式。
#: 加一类托管 runner（比如 `ubuntu-24.04-arm`）要回到这里登记一次，顺便被问一句
#: 「它是托管的吗」。`macos-26-intel` 是 GitHub 托管的 Intel 镜像（ADR 0076，只有
#: desktop-tauri.yml 的发行构建用它；那条 workflow 不监听 PR 事件）。
_HOSTED_RUNNERS = frozenset({"ubuntu-latest", "macos-latest", "windows-latest", "macos-26-intel"})

#: 注册 self-hosted runner 时 GitHub 自动打上的标签（`self-hosted` + OS + 架构）。
#: actionlint 认得它们，所以它们不用出现在 `.github/actionlint.yaml` 里；
#: 判「自定义标签集合」时要把它们剪掉。
_BUILTIN_SELF_HOSTED_LABELS = frozenset(
    {"self-hosted", "linux", "windows", "macos", "x64", "arm64", "arm"}
)

#: 「PR 事件」——payload 由不可信的一方决定内容的三种触发。`merge_group` 也算：
#: 队列候选是「最新 main + 前序 PR + 当前 PR」的组合提交，PR 里的 workflow 文件
#: 一样会随之被执行。
_UNTRUSTED_EVENTS = frozenset({"pull_request", "pull_request_target", "merge_group"})

#: 本仓库 workflow 用到的触发事件的闭集。写成正面枚举而不是「不许出现
#: pull_request_target」：否定式会被自己的注释咬到，而闭集在**任何**新事件进来时都红，
#: 逼着写的人到这里登记一次——`pull_request_target` / `issue_comment` /
#: `workflow_run` 这几种「带着更高权限跑不可信输入」的事件，就是靠这一下被拦住的。
_KNOWN_EVENTS = frozenset(
    {"push", "pull_request", "merge_group", "schedule", "workflow_dispatch", "workflow_call"}
)

_ACTIONLINT = ROOT / ".github" / "actionlint.yaml"

#: 派发到私有仓库 ci-infra 的命令开头（F 组，2026-09-16）——与
#: tests/test_release_workflow_contract.py 的 `_DISPATCH_CMD` 同一串；那边钉「谁派发、
#: SHA 从哪来」，这边只钉「派发方的事件也在可信集合里」。
_DISPATCH_CMD = "gh workflow run lab-qualification.yml -R Tavotto/ci-infra"

#: PR B（F-6，2026-09-16）之后公开仓库里**没有**直接 `uses` reusable 的 workflow：
#: `release.yml::lab_release_gate` 改成了派发 + 回调（`dispatch_lab` → ci-infra →
#: `release-publish.yml`）。两个集合一个空、一个全，是 F-8 注销公开仓库 runner 的前提
#: （ADMIN_HANDOFF_RUNNER_POOL.md F.3 / F.4）；谁再把派发改回 `uses`，runner 不在这里，
#: 那个 job 会永远排队。
_REUSABLE_CALLING_WORKFLOWS: frozenset[str] = frozenset()
_DISPATCHING_WORKFLOWS = frozenset({"lab-ci.yml", "release.yml"})


def _top_level_block(text: str, key: str) -> str:
    """顶格键 `key:` 之下所有缩进行（注释已剥）；切不出来当场抛。"""
    lines = _code(text).splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.rstrip() == f"{key}:")
    except StopIteration:
        raise AssertionError(f"切不出顶格的 `{key}:` 块") from None
    body: list[str] = []
    for ln in lines[start + 1 :]:
        if ln.strip() and not ln.startswith(" "):
            break
        body.append(ln)
    assert any(ln.strip() for ln in body), f"`{key}:` 块是空的"
    return "\n".join(body)


def _events_of(text: str) -> set[str]:
    """workflow 监听的事件：`on:` 之下缩进两格的键。"""
    events = set(re.findall(r"(?m)^  ([a-z_]+):", _top_level_block(text, "on")))
    assert events, "`on:` 块里一个事件都读不出来——写成了流式 `on: [push]`？本仓库不用那种写法"
    return events


def _jobs_of(text: str) -> dict[str, str]:
    """`jobs:` 之下缩进两格的键 → 各自的块（与 test_release_workflow_contract 的读法同形）。"""
    parts = re.split(r"(?m)^  ([A-Za-z_][\w-]*):\s*$", _top_level_block(text, "jobs"))
    jobs = {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}
    assert jobs, "`jobs:` 块里一个 job 都没切出来——缩进形状变了？"
    return jobs


def _matrix_os_values(job_block: str) -> set[str]:
    """`runs-on: ${{ matrix.os }}` 时 os 的全部取值：轴 `os: [a, b]` 或 include 条目里的 `os: x`。"""
    values: set[str] = set()
    for axis in re.finditer(r"(?m)^\s+os: \[([^\]]*)\]", job_block):
        values |= {v.strip().strip("\"'") for v in axis.group(1).split(",") if v.strip()}
    for entry in re.finditer(r"(?m)^\s+- (?:\{ *)?os: ([^,}\s]+)", job_block):
        values.add(entry.group(1).strip("\"'"))
    assert values, "runs-on 写的是 `${{ matrix.os }}`，job 里却读不出任何 os 取值——空档"
    return values


def _runs_on_atoms(job_block: str, where: str) -> set[str]:
    """一个 job 的 `runs-on` 展开成「原子」集合。

    四种写法：字面量 / `[a, b]` 列表 / `${{ matrix.os }}`（按矩阵展开）/
    `group:` + `labels:` 映射（记成 `group:<名>` 加各标签）。别的写法一律「认不出」→ 抛，
    不猜。
    """
    m = re.search(r"(?m)^    runs-on:(.*)$", job_block)
    assert m, f"{where}: 读不出 `runs-on:`"
    # `_code` 只剥整行注释；行尾的 `# …` 在这里剥（runs-on 的值里没有引号）。
    value = re.sub(r"\s+#.*$", "", m.group(1)).strip()
    if not value:
        rest = job_block[m.end() :]
        block = "\n".join(
            ln for ln in rest.splitlines() if ln.startswith("      ") or not ln.strip()
        )
        block = re.split(r"(?m)^    \S", block, maxsplit=1)[0]
        group = re.search(r"(?m)^\s+group: (\S+)", block)
        labels = re.search(r"(?m)^\s+labels: \[([^\]]*)\]", block)
        assert group or labels, f"{where}: runs-on 是映射，却既没有 group 也没有 labels"
        atoms: set[str] = set()
        if group:
            atoms.add(f"group:{group.group(1)}")
        if labels:
            atoms |= {v.strip().strip("\"'") for v in labels.group(1).split(",") if v.strip()}
        return atoms
    if value == "${{ matrix.os }}":
        return _matrix_os_values(job_block)
    if value.startswith("[") and value.endswith("]"):
        return {v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()}
    assert re.fullmatch(r"[\w.-]+", value), f"{where}: runs-on 的写法认不出：{value!r}"
    return {value}


def _workflow_texts() -> dict[str, str]:
    """`.github/workflows/` 里的**每一个文件** → 文本，不是「每个 .yml」。

    GitHub 同样认 `.yaml`：只 glob `*.yml` 的话，一个将来新增的 `bypass.yaml` 里可以躺着不带
    `branches: [main]` 的 `pull_request`、或派到 self-hosted 的 job，而这里的每条集合判据
    **照样绿**（与 `tests/test_source_hygiene.py` 的 `_WORKFLOWS` 同一课，#195 P2）。所以扫整个
    目录；目录里出现 GitHub 不当 workflow 的后缀（比如一份 README）当场抛——它不该在这里，
    而且下游的 `on:` 解析对它本来就是「认不出」。
    """
    files = sorted(p for p in WF.iterdir() if p.is_file())
    stray = [p.name for p in files if p.suffix not in (".yml", ".yaml")]
    assert not stray, f"{WF.relative_to(ROOT)}/ 里有 GitHub 不当 workflow 的文件：{stray}——别放这里"
    texts = {p.name: p.read_text(encoding="utf-8") for p in files}
    assert {"ci.yml", "codeql.yml", "_lab-qualification.yml"} <= set(texts), sorted(texts)
    return texts


def _local_reusable(job_block: str) -> str | None:
    """job 级 `uses: ./.github/workflows/<file>` → 文件名；没有就是 None。"""
    m = re.search(r"(?m)^    uses: \./\.github/workflows/([\w.-]+\.yml)$", job_block)
    return m.group(1) if m else None


def _runners_of_workflow(
    name: str, texts: dict[str, str], _seen: frozenset = frozenset()
) -> set[str]:
    """workflow 里**每个** job 会派到的 runner 原子之并——经本仓库可复用 workflow 的 job
    递归到被调用方（`lab-ci.yml::qualify` 自己没有 runs-on，它的 runner 在
    `_lab-qualification.yml` 里）。"""
    assert name not in _seen, f"可复用 workflow 互相调用成环：{name}"
    atoms: set[str] = set()
    for job_id, block in _jobs_of(texts[name]).items():
        callee = _local_reusable(_code(block))
        if callee:
            assert callee in texts, f"{name}::{job_id} 调用了不存在的 {callee}"
            atoms |= _runners_of_workflow(callee, texts, _seen | {name})
        else:
            atoms |= _runs_on_atoms(_code(block), f"{name}::{job_id}")
    assert atoms, f"{name}: 一个 runner 都没读出来"
    return atoms


def _actionlint_custom_labels() -> set[str]:
    """`.github/actionlint.yaml` → `self-hosted-runner.labels` 的集合（只认列表项，不认注释）。"""
    block = _top_level_block(_ACTIONLINT.read_text(encoding="utf-8"), "self-hosted-runner")
    m = re.search(r"(?m)^  labels:\s*$\n((?:^    - .*\n?)+)", block)
    assert m, "actionlint.yaml 里切不出 self-hosted-runner.labels 的列表"
    labels = {ln.strip()[2:].strip() for ln in m.group(1).splitlines() if ln.strip()}
    assert labels, "actionlint.yaml 的 labels 列表是空的"
    return labels


class TestRunnerTrustZones:
    """CI04（2026-09-16）：三个信任区在 workflow 文件层面的静态守卫。

    主语写在前面，免得读的人以为它挡的比实际多：

    * 它量的是 **main 上（本次 checkout 里）的 workflow 文件**——事件 × job × `runs-on`
      （矩阵展开后）的集合。
    * 它挡**不住** PR 自带的 workflow：`pull_request` 事件执行的是 PR 那一版的 yml，本模块
      在那个 run 里根本不在 required 路径上。「同仓库分支的 PR 加一行
      `runs-on: [self-hosted, tavotto-lab]` 会不会被派到实验室真机」这一半由 runner group
      的 workflow 限制 / fork PR 审批策略 / 私有 infra 仓库决定（03_RUNNERS_AND_TRUST §3），
      不是任何测试能决定的——现状与缺口写在
      docs/implementation/ci-foundation/CI04_RUNNER_PILOT.md §2。
    * 它把「未部署的池不进配置」变成机器判据：actionlint 的自定义标签集合 == 仓库里实际
      用到的自托管标签集合，多一个「预留」标签也红——预留标签是下一个人「顺手用一下」
      的入口。

    四条判据一律正面形式（集合 ⊆ 枚举 / 集合 == 集合），不写「不许出现 xxx」。
    """

    @staticmethod
    def _texts() -> dict[str, str]:
        return _workflow_texts()

    def test_untrusted_events_reach_only_hosted_runners(self):
        """监听 pull_request / pull_request_target / merge_group 的每个 workflow，其**全部** job
        （含矩阵展开、含经可复用 workflow 转到的）的 `runs-on` ⊆ 托管 runner 枚举。"""
        texts = self._texts()
        listening = {n for n, t in texts.items() if _events_of(t) & _UNTRUSTED_EVENTS}
        assert {"ci.yml", "codeql.yml"} <= listening, (
            f"两个 Gate workflow 居然不在监听 PR 事件的集合里：{sorted(listening)}——判据量错了对象"
        )
        for name in sorted(listening):
            runners = _runners_of_workflow(name, texts)
            stray = runners - _HOSTED_RUNNERS
            assert not stray, (
                f"{name} 监听 {sorted(_events_of(texts[name]) & _UNTRUSTED_EVENTS)}，"
                f"却有 job 会派到 {sorted(stray)}——不可信代码将跑在常驻真机上"
            )

    def test_the_lab_label_reaches_only_the_reusable_qualification_and_its_trusted_callers(self):
        """`tavotto-lab` 只出现在 `_lab-qualification.yml`（只可 `workflow_call`）。

        通向它的公开仓库 workflow 分两类，各是一个**集合相等**的判据：

        * 直接 `uses` 它的 == `_REUSABLE_CALLING_WORKFLOWS`——PR B 之后是**空集**
          （F 组：实验室 runner 已迁到私有仓库 ci-infra，公开仓库里的 `uses` 只会永远排队；
          集合为空是 F-8 注销公开仓库上 runner 的前提）；
        * 用 `gh workflow run … -R Tavotto/ci-infra` **派发**的 == `_DISPATCHING_WORKFLOWS`
          ——`lab-ci.yml` 与 `release.yml`。

        两类的事件都 ⊆ {push, schedule, workflow_dispatch}：派发方决定了实验室 runner
        会 checkout 哪个 SHA，与直接调用方同一信任等级（SHA 本身的可信判定另有
        test_release_workflow_contract 看住）。
        """
        texts = self._texts()
        users = {n for n, t in texts.items() if "tavotto-lab" in _code(t)}
        assert users == {"_lab-qualification.yml"}, (
            f"`tavotto-lab` 出现在了 {sorted(users)}——资格验证的定义只许有一份，标签也只许在那一份里"
        )
        assert _events_of(texts["_lab-qualification.yml"]) == {"workflow_call"}, (
            "_lab-qualification.yml 只能被调用，不能自己被任何事件触发"
        )
        callers = {
            n
            for n, t in texts.items()
            if "_lab-qualification.yml" in _code(t) and n != "_lab-qualification.yml"
        }
        assert callers == _REUSABLE_CALLING_WORKFLOWS, (
            f"直接 uses reusable 的 workflow 集合变了：{sorted(callers)}"
            f"（期望 {sorted(_REUSABLE_CALLING_WORKFLOWS)}——PR B 之后是空集，改回 uses 会永远排队）"
        )
        dispatchers = {n for n, t in texts.items() if _DISPATCH_CMD in _code(t)}
        assert dispatchers == _DISPATCHING_WORKFLOWS, (
            f"派发 ci-infra 的 workflow 集合变了：{sorted(dispatchers)}（期望 {sorted(_DISPATCHING_WORKFLOWS)}）"
        )
        for name in sorted(callers | dispatchers):
            events = _events_of(texts[name])
            assert events <= {"push", "schedule", "workflow_dispatch"}, (
                f"{name} 监听了 {sorted(events - {'push', 'schedule', 'workflow_dispatch'})}——"
                "它通向实验室 runner，只许维护者才能造成的事件"
            )

    def test_every_workflow_event_is_in_the_known_closed_set(self):
        """每个 workflow 的事件 ⊆ 闭集，且闭集里的每个事件今天都真的有人用（不是一张宽过头的表）。
        `pull_request_target` 不在闭集里——03 §3「不因本计划改用 pull_request_target」。"""
        texts = self._texts()
        seen: set[str] = set()
        for name, text in sorted(texts.items()):
            events = _events_of(text)
            assert events <= _KNOWN_EVENTS, (
                f"{name} 用了闭集之外的事件 {sorted(events - _KNOWN_EVENTS)}——先到 _KNOWN_EVENTS 登记，"
                "顺便回答「它会不会带着更高权限执行不可信输入」"
            )
            seen |= events
        assert seen == _KNOWN_EVENTS, f"闭集与实际用到的不一致：多出 {sorted(_KNOWN_EVENTS - seen)}"

    def test_actionlint_custom_labels_are_exactly_the_self_hosted_labels_in_use(self):
        """`.github/actionlint.yaml` 声明的自定义标签 == 全部 workflow 的 `runs-on` 里实际出现的
        自托管标签（剪掉托管名与注册时自动打的内建标签）。多一个「预留」也红。"""
        texts = self._texts()
        used: set[str] = set()
        for name in texts:
            for job_id, block in _jobs_of(texts[name]).items():
                if _local_reusable(_code(block)):
                    continue
                used |= _runs_on_atoms(_code(block), f"{name}::{job_id}")
        custom = {
            a
            for a in used - _HOSTED_RUNNERS - _BUILTIN_SELF_HOSTED_LABELS
            if not a.startswith("group:")
        }
        assert "tavotto-lab" in custom, (
            f"实验室标签不在用到的集合里：{sorted(used)}——判据量错了对象"
        )
        declared = _actionlint_custom_labels()
        assert custom == declared, (
            f"actionlint 声明 {sorted(declared)}，workflow 实际用 {sorted(custom)}——"
            "未部署的池不进配置：先有真 runner 与真 job，再登记标签"
        )
