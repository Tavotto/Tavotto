"""「AI 会话结束」日志行带出失败事实（诊断包 TVD-BCZ0）：内置执行器 0.1 秒失败却没有任何原因。

事实逐项作为**独立的日志参数**，经 `logsafe.known*` 对源码里的闭集常量放行：诊断包读的是
`ExportLogFormatter` 写的 `diagnostics.log`，字符串参数默认被哈希，所以判据的主语是**格式化后的诊断输出**
（Codex P2）：已知码明文可见，落进同一槽位的自由文本金丝雀被哈希、不出现。
另用 AST 看护产出点写出的码字面量都在对应闭集里（防漂移）。
"""

import ast
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto.engine import ai_agents, ai_bridge, ai_tasks, diagnostics, exectrace
from tavotto.engine.agent_runner import loop as agent_loop

ENGINE = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine"
TEMPLATE = (
    "AI 会话结束: %s %s status=%s changed=%s refresh=%s exit=%s runner=%s runner_detail=%s "
    "unmet=%s unmet_reasons=%s stderr_bytes=%s stderr_codes=%s"
)
# 自由文本：大写 / 空格 / 像脚本名的片段（绝对路径另有一条括起来出包的通道，不是本测试的对象）
CANARY = "Alice Smith: Boom failed in fig_Secret.R"


def _proc(code):
    return SimpleNamespace(returncode=code)


def _exported(facts: tuple) -> str:
    """走真正的诊断包格式器（`cache/diagnostics.log` 那份），不是 app.log。"""
    rec = logging.LogRecord(
        "tavotto",
        logging.INFO,
        __file__,
        1,
        TEMPLATE,
        ("builtin", "x.py", "failed", False, "none", *facts),
        None,
    )
    return diagnostics.ExportLogFormatter().format(rec)


def _plain(facts: tuple) -> str:
    """本机完整日志（app.log）的写法：`%s` 直接格式化，Plain 就是原值。"""
    return TEMPLATE % ("builtin", "x.py", "failed", False, "none", *facts)


def test_builtin_done_reason_and_detail_are_plain_in_the_bundle():
    sess = {"status": "failed", "runner_error": "agent_internal", "runner_detail": "bad_target"}
    facts = ai_bridge._end_facts(sess, _proc(1), "nosuchsid", True)
    out = _exported(facts)
    assert "exit=1 runner=agent_internal runner_detail=bad_target" in out
    assert "exit=1 runner=agent_internal runner_detail=bad_target" in _plain(facts)


def test_free_text_in_a_fact_slot_is_hashed_in_the_bundle_but_kept_locally():
    sess = {"status": "failed", "runner_error": CANARY, "runner_detail": CANARY}
    facts = ai_bridge._end_facts(sess, _proc(1), "nosuchsid", True)
    out = _exported(facts)
    assert "Alice" not in out and "fig_Secret" not in out and "Boom" not in out
    assert "runner=str:" in out and "runner_detail=str:" in out
    assert CANARY in _plain(facts), "对照：本机完整日志里原值照写"


def test_builtin_exit_without_done_event_is_marked(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path)
    (tmp_path / "s1.stderr.log").write_bytes(
        b'Traceback (most recent call last):\n  File "C:\\Users\\alice\\x.py"\nImportError\n'
    )
    facts = ai_bridge._end_facts({"status": "failed"}, _proc(3), "s1", True)
    out = _exported(facts)
    assert "exit=3 runner=no_done_event runner_detail=-" in out
    assert "stderr_bytes=" in out and "stderr_codes=-" in out
    assert "alice" not in out and "Traceback" not in out


def test_runner_stderr_short_codes_are_plain_in_the_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path)
    (tmp_path / "s2.stderr.log").write_bytes(b"agent_runner: bad_prompt\n")
    out = _exported(ai_bridge._end_facts({"status": "failed"}, _proc(1), "s2", True))
    assert "stderr_codes=bad_prompt" in out
    # 解释器之外的词（不在闭集里）→ 整组哈希
    (tmp_path / "s2b.stderr.log").write_bytes(b"agent_runner: surprise_word\n")
    out = _exported(ai_bridge._end_facts({"status": "failed"}, _proc(1), "s2b", True))
    assert "surprise_word" not in out and "stderr_codes=str:" in out


