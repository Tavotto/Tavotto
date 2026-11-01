"""Codex CLI 运行事实（诊断包 TVD-BCZ0）：几分钟的 Codex 会话在诊断包里只剩 `opaque`，判不了根因。

`ai_agents.CliRunFacts` 在 `classify_event` 同一条 stdout 解析路径上累计**数字 / 布尔 / 闭集码**，写进
(a) exec trace 的 `agent_run` span attributes，(b)「AI 会话结束」日志行。判据的主语是**格式化后的诊断输出**：
闭集码明文可见，命令文本 / 路径 / 模型输出这类自由文本金丝雀一个字都不出现。
"""

import ast
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

from tavotto.engine import ai_agents, ai_bridge, diagnostics, exectrace

ENGINE = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine"
CANARY_CMD = "Rscript /Users/alice/secret_fig_Boom.R"
CANARY_PATH = "/Users/alice/Secret Project/out.py"
CANARY_TEXT = "Alice Smith asked me to plot HeLa"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _ev(**kw) -> str:
    return json.dumps(kw)


def _item(etype, **item) -> str:
    return _ev(type=etype, item=item)


def _feed(facts, clock, steps):
    for dt, line in steps:
        clock.t += dt
        facts.observe(line)


def _session(tmp_path, clock):
    cwd = tmp_path / "cache" / "ai_convert" / "s1"
    cwd.mkdir(parents=True)
    return cwd, ai_agents.CliRunFacts(str(cwd), "fig_matplotlib.py", clock=clock)


def test_codex_facts_accumulate_from_real_shaped_jsonl(tmp_path):
    clock = Clock()
    cwd, facts = _session(tmp_path, clock)
    inside = str(cwd / "fig_matplotlib.py")
    _feed(
        facts,
        clock,
        [
            (0.5, "OpenAI Codex v0.166.0 (research preview)"),  # 横幅：不算事件
            (2.0, _ev(type="thread.started", thread_id="t1")),
            (0.1, _ev(type="turn.started")),
            (1.0, _item("item.completed", id="i0", type="reasoning", text=CANARY_TEXT)),
            (0.2, _item("item.started", id="i1", type="command_execution", command=CANARY_CMD)),
            (
                30.0,
                _item(
                    "item.completed",
                    id="i1",
                    type="command_execution",
                    command=CANARY_CMD,
                    aggregated_output=CANARY_TEXT,
                    exit_code=127,
                    status="failed",
                ),
            ),
            (0.2, _item("item.started", id="i2", type="command_execution", command="ls")),
            (
                0.3,
                _item(
                    "item.completed",
                    id="i2",
                    type="command_execution",
                    command="ls",
                    exit_code=0,
                    status="completed",
                ),
            ),
            (
                0.5,
                _item(
                    "item.completed",
                    id="i3",
                    type="file_change",
                    status="completed",
                    changes=[{"path": inside, "kind": "add"}],
                ),
            ),
            (
                0.5,
                _item(
                    "item.completed",
                    id="i4",
                    type="file_change",
                    status="completed",
                    changes=[{"path": CANARY_PATH, "kind": "add"}, {"path": "notes.txt"}],
                ),
            ),
            (0.2, _item("item.completed", id="i5", type="agent_message", text=CANARY_TEXT)),
            (0.1, _ev(type="error", message=CANARY_TEXT)),
            (0.1, _ev(type="turn.failed", error={"message": CANARY_TEXT})),
        ],
    )
    clock.t += 7.0  # 最后一个事件之后到进程结束
    snap = facts.snapshot(relay=True)
    assert snap["events"] == 12
    assert (snap["commands"], snap["commands_failed"], snap["commands_open"]) == (2, 1, 0)
    assert snap["file_changes"] == 2
    assert (snap["files_inside"], snap["files_outside"]) == (2, 1)  # 相对路径按工作区算
    assert snap["target_touched"] is True
    assert (snap["reasoning"], snap["messages"]) == (1, 1)
    assert (snap["errors"], snap["turns_failed"]) == (1, 1)
    assert (snap["last_event"], snap["last_item"]) == ("turn.failed", "agent_message")
    assert snap["first_event_ms"] == 2500
    assert snap["max_gap_ms"] == 30000
    assert snap["idle_end_ms"] == 7000
    assert snap["endpoint"] == "relay"
    assert ai_agents.CliRunFacts(str(cwd), "x.py").snapshot(False)["endpoint"] == "official"
    blob = json.dumps(snap)
    for canary in ("alice", "Alice", "Rscript", "HeLa", "Secret"):
        assert canary not in blob


