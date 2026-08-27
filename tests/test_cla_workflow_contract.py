"""`cla-check` 这个 CI job 的形状与安全契约。

**为什么单独一个文件、而且与 job 在同一个 PR 落地**：在 job 还不存在的树上
断言它的形状，只会得到一个必红的空门禁。判据必须和它守的东西一起来。

这些判据钉的都是「坏掉之后不会有任何别的测试红、只会在线上锁死或漏验」的形状：

* CLA workflow 在 pull_request 上不跑 → 门禁形同虚设，而且全绿；
* CLA job 在 merge_group 上被跳过 → `aggregate_gate --mode fast` 把 skipped 当
  失败，`CI fast gate` 永久红，**整个仓库合不进任何东西**；
* privileged 触发器 + checkout PR 代码 → fork PR 能在写权限下执行任意代码；
* 判定器改成跑本 revision 的副本 → PR 供给了审判自己的那把尺子；
* 第三方 action 从 SHA 退回浮动 tag → 供应链面重新打开。

与 tests/test_merge_queue_workflows.py 同一条纪律：**不用 PyYAML**（它不在
`.venv` 里，importorskip 会让整个模块静默跳过——那正是空门禁），用只认本仓库
缩进形状的字符串判据，解析不出预期形状时当场抛。

判据本身做过反证（见 PR 描述的 mutation 表）：每一条都手工破坏过一次。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
CI = (WF / "ci.yml").read_text(encoding="utf-8")
LEGAL = ROOT / "docs" / "legal"
POLICY_PATH = ROOT / ".github" / "cla-policy.json"

#: CLA job 的 id 与 check run 名字。改名要同步 ci.yml 与这里。
CLA_JOB = "cla-check"
CLA_JOB_NAME = "Contributor licence (CLA)"


def _code(text: str) -> str:
    """剥掉注释行——判据只看会被执行的部分。

    这条很重要：注释里出现 `pull_request_target` 或 `actions/checkout`（比如
    解释「为什么**不**用它」）不该让安全判据红，而真写在 steps 里必须红。
    """
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _job(text: str, job_id: str) -> str:
    """按缩进切出一个 job 块；切不出来当场抛（安静的空判据比没有更坏）。"""
    m = re.search(rf"(?m)^  {re.escape(job_id)}:\n(.*?)(?=^  [\w-]+:|\Z)", text, re.S)
    assert m, f"ci.yml 里切不出 job `{job_id}`——缩进形状变了？"
    return m.group(0)


@pytest.fixture(scope="module")
def cla_job():
    return _job(CI, CLA_JOB)


class TestClaWorkflowContract:
    def test_job_exists_with_a_pinned_name(self, cla_job):
        assert f"name: {CLA_JOB_NAME}" in cla_job, (
            f"CLA cla_job 的 name 必须固定为 `{CLA_JOB_NAME}`"
        )

    def test_runs_on_pull_request(self, cla_job):
        """PR 路径必须执行——不跑的门禁是全绿的门禁。"""
        code = _code(cla_job)
        m = re.search(r"(?m)^\s+if:\s*(.+)$", code)
        assert m, "CLA cla_job 读不出 if 条件"
        assert "pull_request" in m.group(1), "CLA cla_job 必须在 pull_request 上跑"

    def test_runs_on_merge_group_too(self, cla_job):
        """**这条是仓库能不能合并的开关。**

        `aggregate_gate.py --mode fast` 把 skipped 一律当失败。CLA cla_job 一旦在
        merge_group 上被跳过，`CI fast gate` 就永久红，队列里谁也合不进去。
        """
        code = _code(cla_job)
        m = re.search(r"(?m)^\s+if:\s*(.+)$", code)
        assert m and "merge_group" in m.group(1), (
            "CLA cla_job 必须在 merge_group 上也跑——被跳过会把 CI fast gate 卡死"
        )

    def test_feeds_the_fast_gate_without_a_new_required_context(self):
        """接进既有 Gate 的 needs + --required，**不新增第四个 required context**。"""
        gate_job = _job(CI, "ci-fast-gate")
        needs = re.search(r"(?m)^\s+needs:\s*\[([^\]]+)\]", gate_job)
        required = re.search(r"--required\s+([\w,\-]+)", gate_job)
        assert needs and required, "fast gate 读不出 needs / --required"
        needs_set = {s.strip() for s in needs.group(1).split(",")}
        req_set = set(required.group(1).split(","))
        assert CLA_JOB in needs_set, f"`{CLA_JOB}` 不在 fast gate 的 needs 里"
        assert CLA_JOB in req_set, f"`{CLA_JOB}` 不在 fast gate 的 --required 里"
        assert needs_set == req_set, (
            f"fast gate 的 needs 与 --required 漂开了：{needs_set ^ req_set}"
        )

    def test_gate_script_and_policy_exist(self):
        """接线的前提：判定器与政策必须已经在树里（内容层 PR 先落地）。"""
        for f in (ROOT / "scripts" / "ci" / "cla_gate.py", POLICY_PATH):
            assert f.is_file(), f"CLA 判定链缺文件：{f}"


class TestClaPaginationContract:
    """分页只取到第一页 = 按不完整的贡献者名单判绿。**这是最坏的失败形态。**

    `gh api --paginate` 的输出形状随版本而变（gh 2.97 合并数组，`--help` 却写
    「Each page is a separate JSON array」），而 `test -s` 拦不住截断——文件非空。
    所以判定器不信分页，核数量：PR 自己声明的提交数对不上就红。
    """

    def test_workflow_verifies_the_commit_count_itself(self, cla_job):
        """摘掉这段核对，41 提交的 PR 就会被静默少判。

        **断言写在 workflow 的 bash 里，不推给判定器**——判定器取自默认分支，
        给它加新参数会在同一个 PR 里报 `unrecognized arguments`（见 job 顶部
        那段自举约束的注释）。能在 workflow 里做的断言就别跨那道边界。
        """
        code = _code(cla_job)
        assert "pulls/$PR" in code and ".commits" in code, (
            "workflow 必须取 PR 自身的 `commits` 字段当对照组"
        )
        assert re.search(r'--jq\s+[\'"]\.\[\]\.sha', code), (
            "计数要用 `--jq '.[].sha'` **流式**输出：它按页应用过滤器，"
            "与「gh 有没有把各页合并成一个数组」无关"
        )
        assert re.search(r'\$got.*!=.*\$want|\$want.*!=.*\$got', code), (
            "取到的条数必须与 PR 声明的条数比对——不比就等于信任分页"
        )
        assert "exit 1" in code, "对不上必须让这一步失败，不能只打印警告"

    def test_workflow_does_not_use_slurp(self, cla_job):
        """`--slurp` 把每页包成一层，产出数组的数组——它是错的解法，不是修法。"""
        assert "--slurp" not in _code(cla_job), (
            "不要给数组端点加 --slurp：它产出数组的数组，判定器反而要额外兼容"
        )


class TestClaWorkflowSecurity:
    def test_does_not_use_pull_request_target(self, cla_job):
        """privileged 触发器会带来写 token 与 secret；这个 cla_job 不需要它们。"""
        assert "pull_request_target" not in _code(cla_job), (
            "CLA cla_job 不许用 pull_request_target——它不需要写权限，"
            "用了就把整类 fork PR 提权风险请了进来"
        )

    def test_workflow_is_not_triggered_by_pull_request_target(self):
        header = CI.split("jobs:", 1)[0]
        assert "pull_request_target" not in _code(header), "ci.yml 顶层不许监听 pull_request_target"

    def test_does_not_checkout_pr_code(self, cla_job):
        """判定的输入全部取自默认分支；被审的树不能参与判定自己。"""
        assert "actions/checkout" not in _code(cla_job), (
            "CLA cla_job 不许 checkout——判定器/政策/签署记录全部取自默认分支"
        )

    def test_does_not_execute_anything_from_the_pr(self, cla_job):
        code = _code(cla_job)
        # 判定器必须来自默认分支拉下来的可信副本（$TRUSTED），
        # 绝不是 `python scripts/ci/cla_gate.py` 这种相对本次 checkout 的路径。
        assert re.search(r"python3\s+-I\s+\"\$TRUSTED/scripts/ci/cla_gate\.py\"", code), (
            "判定器必须从默认分支取下来的 $TRUSTED 副本执行，且带 -I 隔离"
        )
        assert not re.search(r"(?m)^\s+run:.*\bpython3?\s+scripts/", code), (
            "CLA cla_job 不许执行本次 revision 里的脚本"
        )

    def test_trusted_inputs_come_from_the_default_branch(self, cla_job):
        code = _code(cla_job)
        assert "default_branch" in code, "可信输入必须显式取自 default_branch"
        for path in (
            "scripts/ci/cla_gate.py",
            ".github/cla-policy.json",
            "docs/legal/CLA_INDIVIDUAL.md",
        ):
            assert path in code, f"可信输入里少了 {path}"
        assert "cla-signatures.json" not in code, (
            "仓库不保存签署事实——workflow 不该再去取一份 signer 名单"
        )

    def test_permissions_are_minimal(self, cla_job):
        code = _code(cla_job)
        m = re.search(r"(?m)^\s+permissions:\n((?:\s+\w[\w-]*:\s*\w+\n)+)", code)
        assert m, "CLA cla_job 必须显式声明 permissions"
        perms = dict(re.findall(r"(\w[\w-]*):\s*(\w+)", m.group(1)))
        assert perms == {"contents": "read", "pull-requests": "read"}, (
            f"CLA cla_job 的权限必须恰好是两个只读项，实际：{perms}"
        )
        assert "write-all" not in code
        for scope in ("contents: write", "pull-requests: write", "issues: write"):
            assert scope not in code, f"CLA cla_job 不该有 `{scope}`"

    def test_third_party_actions_are_pinned_to_full_sha(self, cla_job):
        """浮动 tag 可以被重新指向新代码；SHA 不能。

        本 cla_job 目前一个第三方 action 都不用（只用 runner 自带的 gh）。这条判据
        是为「将来有人加一个」准备的——加的时候必须钉 40 位 SHA。
        """
        uses = re.findall(r"(?m)^\s+-?\s*uses:\s*(\S+)", _code(cla_job))
        for ref in uses:
            if ref.startswith("./"):
                continue
            assert re.search(r"@[0-9a-f]{40}$", ref), (
                f"CLA cla_job 里的第三方 action `{ref}` 必须钉到 40 位 commit SHA，"
                f"不能是 @main / @v1 / @v2"
            )

    def test_no_secrets_are_referenced(self, cla_job):
        assert "secrets." not in _code(cla_job), "CLA cla_job 不该用任何 secret——它只读公开元数据"
