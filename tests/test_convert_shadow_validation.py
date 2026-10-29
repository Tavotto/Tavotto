# ruff: noqa: F811 - imported pytest fixture
"""转换校验不往用户项目里写任何文件（ADR 0143 修订：影子脚本）。

真 safe worker 端到端：候选字节留在候选目录，worker 以 `--script-source` 读它、`__file__` /
`sys.argv[0]` 指向采用之后它在项目里的位置，cwd 仍是原脚本所在目录（相对路径读数据与原脚本一致）。
同步盘会把项目里冒出的临时文件同步出去——所以这里用轮询把整个校验期间项目目录的每一次变化都记下来。
"""

from __future__ import annotations

import importlib.util
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto import app as app_module
from tavotto.engine import ai_convert, ai_figure_edit, execspec, pool, shadowscript, workdir

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

CANDIDATE = """\
import os
import sys

import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
assert os.path.basename(__file__) == "fig_matplotlib.py", __file__
assert os.path.abspath(sys.argv[0]) == os.path.abspath(__file__), sys.argv
beside = open(os.path.join(HERE, "data.csv"), encoding="utf-8").read()
relative = open("data.csv", encoding="utf-8").read()
assert beside == relative == "x,y\\n1,2\\n"


def main():
    fig, ax = plt.subplots(figsize=(3.2, 1.6))
    ax.plot([0, 1, 2], [0, 1, 4])
    fig.savefig("out.png")


if __name__ == "__main__":
    main()
"""


class DirWatcher:
    """轮询项目目录（含子目录），把期间出现过的每一个名字都记下来——哪怕只存在几毫秒。"""

    def __init__(self, root: Path):
        self.root = root
        self.seen: set[str] = set()
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self._stop.is_set():
            for dp, _dn, fn in os.walk(self.root):
                for n in fn:
                    self.seen.add(Path(os.path.relpath(os.path.join(dp, n), self.root)).as_posix())
            time.sleep(0.002)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._t.join(5)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_convert, "CONVERT_DIR", tmp_path / "candidates")
    # pytest 的 tmp_path 在系统临时目录之下，而绊线白名单放行系统临时目录（ADR 0141 的盲区）：把 worker 的临时目录挪走
    worker_tmp = tmp_path / "worker-tmp"
    worker_tmp.mkdir()
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(worker_tmp))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    root = tmp_path / "project"
    (root / "sub").mkdir(parents=True)
    script = root / "sub" / "fig.R"
    script.write_text("plot(1)\n", encoding="utf-8")
    (root / "sub" / "data.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    cand = ai_convert.create_candidate(
        "abcdef012345", script, root, "sub/fig.R", library=None, source_language="r"
    )
    cand.new_bytes = CANDIDATE.encode()
    cand.target.write_bytes(cand.new_bytes)
    runner = app_module._convert_runner(SimpleNamespace(path=root), "sub/fig.R")
    yield root, cand, runner
    pool.shutdown_all(figures_dir=str(root), wait=True)
    workdir.forget(root)


@pytest.mark.parametrize("mode", [workdir.MODE_SANDBOX, workdir.MODE_PROJECT])
def test_validation_writes_nothing_into_the_project_and_sees_the_original_folder(world, mode):
    root, cand, runner = world
    workdir.set_mode(root, mode)
    before = ai_figure_edit.project_fingerprint(root)
    with DirWatcher(root) as watcher:
        result = ai_convert.run_validation(cand, runner)
    assert result["status"] == "ok", result
    # 候选没有以任何名字在项目里出现过，哪怕一瞬间
    assert watcher.seen == {"sub/fig.R", "sub/data.csv"}, watcher.seen
    assert (
        ai_figure_edit.fingerprint_changes(before, ai_figure_edit.project_fingerprint(root)) == []
    )
    assert not (root / "sub" / "fig_matplotlib.py").exists()


