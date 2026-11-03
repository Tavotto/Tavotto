"""AI 修复工具箱：`ignore_import_folder`（ADR 0154，TVD-ZCR4）以及「别再走错方向」的几条路由。

判据的主语：

* 名单：**磁盘上的项目设置**（`pathignore.paths_for`）；不看工具返回的文案。
* 同意：**注入的假 ask 看到的卡片**与「没点头 = 磁盘上一个字都没写」。
* 方向：`change_python` 在这个失败上被拒（报告里的错误码）；`pyversionfix` 的闭集不认这个码；系统提示点名新工具。
* 回滚：快照 → 回滚后名单逐项相等（含「原来没有这个键」）。
"""

from __future__ import annotations

import pytest

from tavotto.engine import config, fixsession, pathignore, pool, pyversionfix
from tavotto.engine.fixtools import REGISTRY, snapshots
from tests.test_fixtools import call, make_ctx, put
from tests.test_fixtools_change import Asker, audit_lines


@pytest.fixture
def box(tmp_path, monkeypatch):
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
    pathignore.reset()
    yield type("Box", (), {"home": home, "proj": proj, "cfg": cfg, "data": data, "tmp": tmp_path})
    pathignore.reset()


def _report(code=pool.IMPORT_FOLDER_ABI_CODE):
    return lambda: {
        "result": {
            "status": "error",
            "error": {
                "code": code,
                "params": {"folder": ".kegg_python", "abi": "3.12", "current": "3.14"},
            },
        }
    }


def _setup(box, ask="yes", failed=True, **kw):
    vendor = box.proj / ".kegg_python"
    vendor.mkdir()
    if failed:
        # 失败那一刻 Tavotto 登记的目录（`pool.foreign_abi_error` 换令牌时的同一处）
        assert pathignore.issue(str(box.proj), str(vendor))
    asker = None if ask is None else (ask if callable(ask) else Asker(ask))
    ctx = make_ctx(box, script_path=put(box.proj / "fig.py"), ask=asker, report=_report(), **kw)
    ctx.asker = asker  # type: ignore[attr-defined]
    return ctx, vendor


ARGS = {
    "summary": "The script's own folder was installed for Python 3.12 and cannot work here. Skip it?"
}


def test_registered_as_a_tavotto_tier_tool_that_takes_no_path():
    t = REGISTRY["ignore_import_folder"]
    assert t.tier == "tavotto" and t.waits_for_user
    assert set(t.schema["properties"]) == {"summary"} and t.schema["required"] == ["summary"]


@pytest.mark.parametrize("answer", [None, "no", "cancelled", "timeout", "garbage"])
def test_a_user_who_does_not_say_yes_gets_nothing_written(box, answer):
    ctx, _ = _setup(box, ask=answer)
    r = call(ctx, "ignore_import_folder", ARGS)
    assert r.is_error and r.code == "needs_approval"
    assert pathignore.paths_for(box.proj) == ()
    assert pathignore.SETTINGS_KEY not in config.project_settings(str(box.proj))
    assert snapshots.list_ids(ctx) == []  # 没获同意连快照都不打


def test_yes_records_the_folder_through_the_one_shared_function(box, monkeypatch):
    seen = []
    real = pathignore.remember

    def spy(project, path, on_changed=None):
        seen.append((str(project), path))
        return real(project, path, on_changed)

    monkeypatch.setattr(pathignore, "remember", spy)
    changed = []
    ctx, vendor = _setup(box, on_ignored_paths_changed=lambda: changed.append(1))
    r = call(ctx, "ignore_import_folder", ARGS)
    assert not r.is_error, r.content
    assert r.mutated and "try_run" in r.content and ".kegg_python" in r.content
    assert seen == [(str(box.proj), str(vendor))]
    assert pathignore.paths_for(box.proj) == (str(vendor),)
    assert changed == [1]  # 应用据此收掉这个项目的会话
    [card] = ctx.asker.cards
    assert card["kind"] == "approve" and card["tool"] == "ignore_import_folder"
    assert ".kegg_python" in card["details"] and "not changed" in card["details"]
    assert ctx.call_meta["approved_by"] == "user" and ctx.call_meta["snapshot"]
    assert (vendor).is_dir()  # 文件夹本身不动


