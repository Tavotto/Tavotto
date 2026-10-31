"""#1254：沙盒 cwd 下 `glob` / `listdir` / `exists` 落空的运行时证据（ADR 0084 §运行时证据）。

脚本把模式当参数传进 helper（`def read_parts(pattern): glob.glob(pattern)`）时，首开的静态探测认不出来；
脚本在沙盒里 glob 回空、继续往下跑，死在下游（`x and y must have same first dimension`），诊断卡上看不出是
输入没找到。worker 的观察层（`figcapture.install_probe_observer`）只记「问了什么」，父进程按脚本目录再判
（`workdir.probe_hint`），错误上挂 `workdir_hint`（与首开确认框同形的「在脚本目录里运行」引导）。

三层看护：

* **观察层**：返回值逐项不变、只记相对目标且 cwd 还是沙盒、stdlib 内部的探路不记、名额有限、卸得干净；
* **判据**：脚本目录里找得到才出引导；项目外 / 找不到 / 不是沙盒模式一律没有；
* **真 worker**：沙盒下带引导且码与消息一字不变；同一项目切到脚本目录模式出图；已在脚本目录模式不装观察层。
"""

import glob
import os
from pathlib import Path

import pytest

from tavotto.engine import figcapture, pool as engine_pool, probe as engine_probe, workdir

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


# --------------------------------------------------------------- 观察层（进程内）
@pytest.fixture
def box(tmp_path, monkeypatch):
    """一个有些文件的 cwd；用例结束把观察层卸干净。"""
    d = tmp_path / "box"
    (d / "data").mkdir(parents=True)
    (d / "data" / "a_1.csv").write_text("1", encoding="utf-8")
    (d / "data" / "a_2.csv").write_text("2", encoding="utf-8")
    monkeypatch.chdir(d)
    installed = []

    def install():
        misses = figcapture.ProbeMisses()
        installed.append(figcapture.install_probe_observer(misses, str(d)))
        return misses

    yield install
    for undo in reversed(installed):
        undo()


def _answers():
    """脚本能问到的所有探路答案（含存在与不存在的）——观察层装不装，这份必须逐项相同。"""
    return {
        "glob_hit": glob.glob("data/a_*.csv"),
        "glob_miss": glob.glob("data/b_*.csv"),
        "glob_root_dir": glob.glob("a_*.csv", root_dir="data"),
        "iglob_hit": list(glob.iglob("data/a_*.csv")),
        "iglob_miss": list(glob.iglob("data/b_*.csv")),
        "listdir": sorted(os.listdir("data")),
        "listdir_cwd": sorted(os.listdir()),
        "exists": [os.path.exists("data"), os.path.exists("nope")],
        "isfile": [os.path.isfile("data/a_1.csv"), os.path.isfile("data")],
        "isdir": [os.path.isdir("data"), os.path.isdir("nope")],
        "p_glob": sorted(map(str, Path("data").glob("a_*.csv"))),
        "p_glob_miss": sorted(map(str, Path("data").glob("b_*.csv"))),
        "p_rglob": sorted(map(str, Path(".").rglob("*.csv"))),
        "p_iterdir": sorted(map(str, Path("data").iterdir())),
        "p_exists": [Path("data").exists(), Path("nope").exists()],
        "p_is_file": [Path("data/a_1.csv").is_file(), Path("data").is_file()],
        "p_is_dir": [Path("data").is_dir(), Path("nope").is_dir()],
    }


def test_the_observer_never_changes_what_the_script_gets_back(box):
    before = _answers()
    types_before = {
        "glob": type(glob.glob("data/*")),
        "listdir": type(os.listdir("data")),
        "exists": type(os.path.exists("data")),
    }
    misses = box()
    after = _answers()
    assert after == before
    assert types_before == {
        "glob": type(glob.glob("data/*")),
        "listdir": type(os.listdir("data")),
        "exists": type(os.path.exists("data")),
    }
    # 迭代器的元素与顺序逐项相同，异常原样穿过去
    assert list(glob.iglob("data/a_*.csv")) == before["iglob_hit"]
    with pytest.raises(FileNotFoundError):
        os.listdir("nope")
    with pytest.raises(FileNotFoundError):
        list(Path("nope").iterdir())
    with pytest.raises(FileNotFoundError):
        os.scandir("nope")
    assert misses.items  # 它确实在看


