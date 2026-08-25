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

    def __init__(self, prs: dict[int, list[str]], titles: dict[int, str] | None = None,
                 drafts: set[int] = frozenset()):
        self.prs = prs
        self.titles = titles or {}
        self.drafts = set(drafts)
        self.urls: list[str] = []

    def __call__(self, url: str, token):
        self.urls.append(url)
        page = int(url.split("page=")[-1])
        if "/pulls?" in url:
            rows = [{"number": n, "state": "open", "title": self.titles.get(n, f"PR {n}"),
                     "draft": n in self.drafts} for n in sorted(self.prs)]
            return self._page(rows, page)
        for n, files in self.prs.items():
            if f"/pulls/{n}/files" in url:
                return self._page([{"filename": f} for f in files], page)
        raise AssertionError(f"FakeGitHub 不认识 {url}")

    @staticmethod
    def _page(rows, page):
        return rows[(page - 1) * 100: page * 100]


# ============================================================ 配置本身
class TestRealConfig:
    def test_config_parses_and_declares_the_hot_domains(self):
        d = _domains()
        assert "mcp-widget" in d and "ci-control-plane" in d

    def test_web_src_and_canvas_html_share_the_widget_domain(self):
        """本仓库最热的冲突：web/src 的改动经 build_mcp_widget.py 落进
        canvas.html。两端必须在同一个域里，否则检查对它视而不见。"""
        d = _domains()
        spec = d["mcp-widget"]
        assert CD.matches("web/src/canvas/ContextBar.tsx", spec["sources"])
        assert CD.matches("codex-plugin/mcp/widget/canvas.html", spec["generated"])

    def test_every_declared_path_style_matches_something_plausible(self):
        d = _domains()
        assert CD.matches("AGENTS.md", d["root-agent-contract"]["files"])
        assert CD.matches(".github/workflows/ci.yml", d["ci-control-plane"]["files"])
        assert CD.matches("scripts/ci/aggregate_gate.py", d["ci-control-plane"]["files"])
        assert CD.matches("docs/release-notes/v0.1.1.md",
                          d["release-control-plane"]["files"])

    def test_broken_config_shapes_are_rejected(self, tmp_path):
        p = tmp_path / "c.json"
        p.write_text('{"domains": {}}', encoding="utf-8")
        with pytest.raises(CD.ConfigError):
            CD.load_config(p)
        p.write_text('{"domains": {"x": {"files": ["a"], "policy": "??"}}}',
                     encoding="utf-8")
        with pytest.raises(CD.ConfigError):
            CD.load_config(p)


# ============================================================ glob
class TestGlob:
    @pytest.mark.parametrize("pattern,path,ok", [
        ("web/src/**", "web/src/a.ts", True),
        ("web/src/**", "web/src/deep/nested/b.tsx", True),
        ("web/src/**", "web/dist/a.ts", False),
        (".github/workflows/**", ".github/workflows/ci.yml", True),
        ("AGENTS.md", "AGENTS.md", True),
        ("AGENTS.md", "docs/AGENTS.md", False),
        ("scripts/ci/*.py", "scripts/ci/soak.py", True),
        ("scripts/ci/*.py", "scripts/ci/sub/x.py", False),
        ("tests/golden/**", "tests/golden/patch_vectors.json", True),
    ])
    def test_matching(self, pattern, path, ok):
        assert CD.matches(path, [pattern]) is ok

    def test_a_file_can_belong_to_multiple_domains(self):
        d = _domains()
        hits = CD.classify([".github/workflows/release.yml"], d)
        assert "ci-control-plane" in hits and "release-control-plane" in hits


