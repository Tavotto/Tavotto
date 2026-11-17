"""失败回包的 `next`（ADR 0189 第 3 条）与 `fix_to_journal` 的冲突点名。

* 映射单元：码 → `next` 的三种形状、参数补全、显式带 next 的不覆盖、未登记码不编；
* AST 守卫：桥 / server / roots 里出现的**每个**错误码，要么有 `BUILDERS` 映射，要么在 `NO_NEXT`
  显式清单里（带理由）；反过来清单里不许有桥里根本没有的陈旧码。守卫有自检：喂一段故意漏登记的源码，必须抓得到；
* 端到端：经 `server.call_tool` 的真链路（unknown_session、journal 不认识的 fix_to_journal 前置错误、
  column_choice_required），`code` 不变、`next` 是增量；
* 冲突点名（真 matplotlib）：图幅步骤、复检、面板步骤、没修成的 skipped 都点名 rule / gid / 两个约束 / prop。

缺带科学栈的解释器时，真 matplotlib 那几条跳过（.venv 里没有 matplotlib 是常态）。
"""

from __future__ import annotations

import ast
import copy
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "codex-plugin" / "mcp"
sys.path.insert(0, str(PLUGIN))

from tavotto.engine import journals, pool as engine_pool, specfix  # noqa: E402
from tavotto_mcp import bridge, nextstep, roots, server  # noqa: E402

PKG = PLUGIN / "tavotto_mcp"


# =============================== 映射单元 ===============================
def _next(code, tool="tavotto_apply_overrides", args=None, **payload):
    return nextstep.next_for(code, tool, args or {}, {"code": code, **payload})


def test_unknown_session_reopens_with_the_project_and_stem_from_the_call():
    step = _next("unknown_session", args={"session_id": "s1", "project_path": "/p", "stem": "Fig1"})
    assert step["tool"] == "tavotto_open_figure"
    assert step["args"] == {"project_path": "/p", "stem": "Fig1"}
    assert step["why"]


def test_unknown_session_without_a_path_uses_a_placeholder_not_an_invented_value():
    step = _next("unknown_session", args={"session_id": "s1"})
    assert step["args"]["project_path"].startswith("<")


def test_journal_unknown_lists_journals_with_the_query_from_the_call():
    step = _next("journal_unknown", tool="tavotto_get_journal_facts", args={"journal_id": "natur"})
    assert step == {
        "tool": "tavotto_list_journals",
        "args": {"query": "natur"},
        "why": step["why"],
    }
    assert _next("journal_unknown", args={})["args"] == {}


def test_column_choice_and_unknown_column_ask_the_user_with_the_candidates():
    cols = [
        {"id": "single", "label": "单栏", "width_mm": 110.0},
        {"id": "double", "label": "双栏", "width_mm": 183.0},
    ]
    for code in ("column_choice_required", "unknown_column"):
        step = _next(code, tool="tavotto_fix_to_journal", columns=cols)
        assert step["action"] == "ask_user" and step["question"]
        assert len(step["options"]) == 2 and "single" in step["options"][0]


def test_stem_required_and_ambiguous_project_offer_the_candidates():
    assert _next("stem_required", stems=["A", "B"])["options"] == ["A", "B"]
    step = _next("ambiguous_project", sessions={"s2": "p2", "s1": "p1"})
    assert step["options"] == ["s1", "s2"]


def test_workspace_root_changed_asks_the_user_instead_of_guessing_a_root():
    step = _next("workspace_root_changed")
    assert step["action"] == "ask_user" and step["options"]


def test_first_open_questions_are_asked_not_answered_by_the_agent():
    step = nextstep.next_for(
        "workdir_confirmation_required",
        "tavotto_open_figure",
        {},
        {"confirmation": {"options": [{"mode": "sandbox"}, {"mode": "project"}]}},
    )
    assert step["action"] == "ask_user" and step["options"] == ["sandbox", "project"]
    dep = nextstep.next_for(
        "dependency_preparation_required",
        "tavotto_open_figure",
        {},
        {
            "dependency_preparation": {
                "targets": [{"kind": "tavotto_managed"}],
                "impact_digest": "D1",
            }
        },
    )
    assert dep["action"] == "ask_user" and dep["options"][-1] == "skip"
    assert "D1" in dep["question"]