def test_returned_objects_are_the_originals_themselves(tmp_path, monkeypatch):
    """不只是「值相等」：list 返回的就是原函数交出来的那个对象（顺序、身份都不动）。"""
    monkeypatch.chdir(tmp_path)
    sentinel = ["z", "a", "m"]  # 故意不是排序的
    monkeypatch.setattr(glob, "glob", lambda *a, **k: sentinel)
    monkeypatch.setattr(os, "listdir", lambda *a, **k: sentinel)
    undo = figcapture.install_probe_observer(figcapture.ProbeMisses(), str(tmp_path))
    try:
        assert glob.glob("data/*") is sentinel
        assert os.listdir("data") is sentinel
    finally:
        undo()


def test_only_empty_relative_probes_in_the_sandbox_are_recorded(box, tmp_path, monkeypatch):
    (Path.cwd() / "empty").mkdir()
    misses = box()
    os.listdir("empty")  # 目录在、但空
    glob.glob("data/a_*.csv")  # 有结果：不记
    os.path.exists("data")
    os.listdir("data")
    glob.glob("data/b_*.csv")
    list(glob.iglob("data/c_*.csv"))
    os.path.exists("nope.txt")
    os.path.isdir("nodir")
    Path("nopath").exists()
    list(Path("data").glob("d_*.csv"))
    assert {(m["kind"], m["target"]) for m in misses.items} == {
        ("dir", "empty"),
        ("glob", "data/b_*.csv"),
        ("glob", "data/c_*.csv"),
        ("path", "nope.txt"),
        ("dir", "nodir"),
        ("path", "nopath"),
        ("glob", "data/d_*.csv"),
    }
    # 绝对路径、列 cwd 本身（说明不了什么）、`~`：不记
    before = len(misses.items)
    glob.glob(str(tmp_path / "elsewhere" / "*.csv"))
    os.path.exists(str(tmp_path / "gone"))
    glob.glob("*.nothing")  # 相对的、有通配：要记
    assert len(misses.items) == before + 1
    list(Path(".").iterdir())
    os.path.exists(".")
    assert len(misses.items) == before + 1
    # 脚本 chdir 走了就不是沙盒里的事实
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    os.path.exists("moved-away.txt")
    assert len(misses.items) == before + 1


def test_stdlib_internal_probing_is_not_recorded_and_the_cap_holds(box):
    misses = box()
    glob.glob("nodir/sub/*.csv")  # glob 内部逐段 isdir：只该有一条 glob 记录，没有 isdir 的杂音
    assert [(m["kind"], m["target"]) for m in misses.items] == [("glob", "nodir/sub/*.csv")]
    for i in range(figcapture.MAX_PROBE_MISSES * 3):
        os.path.exists(f"missing-{i}.txt")
    assert len(misses.items) == figcapture.MAX_PROBE_MISSES
    # 同一条再问不重复
    n = len(misses.items)
    os.path.exists("missing-0.txt")
    assert len(misses.items) == n


def test_recorded_flags_follow_the_call(box):
    misses = box()
    glob.glob("**/z.csv", recursive=True)
    glob.glob("z2.csv", root_dir="data")
    glob.glob("z3.csv", root_dir=os.path.abspath("data"))  # 绝对 root_dir 不依赖 cwd
    assert [(m["target"], m["recursive"]) for m in misses.items] == [
        ("**/z.csv", True),
        ("data/z2.csv", False),
    ]