def test_without_a_registered_failure_there_is_nothing_to_skip(box):
    ctx, _ = _setup(box, failed=False)
    r = call(ctx, "ignore_import_folder", ARGS)
    assert r.is_error and r.code == "no_foreign_folder"
    assert ctx.asker.cards == []  # 没有要忽略的东西就不打扰用户


def test_the_model_cannot_name_a_folder(box):
    """工具不收路径：模型塞进来的 `path` 不起作用，记下的只有失败时登记的那一个。"""
    ctx, vendor = _setup(box)
    assert not call(ctx, "ignore_import_folder", {**ARGS, "path": "/etc"}).is_error
    assert pathignore.paths_for(box.proj) == (str(vendor),)


def test_bad_summary_is_refused(box):
    ctx, _ = _setup(box)
    assert call(ctx, "ignore_import_folder", {"summary": " "}).code == "bad_arguments"


def test_audit_records_the_user_approval_and_the_snapshot(box):
    from tavotto.engine.fixtools import AuditLog, run_tool

    ctx, _ = _setup(box)
    audit = AuditLog(box.data / "ai" / "fix_audit.jsonl")
    run_tool(REGISTRY["ignore_import_folder"], ctx, ARGS, audit)
    [line] = audit_lines(box)
    assert (
        line["tool"] == "ignore_import_folder"
        and line["approved_by"] == "user"
        and line["snapshot"]
    )


def test_rollback_restores_the_list_exactly_and_tells_the_application(box):
    changed = []
    ctx, vendor = _setup(box, on_ignored_paths_changed=lambda: changed.append(1))
    assert not call(ctx, "ignore_import_folder", ARGS).is_error
    assert pathignore.paths_for(box.proj) == (str(vendor),)
    assert changed == [1]
    r = call(ctx, "rollback", {"snapshot": ctx.call_meta["snapshot"]})
    assert not r.is_error, r.content
    assert pathignore.paths_for(box.proj) == ()
    assert pathignore.SETTINGS_KEY not in config.project_settings(str(box.proj))  # 原来没有这个键
    assert changed == [1, 1]


# --------------------------------------------------------------------------- 别再走错方向
def test_change_python_is_refused_for_this_failure_before_anything_is_touched(box):
    ctx, _ = _setup(box)
    r = call(ctx, "change_python", {"version": "3.12"})
    assert r.is_error and r.code == "python_change_not_applicable"
    assert "ignore_import_folder" in r.content
    assert snapshots.list_ids(ctx) == []  # 没打快照、没建环境


def test_change_python_is_not_blocked_for_other_failures(box, monkeypatch):
    ctx, _ = _setup(box)
    ctx.report = _report("script_error")
    called = []
    monkeypatch.setattr(
        "tavotto.engine.fixtools.executors.run_python_changer",
        lambda c, req: called.append(req) or {"ok": False, "code": "python_change_unavailable"},
    )
    r = call(ctx, "change_python", {"version": "3.12"})
    assert called and r.code == "python_change_unavailable"


def test_pyversionfix_does_not_treat_the_new_code_as_a_version_mismatch():
    assert pool.IMPORT_FOLDER_ABI_CODE not in pyversionfix.FAILURE_CODES
    error = {
        "code": pool.IMPORT_FOLDER_ABI_CODE,
        "params": {"folder": ".kegg_python", "abi": "3.12", "current": "3.14"},
    }
    # 即使落选条目里恰好有版本事实，也不会被认成「版本不合」去换版本
    rows = [{"reason": "root_python_mismatch", "root_python": "3.12", "env_python": "3.14"}]
    assert pyversionfix.failure_from_report(script="fig.py", error=error, not_used=rows) is None
    assert pyversionfix.required_span({"code": pool.IMPORT_FOLDER_ABI_CODE}, "/p") is None


def test_the_system_prompt_routes_this_failure_to_the_new_tool():
    prompt = fixsession.SYSTEM_PROMPT if hasattr(fixsession, "SYSTEM_PROMPT") else None
    if prompt is None:  # 名字不同时，取模块里含 3g 条目的那个长字符串
        prompt = next(
            v for v in vars(fixsession).values() if isinstance(v, str) and "3g. UNREADABLE" in v
        )
    assert "3h." in prompt and "import_folder_abi_mismatch" in prompt
    assert "ignore_import_folder" in prompt and "DO NOT change" in prompt
    assert "ignore_import_folder" in REGISTRY
