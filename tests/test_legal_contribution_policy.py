"""法律与贡献者治理的结构性契约（LICENSE / CLA / 商标 / CLA workflow）。

这些判据钉的都是「坏掉之后不会有任何别的测试红、只会在**将来某次法律审查**
才暴露」的形状——那种缺陷的发现成本比一次 CI 红高几个数量级：

* 社区版许可证被悄悄改宽（AGPL-3.0-only → -or-later）→ 权利边界变了，没人看得见；
* CONTRIBUTING 掉了 CLA 链接 → 外部贡献者按旧规则提交，权利链当场断掉；
* CLA workflow 在 pull_request 上不跑 → 门禁形同虚设，而且全绿；
* CLA job 在 merge_group 上被跳过 → `aggregate_gate --mode fast` 把 skipped 当
  失败，`CI fast gate` 永久红，**整个仓库合不进任何东西**；
* privileged 触发器 + checkout PR 代码 → fork PR 能在写权限下执行任意代码；
* 第三方 action 从 SHA 退回浮动 tag → 供应链面重新打开；
* 改了 CLA 正文却不 bump 版本/哈希 → 旧签名被静默套用到新文本上；
* 出现 ® → 主张一个并不存在的注册。

与 tests/test_merge_queue_workflows.py 同一条纪律：**不用 PyYAML**（它不在
`.venv` 里，importorskip 会让整个模块静默跳过——那正是空门禁），用只认本仓库
缩进形状的字符串判据，解析不出预期形状时当场抛。

判据本身也做过反证（见 PR 描述的 mutation 表）：每一条都手工破坏过一次，
确认它真的红。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
CI = (WF / "ci.yml").read_text(encoding="utf-8")

LEGAL = ROOT / "docs" / "legal"
POLICY_PATH = ROOT / ".github" / "cla-policy.json"
PROVENANCE = LEGAL / "IP_PROVENANCE.md"

#: 首次审计的基线。它是**历史记录**，永远留在 IP_PROVENANCE 里；
#: 但它不许再被当成「当前基线」——那正是本轮要修的陈旧断言。
INITIAL_BASELINE = "aaa065f298ac4ce8a66a3482786bedf516a1154b"

#: 社区版许可证的唯一正确取值。**不是** -or-later，也不是 dual。
LICENCE_ID = "AGPL-3.0-only"

#: CLA job 的 id 与 check run 名字。改名要同步 ci.yml 与这里。
CLA_JOB = "cla-check"
CLA_JOB_NAME = "Contributor licence (CLA)"


def _code(text: str) -> str:
    """剥掉注释行——判据只看会被执行的部分。

    这条很重要：注释里出现 `pull_request_target` 或 `actions/checkout`（比如
    解释「为什么**不**用它」）不该让安全判据红，而真写在 steps 里必须红。
    """
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.lstrip().startswith("#"))


def _job(text: str, job_id: str) -> str:
    """按缩进切出一个 job 块；切不出来当场抛（安静的空判据比没有更坏）。"""
    m = re.search(rf"(?m)^  {re.escape(job_id)}:\n(.*?)(?=^  [\w-]+:|\Z)",
                  text, re.S)
    assert m, f"ci.yml 里切不出 job `{job_id}`——缩进形状变了？"
    return m.group(0)


def _load_gate():
    """按路径加载判定器（scripts/ 不是包，也不该为了测试变成包）。"""
    path = ROOT / "scripts" / "ci" / "cla_gate.py"
    spec = importlib.util.spec_from_file_location("cla_gate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load_gate()


@pytest.fixture(scope="module")
def policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


# ═════════════════════════════════════════════ 1. 公开许可证不变式
class TestPublicLicence:
    def test_license_file_is_agpl_v3(self):
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        assert "GNU AFFERO GENERAL PUBLIC LICENSE" in text
        assert "Version 3, 19 November 2007" in text

    def test_pyproject_declares_agpl_only(self):
        """`-or-later` 会把「将来的 AGPL 版本」也许诺出去——那是权利边界的改变。"""
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'(?m)^license\s*=\s*"([^"]+)"', pyproject)
        assert m, "pyproject 里读不出 license"
        assert m.group(1) == LICENCE_ID, (
            f"pyproject 的 license 是 {m.group(1)}，必须是 {LICENCE_ID}")

    def test_pyproject_ships_the_licence_file(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert re.search(r'(?m)^license-files\s*=\s*\[\s*"LICENSE"\s*\]', pyproject), (
            "wheel/sdist 必须带上 LICENSE")

    @pytest.mark.parametrize("manifest", [
        "workerd/Cargo.toml", "src-tauri/Cargo.toml",
    ])
    def test_rust_crates_declare_agpl_only(self, manifest):
        text = (ROOT / manifest).read_text(encoding="utf-8")
        m = re.search(r'(?m)^license\s*=\s*"([^"]+)"', text)
        assert m, f"{manifest} 里读不出 license"
        assert m.group(1) == LICENCE_ID

    def test_no_second_licence_file_at_root(self):
        """「官方另有商业授权权利」与「公开仓库同时摆两份 LICENSE」不是一回事。"""
        strays = [p.name for p in ROOT.glob("LICENSE*")
                  if p.name not in ("LICENSE",)]
        assert not strays, f"仓库根出现了第二份许可证文件：{strays}"


# ═════════════════════════════════════════════ 2. CONTRIBUTING 不变式
@pytest.fixture(scope="module")
def contributing():
    return (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cla_job():
    return _job(CI, CLA_JOB)


class TestContributing:
    def test_links_both_agreements(self, contributing):
        for target in ("docs/legal/CLA_INDIVIDUAL.md", "docs/legal/CLA_CORPORATE.md"):
            assert target in contributing, f"CONTRIBUTING 必须链到 {target}"

    def test_says_contributor_keeps_copyright(self, contributing):
        assert re.search(r"(?i)keep\s+the\s+copyright", contributing), (
            "CONTRIBUTING 必须明说贡献者保留著作权——这是本模型的核心承诺")

    def test_states_the_community_licence(self, contributing):
        assert LICENCE_ID in contributing

    def test_does_not_claim_dco_replaces_cla(self, contributing):
        """DCO 证明来源，不是著作权授权。把它当 CLA 用是这轮最要防的误解。"""
        bad = re.search(
            r"(?i)DCO[^.\n]{0,80}(replaces?|instead of|substitute for|is a|"
            r"serves? as)[^.\n]{0,40}CLA", contributing)
        assert not bad, f"CONTRIBUTING 把 DCO 说成了 CLA 的替代：{bad.group(0)!r}"

    def test_requires_cla_for_pull_requests_not_issues(self, contributing):
        assert re.search(r"(?is)issue.{0,200}\|\s*\*\*No\*\*", contributing), (
            "CONTRIBUTING 应说明 issue / 讨论不要求 CLA")
        assert re.search(r"(?is)pull request.{0,200}\|\s*\*\*Yes\*\*", contributing), (
            "CONTRIBUTING 应说明 PR 一律要求 CLA")

    def test_points_employer_owned_work_at_corporate_cla(self, contributing):
        assert re.search(r"(?i)employer", contributing), (
            "CONTRIBUTING 必须处理「成果归雇主」的情形")


# ═════════════════════════════════════════════ 3. README 不变式（中英同步）
class TestReadmes:
    @pytest.mark.parametrize("name", ["README.md", "README.zh-CN.md"])
    def test_states_agpl_and_points_at_legal_docs(self, name):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert LICENCE_ID in text, f"{name} 必须写明社区版许可证"
        assert "docs/legal/" in text, f"{name} 必须指向 docs/legal/"
        assert "CONTRIBUTING.md" in text
        assert "TRADEMARKS.md" in text, f"{name} 必须指向商标政策"

    @pytest.mark.parametrize("name", ["README.md", "README.zh-CN.md"])
    def test_does_not_announce_a_product_that_does_not_exist(self, name):
        """没有 Pro 就不许写 Pro。「可以另行授权」是权利，「已经有产品」是承诺。"""
        text = (ROOT / name).read_text(encoding="utf-8")
        bad = re.search(r"(?i)Tavotto\s+Pro\b", text)
        assert not bad, f"{name} 宣称了并不存在的商业版：{bad.group(0)!r}"


# ═════════════════════════════════════════════ 4. 商标不变式
class TestTrademark:
    #: 允许出现 ® 的白名单。**现在是空的**：Tavotto 未注册。
    #: 将来某个法域真注册下来了，在这里显式开口，并同步 TRADEMARKS.md。
    REGISTERED_MARK_ALLOWED: tuple[str, ...] = ()

    def test_policy_file_exists_and_covers_the_basics(self):
        text = (ROOT / "TRADEMARKS.md").read_text(encoding="utf-8")
        for topic in (r"(?i)fork", r"(?i)logo", r"(?i)not a registered trademark"):
            assert re.search(topic, text), f"TRADEMARKS.md 缺少 {topic} 这一节"
        assert re.search(r"(?i)licence.{0,40}not.{0,40}trademark|"
                         r"does not cover trademarks", text), (
            "TRADEMARKS.md 必须讲清「著作权许可 ≠ 商标许可」")

    @pytest.mark.parametrize("name", [
        "README.md", "README.zh-CN.md", "TRADEMARKS.md", "CONTRIBUTING.md",
    ])
    def test_no_registered_symbol(self, name):
        """未注册就用 ® 是在主张一个不存在的注册。"""
        if name in self.REGISTERED_MARK_ALLOWED:
            pytest.skip("显式配置为已注册")
        text = (ROOT / name).read_text(encoding="utf-8")
        # ® 出现在讲「不许用 ®」的句子里是合法的；判据只看 `Tavotto®` 这种真主张。
        bad = re.search(r"Tavotto\s*®", text)
        assert not bad, f"{name} 里出现了 Tavotto®，但本项目未注册"

    def test_readme_trademark_wording_matches_policy(self):
        """README 的措辞必须与 TRADEMARKS.md 一致：未注册 + ™。"""
        for name in ("README.md", "README.zh-CN.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            assert "Tavotto™" in text
            assert re.search(r"(?i)unregistered|未注册", text), (
                f"{name} 必须说明 Tavotto 是未注册商标")


# ═════════════════════════════════════════════ 5. CLA workflow 契约
class TestClaWorkflowContract:
    def test_job_exists_with_a_pinned_name(self, cla_job):
        assert f"name: {CLA_JOB_NAME}" in cla_job, (
            f"CLA cla_job 的 name 必须固定为 `{CLA_JOB_NAME}`")

    def test_runs_on_pull_request(self, cla_job):
        """PR 路径必须执行——不跑的门禁是全绿的门禁。"""
        code = _code(cla_job)
        m = re.search(r"(?m)^\s+if:\s*(.+)$", code)
        assert m, "CLA cla_job 读不出 if 条件"
        assert "pull_request" in m.group(1), (
            "CLA cla_job 必须在 pull_request 上跑")

    def test_runs_on_merge_group_too(self, cla_job):
        """**这条是仓库能不能合并的开关。**

        `aggregate_gate.py --mode fast` 把 skipped 一律当失败。CLA cla_job 一旦在
        merge_group 上被跳过，`CI fast gate` 就永久红，队列里谁也合不进去。
        """
        code = _code(cla_job)
        m = re.search(r"(?m)^\s+if:\s*(.+)$", code)
        assert m and "merge_group" in m.group(1), (
            "CLA cla_job 必须在 merge_group 上也跑——被跳过会把 CI fast gate 卡死")

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
            f"fast gate 的 needs 与 --required 漂开了：{needs_set ^ req_set}")

    def test_gate_script_and_policy_exist(self):
        for f in (ROOT / "scripts" / "ci" / "cla_gate.py", POLICY_PATH):
            assert f.is_file(), f"CLA 判定链缺文件：{f}"


# ═════════════════════════════════════════════ 6. CLA workflow 安全边界
class TestClaWorkflowSecurity:
    def test_does_not_use_pull_request_target(self, cla_job):
        """privileged 触发器会带来写 token 与 secret；这个 cla_job 不需要它们。"""
        assert "pull_request_target" not in _code(cla_job), (
            "CLA cla_job 不许用 pull_request_target——它不需要写权限，"
            "用了就把整类 fork PR 提权风险请了进来")

    def test_workflow_is_not_triggered_by_pull_request_target(self):
        header = CI.split("jobs:", 1)[0]
        assert "pull_request_target" not in _code(header), (
            "ci.yml 顶层不许监听 pull_request_target")

    def test_does_not_checkout_pr_code(self, cla_job):
        """判定的输入全部取自默认分支；被审的树不能参与判定自己。"""
        assert "actions/checkout" not in _code(cla_job), (
            "CLA cla_job 不许 checkout——判定器/政策/签署记录全部取自默认分支")

    def test_does_not_execute_anything_from_the_pr(self, cla_job):
        code = _code(cla_job)
        # 判定器必须来自默认分支拉下来的可信副本（$TRUSTED），
        # 绝不是 `python scripts/ci/cla_gate.py` 这种相对本次 checkout 的路径。
        assert re.search(r"python3\s+-I\s+\"\$TRUSTED/scripts/ci/cla_gate\.py\"", code), (
            "判定器必须从默认分支取下来的 $TRUSTED 副本执行，且带 -I 隔离")
        assert not re.search(r"(?m)^\s+run:.*\bpython3?\s+scripts/", code), (
            "CLA cla_job 不许执行本次 revision 里的脚本")

    def test_trusted_inputs_come_from_the_default_branch(self, cla_job):
        code = _code(cla_job)
        assert "default_branch" in code, (
            "可信输入必须显式取自 default_branch")
        for path in ("scripts/ci/cla_gate.py", ".github/cla-policy.json",
                     "docs/legal/CLA_INDIVIDUAL.md"):
            assert path in code, f"可信输入里少了 {path}"
        assert "cla-signatures.json" not in code, (
            "仓库不保存签署事实——workflow 不该再去取一份 signer 名单")

    def test_permissions_are_minimal(self, cla_job):
        code = _code(cla_job)
        m = re.search(r"(?m)^\s+permissions:\n((?:\s+\w[\w-]*:\s*\w+\n)+)", code)
        assert m, "CLA cla_job 必须显式声明 permissions"
        perms = dict(re.findall(r"(\w[\w-]*):\s*(\w+)", m.group(1)))
        assert perms == {"contents": "read", "pull-requests": "read"}, (
            f"CLA cla_job 的权限必须恰好是两个只读项，实际：{perms}")
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
                f"不能是 @main / @v1 / @v2")

    def test_no_secrets_are_referenced(self, cla_job):
        assert "secrets." not in _code(cla_job), (
            "CLA cla_job 不该用任何 secret——它只读公开元数据")


# ═════════════════════════════════════════════ 7. 协议版本与哈希绑定
class TestAgreementVersionBinding:
    def test_policy_shape_is_valid(self, gate, policy):
        gate.validate_policy(policy)          # 形状不对当场抛

    @pytest.mark.parametrize("kind", ["individual", "corporate"])
    def test_recorded_hash_matches_the_document(self, gate, policy, kind):
        """**改了 CLA 正文却不更新哈希 → 红。** 旧签名不能被静默套到新文本上。"""
        ag = policy["agreements"][kind]
        path = ROOT / ag["path"]
        assert path.is_file(), f"协议正文不存在：{ag['path']}"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == ag["sha256"], (
            f"{ag['path']} 的 SHA-256 是 {actual}，policy 里记的是 {ag['sha256']}"
            "——改了正文就必须走 docs/legal/CLA_VERSIONING.md 的版本流程")

    @pytest.mark.parametrize("kind", ["individual", "corporate"])
    def test_document_declares_the_same_version_as_the_policy(self, policy, kind):
        ag = policy["agreements"][kind]
        text = (ROOT / ag["path"]).read_text(encoding="utf-8")
        m = re.search(r"(?m)^\*\*CLA_VERSION:\s*(\S+?)\*\*", text)
        assert m, f"{ag['path']} 里读不出 CLA_VERSION"
        assert m.group(1) == ag["version"], (
            f"{ag['path']} 声明的版本是 {m.group(1)}，policy 里是 {ag['version']}")

    def test_versioning_history_lists_every_current_version(self, policy):
        text = (LEGAL / "CLA_VERSIONING.md").read_text(encoding="utf-8")
        for kind, ag in policy["agreements"].items():
            assert f"`{ag['version']}`" in text, (
                f"CLA_VERSIONING.md 的版本历史里没有 {kind} 的当前版本 {ag['version']}")

    def test_repository_stores_no_signature_records(self):
        """**签署事实的权威只有一个：provider。**

        仓库里不许再出现手工维护的 signer 名单——它会和服务商的数据库变成
        两个法律权威，分叉之后没有任何机制说得清哪一份算数。
        """
        stray = LEGAL / "cla-signatures.json"
        assert not stray.exists(), (
            "docs/legal/cla-signatures.json 又出现了——仓库不保存签署事实，"
            "见 docs/legal/CLA_VERSIONING.md#where-signature-records-live")
        for name in ("signatures", "signers", "ledger"):
            assert name not in json.loads(POLICY_PATH.read_text(encoding="utf-8")), (
                f"cla-policy 里出现了 `{name}` 字段——签署记录不归仓库管")

    def test_provider_is_the_declared_authority(self, gate, policy):
        prov = policy["provider"]
        assert isinstance(prov.get("configured"), bool)
        if prov["configured"]:
            assert prov.get("name") and prov.get("check_name"), (
                "provider 已配置就必须指向一个具体的、可核对的 check")

    def test_draft_agreements_forbid_a_configured_provider(self, gate, policy):
        """草案上不存在有效签署——这条是结构性的，不靠人记得。"""
        pol = json.loads(json.dumps(policy))
        pol["provider"] = {"configured": True, "name": "X", "check_name": "Y"}
        drafts = [k for k, ag in pol["agreements"].items()
                  if str(ag["version"]).endswith("-draft")]
        if not drafts:
            pytest.skip("协议已脱离草案")
        with pytest.raises(gate.ConfigError):
            gate.validate_policy(pol)

    def test_draft_versions_carry_the_configuration_marker(self, policy):
        """草案必须自带 RIGHTS_HOLDER_CONFIGURATION_REQUIRED——

        「还是草案」和「已经能签」之间不许只差一个没人注意的后缀。
        """
        for kind, ag in policy["agreements"].items():
            if not str(ag["version"]).endswith("-draft"):
                continue
            text = (ROOT / ag["path"]).read_text(encoding="utf-8")
            assert "RIGHTS_HOLDER_CONFIGURATION_REQUIRED" in text, (
                f"{ag['path']} 是草案，却没标出待配置的法律主体")

    def test_exemptions_are_explicit_and_justified(self, policy):
        assert policy["exemptions"], "豁免表是空的？至少应有著作权人本人"
        for ex in policy["exemptions"]:
            assert ex["kind"] in ("rights_holder", "bot")
            assert len(ex["reason"]) > 20, (
                f"豁免 `{ex['login']}` 的理由太短——写不出理由的豁免不该存在")


# ═══════════════════════════════ 7b. 法律表述的精度（本轮收紧的几处）
class TestLegalWordingPrecision:
    """守的是**具体的错误主张**，不是某几个单词。

    判据刻意写窄：文档里出现 `permanent` / `never` 本身完全正常（版本绑定
    那条不变式就该这么说）。会误导未来维护者的是两类**具体断言**——
    「合入外部贡献 = 永久失去再授权能力」与「必须先有公司才能签 CLA」——
    它们都不成立，也都有真正的后续路径。
    """

    LEGAL_DOCS = sorted(LEGAL.glob("*.md")) + [
        ROOT / "CONTRIBUTING.md", ROOT / "README.md",
        ROOT / "README.zh-CN.md", ROOT / "TRADEMARKS.md",
    ]

    #: 「再授权能力被永久剥夺」的错误主张。命中即红。
    PERMANENT_LOSS = (
        r"(?i)(permanently|irreversibl\w*|forever)[^.\n]{0,60}(relicens|re-licens|proprietary|commercial)",
        r"(?i)can\s+never[^.\n]{0,40}relicens",
        r"(?i)(cannot|could)\s+ever\s+be\s+relicens",
        r"(?i)the\s+window[^.\n]{0,30}(can\s+never|never)\s+reopen",
        r"永久(?:地)?(?:失去|丧失)[^。\n]{0,20}(?:再)?授权",
        r"(?:再也|永远)(?:不能|无法)(?:再)?授权",
    )

    #: 「必须先成立公司/法人」的错误前提。命中即红。
    ENTITY_REQUIRED = (
        r"(?i)must\s+(?:first\s+)?(?:form|incorporate|create|establish)\s+a\s+(?:compan|corporation|legal entity)",
        r"(?i)(?:compan\w+|corporation|legal entity)\s+is\s+required\s+(?:before|for)[^.\n]{0,40}CLA",
        r"(?i)cannot\s+be\s+signed\s+until[^.\n]{0,40}(compan|corporation|entity)[^.\n]{0,20}(exists|is formed)",
        r"(?i)without\s+one,?\s+the\s+CLA\s+cannot\s+be\s+executed",
        r"必须(?:先)?(?:成立|注册)(?:公司|法人)[^。\n]{0,20}(?:才能|方可)",
    )

    @pytest.mark.parametrize("pattern", PERMANENT_LOSS)
    def test_no_permanent_relicensing_loss_claim(self, pattern):
        hits = []
        for f in self.LEGAL_DOCS:
            for m in re.finditer(pattern, f.read_text(encoding="utf-8")):
                hits.append(f"{f.relative_to(ROOT)}: {m.group(0)!r}")
        assert not hits, (
            "出现了「合入外部贡献即永久失去再授权能力」这类主张，但它不成立：\n  "
            + "\n  ".join(hits)
            + "\n准确说法是 Tavotto 不能**单方面**再授权；仍可通过事后 CLA、"
              "单独许可、重写替换或排除在商业版之外解决。")

    @pytest.mark.parametrize("pattern", ENTITY_REQUIRED)
    def test_no_company_required_precondition(self, pattern):
        hits = []
        for f in self.LEGAL_DOCS:
            for m in re.finditer(pattern, f.read_text(encoding="utf-8")):
                hits.append(f"{f.relative_to(ROOT)}: {m.group(0)!r}")
        assert not hits, (
            "出现了「必须先有公司/法人才能签 CLA」这类前提，但它不成立——"
            "自然人同样可以是缔约方：\n  " + "\n  ".join(hits))

    def test_rights_holder_marker_is_defined_accurately(self):
        """RIGHTS_HOLDER_CONFIGURATION_REQUIRED 必须有准确定义，而不只是个标记。"""
        text = (LEGAL / "CLA_AUTOMATION_SETUP.md").read_text(encoding="utf-8")
        assert "RIGHTS_HOLDER_CONFIGURATION_REQUIRED" in text
        assert re.search(r"(?i)legal person or entity", text), (
            "定义必须说明要识别的是「legal person or entity」")
        assert re.search(r"(?i)does not (mean|require)[^.\n]{0,40}compan", text), (
            "定义必须明说这不要求成立公司——否则读的人会以为要先注册法人")
        assert re.search(r"(?i)individual rights holder is[^.\n]{0,40}supported"
                         r"|natural person can", text), (
            "必须明说自然人作为权利人是被支持的形态")

    def test_draft_status_is_explained_by_the_marker_not_by_legal_form(self):
        for f in (LEGAL / "CLA_VERSIONING.md", ROOT / "CONTRIBUTING.md"):
            text = f.read_text(encoding="utf-8")
            if "-draft" not in text:
                continue
            ok = ("RIGHTS_HOLDER_CONFIGURATION_REQUIRED" in text
                  or re.search(r"(?i)counterparty|governing law", text))
            assert ok, (
                f"{f.name} 解释草案状态时必须指向 "
                "RIGHTS_HOLDER_CONFIGURATION_REQUIRED / 缺失的缔约细节，"
                "而不是某种法律形态")


# ═══════════════════════════════ 7c. 审计基线的时效
class TestAuditBaseline:
    """`IP_PROVENANCE.md` 必须审计到一个**比首次基线更新**的真实提交。

    **刻意不要求 baseline == HEAD**：那会让每一个正常 PR 都把 legal 门禁
    弄红（main 一前进就过期）。这里守的是「最近一次审计过的权利基线」这个
    概念本身——它必须真实存在、且在本分支历史里。新代码的持续保证来自
    CLA gate 逐 PR 执行，不是靠每次提交重审全史。
    """

    @staticmethod
    def _sha(label):
        text = PROVENANCE.read_text(encoding="utf-8")
        i = text.index(label)
        m = re.search(r"`([0-9a-f]{40})`", text[i:i + 400])
        assert m, f"IP_PROVENANCE 的「{label}」下读不出 40 位 SHA"
        return m.group(1)

    def test_initial_baseline_is_retained_as_history(self):
        assert self._sha("### Initial audited baseline") == INITIAL_BASELINE, (
            "首次审计基线是历史记录，不许被改掉或删掉")

    def test_current_baseline_has_moved_past_the_initial_one(self):
        cur = self._sha("### Current audited baseline")
        assert cur != INITIAL_BASELINE, (
            f"当前审计基线仍写着首次基线 {INITIAL_BASELINE[:7]}——"
            "main 已经前进，基线必须重新审计并更新")

    def test_current_baseline_is_a_real_commit_in_this_history(self):
        cur = self._sha("### Current audited baseline")
        r = subprocess.run(["git", "merge-base", "--is-ancestor", cur, "HEAD"],
                           cwd=ROOT, capture_output=True)
        assert r.returncode == 0, (
            f"当前审计基线 {cur[:7]} 不在本分支历史里——"
            "它必须是一个真实审计过的、可达的提交")


# ═════════════════════════════════════════════ 8. 判定器行为（单测）
class TestGateDecisions:
    def _policy(self, *, provider_on=False, draft=False):
        ver = "1.0-draft" if draft else "1.0"
        prov = ({"configured": True, "name": "P", "check_name": "P check"}
                if provider_on else {"configured": False, "name": None,
                                     "check_name": None})
        return {
            "schema": 2,
            "agreements": {"individual": {"path": "x.md", "version": ver,
                                          "sha256": "a" * 64}},
            "provider": prov,
            "exemptions": [{"login": "owner", "kind": "rights_holder",
                            "reason": "the rights holder, at length enough"}],
        }

    @staticmethod
    def _ok_check():
        return [{"name": "P check", "status": "completed", "conclusion": "success"}]

    def test_merge_group_is_success_not_skipped(self, gate):
        """判定器在队列候选上必须给出**成功**结论，而不是靠 job 被跳过。"""
        v = gate.decide("merge_group", self._policy(), None, [])
        assert v["status"] == "not_applicable"

    def test_unknown_event_is_a_config_error(self, gate):
        with pytest.raises(gate.ConfigError):
            gate.decide("push", self._policy(), None, [])

    def test_exempt_contributor_passes_even_without_a_provider(self, gate):
        v = gate.decide("pull_request", self._policy(), None,
                        [{"login": "owner", "sources": ["pr_author"]}])
        assert v["status"] == "success"

    def test_unconfigured_provider_blocks_everyone_else(self, gate):
        """**未配置 ≠ 放行。** 绝不因为服务没接上就把外部贡献者当成签过。"""
        v = gate.decide("pull_request", self._policy(), None,
                        [{"login": "stranger", "sources": ["pr_author"]}])
        assert v["status"] == "failure"
        assert "尚未启用" in " ".join(v["problems"])

    def test_unconfigured_provider_failure_is_actionable(self, gate):
        """只有一句 `CLA check failed` 会让合法 PR 卡死而没人知道下一步。"""
        v = gate.decide("pull_request", self._policy(), None,
                        [{"login": "stranger", "sources": ["pr_author"]}])
        msg = " ".join(v["problems"])
        assert "CLA_AUTOMATION_SETUP.md" in msg, "失败信息必须指出去哪儿看"
        assert "issue" in msg, "失败信息必须告诉贡献者眼下能做什么"

    def test_provider_success_qualifies_contributors(self, gate):
        v = gate.decide("pull_request", self._policy(provider_on=True),
                        self._ok_check(),
                        [{"login": "stranger", "sources": ["pr_author"]}])
        assert v["status"] == "success"

    def test_provider_failure_blocks(self, gate):
        checks = [{"name": "P check", "status": "completed", "conclusion": "failure"}]
        v = gate.decide("pull_request", self._policy(provider_on=True), checks,
                        [{"login": "stranger", "sources": ["pr_author"]}])
        assert v["status"] == "failure"

    def test_missing_provider_check_blocks_rather_than_guesses(self, gate):
        v = gate.decide("pull_request", self._policy(provider_on=True), [],
                        [{"login": "stranger", "sources": ["pr_author"]}])
        assert v["status"] == "failure"
        assert "没找到" in " ".join(v["problems"])

    def test_incomplete_provider_check_blocks(self, gate):
        checks = [{"name": "P check", "status": "in_progress", "conclusion": None}]
        v = gate.decide("pull_request", self._policy(provider_on=True), checks,
                        [{"login": "stranger", "sources": ["pr_author"]}])
        assert v["status"] == "failure"

    def test_no_contributors_collected_is_a_failure(self, gate):
        """「一个人都没收集到」是取数出错，不是「所有人都签了」。"""
        v = gate.decide("pull_request", self._policy(provider_on=True),
                        self._ok_check(), [])
        assert v["status"] == "failure"

    def test_unresolved_co_author_fails_rather_than_being_ignored(self, gate):
        v = gate.decide("pull_request", self._policy(provider_on=True),
                        self._ok_check(),
                        [{"login": "owner", "sources": ["pr_author"]}],
                        [{"kind": "co_author", "sha": "abc", "name": "X",
                          "email": "x@corp.example"}])
        assert v["status"] == "failure"
        msg = " ".join(v["problems"])
        # 可操作性：说清哪个 commit、哪个身份、为什么、怎么处置
        assert "abc" in msg and "x@corp.example" in msg
        assert "处置" in msg, "认不出账号的红必须给出人工处置办法"

    def test_exemption_without_a_reason_is_rejected(self, gate):
        pol = self._policy()
        pol["exemptions"] = [{"login": "x", "kind": "bot", "reason": ""}]
        with pytest.raises(gate.ConfigError):
            gate.validate_policy(pol)

    def test_bot_suffix_alone_does_not_grant_an_exemption(self, gate):
        """没有「名字带 [bot] 就放行」这条规则。"""
        v = gate.decide("pull_request", self._policy(), None,
                        [{"login": "some-random[bot]", "sources": ["pr_author"]}])
        assert v["status"] == "failure"

    def test_repository_owner_is_not_dynamically_trusted(self, gate):
        """豁免只认显式点名，不许按「PR 作者 == 仓库 owner」动态猜。"""
        v = gate.decide("pull_request", self._policy(), None,
                        [{"login": "Tavotto", "sources": ["pr_author"]}])
        assert v["status"] == "failure"


# ═════════════════════════════════════════════ 9. 贡献者收集（单测）
class TestContributorCollection:
    def test_collects_author_commit_authors_and_co_authors(self, gate):
        commits = [{
            "sha": "deadbeef",
            "commit": {"author": {"name": "A", "email": "a@users.noreply.github.com"},
                       "message": "x\n\nCo-authored-by: B <2+bee@users.noreply.github.com>"},
            "author": {"login": "alpha"},
        }]
        found, unresolved = gate.collect_contributors("opener", commits)
        assert {c["login"].lower() for c in found} == {"opener", "alpha", "bee"}
        assert unresolved == []

    def test_commit_author_without_a_linked_account_is_unresolved(self, gate):
        commits = [{"sha": "cafe", "commit": {"author": {"name": "N", "email": "n@corp.example"},
                                              "message": "x"}, "author": None}]
        found, unresolved = gate.collect_contributors(None, commits)
        assert found == []
        assert unresolved and unresolved[0]["kind"] == "commit_author"

    def test_numeric_prefixed_noreply_resolves(self, gate):
        found, _ = gate.collect_contributors(None, [
            {"sha": "1", "commit": {"author": {"name": "E",
                                               "email": "88193520+erwanjun@users.noreply.github.com"},
                                    "message": "x"}, "author": None}])
        assert [c["login"] for c in found] == ["erwanjun"]

    def test_malformed_commits_payload_raises(self, gate):
        with pytest.raises(gate.ConfigError):
            gate.collect_contributors(None, {"not": "a list"})
