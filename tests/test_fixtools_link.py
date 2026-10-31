"""AI 修复工具箱：`link_data_file`（把 AI 找到的数据文件接到脚本上，ADR 0106 修订 2026-10-24）。

判据的主语：

* 规则：**磁盘上的项目设置**（`inputremap.rules_for`）与脚本字节；不看工具返回的文案。
* 同意：**注入的假 ask 看到的卡片**与「没点头 = 磁盘上一个字都没写」。
* 试跑能不能读到数据：**真的子进程**（`try_run` 的守卫进程）里脚本打开项目外的文件后出图。
* 回滚：快照 → 回滚后设置逐键相等（含「原来没有这个键」）。
"""

from __future__ import annotations

import hashlib
import json
import sys

import pytest

from tavotto.engine import config, inputremap
from tavotto.engine.fixtools import REGISTRY, readscope, snapshots, trial
from tests.test_fixtools import call, make_ctx, put
from tests.test_fixtools_change import Asker, audit_lines


@pytest.fixture
def box(tmp_path, monkeypatch):
    """假主目录 + 假配置 / 数据目录（与 test_fixtools_change 的 box 同形状；从不碰真实主目录）。"""
    home = tmp_path / "home"
    proj = home / "proj"
    proj.mkdir(parents=True)
    cfg, data = tmp_path / "cfg", tmp_path / "data"
    cfg.mkdir()
    data.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(data))
    (cfg / "config.json").write_bytes(b'{"ai": {"providers": []}}')
    return type("Box", (), {"home": home, "proj": proj, "cfg": cfg, "data": data, "tmp": tmp_path})


PLOT = (
    "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
    "rows = [l.split(',') for l in open({path!r}).read().split()]\n"
    "plt.plot([float(a) for a, b in rows], [float(b) for a, b in rows])\n"
)
RELATIVE = PLOT.format(path="data/x.csv")


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rules(box):
    return inputremap.rules_for(box.proj)


def _report(box, script, requested="data/x.csv", absolute=False):
    """报告里 `input_location` 需求的载荷：与数据失联弹窗同一份（`payload_for`）。"""
    payload = inputremap.payload_for(
        script, box.proj, {"requested": requested, "absolute": absolute}
    )
    return lambda: {"requirements": [{"kind": "input_location", "payload": payload}]}


def _setup(box, script_text=RELATIVE, ask="yes", **kw):
    script = put(box.proj / "fig.py", script_text)
    data = put(box.home / "Downloads" / "data" / "x.csv", "1,2\n3,4\n")
    asker = None if ask is None else (ask if callable(ask) else Asker(ask))
    kw.setdefault("report", _report(box, "fig.py"))
    ctx = make_ctx(
        box,
        script_path=script,
        ask=asker,
        environments={"e": sys.executable},
        **kw,
    )
    ctx.asker = asker  # type: ignore[attr-defined]
    return ctx, script, data


def _args(data, **over):
    return {
        "requested": "data/x.csv",
        "chosen": str(data),
        "summary": "I found x.csv in your Downloads folder; its columns match. Use it?",
        "evidence": "two numeric columns, like the script reads",
        **over,
    }


# --------------------------------------------------------------------------- 登记
def test_link_data_file_is_registered_as_a_tavotto_tier_tool_that_waits_for_the_user():
    t = REGISTRY["link_data_file"]
    assert t.tier == "tavotto" and t.waits_for_user
    assert set(t.schema["required"]) == {"requested", "chosen", "summary", "evidence"}


# --------------------------------------------------------------------------- 总是先问
def test_the_user_is_always_asked_and_the_card_shows_the_path_and_the_evidence(box):
    ctx, script, data = _setup(box)
    before = _sha(script)
    r = call(ctx, "link_data_file", _args(data))
    assert not r.is_error, r.content
    [card] = ctx.asker.cards
    assert card["kind"] == "approve" and card["tool"] == "link_data_file"
    assert card["summary"].startswith("I found x.csv")
    # 卡片里的路径用平台的分隔符（Windows 是反斜杠）；比较时统一斜杠
    assert "~/Downloads/data/x.csv" in card["details"].replace("\\", "/")
    assert "two numeric columns" in card["details"]
    assert _sha(script) == before  # 脚本字节不变
    assert (box.proj / "data").exists() is False  # 没有往项目里复制数据


