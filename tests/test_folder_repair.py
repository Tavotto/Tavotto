"""「项目外文件夹读不了」的一键修复权限（ADR 0153 修订 2026-10-30，`engine/folderrepair`）。

守五件事：

* **令牌**：一次性、绑定（项目、目录）、会过期；取消 / 没拿到提权不作废（可再点一次，每次点击至多一次 UAC），
  修复跑过（成功或失败）就作废；同一令牌同时只有一次修复；非 Windows 不发令牌。
* **路径再验**：`validate_target` 的每一条拒绝各有一个用例，变异（去掉任意一条）对应用例变红。
* **命令 argv 精确**：假 runner 收到的就是 `takeown /f <目录> /r /d y` 与 `icacls <目录> /reset /t /c /q`，系统工具绝对路径；
  提权启动参数里的 PowerShell 脚本把每个参数当单引号字面量（`'` `%` `&` 不被 shell 展开）。
* **路径不外泄**：失败事实的私有键 → 令牌；`err.extra` / `recovery_params` / 响应里没有完整路径；敏感运行的白名单不放行私有键。
* **接口**：只收令牌；要「页面上点了」；错误码稳定。

不真提权：runner 全是假的；真 `shell_execute_runner` 只有 Windows 真机能验。
"""

from __future__ import annotations

import base64
import json
import os
import re
from types import SimpleNamespace

import pytest

from tavotto.engine import bridgeboot, folderrepair, pool as engine_pool

WIN = "win32"


@pytest.fixture(autouse=True)
def _clean():
    folderrepair.reset()
    yield
    folderrepair.reset()


@pytest.fixture
def world(tmp_path):
    """假的 Windows 目录布局：项目、主目录、系统目录、一个项目外的依赖夹（里面有「读不了」的文件）。"""
    home = tmp_path / "Users" / "me"
    project = home / "work" / "paper"
    dep = home / ".work_deps"
    (dep / "tzdata").mkdir(parents=True)
    blocked = dep / "tzdata" / "__init__.py"
    blocked.write_text("x = 1")
    project.mkdir(parents=True)
    windows = tmp_path / "Windows"
    pf = tmp_path / "Program Files"
    windows.mkdir()
    pf.mkdir()
    env = {"SystemRoot": str(windows), "ProgramFiles": str(pf)}
    return SimpleNamespace(
        tmp=tmp_path, home=home, project=str(project), dep=dep, blocked=blocked, env=env
    )


def _issue(w, path=None, **kw):
    return folderrepair.issue(w.project, str(path or w.dep), str(w.blocked), platform=WIN, **kw)


def _unreadable_then_fixed():
    """动手前读不了、修完读得了（假的「此刻读不读得了」）。"""
    answers = iter([True, False, False, False])
    return lambda f: next(answers, False)


def _run(w, token, runner, **kw):
    kw.setdefault("unreadable", _unreadable_then_fixed())
    return folderrepair.run_repair(
        token, w.project, runner=runner, platform=WIN, home=str(w.home), env=w.env, **kw
    )


def _ok_runner(calls):
    def runner(commands):
        calls.append(commands)
        return folderrepair.RunOutcome("done", 0)

    return runner


# --------------------------------------------------------------------------- 令牌
def test_no_token_off_windows_or_without_project_or_path(world):
    assert folderrepair.issue(world.project, str(world.dep), "", platform="darwin") is None
    assert folderrepair.issue(world.project, str(world.dep), "", platform="linux") is None
    assert folderrepair.issue("", str(world.dep), "", platform=WIN) is None
    assert folderrepair.issue(world.project, "", "", platform=WIN) is None
    assert folderrepair.issue(world.project, None, "", platform=WIN) is None
    assert isinstance(_issue(world), str)


def test_token_is_opaque_and_unrelated_to_the_path(world):
    token = _issue(world)
    assert len(token) >= 24 and str(world.dep.name) not in token
    assert token != _issue(world, world.dep / "tzdata")