def test_the_observer_restores_every_entry_it_wrapped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    originals = (glob.glob, glob.iglob, os.listdir, os.scandir, os.path.exists, os.path.isfile)
    originals += (os.path.isdir,)
    p_originals = (Path.glob, Path.rglob, Path.iterdir, Path.exists, Path.is_file, Path.is_dir)
    undo = figcapture.install_probe_observer(figcapture.ProbeMisses(), str(tmp_path))
    assert glob.glob is not originals[0] and Path.exists is not p_originals[3]
    undo()
    assert (
        glob.glob,
        glob.iglob,
        os.listdir,
        os.scandir,
        os.path.exists,
        os.path.isfile,
        os.path.isdir,
    ) == originals
    assert (
        Path.glob,
        Path.rglob,
        Path.iterdir,
        Path.exists,
        Path.is_file,
        Path.is_dir,
    ) == p_originals


# --------------------------------------------------------------- 判据（父进程）
@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "sub" / "data").mkdir(parents=True)
    (root / "sub" / "data" / "run_1.txt").write_text("1", encoding="utf-8")
    (root / "sub" / "fig.py").write_text("print()\n", encoding="utf-8")
    yield root
    workdir.forget(root)


def _glob(target, **kw):
    return {"kind": "glob", "target": target, "recursive": False, "hidden": False, **kw}


def test_the_hint_is_the_first_open_payload_with_runtime_evidence(project):
    hint = workdir.probe_hint(project, "sub/fig.py", [_glob("data/run_*.txt")])
    assert hint is not None
    assert hint["reason"] == workdir.REASON_SCRIPT_DIR_EVIDENCE
    assert hint["recommended"] == workdir.MODE_PROJECT
    assert hint["source"] == "runtime" and hint["kind"] == "workdir"
    by_mode = {o["mode"]: o for o in hint["options"]}
    assert by_mode[workdir.MODE_PROJECT]["found"] == ["data/run_*.txt"]
    assert by_mode[workdir.MODE_PROJECT]["recommended"] is True
    assert by_mode[workdir.MODE_SANDBOX]["found"] == []  # 回退救不回探路目标（ADR 0084）
    assert hint["probes"] == ["data/run_*.txt"]
    assert hint["answer"]["http"] == "PATCH /api/engine/workdir"


def test_no_hint_unless_the_script_dir_has_what_the_sandbox_lacked(project):
    # 脚本目录里也没有：问的是一个本来就不存在的东西
    assert workdir.probe_hint(project, "sub/fig.py", [_glob("data/none_*.txt")]) is None
    # 项目根下有、脚本目录下没有：不是 script_dir_evidence
    (project / "elsewhere").mkdir()
    (project / "elsewhere" / "x.txt").write_text("x", encoding="utf-8")
    assert workdir.probe_hint(project, "sub/fig.py", [_glob("elsewhere/*.txt")]) is None
    # 项目外不看（不列、不 stat）
    outside = project.parent / "outside"
    outside.mkdir()
    (outside / "o.txt").write_text("o", encoding="utf-8")
    assert workdir.probe_hint(project, "sub/fig.py", [_glob("../../outside/*.txt")]) is None
    # 没有记录 / 脏数据
    for junk in (None, [], "x", [{"kind": "nope", "target": "data"}, {"kind": "glob"}, 3]):
        assert workdir.probe_hint(project, "sub/fig.py", junk) is None


def test_the_three_kinds_are_judged_by_the_same_ruler_as_the_first_open(project):
    kinds = [
        {"kind": "dir", "target": "data"},
        {"kind": "path", "target": "data/run_1.txt"},
    ]
    for item in kinds:
        hint = workdir.probe_hint(project, "sub/fig.py", [item])
        assert hint is not None and hint["probes"] == [item["target"]]


def test_a_project_already_running_in_a_real_directory_gets_no_hint(project):
    workdir.set_mode(project, workdir.MODE_PROJECT)
    assert workdir.probe_hint(project, "sub/fig.py", [_glob("data/run_*.txt")]) is None
    workdir.set_mode(project, workdir.MODE_PROJECT_ROOT)
    assert workdir.probe_hint(project, "sub/fig.py", [_glob("data/run_*.txt")]) is None


