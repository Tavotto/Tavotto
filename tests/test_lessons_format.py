"""错题本 `docs/rules/repo/lessons.md` 的格式、体积与脱敏门禁。

错题本由每日复盘写入、由 agent 按层读取（根 `AGENTS.md`「任务路由」）。解析器只有一份：
`scripts/dev/lessons.py`；这里既判仓库里那份真文件，也用自造的坏样本反证每条检查真的会红——
空错题本上「全部合格」恒真，不能只靠真文件。
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import subprocess
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
#: 通用条目（触发: *）条数上限；要加先问是不是真的「任何改动都适用」。
MAX_UNIVERSAL = 13
TODAY = dt.date(2026, 11, 2)
#: 假仓库的 `git ls-files`：触发路径要在这里匹配到至少一个文件。
TRACKED = ["tests/test_a.py", "tests/test_b.py", "scripts/dev/x.py", "scripts/dev/sub/y.py"]

GOOD = """# 错题本 · ci

> 文件头的引用行不算条目。

### L-001 测试与提交之间判退出码，不接 grep
- 类: 别这样
- 触发: tests/test_a*, scripts/dev/
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
    target.write_text(
        "- **规则（错题本 L-002 升级，2026-11-01）。** 看护 `tests/test_a.py`。\n", encoding="utf-8"
    )
    return tmp_path


def _problems(text, root):
    return L.problems(text, "ci", root=root, today=TODAY, tracked=TRACKED)


def test_the_real_book_passes():
    assert L.check_all() == []


def test_the_real_book_triggers_are_backed_by_git_ls_files():
    """真文件：每条未升级条目都有触发，每个路径条目匹配至少一个已跟踪文件（空条目 = 空转）。"""
    tracked = L.tracked_files(ROOT)
    active = [x for x in L.load() if x.status == "active"]
    assert active, "错题本里一条未升级的都没有：这条检查在空集上恒真"
    for x in active:
        assert x.triggers, x.id
        for t in x.triggers:
            if t != L.UNIVERSAL:
                assert any(L.trigger_matches(t, p) for p in tracked), (x.id, t)


def test_universal_triggers_stay_rare():
    """「*」= 每次都适用的通用纪律；多了就等于没有触发，评审照样得整篇读。"""
    universal = [x.id for x in L.load() if x.universal]
    assert len(universal) <= MAX_UNIVERSAL, universal


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
    assert lessons[0].triggers == ("tests/test_a*", "scripts/dev/")
    assert lessons[1].triggers == ()
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


@pytest.mark.parametrize(
    ("new", "needle"),
    [
        ("", "缺「触发」"),  # 删掉触发字段
        ("- 触发: no/such/dir/", "没匹配到"),
        ("- 触发: scripts/dev/nope.py", "没匹配到"),
        ("- 触发: tests/test_zzz*", "没匹配到"),
        ("- 触发: tests/*.py", "不支持别的通配"),
        ("- 触发: scripts/**/x.py", "不支持别的通配"),
        ("- 触发: /scripts/dev/", "相对"),
        ("- 触发: ../x", "相对"),
        ("- 触发: *, scripts/dev/", "单独"),
        ("- 触发: scripts/dev/, scripts/dev/", "重复"),
        ("- 触发: scripts/dev/,, tests/test_a.py", None),  # 空段被忽略，不算错
    ],
)
def test_each_trigger_rule_goes_red(root, new, needle):
    old = "- 触发: tests/test_a*, scripts/dev/\n"
    bad = GOOD.replace(old, (new + "\n") if new else "", 1)
    assert bad != GOOD
    errs = _problems(bad, root)
    if needle is None:
        assert errs == []
    else:
        assert any(needle in e for e in errs), errs


def test_universal_trigger_is_accepted_alone(root):
    ok = GOOD.replace("- 触发: tests/test_a*, scripts/dev/", "- 触发: *", 1)
    assert _problems(ok, root) == []
    assert L.parse(ok, "ci")[0].universal


def test_promoted_pointer_needs_no_trigger(root):
    """已升级的条目只留标题一行，不要求也不接受触发字段。"""
    assert [x.id for x in L.parse(GOOD, "ci") if x.status == "promoted"] == ["L-002"]
    assert _problems(GOOD, root) == []


@pytest.mark.parametrize(
    ("entry", "path", "hit"),
    [
        ("scripts/dev/", "scripts/dev/x.py", True),
        ("scripts/dev/", "scripts/dev/sub/y.py", True),  # 目录前缀含子目录
        ("scripts/dev/", "scripts/devx/x.py", False),  # 不是子串匹配
        ("scripts/dev", "scripts/dev/x.py", False),  # 无斜杠 = 精确路径
        ("scripts/dev/", "other/scripts/dev/x.py", False),  # 前缀从路径开头算，不是子串
        ("scripts/dev/x.py", "scripts/dev/x.py", True),
        ("scripts/dev/x.py", "scripts/dev/x.pyc", False),
        ("tests/test_a*", "tests/test_a.py", True),
        ("tests/test_a*", "tests/test_abc.py", True),
        ("tests/test_a*", "tests/sub/test_a.py", False),  # 前缀只在同一目录
        ("tests/test_a*", "tests/test_b.py", False),
        ("tests/*", "tests/test_b.py", True),
        ("tests/*", "tests/sub/test_b.py", False),
        ("*", "tests/test_a.py", False),  # 通用条目不按路径命中
    ],
)
def test_trigger_matching_semantics(entry, path, hit):
    assert L.trigger_matches(entry, path) is hit


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
    assert L.check_all(books, root=root, today=TODAY, tracked=TRACKED) == []
    (books / "repo.md").write_text(GOOD.split("### L-002")[0], encoding="utf-8")
    assert any("编号重复" in e for e in L.check_all(books, root=root, today=TODAY, tracked=TRACKED))