def test_no_ask_hook_means_needs_approval_and_nothing_is_written(box):
    ctx, script, data = _setup(box, ask=None)
    r = call(ctx, "link_data_file", _args(data))
    assert r.is_error and r.code == "needs_approval"
    assert _rules(box) == [] and "input_remap" not in config.project_settings(str(box.proj))
    assert snapshots.list_ids(ctx) == []  # 没获同意连快照都不打


@pytest.mark.parametrize("answer", ["no", "cancelled", "timeout", "garbage"])
def test_a_user_who_does_not_say_yes_gets_nothing_written(box, answer):
    ctx, script, data = _setup(box, ask=answer)
    r = call(ctx, "link_data_file", _args(data))
    assert r.is_error and r.code == "needs_approval"
    assert "input_remap" not in config.project_settings(str(box.proj))


def test_a_crashing_ask_is_not_consent(box):
    def boom(card):
        raise RuntimeError("ui gone")

    ctx, script, data = _setup(box, ask=boom)
    assert call(ctx, "link_data_file", _args(data)).code == "needs_approval"
    assert _rules(box) == []


# --------------------------------------------------------------------------- 同意之后
def test_yes_writes_the_same_rule_the_missing_data_dialog_would(box):
    ctx, script, data = _setup(box)
    r = call(ctx, "link_data_file", _args(data))
    assert not r.is_error and "try_run" in r.content and r.mutated
    real = str(data.resolve())
    expected = inputremap.derive("data/x.csv", real, chosen_is_dir=False)
    assert _rules(box) == [expected]
    assert expected["kind"] == "prefix" and expected["from"] == ""
    assert ctx.call_meta["approved_by"] == "user" and ctx.call_meta["snapshot"]


def test_the_tool_goes_through_the_one_shared_link_function(box, monkeypatch):
    ctx, script, data = _setup(box)
    seen = []
    real_link = inputremap.link

    def spy(root, requested, chosen, kind):
        seen.append((requested, chosen, kind))
        return real_link(root, requested, chosen, kind)

    monkeypatch.setattr(inputremap, "link", spy)
    assert not call(ctx, "link_data_file", _args(data)).is_error
    assert seen == [("data/x.csv", str(data.resolve()), "file")]


def test_a_folder_can_be_linked_and_the_rule_is_found_inside_it(box):
    ctx, script, data = _setup(box)
    r = call(ctx, "link_data_file", _args(data, chosen=str(data.parent.parent)))
    assert not r.is_error, r.content
    assert _rules(box) == [{"kind": "prefix", "from": "", "to": str(data.parent.parent.resolve())}]


def test_the_application_is_told_so_windows_and_sessions_can_refresh(box):
    seen = []
    ctx, script, data = _setup(box, on_input_remap_changed=lambda st, why: seen.append((st, why)))
    assert not call(ctx, "link_data_file", _args(data)).is_error
    [(state, why)] = seen
    assert why == "added" and len(state["rules"]) == 1 and state["generation"]
    # 通知炸了也不丢已落盘的规则
    ctx2, _, _ = _setup(box, on_input_remap_changed=lambda st, why: 1 / 0)
    assert not call(ctx2, "link_data_file", _args(data)).is_error


def test_audit_records_the_user_approval_and_the_snapshot(box):
    from tavotto.engine.fixtools import AuditLog, run_tool

    ctx, script, data = _setup(box)
    audit = AuditLog(box.data / "ai" / "fix_audit.jsonl")
    run_tool(REGISTRY["link_data_file"], ctx, _args(data), audit)
    [line] = audit_lines(box)
    assert line["tool"] == "link_data_file" and line["approved_by"] == "user" and line["snapshot"]


# --------------------------------------------------------------------------- requested 必须有出处
def test_a_requested_path_the_script_never_mentions_is_refused_before_asking(box):
    ctx, script, data = _setup(box)
    r = call(ctx, "link_data_file", _args(data, requested="secret/other.csv"))
    assert r.is_error and r.code == "requested_unknown"
    assert ctx.asker.cards == [] and _rules(box) == []


def test_a_literal_in_the_script_is_enough_even_without_a_report(box):
    ctx, script, data = _setup(box, report=None)
    assert not call(ctx, "link_data_file", _args(data)).is_error
    assert len(_rules(box)) == 1


