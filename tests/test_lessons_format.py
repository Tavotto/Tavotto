"""错题本 `docs/rules/repo/lessons.md` 的格式、体积与脱敏门禁。

错题本由每日复盘写入、由 agent 按层读取（根 `AGENTS.md`「任务路由」）。解析器只有一份：
`scripts/dev/lessons.py`；这里既判仓库里那份真文件，也用自造的坏样本反证每条检查真的会红——
空错题本上「全部合格」恒真，不能只靠真文件。
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _mod():
    spec = importlib.util.spec_from_file_location(
        "tavotto_lessons", ROOT / "scripts" / "dev" / "lessons.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass 解析注解时要在 sys.modules 里找到模块
    spec.loader.exec_module(mod)
    return mod


L = _mod()
TODAY = dt.date(2026, 11, 2)

GOOD = """# 错题本

## ci

### L-001 测试与提交之间判退出码，不接 grep
- 类: 别这样
- 状态: active
- Seen: 2026-10-31, 2026-11-01, 2026-11-02
- 为什么: 管道后的 grep 匹配到汇总行就返回 0，
  红了照样提交。
- 证据: #1575

### L-002 只在 Windows 生效的改动入队前整文件跑 Lab
- 类: 别这样
- 状态: promoted → docs/rules/ci/windows-lab.md
- Seen: 2026-11-01, 2026-11-02
- 为什么: PR 阶段不跑 Windows 后端分片。
- 证据: #1587, #1604
"""


def test_the_real_book_passes():
    text = L.LESSONS.read_text(encoding="utf-8")
    assert L.problems(text) == []


def test_the_real_book_is_routed_from_root_agents():
    """没人指向的错题本等于不存在：加载路径是根 AGENTS.md 的任务路由。"""
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "docs/rules/repo/lessons.md" in agents


def test_good_sample_parses_with_continuation_and_due():
    lessons = L.parse(GOOD)
    assert [x.id for x in lessons] == ["L-001", "L-002"]
    assert lessons[0].why.endswith("红了照样提交。")
    assert lessons[0].due is True
    assert lessons[1].status == "promoted"
    assert lessons[1].promoted_to == "docs/rules/ci/windows-lab.md"
    assert lessons[1].due is False
    assert L.problems(GOOD, today=TODAY) == []


def test_format_spec_inside_the_book_is_not_parsed_as_an_entry():
    """文件里「条目格式」那段示例在代码块里，不算条目。"""
    text = L.LESSONS.read_text(encoding="utf-8")
    assert "### L-007" in text
    assert all(x.id != "L-007" for x in L.parse(text))


@pytest.mark.parametrize(
    ("old", "new", "needle"),
    [
        ("- 类: 别这样\n- 状态: active", "- 类: 别犯\n- 状态: active", "类须是"),
        ("- 状态: active", "- 状态: done", "状态须是"),
        ("2026-10-31, 2026-11-01", "2026-11-01, 2026-10-31", "升序"),
        ("2026-10-31, 2026-11-01", "2026-10-31, 2026-10-31", "升序"),
        ("2026-10-31, 2026-11-01", "2026-10-31, 11-01", "YYYY-MM-DD"),
        ("- 证据: #1575\n", "", "缺字段"),
        ("- 证据: #1575", "- 证据:", "不能空"),
        ("## ci\n", "## misc\n", "不在任何层"),
        ("  红了照样提交。", "红了照样提交。", "认不出的行"),
    ],
)
def test_each_parse_rule_goes_red(old, new, needle):
    bad = GOOD.replace(old, new, 1)
    assert bad != GOOD
    errs = L.problems(bad, today=TODAY)
    assert errs and needle in errs[0], errs


@pytest.mark.parametrize(
    ("old", "new", "needle"),
    [
        ("### L-002", "### L-001", "编号重复"),
        ("### L-001", "### L-003", "没按升序"),
        ("docs/rules/ci/windows-lab.md", "docs/rules/ci/no-such-file.md", "不存在"),
        ("2026-11-01, 2026-11-02\n- 为什么: PR", "2026-11-01, 2026-11-09\n- 为什么: PR", "未来"),
    ],
)
def test_each_book_rule_goes_red(old, new, needle):
    bad = GOOD.replace(old, new, 1)
    assert bad != GOOD
    errs = L.problems(bad, today=TODAY)
    assert any(needle in e for e in errs), errs


@pytest.mark.parametrize(
    "leak",
    [
        "Lab 在 10.191.198.3 上复现",
        "一份用户反馈 TVD-AB12",
        "路径 /Users/someone/proj",
        r"路径 C:\Users\someone",
        "联系 someone@example.com",
        "api_key = xxx",
    ],
)
def test_private_facts_go_red(leak):
    bad = GOOD.replace("红了照样提交。", f"红了照样提交。{leak}", 1)
    assert any("私有信息" in e for e in L.problems(bad, today=TODAY))


def test_version_numbers_are_not_mistaken_for_addresses():
    ok = GOOD.replace("证据: #1575", "证据: #1575（v0.19.2 → v0.20.0；matplotlib 3.10.1）", 1)
    assert L.problems(ok, today=TODAY) == []


def test_size_cap_goes_red():
    big = GOOD + "\n## 附\n\n" + "填充一行。\n" * (L.CAP_BYTES // 10)
    assert any("体积" in e for e in L.problems(big, today=TODAY))


def test_cli_check_exit_code(tmp_path):
    good = tmp_path / "good.md"
    good.write_text(GOOD, encoding="utf-8")
    bad = tmp_path / "bad.md"
    bad.write_text(GOOD.replace("- 状态: active", "- 状态: done", 1), encoding="utf-8")
    assert L.main(["--check", "--file", str(bad)]) == 1
    assert L.main(["--due", "--file", str(good)]) == 0
