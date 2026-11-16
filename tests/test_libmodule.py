"""`engine/libmodule`：只定义了画图函数的库模块的判定，与「谁 import 了它」的反查（反馈包 TVD-Y5JF）。

判定按 L-154 逐种写法各一条：断言的是「会不会出提示」，不是内部分支。反查用真实的临时目录树，
「import 了它」由 `importscan` 的正向解析定，不按文件名猜。
"""

from __future__ import annotations

import ast
import os
import sys
import textwrap
from pathlib import Path

import pytest

from tavotto.engine import libmodule

# 与用户那份文件同形（自己写的最小复现）：docstring、`matplotlib.use`、常量构造、只带参数的画图函数
LIB = textwrap.dedent(
    '''
    """Plot helpers; the entry script prepares the data."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    import numpy as np

    DPI = 300
    CMAP = LinearSegmentedColormap.from_list("c", ["#000", "#fff"])
    COLORS = {"a": "#123456"}


    def _save(fig, path):
        fig.savefig(path, dpi=DPI)
        plt.close(fig)


    def plot_curves(df, out_path):
        fig = plt.figure(figsize=(4, 3))
        ax = fig.add_subplot(111)
        ax.plot(np.asarray(df["x"]), np.asarray(df["y"]))
        _save(fig, out_path)
    '''
)


def _is_lib(src: str, tmp_path: Path | None = None, **kw) -> bool:
    dirs = (tmp_path,) if tmp_path is not None else ()
    return libmodule.is_library(ast.parse(textwrap.dedent(src)), local_dirs=dirs, **kw)


# ---------------------------------------------------------------- 判定：是库模块


def test_the_user_shaped_plot_helper_module_is_a_library():
    assert _is_lib(LIB)


@pytest.mark.parametrize(
    "tail",
    [
        "plt.rcParams.update({'font.size': 8})\n",
        "plt.style.use('default')\n",
        "import seaborn as sns\nsns.set_theme()\n",
        "np.random.seed(0)\n",
        "import os\nHERE = os.path.dirname(os.path.abspath(__file__))\n",
        "class Settings:\n    dpi = 300\n\n    def scaled(self, k):\n        return self.dpi * k\n",
    ],
)
def test_configuration_at_import_time_still_reads_as_a_library(tail):
    assert _is_lib(LIB + "\n" + tail)


def test_a_package_module_with_relative_imports_is_a_library():
    assert _is_lib("from .config import Settings\n" + LIB)


def test_try_import_and_type_checking_blocks_do_not_hide_a_library():
    src = LIB + textwrap.dedent(
        """
        try:
            import seaborn as sns
        except ImportError:
            sns = None
        if TYPE_CHECKING:
            from pandas import DataFrame
        if sys.version_info >= (3, 11):
            import tomllib
        """
    )
    assert _is_lib(src)


# ---------------------------------------------------------------- 判定：不是（产品会跑出东西，或判不出）