def test_the_shadow_name_is_the_one_adoption_will_pick(world):
    root, cand, _ = world
    assert ai_convert.adopt_names(root, "sub/fig.R", cand.target_name) == ["sub/fig_matplotlib.py"]
    (root / "sub" / "fig_matplotlib.py").write_text("# taken\n", encoding="utf-8")
    assert ai_convert.adopt_names(root, "sub/fig.R", cand.target_name) == [
        "sub/fig_matplotlib_2.py"
    ]


def test_module_entry_also_gets_the_display_file(world):
    """入口是函数（不是 `__main__`）时走的是 import 路径：模块的 `__file__` 同样是显示路径。"""
    root, cand, runner = world
    cand.new_bytes = (
        b"import os\nimport matplotlib.pyplot as plt\n\n\ndef main():\n"
        b"    assert os.path.basename(__file__) == 'fig_matplotlib.py'\n"
        b"    assert os.path.dirname(os.path.abspath(__file__)).endswith('sub')\n"
        b"    fig, ax = plt.subplots()\n    ax.plot([1, 2])\n    fig.savefig('o.png')\n"
    )
    cand.target.write_bytes(cand.new_bytes)
    result = ai_convert.run_validation(cand, runner)
    assert result["status"] == "ok", result


def test_candidate_writing_into_the_project_still_trips_the_write_roots(world):
    root, cand, runner = world
    workdir.set_mode(
        root, workdir.MODE_PROJECT
    )  # cwd 就是原脚本所在目录：相对路径的写会落进用户项目
    cand.new_bytes = (
        b"import matplotlib.pyplot as plt\nopen('sneaky.txt', 'w').write('x')\n"
        b"plt.plot([1])\nplt.savefig('o.png')\n"
    )
    cand.target.write_bytes(cand.new_bytes)
    result = ai_convert.run_validation(cand, runner)
    assert result["status"] == "error" and result["code"] == "write_outside_roots", result
    assert not (root / "sub" / "sneaky.txt").exists()


def test_registration_is_scoped_and_isolated_per_write_roots(tmp_path):
    src = tmp_path / "c" / "a.py"
    src.parent.mkdir()
    src.write_text("x=1\n")
    key = execspec.write_roots_key((str(src.parent),))
    other = execspec.write_roots_key((str(tmp_path / "other"),))
    with shadowscript.shadow(tmp_path / "p", "d/a.py", src, key):
        assert shadowscript.lookup(tmp_path / "p", "d/a.py", key) == str(src)
        assert shadowscript.lookup(tmp_path / "p", "d/a.py", other) is None
        assert shadowscript.lookup(tmp_path / "p", "d/a.py", "") is None
        assert shadowscript.lookup_active(tmp_path / "p", "d/a.py") == str(src)
        second = tmp_path / "c" / "b.py"
        second.write_text("x=2\n")
        with shadowscript.shadow(tmp_path / "p", "d/a.py", second, other):
            # 内层校验只看见自己的字节，不会串到外层那一份
            assert shadowscript.lookup_active(tmp_path / "p", "d/a.py") == str(second)
        assert shadowscript.lookup_active(tmp_path / "p", "d/a.py") == str(src)
    assert shadowscript.lookup(tmp_path / "p", "d/a.py", key) is None


def test_dependency_survey_reads_the_shadow_candidate(world):
    """依赖调研读的是候选字节：项目里没有这个文件，也不能让“缺包要先装”的步骤悄悄消失。"""
    from tavotto.engine import convertdeps

    root, cand, _ = world
    # 缺的包要选测试环境里一定没有的：release 档 lab 的验证环境装着 pandas，用它调研结论是「什么都不缺」。
    # adjustText 是登记过的公开包（depresolve 名单），不在 Tavotto 与测试的依赖里——也是诊断包 TVD-ZG0Y 里缺的那个
    if importlib.util.find_spec("adjustText") is not None:
        pytest.skip("这个环境装了 adjustText，缺包调研无从发生")
    cand.target.write_bytes(b"import adjustText\nimport matplotlib\n")
    key = execspec.write_roots_key((str(cand.dir),))
    with shadowscript.shadow(root, "sub/fig_matplotlib.py", cand.target, key):
        survey = convertdeps._survey(str(root), "sub/fig_matplotlib.py")
    assert survey["verdict"] != "none" and "adjusttext" in survey.get("names", []), survey