def test_run_off_windows_is_refused_before_touching_the_token(world):
    token = _issue(world)
    with pytest.raises(folderrepair.RepairError) as e:
        folderrepair.run_repair(token, world.project, runner=_ok_runner([]), platform="darwin")
    assert e.value.code == folderrepair.ERROR_UNSUPPORTED
    calls = []
    assert _run(world, token, _ok_runner(calls)) == {"repaired": True}


def test_token_is_one_shot(world):
    token, calls = _issue(world), []
    assert _run(world, token, _ok_runner(calls)) == {"repaired": True}
    # 修复跑过 → 作废；同一个令牌再来不弹第二次
    with pytest.raises(folderrepair.RepairError) as e:
        _run(world, token, _ok_runner(calls))
    assert e.value.code == folderrepair.ERROR_TOKEN_INVALID
    assert len(calls) == 1


def test_unknown_or_malformed_token_is_invalid(world):
    for bad in ("nope", "", None, 7, ["x"]):
        with pytest.raises(folderrepair.RepairError) as e:
            _run(world, bad, _ok_runner([]))
        assert e.value.code == folderrepair.ERROR_TOKEN_INVALID


def test_token_is_bound_to_the_project(world, tmp_path):
    token = _issue(world)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(folderrepair.RepairError) as e:
        folderrepair.run_repair(
            token,
            str(other),
            runner=_ok_runner([]),
            platform=WIN,
            home=str(world.home),
            env=world.env,
        )
    assert e.value.code == folderrepair.ERROR_TOKEN_INVALID
    # 别的项目拿它失败，不耗掉它
    assert _run(world, token, _ok_runner([])) == {"repaired": True}


def test_token_expires(world):
    token = _issue(world, now=1000.0)
    with pytest.raises(folderrepair.RepairError) as e:
        _run(world, token, _ok_runner([]), now=1000.0 + folderrepair.TOKEN_TTL + 1)
    assert e.value.code == folderrepair.ERROR_TOKEN_INVALID
    token = _issue(world, now=1000.0)
    assert _run(world, token, _ok_runner([]), now=1000.0 + folderrepair.TOKEN_TTL - 1)


def test_token_is_bound_to_the_directory_registered_at_failure(world, tmp_path):
    """令牌登记的是失败那一刻的目录；登记后目录被换成指向别处的联接点 / 符号链接 → 拒绝，runner 不被调用。"""
    elsewhere = world.home / "somewhere_else"
    elsewhere.mkdir()
    token, calls = _issue(world), []
    import shutil

    shutil.rmtree(world.dep)
    os.symlink(elsewhere, world.dep)
    with pytest.raises(folderrepair.RepairError) as e:
        _run(world, token, _ok_runner(calls))
    assert e.value.code == folderrepair.ERROR_TARGET_REJECTED and e.value.reason == "changed"
    assert calls == []


def test_cancel_and_no_elevation_keep_the_token_but_failure_consumes_it(world):
    token = _issue(world)
    for status, code in (
        ("cancelled", folderrepair.ERROR_CANCELLED),
        ("unavailable", folderrepair.ERROR_NOT_ELEVATED),
    ):
        with pytest.raises(folderrepair.RepairError) as e:
            _run(world, token, lambda c, s=status: folderrepair.RunOutcome(s, 1223))
        assert e.value.code == code
    # 取消过两次，令牌仍可用（用户可再点一次）
    calls = []
    assert _run(world, token, _ok_runner(calls)) == {"repaired": True}

    token = _issue(world)
    with pytest.raises(folderrepair.RepairError) as e:
        _run(world, token, lambda c: folderrepair.RunOutcome("done", 5))
    assert e.value.code == folderrepair.ERROR_FAILED
    with pytest.raises(folderrepair.RepairError) as e:
        _run(world, token, _ok_runner([]))
    assert e.value.code == folderrepair.ERROR_TOKEN_INVALID


