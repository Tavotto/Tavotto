"""发布链健康监控的看护。

这个监控要挡的是审计里最刺眼的那一条
（`docs/audit/2026-08-22-v1-release-process-audit.md` §5）：
Lab Qualification **25 次 run 里 0 次成功**，release.yml 最近一次成功还是
两天前的 v0.8.0，nightly 连续四晚失败——而**没有任何人被通知到**。

所以这里的用例几乎全在钉一件事：**这个监控自己不许变成它要消灭的那种东西**
（一个报平安的门禁）。
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_DIR = ROOT / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import check_release_health as RH  # noqa: E402

WF = ROOT / ".github" / "workflows" / "release-health.yml"
NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _run(days_ago: float, conclusion="success", status="completed", rid=1):
    return {"databaseId": rid, "status": status, "conclusion": conclusion,
            "createdAt": (NOW - timedelta(days=days_ago)).isoformat().replace(
                "+00:00", "Z"),
            "headBranch": "main", "event": "push",
            "url": f"https://x/{rid}"}


SPEC = {"file": "lab-ci.yml", "label": "实验室", "max_age_days": 3, "why": "…"}


# ── 判据的核心：有结论 ≠ 有 run ───────────────────────────────────────────

def test_cancelled_runs_do_not_count_as_a_verification():
    """**这是整个脚本的立足点。**

    Lab Qualification 当时 25 次 run 里 17 次是 cancelled——按「最近有没有
    run」判的话它天天新鲜，而真相是那条通道**从来没跑完过**。
    """
    runs = [_run(0.1, "cancelled", rid=i) for i in range(10)]
    r = RH.assess(SPEC, runs, NOW)
    assert r["level"] == "error"
    assert any("一次都没有跑到结论" in p for p in r["problems"])
    assert r["last_conclusive"] is None


def test_queued_runs_do_not_count_either():
    runs = [_run(0.1, None, status="queued", rid=i) for i in range(4)]
    r = RH.assess(SPEC, runs, NOW)
    assert r["level"] == "error"
    assert r["last_conclusive"] is None


def test_a_failure_is_still_a_conclusion():
    """失败是一次**有效的验证**——它回答了「这条通道现在什么状态」。

    把 failure 也排除在「有结论」之外，会让一条天天失败的通道显示成
    「很久没跑了」，而那是两个完全不同的问题、两种完全不同的处置。
    """
    # 要给一次较早的成功——否则会先命中「一次成功都没有」那条（那是 error），
    # 而这条验的是另一件事：**最近一次结论是失败**本身该怎么报。
    runs = [_run(0.1, "failure", rid=1), _run(1.0, "success", rid=2)]
    r = RH.assess(SPEC, runs, NOW)
    assert r["last_conclusive"]["conclusion"] == "failure"
    assert any("最近一次有结论的 run 是 **failure**" in p for p in r["problems"])
    assert r["level"] == "warning", "失败要告警，但新鲜度本身是好的"


def test_never_succeeded_is_an_error_even_if_recent():
    """**「刚跑过」不等于「跑通过」。**

    这正是 lab-ci 当时的状态：每小时都在跑，每次都失败。
    只看新鲜度的话它是绿的。
    """
    runs = [_run(0.1, "failure", rid=i) for i in range(8)]
    r = RH.assess(SPEC, runs, NOW)
    assert r["level"] == "error"
    assert any("一次成功都没有" in p for p in r["problems"])


def test_a_stale_channel_is_an_error():
    r = RH.assess(SPEC, [_run(9, "success")], NOW)
    assert r["level"] == "error"
    assert any("最近一次有结论是" in p for p in r["problems"])


def test_a_healthy_channel_is_quiet():
    runs = [_run(0.2, "success", rid=1), _run(1.0, "success", rid=2)]
    r = RH.assess(SPEC, runs, NOW)
    assert r["level"] == "ok" and r["problems"] == []


# ── 排队：按**多久**判，不按**几个**判 ───────────────────────────────────

def test_a_long_stuck_queue_is_flagged():
    """判据的主语是「有没有 run 卡住」，不是「有几个在排队」。

    第一版写的是「≥3 个 queued 就告警」，而实测那一刻 release.yml 有
    **2 个**卡了一小时以上的 run——数量判据把它放过去了。
    """
    runs = [_run(0.1, "success", rid=0),
            _run(0.5, None, status="queued", rid=1)]     # 12 小时
    r = RH.assess(SPEC, runs, NOW)
    assert r["stuck_in_queue"] == 1
    assert any("卡在 queued" in p for p in r["problems"])


def test_a_briefly_queued_run_is_not_flagged():
    """正常触发的并发 run 完全无害——只等了几分钟的不该告警。"""
    runs = [_run(0.1, "success", rid=0)] + [
        _run(0.01, None, status="queued", rid=i) for i in range(1, 5)]
    r = RH.assess(SPEC, runs, NOW)
    assert r["stuck_in_queue"] == 0
    assert not any("卡在 queued" in p for p in r["problems"])


def test_stuck_queue_threshold_leaves_room_for_a_real_run():
    """阈值要盖得住实验室 job 自己的时长，否则每次正常排队都会告警。"""
    assert RH.STUCK_QUEUE_HOURS >= 3.0, (
        "实验室资格验证跑满要 3 小时，并发槽让人等一两个小时是正常的")


# ── 被盯的对象 ────────────────────────────────────────────────────────────

def test_every_release_critical_workflow_is_watched():
    watched = {w["file"] for w in RH.WATCH}
    for f in ("release.yml", "lab-ci.yml", "nightly.yml", "desktop-tauri.yml"):
        assert f in watched, f"{f} 没有被盯着"


def test_every_watched_workflow_says_why():
    """阈值要写得出理由。写不出理由的阈值，下次超期时没人知道该不该管。"""
    for w in RH.WATCH:
        assert len(w["why"]) > 15, f"{w['file']} 的 why 太敷衍"
        assert w["max_age_days"] > 0


def test_release_threshold_leaves_room_for_a_weekly_canary():
    """release.yml 平时不跑，靠每周一次的 canary 保持新鲜。

    阈值必须 > 7 天，否则周中每天都会误报一次——而**天天红的监控
    会在第二周被人静音，静音之后它连警告都不会再发出来**。
    """
    rel = next(w for w in RH.WATCH if w["file"] == "release.yml")
    assert rel["max_age_days"] > 7


# ── workflow 本身 ─────────────────────────────────────────────────────────

def _wf_text() -> str:
    return WF.read_text(encoding="utf-8")


def _code_only() -> str:
    """剥掉注释。判据只该看会被执行的那部分。"""
    return "\n".join(ln for ln in _wf_text().splitlines()
                     if not ln.lstrip().startswith("#"))


def test_the_monitor_runs_on_github_hosted_runners():
    """**监控实验室健康的东西不能挂在实验室上。**"""
    code = _code_only()
    assert "self-hosted" not in code
    assert "tavotto-lab" not in code
    assert code.count("runs-on: ubuntu-latest") >= 2


def test_the_monitor_does_not_install_anything():
    """一个监控别人是否健康的东西，自己的成败不该押在一次 pip install 上。"""
    code = _code_only()
    assert "pip install" not in code
    assert "setup-python" not in code
    assert "python3 scripts/ci/check_release_health.py" in code


def test_the_monitor_runs_on_a_schedule():
    # 剥注释再判：`schedule:` 与 `- cron:` 之间隔着一行说明，
    # 而判据不该被排版打断。
    assert re.search(r"schedule:\s*\n\s*- cron:", _code_only())


def test_the_report_is_uploaded_even_when_the_check_fails():
    """报告在**失败时**最有用，而失败正是这一步会被跳过的时候。"""
    text = _wf_text()
    block = text[text.index("上传报告"):]
    assert re.search(r"if:\s*always\(\)", block.split("- name:")[0])


def test_the_canary_never_publishes():
    """canary 演练**绝不发布**。这条错了会让每周一次的监控变成每周一次发版。"""
    code = _code_only()
    assert "-f publish=false" in code
    assert "-f pypi=none" in code
    assert "publish=true" not in code


def test_the_canary_does_not_poll_for_its_own_result():
    """**不轮询等它跑完**——跨 workflow 轮询正是这一轮删掉的那个反模式。

    演练要 30~60 分钟且要占实验室并发槽；结论由下一次 freshness 检查读到。
    """
    code = _code_only()
    assert not re.search(r"\bsleep\s+\d+", code)
    assert not re.search(r"seq\s+1\s+\d{2,}", code)
    assert "gh run watch" not in code


def test_the_canary_dispatches_against_an_exact_sha():
    """演练必须钉在一个精确 SHA 上，不能只说「main」。

    否则「演练过的那份」与「后来打 tag 的那份」可能不是同一个 commit，
    而这条链的全部意义就是**同一个候选 SHA 可以稳定地成为一次正式发布**。
    """
    code = _code_only()
    assert 'SHA="$(git rev-parse HEAD)"' in code
    assert '-f ref="$SHA"' in code


def test_the_monitor_only_needs_actions_read_for_the_check():
    """**这条用例自己曾经是空的，值得记下来。**

    第一版切片写的是 `text.index("  canary:")`，而 `workflow_dispatch` 的输入
    里有一行 `      canary:`——`"  canary:"` 是它的子串，`index` 命中的是那一行，
    位置在 `  freshness:` **之前**，于是切出来的是**空字符串**，
    `"write" not in ""` 恒真。变异测试当场逮到（改了却不红）。

    这是本轮第二次踩同一个坑（另一次是 `"  pypi:"` 命中 `on:` 里的输入名）。
    教训：按 job 名切片必须带换行锚，`"\n  <name>:\n"`。
    """
    text = _wf_text()
    i = text.index("\n  freshness:\n")
    j = text.index("\n  canary:\n")
    assert j > i, "切片顺序不对——这条判据会变成空的"
    fresh = text[i:j]
    assert len(fresh) > 200, f"freshness 段只切出 {len(fresh)} 字符，切分判据失效了"
    code = "\n".join(ln for ln in fresh.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "write" not in code, "freshness 只读，不该有任何 write 权限"


# ── Codex 第一轮逮到的（2026-08-23）────────────────────────────────────

def test_a_legitimately_skipped_job_is_not_reported_as_never_run():
    """**weekly canary 会让 `github_release` / `pypi` 合法 skipped。**

    判「这个 job 有没有真跑过」时 skipped 不算一次验证 —— 那是新鲜度表的
    问题。但判「它是不是从来没出现过」时，skipped 恰恰证明它**出现了、
    并且被有意跳过**。

    不认它的后果是每周误报一次，而**天天红的监控会在第二周被人静音，
    静音之后它连警告都不会再发出来** —— 那正是这个脚本自己写在注释里
    要避免的事。
    """
    import ast
    src = (ROOT / "scripts" / "ci" / "check_release_health.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "find_jobs_never_seen")
    body = ast.unparse(fn)
    assert "skipped" in body, (
        "`find_jobs_never_seen` 不认 skipped —— canary 的 publish=false 会让"
        "`github_release` / `pypi` 每周被误报成「从未执行」")


def test_a_failed_fetch_still_renders_instead_of_crashing():
    """**拿不到数据是最需要它把话说出来的时候。**

    `render_summary()` 无条件读 `counts['success'] / ['failure'] / ['cancelled']`。
    fetch 失败那条路径从前把 `counts` 设成 `{}`，于是渲染当场 KeyError ——
    诊断在最需要时自己挂掉，与 #61 是同一个形状。
    """
    from datetime import datetime, timezone
    broken = {"file": "release.yml", "label": "发布编排", "level": "warning",
              "max_age_days": 8, "why": "x" * 20, "runs_examined": 0,
              "last_conclusive": None, "last_success": None,
              "queued": 0, "stuck_in_queue": 0,
              "counts": {"success": 0, "failure": 0, "cancelled": 0},
              "problems": ["拿不到数据：boom"]}
    out = RH.render_summary([broken], [], datetime(2026, 8, 23, tzinfo=timezone.utc))
    assert "拿不到数据" in out and "release.yml" in out


def test_the_fetch_failure_path_supplies_every_key_render_summary_reads():
    """把两半接起来：**失败路径造出来的那个 dict，渲染读得动。**

    上一条验渲染，这一条验**生产者**——只验渲染的话，
    把 `counts` 改回 `{}` 照样绿。
    """
    import ast
    src = (ROOT / "scripts" / "ci" / "check_release_health.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    # 失败分支里给 counts 的那个字面量必须带齐三个键
    found = False
    for node in ast.walk(main):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
        if "counts" not in keys:
            continue
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and k.value == "counts":
                if isinstance(v, ast.Dict):
                    inner = {kk.value for kk in v.keys if isinstance(kk, ast.Constant)}
                    assert {"success", "failure", "cancelled"} <= inner, (
                        f"失败路径的 counts 缺键：{inner} —— render_summary 会 KeyError")
                    found = True
    assert found, "找不到失败路径里那个带 counts 的字面量"