def test_a_path_only_the_report_knows_is_accepted(box):
    # 脚本用拼出来的路径：源码里没有这个常量，只有 worker 亲眼见到的那串在报告里
    script = put(
        box.proj / "fig.py",
        "import os\nname = 'x'\nopen(os.path.join('data', name + '.csv'))\n",
    )
    data = put(box.home / "Downloads" / "data" / "x.csv", "1,2\n")
    asker = Asker("yes")
    ctx = make_ctx(
        box,
        script_path=script,
        ask=asker,
        report=_report(box, "fig.py", requested="data/x.csv"),
    )
    assert not call(ctx, "link_data_file", _args(data)).is_error
    # 没有报告时同一个 requested 就没有出处
    ctx2 = make_ctx(box, script_path=script, ask=Asker("yes"))
    assert call(ctx2, "link_data_file", _args(data, requested="data/y.csv")).code == (
        "requested_unknown"
    )


def test_a_path_the_script_only_probes_cannot_be_linked_and_the_script_is_not_edited(box):
    script = put(
        box.proj / "fig.py",
        "import os, sys\nif not os.path.exists('data/x.csv'):\n    sys.exit('no data')\n",
    )
    data = put(box.home / "Downloads" / "data" / "x.csv", "1,2\n")
    before = _sha(script)
    ctx = make_ctx(box, script_path=script, ask=Asker("yes"))
    r = call(ctx, "link_data_file", _args(data))
    assert r.is_error and r.code == "link_unreachable"
    assert ctx.ask.cards == [] and _rules(box) == [] and _sha(script) == before


# --------------------------------------------------------------------------- chosen 的范围
def test_chosen_outside_the_readable_scope_is_refused_before_asking(box):
    ctx, script, data = _setup(box)
    outside = put(box.tmp / "elsewhere" / "data" / "x.csv", "1,2\n")
    r = call(ctx, "link_data_file", _args(data, chosen=str(outside)))
    assert r.is_error and r.code == readscope.REASON_OUTSIDE
    assert ctx.asker.cards == [] and _rules(box) == []


def test_chosen_in_a_credential_location_is_refused(box):
    ctx, script, data = _setup(box)
    bad = put(box.home / ".ssh" / "data" / "x.csv", "1,2\n")
    r = call(ctx, "link_data_file", _args(data, chosen=str(bad)))
    assert r.is_error and r.code == readscope.REASON_DENIED
    assert ctx.asker.cards == [] and _rules(box) == []


def test_a_symlink_into_a_credential_location_is_refused(box):
    ctx, script, data = _setup(box)
    target = put(box.home / ".aws" / "x.csv", "1,2\n")
    link = box.home / "Downloads" / "link.csv"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink not available")
    r = call(ctx, "link_data_file", _args(data, chosen=str(link)))
    assert r.is_error and r.code == readscope.REASON_DENIED and _rules(box) == []


def test_chosen_that_does_not_exist_or_does_not_contain_the_file_is_refused(box):
    ctx, script, data = _setup(box)
    r = call(ctx, "link_data_file", _args(data, chosen=str(box.home / "Downloads" / "nope.csv")))
    assert r.is_error and r.code == "chosen_not_found"
    empty = box.home / "Downloads" / "empty"
    empty.mkdir()
    r = call(ctx, "link_data_file", _args(data, chosen=str(empty)))
    assert r.is_error and r.code == inputremap.ERROR_NOT_FOUND_IN_DIR
    assert ctx.asker.cards == [] and _rules(box) == []


@pytest.mark.parametrize(
    "bad",
    [{"requested": ""}, {"chosen": 5}, {"evidence": ""}, {"summary": "   "}, {"requested": None}],
)
def test_bad_arguments(box, bad):
    ctx, script, data = _setup(box)
    r = call(ctx, "link_data_file", _args(data, **bad))
    assert r.is_error and r.code in ("bad_arguments", "bad_path")
    assert ctx.asker.cards == [] and _rules(box) == []


