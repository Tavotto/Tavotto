"""P05a（AC-26 / AC-24）：同一个真实 `WorkerError`，经准备会话序列化到界面，不再只剩 `code` + `message`。

旧试运行出口（`probe._error_from_worker`）与渲染端点（`app._worker_error_payload`）早就保留的恢复信息——
本地 traceback、`argv_count` / `parse_kind`、input 的提示与理由、`missing_input` 载荷——在新的准备出口曾经丢光。
这里钉四件事：

* 每一类错误各自保留稳定的恢复字段，并且与另外两个出口**同一份**（错误转换合同 `pool.recovery_params`）；
* 本地详情完整，**共享诊断（任务快照 / 诊断包）一个字都不带**；
* 敏感运行（argv 含口令）不恢复 worker 已经隐藏的内容：不带 traceback，个数与类别（不含值）照留；
* 前端契约 fixture（`tests/golden/preparation_error_reports.json`）就是这几个场景的**真实序列化结果**，
  漂了就红（重新生成：`TAVOTTO_WRITE_GOLDEN=1 pytest tests/test_preparation_error_detail.py`）。
"""

# ruff: noqa: F811 — 夹具（client / fake_pool）从 test_preparation_api 导入复用，参数名与导入名相同
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import pool as engine_pool, prepsession, probe, taskdiag
from test_preparation_api import (  # noqa: F401 — 夹具与替身复用，不重写第二套
    _open,
    _project,
    client,
    fake_pool,
)
from test_preparation_session import (  # noqa: F401
    SESSIONS,
    _act,
    _action,
    _create,
    _get,
    _settle,
    sessions,
)

GOLDEN = Path(__file__).parent / "golden" / "preparation_error_reports.json"

SECRET = "hunter2-s3cret-token"
PROMPT_MARK = "Enter output directory: "

#: 一个真实长相的 traceback（多帧；最后一行是脚本自己的异常）
TB = (
    "Traceback (most recent call last):\n"
    '  File "/work/proj/fig.py", line 12, in <module>\n'
    "    main()\n"
    '  File "/work/proj/fig.py", line 7, in main\n'
    "    args = parser.parse_args()\n"
    "SystemExit: 2\n"
)


def _error(
    message: str,
    code: str,
    *,
    traceback_text: str = TB,
    extra: dict | None = None,
    missing_input: dict | None = None,
) -> engine_pool.WorkerError:
    err = engine_pool.WorkerError(message, traceback_text, code=code)
    err.extra = dict(extra or {})
    err.missing_input = missing_input
    return err


#: 场景表：名字 → (WorkerError 工厂, 是否敏感 argv)。同一张表同时喂 golden 与断言
SCENARIOS = {
    "plain_exception": (
        lambda: _error(
            "ZeroDivisionError: division by zero",
            "script_error",
            traceback_text=TB.replace("SystemExit: 2", "ZeroDivisionError: division by zero"),
        ),
        False,
    ),
    "invalid_argv": (
        lambda: _error(
            "脚本要求命令行参数",
            "script_needs_arguments",
            extra={"argv_count": 2, "parse_kind": "invalid_value"},
        ),
        False,
    ),
    "missing_argv": (
        lambda: _error(
            "脚本要求命令行参数",
            "script_needs_arguments",
            extra={"parse_kind": "missing_required"},
        ),
        False,
    ),
    "unanswered_input": (
        lambda: _error(
            f"脚本需要输入：{PROMPT_MARK}",
            "script_needs_input",
            traceback_text=TB.replace("SystemExit: 2", "EOFError: EOF when reading a line"),
            extra={"prompt": PROMPT_MARK, "reason": "no_interactive_client"},
        ),
        False,
    ),
    "missing_input_file": (
        lambda: _error(
            "FileNotFoundError: [Errno 2] No such file or directory: 'points.csv'",
            engine_pool.MISSING_INPUT_CODE,
            traceback_text=TB.replace(
                "SystemExit: 2",
                "FileNotFoundError: [Errno 2] No such file or directory: 'points.csv'",
            ),
            missing_input={
                "code": "missing_input",
                "calls": [{"api": "np.loadtxt", "name": "points.csv", "line": 7}],
                "candidates": [],
            },
        ),
        False,
    ),
}


def _run(client, tmp_path, fake_pool, name: str, *, sensitive: bool = False):
    """起会话 → 点运行 → 等终局，回 `(root, pj, 终局报告)`。"""
    root = _project(tmp_path, "p")
    pj = _open(client, root)
    body: dict = {"script": "fig.py"}
    if sensitive:
        body.update({"argv": ["--token", SECRET], "argv_sensitive": True})
    created = _create(client, body)
    assert created.status_code in (200, 201), created.get_json()
    report = created.get_json()
    fake_pool["error"] = SCENARIOS[name][0]()
    acted = _act(
        client, report["session_id"], _action(report, "run")["id"], report["config_revision"]
    )
    assert acted.status_code == 202, acted.get_json()
    return root, pj, _settle(client, report["session_id"])


