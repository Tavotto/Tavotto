"""safe 档的工作目录模式：项目级「在脚本目录里运行」（ADR 0047）。

四层看护：

* **spec**：默认模式的 argv 逐字节不变（golden 在 test_execspec）；project 模式只多
  `--cwd <脚本目录>`，`--sandbox` 仍在；`cwd_mode` 进 stable_payload；native 没有这个维度。
* **开关**：项目级、不写全局；不认识的值当默认；改了就关掉该项目的会话。
* **真 worker**：沙盒模式下 `exists()` 为假、零张图；project 模式下 exists / glob /
  listdir / 相对 open 全部成立、图被捕获；**守卫与 savefig 捕获一字不动**（原件不被删、
  savefig 不落盘）；相对路径**写**的中间文件落进项目——这是定义，不是漏洞。
* **同源**：Python 池、workerd spawn 规格、one_shot 三处从同一个出处取模式。
"""

import os
from pathlib import Path

import pytest

from tavotto.engine import (
    config as engine_config,
    execspec,
    pool as engine_pool,
    workdir,
)

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

#: 用户脚本的真实形状（2026-09-06 的九个 ovito 脚本）：相对路径 + exists / glob /
#: listdir，只有全部成立才画图；顺手写一个相对路径的中间文件、删一个「过期输出」。
SCRIPT = """\
import glob
import os
import shutil
from pathlib import Path
import matplotlib.pyplot as plt

found = os.path.exists("1/etch_5-5.lammpstrj")
globbed = glob.glob("./**/*.lammpstrj", recursive=True)
listed = "1" in os.listdir(".")
print(f"[probe] exists={found} glob={len(globbed)} listdir={listed} cwd={os.getcwd()}")
if found and globbed and listed:
    with open("1/etch_5-5.lammpstrj") as f:
        n = len(f.read().split())
    os.makedirs("cache", exist_ok=True)
    with open("cache/impact.txt", "w") as f:
        f.write(str(n))
    Path("stale_output.png").unlink()   # 守卫：真实图库里的文件不许删
    os.remove("stale_output.png")       # 同一条守卫的另外几个入口
    shutil.rmtree("1")
    os.rename("stale_output.png", "renamed.png")
    fig, ax = plt.subplots()
    ax.plot([1, 2], [3, n])
    fig.savefig("impact_histogram.png")  # 捕获，不落盘
else:
    print("[ERROR] 文件未找到")
"""


@pytest.fixture
def figs(tmp_path):
    root = tmp_path / "figs"
    root.mkdir()
    (root / "run_all.py").write_text(SCRIPT, encoding="utf-8")
    (root / "1").mkdir()
    (root / "1" / "etch_5-5.lammpstrj").write_text("a b c d", encoding="utf-8")
    (root / "stale_output.png").write_bytes(b"orig")
    yield root
    engine_pool.shutdown_all(str(root), wait=True)
    workdir.set_mode(root, workdir.MODE_SANDBOX)


# --------------------------------------------------------------- spec
def _spec(mode):
    return execspec.safe_spec(
        "sub/fig.py", "/proj", "main", interpreter="/usr/bin/python3", sandbox="/box", cwd_mode=mode
    )


def test_project_mode_only_adds_cwd_and_keeps_the_sandbox():
    default = execspec.worker_argv(_spec(execspec.CWD_SANDBOX), worker_py="/w.py", out_dir="/o")
    project = execspec.worker_argv(_spec(execspec.CWD_PROJECT), worker_py="/w.py", out_dir="/o")
    assert "--cwd" not in default
    assert project[: len(default)] == default, "默认部分逐字节不变"
    assert project[len(default) :] == ["--cwd", str(Path("/proj") / "sub")]
    assert project[project.index("--sandbox") + 1] == "/box"


def test_cwd_mode_is_execution_semantics_not_a_path():
    s = _spec(execspec.CWD_PROJECT)
    assert s.cwd == str(Path("/proj") / "sub") and s.sandbox == "/box"
    assert s.stable_payload()["cwd_mode"] == "project"
    back = execspec.spec_from_payload(s.to_payload())
    assert back == s
    # 老 payload（没有这两个字段）读回来是默认模式
    legacy = {k: v for k, v in _spec(execspec.CWD_SANDBOX).to_payload().items()}
    legacy.pop("cwd_mode"), legacy.pop("sandbox")
    assert execspec.spec_from_payload(legacy).cwd_mode == execspec.CWD_SANDBOX
    with pytest.raises(ValueError, match="cwd_mode"):
        _spec("elsewhere")
    with pytest.raises(ValueError, match="native"):
        execspec.ExecutionSpec(
            profile=execspec.PROFILE_NATIVE,
            interpreter="/usr/bin/python3",
            target_kind=execspec.TARGET_SCRIPT,
            target="fig.py",
            entry=None,
            argv=(),
            cwd="/home/u",
            env=None,
            project_root="/home/u",
            passthrough_savefig=True,
            raw_target="fig.py",
            cwd_mode=execspec.CWD_PROJECT,
        )