# --------------------------------------------------------------------------- 快照与回滚
def test_rollback_removes_the_link_and_restores_what_there_was_before(box):
    ctx, script, data = _setup(box)
    assert "input_remap" not in config.project_settings(str(box.proj))  # 原来没有
    assert not call(ctx, "link_data_file", _args(data)).is_error
    snap = ctx.call_meta["snapshot"]
    assert snapshots.load(ctx, snap)["settings"]["input_remap"] is None
    gen = inputremap.generation(box.proj)
    seen = []
    ctx.on_input_remap_changed = lambda st, why: seen.append(why)
    assert not call(ctx, "rollback", {"snapshot": snap}).is_error
    assert "input_remap" not in config.project_settings(str(box.proj))
    assert _rules(box) == [] and inputremap.generation(box.proj) > gen and seen == ["restored"]


def test_rollback_puts_back_a_previous_rule_exactly(box):
    ctx, script, data = _setup(box)
    first = {"kind": "prefix", "from": "", "to": str(data.parent.parent.resolve())}
    inputremap.add_rule(box.proj, first)
    raw_before = config.project_settings(str(box.proj))["input_remap"]
    other = put(box.home / "Downloads" / "b" / "data" / "x.csv", "9,9\n")
    assert not call(ctx, "link_data_file", _args(data, chosen=str(other))).is_error
    assert _rules(box) == [{"kind": "prefix", "from": "", "to": str(other.parent.parent.resolve())}]
    assert not call(ctx, "rollback", {"snapshot": ctx.call_meta["snapshot"]}).is_error
    assert config.project_settings(str(box.proj))["input_remap"] == raw_before


def test_rolling_back_something_else_does_not_touch_the_remap_table(box):
    ctx, script, data = _setup(box)
    assert not call(ctx, "link_data_file", _args(data)).is_error
    link_snap = ctx.call_meta["snapshot"]
    assert not call(ctx, "switch_execution", {"mode": "safe"}).is_error
    seen = []
    ctx.on_input_remap_changed = lambda st, why: seen.append(why)
    gen = inputremap.generation(box.proj)
    # 回到 switch 之前：链接还在，改指表不换代、不通知
    assert not call(ctx, "rollback", {"snapshot": ctx.call_meta["snapshot"]}).is_error
    assert len(_rules(box)) == 1 and inputremap.generation(box.proj) == gen and seen == []
    assert link_snap


# --------------------------------------------------------------------------- try_run 带上规则（真子进程）
def test_try_run_reads_a_linked_file_from_outside_the_project_end_to_end(box):
    ctx, script, data = _setup(box)
    before = _sha(script)
    first = call(ctx, "try_run", {"env": "e"})
    assert "figures: None" in first.content or "exit code 1" in first.content  # 没接上之前读不到
    assert "FileNotFoundError" in first.content
    assert not call(ctx, "link_data_file", _args(data)).is_error
    r = call(ctx, "try_run", {"env": "e"})
    assert not r.is_error, r.content
    assert "exit code 0" in r.content and "figures: 1" in r.content
    assert "input_remap: this project's data links were applied" in r.content
    assert _sha(script) == before and not (box.proj / "data").exists()
    assert data.read_text() == "1,2\n3,4\n"  # 数据文件也没动


def test_try_run_resolves_an_absolute_project_path_inside_the_copy(box):
    # 脚本用 __file__ 拼出 <项目>/data/x.csv：副本里这一串是 <副本>/data/x.csv，规则按原项目路径记，要换算
    src = (
        "import os\nimport matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'x.csv')\n"
        "rows = [l.split(',') for l in open(p).read().split()]\n"
        "plt.plot([float(a) for a, b in rows], [float(b) for a, b in rows])\n"
    )
    script = put(box.proj / "fig.py", src)
    data = put(box.home / "Downloads" / "data" / "x.csv", "1,2\n3,4\n")
    inputremap.add_rule(
        box.proj, {"kind": "prefix", "from": str(script.parent), "to": str(data.parent.parent)}
    )
    ctx = make_ctx(box, script_path=script, environments={"e": sys.executable})
    r = call(ctx, "try_run", {"env": "e"})
    assert "exit code 0" in r.content and "figures: 1" in r.content, r.content


def _canon(rules):
    "规则的 from/to 按引擎比对用的同一个规范化（remap_parts：反斜杠即分隔符、盘符不分大小写）折成可比较形式。"
    from tavotto.engine import figcapture

    return [
        {
            **r,
            "from": figcapture.remap_parts(r["from"]) if r["from"] else None,
            "to": figcapture.remap_parts(r["to"]),
        }
        for r in rules
    ]


