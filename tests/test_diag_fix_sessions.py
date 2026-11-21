"""诊断包里的「AI 修复经过（内容）」（ADR 0197 第 4 条，维护者 11-19 裁决）。

* 不论勾没勾「脚本源码」都收：试运行记录、提示、模型、AI 的总结、工具返回尾部（含读过的脚本片段与报错源码行）、启动脚本。
* 一律过 `Scrubber`：密钥换掉、家目录换成 `~`。
* 模型只带厂商 id 与模型名，不带地址。
* 工具返回只在内存里、有界（每条尾部、总条数）；审计照旧不落内容。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用
from __future__ import annotations

import json
import sys
from pathlib import Path

from tavotto.engine import diagdetail, fixsession, launchscript
from tavotto.engine.fixtools import (
    REGISTRY,
    TRANSCRIPT_ENTRY_CHARS,
    TRANSCRIPT_MAX_ENTRIES,
    run_tool,
)
from test_fixtools import box, make_ctx, put  # noqa: F401

CANARY = "FIX_SESSION_SOURCE_CANARY"


def _runs(fix_sessions, *, source: bool) -> dict:
    src = diagdetail.Sources(fix_sessions=fix_sessions)
    items = diagdetail.collect_runs(
        src, diagdetail.DetailOptions(source=source), diagdetail.Scrubber()
    )
    return json.loads(items[0].data)


def _session(home: Path) -> dict:
    return {
        "fix_id": "fx1",
        "script": "fig.py",
        "state": "finished",
        "outcome": "fixed",
        "summary": "Set the data argument.",
        "model": {"provider": "openai-api", "model": "gpt-6-luna"},
        "attempts": [
            {
                "n": 1,
                "before": "script_needs_arguments",
                "after": "ok",
                "hint": "args",
                "changes": [],
            }
        ],
        "transcript": [
            {
                "step": 3,
                "tool": "read_file",
                "code": "",
                "text": f'Traceback (most recent call last):\n  File "{home}/proj/fig.py", line 2, in <module>\n'
                f"    x = {CANARY}\nNameError: boom\napi_key=sk-abcdefghijklmnopqrstuvwx",
            }
        ],
        "launcher": f"import os\nos.environ.setdefault('LAB', '{home}/data')\nrun_original()\n",
    }


def test_fix_sessions_are_in_the_pack_even_without_source_and_are_scrubbed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    doc = _runs([_session(home)], source=False)
    s = doc["fix_sessions"][0]
    text = json.dumps(s)
    assert CANARY in text  # 维护者裁决：不勾也收 AI 读过的脚本片段（发送框写明）
    assert s["attempts"][0]["hint"] == "args" and s["model"] == {
        "provider": "openai-api",
        "model": "gpt-6-luna",
    }
    assert "run_original()" in s["launcher"]
    assert "sk-abcdefghijklmnopqrstuvwx" not in text  # 密钥照样换掉
    assert str(home) not in text  # 家目录换成 ~


def test_the_rest_of_the_runs_file_still_strips_source_lines_when_unticked(tmp_path, monkeypatch):
    """例外只给 AI 修复经过：同一个文件里其余部分（修复经过的报错、日志记录）不勾照旧剥源码行。"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    src = diagdetail.Sources(
        fix_journey=[
            {
                "error": f'Traceback (most recent call last):\n  File "x.py", line 1, in <module>\n    {CANARY}\nNameError: x'
            }
        ],
        fix_sessions=[],
    )
    items = diagdetail.collect_runs(
        src, diagdetail.DetailOptions(source=False), diagdetail.Scrubber()
    )
    assert CANARY not in items[0].data.decode()


def test_the_dialog_says_ai_repair_parts_are_included():
    for lang, phrase in (
        ("zh-CN", "AI 修复时还会带上它读过的脚本片段"),
        ("en-US", "the parts of your script it read"),
    ):
        path = (
            Path(__file__).resolve().parents[1]
            / "web"
            / "src"
            / "i18n"
            / "locales"
            / lang
            / "dialogs.json"
        )
        assert phrase in path.read_text(encoding="utf-8"), lang


# ---------------------------------------------------------------- 会话这一侧
def test_tool_results_are_remembered_scrubbed_and_bounded(box):
    ctx = make_ctx(box, environments={"e": sys.executable})
    put(
        box.proj / "big.txt",
        "x" * (TRANSCRIPT_ENTRY_CHARS * 3) + "\napi_key=sk-abcdefghijklmnopqrstuvwx\n",
    )
    for _ in range(TRANSCRIPT_MAX_ENTRIES + 5):
        run_tool(REGISTRY["read_file"], ctx, {"path": "big.txt"})
    assert len(ctx.transcript) == TRANSCRIPT_MAX_ENTRIES
    last = ctx.transcript[-1]
    assert last["tool"] == "read_file" and len(last["text"]) <= TRANSCRIPT_ENTRY_CHARS + 1
    assert "sk-abcdefghijklmnopqrstuvwx" not in json.dumps(ctx.transcript)


def test_sessions_export_model_without_address_and_the_launcher(box):
    put(box.proj / "fig.py", "print(1)\n")
    launchscript.write(box.proj, "fig.py", "run_original()\n")
    sessions = fixsession.FixSessions()
    s = fixsession.FixSession(id="fx", scope="proj-1", script="fig.py", prep_session_id="p")
    s.endpoint = ("openai-api", "gpt-6-luna", "https://private.example/v1")
    s.ctx = make_ctx(box)
    s.ctx.attempts.append({"n": 1, "before": "a", "after": "ok", "hint": "", "changes": []})
    sessions._by_id[s.id] = s
    out = sessions.diagnostics_for("proj-1")
    assert out[0]["model"] == {"provider": "openai-api", "model": "gpt-6-luna"}
    assert "private.example" not in json.dumps(out)
    assert out[0]["launcher"] == "run_original()\n" and out[0]["attempts"][0]["after"] == "ok"
    assert sessions.diagnostics_for("another-project") == []