# --------------------------------------------------------------------------- 改图（figure_edit）候选同样走影子脚本
from test_ai_edit_check_app import world as edit_world  # noqa: E402,F401,I001

EDIT_BASE = (
    "import os\nimport matplotlib.pyplot as plt\n\n\ndef main():\n"
    "    here = os.path.dirname(os.path.abspath(__file__))\n"
    "    assert os.path.basename(__file__) == 'plot.py', __file__\n"
    "    assert open(os.path.join(here, 'data.csv')).read() == open('data.csv').read() == 'x\\n'\n"
    "    plt.plot([1, 2, 3], [1, 4, 9])\n    plt.title('old')\n    plt.show()\n\n\n"
    "if __name__ == '__main__':\n    main()\n"
)


def _edit_candidate(m, figs, new_src):
    (figs / "data.csv").write_text("x\n", encoding="utf-8")
    script = figs / "plot.py"
    script.write_text(EDIT_BASE, encoding="utf-8")
    ctx = next(iter(m.PROJECTS.values()))
    runner = m._convert_runner(ctx, "plot.py", with_manifests=True)
    cand = ai_figure_edit.create_candidate("fedcba987654", script, figs, "plot.py")
    cand.target.write_text(new_src, encoding="utf-8")
    cand.new_bytes = cand.target.read_bytes()
    return cand, runner


def test_edit_validation_writes_nothing_and_file_points_at_the_original(edit_world):
    m, figs = edit_world
    cand, runner = _edit_candidate(m, figs, EDIT_BASE.replace("'old'", "'new'"))
    before = ai_figure_edit.project_fingerprint(figs)
    try:
        with DirWatcher(figs) as watcher:
            result = ai_convert.run_validation(cand, runner)
        assert result["status"] == "ok", result
        assert not any(n.startswith(ai_convert.STAGE_PREFIX) for n in watcher.seen), watcher.seen
        assert watcher.seen <= {"plot.py", "data.csv", "tavotto_registry.json"}, watcher.seen
        assert (
            ai_figure_edit.fingerprint_changes(before, ai_figure_edit.project_fingerprint(figs))
            == []
        )
        assert (figs / "plot.py").read_text(encoding="utf-8") == EDIT_BASE, "原脚本字节不变"
    finally:
        ai_figure_edit.remove_candidate(cand.dir)


def test_edit_validation_runs_the_candidate_bytes_not_the_original(edit_world):
    """反证：项目里的原脚本断言通过，候选故意断言失败——必须看到候选的失败，说明跑的是候选字节。"""
    m, figs = edit_world
    cand, runner = _edit_candidate(
        m, figs, EDIT_BASE.replace("'old'", "'new'") + "\nraise SystemExit('candidate ran')\n"
    )
    try:
        result = ai_convert.run_validation(cand, runner)
        assert result["status"] == "error", result
    finally:
        ai_figure_edit.remove_candidate(cand.dir)


def test_edit_candidate_writing_into_the_project_is_still_stopped(edit_world):
    m, figs = edit_world
    workdir.set_mode(figs, workdir.MODE_PROJECT)
    try:
        cand, runner = _edit_candidate(
            m,
            figs,
            EDIT_BASE.replace(
                "    plt.show()", "    open('sneaky.csv', 'w').write('x')\n    plt.show()"
            ),
        )
        result = ai_convert.run_validation(cand, runner)
        assert result["status"] == "error" and result["code"] == "write_outside_roots", result
        assert not (figs / "sneaky.csv").exists()
    finally:
        workdir.forget(figs)
        ai_figure_edit.remove_candidate(cand.dir)