def test_a_target_written_by_a_shell_command_is_seen_on_disk(tmp_path):
    """真模型复现（TVD-BCZ0）：模型把 `apply_patch` 当 shell 命令跑，事件流里只有 command_execution、
    没有 file_change——`target_touched` 看不见，结束时的 `target_on_disk` / `target_bytes` 要看得见。"""
    clock = Clock()
    cwd, facts = _session(tmp_path, clock)
    _feed(
        facts,
        clock,
        [
            (1.0, _item("item.started", type="command_execution", command="apply_patch <<EOF")),
            (
                2.0,
                _item("item.completed", type="command_execution", exit_code=0, status="completed"),
            ),
        ],
    )
    (cwd / "fig_matplotlib.py").write_text("import matplotlib\n", encoding="utf-8")
    snap = facts.snapshot(False)
    assert snap["file_changes"] == 0 and snap["target_touched"] is False
    assert snap["target_on_disk"] is True and snap["target_bytes"] == len("import matplotlib\n")


def test_target_state_ignores_symlinks_and_missing_files(tmp_path):
    clock = Clock()
    cwd, facts = _session(tmp_path, clock)
    assert facts.snapshot(False)["target_on_disk"] is False
    real = tmp_path / "elsewhere.py"
    real.write_text("x", encoding="utf-8")
    (cwd / "fig_matplotlib.py").symlink_to(real)
    snap = facts.snapshot(False)
    assert snap["target_on_disk"] is False and snap["target_bytes"] is None


def test_hung_command_and_aliases_and_declined_are_structural(tmp_path):
    clock = Clock()
    cwd, facts = _session(tmp_path, clock)
    _feed(
        facts,
        clock,
        [
            (1.0, _item("item.started", type="local_shell_call", command="sleep 9999")),
            (
                1.0,
                _item("item.completed", type="local_shell_call", command="rm", status="declined"),
            ),
            (1.0, _item("item.started", type="command_execution", command="Rscript fig.R")),
            (1.0, _item("item.completed", type="patch_apply", status="failed", changes={})),
            (1.0, _item("item.started", type="brand_new_item_kind")),
        ],
    )
    snap = facts.snapshot(False)
    assert snap["commands"] == 1 and snap["commands_declined"] == 1 and snap["commands_failed"] == 0
    assert snap["commands_open"] == 1  # 起了没结束 = 疑似卡住的命令
    assert snap["file_changes"] == 1 and snap["file_changes_failed"] == 1
    assert snap["last_item"] == "other"  # 集合外的 item 类型归 other，不带原词


def test_no_events_and_garbage_never_raise(tmp_path):
    clock = Clock()
    _, facts = _session(tmp_path, clock)
    for line in ("", "not json", "[1,2]", "null", '{"type": ["x"], "item": 5}', '{"item": null}'):
        facts.observe(line)
    clock.t += 900.0
    snap = facts.snapshot(False)
    assert snap["events"] == 2  # 后两行是 JSON 对象（类型坏了也算一个事件，但归 other）
    assert snap["last_event"] == "other"
    empty = ai_agents.CliRunFacts(str(tmp_path), "x.py", clock=clock)
    clock.t += 900.0
    s = empty.snapshot(False)
    assert s["events"] == 0 and s["last_event"] == "none" and s["first_event_ms"] is None
    assert s["idle_end_ms"] == 900000  # 一个事件都没有：整段时间都是空闲


def test_unknown_event_type_is_other_not_echoed(tmp_path):
    clock = Clock()
    _, facts = _session(tmp_path, clock)
    facts.observe(_ev(type="Alice.Smith.event"))
    assert facts.snapshot(False)["last_event"] == "other"


def test_merge_runs_sums_counts_and_keeps_last_state(tmp_path):
    clock = Clock()
    cwd, a = _session(tmp_path, clock)
    _feed(a, clock, [(1.0, _ev(type="thread.started")), (60.0, _ev(type="turn.completed"))])
    first = a.snapshot(False)
    clock.t += 5
    b = ai_agents.CliRunFacts(str(cwd), "fig_matplotlib.py", clock=clock)
    _feed(
        b,
        clock,
        [
            (3.0, _item("item.started", type="command_execution", command="x")),
            (
                1.0,
                _item(
                    "item.completed",
                    type="file_change",
                    changes=[{"path": str(cwd / "fig_matplotlib.py")}],
                ),
            ),
        ],
    )
    second = b.snapshot(True)
    merged = ai_agents.merge_run_facts([first, second])
    assert merged["events"] == 4
    assert merged["max_gap_ms"] == 60000
    assert merged["first_event_ms"] == first["first_event_ms"]
    assert merged["target_touched"] is True and merged["commands_open"] == 1
    assert merged["last_event"] == "item.completed" and merged["endpoint"] == "relay"
    assert ai_agents.merge_run_facts([]) is None