@pytest.mark.parametrize(
    "tail",
    [
        # 有 __main__ 守卫：顶层会跑
        'if __name__ == "__main__":\n    plot_curves({"x": [1], "y": [1]}, "a.png")\n',
        # 零参 main：产品会调它
        'def main():\n    plot_curves({"x": [1], "y": [1]}, "a.png")\n',
        # 零参自定义画图函数：产品会挑它当入口
        "def draw():\n    plt.plot([1, 2])\n",
        # 顶层直接画
        "plt.plot([1, 2])\n",
        # 顶层调本文件的函数
        'plot_curves({"x": [1], "y": [1]}, "a.png")\n',
        # 顶层赋值里调本文件的函数
        'RESULT = plot_curves({"x": [1], "y": [1]}, "a.png")\n',
        # 顶层控制流：判不出就不说
        "for k in COLORS:\n    print(k)\n",
        "with open('x.txt') as fh:\n    data = fh.read()\n",
        # 普通 if
        "if DPI > 100:\n    print('hi')\n",
        # import * 把名字藏起来了
        "from helpers import *\n",
        # 装饰器调本文件的函数
        "@_save\ndef other(df):\n    return df\n",
        # 把本文件的函数交出去跑（没有直接调用，也不是只定义）
        "RESULTS = list(map(plot_curves, [{}], ['a.png']))\n",
        "import fire\nfire.Fire(plot_curves)\n",
        "import pandas as pd\nDF = pd.DataFrame({'k': [1]})\nDF.groupby('k').apply(plot_curves)\n",
        # 画图库的非配置调用（改名导入按原名判）、画图库的高层函数、来历不明的第三方（Codex #1940）
        "from matplotlib.pyplot import subplots as make_canvas\nFIG, AX = make_canvas()\n",
        "import seaborn as sns\nsns.relplot(x=[1], y=[1])\n",
        "import mysterious_pkg\nmysterious_pkg.go()\n",
        # 类体与注解在定义时就执行
        "class Preview:\n    fig = plt.figure()\n",
        "def other(x: plt.figure()):\n    return x\n",
        # 实例化本文件的类
        "class Plotter:\n    def run(self):\n        plt.plot([1])\n\n\nPlotter().run()\n",
    ],
)
def test_scripts_the_product_would_run_are_not_libraries(tail):
    assert not _is_lib(LIB + "\n" + tail)


def test_a_top_level_call_into_a_local_module_is_not_a_library(tmp_path):
    (tmp_path / "pipeline.py").write_text("def run():\n    pass\n", encoding="utf-8")
    src = "from pipeline import run\n" + LIB + "\nrun()\n"
    assert not _is_lib(src, tmp_path)
    # 同样的写法，`run` 来自第三方 / 标准库：照样是库（只是配置调用）
    assert _is_lib(
        "from warnings import filterwarnings\n" + LIB + "\nfilterwarnings('ignore')\n", tmp_path
    )


def test_a_relative_import_called_at_top_level_is_not_a_library():
    assert not _is_lib("from .pipeline import run\n" + LIB + "\nrun()\n")


def test_a_registered_function_entry_is_not_a_library():
    assert not _is_lib(LIB, registered_entry="plot_curves")
    assert _is_lib(LIB, registered_entry="__main__")


def test_a_utility_module_that_never_plots_is_not_flagged():
    assert not _is_lib("import numpy as np\n\ndef normalize(x):\n    return x / np.max(x)\n")


def test_an_empty_or_constant_only_module_is_not_flagged():
    assert not _is_lib("X = 1\n")
    assert not _is_lib('"""doc"""\n')


# ---------------------------------------------------------------- 反查：谁 import 了它


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch, tmp_path):
    libmodule.reset_cache()
    # 主目录换到临时目录之外的一个兄弟目录：「不扫主目录」的护栏不误伤用例的仓库
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path.parent / "__home__"))
    yield
    libmodule.reset_cache()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _write(repo / "pyproject.toml", "[project]\nname = 'x'\n")
    _write(repo / "labkit" / "__init__.py", "")
    _write(repo / "labkit" / "plot_helpers.py", LIB)
    return repo


def test_the_entry_at_the_repo_root_is_found_from_a_project_opened_on_the_package(tmp_path):
    repo = _repo(tmp_path)
    _write(
        repo / "make_all_figures.py",
        """
        from labkit.plot_helpers import plot_curves

        plot_curves({"x": [1, 2], "y": [3, 4]}, "out.png")
        """,
    )
    project = repo / "labkit"
    hint = libmodule.analyze(project, "plot_helpers.py")
    assert hint is not None
    assert hint["scope"] == "repo" and hint["complete"] is True
    (entry,) = hint["entries"]
    assert entry["in_project"] is False and "script" not in entry
    assert Path(entry["open_root"]) == Path(os.path.realpath(repo))
    assert entry["open_script"] == "make_all_figures.py"
    assert entry["direct"] is True


