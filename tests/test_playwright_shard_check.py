"""`scripts/ci/playwright_shard_check.py`（CI03c：Playwright 按 project 分片的每片自验）的看护。

分片漏掉一个 project 的两个方向都**不会有任何用例红**：漏片时两片各自全绿，重叠时只是
慢一点。所以这里钉的是自验脚本自己的合同——主语是 **(project, file:line:col, title) 的
集合**，不是条数：

* 解析：`--list` 的每一行都要认得出；空输出、认不出的行、重复条目、`Total:` 数字与解析
  条数不符、没有 `Total:` 行 → `ListError`（CLI 退 2）；
* 参数：`--project=NAME` 之外的 token、空串、重复 → `ListError`；
* 判定：本片 ∪ 另一片 == 全集的 project 集、两片不交、本片集合 == 全集里 project ∈ 本片
  的那些、本片非空——任一条不成立 → 问题清单非空（CLI 退 1）；
* 正例：CI 现在的两片（chromium / webkit + chromium-en）对着仓库里存的三份真 `--list`
  输出（`docs/implementation/ci-foundation/evidence/ci03c/`）退 0。

每条负例写完都做过一次变异反证（把脚本里对应的检查拿掉 → 该条红，退出码判），记录在
docs/implementation/ci-foundation/CI03C_PLAYWRIGHT_SHARDS.md。纯标准库、平台无关。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI_DIR = ROOT / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import playwright_shard_check as PW  # noqa: E402

SCRIPT = CI_DIR / "playwright_shard_check.py"
EVIDENCE = ROOT / "docs" / "implementation" / "ci-foundation" / "evidence" / "ci03c"

#: 一份缩小的 `--list` 全集：三个 project、标题里带 ` › `（describe 块）与中文。
FULL = """Listing tests:
  [chromium] › a11y.spec.ts:205:1 › 项目选择器：axe 无违规
  [chromium] › i18n.spec.ts:68:5 › 界面语言 zh-CN › 项目选择器
  [chromium] › i18n.spec.ts:68:5 › 界面语言 en-US › 项目选择器
  [webkit] › a11y.spec.ts:205:1 › 项目选择器：axe 无违规
  [webkit] › golden-paths.spec.ts:30:1 › 首次启动
  [chromium-en] › a11y.spec.ts:205:1 › 项目选择器：axe 无违规
Total: 6 tests in 3 files
"""

SHARD1 = """Listing tests:
  [chromium] › a11y.spec.ts:205:1 › 项目选择器：axe 无违规
  [chromium] › i18n.spec.ts:68:5 › 界面语言 zh-CN › 项目选择器
  [chromium] › i18n.spec.ts:68:5 › 界面语言 en-US › 项目选择器
Total: 3 tests in 2 files
"""

SHARD2 = """Listing tests:
  [webkit] › a11y.spec.ts:205:1 › 项目选择器：axe 无违规
  [webkit] › golden-paths.spec.ts:30:1 › 首次启动
  [chromium-en] › a11y.spec.ts:205:1 › 项目选择器：axe 无违规
