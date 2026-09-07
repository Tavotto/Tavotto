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
    assert workdir.SETTINGS_KEY not in engine_config.project_settings(str(a))
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
