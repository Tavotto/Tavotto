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
