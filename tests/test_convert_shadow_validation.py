# ruff: noqa: F811 - imported pytest fixture
"""转换校验不往用户项目里写任何文件（ADR 0143 修订：影子脚本）。

真 safe worker 端到端：候选字节留在候选目录，worker 以 `--script-source` 读它、`__file__` /
`sys.argv[0]` 指向采用之后它在项目里的位置，cwd 仍是原脚本所在目录（相对路径读数据与原脚本一致）。
同步盘会把项目里冒出的临时文件同步出去——所以这里用轮询把整个校验期间项目目录的每一次变化都记下来。
"""

from __future__ import annotations

import importlib.util
import os
import sys
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


def _redirected(cand, rel):
    return cand.dir / ai_convert.REDIRECT_DIRNAME / rel


def test_candidate_writing_into_the_project_is_redirected_not_failed(world):
    """相对路径（chdir 到脚本目录后）与绝对路径写 CSV：校验通过、项目逐字节不变、文件落在候选的改道区。"""
    root, cand, runner = world
    workdir.set_mode(root, workdir.MODE_PROJECT)  # cwd 就是原脚本所在目录
    before = ai_figure_edit.project_fingerprint(root)
    cand.new_bytes = (
        "import os\nimport matplotlib.pyplot as plt\n"
        "here = os.path.dirname(os.path.abspath(__file__))\n"
        "open('rel.csv', 'w').write('rel')\n"
        "os.makedirs(os.path.join(here, 'out', 'deep'), exist_ok=True)\n"
        "open(os.path.join(here, 'out', 'deep', 'abs.csv'), 'w').write('abs')\n"
        "plt.plot([1])\nplt.savefig('o.png')\n"
    ).encode()
    cand.target.write_bytes(cand.new_bytes)
    with DirWatcher(root) as watcher:
        result = ai_convert.run_validation(cand, runner)
    assert result["status"] == "ok", result
    assert watcher.seen == {"sub/fig.R", "sub/data.csv"}, watcher.seen
    assert (
        ai_figure_edit.fingerprint_changes(before, ai_figure_edit.project_fingerprint(root)) == []
    )
    assert _redirected(cand, "sub/rel.csv").read_text() == "rel"
    assert _redirected(cand, "sub/out/deep/abs.csv").read_text() == "abs"
    assert not (root / "sub" / "out").exists()
    # 影子 / 指纹复核仍然通过
    assert all(
        c.verdict == ai_convert.VERIFIED
        for c in ai_convert.ADAPTER._project_untouched(cand, "precommit")
    )


def test_redirected_write_can_be_read_back_and_originals_are_copy_on_write(world):
    root, cand, runner = world
    workdir.set_mode(root, workdir.MODE_PROJECT)
    cand.new_bytes = (
        "import os\nimport pandas_free as _x\n".replace("import pandas_free as _x\n", "")
        + "import matplotlib.pyplot as plt\n"
        "here = os.path.dirname(os.path.abspath(__file__))\n"
        "p = os.path.join(here, 'made.csv')\n"
        "open(p, 'w').write('a,b\\n1,2\\n')\n"
        "assert os.path.exists(p) and os.path.isfile(p)\n"
        "assert open(p).read() == 'a,b\\n1,2\\n'\n"
        "d = os.path.join(here, 'data.csv')\n"
        "open(d, 'a').write('9,9\\n')\n"
        "assert open(d).read() == 'x,y\\n1,2\\n9,9\\n', open(d).read()\n"
        "plt.plot([1])\nplt.savefig('o.png')\n"
    ).encode()
    cand.target.write_bytes(cand.new_bytes)
    result = ai_convert.run_validation(cand, runner)
    assert result["status"] == "ok", result
    assert (root / "sub" / "data.csv").read_text() == "x,y\n1,2\n"  # 用户的原文件没动
    assert _redirected(cand, "sub/data.csv").read_text() == "x,y\n1,2\n9,9\n"


EXCLUSIVE = (
    "import os\nimport matplotlib.pyplot as plt\n"
    "here = os.path.dirname(os.path.abspath(__file__))\n"
    "open(os.path.join(here, 'once.csv'), 'x').write('1')\n"
    "plt.plot([1])\nplt.savefig('o.png')\n"
)
APPEND_READ = (
    "import os\nimport matplotlib.pyplot as plt\n"
    "here = os.path.dirname(os.path.abspath(__file__))\n"
    "p = os.path.join(here, 'log.csv')\n"
    "open(p, 'a').write('row\\n')\n"
    "assert open(p).read() == 'row\\n', open(p).read()\n"
    "plt.plot([1])\nplt.savefig('o.png')\n"
)