def test_an_entry_inside_the_project_carries_its_project_relative_path(tmp_path):
    repo = _repo(tmp_path)
    _write(
        repo / "scripts" / "make_figs.py",
        """
        import labkit.plot_helpers as ep

        if __name__ == "__main__":
            ep.plot_curves({"x": [1], "y": [1]}, "a.png")
        """,
    )
    hint = libmodule.analyze(repo, "labkit/plot_helpers.py")
    (entry,) = hint["entries"]
    assert entry["in_project"] is True
    assert entry["script"] == entry["open_script"] == "scripts/make_figs.py"
    assert Path(entry["open_root"]) == Path(os.path.realpath(repo))


def test_an_entry_that_reaches_the_module_through_another_library_is_found(tmp_path):
    repo = _repo(tmp_path)
    _write(
        repo / "labkit" / "panels.py",
        "from . import plot_helpers\n\n\ndef plot_all(df):\n    plot_helpers.plot_curves(df, 'a.png')\n",
    )
    _write(
        repo / "run_all.py",
        "from labkit.panels import plot_all\n\nplot_all({'x': [1], 'y': [1]})\n",
    )
    hint = libmodule.analyze(repo, "labkit/plot_helpers.py")
    rels = [e["open_script"] for e in hint["entries"]]
    assert rels == ["run_all.py"]
    assert hint["entries"][0]["direct"] is False


def test_an_in_project_entry_that_only_resolves_from_the_repo_root_opens_the_repo(tmp_path):
    """项目开在包目录上、入口也在包里但写的是 `from labkit.x import …`：在当前项目里跑解析不到 `labkit`，按「打开仓库再运行」给。"""
    repo = _repo(tmp_path)
    _write(
        repo / "labkit" / "run_figs.py",
        "from labkit.plot_helpers import plot_curves\n\nplot_curves({'x': [1], 'y': [1]}, 'a.png')\n",
    )
    hint = libmodule.analyze(repo / "labkit", "plot_helpers.py")
    (entry,) = hint["entries"]
    assert entry["in_project"] is False and "script" not in entry
    assert Path(entry["open_root"]) == Path(os.path.realpath(repo))
    assert entry["open_script"] == "labkit/run_figs.py"


def test_an_entry_that_only_works_as_python_dash_m_is_not_offered(tmp_path):
    """包里用相对导入调用它的脚本：按 `python -m` 才解析得到，产品按脚本路径跑会 ImportError，不推荐（Codex #1940）。"""
    repo = _repo(tmp_path)
    _write(
        repo / "labkit" / "run_rel.py",
        "from .plot_helpers import plot_curves\n\nplot_curves({'x': [1], 'y': [1]}, 'a.png')\n",
    )
    assert libmodule.analyze(repo, "labkit/plot_helpers.py")["entries"] == []


@pytest.mark.skipif(sys.platform == "win32", reason="chmod 000 在 Windows 上不挡读")
def test_an_unreadable_candidate_makes_the_search_incomplete(tmp_path):
    repo = _repo(tmp_path)
    locked = _write(
        repo / "draw.py", "from labkit.plot_helpers import plot_curves\nplot_curves({}, 'a')\n"
    )
    locked.chmod(0)
    try:
        hint = libmodule.analyze(repo, "labkit/plot_helpers.py")
    finally:
        locked.chmod(0o644)
    assert hint["entries"] == [] and hint["complete"] is False


def test_the_in_project_recheck_shares_the_scan_budget(tmp_path, monkeypatch):
    """项目内复核与前面共用解析次数：用完了就按「打开仓库再运行」给（在仓库根跑两种情况都成立），不另开一份预算。"""
    repo = _repo(tmp_path)
    _write(
        repo / "labkit" / "run_figs.py", "import labkit.plot_helpers as h\nh.plot_curves({}, 'a')\n"
    )
    monkeypatch.setattr(libmodule, "MAX_SCANS", 1)
    hint = libmodule.analyze(repo / "labkit", "plot_helpers.py")
    (entry,) = hint["entries"]
    assert entry["in_project"] is False
    assert Path(entry["open_root"]) == Path(os.path.realpath(repo))


