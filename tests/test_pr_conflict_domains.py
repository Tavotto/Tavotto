"""冲突域检查（scripts/ci/pr_conflict_domains.py + .github/conflict-domains.json）。

要点有两类：**判定对不对**（canvas.html 那类「不同源码、同一生成物」的
间接重叠必须被认出来）与**失败姿态对不对**（这是导航不是门禁——API 打不通
必须 warning + 退出 0，绝不拦产品 PR；输出里绝不带 token）。

全部平台无关、纯标准库、零网络——fetch 一律注入假实现。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI_DIR = ROOT / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import pr_conflict_domains as CD  # noqa: E402

CONFIG = ROOT / ".github" / "conflict-domains.json"
REPO = "Tavotto/Tavotto"


def _domains():
    return CD.load_config(CONFIG)


class FakeGitHub:
    """挂在 fetch 形参上的假 API：按 URL 分派，带真实的分页形状。"""

    def __init__(
        self,
        prs: dict[int, list[str]],
        titles: dict[int, str] | None = None,
        drafts: set[int] = frozenset(),
    ):
        self.prs = prs
        self.titles = titles or {}
        self.drafts = set(drafts)
        self.urls: list[str] = []

    def __call__(self, url: str, token):
        self.urls.append(url)
        page = int(url.split("page=")[-1])
        if "/pulls?" in url:
            rows = [
                {
                    "number": n,
                    "state": "open",
                    "title": self.titles.get(n, f"PR {n}"),
                    "draft": n in self.drafts,
                }
                for n in sorted(self.prs)
            ]
            return self._page(rows, page)
        for n, files in self.prs.items():
            if f"/pulls/{n}/files" in url:
                return self._page([{"filename": f} for f in files], page)
        raise AssertionError(f"FakeGitHub 不认识 {url}")

    @staticmethod
    def _page(rows, page):
        return rows[(page - 1) * 100 : page * 100]


# ============================================================ 配置本身
class TestRealConfig:
    def test_config_parses_and_declares_the_hot_domains(self):
        d = _domains()
        assert "ci-control-plane" in d and "adr-numbering" in d and "root-agent-contract" in d

    def test_no_domain_declares_an_untracked_generated_path(self):
        """声明了 `generated` 的域会把两个各改 sources 的 PR 判成「生成物重叠」。这个判定
        只在生成物**真在索引里**时成立——画布不入库之后（ADR 0043），本仓库没有任何跟踪
        的前端生成物，`mcp-widget` / `browser-playground` 两个域已删；这条钉住它们不回来。
        判据用 `git ls-files` 问索引，不读配置写了什么。"""
        import subprocess

        for name, spec in _domains().items():
            for pattern in spec.get("generated", []):
                probe = pattern.split("*")[0].rstrip("/")
                listed = subprocess.run(
                    ["git", "-C", str(ROOT), "ls-files", "--", probe],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=True,
                ).stdout.strip()
                assert listed, (
                    f"域 {name} 声明的 generated `{pattern}` 不在索引里——它会把无关的前端 PR "
                    f"判成生成物重叠。生成物不入库就别声明它。"
                )
        assert "mcp-widget" not in _domains()
        assert "browser-playground" not in _domains()

    def test_two_frontend_prs_touching_different_files_do_not_warn(self, capsys):
        """本次改造的核心验收（冲突域这一侧）：两个各改 web/src 不同文件的 PR，什么都不报。"""
        fake = FakeGitHub(
            {1: ["web/src/canvas/ContextBar.tsx"], 2: ["web/src/store/renderStore.ts"]}
        )
        rc = CD.run(REPO, 1, CONFIG, token=None, fetch=fake)
        out = capsys.readouterr().out
        assert rc == 0
        assert "::warning::" not in out
        assert json.loads(out.strip().splitlines()[-1])["overlapping_prs"] == []

    def test_every_declared_path_style_matches_something_plausible(self):
        d = _domains()
        assert CD.matches("AGENTS.md", d["root-agent-contract"]["files"])
        assert CD.matches(".github/workflows/ci.yml", d["ci-control-plane"]["files"])
        assert CD.matches("scripts/ci/aggregate_gate.py", d["ci-control-plane"]["files"])
        assert CD.matches("docs/release-notes/v0.1.1.md", d["release-control-plane"]["files"])

    def test_adr_numbering_is_a_declared_domain(self):
        """两个 PR 各加一份 ADR **不会**产生 Git 冲突（文件名不同），
        合完的 main 上却会有两个同号 ADR。2026-08-28 实测撞过一次。

        撞的是**编号空间**，不是路径——所以既有的 direct / generated 两条
        判据一条都够不着，只能靠「同域」把两个 PR 摆到一起。
        """
        d = _domains()
        spec = d["adr-numbering"]
        assert CD.matches("docs/adr/0022-complexity-aware-editor-preview.md", spec["files"])
        assert not CD.matches("docs/perf-baseline.md", spec["files"])
        assert spec["policy"] == "coordinate"

    def test_the_adr_advice_asks_you_to_check_instead_of_asserting_a_collision(self):
        """处方只许说「去核对」，不许断言「你们撞了」。

        域只知道两个 PR 都动了 `docs/adr/**`，**不知道它们的编号**——一个改
        0008、一个加 0021 完全不撞。断言一件检查本身证不了的事比不说更坏：
        判据一旦对，人更会信它说的那句话（Codex 在 #194 上指出）。
        """
        advice = _domains()["adr-numbering"]["advice"]
        assert "核对" in advice, "处方要让人去核对编号"
        # 不许出现无条件的断言句式
        for claimed in ("合完 main 上会有两个同号", "你们撞了", "一定会撞"):
            assert claimed not in advice, f"处方断言了它证不了的事：{claimed}"

    def test_the_adr_domain_carries_its_own_advice(self):
        """通用兜底文案在这个域上是**对的判据 + 错的处方**：它说「rebase 后
        重跑快线即可」，而 rebase 根本不会报冲突。判据一旦对，人更会信它说
        的那句话，所以这个域必须自带处方。"""
        spec = _domains()["adr-numbering"]
        assert spec.get("advice"), "adr-numbering 必须自带处方"
        assert CD.advice(spec["policy"], False, spec["advice"]) == spec["advice"]
        # 兜底那句在这里是错的——确认它确实被顶掉了
        assert "重跑快线" not in CD.advice(spec["policy"], False, spec["advice"])

    def test_coordinate_is_an_accepted_policy(self, tmp_path):
        p = tmp_path / "c.json"
        p.write_text(
            '{"domains": {"x": {"files": ["a"], "policy": "coordinate"}}}', encoding="utf-8"
        )
        assert CD.load_config(p)["x"]["policy"] == "coordinate"

    def test_a_non_string_advice_is_rejected(self, tmp_path):
        """处方要么是一句话，要么没有。给个列表进来的话它会被原样拼进
        Markdown 表格里——那一格从此谁也读不懂。"""
        p = tmp_path / "c.json"
        p.write_text(
            '{"domains": {"x": {"files": ["a"], "policy": "serialize", "advice": ["x"]}}}',
            encoding="utf-8",
        )
        with pytest.raises(CD.ConfigError):
            CD.load_config(p)

    def test_broken_config_shapes_are_rejected(self, tmp_path):
        p = tmp_path / "c.json"
        p.write_text('{"domains": {}}', encoding="utf-8")
        with pytest.raises(CD.ConfigError):
            CD.load_config(p)
        p.write_text('{"domains": {"x": {"files": ["a"], "policy": "??"}}}', encoding="utf-8")
        with pytest.raises(CD.ConfigError):
            CD.load_config(p)


# ============================================================ glob
class TestGlob:
    @pytest.mark.parametrize(
        "pattern,path,ok",
        [
            ("web/src/**", "web/src/a.ts", True),
            ("web/src/**", "web/src/deep/nested/b.tsx", True),
            ("web/src/**", "web/dist/a.ts", False),
            (".github/workflows/**", ".github/workflows/ci.yml", True),
            ("AGENTS.md", "AGENTS.md", True),
            ("AGENTS.md", "docs/AGENTS.md", False),
            ("scripts/ci/*.py", "scripts/ci/soak.py", True),
            ("scripts/ci/*.py", "scripts/ci/sub/x.py", False),
            ("tests/golden/**", "tests/golden/patch_vectors.json", True),
        ],
    )
    def test_matching(self, pattern, path, ok):
        assert CD.matches(path, [pattern]) is ok

    def test_a_file_can_belong_to_multiple_domains(self):
        d = _domains()
        hits = CD.classify([".github/workflows/release.yml"], d)
        assert "ci-control-plane" in hits and "release-control-plane" in hits


# ============================================================ 重叠判定
#: 机制用例用的**合成**域：一个声明了 sources + generated 的域。真实配置里已经没有这种域
#: （生成物不入库了），但判定器的「不同源码、同一生成物」逻辑一个字没变，仍要看住。
SYNTHETIC_DOMAINS = {
    "domains": {
        "synthetic-widget": {
            "sources": ["web/src/**", "scripts/build_synthetic.py"],
            "generated": ["generated/bundle.html"],
            "policy": "stack-or-train",
        },
        "root-agent-contract": {"files": ["AGENTS.md", "CLAUDE.md"], "policy": "serialize"},
    }
}


@pytest.fixture()
def synthetic_config(tmp_path):
    p = tmp_path / "conflict-domains.json"
    p.write_text(json.dumps(SYNTHETIC_DOMAINS), encoding="utf-8")
    return p


class TestOverlaps:
    def _run(self, my_pr, prs, config=CONFIG, **kw):
        fake = FakeGitHub(prs, **kw)
        rc = CD.run(REPO, my_pr, config, token=None, fetch=fake)
        return rc, fake

    def test_two_prs_both_touching_the_generated_bundle(self, capsys, synthetic_config):
        rc, _ = self._run(
            1,
            {1: ["generated/bundle.html"], 2: ["generated/bundle.html"]},
            config=synthetic_config,
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "::warning::" in out and "synthetic-widget" in out
        payload = json.loads(out.strip().splitlines()[-1])
        assert payload["overlapping_prs"] == [2]

    def test_source_change_vs_generated_change_is_an_indirect_overlap(
        self, capsys, synthetic_config
    ):
        """一个改 web/src、一个带生成物——文件毫无交集，仍然要报（合成域）。"""
        rc, _ = self._run(
            1,
            {1: ["web/src/canvas/ContextBar.tsx"], 2: ["generated/bundle.html"]},
            config=synthetic_config,
        )
        out = capsys.readouterr().out
        assert "生成物重叠" in out
        assert json.loads(out.strip().splitlines()[-1])["overlapping_prs"] == [2]

    def test_two_source_edits_in_a_generated_domain_are_a_generated_overlap(
        self, capsys, tmp_path, monkeypatch, synthetic_config
    ):
        """两个 PR 各改 web/src 的**不同**文件、谁都没带生成物——在一个**声明了 generated**
        的域里（合成域），合并时各自重建的仍是同一个 bundle。判定按域声明走（#120 评审 P2）：
        sources×sources 就是生成物重叠，建议 train。真实配置里已经没有这种域（ADR 0043），
        所以真实配置下同样的两个 PR 什么都不报——见 TestRealConfig。"""
        summary = tmp_path / "s.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        rc, _ = self._run(
            1,
            {1: ["web/src/canvas/ContextBar.tsx"], 2: ["web/src/store/renderStore.ts"]},
            config=synthetic_config,
        )
        out = capsys.readouterr().out
        assert "生成物重叠" in out, "warning 一档就要说出是生成物撞点"
        assert "train" in summary.read_text(encoding="utf-8"), "建议里必须指向 train 工作流"

    def test_domains_without_generated_do_not_invent_an_overlap(
        self, capsys, tmp_path, monkeypatch
    ):
        """没有 generated 声明的域（如 root-agent-contract）不许把同域
        误报成生成物重叠——那会让 serialize 域的建议文案错位。"""
        summary = tmp_path / "s.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        rc, _ = self._run(1, {1: ["AGENTS.md"], 2: ["CLAUDE.md"]})
        out = capsys.readouterr().out
        assert "::warning::" in out and "生成物重叠" not in out
        text = summary.read_text(encoding="utf-8")
        assert "串行" in text, "serialize 域的建议必须是排队，不是 train"

    def test_unrelated_prs_do_not_warn(self, capsys):
        rc, _ = self._run(1, {1: ["src/tavotto/app.py"], 2: ["docs/i18n.md"]})
        out = capsys.readouterr().out
        assert "::warning::" not in out
        assert json.loads(out.strip().splitlines()[-1])["overlapping_prs"] == []

    def test_draft_prs_still_warn(self, capsys, synthetic_config):
        """draft 也在开发、也会撞——不因为暂时不能合并就装看不见。"""
        rc, _ = self._run(
            1, {1: ["web/src/a.ts"], 2: ["web/src/b.ts"]}, config=synthetic_config, drafts={2}
        )
        out = capsys.readouterr().out
        assert json.loads(out.strip().splitlines()[-1])["overlapping_prs"] == [2]

    def test_closed_prs_never_enter(self):
        """查询本身就限定 state=open；这里钉住过滤器不被拿掉。"""
        fake = FakeGitHub({1: ["web/src/a.ts"]})
        CD.run(REPO, 1, CONFIG, token=None, fetch=fake)
        assert any("state=open" in u for u in fake.urls)

    def test_own_pr_is_not_its_own_conflict(self, capsys):
        rc, _ = self._run(7, {7: ["web/src/a.ts"]})
        assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["overlapping_prs"] == []

    def test_stack_parent_and_child_are_reported_as_overlap(self, capsys):
        """同一个 stack 的父子 PR 改同一批文件——照报。检查不知道 stack
        关系，报出来让作者自己确认「这是刻意的」比静默漏掉安全。"""
        rc, _ = self._run(
            1, {1: ["scripts/ci/aggregate_gate.py"], 2: ["scripts/ci/aggregate_gate.py"]}
        )
        out = capsys.readouterr().out
        assert "ci-control-plane" in out


# ============================================================ 分页与失败姿态
class TestApiBehaviour:
    def test_pagination_walks_past_one_hundred(self):
        files = [f"web/src/f{i}.ts" for i in range(230)]
        fake = FakeGitHub({1: files})
        got = CD.pr_files(REPO, 1, None, fetch=fake)
        assert len(got) == 230
        assert sum("/pulls/1/files" in u for u in fake.urls) == 3

    def test_api_unavailable_warns_and_exits_zero(self, capsys):
        def down(url, token):
            raise CD.ApiUnavailable("GitHub API 请求失败")

        rc = CD.run(REPO, 1, CONFIG, token=None, fetch=down)
        out = capsys.readouterr().out
        assert rc == 0, "咨询性检查绝不阻断产品 CI"
        assert "::warning::" in out

    def test_missing_config_warns_and_exits_zero(self, capsys, tmp_path):
        rc = CD.run(REPO, 1, tmp_path / "nope.json", token=None, fetch=FakeGitHub({1: []}))
        assert rc == 0
        assert "::warning::" in capsys.readouterr().out

    def test_output_never_contains_the_token(self, capsys, monkeypatch):
        """token 只进请求头；warning / summary / JSON 里一个字节都不许出现。"""
        secret = "ghs_SECRETSECRETSECRET"
        monkeypatch.setenv("GITHUB_TOKEN", secret)

        def down(url, token):
            raise CD.ApiUnavailable(f"GitHub API 请求失败（{url.split('?')[0]}）：URLError")

        CD.run(REPO, 1, CONFIG, token=secret, fetch=down)
        captured = capsys.readouterr()
        assert secret not in captured.out + captured.err

    def test_no_hits_skips_fetching_other_prs_files(self):
        """自己不落任何域时不该去翻每个 open PR 的文件清单（API 配额）。"""
        fake = FakeGitHub({1: ["docs/i18n.md"], 2: ["web/src/a.ts"]})
        CD.run(REPO, 1, CONFIG, token=None, fetch=fake)
        assert not any("/pulls/2/files" in u for u in fake.urls)


# ============================================================ workflow 契约
class TestWorkflowContract:
    WF = (ROOT / ".github" / "workflows" / "pr-conflict-domains.yml").read_text(encoding="utf-8")

    def _code(self):
        return "\n".join(ln for ln in self.WF.splitlines() if not ln.lstrip().startswith("#"))

    def test_read_only_permissions(self):
        code = self._code()
        assert "contents: read" in code and "pull-requests: read" in code
        for esc in ("contents: write", "pull-requests: write", "issues: write"):
            assert esc not in code

    def test_checks_out_the_trusted_default_branch(self):
        """fork PR 改过的脚本不在这里执行——checkout 钉在默认分支上。"""
        assert "ref: ${{ github.event.repository.default_branch }}" in self.WF

    def test_it_is_not_wired_into_the_merge_queue(self):
        assert "merge_group" not in self._code()

    def test_it_is_not_a_required_context_in_the_migration_tool(self):
        import merge_queue_ruleset as MQ

        assert "conflict domains (advisory)" not in MQ.GATE_CONTEXTS

    def test_bootstrap_window_is_a_skip_not_a_failure(self):
        """脚本还没落到默认分支时必须优雅跳过。

        「跑默认分支上的可信脚本」与「脚本还在 PR 里」在 bootstrap 期是
        冲突的——#120 第一轮就红在这：checkout 了 main，脚本不存在，
        python3 退出码 2。同样的形状在未来仍会出现（fork 的默认分支落后、
        脚本被移动改名的过渡 PR），所以守卫要留着，不是一次性补丁。"""
        code = self._code()
        assert "if [ ! -f scripts/ci/pr_conflict_domains.py ]" in code, (
            "bootstrap 守卫没了——脚本缺席时这个咨询检查会红，阻断的正是产品 PR"
        )
        guard = code.split("if [ ! -f scripts/ci/pr_conflict_domains.py ]", 1)[1]
        assert "exit 0" in guard.split("fi", 1)[0], "守卫必须以 exit 0 收尾"
