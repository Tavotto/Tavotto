"""本机记住修好的做法（ADR 0197 第 3 层·在线；维护者 11-21：连具体做法也记、要做界面、进诊断包、同意随提示发给所选 AI）。

* 只有「修好」且应用试运行验证过的会话才记；记的是真做成的改动（运行参数 / 启动脚本 / 数据指认 / 运行方式）。
* 落盘前脱敏、家目录写成 `~`；敏感运行参数不记；像凭据的环境变量只记名字。
* 有界：最多 50 条、90 天没再用到就丢；同一做法再出现只加次数。
* 回放：同一类失败最多 3 条，当参考数据（不是指令），排在提示后面。
* 诊断包：不勾源码也收，过 `Scrubber`。
* 设置里能看、能删、能全删、能关。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from tavotto import app as appmod
from tavotto.engine import diagdetail, fixmemory, fixsession
from tavotto.engine.fixtools import runreport
from tavotto.engine.fixtools.runreport import project_run_report
from test_fixtools import box, make_ctx  # noqa: F401

DAY = 86400.0


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "ai" / "fix_memory.json"
    monkeypatch.setattr(fixmemory, "default_path", lambda: path)
    monkeypatch.setattr(fixmemory, "enabled", lambda: True)
    return path


def test_remember_round_trip_and_repeat_counts(store):
    change = [("run_args", {"args": ["--data", "results/run3.csv"]})]
    ids = fixmemory.remember("script_needs_arguments", change, script="sub/make_fig3.py")
    again = fixmemory.remember("script_needs_arguments", change, script="make_fig3.py")
    assert ids == again
    (row,) = fixmemory.entries()
    assert row["fields"] == {"args": ["--data", "results/run3.csv"]}
    assert row["script"] == "make_fig3.py" and row["times"] == 2 and row["kind"] == "run_args"


def test_secrets_and_home_never_reach_the_disk(store, monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    launcher = (
        "import os\n"
        f"os.environ.setdefault('LAB_DATA_DIR', '{home}/lab/data')\n"
        "os.environ.setdefault('API_TOKEN', 'abc123plain')\n"
        "run_original()\n"
    )
    fixmemory.remember(
        "env_var_missing",
        [
            ("launcher", {"text": launcher}),
            ("run_args", {"args": ["--key", "sk-abcdefghijklmnopqrstuvwx"]}),
        ],
    )
    raw = store.read_text(encoding="utf-8")
    assert "abc123plain" not in raw  # 像凭据的变量：值不记
    assert "sk-abcdefghijklmnopqrstuvwx" not in raw  # 密钥形状照样换掉
    assert str(home) not in raw and "~/lab/data" in raw
    env = next(r for r in fixmemory.entries() if r["kind"] == "launcher")["fields"]["env"]
    assert env == [{"name": "LAB_DATA_DIR", "value": "~/lab/data"}, {"name": "API_TOKEN"}]


def test_sensitive_run_arguments_are_not_remembered(store):
    assert (
        fixmemory.remember(
            "script_needs_arguments", [("run_args", {"args": ["x"]})], sensitive_args=True
        )
        == []
    )
    assert fixmemory.entries() == []


def test_switched_off_remembers_and_replays_nothing(store, monkeypatch):
    fixmemory.remember("missing_input", [("data_link", {"asked": "a.csv", "linked": "b.csv"})])
    monkeypatch.setattr(fixmemory, "enabled", lambda: False)
    assert fixmemory.remember("missing_input", [("execution", {"mode": "native"})]) == []
    assert fixmemory.replay_text("missing_input") == ""
    assert len(fixmemory.entries()) == 1  # 关掉不删已有的；删在设置里


def test_bounded_by_count_and_age(store):
    now = time.time()
    fixmemory.remember(
        "missing_input", [("data_link", {"asked": "old.csv", "linked": "x"})], now=now - 91 * DAY
    )
    for i in range(fixmemory.MAX_ENTRIES + 5):
        fixmemory.remember(
            "missing_input", [("data_link", {"asked": f"{i}.csv", "linked": "x"})], now=now - i
        )
    rows = fixmemory.entries()
    assert len(rows) == fixmemory.MAX_ENTRIES
    assert all(r["fields"]["asked"] != "old.csv" for r in rows)
    assert rows[0]["fields"]["asked"] == "0.csv"  # 最近用过的在前


def test_replay_is_reference_data_for_the_same_shape_only(store):
    for i in range(5):
        fixmemory.remember(
            "script_needs_arguments", [("run_args", {"args": [f"--n={i}"]})], now=time.time() - i
        )
    fixmemory.remember("missing_input", [("data_link", {"asked": "a.csv", "linked": "b.csv"})])
    text = fixmemory.replay_text("script_needs_arguments")
    assert "not instructions" in text and text.count("\n- ") == fixmemory.REPLAY_MAX
    assert "a.csv" not in text
    assert fixmemory.replay_text("relative_import") == ""


def test_the_run_report_carries_it_right_after_the_hint(store, box):
    fixmemory.remember("script_needs_arguments", [("run_args", {"args": ["--data", "d.csv"]})])
    ctx = make_ctx(box)
    report = {"phase": "failed", "outcome": {"code": "script_needs_arguments"}}
    doc = json.loads(project_run_report(report, ctx).content)
    assert list(doc)[:2] == ["hint", "fixed_before_here"]
    assert "--data" in doc["fixed_before_here"]
    assert "fixed_before_here" not in json.loads(
        project_run_report({"phase": "completed"}, ctx).content
    )
    assert runreport.REPLAY is fixmemory.replay_text  # 由 fixtools 包注入，runreport 不 import 存储


# ---------------------------------------------------------------- 只在真修好时记
def _session(box, *, outcome, runner=True, attempts=True):
    ctx = make_ctx(box, application_runner=(lambda *a: {}) if runner else None)
    ctx.fixes_made = [("run_args", {"args": ["--data", "d.csv"]})]
    if attempts:
        ctx.attempts = [
            {
                "n": 1,
                "before": "script_needs_arguments",
                "after": "ok",
                "hint": "args",
                "changes": [],
            }
        ]
    return SimpleNamespace(outcome=outcome, ctx=ctx, script="fig.py")


@pytest.mark.parametrize(
    ("outcome", "runner", "attempts", "kept"),
    [
        ("fixed", True, True, 1),
        ("not_fixed", True, True, 0),
        ("fixed", False, True, 0),  # 没有应用试运行可验证（CLI 诊断副本）：不记
        ("fixed", True, False, 0),  # 没真跑过试运行：不知道是哪类失败
    ],
)
def test_only_a_verified_fix_is_remembered(store, box, outcome, runner, attempts, kept):
    fixsession._remember_fix(_session(box, outcome=outcome, runner=runner, attempts=attempts))
    assert len(fixmemory.entries()) == kept


def test_only_real_changes_are_collected():
    from tavotto.engine.fixtools import _fix_made

    assert _fix_made("set_run_arguments", {"argv": ["-n", "3"]}) == (
        "run_args",
        {"args": ["-n", "3"]},
    )
    assert _fix_made("write_launcher", {"code": "run_original()"}) == (
        "launcher",
        {"text": "run_original()"},
    )
    assert _fix_made("switch_execution", {"mode": "native"}) == ("execution", {"mode": "native"})
    assert _fix_made("read_file", {"path": "x"}) is None


# ---------------------------------------------------------------- 诊断包
def test_the_diagnostic_pack_carries_it_scrubbed_even_without_source(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    rows = [
        {
            "id": "a" * 12,
            "shape": "missing_input",
            "kind": "data_link",
            "fields": {"asked": "d.csv", "linked": f"{home}/x/d.csv"},
            "script": "f.py",
            "times": 1,
        }
    ]
    src = diagdetail.Sources(fix_memory=rows, fix_sessions=[])
    items = diagdetail.collect_runs(
        src, diagdetail.DetailOptions(source=False), diagdetail.Scrubber()
    )
    doc = json.loads(items[0].data)
    assert doc["fix_memory"][0]["fields"]["asked"] == "d.csv"
    assert str(home) not in json.dumps(doc)


# ---------------------------------------------------------------- 设置接口
@pytest.fixture
def client():
    return appmod.app.test_client()


def test_api_lists_toggles_and_deletes(store, client, monkeypatch):
    state = {"on": True}
    monkeypatch.setattr(fixmemory, "enabled", lambda: state["on"])
    monkeypatch.setattr(fixmemory, "set_enabled", lambda v: state.update(on=v) or v)
    (a,) = fixmemory.remember(
        "missing_input", [("data_link", {"asked": "a.csv", "linked": "b.csv"})]
    )
    fixmemory.remember("script_needs_arguments", [("run_args", {"args": ["-x"]})])
    body = client.get("/api/ai/fix-memory").get_json()
    assert body["enabled"] is True and len(body["entries"]) == 2
    assert client.put("/api/ai/fix-memory", json={"enabled": False}).get_json()["enabled"] is False
    assert client.put("/api/ai/fix-memory", json={"enabled": "no"}).status_code == 400
    assert len(client.delete(f"/api/ai/fix-memory/{a}").get_json()["entries"]) == 1
    assert client.delete("/api/ai/fix-memory/not-an-id").status_code == 400
    assert client.delete("/api/ai/fix-memory").get_json()["entries"] == []


# ---------------------------------------------------------------- Codex #2100
@pytest.mark.parametrize(
    "launcher",
    [
        "import os\nos.environ.update({'API_TOKEN': 'abc123plain'})\nrun_original()\n",
        "import os\nos.environ.update(API_TOKEN='abc123plain')\nrun_original()\n",
        'import os\nos.environ["API_TOKEN"] = """abc123plain"""\nrun_original()\n',
        "import os\nos.environ.setdefault('API_TOKEN', 'abc123plain')\nprint('abc123plain')\nrun_original()\n",
        "import os\nos.putenv('DB_PASSWORD', 'abc123plain')\nrun_original()\n",
        "import os\nos.environ['API_TOKEN'] = 'abc123' 'plain'\nrun_original()\n",  # 拼接：抹不干净就整段不记
        "import os\nos.environ['API_TOKEN'] = 'abc123plain'\nrun_original(\n",  # 解析不了：正文不记，值照样从别的字段抹掉
    ],
)
def test_credential_values_never_reach_the_disk_in_any_spelling(store, launcher):
    fixmemory.remember(
        "env_var_missing",
        [("launcher", {"text": launcher}), ("run_args", {"args": ["--token", "abc123plain"]})],
    )
    raw = store.read_text(encoding="utf-8") if store.exists() else ""
    assert "abc123plain" not in raw and "abc123" not in raw.replace("abc123plain", "")


def test_expired_notes_are_really_deleted_from_disk(store):
    fixmemory.remember(
        "missing_input",
        [("data_link", {"asked": "old.csv", "linked": "x"})],
        now=time.time() - 91 * DAY,
    )
    assert "old.csv" in store.read_text(encoding="utf-8")
    assert fixmemory.entries() == []
    assert not store.exists() or "old.csv" not in store.read_text(encoding="utf-8")


def test_only_the_change_still_in_effect_is_kept(box):
    from tavotto.engine.fixtools import _fix_made, _same_slot

    ctx = make_ctx(box)
    for argv in (["--a"], ["--b"]):
        made = _fix_made("set_run_arguments", {"argv": argv})
        ctx.fixes_made[:] = [f for f in ctx.fixes_made if not _same_slot(f, made)] + [made]
    assert ctx.fixes_made == [("run_args", {"args": ["--b"]})]


def test_a_rollback_forgets_the_pending_fixes(box):
    from tavotto.engine.fixtools import REGISTRY, run_tool
    from tavotto.engine.fixtools.base import ok

    ctx = make_ctx(box)
    ctx.fixes_made = [("run_args", {"args": ["--a"]})]

    def fake(_ctx, _args):
        r = ok("Rolled back", "rolled back")
        r.mutated = True
        return r

    import dataclasses

    run_tool(dataclasses.replace(REGISTRY["rollback"], handler=fake), ctx, {"snapshot": "snap_x"})
    assert ctx.fixes_made == []