def test_unregistered_code_falls_back_to_structured_input_only_and_never_invents():
    assert _next("some_new_worker_code") is None
    step = nextstep.next_for(
        "some_new_worker_code", "tavotto_open_figure", {}, {"missing_input": {"requested": "a.csv"}}
    )
    assert step["action"] == "ask_user"


def test_a_confirmation_without_options_gives_no_next():
    # 旧调用方 / 其它码带的 confirmation 形状五花八门：没有可选项就不编一个 ask_user
    payload = {"confirmation": {"option": "project"}, "dependency_preparation": {"targets": []}}
    assert nextstep.next_for("needs_input", "tavotto_apply_overrides", {}, payload) is None


def test_no_next_codes_give_nothing_and_a_broken_builder_never_raises():
    assert _next("journal_catalog_disabled") is None
    original = nextstep.BUILDERS["unknown_session"]
    nextstep.BUILDERS["unknown_session"] = lambda ctx: 1 / 0
    try:
        assert _next("unknown_session") is None
    finally:
        nextstep.BUILDERS["unknown_session"] = original


def test_attach_does_not_override_an_explicit_next():
    explicit = {"action": "edit_script", "why": "x"}
    payload = {"code": "unknown_session", "error": "e", "next": explicit}
    assert nextstep.attach(payload, "tavotto_apply_overrides", {})["next"] is explicit
    plain = nextstep.attach({"code": "unknown_session", "error": "e"}, "t", {})
    assert plain["next"]["tool"] == "tavotto_open_figure"
    assert "next" not in nextstep.attach({"code": "journal_offline", "error": "e"}, "t", {})


def test_every_next_has_one_of_the_three_shapes_and_a_short_body():
    samples = [
        _next("unknown_session"),
        _next("no_figure"),
        _next("column_choice_required", columns=[{"id": "a", "width_mm": 1.0}]),
        _next("bad_dpi", tool="tavotto_export", args={"dpi": -1, "formats": ["png"]}),
        _next("preflight_blocked", tool="tavotto_export", args={"session_id": "s"}),
    ]
    for step in samples:
        assert step is not None
        if "tool" in step:
            assert set(step) == {"tool", "args", "why"} and step["tool"].startswith("tavotto_")
        elif step["action"] == "edit_script":
            assert set(step) == {"action", "why"}
        else:
            assert step["action"] == "ask_user" and set(step) == {"action", "question", "options"}
        assert len(json.dumps(step, ensure_ascii=False).encode()) < 900


# =============================== AST 守卫 ===============================
def _engine_aliases(tree: ast.Module) -> dict[str, str]:
    """`from tavotto.engine import normalize as engine_normalize` → {engine_normalize: tavotto.engine.normalize}"""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "tavotto.engine":
            for a in node.names:
                out[a.asname or a.name] = f"tavotto.engine.{a.name}"
    return out