# --------------------------------------------------------------- 出口：错误挂件 / HTTP / 试运行
def test_the_hint_rides_on_the_error_without_touching_code_or_message(project):
    err = engine_pool.WorkerError(
        "脚本执行失败: x and y must be the same size", code="script_error"
    )
    err.extra = {"probe_misses": [_glob("data/run_*.txt")]}
    out = engine_pool._offer_missing_input(err, "sub/fig.py", str(project))
    assert out is err and err.code == "script_error"
    assert str(err) == "脚本执行失败: x and y must be the same size"
    assert err.workdir_hint["reason"] == workdir.REASON_SCRIPT_DIR_EVIDENCE
    assert err.missing_input is None
    # 没有记录 / 别的码：不挂
    plain = engine_pool.WorkerError("boom", code="script_error")
    assert engine_pool._offer_missing_input(plain, "sub/fig.py", str(project)).workdir_hint is None
    other = engine_pool.WorkerError("x", code="worker_timeout")
    other.extra = {"probe_misses": [_glob("data/run_*.txt")]}
    assert engine_pool._offer_missing_input(other, "sub/fig.py", str(project)).workdir_hint is None


def test_http_body_and_probe_error_carry_the_hint(project):
    from tavotto import app as tavotto_app

    err = engine_pool.WorkerError("脚本执行失败: x", code="script_error")
    err.extra = {"probe_misses": [_glob("data/run_*.txt")]}
    engine_pool._offer_missing_input(err, "sub/fig.py", str(project))
    body = tavotto_app._worker_error_payload(err)
    assert body["workdir_hint"] == err.workdir_hint and "confirmation" not in body
    probed = engine_probe._error_from_worker(
        err, "main", figures_dir=str(project), script="sub/fig.py"
    )
    assert probed["workdir_hint"] == err.workdir_hint
    assert probed["code"] == "script_probe_failed"  # 码不变，不新造一套


# --------------------------------------------------------------- 真 worker
#: Guardian CG-07 的形状：模式是 helper 的参数，首开的静态探测认不出；沙盒里 glob 回空，之后死在 matplotlib
HELPER_SCRIPT = """\
import glob
import matplotlib.pyplot as plt


def _read_parts(pattern):
    out = []
    for p in sorted(glob.glob(pattern)):
        with open(p, encoding="utf-8") as f:
            out.extend(float(x) for x in f.read().split())
    return out


xs = _read_parts("data/run_*.txt")
ys = [1.0, 2.0, 3.0]
print(f"[parts] {len(xs)}")
fig, ax = plt.subplots()
ax.plot(xs, ys)
fig.savefig("p.png")
"""

#: 同一份数据、没出图的形状：先判空再自己收工
EMPTY_SCRIPT = """\
import glob
import matplotlib.pyplot as plt


def _parts(pattern):
    return sorted(glob.glob(pattern))


files = _parts("data/run_*.txt")
print(f"[files] {len(files)}")
if files:
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3])
    fig.savefig("q.png")
"""


@pytest.fixture
def figs(tmp_path):
    root = tmp_path / "figs"
    (root / "data").mkdir(parents=True)
    (root / "data" / "run_1.txt").write_text("1 2 3", encoding="utf-8")
    yield root
    engine_pool.shutdown_all(str(root), wait=True)
    workdir.forget(root)


