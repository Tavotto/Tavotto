"""用户自己的 Python 环境（ADR 0079）：发现 → 体检 → 挑最好的 → 跑前的门里自动改用。

判据的主语：
* `userenvs.discover()` 的**候选表**（顺序 = 优先级、来源、标签、去重）——只读磁盘 + 问一次登录 shell；
* `userenvs.evaluate()` / `best()` 的**挑选结果**——装齐 = 脚本要的 import 在那个环境里 import 得到；
* `deprepair.decide_environment()` 的**记录**——自动改用时本项目记成 `automatic=True, trigger=user_environment`，
  用户显式选过 / 明确选回默认 / 项目自己的 venv 时一个都不碰；`deprepair.gate()` 只读、不换解释器；
* **决定的时刻**（Codex #522 两条 P1）——换解释器发生在「解析解释器 / 查租约」之前：`pool.acquire()`
  的 `is_mutating` 与构造函数解析到的是同一个解释器，worker 不起在被占用的环境上；
* **采用前的复核**（Codex #522 P2）——弹窗交回的 id 按此刻的计划重新量装没装齐，不读体检缓存。
"""

from __future__ import annotations

import json
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
    (project / "fig.py").write_text("import openpyxl\n", encoding="utf-8")
    env = _python(tmp_path / "lab")
    heard = []
    monkeypatch.setattr(deprepair, "_adoption_listeners", [lambda p, e: heard.append((p, e))])
    monkeypatch.setattr(engine_pool, "explicit_worker_python", lambda: "")
    monkeypatch.setattr(deprepair, "_config_worker_python", lambda: "")
    return project, env, heard


def test_decide_adopts_the_best_user_environment(adopt_env, monkeypatch):
    project, env, heard = adopt_env
    envs = [
        _entry(str(env), userenvs.SOURCE_LOGIN_SHELL),
        _entry("/other", userenvs.SOURCE_CONDA),
    ]
    monkeypatch.setattr(deprepair, "_preparation_offer", lambda p, s: _door(envs))
    got = deprepair.decide_environment(project, "fig.py")
    assert got is not None and got["python"] == str(env), "装齐的用户环境 → 直接改用"
    record = projectenv.remembered_record(project)
    assert record["automatic"] is True
    assert record["trigger"] == deprepair.TRIGGER_USER_ENVIRONMENT
    assert record["path"] == str(env)
    assert [(p, e["id"]) for p, e in heard] == [(str(project), userenvs.env_id(str(env)))]
    assert "python" not in heard[0][1], "通知也不带路径"


def test_the_gate_only_reads_and_never_switches(adopt_env, monkeypatch):
    """门只读：换不换解释器只在 `decide_environment()` 一处（它跑在解析解释器与查租约之前）。门里再换一次，
    就回到了 #522 的形状——快照 / 租约查的是旧的，起的是新的。"""
    project, env, heard = adopt_env
    envs = [_entry(str(env), userenvs.SOURCE_LOGIN_SHELL)]
    monkeypatch.setattr(deprepair, "_preparation_offer", lambda p, s: _door(envs))
    got = deprepair.gate(project, "fig.py")
    assert got is not None and got["user_environments"] == [userenvs.public(e) for e in envs]
    assert projectenv.remembered_record(project) is None and heard == []


def test_decide_does_nothing_when_nothing_is_complete(adopt_env, monkeypatch):
    project, env, heard = adopt_env
    envs = [_entry(str(env), userenvs.SOURCE_LOGIN_SHELL, missing=["openpyxl"])]
    monkeypatch.setattr(deprepair, "_preparation_offer", lambda p, s: _door(envs))
    assert deprepair.decide_environment(project, "fig.py") is None
    got = deprepair.gate(project, "fig.py")
    assert got is not None
    assert got["user_environments"] == [userenvs.public(e) for e in envs]
    assert str(env) not in repr(got), "载荷不带解释器路径（ADR 0053 §二）"
    assert projectenv.remembered_record(project) is None and heard == []


@pytest.mark.parametrize(
    "case", ["default_chain", "user_picked", "project_venv", "explicit", "configured"]
)
def test_decide_never_overrides_a_user_decision(adopt_env, monkeypatch, case):
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
    assert deprepair.decide_environment(project, "fig.py") is None, case
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