# --------------------------------------------------------------------------- spawn 子进程也跑候选字节
SPAWN_BODY = """\
import multiprocessing as mp
import os
from concurrent.futures import ProcessPoolExecutor

import matplotlib.pyplot as plt

MARK = "{mark}"
PARENT_CWD = os.getcwd()


def child(x):
    return MARK, os.path.basename(__file__), os.getcwd(), x


def check(got, x):
    assert got[0] == "CANDIDATE-BYTES", got
    assert got[1] == "{name}", got
    assert os.path.realpath(got[2]) == os.path.realpath(PARENT_CWD), got
    assert got[3] == x, got


def main():
    ctx = mp.get_context("spawn")
    with ctx.Pool(1) as pool:
        check(pool.apply(child, (1,)), 1)
    with ProcessPoolExecutor(1, mp_context=ctx) as ex:
        check(ex.submit(child, 2).result(), 2)
    p = ctx.Process(target=child, args=(3,))
    p.start()
    p.join()
    assert p.exitcode == 0, p.exitcode
    fig, ax = plt.subplots()
    ax.plot([1, 2])
    fig.savefig("o.png")
"""


@pytest.mark.parametrize("guarded", [True, False], ids=["main_entry", "module_entry"])
def test_spawn_children_run_the_candidate_bytes_for_conversion(world, guarded):
    root, cand, runner = world
    src = SPAWN_BODY.format(mark="CANDIDATE-BYTES", name="fig_matplotlib.py")
    if guarded:
        src += "\n\nif __name__ == '__main__':\n    main()\n"
    cand.new_bytes = src.encode()
    cand.target.write_bytes(cand.new_bytes)
    result = ai_convert.run_validation(cand, runner)
    assert result["status"] == "ok", result
    assert not (root / "sub" / "fig_matplotlib.py").exists()


@pytest.mark.parametrize("guarded", [True, False], ids=["main_entry", "module_entry"])
def test_spawn_children_run_the_candidate_bytes_for_edit(edit_world, guarded):
    """改图：磁盘上的原脚本标记是 ORIGINAL——子进程拿到 CANDIDATE-BYTES 才说明读的是候选而不是原件。"""
    m, figs = edit_world
    (figs / "data.csv").write_text("x\n", encoding="utf-8")
    script = figs / "plot.py"
    tail = "\n\nif __name__ == '__main__':\n    main()\n" if guarded else ""
    script.write_text(SPAWN_BODY.format(mark="ORIGINAL", name="plot.py") + tail, encoding="utf-8")
    ctx = next(iter(m.PROJECTS.values()))
    runner = m._convert_runner(ctx, "plot.py", with_manifests=True)
    cand = ai_figure_edit.create_candidate("fedcba987655", script, figs, "plot.py")
    cand.target.write_text(
        SPAWN_BODY.format(mark="CANDIDATE-BYTES", name="plot.py") + tail, encoding="utf-8"
    )
    cand.new_bytes = cand.target.read_bytes()
    try:
        result = ai_convert.run_validation(cand, runner)
        assert result["status"] == "ok", result
    finally:
        ai_figure_edit.remove_candidate(cand.dir)