def test_unmet_checks_are_plain_closed_codes_and_free_text_is_hashed():
    sess = {
        "status": "failed",
        "task": {
            "unmet": [
                {"constraint_id": "candidate.target", "reason_code": "target_missing"},
                {"constraint_id": "budget.max_wall_s", "reason_code": "timeout"},
            ]
        },
    }
    out = _exported(ai_bridge._end_facts(sess, _proc(0), "x", False))
    assert "unmet=candidate.target, budget.max_wall_s unmet_reasons=target_missing, timeout" in out
    sess["task"]["unmet"].append({"constraint_id": CANARY, "reason_code": "free text here"})
    out = _exported(ai_bridge._end_facts(sess, _proc(0), "x", False))
    assert "Alice" not in out and "free text" not in out and "candidate.target" not in out
    assert "unmet=str:" in out and "unmet_reasons=str:" in out


def test_successful_cli_session_has_only_the_exit_code():
    facts = ai_bridge._end_facts({"status": "done"}, _proc(0), "x", False)
    assert [str(f) for f in facts] == ["0", "-", "-", "-", "-", "None", "-"]
    assert "exit=0 runner=- runner_detail=- unmet=- unmet_reasons=- stderr_bytes=None" in _exported(
        facts
    )


def test_real_child_that_dies_is_described_without_its_stderr(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path)
    with (tmp_path / "s3.stderr.log").open("wb") as log:
        child = subprocess.run(
            [sys.executable, "-c", "import sys; sys.stderr.write('secret-path'); sys.exit(7)"],
            stderr=log,
            check=False,
            encoding="utf-8",
        )
    out = _exported(ai_bridge._end_facts({"status": "failed"}, child, "s3", True))
    assert "exit=7 runner=no_done_event" in out
    assert "secret-path" not in out


# ------------------------------------------------------------------ 防漂移：产出点的字面量都在闭集里


def _tree(rel: str) -> ast.AST:
    return ast.parse((ENGINE / rel).read_text(encoding="utf-8"))


def _s(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def test_runner_done_reasons_written_by_the_loop_are_in_the_set():
    """`loop.py` 里 `reason=` / `_finish(..., reason)` / `RunnerFailure(reason, ...)` 写出的字面量，
    以及 `BuiltinAgent._FAILURES` 的键，都在 `DONE_REASONS` 里；`RUNNER_ERRORS` 是它们的 `agent_` 映射。"""
    found = set()
    for node in ast.walk(_tree("agent_runner/loop.py")):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", "")
            for kw in node.keywords:
                if kw.arg == "reason" and _s(kw.value):
                    found.add(_s(kw.value))
            if name == "_finish" and len(node.args) > 2 and _s(node.args[2]):
                found.add(_s(node.args[2]))
            if name in ("RunnerFailure", "_Failure") and node.args and _s(node.args[0]):
                found.add(_s(node.args[0]))
    assert {"internal", "completed"} <= found, "对照：扫描确实看到了 reason 字面量"
    assert found <= agent_loop.DONE_REASONS, found - agent_loop.DONE_REASONS
    failures = set(ai_agents.BuiltinAgent._FAILURES)
    assert failures <= agent_loop.DONE_REASONS
    mapped = {"agent_" + r for r in failures} | {
        "agent_output_truncated",
        "agent_html_response",
        "no_done_event",
    }
    assert ai_agents.RUNNER_ERRORS == mapped


def test_runner_error_literals_in_ai_agents_and_bridge_are_in_the_set():
    found = set()
    for rel in ("ai_agents.py", "ai_bridge.py"):
        for node in ast.walk(_tree(rel)):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Subscript) and _s(t.slice) == "runner_error" for t in node.targets
            ):
                for c in ast.walk(node.value):
                    if _s(c) and c.value.startswith("agent_") and c.value != "agent_":
                        found.add(c.value)
    assert "agent_html_response" in found, "对照：扫描确实看到了产出点"
    assert found <= ai_agents.RUNNER_ERRORS, found - ai_agents.RUNNER_ERRORS