@pytest.mark.parametrize("source", [EXCLUSIVE, APPEND_READ], ids=["exclusive", "append_read"])
def test_every_validation_run_starts_from_an_empty_redirect_area(world, source):
    """agent_check 与提交前校验（以及依赖重试 / 续跑）共用候选目录：第二次必须与全新一次相同。"""
    root, cand, runner = world
    workdir.set_mode(root, workdir.MODE_PROJECT)
    cand.new_bytes = source.encode()
    cand.target.write_bytes(cand.new_bytes)
    first = ai_convert.run_validation(cand, runner)
    second = ai_convert.run_validation(cand, runner)
    third = ai_convert.run_validation(cand, runner)
    assert first["status"] == second["status"] == third["status"] == "ok", (first, second, third)


def test_dependency_retry_rerun_also_sees_a_fresh_redirect_area(world, monkeypatch):
    root, cand, runner = world
    workdir.set_mode(root, workdir.MODE_PROJECT)
    cand.new_bytes = EXCLUSIVE.encode()
    cand.target.write_bytes(cand.new_bytes)
    calls = []
    real = ai_convert._runtime_dependency_retry

    def retry_once(c, rel, out):
        calls.append(1)
        return {} if len(calls) == 1 else real(c, rel, out)

    monkeypatch.setattr(ai_convert, "_runtime_dependency_retry", retry_once)
    result = ai_convert.run_validation(cand, runner)
    assert result["status"] == "ok", result
    assert len(calls) == 1


def test_unclearable_redirect_area_fails_the_run_instead_of_using_stale_data(world, monkeypatch):
    root, cand, runner = world
    workdir.set_mode(root, workdir.MODE_PROJECT)
    cand.new_bytes = EXCLUSIVE.encode()
    cand.target.write_bytes(cand.new_bytes)
    assert ai_convert.run_validation(cand, runner)["status"] == "ok"

    def boom(*_a, **_k):
        raise PermissionError("busy")

    monkeypatch.setattr(ai_convert.shutil, "rmtree", boom)
    result = ai_convert.run_validation(cand, runner)
    assert result["status"] == "error" and result["code"] == "redirect_area_not_clearable", result


def test_keep_failed_does_not_retain_a_candidate_with_undeletable_redirected_data(
    world, monkeypatch
):
    root, cand, runner = world
    cand.new_bytes = cand.target.read_bytes() if cand.target.exists() else b"x = 1\n"
    cand.target.write_bytes(cand.new_bytes)
    area = cand.dir / ai_convert.REDIRECT_DIRNAME
    area.mkdir()
    (area / "data.csv").write_text("derived")
    monkeypatch.setattr(
        ai_convert.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(PermissionError("x"))
    )
    assert ai_convert.keep_failed(cand) is False
    assert not cand.kept_failed


@pytest.mark.skipif(os.name == "nt", reason="POSIX 权限位")
def test_keep_failed_removes_read_only_redirected_data_then_retains(world):
    root, cand, runner = world
    cand.new_bytes = b"x = 1\n"
    cand.target.write_bytes(cand.new_bytes)
    sub = cand.dir / ai_convert.REDIRECT_DIRNAME / "d"
    sub.mkdir(parents=True)
    (sub / "ro.csv").write_text("derived")
    (sub / "ro.csv").chmod(0o400)
    sub.chmod(0o500)
    assert ai_convert.keep_failed(cand) is True
    assert not (cand.dir / ai_convert.REDIRECT_DIRNAME).exists()


PRELUDE = (
    "import os\nimport matplotlib.pyplot as plt\n"
    "here = os.path.dirname(os.path.abspath(__file__))\n"
    "data = os.path.join(here, 'data.csv')\n"
    "ORIG = 'x,y\\n1,2\\n'\n"
    "assert os.path.exists(data) and open(data).read() == ORIG\n"
)
EPILOGUE = "plt.plot([1])\nplt.savefig('o.png')\n"


def _validate(world, body, twice=False):
    root, cand, runner = world
    workdir.set_mode(root, workdir.MODE_PROJECT)
    cand.new_bytes = (PRELUDE + body + EPILOGUE).encode()
    cand.target.write_bytes(cand.new_bytes)
    result = ai_convert.run_validation(cand, runner)
    if twice:  # 白名单随 worker 走：第二次校验的 worker 是新的，原文件重新可见（PRELUDE 会断言）
        assert result["status"] == "ok", result
        result = ai_convert.run_validation(cand, runner)
    assert (root / "sub" / "data.csv").read_text() == "x,y\n1,2\n"  # 项目里的原件始终没动
    return result, cand