@needs_worker
def test_a_helper_glob_that_is_empty_in_the_sandbox_points_at_the_script_dir(figs):
    (figs / "fig.py").write_text(HELPER_SCRIPT, encoding="utf-8")
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.build("fig.py", str(figs), "__main__")
    exc = err.value
    # 码与消息是脚本自己的失败，一字不变（只诊断、不新造码）
    assert exc.code == "script_error"
    assert "same" in str(exc)
    assert exc.missing_input is None
    hint = exc.workdir_hint
    assert hint is not None
    assert hint["reason"] == workdir.REASON_SCRIPT_DIR_EVIDENCE
    assert hint["recommended"] == workdir.MODE_PROJECT
    assert {o["mode"]: o["found"] for o in hint["options"]}[workdir.MODE_PROJECT] == [
        "data/run_*.txt"
    ]

    # 同一个项目切到脚本目录模式：出图，且不装观察层（也就没有引导）
    workdir.set_mode(figs, workdir.MODE_PROJECT)
    engine_pool.shutdown_all(str(figs), wait=True)
    worker, resp = engine_pool.build("fig.py", str(figs), "__main__")
    assert sorted(resp.get("stems") or {}) == ["p"]
    assert "[parts] 3" in worker._log_tail()
    assert "probe_misses" not in resp


@needs_worker
def test_a_glob_that_finds_nothing_anywhere_gets_no_hint(figs):
    (figs / "fig.py").write_text(
        HELPER_SCRIPT.replace("data/run_*.txt", "data/never_*.txt"), encoding="utf-8"
    )
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.build("fig.py", str(figs), "__main__")
    assert err.value.code == "script_error"
    assert err.value.workdir_hint is None


@needs_worker
def test_an_empty_glob_that_ends_in_no_figures_carries_the_same_hint(figs):
    (figs / "fig.py").write_text(EMPTY_SCRIPT, encoding="utf-8")
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    # 渲染入口：build 成功、零张图，请求某个 stem 时报 no_figures_captured*
    worker, resp = engine_pool.build("fig.py", str(figs), "__main__")
    assert resp.get("stems") == {}
    assert [m["target"] for m in resp["probe_misses"]] == ["data/run_*.txt"]
    with pytest.raises(engine_pool.WorkerError) as err:
        worker.override("q", [])
    assert err.value.code in (engine_pool.NO_FIGURES_CODE, engine_pool.NO_FIGURES_SILENT_CODE)
    assert err.value.workdir_hint["reason"] == workdir.REASON_SCRIPT_DIR_EVIDENCE


@needs_worker
def test_the_probe_reports_the_hint_for_a_failing_script(figs):
    (figs / "fig.py").write_text(HELPER_SCRIPT, encoding="utf-8")
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    result = engine_probe.probe(str(figs), "fig.py")
    assert result["error"]["code"] == "script_probe_failed"
    assert result["error"]["workdir_hint"]["reason"] == workdir.REASON_SCRIPT_DIR_EVIDENCE


@needs_worker
def test_the_probe_reports_the_hint_when_it_ran_through_without_a_figure(figs):
    (figs / "fig.py").write_text(EMPTY_SCRIPT, encoding="utf-8")
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    result = engine_probe.probe(str(figs), "fig.py")
    assert result["error"]["code"] == engine_probe.ERROR_NO_FIGURE
    assert result["error"]["workdir_hint"]["recommended"] == workdir.MODE_PROJECT


# --------------------------------------------------------------- 回退救回来的读不是「落空」
#: 读到了存在的数据（相对路径只读回退救回来的）、内容不对才失败——跟运行目录无关，不该问「在哪个目录运行」
RESCUED_READ_SCRIPT = """\
import numpy as np
import matplotlib.pyplot as plt

d = np.loadtxt("data/run_1.txt")
print("[loaded]", d.shape)
raise ValueError("the content is wrong")
"""

PLAIN_OPEN_SCRIPT = """\
import matplotlib.pyplot as plt

with open("data/run_1.txt", encoding="utf-8") as f:
    text = f.read()
raise ValueError("the content is wrong: " + text)
"""