def test_trial_remap_rules_only_translate_absolute_paths_inside_the_copied_folder(tmp_path):
    src, work = tmp_path / "proj", tmp_path / "trial" / "work"
    rules = [
        {"kind": "prefix", "from": "", "to": "/data"},
        {"kind": "prefix", "from": "data", "to": "/data2"},
        {"kind": "prefix", "from": (src / "data").as_posix(), "to": "/data3"},
        {"kind": "prefix", "from": (tmp_path / "other").as_posix(), "to": "/data4"},
    ]
    out = trial.trial_remap_rules(rules, [src], work)
    assert _canon(out[:4]) == _canon(rules)  # 原规则一条不少（写死的绝对路径照旧命中）
    assert _canon(out[4:]) == _canon(
        [{"kind": "prefix", "from": str(work / "data"), "to": "/data3"}]
    )


def test_a_custom_runner_gets_the_rules_only_when_there_are_some(box):
    seen = []

    def runner(python, work, name, mode, timeout, **kw):
        seen.append(kw)
        return {"status": "ok", "returncode": 0, "figures": 1, "stderr": ""}

    ctx, script, data = _setup(box, runner=runner)
    call(ctx, "try_run", {"env": "e"})
    assert seen == [{}]
    inputremap.add_rule(box.proj, {"kind": "prefix", "from": "", "to": str(data.parent.parent)})
    call(ctx, "try_run", {"env": "e"})
    assert seen[1]["input_remap"][0]["to"] == str(data.parent.parent)


# --------------------------------------------------------------------------- 报告里能看到 requested
def test_read_run_report_shows_the_missing_input_payload(box):
    ctx, script, data = _setup(box)
    r = call(ctx, "read_run_report", {})
    body = json.loads(r.content) if r.content.startswith("{") else {}
    assert body["missing_input"]["requested"] == "data/x.csv"


# --------------------------------------------------------------------------- 应用侧的通知
def test_the_apps_notifier_stops_dependent_work_and_publishes_under_the_bound_project(
    box, monkeypatch
):
    """修复会话在工作线程里，没有请求上下文：通知必须钉住项目，否则会对默认项目收会话、发事件。"""
    import types

    from tavotto import app as m

    ctx = types.SimpleNamespace(path=box.proj, id="pj1")
    seen = []
    monkeypatch.setattr(
        m, "_stop_remap_dependent_work", lambda root: seen.append(("stop", root, m._request_ctx()))
    )
    monkeypatch.setattr(
        m,
        "_publish_remap_changed",
        lambda root, why, gen: seen.append(("publish", why, gen, m._request_ctx())),
    )
    m._fix_remap_notifier(ctx)({"generation": 7}, "added")
    assert seen == [("stop", str(box.proj), ctx), ("publish", "added", 7, ctx)]


def test_the_system_prompt_teaches_the_missing_data_routine():
    from tavotto.engine import fixsession

    p = fixsession.SYSTEM_PROMPT
    assert "missing_input" in p and "link_data_file" in p and "find_files" in p
    assert "pick_file" in p and "NEVER edit the script" in p


# --------------------------------------------------------------------------- 用户选过的文件免第二张卡
def _opts(*paths):
    # 真实使用时主目录下的路径在标签里被脱敏成 `~/…`（选项 id 有长度上限），路径就写在标签里
    return [{"id": f"o{i}", "label": f"{p} - candidate"} for i, p in enumerate(paths)]


def _choice_asker(answer_for):
    cards = []

    def ask(card):
        cards.append(card)
        return {"answer": answer_for(card)}

    ask.cards = cards  # type: ignore[attr-defined]
    return ask


def test_a_file_chosen_in_ask_user_is_linked_without_a_second_card(box):
    ctx, script, data = _setup(box, ask=None)
    ask = _choice_asker(lambda c: "o0" if c["kind"] == "choice" else "no")
    ctx.ask = ask
    r = call(ctx, "ask_user", {"question": "Which?", "options": _opts("~/Downloads/data/x.csv")})
    assert not r.is_error
    step = ctx.step
    assert not call(ctx, "link_data_file", _args(data)).is_error
    assert [c["kind"] for c in ask.cards] == ["choice"]  # 没有 approve 卡
    assert ctx.call_meta["approved_by"] == "user" and ctx.call_meta["approved_step"] == step
    assert len(_rules(box)) == 1 and ctx.call_meta["snapshot"]