def test_replace_of_a_redirected_output_does_not_resurrect_the_project_file(world):
    result, cand = _validate(
        world,
        "open(data, 'w').write('new')\n"
        "arch = os.path.join(here, 'archive.csv')\n"
        "os.replace(data, arch)\n"
        "assert not os.path.exists(data) and not os.path.isfile(data)\n"
        "try:\n    open(data)\n    raise SystemExit('stale data visible')\nexcept FileNotFoundError:\n    pass\n"
        "assert open(arch).read() == 'new'\n",
    )
    assert result["status"] == "ok", result
    assert _redirected(cand, "sub/archive.csv").read_text() == "new"


def test_remove_after_write_and_remove_of_a_project_only_file_are_whiteouts(world):
    result, cand = _validate(
        world,
        "open(data, 'w').write('new')\nos.remove(data)\n"
        "assert not os.path.exists(data)\n"
        "try:\n    open(data)\n    raise SystemExit('visible')\nexcept FileNotFoundError:\n    pass\n",
        twice=True,
    )
    assert result["status"] == "ok", result
    result, _ = _validate(
        world,
        "os.remove(data)\nassert not os.path.exists(data)\n"
        "try:\n    os.path.getsize(data)\n    raise SystemExit('visible')\n"
        "except FileNotFoundError:\n    pass\n"
        "try:\n    os.remove(data)\n    raise SystemExit('twice')\nexcept FileNotFoundError:\n    pass\n",
    )
    assert result["status"] == "ok", result


def test_write_again_after_a_whiteout_works(world):
    result, cand = _validate(
        world,
        "os.remove(data)\nassert not os.path.exists(data)\n"
        "open(data, 'w').write('again')\n"
        "assert os.path.exists(data) and open(data).read() == 'again'\n",
    )
    assert result["status"] == "ok", result
    assert _redirected(cand, "sub/data.csv").read_text() == "again"


def test_os_open_without_creat_on_an_existing_project_file(world):
    result, cand = _validate(
        world,
        "fd = os.open(data, os.O_WRONLY | os.O_TRUNC)\nos.write(fd, b'z')\nos.close(fd)\n"
        "assert open(data).read() == 'z'\n"
        "fd = os.open(os.path.join(here, 'data.csv'), os.O_WRONLY)\nos.close(fd)\n",
    )
    assert result["status"] == "ok", result
    assert _redirected(cand, "sub/data.csv").read_text() == "z"


def test_os_open_without_creat_on_a_missing_file_still_fails(world):
    result, cand = _validate(
        world,
        "try:\n    os.open(os.path.join(here, 'nope.csv'), os.O_WRONLY)\n"
        "    raise SystemExit('created')\nexcept FileNotFoundError:\n    pass\n"
        "os.remove(data)\n"
        "try:\n    os.open(data, os.O_WRONLY | os.O_TRUNC)\n"
        "    raise SystemExit('whited-out file opened')\nexcept FileNotFoundError:\n    pass\n",
    )
    assert result["status"] == "ok", result
    assert not _redirected(cand, "sub/nope.csv").exists()


def _project_dir(world):
    out = world[0] / "sub" / "out"
    out.mkdir(exist_ok=True)
    (out / "old.csv").write_text("old")
    return out


def _tripped(result):
    assert result["status"] == "error" and result["code"] == "write_outside_roots", result
    return True


def test_rmtree_of_an_existing_project_dir_trips(world):
    out = _project_dir(world)
    result, _ = _validate(
        world,
        "o = os.path.join(here, 'out')\nopen(os.path.join(o, 'new.csv'), 'w').write('n')\n"
        "import shutil\nshutil.rmtree(o)\n",
    )
    assert _tripped(result)
    assert (out / "old.csv").read_text() == "old" and not (out / "new.csv").exists()


def test_moving_a_partially_written_project_dir_trips(world):
    """项目目录里写了新文件再整体 shutil.move 走：只移暂存副本会让 old.csv 留在原处（Codex #1516 P1）。"""
    out = _project_dir(world)
    result, _ = _validate(
        world,
        "o = os.path.join(here, 'out')\nopen(os.path.join(o, 'new.csv'), 'w').write('n')\n"
        "import shutil\nshutil.move(o, os.path.join(here, 'archive'))\n",
    )
    assert _tripped(result)
    assert (out / "old.csv").read_text() == "old" and not (out / "new.csv").exists()
    assert not (world[0] / "sub" / "archive").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="符号链接需要权限")