def test_exit_zero_but_still_unreadable_is_a_failure(world):
    token = _issue(world)
    with pytest.raises(folderrepair.RepairError) as e:
        _run(world, token, _ok_runner([]), unreadable=lambda f: True)
    assert e.value.code == folderrepair.ERROR_FAILED


def test_already_readable_does_not_elevate(world):
    token, calls = _issue(world), []
    assert _run(world, token, _ok_runner(calls), unreadable=lambda f: False) == {"repaired": False}
    assert calls == []


def test_concurrent_click_is_refused_while_in_flight(world):
    token = _issue(world)
    seen = []

    def runner(commands):
        try:
            _run(world, token, _ok_runner([]))
        except folderrepair.RepairError as exc:
            seen.append(exc.code)
        return folderrepair.RunOutcome("done", 0)

    assert _run(world, token, runner, unreadable=_unreadable_then_fixed())
    assert seen == [folderrepair.ERROR_IN_PROGRESS]


def test_registry_is_bounded(world):
    for i in range(folderrepair.MAX_RECORDS + 10):
        d = world.home / f"d{i}"
        d.mkdir()
        folderrepair.issue(world.project, str(d), "", platform=WIN)
    assert len(folderrepair._RECORDS) <= folderrepair.MAX_RECORDS


# --------------------------------------------------------------------------- 路径再验：每条拒绝一个用例
def _validate(w, path, **kw):
    return folderrepair.validate_target(str(path), w.project, home=str(w.home), env=w.env, **kw)


def _rejected(w, path, reason, **kw):
    with pytest.raises(folderrepair.RepairError) as e:
        _validate(w, path, **kw)
    assert e.value.code == folderrepair.ERROR_TARGET_REJECTED
    assert e.value.reason == reason, e.value.reason


def test_accepts_the_outside_dependency_folder(world):
    assert _validate(world, world.dep) == os.path.realpath(world.dep)


def test_rejects_relative_and_empty(world):
    _rejected(world, "relative/dir", folderrepair.REASON_NOT_ABSOLUTE)
    _rejected(world, "", folderrepair.REASON_NOT_ABSOLUTE)


def test_rejects_unc(world):
    _rejected(world, "\\\\server\\share\\deps", folderrepair.REASON_UNC)


def test_rejects_missing_and_files(world):
    _rejected(world, world.home / "nope", folderrepair.REASON_NOT_DIR)
    _rejected(world, world.blocked, folderrepair.REASON_NOT_DIR)


def test_rejects_drive_root(world):
    _rejected(world, os.path.abspath(os.sep), folderrepair.REASON_DRIVE_ROOT)


def test_rejects_the_home_directory_itself(world):
    _rejected(world, world.home, folderrepair.REASON_HOME)


def test_rejects_ancestors_of_home(world):
    _rejected(world, world.home.parent, folderrepair.REASON_HOME_ANCESTOR)
    _rejected(world, world.tmp, folderrepair.REASON_HOME_ANCESTOR)


@pytest.mark.parametrize("name", ["Documents", "Desktop", "Downloads", "AppData", "OneDrive"])
def test_rejects_standard_folders_directly_under_home(world, name):
    d = world.home / name
    d.mkdir()
    _rejected(world, d, folderrepair.REASON_HOME_KNOWN_FOLDER)


def test_standard_folder_names_are_fine_elsewhere(world):
    d = world.home / "work" / "Documents"
    d.mkdir()
    assert _validate(world, d)


@pytest.mark.parametrize("var", ["SystemRoot", "ProgramFiles"])
def test_rejects_system_directories_and_what_is_below_them(world, var):
    root = world.env[var]
    _rejected(world, root, folderrepair.REASON_SYSTEM_DIR)
    sub = os.path.join(root, "sub")
    os.mkdir(sub)
    _rejected(world, sub, folderrepair.REASON_SYSTEM_DIR)


def test_rejects_the_project_and_what_is_inside_it(world):
    _rejected(world, world.project, folderrepair.REASON_INSIDE_PROJECT)
    inner = os.path.join(world.project, "data")
    os.mkdir(inner)
    _rejected(world, inner, folderrepair.REASON_INSIDE_PROJECT)


