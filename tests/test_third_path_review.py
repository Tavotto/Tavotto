"""第三路径审查（docs/rules/repo/third-path-review.md）里机器判得了的那部分。

两件事：

1. **三处同源**：规则全文（字段清单与结论五档）、PR 模板那一节、校验脚本的 `FIELDS` / `VERDICTS`
   是同一份清单的三个出处——任一处改了另两处没跟，作者照模板填的正文就会被脚本判错，或者规则里
   说的字段脚本根本不查。
2. **结论与 PR 的实际效果不矛盾**：`temporary_mitigation` / `promising_unverified` 声明根问题没解决，
   正文里却用了 GitHub 的关闭关键字（合并即自动关掉根 issue）——这是「把阶段性交付写成最终解决」
   在机器上看得见的那一种形状。

判不了的（审查做得好不好）不判，见规则第六节。不 import 产品代码。
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RULE = ROOT / "docs" / "rules" / "repo" / "third-path-review.md"
TEMPLATE = ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
SCRIPT = ROOT / "scripts" / "dev" / "third_path_review.py"


def _load():
    spec = importlib.util.spec_from_file_location("third_path_review", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TPR = _load()


def _body(
    verdict: str,
    *,
    tracking: str = "#1200",
    extra: str = "",
    evidence: str = "复现 tests/x.py 红→绿",
) -> str:
    return f"""## 基线与风险

- 风险等级：中
{extra}
## Third-Path Review

- 结论：{verdict}
- 用户目标：PNG 面板带 override 导出时预检按真实输出判
- 现有冲突：前端按输入后缀判位图
- 共同前提：输出事实由两处各自猜
- 第三路径候选：后端导出计划作为唯一权威
- 证据：{evidence}
- 最终决定：预检消费导出计划
- 遗留限制：无
- 跟踪：{tracking}

## How it was verified
"""


# ---------------------------------------------------------------- 一、三处同源


def _rule_template_block() -> str:
    text = RULE.read_text(encoding="utf-8")
    m = re.search(r"```text\n(## Third-Path Review\n.*?)```", text, re.S)
    assert m, "规则里没有 PR 记录的模板代码块"
    return m.group(1)


def _rule_verdict_table() -> list[str]:
    text = RULE.read_text(encoding="utf-8")
    sec = text.split("**F. 给结论**", 1)[1].split("## 三、", 1)[0]
    return re.findall(r"^\| `([a-z_]+)` \|", sec, re.M)


def test_rule_template_and_script_list_the_same_fields():
    """规则的记录模板、PR 模板、脚本的 `FIELDS`：字段与顺序逐个相同。"""
    rule_fields = tuple(TPR.FIELD_LINE.findall(_rule_template_block()))
    template_section = TPR.extract_section(TEMPLATE.read_text(encoding="utf-8"))
    assert template_section is not None, "PR 模板里没有 ## Third-Path Review 一节"
    template_fields = tuple(TPR.FIELD_LINE.findall(template_section))
    # 前提：真的解析到了一串字段（空元组相等恒真）。
    assert len(TPR.FIELDS) == 9
    assert tuple(k for k, _ in rule_fields) == TPR.FIELDS
    assert tuple(k for k, _ in template_fields) == TPR.FIELDS


def test_rule_verdict_table_matches_the_script():
    verdicts = _rule_verdict_table()
    assert len(verdicts) == 5
    assert tuple(verdicts) == TPR.VERDICTS


def test_untouched_template_is_not_accepted():
    """照模板原样留着占位的那一节必须被判出来（否则「填了这一节」可以只是没删）。"""
    problems = TPR.check(TEMPLATE.read_text(encoding="utf-8"))
    assert problems and "结论必须是" in problems[0]


def test_the_rule_is_routed_from_root_and_review_tiers():
    """加载路径：根 AGENTS.md（每次会话读）与评审分档文档都指向规则全文。"""
    rel = "docs/rules/repo/third-path-review.md"
    assert rel in (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert rel in (ROOT / "docs" / "ci" / "pr-review-tiers.md").read_text(encoding="utf-8")
    assert rel in TEMPLATE.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 二、结论与 PR 的实际效果


def test_no_section_means_not_triggered():
    assert TPR.check("## What this changes\n\nFixes #12\n") == []


def test_complete_structural_record_passes():
    assert TPR.check(_body("structural_solution_found", tracking="无", extra="Fixes #477\n")) == []


@pytest.mark.parametrize(
    "closing",
    [
        "Fixes #1200",
        "closes #1200",
        "Resolved: #1200",
        "fixed Tavotto/Tavotto#1200",
        "Closes https://github.com/Tavotto/Tavotto/issues/1200",
    ],
)
def test_mitigation_that_would_auto_close_the_root_issue_is_flagged(closing: str):
    body = _body(
        "temporary_mitigation",
        extra=f"Temporary mitigation — root problem unresolved\n\n{closing}\n",
    )
    problems = TPR.check(body)
    assert any("关闭关键字" in p for p in problems), problems


def test_mitigation_with_refs_and_banner_passes():
    body = _body(
        "temporary_mitigation",
        extra="Temporary mitigation — root problem unresolved\n\nRefs #1200\n",
    )
    assert TPR.check(body) == []


def test_mitigation_without_banner_is_flagged():
    problems = TPR.check(_body("temporary_mitigation", extra="Refs #1200\n"))
    assert any("Temporary mitigation" in p for p in problems), problems


@pytest.mark.parametrize("tracking", ["", "<#根因 issue>", "之后再开", "无"])
def test_open_root_verdicts_need_an_issue_number(tracking: str):
    problems = TPR.check(_body("promising_unverified", tracking=tracking))
    assert any("引用根因 issue" in p for p in problems), problems


def test_closing_words_inside_html_comments_and_lookalikes_do_not_count():
    """注释里的说明、`prefix #3` / `fixture #4` 这类词不是关闭关键字。"""
    body = _body(
        "promising_unverified",
        extra="<!-- 别写 Fixes #1200 -->\nprefix #3 与 fixture #4 无关\n",
    )
    assert TPR.check(body) == []


def test_unknown_verdict_including_runtime_verdict_names_is_flagged():
    """产品运行时的验证状态名（rendercore.inspector.VERDICTS 那一套）不是审查结论。"""
    for verdict in ("verified", "fixed", ""):
        problems = TPR.check(_body(verdict))
        assert problems and "结论必须是" in problems[0], verdict


def test_tradeoff_without_evidence_is_flagged():
    problems = TPR.check(_body("necessary_tradeoff", tracking="无", evidence="待补"))
    assert any("证据" in p for p in problems), problems


def test_not_applicable_needs_only_the_verdict():
    assert TPR.check("## Third-Path Review\n\n- 结论：not_applicable\n") == []


# ---------------------------------------------------------------- 三、命令行的退出码


def _run(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_cli_exit_codes_distinguish_absent_bad_and_unreadable(tmp_path: Path):
    good = tmp_path / "good.md"
    good.write_text(_body("structural_solution_found", tracking="无"), encoding="utf-8")
    bad = tmp_path / "bad.md"
    bad.write_text(
        _body(
            "temporary_mitigation",
            extra="Temporary mitigation — root problem unresolved\nFixes #1\n",
        ),
        encoding="utf-8",
    )
    assert _run(str(good)).returncode == 0
    assert _run(str(bad)).returncode == 1
    assert _run("-", stdin="## What this changes\n").returncode == 0  # 没有这一节
    # 「没取到」不是「没有」：空正文、读不到的文件都不是 0。
    assert _run("-", stdin="  \n").returncode == 2
    assert _run(str(tmp_path / "missing.md")).returncode == 2