def test_a_user_decision_landing_during_the_decision_is_not_overwritten(adopt_env, monkeypatch):
    """同形清扫（先判后写分两步）：`_auto_adopt_allowed` 判完、写入之前，用户在设置里点了「改回」（明确选回
    默认链条）。以前那条自动决策照写，把用户的决定盖掉；现在判断与写入在同一把锁里重判，不写、不通知。
    同步点：判据一返回就让用户的那一下落地，不用 sleep。"""
    project, env, heard = adopt_env
    envs = [_entry(str(env), userenvs.SOURCE_LOGIN_SHELL)]
    monkeypatch.setattr(deprepair, "_preparation_offer", lambda p, s: _door(envs))
    real = deprepair._auto_adopt_allowed
    verdicts = []

    def allowed_then_user_clicks_revert(p, offer):
        verdicts.append(real(p, offer))
        projectenv.remember_default(p)
        return verdicts[-1]

    monkeypatch.setattr(deprepair, "_auto_adopt_allowed", allowed_then_user_clicks_revert)
    assert deprepair.decide_environment(project, "fig.py") is None
    assert verdicts == [True], "尺子是活的：判的那一刻确实允许"
    record = projectenv.remembered_record(project)
    assert record["mode"] == projectenv.MODE_DEFAULT_CHAIN, record
    assert heard == []


def test_decide_adopts_without_deadlocking_under_the_pool_lock(adopt_env, monkeypatch):
    """改用时不许去拿 `pool._lock`（例如 `pool.reset_worker_python()`）：2026-09-23 真机端到端抓到过一次
    死锁，那时决定还跑在持锁的 `_new_worker()` 里。今天 `pool.acquire()` 在锁外调它（见下面的
    `test_acquire_decides_before_the_lease_check_*`），这条仍在持锁的线程里调，钉住「决定本身不碰池锁」。
    锁换成一把替身（`reset_worker_python` 按名字取模块全局的 `_lock`，照样撞上），**在 finally 里自己换回**——
    不用 monkeypatch：它的还原排在 `clean_state` 收尾之后，反证时收尾会撞上被死锁线程占住的替身（实测挂死过）。"""
    import threading

    project, env, heard = adopt_env
    envs = [_entry(str(env), userenvs.SOURCE_LOGIN_SHELL)]
    monkeypatch.setattr(deprepair, "_preparation_offer", lambda p, s: _door(envs))
    out: dict = {}

    def run():
        with engine_pool._lock:
            out["got"] = deprepair.decide_environment(project, "fig.py")

    original = engine_pool._lock
    engine_pool._lock = threading.Lock()
    try:
        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(10)
    finally:
        engine_pool._lock = original
    assert not th.is_alive(), "持有 pool._lock 时自动改用卡死了"
    assert out["got"] is not None and projectenv.remembered_record(project)["path"] == str(env)


# ------------------------------------------------ 决定的时刻：先于解析解释器与租约检查（Codex #522 P1）

BEFORE = "/envs/before/bin/python"


@pytest.fixture
def spawn_box(adopt_env, monkeypatch):
    """真 `pool.acquire()`，只把「起进程」换成记账：构造函数照真 worker 那样自己解析解释器（`EngineWorker`
    就是这么拿 `self.python` 的），并记下那一刻那个环境是不是正被改动——主语是**起会话那一刻、它用的解释器**。

    解释器解析换成「项目记住了谁就是谁，没记住是 BEFORE」：`remember()` 改变解析结果这件事照真的来，
    只是不去体检一个并不存在的解释器。依赖门（`_spawn_gate`）与工作目录门都是真的。"""
    from tavotto.engine import envlease, workerd_client

    project, env, heard = adopt_env

    def resolve(root=None, **kw):
        record = projectenv.remembered_record(root) if root else None
        if record and record.get("path"):
            return record["path"], "project"
        return BEFORE, "bundled"

    monkeypatch.setattr(engine_pool, "resolve_worker_python", resolve)
    monkeypatch.setattr(workerd_client, "find_workerd", lambda: None)
    monkeypatch.setattr(engine_pool, "_schedule_prune", lambda: None)  # 替身没有会话目录可收
    spawned: list[dict] = []

    class Recorder:
        def __init__(self, script_name, figures_dir, entry, base_dir=None):
            self.script_name, self.entry = script_name, entry
            self.python = engine_pool.resolve_worker_python(figures_dir, script=script_name)[0]
            self.last_used = 0.0
            spawned.append({"python": self.python, "mutating": envlease.is_mutating(self.python)})

        def alive(self):
            return True

        def shutdown(self):
            pass

    monkeypatch.setattr(engine_pool, "EngineWorker", Recorder)

    def door(p, s):
        """按**此刻**解析到的解释器回计划：还是 BEFORE 就缺包（ready + 候选表），换过了就什么都不缺。"""
        if resolve(str(p))[0] != BEFORE:
            offer = _offer([])
            offer["plan"] = {"status": "nothing_needed", "missing": []}
            return offer, []
        return _door(box["envs"])

    monkeypatch.setattr(deprepair, "_preparation_offer", door)
    box = {"project": project, "env": env, "spawned": spawned, "envs": []}
    yield box
    with engine_pool._lock:
        engine_pool._workers.pop((engine_pool._norm_dir(str(project)), "fig.py"), None)