def test_rejects_a_folder_that_contains_the_project(world):
    _rejected(world, world.home / "work", folderrepair.REASON_CONTAINS_PROJECT)


def test_rejects_when_the_real_path_is_not_the_registered_one(world):
    other = world.home / "other"
    other.mkdir()
    _rejected(world, world.dep, folderrepair.REASON_CHANGED, expected_real=str(other))


def test_rejects_a_symlink_into_a_forbidden_place(world):
    """目标本身是指向主目录 / 项目的链接：验的是真实路径，不是字面路径。"""
    link = world.home / "innocent"
    os.symlink(world.home, link)
    _rejected(world, link, folderrepair.REASON_HOME)
    link2 = world.home / "innocent2"
    os.symlink(world.project, link2)
    _rejected(world, link2, folderrepair.REASON_INSIDE_PROJECT)


def test_run_repair_validates_before_elevating(world):
    token, calls = _issue(world, world.home / "work"), []
    with pytest.raises(folderrepair.RepairError) as e:
        _run(world, token, _ok_runner(calls))
    assert e.value.code == folderrepair.ERROR_TARGET_REJECTED
    assert calls == []
    # 被拒的目标不留着再试
    with pytest.raises(folderrepair.RepairError) as e:
        _run(world, token, _ok_runner(calls))
    assert e.value.code == folderrepair.ERROR_TOKEN_INVALID


# --------------------------------------------------------------------------- 命令
def test_commands_are_exact_down_to_every_argument():
    env = {"SystemRoot": "C:\\Windows"}
    assert folderrepair.build_commands("D:\\deps\\.work_deps_20261030", env) == [
        [
            "C:\\Windows\\System32\\takeown.exe",
            "/f",
            "D:\\deps\\.work_deps_20261030",
            "/r",
            "/d",
            "y",
        ],
        [
            "C:\\Windows\\System32\\icacls.exe",
            "D:\\deps\\.work_deps_20261030",
            "/reset",
            "/t",
            "/c",
            "/q",
        ],
    ]


def test_commands_fall_back_to_the_default_windows_dir():
    cmds = folderrepair.build_commands("D:\\x", {})
    assert cmds[0][0] == "C:\\Windows\\System32\\takeown.exe"


def test_the_runner_receives_exactly_those_commands_for_the_real_path(world):
    token, calls = _issue(world), []
    _run(world, token, _ok_runner(calls))
    real = os.path.realpath(world.dep)
    assert calls == [folderrepair.build_commands(real, world.env)]
    assert len(calls[0]) == 2 and calls[0][0][2] == real and calls[0][1][1] == real


def _decode(params: str) -> str:
    m = re.fullmatch(
        r"-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand (\S+)", params
    )
    assert m, params
    return base64.b64decode(m.group(1)).decode("utf-16-le")


def test_elevated_launch_is_one_powershell_with_literal_arguments():
    env = {"SystemRoot": "C:\\Windows"}
    path = "D:\\it's 100% & ^odd\\deps"
    exe, params = folderrepair.build_elevated_launch(folderrepair.build_commands(path, env), env)
    assert exe == "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
    script = _decode(params).split("\n")
    q = "'D:\\it''s 100% & ^odd\\deps'"
    assert script == [
        f"& 'C:\\Windows\\System32\\takeown.exe' '/f' {q} '/r' '/d' 'y' | Out-Null",
        "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }",
        f"& 'C:\\Windows\\System32\\icacls.exe' {q} '/reset' '/t' '/c' '/q' | Out-Null",
        "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }",
        "exit 0",
    ]


# --------------------------------------------------------------------------- 路径不外泄
def _private(w):
    return {"path": str(w.dep), "file": str(w.blocked)}


def _fact(w, **extra_public):
    return {
        "unreadable_folder": {"folder": w.dep.name, **extra_public},
        "unreadable_folder_private": _private(w),
    }


