"""功能登记表门禁（ADR 0097）的看护：`scripts/ci/feature_registry.py` 与它在 CI 里的接线。

登记表的用处是「后面的修改不能悄悄关掉前面做好的功能」，而这类门禁最容易出的错是
**空转**：判据量的不是它以为的那个对象，于是一直是绿的。所以这里的每条负例都对应
一种真实的「功能被关掉而 CI 仍绿」的形状，并且先证明落点（那条用例 / 那个条目确实在
输入里），再看判定：

* 登记的用例被删 / 改名 → Playwright 清单里没有它；
* 用例被加了 `test.skip(...)` / `test.fixme(...)`（声明期：`--list` 的 expectedStatus；
  运行期：AST 扫描出的修饰）；
* 登记表里写了一条不存在的用例；
* 带 `@feature:` 标签的用例没有登记（孤儿）、登记在一个 removed 的功能上、或功能没把它列在名下；
* removed 缺原因 / 日期 / 批准人；
* 合并态那次真跑里登记的用例是 skipped、或者根本不在报告里；
* 条目从登记表里消失、active → removed 没打标签。

主语：`check` 的输入是 **Playwright 自己算出来的用例集合**（JSON 报告形状）与 **AST 扫描
结果**；这里用合成的同形状输入驱动纯函数——真的 `playwright --list` 与扫描器在 frontend
job 里跑（扫描器另有 `--self-test` 反证它自己）。纯标准库，任何环境都跑得起来。
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI_DIR = ROOT / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import feature_registry as FR  # noqa: E402

SCRIPT = CI_DIR / "feature_registry.py"
CI_YML = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
PW_CONFIG = (ROOT / "web" / "playwright.config.ts").read_text(encoding="utf-8")


# ── 合成输入（与真实输出同形状） ─────────────────────────────────────────


def _feature(fid: str, refs: list[tuple[str, str]], **kw) -> dict:
    d = {
        "id": fid,
        "status": "active",
        "platform": "both",
        "summary": {"zh-CN": "一句话", "en-US": "One line"},
        "entry": "某处",
        "e2e": [{"spec": s, "title": t} for s, t in refs],
    }
    d.update(kw)
    return d


def _registry(*features: dict) -> dict:
    return {"version": 1, "features": list(features)}


def _spec(
    title: str,
    line: int,
    *,
    tags=(),
    projects=("chromium",),
    expected="passed",
    status=None,
    results=None,
):
    tests = []
    for p in projects:
        t = {"projectName": p, "projectId": p, "expectedStatus": expected, "annotations": []}
        if status is not None:
            t["status"] = status
            t["results"] = results if results is not None else [{"status": "passed"}]
        else:
            t["status"] = "skipped"  # `--list` 里每条都是 skipped、results 为空
            t["results"] = []
        tests.append(t)
    return {"title": title, "line": line, "column": 1, "tags": list(tags), "tests": tests}


def _listing(
    files: dict[str, list[dict]], describes: dict[str, dict[str, list[dict]]] | None = None
) -> dict:
    suites = []
    for f, specs in files.items():
        s = {"title": f, "file": f, "specs": specs, "suites": []}
        for dt, dspecs in (describes or {}).get(f, {}).items():
            s["suites"].append({"title": dt, "file": f, "specs": dspecs, "suites": []})
        suites.append(s)
    return {"config": {}, "suites": suites, "errors": [], "stats": {}}


BASE_REG = _registry(
    _feature("canvas.drag", [("a.spec.ts", "拖动")]),
    _feature("legend.move", [("b.spec.ts", "组 › 挪图例")]),
    _feature(
        "desktop.close",
        [],
        platform="desktop",
        desktop_manual=["点红灯：弹三选一"],
    ),
    _feature(
        "old.thing",
        [("gone.spec.ts", "早没了")],
        status="removed",
        removed={"reason": "被 X 取代", "date": "2026-09-26", "approved_by": "用户"},
    ),
)
BASE_LIST = _listing(
    {
        "a.spec.ts": [_spec("拖动", 10, tags=["feature:canvas.drag"]), _spec("无关", 30)],
        "b.spec.ts": [],
    },
    {"b.spec.ts": {"组": [_spec("挪图例", 12)]}},
)
BASE_SCAN = {
    "tests": [
        {"file": "a.spec.ts", "line": 10, "column": 1, "title": "拖动", "modifiers": []},
        {"file": "a.spec.ts", "line": 30, "column": 1, "title": "无关", "modifiers": []},
        {"file": "b.spec.ts", "line": 12, "column": 1, "title": "挪图例", "modifiers": []},
    ]
}


def _check(reg=BASE_REG, listing=BASE_LIST, scan=BASE_SCAN) -> list[str]:
    feats, shape = FR.parse_registry(copy.deepcopy(reg))
    return shape + FR.check(
        feats,
        FR.parse_playwright_json(copy.deepcopy(listing), "t"),
        FR.parse_scan(copy.deepcopy(scan)),
    )


def test_baseline_is_green():
    """对照：下面每条负例都从这份绿的输入出发，只改一处。"""
    assert _check() == []


# ── check：登记表 → 用例 ────────────────────────────────────────────────


def test_deleted_test_is_red():
    listing = copy.deepcopy(BASE_LIST)
    specs = listing["suites"][0]["specs"]
    assert [s["title"] for s in specs] == ["拖动", "无关"]  # 落点
    listing["suites"][0]["specs"] = [s for s in specs if s["title"] != "拖动"]
    problems = _check(listing=listing)
    assert any("canvas.drag" in p and "不存在" in p for p in problems), problems


def test_renamed_describe_is_red():
    """标题路径含 describe：改了 describe 名，登记的「组 › 挪图例」就不存在了。"""
    listing = copy.deepcopy(BASE_LIST)
    assert listing["suites"][1]["suites"][0]["title"] == "组"
    listing["suites"][1]["suites"][0]["title"] = "别的组"
    problems = _check(listing=listing)
    assert any("legend.move" in p and "不存在" in p for p in problems), problems


def test_declared_skip_is_red():
    listing = copy.deepcopy(BASE_LIST)
    spec = listing["suites"][0]["specs"][0]
    assert spec["title"] == "拖动"
    spec["tests"][0]["expectedStatus"] = "skipped"  # `test.skip('拖动', …)` 在 --list 里的样子
    problems = _check(listing=listing)
    assert any("canvas.drag" in p and "声明期跳过" in p for p in problems), problems


@pytest.mark.parametrize("kind,scope", [("skip", "test"), ("fixme", "describe"), ("fail", "file")])
def test_runtime_modifier_is_red(kind, scope):
    scan = copy.deepcopy(BASE_SCAN)
    assert scan["tests"][0]["title"] == "拖动"
    scan["tests"][0]["modifiers"] = [{"kind": kind, "line": 11, "scope": scope}]
    problems = _check(scan=scan)
    assert any("canvas.drag" in p and f"test.{kind}" in p for p in problems), problems


def test_modifier_on_unregistered_test_is_not_our_business():
    """没登记的用例带 skip 与登记表无关（判据只对登记的那些负责）。"""
    scan = copy.deepcopy(BASE_SCAN)
    assert scan["tests"][1]["title"] == "无关"
    scan["tests"][1]["modifiers"] = [{"kind": "skip", "line": 31, "scope": "test"}]
    assert _check(scan=scan) == []


def test_scan_not_matching_location_is_red():
    """扫描器与 Playwright 对不上位置 = 判不出有没有被跳过；不许当成「没有修饰」。"""
    scan = copy.deepcopy(BASE_SCAN)
    scan["tests"][0]["line"] = 999
    problems = _check(scan=scan)
    assert any("canvas.drag" in p and "源码扫描里找不到" in p for p in problems), problems


def test_registry_pointing_at_nonexistent_test_is_red():
    reg = copy.deepcopy(BASE_REG)
    reg["features"][0]["e2e"].append({"spec": "a.spec.ts", "title": "根本没有这条"})
    problems = _check(reg=reg)
    assert any("根本没有这条" in p for p in problems), problems


# ── check：用例 → 登记表（孤儿） ─────────────────────────────────────────


def test_orphan_tag_is_red():
    listing = copy.deepcopy(BASE_LIST)
    listing["suites"][0]["specs"][1]["tags"] = ["feature:nobody.knows"]
    problems = _check(listing=listing)
    assert any("nobody.knows" in p and "孤儿" in p for p in problems), problems


def test_tag_on_removed_feature_is_red():
    listing = copy.deepcopy(BASE_LIST)
    listing["suites"][0]["specs"][1]["tags"] = ["feature:old.thing"]
    problems = _check(listing=listing)
    assert any("old.thing" in p and "removed" in p for p in problems), problems


def test_tag_without_listing_under_the_feature_is_red():
    listing = copy.deepcopy(BASE_LIST)
    listing["suites"][0]["specs"][1]["tags"] = ["feature:canvas.drag"]
    problems = _check(listing=listing)
    assert any("无关" in p and "没有列它" in p for p in problems), problems


def test_tags_match_with_or_without_the_leading_at():
    """Playwright 的 JSON 报告里 tags 没有前导 @（源码里写 `@feature:x`）：两种都要认。

    合成输入曾经照源码写成 `@feature:x`，于是这组用例全绿，而对着真 `--list` 的反向判据
    恒绿——自己捏的输入形状会说谎。真形状以不带 @ 为准，带 @ 的也认。"""
    for tag in ("feature:nobody.knows", "@feature:nobody.knows"):
        listing = copy.deepcopy(BASE_LIST)
        listing["suites"][0]["specs"][1]["tags"] = [tag]
        assert any("nobody.knows" in p and "孤儿" in p for p in _check(listing=listing)), tag


def test_other_tags_are_ignored():
    listing = copy.deepcopy(BASE_LIST)
    listing["suites"][0]["specs"][1]["tags"] = ["slow", "featureish"]
    assert _check(listing=listing) == []


# ── 登记表形状 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("missing", ["reason", "date", "approved_by"])
def test_removed_without_paperwork_is_red(missing):
    reg = copy.deepcopy(BASE_REG)
    rm = reg["features"][3]["removed"]
    assert missing in rm
    del rm[missing]
    problems = _check(reg=reg)
    assert any("old.thing" in p and f"removed.{missing}" in p for p in problems), problems


def test_removed_with_bad_date_is_red():
    reg = copy.deepcopy(BASE_REG)
    reg["features"][3]["removed"]["date"] = "昨天"
    assert any("YYYY-MM-DD" in p for p in _check(reg=reg))


def test_removed_without_block_is_red():
    reg = copy.deepcopy(BASE_REG)
    del reg["features"][3]["removed"]
    assert any("old.thing" in p and "removed 块" in p for p in _check(reg=reg))


def test_removed_feature_tests_are_not_required():
    """removed 的功能引用的用例不存在是正常的（功能都没了）——不判。"""
    assert "gone.spec.ts" not in json.dumps(BASE_LIST)
    assert _check() == []


def test_active_browser_feature_needs_e2e():
    reg = copy.deepcopy(BASE_REG)
    reg["features"][0]["e2e"] = []
    assert any("canvas.drag" in p and "至少要一条 e2e" in p for p in _check(reg=reg))


def test_desktop_only_feature_needs_manual_steps():
    reg = copy.deepcopy(BASE_REG)
    reg["features"][2]["desktop_manual"] = []
    assert any("desktop.close" in p and "desktop_manual" in p for p in _check(reg=reg))


@pytest.mark.parametrize(
    "field,value,needle",
    [
        ("status", "paused", "status"),
        ("platform", "mobile", "platform"),
        ("id", "Canvas Drag", "不合形状"),
        ("summary", {"zh-CN": "只有中文"}, "summary"),
        ("entry", "", "entry"),
    ],
)
def test_shape_violations_are_red(field, value, needle):
    reg = copy.deepcopy(BASE_REG)
    reg["features"][0][field] = value
    assert any(needle in p for p in _check(reg=reg))


def test_duplicate_id_is_red():
    reg = copy.deepcopy(BASE_REG)
    reg["features"].append(copy.deepcopy(reg["features"][0]))
    assert any("id 重复" in p for p in _check(reg=reg))


def test_unreadable_inputs_are_input_errors():
    with pytest.raises(FR.InputError):
        FR.parse_registry({"features": []})
    with pytest.raises(FR.InputError):
        FR.parse_playwright_json({"suites": []}, "空")
    with pytest.raises(FR.InputError):
        FR.parse_playwright_json({"suites": [], "errors": [{"message": "SyntaxError"}]}, "加载错误")
    with pytest.raises(FR.InputError):
        FR.parse_scan({"tests": []})


# ── verify-run：合并态那次真跑 ───────────────────────────────────────────


def _run_report(status_drag="expected", status_legend="expected", drop_legend=False) -> dict:
    files = {
        "a.spec.ts": [_spec("拖动", 10, status=status_drag, projects=("chromium", "chromium-en"))],
        "b.spec.ts": [],
    }
    desc = {} if drop_legend else {"b.spec.ts": {"组": [_spec("挪图例", 12, status=status_legend)]}}
    return _listing(files, desc)


def _verify(report: dict) -> tuple[list[str], int]:
    feats, _ = FR.parse_registry(copy.deepcopy(BASE_REG))
    return FR.verify_run(feats, FR.parse_playwright_json(report, "run"))


def test_verify_run_green_counts_every_registered_test():
    problems, counted = _verify(_run_report())
    assert problems == [] and counted == 2


def test_verify_run_flaky_counts_as_ran():
    problems, _ = _verify(_run_report(status_drag="flaky"))
    assert problems == []


def test_verify_run_skipped_is_red():
    problems, _ = _verify(_run_report(status_legend="skipped"))
    assert any("legend.move" in p and "skipped" in p for p in problems), problems


def test_verify_run_skipped_with_empty_results_is_red():
    report = _run_report()
    t = report["suites"][1]["suites"][0]["specs"][0]["tests"][0]
    t["status"], t["results"] = "skipped", []
    problems, _ = _verify(report)
    assert any("legend.move" in p for p in problems), problems


def test_verify_run_missing_is_red():
    problems, _ = _verify(_run_report(drop_legend=True))
    assert any("legend.move" in p and "不在这次运行里" in p for p in problems), problems


def test_verify_run_failed_is_red():
    problems, _ = _verify(_run_report(status_drag="unexpected"))
    assert any("canvas.drag" in p for p in problems), problems


def test_verify_run_rejects_a_list_report():
    """拿 `--list` 的报告冒充真跑（每条都 skipped、results 为空）：必须红。"""
    problems, counted = _verify(copy.deepcopy(BASE_LIST))
    assert counted == 0 and problems


# ── transitions：与 base 比 ──────────────────────────────────────────────


def _feats(reg: dict):
    return FR.parse_registry(copy.deepcopy(reg))[0]


def _removed_head() -> dict:
    head = copy.deepcopy(BASE_REG)
    head["features"][0]["status"] = "removed"
    head["features"][0]["removed"] = {"reason": "r", "date": "2026-09-26", "approved_by": "u"}
    return head


def test_transition_deleting_an_entry_is_red_even_without_labels():
    head = copy.deepcopy(BASE_REG)
    head["features"] = [f for f in head["features"] if f["id"] != "legend.move"]
    for labels in (None, [], [FR.REMOVAL_LABEL]):
        problems = FR.transitions(_feats(BASE_REG), _feats(head), labels)
        assert any("legend.move" in p and "消失" in p for p in problems), (labels, problems)


def test_transition_removed_without_label_is_red():
    problems = FR.transitions(_feats(BASE_REG), _feats(_removed_head()), ["full-ci"])
    assert any("canvas.drag" in p and FR.REMOVAL_LABEL in p for p in problems), problems


def test_transition_removed_with_label_is_green():
    assert FR.transitions(_feats(BASE_REG), _feats(_removed_head()), [FR.REMOVAL_LABEL]) == []


def test_transition_without_labels_only_checks_deletion():
    """merge_group 没有 PR 标签：active → removed 不判（PR 上已判过），删条目照判。"""
    assert FR.transitions(_feats(BASE_REG), _feats(_removed_head()), None) == []


def test_transition_first_introduction_is_green():
    assert FR.transitions(None, _feats(BASE_REG), []) == []


# ── beta 手动清单 ───────────────────────────────────────────────────────


def test_beta_checklist_lists_desktop_manual_items_only():
    md = FR.beta_checklist(_feats(BASE_REG))
    assert "`desktop.close`" in md and "点红灯：弹三选一" in md
    assert "`canvas.drag`" not in md  # 没有手动步骤的不进清单
    assert "`old.thing`" not in md


# ── 仓库里那份真登记表 ──────────────────────────────────────────────────


def test_the_real_registry_has_a_valid_shape():
    feats, shape = FR.load_registry_text(FR.REGISTRY.read_text(encoding="utf-8"))
    assert shape == []
    ids = {f.id for f in feats}
    # 第一版盘点范围（ADR 0097）里点过名的核心路径都在，且都有 e2e
    for fid in (
        "project.first-launch",
        "canvas.place-and-render",
        "canvas.select-and-drag",
        "figure.select-and-edit",
        "canvas.single-context-bar",
        "canvas.multi-context-bar-align",
        "edit.undo-redo",
        "file.autosave-restore",
        "export.pdf",
        "export.png",
        "export.tiff",
        "style.panel",
        "legend.properties",
        "problems.panel",
        "help.shortcuts",
        "command.palette",
        "tutorial.entry",
    ):
        assert fid in ids, fid
        f = next(x for x in feats if x.id == fid)
        assert f.status == "active" and f.e2e, fid
    # 引用的 spec 文件都在 web/e2e 下（标题是否存在由 frontend job 对着 --list 判）
    for f in feats:
        for r in f.e2e:
            assert (ROOT / "web" / "e2e" / r.spec).is_file(), (f.id, r.spec)


def test_beta_checklist_cli_runs_on_the_real_registry():
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "beta-checklist"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("## 桌面壳手动试用清单")
    assert "- [ ] " in out.stdout


def test_cli_exit_codes(tmp_path):
    """结论用退出码：判据不成立 1、输入不可信 2、通过 0。"""
    reg = tmp_path / "r.json"
    lst = tmp_path / "l.json"
    scan = tmp_path / "s.json"
    reg.write_text(json.dumps(BASE_REG, ensure_ascii=False), encoding="utf-8")
    lst.write_text(json.dumps(BASE_LIST, ensure_ascii=False), encoding="utf-8")
    scan.write_text(json.dumps(BASE_SCAN, ensure_ascii=False), encoding="utf-8")

    def run(*args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--registry", str(reg), *args], capture_output=True
        ).returncode

    assert run("check", "--list-json", str(lst), "--scan-json", str(scan)) == 0
    bad = copy.deepcopy(BASE_SCAN)
    bad["tests"][0]["modifiers"] = [{"kind": "skip", "line": 11, "scope": "test"}]
    scan.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    assert run("check", "--list-json", str(lst), "--scan-json", str(scan)) == 1
    lst.write_text("{}", encoding="utf-8")
    assert run("check", "--list-json", str(lst), "--scan-json", str(scan)) == 2
    rep = tmp_path / "run.json"
    rep.write_text(json.dumps(_run_report(), ensure_ascii=False), encoding="utf-8")
    assert run("verify-run", str(rep)) == 0
    rep.write_text(
        json.dumps(_run_report(status_drag="skipped"), ensure_ascii=False), encoding="utf-8"
    )
    assert run("verify-run", str(rep)) == 1


# ── CI 接线 ─────────────────────────────────────────────────────────────


def _code(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _job(job_id: str) -> str:
    m = re.search(rf"(?m)^  {re.escape(job_id)}:\n(.*?)(?=^  [\w-]+:|\Z)", CI_YML, re.S)
    assert m, f"ci.yml 里切不出 job `{job_id}`"
    return _code(m.group(0))


def _steps(job_block: str) -> list[str]:
    m = re.search(r"(?m)^    steps:\n", job_block)
    assert m
    return re.split(r"(?m)^      - ", job_block[m.end() :])[1:]


def test_frontend_job_runs_the_static_gate_unconditionally():
    """快线（PR + merge_group）上跑：扫描器自测 → check → transitions，三步都不带 if:。"""
    steps = _steps(_job("frontend"))
    runs = "\n".join(steps)
    assert "node web/scripts/e2e-skip-scan.mjs --self-test" in runs
    assert "python3 scripts/ci/feature_registry.py check" in runs
    trans = [s for s in steps if "feature_registry.py transitions" in s]
    assert len(trans) == 1
    assert not re.search(r"(?m)^\s+if:", trans[0])
    for s in steps:
        if "feature_registry.py" in s or "e2e-skip-scan" in s:
            assert not re.search(r"(?m)^\s+if:", s), s
    # PR 字段先按事件分支；merge_group 读自己的 base_sha、标签给 null
    assert (
        "github.event_name == 'pull_request' && github.event.pull_request.base.sha || github.event.merge_group.base_sha"
        in trans[0]
    )
    assert "toJSON(github.event.pull_request.labels.*.name) || 'null'" in trans[0]


def test_posix_e2e_writes_a_json_report_and_verifies_it_right_after():
    """真跑那一半：e2e 那一步写 JSON 报告，紧接着的一步读**同一个路径**判 verify-run。"""
    steps = _steps(_job("posix-e2e"))
    idx = [
        i for i, s in enumerate(steps) if "pnpm e2e --project=chromium --project=chromium-en" in s
    ]
    assert len(idx) == 1
    e2e = steps[idx[0]]
    verify = steps[idx[0] + 1]
    path_expr = "TAVOTTO_E2E_JSON: ${{ runner.temp }}/e2e-results.json"
    assert path_expr in e2e
    assert path_expr in verify
    assert 'feature_registry.py verify-run "$TAVOTTO_E2E_JSON"' in verify
    assert not re.search(r"(?m)^\s+if:", verify)


def test_playwright_config_adds_the_json_reporter_from_that_env():
    assert "process.env.TAVOTTO_E2E_JSON" in PW_CONFIG
    assert re.search(
        r"\['json',\s*\{\s*outputFile:\s*process\.env\.TAVOTTO_E2E_JSON\s*\}\]", PW_CONFIG
    )