def test_unlinking_or_moving_a_project_symlink_trips_instead_of_whiting_out_its_target(world):
    """项目里 `alias.csv -> data.csv`：删 / 改名 / 移走的是链接本身，不能被 realpath 模拟成删目标（Codex #1516 P1）。"""
    sub = world[0] / "sub"
    (sub / "alias.csv").symlink_to("data.csv")
    for op in (
        "os.unlink(os.path.join(here, 'alias.csv'))",
        "os.rename(os.path.join(here, 'alias.csv'), os.path.join(here, 'b.csv'))",
        "import shutil\nshutil.move(os.path.join(here, 'alias.csv'), os.path.join(here, 'b.csv'))",
        # 目标是链接：真实执行只替换链接本身，data.csv 不变
        "n = os.path.join(here, 'new.csv')\nopen(n, 'w').write('z')\nos.replace(n, os.path.join(here, 'alias.csv'))",
        "import shutil\nn = os.path.join(here, 'new.csv')\nopen(n, 'w').write('z')\n"
        "shutil.move(n, os.path.join(here, 'alias.csv'))",
    ):
        result, _ = _validate(world, op + "\n")
        assert _tripped(result)
        assert (sub / "data.csv").read_text() == "x,y\n1,2\n" and (sub / "alias.csv").is_symlink()


def test_copying_a_project_file_onto_itself_is_still_a_same_file_error(world):
    """`shutil.copyfile(data, data)`：真实执行抛 SameFileError，改道不能让它悄悄成功（Codex #1516 P2）。"""
    result, cand = _validate(
        world,
        "import shutil\nd = os.path.join(here, 'data.csv')\n"
        "for fn in (shutil.copyfile, shutil.copy, shutil.copy2):\n"
        "    try:\n        fn(d, d)\n        raise SystemExit('copied onto itself')\n"
        "    except OSError:\n        pass\n",  # SameFileError 是 OSError；Windows 3.12+ 真实 copy2(x, x) 抛共享冲突（WinError 32）
    )
    assert result["status"] == "ok", result
    assert not _redirected(cand, "sub/data.csv").exists()


def test_same_file_error_is_what_copyfile_and_copy_raise(world):
    result, _ = _validate(
        world,
        "import shutil\nd = os.path.join(here, 'data.csv')\n"
        "for fn in (shutil.copyfile, shutil.copy):\n"
        "    try:\n        fn(d, d)\n        raise SystemExit('copied onto itself')\n"
        "    except shutil.SameFileError:\n        pass\n",
    )
    assert result["status"] == "ok", result


def test_copy2_copy_copyfile_into_an_existing_project_file_land_in_the_overlay(world):
    """每个平台：copy2 / copy / copyfile 到项目里已有文件上，项目那份不动，结果在改道区（Windows 的 CopyFile2 不能绕过）。"""
    root = world[0]
    (root / "sub" / "src.csv").write_text("from-src")
    for i, call in enumerate(("copy2", "copy", "copyfile")):
        result, cand = _validate(
            world,
            "import shutil\n"
            f"shutil.{call}(os.path.join(here, 'src.csv'), data)\n"
            "assert open(data).read() == 'from-src'\n",
        )
        assert result["status"] == "ok", (call, result)
        assert _redirected(cand, "sub/data.csv").read_text() == "from-src", call
    assert (root / "sub" / "src.csv").read_text() == "from-src"


def test_copy2_into_a_project_directory_goes_to_the_overlay(world):
    _project_dir(world)
    (world[0] / "sub" / "src.csv").write_text("from-src")
    result, cand = _validate(
        world,
        "import shutil\nshutil.copy2(os.path.join(here, 'src.csv'), os.path.join(here, 'out'))\n"
        "assert open(os.path.join(here, 'out', 'src.csv')).read() == 'from-src'\n",
    )
    assert result["status"] == "ok", result
    assert _redirected(cand, "sub/out/src.csv").read_text() == "from-src"
    assert not (world[0] / "sub" / "out" / "src.csv").exists()


