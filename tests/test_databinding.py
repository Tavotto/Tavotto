"""`engine/databinding.py`（U03，ADR 0057）：脚本的相对数据路径在哪个候选 cwd 下找得到。

判据的主语：**脚本源码里像相对数据路径的字符串常量**，在「脚本目录」与「项目根」下各自
是不是一个文件——不执行脚本、不猜、不搜索同名。五档结论各一条正例，外加「不猜」的负例。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tavotto.engine import databinding as db, discover


# ---------------------------------------------------------------- 字面量判据
@pytest.mark.parametrize(
    "text,expected",
    [
        ("data.csv", True),
        ("data/x.csv", True),
        ("../data/points.csv", True),
        ("runs/2024/a.lammpstrj", True),
        ("data/run1", True),  # 带目录分隔符的裸名
        ("os.path", False),  # 模块名
        ("matplotlib.pyplot", False),
        ("/abs/x.csv", False),  # 绝对路径：明确指名的位置，不在本模块的问题里
        ("C:\\x.csv", False),
        ("-o", False),
        ("http://x/y.csv", False),
        ("{stem}.pdf", False),  # 模板
        ("*.csv", False),  # glob：动态
        ("figures/", False),
        ("Fig1", False),
        ("y = 1.5x + 1", False),
        ("utf-8", False),
        ("", False),
        ("..", False),
        (" data.csv", False),  # 首尾空白：不是路径写法
    ],
)
def test_relative_data_path_predicate(text, expected):
    assert db.looks_like_relative_data_path(text) is expected


def test_save_calls_are_outputs_not_reads_and_the_func_table_mirrors_discover():
    src = "import pandas as pd\ndf = pd.read_csv('data/x.csv')\nfig.savefig('out/fig.pdf')\nplt.imsave(fname='a.png', arr=df)\n"
    lits = db.relative_path_literals(src)
    assert lits == {"reads": ["data/x.csv"], "outputs": ["out/fig.pdf", "a.png"]}
    # 存图调用名的唯一出处在 discover；这里是镜像（import 图的原因见模块头），必须相等
    assert db.SAVE_FUNCS == frozenset(discover.SAVE_FUNCS)


def test_unparseable_source_yields_no_literals():
    assert db.relative_path_literals("def (:\n") == {"reads": [], "outputs": []}


# ---------------------------------------------------------------- 五档结论
def _project(tmp_path: Path, script_rel: str, source: str, files: dict[str, str]) -> Path:
    root = tmp_path / "proj"
    (root / Path(script_rel)).parent.mkdir(parents=True, exist_ok=True)
    (root / script_rel).write_text(source, encoding="utf-8")
    for rel, content in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content, encoding="utf-8")
    return root


def test_none_when_the_script_has_no_relative_data_literals(tmp_path):
    root = _project(tmp_path, "s/fig.py", "x = [1, 2]\nfig.savefig('fig.pdf')\n", {})
    assert db.evidence(root / "s/fig.py", root)["verdict"] == db.VERDICT_NONE


def test_default_ok_when_the_script_directory_has_the_data(tmp_path):
    root = _project(tmp_path, "s/fig.py", "open('data.csv')\n", {"s/data.csv": "x\n1\n"})
    ev = db.evidence(root / "s/fig.py", root)
    assert ev["verdict"] == db.VERDICT_DEFAULT_OK
    assert list(ev["candidates"]["script.parent"]["found"]) == ["data.csv"]
    assert ev["candidates"]["project.root"]["missing"] == ["data.csv"]


def test_default_ok_when_script_is_at_the_project_root(tmp_path):
    root = _project(tmp_path, "fig.py", "open('data.csv')\n", {"data.csv": "x\n1\n"})
    ev = db.evidence(root / "fig.py", root)
    assert ev["same_dir"] is True and ev["verdict"] == db.VERDICT_DEFAULT_OK


def test_project_root_when_only_the_root_has_the_data(tmp_path):
    root = _project(tmp_path, "s/fig.py", "open('data/x.csv')\n", {"data/x.csv": "x\n1\n"})
    ev = db.evidence(root / "s/fig.py", root)
    assert ev["verdict"] == db.VERDICT_PROJECT_ROOT
    assert ev["conflicts"] == []


def test_ambiguous_when_both_places_have_a_same_name_file_with_different_bytes(tmp_path):
    root = _project(
        tmp_path,
        "s/fig.py",
        "open('data.csv')\n",
        {"s/data.csv": "x\n2\n4\n8\n", "data.csv": "x\n200\n400\n800\n"},
    )
    ev = db.evidence(root / "s/fig.py", root)
    assert ev["verdict"] == db.VERDICT_AMBIGUOUS
    assert ev["conflicts"] == ["data.csv"]


def test_identical_bytes_in_both_places_is_not_ambiguous(tmp_path):
    root = _project(
        tmp_path, "s/fig.py", "open('data.csv')\n", {"s/data.csv": "x\n1\n", "data.csv": "x\n1\n"}
    )
    assert db.evidence(root / "s/fig.py", root)["verdict"] == db.VERDICT_DEFAULT_OK


def test_ambiguous_when_the_two_places_hold_different_halves_of_the_reads(tmp_path):
    root = _project(
        tmp_path,
        "s/fig.py",
        "open('a.csv'); open('b.csv')\n",
        {"s/a.csv": "a\n", "b.csv": "b\n"},
    )
    assert db.evidence(root / "s/fig.py", root)["verdict"] == db.VERDICT_AMBIGUOUS


def test_unknown_when_nothing_is_found_anywhere_and_no_same_name_search_happens(tmp_path):
    """FO08：找不到就是找不到——旁边目录里有同名文件也**不去翻**。"""
    root = _project(
        tmp_path,
        "s/fig.py",
        "open('experiment.csv')\n",
        {"elsewhere/experiment.csv": "x\n999\n"},
    )
    ev = db.evidence(root / "s/fig.py", root)
    assert ev["verdict"] == db.VERDICT_UNKNOWN
    assert ev["candidates"]["script.parent"]["found"] == {}
    assert ev["candidates"]["project.root"]["found"] == {}


def test_absolute_paths_are_never_touched(tmp_path):
    """FO04：明确有效的外部绝对路径沿用，本模块连看都不看（不搬、不重映射）。"""
    ext = tmp_path / "ext" / "x.csv"
    ext.parent.mkdir()
    ext.write_text("x\n1\n", encoding="utf-8")
    root = _project(tmp_path, "s/fig.py", f"open({str(ext)!r})\n", {})
    ev = db.evidence(root / "s/fig.py", root)
    assert ev["reads"] == [] and ev["verdict"] == db.VERDICT_NONE


def test_only_files_count_as_evidence_not_directories(tmp_path):
    root = _project(tmp_path, "s/fig.py", "open('data/run1')\n", {"data/run1/.keep": ""})
    assert db.evidence(root / "s/fig.py", root)["verdict"] == db.VERDICT_UNKNOWN