def test_acquire_decides_before_the_lease_check_so_no_worker_starts_on_a_busy_environment(
    spawn_box, monkeypatch
):
    """确定性的同步点：改用的通知在 `remember()` 之后、调用方解析解释器之前触发——在那一刻让另一个作业
    拿住被选中环境的租约（真的 `envlease`，环境占用唯一的那张表）。修好之后 `acquire()` 是按**决定之后**的
    解释器查租约的：拒起、`environment_mutating`，一个 worker 都没有构造。以前决定藏在 `_new_worker()` 的
    门里，`is_mutating` 查的是 BEFORE，构造函数却解析到被占用的那一个——worker 起在装了一半的 site-packages 上。"""
    from tavotto.engine import envlease

    project, env = spawn_box["project"], spawn_box["env"]
    spawn_box["envs"] = [_entry(str(env), userenvs.SOURCE_LOGIN_SHELL)]
    lease = envlease.mutating(envlease.env_key_of(str(env)), str(env))

    def another_job_starts_installing(p, e):
        lease.__enter__()

    monkeypatch.setattr(deprepair, "_adoption_listeners", [another_job_starts_installing])
    try:
        with pytest.raises(engine_pool.WorkerError) as err:
            engine_pool.acquire("fig.py", str(project), "__main__")
        held = envlease.is_mutating(str(env))
    finally:
        lease.__exit__(None, None, None)
    assert held, "尺子是活的：同步点确实拿住了被选中环境的租约"
    assert spawn_box["spawned"] == [], f"worker 起在了被占用的环境上: {spawn_box['spawned']}"
    assert err.value.code == engine_pool.ENVIRONMENT_MUTATING
    assert projectenv.remembered_record(project)["path"] == str(env)


def test_acquire_starts_the_worker_on_the_adopted_environment(spawn_box):
    """对照组（同一副替身、没有别的作业）：决定落地之后，查租约与起会话用的是同一个——改用的那个。"""
    project, env = spawn_box["project"], spawn_box["env"]
    spawn_box["envs"] = [_entry(str(env), userenvs.SOURCE_LOGIN_SHELL)]
    w, created = engine_pool.acquire("fig.py", str(project), "__main__")
    assert created and w.python == str(env)
    assert spawn_box["spawned"] == [{"python": str(env), "mutating": False}]


def test_an_environment_already_being_changed_is_not_adopted(spawn_box, tmp_path):
    """挑选时就跳过正被改动的环境：它此刻的体检读的是装了一半的 site-packages。装齐的另一个照常被选中。"""
    from tavotto.engine import envlease

    project, busy = spawn_box["project"], spawn_box["env"]
    other = _python(tmp_path / "other")
    spawn_box["envs"] = [
        _entry(str(busy), userenvs.SOURCE_LOGIN_SHELL),  # 排第一：不过滤就选它
        _entry(str(other), userenvs.SOURCE_CONDA),
    ]
    with envlease.mutating(envlease.env_key_of(str(busy)), str(busy)):
        w, _created = engine_pool.acquire("fig.py", str(project), "__main__")
    assert projectenv.remembered_record(project)["path"] == str(other)
    assert spawn_box["spawned"] == [{"python": str(other), "mutating": False}]


def test_a_reusable_session_is_not_asked_to_decide_again(spawn_box, monkeypatch):
    """热会话复用时不重新决定（决定要算计划、可能体检候选——每次渲染都来一遍不可接受）。"""
    project, env = spawn_box["project"], spawn_box["env"]
    spawn_box["envs"] = [_entry(str(env), userenvs.SOURCE_LOGIN_SHELL)]
    engine_pool.acquire("fig.py", str(project), "__main__")
    calls = []
    monkeypatch.setattr(engine_pool, "ENVIRONMENT_DECIDERS", [lambda d, s: calls.append((d, s))])
    _w, created = engine_pool.acquire("fig.py", str(project), "__main__")
    assert not created and calls == []