def test_pool_swaps_the_private_path_for_a_token(world, monkeypatch):
    monkeypatch.setattr(folderrepair, "supported", lambda platform=None: True)
    err = engine_pool.unreadable_folder_error(
        "m", "tb", _fact(world, module="ROOT"), project=world.project
    )
    assert err.code == engine_pool.IMPORT_FOLDER_UNREADABLE_CODE
    token = err.extra["unreadable_folder"]["repair_token"]
    params = engine_pool.recovery_params(err)
    assert params == {"folder": world.dep.name, "repair_token": token}
    # 完整路径不在错误对象的任何公开部位
    blob = json.dumps([err.extra, params, str(err), err.traceback_text])
    assert str(world.dep) not in blob and "unreadable_folder_private" not in blob
    assert str(world.home) not in blob


def test_a_mappable_module_gets_the_install_card_and_still_carries_the_token(world, monkeypatch):
    monkeypatch.setattr(folderrepair, "supported", lambda platform=None: True)
    err = engine_pool.unreadable_folder_error(
        "m", "tb", _fact(world, module="tzdata"), project=world.project
    )
    assert err.code == "missing_dependency"
    assert "repair_token" in err.extra["unreadable_folder"]
    assert str(world.dep) not in json.dumps(err.extra)


def test_no_token_off_windows(world, monkeypatch):
    monkeypatch.setattr(folderrepair.sys, "platform", "darwin")
    err = engine_pool.unreadable_folder_error(
        "m", "tb", _fact(world, module="ROOT"), project=world.project
    )
    assert engine_pool.recovery_params(err) == {"folder": world.dep.name}


def test_no_token_without_a_private_fact(world, monkeypatch):
    monkeypatch.setattr(folderrepair, "supported", lambda platform=None: True)
    fact = {"unreadable_folder": {"folder": world.dep.name, "module": "ROOT"}}
    err = engine_pool.unreadable_folder_error("m", "tb", fact, project=world.project)
    assert engine_pool.recovery_params(err) == {"folder": world.dep.name}


def test_worker_and_bridge_facts_split_public_and_private(tmp_path, monkeypatch):
    dep = tmp_path / ".deps"
    dep.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    err = PermissionError(13, "Permission denied", str(dep / "tzdata" / "__init__.py"))
    baseline = list(os.sys.path)
    monkeypatch.syspath_prepend(str(dep))
    public, private = bridgeboot.unreadable_folder_facts(
        err, baseline, [str(proj)], (), {bridgeboot._norm(err.filename)}
    )
    assert public == {"folder": ".deps", "module": "tzdata"}
    assert private == {"path": str(dep), "file": str(dep / "tzdata" / "__init__.py")}
    assert str(dep) not in json.dumps(public)
    assert bridgeboot.unreadable_folder_facts(err, baseline, [str(proj)], (), set()) == ({}, {})


def test_sensitive_runs_drop_the_private_fact():
    from tavotto.engine import worker

    resp = {
        "ok": False,
        "error": {
            "code": "script_error",
            "message": "m",
            "traceback": "t",
            **{
                "unreadable_folder": {"folder": "d"},
                "unreadable_folder_private": {"path": "/secret/d", "file": "/secret/d/x.py"},
            },
        },
    }
    out = worker.Worker.diagnostic_response(
        SimpleNamespace(sensitive_argv=True, _sensitive_tex_fact=lambda err: None), resp
    )
    assert (
        "unreadable_folder_private" not in out["error"] and "unreadable_folder" not in out["error"]
    )
    assert "/secret" not in json.dumps(out)


# --------------------------------------------------------------------------- 接口
@pytest.fixture
def client():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    return m.app.test_client()


@pytest.fixture
def repair_client(client, world, monkeypatch):
    from tavotto import app as m

    m.open_project(world.project)
    monkeypatch.setattr(folderrepair, "supported", lambda platform=None: True)
    monkeypatch.setattr(folderrepair, "still_unreadable", lambda f: True)
    monkeypatch.setattr(folderrepair, "_system32", lambda env: "C:\\Windows\\System32")
    # 假 home / env：验证用真实的 world 布局
    real_validate = folderrepair.validate_target
    monkeypatch.setattr(
        folderrepair,
        "validate_target",
        lambda path, project, **kw: real_validate(
            path, project, **{**kw, "home": str(world.home), "env": world.env}
        ),
    )
    return client