def test_a_read_the_fallback_rescues_is_not_recorded_as_a_sandbox_miss(figs, monkeypatch):
    """回退自己问「沙盒里有没有这份」不经观察器：被救回来的读不留 `probe_misses`（in-process，不用 worker）。"""
    sandbox = figs / "sandbox"
    sandbox.mkdir()
    monkeypatch.chdir(sandbox)
    misses = figcapture.ProbeMisses()
    undo_obs = figcapture.install_probe_observer(misses, str(sandbox))
    undo_fb = figcapture.install_relative_read_fallback(str(figs), str(figs), str(sandbox))
    try:
        with open("data/run_1.txt", encoding="utf-8") as f:
            assert f.read() == "1 2 3"
        assert Path("data/run_1.txt").read_text(encoding="utf-8") == "1 2 3"
        assert misses.items == []
        # 脚本自己问的探路照记（观察层没被关）
        assert os.path.exists("data/never.txt") is False
        assert [m["target"] for m in misses.items] == ["data/never.txt"]
    finally:
        undo_fb()
        undo_obs()


@needs_worker
@pytest.mark.parametrize("script", [RESCUED_READ_SCRIPT, PLAIN_OPEN_SCRIPT])
def test_a_failure_after_a_successful_read_carries_no_workdir_hint(figs, script):
    (figs / "fig.py").write_text(script, encoding="utf-8")
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.build("fig.py", str(figs), "__main__")
    assert err.value.code == "script_error"
    assert "wrong" in str(err.value)
    assert err.value.workdir_hint is None
    assert not (err.value.extra or {}).get("probe_misses")


# --------------------------------------------------------------- C++ 读取器：extra.enoent 点名的相对路径也是证据
PARQUET_SCRIPT = """\
import matplotlib.pyplot as plt
import pyarrow.parquet as pq

y = pq.read_table({name!r}).column("y").to_pylist()
fig, ax = plt.subplots()
ax.plot(y)
fig.savefig("p.png")
"""


def _worker_has_pyarrow() -> bool:
    import subprocess
    import sys

    return (
        subprocess.run(
            [WORKER_PY or sys.executable, "-c", "import pyarrow.parquet"], capture_output=True
        ).returncode
        == 0
    )


def test_an_enoent_relative_path_found_in_the_script_dir_is_runtime_evidence(project):
    (project / "sub" / "cg_data.parquet").write_bytes(b"x")
    # 沙盒 cwd 用本机真实的绝对路径：`"/sandbox"` 这种无盘符的写法在 Windows（3.13 起）不是绝对路径，
    # 「cwd 之内的绝对路径换回相对」那条就测不到（#1299 合并组 Windows 腿）
    sandbox = str(project.parent / "sandbox")
    err = engine_pool.WorkerError("脚本执行失败: cg_data.parquet", code="script_error")
    err.extra = {"enoent": {"filename": None, "named": "cg_data.parquet", "cwd": sandbox}}
    engine_pool._offer_missing_input(err, "sub/fig.py", str(project))
    assert err.code == "script_error" and err.missing_input is None  # 不放宽 native_miss
    assert err.workdir_hint["probes"] == ["cg_data.parquet"]
    assert err.workdir_hint["reason"] == workdir.REASON_SCRIPT_DIR_EVIDENCE
    # 在 cwd 之内的绝对路径（xarray 先 abspath 再报）换回相对
    err = engine_pool.WorkerError("x", code="script_error")
    err.extra = {
        "enoent": {
            "filename": os.path.join(sandbox, "cg_data.parquet"),
            "named": None,
            "cwd": sandbox,
        }
    }
    engine_pool._offer_missing_input(err, "sub/fig.py", str(project))
    assert err.workdir_hint["probes"] == ["cg_data.parquet"]


def test_an_enoent_path_that_is_absolute_or_absent_from_the_script_dir_gets_no_hint(project):
    (project / "sub" / "cg_data.parquet").write_bytes(b"x")
    sandbox = str(project.parent / "sandbox")
    for enoent in (
        {"named": str(project / "sub" / "cg_data.parquet"), "cwd": sandbox},  # 绝对：不依赖 cwd
        {"named": "other.parquet", "cwd": sandbox},  # 脚本目录里也没有
        {"named": None, "filename": None, "cwd": sandbox},
        "junk",
    ):
        err = engine_pool.WorkerError("x", code="script_error")
        err.extra = {"enoent": enoent}
        engine_pool._offer_missing_input(err, "sub/fig.py", str(project))
        assert err.workdir_hint is None, enoent