def test_a_tilde_path_in_the_chosen_option_counts(box):
    ctx, script, data = _setup(box, ask=None)
    ctx.ask = _choice_asker(lambda c: "o0")
    call(ctx, "ask_user", {"question": "Which?", "options": _opts("~/Downloads/data/x.csv")})
    assert not call(ctx, "link_data_file", _args(data)).is_error
    assert ctx.call_meta["approved_by"] == "user" and ctx.call_meta.get("approved_step")


def test_choosing_a_then_linking_b_still_asks(box):
    ctx, script, data = _setup(box, ask=None)
    other = put(box.home / "Downloads" / "b" / "data" / "x.csv", "9,9\n")
    ask = _choice_asker(lambda c: "o0" if c["kind"] == "choice" else "yes")
    ctx.ask = ask
    call(
        ctx,
        "ask_user",
        {
            "question": "Which?",
            "options": _opts("~/Downloads/data/x.csv", "~/Downloads/b/data/x.csv"),
        },
    )
    assert not call(ctx, "link_data_file", _args(data, chosen=str(other))).is_error
    assert [c["kind"] for c in ask.cards] == ["choice", "approve"]


def test_a_file_the_ai_found_itself_still_asks(box):
    ctx, script, data = _setup(box)
    assert not call(ctx, "link_data_file", _args(data)).is_error
    assert [c["kind"] for c in ctx.asker.cards] == ["approve"]


def test_an_option_without_a_path_does_not_unlock_anything(box):
    ctx, script, data = _setup(box, ask=None)
    ask = _choice_asker(lambda c: "o0" if c["kind"] == "choice" else "yes")
    ctx.ask = ask
    call(
        ctx, "ask_user", {"question": "Which?", "options": [{"id": "o0", "label": "the first one"}]}
    )
    assert ctx.user_chosen_paths == {}
    call(ctx, "link_data_file", _args(data))
    assert ask.cards[-1]["kind"] == "approve"


def test_a_file_picked_with_pick_file_is_linked_without_a_card(box):
    ctx, script, data = _setup(box, ask=None)
    ask = _choice_asker(lambda c: str(data) if c["kind"] == "pick" else "no")
    ctx.ask = ask
    assert not call(ctx, "pick_file", {"purpose": "the data", "kind": "file"}).is_error
    assert not call(ctx, "link_data_file", _args(data)).is_error
    assert [c["kind"] for c in ask.cards] == ["pick"]
    assert ctx.call_meta["approved_by"] == "user"


def test_prompt_and_description_say_link_data_file_has_its_own_card():
    from tavotto.engine import fixsession

    assert "do NOT propose_fix" in fixsession.SYSTEM_PROMPT
    assert "do NOT call propose_fix first" in REGISTRY["link_data_file"].description


# --------------------------------------------------------------------------- 选项的 path 与标签兜底
def _ask_with(ctx, options):
    ask = _choice_asker(lambda c: "a" if c["kind"] == "choice" else "no")
    ctx.ask = ask
    assert not call(ctx, "ask_user", {"question": "Which?", "options": options}).is_error
    return ask


def test_an_option_path_is_enough_and_stays_out_of_the_public_card(box):
    ctx, script, data = _setup(box, ask=None)
    ask = _ask_with(ctx, [{"id": "a", "label": "the first one", "path": str(data)}])
    assert "path" not in ask.cards[0]["options"][0]  # 公开卡片只有 id + label
    assert str(data) not in json.dumps(ask.cards)
    assert not call(ctx, "link_data_file", _args(data)).is_error
    assert [c["kind"] for c in ask.cards] == ["choice"]
    assert ctx.call_meta["approved_by"] == "user"


def test_an_option_path_outside_scope_or_in_a_credential_place_is_not_remembered(box):
    ctx, script, data = _setup(box, ask=None)
    outside = put(box.tmp / "elsewhere" / "x.csv", "1,2\n")
    secret = put(box.home / ".ssh" / "x.csv", "1,2\n")
    for p in (outside, secret):
        ctx.user_chosen_paths.clear()
        _ask_with(ctx, [{"id": "a", "label": "x", "path": str(p)}])
        assert ctx.user_chosen_paths == {}