# --------------------------------------------------------------- 开关
def test_the_switch_is_project_scoped_and_defaults_to_the_sandbox(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    assert workdir.mode_for(a) == workdir.MODE_SANDBOX
    workdir.set_mode(a, workdir.MODE_PROJECT)
    assert workdir.mode_for(a) == workdir.MODE_PROJECT
    assert workdir.mode_for(b) == workdir.MODE_SANDBOX
    assert engine_config.load().get("worker", {}).get("workdir") is None
    workdir.set_mode(a, workdir.MODE_SANDBOX)
    # 切回沙盒：授权记录消失，但「决定过」留着（U03，首开确认框不再问）；`forget()` 才回到没决定
    stored = engine_config.project_settings(str(a))[workdir.SETTINGS_KEY]
    assert stored["mode"] == workdir.MODE_SANDBOX and workdir.GRANT_KEY not in stored
    assert workdir.decided(a) is True
    workdir.forget(a)
    assert workdir.SETTINGS_KEY not in engine_config.project_settings(str(a))
    assert workdir.decided(a) is False
    with pytest.raises(ValueError):
        workdir.set_mode(a, "native")
    # 设置文件被手改坏：写入边界不许悄悄消失
    engine_config.set_project_settings(str(a), {workdir.SETTINGS_KEY: {"mode": "anything"}})
    assert workdir.mode_for(a) == workdir.MODE_SANDBOX


def test_all_three_spawn_paths_take_the_mode_from_one_place(monkeypatch, tmp_path):
    box = {}

    class _Rec:
        def __init__(self, argv, **kw):
            box.setdefault("argv", []).append(argv)
            self.pid = 1

        def poll(self):
            return None

    monkeypatch.setattr(engine_pool.subprocess, "Popen", _Rec)
    monkeypatch.setattr(
        engine_pool, "select_worker_python", lambda: ("/usr/bin/python3", engine_pool.SOURCE_SYSTEM)
    )
    workdir.set_mode(tmp_path, workdir.MODE_PROJECT)
    w = engine_pool.EngineWorker("fig.py", str(tmp_path), "draw")
    assert w.spec.cwd_mode == execspec.CWD_PROJECT
    assert box["argv"][-1][-2:] == ["--cwd", str(tmp_path)]
    spec = engine_pool._spawn_spec(
        "fig.py",
        str(tmp_path),
        "draw",
        w.out_dir,
        w.sandbox,
        w.log_path,
        "/usr/bin/python3",
        engine_pool.SOURCE_SYSTEM,
    )
    assert spec["argv"] == box["argv"][-1]
    shot = engine_pool.one_shot("fig.py", str(tmp_path), "draw")
    try:
        assert shot.spec.cwd_mode == execspec.CWD_PROJECT
    finally:
        engine_pool.discard(shot)
    workdir.set_mode(tmp_path, workdir.MODE_SANDBOX)


# --------------------------------------------------------------- 真 worker
@needs_worker
def test_sandbox_mode_still_hides_relative_data_from_exists_and_glob(figs):
    # 没决定过的话首开会先问（ADR 0084：exists / glob 是「沙盒不够用」的证据）；这里钉的是
    # 用户**选了沙盒**之后盲区依旧——回退不扩到 exists / glob
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    worker, resp = engine_pool.build("run_all.py", str(figs), "__main__")
    assert resp.get("stems") == {}
    assert worker.spec.cwd_mode == execspec.CWD_SANDBOX
    assert "[ERROR] 文件未找到" in worker._log_tail()


@needs_worker
def test_project_mode_runs_the_script_where_it_lives(figs):
    workdir.set_mode(figs, workdir.MODE_PROJECT)
    worker, resp = engine_pool.build("run_all.py", str(figs), "__main__")
    assert sorted(resp.get("stems") or {}) == ["impact_histogram"]
    assert worker.spec.cwd_mode == execspec.CWD_PROJECT
    tail = worker._log_tail()
    assert "exists=True glob=1 listdir=True" in tail
    assert os.path.realpath(str(figs)) in tail  # cwd 就是脚本目录
    # 定义，不是漏洞：相对路径**写**的中间文件落进项目
    assert (figs / "cache" / "impact.txt").read_text(encoding="utf-8") == "4"
    # 守卫一字不动，而且不止 Path.unlink 一个入口：删除 / 删目录树 / 改名全被拦下
    assert (figs / "stale_output.png").read_bytes() == b"orig"
    assert (figs / "1" / "etch_5-5.lammpstrj").is_file()
    assert not (figs / "renamed.png").exists()
    assert tail.count("[guard]") >= 4
    # savefig 仍是捕获、不落盘
    assert not (figs / "impact_histogram.png").exists()
    # 沙盒目录仍然存在（写入边界的参照），但脚本没往里写任何东西
    assert worker.sandbox.is_dir() and not any(worker.sandbox.iterdir())


@needs_worker
def test_switching_the_mode_restarts_the_sessions_of_that_project(client, figs):
    from tavotto import app as m

    m.open_project(str(figs))
    workdir.set_mode(figs, workdir.MODE_SANDBOX)  # 已决定用沙盒（否则首开先问，ADR 0084）
    before, _ = engine_pool.build("run_all.py", str(figs), "__main__")
    assert (
        client.get("/api/engine/environment").get_json()["project"]["workdir"]["mode"] == "sandbox"
    )
    resp = client.patch("/api/engine/workdir", json={"mode": "native"})
    assert resp.status_code == 400 and resp.get_json()["code"] == "workdir_mode_invalid"
    resp = client.patch("/api/engine/workdir", json={"mode": "project"})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["workdir"]["mode"] == "project"
    assert resp.get_json()["project"]["workdir"]["mode"] == "project"
    after, resp2 = engine_pool.build("run_all.py", str(figs), "__main__")
    assert after is not before, "旧会话端着旧 cwd，必须重建"
    assert after.spec.cwd_mode == execspec.CWD_PROJECT
    assert sorted(resp2.get("stems") or {}) == ["impact_histogram"]
    # 全局设置一个字节没动
    assert engine_config.load().get("worker", {}).get("workdir") is None
    for pid in [p for p, ctx in list(m.PROJECTS.items()) if str(ctx.path) == str(figs)]:
        m.close_project(pid, wait=True)


@pytest.fixture
def client():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    return m.app.test_client()


# ------------------------------- 用户自己的 Python：默认在脚本目录里跑（ADR 0107 §二）
# 用户 2026-09-28 拍板：采用用户自己的 Python（缺包时自动采用的，或在渲染环境里为项目挑的）时，脚本的
# 默认运行目录是脚本目录——与原生 `cd 脚本目录 && python fig.py` 一致。派生的默认，不写设置；用户选过
# 任何一档都按他选的；内置 / 受管 / 项目 venv / 全局显式选择仍默认沙盒。
def _no_global_choice(monkeypatch):
    """外面带进来的 `TAVOTTO_WORKER_PYTHON` / 设置不许让用例结果随机器变。"""
    monkeypatch.setattr(engine_pool, "explicit_worker_python", lambda: None)
    monkeypatch.setattr(engine_pool.config, "worker_python", lambda: None)


def _python_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return str(path)


@pytest.mark.parametrize(
    "case, expected",
    [
        ("nothing_remembered", workdir.MODE_SANDBOX),  # 内置 / 老链条
        ("project_venv", workdir.MODE_SANDBOX),
        ("managed", workdir.MODE_SANDBOX),
        ("default_chain", workdir.MODE_SANDBOX),
        ("system_auto", workdir.MODE_PROJECT),
        ("system_user_picked", workdir.MODE_PROJECT),
        ("system_but_global_explicit", workdir.MODE_SANDBOX),
        ("system_but_configured", workdir.MODE_SANDBOX),
        ("system_file_gone", workdir.MODE_SANDBOX),
    ],
)
def test_the_undecided_default_follows_the_users_own_interpreter(
    tmp_path, monkeypatch, case, expected
):
    from tavotto.engine import managedenv, projectenv

    _no_global_choice(monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    outside = _python_file(tmp_path / "machine" / "python")
    if case == "project_venv":
        projectenv.remember(
            root,
            _python_file(root / ".venv" / "bin" / "python"),
            automatic=True,
            trigger="first_open",
        )
    elif case == "managed":
        projectenv.remember(root, outside, automatic=False, trigger="dependency_repair")
        monkeypatch.setattr(managedenv, "is_managed_python", lambda r, p: True)
    elif case == "default_chain":
        projectenv.remember_default(root)
    elif case == "system_auto":
        projectenv.remember(root, outside, automatic=True, trigger="missing_dependency")
    elif case == "system_user_picked":
        projectenv.remember(root, outside, automatic=False, trigger="user_selected")
    elif case == "system_but_global_explicit":
        projectenv.remember(root, outside, automatic=True, trigger="missing_dependency")
        monkeypatch.setattr(
            engine_pool, "explicit_worker_python", lambda: ("/usr/bin/python3", "env_override")
        )
    elif case == "system_but_configured":
        projectenv.remember(root, outside, automatic=True, trigger="missing_dependency")
        monkeypatch.setattr(engine_pool.config, "worker_python", lambda: "/usr/bin/python3")
    elif case == "system_file_gone":
        projectenv.remember(root, outside, automatic=True, trigger="missing_dependency")
        Path(outside).unlink()
    try:
        assert workdir.mode_for(root) == expected, case
        # 准备计划读的那一份（`decision_for`：LaunchContext 的 cwd_mode）与 spawn 读的同一个默认
        (root / "fig.py").write_text("import matplotlib\n", encoding="utf-8")
        decision = workdir.decision_for(root, "fig.py")
        assert (decision["mode"], decision["decided"]) == (expected, False), case
        implied = expected == workdir.MODE_PROJECT
        assert workdir.implied_by(root) == (
            workdir.IMPLIED_BY_USER_INTERPRETER if implied else None
        )
        grant = workdir.grant_for(root)
        # 派生的默认：写入许可成立、但没有那一次点头的时刻；「决定过」仍是 False（没写任何设置）
        assert grant["cwd_write"] == {
            "granted": implied,
            "granted_at": None,
            "mode": workdir.MODE_PROJECT if implied else None,
        }
        assert grant["decided"] is False and workdir.decided(root) is False
        assert grant["implied_by"] == workdir.implied_by(root)
        assert workdir.state(root)["implied_by"] == workdir.implied_by(root)
        assert workdir.SETTINGS_KEY not in (engine_config.project_settings(str(root)) or {})
    finally:
        projectenv.forget(root)


def test_the_user_interpreter_probe_is_the_pools_and_absent_means_sandbox(monkeypatch, tmp_path):
    """workdir 不 import pool（否则进 bootstrap / managedenv / pool 那个环）：判据由 pool 加载时登记。
    登记的必须就是 pool 那一份；没登记时默认回沙盒（更窄那一档），不因缺判据放宽。"""
    assert workdir._user_interpreter_probe is engine_pool.user_interpreter_in_effect
    monkeypatch.setattr(workdir, "_user_interpreter_probe", None)
    assert workdir.implied_by(tmp_path) is None
    assert workdir.default_mode(tmp_path) == workdir.MODE_SANDBOX
    monkeypatch.setattr(workdir, "_user_interpreter_probe", lambda root: True)
    assert workdir.implied_by(tmp_path) == workdir.IMPLIED_BY_USER_INTERPRETER
    assert workdir.default_mode(tmp_path) == workdir.MODE_PROJECT


def test_build_asks_before_retry_between_the_two_executions(monkeypatch):
    """`_build_with`：缺包 → 自动接手成功之后、**第二次执行之前**问一次 `before_retry`；它抛出就不再执行
    （准备接口据此在授权变了时作废计划，Codex #713 P1）。"""
    events = []

    class W:
        def __init__(self, n):
            self.n = n

        def ensure_built(self):
            events.append(f"build{self.n}")
            if self.n == 1:
                raise engine_pool.WorkerError("缺 lmfit", code="missing_dependency", module="lmfit")
            return {}

    takes = iter([W(1), W(2)])
    monkeypatch.setattr(
        engine_pool, "try_project_env", lambda *a: events.append("adopt") or {"ok": True}
    )

    class Stale(Exception):
        pass

    def guard():
        events.append("guard")
        raise Stale

    with pytest.raises(Stale):
        engine_pool._build_with(
            lambda: (next(takes), True), "fig.py", "/p", allow_project_env=True, before_retry=guard
        )
    assert events == ["build1", "adopt", "guard"]


def test_an_unpersisted_adoption_changes_neither_the_interpreter_nor_the_workdir(
    tmp_path, monkeypatch
):
    """Codex #713 P2 的前提核对：数据目录只读 / 满时 `remember()` 写不进项目设置（只进进程缓存），而
    `resolve_worker_python` 与 `user_interpreter_in_effect` 读的都是**持久化的记录**——两边一致地
    认为「没换」：不会出现「解释器是用户的、工作目录还是沙盒」的错位。"""
    from tavotto.engine import projectenv

    _no_global_choice(monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    outside = _python_file(tmp_path / "machine" / "python")

    def full(*_a, **_kw):
        raise OSError("磁盘满")

    monkeypatch.setattr(engine_config, "set_project_settings", full)
    try:
        projectenv.remember(root, outside, automatic=True, trigger="missing_dependency")
        python_now = engine_pool.resolve_worker_python(root, discover=False)[0]
        assert not engine_pool.same_python(python_now, outside)
        assert engine_pool.user_interpreter_in_effect(root) is False
        assert workdir.mode_for(root) == workdir.MODE_SANDBOX
    finally:
        projectenv.forget(root)


def test_a_users_choice_beats_the_implied_default_and_reverting_the_interpreter_reverts_it(
    tmp_path, monkeypatch
):
    """用户在渲染环境里改回沙盒 → 按他选的（不再被派生默认盖掉）；选项目根同理。解释器改回默认链条（「改回」）
    → 默认随之回到沙盒——派生默认不是写下来的设置，环境一变它就跟着变（不把临时状况变成长期设置）。"""
    from tavotto.engine import projectenv

    _no_global_choice(monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    outside = _python_file(tmp_path / "machine" / "python")
    projectenv.remember(root, outside, automatic=True, trigger="missing_dependency")
    try:
        assert workdir.mode_for(root) == workdir.MODE_PROJECT
        workdir.set_mode(root, workdir.MODE_SANDBOX)
        assert workdir.mode_for(root) == workdir.MODE_SANDBOX
        assert workdir.implied_by(root) is None
        assert workdir.grant_for(root)["cwd_write"]["granted"] is False
        workdir.set_mode(root, workdir.MODE_PROJECT_ROOT)
        assert workdir.mode_for(root) == workdir.MODE_PROJECT_ROOT
        workdir.forget(root)
        assert workdir.mode_for(root) == workdir.MODE_PROJECT
        projectenv.remember_default(root)  # 界面上的「改回」
        assert workdir.mode_for(root) == workdir.MODE_SANDBOX
        assert workdir.implied_by(root) is None
    finally:
        projectenv.forget(root)
        workdir.forget(root)


@needs_worker
def test_the_users_own_python_runs_the_script_where_it_lives_until_they_pick_the_sandbox(
    figs, tmp_path, monkeypatch
):
    """真 worker：项目用的是用户自己的 Python（项目外的解释器）→ 没决定过也在脚本目录里跑——exists / glob /
    相对写全部成立，**不问**（ADR 0084 的证据只指向脚本目录，答案就是默认）；守卫与 savefig 捕获一字不动。
    用户改回沙盒之后按他选的：盲区回来，相对写不进项目。"""
    from tavotto.engine import projectenv

    _no_global_choice(monkeypatch)
    workdir.forget(figs)
    projectenv.remember(figs, WORKER_PY, automatic=False, trigger="user_selected")
    try:
        assert engine_pool.remembered_source(figs, WORKER_PY) == engine_pool.SOURCE_SYSTEM
        worker, resp = engine_pool.build("run_all.py", str(figs), "__main__")
        assert sorted(resp.get("stems") or {}) == ["impact_histogram"]
        assert worker.spec.cwd_mode == execspec.CWD_PROJECT
        assert "exists=True glob=1 listdir=True" in worker._log_tail()
        assert (figs / "cache" / "impact.txt").read_text(encoding="utf-8") == "4"
        assert (figs / "stale_output.png").read_bytes() == b"orig", "守卫原样"
        assert not (figs / "impact_histogram.png").exists(), "savefig 仍是捕获"
        assert workdir.decided(figs) is False, "派生的默认，没写设置"

        (figs / "cache" / "impact.txt").unlink()
        workdir.set_mode(figs, workdir.MODE_SANDBOX)  # 用户改回沙盒
        engine_pool.shutdown_all(str(figs), wait=True)
        worker, resp = engine_pool.build("run_all.py", str(figs), "__main__")
        assert worker.spec.cwd_mode == execspec.CWD_SANDBOX
        assert resp.get("stems") == {}
        assert not (figs / "cache" / "impact.txt").exists()
    finally:
        projectenv.forget(figs)