def test_a_worker_that_dies_after_the_peek_still_gets_the_decision(spawn_box):
    """Codex #562 P2：锁外窥视说「能复用」（于是没做决定），进锁时那条会话已经死了。锁内重建走的是只读的
    依赖门——以前这里只会弹框，本该自动改用的没改。现在锁内发现要重建而还没决定过，就出锁补上决定再来一遍。
    时刻用替身钉死：同一条会话第一次被问 `alive()`（窥视）答活着，第二次（锁内）答死了。"""
    project, env = spawn_box["project"], spawn_box["env"]
    spawn_box["envs"] = [_entry(str(env), userenvs.SOURCE_LOGIN_SHELL)]
    answers = iter([True, False])

    class DiesAfterThePeek:
        script_name, entry, python, last_used = "fig.py", "__main__", BEFORE, 0.0

        def alive(self):
            return next(answers, False)

        def shutdown(self):
            pass

    with engine_pool._lock:
        engine_pool._workers[(engine_pool._norm_dir(str(project)), "fig.py")] = DiesAfterThePeek()
    w, created = engine_pool.acquire("fig.py", str(project), "__main__")
    assert next(answers, "spent") == "spent", "尺子是活的：窥视与锁内各问了一次"
    assert created and w.python == str(env), "补上了决定：改用了装齐的环境，而不是弹框"
    assert spawn_box["spawned"] == [{"python": str(env), "mutating": False}]


def test_a_lease_taken_between_the_check_and_the_lock_stops_the_spawn(spawn_box, monkeypatch):
    """同一形状（锁外的检查被锁内信任）的另一处：锁外 `is_mutating` 说没人在装，进锁之前装包作业拿到了
    这个环境的租约。锁内再查一次，拒起；以前 worker 照样起在正被写的 site-packages 上。
    同步点：锁外那次检查一返回就让另一个作业拿住租约（真的 `envlease`），不用 sleep。"""
    from tavotto.engine import envlease

    project = spawn_box["project"]
    offer = _offer([])
    offer["plan"] = {"status": "nothing_needed", "missing": []}
    monkeypatch.setattr(deprepair, "_preparation_offer", lambda p, s: (offer, []))
    lease = envlease.mutating(envlease.env_key_of(BEFORE), BEFORE)
    real = engine_pool.is_mutating
    calls = []

    def check_then_someone_starts_installing(python):
        got = real(python)
        calls.append(got)
        if len(calls) == 1:
            lease.__enter__()
        return got

    monkeypatch.setattr(engine_pool, "is_mutating", check_then_someone_starts_installing)
    try:
        with pytest.raises(engine_pool.WorkerError) as err:
            engine_pool.acquire("fig.py", str(project), "__main__")
    finally:
        lease.__exit__(None, None, None)
    assert calls[0] is False, "尺子是活的：锁外那次确实说没人在装"
    assert spawn_box["spawned"] == [], f"worker 起在了被占用的环境上: {spawn_box['spawned']}"
    assert err.value.code == engine_pool.ENVIRONMENT_MUTATING


# ------------------------------------------------ 采用前复核（Codex #522 P2）


@pytest.fixture
def adopt_api(project, client, monkeypatch, tmp_path):
    """真端点 `PATCH /api/engine/environment {scope: project, user_environment: id}`：发现结果与计划由替身给，
    体检换成可切换的替身（`probe_environment` 是体检唯一的出口）。"""
    from tavotto import app as m

    m.open_project(str(project))
    env = _python(tmp_path / "lab")
    monkeypatch.setattr(
        deprepair,
        "user_environment_candidates",
        lambda root, script, exclude="": [
            {"python": str(env), "source": userenvs.SOURCE_CONDA, "label": "lab"}
        ],
    )

    class _Plan:
        def to_payload(self):
            return {
                "status": "ready",
                "missing": [{"import_name": "openpyxl", "distribution": "openpyxl"}],
                "unknown": [],
            }

    monkeypatch.setattr(
        deprepair, "joint_plan_for", lambda root, script: (_Plan(), "tavotto_managed", BEFORE)
    )
    state = {"has_openpyxl": True}

    def probe(python, module=None, *, modules=()):
        return {
            "ok": True,
            "code": "",
            "support": "verified",
            "python_version": "3.12.1",
            "matplotlib_version": "3.10.0",
            "modules_ok": {name: state["has_openpyxl"] for name in modules},
        }

    monkeypatch.setattr(projectenv, "probe_environment", probe)

    def adopt():
        return client.patch(
            "/api/engine/environment",
            json={
                "scope": "project",
                "user_environment": userenvs.env_id(str(env)),
                "script": "figure.py",
            },
        )

    return {"project": project, "env": env, "state": state, "adopt": adopt}