def test_only_codex_accumulates():
    assert ai_agents.get_agent("codex").run_facts("/w", "t.py") is not None
    assert ai_agents.get_agent("claude").run_facts("/w", "t.py") is None


def test_relay_means_a_base_url_was_configured():
    assert ai_bridge._is_relay({"base_url": " https://relay.example/v1 "}) is True
    assert ai_bridge._is_relay({"base_url": " "}) is False
    assert ai_bridge._is_relay({}) is False
    assert ai_bridge._is_relay(None) is False


# ------------------------------------------------------------------ exec trace span


def test_agent_run_span_carries_the_facts_and_rejects_nothing(tmp_path):
    clock = Clock()
    _, facts = _session(tmp_path, clock)
    _feed(facts, clock, [(1.0, _item("item.started", type="command_execution", command="x"))])
    snap = facts.snapshot(True)
    attrs = ai_bridge._cli_fact_attributes(snap)
    kept, rejected = exectrace.project_attributes(attrs)
    assert rejected == 0 and kept == attrs
    assert kept["cli_last_event"] == "item.started" and kept["cli_last_item"] == "command_execution"
    assert kept["endpoint_kind"] == "relay" and kept["cli_commands_open"] == 1
    assert "cli_first_event_ms" in kept
    # 没收到过事件：first_event_ms 是 None → 不写这一键，其余照写
    nothing = ai_bridge._cli_fact_attributes(
        ai_agents.CliRunFacts(str(tmp_path), "x.py", clock=clock).snapshot(False)
    )
    assert "cli_first_event_ms" not in nothing and nothing["cli_last_event"] == "none"
    assert exectrace.project_attributes(nothing)[1] == 0
    # 集合外的词仍被词表拒绝
    assert exectrace.project_attributes({"cli_last_event": "Alice.Smith"}) == ({}, 1)
    assert exectrace.project_attributes({"endpoint_kind": "https://relay.example"}) == ({}, 1)


def test_every_snapshot_key_maps_to_a_registered_attribute(tmp_path):
    snap = ai_agents.CliRunFacts(str(tmp_path), "x.py", clock=Clock()).snapshot(False)
    assert set(snap) == set(ai_bridge._CLI_FACT_ATTRS)
    for attr in ai_bridge._CLI_FACT_ATTRS.values():
        assert attr in exectrace.ATTRIBUTES


def test_span_end_writes_facts_for_each_ending(tmp_path):
    clock = Clock()
    _, facts = _session(tmp_path, clock)
    snap = facts.snapshot(False)
    for status, rc in (("done", 0), ("failed", 1), ("cancelled", None), ("timeout", None)):
        ended = {}

        class T:
            def end_span(self, span, st, **kw):
                ended.update(status=st, **kw)

        sess = {"status": status}
        ai_bridge._trace_agent_exit(T(), "sp", sess, SimpleNamespace(returncode=rc), snap)
        assert ended["attributes"]["cli_events"] == 0, status
        assert ended["attributes"]["endpoint_kind"] == "official", status


# ------------------------------------------------------------------ 诊断包日志