def test_rmdir_of_a_project_dir_trips_but_a_purely_staged_dir_can_be_removed(world):
    _project_dir(world)
    result, _ = _validate(world, "os.rmdir(os.path.join(here, 'out'))\n")
    assert _tripped(result)
    result, _ = _validate(
        world,
        "import shutil\nn = os.path.join(here, 'staged', 'deep')\nos.makedirs(n)\n"
        "assert os.path.isdir(n)\nopen(os.path.join(n, 'f.csv'), 'w').write('x')\n"
        "shutil.rmtree(os.path.join(here, 'staged'))\nassert not os.path.exists(n)\n",
    )
    assert result["status"] == "ok", result


def test_writing_a_file_over_a_project_directory_is_is_a_directory_error(world):
    _project_dir(world)
    result, cand = _validate(
        world,
        "o = os.path.join(here, 'out')\n"
        "for fn in (lambda: open(o, 'w'), lambda: open(o, 'a'),\n"
        "           lambda: os.open(o, os.O_WRONLY | os.O_CREAT)):\n"
        "    try:\n        fn()\n        raise SystemExit('created a file over a dir')\n"
        "    except IsADirectoryError:\n        pass\n"
        "assert os.path.isdir(o) and not os.path.isfile(o)\n",
    )
    assert result["status"] == "ok", result
    assert not _redirected(cand, "sub/out").exists()


def test_mkdir_over_project_file_raises_and_over_a_whited_out_file_trips(world):
    result, _ = _validate(
        world,
        "try:\n    os.mkdir(data)\n    raise SystemExit('mkdir over file')\n"
        "except FileExistsError:\n    pass\n",
    )
    assert result["status"] == "ok", result
    result, _ = _validate(world, "os.remove(data)\nos.mkdir(data)\n")
    assert _tripped(result)


def test_rename_onto_a_project_directory(world):
    _project_dir(world)
    result, _ = _validate(
        world,
        "open(data, 'w').write('n')\n"
        "try:\n    os.replace(data, os.path.join(here, 'out'))\n    raise SystemExit('replaced dir')\n"
        "except IsADirectoryError:\n    pass\n",
    )
    assert result["status"] == "ok", result
    result, _ = _validate(
        world,
        "os.makedirs(os.path.join(here, 'stg'))\nos.rename(os.path.join(here, 'stg'), os.path.join(here, 'out'))\n",
    )
    assert _tripped(result)


def test_copy_destinations_colliding_with_project_entries(world):
    _project_dir(world)
    result, _ = _validate(
        world,
        "import shutil\nsrc = os.path.join(here, 'srcdir')\nos.makedirs(src)\n"
        "open(os.path.join(src, 'a.csv'), 'w').write('a')\n"
        "o = os.path.join(here, 'out')\n"
        "try:\n    shutil.copyfile(os.path.join(src, 'a.csv'), o)\n    raise SystemExit('copyfile over dir')\n"
        "except IsADirectoryError:\n    pass\n"
        "try:\n    shutil.copytree(src, o)\n    raise SystemExit('copytree over dir')\n"
        "except FileExistsError:\n    pass\n"
        "shutil.copytree(src, os.path.join(here, 'fresh'))\nassert os.path.isfile(os.path.join(here, 'fresh', 'a.csv'))\n",
    )
    assert result["status"] == "ok", result
    result, _ = _validate(
        world,
        "import shutil\nos.makedirs(os.path.join(here, 'srcdir'))\n"
        "shutil.copytree(os.path.join(here, 'srcdir'), os.path.join(here, 'out'), dirs_exist_ok=True)\n",
    )
    assert _tripped(result)
    result, _ = _validate(
        world, "import shutil\nshutil.copymode(os.path.join(here, 'data.csv'), data)\n"
    )
    assert _tripped(result)


def test_exclusive_create_against_project_file_and_append_to_whited_out_file(world):
    result, cand = _validate(
        world,
        "for fn in (lambda: open(data, 'x'),\n"
        "           lambda: os.open(data, os.O_WRONLY | os.O_CREAT | os.O_EXCL)):\n"
        "    try:\n        fn()\n        raise SystemExit('x over existing')\n"
        "    except FileExistsError:\n        pass\n"
        "os.remove(data)\nopen(data, 'a').write('tail')\n"
        "assert open(data).read() == 'tail'\n",
    )
    assert result["status"] == "ok", result
    assert _redirected(cand, "sub/data.csv").read_text() == "tail"


