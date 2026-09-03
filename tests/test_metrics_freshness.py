"""采集陈旧度门禁：判据本身、阈值边界，以及 workflow 的形状。

这道门禁盯的是一类**不会有任何测试红、只会安静发生**的事故：cron 槽被
GitHub 整个丢弃，采集从此不跑，而没有任何红灯——因为「跑了但失败」才有
红灯，「从没跑」只有沉默。

与 tests/test_merge_queue_workflows.py 同一条纪律：**不用 PyYAML**（它不在
`.venv` 里，importorskip 会让整个模块静默跳过——那正是空门禁），workflow
判据用只认本仓库缩进形状的字符串。
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_metrics_freshness.py"

if not SCRIPT.is_file():
    pytest.skip("没有 scripts/（wheel/sdist 里不含构建与采集脚本）", allow_module_level=True)


def _load():
    spec = importlib.util.spec_from_file_location("check_metrics_freshness", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


checker = _load()
NOW = _dt.datetime(2026, 8, 27, 12, 47, tzinfo=_dt.timezone.utc)


def _run(hours_ago: float, conclusion: str = "success", uploaded: bool = True) -> dict:
    ts = NOW - _dt.timedelta(hours=hours_ago)
    return {
        "conclusion": conclusion,
        "uploaded": uploaded,
        "updated_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# 判据本身
# ---------------------------------------------------------------------------
def test_recent_success_is_ok():
    status, age, _ = checker.evaluate([_run(8.8)], NOW)
    assert status == checker.OK
    assert age == pytest.approx(8.8, abs=0.1)


def test_one_missed_day_is_stale():
    """采集器名义每 24 小时一次；真漏一槽，年龄跳到约 48 小时。"""
    status, age, msg = checker.evaluate([_run(48)], NOW)
    assert status == checker.STALE
    assert age == pytest.approx(48, abs=0.1)
    # 报错必须带补跑命令，否则看见红灯的人还得自己去翻文档
    assert "gh workflow run" in msg


def test_a_delayed_but_delivered_slot_is_not_reported_stale():
    """**这条是被实测加进来的。** 2026-08-27 那天 03:17 的槽迟到约 11 小时
    才投递（run 33080727239，14:10），相邻两次上报的间隔因此可达约 35 小时。
    阈值曾经是 30 小时，那天就会误报——而误报比漏报更贵。
    """
    status, _, _ = checker.evaluate([_run(35)], NOW)
    assert status == checker.OK


def test_threshold_separates_a_delayed_slot_from_a_missed_one():
    """阈值必须**夹在**「迟到但送到了」与「真漏一槽」之间。

    上界不是 48 就够——真漏一槽后下一次还可能再迟到，所以留在 48 以下即可；
    下界必须容得下实测到的 11 小时延迟（24 + 11 = 35）。
    """
    assert 35 < checker.MAX_AGE_HOURS < 48


def test_newest_success_wins_not_newest_run():
    """有新的失败 run 时，年龄仍要按上一次**成功**算——失败不代表数据落了。"""
    status, age, _ = checker.evaluate([_run(1, "failure"), _run(50)], NOW)
    assert status == checker.STALE
    assert age == pytest.approx(50, abs=0.1)


def test_runs_exist_but_none_succeeded():
    status, age, _ = checker.evaluate([_run(2, "failure"), _run(26, "cancelled")], NOW)
    assert status == checker.NO_SUCCESS
    assert age is None


def test_a_queued_or_running_run_is_not_a_conclusion():
    """**排队中 / 跑着的 run 不算「跑过了」。**

    GitHub 对这两种 run 回 `conclusion: null`，而 `updated_at` 照常在动。
    判据要是只看「有没有新的 run」，一条**从没跑通过**的通道就会显示成
    天天新鲜——排队本身会不断刷新时间戳。这里钉死：只有有结论、而且结论
    是 success 的才进计数。

    代码本来就是对的（`conclusion == "success"`），但在此之前没有一条用例
    喂过 `None`——现有参数只覆盖了 failure 与 cancelled。没被执行过的正确
    分支不会保持正确。
    """
    only_queued = [_run(0.1, None), _run(1, None), _run(2, None)]
    status, age, _ = checker.evaluate(only_queued, NOW)
    assert status == checker.NO_SUCCESS, "排队中的 run 被当成了「跑过了」"
    assert age is None
    # 混进一个真正成功但很旧的，仍然要判 STALE 而不是被新排队的 run 顶成 OK
    status, age, _ = checker.evaluate([*only_queued, _run(50)], NOW)
    assert status == checker.STALE
    assert age == pytest.approx(50, abs=0.1)


# ---------------------------------------------------------------------------
# 「跑过了」不是「数据落了」
# ---------------------------------------------------------------------------
def test_dry_run_success_does_not_count_as_fresh():
    """演练跑会跳过上报那一步却整体成功。

    真实场景：cron 漏了一槽，有人手动 `dry_run=true` 看一眼输出——只看
    run 的 conclusion，看门狗就会为此闭嘴 30 小时，而磁盘上那份快照仍是旧的。
    """
    status, age, msg = checker.evaluate([_run(2, uploaded=False)], NOW)
    assert status == checker.DRY_RUN_ONLY
    assert status != checker.OK
    assert age is None
    assert checker.UPLOAD_STEP in msg
    assert "gh workflow run" in msg


def test_age_is_measured_from_the_last_real_upload_not_the_last_dry_run():
    """新的演练跑不能把年龄「刷新」——它没落任何数据。"""
    status, age, _ = checker.evaluate([_run(1, uploaded=False), _run(50, uploaded=True)], NOW)
    assert status == checker.STALE
    assert age == pytest.approx(50, abs=0.1)


def test_upload_step_name_matches_the_collector_workflow():
    """判据认的是一个步骤名。名字在采集器那边一改，这里就该当场红——
    否则判据会安静地永远判成「没上报」。"""
    wf = (ROOT / ".github" / "workflows" / "telemetry-metrics.yml").read_text(encoding="utf-8")
    assert f"- name: {checker.UPLOAD_STEP}" in wf, (
        f"telemetry-metrics.yml 里没有名为 {checker.UPLOAD_STEP!r} 的步骤"
    )
    # 而且它必须仍然是「非演练才跑」的那一步，否则演练照样会被算成上报
    m = re.search(rf"- name: {re.escape(checker.UPLOAD_STEP)}\n\s*if: (.+)", wf)
    assert m and "dry_run" in m.group(1), (
        f"{checker.UPLOAD_STEP} 那一步不再由 dry_run 守卫，判据失效"
    )


# ---------------------------------------------------------------------------
# 「查不到」不是「很旧」
# ---------------------------------------------------------------------------
def test_no_runs_at_all_is_a_broken_observation_not_a_stale_reading():
    """一次都查不到有两种成因：真的从没跑过，或者我们问错了地方。

    把它当成「年龄无穷大」会让一个坏掉的判据（改名 / 换仓库 / token 权限
    不足）看起来像一条真实告警——那比没有告警更坏，因为它会被当成真的。
    """
    status, age, msg = checker.evaluate([], NOW)
    assert status == checker.NO_DATA
    assert status != checker.STALE
    assert age is None
    assert "观测无效" in msg


def test_every_non_ok_status_is_red():
    """NO_DATA / NO_SUCCESS 也必须红：它们同样意味着我们不知道数据还在不在落。"""
    import json
    import tempfile

    for payload, expect in (
        ({"workflow_runs": []}, 1),
        ({"workflow_runs": [_run(2, "failure")]}, 1),
        ({"workflow_runs": [_run(2, uploaded=False)]}, 1),
        ({"workflow_runs": [_run(50)]}, 1),
        ({"workflow_runs": [_run(3)]}, 0),
    ):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(payload, fh)
            path = fh.name
        assert checker.main(["--runs-json", path, "--now", NOW.isoformat()]) == expect


# ---------------------------------------------------------------------------
# workflow 形状：这道门禁自己不能有它要防的那个毛病
# ---------------------------------------------------------------------------
WF = ROOT / ".github" / "workflows" / "metrics-freshness.yml"


def _code() -> str:
    return "\n".join(
        ln for ln in WF.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")
    )


def test_gate_does_not_rely_on_cron_alone():
    """**这是本文件最重要的一条。** 只挂 schedule 的看门狗和它看的那个东西
    共享同一个失败模式：cron 被丢弃时两个一起哑。必须有事件触发兜底。"""
    code = _code()
    assert "schedule:" in code
    assert "push:" in code and "branches: [main]" in code


def test_watcher_does_not_share_a_concurrency_group_with_the_watched():
    """共用一个组，检查会在采集器运行时排队甚至被挤掉——观测者与被观测者
    绝不能共享命运。"""
    code = _code()
    assert "group: metrics-freshness" in code
    assert "group: distribution-metrics" not in code


def test_gate_is_read_only_and_actually_runs_the_script():
    code = _code()
    assert "contents: read" in code and "actions: read" in code
    assert "contents: write" not in code
    assert "scripts/check_metrics_freshness.py" in code


def test_freshness_cron_does_not_collide_with_the_collector_slot():
    """两道 cron 挤在同一个调度窗口，会一起被同一波丢弃。"""
    import re

    mine = re.search(r'cron: "(\S+) (\S+) ', _code())
    other = re.search(
        r'cron: "(\S+) (\S+) ',
        (ROOT / ".github" / "workflows" / "telemetry-metrics.yml").read_text(encoding="utf-8"),
    )
    assert mine and other, "解析不出 cron——判据失效，当场红"
    assert abs(int(mine.group(2)) - int(other.group(2))) >= 4, "两道 cron 相隔不足 4 小时"
