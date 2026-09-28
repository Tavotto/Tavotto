"""`engine/databinding.py`（U03，ADR 0057）：脚本的相对数据路径在哪个候选 cwd 下找得到。

判据的主语：**脚本源码里像相对数据路径的字符串常量**，在「脚本目录」与「项目根」下各自
是不是一个文件——不执行脚本、不猜、不搜索同名。五档结论各一条正例，外加「不猜」的负例。
"""

from __future__ import annotations

import os
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
        ("C:/x.csv", False),  # Windows 盘符绝对路径（作者在 Windows 上写、脚本在 POSIX 宿主上被扫）
        ("\\\\srv\\share\\x.csv", False),  # UNC
        ("C:x.csv", False),  # 盘符相对：也是指名的位置
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


def test_a_literal_that_escapes_the_project_root_is_never_touched(tmp_path, monkeypatch):
    """Codex 评 #459（P1）：`open('../../secret.csv')` 这样的字面量 join 到脚本目录后落在项目根
    之外——准备阶段不许替脚本去看它：不 stat、不读、不 hash（hash / size 会进准备计划的证据）。
    它登记在 `outside`，既不算找到也不算 missing，判决按剩下的字面量走。"""
    secret = tmp_path / "secret.csv"
    secret.write_text("k\nv\n", encoding="utf-8")
    root = _project(tmp_path, "s/fig.py", "open('../../secret.csv')\nopen('data.csv')\n", {})
    touched: list[str] = []
    real_sha1 = db._sha1_of

    def spy(path):
        touched.append(str(Path(path).resolve()))
        return real_sha1(path)

    monkeypatch.setattr(db, "_sha1_of", spy)
    ev = db.evidence(root / "s/fig.py", root)
    parent = ev["candidates"]["script.parent"]
    assert parent["outside"] == ["../../secret.csv"]
    assert "../../secret.csv" not in parent["found"] and "../../secret.csv" not in parent["missing"]
    assert ev["candidates"]["project.root"]["outside"] == ["../../secret.csv"]  # 从根起也出界
    assert str(secret.resolve()) not in touched, "项目外的文件被 hash 了"
    assert ev["verdict"] == db.VERDICT_UNKNOWN  # data.csv 两处都没有；出界那条不参与判决


@pytest.mark.skipif(os.name == "nt", reason="软链接在 Windows 上要特权")
def test_a_symlink_inside_the_project_pointing_outside_is_outside(tmp_path):
    """字符串上在项目里、实体在项目外（`data/x.csv -> ~/secret.csv`）：按 realpath 判，与
    `projectenv.within` 同一个判据。"""
    secret = tmp_path / "secret.csv"
    secret.write_text("k\nv\n", encoding="utf-8")
    root = _project(tmp_path, "fig.py", "open('data/x.csv')\n", {})
    (root / "data").mkdir()
    os.symlink(secret, root / "data" / "x.csv")
    ev = db.evidence(root / "fig.py", root)
    assert ev["candidates"]["project.root"]["outside"] == ["data/x.csv"]
    assert ev["candidates"]["project.root"]["found"] == {}
    assert ev["verdict"] == db.VERDICT_UNKNOWN


def test_a_script_outside_the_project_root_yields_no_evidence_and_is_not_read(
    tmp_path, monkeypatch
):
    """CodeQL #143 / #144：脚本路径本身也钉在项目根之内（realpath）再读；`../` 到项目外的
    脚本一个字节不读，证据就是「没有字面量」（verdict none），不是把项目外文件当脚本解析。"""
    root = _project(tmp_path, "fig.py", "open('data.csv')\n", {"data.csv": "x\n1\n"})
    outside = tmp_path / "secret.py"
    outside.write_text("open('data.csv')\n", encoding="utf-8")
    reads: list[str] = []
    real_read = Path.read_bytes

    def spy(self):
        reads.append(str(Path(self).resolve()))
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", spy)
    ev = db.evidence(root / ".." / "secret.py", root)
    assert ev["verdict"] == db.VERDICT_NONE and ev["reads"] == [] and ev["candidates"] == {}
    assert str(outside.resolve()) not in reads, "项目外的脚本被读了"
    # 对照：项目里的同名脚本照常
    assert db.evidence(root / "fig.py", root)["verdict"] == db.VERDICT_DEFAULT_OK