def test_adopting_an_environment_that_changed_while_the_dialog_was_open_is_refused(adopt_api):
    """弹窗列出时装齐（这一次体检进了缓存），点下去之前 openpyxl 被卸了：采用前按此刻的计划重量一次，
    `user_environment_incomplete` + 缺什么，什么都不记。以前只核「还被发现得到」+ 不带模块的体检，照样记下、
    关框，下一次渲染撞同一个缺包。"""
    project, env = adopt_api["project"], adopt_api["env"]
    listed = deprepair.user_environment_offer(
        project,
        "figure.py",
        {"missing": [{"import_name": "openpyxl", "distribution": "openpyxl"}], "unknown": []},
        BEFORE,
    )
    assert [e["satisfies"] for e in listed] == [True], "弹窗打开那一刻它是装齐的"
    adopt_api["state"]["has_openpyxl"] = False
    resp = adopt_api["adopt"]()
    assert resp.status_code == 400, resp.get_json()
    body = resp.get_json()
    assert body["code"] == "user_environment_incomplete"
    assert body["params"] == {"packages": "openpyxl"}
    assert str(env) not in json.dumps(body)
    assert projectenv.remembered_record(project) is None


def test_the_uncached_recheck_never_accepts_a_result_slipped_into_the_cache(monkeypatch):
    """Codex #562 P2 的交错，确定性地复现：复核（`use_cache=False`）量的必须是此刻的环境。旧实现先把缓存那条
    删掉、放锁，再走 `_probe()` 重新读缓存——两步之间并发的一次更早开始的体检可以把旧结论（「装齐」）塞回去，
    复核就收下了它。这里用缓存的替身把那一刻钉死：每次被删之后立刻有人塞回一条过期的「装齐」，不用 sleep。
    判据：复核回的是真体检的结论（缺 openpyxl），不是塞进来的那条；真体检确实跑了（尺子是活的）。"""
    stale = {
        "ok": True,
        "support": "verified",
        "python_version": "3.12.1",
        "modules_ok": {"openpyxl": True},
    }
    imports = ("openpyxl",)
    key = (userenvs._key("/lab/python"), imports, False)  # 第三维：是不是按内置 runtime 的环境量的

    class Racy(dict):
        def pop(self, k, *default):
            got = super().pop(k, *default)
            self[k] = dict(stale)  # 另一个线程的旧体检恰好在这一刻写回
            return got

    monkeypatch.setattr(userenvs, "_probe_cache", Racy({key: dict(stale)}))
    probed = []

    def fresh(python, module=None, *, modules=()):
        probed.append((python, tuple(modules)))
        return {**stale, "modules_ok": {"openpyxl": False}}

    monkeypatch.setattr(projectenv, "probe_environment", fresh)
    got = userenvs.evaluate(
        [{"python": "/lab/python", "source": userenvs.SOURCE_CONDA, "label": "lab"}],
        [{"import_name": "openpyxl", "distribution": "openpyxl"}],
        [],
        use_cache=False,
    )[0]
    assert probed == [("/lab/python", imports)], "复核真起了一次体检"
    assert got["satisfies"] is False and got["missing"] == ["openpyxl"], got
    # 走缓存的那条路（弹窗列表）照旧读缓存：同一个替身下它回的是缓存里的那条
    cached = userenvs.evaluate(
        [{"python": "/lab/python", "source": userenvs.SOURCE_CONDA, "label": "lab"}],
        [{"import_name": "openpyxl", "distribution": "openpyxl"}],
        [],
    )[0]
    assert cached["satisfies"] is True and len(probed) == 1