def test_endpoint_requires_a_click_in_the_page(repair_client, world, monkeypatch):
    from tavotto import app as m

    token = _issue(world)
    monkeypatch.setattr(m, "_page_authenticated", lambda: False)
    calls = []
    monkeypatch.setattr(folderrepair, "shell_execute_runner", _ok_runner(calls))
    resp = repair_client.post("/api/engine/folder-repair", json={"token": token})
    assert resp.status_code == 403 and resp.get_json()["code"] == folderrepair.ERROR_NOT_CONFIRMED
    assert calls == []


def test_endpoint_repairs_and_the_response_has_no_path(repair_client, world, monkeypatch):
    from tavotto import app as m

    token, calls = _issue(world), []
    monkeypatch.setattr(m, "_page_authenticated", lambda: True)
    monkeypatch.setattr(folderrepair, "shell_execute_runner", _ok_runner(calls))
    states = iter([True, False])  # 动手前读不了，修完读得了
    monkeypatch.setattr(folderrepair, "still_unreadable", lambda f: next(states))
    resp = repair_client.post("/api/engine/folder-repair", json={"token": token})
    assert resp.status_code == 200 and resp.get_json() == {"ok": True, "repaired": True}
    assert calls == [folderrepair.build_commands(os.path.realpath(world.dep))]
    # 一次性
    resp = repair_client.post("/api/engine/folder-repair", json={"token": token})
    assert resp.status_code == 410 and resp.get_json()["code"] == folderrepair.ERROR_TOKEN_INVALID


@pytest.mark.parametrize(
    "outcome, code, status",
    [
        (folderrepair.RunOutcome("cancelled", 1223), folderrepair.ERROR_CANCELLED, 409),
        (folderrepair.RunOutcome("unavailable", 5), folderrepair.ERROR_NOT_ELEVATED, 409),
        (folderrepair.RunOutcome("done", 1), folderrepair.ERROR_FAILED, 500),
    ],
)
def test_endpoint_reports_each_outcome_with_a_stable_code(
    repair_client, world, monkeypatch, outcome, code, status
):
    from tavotto import app as m

    token = _issue(world)
    monkeypatch.setattr(m, "_page_authenticated", lambda: True)
    monkeypatch.setattr(folderrepair, "shell_execute_runner", lambda c: outcome)
    resp = repair_client.post("/api/engine/folder-repair", json={"token": token})
    body = resp.get_json()
    assert (resp.status_code, body["code"], body["params"]) == (status, code, {})
    assert str(world.dep) not in json.dumps(body) and token not in json.dumps(body)


def test_endpoint_takes_only_a_token_never_a_path(repair_client, world, monkeypatch):
    from tavotto import app as m

    monkeypatch.setattr(m, "_page_authenticated", lambda: True)
    calls = []
    monkeypatch.setattr(folderrepair, "shell_execute_runner", _ok_runner(calls))
    for body in ({"path": str(world.dep)}, {"token": str(world.dep)}, {}, None):
        resp = repair_client.post("/api/engine/folder-repair", json=body)
        assert (
            resp.status_code == 410 and resp.get_json()["code"] == folderrepair.ERROR_TOKEN_INVALID
        )
    assert calls == []


def test_endpoint_off_windows(client, world, monkeypatch):
    from tavotto import app as m

    m.open_project(world.project)
    monkeypatch.setattr(m, "_page_authenticated", lambda: True)
    monkeypatch.setattr(folderrepair.sys, "platform", "darwin")
    resp = client.post("/api/engine/folder-repair", json={"token": "x"})
    assert resp.status_code == 400 and resp.get_json()["code"] == folderrepair.ERROR_UNSUPPORTED