def test_only_files_count_as_evidence_not_directories(tmp_path):
    root = _project(tmp_path, "s/fig.py", "open('data/run1')\n", {"data/run1/.keep": ""})
    assert db.evidence(root / "s/fig.py", root)["verdict"] == db.VERDICT_UNKNOWN


# ---------------------------------------------------------------- 探路调用（ADR 0084）
#: 用户实报（2026-09-26）的原样形状：相对 glob 找
#: 同目录的轨迹，找不到就打印一句退出。沙盒 cwd 下 glob 是空的，而旧判据把 glob 模式当「动态」
#: 不算证据（verdict none）→ 首开不问、沙盒里跑、一张图都不画。
USER_GLOB = (
    "import glob\n"
    "all_files = sorted(glob.glob('run-*-*[Ll]ongrun.traj'))\n"
    "if not all_files:\n"
    "    print('[ERROR] no data files found')\n"
    "    exit()\n"
)
TRAJ = {
    "长时间 数据/run-Ar-longrun.traj": "ITEM: TIMESTEP\n0\n",
    "长时间 数据/run-O-longrun.traj": "ITEM: TIMESTEP\n0\n",
}


@pytest.mark.parametrize(
    "source,expected",
    [
        (USER_GLOB, [("glob", "run-*-*[Ll]ongrun.traj")]),
        (
            "from glob import glob, iglob\nglob('*.csv')\niglob(pathname='d/*.x')\n",
            [("glob", "*.csv"), ("glob", "d/*.x")],
        ),
        (
            "import os\nos.listdir()\nos.listdir('runs')\nos.scandir('.')\nos.walk('1')\n",
            [("dir", "."), ("dir", "runs"), ("dir", "1")],
        ),
        (
            "import os\nos.path.exists('1/run_1.traj')\nos.path.isdir('1')\n",
            [("path", "1/run_1.traj"), ("path", "1")],
        ),
        (
            "from ovito.io import import_file\nimport_file('a.traj')\n",
            [("path", "a.traj")],
        ),
        (
            "from pathlib import Path\nPath('d').glob('*.csv')\nPath('d').rglob('*.x')\nPath().iterdir()\n",
            [("glob", "d/*.csv"), ("glob", "d/**/*.x"), ("dir", ".")],
        ),
        (
            "import pathlib\npathlib.Path.cwd().glob('*.npy')\npathlib.Path('1/x.dat').exists()\n",
            [("glob", "*.npy"), ("path", "1/x.dat")],
        ),
        # 说不出话的一律不算：绝对的、动态的、`Path(__file__)` 起算的、`~`、模板
        (
            "import glob, os\nfrom pathlib import Path\nglob.glob('/abs/*.csv')\nglob.glob(f'{p}/*.csv')\n"
            "Path(__file__).parent.glob('*.csv')\nos.listdir(d)\nos.path.exists('~/x')\nos.path.exists('%s.csv')\n",
            [],
        ),
    ],
)
def test_probe_calls_are_recognised_only_from_constant_relative_targets(source, expected):
    got = [(p["kind"], p["target"]) for p in db.probe_literals(source)]
    assert got == expected


def test_a_relative_glob_that_only_matches_in_the_script_directory_is_script_parent_evidence(
    tmp_path,
):
    """主语：脚本目录（路径含中文与空格）下有匹配、项目根下没有 → `script_parent`；打开类字面量为空。"""
    root = _project(tmp_path, "长时间 数据/analysis.py", USER_GLOB, TRAJ)
    ev = db.evidence(root / "长时间 数据/analysis.py", root)
    assert ev["reads"] == []
    assert ev["probes"] == ["run-*-*[Ll]ongrun.traj"]
    assert ev["candidates"]["script.parent"]["probes"]["found"] == ["run-*-*[Ll]ongrun.traj"]
    assert ev["candidates"]["project.root"]["probes"]["missing"] == ["run-*-*[Ll]ongrun.traj"]
    assert ev["verdict"] == db.VERDICT_SCRIPT_PARENT


def test_the_same_glob_with_the_script_at_the_project_root_is_still_script_parent(tmp_path):
    """用户的真实布局：脚本就在项目根（两个候选是同一个目录），仍然要问——沙盒救不回 glob。"""
    root = _project(
        tmp_path, "analysis.py", USER_GLOB, {k.split("/", 1)[1]: v for k, v in TRAJ.items()}
    )
    ev = db.evidence(root / "analysis.py", root)
    assert ev["same_dir"] is True and ev["verdict"] == db.VERDICT_SCRIPT_PARENT


