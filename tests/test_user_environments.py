"""用户自己的 Python 环境（ADR 0079）：发现 → 体检 → 挑最好的 → 跑前的门里自动改用。

判据的主语：
* `userenvs.discover()` 的**候选表**（顺序 = 优先级、来源、标签、去重）——只读磁盘 + 问一次登录 shell；
* `userenvs.evaluate()` / `best()` 的**挑选结果**——装齐 = 脚本要的 import 在那个环境里 import 得到；
* `deprepair.gate()` 的**放行与记录**——自动改用时本项目记成 `automatic=True, trigger=user_environment`，
  用户显式选过 / 明确选回默认 / 项目自己的 venv 时一个都不碰。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tavotto.engine import deprepair, pool as engine_pool, projectenv, userenvs

pytest_plugins = ("support.dependency_repair",)

POSIX = pytest.mark.skipif(os.name == "nt", reason="登录 shell / bin/python 布局只在 POSIX 上")


@pytest.fixture(autouse=True)
def _clean(clean_state, monkeypatch):
    # conftest 默认把这件事关掉（别的用例不许随机器变）；这里测的就是它
    monkeypatch.delenv("TAVOTTO_USER_ENV_DISCOVERY", raising=False)
    userenvs.reset_cache()
    yield
    userenvs.reset_cache()


def _python(prefix: Path, name: str = "python3") -> Path:
    """在环境前缀里摆一个「解释器」文件（发现只看文件在不在，不执行它）。"""
    sub = "" if os.name == "nt" else "bin"
    exe = prefix / sub / ("python.exe" if os.name == "nt" else name)
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    return exe


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.delenv("PYENV_ROOT", raising=False)
    # 不问真的登录 shell：用例各自决定它回什么
    monkeypatch.setattr(userenvs, "login_shell_pythons", lambda: [])
    # 常见安装根里会有这台机器真实的 Conda（/opt/homebrew/…）：只留 HOME 下的
    real_roots = userenvs._conda_roots
    monkeypatch.setattr(
        userenvs, "_conda_roots", lambda: [r for r in real_roots() if r.startswith(str(h))]
    )
    return h


@POSIX
def test_discovery_order_sources_and_labels(tmp_path, home, monkeypatch):
    project = tmp_path / "paper"
    (project / "figs").mkdir(parents=True)
    script = "figs/fig.py"

    # 项目线索：VS Code 指定 / .python-version / environment.yml / shebang
    vscode_env = _python(tmp_path / "vsenv")
    (project / ".vscode").mkdir()
    (project / ".vscode" / "settings.json").write_text(
        '{\n  // JSONC 注释\n  "python.defaultInterpreterPath": "%s",\n}\n' % vscode_env,
        encoding="utf-8",
    )
    pyenv_311 = _python(home / ".pyenv" / "versions" / "3.11.9")
    _python(home / ".pyenv" / "versions" / "3.12.4")
    (project / ".python-version").write_text("3.11.9\n", encoding="utf-8")
    lab = _python(home / "miniforge3" / "envs" / "lab")
    (project / "environment.yml").write_text("name: lab\ndependencies: [numpy]\n", encoding="utf-8")
    shebang = _python(tmp_path / "shebang-env")
    (project / script).write_text(f"#!{shebang}\nimport openpyxl\n", encoding="utf-8")

    # 终端里默认的 python（与 conda base 同一个目录下的两个别名 → 只算一个）
    base = _python(home / "miniforge3")
    (base.parent / "python").symlink_to(base)
    monkeypatch.setattr(
        userenvs, "login_shell_pythons", lambda: [str(base), str(base.parent / "python")]
    )

    # environments.txt 里记着一个不在常见根下的环境
    elsewhere = _python(tmp_path / "somewhere" / "envs" / "thesis")
    (home / ".conda").mkdir()
    (home / ".conda" / "environments.txt").write_text(
        f"{elsewhere.parent.parent}\n/does/not/exist\n", encoding="utf-8"
    )

    got = userenvs.discover(project, script)
    pairs = [(Path(c["python"]), c["source"], c["label"]) for c in got]
    assert pairs[0] == (vscode_env, userenvs.SOURCE_VSCODE, "")
    assert pairs[1] == (pyenv_311, userenvs.SOURCE_PYTHON_VERSION, "3.11.9")
    assert pairs[2] == (lab, userenvs.SOURCE_ENVIRONMENT_YML, "lab")
    assert pairs[3] == (shebang, userenvs.SOURCE_SHEBANG, "")
    assert pairs[4] == (base, userenvs.SOURCE_LOGIN_SHELL, "")
    rest = pairs[5:]
    assert (elsewhere, userenvs.SOURCE_CONDA, "thesis") in rest
    assert any(p.parent.parent.name == "3.12.4" and s == userenvs.SOURCE_PYENV for p, s, _ in rest)
    # 去重：lab / base / 3.11.9 已经以更靠前的来源出现过，不再以 conda / pyenv 重复
    keys = [userenvs._key(str(p)) for p, _, _ in pairs]
    assert len(keys) == len(set(keys))
    assert not any(p == lab and s == userenvs.SOURCE_CONDA for p, s, _ in rest)


@POSIX
def test_hints_never_come_from_outside_the_project(tmp_path, home):
    outside = tmp_path / "outer"
    project = outside / "paper"
    project.mkdir(parents=True)
    env = _python(tmp_path / "outer-env")
    (outside / ".vscode").mkdir()
    (outside / ".vscode" / "settings.json").write_text(
        '{"python.defaultInterpreterPath": "%s"}' % env, encoding="utf-8"
    )
    (project / "fig.py").write_text("import x\n", encoding="utf-8")
    assert userenvs.discover(project, "fig.py") == []
    # 脚本路径本身跳出项目（`../`，它可以来自请求体）：往上找的起点、shebang 都不读项目外的
    stray_env = _python(tmp_path / "stray-env")
    (outside / "stray.py").write_text(f"#!{stray_env}\nimport x\n", encoding="utf-8")
    assert userenvs.discover(project, "../stray.py") == []


@POSIX
def test_env_shebang_is_left_to_the_login_shell(tmp_path, home):
    project = tmp_path / "paper"
    project.mkdir()
    (project / "fig.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    assert userenvs._shebang_python(str(project / "fig.py")) is None


@POSIX
def test_login_shell_output_is_read_by_marker_lines_only(tmp_path, monkeypatch):
    """rc 文件会往 stdout 打杂讯（欢迎语、conda 提示）：只认带标记的行，空结果不算。真起一个子进程。"""
    target = _python(tmp_path / "env")
    shell = tmp_path / "fakesh"
    shell.write_text(
        "#!/bin/sh\n"
        'echo "Welcome back!"\n'
        f'echo "{userenvs._MARK}{target}"\n'
        f'echo "{userenvs._MARK}"\n'
        f'echo "{userenvs._MARK}relative/python"\n',
        encoding="utf-8",
    )
    shell.chmod(0o755)
    monkeypatch.setenv("SHELL", str(shell))
    assert userenvs.login_shell_pythons() == [str(target)]


def _entry(python, source, *, ok=True, support="verified", version="3.12.1", missing=(), label=""):
    return {
        "python": python,
        "source": source,
        "label": label,
        "ok": ok,
        "code": "" if ok else "project_env_no_matplotlib",
        "support": support,
        "python_version": version,
        "matplotlib_version": "3.10.0",
        "missing": list(missing),
        "satisfies": ok and not missing,
    }


def test_evaluate_judges_by_import_not_metadata(monkeypatch):
    health = {
        "/a/python": {
            "ok": True,
            "support": "verified",
            "python_version": "3.12.1",
            "modules_ok": {"openpyxl": True, "pypdf": True, "weird": True},
        },
        "/b/python": {
            "ok": True,
            "support": "verified",
            "python_version": "3.12.1",
            "modules_ok": {"openpyxl": True, "pypdf": False, "weird": True},
        },
        "/c/python": {
            "ok": False,
            "code": "project_env_unsupported_python",
            "python_version": "3.9.6",
            "modules_ok": {"openpyxl": True},
        },
    }
    seen = []

    def fake_probe(python, modules):
        seen.append(modules)
        return health[python]

    monkeypatch.setattr(userenvs, "_probe", fake_probe)
    needed = [
        {"import_name": "openpyxl", "distribution": "openpyxl"},
        {"import_name": "pypdf", "distribution": "pypdf"},
    ]
    cands = [{"python": p, "source": "conda", "label": ""} for p in health]
    got = {e["python"]: e for e in userenvs.evaluate(cands, needed, ["weird"])}
    assert seen[0] == ("openpyxl", "pypdf", "weird")
    assert got["/a/python"]["satisfies"] and got["/a/python"]["missing"] == []
    assert got["/b/python"]["missing"] == ["pypdf"] and not got["/b/python"]["satisfies"]
    # 环境本身不合格：不算装齐，也不报缺什么（那不是它的问题）
    assert not got["/c/python"]["satisfies"] and got["/c/python"]["missing"] == []


def test_best_prefers_intent_then_verified_then_newer():
    S = userenvs
    pool = [
        _entry("/sys", S.SOURCE_SYSTEM, version="3.13.1"),
        _entry("/conda-other", S.SOURCE_CONDA, label="other", version="3.13.0"),
        _entry("/conda-paper", S.SOURCE_CONDA, label="Paper", version="3.11.0"),
        _entry(
            "/shell", S.SOURCE_LOGIN_SHELL, version="3.10.0", support="unverified_but_compatible"
        ),
        _entry("/vscode", S.SOURCE_VSCODE, missing=["pypdf"]),  # 最近的意图，但没装齐
    ]
    assert S.best(pool, "paper")["python"] == "/shell"
    pool = [e for e in pool if e["python"] != "/shell"]
    assert S.best(pool, "paper")["python"] == "/conda-paper"  # 名字对上项目目录
    pool = [e for e in pool if e["python"] != "/conda-paper"]
    assert S.best(pool, "paper")["python"] == "/conda-other"
    # 同档：verified 压过版本新
    same = [
        _entry("/p1", S.SOURCE_PYENV, version="3.14.0", support="unverified_but_compatible"),
        _entry("/p2", S.SOURCE_PYENV, version="3.11.0"),
        _entry("/p3", S.SOURCE_PYENV, version="3.13.0"),
    ]
    assert S.best(same)["python"] == "/p3"
    assert S.best([_entry("/x", S.SOURCE_CONDA, missing=["a"])]) is None


# ------------------------------------------------------------------ 跑前的门


def _door(envs, *, target_kind="tavotto_managed"):
    """`_preparation_offer` 的替身：(公开载荷, 带路径的内部结果表)。"""
    return _offer([userenvs.public(e) for e in envs], target_kind=target_kind), envs


def _offer(envs, *, target_kind="tavotto_managed"):
    return {
        "code": deprepair.ERROR_PREPARATION_REQUIRED,
        "script": "fig.py",
        "plan": {
            "status": "ready",
            "requirements": ["openpyxl"],
            "missing": [{"distribution": "openpyxl"}],
        },
        "target_kind": target_kind,
        "targets": [],
        "rounds_remaining": 3,
        "skipped": False,
        "clean_machine": False,
        "user_environments": envs,
    }


@pytest.fixture
def adopt_env(tmp_path, monkeypatch):
    project = tmp_path / "paper"
    project.mkdir()
    env = _python(tmp_path / "lab")
    heard = []
    monkeypatch.setattr(deprepair, "_adoption_listeners", [lambda p, e: heard.append((p, e))])
    monkeypatch.setattr(engine_pool, "explicit_worker_python", lambda: "")
    monkeypatch.setattr(deprepair, "_config_worker_python", lambda: "")
    return project, env, heard


def test_gate_adopts_the_best_user_environment_and_passes(adopt_env, monkeypatch):
    project, env, heard = adopt_env
    envs = [
        _entry(str(env), userenvs.SOURCE_LOGIN_SHELL),
        _entry("/other", userenvs.SOURCE_CONDA),
    ]
    monkeypatch.setattr(deprepair, "_preparation_offer", lambda p, s: _door(envs))
    assert deprepair.gate(project, "fig.py") is None, "装齐的用户环境 → 直接改用、放行"
    record = projectenv.remembered_record(project)
    assert record["automatic"] is True
    assert record["trigger"] == deprepair.TRIGGER_USER_ENVIRONMENT
    assert record["path"] == str(env)
    assert [(p, e["id"]) for p, e in heard] == [(str(project), userenvs.env_id(str(env)))]
    assert "python" not in heard[0][1], "通知也不带路径"


def test_gate_asks_when_nothing_is_complete(adopt_env, monkeypatch):
    project, env, heard = adopt_env
    envs = [_entry(str(env), userenvs.SOURCE_LOGIN_SHELL, missing=["openpyxl"])]
    monkeypatch.setattr(deprepair, "_preparation_offer", lambda p, s: _door(envs))
    got = deprepair.gate(project, "fig.py")
    assert got is not None
    assert got["user_environments"] == [userenvs.public(e) for e in envs]
    assert str(env) not in repr(got), "载荷不带解释器路径（ADR 0053 §二）"
    assert projectenv.remembered_record(project) is None and heard == []


@pytest.mark.parametrize(
    "case", ["default_chain", "user_picked", "project_venv", "explicit", "configured"]
)
def test_gate_never_overrides_a_user_decision(adopt_env, monkeypatch, case):
    project, env, heard = adopt_env
    target_kind = "tavotto_managed"
    if case == "default_chain":
        projectenv.remember_default(project)  # 「改回」写的就是这一条
    elif case == "user_picked":
        projectenv.remember(project, str(env), automatic=False, trigger="user_selected")
    elif case == "project_venv":
        target_kind = "project_venv"
    elif case == "explicit":
        monkeypatch.setattr(engine_pool, "explicit_worker_python", lambda: "/usr/bin/python3")
    else:
        monkeypatch.setattr(deprepair, "_config_worker_python", lambda: "/usr/bin/python3")
    other = _python(Path(str(env)).parent.parent.parent / "other-env")
    envs = [_entry(str(other), userenvs.SOURCE_LOGIN_SHELL)]
    monkeypatch.setattr(
        deprepair, "_preparation_offer", lambda p, s: _door(envs, target_kind=target_kind)
    )
    assert deprepair.gate(project, "fig.py") is not None, case
    assert heard == []
    record = projectenv.remembered_record(project)
    assert record is None or record.get("path") != str(other)


def test_offer_excludes_the_interpreter_that_is_missing_things(tmp_path, monkeypatch):
    project = tmp_path / "paper"
    project.mkdir()
    cur = _python(tmp_path / "current")
    good = _python(tmp_path / "good")
    monkeypatch.setattr(
        userenvs,
        "discover",
        lambda root, script: [
            {"python": str(cur), "source": userenvs.SOURCE_LOGIN_SHELL, "label": ""},
            {"python": str(good), "source": userenvs.SOURCE_CONDA, "label": "good"},
        ],
    )
    monkeypatch.setattr(engine_pool, "system_python_candidates", lambda: [(str(good), "system")])
    probed = []

    def fake_eval(cands, needed, unknown):
        probed.extend(c["python"] for c in cands)
        return [_entry(c["python"], c["source"], label=c["label"]) for c in cands]

    monkeypatch.setattr(userenvs, "evaluate", fake_eval)
    plan = {"missing": [{"import_name": "openpyxl", "distribution": "openpyxl"}], "unknown": []}
    got = deprepair.user_environment_offer(project, "fig.py", plan, str(cur))
    assert probed == [str(good)], "正缺包的那个不再体检；系统链里的同一个解释器不重复"
    assert [e["python"] for e in got] == [str(good)]
    # 什么都不缺：不发现、不体检
    probed.clear()
    assert (
        deprepair.user_environment_offer(project, "fig.py", {"missing": [], "unknown": []}, "")
        == []
    )
    assert probed == []


def test_real_probe_reports_each_module():
    """真起一次解释器：`modules` 的结果逐个回来，且不影响 `ok`（环境健康与装没装齐分开判）。"""
    health = projectenv.probe_environment(
        sys.executable, modules=("json", "tavotto_no_such_mod_xyz")
    )
    assert health["modules_ok"] == {"json": True, "tavotto_no_such_mod_xyz": False}
    if health.get("matplotlib_version"):
        assert health["ok"] is True


def test_gate_adopts_without_deadlocking_under_the_pool_lock(adopt_env, monkeypatch):
    """门跑在 `pool.get()` 持有 `pool._lock` 的 `_new_worker()` 里：改用时再去拿这把锁（例如
    `pool.reset_worker_python()`）就是死锁。真机端到端抓到过一次——单测直接调 gate 看不见，所以这里
    在持锁的线程里调。锁换成一把替身（`reset_worker_python` 按名字取模块全局的 `_lock`，照样撞上），
    **在 finally 里自己换回**——不用 monkeypatch：它的还原排在 `clean_state` 收尾之后，反证时收尾会撞上
    被死锁线程占住的替身（实测挂死过）。"""
    import threading

    project, env, heard = adopt_env
    envs = [_entry(str(env), userenvs.SOURCE_LOGIN_SHELL)]
    monkeypatch.setattr(deprepair, "_preparation_offer", lambda p, s: _door(envs))
    out: dict = {}

    def run():
        with engine_pool._lock:
            out["gate"] = deprepair.gate(project, "fig.py")

    original = engine_pool._lock
    engine_pool._lock = threading.Lock()
    try:
        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(10)
    finally:
        engine_pool._lock = original
    assert not th.is_alive(), "持有 pool._lock 时门里的自动改用卡死了"
    assert out["gate"] is None and projectenv.remembered_record(project)["path"] == str(env)


def test_the_switch_turns_discovery_off(tmp_path, monkeypatch):
    """`TAVOTTO_USER_ENV_DISCOVERY=0`：不发现、不体检（测试进程默认就是这样；也是用户的逃生口）。"""
    monkeypatch.setenv("TAVOTTO_USER_ENV_DISCOVERY", "0")
    called = []
    monkeypatch.setattr(userenvs, "discover", lambda *a: called.append(a) or [])
    plan = {"missing": [{"import_name": "openpyxl", "distribution": "openpyxl"}], "unknown": []}
    assert deprepair.user_environment_offer(tmp_path, "fig.py", plan, "") == []
    assert called == []