@needs_worker
def test_pyarrow_reading_a_cwd_relative_parquet_points_at_the_script_dir(figs):
    if not _worker_has_pyarrow():
        pytest.skip("worker 解释器没有 pyarrow")
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    pq.write_table(pa.table({"y": [1, 2, 3]}), str(figs / "cg_data.parquet"))
    (figs / "fig.py").write_text(PARQUET_SCRIPT.format(name="cg_data.parquet"), encoding="utf-8")
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.build("fig.py", str(figs), "__main__")
    exc = err.value
    assert exc.code == "script_error" and exc.missing_input is None
    assert exc.workdir_hint["recommended"] == workdir.MODE_PROJECT
    by_mode = {o["mode"]: o["found"] for o in exc.workdir_hint["options"]}
    assert "cg_data.parquet" in by_mode[workdir.MODE_PROJECT]
    assert by_mode[workdir.MODE_SANDBOX] == []

    workdir.set_mode(figs, workdir.MODE_PROJECT)
    engine_pool.shutdown_all(str(figs), wait=True)
    _, resp = engine_pool.build("fig.py", str(figs), "__main__")
    assert sorted(resp.get("stems") or {}) == ["p"]


@needs_worker
def test_pyarrow_reading_a_parquet_that_exists_nowhere_gets_no_hint(figs):
    if not _worker_has_pyarrow():
        pytest.skip("worker 解释器没有 pyarrow")
    (figs / "fig.py").write_text(PARQUET_SCRIPT.format(name="nowhere.parquet"), encoding="utf-8")
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.build("fig.py", str(figs), "__main__")
    assert err.value.code == "script_error" and err.value.workdir_hint is None


# --------------------------------------------------------------- build 成功、图却是空的（#1254）
#: 沙盒里 glob 回空，`ax.plot([])` 画出一条空线，脚本不报错、Tavotto 照常出图——静默的错误结果
EMPTY_PLOT_SCRIPT = """\
import glob
import matplotlib.pyplot as plt


def _read_parts(pattern):
    out = []
    for p in sorted(glob.glob(pattern)):
        with open(p, encoding="utf-8") as f:
            out.extend(float(x) for x in f.read().split())
    return out


fig, ax = plt.subplots()
ax.plot(_read_parts("data/run_*.txt"), color="#B4473C")
ax.set_title("custom entry")
fig.savefig("p.png")
"""


@needs_worker
def test_a_successful_build_with_an_empty_glob_carries_the_hint_and_the_same_figure(figs):
    (figs / "fig.py").write_text(EMPTY_PLOT_SCRIPT, encoding="utf-8")
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    worker, resp = engine_pool.build("fig.py", str(figs), "__main__")
    assert sorted(resp["stems"]) == ["p"]  # 出图结果不变
    hint = engine_pool.workdir_hint_of_build(worker)
    assert hint is not None and hint["source"] == "runtime"
    assert hint["reason"] == workdir.REASON_SCRIPT_DIR_EVIDENCE
    assert hint["probes"] == ["data/run_*.txt"]
    # 同一个会话还能照常渲染
    assert worker.override("p", [])["manifest"]

    # 切到脚本目录：有数据，不再有记录，也就不带 hint
    workdir.set_mode(figs, workdir.MODE_PROJECT)
    engine_pool.shutdown_all(str(figs), wait=True)
    worker, resp = engine_pool.build("fig.py", str(figs), "__main__")
    assert sorted(resp["stems"]) == ["p"] and "probe_misses" not in resp
    assert engine_pool.workdir_hint_of_build(worker) is None