def collect_codes(source: str) -> set[str]:
    """一份源码里**字面可见**的错误码：

    * `code=` / `error_code=` 关键字（字面量、模块级字符串常量、`mod.NAME` 引擎常量、`x or "字面量"`）；
    * 形参默认值里叫 `code` / `default_code` 的；
    * `if code == / in ...` 里与 `code` 比的字面量（worker 的 passthrough 码）；
    * `fail("...")`（fix_to_journal 的结局码）的第一个参数。
    动态来源（`code=exc.code`）看不见，由它的上游那一处登记。
    """
    tree = ast.parse(source)
    consts = {
        n.targets[0].id: n.value.value
        for n in tree.body
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name)
        and isinstance(n.value, ast.Constant)
        and isinstance(n.value.value, str)
    }
    aliases = _engine_aliases(tree)
    found: set[str] = set()

    def resolve(node) -> list[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.Name) and node.id in consts:
            return [consts[node.id]]
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            mod = aliases.get(node.value.id)
            if mod:
                value = getattr(importlib.import_module(mod), node.attr, None)
                assert isinstance(value, str), f"{mod}.{node.attr} 解析不出字符串常量"
                return [value]
        if isinstance(node, ast.BoolOp):
            return [v for sub in node.values for v in resolve(sub)]
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return [v for sub in node.elts for v in resolve(sub)]
        return []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in ("code", "error_code", "default_code"):
                    found.update(resolve(kw.value))
            if isinstance(node.func, ast.Name) and node.func.id == "fail" and node.args:
                found.update(resolve(node.args[0]))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            pos = args.posonlyargs + args.args
            for a, d in zip(pos[len(pos) - len(args.defaults) :], args.defaults):
                if a.arg in ("code", "default_code"):
                    found.update(resolve(d))
            for a, d in zip(args.kwonlyargs, args.kw_defaults):
                if d is not None and a.arg in ("code", "default_code"):
                    found.update(resolve(d))
        elif isinstance(node, ast.Compare):
            if isinstance(node.left, ast.Name) and node.left.id == "code":
                for cmp_ in node.comparators:
                    found.update(resolve(cmp_))
    return found


def registered() -> set[str]:
    return set(nextstep.BUILDERS) | set(nextstep.NO_NEXT) | set(nextstep.FIX_EXITS)


def _all_source_codes() -> set[str]:
    codes: set[str] = set()
    for name in ("bridge.py", "server.py", "roots.py"):
        codes |= collect_codes((PKG / name).read_text(encoding="utf-8"))
    # roots 里的授权失败码是表（WORKSPACE_FAILURES），不是 code= 关键字
    codes |= set(roots.WORKSPACE_FAILURES)
    # worker 的首开「需要输入」码来自引擎常量表
    from tavotto.engine import workdir as engine_workdir

    codes |= set(engine_workdir.ERROR_CODES)
    return codes


def test_every_error_code_in_the_bridge_is_mapped_or_explicitly_has_no_next():
    codes = _all_source_codes()
    assert len(codes) > 60, "守卫收集到的码太少，收集逻辑坏了"
    missing = sorted(codes - registered())
    assert not missing, (
        f"这些错误码既没有 nextstep.BUILDERS 映射、也不在 NO_NEXT 清单里：{missing}。"
        "给它一个可行的下一步，或写进 NO_NEXT 并给出一句理由。"
    )


def test_no_stale_entries_for_codes_the_bridge_no_longer_has():
    seen = _all_source_codes()
    stale = sorted(c for c in set(nextstep.BUILDERS) | set(nextstep.NO_NEXT) if c not in seen)
    assert not stale, f"映射里有桥里已经没有的码：{stale}"


def test_mapped_and_no_next_do_not_overlap_and_every_reason_is_one_sentence():
    assert not set(nextstep.BUILDERS) & set(nextstep.NO_NEXT)
    for code, reason in nextstep.NO_NEXT.items():
        assert isinstance(reason, str) and 8 <= len(reason) <= 80 and "\n" not in reason, code


def test_guard_self_check_catches_an_unregistered_code():
    source = (
        "class BridgeError(Exception): pass\n"
        "def f():\n"
        "    raise BridgeError('x', code='brand_new_unregistered_code')\n"
        "def g(code='another_new_default'):\n"
        "    if code == 'third_new_compare':\n"
        "        pass\n"
        "    fail('fourth_new_exit')\n"
        "K = 'fifth_new_const'\n"
        "def h():\n"
        "    raise BridgeError('x', code=K)\n"
    )
    codes = collect_codes(source)
    assert codes == {
        "brand_new_unregistered_code",
        "another_new_default",
        "third_new_compare",
        "fourth_new_exit",
        "fifth_new_const",
    }
    assert sorted(codes - registered()) == sorted(codes)