def test_exists_on_a_file_the_fallback_would_open_is_no_longer_default_ok(tmp_path):
    """ADR 0047 背景里那九个 ovito 脚本的形状：`exists("1/x")` 先判再交给 C++。字面量在脚本目录
    找得到，旧判据说 `default_ok`（沙盒回退够用）——对 `open` 够，对 `exists` 不够。"""
    src = "import os\nif os.path.exists('1/run_1.traj'):\n    open('1/run_1.traj')\n"
    root = _project(tmp_path, "s/fig.py", src, {"s/1/run_1.traj": "a b"})
    assert db.evidence(root / "s/fig.py", root)["verdict"] == db.VERDICT_SCRIPT_PARENT
    # 对照：只有 open、没有探路调用的同一份数据，结论照旧
    root2 = _project(
        tmp_path / "b",
        "s/fig.py",
        "open('1/run_1.traj')\n",
        {"s/1/run_1.traj": "a b"},
    )
    assert db.evidence(root2 / "s/fig.py", root2)["verdict"] == db.VERDICT_DEFAULT_OK


@pytest.mark.parametrize(
    "files,expected",
    [
        ({"x-1.csv": "1"}, db.VERDICT_PROJECT_ROOT),  # 只有项目根有匹配
        ({"x-1.csv": "1", "s/x-2.csv": "2"}, db.VERDICT_AMBIGUOUS),  # 两处都有：机器不挑
        ({}, db.VERDICT_NONE),  # 哪儿都没有：不说话，与没有探路调用时逐字相同
    ],
)
def test_where_the_glob_matches_decides_and_nowhere_means_silence(tmp_path, files, expected):
    root = _project(tmp_path, "s/fig.py", "import glob\nglob.glob('x-*.csv')\n", files)
    assert db.evidence(root / "s/fig.py", root)["verdict"] == expected


def test_probe_and_open_evidence_pointing_at_different_directories_is_ambiguous(tmp_path):
    src = "import glob\nglob.glob('*.traj')\nopen('data/x.csv')\n"
    root = _project(tmp_path, "s/fig.py", src, {"s/a.traj": "1", "data/x.csv": "x"})
    assert db.evidence(root / "s/fig.py", root)["verdict"] == db.VERDICT_AMBIGUOUS


def test_a_glob_that_escapes_the_project_is_neither_listed_nor_found(tmp_path, monkeypatch):
    """与 `_lookup` 同一条纪律：`../` 出到项目外的探路目标不列目录、不算找到。"""
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.csv").write_text("k", encoding="utf-8")
    root = _project(
        tmp_path,
        "fig.py",
        "import glob, os\nglob.glob('../outside/*.csv')\nos.listdir('../outside')\n",
        {},
    )
    listed = _spy_scandir(monkeypatch)
    ev = db.evidence(root / "fig.py", root)
    assert ev["candidates"]["project.root"]["probes"]["outside"] == [
        "../outside/*.csv",
        "../outside",
    ]
    assert not [d for d in listed if "outside" in d], "项目外的目录被列了"
    # `../outside` 同时是一条打开类字面量（带分隔符），出界、不参与判决 → 与旧判据相同的 unknown
    assert ev["verdict"] == db.VERDICT_UNKNOWN


def test_listing_the_cwd_itself_counts_for_the_script_directory_only(tmp_path):
    """`os.listdir()` 不指名任何东西，在哪个候选下都「存在」：只记脚本目录那档（产品的默认假设），
    否则任何不在项目根上的脚本 `listdir()` 一下就成了歧义（QA PATH-06 的形状）。"""
    src = "import os\nnames = os.listdir()\n"
    root = _project(tmp_path, "s/fig.py", src, {"data/x.csv": "x"})
    ev = db.evidence(root / "s/fig.py", root)
    assert ev["candidates"]["script.parent"]["probes"]["found"] == ["."]
    assert ev["candidates"]["project.root"]["probes"] == {
        "found": [],
        "missing": [],
        "outside": [],
        "unjudged": [],
    }
    assert ev["verdict"] == db.VERDICT_SCRIPT_PARENT
    # 与只在项目根找得到的打开类字面量同在：两个方向 → 歧义，机器不挑
    root2 = _project(tmp_path / "b", "s/fig.py", src + "open('data/x.csv')\n", {"data/x.csv": "x"})
    assert db.evidence(root2 / "s/fig.py", root2)["verdict"] == db.VERDICT_AMBIGUOUS


