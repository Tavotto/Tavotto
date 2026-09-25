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

import json
import os
import re
import shutil
import subprocess
import sys
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
    """枚举不全 = 按不完整的贡献者名单判绿。**这是最坏的失败形态。**

    两种「不全」都见过：`gh api --paginate` 的输出形状随版本而变、`test -s` 拦不住
    截断（文件非空）；以及 #318——`pulls/{n}/commits` 的响应**封顶 250 条**，分页
    也突破不了，于是超过 250 个提交的 PR 永远数不全、永远合不进去。所以数据源换成
    compare（带分页参数时逐页给全），并且取完之后对账，不信任何一端。
    """

    def test_commits_come_from_compare_not_the_capped_pull_endpoint(self, cla_job):
        """`pulls/{n}/commits` 封顶 250（官方文档原话），换回它，#318 就回来了。"""
        code = _code(cla_job)
        assert not re.search(r"pulls/\$PR/commits", code), (
            "不许再从 `pulls/{n}/commits` 取提交：它最多返回 250 条，分页也突破不了（#318）"
        )
        assert re.search(r'cmp="repos/\$REPO/compare/\$base\.\.\.\$head"', code), (
            "提交列表必须取自 `repos/{r}/compare/{base}...{head}`，两端来自同一个 PR 快照"
        )
        assert """gh api --paginate "$cmp?per_page=100" --jq '.commits'""" in code, (
            "compare 必须带显式分页参数逐页取——不带分页参数时它同样封顶 250"
        )

    def test_snapshot_is_read_once_from_the_pull_object(self, cla_job):
        """base / head / 声明的提交数必须出自**同一个**响应：主语是同一时刻的同一个 PR。"""
        code = _code(cla_job)
        snap = (
            """gh api "repos/$REPO/pulls/$PR" --jq '"\\(.base.sha) \\(.head.sha) \\(.commits)"'"""
        )
        assert snap in code, "base / head / commits 必须从同一次 `pulls/{n}` 读取里一起取出"

    def test_workflow_reconciles_every_count(self, cla_job):
        """摘掉任何一处对账，截断 / 重叠 / 比错对象就会被静默判绿。

        **断言写在 workflow 的 bash 里，不推给判定器**——判定器取自默认分支，
        给它加新参数会在同一个 PR 里报 `unrecognized arguments`（见 job 顶部
        那段自举约束的注释）。能在 workflow 里做的断言就别跨那道边界。
        """
        code = _code(cla_job)
        for pair in ('"$got" != "$uniq"', '"$got" != "$total"', '"$got" != "$want"'):
            assert pair in code, f"少了一处对账：{pair}"
        assert "--jq '.total_commits'" in code, "compare 自报的总数要当对照组"
        assert 'grep -qx "$head" "$shas"' in code, "head SHA 必须在枚举结果里"
        assert "exit 1" in code, "对不上必须让这一步失败，不能只打印警告"

    def test_judge_recounts_what_it_parsed(self, cla_job):
        """判定器按自己解析出的条数再核一遍——参数在 main 上早就有，不跨自举边界。"""
        code = _code(cla_job)
        assert 'echo "CLA_EXPECTED_COMMITS=$want" >> "$GITHUB_ENV"' in code
        assert '--expected-commits "$CLA_EXPECTED_COMMITS"' in code

    def test_workflow_does_not_use_slurp(self, cla_job):
        """`--slurp` 把每页包成一层，产出数组的数组——它是错的解法，不是修法。"""
        assert "--slurp" not in _code(cla_job), (
            "不要给数组端点加 --slurp：它产出数组的数组，判定器反而要额外兼容"
        )


# ─────────────────────────────────────────────── 收集那一步原样跑（#318 的 >250 路径）
#
# 上面的形状判据证明「写了对账」，证明不了「对账在 251 个提交上真的给出结论」。
# 这里把 ci.yml 里「收集这个 PR 的贡献者」与「判定」两步的 `run: |` **原文**交给
# bash 执行，`gh` 换成一个按真实 API 行为建模的替身（`pulls/{n}/commits` 封顶 250、
# compare 不带分页参数封顶 250、带了就逐页给全；`--paginate` 没写 per_page 时 gh 自己
# 补 100——本机 gh 2.63 / 2.97 实测），`--jq` 交给真 jq。判定器是本树的
# scripts/ci/cla_gate.py 副本，放在 $TRUSTED 下，与 job 取默认分支副本落的位置相同。
#
# 前提写在这里：替身的「封顶 250 / 逐页」是按官方 REST 描述与 2026-09-24 对本仓库
# `archive/ui-audit-pre-squash`（252 个提交）的实测建的；GitHub 改了这两条行为，
# 替身不会自己知道。
#
# 两步在 ci.yml 里 `runs-on: ubuntu-latest`，bash + jq 是它们唯一的执行环境；Windows 上
# PATH 里的 bash 是 WSL 启动器（见 tests/test_update_chain_gates.py 的同名说明），如实 skip。
_NEEDS_BASH_AND_JQ = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None or shutil.which("jq") is None,
    reason="收集那一步只在 ubuntu-latest 的 bash + jq 里执行；本机没有可用的 bash / jq",
)