def test_a_file_that_only_mentions_the_name_is_not_an_importer(tmp_path):
    repo = _repo(tmp_path)
    _write(repo / "notes.py", "print('see plot_helpers for details')\n")
    _write(repo / "other" / "plot_helpers.py", "import matplotlib.pyplot as plt\nplt.plot([1])\n")
    _write(repo / "uses_other.py", "import sys\nsys.path.insert(0, 'other')\n")
    hint = libmodule.analyze(repo, "labkit/plot_helpers.py")
    assert hint["entries"] == []


def test_tests_that_import_the_module_are_not_offered_as_entries(tmp_path):
    repo = _repo(tmp_path)
    _write(
        repo / "tests" / "test_plots.py",
        "from labkit.plot_helpers import plot_curves\nplot_curves({}, 'a')\n",
    )
    assert libmodule.analyze(repo, "labkit/plot_helpers.py")["entries"] == []


def test_without_a_repo_marker_the_search_stays_inside_the_project(tmp_path):
    outer = tmp_path / "outer"
    _write(outer / "pkg" / "plot_helpers.py", LIB)
    _write(outer / "draw.py", "from pkg.plot_helpers import plot_curves\nplot_curves({}, 'a')\n")
    hint = libmodule.analyze(outer / "pkg", "plot_helpers.py")
    assert hint["scope"] == "project" and hint["entries"] == []


def test_the_home_directory_is_never_taken_as_the_repo(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write(repo / "draw.py", "from labkit.plot_helpers import plot_curves\nplot_curves({}, 'a')\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: repo))
    hint = libmodule.analyze(repo / "labkit", "plot_helpers.py")
    assert hint["scope"] == "project" and hint["entries"] == []


def test_a_marker_inside_the_project_does_not_shrink_the_search(tmp_path):
    project = tmp_path / "proj"
    _write(project / "sub" / "pyproject.toml", "")
    _write(project / "sub" / "plot_helpers.py", LIB)
    _write(
        project / "main.py",
        "import sys\nfrom sub.plot_helpers import plot_curves\nplot_curves({}, 'a')\n",
    )
    hint = libmodule.analyze(project, "sub/plot_helpers.py")
    assert hint["scope"] == "project"
    assert [e["script"] for e in hint["entries"]] == ["main.py"]


@pytest.mark.skipif(sys.platform == "win32", reason="符号链接需要管理员权限")
def test_symlinked_directories_are_not_followed(tmp_path):
    repo = _repo(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    _write(
        elsewhere / "draw.py", "from labkit.plot_helpers import plot_curves\nplot_curves({}, 'a')\n"
    )
    (repo / "linked").symlink_to(elsewhere, target_is_directory=True)
    hint = libmodule.analyze(repo, "labkit/plot_helpers.py")
    assert hint["entries"] == []


def test_a_script_that_is_not_a_library_gets_no_hint(tmp_path):
    project = tmp_path / "p"
    _write(
        project / "fig.py", "import matplotlib.pyplot as plt\nplt.plot([1])\nplt.savefig('a.png')\n"
    )
    assert libmodule.analyze(project, "fig.py") is None


def test_the_hint_is_recomputed_when_the_script_changes(tmp_path):
    repo = _repo(tmp_path)
    assert libmodule.analyze(repo, "labkit/plot_helpers.py") is not None
    path = repo / "labkit" / "plot_helpers.py"
    path.write_text(
        LIB + '\nif __name__ == "__main__":\n    plot_curves({}, "a")\n', encoding="utf-8"
    )
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert libmodule.analyze(repo, "labkit/plot_helpers.py") is None


def test_a_spent_budget_reports_an_incomplete_search(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write(repo / "draw.py", "from labkit.plot_helpers import plot_curves\nplot_curves({}, 'a')\n")
    monkeypatch.setattr(libmodule, "MAX_SCANS", 0)
    hint = libmodule.analyze(repo, "labkit/plot_helpers.py")
    assert hint["entries"] == [] and hint["complete"] is False