def test_a_label_of_the_form_path_colon_description_counts(box):
    ctx, script, data = _setup(box, ask=None)
    ask = _ask_with(
        ctx, [{"id": "a", "label": "~/Downloads/data/x.csv: two columns, time and signal"}]
    )
    assert not call(ctx, "link_data_file", _args(data)).is_error
    assert [c["kind"] for c in ask.cards] == ["choice"]


@pytest.mark.parametrize("tail", [",", ";", ".", ")", "'", '"'])
def test_trailing_punctuation_is_stripped_but_the_extension_dot_stays(box, tail):
    ctx, script, data = _setup(box, ask=None)
    _ask_with(ctx, [{"id": "a", "label": f"see ~/Downloads/data/x.csv{tail} for details"}])
    assert len(ctx.user_chosen_paths) == 1


def test_try_run_reaches_numpy_loadtxt_that_is_imported_only_inside_the_script(box):
    # numpy 在脚本里才 import：改指装得太早就包不到 DataSource.open，np.loadtxt 先查存在性会绕过
    src = (
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "import numpy as np\n"
        "a = np.loadtxt('data/x.txt', delimiter=',')\n"
        "plt.plot(a[:, 0], a[:, 1])\n"
    )
    script = put(box.proj / "fig.py", src)
    data = put(box.home / "Downloads" / "data" / "x.txt", "1,2\n3,4\n")
    inputremap.add_rule(box.proj, {"kind": "prefix", "from": "", "to": str(data.parent.parent)})
    ctx = make_ctx(box, script_path=script, environments={"e": sys.executable})
    r = call(ctx, "try_run", {"env": "e"})
    assert "exit code 0" in r.content and "figures: 1" in r.content, r.content


_FILE_PLOT = (
    "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
    "from pathlib import Path\n"
    "rows = [l.split(',') for l in (Path(__file__).parent / 'data' / 'x.csv').read_text().split()]\n"
    "plt.plot([float(a) for a, b in rows], [float(b) for a, b in rows])\n"
)


def test_try_run_translates_a_rule_whose_from_is_an_ancestor_of_the_copied_folder(box):
    # 项目 <proj>、脚本在 scripts/ 下用 __file__ 拼路径；用户选了另一棵树里同构的位置：
    # derive 推出 prefix <proj> -> <Downloads>，from 是脚本目录的祖先
    script = put(box.proj / "scripts" / "fig.py", _FILE_PLOT)
    data = put(box.home / "Downloads" / "scripts" / "data" / "x.csv", "1,2\n3,4\n")
    rule = inputremap.derive(str(script.parent / "data" / "x.csv"), str(data), chosen_is_dir=False)
    assert _canon([rule]) == _canon(
        [{"kind": "prefix", "from": str(box.proj), "to": str(box.home / "Downloads")}]
    )
    inputremap.add_rule(box.proj, rule)
    ctx = make_ctx(box, script_path=script, environments={"e": sys.executable})
    r = call(ctx, "try_run", {"env": "e"})
    assert "exit code 0" in r.content and "figures: 1" in r.content, r.content


def test_try_run_translates_a_rule_whose_from_is_exactly_the_copied_folder(box):
    script = put(box.proj / "scripts" / "fig.py", _FILE_PLOT)
    data = put(box.home / "Downloads" / "elsewhere" / "data" / "x.csv", "1,2\n3,4\n")
    inputremap.add_rule(
        box.proj, {"kind": "prefix", "from": str(script.parent), "to": str(data.parent.parent)}
    )
    ctx = make_ctx(box, script_path=script, environments={"e": sys.executable})
    r = call(ctx, "try_run", {"env": "e"})
    assert "exit code 0" in r.content and "figures: 1" in r.content, r.content


def test_trial_remap_rules_translate_ancestor_rules_and_leave_unrelated_ones(tmp_path):
    src, work = tmp_path / "proj" / "scripts", tmp_path / "trial" / "work"
    anc = {"kind": "prefix", "from": (tmp_path / "proj").as_posix(), "to": "/dl"}
    other = {"kind": "prefix", "from": (tmp_path / "else").as_posix(), "to": "/dl2"}
    out = trial.trial_remap_rules([anc, other], [src], work)
    assert _canon(out) == _canon(
        [anc, other, {"kind": "prefix", "from": str(work), "to": "/dl/scripts"}]
    )