#: gh 的替身。状态从 $FAKE_GH_STATE 读：pr 号、pull 对象、全部提交、total_commits、faults。
_FAKE_GH = r"""
import json, os, subprocess, sys
from urllib.parse import parse_qs, urlsplit

st = json.load(open(os.environ["FAKE_GH_STATE"], encoding="utf-8"))
args, paginate, jq, path = sys.argv[1:], False, None, None
assert args and args[0] == "api", args
i = 1
while i < len(args):
    if args[i] == "--paginate":
        paginate = True
    elif args[i] == "--jq":
        i += 1
        jq = args[i]
    elif path is None:
        path = args[i]
    else:
        sys.exit(f"fake gh: 认不出的参数 {args[i]}")
    i += 1

def emit(doc):
    if jq is None:
        sys.stdout.write(json.dumps(doc))
        return
    r = subprocess.run(["jq", "-rc", jq], input=json.dumps(doc), capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"fake gh: jq 失败：{r.stderr}")
    sys.stdout.write(r.stdout)

def paged(items, q, cap_without_params):
    if paginate and "per_page" not in q:
        q["per_page"] = ["100"]  # gh --paginate 自己补 per_page=100
    if "per_page" not in q and "page" not in q:
        return [items[:cap_without_params]]
    k, start = int(q.get("per_page", ["30"])[0]), int(q.get("page", ["1"])[0])
    pages = [items[j : j + k] for j in range(0, len(items), k)] or [[]]
    return pages[start - 1 :] if paginate else pages[start - 1 : start]

u = urlsplit(path)
q = parse_qs(u.query)
commits, faults = st["commits"], st.get("faults", [])
if u.path.endswith(f"/pulls/{st['pr']}"):
    emit(st["pull"])
elif "/compare/" in u.path:
    pages = paged(commits, q, 250)
    if paginate and "drop_last_page" in faults:
        pages = pages[:-1]
    if paginate and "overlap_pages" in faults:
        pages[1] = pages[0]  # 第二页重发了第一页；head 所在的末页还在——只有去重看得见
    for p in pages:
        emit({"total_commits": st.get("total_commits", len(commits)), "commits": p})
elif u.path.endswith(f"/pulls/{st['pr']}/commits"):
    for p in paged(commits[:250], q, 250):  # 官方：Lists a maximum of 250 commits
        emit(p)
else:
    sys.exit(f"fake gh: 未建模的端点 {path}")
"""


def _step_script(step_name: str) -> str:
    """cla-check 里某一步 `run: |` 的原文（允许空行）——要执行的就是它，不复刻。"""
    block = _job(CI, CLA_JOB)
    m = re.search(
        rf"(?ms)^      - name: {re.escape(step_name)}\n.*?^        run: \|\n((?:(?:          [^\n]*)?\n)+)",
        block,
    )
    assert m, f"cla-check 里读不出「{step_name}」的 run: | 块"
    return "\n".join(ln[10:] for ln in m.group(1).splitlines()) + "\n"


def _commit(i: int, login: str = "erwanjun") -> dict:
    return {
        "sha": f"{i + 1:040x}",
        "author": {"login": login},
        "commit": {
            "author": {"name": login, "email": f"{login}@users.noreply.github.com"},
            "message": f"c{i}",
        },
    }


