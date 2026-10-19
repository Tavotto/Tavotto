"""`scripts/ci/playwright_shard_check.py`（CI03c：Playwright 按 project 分片的每片自验）的看护。

分片漏掉一个 project 的两个方向都**不会有任何用例红**：漏片时两片各自全绿，重叠时只是
慢一点。所以这里钉的是自验脚本自己的合同——主语是 **(project, file:line:col, title) 的
集合**，不是条数：

* 解析：`--list` 的每一行都要认得出；空输出、认不出的行、重复条目、`Total:` 数字与解析
  条数不符、没有 `Total:` 行 → `ListError`（CLI 退 2）；
* 参数：`--project=NAME` / 至多一个 `--shard=i/n` 之外的 token、空串、重复、越界 → `ListError`；
* 判定：各片 claim 的 project 并集 == 全集的 project 集、同一 project 的多片必须是
  `--shard=1/n..n/n` 各一次、各片清单两两不交且并集 == 全集——任一条不成立 → 问题清单非空
  （CLI 退 1）；
* 正例：CI 现在的三片（chromium 前半 / webkit + chromium-en / chromium 后半）退 0；另有旧的
  两片形状（chromium / webkit + chromium-en）对着仓库里存的真 `--list` 输出
  （`docs/implementation/ci-foundation/evidence/ci03c/`）退 0。

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

#: chromium 内部再切的两半（Playwright 原生 `--shard=1/2`、`--shard=2/2` 的形状）
HALF1 = """Listing tests:
  [chromium] › a11y.spec.ts:205:1 › 项目选择器：axe 无违规
Total: 1 test in 1 file
"""

HALF2 = """Listing tests:
  [chromium] › i18n.spec.ts:68:5 › 界面语言 zh-CN › 项目选择器
  [chromium] › i18n.spec.ts:68:5 › 界面语言 en-US › 项目选择器