def _end_template() -> str:
    tree = ast.parse((ENGINE / "ai_bridge.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith("AI 会话结束")
        ):
            return node.args[0].value
    raise AssertionError("找不到「AI 会话结束」日志调用")


def _exported(sess: dict, params=("codex", "x.py", "done", False, "none")) -> str:
    tail = (*ai_bridge._end_facts(sess, SimpleNamespace(returncode=0), "nosid", False),)
    args = (*params, *tail, *ai_bridge._cli_log_facts(sess))
    template = _end_template()
    assert template.count("%s") == len(args), "模板占位符与参数个数对不上"
    rec = logging.LogRecord("tavotto", logging.INFO, __file__, 1, template, args, None)
    return diagnostics.ExportLogFormatter().format(rec)


def test_bundle_log_shows_codes_and_numbers_in_clear(tmp_path):
    clock = Clock()
    cwd, facts = _session(tmp_path, clock)
    _feed(
        facts,
        clock,
        [
            (1.0, _item("item.started", type="command_execution", command=CANARY_CMD)),
            (
                90.0,
                _item(
                    "item.completed",
                    type="file_change",
                    changes=[{"path": CANARY_PATH}],
                    text=CANARY_TEXT,
                ),
            ),
        ],
    )
    clock.t += 600
    sess = {"status": "timeout", "cli_runs": [facts.snapshot(True)]}
    out = _exported(sess)
    assert "cli_events=2 cli_cmds=0 cli_cmds_failed=0 cli_cmds_declined=0 cli_cmds_open=1" in out
    assert (
        "cli_file_changes=1 cli_files_in=0 cli_files_out=1 cli_target_touched=False cli_target_on_disk=False cli_target_bytes=None"
        in out
    )
    assert "cli_last_event=item.completed cli_last_item=file_change" in out
    assert (
        "cli_first_event_ms=1000 cli_max_gap_ms=90000 cli_idle_end_ms=600000 endpoint=relay" in out
    )
    for canary in ("alice", "Alice", "Rscript", "Secret", "HeLa", "Boom"):
        assert canary not in out


def test_bundle_log_without_facts_is_placeholders():
    out = _exported({"status": "done"})
    assert "cli_events=None" in out and "cli_last_event=- cli_last_item=-" in out
    assert "endpoint=-" in out


def test_free_text_in_a_code_slot_is_hashed(tmp_path):
    """防后门：即便将来有人把自由文本塞进事件类别 / 接口类别槽位，诊断包里也是哈希。"""
    sess = {
        "status": "done",
        "cli_runs": [
            {
                "events": 1,
                "last_event": CANARY_TEXT,
                "last_item": "Boom failed in fig_Secret.R",  # 绝对路径另有括起来出包的通道，不是本测试的对象
                "endpoint": "Rscript fig_Boom.R",
                "idle_end_ms": 5,
                "max_gap_ms": 0,
                "first_event_ms": 1,
            }
        ],
    }
    out = _exported(sess)
    assert "Alice" not in out and "alice" not in out and "Rscript" not in out
    assert "cli_last_event=str:" in out and "cli_last_item=str:" in out and "endpoint=str:" in out
    assert CANARY_TEXT in str(ai_bridge._cli_log_facts(sess)[13]), "对照：本机完整日志里原值照写"


def test_log_formatter_renders_plain_closed_codes_locally_too(tmp_path):
    sess = {
        "status": "done",
        "cli_runs": [ai_agents.CliRunFacts(str(tmp_path), "x.py").snapshot(False)],
    }
    local = [str(v) for v in ai_bridge._cli_log_facts(sess)]
    assert local[13:15] == ["none", "none"] and local[18] == "official"


# ------------------------------------------------------------------ 防漂移


def _tree(rel: str) -> ast.AST:
    return ast.parse((ENGINE / rel).read_text(encoding="utf-8"))


def test_snapshot_and_classifier_literals_are_in_the_closed_sets():
    """`CliRunFacts` 写进 `last_event` / `last_item` / `endpoint` 的字面量都在闭集里；
    被它读成「已知类型」的集合就是闭集自己减去 none / other（改集合、不改解析 = 这条红）。"""
    src = (ENGINE / "ai_agents.py").read_text(encoding="utf-8")
    cls = next(
        n
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.ClassDef) and n.name == "CliRunFacts"
    )
    written = set()
    for node in ast.walk(cls):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Attribute) and t.attr in ("last_event", "last_item"):
                    if isinstance(node.value, ast.Constant):
                        written.add((t.attr, node.value.value))
        if isinstance(node, ast.IfExp) and isinstance(node.body, ast.Constant):
            if node.body.value in ("relay",):
                assert {node.body.value, node.orelse.value} <= ai_agents.ENDPOINT_KINDS
    assert ("last_event", "none") in written and ("last_item", "none") in written
    assert {"none", "other"} <= ai_agents.CLI_EVENT_TYPES & ai_agents.CLI_ITEM_TYPES
    # 解析里比较的 item 字面量 / 别名目标都在 item 闭集里
    assert set(ai_agents._ITEM_ALIASES.values()) <= ai_agents.CLI_ITEM_TYPES
    compared = {
        n.value
        for n in ast.walk(cls)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and n.value in (ai_agents.CLI_EVENT_TYPES | ai_agents.CLI_ITEM_TYPES)
    }
    assert {
        "item.started",
        "item.completed",
        "error",
        "turn.failed",
        "command_execution",
    } <= compared
    for lit in (n.value for n in ast.walk(cls) if isinstance(n, ast.Constant)):
        if isinstance(lit, str) and lit.startswith("item."):
            assert lit in ai_agents.CLI_EVENT_TYPES


def test_every_observed_event_kind_is_registered_in_the_vocabularies():
    for word in ai_agents.CLI_EVENT_TYPES:
        assert word in exectrace.vocabulary("cli_event")
    for word in ai_agents.CLI_ITEM_TYPES:
        assert word in exectrace.vocabulary("cli_item")
    assert ai_agents.ENDPOINT_KINDS <= exectrace.vocabulary("endpoint_kind")
    assert os.sep  # 避免 os 导入被误删（路径分隔符参与工作区判定）
