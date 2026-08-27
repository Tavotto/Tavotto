"""法律与贡献者治理的结构性契约（LICENSE / CLA / 商标 / CLA workflow）。

这些判据钉的都是「坏掉之后不会有任何别的测试红、只会在**将来某次法律审查**
才暴露」的形状——那种缺陷的发现成本比一次 CI 红高几个数量级：

* 社区版许可证被悄悄改宽（AGPL-3.0-only → -or-later）→ 权利边界变了，没人看得见；
* CONTRIBUTING 掉了 CLA 链接 → 外部贡献者按旧规则提交，权利链当场断掉；
* 仓库里重新出现手工维护的 signer 名单 → 与签名服务商构成两个法律权威；
* provider 未配置时静默放行 → 外部贡献被当成签过，权利链断在没人看见的地方；
* 改了 CLA 正文却不 bump 版本/哈希 → 旧签名被静默套用到新文本上；
* 出现 ® → 主张一个并不存在的注册。

**`cla-check` 这个 CI job 的形状契约不在这里**，在
`tests/test_cla_workflow_contract.py`——那份测试与 job 本身在同一个 PR 里落地，
因为在 job 还不存在的树上断言它的形状只会是个必红的空门禁。

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
LEGAL = ROOT / "docs" / "legal"
POLICY_PATH = ROOT / ".github" / "cla-policy.json"
PROVENANCE = LEGAL / "IP_PROVENANCE.md"

#: 首次审计的基线。它是**历史记录**，永远留在 IP_PROVENANCE 里；
#: 但它不许再被当成「当前基线」——那正是本轮要修的陈旧断言。
INITIAL_BASELINE = "aaa065f298ac4ce8a66a3482786bedf516a1154b"

#: 社区版许可证的唯一正确取值。**不是** -or-later，也不是 dual。
LICENCE_ID = "AGPL-3.0-only"


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
            f"pyproject 的 license 是 {m.group(1)}，必须是 {LICENCE_ID}"
        )

    def test_pyproject_ships_the_licence_file(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert re.search(r'(?m)^license-files\s*=\s*\[\s*"LICENSE"\s*\]', pyproject), (
            "wheel/sdist 必须带上 LICENSE"
        )

    @pytest.mark.parametrize(
        "manifest",
        [
            "workerd/Cargo.toml",
            "src-tauri/Cargo.toml",
        ],
    )
    def test_rust_crates_declare_agpl_only(self, manifest):
        text = (ROOT / manifest).read_text(encoding="utf-8")
        m = re.search(r'(?m)^license\s*=\s*"([^"]+)"', text)
        assert m, f"{manifest} 里读不出 license"
        assert m.group(1) == LICENCE_ID

    def test_no_second_licence_file_at_root(self):
        """「官方另有商业授权权利」与「公开仓库同时摆两份 LICENSE」不是一回事。"""
        strays = [p.name for p in ROOT.glob("LICENSE*") if p.name not in ("LICENSE",)]
        assert not strays, f"仓库根出现了第二份许可证文件：{strays}"


# ═════════════════════════════════════════════ 2. CONTRIBUTING 不变式
@pytest.fixture(scope="module")
def contributing():
    return (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")


class TestContributing:
    def test_links_both_agreements(self, contributing):
        for target in ("docs/legal/CLA_INDIVIDUAL.md", "docs/legal/CLA_CORPORATE.md"):
            assert target in contributing, f"CONTRIBUTING 必须链到 {target}"

    def test_says_contributor_keeps_copyright(self, contributing):
        assert re.search(r"(?i)keep\s+the\s+copyright", contributing), (
            "CONTRIBUTING 必须明说贡献者保留著作权——这是本模型的核心承诺"
        )

    def test_states_the_community_licence(self, contributing):
        assert LICENCE_ID in contributing

    def test_does_not_claim_dco_replaces_cla(self, contributing):
        """DCO 证明来源，不是著作权授权。把它当 CLA 用是这轮最要防的误解。"""
        bad = re.search(
            r"(?i)DCO[^.\n]{0,80}(replaces?|instead of|substitute for|is a|"
            r"serves? as)[^.\n]{0,40}CLA",
            contributing,
        )
        assert not bad, f"CONTRIBUTING 把 DCO 说成了 CLA 的替代：{bad.group(0)!r}"

    def test_requires_cla_for_pull_requests_not_issues(self, contributing):
        assert re.search(r"(?is)issue.{0,200}\|\s*\*\*No\*\*", contributing), (
            "CONTRIBUTING 应说明 issue / 讨论不要求 CLA"
        )
        assert re.search(r"(?is)pull request.{0,200}\|\s*\*\*Yes\*\*", contributing), (
            "CONTRIBUTING 应说明 PR 一律要求 CLA"
        )

    def test_points_employer_owned_work_at_corporate_cla(self, contributing):
        assert re.search(r"(?i)employer", contributing), "CONTRIBUTING 必须处理「成果归雇主」的情形"


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
        assert re.search(
            r"(?i)licence.{0,40}not.{0,40}trademark|"
            r"does not cover trademarks",
            text,
        ), "TRADEMARKS.md 必须讲清「著作权许可 ≠ 商标许可」"

    @pytest.mark.parametrize(
        "name",
        [
            "README.md",
            "README.zh-CN.md",
            "TRADEMARKS.md",
            "CONTRIBUTING.md",
        ],
    )
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
                f"{name} 必须说明 Tavotto 是未注册商标"
            )


# ═════════════════════════════════════════════ 7. 协议版本与哈希绑定
class TestAgreementVersionBinding:
    def test_policy_shape_is_valid(self, gate, policy):
        gate.validate_policy(policy)  # 形状不对当场抛

    @pytest.mark.parametrize("kind", ["individual", "corporate"])
    def test_recorded_hash_matches_the_document(self, gate, policy, kind):
        """**改了 CLA 正文却不更新哈希 → 红。** 旧签名不能被静默套到新文本上。"""
        ag = policy["agreements"][kind]
        path = ROOT / ag["path"]
        assert path.is_file(), f"协议正文不存在：{ag['path']}"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == ag["sha256"], (
            f"{ag['path']} 的 SHA-256 是 {actual}，policy 里记的是 {ag['sha256']}"
            "——改了正文就必须走 docs/legal/CLA_VERSIONING.md 的版本流程"
        )

    @pytest.mark.parametrize("kind", ["individual", "corporate"])
    def test_document_declares_the_same_version_as_the_policy(self, policy, kind):
        ag = policy["agreements"][kind]
        text = (ROOT / ag["path"]).read_text(encoding="utf-8")
        m = re.search(r"(?m)^\*\*CLA_VERSION:\s*(\S+?)\*\*", text)
        assert m, f"{ag['path']} 里读不出 CLA_VERSION"
        assert m.group(1) == ag["version"], (
            f"{ag['path']} 声明的版本是 {m.group(1)}，policy 里是 {ag['version']}"
        )

    def test_versioning_history_lists_every_current_version(self, policy):
        text = (LEGAL / "CLA_VERSIONING.md").read_text(encoding="utf-8")
        for kind, ag in policy["agreements"].items():
            assert f"`{ag['version']}`" in text, (
                f"CLA_VERSIONING.md 的版本历史里没有 {kind} 的当前版本 {ag['version']}"
            )

    def test_repository_stores_no_signature_records(self):
        """**签署事实的权威只有一个：provider。**

        仓库里不许再出现手工维护的 signer 名单——它会和服务商的数据库变成
        两个法律权威，分叉之后没有任何机制说得清哪一份算数。
        """
        stray = LEGAL / "cla-signatures.json"
        assert not stray.exists(), (
            "docs/legal/cla-signatures.json 又出现了——仓库不保存签署事实，"
            "见 docs/legal/CLA_VERSIONING.md#where-signature-records-live"
        )
        for name in ("signatures", "signers", "ledger"):
            assert name not in json.loads(POLICY_PATH.read_text(encoding="utf-8")), (
                f"cla-policy 里出现了 `{name}` 字段——签署记录不归仓库管"
            )

    def test_provider_is_the_declared_authority(self, gate, policy):
        prov = policy["provider"]
        assert isinstance(prov.get("configured"), bool)
        if prov["configured"]:
            assert prov.get("name") and prov.get("check_name"), (
                "provider 已配置就必须指向一个具体的、可核对的 check"
            )

    def test_draft_agreements_forbid_a_configured_provider(self, gate, policy):
        """草案上不存在有效签署——这条是结构性的，不靠人记得。"""
        pol = json.loads(json.dumps(policy))
        pol["provider"] = {"configured": True, "name": "X", "check_name": "Y"}
        drafts = [k for k, ag in pol["agreements"].items() if str(ag["version"]).endswith("-draft")]
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
                f"{ag['path']} 是草案，却没标出待配置的法律主体"
            )

    def test_exemptions_are_explicit_and_justified(self, policy):
        assert policy["exemptions"], "豁免表是空的？至少应有著作权人本人"
        for ex in policy["exemptions"]:
            assert ex["kind"] in ("rights_holder", "bot")
            assert len(ex["reason"]) > 20, (
                f"豁免 `{ex['login']}` 的理由太短——写不出理由的豁免不该存在"
            )


# ═══════════════════════════════ 7b. 法律表述的精度（本轮收紧的几处）
class TestLegalWordingPrecision:
    """守的是**具体的错误主张**，不是某几个单词。

    判据刻意写窄：文档里出现 `permanent` / `never` 本身完全正常（版本绑定
    那条不变式就该这么说）。会误导未来维护者的是两类**具体断言**——
    「合入外部贡献 = 永久失去再授权能力」与「必须先有公司才能签 CLA」——
    它们都不成立，也都有真正的后续路径。
    """

    LEGAL_DOCS = sorted(LEGAL.glob("*.md")) + [
        ROOT / "CONTRIBUTING.md",
        ROOT / "README.md",
        ROOT / "README.zh-CN.md",
        ROOT / "TRADEMARKS.md",
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
            "单独许可、重写替换或排除在商业版之外解决。"
        )

    @pytest.mark.parametrize("pattern", ENTITY_REQUIRED)
    def test_no_company_required_precondition(self, pattern):
        hits = []
        for f in self.LEGAL_DOCS:
            for m in re.finditer(pattern, f.read_text(encoding="utf-8")):
                hits.append(f"{f.relative_to(ROOT)}: {m.group(0)!r}")
        assert not hits, (
            "出现了「必须先有公司/法人才能签 CLA」这类前提，但它不成立——"
            "自然人同样可以是缔约方：\n  " + "\n  ".join(hits)
        )

    def test_rights_holder_marker_is_defined_accurately(self):
        """RIGHTS_HOLDER_CONFIGURATION_REQUIRED 必须有准确定义，而不只是个标记。"""
        text = (LEGAL / "CLA_AUTOMATION_SETUP.md").read_text(encoding="utf-8")
        assert "RIGHTS_HOLDER_CONFIGURATION_REQUIRED" in text
        assert re.search(r"(?i)legal person or entity", text), (
            "定义必须说明要识别的是「legal person or entity」"
        )
        assert re.search(r"(?i)does not (mean|require)[^.\n]{0,40}compan", text), (
            "定义必须明说这不要求成立公司——否则读的人会以为要先注册法人"
        )
        assert re.search(
            r"(?i)individual rights holder is[^.\n]{0,40}supported"
            r"|natural person can",
            text,
        ), "必须明说自然人作为权利人是被支持的形态"

    def test_resolved_rights_holder_is_recorded_consistently(self):
        """权利人一旦定下来，就不许在任何一处被静默改掉或抹掉。

        它是 CLA 的缔约方——两份协议、商标政策、以及给维护者看的状态表必须
        说同一件事。**只在已经解析出权利人时才生效**：还没定的仓库不该被这条
        判据逼着编一个出来。
        """
        individual = (LEGAL / "CLA_INDIVIDUAL.md").read_text(encoding="utf-8")
        m = re.search(r'(?m)^\*\*"We"/"Us" is ([^*]+)\*\*', individual)
        if not m:
            pytest.skip("权利人尚未配置")
        holder = m.group(1).strip()
        assert len(holder) > 2, "权利人名字读出来是空的？"
        for f in (
            "docs/legal/CLA_CORPORATE.md",
            "TRADEMARKS.md",
            "docs/legal/README.md",
            "CONTRIBUTING.md",
        ):
            assert holder in (ROOT / f).read_text(encoding="utf-8"), (
                f"{f} 里没有权利人 `{holder}`——缔约方必须处处一致"
            )
        # 签名块里的 Us 也必须是同一个人，而不是残留的占位
        assert f"Name:      {holder}" in individual, "Individual CLA 的 Us 签名块没填权利人"

    def test_no_personal_postal_address_is_published(self):
        """自然人权利人的住址不该出现在公开仓库里。

        地址在**签署时的执行副本**上填，不在模板里。这条判据挡的是「顺手把
        联系方式补全」——那是隐私暴露，而且换不来任何东西。
        """
        for f in ("docs/legal/CLA_INDIVIDUAL.md", "docs/legal/CLA_CORPORATE.md"):
            text = (ROOT / f).read_text(encoding="utf-8")
            # 「Us」签名块里 Address 后面必须仍是空白横线，不是真地址
            m = re.search(r"(?s)\*\*Us\*\*.{0,400}?Address:\s*(\S+)", text)
            if m:
                assert set(m.group(1)) <= {"_"}, (
                    f"{f} 的 Us 签名块里填了真实地址：{m.group(1)[:40]!r}"
                )

    def test_draft_status_is_explained_by_the_marker_not_by_legal_form(self):
        for f in (LEGAL / "CLA_VERSIONING.md", ROOT / "CONTRIBUTING.md"):
            text = f.read_text(encoding="utf-8")
            if "-draft" not in text:
                continue
            ok = "RIGHTS_HOLDER_CONFIGURATION_REQUIRED" in text or re.search(
                r"(?i)counterparty|governing law", text
            )
            assert ok, (
                f"{f.name} 解释草案状态时必须指向 "
                "RIGHTS_HOLDER_CONFIGURATION_REQUIRED / 缺失的缔约细节，"
                "而不是某种法律形态"
            )


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
        m = re.search(r"`([0-9a-f]{40})`", text[i : i + 400])
        assert m, f"IP_PROVENANCE 的「{label}」下读不出 40 位 SHA"
        return m.group(1)

    def test_initial_baseline_is_retained_as_history(self):
        assert self._sha("### Initial audited baseline") == INITIAL_BASELINE, (
            "首次审计基线是历史记录，不许被改掉或删掉"
        )

    def test_current_baseline_has_moved_past_the_initial_one(self):
        cur = self._sha("### Current audited baseline")
        assert cur != INITIAL_BASELINE, (
            f"当前审计基线仍写着首次基线 {INITIAL_BASELINE[:7]}——"
            "main 已经前进，基线必须重新审计并更新"
        )

    @staticmethod
    def _git(*args: str) -> subprocess.CompletedProcess:
        # **`encoding="utf-8"` 不能省。** `text=True` 单独出现时按系统默认代码页
        # 解码，Windows 上是 ANSI——git 的中文输出（本仓库的提交信息、以及
        # git 自己的本地化错误）会静默丢掉 stdout/stderr，于是这里的失败信息
        # 在最需要它的那台机器上正好是空的。
        # 看护判据：tests/test_source_hygiene.py::test_windows_bound_subprocesses_pin_their_decoding
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
        )

    @classmethod
    def _is_ancestor(cls, sha: str) -> tuple[int, bool]:
        """`git merge-base --is-ancestor` 的三种退出码必须分开读。

        0   是祖先
        1   **不是**祖先          ← 判据真正要抓的缺陷
        128 对象不在这个克隆里     ← 「看不见」，不是「不是」

        CI 的 `actions/checkout` 默认 `fetch-depth: 1`（本仓库没有任何一处设过
        它），浅克隆里只有 HEAD 一个提交。早先这里把 128 当成失败，于是
        **本地永远绿（完整克隆）、CI 永远红**，失败信息还断言「不在本分支历史
        里」——那是假结论，它只是没被下载。`backend-fast` 在 fast gate 的闭集
        里，这条一旦进 main，此后每个 PR 与 merge_group 都会红，包括修它的
        那个 PR。

        **「按 SHA 定向 fetch 一次」不够，实测过**：`git fetch --depth=1 origin
        <sha>` 之后 `is-ancestor` 回的是 **1** 而不是 0——取回来的是浅对象，
        HEAD 自己也没有父提交，跨越 shallow 边界算不出可达性。那只是把 128
        换成 1，仓库照样锁死。

        所以浅克隆下**补全历史再判**（实测 3.7s / +7MB，只在浅克隆里付一次）。
        不把 128 判成 skip：一条在 CI 上从没执行过的判据，是用假绿换真红；
        也不给 `backend-fast` 加 `fetch-depth: 0`，那会把代价记在每个 job 上。

        返回 (退出码, 是否补全过历史)。
        """
        rc = cls._git("merge-base", "--is-ancestor", sha, "HEAD").returncode
        shallow = cls._git("rev-parse", "--is-shallow-repository").stdout.strip() == "true"
        if rc == 0 or not shallow:
            # 完整克隆里的 0/1 都是可信答案，一分钱不花。
            return rc, False
        cls._git("fetch", "--quiet", "--unshallow")
        return cls._git("merge-base", "--is-ancestor", sha, "HEAD").returncode, True

    def test_current_baseline_is_a_real_commit_in_this_history(self):
        cur = self._sha("### Current audited baseline")
        rc, deepened = self._is_ancestor(cur)
        where = "（补全历史之后仍然如此）" if deepened else ""
        if rc == 128:
            # **不要在这里断言「这个 SHA 不存在」**——补全之后仍取不到，可能是
            # 它真的不存在，也可能是它在一条本次没有 fetch 的分支上（浅克隆
            # 只补它跟踪的那些）。两种都该红，但只有一种成立时说死就是又一个
            # 假结论。判据要的是「在这条历史上可达」，如实说到这里为止。
            raise AssertionError(
                f"当前审计基线 {cur[:7]} 在这个克隆里取不到{where}——"
                "要么这个 SHA 不存在，要么它不在本分支的历史上。"
                "IP_PROVENANCE 记的必须是本历史上一个真实审计过的提交"
            )
        assert rc == 0, (
            f"当前审计基线 {cur[:7]} 是真实提交，但**不是本分支的祖先**{where}——"
            "它必须是这条历史上真实审计过的那个点"
        )

    def test_commit_counts_agree_with_the_audited_baseline(self):
        """法律文档里引用的提交数，必须与 IP_PROVENANCE 记的审计基线一致。

        今天这个形状出现了三次（陈旧指纹、陈旧 CLA 版本示例、陈旧的全 ref
        计数），共同点是**引用一个别处算出来的数，而那个数的口径已经变了**。
        判据只认一件事：谁引用了「X of the Y commits」，Y 就得是基线那个 Y。

        刻意**不禁止**提到旧数字本身——IP_PROVENANCE 里那句「745 来自
        `git rev-list --all`」正是解释口径的，禁掉它反而会让纠正无法留档。
        """
        prov = PROVENANCE.read_text(encoding="utf-8")
        m = re.search(r"\| Commits reachable from this baseline \| \*\*(\d+)\*\* \|", prov)
        assert m, "IP_PROVENANCE 里读不出审计基线的提交数"
        audited = m.group(1)

        pattern = re.compile(r"(\d{3,})\s*(?:of the|of|/)\s*(\d{3,})\s+commits", re.I)
        bad = []
        for f in sorted(LEGAL.glob("*.md")) + [ROOT / "CONTRIBUTING.md", ROOT / "README.md"]:
            text = f.read_text(encoding="utf-8")
            for hit in pattern.finditer(text):
                if hit.group(2) != audited:
                    # 明确标注为「早先草稿/已纠正」的引用是留档，不算漂移。
                    ctx = text[max(0, hit.start() - 160) : hit.end() + 60]
                    if re.search(r"(?i)earlier draft|initial audit|rev-list --all|已纠正", ctx):
                        continue
                    bad.append(f"{f.relative_to(ROOT)}: {hit.group(0)!r}（基线是 {audited}）")
        assert not bad, (
            "法律文档引用的提交数与审计基线对不上——口径变了但引用没跟上：\n  " + "\n  ".join(bad)
        )


# ═════════════════════════════════════════════ 8. 判定器行为（单测）
class TestGateDecisions:
    def _policy(self, *, provider_on=False, draft=False):
        ver = "1.0-draft" if draft else "1.0"
        # 夹具必须与**真实** check-runs 响应同形状：provider 已配置时要有
        # App 身份（见 TestProviderImpersonation——只认名字会被 PR 冒充）。
        prov = (
            {
                "configured": True,
                "name": "P",
                "check_name": "P check",
                "app_slug": "p-app",
                "app_id": 4242,
            }
            if provider_on
            else {"configured": False, "name": None, "check_name": None}
        )
        return {
            "schema": 2,
            "agreements": {"individual": {"path": "x.md", "version": ver, "sha256": "a" * 64}},
            "provider": prov,
            "exemptions": [
                {
                    "login": "owner",
                    "kind": "rights_holder",
                    "reason": "the rights holder, at length enough",
                }
            ],
        }

    #: 与真实 `/commits/{sha}/check-runs` 同形状——含 `app` 身份。
    APP = {"id": 4242, "slug": "p-app"}

    @classmethod
    def _ok_check(cls):
        return [
            {
                "name": "P check",
                "status": "completed",
                "conclusion": "success",
                "app": cls.APP,
            }
        ]

    def test_merge_group_is_success_not_skipped(self, gate):
        """判定器在队列候选上必须给出**成功**结论，而不是靠 job 被跳过。"""
        v = gate.decide("merge_group", self._policy(), None, [])
        assert v["status"] == "not_applicable"

    def test_unknown_event_is_a_config_error(self, gate):
        with pytest.raises(gate.ConfigError):
            gate.decide("push", self._policy(), None, [])

    def test_exempt_contributor_passes_even_without_a_provider(self, gate):
        v = gate.decide(
            "pull_request", self._policy(), None, [{"login": "owner", "sources": ["pr_author"]}]
        )
        assert v["status"] == "success"

    def test_unconfigured_provider_blocks_everyone_else(self, gate):
        """**未配置 ≠ 放行。** 绝不因为服务没接上就把外部贡献者当成签过。"""
        v = gate.decide(
            "pull_request", self._policy(), None, [{"login": "stranger", "sources": ["pr_author"]}]
        )
        assert v["status"] == "failure"
        assert "尚未启用" in " ".join(v["problems"])

    def test_unconfigured_provider_failure_is_actionable(self, gate):
        """只有一句 `CLA check failed` 会让合法 PR 卡死而没人知道下一步。"""
        v = gate.decide(
            "pull_request", self._policy(), None, [{"login": "stranger", "sources": ["pr_author"]}]
        )
        msg = " ".join(v["problems"])
        assert "CLA_AUTOMATION_SETUP.md" in msg, "失败信息必须指出去哪儿看"
        assert "issue" in msg, "失败信息必须告诉贡献者眼下能做什么"

    def test_provider_success_qualifies_contributors(self, gate):
        v = gate.decide(
            "pull_request",
            self._policy(provider_on=True),
            self._ok_check(),
            [{"login": "stranger", "sources": ["pr_author"]}],
        )
        assert v["status"] == "success"

    def test_provider_failure_blocks(self, gate):
        checks = [
            {"name": "P check", "status": "completed", "conclusion": "failure", "app": self.APP}
        ]
        v = gate.decide(
            "pull_request",
            self._policy(provider_on=True),
            checks,
            [{"login": "stranger", "sources": ["pr_author"]}],
        )
        assert v["status"] == "failure"

    def test_missing_provider_check_blocks_rather_than_guesses(self, gate):
        v = gate.decide(
            "pull_request",
            self._policy(provider_on=True),
            [],
            [{"login": "stranger", "sources": ["pr_author"]}],
        )
        assert v["status"] == "failure"
        assert "没找到" in " ".join(v["problems"])

    def test_incomplete_provider_check_blocks(self, gate):
        checks = [{"name": "P check", "status": "in_progress", "conclusion": None, "app": self.APP}]
        v = gate.decide(
            "pull_request",
            self._policy(provider_on=True),
            checks,
            [{"login": "stranger", "sources": ["pr_author"]}],
        )
        assert v["status"] == "failure"

    def test_no_contributors_collected_is_a_failure(self, gate):
        """「一个人都没收集到」是取数出错，不是「所有人都签了」。"""
        v = gate.decide("pull_request", self._policy(provider_on=True), self._ok_check(), [])
        assert v["status"] == "failure"

    def test_unresolved_co_author_fails_rather_than_being_ignored(self, gate):
        v = gate.decide(
            "pull_request",
            self._policy(provider_on=True),
            self._ok_check(),
            [{"login": "owner", "sources": ["pr_author"]}],
            [{"kind": "co_author", "sha": "abc", "name": "X", "email": "x@corp.example"}],
        )
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
        v = gate.decide(
            "pull_request",
            self._policy(),
            None,
            [{"login": "some-random[bot]", "sources": ["pr_author"]}],
        )
        assert v["status"] == "failure"

    def test_repository_owner_is_not_dynamically_trusted(self, gate):
        """豁免只认显式点名，不许按「PR 作者 == 仓库 owner」动态猜。"""
        v = gate.decide(
            "pull_request", self._policy(), None, [{"login": "Tavotto", "sources": ["pr_author"]}]
        )
        assert v["status"] == "failure"


# ═══════════════════════════════ 8b. provider 冒充（Codex 评审 P1，#184）
class TestProviderImpersonation:
    """**判据的输入不能由被判定的对象提供。**

    check-run 的名字是任何集成都能取的。只匹配名字的话，一个 PR 在自己的
    workflow 里加一个 job、或把已有 job 改名成配置里那个 check 名，跑绿之后
    就出现在同一个 head SHA 上——判定器会认，**PR 给自己签了 CLA**，哪怕真正
    的服务商红了或压根没跑。

    这与「PR 自带 policy 把自己写进豁免表」是同一个洞的两个入口（后者正是
    否决 bootstrap 回退的理由），堵法也一样：认**应用身份**，不只认名字。
    """

    REAL_APP = {"id": 34321, "slug": "cla-assistant"}
    ACTIONS_APP = {"id": 15368, "slug": "github-actions"}

    def _policy(self, **over):
        prov = {
            "configured": True,
            "name": "CLA Assistant",
            "check_name": "license/cla",
            "app_slug": "cla-assistant",
            "app_id": 34321,
        }
        prov.update(over)
        return {
            "schema": 2,
            "agreements": {"individual": {"path": "x.md", "version": "1.0", "sha256": "a" * 64}},
            "provider": prov,
            "exemptions": [
                {
                    "login": "owner",
                    "kind": "rights_holder",
                    "reason": "the rights holder, long enough",
                }
            ],
        }

    def _check(self, app, conclusion="success", name="license/cla"):
        return {"name": name, "status": "completed", "conclusion": conclusion, "app": app}

    @staticmethod
    def _outsider():
        return [{"login": "stranger", "sources": ["pr_author"]}]

    def test_same_named_actions_check_cannot_sign_for_the_pr(self, gate):
        """核心攻击：PR 自己加一个同名 GitHub Actions job → 必须不被认。"""
        v = gate.decide(
            "pull_request", self._policy(), [self._check(self.ACTIONS_APP)], self._outsider()
        )
        assert v["status"] == "failure"
        assert "不是来自配置的签名服务商" in " ".join(v["problems"])

    def test_impostor_does_not_crowd_out_the_real_provider(self, gate):
        """冒名的排在前面时，不许让真 provider 的结论被挤掉。"""
        v = gate.decide(
            "pull_request",
            self._policy(),
            [self._check(self.ACTIONS_APP), self._check(self.REAL_APP)],
            self._outsider(),
        )
        assert v["status"] == "success"

    def test_impostor_cannot_override_a_failing_real_provider(self, gate):
        v = gate.decide(
            "pull_request",
            self._policy(),
            [self._check(self.REAL_APP, "failure"), self._check(self.ACTIONS_APP)],
            self._outsider(),
        )
        assert v["status"] == "failure"

    def test_genuine_provider_still_passes(self, gate):
        v = gate.decide(
            "pull_request", self._policy(), [self._check(self.REAL_APP)], self._outsider()
        )
        assert v["status"] == "success"

    @pytest.mark.parametrize(
        "app",
        [
            pytest.param({"id": 34321, "slug": "someone-else"}, id="right-id-wrong-slug"),
            pytest.param({"id": 999999, "slug": "cla-assistant"}, id="right-slug-wrong-id"),
            pytest.param({"id": 999999, "slug": "someone-else"}, id="both-wrong"),
            pytest.param({}, id="no-app-at-all"),
        ],
    )
    def test_half_a_matching_identity_is_not_enough(self, gate, app):
        """**身份是两半，两半都要断言。**

        只断言其中一半，另一半的判据被删掉时没有任何用例会红——变异反证里
        「只认 slug 不认 id」正是这么漏过去的。slug 好猜（就是服务商的名字），
        整数 id 才是难冒充的那半，两个都要对。
        """
        v = gate.decide("pull_request", self._policy(), [self._check(app)], self._outsider())
        assert v["status"] == "failure"

    @pytest.mark.parametrize(
        "over",
        [
            {"app_slug": "github-actions"},
            {"app_id": 15368},
            {"app_slug": None},
            {"app_id": None},
            {"app_id": "34321"},
        ],
    )
    def test_policy_rejects_unusable_provider_identity(self, gate, over):
        """配置层就挡住：没有 App 身份、或把 GitHub Actions 当 provider。

        GitHub Actions 是**每个 PR 都能驱动**的那个 App，把它配成 provider
        等于把刚堵上的洞重新打开。
        """
        with pytest.raises(gate.ConfigError):
            gate.validate_policy(self._policy(**over))


# ═════════════════════════════════════════════ 9. 贡献者收集（单测）
class TestContributorCollection:
    def test_collects_author_commit_authors_and_co_authors(self, gate):
        commits = [
            {
                "sha": "deadbeef",
                "commit": {
                    "author": {"name": "A", "email": "a@users.noreply.github.com"},
                    "message": "x\n\nCo-authored-by: B <2+bee@users.noreply.github.com>",
                },
                "author": {"login": "alpha"},
            }
        ]
        found, unresolved = gate.collect_contributors("opener", commits)
        assert {c["login"].lower() for c in found} == {"opener", "alpha", "bee"}
        assert unresolved == []

    def test_commit_author_without_a_linked_account_is_unresolved(self, gate):
        commits = [
            {
                "sha": "cafe",
                "commit": {"author": {"name": "N", "email": "n@corp.example"}, "message": "x"},
                "author": None,
            }
        ]
        found, unresolved = gate.collect_contributors(None, commits)
        assert found == []
        assert unresolved and unresolved[0]["kind"] == "commit_author"

    def test_numeric_prefixed_noreply_resolves(self, gate):
        found, _ = gate.collect_contributors(
            None,
            [
                {
                    "sha": "1",
                    "commit": {
                        "author": {
                            "name": "E",
                            "email": "88193520+erwanjun@users.noreply.github.com",
                        },
                        "message": "x",
                    },
                    "author": None,
                }
            ],
        )
        assert [c["login"] for c in found] == ["erwanjun"]

    def test_malformed_commits_payload_raises(self, gate):
        with pytest.raises(gate.ConfigError):
            gate.collect_contributors(None, {"not": "a list"})