def _golden_view(report: dict) -> dict:
    """前端只认这几块；会话 / 计划 id 与时间戳不进 golden（每次都不同）。"""
    return {
        "phase": report["phase"],
        "action_kinds": sorted(a["kind"] for a in report["actions"]),
        "outcome": report["outcome"],
        "requirements": report["requirements"],
        "result": {"status": report["result"]["status"], "error": report["result"]["error"]},
    }


# ---------------------------------------------------------------- 每一类各自保留稳定的恢复字段


def test_a_plain_exception_keeps_the_full_local_traceback(client, tmp_path, fake_pool, sessions):
    _root, _pj, report = _run(client, tmp_path, fake_pool, "plain_exception")
    err = report["result"]["error"]
    assert err["code"] == "script_error"
    assert err["message"] == "ZeroDivisionError: division by zero"
    assert err["traceback"] == SCENARIOS["plain_exception"][0]().traceback_text.strip()
    assert "params" not in err  # 没有恢复字段的码不捏造
    assert "traceback_truncated" not in err


def test_invalid_argv_keeps_the_count_and_parse_kind_but_never_the_values(
    client, tmp_path, fake_pool, sessions
):
    _root, _pj, report = _run(client, tmp_path, fake_pool, "invalid_argv")
    err = report["result"]["error"]
    assert err["code"] == "script_needs_arguments"
    assert err["params"] == {"argv_count": "2", "parse_kind": "invalid_value"}
    assert err["traceback"].endswith("SystemExit: 2")
    assert report["phase"] == "action_required"


def test_missing_argv_keeps_the_parse_kind_without_a_count(client, tmp_path, fake_pool, sessions):
    _root, _pj, report = _run(client, tmp_path, fake_pool, "missing_argv")
    err = report["result"]["error"]
    assert err["code"] == "script_needs_arguments"
    assert err["params"] == {"parse_kind": "missing_required"}  # 没给 token = 没有 argv_count


def test_an_unanswered_input_keeps_the_prompt_and_the_reason(client, tmp_path, fake_pool, sessions):
    _root, _pj, report = _run(client, tmp_path, fake_pool, "unanswered_input")
    err = report["result"]["error"]
    assert err["code"] == "script_needs_input"
    assert err["params"] == {"prompt": PROMPT_MARK, "reason": "no_interactive_client"}
    assert err["traceback"].endswith("EOFError: EOF when reading a line")


def test_a_reason_outside_the_closed_set_is_dropped(client, tmp_path, fake_pool, sessions):
    err = _error("x", "script_needs_input", extra={"prompt": "p", "reason": "free text <script>"})
    assert engine_pool.recovery_params(err) == {"prompt": "p"}


def test_missing_input_keeps_the_relink_payload_and_the_traceback(
    client, tmp_path, fake_pool, sessions
):
    _root, _pj, report = _run(client, tmp_path, fake_pool, "missing_input_file")
    err = report["result"]["error"]
    assert err["code"] == engine_pool.MISSING_INPUT_CODE
    assert err["traceback"].endswith("'points.csv'")
    (req,) = [r for r in report["requirements"] if r["kind"] == "input_location"]
    assert req["payload"]["calls"][0]["name"] == "points.csv"  # 回答走既有 /api/engine/input-remap


def test_a_long_traceback_keeps_the_tail_and_says_it_was_cut(client, tmp_path, fake_pool, sessions):
    limit = engine_pool.LOCAL_TRACEBACK_LIMIT
    long_tb = "HEAD-FRAME\n" + "x" * (limit * 2) + "\nLAST-LINE: boom"
    err = _error("boom", "script_error", traceback_text=long_tb)
    fake_pool["error"] = err
    root = _project(tmp_path, "p")
    _open(client, root)
    report = _create(client, {"script": "fig.py"}).get_json()
    assert (
        _act(
            client, report["session_id"], _action(report, "run")["id"], report["config_revision"]
        ).status_code
        == 202
    )
    got = _settle(client, report["session_id"])["result"]["error"]
    assert got["traceback"].endswith("LAST-LINE: boom")
    assert "HEAD-FRAME" not in got["traceback"]
    assert len(got["traceback"]) == limit and got["traceback_truncated"] is True


# ---------------------------------------------------------------- 三个出口同一份合同


@pytest.mark.parametrize("name", ["invalid_argv", "missing_argv", "unanswered_input"])
def test_preparation_probe_and_render_endpoint_share_one_recovery_contract(name):
    exc = SCENARIOS[name][0]()
    expected = engine_pool.recovery_params(exc)
    assert expected  # 这三类都有恢复字段
    assert m._worker_error_payload(exc)["params"] == expected
    probe_params = probe._error_from_worker(exc, "main")["params"]
    assert {k: v for k, v in probe_params.items() if k in expected} == expected


# ---------------------------------------------------------------- 本地详情≠共享诊断


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