def test_stray_or_missing_book_goes_red(root, tmp_path):
    books = tmp_path / "lessons"
    _write_books(books, GOOD)
    (books / "misc.md").write_text("# 没人读\n", encoding="utf-8")
    assert any("misc.md" in e for e in L.check_all(books, root=root, today=TODAY, tracked=TRACKED))
    (books / "misc.md").unlink()
    (books / "desktop.md").unlink()
    assert any(
        "desktop.md" in e for e in L.check_all(books, root=root, today=TODAY, tracked=TRACKED)
    )


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


# ---- 升级那一段：看护与再犯（复盘的效果数） ----

RULES = """# 细则

## 一节标题（错题本 L-010 升级，2026-11-03）

规则正文。为什么：2026-11-01 第一次（#1）。2026-11-05 升级后再犯（#2）；
2026-11-07 又一次（#3），同日 2026-11-07 不重计。看护 `tests/test_a.py::test_x`。

### 子标题仍属这一节

## 下一节（错题本 L-011 升级，2026-11-04）

正文。看护：只靠人记。2026-11-09 再犯（#4）。

- **条目式（错题本 L-012
  升级，2026-11-02）。** 续行 2026-11-06（#5）。
  **条目里缩进的粗体段仍属于它** 2026-11-08。
- **下一条** 2026-11-10。
"""


def test_upgrade_block_extents():
    d, body = L.upgrade_block(RULES, "L-010")
    assert d == "2026-11-03"
    assert "子标题仍属这一节" in body and "下一节" not in body
    d, body = L.upgrade_block(RULES, "L-012")
    assert d == "2026-11-02"  # 标记折行也认
    assert "2026-11-06" in body and "2026-11-08" in body and "2026-11-10" not in body
    # 标题式一节在下一条升级标记前停下：L-012 的日子不算到 L-011 头上
    assert "2026-11-06" not in L.upgrade_block(RULES, "L-011")[1]
    assert L.upgrade_block(RULES, "L-013") is None


def _upgrade_root(tmp_path, rules=RULES):
    target = tmp_path / "docs" / "rules" / "ci" / "x.md"
    target.parent.mkdir(parents=True)
    target.write_text(rules, encoding="utf-8")
    book = "".join(f"### L-{n:03d} 规则 {n} → docs/rules/ci/x.md\n" for n in (10, 11, 12))
    return tmp_path, L.parse(book, "ci")


def test_upgrades_count_recurrence_and_guards(tmp_path):
    root, lessons = _upgrade_root(tmp_path)
    ups, errs = L.upgrades(lessons, root=root, tracked=TRACKED)
    assert errs == []
    by = {u.id: u for u in ups}
    # 只数晚于升级日的、不同的日子；升级前的那次不算
    assert by["L-010"].recurrences == ("2026-11-05", "2026-11-07")
    assert by["L-010"].guards == ("tests/test_a.py",)
    assert by["L-011"].recurrences == ("2026-11-09",) and by["L-011"].manual
    assert by["L-012"].recurrences == ("2026-11-06", "2026-11-08")
    assert by["L-012"].guards == () and not by["L-012"].manual
    errs = L.upgrade_problems(lessons, root=root, tracked=TRACKED)
    assert len(errs) == 1 and "L-012" in errs[0] and "只靠人记" in errs[0]


def test_guard_must_name_a_tracked_file(tmp_path):
    """写错名 / 已删的测试不算看护：否则一句反引号就能让检查恒绿。"""
    root, lessons = _upgrade_root(tmp_path, RULES.replace("tests/test_a.py", "tests/test_gone.py"))
    errs = L.upgrade_problems(lessons, root=root, tracked=TRACKED)
    assert any("L-010" in e for e in errs)


def test_upgrade_without_dated_mark_goes_red(tmp_path):
    root, lessons = _upgrade_root(tmp_path, RULES.replace("L-011 升级，2026-11-04", "L-011 升级"))
    errs = L.upgrade_problems(lessons, root=root, tracked=TRACKED)
    assert any("L-011" in e and "YYYY-MM-DD" in e for e in errs)


def test_recurrence_cli_reports_totals_and_since(tmp_path, capsys):
    root, _ = _upgrade_root(tmp_path)
    d = root / "docs" / "rules" / "repo" / "lessons"
    d.mkdir(parents=True)
    for x in L.LAYERS:
        (d / f"{x}.md").write_text("# 空\n", encoding="utf-8")
    (d / "ci.md").write_text(
        "".join(f"### L-{n:03d} 规则 {n} → docs/rules/ci/x.md\n" for n in (10, 11, 12)),
        encoding="utf-8",
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    assert L.main(["--recurrence", "--dir", str(d)]) == 0
    out = capsys.readouterr().out
    assert "3 条已升级，升级后再犯 5 次、涉及 3 条；2 条没有机械看护" in out
    assert L.main(["--recurrence", "--dir", str(d), "--since", "2026-11-07"]) == 0
    assert "2026-11-07 起再犯 3 次、涉及 3 条" in capsys.readouterr().out
    assert L.main(["--recurrence", "--dir", str(d), "--since", "11-07"]) == 2


def test_the_real_rules_name_every_upgrade_with_a_date():
    """真细则：每条已升级条目都找得到带日期的标记，否则它的再犯数不进复盘。"""
    lessons = L.load()
    ups, errs = L.upgrades(lessons)
    assert errs == []
    assert len(ups) == sum(1 for x in lessons if x.status == "promoted") > 0