def test_adopting_when_the_plan_cannot_be_computed_is_refused(adopt_api, monkeypatch):
    """Codex #562 P2：复核时联合计划算不出来，以前退回空的需求集合——什么都不量，任何健康的环境都「装齐」、
    被记下。现在不知道脚本要什么就不下结论：`user_environment_unverifiable`，什么都不记。"""
    project = adopt_api["project"]

    def broken(root, script):
        raise engine_pool.WorkerError("解释器解析失败", code="no_worker_python")

    monkeypatch.setattr(deprepair, "joint_plan_for", broken)
    resp = adopt_api["adopt"]()
    assert resp.status_code == 409, resp.get_json()
    assert resp.get_json()["code"] == deprepair.ERROR_USER_ENV_UNVERIFIABLE
    assert projectenv.remembered_record(project) is None


def test_the_recheck_measures_everything_the_script_needs_not_only_the_current_gap(
    adopt_api, monkeypatch
):
    """同一形状（需求集合比判据要的窄）的另一处：计划的 `missing` 是相对**此刻的**解释器量的差集。内置
    runtime 有 numpy、缺 openpyxl 时，一个只装了 openpyxl 的环境按差集量是「装齐」，改用之后脚本在 numpy 上
    缺包。候选要量的是脚本开跑要的全部（`missing` + `satisfied`）。"""
    project = adopt_api["project"]

    class _Plan:
        def to_payload(self):
            return {
                "status": "ready",
                "missing": [{"import_name": "openpyxl", "distribution": "openpyxl"}],
                "satisfied": [{"import_name": "numpy", "distribution": "numpy"}],
                "unknown": [],
            }

    monkeypatch.setattr(
        deprepair, "joint_plan_for", lambda root, script: (_Plan(), "tavotto_managed", BEFORE)
    )
    probed = []

    def probe(python, module=None, *, modules=()):
        probed.append(tuple(modules))
        return {
            "ok": True,
            "code": "",
            "support": "verified",
            "python_version": "3.12.1",
            "matplotlib_version": "3.10.0",
            "modules_ok": {name: name == "openpyxl" for name in modules},
        }

    monkeypatch.setattr(projectenv, "probe_environment", probe)
    resp = adopt_api["adopt"]()
    assert probed and "numpy" in probed[-1], "尺子是活的：numpy 确实被量了"
    assert resp.status_code == 400, resp.get_json()
    assert resp.get_json()["params"] == {"packages": "numpy"}
    assert projectenv.remembered_record(project) is None


def test_adopting_a_complete_environment_is_remembered_as_the_users_choice(adopt_api):
    """对照组：装齐的照常采用，记成用户的选择（`automatic=False`）。"""
    project, env = adopt_api["project"], adopt_api["env"]
    resp = adopt_api["adopt"]()
    assert resp.status_code == 200, resp.get_json()
    record = projectenv.remembered_record(project)
    assert record["path"] == str(env) and record["automatic"] is False
    assert record["trigger"] == deprepair.TRIGGER_USER_ENVIRONMENT


def test_the_switch_turns_discovery_off(tmp_path, monkeypatch):
    """`TAVOTTO_USER_ENV_DISCOVERY=0`：不发现、不体检（测试进程默认就是这样；也是用户的逃生口）。"""
    monkeypatch.setenv("TAVOTTO_USER_ENV_DISCOVERY", "0")
    called = []
    monkeypatch.setattr(userenvs, "discover", lambda *a: called.append(a) or [])
    plan = {"missing": [{"import_name": "openpyxl", "distribution": "openpyxl"}], "unknown": []}
    assert deprepair.user_environment_offer(tmp_path, "fig.py", plan, "") == []
    assert called == []


# ------------------------------------------------ 映射不到包名的 import（ADR 0079 修订 2026-09-25）
#
# QA ENV-08-B1：脚本唯一缺的 import 映射不到分发名 → 计划是 `nothing_needed` 而不是 `ready` → 以前从不
# 去找用户环境。这一组用**真解释器**：隔离 HOME 里摆 Conda 的目录布局（base = ~/miniforge3，具名环境
# = ~/miniforge3/envs/lab，都是真 venv），lab 里多一个 `qa_probe_pkg`。

try:
    _WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    _WORKER_PY = None

