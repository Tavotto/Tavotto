"""T07：参数 schema / 数据指认 / 输出写入影响进**同一个准备会话**的 checks / requirements / actions（假 pool）。

判据的主语：会话报告与动作是**后端**给的投影——表单只是建议（`blocking: False`，phase 仍 `ready_to_run`、`run`
动作照样在）；没有 argparse 证据的脚本报告形状与 T06 一字不差；读 schema 不执行脚本、不打开输出文件。
真 worker 穿过同一组端点的用例在 `test_script_args_e2e.py`。
"""

# ruff: noqa: F811 — 夹具（client / fake_pool / sessions）从既有用例导入复用，参数名与导入名相同
from __future__ import annotations

from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import inputremap, pool as engine_pool, preparation, prepsession, scriptargs
from test_preparation_api import _open, client, fake_pool  # noqa: F401
from test_preparation_session import _act, _action, _create, _get, _settle, sessions  # noqa: F401

ARGPARSE = """\
import argparse
open({sentinel!r}, "a").write("ran")
p = argparse.ArgumentParser()
p.add_argument("--freq", type=float, required=True, help="Hz")
p.add_argument("--out", type=argparse.FileType("w"), default={out!r})
p.add_argument("--mode", choices=["sin", "cos"], default="sin")
args = p.parse_args()
"""


def _proj(tmp_path: Path, text: str, name: str = "plot.py") -> Path:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    (root / name).write_text(text, encoding="utf-8")
    return root


def _argparse_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    sentinel = tmp_path / "ran.txt"
    out = tmp_path / "proj" / "result.txt"
    root = _proj(tmp_path, ARGPARSE.format(sentinel=str(sentinel), out=str(out)))
    out.write_bytes(b"keep me")
    return root, sentinel, out


def test_an_argparse_script_gets_an_advisory_form_not_a_gate(client, tmp_path, fake_pool, sessions):
    root, sentinel, out = _argparse_project(tmp_path)
    _open(client, root)
    before = sorted(p.name for p in root.iterdir())
    report = _create(client, {"script": "plot.py"}).get_json()
    # 必填 --freq 一个都没给：表单是建议——仍然 ready_to_run、run 动作在（真 parser 说缺参是 script_needs_arguments）
    assert report["phase"] == "ready_to_run", report
    by = {c["id"]: c for c in report["checks"]}
    assert by["arguments"] == {
        "id": "arguments",
        "status": "ok",
        "detail": {
            "schema": "complete",
            "arguments": 3,
            "required": 1,
            "output_files": 1,
            "form_enabled": True,
        },
    }
    (req,) = [r for r in report["requirements"] if r["kind"] == "script_arguments"]
    assert req["blocking"] is False and req["code"] == "script_arguments_available"
    schema = req["payload"]["schema"]
    assert [a["dest"] for a in schema["arguments"]] == ["freq", "out", "mode"]
    assert req["payload"]["argv_count"] == 0 and req["payload"]["run_config"] is None
    run = _action(report, "run")
    assert run["impact"]["script_writes"] == {"declared_output_arguments": 1, "cwd_mode": "sandbox"}
    # 读 schema 零执行：脚本顶层哨兵没出现、已有输出没被截断、项目里没多文件、pool 一次没调
    assert not sentinel.exists()
    assert out.read_bytes() == b"keep me"
    assert sorted(p.name for p in root.iterdir()) == before
    assert fake_pool["build_calls"] == 0


def test_with_arguments_the_requirement_names_the_configuration_not_the_values(
    client, tmp_path, fake_pool, sessions
):
    root, _sentinel, _out = _argparse_project(tmp_path)
    _open(client, root)
    report = _create(
        client, {"script": "plot.py", "argv": ["--freq", "SECRET-3.5", "--mode", "cos"]}
    ).get_json()
    (req,) = [r for r in report["requirements"] if r["kind"] == "script_arguments"]
    assert req["payload"]["argv_count"] == 4
    assert req["payload"]["run_config"] == report["target"]["run_config"]
    assert "SECRET-3.5" not in str(report)


def test_a_script_without_argparse_keeps_the_t06_report_shape(
    client, tmp_path, fake_pool, sessions
):
    root = _proj(tmp_path, "import sys\nprint(sys.argv)\n")
    _open(client, root)
    report = _create(client, {"script": "plot.py"}).get_json()
    assert [c["id"] for c in report["checks"]] == [
        "target",
        "environment",
        "workdir",
        "dependencies",
        "data",
    ]
    assert report["requirements"] == []
    assert "script_writes" not in _action(report, "run")["impact"]


