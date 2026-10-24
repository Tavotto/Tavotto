"""AI 修复工具箱 D2a（ADR 0149 §七）：「改」组六个工具 · 快照与回滚 · #1361 三条修复。

判据的主语：

* 装 / 卸包：**注入的假执行器看到的调用**与假环境里最终的包集合——绝不往真实环境装包；缺省执行器只用 monkeypatch 的假 `deprepair` 钉「走的是现有路径」。
* 设置：**磁盘上的项目设置**（`config.project_settings`），回滚后逐键相等。
* 审计：**磁盘上的 jsonl**（`approved_by` / `snapshot` 字段）。
* 一切在 tmp_path 的假主目录里；密钥串运行时拼接。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from tavotto.engine import config
from tavotto.engine.fixtools import (
    FIX_SESSION_TIERS,
    REGISTRY,
    AuditLog,
    FixContext,
    change,
    executors,
    snapshots,
    trial,
)
from tests.test_fixtools import (
    _guarded_ctx,
    call,
    make_ctx,
    put,
)  # 辅助函数复用（夹具 box 在下面按同一形状重建）

CANARY = "sk-ant-" + "api03-CANARYsecretValue1234567890"


# --------------------------------------------------------------------------- 夹具
@pytest.fixture
def box(tmp_path, monkeypatch):
    """假主目录 + 假配置 / 数据目录（与 test_fixtools 的 box 同形状；从不碰真实主目录）。"""
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


class FakeEnv:
    """一个假环境：包集合 + 调用记录；注入为 installer / uninstaller / freezer。"""

    def __init__(self, **pkgs):
        self.pkgs = {"pip": "24.0", "numpy": "1.26.4", **pkgs}
        self.installs: list[list[str]] = []
        self.uninstalls: list[list[str]] = []

    def freezer(self, python):
        if not self.pkgs:
            return ""  # 全新空环境：freeze 成功但没有输出
        return "\n".join(f"{k}=={v}" for k, v in sorted(self.pkgs.items())) + "\n-e /home/x/proj\n"

    def installer(self, python, reqs):
        self.installs.append(list(reqs))
        for r in reqs:
            name, _, ver = r.partition("==")
            self.pkgs[name.lower()] = ver or "9.9"
        return {"ok": True, "code": "", "done": list(reqs)}

    def uninstaller(self, python, dists):
        self.uninstalls.append(list(dists))
        for d in dists:
            self.pkgs.pop(d, None)
        return {"ok": True, "code": "", "done": list(dists)}


class Asker:
    """记录卡片的假 ask。"""

    def __init__(self, answer="yes"):
        self.answer = answer
        self.cards: list[dict] = []

    def __call__(self, card):
        self.cards.append(card)
        return {"answer": self.answer}


def fake_python(box, name="lab") -> str:
    """一个存在但不会被当作系统 / 内置的「解释器」文件（只用于分类与 `is_file`，不会被执行）。"""
    p = box.tmp / "venvs" / name / ("Scripts" if os.name == "nt" else "bin") / "python"
    put(p, b"")
    return str(p)


def change_ctx(box, env: FakeEnv | None = None, *, ask="yes", **kw) -> FixContext:
    env = env or FakeEnv()
    py = fake_python(box)
    kw.setdefault("environments", {"e": py})
    asker = None if ask is None else (ask if callable(ask) else Asker(ask))
    ctx = make_ctx(
        box,
        installer=env.installer,
        uninstaller=env.uninstaller,
        freezer=env.freezer,
        ask=asker,
        **kw,
    )
    ctx.fake_env, ctx.asker = env, asker  # type: ignore[attr-defined]
    return ctx


def audit_lines(box) -> list[dict]:
    p = box.data / "ai" / "fix_audit.jsonl"
    return [json.loads(x) for x in p.read_text("utf-8").splitlines()] if p.is_file() else []


def project_settings(box) -> dict:
    return config.project_settings(str(box.proj))


# =========================================================================== 注册与档位
def test_change_tools_are_registered_with_the_planned_tiers():
    want = {
        "install_packages": "listed",
        "switch_execution": "tavotto",
        "change_python": "tavotto",
        "select_environment": "user_env",
        "convert_script": "tavotto",
        "rollback": "tavotto",
    }
    assert {n: REGISTRY[n].tier for n in want} == want
    assert FIX_SESSION_TIERS == {"observe", "run", "tavotto", "listed", "user_env", "outward"}


def test_a_bare_context_only_reads_so_every_change_tool_is_refused(box):
    ctx = FixContext(session_id="s1", project_root=box.proj, home=box.home)
    for name in ("install_packages", "rollback", "switch_execution"):
        assert call(ctx, name, {}).code == "tier_not_allowed"


# =========================================================================== install_packages
def test_listed_packages_install_without_asking_and_leave_a_snapshot_and_audit(box):
    ctx = change_ctx(box)
    audit = AuditLog()
    r = call(ctx, "install_packages", {"env": "e", "packages": ["seaborn", "pandas==2.2.0"]}, audit)
    assert not r.is_error, r.content
    assert ctx.fake_env.installs == [["seaborn", "pandas==2.2.0"]]
    assert ctx.asker.cards == []  # 名单内不问
    snap = ctx.call_meta["snapshot"]
    assert snap in r.content and r.mutated
    doc = json.loads(
        (box.data / "cache" / "fix_snapshots" / "s1" / f"{snap}.json").read_text("utf-8")
    )
    assert doc["envs"]["e"]["packages"]["numpy"] == "1.26.4"  # 快照存的是改动前的包
    assert "seaborn" not in doc["envs"]["e"]["packages"]
    line = audit_lines(box)[-1]
    assert (line["tool"], line["tier"], line["approved_by"], line["snapshot"]) == (
        "install_packages",
        "listed",
        "policy",
        snap,
    )
    assert line["args"]["env"] == "e" and line["args"]["packages"] == "<list>"


def test_unlisted_package_asks_and_installs_only_when_the_user_says_yes(box):
    name = "zzz-not-on-any-list"
    ctx = change_ctx(box)
    r = call(ctx, "install_packages", {"env": "e", "packages": ["seaborn", name]}, AuditLog())
    assert not r.is_error and ctx.fake_env.installs == [["seaborn", name]]
    (card,) = ctx.asker.cards
    assert card["kind"] == "approve" and card["tool"] == "install_packages"
    assert card["outside_list"] == [name] and name in card["summary"]
    assert audit_lines(box)[-1]["approved_by"] == "user"


@pytest.mark.parametrize("answer", ["no", "cancelled", "timeout", "", None])
def test_unlisted_package_without_a_yes_changes_nothing_and_is_audited_as_blocked(box, answer):
    ctx = change_ctx(box, ask=Asker(answer))
    r = call(ctx, "install_packages", {"env": "e", "packages": ["zzz-not-on-any-list"]}, AuditLog())
    assert r.is_error and r.code == "needs_approval"
    assert ctx.fake_env.installs == [] and "zzz-not-on-any-list" not in ctx.fake_env.pkgs
    assert audit_lines(box)[-1]["approved_by"] == "blocked"
    assert not (box.data / "cache" / "fix_snapshots").exists()  # 没批准就没动，也没快照


def test_no_ask_hook_means_not_approved(box):
    ctx = change_ctx(box, ask=None)
    r = call(ctx, "install_packages", {"env": "e", "packages": ["zzz-not-on-any-list"]})
    assert r.code == "needs_approval" and ctx.fake_env.installs == []


def test_a_crashing_ask_is_not_consent(box):
    def boom(card):
        raise RuntimeError("ui gone")

    ctx = change_ctx(box, ask=boom)
    r = call(ctx, "install_packages", {"env": "e", "packages": ["zzz-not-on-any-list"]})
    assert r.code == "needs_approval" and ctx.fake_env.installs == []


@pytest.mark.parametrize(
    "bad",
    [
        "-r reqs.txt",
        "--index-url=http://x",
        "https://x/y.whl",
        "../pkg",
        "pkg[extra]",
        "a b",
        "x;y",
        "",
    ],
)
def test_requirement_shapes_outside_the_narrow_grammar_never_reach_the_installer(box, bad):
    ctx = change_ctx(box)
    r = call(ctx, "install_packages", {"env": "e", "packages": [bad]})
    assert r.is_error and r.code == "bad_requirement" and ctx.fake_env.installs == []
    assert ctx.asker.cards == []


def test_unknown_env_and_bad_package_lists(box):
    ctx = change_ctx(box)
    assert (
        call(ctx, "install_packages", {"env": "nope", "packages": ["seaborn"]}).code
        == "env_unknown"
    )
    for pk in (None, [], "seaborn", [1], ["x"] * (change.PACKAGES_MAX + 1)):
        assert call(ctx, "install_packages", {"env": "e", "packages": pk}).code == "bad_arguments"
    assert ctx.fake_env.installs == []


def test_system_python_is_never_installed_into_even_with_consent(box, monkeypatch):
    # 「是不是系统自带 Python」按平台判（`convertdeps._is_os_python`）；这里钉 change.py 的护栏本身，与平台无关
    from tavotto.engine import convertdeps

    system_py = str(box.home / "system" / "python3")
    monkeypatch.setattr(convertdeps, "_is_os_python", lambda python: python == system_py)
    ctx = change_ctx(box, environments={"e": system_py})
    r = call(ctx, "install_packages", {"env": "e", "packages": ["seaborn"]})
    assert r.is_error and r.code == "env_not_installable"
    assert ctx.fake_env.installs == [] and ctx.asker.cards == []


def test_installer_failure_is_reported_with_the_snapshot_to_roll_back_to(box):
    ctx = change_ctx(box)
    ctx.installer = lambda python, reqs: {"ok": False, "code": "pip_failed", "done": ["a"]}
    r = call(ctx, "install_packages", {"env": "e", "packages": ["seaborn"]})
    assert r.is_error and r.code == "pip_failed" and "snap_" in r.content and "a" in r.content


def test_a_raising_installer_does_not_crash_the_session(box):
    ctx = change_ctx(box)

    def boom(python, reqs):
        raise OSError("disk")

    ctx.installer = boom
    r = call(ctx, "install_packages", {"env": "e", "packages": ["seaborn"]})
    assert r.is_error and r.code == "executor_OSError"


def test_no_snapshot_no_change(box, monkeypatch):
    ctx = change_ctx(box)
    monkeypatch.setattr(snapshots, "take", lambda *a, **k: None)
    r = call(ctx, "install_packages", {"env": "e", "packages": ["seaborn"]})
    assert r.code == "snapshot_failed" and ctx.fake_env.installs == []


# --------------------------------------------------------------------------- 缺省执行器 = 现有路径
def test_default_installer_goes_through_the_existing_pip_path_under_the_env_lock(monkeypatch):
    from contextlib import contextmanager

    from tavotto.engine import deprepair, pool

    log: list = []

    @contextmanager
    def lock(key, python="", **kw):
        log.append(("lock", key, python))
        yield
        log.append(("unlock",))

    def pip(python, req, cancel, on_log, **kw):
        log.append(("pip", python, req))
        return ("", "")

    monkeypatch.setattr(pool, "mutating_environment", lock)
    monkeypatch.setattr(deprepair, "_pip_install", pip)
    out = executors._default_installer("/x/bin/python", ["a==1", "b"])
    assert out == {"ok": True, "code": "", "done": ["a==1", "b"]}
    assert [e[0] for e in log] == ["lock", "pip", "pip", "unlock"]
    assert log[1][2] == "a==1" and log[0][2] == "/x/bin/python"


def test_default_installer_stops_at_the_first_failure_and_maps_a_busy_environment(monkeypatch):
    from contextlib import contextmanager

    from tavotto.engine import deprepair, pool

    @contextmanager
    def lock(key, python="", **kw):
        yield

    monkeypatch.setattr(pool, "mutating_environment", lock)
    monkeypatch.setattr(
        deprepair,
        "_pip_install",
        lambda p, r, c, log, **k: ("pip_failed", "") if r == "b" else ("", ""),
    )
    out = executors._default_installer("/x/python", ["a", "b", "c"])
    assert out == {"ok": False, "code": "pip_failed", "done": ["a"]}

    @contextmanager
    def busy(key, python="", **kw):
        raise pool.EnvironmentBusy("busy")
        yield

    monkeypatch.setattr(pool, "mutating_environment", busy)
    assert executors._default_installer("/x/python", ["a"])["code"] == "env_busy"


def test_default_uninstaller_and_freezer_use_the_existing_helpers(monkeypatch):
    from contextlib import contextmanager

    from tavotto.engine import deprepair, pool

    seen: list = []

    @contextmanager
    def lock(key, python="", **kw):
        yield

    monkeypatch.setattr(pool, "mutating_environment", lock)
    monkeypatch.setattr(
        deprepair, "_pip_uninstall", lambda p, d, c, log: seen.append(d) or ("", "")
    )
    monkeypatch.setattr(deprepair, "_run", lambda argv, timeout: (0, "numpy==1.0\n"))
    assert executors._default_uninstaller("/x/python", ["a", "b"])["done"] == [
        "a",
        "b",
    ] and seen == [
        "a",
        "b",
    ]
    assert snapshots.default_freezer("/x/python") == "numpy==1.0\n"
    # 成功但输出为空（全新空环境）= ""；退出码非 0 = None——两者不能混
    monkeypatch.setattr(deprepair, "_run", lambda argv, timeout: (0, ""))
    assert snapshots.default_freezer("/x/python") == ""
    monkeypatch.setattr(deprepair, "_run", lambda argv, timeout: (1, "boom"))
    assert snapshots.default_freezer("/x/python") is None


# =========================================================================== 快照与回滚
def test_rollback_restores_packages_by_the_freeze_difference(box):
    ctx = change_ctx(box)
    r = call(ctx, "install_packages", {"env": "e", "packages": ["seaborn", "numpy==2.0.0"]})
    snap = ctx.call_meta["snapshot"]
    assert ctx.fake_env.pkgs["seaborn"] == "9.9" and ctx.fake_env.pkgs["numpy"] == "2.0.0", (
        r.content
    )
    r = call(ctx, "rollback", {"snapshot": snap}, AuditLog())
    assert not r.is_error, r.content
    assert ctx.fake_env.pkgs == {"pip": "24.0", "numpy": "1.26.4"}  # 新装的卸掉、改过版本的装回
    assert ctx.fake_env.uninstalls == [["seaborn"]] and ctx.fake_env.installs[-1] == [
        "numpy==1.26.4"
    ]
    assert audit_lines(box)[-1]["tool"] == "rollback"


def test_rollback_never_uninstalls_pip_and_friends(box):
    ctx = change_ctx(box)
    call(ctx, "install_packages", {"env": "e", "packages": ["seaborn"]})
    snap = ctx.call_meta["snapshot"]
    ctx.fake_env.pkgs.update(setuptools="70.0", wheel="0.43")  # 快照之后冒出来的
    call(ctx, "rollback", {"snapshot": snap})
    assert "setuptools" in ctx.fake_env.pkgs and "wheel" in ctx.fake_env.pkgs
    assert ctx.fake_env.uninstalls == [["seaborn"]]


def test_rollback_restores_a_removed_package(box):
    ctx = change_ctx(box)
    assert snapshots.take(ctx, "t", ["e"]) is not None
    snap = ctx.call_meta["snapshot"]
    assert "e" in snapshots.load(ctx, snap)["envs"]
    ctx.fake_env.pkgs.pop("numpy")
    assert not call(ctx, "rollback", {"snapshot": snap}).is_error
    assert ctx.fake_env.pkgs["numpy"] == "1.26.4"


def test_rollback_restores_project_settings_exactly(box):
    config.set_project_settings(
        str(box.proj), {"environment": {"python": "/old/python", "automatic": True}}
    )
    before = dict(project_settings(box))
    ctx = change_ctx(box)
    assert not call(ctx, "switch_execution", {"mode": "safe"}).is_error
    snap_a = ctx.call_meta["snapshot"]
    assert not call(ctx, "select_environment", {"env": "e"}).is_error
    snap_b = ctx.call_meta["snapshot"]
    assert project_settings(box)["environment"]["python"] != "/old/python"
    assert project_settings(box)["execution_policy"] == {"mode": "safe_only"}
    # 回到 select 之前（= safe 已设）：environment 精确回来，execution_policy 仍是 safe_only
    assert not call(ctx, "rollback", {"snapshot": snap_b}).is_error
    assert project_settings(box)["environment"] == {"python": "/old/python", "automatic": True}
    # 回到最初：execution_policy 这个键原本不存在，回滚后也不存在
    assert not call(ctx, "rollback", {"snapshot": snap_a}).is_error
    assert project_settings(box) == before


def test_rollback_rejects_unknown_foreign_and_path_like_ids(box):
    ctx = change_ctx(box)
    call(ctx, "switch_execution", {"mode": "safe"})
    snap = ctx.call_meta["snapshot"]
    for bad in ("../x", "snap_x", "", None, 3, snap + "/..", "C:\\x"):
        r = call(ctx, "rollback", {"snapshot": bad})
        assert r.is_error and r.code == "snapshot_unknown"
    other = FixContext.for_fix_session("other-session", box.proj, home=box.home)
    assert call(other, "rollback", {"snapshot": snap}).code == "snapshot_unknown"  # 只认本会话
    elsewhere = box.tmp / "elsewhere"
    elsewhere.mkdir()
    ctx_b = change_ctx(box)
    ctx_b.project_root = elsewhere
    assert snapshots.load(ctx_b, snap) is None  # 别的项目的快照不在这个项目上回滚


def test_a_forged_snapshot_outside_the_session_folder_is_never_loaded(box):
    """id 里带 `..` 指到会话目录外的文件，哪怕那里恰有一份形状合格、id 自洽的 JSON，也不读。"""
    ctx = change_ctx(box)
    call(ctx, "switch_execution", {"mode": "safe"})
    root = snapshots.snapshot_root("s1")
    forged = {
        "id": "../forged",
        "project": os.path.normcase(os.path.abspath(str(box.proj))),
        "settings": {"execution_policy": {"mode": "compatible"}},
        "envs": {},
    }
    put(root.parent / "forged.json", json.dumps(forged).encode())
    r = call(ctx, "rollback", {"snapshot": "../forged"})
    assert r.code == "snapshot_unknown"
    assert project_settings(box)["execution_policy"]["mode"] == "safe_only"


def test_rollback_reports_when_the_package_list_was_unavailable(box):
    ctx = change_ctx(box)
    ctx.freezer = lambda python: None
    call(ctx, "install_packages", {"env": "e", "packages": ["seaborn"]})
    snap = ctx.call_meta["snapshot"]
    r = call(ctx, "rollback", {"snapshot": snap})
    assert r.is_error and r.code == "rollback_incomplete" and "no package list" in r.content


def test_snapshots_keep_only_the_newest_per_session(box, monkeypatch):
    monkeypatch.setattr(snapshots, "KEEP_PER_SESSION", 3)
    ctx = change_ctx(box)
    ids = []
    for _ in range(5):
        assert snapshots.take(ctx, "t") is not None
        ids.append(ctx.call_meta["snapshot"])
    assert len(snapshots.list_ids(ctx)) == 3


# =========================================================================== switch_execution
def test_switch_execution_writes_the_project_policy_and_audits_the_mode(box):
    ctx = change_ctx(box)
    r = call(ctx, "switch_execution", {"mode": "safe"}, AuditLog())
    assert not r.is_error and project_settings(box)["execution_policy"] == {"mode": "safe_only"}
    assert call(ctx, "switch_execution", {"mode": "safe"}).summary == "no change"
    assert ctx.asker.cards == []  # tavotto 档：自己的设置，不问
    line = audit_lines(box)[-1]
    assert line["args"] == {"mode": "safe"} and line["snapshot"] and line["tier"] == "tavotto"


def test_loosening_a_users_safe_only_choice_asks_first(box):
    from tavotto.engine import execpolicy

    execpolicy.set_mode(str(box.proj), "safe_only")  # 用户明确选过
    ctx = change_ctx(box, ask="no")
    r = call(ctx, "switch_execution", {"mode": "native"})
    assert (
        r.code == "needs_approval"
        and project_settings(box)["execution_policy"]["mode"] == "safe_only"
    )
    ctx = change_ctx(box, ask="yes")
    assert not call(ctx, "switch_execution", {"mode": "native"}).is_error
    assert project_settings(box)["execution_policy"]["mode"] == "compatible"


def test_switch_execution_rejects_other_modes(box):
    ctx = change_ctx(box)
    for m in ("yolo", None, "script", 1):
        assert call(ctx, "switch_execution", {"mode": m}).code == "bad_arguments"


# =========================================================================== change_python
def test_change_python_without_injection_uses_the_pyversionfix_executor(box, monkeypatch):
    """没注入就走缺省执行器（`pyversionfix` 的公开入口）；这里把入口换成假的：不下载、不建真实环境。"""
    from tavotto.engine import projectenv, pyversionfix

    newpy = fake_python(box, "py312")
    seen = {}

    def fake_plan(failure, project):
        seen["failure"], seen["project"] = failure, project
        return pyversionfix.PythonChangePlan(
            from_version="3.14",
            to_version="3.12",
            base=pyversionfix.BASE_SYSTEM,
            download_bytes=0,
            code=failure["code"],
            project=str(project),
            script=failure["script"],
            base_python="/sys/python3.12",
        )

    def fake_apply(plan, progress, cancel):
        seen["plan"] = plan
        return {"ok": True, "code": "", "to": plan.to_version}

    monkeypatch.setattr(pyversionfix, "plan_python_change", fake_plan)
    monkeypatch.setattr(pyversionfix, "apply_python_change", fake_apply)
    monkeypatch.setattr(projectenv, "remembered", lambda root: newpy)
    ctx = change_ctx(box)
    r = call(ctx, "change_python", {"version": "3.12"})
    assert not r.is_error, r.content
    assert seen["failure"]["requires_python"] == "==3.12.*" and seen["project"] == str(box.proj)
    assert seen["plan"].to_version == "3.12"
    new_id = r.content.split("new environment id: ")[1].split()[0]
    assert ctx.environments[new_id] == newpy and "snap_" in r.content


def test_default_python_changer_reports_a_version_it_cannot_provide(box, monkeypatch):
    from tavotto.engine import pyversionfix

    monkeypatch.setattr(pyversionfix, "plan_python_change", lambda failure, project: None)
    monkeypatch.setattr(
        pyversionfix,
        "apply_python_change",
        lambda *a, **k: pytest.fail("no plan = nothing may be applied"),
    )
    r = call(change_ctx(box), "change_python", {"version": "3.9"})
    assert r.is_error and r.code == "python_change_not_possible"


def test_default_python_changer_reports_a_failed_build(box, monkeypatch):
    from tavotto.engine import pyversionfix

    plan = pyversionfix.PythonChangePlan("", "3.12", "system", 0, "x", str(box.proj), "")
    monkeypatch.setattr(pyversionfix, "plan_python_change", lambda failure, project: plan)
    monkeypatch.setattr(
        pyversionfix,
        "apply_python_change",
        lambda p, progress, cancel: {"ok": False, "code": "python_change_failed", "to": "3.12"},
    )
    ctx = change_ctx(box)
    r = call(ctx, "change_python", {"version": "3.12"})
    assert r.is_error and r.code == "python_change_failed"
    assert "new environment id" not in r.content and list(ctx.environments) == ["e"]


def _fake_plan_factory(pyversionfix):
    def fake_plan(failure, project):
        return pyversionfix.PythonChangePlan(
            from_version="3.14",
            to_version="3.12",
            base=pyversionfix.BASE_SYSTEM,
            download_bytes=0,
            code=failure["code"],
            project=str(project),
            script=failure["script"],
            base_python="/sys/python3.12",
        )

    return fake_plan


def test_change_python_hands_the_session_cancel_event_to_pyversionfix(box, monkeypatch):
    """取消要真的传下去：缺省执行器把 `ctx.cancel`（修复会话的 `s.cancel`）交给 `apply_python_change`。"""
    import threading

    from tavotto.engine import projectenv, pyversionfix

    got = {}

    def fake_apply(plan, progress, cancel):
        got["cancel"] = cancel
        return {"ok": False, "code": "x"}

    monkeypatch.setattr(pyversionfix, "plan_python_change", _fake_plan_factory(pyversionfix))
    monkeypatch.setattr(pyversionfix, "apply_python_change", fake_apply)
    monkeypatch.setattr(projectenv, "remembered", lambda root: None)
    ctx = change_ctx(box)
    ctx.cancel = threading.Event()
    call(ctx, "change_python", {"version": "3.12"})
    assert got["cancel"] is ctx.cancel


def test_change_python_cancelled_after_planning_does_not_start_the_change(box, monkeypatch):
    """规划期间用户点了取消：不动手（不调 apply），如实报 cancelled。"""
    import threading

    from tavotto.engine import projectenv, pyversionfix

    ctx = change_ctx(box)
    ctx.cancel = threading.Event()
    plan = _fake_plan_factory(pyversionfix)

    def plan_then_cancel(failure, project):
        ctx.cancel.set()
        return plan(failure, project)

    applied = []
    monkeypatch.setattr(pyversionfix, "plan_python_change", plan_then_cancel)
    monkeypatch.setattr(
        pyversionfix, "apply_python_change", lambda *a: applied.append(a) or {"ok": True}
    )
    monkeypatch.setattr(projectenv, "remembered", lambda root: None)
    r = call(ctx, "change_python", {"version": "3.12"})
    assert r.is_error and r.code == "cancelled" and applied == []


def test_change_python_calls_the_executor_registers_the_new_env_and_snapshots(box):
    seen = []
    newpy = fake_python(box, "py312")

    def changer(req):
        seen.append(req)
        return {"ok": True, "message": "Created.", "python": newpy}

    ctx = change_ctx(box, python_changer=changer)
    r = call(ctx, "change_python", {"version": "3.12"})
    assert not r.is_error and seen == [{"version": "3.12", "project": str(box.proj)}]
    assert ctx.script_path is None  # 没有被修的脚本就不带 script
    new_id = r.content.split("new environment id: ")[1].split()[0]
    assert ctx.environments[new_id] == newpy and "snap_" in r.content


def test_change_python_passes_the_project_relative_script_to_the_executor(box):
    seen = []
    target = put(box.proj / "sub" / "plot.py", "print(1)")
    ctx = change_ctx(
        box,
        script_path=target,
        python_changer=lambda req: seen.append(req) or {"ok": False, "code": "x"},
    )
    call(ctx, "change_python", {"version": "3.12"})
    assert seen[0]["script"] == "sub/plot.py"


@pytest.mark.parametrize("bad", ["3", "3.x", "2.7", "3.12; rm", "", None, "3.123.4.5", "latest"])
def test_change_python_validates_the_version(box, bad):
    ctx = change_ctx(box, python_changer=lambda req: {"ok": True})
    assert call(ctx, "change_python", {"version": bad}).code == "bad_arguments"


def test_change_python_failure_codes_pass_through(box):
    ctx = change_ctx(
        box, python_changer=lambda req: {"ok": False, "code": "no_base_python", "message": "none"}
    )
    r = call(ctx, "change_python", {"version": "3.12"})
    assert r.is_error and r.code == "no_base_python"


# =========================================================================== select_environment
def test_select_environment_always_asks_then_remembers_for_the_project(box):
    ctx = change_ctx(box)
    py = ctx.environments["e"]
    r = call(ctx, "select_environment", {"env": "e"}, AuditLog())
    assert not r.is_error, r.content
    (card,) = ctx.asker.cards
    assert card["tool"] == "select_environment" and card["kind"] == "approve"
    rec = project_settings(box)["environment"]
    assert rec["automatic"] is False and (
        rec.get("python") == py or py.endswith(rec["python_relative"])
    )
    assert audit_lines(box)[-1]["approved_by"] == "user"


def test_select_environment_without_consent_changes_nothing(box):
    for ask in (None, "no"):
        ctx = change_ctx(box, ask=ask)
        r = call(ctx, "select_environment", {"env": "e"}, AuditLog())
        assert r.code == "needs_approval" and "environment" not in project_settings(box)
        assert audit_lines(box)[-1]["approved_by"] == "blocked"


def test_select_environment_unknown_or_vanished(box):
    ctx = change_ctx(box)
    assert call(ctx, "select_environment", {"env": "nope"}).code == "env_unknown"
    ctx.environments["gone"] = str(box.tmp / "no" / "python")
    assert call(ctx, "select_environment", {"env": "gone"}).code == "env_missing"
    assert ctx.asker.cards == []


# =========================================================================== convert_script
def _converter(content=b"import matplotlib\n", ok_=True, tamper=False, write=True, seen=None):
    def run(req):
        if seen is not None:
            seen.append(req)
        if tamper == "copy":
            Path(req["script"]).write_bytes(b"CHANGED")  # 写临时副本：原脚本不受影响
        elif tamper:
            (Path(req["project"]) / "fig.py").write_bytes(b"CHANGED")  # 误写原路径
        if write:
            Path(req["output"]).write_bytes(content)
        return {"ok": ok_, "message": "x", "code": "" if ok_ else "convert_error"}

    return run


def test_convert_script_is_unavailable_without_an_executor(box):
    put(box.proj / "fig.py", b"print(1)\n")
    ctx = change_ctx(box, script_path=box.proj / "fig.py")
    assert call(ctx, "convert_script", {}).code == "unavailable"


def test_convert_script_saves_next_to_the_original_and_never_touches_it(box):
    orig = put(box.proj / "fig.py", b"from PIL import Image\r\n")
    seen: list = []
    ctx = change_ctx(box, script_path=orig, script_converter=_converter(seen=seen))
    r = call(ctx, "convert_script", {}, AuditLog())
    assert not r.is_error, r.content
    assert orig.read_bytes() == b"from PIL import Image\r\n"
    out = box.proj / "fig_matplotlib.py"
    assert out.read_bytes() == b"import matplotlib\n" and "fig_matplotlib.py" in r.content
    assert seen[0]["output"] == str(out)
    assert (
        seen[0]["script"] != str(orig) and Path(seen[0]["script"]).name == "fig.py"
    )  # 拿到的是临时副本
    assert not Path(seen[0]["script"]).exists()  # 临时副本用完即删
    # 再转一次不覆盖上一份
    r = call(ctx, "convert_script", {"target": "fig.py"})
    assert not r.is_error and (box.proj / "fig_matplotlib_2.py").is_file() and out.is_file()
    assert audit_lines(box)[-1]["snapshot"]


def test_convert_script_fails_loudly_if_the_executor_touches_the_original(box):
    orig = put(box.proj / "fig.py", b"print(1)\n")
    ctx = change_ctx(box, script_path=orig, script_converter=_converter(tamper=True))
    r = call(ctx, "convert_script", {})
    assert r.is_error and r.code == "original_modified" and "put back" in r.content
    assert orig.read_bytes() == b"print(1)\n"  # 用执行前读到的原字节恢复了


def test_the_converter_gets_a_copy_so_writing_it_never_reaches_the_original(box):
    orig = put(box.proj / "fig.py", b"print(1)\n")
    ctx = change_ctx(box, script_path=orig, script_converter=_converter(tamper="copy"))
    r = call(ctx, "convert_script", {})
    assert not r.is_error and orig.read_bytes() == b"print(1)\n"


def test_a_failed_restore_of_the_original_is_reported_honestly(box, monkeypatch):
    orig = put(box.proj / "fig.py", b"print(1)\n")
    ctx = change_ctx(box, script_path=orig, script_converter=_converter(tamper=True))
    monkeypatch.setattr(snapshots, "restore_script", lambda path, data: False)
    r = call(ctx, "convert_script", {})
    assert r.code == "original_modified" and "NOT be put back" in r.content


def test_convert_script_failure_and_missing_output(box):
    orig = put(box.proj / "fig.py", b"print(1)\n")
    ctx = change_ctx(box, script_path=orig, script_converter=_converter(ok_=False, write=False))
    assert call(ctx, "convert_script", {}).code == "convert_error"
    ctx = change_ctx(box, script_path=orig, script_converter=_converter(write=False))
    assert call(ctx, "convert_script", {}).code == "convert_failed"
    assert orig.read_bytes() == b"print(1)\n"


def test_convert_script_stays_inside_the_project(box):
    other = put(box.home / "elsewhere" / "fig.py", b"print(1)\n")
    ctx = change_ctx(box, script_converter=_converter())
    assert call(ctx, "convert_script", {"target": str(other)}).code == "outside_project"
    link = box.proj / "fig.py"
    link.symlink_to(other)  # 项目里的链接指向项目外：解析后在项目外，也不转
    assert call(ctx, "convert_script", {"target": "fig.py"}).code == "outside_project"
    assert not (box.home / "elsewhere" / "fig_matplotlib.py").exists()
    assert call(ctx, "convert_script", {"target": "nope.py"}).code == "no_script"
    assert call(ctx, "convert_script", {}).code == "no_script"
    assert call(ctx, "convert_script", {"target": 3}).code == "bad_arguments"


# =========================================================================== #1361
@pytest.mark.skipif(os.name == "nt", reason="FIFO 只在 POSIX")
def test_try_run_does_not_hang_on_fifos_and_reports_the_skipped_special_files(box):
    put(box.proj / "s.py", "print('hi')\n")
    os.mkfifo(box.proj / "pipe.fifo")
    os.mkfifo(box.proj / "sub_fifo_target")
    (box.proj / "linked.fifo").symlink_to(box.proj / "sub_fifo_target")
    ctx = _guarded_ctx(box, "import os, sys\nsys.exit(3 if os.path.exists('pipe.fifo') else 0)\n")
    box_result: dict = {}

    def go():
        box_result["r"] = call(ctx, "try_run", {"env": "e"})

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(60)
    assert not t.is_alive(), "try_run hung on a FIFO"
    r = box_result["r"]
    assert not r.is_error, r.content
    assert "exit code 0" in r.content  # 副本里根本没有 FIFO
    assert "3 special file(s)" in r.content
    assert "pipe.fifo" in r.content and "linked.fifo" in r.content


@pytest.mark.skipif(os.name == "nt", reason="FIFO 只在 POSIX")
def test_copy_project_never_opens_a_special_file(box):
    put(box.proj / "s.py", "print(1)\n")
    os.mkfifo(box.proj / "p")
    dest = box.tmp / "dest"
    skipped: dict = {}
    done: dict = {}

    def go():
        done["r"] = trial._copy_project(
            box.proj, box.proj / "s.py", dest, make_ctx(box).scope, skipped
        )

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(30)
    assert not t.is_alive()
    assert skipped["special"] == ["p"] and sorted(x.name for x in dest.iterdir()) == ["s.py"]


@pytest.mark.skipif(os.name == "nt", reason="FIFO 只在 POSIX")
def test_a_file_swapped_for_a_fifo_after_enumeration_is_still_not_opened(box, monkeypatch):
    """枚举时是普通文件、复制前被换成 FIFO：复制前的再核一次挡住它。"""
    put(box.proj / "s.py", "print(1)\n")
    victim = put(box.proj / "late.txt", "x")
    real_copy2 = trial.shutil.copy2
    opened: list = []

    real_stat = os.stat

    def swap_then_stat(p, *a, **k):
        if str(p) == str(victim) and not opened:
            opened.append(1)
            os.remove(victim)
            os.mkfifo(victim)
        return real_stat(p, *a, **k)

    monkeypatch.setattr(trial.os, "stat", swap_then_stat)
    monkeypatch.setattr(trial.shutil, "copy2", lambda *a, **k: real_copy2(*a, **k))
    skipped: dict = {}
    done: dict = {}
    t = threading.Thread(
        target=lambda: done.setdefault(
            "r",
            trial._copy_project(
                box.proj, box.proj / "s.py", box.tmp / "d2", make_ctx(box).scope, skipped
            ),
        ),
        daemon=True,
    )
    t.start()
    t.join(30)
    assert not t.is_alive() and "late.txt" in skipped["special"]


def test_list_environments_reports_truncation_and_registers_only_what_it_shows(box):
    entries = [
        {
            "python": str(box.tmp / "envs" / f"e{i:02d}" / "bin" / "python"),
            "source": "conda",
            "label": f"env{i:02d}",
        }
        for i in range(45)
    ]
    ctx = change_ctx(box, environment_source=lambda: entries, environments={})
    r = call(ctx, "list_environments", {})
    assert not r.is_error
    head, _, note = r.content.rpartition("\n[")
    rows = json.loads(head)
    assert len(rows) == 30 and "showing 30 of 45" in note and "match" in note
    assert len(ctx.environments) == 30  # 没显示的不登记
    assert "of 45" in r.summary


def test_list_environments_puts_the_projects_own_environment_first(box):
    mine = box.tmp / "proj-env" / "bin" / "python"
    entries = [
        {
            "python": str(box.tmp / "envs" / f"e{i:02d}" / "bin" / "python"),
            "source": "conda",
            "label": f"env{i:02d}",
        }
        for i in range(40)
    ] + [{"python": str(mine), "source": "conda", "label": "the-one-in-use"}]
    ctx = change_ctx(
        box,
        environment_source=lambda: entries,
        environments={},
        env_prefixes=[box.tmp / "proj-env"],
    )
    r = call(ctx, "list_environments", {})
    rows = json.loads(r.content.rpartition("\n[")[0])
    assert rows[0]["label"] == "the-one-in-use"


def test_list_environments_match_filters_by_name_and_registers_beyond_the_first_30(box):
    entries = [
        {
            "python": str(box.tmp / "envs" / f"e{i:02d}" / "bin" / "python"),
            "source": "conda",
            "label": f"env{i:02d}",
        }
        for i in range(45)
    ]
    entries.append(
        {
            "python": str(box.tmp / "x" / "lab-special" / "bin" / "python"),
            "source": "pyenv",
            "label": "last",
        }
    )
    ctx = change_ctx(box, environment_source=lambda: entries, environments={})
    r = call(ctx, "list_environments", {"match": "lab-special"})  # 路径片段也能筛
    rows = json.loads(r.content.rpartition("\n[")[0])
    assert [x["label"] for x in rows] == ["last"] and rows[0]["id"] in ctx.environments
    assert str(box.tmp) not in r.content  # 路径只用来筛，不返回
    r = call(ctx, "list_environments", {"match": "ENV4"})  # 名字，大小写不敏感
    assert {x["label"] for x in json.loads(r.content.rpartition("\n[")[0])} == {
        f"env4{i}" for i in range(5)
    }
    assert (
        "no environments"
        not in call(ctx, "list_environments", {"match": "nothing-like-this"}).content
    )
    assert (
        "none of the 46" in call(ctx, "list_environments", {"match": "nothing-like-this"}).content
    )
    assert call(ctx, "list_environments", {"match": 3}).code == "bad_arguments"


def test_symlink_target_outside_the_project_is_recorded_in_reads(box):
    put(box.home / "shared" / "data.csv", "1,2\n")
    put(box.proj / "inside.csv", "9\n")
    (box.proj / "data.csv").symlink_to(Path("..") / "shared" / "data.csv")
    (box.proj / "alias.csv").symlink_to(box.proj / "inside.csv")  # 项目内的目标不记
    ctx = _guarded_ctx(box, "print(1)\n")
    r = call(ctx, "try_run", {"env": "e"})
    assert not r.is_error, r.content
    paths = [x["path"] for x in ctx.reads if x["tool"] == "try_run"]
    assert paths == [os.path.realpath(box.home / "shared" / "data.csv")]


def test_unreadable_link_targets_are_not_recorded_as_reads(box):
    put(box.home / ".ssh" / "x", "PRIVATE")
    (box.proj / "k.txt").symlink_to(box.home / ".ssh" / "x")
    ctx = _guarded_ctx(box, "print(1)\n")
    call(ctx, "try_run", {"env": "e"})
    assert ctx.reads == []


# =========================================================================== P1：空环境的 freeze 是有效快照
def test_an_empty_environment_snapshot_lets_rollback_remove_the_first_package(box):
    env = FakeEnv()
    env.pkgs.clear()
    ctx = change_ctx(box, env)
    assert not call(ctx, "install_packages", {"env": "e", "packages": ["seaborn"]}).is_error
    snap = ctx.call_meta["snapshot"]
    doc = snapshots.load(ctx, snap)
    assert doc["envs"]["e"]["freeze_available"] is True and doc["envs"]["e"]["packages"] == {}
    assert env.pkgs == {"seaborn": "9.9"}
    r = call(ctx, "rollback", {"snapshot": snap})
    assert not r.is_error, r.content
    assert env.pkgs == {} and env.uninstalls == [["seaborn"]]


def test_a_failed_freeze_is_still_unavailable_not_empty(box):
    ctx = change_ctx(box)
    ctx.freezer = lambda python: None
    call(ctx, "install_packages", {"env": "e", "packages": ["seaborn"]})
    doc = snapshots.load(ctx, ctx.call_meta["snapshot"])
    assert doc["envs"]["e"]["freeze_available"] is False


# =========================================================================== convert_script：持锁的基线与副本清理
def _ui_save(path: Path, data: bytes, done: dict) -> threading.Thread:
    """界面保存脚本走的写入口：持脚本锁 + write_script（与 scriptbackup.replace 同一把锁）。"""
    from tavotto.engine import scriptlock

    def go():
        with scriptlock.script_guard(path):
            scriptlock.write_script(path, data)
        done["at"] = __import__("time").monotonic()

    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


def test_a_ui_save_during_the_conversion_waits_and_is_not_treated_as_tampering(box):
    import time

    orig = put(box.proj / "fig.py", b"print(1)\n")
    done: dict = {}
    state: dict = {}

    def converter(req):
        state["saver"] = _ui_save(orig, b"USER NEW\n", done)
        time.sleep(0.6)
        state["converter_returned_at"] = time.monotonic()
        Path(req["output"]).write_bytes(b"import matplotlib\n")
        return {"ok": True}

    ctx = change_ctx(box, script_path=orig, script_converter=converter)
    r = call(ctx, "convert_script", {})
    state["saver"].join(30)
    assert not r.is_error, r.content
    assert done["at"] >= state["converter_returned_at"]
    assert orig.read_bytes() == b"USER NEW\n"


def test_converter_scratch_copy_cleanup_failure_is_reported_and_swept_later(box, monkeypatch):
    orig = put(box.proj / "fig.py", b"print(1)\n")
    ctx = change_ctx(box, script_path=orig, script_converter=_converter())
    real = trial._rmtree_once
    monkeypatch.setattr(
        trial, "_rmtree_once", lambda path: (_ for _ in ()).throw(OSError("in use"))
    )
    monkeypatch.setattr(trial, "CLEANUP_DELAY_S", 0)
    r = call(ctx, "convert_script", {})
    assert not r.is_error and "cleanup_pending" in r.content
    left = [d for d in change.convert_root().iterdir()]
    assert len(left) == 1 and (left[0] / trial.PENDING_MARKER).exists()
    monkeypatch.setattr(trial, "_rmtree_once", real)
    r = call(ctx, "convert_script", {})  # 下一次开头扫掉残留
    assert not r.is_error and "cleanup_pending" not in r.content
    assert list(change.convert_root().iterdir()) == []


# =========================================================================== convert_script：建副本失败时的清理


def test_a_failed_scratch_copy_with_a_failed_cleanup_is_reported_honestly(box, monkeypatch):
    from tavotto.engine.fixtools import change

    orig = put(box.proj / "fig.py", b"print(1)\n")
    ctx = change_ctx(box, script_path=orig, script_converter=_converter())
    real_write = Path.write_bytes

    def broken(self, data):
        if "fix_convert" in str(self):
            raise OSError("disk full")
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", broken)
    monkeypatch.setattr(trial, "_rmtree_once", lambda path: (_ for _ in ()).throw(OSError("busy")))
    monkeypatch.setattr(trial, "CLEANUP_DELAY_S", 0)
    r = call(ctx, "convert_script", {})
    assert r.is_error and r.code == "copy_failed" and "cleanup_pending" in r.content
    (left,) = list(change.convert_root().iterdir())
    assert (left / trial.PENDING_MARKER).exists()
