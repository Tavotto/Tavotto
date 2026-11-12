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

GOOD = """# 错题本 · ci

> 文件头的引用行不算条目。

### L-001 测试与提交之间判退出码，不接 grep
- 类: 别这样
- Seen: 2026-10-31, 2026-11-01, 2026-11-02
- 为什么: 管道后的 grep 匹配到汇总行就返回 0，
  红了照样提交。
- 证据: #1575

### L-002 只在 Windows 生效的改动入队前整文件跑 Lab → docs/rules/ci/windows-lab.md
"""


@pytest.fixture
def root(tmp_path):
    """假仓库：升级目标存在且点名了 L-002。"""
    target = tmp_path / "docs" / "rules" / "ci" / "windows-lab.md"
    target.parent.mkdir(parents=True)
    target.write_text("（错题本 L-002 升级）\n", encoding="utf-8")
    return tmp_path


def _problems(text, root):
    return L.problems(text, "ci", root=root, today=TODAY)


def test_the_real_book_passes():
    assert L.check_all() == []


def test_the_real_book_is_routed_from_root_agents():
    """没人指向的错题本等于不存在：加载路径是根 AGENTS.md 的任务路由。"""
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "docs/rules/repo/lessons/" in agents


def test_every_book_is_a_rules_layer():
    """文件名就是路由：`docs/rules/<层>/` 多一层而这里没跟上，那一层的人读不到错题本。"""
    on_disk = {p.name for p in (ROOT / "docs" / "rules").iterdir() if p.is_dir()}
    assert set(L.LAYERS) == on_disk
    assert all((L.LESSONS / f"{x}.md").is_file() for x in L.LAYERS)


def test_good_sample_parses_with_continuation_and_due(root):
    lessons = L.parse(GOOD, "ci")
    assert [x.id for x in lessons] == ["L-001", "L-002"]
    assert lessons[0].why.endswith("红了照样提交。")
    assert lessons[0].due is True
    assert lessons[1].status == "promoted"
    assert lessons[1].rule == "只在 Windows 生效的改动入队前整文件跑 Lab"
    assert lessons[1].promoted_to == "docs/rules/ci/windows-lab.md"
    assert lessons[1].due is False
    assert _problems(GOOD, root) == []


def test_format_spec_in_readme_is_not_a_book():
    """README 里「条目格式」的示例不算条目；README 也不算一层。"""
    assert "### L-007" in (L.LESSONS / "README.md").read_text(encoding="utf-8")
    assert all(x.id != "L-007" for x in L.load())


@pytest.mark.parametrize(
    ("old", "new", "needle"),
    [
        ("- 类: 别这样", "- 类: 别犯", "类须是"),
        ("- 类: 别这样", "- 类: 别这样\n- 状态: active", "认不出的行"),
        ("2026-10-31, 2026-11-01", "2026-11-01, 2026-10-31", "升序"),
        ("2026-10-31, 2026-11-01", "2026-10-31, 2026-10-31", "升序"),
        ("2026-10-31, 2026-11-01", "2026-10-31, 11-01", "YYYY-MM-DD"),
        ("- 证据: #1575\n", "", "缺字段"),
        ("- 证据: #1575", "- 证据:", "不能空"),
        ("  红了照样提交。", "红了照样提交。", "认不出的行"),
        ("windows-lab.md\n", "windows-lab.md\n- 证据: #1\n", "只留标题一行"),
    ],
)
def test_each_parse_rule_goes_red(root, old, new, needle):
    bad = GOOD.replace(old, new, 1)
    assert bad != GOOD
    errs = _problems(bad, root)
    assert errs and needle in errs[0], errs


@pytest.mark.parametrize(
    ("old", "new", "needle"),
    [
        ("### L-001", "### L-003", "没按升序"),
        ("docs/rules/ci/windows-lab.md", "docs/rules/ci/no-such-file.md", "不存在"),
        ("2026-11-02\n", "2026-11-09\n", "未来"),
    ],
)
def test_each_book_rule_goes_red(root, old, new, needle):
    bad = GOOD.replace(old, new, 1)
    assert bad != GOOD
    errs = _problems(bad, root)
    assert any(needle in e for e in errs), errs


def test_promoted_target_must_name_the_lesson(root):
    """指针留下、规则却没写进细则：这条教训就丢了。"""
    (root / "docs" / "rules" / "ci" / "windows-lab.md").write_text("别的规则\n", encoding="utf-8")
    assert any("找不到 L-002" in e for e in _problems(GOOD, root))


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
def test_private_facts_go_red(root, leak):
    bad = GOOD.replace("红了照样提交。", f"红了照样提交。{leak}", 1)
    assert any("私有信息" in e for e in _problems(bad, root))