def test_a_partial_schema_is_labelled_and_still_runnable(client, tmp_path, fake_pool, sessions):
    root = _proj(
        tmp_path,
        "import argparse\np = argparse.ArgumentParser()\n"
        "for n in ('a', 'b'):\n    p.add_argument('--' + n, required=True)\n"
        "p.add_argument('--known')\nargs = p.parse_args()\n",
    )
    _open(client, root)
    report = _create(client, {"script": "plot.py"}).get_json()
    by = {c["id"]: c for c in report["checks"]}
    assert by["arguments"]["detail"]["schema"] == "partial"
    (req,) = [r for r in report["requirements"] if r["kind"] == "script_arguments"]
    assert req["payload"]["schema"]["reasons"] == ["dynamic_add_argument"]
    assert report["phase"] == "ready_to_run" and _action(report, "run")


def test_the_schema_endpoint_reads_only_and_guards_the_path(client, tmp_path, fake_pool):
    root, sentinel, out = _argparse_project(tmp_path)
    (tmp_path / "outside.py").write_text("import argparse\n", encoding="utf-8")
    _open(client, root)
    resp = client.get("/api/engine/script-arguments", query_string={"script": "plot.py"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["script"] == "plot.py" and body["arguments"]["status"] == "complete"
    assert not sentinel.exists() and out.read_bytes() == b"keep me"
    outside = client.get("/api/engine/script-arguments", query_string={"script": "../outside.py"})
    assert outside.status_code == 400
    assert outside.get_json()["code"] == "script_path_outside_project"
    missing = client.get("/api/engine/script-arguments", query_string={"script": "nope.py"})
    assert missing.status_code == 404 and missing.get_json()["code"] == "script_not_found"
    assert fake_pool["build_calls"] == 0


def test_a_missing_input_failure_becomes_a_data_requirement_answered_by_the_remap_endpoint(
    client, tmp_path, fake_pool, sessions
):
    """数据找不到（ADR 0106）：载荷进会话的 requirements（不进计划 / 回执 / 结果投影）；用户指认之后 recheck 换修订，
    旧失败不再是当前的。"""
    root = _proj(tmp_path, "import pandas as pd\npd.read_csv('data/raw.csv')\n")
    (root / "elsewhere").mkdir()
    (root / "elsewhere" / "raw.csv").write_text("a\n1\n", encoding="utf-8")
    _open(client, root)
    err = engine_pool.WorkerError("找不到 data/raw.csv", code="missing_input")
    err.missing_input = {
        "script": "plot.py",
        "requested": "data/raw.csv",
        "absolute": False,
        "via": "open",
        "probe_kind": "any",
        "others": [],
    }
    fake_pool["error"] = err
    first = _create(client, {"script": "plot.py"}).get_json()
    claimed = _act(client, first["session_id"], _action(first, "run")["id"], 1)
    assert claimed.status_code == 202, claimed.get_json()
    report = _settle(client, first["session_id"])
    assert report["outcome"]["code"] == "missing_input"
    (req,) = [r for r in report["requirements"] if r["kind"] == "input_location"]
    assert req["payload"]["requested"] == "data/raw.csv" and req["blocking"] is False
    assert "missing_input" not in report["result"] and "missing_input" not in report["plan"]
    # 回答走既有端点（用户亲手指认）；recheck → 修订 +1，旧失败的需求消失
    resp = client.post(
        "/api/engine/input-remap",
        json={
            "requested": "data/raw.csv",
            "chosen": str(root / "elsewhere" / "raw.csv"),
            "chosen_kind": "file",
        },
    )
    assert resp.status_code == 200, resp.get_json()
    # 报告上已经发出的重跑动作也不能直接消费新改指表：先拒绝，再要求 recheck。
    rejected = _act(
        client, first["session_id"], _action(report, "run")["id"], report["config_revision"]
    )
    assert rejected.status_code == 409, rejected.get_json()
    assert rejected.get_json()["code"] == "preparation_plan_stale"
    assert fake_pool["build_calls"] == 1
    report = _get(client, first["session_id"]).get_json()
    recheck = _action(report, "recheck")
    assert _act(
        client, first["session_id"], recheck["id"], report["config_revision"]
    ).status_code in (
        200,
        202,
    )
    after = _get(client, first["session_id"]).get_json()
    assert after["config_revision"] == report["config_revision"] + 1
    assert not [r for r in after["requirements"] if r["kind"] == "input_location"]
    assert after["phase"] == "ready_to_run"
    assert inputremap.rules_for(str(root))


def test_the_missing_input_payload_stays_out_of_public_projections(tmp_path):
    result = preparation.PreparationResult()
    result.missing_input = {"requested": "secret/path.csv"}
    assert "missing_input" not in result.to_payload()
    assert "secret/path.csv" not in str(result.to_payload())


@pytest.mark.parametrize("change", ["add", "replace", "remove"])
def test_changed_remap_rejects_issued_run_until_rechecked(
    client, tmp_path, fake_pool, sessions, change
):
    root = _proj(tmp_path, "print('ready')\n")
    _open(client, root)
    rule = {"kind": "file", "from": "data.csv", "to": str(tmp_path / "old.csv")}
    if change != "add":
        inputremap.add_rule(root, rule)
    report = _create(client, {"script": "plot.py"}).get_json()
    run = _action(report, "run")
    if change == "remove":
        inputremap.remove_rule(root, "file", "data.csv")
    else:
        inputremap.add_rule(root, {**rule, "to": str(tmp_path / "new.csv")})

    rejected = _act(client, report["session_id"], run["id"], report["config_revision"])
    assert rejected.status_code == 409, rejected.get_json()
    assert rejected.get_json()["code"] == "preparation_plan_stale"
    assert rejected.get_json()["params"] == {
        "reason": "data_binding_changed",
        "executed": False,
    }
    assert fake_pool["build_calls"] == 0
    stale = _get(client, report["session_id"]).get_json()
    assert not [a for a in stale["actions"] if a["kind"] == "run"]
    rechecked = _act(
        client, report["session_id"], _action(stale, "recheck")["id"], report["config_revision"]
    )
    assert rechecked.status_code in (200, 202), rechecked.get_json()
    refreshed = _get(client, report["session_id"]).get_json()
    assert refreshed["config_revision"] == report["config_revision"] + 1
    assert refreshed["phase"] == "ready_to_run"
    assert _action(refreshed, "run")["id"] != run["id"]
    assert (
        _act(
            client,
            report["session_id"],
            _action(refreshed, "run")["id"],
            refreshed["config_revision"],
        ).status_code
        == 202
    )
    assert _settle(client, report["session_id"])["phase"] == "completed"
    assert fake_pool["build_calls"] == 1


def test_changed_remap_is_rechecked_before_delayed_execution(
    client, tmp_path, fake_pool, sessions, monkeypatch
):
    root = _proj(tmp_path, "print('ready')\n")
    _open(client, root)
    report = _create(client, {"script": "plot.py"}).get_json()
    start = preparation.SERVICE.start

    def delayed_start(plan_id, **kwargs):
        inputremap.add_rule(
            root, {"kind": "file", "from": "data.csv", "to": str(tmp_path / "new.csv")}
        )
        start(plan_id, **kwargs)

    monkeypatch.setattr(preparation.SERVICE, "start", delayed_start)
    claimed = _act(client, report["session_id"], _action(report, "run")["id"], 1)
    assert claimed.status_code == 202, claimed.get_json()
    settled = _settle(client, report["session_id"])
    assert settled["result"]["error"]["code"] == "preparation_plan_stale"
    assert settled["result"]["error"]["reason"] == "data_binding_changed"
    assert settled["result"]["error"]["executed"] is False
    assert fake_pool["build_calls"] == 0


def test_an_asset_target_never_gets_a_form(client, tmp_path, fake_pool, sessions, monkeypatch):
    """已知素材不接受另给 argv（T03）：它的会话不提议参数表单。"""
    root, _s, _o = _argparse_project(tmp_path)
    _open(client, root)
    from tavotto.engine import prepsession

    plan = preparation.PreparationPlan(
        plan_id="p",
        project_id="x",
        project_root=str(root),
        interpreter="",
        asset_id="a",
        stem="s",
        script="plot.py",
        entry="__main__",
        static_source=None,
        environment={},
        python_requirement={},
        dependency_intents=(),
        dependency_conflicts=(),
        launch_context=None,
        grant={},
        budget={},
        created_at=0.0,
    )
    assert prepsession.arguments_schema(plan) is None
    assert m  # app 已导入（夹具）


# ---------------------------------------------------------------- 源码修订绑进计划与动作（Codex r4220829632）

PLAIN_OUT = """\
import argparse
p = argparse.ArgumentParser()
p.add_argument("--out", default="report.txt")
args = p.parse_args()
"""

FILETYPE_OUT = """\
import argparse
p = argparse.ArgumentParser()
p.add_argument("--out", type=argparse.FileType("w"), default="report.txt")
args = p.parse_args()
"""


@pytest.mark.parametrize("polled_in_between", [False, True])
def test_editing_the_script_after_a_run_action_was_issued_rejects_the_claim(
    client, tmp_path, fake_pool, sessions, polled_in_between
):
    """发出 `run` 时 `--out` 是普通字符串；随后脚本改成 `FileType('w')`（打开即截断）。旧动作披露里没有
    `script_writes`，认领它就是拿过期的影响披露去执行改过的脚本：必须拒绝、一行不跑、要求重新检查。"""
    root = _proj(tmp_path, PLAIN_OUT)
    existing = root / "existing.txt"
    existing.write_bytes(b"keep me")
    _open(client, root)
    report = _create(client, {"script": "plot.py"}).get_json()
    run = _action(report, "run")
    assert "script_writes" not in run["impact"]

    (root / "plot.py").write_text(FILETYPE_OUT, encoding="utf-8")
    if polled_in_between:
        # 读报告时就把过期的 run 撤掉（不再摆给用户），并标失效等 recheck
        polled = _get(client, report["session_id"]).get_json()
        assert not [a for a in polled["actions"] if a["kind"] == "run"]
        assert polled["phase"] == "action_required" or polled["outcome"]["kind"] == "stale", polled
        rejected = _act(client, report["session_id"], run["id"], report["config_revision"])
        assert rejected.status_code == 404
    else:
        rejected = _act(client, report["session_id"], run["id"], report["config_revision"])
        assert rejected.status_code == 409, rejected.get_json()
        assert rejected.get_json()["code"] == "preparation_plan_stale"
        assert rejected.get_json()["params"] == {"reason": "script_changed", "executed": False}
    assert fake_pool["build_calls"] == 0
    assert existing.read_bytes() == b"keep me"

    stale = _get(client, report["session_id"]).get_json()
    assert not [a for a in stale["actions"] if a["kind"] == "run"]
    recheck = _act(
        client, report["session_id"], _action(stale, "recheck")["id"], report["config_revision"]
    )
    assert recheck.status_code in (200, 202), recheck.get_json()
    fresh = _get(client, report["session_id"]).get_json()
    assert fresh["config_revision"] == report["config_revision"] + 1
    # 重新披露：新的 run 动作带着 script_writes
    assert _action(fresh, "run")["impact"]["script_writes"]["declared_output_arguments"] == 1


def test_an_unchanged_script_keeps_its_run_action_valid(client, tmp_path, fake_pool, sessions):
    root = _proj(tmp_path, PLAIN_OUT)
    _open(client, root)
    report = _create(client, {"script": "plot.py"}).get_json()
    again = _get(client, report["session_id"]).get_json()
    assert _action(again, "run")["id"] == _action(report, "run")["id"]
    claimed = _act(client, report["session_id"], _action(report, "run")["id"], 1)
    assert claimed.status_code == 202, claimed.get_json()


def test_the_script_revision_covers_bytes_and_schema():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "s.py"
        path.write_text(PLAIN_OUT, encoding="utf-8")
        first = scriptargs.source_revision(path)
        assert first == scriptargs.source_revision(path)
        path.write_text(PLAIN_OUT + "# comment\n", encoding="utf-8")  # 字节变了、schema 没变
        assert scriptargs.source_revision(path) != first
        assert scriptargs.source_revision(path).split(":")[1] == first.split(":")[1]
        path.write_text("# 一行注释\n" + PLAIN_OUT, encoding="utf-8")  # 只挪了行号：披露的内容没变
        assert scriptargs.source_revision(path).split(":")[1] == first.split(":")[1]
        path.write_text(FILETYPE_OUT, encoding="utf-8")  # schema 也变了
        assert scriptargs.source_revision(path).split(":")[1] != first.split(":")[1]


def test_a_dependency_authorisation_survives_script_edits_that_do_not_touch_the_schema(
    client, tmp_path, fake_pool, sessions
):
    """依赖准备不执行脚本、用自己的影响摘要验证要装什么：与参数声明无关的脚本改动不撤销已发出的授权动作。"""
    root = _proj(tmp_path, PLAIN_OUT)
    _open(client, root)
    report = _create(client, {"script": "plot.py"}).get_json()
    (root / "plot.py").write_text("# 无关改动\n" + PLAIN_OUT, encoding="utf-8")
    polled = _get(client, report["session_id"]).get_json()
    assert polled["config_revision"] == report["config_revision"]
    assert _action(polled, "run")["id"] == _action(report, "run")["id"]
    sess = prepsession.SESSIONS.get(report["session_id"], polled["project_id"])
    assert preparation.stale_reason(sess.plan, source=False) is None
    assert preparation.stale_reason(sess.plan)[0] == "script_changed"  # 要执行脚本的认领仍核字节