def test_runner_detail_vocabulary_is_a_subset_of_what_the_runner_emits():
    """`RUNNER_DETAILS` 是内置执行器 `detail` 的**登记子集**（`classify_event` 只保留登记过的，其余丢弃）：
    登记的每个词都得真在 agent_runner 里有产出点，否则是死词 / 拼写漂移。"""
    found = set()
    for path in sorted((ENGINE / "agent_runner").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.add(node.value)
    # `http_<状态码>` 由 f-string 拼出，字面量里看不到
    missing = {d for d in exectrace.RUNNER_DETAILS if not d.startswith("http_")} - found
    assert not missing, missing


def test_runner_stderr_short_codes_are_in_the_set():
    """`runner_cli.diag("agent_runner: <词> …")` 的第一个词都在 `STDERR_CODES`。"""
    found = set()
    for node in ast.walk(_tree("agent_runner/runner_cli.py")):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "diag" and node.args:
            arg = node.args[0]
            text = _s(arg)
            if text is None and isinstance(arg, ast.JoinedStr):
                text = "".join(v.value for v in arg.values if _s(v))
            if text and text.startswith("agent_runner: "):
                found.add(text.split()[1])
    assert {"bad_prompt", "internal"} <= found, "对照：扫描确实看到了 diag 短码"
    assert found <= agent_loop.STDERR_CODES, found - agent_loop.STDERR_CODES
    assert all(ai_bridge._RUNNER_STDERR_CODE.match(f"agent_runner: {c}") for c in found)


_CHECK_MODULES = (
    "ai_tasks.py",
    "ai_figure_edit.py",
    "ai_data_task.py",
    "ai_convert.py",
    "figureverify.py",
    "ai_bridge.py",
)


def test_unmet_constraint_ids_and_reason_codes_written_by_checks_are_in_the_sets():
    """检查适配器写出的 constraint id / reason_code 字面量（`_check` / `_render` / `CheckResult` / `unmet` 字典）。
    带用户元素名的动态 id（`data.<gid>`）不在集合里，诊断包里会被哈希——这是约定的降级。"""
    ids, reasons = set(), set()
    for rel in _CHECK_MODULES:
        for node in ast.walk(_tree(rel)):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", "")
                args = node.args
                if name in ("_check", "check"):
                    if args and _s(args[0]):
                        ids.add(_s(args[0]))
                    if len(args) > 4 and _s(args[4]):
                        reasons.add(_s(args[4]))
                if name == "_render" and len(args) > 2 and _s(args[2]):
                    reasons.add(_s(args[2]))
                for kw in node.keywords:
                    if kw.arg == "reason_code" and _s(kw.value):
                        reasons.add(_s(kw.value))
                    if (
                        kw.arg in ("id", "cid")
                        and _s(kw.value)
                        and name in ("CheckResult", "_check", "_render")
                    ):
                        ids.add(_s(kw.value))
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values, strict=True):
                    if _s(k) == "reason_code" and _s(v):
                        reasons.add(_s(v))
                    if (
                        _s(k) in ("constraint_id", "id")
                        and _s(v)
                        and rel in ("ai_tasks.py", "figureverify.py")
                    ):
                        ids.add(_s(v))
    assert {"candidate.target", "render.figure", "intent"} <= ids, "对照：扫描确实看到了 id 字面量"
    assert {"target_missing", "timeout", "not_correctable"} <= reasons, "对照：扫描确实看到了原因码"
    assert ids <= ai_tasks.UNMET_CONSTRAINT_IDS, ids - ai_tasks.UNMET_CONSTRAINT_IDS
    assert reasons <= ai_tasks.UNMET_REASON_CODES, reasons - ai_tasks.UNMET_REASON_CODES


def test_unmet_set_members_are_plain_looking_codes():
    """集合本身不夹带空格 / 路径 / 大写（只有 `L3.` 前缀这一个历史例外）。"""
    for code in (
        ai_tasks.UNMET_CONSTRAINT_IDS | ai_tasks.UNMET_REASON_CODES | exectrace.RUNNER_DETAILS
    ):
        assert code.replace("L3.", "").replace(".", "_").replace("_", "").isalnum(), code
        assert " " not in code and "/" not in code and "\\" not in code


@pytest.mark.parametrize("facts_slot", range(7))
def test_the_log_template_is_a_literal_with_one_slot_per_fact(facts_slot):
    """模板是固定字面量、事实槽位个数与 `_end_facts` 的返回一一对应。"""
    facts = ai_bridge._end_facts({"status": "done"}, _proc(0), "x", False)
    assert len(facts) == 7 and TEMPLATE.count("%s") == 5 + len(facts)