# ============================================================ 重叠判定
class TestOverlaps:
    def _run(self, my_pr, prs, **kw):
        fake = FakeGitHub(prs, **kw)
        rc = CD.run(REPO, my_pr, CONFIG, token=None, fetch=fake)
        return rc, fake

    def test_two_prs_both_touching_canvas_html(self, capsys):
        rc, _ = self._run(1, {1: ["codex-plugin/mcp/widget/canvas.html"],
                              2: ["codex-plugin/mcp/widget/canvas.html"]})
        out = capsys.readouterr().out
        assert rc == 0
        assert "::warning::" in out and "mcp-widget" in out
        payload = json.loads(out.strip().splitlines()[-1])
        assert payload["overlapping_prs"] == [2]

    def test_source_change_vs_generated_change_is_an_indirect_overlap(self, capsys):
        """一个改 web/src、一个带 canvas.html——文件毫无交集，仍然要报。"""
        rc, _ = self._run(1, {1: ["web/src/canvas/ContextBar.tsx"],
                              2: ["codex-plugin/mcp/widget/canvas.html"]})
        out = capsys.readouterr().out
        assert "生成物重叠" in out
        assert json.loads(out.strip().splitlines()[-1])["overlapping_prs"] == [2]

    def test_unrelated_prs_do_not_warn(self, capsys):
        rc, _ = self._run(1, {1: ["src/tavotto/app.py"],
                              2: ["docs/i18n.md"]})
        out = capsys.readouterr().out
        assert "::warning::" not in out
        assert json.loads(out.strip().splitlines()[-1])["overlapping_prs"] == []

    def test_draft_prs_still_warn(self, capsys):
        """draft 也在开发、也会撞——不因为暂时不能合并就装看不见。"""
        rc, _ = self._run(1, {1: ["web/src/a.ts"],
                              2: ["web/src/b.ts"]}, drafts={2})
        out = capsys.readouterr().out
        assert json.loads(out.strip().splitlines()[-1])["overlapping_prs"] == [2]

    def test_closed_prs_never_enter(self):
        """查询本身就限定 state=open；这里钉住过滤器不被拿掉。"""
        fake = FakeGitHub({1: ["web/src/a.ts"]})
        CD.run(REPO, 1, CONFIG, token=None, fetch=fake)
        assert any("state=open" in u for u in fake.urls)

    def test_own_pr_is_not_its_own_conflict(self, capsys):
        rc, _ = self._run(7, {7: ["web/src/a.ts"]})
        assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])[
            "overlapping_prs"] == []

    def test_stack_parent_and_child_are_reported_as_overlap(self, capsys):
        """同一个 stack 的父子 PR 改同一批文件——照报。检查不知道 stack
        关系，报出来让作者自己确认「这是刻意的」比静默漏掉安全。"""
        rc, _ = self._run(1, {1: ["scripts/ci/aggregate_gate.py"],
                              2: ["scripts/ci/aggregate_gate.py"]})
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
        rc = CD.run(REPO, 1, tmp_path / "nope.json", token=None,
                    fetch=FakeGitHub({1: []}))
        assert rc == 0
        assert "::warning::" in capsys.readouterr().out

    def test_output_never_contains_the_token(self, capsys, monkeypatch):
        """token 只进请求头；warning / summary / JSON 里一个字节都不许出现。"""
        secret = "ghs_SECRETSECRETSECRET"
        monkeypatch.setenv("GITHUB_TOKEN", secret)

        def down(url, token):
            raise CD.ApiUnavailable(f"GitHub API 请求失败（{url.split('?')[0]}）：URLError")
        rc = CD.run(REPO, 1, CONFIG, token=secret, fetch=down)
        captured = capsys.readouterr()
        assert secret not in captured.out + captured.err

    def test_no_hits_skips_fetching_other_prs_files(self):
        """自己不落任何域时不该去翻每个 open PR 的文件清单（API 配额）。"""
        fake = FakeGitHub({1: ["docs/i18n.md"], 2: ["web/src/a.ts"]})
        CD.run(REPO, 1, CONFIG, token=None, fetch=fake)
        assert not any("/pulls/2/files" in u for u in fake.urls)


# ============================================================ workflow 契约
class TestWorkflowContract:
    WF = (ROOT / ".github" / "workflows" / "pr-conflict-domains.yml").read_text(
        encoding="utf-8")

    def _code(self):
        return "\n".join(ln for ln in self.WF.splitlines()
                         if not ln.lstrip().startswith("#"))

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