def test_the_codes_the_adr_names_are_mapped_not_waived():
    for code in (
        "unknown_session",
        "column_choice_required",
        "workspace_root_changed",
        "journal_unknown",
    ):
        assert code in nextstep.BUILDERS and code not in nextstep.NO_NEXT


# =============================== 端到端（server.call_tool） ===============================
@pytest.fixture
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    bridge.reset_root_authority()
    bridge.sessions().clear()
    yield
    bridge.sessions().clear()
    bridge.reset_root_authority()


def test_unknown_session_through_call_tool_keeps_its_code_and_gains_next(_env):
    res = server.call_tool("tavotto_preflight", {"session_id": "nope", "project_path": "/x"})
    body = res["structuredContent"]
    assert res["isError"] is True and body["code"] == "unknown_session"
    assert body["next"]["tool"] == "tavotto_open_figure"
    assert body["next"]["args"] == {"project_path": "/x"}
    # 念给用户听的那一份不带机器码、也不被 next 污染
    text = json.dumps(res["content"], ensure_ascii=False)
    assert "unknown_session" not in text and '"next"' not in text


def test_journal_unknown_chain_points_at_list_journals(_env, monkeypatch):
    def boom(*a, **k):
        raise bridge.BridgeError("期刊事实目录中没有这本刊。", code="journal_unknown")

    monkeypatch.setattr(bridge, "journal_facts", boom)
    monkeypatch.setattr(bridge, "journal_facts_enabled", lambda: True)
    res = server.call_tool("tavotto_get_journal_facts", {"journal_id": "natur"})
    body = res["structuredContent"]
    assert body["code"] == "journal_unknown"
    assert body["next"]["tool"] == "tavotto_list_journals" and body["next"]["args"] == {
        "query": "natur"
    }


def test_next_survives_the_error_budget_elision(_env):
    # 很多个各 1 KiB 的字段：截断不够、要整键删——next 是保护键，必须留下
    big = {"code": "unknown_session", "error": "e", **{f"k{i}": "x" * 1024 for i in range(600)}}
    result = {
        "isError": True,
        "content": server._text("e"),
        "structuredContent": nextstep.attach(big, "t", {}),
    }
    server._fit_error_budget(result)
    body = result["structuredContent"]
    assert server._serialized_bytes(result) <= server.ERROR_RESULT_BUDGET_BYTES
    assert any(f"k{i}" not in body for i in range(600))  # 真的删过键
    assert body["next"]["tool"] == "tavotto_open_figure" and body["code"] == "unknown_session"


def test_server_instructions_tell_the_agent_to_follow_next(_env):
    src = (PKG / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    literals = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "失败结果带 next" in n.value
    ]
    assert literals, "server instructions 缺「失败结果带 next 时照它做」"


# =============================== fix_to_journal：冲突点名（真 matplotlib） ===============================
def _worker_python():
    try:
        return engine_pool.find_worker_python()
    except engine_pool.WorkerError:
        return None


WORKER_PY = _worker_python()
needs_mpl = pytest.mark.skipif(WORKER_PY is None, reason="没有带科学栈的解释器，跳过真链路用例")

# 复用 test_mcp_fix_to_journal 里已验证过的图与期刊覆盖
sys.path.insert(0, str(ROOT / "tests"))
import test_mcp_fix_to_journal as fixmod  # noqa: E402