@pytest.mark.parametrize("name", ["invalid_argv", "unanswered_input", "missing_input_file"])
def test_shared_diagnostics_carry_none_of_the_local_detail(
    client, tmp_path, fake_pool, sessions, name
):
    _root, pj, report = _run(client, tmp_path, fake_pool, name)
    local = _dump(report["result"]["error"])
    assert "traceback" in local  # 本地确实完整
    attempt = report["provider"]["attempt_id"]
    resp = client.get(
        "/api/diagnostics/task", query_string={"kind": "preparation", "ref": attempt, "pj": pj}
    )
    assert resp.status_code == 200
    shared = _dump(resp.get_json()) + _dump(taskdiag.bundle_section(taskdiag.STORE, pj))
    for needle in (
        "Traceback",
        "fig.py",
        "SystemExit",
        PROMPT_MARK,
        "points.csv",
        "/work/proj",
        "invalid_value",
    ):
        assert needle not in shared, needle


# ---------------------------------------------------------------- 敏感值反例


def test_a_sensitive_run_does_not_restore_what_the_worker_hid(
    client, tmp_path, fake_pool, sessions, monkeypatch
):
    """worker 已把敏感运行的脚本输出换成占位；即便上游某一环漏了（这里模拟 traceback 里有口令），准备出口
    也不带 traceback。个数 / 类别不含值，照常保留，恢复动作不丢。"""
    leaking = _error(
        f"parse failed for --token {SECRET}",
        "script_needs_arguments",
        traceback_text=f"Traceback ...\nSystemExit: argument --token: invalid value {SECRET!r}",
        extra={"argv_count": 2, "parse_kind": "invalid_value"},
    )
    # 带运行配置（argv）时执行线程以 `run=` 查池；通用替身的 peek 不收这个参数
    monkeypatch.setattr(engine_pool, "peek", lambda script, root, **_kw: None)
    SCENARIOS["_sensitive"] = (lambda: leaking, True)
    try:
        _root, pj, report = _run(client, tmp_path, fake_pool, "_sensitive", sensitive=True)
    finally:
        SCENARIOS.pop("_sensitive")
    err = report["result"]["error"]
    assert "traceback" not in err
    assert err["params"] == {"argv_count": "2", "parse_kind": "invalid_value"}
    # 共享出口当然也没有口令（message 本身是 worker 的责任：它对敏感运行不会把值写进 message）
    attempt = report["provider"]["attempt_id"]
    shared = _dump(
        client.get(
            "/api/diagnostics/task", query_string={"kind": "preparation", "ref": attempt, "pj": pj}
        ).get_json()
    )
    assert SECRET not in shared


def test_the_input_kind_survives_so_an_unanswered_getpass_is_still_a_secret():
    """getpass 首问没人能答：理由是 no_interactive_client，口令性质只在 input_kind 里——转换不能丢（Codex #973）。"""
    err = _error(
        "x",
        "script_needs_input",
        extra={"prompt": "Password: ", "reason": "no_interactive_client", "input_kind": "getpass"},
    )
    assert engine_pool.recovery_params(err) == {
        "prompt": "Password: ",
        "reason": "no_interactive_client",
        "input_kind": "getpass",
    }
    bogus = _error("x", "script_input_timeout", extra={"input_kind": "../etc"})
    assert "input_kind" not in engine_pool.recovery_params(bogus)  # 闭集外的值不带出去


def test_the_getpass_secret_is_not_in_the_report_when_the_worker_hid_it(
    client, tmp_path, fake_pool, sessions
):
    """getpass 的答案从来不进异常；口令类的理由只是闭集字符串 `secret_required`。"""
    err = _error(
        "x", "script_needs_input", extra={"prompt": "Password: ", "reason": "secret_required"}
    )
    assert engine_pool.recovery_params(err) == {"prompt": "Password: ", "reason": "secret_required"}


# ---------------------------------------------------------------- 普通图不受影响


def test_a_successful_run_has_no_error_detail(client, tmp_path, fake_pool, sessions):
    root = _project(tmp_path, "p")
    _open(client, root)
    report = _create(client, {"script": "fig.py"}).get_json()
    assert (
        _act(
            client, report["session_id"], _action(report, "run")["id"], report["config_revision"]
        ).status_code
        == 202
    )
    done = _settle(client, report["session_id"])
    assert done["phase"] == "completed"
    assert done["result"]["error"] is None


# ---------------------------------------------------------------- 前端契约 fixture = 真实序列化


def test_the_frontend_fixture_is_the_real_serialization(client, tmp_path, fake_pool, sessions):
    got: dict = {}
    for name in SCENARIOS:
        # 每个场景一个新项目目录，互不串
        sub = tmp_path / name
        sub.mkdir()
        prepsession.SESSIONS.reset_for_tests()
        _root, _pj, report = _run(client, sub, fake_pool, name)
        got[name] = _golden_view(report)
    if os.environ.get("TAVOTTO_WRITE_GOLDEN") == "1":
        GOLDEN.write_text(
            json.dumps(got, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8"
        )
    assert json.loads(GOLDEN.read_text("utf-8")) == json.loads(json.dumps(got, sort_keys=True)), (
        "golden 漂了：TAVOTTO_WRITE_GOLDEN=1 重新生成并同步前端用例"
    )