_needs_worker = pytest.mark.skipif(
    _WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

_FIGURE_TAIL = (
    "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
    "fig, ax = plt.subplots()\nax.plot([0, 1])\nfig.savefig('Fig1.png')\n"
)


def _freeze(python: str) -> str:
    import subprocess

    out = subprocess.run(
        [python, "-m", "pip", "freeze", "--all"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=True,
    )
    return out.stdout


@pytest.fixture
def conda_layout(tmp_path, home, monkeypatch):
    """`env08_conda_layout_feasibility.py` 的场景，落成正式用例的夹具。"""
    from support import venvfixture

    base = venvfixture.make_project_venv(home, "miniforge3", python=_WORKER_PY)
    (base / "envs").mkdir()
    lab = venvfixture.make_project_venv(base / "envs", "lab", python=_WORKER_PY)
    site = next(p for p in lab.rglob("site-packages") if p.is_dir())
    (site / "qa_probe_pkg").mkdir()
    (site / "qa_probe_pkg" / "__init__.py").write_text("VALUE = 7\n", encoding="utf-8")
    (home / ".conda").mkdir()
    (home / ".conda" / "environments.txt").write_text(f"{base}\n{lab}\n", encoding="utf-8")
    project = tmp_path / "paper"
    project.mkdir()
    (project / "environment.yml").write_text("name: lab\ndependencies:\n  - matplotlib\n")
    # 这台机器上真实的系统 Python 不进候选：结果不许随机器变
    monkeypatch.setattr(engine_pool, "system_python_candidates", lambda: [])
    discovered = []
    real_discover = userenvs.discover
    monkeypatch.setattr(userenvs, "discover", lambda *a: discovered.append(a) or real_discover(*a))
    pythons = {
        "base": projectenv.interpreter_of(base),
        "lab": projectenv.interpreter_of(lab),
        "current": engine_pool.find_worker_python(),
    }
    frozen = {k: _freeze(v) for k, v in pythons.items()}
    yield {"project": project, "pythons": pythons, "discovered": discovered, "lab": lab}
    engine_pool.shutdown_all(str(project), wait=True)
    # 全程无安装：三个解释器前后 `pip freeze` 逐字相同
    for k, v in pythons.items():
        assert _freeze(v) == frozen[k], f"{k} 的已装包变了——这一支只找、只改用，绝不安装"


@POSIX
@_needs_worker
def test_an_unmapped_import_finds_and_adopts_the_named_conda_env_that_has_it(conda_layout):
    """变异反证：拿掉 `_preparation_offer` 里 `nothing_needed` 那一支，或把 `decide_environment` 的条件改回
    只认 `ready`，脚本就在此刻的解释器里跑（`runtime.prefix` 不是 lab）。"""
    project, py = conda_layout["project"], conda_layout["pythons"]
    (project / "figure.py").write_text("import qa_probe_pkg\n" + _FIGURE_TAIL, encoding="utf-8")
    offer = deprepair.preparation_offer(project, "figure.py")
    assert offer["plan"]["status"] == "nothing_needed", offer["plan"]["status"]
    assert offer["plan"]["unknown"] == ["qa_probe_pkg"]
    assert offer["unknown_missing"] == ["qa_probe_pkg"]
    complete = [e for e in offer["user_environments"] if e["satisfies"]]
    assert [e["label"] for e in complete] == ["lab"], offer["user_environments"]
    assert str(conda_layout["lab"]) not in json.dumps(offer), "载荷不带路径（ADR 0053 §二）"
    # 起会话：决定在解析解释器之前（`pool.ENVIRONMENT_DECIDERS`）
    worker, resp = engine_pool.build("figure.py", str(project), "__main__")
    assert Path(resp["runtime"]["prefix"]).resolve() == conda_layout["lab"].resolve()
    # 同一个环境的 `python` / `python3` 两个别名是一个候选（ADR 0079 §一的去重键按所在目录）
    assert Path(worker.python).parent == Path(py["lab"]).parent
    assert Path(worker.python).parent != Path(py["base"]).parent, "base 不被使用"
    record = projectenv.remembered_record(project)
    assert record["automatic"] is True and record["trigger"] == deprepair.TRIGGER_USER_ENVIRONMENT
    assert Path(record["path"]).parent == Path(py["lab"]).parent


@POSIX
@_needs_worker
def test_an_unmapped_import_the_current_interpreter_has_triggers_no_discovery(conda_layout):
    """对照组：`pyparsing` 映射不到包名（进 `unknown`），但 matplotlib 依赖它、此刻的解释器 import 得到——
    不发现、不体检候选、不换环境。变异反证：`userenvs.imports_missing` 不看体检、把 unknown 全当缺，
    `discover` 就被调用。"""
    project = conda_layout["project"]
    (project / "figure.py").write_text("import pyparsing\n" + _FIGURE_TAIL, encoding="utf-8")
    offer = deprepair.preparation_offer(project, "figure.py")
    assert offer["plan"]["unknown"] == ["pyparsing"], "前提：它确实是 unknown"
    assert offer["unknown_missing"] == [] and offer["user_environments"] == []
    worker, _ = engine_pool.build("figure.py", str(project), "__main__")
    assert engine_pool.same_python(worker.python, conda_layout["pythons"]["current"])
    assert conda_layout["discovered"] == []
    assert projectenv.remembered_record(project) is None


@POSIX
@_needs_worker
def test_a_conditional_unmapped_import_triggers_no_discovery(conda_layout):
    """对照组：`try/except ImportError` 包着的 import 不在 `unknown` 里（只进 `possible`），缺了是脚本自己
    兜住的——不为它换环境。变异反证：`depplan` 收 unknown 时不再只认 `CONTEXT_UNCONDITIONAL`，lab 就被采用。"""
    project = conda_layout["project"]
    (project / "figure.py").write_text(
        "try:\n    import qa_probe_pkg\nexcept ImportError:\n    qa_probe_pkg = None\n"
        + _FIGURE_TAIL,
        encoding="utf-8",
    )
    offer = deprepair.preparation_offer(project, "figure.py")
    assert offer["plan"]["unknown"] == [] and offer["unknown_missing"] == []
    worker, _ = engine_pool.build("figure.py", str(project), "__main__")
    assert engine_pool.same_python(worker.python, conda_layout["pythons"]["current"])
    assert conda_layout["discovered"] == []
    assert projectenv.remembered_record(project) is None


def test_an_import_that_could_not_be_measured_is_not_counted_as_missing(monkeypatch):
    """判不出的不算缺：体检起不来（没有 `modules_ok`）、或结果里没有这一项，都不触发发现。
    变异反证：`imports_missing` 用 `is not True` 代替 `is False`，这里回出两个名字。"""
    monkeypatch.setattr(
        projectenv, "probe_environment", lambda python, module=None, *, modules=(): {"ok": False}
    )
    assert userenvs.imports_missing("/nowhere/python", ["qa_a", "qa_b"]) == []
    userenvs.reset_cache()
    monkeypatch.setattr(
        projectenv,
        "probe_environment",
        lambda python, module=None, *, modules=(): {"ok": True, "modules_ok": {"qa_a": False}},
    )
    assert userenvs.imports_missing("/nowhere/python", ["qa_a", "qa_b"]) == ["qa_a"]


def test_the_switch_also_stops_measuring_unknown_imports(monkeypatch):
    monkeypatch.setenv("TAVOTTO_USER_ENV_DISCOVERY", "0")
    called = []
    monkeypatch.setattr(userenvs, "imports_missing", lambda *a: called.append(a) or ["x"])
    assert deprepair.unknown_imports_missing({"unknown": ["x"]}, "/p/python") == []
    assert called == []


@POSIX
@_needs_worker
def test_the_bundled_interpreter_is_measured_with_the_workers_own_environment(
    tmp_path, monkeypatch
):
    """Codex #609 P2：此刻的解释器是内置 runtime 时，worker 起它用的是 `runtime.child_env()` /
    `child_args()`（摘掉 `PYTHONPATH` 等、带 `-B`）。体检若不用同一套，从终端启动、shell 里
    `PYTHONPATH` 指着某个包时，体检说「import 得到」、worker 却缺它，发现就被错过了。

    变异反证：`unknown_imports_missing` 不把内置 runtime 认出来（`bundled=False`），第二个断言回 []。
    """
    from tavotto.engine import runtime

    shell_path = tmp_path / "shell_path"
    (shell_path / "qa_shell_pkg").mkdir(parents=True)
    (shell_path / "qa_shell_pkg" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(shell_path))
    plan = {"unknown": ["qa_shell_pkg"]}
    # 对照：用户自己的解释器——worker 继承同一份环境，import 得到就是 import 得到
    monkeypatch.setattr(runtime, "bundled_python", lambda: None)
    assert deprepair.unknown_imports_missing(plan, _WORKER_PY) == []
    # 同一个解释器当内置 runtime：按 worker 的环境量，`PYTHONPATH` 被摘掉，确实缺
    monkeypatch.setattr(runtime, "bundled_python", lambda: _WORKER_PY)
    assert deprepair.unknown_imports_missing(plan, _WORKER_PY) == ["qa_shell_pkg"]
