"""转换校验不往用户项目里写任何文件（ADR 0143 修订：影子脚本）。

真 safe worker 端到端：候选字节留在候选目录，worker 以 `--script-source` 读它、`__file__` /
`sys.argv[0]` 指向采用之后它在项目里的位置，cwd 仍是原脚本所在目录（相对路径读数据与原脚本一致）。
同步盘会把项目里冒出的临时文件同步出去——所以这里用轮询把整个校验期间项目目录的每一次变化都记下来。
"""

from __future__ import annotations

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
                    self.seen.add(os.path.relpath(os.path.join(dp, n), self.root))
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
        assert shadowscript.lookup_any(tmp_path / "p", "d/a.py") == str(src)
        second = tmp_path / "c" / "b.py"
        second.write_text("x=2\n")
        with shadowscript.shadow(tmp_path / "p", "d/a.py", second, other):
            assert shadowscript.lookup_any(tmp_path / "p", "d/a.py") is None  # 歧义 = 当不存在
    assert shadowscript.lookup(tmp_path / "p", "d/a.py", key) is None


def test_dependency_survey_reads_the_shadow_candidate(world):
    """依赖调研读的是候选字节：项目里没有这个文件，也不能让“缺包要先装”的步骤悄悄消失。"""
    from tavotto.engine import convertdeps

    root, cand, _ = world
    cand.target.write_bytes(b"import pandas\nimport matplotlib\n")
    key = execspec.write_roots_key((str(cand.dir),))
    with shadowscript.shadow(root, "sub/fig_matplotlib.py", cand.target, key):
        survey = convertdeps._survey(str(root), "sub/fig_matplotlib.py")
    assert survey["verdict"] != "none" and "pandas" in survey.get("names", []), survey