# --------------------------------------------------------------------------- 影子只对那次校验运行生效
def test_windows_style_case_insensitive_paths_hit_the_same_key(tmp_path, monkeypatch):
    """Windows 的 normcase 会小写化：登记与查询共用同一个规范化，含大写的目录 / 脚本名也命中。"""
    import ntpath

    monkeypatch.setattr(os.path, "normcase", ntpath.normcase)
    src = tmp_path / "c" / "a.py"
    src.parent.mkdir()
    src.write_text("x=1\n")
    proj = tmp_path / "p"
    (proj / "Figures").mkdir(parents=True)
    key = execspec.write_roots_key((str(src.parent),))
    with shadowscript.shadow(proj, "Figures/Plot_matplotlib.py", src, key):
        assert shadowscript.lookup(proj, "Figures/Plot_matplotlib.py", key) == str(src)
        assert shadowscript.lookup(proj, "figures\\plot_matplotlib.py", key) == str(src)
        assert shadowscript.lookup_active(proj, "Figures/Plot_matplotlib.py") == str(src)
        absolute = os.path.join(os.path.realpath(proj), "Figures", "Plot_matplotlib.py")
        assert shadowscript.lookup_active(proj, absolute) == str(src)


def test_shadow_is_invisible_outside_the_validating_context(tmp_path):
    """校验进行中，别的线程里的普通会话（和校验自己以外的一切）对同一路径只看到磁盘原件。"""
    src = tmp_path / "c" / "a.py"
    src.parent.mkdir()
    src.write_text("x=1\n")
    proj = tmp_path / "p"
    (proj / "d").mkdir(parents=True)
    key = execspec.write_roots_key((str(src.parent),))
    seen: list = []
    with shadowscript.shadow(proj, "d/a.py", src, key):
        assert shadowscript.lookup_active(proj, "d/a.py") == str(src)
        assert shadowscript.lookup_active(proj, "d/other.py") is None
        t = threading.Thread(target=lambda: seen.append(shadowscript.lookup_active(proj, "d/a.py")))
        t.start()
        t.join()
    assert seen == [None]
    assert shadowscript.lookup_active(proj, "d/a.py") is None


def test_ordinary_analysis_ignores_a_running_validation_but_validation_reads_the_candidate(
    tmp_path,
):
    from tavotto.engine import importscan

    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "plot.py").write_text("import json\n", encoding="utf-8")
    (proj / "data.csv").write_text("a\n1\n", encoding="utf-8")
    cand = tmp_path / "c" / "plot.py"
    cand.parent.mkdir()
    cand.write_text("import json\nopen('data.csv').read()\nimport pandas\n", encoding="utf-8")
    key = execspec.write_roots_key((str(cand.parent),))

    def decide():
        workdir.forget(proj)
        return workdir.decision_for(proj, "plot.py")["evidence"]["verdict"]

    baseline = decide()
    baseline_scan = importscan.scan(proj, "plot.py")
    results: dict = {}

    def ordinary_session():
        results["verdict"] = decide()
        results["scan"] = importscan.scan(proj, "plot.py")

    with shadowscript.shadow(proj, "plot.py", cand, key):
        t = threading.Thread(target=ordinary_session)
        t.start()
        t.join()
        inside = decide()
        inside_scan = importscan.scan(proj, "plot.py")
    assert results["verdict"] == baseline == "none"
    assert results["scan"] == baseline_scan
    # 校验自己仍读到候选：读 data.csv 的证据、以及新增的 pandas
    assert inside != baseline, inside
    assert inside_scan != baseline_scan
    workdir.forget(proj)


def test_edit_check_runner_sees_the_candidate_data_not_the_original(edit_world):
    """回归：改图检查要能比出“候选改了数据”——影子之后原脚本那一次与候选那一次必须读到不同字节。"""
    m, figs = edit_world
    new = EDIT_BASE.replace("[1, 4, 9]", "[1, 4, 10]")
    cand, runner = _edit_candidate(m, figs, new)
    try:
        original = runner(str(figs), "plot.py")
        roots = (str(cand.dir),)
        with shadowscript.shadow(
            cand.project_root, "plot.py", cand.target, execspec.write_roots_key(roots)
        ):
            changed = runner(str(figs), "plot.py", roots)
        assert not original.get("error") and not changed.get("error"), (original, changed)
        assert original["manifests"] != changed["manifests"]
    finally:
        ai_figure_edit.remove_candidate(cand.dir)