def test_version_numbers_are_not_mistaken_for_addresses(root):
    ok = GOOD.replace("证据: #1575", "证据: #1575（v0.19.2 → v0.20.0；matplotlib 3.10.1）", 1)
    assert _problems(ok, root) == []


def test_size_cap_is_per_layer_and_goes_red(root):
    big = GOOD + "\n" + "填充一行。\n" * (L.CAP_BYTES // 10)
    assert any("体积" in e for e in _problems(big, root))


def _write_books(directory, ci):
    directory.mkdir(exist_ok=True)
    for x in L.LAYERS:
        (directory / f"{x}.md").write_text("# 空\n", encoding="utf-8")
    (directory / "ci.md").write_text(ci, encoding="utf-8")


def test_numbers_are_unique_across_files(root, tmp_path):
    books = tmp_path / "lessons"
    _write_books(books, GOOD)
    assert L.check_all(books, root=root, today=TODAY) == []
    (books / "repo.md").write_text(GOOD.split("### L-002")[0], encoding="utf-8")
    assert any("编号重复" in e for e in L.check_all(books, root=root, today=TODAY))


def test_stray_or_missing_book_goes_red(root, tmp_path):
    books = tmp_path / "lessons"
    _write_books(books, GOOD)
    (books / "misc.md").write_text("# 没人读\n", encoding="utf-8")
    assert any("misc.md" in e for e in L.check_all(books, root=root, today=TODAY))
    (books / "misc.md").unlink()
    (books / "desktop.md").unlink()
    assert any("desktop.md" in e for e in L.check_all(books, root=root, today=TODAY))


def test_cli_exit_codes_and_stats(tmp_path, capsys):
    good = tmp_path / "good"
    _write_books(good, GOOD.split("### L-002")[0])
    bad = tmp_path / "bad"
    _write_books(bad, GOOD.replace("- 类: 别这样", "- 类: 别犯", 1))
    assert L.main(["--check", "--dir", str(bad)]) == 1
    assert L.main(["--due", "--dir", str(bad)]) == 1
    capsys.readouterr()
    assert L.main(["--due", "--dir", str(good)]) == 0
    assert "L-001" in capsys.readouterr().out
    assert L.main(["--stats", "--dir", str(good)]) == 0
    assert "L-002" in capsys.readouterr().out  # 下一个编号


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # 每日复盘 04:30（北京）开工时 UTC 还是前一天 20:30：Seen 写当天北京日期，不许被判成未来（#1656）
        (dt.datetime(2026, 11, 2, 20, 30, tzinfo=dt.timezone.utc), dt.date(2026, 11, 3)),
        # 跨零点前一刻：UTC 15:59 = 北京 23:59，仍是当天
        (dt.datetime(2026, 11, 2, 15, 59, tzinfo=dt.timezone.utc), dt.date(2026, 11, 2)),
        (dt.datetime(2026, 11, 2, 16, 0, tzinfo=dt.timezone.utc), dt.date(2026, 11, 3)),
        # 不带时区按 UTC 解读
        (dt.datetime(2026, 11, 2, 20, 30), dt.date(2026, 11, 3)),
    ],
)
def test_today_is_beijing_date(now, expected):
    assert L.beijing_today(now) == expected


def test_future_check_defaults_to_beijing_today(monkeypatch):
    """不传 today 时「未来」按北京日期判。伪造 CI runner 的时钟：UTC 11-02 20:30、本地 date.today() = 11-02；
    此刻北京已是 11-03，写当天北京日期的 Seen 不许红，11-04 照样红（#1656）。"""
    import types

    class RunnerDate(dt.date):
        @classmethod
        def today(cls):
            return dt.date(2026, 11, 2)

    class RunnerDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            utc = dt.datetime(2026, 11, 2, 20, 30, tzinfo=dt.timezone.utc)
            return utc if tz is None else utc.astimezone(tz)

    fake = types.SimpleNamespace(
        date=RunnerDate, datetime=RunnerDateTime, timezone=dt.timezone, timedelta=dt.timedelta
    )
    monkeypatch.setattr(L, "dt", fake)
    today_bj = GOOD.replace("2026-11-01, 2026-11-02\n", "2026-11-01, 2026-11-03\n", 1)
    assert today_bj != GOOD
    assert not any("未来" in e for e in L.problems(today_bj, "ci"))
    tomorrow = GOOD.replace("2026-11-01, 2026-11-02\n", "2026-11-01, 2026-11-04\n", 1)
    assert any("未来" in e for e in L.problems(tomorrow, "ci"))