# ---------------------------------------------------------------- Codex 评 #673：glob 自己走、有界、认 root_dir
def _spy_scandir(monkeypatch) -> list[str]:
    """记下 `databinding` 列过的每一个目录（realpath）。判据的主语：**准备阶段列了谁**，不是结论。"""
    listed: list[str] = []
    real = os.scandir

    def spy(path="."):
        listed.append(os.path.realpath(path))
        return real(path)

    monkeypatch.setattr(db.os, "scandir", spy)
    return listed


@pytest.mark.skipif(os.name == "nt", reason="软链接在 Windows 上要特权")
def test_a_wildcard_matching_a_symlink_out_of_the_project_never_lists_the_outside(
    tmp_path, monkeypatch
):
    """P1：通配段在软链接**之前**（`*/*.dat` 匹配到 `link -> <项目外>`）——stdlib glob 会先把项目外
    的目录列完再吐匹配，事后按 `within` 过滤已经晚了。自己走的遍历在进目录之前就判。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.dat").write_text("k", encoding="utf-8")
    root = _project(tmp_path, "fig.py", "import glob\nglob.glob('*/*.dat')\n", {"inside/.keep": ""})
    os.symlink(outside, root / "link")
    listed = _spy_scandir(monkeypatch)
    ev = db.evidence(root / "fig.py", root)
    assert str(outside.resolve()) not in listed, "项目外的目录被列了"
    assert ev["candidates"]["project.root"]["probes"]["found"] == []
    assert ev["verdict"] == db.VERDICT_NONE
    # 对照：同样的形状、目标在项目里 → 找得到（判据不是恒假）
    (root / "inside" / "x.dat").write_text("1", encoding="utf-8")
    assert db.evidence(root / "fig.py", root)["verdict"] == db.VERDICT_SCRIPT_PARENT


def test_a_recursive_glob_with_no_match_is_bounded_by_entries_seen_not_matches(
    tmp_path, monkeypatch
):
    """P1：预算按**看过的目录条目**计。没有匹配的 `**` 以前会把整棵树走完；现在看满预算就停，
    结论是「判不出」（`unjudged`），不说话。"""
    files = {f"d{i}/f{j}.txt": "" for i in range(20) for j in range(20)}
    root = _project(
        tmp_path, "fig.py", "import glob\nglob.glob('**/*.nomatch', recursive=True)\n", files
    )
    monkeypatch.setattr(db, "MAX_GLOB_SCAN", 50)
    listed = _spy_scandir(monkeypatch)
    ev = db.evidence(root / "fig.py", root)
    probes = ev["candidates"]["project.root"]["probes"]
    assert probes["unjudged"] == ["**/*.nomatch"] and probes["found"] == []
    assert len(listed) <= 50  # 远少于 21 个目录 × 2 个候选
    assert ev["verdict"] == db.VERDICT_NONE
    # 对照：预算够时同一棵树走得完，结论是 missing（不是 unjudged）
    monkeypatch.setattr(db, "MAX_GLOB_SCAN", 10_000)
    assert db.evidence(root / "fig.py", root)["candidates"]["project.root"]["probes"][
        "missing"
    ] == ["**/*.nomatch"]


def test_the_scan_budget_covers_the_whole_evidence_call_not_each_pattern(tmp_path, monkeypatch):
    """P1：预算是整次 `evidence()` 合用的一份——按模式各给一份的话，64 个没有匹配的模式 × 2 个候选
    又能把同一棵大树走几十万条。主语：**准备阶段一共看了多少个目录条目**。"""
    files = {f"d{i}/f{j}.txt": "" for i in range(10) for j in range(10)}
    src = "import glob\n" + "".join(
        f"glob.glob('**/*.nomatch{k}', recursive=True)\n" for k in range(30)
    )
    root = _project(tmp_path, "s/fig.py", src, files)
    seen = [0]
    real = os.scandir

    class _Counting:
        def __init__(self, it):
            self._it = it

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._it.close()

        def __iter__(self):
            for entry in self._it:
                seen[0] += 1
                yield entry

    monkeypatch.setattr(db.os, "scandir", lambda path=".": _Counting(real(path)))
    monkeypatch.setattr(db, "MAX_GLOB_SCAN", 40)
    ev = db.evidence(root / "s/fig.py", root)
    assert len(ev["candidates"]["project.root"]["probes"]["unjudged"]) == 30
    assert seen[0] <= 40 + 1  # 第 41 条就停；按模式各给一份的话是 30 × 2 × 40
    assert ev["verdict"] == db.VERDICT_NONE


@pytest.mark.parametrize(
    "call,nested_found",
    [
        ("glob.glob('**/*.csv')", False),  # 非递归：`**` 等同 `*`，只看一层
        ("glob.glob('**/*.csv', recursive=True)", True),
        ("Path('.').rglob('*.csv')", True),
    ],
)
def test_the_walker_keeps_globs_matching_semantics(tmp_path, call, nested_found):
    """自己走的遍历与 glob 模块的匹配语义一致：`recursive` 开关、隐藏名。"""
    src = f"import glob\nfrom pathlib import Path\n{call}\n"
    root = _project(tmp_path, "fig.py", src, {"a/b/x.csv": "1"})
    got = db.evidence(root / "fig.py", root)["verdict"] == db.VERDICT_SCRIPT_PARENT
    assert got is nested_found


def test_hidden_names_follow_the_glob_module_rules(tmp_path):
    root = _project(tmp_path, "fig.py", "import glob\nglob.glob('*.csv')\n", {".x.csv": "1"})
    assert db.evidence(root / "fig.py", root)["verdict"] == db.VERDICT_NONE
    root2 = _project(
        tmp_path / "b",
        "fig.py",
        "import glob\nglob.glob('*.csv', include_hidden=True)\n",
        {".x.csv": "1"},
    )
    assert db.evidence(root2 / "fig.py", root2)["verdict"] == db.VERDICT_SCRIPT_PARENT


@pytest.mark.parametrize(
    "source,expected",
    [
        # P2：`root_dir=` 换了起算点——常量相对的拼进目标
        ("import glob\nglob.glob('*.csv', root_dir='data')\n", [("glob", "data/*.csv")]),
        (
            "from glob import iglob\niglob(pathname='*.csv', root_dir='data')\n",
            [("glob", "data/*.csv")],
        ),
        # 动态 / 绝对的 root_dir、认不出的关键字、动态开关：说不出话，不判（而不是判另一条路径）
        ("import glob\nglob.glob('*.csv', root_dir=d)\n", []),
        ("import glob\nglob.glob('*.csv', root_dir='/abs')\n", []),
        ("import glob\nglob.glob('*.csv', dir_fd=fd)\n", []),
        ("import glob\nglob.glob('*.csv', recursive=flag)\n", []),
        (
            "import os\nos.listdir(path='runs')\nos.walk(top='1', topdown=False)\n",
            [("dir", "runs"), ("dir", "1")],
        ),
        ("import os\nos.listdir(dir_fd)\nos.scandir(path=p)\n", []),
        (
            "import os\nos.path.exists(path='1/x.dat')\nos.stat('1/y.dat', dir_fd=fd)\n",
            [("path", "1/x.dat")],
        ),
        ("from pathlib import Path\nPath('d').glob('*.csv', case_sensitive=False)\n", []),
    ],
)
def test_arguments_that_move_the_lookup_are_honoured_or_declined(source, expected):
    got = [(p["kind"], p["target"]) for p in db.probe_literals(source)]
    assert got == expected


def test_glob_root_dir_evidence_decides_like_the_real_lookup(tmp_path):
    """P2 的失败场景原样：只有 `data/x.csv`，脚本 `glob('*.csv', root_dir='data')`——以前查的是
    `<候选>/x.csv`，查不到、不问、沙盒里失败。"""
    src = "import glob\nglob.glob('*.csv', root_dir='data')\n"
    root = _project(tmp_path, "s/fig.py", src, {"s/data/x.csv": "1"})
    assert db.evidence(root / "s/fig.py", root)["verdict"] == db.VERDICT_SCRIPT_PARENT


@pytest.mark.skipif(os.name == "nt", reason="软链接在 Windows 上要特权")
def test_a_symlink_cycle_inside_the_project_does_not_spin_the_recursive_walk(tmp_path):
    root = _project(
        tmp_path,
        "fig.py",
        "import glob\nglob.glob('**/*.nomatch', recursive=True)\n",
        {"a/x.txt": ""},
    )
    os.symlink(root, root / "a" / "loop")  # 项目里一条指回项目根的软链接
    ev = db.evidence(root / "fig.py", root)
    assert ev["candidates"]["project.root"]["probes"]["missing"] == ["**/*.nomatch"]


# ---------------------------------------------------------------- Codex 评 #673 P2：大目录边列边匹配、glob 的别名
def _ordered_scandir(monkeypatch, *, hit_first: bool) -> list[int]:
    """把 `databinding` 看到的目录条目排成确定的顺序（命中的 `hit.csv` 排最前或最后——真实
    scandir 的顺序由文件系统定），并记下它**实际取走**了多少条。"""
    taken = [0]
    real = os.scandir

    class _Ordered:
        def __init__(self, path):
            with real(path) as it:
                self._entries = sorted(it, key=lambda e: (e.name == "hit.csv") != hit_first)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def __iter__(self):
            for entry in self._entries:
                taken[0] += 1
                yield entry

    monkeypatch.setattr(db.os, "scandir", lambda path=".": _Ordered(path))
    return taken


def _big_data_dir(tmp_path: Path, *, hit: bool) -> Path:
    """科研项目常见的形状：`data/` 下几千个文件，脚本 `glob('data/*.csv')` 找其中的一个。"""
    root = _project(tmp_path, "s/fig.py", "import glob\nglob.glob('data/*.csv')\n", {})
    data = root / "s" / "data"
    data.mkdir()
    for i in range(db.MAX_GLOB_SCAN + 100):
        (data / f"f{i:05d}.txt").touch()
    if hit:
        (data / "hit.csv").touch()
    return root


def test_a_hit_early_in_a_directory_larger_than_the_budget_is_found(tmp_path, monkeypatch):
    """P2：起始目录条目数超过 `MAX_GLOB_SCAN` 时，以前先把整个目录列完才匹配——第一条就命中也
    抛预算用完 → `unjudged` → 首开不问、脚本在空沙盒里跑。现在边列边匹配，命中即停。"""
    root = _big_data_dir(tmp_path, hit=True)
    taken = _ordered_scandir(monkeypatch, hit_first=True)
    ev = db.evidence(root / "s/fig.py", root)
    assert ev["candidates"]["script.parent"]["probes"]["found"] == ["data/*.csv"]
    assert ev["verdict"] == db.VERDICT_SCRIPT_PARENT
    assert taken[0] <= 1  # 命中之后一条都不再取


def test_a_hit_beyond_the_budget_is_still_unjudged_not_missing(tmp_path, monkeypatch):
    """预算只约束「还没命中」的遍历量：命中排在第 5101 条时看满预算就停，结论仍是「判不出」
    ——不是错误地判「没有」。"""
    root = _big_data_dir(tmp_path, hit=True)
    taken = _ordered_scandir(monkeypatch, hit_first=False)
    ev = db.evidence(root / "s/fig.py", root)
    probes = ev["candidates"]["script.parent"]["probes"]
    assert probes["unjudged"] == ["data/*.csv"] and probes["missing"] == []
    assert taken[0] <= db.MAX_GLOB_SCAN
    assert ev["verdict"] == db.VERDICT_NONE


def test_no_match_in_a_directory_larger_than_the_budget_is_unjudged(tmp_path, monkeypatch):
    root = _big_data_dir(tmp_path, hit=False)
    taken = _ordered_scandir(monkeypatch, hit_first=True)
    ev = db.evidence(root / "s/fig.py", root)
    probes = ev["candidates"]["script.parent"]["probes"]
    assert probes["unjudged"] == ["data/*.csv"] and probes["missing"] == []
    assert taken[0] <= db.MAX_GLOB_SCAN
    # 对照：同一个目录、预算够时列得完，结论是 missing（不是 unjudged）
    monkeypatch.setattr(db, "MAX_GLOB_SCAN", 10 * db.MAX_GLOB_SCAN)
    probes = db.evidence(root / "s/fig.py", root)["candidates"]["script.parent"]["probes"]
    assert probes["missing"] == ["data/*.csv"]


@pytest.mark.parametrize(
    "source,expected",
    [
        ("import glob as g\ng.glob('run-*.traj')\n", [("glob", "run-*.traj")]),
        ("from glob import glob as gg\ngg('run-*.traj')\n", [("glob", "run-*.traj")]),
        ("from glob import iglob\niglob('run-*.traj')\n", [("glob", "run-*.traj")]),
        (
            "from pathlib import Path as P\nP('d').rglob('*.x')\nP.cwd().glob('*.n')\n",
            [("glob", "d/**/*.x"), ("glob", "*.n")],
        ),
        # 别名只加名字：不是 glob 模块的 `g` 上的 `.glob()` 仍不算
        ("import fnmatch as g\ng.glob('run-*.traj')\n", []),
    ],
)
def test_aliased_glob_and_path_imports_are_recognised(source, expected):
    """P2：以前写死 `glob.glob` / 裸 `glob` / `Path` 这几个名字，`import glob as g` 这类合法写法
    认不出 → 没有探路证据 → 首开不问。"""
    got = [(p["kind"], p["target"]) for p in db.probe_literals(source)]
    assert got == expected


def test_an_aliased_glob_decides_the_first_open_like_the_plain_one(tmp_path):
    src = "import glob as g\nfiles = sorted(g.glob('run-*-*[Ll]ongrun.traj'))\n"
    root = _project(tmp_path, "长时间 数据/analysis.py", src, TRAJ)
    assert (
        db.evidence(root / "长时间 数据/analysis.py", root)["verdict"] == db.VERDICT_SCRIPT_PARENT
    )


# ---------------------------------------------------------------- Codex 评 #699 P2：别名被重新绑定时取并集语义
#: Codex 的反例原样：模块级 `import glob as g`，函数参数 `g` 其实是个 `Path`——`g.glob` 是
#: `Path.glob`（含隐藏名）。按模块 glob（不含隐藏名）判的话只有 `.x.csv` 时是 none，不问。
CODEX_SHADOW = (
    "import glob as g\nfrom pathlib import Path\n"
    "def find(g):\n    return g.glob('*.csv')\n"
    "find(Path('.'))\n"
)


@pytest.mark.parametrize(
    "src",
    [
        CODEX_SHADOW,
        # 赋值遮蔽
        "import glob as g\nfrom pathlib import Path\ng = Path('.')\ng.glob('*.csv')\n",
        # for 目标遮蔽
        "import glob as g\nfrom pathlib import Path\nfor g in [Path('.')]:\n    g.glob('*.csv')\n",
    ],
)
def test_a_rebound_glob_alias_still_asks_when_only_hidden_files_match(tmp_path, src):
    root = _project(tmp_path, "s/fig.py", src, {"s/.x.csv": "1"})
    ev = db.evidence(root / "s/fig.py", root)
    assert ev["candidates"]["script.parent"]["probes"]["found"] == ["*.csv"]
    assert ev["verdict"] == db.VERDICT_SCRIPT_PARENT


def test_an_unshadowed_alias_keeps_the_glob_module_semantics(tmp_path):
    """对照：没被重新绑定的别名照旧按模块 glob 判——隐藏名不算，普通名算。"""
    src = "import glob as g\ng.glob('*.csv')\n"
    hidden = _project(tmp_path / "a", "s/fig.py", src, {"s/.x.csv": "1"})
    assert db.evidence(hidden / "s/fig.py", hidden)["verdict"] == db.VERDICT_NONE
    plain = _project(tmp_path / "b", "s/fig.py", src, {"s/x.csv": "1"})
    assert db.evidence(plain / "s/fig.py", plain)["verdict"] == db.VERDICT_SCRIPT_PARENT


@pytest.mark.parametrize(
    "binding",
    [
        "def f(g): pass",
        "def f(*g): pass",
        "def f(**g): pass",
        "def f(*, g): pass",
        "lambda g: 0",
        "g = 1",
        "g += 1",
        "g: int = 1",
        "for g in []: pass",
        "with open('x') as g: pass",
        "try:\n    pass\nexcept Exception as g:\n    pass",
        "[0 for g in []]",
        "(g := 1)",
        "del g",
        "def f():\n    global g",
        "def f():\n    def h():\n        nonlocal g",
        "def g(): pass",
        "class g: pass",
        "match 1:\n    case g:\n        pass",
        "match []:\n    case [*g]:\n        pass",
        "match {}:\n    case {**g}:\n        pass",
        "import fnmatch as g",
        "from os import path as g",
        "from pylab import *",
    ],
)
def test_every_binding_form_marks_the_alias_as_rebound(binding):
    src = f"import glob as g\n{binding}\ng.glob('*.csv')\n"
    (probe,) = db.probe_literals(src)
    assert (probe["recursive"], probe["hidden"]) == (True, True)
