"""T10：MCP 入口与 GUI 消费同一份运行配置 / 同一个采用服务，旧引擎不静默吞参数，没有界面时立即给结构化待办。

假 worker（协议层，跑在 .venv 里）；真 worker + 真 stdio + 真 GUI 服务的对拍在 `tests/test_mcp_compat_e2e.py`。

「旧引擎」在这里是**模拟**的：插件可能配着一份更早安装的 Tavotto（插件版本 ≠ 已装引擎版本），旧引擎没有
`tavotto.engine.capabilities` 这个模块——用例把它从 `sys.modules` 里遮掉，等价于 import 失败。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto.engine import capabilities, inputbroker, runconfig  # noqa: E402
from tavotto_mcp import bridge, rpc, server, sessionjournal  # noqa: E402
from test_mcp_server import FakeWorker  # noqa: E402

ARGV = ["--freq", "2", "中 文", "", "-1", "--"]


def _encoded(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    bridge.reset_root_authority()
    bridge.sessions().clear()
    monkeypatch.setattr(bridge.engine_handoff, "http_json_status", lambda *a, **k: (None, None))
    yield
    bridge.sessions().clear()
    bridge.reset_root_authority()
    runconfig.forget_secrets()


@pytest.fixture
def project(tmp_path, monkeypatch):
    figures = tmp_path / "figures"
    figures.mkdir()
    (figures / "fig1.py").write_text(
        "import argparse\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--freq', required=True, type=float)\n"
        "p.add_argument('--mode', choices=['a', 'b'])\n"
        "args = p.parse_args()\n"
        "def main():\n    pass\n",
        encoding="utf-8",
    )
    (figures / "Fig1.pdf").write_bytes(b"%PDF-1.4\n")
    (figures / "tavotto_registry.json").write_text(
        json.dumps({"scripts": {"fig1.py": {"entry": "main", "cost": "light", "stems": ["Fig1"]}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    return figures


class Pool:
    """记下每一次 `pool.get` 的 run（None = 调用方根本没传 `run=`，旧引擎的调用形状）。"""

    def __init__(self) -> None:
        self.worker = FakeWorker()
        self.runs: list = []

    def get(self, *args, **kwargs):
        self.runs.append(kwargs["run"] if "run" in kwargs else None)
        return self.worker


@pytest.fixture
def pool(monkeypatch):
    p = Pool()
    monkeypatch.setattr(bridge.engine_pool, "get", p.get)
    return p


@pytest.fixture
def old_engine(monkeypatch):
    """旧引擎：没有能力模块（import 失败）。包上的同名属性也摘掉，否则 `from tavotto.engine import capabilities`
    直接读到已 import 过的属性。"""
    import tavotto.engine

    monkeypatch.setitem(sys.modules, "tavotto.engine.capabilities", None)
    monkeypatch.delattr(tavotto.engine, "capabilities", raising=False)


def _open(project, **args):
    return server.call_tool("tavotto_open_figure", {"project_path": str(project), **args})


# --------------------------------------------------------------------- 能力协商
def test_health_reports_the_engine_features_the_bridge_negotiates_with(project):
    body = server.call_tool("tavotto_health", {})["structuredContent"]
    assert body["engine_features"] == list(capabilities.FEATURES)
    # 插件要能配更早的 Tavotto：桥里那份名字是引擎常量的镜像（旧引擎没有那个模块可 import）
    assert bridge.SCRIPT_ARGV_FEATURE == capabilities.SCRIPT_ARGV
    assert bridge.ENVIRONMENT_ADOPTION_FEATURE == capabilities.ENVIRONMENT_ADOPTION
    assert bridge.CAPABILITY_MISSING == capabilities.ERROR_CAPABILITY_MISSING


def test_health_on_an_old_engine_says_it_negotiates_nothing(project, old_engine):
    body = server.call_tool("tavotto_health", {})["structuredContent"]
    assert body["engine_features"] == []


def test_non_empty_argv_on_an_old_engine_is_refused_before_anything_runs(project, pool, old_engine):
    """X03/C20：旧引擎不认识 argv → 明确拒绝；**不先试一次无参数**（pool 一次都没被调）、也不登记配置。"""
    res = _open(project, argv=["--freq", "2"])
    assert res["isError"] is True
    body = res["structuredContent"]
    assert body["code"] == "engine_capability_missing"
    assert body["capability"] == "script-argv"
    assert body["requirements"] == [
        {"kind": "engine_upgrade", "answer_with": None, "where": "user"}
    ]
    assert pool.runs == []
    assert not runconfig.store_path(project).exists()
    assert bridge.sessions() == {}


@pytest.mark.parametrize("args", [{}, {"argv": []}])
def test_no_arguments_on_an_old_engine_runs_exactly_as_before(project, pool, old_engine, args):
    """缺省与空 argv = 旧行为：调用形状里根本没有 `run=`，回包里没有 `run_config`。"""
    body = _open(project, **args)["structuredContent"]
    assert body["ok"] is True
    assert pool.runs == [None]
    assert "run_config" not in body


def test_run_config_reference_on_an_old_engine_is_refused(project, pool, old_engine):
    res = _open(project, run_config="rc_0123456789ab")
    assert res["structuredContent"]["code"] == "engine_capability_missing"
    assert pool.runs == []


# --------------------------------------------------------------------- argv / 运行配置
def test_argv_reaches_the_pool_as_the_same_configuration_the_gui_registers(project, pool):
    """C19：Agent 给的 token 与 GUI 同一份登记 → 同一个 `rc_` 引用；回包只有引用与个数，没有参数原文。"""
    body = _open(project, argv=ARGV)["structuredContent"]
    run = pool.runs[-1]
    assert run is not None and run.argv == tuple(ARGV)
    gui = runconfig.selection_for(
        project, "fig1.py", ARGV
    )  # GUI 试运行 / 准备会话走的就是这一个函数
    assert run.config_id == gui.config_id
    assert body["run_config"] == {"id": gui.config_id, "argv_count": len(ARGV), "source": "argv"}
    assert "中 文" not in json.dumps(body, ensure_ascii=False)
    # 登记里记下它来自 MCP（只是来源，不进身份）；没有改磁盘面板的默认配置
    assert runconfig.get(project, gui.config_id).source == "mcp"
    assert runconfig.default_selection(project, "fig1.py") is None


def test_a_gui_run_configuration_reference_is_used_verbatim(project, pool):
    cfg = runconfig.put(project, "fig1.py", ["--freq", "7"])
    body = _open(project, run_config=cfg.id)["structuredContent"]
    assert pool.runs[-1].argv == ("--freq", "7") and pool.runs[-1].config_id == cfg.id
    assert body["run_config"]["source"] == "run_config"


@pytest.mark.parametrize(
    "setup, code",
    [
        (lambda p: "rc_ffffffffffff", "run_config_missing"),
        (lambda p: runconfig.put(p, "other.py", ["--x"]).id, "run_config_missing"),
        (lambda p: runconfig.put(p, "fig1.py", ["--token", "s"], sensitive=True).id, None),
    ],
    ids=["not_on_this_machine", "other_script", "secret_gone"],
)
def test_a_reference_that_cannot_be_used_is_refused_not_run_without_arguments(
    project, pool, setup, code
):
    """C26：用不了的引用（别的机器 / 别的脚本 / 敏感值只在 GUI 进程内存里）→ 明确拒绝，脚本一次都不执行。"""
    ref = setup(project)
    runconfig.forget_secrets()
    body = _open(project, run_config=ref)["structuredContent"]
    assert body["code"] == (code or "run_config_secret_missing")
    assert pool.runs == []
    if code is None:
        # 口令不经 Agent 透传：只能请用户在 Tavotto 窗口里重新输入
        assert body["requirements"][0] == {
            "kind": "script_arguments",
            "answer_with": None,
            "where": "tavotto_app",
        }


def test_omitted_argv_follows_the_scripts_last_explicit_run_like_the_gui_disk_panel(project, pool):
    """磁盘上的 Fig1.pdf 是用户在 GUI 里带参数那次运行写的：MCP 打开它用同一份配置（`_disk_panel_run` 的同一个判据），
    不是悄悄换成无参数版本。显式 `argv=[]` 仍是无参数。"""
    cfg = runconfig.put(project, "fig1.py", ["--freq", "3"])
    runconfig.set_default(project, "fig1.py", cfg.id)
    body = _open(project)["structuredContent"]
    assert pool.runs[-1].config_id == cfg.id
    assert body["run_config"] == {"id": cfg.id, "argv_count": 2, "source": "script_default"}
    bridge.sessions().clear()
    body = _open(project, argv=[])["structuredContent"]
    assert pool.runs[-1] is None and "run_config" not in body


def test_same_stem_with_different_argv_never_shares_a_session(project, pool):
    """R02：同脚本同 stem、不同参数 → 两个会话；同一份参数再开 → 沿用自己那个。"""
    a = _open(project, argv=["--freq", "1"])["structuredContent"]
    b = _open(project, argv=["--freq", "2"])["structuredContent"]
    plain = _open(project, argv=[])["structuredContent"]
    assert len({a["session_id"], b["session_id"], plain["session_id"]}) == 3
    again = _open(project, argv=["--freq", "1"])["structuredContent"]
    assert again["session_id"] == a["session_id"] and again["reused"] is True


@pytest.mark.parametrize(
    "bad",
    ["--freq 2", [1, 2], ["a\x00b"], ["x"] * 1000],
    ids=["shell_string", "not_strings", "nul", "too_many"],
)
def test_argv_must_be_an_exact_token_list(project, pool, bad):
    """不把一整串命令按空格猜成 token；坏形状在边界上拒绝，脚本不执行。"""
    try:
        res = _open(project, argv=bad)
    except rpc.RpcError as exc:
        assert exc.code == rpc.INVALID_PARAMS
    else:
        assert res["structuredContent"]["code"] == "invalid_argv"
    assert pool.runs == []


def test_argv_and_run_config_are_mutually_exclusive(project, pool):
    with pytest.raises(rpc.RpcError):
        _open(project, argv=["--freq", "1"], run_config="rc_0123456789ab")
    assert pool.runs == []


@pytest.mark.parametrize(
    "extra",
    [{"argv": ["--freq", "1"]}, {"run_config": "rc_0123456789ab"}, {"adopt_environment": "x"}],
)
def test_batch_open_refuses_per_script_answers(project, pool, extra):
    with pytest.raises(rpc.RpcError):
        _open(project, stems=["Fig1"], **extra)
    assert pool.runs == []


def test_every_later_worker_call_of_the_session_uses_its_frozen_configuration(project, pool):
    """apply / 导出 / 重放都经 `Session.acquire` 或 `one_shot`：用的是会话打开时冻结的那份，不是之后的默认。"""
    body = _open(project, argv=["--freq", "4"])["structuredContent"]
    sid = body["session_id"]
    other = runconfig.put(project, "fig1.py", ["--freq", "9"])
    runconfig.set_default(project, "fig1.py", other.id)  # 之后 GUI 又跑了别的参数
    server.call_tool("tavotto_apply_overrides", {"session_id": sid, "patches": []})
    assert {r.argv for r in pool.runs} == {("--freq", "4")}


def test_verify_replay_replays_with_the_sessions_configuration(project, pool, monkeypatch):
    seen: list = []

    def one_shot(*a, **k):
        seen.append(k.get("run"))
        return pool.worker

    monkeypatch.setattr(bridge.engine_pool, "one_shot", one_shot)
    monkeypatch.setattr(bridge.engine_pool, "discard", lambda w: None)
    sid = _open(project, argv=["--freq", "5"])["structuredContent"]["session_id"]
    bridge.verify_replay(sid)
    assert seen[0].argv == ("--freq", "5")


# --------------------------------------------------------------------- 会话落盘
def test_the_journal_keeps_only_the_reference_and_restores_with_it(project, pool):
    sid = _open(project, argv=["--freq", "6"])["structuredContent"]["session_id"]
    raw = (bridge._journal_dir() / f"{sid}.json").read_text(encoding="utf-8")
    assert "--freq" not in raw  # 落盘的只有不透明引用
    record = json.loads(raw)
    assert record["run_config"].startswith("rc_")
    # 带配置的记录**旧读者不认识**（版本号不同 → 当作不存在），不会被旧插件当成无参数会话复活
    assert record["v"] != 1
    old_reader = pytest.MonkeyPatch()
    old_reader.setattr(sessionjournal, "READABLE_VERSIONS", (sessionjournal.VERSION,))
    try:
        assert sessionjournal.load(bridge._journal_dir(), sid) is None  # 模拟旧插件（只认 v=1）
    finally:
        old_reader.undo()
    bridge.sessions().clear()
    pool.runs.clear()
    restored = bridge.get_session(sid)
    assert restored.restored is True and pool.runs[-1].argv == ("--freq", "6")


def test_a_plain_session_is_still_written_in_the_old_format(project, pool):
    sid = _open(project)["structuredContent"]["session_id"]
    record = json.loads((bridge._journal_dir() / f"{sid}.json").read_text(encoding="utf-8"))
    assert record["v"] == 1 and "run_config" not in record
    assert sessionjournal.load(bridge._journal_dir(), sid) is not None


def test_restoring_a_session_whose_secret_is_gone_refuses_instead_of_running_bare(project, pool):
    cfg = runconfig.put(project, "fig1.py", ["--token", "s"], sensitive=True)
    sid = _open(project, run_config=cfg.id)["structuredContent"]["session_id"]
    bridge.sessions().clear()
    runconfig.forget_secrets()
    pool.runs.clear()
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.get_session(sid)
    assert exc.value.code == "run_config_secret_missing"
    assert pool.runs == []


# --------------------------------------------------------------------- 没有界面时的结构化待办
def _raising(code: str, **attrs):
    def get(*a, **k):
        err = bridge.engine_pool.WorkerError(
            "脚本需要输入：请输入阈值，请在 Tavotto 界面里运行", code=code
        )
        for key, value in attrs.items():
            setattr(err, key, value)
        raise err

    return get


@pytest.mark.parametrize(
    "reason", [inputbroker.REASON_NO_CLIENT, inputbroker.REASON_SECRET_REQUIRED]
)
def test_runtime_input_without_a_ui_is_a_structured_requirement_not_a_wait(
    project, monkeypatch, reason
):
    """I05：没有能答题的界面 → 立即结构化返回（broker 不起等待）；口令不经 Agent 透传。"""
    monkeypatch.setattr(
        bridge.engine_pool,
        "get",
        _raising("script_needs_input", extra={"prompt": "请输入阈值", "reason": reason}),
    )
    res = _open(project)
    body = res["structuredContent"]
    assert body["code"] == "script_needs_input"
    assert body["input"] == {
        "reason": reason,
        "secret": reason == inputbroker.REASON_SECRET_REQUIRED,
    }
    assert body["requirements"] == [
        {"kind": "runtime_input", "answer_with": None, "where": "tavotto_app", "reason": reason}
    ]
    text = res["content"][0]["text"]
    assert "Tavotto" in text
    if reason == inputbroker.REASON_SECRET_REQUIRED:
        assert "不要" in body["recovery"] and "口令" in body["recovery"]


def test_a_first_time_getpass_is_a_secret_even_though_the_reason_is_no_interactive_client(
    project, monkeypatch
):
    """#818 r4220889705：首次 getpass 的 reason 是 no_interactive_client（secret_required 只在重放已记录的口令时用）；
    引擎带出的读取方式 `input_kind == getpass` 才是口令的凭据——input.secret 为真并带「不要把口令交给 Agent」。"""
    monkeypatch.setattr(
        bridge.engine_pool,
        "get",
        _raising(
            "script_needs_input",
            extra={
                "prompt": "Password:",
                "reason": inputbroker.REASON_NO_CLIENT,
                "input_kind": "getpass",
            },
        ),
    )
    body = _open(project)["structuredContent"]
    assert body["input"] == {"reason": inputbroker.REASON_NO_CLIENT, "secret": True}
    assert "不要让用户把口令发给你" in body["recovery"]


def test_a_plain_input_stays_non_secret_and_carries_no_warning(project, monkeypatch):
    monkeypatch.setattr(
        bridge.engine_pool,
        "get",
        _raising(
            "script_needs_input",
            extra={
                "prompt": "阈值",
                "reason": inputbroker.REASON_NO_CLIENT,
                "input_kind": "input",
            },
        ),
    )
    body = _open(project)["structuredContent"]
    assert body["input"] == {"reason": inputbroker.REASON_NO_CLIENT, "secret": False}
    assert "口令" not in body["recovery"]


def test_missing_arguments_carry_a_read_only_schema_summary_and_the_argv_answer(
    project, monkeypatch
):
    monkeypatch.setattr(
        bridge.engine_pool,
        "get",
        _raising(
            "script_needs_arguments", extra={"exit_code": 2, "parse_kind": "missing_required"}
        ),
    )
    body = _open(project)["structuredContent"]
    assert body["requirements"] == [
        {"kind": "script_arguments", "answer_with": "argv", "where": "this_tool"}
    ]
    args = body["arguments"]
    assert args["parse_kind"] == "missing_required"
    assert args["schema"]["status"] == "complete"
    flags = {a["flags"][0]: a for a in args["schema"]["arguments"]}
    assert flags["--freq"]["required"] is True and flags["--mode"]["choices"] == ["a", "b"]
    assert "argv" in body["recovery"] and "不要猜" in body["recovery"]


def test_environment_confirmation_lists_candidates_and_the_adopt_answer(project, monkeypatch):
    """T05 的确认模式：MCP 拿到候选（建议是纯读）与「怎么答」；Agent 不替用户点。"""
    from tavotto.engine import envadvice, projectenv

    rec = {
        "version": 1,
        "decision": {
            "consent": "none",
            "locked_by": None,
            "needs_decision": True,
            "current_id": None,
        },
        "recommended_id": "p_venv",
        "candidates": [
            {
                "id": "p_venv",
                "label": "project_hint",
                "name": ".venv",
                "sources": ["project_venv"],
                "scope": "project",
                "python_relative": ".venv/bin/python",
                "generation": "g1",
                "status": "unchecked",
                "checked": False,
                "health": None,
                "current": False,
            }
        ],
    }
    monkeypatch.setattr(envadvice, "recommend", lambda root, script=None: rec)
    monkeypatch.setattr(
        bridge.engine_pool,
        "get",
        _raising(
            "missing_dependency",
            module="scipy",
            project_env={"code": projectenv.ERROR_CONFIRMATION_REQUIRED, "venv": "/x/.venv"},
        ),
    )
    body = _open(project)["structuredContent"]
    assert body["environment"]["recommended_id"] == "p_venv"
    assert body["environment"]["candidates"][0] == {
        "id": "p_venv",
        "label": "project_hint",
        "status": "unchecked",
        "scope": "project",
        "python_relative": ".venv/bin/python",
        "generation": "g1",
    }
    assert {
        "kind": "environment_choice",
        "answer_with": "adopt_environment",
        "where": "this_tool",
    } in body["requirements"]
    assert "/x/.venv" not in json.dumps(body, ensure_ascii=False)


def test_adopt_environment_goes_through_the_one_adoption_service_then_opens(
    project, pool, monkeypatch
):
    from tavotto.engine import envadvice

    calls: list = []

    def adopt(root, script, candidate, *, expected_generation="", module=""):
        calls.append((root, script, candidate, expected_generation))
        assert pool.runs == []  # 先采用，再开图
        return {"ok": True, "python_version": "3.12.1"}

    monkeypatch.setattr(envadvice, "adopt_candidate", adopt)
    body = _open(project, adopt_environment="p_venv", expected_environment_generation="g1")[
        "structuredContent"
    ]
    assert calls == [(str(project), "fig1.py", "p_venv", "g1")]
    assert body["ok"] is True and body["adopted_environment"] == {
        "candidate": "p_venv",
        "python_version": "3.12.1",
    }


def test_a_refused_adoption_never_opens_the_figure(project, pool, monkeypatch):
    from tavotto.engine import envadvice

    def adopt(*a, **k):
        raise envadvice.AdoptionRefused("被重建过", code="environment_changed", status=409)

    monkeypatch.setattr(envadvice, "adopt_candidate", adopt)
    body = _open(project, adopt_environment="p_venv", expected_environment_generation="g0")[
        "structuredContent"
    ]
    assert body["code"] == "environment_changed"
    assert pool.runs == []


def test_adopt_environment_on_an_old_engine_is_refused(project, pool, old_engine):
    body = _open(project, adopt_environment="p_venv", expected_environment_generation="g1")[
        "structuredContent"
    ]
    assert body["code"] == "engine_capability_missing"
    assert body["capability"] == "environment-adoption"
    assert pool.runs == []


def test_existing_answers_point_at_their_own_parameters(project, monkeypatch):
    """既有的两种「需要输入」也进同一张待办表（只加索引，原载荷不变）。"""
    from tavotto.engine import workdir

    monkeypatch.setattr(
        bridge.engine_pool,
        "get",
        _raising(
            workdir.ERROR_CONFIRMATION_REQUIRED, confirmation={"options": [], "recommended": None}
        ),
    )
    body = _open(project)["structuredContent"]
    assert body["confirmation"] == {"options": [], "recommended": None}
    assert body["requirements"] == [
        {"kind": "workdir", "answer_with": "workdir", "where": "this_tool"}
    ]


# --------------------------------------------------------------------- 有界错误
def test_a_huge_open_error_stays_within_the_encoded_budget_and_keeps_the_answer(
    project, monkeypatch
):
    """C21：巨大的 traceback / 待办载荷量的是编码后的整个 CallToolResult；code、待办与怎么答都留着，截了就说。"""
    monkeypatch.setattr(
        bridge.engine_pool,
        "get",
        _raising(
            "script_needs_input",
            extra={"prompt": "问" * 10, "reason": inputbroker.REASON_NO_CLIENT},
            traceback_text="Traceback\n" + "行" * 3_000_000,
            missing_input={
                "requested": "x",
                "others": [{"path": "数据" * 50} for _ in range(5000)],
            },
        ),
    )
    res = _open(project)
    assert _encoded(res) <= server.ERROR_RESULT_BUDGET_BYTES
    body = res["structuredContent"]
    assert body["ok"] is False and body["code"] == "script_needs_input"
    kinds = [r["kind"] for r in body["requirements"]]
    assert kinds == ["input_location", "runtime_input"]
    assert body["input"]["reason"] == inputbroker.REASON_NO_CLIENT
    assert body["elided"]["reason"] == "error_budget"
    assert "traceback" in body["elided"]["fields"]
    assert body["elided"]["budget_bytes"] == server.ERROR_RESULT_BUDGET_BYTES


def test_errors_within_the_budget_are_returned_unchanged(project, monkeypatch):
    exc = bridge.BridgeError("找不到", code="not_found", recovery="换个路径")

    def failed(args):
        raise exc

    monkeypatch.setitem(server.HANDLERS, "tavotto_open_figure", failed)
    res = server.call_tool("tavotto_open_figure", {})
    assert res == {
        "isError": True,
        "content": [{"type": "text", "text": "找不到\n下一步：换个路径"}],
        "structuredContent": exc.payload(),
    }


def test_optional_engine_modules_never_raise_the_plugins_minimum_engine():
    """可选模块（比插件最低引擎版本新）只经 `_optional_engine` 取：不进必需 import 集（那会把最低版本抬到今天），
    桥里也没有绕过它的第二条 import 路径。"""
    import ast
    import importlib.util

    src = (ROOT / "codex-plugin" / "mcp" / "tavotto_mcp" / "bridge.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    required = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "tavotto.engine":
            required |= {a.name for a in node.names}
    assert not (set(bridge.OPTIONAL_ENGINE_MODULES) & required)
    spec = importlib.util.spec_from_file_location(
        "make_plugin_manifest", ROOT / "scripts" / "make_plugin_manifest.py"
    )
    manifest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manifest)
    assert not (set(bridge.OPTIONAL_ENGINE_MODULES) & set(manifest.BRIDGE_IMPORTS_AT_MIN))
    named = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "_optional_engine"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert named == set(bridge.OPTIONAL_ENGINE_MODULES)
    dynamic = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "import_module"
    ]
    assert len(dynamic) == 1  # 只有 `_optional_engine` 自己那一处
    with pytest.raises(ValueError):
        bridge._optional_engine("pool")


# ------------------------------------------------- #818 评审线程：采用必须绑环境代 / argv 不越出工作区
@pytest.mark.parametrize(
    "args",
    [
        {"adopt_environment": "p_venv"},
        {"expected_environment_generation": "g1"},
    ],
    ids=["candidate_without_generation", "generation_without_candidate"],
)
def test_adoption_requires_candidate_and_generation_together(project, pool, monkeypatch, args):
    """候选 id 与环境代是一份绑定的回答：缺环境代会让 `adopt_candidate` 跳过比对（采用用户没看过的那一代）。"""
    from tavotto.engine import envadvice

    monkeypatch.setattr(
        envadvice, "adopt_candidate", lambda *a, **k: pytest.fail("不该走到采用服务")
    )
    with pytest.raises(rpc.RpcError) as exc:
        _open(project, **args)
    assert exc.value.code == rpc.INVALID_PARAMS
    assert pool.runs == []


def test_bridge_refuses_an_empty_generation_even_when_called_directly(project, pool, monkeypatch):
    from tavotto.engine import envadvice

    monkeypatch.setattr(
        envadvice, "adopt_candidate", lambda *a, **k: pytest.fail("不该走到采用服务")
    )
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.open_figure(
            str(project), adopt_environment="p_venv", expected_environment_generation=""
        )
    assert exc.value.code == "environment_generation_required"
    assert pool.runs == []


@pytest.mark.parametrize(
    "shape", ["bare", "equals", "short_attached", "tilde", "dotdot", "dotdot_equals", "root"]
)
def test_argv_paths_outside_the_approved_workspace_are_refused(
    project, pool, tmp_path_factory, shape
):
    """argv 会原样成为 sys.argv：`FileType('w')` 一类选项能据此截断项目外的文件。指到根之外一律拒，
    脚本不执行、不登记配置。"""
    outside = str(tmp_path_factory.mktemp("outside") / "victim.txt")
    token = {
        "bare": outside,
        "equals": f"--out={outside}",
        "short_attached": f"-o{outside}",
        "tilde": "~/victim.txt",
        "dotdot": "../../../../../../etc/passwd",
        "dotdot_equals": "--out=../../../x",
        "root": "/",
    }[shape]
    before = runconfig.configs_of(str(project), "fig1.py")
    body = _open(project, argv=["--freq", "1", "--out", token])["structuredContent"]
    assert body["code"] == "argv_path_out_of_scope"
    assert pool.runs == []
    assert runconfig.configs_of(str(project), "fig1.py") == before


def test_argv_paths_inside_the_workspace_and_plain_values_still_pass(project, pool):
    inside = str(project / "out.csv")
    ok = _open(project, argv=["--freq", "2", "--out", inside, "--mode=a", "sub/dir.txt", "-1"])
    assert ok["structuredContent"].get("ok") is True
    assert pool.runs and pool.runs[-1] is not None


@pytest.fixture
def project_cwd(project, monkeypatch):
    """worker 在脚本目录里跑（`workdir=project`）：相对路径落在真实目录里，符号链接会被跟随。"""
    from tavotto.engine import workdir

    monkeypatch.setattr(bridge.engine_workdir, "mode_for", lambda _p: workdir.MODE_PROJECT)
    return project


@pytest.mark.parametrize("shape", ["bare", "equals", "short_attached", "dir_link"])
def test_relative_argv_escaping_through_a_symlink_is_refused(
    project_cwd, pool, tmp_path_factory, shape
):
    """cwd 里的 `escape -> /outside`：`escape/victim.txt` 没有 `..`，但会截断项目外的文件（r4220778822）。"""
    outside = tmp_path_factory.mktemp("outside")
    (outside / "victim.txt").write_text("keep", encoding="utf-8")
    try:
        os.symlink(outside, project_cwd / "escape", target_is_directory=True)
        os.symlink(outside / "victim.txt", project_cwd / "victim_link.txt")
    except OSError:
        pytest.skip("本机不能建符号链接")
    token = {
        "bare": "escape/victim.txt",
        "equals": "--out=escape/new.txt",
        "short_attached": "-oescape/victim.txt",
        "dir_link": "victim_link.txt",
    }[shape]
    before = runconfig.configs_of(str(project_cwd), "fig1.py")
    body = _open(project_cwd, argv=["--freq", "1", "--out", token])["structuredContent"]
    assert body["code"] == "argv_path_out_of_scope"
    assert pool.runs == []
    assert runconfig.configs_of(str(project_cwd), "fig1.py") == before
    assert (outside / "victim.txt").read_text(encoding="utf-8") == "keep"


def test_relative_argv_inside_the_project_is_allowed_in_the_run_cwd(project_cwd, pool):
    (project_cwd / "sub").mkdir()
    ok = _open(
        project_cwd,
        argv=["--freq", "2", "sub/out.csv", "--out=new/dir/x.txt", "-1", "../figures/y.txt"],
    )
    assert ok["structuredContent"].get("ok") is True
    assert pool.runs and pool.runs[-1] is not None


# ---------------------------------------------- #818 第三轮：登记后链接改指根外 / argparse @文件
def _link_escape(project_cwd, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    link = project_cwd / "later"
    inside = project_cwd / "data"
    inside.mkdir()
    try:
        os.symlink(inside, link, target_is_directory=True)
    except OSError:
        pytest.skip("本机不能建符号链接")

    def retarget():
        link.unlink()
        os.symlink(outside, link, target_is_directory=True)

    return retarget


def test_a_registered_run_config_is_rechecked_when_its_symlink_later_leaves_the_root(
    project_cwd, pool, tmp_path_factory, monkeypatch
):
    """登记时 `later/out.txt` 在根内；之后链接改指根外：用 run_config 复用被拒；恢复路径用同一个 `_recheck_run`。"""
    retarget = _link_escape(project_cwd, tmp_path_factory)
    first = _open(project_cwd, argv=["--out", "later/out.txt"])["structuredContent"]
    assert first.get("ok") is True
    rc = first["run_config"]["id"]
    runs_before = len(pool.runs)
    retarget()
    body = _open(project_cwd, run_config=rc)["structuredContent"]
    assert body["code"] == "argv_path_out_of_scope"
    assert len(pool.runs) == runs_before
    run = runconfig.selection(str(project_cwd), rc, script="fig1.py")
    with pytest.raises(bridge.BridgeError) as exc:
        bridge._recheck_run(run, str(project_cwd), "fig1.py")  # 恢复路径用的同一个复核
    assert exc.value.code == "argv_path_out_of_scope"


def test_default_run_config_is_rechecked_before_it_is_reused(project_cwd, pool, tmp_path_factory):
    retarget = _link_escape(project_cwd, tmp_path_factory)
    runconfig.set_default(
        str(project_cwd),
        "fig1.py",
        runconfig.put(str(project_cwd), "fig1.py", ["--out", "later/out.txt"]).id,
    )
    retarget()
    with pytest.raises(bridge.BridgeError) as exc:
        bridge._choose_run(str(project_cwd), "fig1.py", None, None)
    assert exc.value.code == "argv_path_out_of_scope"


@pytest.mark.parametrize(
    "token", ["@/etc/passwd", "@args.txt", "--out=@args.txt", "-o@args.txt", "@"]
)
def test_argparse_response_files_are_refused(project, pool, token):
    """`fromfile_prefix_chars='@'`：argparse 去掉 @ 读文件，文件内容还能再给越界目标。MCP 的 argv 里一律拒。"""
    before = runconfig.configs_of(str(project), "fig1.py")
    body = _open(project, argv=["--freq", "1", token])["structuredContent"]
    assert body["code"] == "argv_path_out_of_scope"
    assert pool.runs == []
    assert runconfig.configs_of(str(project), "fig1.py") == before


# ------------------------------------------------ r4221289208..31：重拉 worker 的复核 / 任意响应文件前缀
def _scope_code(project, argv):
    try:
        bridge._check_argv_scope(argv, str(project), "fig1.py")
    except bridge.BridgeError as exc:
        return exc.code
    return None


def _set_script(project, body):
    (Path(project) / "fig1.py").write_text(body, encoding="utf-8")


ARGPARSE_PCT = (
    "import argparse\n"
    "p = argparse.ArgumentParser(fromfile_prefix_chars='%+')\n"
    "p.add_argument('--freq', type=float)\n"
    "args = p.parse_args()\n"
)


@pytest.mark.parametrize(
    "token", ["%args.txt", "+args.txt", "--out=%args.txt", "-o+args.txt", "@args.txt", "%"]
)
def test_declared_response_file_prefixes_are_refused(project, pool, token):
    """脚本声明 `fromfile_prefix_chars='%+'`：以这些字符（及恒拒的 @）开头的整项 / `--x=值` / `-oVALUE` 都拒。"""
    _set_script(project, ARGPARSE_PCT)
    body = _open(project, argv=["--freq", "1", token])["structuredContent"]
    assert body["code"] == "argv_path_out_of_scope"
    assert pool.runs == []


@pytest.mark.parametrize("token", ["%x", "+1", "--k=%x", "-k+x", "$y", "~", " lead"])
@pytest.mark.parametrize(
    "script",
    [
        # 前缀不是字面量
        "import argparse\nPFX='%'\np = argparse.ArgumentParser(fromfile_prefix_chars=PFX)\np.parse_args()\n",
        # 关键字藏在 **cfg 里
        "import argparse\ncfg={}\np = argparse.ArgumentParser(**cfg)\np.parse_args()\n",
        # 没有 argparse 证据（解析器可能在别的模块里造）
        "from helper import make_parser\nmake_parser().parse_args()\n",
        # 语法错误
        "def (:\n",
        # r4221559036：别名 + **cfg
        "from argparse import ArgumentParser as AP\ncfg={'fromfile_prefix_chars':'%'}\np = AP(**cfg)\np.parse_args()\n",
        # 前缀来自变量（别名模块）
        "import argparse as ap\nv='%'\np = ap.ArgumentParser(fromfile_prefix_chars=v)\np.parse_args()\n",
        # 子类构造
        "import argparse\nclass P(argparse.ArgumentParser):\n    pass\np = P(fromfile_prefix_chars='%')\np.parse_args()\n",
        # parents=
        "import argparse\nb = argparse.ArgumentParser(add_help=False)\np = argparse.ArgumentParser(parents=[b])\np.parse_args()\n",
        # 再赋值的别名 / 位置参数 / 星号导入
        "import argparse\nQ = argparse.ArgumentParser\np = Q(fromfile_prefix_chars='%')\np.parse_args()\n",
        "import argparse\np = argparse.ArgumentParser('prog')\np.parse_args()\n",
        "from argparse import *\np = ArgumentParser(fromfile_prefix_chars='%')\np.parse_args()\n",
    ],
)
def test_unprovable_script_refuses_any_symbol_led_token(project, script, token):
    _set_script(project, script)
    assert _scope_code(project, [token]) == "argv_path_out_of_scope"


@pytest.mark.parametrize(
    "token", ["2", "-1", "--freq", "--freq=2", "-f2", "中 文", "", "a/b.csv", "./x", "_x", "--"]
)
@pytest.mark.parametrize(
    "script",
    [
        ARGPARSE_PCT.replace("'%+'", "'#'"),
        "from helper import make_parser\nmake_parser().parse_args()\n",
    ],
)
def test_ordinary_values_still_pass(project, script, token):
    _set_script(project, script)
    assert _scope_code(project, [token]) is None


def test_pure_literal_alias_script_stays_exact(project):
    """r4221559036：别名 import + 纯字面量仍可证明（精确集合），不退化成保守口径。"""
    from tavotto.engine import scriptargs

    path = Path(project) / "fig1.py"
    path.write_text(
        "from argparse import ArgumentParser as AP\n"
        "p = AP(fromfile_prefix_chars='%', description='x')\np.parse_args()\n",
        encoding="utf-8",
    )
    assert scriptargs.response_file_prefixes(path) == (frozenset("%"), True)
    path.write_text(
        "import argparse as ap\np = ap.ArgumentParser()\np.parse_args()\n", encoding="utf-8"
    )
    assert scriptargs.response_file_prefixes(path) == (frozenset(), True)
    assert _scope_code(project, ["#x"]) is None


def test_exact_prefix_set_does_not_over_reject_other_symbols(project):
    """可证明的前缀集合 = `%+`：`#x` 不在其中，放行；conservative 口径只用于说不准的脚本。"""
    _set_script(project, ARGPARSE_PCT)
    assert _scope_code(project, ["#x"]) is None
    assert _scope_code(project, ["%x"]) == "argv_path_out_of_scope"


def test_worker_respawn_rechecks_the_frozen_run(project_cwd, pool, tmp_path_factory, monkeypatch):
    """打开时在根内的相对路径，之后符号链接改指根外：worker 被淘汰 / 死亡后 `Session.acquire()` 重建前必须重查，
    一次性重放（`verify_replay`）同理；池一次都不许被调。"""
    one_shots: list = []
    monkeypatch.setattr(
        bridge.engine_pool, "one_shot", lambda *a, **k: one_shots.append(k) or pool.worker
    )
    retarget = _link_escape(project_cwd, tmp_path_factory)
    first = _open(project_cwd, argv=["--out", "later/out.txt"])["structuredContent"]
    assert first.get("ok") is True
    sid = first["session_id"]
    session = bridge.get_session(sid)
    spawned = len(pool.runs)
    assert session.acquire() is pool.worker  # 没变：照常
    assert len(pool.runs) == spawned + 1
    retarget()
    with pytest.raises(bridge.BridgeError) as exc:
        session.acquire()
    assert exc.value.code == "argv_path_out_of_scope"
    assert len(pool.runs) == spawned + 1
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.verify_replay(sid)
    assert exc.value.code == "argv_path_out_of_scope"
    assert one_shots == []


def test_bridge_has_a_single_worker_spawn_point():
    """结构性看护：`engine_pool.get` / `.one_shot` 在桥里只许出现在 `_spawn_worker` 里；
    新入口想直接调池会红，必须改走带复核的那一个函数。"""
    import ast

    tree = ast.parse(Path(bridge.__file__).read_text(encoding="utf-8"))
    hits: list[tuple[str, str]] = []

    class V(ast.NodeVisitor):
        def __init__(self):
            self.fn = [""]

        def visit_FunctionDef(self, node):
            self.fn.append(node.name)
            self.generic_visit(node)
            self.fn.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Attribute(self, node):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "engine_pool"
                and node.attr in {"get", "one_shot", "EngineWorker", "_spawn_spec"}
            ):
                hits.append((self.fn[-1], node.attr))
            self.generic_visit(node)

    V().visit(tree)
    assert {fn for fn, _ in hits} == {"_spawn_worker"}, hits