@needs_worker
def test_a_successful_probe_carries_the_hint_too(figs):
    (figs / "fig.py").write_text(EMPTY_PLOT_SCRIPT, encoding="utf-8")
    workdir.set_mode(figs, workdir.MODE_SANDBOX)
    result = engine_probe.probe(str(figs), "fig.py")
    assert result["error"] is None and result["stems"] == ["p"]
    assert result["workdir_hint"]["probes"] == ["data/run_*.txt"]
    workdir.set_mode(figs, workdir.MODE_PROJECT)
    engine_pool.shutdown_all(str(figs), wait=True)
    assert "workdir_hint" not in engine_probe.probe(str(figs), "fig.py")


def test_on_success_only_glob_counts_not_the_exists_checks_of_ordinary_scripts(project):
    (project / "sub" / "cache").mkdir()
    worker = type(
        "W",
        (),
        {
            "script_name": "sub/fig.py",
            "figures_dir": str(project),
            "last_build_probe_misses": [{"kind": "dir", "target": "cache"}],
        },
    )()
    assert engine_pool.workdir_hint_of_build(worker) is None  # 先判再建的常规写法，不提示
    worker.last_build_probe_misses = [_glob("data/run_*.txt")]
    assert engine_pool.workdir_hint_of_build(worker)["probes"] == ["data/run_*.txt"]
    worker.last_build_probe_misses = None
    assert engine_pool.workdir_hint_of_build(worker) is None
    assert engine_pool.workdir_hint_of_build(object()) is None  # native / 测试替身


def test_the_render_response_carries_the_hint_without_touching_anything_else(
    project, tmp_path, monkeypatch
):
    from tavotto import app as tavotto_app

    (project / "p1.pdf").write_bytes(b"%PDF-1.4\n")
    tavotto_app.app.config["TESTING"] = True
    tavotto_app.reset_projects()
    client = tavotto_app.app.test_client()
    client.post("/api/projects/open", json={"path": str(project)})

    class _Worker:
        built = True
        rev = 3
        script_name = "sub/fig.py"
        figures_dir = str(project)
        last_build_probe_misses = [_glob("data/run_*.txt")]

        def override(self, stem, patches, preview_dpi=None, inline_svg=False):
            return {"manifest": {"elements": []}, "warnings": []}

    monkeypatch.setattr(
        tavotto_app.engine_registry.Registry,
        "for_stem",
        lambda self, s: {"script": "sub/fig.py", "entry": "main", "cost": "light"},
    )
    monkeypatch.setattr(tavotto_app.engine_pool, "get", lambda *a, **kw: _Worker())
    try:
        res = client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []})
        assert res.status_code == 200
        body = res.get_json()
        assert body["workdir_hint"]["source"] == "runtime" and body["manifest"] == {"elements": []}
        _Worker.last_build_probe_misses = None
        assert (
            "workdir_hint"
            not in client.post(
                "/api/engine/render", json={"id": "p1.pdf", "patches": []}
            ).get_json()
        )
    finally:
        tavotto_app.reset_projects()


def test_missing_stem_error_keeps_the_probe_misses_so_the_hint_survives_the_rebuild(project):
    """准备流程的「没出图」是 `missing_stem_error` 重建的错误：不能只留 `known`（Codex #1299 P1）。"""
    worker = type(
        "W",
        (),
        {
            "script_name": "sub/fig.py",
            "figures_dir": str(project),
            "last_build_probe_misses": [_glob("data/run_*.txt")],
            "_log_tail": lambda self, n=30: "",
        },
    )()
    err = engine_pool.missing_stem_error(worker, "p", [])
    assert err.code == engine_pool.NO_FIGURES_SILENT_CODE
    assert err.extra["probe_misses"] == worker.last_build_probe_misses
    assert err.workdir_hint["reason"] == workdir.REASON_SCRIPT_DIR_EVIDENCE
    worker.last_build_probe_misses = None
    assert engine_pool.missing_stem_error(worker, "p", []).workdir_hint is None