Total: 2 tests in 1 file
"""

C = "--project=chromium"
W = "--project=webkit --project=chromium-en"

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


# ---------------------------------------------------------------- 片参数


def test_parse_slice_reads_projects_and_the_optional_shard():
    sl = PW.parse_slice("--project=chromium --shard=2/2", "p")
    assert sl == PW.Slice(frozenset({"chromium"}), (2, 2))
    assert PW.parse_slice("--project=webkit --project=chromium-en", "p").shard is None


@pytest.mark.parametrize(
    "arg",
    [
        "--shard=1/2",  # 没有 project
        "--project=chromium --shard=1/2 --shard=2/2",
        "--project=chromium --shard=0/2",
        "--project=chromium --shard=3/2",
        "--project=chromium --shard=1/1",
        "--project=chromium --shard=a/2",
        "--project=chromium --shard 1/2",
    ],
)
def test_parse_slice_rejects_bad_shard_shapes(arg):
    with pytest.raises(PW.ListError):
        PW.parse_slice(arg, "p")


def test_parse_others_splits_on_pipe_and_rejects_an_empty_part():
    got = PW.parse_others(f"{W}|{C} --shard=2/2")
    assert [g.shard for g in got] == [None, (2, 2)]
    with pytest.raises(PW.ListError):
        PW.parse_others(f"{W}|")


# ---------------------------------------------------------------- 判定


def _s(arg: str) -> PW.Slice:
    return PW.parse_slice(arg, "t")


def _three():
    """CI 现在的三片：(片 1 = chromium 1/2, 片 2 = webkit + chromium-en, 片 3 = chromium 2/2)。"""
    return (
        (_s(f"{C} --shard=1/2"), _entries(HALF1)),
        (_s(W), _entries(SHARD2)),
        (_s(f"{C} --shard=2/2"), _entries(HALF2)),
    )


def test_the_three_real_shards_verify_clean_from_every_point_of_view():
    full = _entries(FULL)
    shards = _three()
    for k in range(3):
        mine_slice, mine = shards[k]
        others = [x for j, x in enumerate(shards) if j != k]
        assert PW.verify(full, mine, mine_slice, others) == [], k


def test_the_old_two_shard_shape_still_verifies_clean():
    full = _entries(FULL)
    assert PW.verify(full, _entries(SHARD1), _s(C), [(_s(W), _entries(SHARD2))]) == []
    assert PW.verify(full, _entries(SHARD2), _s(W), [(_s(C), _entries(SHARD1))]) == []


def test_a_project_in_no_shard_is_reported_as_missing():
    full = _entries(FULL)
    shard2 = _entries(
        SHARD2.replace(
            "  [chromium-en] › a11y.spec.ts:205:1 › 项目选择器：axe 无违规\n", ""
        ).replace("Total: 3 tests in 2 files", "Total: 2 tests in 2 files")
    )
    problems = PW.verify(full, shard2, _s("--project=webkit"), [(_s(C), _entries(SHARD1))])
    assert any("chromium-en" in p and "漏片" in p for p in problems), problems


def test_a_whole_project_overlapping_a_sharded_half_is_reported():
    full = _entries(FULL)
    shards = _three()
    problems = PW.verify(
        full, _entries(SHARD1), _s(C), [shards[0], shards[1]]
    )  # 整 chromium + chromium 1/2
    assert any("不带 --shard 的整 project" in p for p in problems), problems
    assert any("重叠" in p for p in problems), problems


def test_a_half_with_no_partner_is_reported_as_missing():
    """只剩 chromium 前半、后半那一片被删了：另一半没人跑。"""
    full = _entries(FULL)
    shards = _three()
    problems = PW.verify(full, shards[0][1], shards[0][0], [shards[1]])
    assert any("只有一片带 --shard" in p for p in problems), problems
    assert any("不在任何一片里" in p for p in problems), problems


def test_halves_with_mismatched_denominators_are_reported():
    full = _entries(FULL)
    shards = _three()
    bad = (_s(f"{C} --shard=2/3"), shards[2][1])
    problems = PW.verify(full, shards[0][1], shards[0][0], [shards[1], bad])
    assert any("--shard 分法对不上" in p for p in problems), problems


def test_the_same_half_twice_is_reported():
    full = _entries(FULL)
    shards = _three()
    problems = PW.verify(full, shards[0][1], shards[0][0], [shards[1], shards[0]])
    assert any("--shard 分法对不上" in p for p in problems), problems
    assert any("重叠" in p for p in problems), problems


def test_a_project_that_the_config_does_not_have_is_reported():
    full = _entries(FULL)
    problems = PW.verify(
        full, _entries(SHARD1), _s(C), [(_s(W + " --project=firefox"), _entries(SHARD2))]
    )
    assert any("firefox" in p and "不存在" in p for p in problems), problems


def test_a_whole_project_shard_that_lacks_one_of_its_own_tests_is_reported():
    full = _entries(FULL)
    mine = _entries(SHARD1)[:-1]
    problems = PW.verify(full, mine, _s(C), [(_s(W), _entries(SHARD2))])
    assert any("少了 1 条" in p for p in problems), problems
    assert any("漏片" in p for p in problems), problems


def test_a_half_that_drops_a_test_is_reported_by_the_union_check():
    """`--shard` 的一片自己没法说「应该有哪些」，漏一条由并集 == 全集兜住。"""
    full = _entries(FULL)
    shards = _three()
    short = (shards[2][0], shards[2][1][:-1])
    problems = PW.verify(full, shards[0][1], shards[0][0], [shards[1], short])
    assert any("不在任何一片里" in p for p in problems), problems


def test_a_shard_list_that_carries_a_foreign_project_is_reported():
    """本片的清单里混进了别的 project 的用例——`--project=` 参数与清单对不上。"""
    full = _entries(FULL)
    problems = PW.verify(full, _entries(SHARD2), _s(C), [(_s(W), _entries(SHARD2))])
    assert any("多了 3 条" in p for p in problems), problems
    assert any("重叠" in p for p in problems), problems


def test_a_shard_entry_outside_the_full_list_is_reported():
    full = _entries(FULL)
    mine = _entries(SHARD1) + [PW.Entry("chromium", "ghost.spec.ts:1:1", "不在全集里")]
    problems = PW.verify(full, mine, _s(C), [(_s(W), _entries(SHARD2))])
    assert any("不在全集里" in p for p in problems), problems


def test_an_empty_shard_list_is_reported():
    full = _entries(FULL)
    problems = PW.verify(full, [], _s(C), [(_s(W), _entries(SHARD2))])
    assert any("是空的" in p for p in problems), problems


def test_the_verdict_is_about_sets_not_counts():
    """两片各漏一条再各多一条：条数对得上，集合对不上。"""
    full = _entries(FULL)
    mine = _entries(SHARD1)[:-1] + [_entries(SHARD2)[0]]  # 少一条自己的，多一条 webkit 的
    assert len(mine) == len(_entries(SHARD1))
    problems = PW.verify(full, mine, _s(C), [(_s(W), _entries(SHARD2))])
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


OTHERS_FOR_SHARD1 = f"{W}|{C} --shard=2/2"


def test_cli_passes_on_the_three_current_shards(tmp_path):
    """CI 现在的三片，每片的视角各跑一遍（离线，缩小的清单）。"""
    full = _write(tmp_path, "full.txt", FULL)
    lists = {
        "h1": _write(tmp_path, "h1.txt", HALF1),
        "w": _write(tmp_path, "w.txt", SHARD2),
        "h2": _write(tmp_path, "h2.txt", HALF2),
    }
    cases = [
        ("1", f"{C} --shard=1/2", f"{W}|{C} --shard=2/2", "h1", ["w", "h2"]),
        ("2", W, f"{C} --shard=1/2|{C} --shard=2/2", "w", ["h1", "h2"]),
        ("3", f"{C} --shard=2/2", f"{C} --shard=1/2|{W}", "h2", ["h1", "w"]),
    ]
    out = tmp_path / "out"
    for shard, projects, others, mine, rest in cases:
        r = _cli(
            "--shard",
            shard,
            f"--projects={projects}",
            f"--others={others}",
            "--full",
            str(full),
            "--mine",
            str(lists[mine]),
            *[a for k in rest for a in ("--other-list", str(lists[k]))],
            "--out",
            str(out),
        )
        assert r.returncode == 0, (shard, r.stdout, r.stderr)
    summary = json.loads((out / "check.json").read_text(encoding="utf-8"))
    assert summary["ok"] and summary["full_total"] == 6 and summary["mine_total"] == 2
    assert (out / "list_other1.txt").is_file() and (out / "list_other2.txt").is_file()


def test_cli_passes_on_the_old_two_shard_shape_from_the_stored_evidence(tmp_path):
    """旧的两片形状，对着仓库里存的真 `--list` 输出（本机 2026-09-16）：整 project 的片仍然合法。"""
    full = EVIDENCE / "list_all.txt"
    s1 = EVIDENCE / "list_shard1.txt"
    s2 = EVIDENCE / "list_shard2.txt"
    assert full.is_file() and s1.is_file() and s2.is_file(), "evidence/ci03c 的三份 --list 输出不在"
    assert len(_entries(full.read_text(encoding="utf-8"))) == 151
    r1 = _cli(
        "--shard",
        "1",
        f"--projects={C}",
        f"--others={W}",
        "--full",
        str(full),
        "--mine",
        str(s1),
        "--other-list",
        str(s2),
    )
    assert r1.returncode == 0, r1.stderr
    summary = json.loads(r1.stdout.splitlines()[0])
    assert summary["ok"] and summary["mine_total"] == 109 and summary["full_total"] == 151
    assert summary["per_project_total"] == {"chromium": 109, "chromium-en": 19, "webkit": 23}


def test_cli_exits_1_when_a_project_is_left_out(tmp_path):
    full = _write(tmp_path, "full.txt", FULL)
    mine = _write(tmp_path, "mine.txt", SHARD1)
    other = _write(
        tmp_path,
        "other.txt",
        "Listing tests:\n  [webkit] › a11y.spec.ts:205:1 › 项目选择器：axe 无违规\n  [webkit] › golden-paths.spec.ts:30:1 › 首次启动\nTotal: 2 tests in 2 files\n",
    )
    r = _cli(
        "--shard",
        "1",
        f"--projects={C}",
        "--others=--project=webkit",
        "--full",
        str(full),
        "--mine",
        str(mine),
        "--other-list",
        str(other),
    )
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "chromium-en" in r.stderr and "漏片" in r.stderr


def test_cli_exits_1_when_both_shards_claim_the_same_project(tmp_path):
    full = _write(tmp_path, "full.txt", FULL)
    mine = _write(tmp_path, "mine.txt", SHARD1)
    other = _write(tmp_path, "other.txt", FULL)
    r = _cli(
        "--shard",
        "1",
        f"--projects={C}",
        "--others=--project=chromium --project=webkit --project=chromium-en",
        "--full",
        str(full),
        "--mine",
        str(mine),
        "--other-list",
        str(other),
    )
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "都声称" in r.stderr


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
    other = _write(tmp_path, "other.txt", SHARD2)
    r = _cli(
        "--shard",
        "1",
        f"--projects={C}",
        f"--others={W}",
        "--full",
        str(full),
        "--mine",
        str(mine),
        "--other-list",
        str(other),
    )
    assert r.returncode == 2, (why, r.stdout, r.stderr)
    assert "ERROR（输入）" in r.stderr


def test_cli_refuses_an_empty_shard(tmp_path):
    full = _write(tmp_path, "full.txt", FULL)
    mine = _write(tmp_path, "mine.txt", SHARD1)
    other = _write(tmp_path, "other.txt", SHARD2)
    r = _cli(
        "--shard",
        "1",
        "--projects=",
        f"--others={W}",
        "--full",
        str(full),
        "--mine",
        str(mine),
        "--other-list",
        str(other),
    )
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "空片" in r.stderr


def test_cli_refuses_half_an_input(tmp_path):
    """只给 --full 不给 --mine（或反过来）是半套配置，不能默认成什么。"""
    full = _write(tmp_path, "full.txt", FULL)
    r = _cli("--shard", "1", f"--projects={C}", f"--others={W}", "--full", str(full))
    assert r.returncode == 2, (r.stdout, r.stderr)


def test_cli_refuses_a_missing_other_list(tmp_path):
    """--others 有两片却只给一份清单：少一份就少一片的并集证据，不能默认成空。"""
    full = _write(tmp_path, "full.txt", FULL)
    mine = _write(tmp_path, "mine.txt", HALF1)
    other = _write(tmp_path, "other.txt", SHARD2)
    r = _cli(
        "--shard",
        "1",
        f"--projects={C} --shard=1/2",
        f"--others={OTHERS_FOR_SHARD1}",
        "--full",
        str(full),
        "--mine",
        str(mine),
        "--other-list",
        str(other),
    )
    assert r.returncode == 2, (r.stdout, r.stderr)


def test_the_entry_point_pins_utf8_stdout():
    """脚本打带中文的 JSON 与 ✓，被 pwsh 捕获时 stdout 是管道——与 aggregate_gate.py 同一条纪律。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert 'reconfigure(encoding="utf-8"' in src