def _make_sid(tmp_path, script: str, stem: str) -> str:
    figures = tmp_path / stem.lower()
    figures.mkdir()
    (figures / "fig.py").write_text(script, encoding="utf-8")
    (figures / "tavotto_registry.json").write_text(
        json.dumps({"scripts": {"fig.py": {"entry": "main", "cost": "light", "stems": [stem]}}}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [WORKER_PY, str(figures / "fig.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(figures),
    )
    assert proc.returncode == 0, proc.stderr
    return bridge.open_figure(str(figures), stem=stem)["session_id"]


@pytest.fixture
def sid(tmp_path, _env):
    yield _make_sid(tmp_path, fixmod.SCRIPT, "Kin")
    bridge.shutdown_all()


@pytest.fixture
def inset_sid(tmp_path, _env):
    yield _make_sid(tmp_path, fixmod.INSET_SCRIPT, "Ins")
    bridge.shutdown_all()


def _call_fix(sid, **kw) -> dict:
    res = server.call_tool("tavotto_fix_to_journal", {"session_id": sid, "previews": False, **kw})
    return res["structuredContent"]


@needs_mpl
def test_size_stage_conflict_names_rule_gid_two_constraints_and_a_next(inset_sid):
    out = _call_fix(inset_sid, journal=fixmod._journal(double=183.0), column="single")
    assert out["ok"] is False and out["stage"] == "size" and out["exit"] == "constraint_conflict"
    conflicts = out["conflicts"]
    assert conflicts, json.dumps(out, ensure_ascii=False)[:1500]
    blocking = out["detail"]["blocking"][0]
    first = conflicts[0]
    assert first["rule"] == blocking["id"] and first["gid"] == blocking["gids"][0]
    a, b = first["constraints"]
    assert "110" in a and blocking["id"] in b  # 目标图幅 vs 不得新增 / 恶化的那条规则
    assert len(conflicts) <= bridge.FIX_CONFLICTS_CAP
    assert out["next"]["action"] == "edit_script" and "figsize" in out["next"]["why"]


@needs_mpl
def test_recheck_conflict_names_the_introduced_issue_and_the_prop_that_caused_it(sid, monkeypatch):
    # 事务替身把刻度「修」到 40 pt：复检新增 font-too-large，且那条 override 就是 fontsize
    inner = fixmod._fake_transaction(
        lambda ticks: [{"gid": g, "prop": "fontsize", "value": 40} for g in ticks]
    )

    def fake(render, base, scale, profile, only):
        out = inner(render, base, scale, profile, only)
        # 替身没有 changes：补上事务真会给的那种逐条说明（规则 / gid / prop），点名才有 prop 可取
        manifest = render(out["patches"])["manifest"]
        ticks = [e["gid"] for e in manifest["elements"] if e.get("role") == "ticks"]
        out["changes"] = [
            {"rule": "font-too-small", "gid": g, "prop": "fontsize", "before": 7, "after": 40}
            for g in ticks
        ]
        return out

    monkeypatch.setattr(specfix, "transaction", fake)
    out = _call_fix(sid, journal=fixmod._journal())
    assert out["ok"] is False and out["exit"] == "constraint_conflict" and out["stage"] == "recheck"
    hit = next(c for c in out["conflicts"] if c["rule"] == "font-too-large")
    assert hit["gid"] and hit["gid"] in next(
        i["gids"] for i in out["issues"]["introduced"] if i["id"] == "font-too-large"
    )
    assert len(hit["constraints"]) == 2 and "font-too-large" in hit["constraints"][1]
    assert hit["prop"] == "fontsize"
    assert out["next"]["action"] == "edit_script"


@needs_mpl
def test_unsupported_names_the_render_warning_instead_of_only_the_exit_code(sid, monkeypatch):
    def fake(render, base, scale, profile, only):
        return {
            "ok": False,
            "exit": "unsupported",
            "warnings": ["热 worker 里上一份 override 恢复不回来：axes0.title"],
            "patches": list(base),
            "changes": [],
            "skipped": [],
            "adjustments": [],
            "unresolved": [],
            "blocking": [],
        }

    monkeypatch.setattr(specfix, "transaction", fake)
    out = _call_fix(sid, journal=fixmod._journal())
    assert out["exit"] == "unsupported" and out["stage"] == "panel"
    (item,) = out["conflicts"]
    assert (
        item["rule"] is None
        and "告警" in item["constraints"][1]
        and "axes0.title" in item["constraints"][1]
    )
    assert out["next"]["action"] == "edit_script"


@needs_mpl
def test_panel_unresolved_and_blocking_name_rule_gid_and_prop(sid, monkeypatch):
    def fake(render, base, scale, profile, only):
        gid = next(
            e["gid"] for e in render(base)["manifest"]["elements"] if e.get("role") == "ticks"
        )
        return {
            "ok": False,
            "exit": "not_resolved",
            "patches": list(base),
            "changes": [
                {"rule": "font-too-small", "gid": gid, "prop": "fontsize", "before": 7, "after": 8}
            ],
            "skipped": [],
            "adjustments": [],
            "unresolved": [{"rule": "font-too-small", "gid": gid}],
            "blocking": [{"id": "text-overlap", "gids": [gid], "bucket": "new"}],
        }

    monkeypatch.setattr(specfix, "transaction", fake)
    out = _call_fix(sid, journal=fixmod._journal())
    assert out["exit"] == "not_resolved" and out["stage"] == "panel"
    by_rule = {c["rule"]: c for c in out["conflicts"]}
    over = by_rule["text-overlap"]
    assert over["prop"] == "fontsize" and "font-too-small" in over["constraints"][0]
    assert "text-overlap" in over["constraints"][1]
    left = by_rule["font-too-small"]
    assert left["prop"] == "fontsize" and left["gid"] == over["gid"]


@needs_mpl
def test_font_unavailable_names_the_wanted_font_and_asks_the_user(sid, monkeypatch):
    # 只修字体族一条规则、而字体本机没有：什么都没改成 → font_unavailable，点名要的字体
    out = _call_fix(
        sid,
        journal=fixmod._journal(fixmod.MISSING_FONT),
        only=[{"rule": "font-family-substituted"}],
    )
    assert out["ok"] is False and out["exit"] == "font_unavailable", json.dumps(
        out, ensure_ascii=False
    )[:1200]
    hit = out["conflicts"][0]
    assert hit["rule"] == "font-family-substituted" and hit["want"] == fixmod.MISSING_FONT
    assert fixmod.MISSING_FONT in hit["constraints"][1]
    assert out["next"]["action"] == "ask_user" and fixmod.MISSING_FONT in out["next"]["question"]


@needs_mpl
def test_column_choice_required_via_call_tool_asks_with_the_columns(sid):
    out = _call_fix(sid, journal=fixmod._journal(double=125.0))
    assert out["exit"] == "column_choice_required"
    assert out["next"]["action"] == "ask_user"
    assert {o.split("（")[0] for o in out["next"]["options"]} == {"single", "double"}
    assert "conflicts" not in out  # 没冲突，只是要选


@needs_mpl
def test_success_has_neither_next_nor_conflicts(sid):
    out = _call_fix(sid, journal=fixmod._journal())
    assert out["ok"] is True and "next" not in out and "conflicts" not in out


@needs_mpl
def test_conflicts_are_short(inset_sid):
    out = _call_fix(inset_sid, journal=fixmod._journal(double=183.0), column="single")
    blob = json.dumps({"conflicts": out["conflicts"], "next": out["next"]}, ensure_ascii=False)
    assert len(blob.encode()) < 3000
    assert all(
        len(t) <= bridge.FIX_CONFLICT_TEXT_CAP for c in out["conflicts"] for t in c["constraints"]
    )


def test_report_lines_show_the_named_conflicts():
    lines = server.fix_report_lines(
        {
            "ok": False,
            "exit": "constraint_conflict",
            "stage": "recheck",
            "issues": {"introduced": []},
            "conflicts": [
                {
                    "rule": "font-too-large",
                    "gid": "ax0.tick",
                    "constraints": ["规范修复 a → 改 fontsize", "font-too-large 不得新增"],
                    "prop": "fontsize",
                }
            ],
        }
    )
    assert any(
        "font-too-large" in line and "ax0.tick" in line and "fontsize" in line and "↔" in line
        for line in lines
    )


def test_copy_of_payload_is_not_shared(_env):
    # attach 就地改的是 payload() 现造的字典，不会污染 BridgeError 自己的 extra
    exc = bridge.BridgeError("e", code="unknown_session")
    nextstep.attach(exc.payload(), "t", {})
    assert "next" not in exc.extra
    assert copy.deepcopy(exc.payload()) == exc.payload()