@_NEEDS_BASH_AND_JQ
class TestClaCollectStepRunsForReal:
    PR = 318
    COLLECT = "收集这个 PR 的贡献者（只读元数据）"

    def _setup(self, tmp_path: Path, commits: list, **over) -> tuple[dict, Path]:
        work = tmp_path / "w"
        (work / "bin").mkdir(parents=True)
        (work / "runner-temp").mkdir()
        gh = work / "bin" / "gh"
        gh.write_text(f"#!{sys.executable}\n{_FAKE_GH}", encoding="utf-8")
        gh.chmod(0o755)
        (work / "bin" / "python3").symlink_to(sys.executable)
        state = {
            "pr": self.PR,
            "commits": commits,
            "pull": {
                "base": {"sha": "b" * 40},
                "head": {"sha": over.pop("head", commits[-1]["sha"])},
                "commits": over.pop("declared", len(commits)),
            },
            **over,
        }
        (work / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (work / "github-env").write_text("", encoding="utf-8")
        env = {
            "PATH": f"{work / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
            "RUNNER_TEMP": str(work / "runner-temp"),
            "GITHUB_ENV": str(work / "github-env"),
            "GH_TOKEN": "fake",
            "REPO": "o/r",
            "PR": str(self.PR),
            "FAKE_GH_STATE": str(work / "state.json"),
        }
        return env, work

    @staticmethod
    def _bash(script: str, env: dict, cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [shutil.which("bash"), "-c", script],
            env=env,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def _collect(self, tmp_path: Path, commits: list, *, script: str | None = None, **over):
        env, work = self._setup(tmp_path, commits, **over)
        proc = self._bash(script or _step_script(self.COLLECT), env, work)
        exported = dict(
            ln.split("=", 1)
            for ln in (work / "github-env").read_text(encoding="utf-8").splitlines()
            if "=" in ln
        )
        return proc, env, work, exported

    def _judge(self, env: dict, work: Path, exported: dict) -> tuple[int, dict]:
        trusted = work / "trusted-cla"
        for rel in (
            "scripts/ci/cla_gate.py",
            ".github/cla-policy.json",
            "docs/legal/CLA_INDIVIDUAL.md",
            "docs/legal/CLA_CORPORATE.md",
        ):
            (trusted / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / rel, trusted / rel)
        script = _step_script("判定").replace("${{ github.event_name }}", "pull_request")
        assert "${{" not in script, "判定那一步多了新的表达式，这里要同步渲染"
        env = {**env, **exported, "TRUSTED": str(trusted), "PR_AUTHOR": "erwanjun"}
        proc = self._bash(script, env, work)
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
        assert len(lines) == 1, f"判定器没有恒输出一行 JSON：{proc.stdout}\n{proc.stderr}"
        return proc.returncode, json.loads(lines[0])

    def test_more_than_250_commits_reach_a_verdict(self, tmp_path):
        """#318 验收一：251 个提交，门禁给出结论（这里是绿），不是拒判。"""
        commits = [_commit(i) for i in range(251)]
        proc, env, work, exported = self._collect(tmp_path, commits)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "提交数核对通过：251 / 251" in proc.stdout
        assert exported.get("CLA_EXPECTED_COMMITS") == "251"
        rc, verdict = self._judge(env, work, exported)
        assert (rc, verdict["status"]) == (0, "success"), verdict

    def test_unsigned_identity_past_the_old_cap_turns_it_red(self, tmp_path):
        """#318 验收二：第 251 个提交（旧端点根本取不到的那一条）换成未签署的身份，必须红。"""
        commits = [_commit(i) for i in range(250)] + [_commit(250, login="outsider")]
        proc, env, work, exported = self._collect(tmp_path, commits)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        rc, verdict = self._judge(env, work, exported)
        assert (rc, verdict["status"]) == (1, "failure"), verdict
        missing = [r["login"] for r in verdict["contributors"] if r["verdict"] == "missing"]
        assert missing == ["outsider"], verdict

    @pytest.mark.parametrize(
        "over",
        [
            pytest.param({"faults": ["drop_last_page"]}, id="last-page-lost"),
            pytest.param({"faults": ["overlap_pages"]}, id="pages-overlap"),
            pytest.param({"total_commits": 252}, id="compare-total-disagrees"),
            pytest.param({"declared": 252}, id="pr-declares-more"),
            pytest.param({"head": "f" * 40}, id="head-not-enumerated"),
        ],
    )
    def test_incomplete_or_wrong_enumeration_refuses(self, tmp_path, over):
        """任何一处对不上：这一步红、不导出条数——判定器不会拿到一份残缺名单。"""
        commits = [_commit(i) for i in range(251)]
        proc, _, _, exported = self._collect(tmp_path, commits, **over)
        assert proc.returncode != 0, proc.stdout
        assert "::error::" in proc.stdout, proc.stdout + proc.stderr
        assert "CLA_EXPECTED_COMMITS" not in exported

    def test_the_capped_pull_endpoint_is_what_broke(self, tmp_path):
        """替身的保真度自检，也是 #318 的原样重现：把数据源换回 `pulls/{n}/commits`，
        251 个提交的 PR 必须被拒判——替身要是没把 250 的顶建出来，上面那几条绿就不作数。"""
        target = """gh api --paginate "$cmp?per_page=100" --jq '.commits' > "$out"\n"""
        script = _step_script(self.COLLECT)
        assert script.count(target) == 1, "变异的落点不在了——先改这里再谈结论"
        mutated = script.replace(
            target, 'gh api --paginate "repos/$REPO/pulls/$PR/commits" > "$out"\n'
        )
        proc, _, _, exported = self._collect(
            tmp_path, [_commit(i) for i in range(251)], script=mutated
        )
        assert proc.returncode != 0, proc.stdout
        assert "取到 250 个提交" in proc.stdout, proc.stdout + proc.stderr
        assert "CLA_EXPECTED_COMMITS" not in exported


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