Total: 3 tests in 2 files
"""


def _entries(text: str) -> list[PW.Entry]:
    return PW.parse_list(text, "t")


# ---------------------------------------------------------------- 解析


def test_parse_reads_every_line_and_keeps_describe_separators_in_the_title():
    es = _entries(FULL)
    assert len(es) == 6
    assert es[1] == PW.Entry("chromium", "i18n.spec.ts:68:5", "界面语言 zh-CN › 项目选择器")
    # 同一 file:line:col 两条不同标题是两条不同的用例（参数化的 describe）
    assert es[1] != es[2]


def test_parse_accepts_windows_style_paths():
    """CI 的 Windows 腿打的是 `e2e\\a11y.spec.ts:205:1`（list reporter 实测）。"""
    text = "Listing tests:\n  [chromium] › e2e\\a11y.spec.ts:205:1 › x\nTotal: 1 test in 1 file\n"
    assert _entries(text)[0].location == "e2e\\a11y.spec.ts:205:1"


@pytest.mark.parametrize(
    "text, why",
    [
        ("", "空输出"),
        ("Listing tests:\nTotal: 0 tests in 0 files\n", "零条"),
        (
            "Listing tests:\n  [chromium] a11y.spec.ts:1:1 没有分隔符\nTotal: 1 test in 1 file\n",
            "认不出的行",
        ),
        ("Listing tests:\n  [chromium] › a11y.spec.ts:1:1 › x\n", "没有 Total: 行"),
        (
            "Listing tests:\n  [chromium] › a11y.spec.ts:1:1 › x\nTotal: 2 tests in 1 file\n",
            "Total 与条数不符",
        ),
        (
            "Listing tests:\n  [chromium] › a11y.spec.ts:1:1 › x\n  [chromium] › a11y.spec.ts:1:1 › x\n"
            "Total: 2 tests in 1 file\n",
            "重复条目",
        ),
        (
            "Listing tests:\n  [chromium] › a11y.spec.ts:1:1 › x\nTotal: 1 test in 1 file\n"
            "Total: 1 test in 1 file\n",
            "两行 Total",
        ),
        (
            "Listing tests:\n  [chromium] › a11y.spec.ts:1:1 › x\nError: boom\nTotal: 1 test in 1 file\n",
            "夹了报错行",
        ),
    ],
)
def test_parse_rejects_malformed_lists(text, why):
    with pytest.raises(PW.ListError):
        _entries(text)
    del why


# ---------------------------------------------------------------- 参数


def test_parse_projects_reads_the_set():
    assert PW.parse_projects("--project=webkit --project=chromium-en", "p") == {
        "webkit",
        "chromium-en",
    }
    assert PW.parse_projects("  --project=chromium ", "p") == {"chromium"}


@pytest.mark.parametrize(
    "arg",
    [
        "",
        "   ",
        "--project=chromium --grep=a11y",
        "chromium",
        "--project=",
        "--project=a --project=a",
    ],
)
def test_parse_projects_rejects_anything_that_is_not_exactly_project_flags(arg):
    with pytest.raises(PW.ListError):
        PW.parse_projects(arg, "p")


# ---------------------------------------------------------------- 判定


def test_the_two_real_shards_verify_clean():
    full = _entries(FULL)
    assert PW.verify(full, _entries(SHARD1), {"chromium"}, {"webkit", "chromium-en"}) == []
    assert PW.verify(full, _entries(SHARD2), {"webkit", "chromium-en"}, {"chromium"}) == []


def test_a_project_in_no_shard_is_reported_as_missing():
    full = _entries(FULL)
    problems = PW.verify(
        full,
        _entries(
            SHARD2.replace(
                "  [chromium-en] › a11y.spec.ts:205:1 › 项目选择器：axe 无违规\n", ""
            ).replace("Total: 3 tests in 2 files", "Total: 2 tests in 2 files")
        ),
        {"webkit"},
        {"chromium"},
    )
    assert any("chromium-en" in p and "漏片" in p for p in problems), problems


def test_a_project_claimed_by_both_shards_is_reported_as_overlap():
    full = _entries(FULL)
    problems = PW.verify(
        full, _entries(SHARD1), {"chromium"}, {"chromium", "webkit", "chromium-en"}
    )
    assert any("两片都声称" in p for p in problems), problems


def test_a_project_that_the_config_does_not_have_is_reported():
    full = _entries(FULL)
    problems = PW.verify(full, _entries(SHARD1), {"chromium"}, {"webkit", "chromium-en", "firefox"})
    assert any("firefox" in p and "不存在" in p for p in problems), problems


def test_a_shard_list_that_lacks_one_of_its_own_tests_is_reported():
    full = _entries(FULL)
    mine = _entries(SHARD1)[:-1]
    problems = PW.verify(full, mine, {"chromium"}, {"webkit", "chromium-en"})
    assert any("少了 1 条" in p for p in problems), problems


def test_a_shard_list_that_carries_a_foreign_project_is_reported():
    """本片的清单里混进了别的 project 的用例——`--project=` 参数与清单对不上。"""
    full = _entries(FULL)
    problems = PW.verify(full, _entries(SHARD2), {"chromium"}, {"webkit", "chromium-en"})
    assert any("多了 3 条" in p for p in problems), problems
    assert any("少了 3 条" in p for p in problems), problems


def test_a_shard_entry_outside_the_full_list_is_reported():
    full = _entries(FULL)
    mine = _entries(SHARD1) + [PW.Entry("chromium", "ghost.spec.ts:1:1", "不在全集里")]
    problems = PW.verify(full, mine, {"chromium"}, {"webkit", "chromium-en"})
    assert any("不在全集里" in p for p in problems), problems


def test_an_empty_shard_list_is_reported():
    full = _entries(FULL)
    problems = PW.verify(full, [], {"chromium"}, {"webkit", "chromium-en"})
    assert any("是空的" in p for p in problems), problems


def test_the_verdict_is_about_sets_not_counts():
    """两片各漏一条再各多一条：条数对得上，集合对不上。"""
    full = _entries(FULL)
    mine = _entries(SHARD1)[:-1] + [_entries(SHARD2)[0]]  # 少一条自己的，多一条 webkit 的
    assert len(mine) == len(_entries(SHARD1))
    problems = PW.verify(full, mine, {"chromium"}, {"webkit", "chromium-en"})
    assert any("少了 1 条" in p for p in problems) and any("多了 1 条" in p for p in problems), (
        problems
    )


# ---------------------------------------------------------------- CLI（真子进程）


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_cli_passes_on_the_two_real_shards_from_the_stored_evidence(tmp_path):
    """CI 现在的两片，对着仓库里存的三份真 `--list` 输出（本机 2026-09-16）。

    前提先钉住：三份文件在、全集 151 条、两片 109 + 42——评审时用的就是这份输入。
    """
    full = EVIDENCE / "list_all.txt"
    s1 = EVIDENCE / "list_shard1.txt"
    s2 = EVIDENCE / "list_shard2.txt"
    assert full.is_file() and s1.is_file() and s2.is_file(), "evidence/ci03c 的三份 --list 输出不在"
    assert len(_entries(full.read_text(encoding="utf-8"))) == 151
    out = tmp_path / "out"
    r1 = _cli(
        "--shard",
        "1",
        "--projects=--project=chromium",
        "--others=--project=webkit --project=chromium-en",
        "--full",
        str(full),
        "--mine",
        str(s1),
        "--out",
        str(out),
    )
    assert r1.returncode == 0, r1.stderr
    r2 = _cli(
        "--shard",
        "2",
        "--projects=--project=webkit --project=chromium-en",
        "--others=--project=chromium",
        "--full",
        str(full),
        "--mine",
        str(s2),
    )
    assert r2.returncode == 0, r2.stderr
    summary = json.loads(r1.stdout.splitlines()[0])
    assert summary["ok"] and summary["mine_total"] == 109 and summary["full_total"] == 151
    assert summary["per_project_total"] == {"chromium": 109, "chromium-en": 19, "webkit": 23}
    # --out 写的是证据：两份原样的清单 + check.json
    assert (out / "list_all.txt").read_text(encoding="utf-8") == full.read_text(encoding="utf-8")
    assert json.loads((out / "check.json").read_text(encoding="utf-8"))["ok"] is True


def test_cli_exits_1_when_a_project_is_left_out(tmp_path):
    full = _write(tmp_path, "full.txt", FULL)
    mine = _write(tmp_path, "mine.txt", SHARD1)
    r = _cli(
        "--shard",
        "1",
        "--projects=--project=chromium",
        "--others=--project=webkit",
        "--full",
        str(full),
        "--mine",
        str(mine),
    )
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "chromium-en" in r.stderr and "漏片" in r.stderr


def test_cli_exits_1_when_both_shards_claim_the_same_project(tmp_path):
    full = _write(tmp_path, "full.txt", FULL)
    mine = _write(tmp_path, "mine.txt", SHARD1)
    r = _cli(
        "--shard",
        "1",
        "--projects=--project=chromium",
        "--others=--project=chromium --project=webkit --project=chromium-en",
        "--full",
        str(full),
        "--mine",
        str(mine),
    )
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "两片都声称" in r.stderr


@pytest.mark.parametrize(
    "mine_text, why",
    [
        ("", "空输出"),
        (
            SHARD1.replace(" › 项目选择器：axe 无违规\n", " 项目选择器：axe 无违规\n", 1),
            "认不出的行",
        ),
        (SHARD1.replace("Total: 3 tests", "Total: 4 tests"), "Total 不符"),
    ],
)
def test_cli_exits_2_on_unreadable_input(tmp_path, mine_text, why):
    full = _write(tmp_path, "full.txt", FULL)
    mine = _write(tmp_path, "mine.txt", mine_text)
    r = _cli(
        "--shard",
        "1",
        "--projects=--project=chromium",
        "--others=--project=webkit --project=chromium-en",
        "--full",
        str(full),
        "--mine",
        str(mine),
    )
    assert r.returncode == 2, (why, r.stdout, r.stderr)
    assert "ERROR（输入）" in r.stderr


def test_cli_refuses_an_empty_shard(tmp_path):
    full = _write(tmp_path, "full.txt", FULL)
    mine = _write(tmp_path, "mine.txt", SHARD1)
    r = _cli(
        "--shard",
        "1",
        "--projects=",
        "--others=--project=webkit --project=chromium-en",
        "--full",
        str(full),
        "--mine",
        str(mine),
    )
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "空片" in r.stderr


def test_cli_refuses_half_an_input(tmp_path):
    """只给 --full 不给 --mine（或反过来）是半套配置，不能默认成什么。"""
    full = _write(tmp_path, "full.txt", FULL)
    r = _cli(
        "--shard",
        "1",
        "--projects=--project=chromium",
        "--others=--project=webkit --project=chromium-en",
        "--full",
        str(full),
    )
    assert r.returncode == 2, (r.stdout, r.stderr)


def test_the_entry_point_pins_utf8_stdout():
    """脚本打带中文的 JSON 与 ✓，被 pwsh 捕获时 stdout 是管道——与 aggregate_gate.py 同一条纪律。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert 'reconfigure(encoding="utf-8"' in src