def test_writes_outside_project_and_allowed_roots_still_trip(world):
    root, cand, runner = world
    workdir.set_mode(root, workdir.MODE_PROJECT)
    elsewhere = root.parent / "elsewhere"
    elsewhere.mkdir()
    cand.new_bytes = (
        "import matplotlib.pyplot as plt\n"
        f"open({str(elsewhere / 'x.txt')!r}, 'w').write('x')\n"
        "plt.plot([1])\nplt.savefig('o.png')\n"
    ).encode()
    cand.target.write_bytes(cand.new_bytes)
    result = ai_convert.run_validation(cand, runner)
    assert result["status"] == "error" and result["code"] == "write_outside_roots", result
    assert not (elsewhere / "x.txt").exists()


def test_c_level_write_into_the_project_is_still_caught_by_the_fingerprint(world):
    """改道的盲区（C 层 / 子进程写）：绊线与改道都看不见，指纹复核照旧判失败。"""
    root, cand, runner = world
    workdir.set_mode(root, workdir.MODE_PROJECT)
    cand.new_bytes = (
        "import os\nimport matplotlib.pyplot as plt\n"
        "here = os.path.dirname(os.path.abspath(__file__))\n"
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-c', 'open(%r, \"w\").write(\"c\")' % "
        "os.path.join(here, 'c_level.txt')], check=True)\n"
        "plt.plot([1])\nplt.savefig('o.png')\n"
    ).encode()
    cand.target.write_bytes(cand.new_bytes)
    ai_convert.run_validation(cand, runner)
    try:
        failed = [
            c
            for c in ai_convert.ADAPTER._project_untouched(cand, "precommit")
            if c.verdict != ai_convert.VERIFIED
        ]
        assert failed, "指纹复核应判出项目被改动"
    finally:
        (root / "sub" / "c_level.txt").unlink(missing_ok=True)


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


def test_edit_candidate_writing_into_the_project_is_redirected(edit_world):
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
        assert result["status"] == "ok", result
        assert not (figs / "sneaky.csv").exists()
        assert (cand.dir / ai_convert.REDIRECT_DIRNAME / "sneaky.csv").read_text() == "x"
        # 改道区不算“候选里多出来的文件”
        assert not any(k.startswith("redirected/") for k in ai_figure_edit._tree(cand.dir))
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


def test_copy2_never_reaches_winapi_copyfile2_during_validation(world):
    """在 POSIX 上模拟 Windows 3.12+ 的 `shutil._winapi.CopyFile2` 分支：改道期间 copy2 不得走到它。"""
    (world[0] / "sub" / "src.csv").write_text("from-src")
    result, cand = _validate(
        world,
        "import shutil, types\n"
        "def boom(*a, **k):\n    raise RuntimeError('CopyFile2 reached')\n"
        "shutil._winapi = types.SimpleNamespace(CopyFile2=boom, COPY_FILE_ALLOW_DECRYPTED_DESTINATION=0,\n"
        "                                        COPY_FILE_COPY_SYMLINK=0, ERROR_PRIVILEGE_NOT_HELD=1314, ERROR_ACCESS_DENIED=5)\n"
        "shutil.copy2(os.path.join(here, 'src.csv'), data)\n"
        "assert open(data).read() == 'from-src'\n",
    )
    assert result["status"] == "ok", result
    assert _redirected(cand, "sub/data.csv").read_text() == "from-src"


def test_pathlib_write_unlink_rename_are_redirected_too(world):
    """Python 3.10 的 pathlib 在 import 时就绑定了 io.open / os.*：改道必须也盖住 Path 的写 / 删 / 改名。"""
    result, cand = _validate(
        world,
        "from pathlib import Path\n"
        "p = Path(here) / 'p.csv'\np.write_bytes(b'bytes')\nassert p.read_bytes() == b'bytes'\n"
        "p.write_bytes(b'text')\nassert p.read_bytes() == b'text'\n"
        "q = Path(here) / 'q.csv'\np.rename(q)\nassert q.read_bytes() == b'text' and not os.path.exists(p)\n"
        "r = Path(here) / 'r.csv'\nq.replace(r)\n"
        "(Path(here) / 'newdir').mkdir()\n(Path(here) / 'newdir').rmdir()\n"
        "assert r.read_bytes() == b'text'\n"
        "Path(here, 'kept.csv').write_bytes(b'kept')\n"
        "Path(here, 't.csv').touch()\n",
    )
    assert result["status"] == "ok", result
    assert _redirected(cand, "sub/kept.csv").read_text() == "kept"
    assert _redirected(cand, "sub/t.csv").exists()
    assert not (world[0] / "sub" / "kept.csv").exists()
