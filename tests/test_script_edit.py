"""经确认改写脚本里的数据路径（ADR 0110）。

七层：挑哪些常量（`scriptedit.plan` 的候选判据与逐条原因）；字节矩阵（编码 / 换行 / BOM：块外一个
字节不动、撤销逐字节回到原样）；推规则扩到文件夹与 glob（`inputremap.derive_location`）；C++ 读取器的
归因（`figcapture.enoent_fact` 三种实测形态 → `inputremap.native_miss`）；共享底座（`scriptbackup`：
两处备份、原子替换、拒绝、保留、历史）；HTTP（预览 → 提交 → 复原，令牌单次且绑定会话）；真 worker
（探路 / C++ 读取器缺数据 → 弹窗载荷 → 改写 → 重跑出图，值等于那份数据的真值）。末尾是结构门禁：
Agent 那一侧的代码里不许出现提交 / 复原端点。
"""

from __future__ import annotations

import ast
import errno
import os
import stat
from pathlib import Path

import pytest

from tavotto.engine import (
    figcapture,
    inputremap,
    pool as engine_pool,
    scriptbackup,
    scriptedit,
    workdir,
)

ROOT = Path(__file__).resolve().parent.parent

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

P = figcapture.REMAP_PREFIX
OLD = "/nonexistent-tavotto/a/proj"


def _touch(path: Path, text: str = "1\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _plan(tmp_path: Path, source: bytes | str, *, rule: dict, missing: list[str]):
    data = source.encode("utf-8") if isinstance(source, str) else source
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    return scriptedit.plan(data, rule=rule, missing=missing, script_dir=proj, root=proj)


@pytest.fixture
def moved(tmp_path):
    """数据的新位置：`<tmp>/new/data/{x.h5, runs/a.csv}`；规则 `OLD` → `<tmp>/new`。"""
    new = tmp_path / "new"
    _touch(new / "data" / "x.h5")
    _touch(new / "data" / "runs" / "a.csv")
    return new, {"kind": P, "from": OLD, "to": str(new)}


# --------------------------------------------------------------- 挑哪些常量


def test_equal_and_prefix_constants_are_rewritten_to_absolute_forward_slash_paths(tmp_path, moved):
    new, rule = moved
    src = (
        "import os, glob\n"
        f'DATA = "{OLD}/data"\n'
        f'if not os.path.exists("{OLD}/data/x.h5"):\n    raise SystemExit(1)\n'
        f'files = glob.glob(r"{OLD}/data/runs/*.csv")\n'
    )
    plan = _plan(tmp_path, src, rule=rule, missing=[f"{OLD}/data/x.h5", f"{OLD}/data/runs/*.csv"])
    got = {e["line"]: e["value_after"] for e in plan.edits}
    base = str(new).replace("\\", "/")
    assert got == {
        2: f"{base}/data",
        3: f"{base}/data/x.h5",
        5: f"{base}/data/runs/*.csv",
    }
    assert plan.edits[2]["after"].startswith('r"')  # 前缀与引号原样
    assert plan.skipped == []


def test_prefix_is_by_path_segment_not_by_characters(tmp_path, moved):
    _new, rule = moved
    src = f'X = "{OLD[:-1]}"\n'  # `/…/pro` 不是 `/…/proj/…` 的前缀
    with pytest.raises(scriptbackup.ScriptEditError) as err:
        _plan(tmp_path, src, rule=rule, missing=[f"{OLD}/data/x.h5"])
    assert err.value.code == scriptedit.ERROR_NOTHING_TO_CHANGE
    assert err.value.params["skipped"] == []  # 根本不是候选，连「没改」都不报


def test_paths_that_exist_are_never_touched(tmp_path, moved):
    new, _rule = moved
    here = tmp_path / "here"
    _touch(here / "data" / "y.csv")
    rule = {"kind": P, "from": str(here), "to": str(new)}
    src = f'D = "{here}"\nF = "{here}/data/x.h5"\n'  # 目录在，缺的是更深那一段
    plan = _plan(tmp_path, src, rule=rule, missing=[f"{here}/data/x.h5"])
    assert [e["line"] for e in plan.edits] == [2]


def test_non_path_positions_are_reported_and_left_alone(tmp_path, moved):
    _new, rule = moved
    src = (
        f'd = {{"{OLD}/data": 1}}\n'
        f'v = d["{OLD}/data"]\n'
        f'ok = v == "{OLD}/data"\n'
        f'fig.savefig("{OLD}/data")\n'
        f'"""{OLD}/data"""\n'
        f'print(f"{OLD}/data/{{1}}")\n'
        f'y = ("{OLD}/da" "ta")\n'
        f'z = "{OLD}/data"\n'
    )
    plan = _plan(tmp_path, src, rule=rule, missing=[f"{OLD}/data/x.h5"])
    assert [e["line"] for e in plan.edits] == [8]
    reasons = {s["line"]: s["reason"] for s in plan.skipped}
    assert reasons == {
        1: scriptedit.SKIP_CONTEXT,
        2: scriptedit.SKIP_CONTEXT,
        3: scriptedit.SKIP_CONTEXT,
        4: scriptedit.SKIP_CONTEXT,
        5: scriptedit.SKIP_CONTEXT,
        6: scriptedit.SKIP_FSTRING,
        7: scriptedit.SKIP_CONCATENATED,
    }


def test_nothing_to_change_says_why_line_by_line(tmp_path, moved):
    _new, rule = moved
    src = f'import h5py\nh5py.File(f"{OLD}/data/{{name}}.h5")\n'
    with pytest.raises(scriptbackup.ScriptEditError) as err:
        _plan(tmp_path, src, rule=rule, missing=[f"{OLD}/data/x.h5"])
    assert err.value.code == scriptedit.ERROR_NOTHING_TO_CHANGE
    assert err.value.params["skipped"] == [
        {"line": 2, "value": f"{OLD}/data/", "reason": scriptedit.SKIP_FSTRING}
    ]


def test_quotes_are_escaped_and_raw_strings_that_cannot_hold_them_are_refused(tmp_path):
    new = tmp_path / "it's"
    _touch(new / "x.csv")
    rule = {"kind": P, "from": OLD, "to": str(new)}
    src = f"a = '{OLD}/x.csv'\nb = r'{OLD}/x.csv'\n"
    plan = _plan(tmp_path, src, rule=rule, missing=[f"{OLD}/x.csv"])
    assert [e["line"] for e in plan.edits] == [1]
    ns: dict = {}
    exec(compile(plan.new_bytes, "fig.py", "exec"), ns)  # noqa: S102 —— 只含两行赋值
    assert ns["a"] == str(new).replace("\\", "/") + "/x.csv"
    assert plan.skipped == [
        {"line": 2, "value": f"{OLD}/x.csv", "reason": scriptedit.SKIP_UNREPRESENTABLE}
    ]


def test_a_renamed_file_rule_does_not_rewrite_the_directory_constant(tmp_path):
    chosen = _touch(tmp_path / "new" / "renamed.h5")
    rule = inputremap.derive_location(f"{OLD}/data/x.h5", str(chosen), chosen_is_dir=False)
    assert rule["kind"] == figcapture.REMAP_FILE
    src = f'D = "{OLD}/data"\nF = "{OLD}/data/x.h5"\n'
    plan = _plan(tmp_path, src, rule=rule, missing=[f"{OLD}/data/x.h5"])
    assert [e["line"] for e in plan.edits] == [2]
    assert plan.skipped == [
        {"line": 1, "value": f"{OLD}/data", "reason": scriptedit.SKIP_RULE_MISMATCH}
    ]


def test_windows_targets_are_written_with_forward_slashes(tmp_path, monkeypatch):
    monkeypatch.setattr(inputremap, "location_exists", lambda p: p.startswith("C:"))
    rule = {"kind": P, "from": OLD, "to": "C:\\Data\\new"}
    plan = _plan(tmp_path, f'F = "{OLD}/x.csv"\n', rule=rule, missing=[f"{OLD}/x.csv"])
    assert plan.edits[0]["value_after"] == "C:/Data/new/x.csv"
    assert plan.edits[0]["after"] == '"C:/Data/new/x.csv"'


def test_rewriting_a_relative_constant_uses_the_relative_root_rule(tmp_path):
    new = tmp_path / "moved"
    _touch(new / "data" / "values.txt")
    rule = inputremap.derive_location(
        "data/values.txt", str(new / "data" / "values.txt"), chosen_is_dir=False
    )
    assert rule == {"kind": P, "from": "", "to": str(new)}
    src = 'import os\nif os.path.exists("data/values.txt"):\n    pass\nk = {"data": 1}\n'
    plan = _plan(tmp_path, src, rule=rule, missing=["data/values.txt"])
    assert [(e["line"], e["value_after"]) for e in plan.edits] == [
        (2, str(new).replace("\\", "/") + "/data/values.txt")
    ]
    assert plan.skipped == []  # `"data"` 不是缺失路径的前缀——字典键、段名一律无关


# --------------------------------------------------------------- 字节矩阵

_MATRIX = {
    "utf8-lf": ("# 中文注释\nF = '{p}'\n", "utf-8", b""),
    "crlf": ("# c\r\nF = '{p}'\r\nG = 1\r\n", "utf-8", b""),
    "bom-crlf": ("# c\r\nF = '{p}'\r\n", "utf-8", b"\xef\xbb\xbf"),
    "gbk": ("# -*- coding: gbk -*-\n# 数据在这里\nF = '{p}'  # 路径\n", "gbk", b""),
    "latin1": ("# -*- coding: latin-1 -*-\n# caf\xe9\nF = '{p}'\n", "latin-1", b""),
    "mixed": ("# a\r\n# b\nF = '{p}'\r\nG = 2\n", "utf-8", b""),
    "lone-cr": ("# a\rF = '{p}'\rG = 3\r", "utf-8", b""),
    "no-final-newline": ("F = '{p}'", "utf-8", b""),
    "tabs-and-unicode-before": ("if 1:\n\tα = 'é'; F = '{p}'\n", "utf-8", b""),
}


@pytest.mark.parametrize("name", sorted(_MATRIX))
def test_bytes_outside_the_edit_never_change_and_undo_restores_them(tmp_path, moved, name):
    new, rule = moved
    text, enc, bom = _MATRIX[name]
    data = bom + text.format(p=f"{OLD}/data/x.h5").encode(enc)
    plan = _plan(tmp_path, data, rule=rule, missing=[f"{OLD}/data/x.h5"])
    assert len(plan.edits) == 1
    old_lit = f"'{OLD}/data/x.h5'".encode(enc)
    new_lit = ("'" + str(new).replace("\\", "/") + "/data/x.h5'").encode(enc)
    assert plan.new_bytes == data.replace(old_lit, new_lit)
    assert scriptedit.undo(plan.new_bytes, plan.edits) == data


def test_an_encoding_that_cannot_hold_the_new_path_is_refused(tmp_path):
    new = tmp_path / "数据"
    _touch(new / "x.csv")
    rule = {"kind": P, "from": OLD, "to": str(new)}
    data = f"# -*- coding: latin-1 -*-\nF = '{OLD}/x.csv'\n".encode("latin-1")
    with pytest.raises(scriptbackup.ScriptEditError) as err:
        _plan(tmp_path, data, rule=rule, missing=[f"{OLD}/x.csv"])
    assert err.value.params["skipped"][0]["reason"] == scriptedit.SKIP_ENCODING


def test_self_check_refuses_any_change_beyond_the_listed_constants(tmp_path, moved):
    _new, rule = moved
    src = f'# note\nG = "keep"\nF = "{OLD}/data/x.h5"\nH = 1\n'.encode()
    plan = _plan(tmp_path, src, rule=rule, missing=[f"{OLD}/data/x.h5"])
    scriptedit.self_check(src, plan.new_bytes, plan.edits)
    # 常量值变了：AST 那一判
    with pytest.raises(scriptbackup.ScriptEditError) as err:
        scriptedit.self_check(src, plan.new_bytes.replace(b'"keep"', b'"kept"'), plan.edits)
    assert err.value.code == scriptedit.ERROR_SELF_CHECK
    # 值没变、只多了一个空格（AST 看不出）：替换之前 / 之后各一处，字节比对都要抓到
    for tampered in (
        plan.new_bytes.replace(b"# note", b"# nope"),  # 等长、AST 看不出：只有逐段比对抓得到
        plan.new_bytes.replace(b'G = "keep"', b'G  = "keep"'),
        plan.new_bytes.replace(b"H = 1", b"H =  1"),
    ):
        with pytest.raises(scriptbackup.ScriptEditError) as err:
            scriptedit.self_check(src, tampered, plan.edits)
        assert err.value.code == scriptedit.ERROR_SELF_CHECK


def test_undo_keeps_later_edits_and_refuses_when_the_path_itself_was_changed(tmp_path, moved):
    _new, rule = moved
    src = f'import os\nF = "{OLD}/data/x.h5"\nG = 1\n'
    plan = _plan(tmp_path, src, rule=rule, missing=[f"{OLD}/data/x.h5"])
    later = b"# my note\n" + plan.new_bytes.replace(b"G = 1", b"G = 2")
    back = scriptedit.undo(later, plan.edits)
    assert back == b"# my note\n" + src.encode().replace(b"G = 1", b"G = 2")
    retouched = plan.new_bytes.replace(plan.edits[0]["after"].encode(), b'"/elsewhere/x.h5"')
    with pytest.raises(scriptbackup.ScriptEditError) as err:
        scriptedit.undo(retouched, plan.edits)
    assert err.value.code == scriptedit.ERROR_RESTORE_CONFLICT


def test_undo_finds_two_rewrites_on_one_line_after_the_first_changed_length(tmp_path, moved):
    """同一行改了两处、前一处换了长度：后一处记的是改写前的列，要按前面的长度差挪过去；两处改成同一串时
    全文回退会撞上两处匹配——不挪的话，Tavotto 自己改出来的脚本「只撤销这几处」反倒复原不了（Codex 评 #730 P2）。"""
    _new, rule = moved
    src = f'import os\nF, G = "{OLD}/data/x.h5", "{OLD}/data/x.h5"\nH = 1\n'
    plan = _plan(tmp_path, src, rule=rule, missing=[f"{OLD}/data/x.h5"])
    assert len(plan.edits) == 2 and len({e["after"] for e in plan.edits}) == 1
    assert len(plan.edits[0]["after"]) != len(plan.edits[0]["before"])
    later = plan.new_bytes.replace(b"H = 1", b"H = 2")
    assert scriptedit.undo(later, plan.edits) == src.encode().replace(b"H = 1", b"H = 2")


# --------------------------------------------------------------- 推规则：文件夹与 glob


def test_folder_entries_derive_from_the_parent_or_a_renamed_folder(tmp_path):
    (tmp_path / "p" / "runs").mkdir(parents=True)
    rule = inputremap.derive_location("data/runs", str(tmp_path / "p"), chosen_is_dir=True)
    assert rule == {"kind": P, "from": "data", "to": str(tmp_path / "p")}
    renamed = tmp_path / "runs-2026"
    renamed.mkdir()
    rule = inputremap.derive_location("data/runs", str(renamed), chosen_is_dir=True)
    assert rule == {"kind": P, "from": "data/runs", "to": str(renamed)}
    assert figcapture.remap_target([rule], "data/runs", whole=True) == str(renamed)
    assert figcapture.remap_target([rule], "data/runs") is None  # worker 改道从不用 whole


def test_dotted_folder_entries_are_folders_when_the_disk_says_so(tmp_path):
    """`listdir("runs.v1")`：名字带点也是文件夹——指认搬走的那个文件夹本身、或它的上级都要推得出
    （Codex 评 #730 P2）；拿一个改了名的文件夹顶替像文件名的条目仍然拒。"""
    moved = tmp_path / "new" / "runs.v1"
    moved.mkdir(parents=True)
    rule = inputremap.derive_location(f"{OLD}/runs.v1", str(moved), chosen_is_dir=True)
    assert rule == {"kind": P, "from": f"{OLD}/runs.v1", "to": str(moved)}
    assert figcapture.remap_target([rule], f"{OLD}/runs.v1", whole=True) == str(moved)
    rule = inputremap.derive_location(f"{OLD}/runs.v1", str(moved.parent), chosen_is_dir=True)
    assert rule == {"kind": P, "from": OLD, "to": str(moved.parent)}
    renamed = tmp_path / "runs-2026"
    renamed.mkdir()
    with pytest.raises(inputremap.RemapError) as err:
        inputremap.derive_location(f"{OLD}/runs.v1", str(renamed), chosen_is_dir=True)
    assert err.value.code == inputremap.ERROR_NOT_FOUND_IN_DIR


def test_a_file_entry_is_not_satisfied_by_a_folder_without_it(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(inputremap.RemapError) as err:
        inputremap.derive_location("data/x.csv", str(tmp_path / "empty"), chosen_is_dir=True)
    assert err.value.code == inputremap.ERROR_NOT_FOUND_IN_DIR


def test_glob_entries_need_at_least_one_match(tmp_path):
    runs = tmp_path / "new" / "data" / "runs"
    _touch(runs / "a.csv")
    rule = inputremap.derive_location(f"{OLD}/data/runs/*.csv", str(runs), chosen_is_dir=True)
    assert (
        figcapture.remap_target([rule], f"{OLD}/data/runs/*.csv")
        == str(runs).replace("\\", "/") + "/*.csv"
    )
    # 指认的是其中一个文件：取它所在的文件夹
    rule2 = inputremap.derive_location(
        f"{OLD}/data/runs/*.csv", str(runs / "a.csv"), chosen_is_dir=False
    )
    assert rule2 == rule
    with pytest.raises(inputremap.RemapError) as err:
        inputremap.derive_location(f"{OLD}/data/runs/*.txt", str(runs), chosen_is_dir=True)
    assert err.value.code == inputremap.ERROR_NOT_FOUND_IN_DIR


# --------------------------------------------------------------- C++ 读取器的归因


def _raise_and_catch(exc: BaseException) -> BaseException:
    try:
        raise exc
    except BaseException as caught:  # noqa: BLE001
        return caught


def test_enoent_fact_reads_the_three_measured_shapes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # h5py：filename 为 None，路径只在消息里
    h5 = FileNotFoundError(
        errno.ENOENT,
        "Unable to synchronously open file (unable to open file: name = 'run/x.h5', errno = 2)",
    )
    assert figcapture.enoent_fact(h5) == {"filename": None, "named": "run/x.h5", "cwd": os.getcwd()}
    # netCDF4：filename 就是脚本写的那串
    nc = FileNotFoundError(errno.ENOENT, "No such file or directory", "run/x.nc")
    assert figcapture.enoent_fact(nc)["filename"] == "run/x.nc"
    # xarray：filename 是 cwd 拼出来的绝对路径，外面还套着一层 KeyError 作 __context__
    try:
        try:
            raise KeyError("cache")
        except KeyError:
            raise FileNotFoundError(
                errno.ENOENT, "No such file or directory", str(tmp_path / "run" / "x.nc")
            ) from None
    except FileNotFoundError as xa:
        fact = figcapture.enoent_fact(xa)
    assert fact["filename"] == str(tmp_path / "run" / "x.nc")
    assert figcapture.enoent_fact(ValueError("nope")) is None


def test_native_miss_matches_exactly_one_literal_or_says_nothing(tmp_path):
    (tmp_path / "fig.py").write_text(
        "import h5py, os\n"
        f'DATA = "{OLD}/data"\n'
        'h5py.File("run/x.h5")\n'
        f'h5py.File(os.path.join(DATA, "y.h5"))\n',
        encoding="utf-8",
    )
    cwd = str(tmp_path)
    exact = inputremap.native_miss("fig.py", tmp_path, {"named": "run/x.h5", "cwd": cwd})
    assert exact["requested"] == "run/x.h5" and exact["literal"] == "run/x.h5"
    # xarray：绝对路径在 cwd 之内 → 换回脚本写法
    xa = inputremap.native_miss(
        "fig.py", tmp_path, {"filename": str(tmp_path / "run" / "x.h5"), "cwd": cwd}
    )
    assert xa["requested"] == "run/x.h5"
    # 目录常量拼出来的：按路径段前缀对上 DATA
    joined = inputremap.native_miss(
        "fig.py", tmp_path, {"filename": f"{OLD}/data/y.h5", "cwd": cwd}
    )
    assert joined["requested"] == f"{OLD}/data/y.h5" and joined["literal"] == f"{OLD}/data"
    assert joined["absolute"] is True
    # 对不上任何一条：不判
    assert (
        inputremap.native_miss("fig.py", tmp_path, {"filename": "/tmp/zz.h5", "cwd": cwd}) is None
    )
    assert inputremap.native_miss("fig.py", tmp_path, {"filename": None, "named": None}) is None


def test_missing_input_of_prefers_the_path_named_in_the_message(tmp_path):
    misses = figcapture.InputMisses()
    misses.note("local.cfg")  # 一次被 try 吞掉的可选读
    h5 = _raise_and_catch(
        FileNotFoundError(errno.ENOENT, "unable to open file: name = 'run/x.h5', errno = 2")
    )
    # 消息里说的是 run/x.h5，它不是一次落空的只读打开 → 不认领（交给 native 归因）
    assert figcapture.missing_input_of(h5, misses) is None
    misses.note("run/x.h5")
    assert figcapture.missing_input_of(h5, misses)["requested"] == "run/x.h5"
    # 消息里没有引号（numpy 的 "d.txt not found."）→ 仍用最近一次落空
    np_like = _raise_and_catch(FileNotFoundError("d.txt not found."))
    assert figcapture.missing_input_of(np_like, misses)["requested"] == "run/x.h5"


# --------------------------------------------------------------- 共享底座


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    return scriptbackup.Store(
        root=root, project_dir=root / "tavottofile" / "script-backups", mirror_dir=tmp_path / "m"
    )


def test_replace_backs_up_twice_then_swaps_and_keeps_the_mode(store):
    script = store.root / "fig.py"
    script.write_bytes(b"A = 1\n")
    os.chmod(script, 0o755)
    before = scriptbackup.sha256(b"A = 1\n")
    rec = scriptbackup.replace(store, "fig.py", b"A = 2\n", kind="t", expect_before=before)
    assert script.read_bytes() == b"A = 2\n"
    assert stat.S_IMODE(script.stat().st_mode) == 0o755
    for base in (store.project_dir, store.mirror_dir):
        d = base / rec["id"]
        assert (d / "original.py").read_bytes() == b"A = 1\n"
    assert rec["pristine"] is True
    rec2 = scriptbackup.replace(
        store, "fig.py", b"A = 3\n", kind="t", expect_before=scriptbackup.sha256(b"A = 2\n")
    )
    assert rec2["pristine"] is False
    states = [h["state"] for h in scriptbackup.history(store, "fig.py")]
    assert states == [scriptbackup.STATE_CURRENT, scriptbackup.STATE_CHANGED]
    meta, data = scriptbackup.load(store, rec["id"])
    assert data == b"A = 1\n" and meta["id"] == rec["id"]


def test_a_failed_backup_leaves_the_script_and_the_backup_dirs_untouched(store, tmp_path):
    script = store.root / "fig.py"
    script.write_bytes(b"A = 1\n")
    blocked = scriptbackup.Store(
        root=store.root, project_dir=store.project_dir, mirror_dir=tmp_path / "file-not-dir"
    )
    (tmp_path / "file-not-dir").write_text("x")
    with pytest.raises(scriptbackup.ScriptEditError) as err:
        scriptbackup.replace(
            blocked, "fig.py", b"B\n", kind="t", expect_before=scriptbackup.sha256(b"A = 1\n")
        )
    assert err.value.code == scriptbackup.ERROR_BACKUP_FAILED
    assert script.read_bytes() == b"A = 1\n"
    assert not any((store.project_dir / scriptbackup.slug_of("fig.py")).glob("*"))
    assert [p.name for p in store.root.iterdir()] != [] and not any(
        p.name.endswith(".tmp") for p in store.root.iterdir()
    )


def test_replace_refuses_when_the_script_changed_since_the_preview(store):
    (store.root / "fig.py").write_bytes(b"A = 1\n")
    with pytest.raises(scriptbackup.ScriptEditError) as err:
        scriptbackup.replace(store, "fig.py", b"B\n", kind="t", expect_before="0" * 64)
    assert err.value.code == scriptbackup.ERROR_SCRIPT_CHANGED
    assert (store.root / "fig.py").read_bytes() == b"A = 1\n"


@pytest.mark.skipif(os.name == "nt", reason="符号链接 / 权限位")
def test_symlinks_hardlinks_and_readonly_dirs_are_refused(store, tmp_path):
    real = _touch(tmp_path / "outside" / "real.py", "A = 1\n")
    os.symlink(real, store.root / "link.py")
    (store.root / "sub").mkdir()
    os.symlink(tmp_path / "outside", store.root / "sub" / "via")
    os.link(real, store.root / "hard.py")
    ro = store.root / "ro"
    _touch(ro / "fig.py", "A = 1\n")
    os.chmod(ro, 0o555)
    try:
        cases = {
            "link.py": scriptbackup.ERROR_SCRIPT_NOT_FOUND,  # 指到项目外：contained_path 就拒了
            "hard.py": scriptbackup.ERROR_SCRIPT_HARDLINKED,
            "ro/fig.py": scriptbackup.ERROR_SCRIPT_READONLY,
            "../outside/real.py": scriptbackup.ERROR_SCRIPT_NOT_FOUND,
        }
        for script, code in cases.items():
            with pytest.raises(scriptbackup.ScriptEditError) as err:
                scriptbackup.resolve(store.root, script)
            assert err.value.code == code, script
        inside = _touch(store.root / "data" / "in.py", "A = 1\n")
        os.symlink(inside, store.root / "alias.py")
        with pytest.raises(scriptbackup.ScriptEditError) as err:
            scriptbackup.resolve(store.root, "alias.py")
        assert err.value.code == scriptbackup.ERROR_SCRIPT_IS_SYMLINK
    finally:
        os.chmod(ro, 0o755)


def test_pristine_is_kept_forever_and_only_recent_ones_are_pruned(store, monkeypatch):
    monkeypatch.setattr(scriptbackup, "KEEP_RECENT", 2)
    script = store.root / "fig.py"
    script.write_bytes(b"0\n")
    ids = []
    for n in range(1, 6):
        prev = f"{n - 1}\n".encode()
        rec = scriptbackup.replace(
            store, "fig.py", f"{n}\n".encode(), kind="t", expect_before=scriptbackup.sha256(prev)
        )
        ids.append(rec["id"])
    left = [h["id"] for h in scriptbackup.history(store, "fig.py")]
    assert left == [ids[4], ids[3], ids[0]]
    assert scriptbackup.load(store, ids[0])[1] == b"0\n"


def test_scripts_whose_readable_slugs_collide_keep_separate_backups(store):
    """`a/b.py` 与 `a__b.py` 的可读前半相同：混进一个目录的话，第二份的第一次备份不是 pristine、
    还会被共用的上限裁掉（Codex 评 #730 P2）。"""
    _touch(store.root / "a" / "b.py", "A = 1\n")
    _touch(store.root / "a__b.py", "A = 1\n")
    assert scriptbackup.slug_of("a/b.py") != scriptbackup.slug_of("a__b.py")
    before = scriptbackup.sha256(b"A = 1\n")
    first = scriptbackup.replace(store, "a/b.py", b"A = 2\n", kind="t", expect_before=before)
    second = scriptbackup.replace(store, "a__b.py", b"A = 2\n", kind="t", expect_before=before)
    assert first["pristine"] is True and second["pristine"] is True
    assert [h["id"] for h in scriptbackup.history(store, "a/b.py")] == [first["id"]]
    assert [h["id"] for h in scriptbackup.history(store, "a__b.py")] == [second["id"]]


def test_long_or_multibyte_script_paths_keep_the_slug_under_the_name_limit(store):
    """可读前半按 UTF-8 字节截、哈希后缀原样保留（Codex 评 #730 P2）：深层路径 / 多字节字符的备份
    目录名不超过文件系统的单段上限，截完仍然互不相同。"""
    deep = "/".join(["很长的一层目录名称"] * 12) + "/fig.py"
    other = "/".join(["很长的一层目录名称"] * 12) + "/fig2.py"
    for script in (deep, other, "a" * 300 + ".py"):
        slug = scriptbackup.slug_of(script)
        assert len(slug.encode("utf-8")) <= scriptbackup.SLUG_READABLE_MAX_BYTES + 13
        assert slug.endswith("-" + scriptbackup.sha256(script.encode("utf-8"))[:12])
    assert scriptbackup.slug_of(deep) != scriptbackup.slug_of(other)
    _touch(store.root / deep, "A = 1\n")
    rec = scriptbackup.replace(
        store, deep, b"A = 2\n", kind="t", expect_before=scriptbackup.sha256(b"A = 1\n")
    )
    assert (store.project_dir / rec["id"] / "original.py").read_bytes() == b"A = 1\n"


def test_new_backup_directories_are_linked_durably_before_the_script_is_replaced(
    store, monkeypatch
):
    """文件与时间戳目录自己 fsync 了还不够：把它们挂进备份树的上级目录项也要落盘，且都在替换原件
    之前（Codex 评 #730 P2）。上级落盘失败 → 备份失败、原件不动。"""
    from tavotto.engine import atomicio

    script = store.root / "fig.py"
    script.write_bytes(b"A = 1\n")
    synced: list[Path] = []
    real_fsync_dir = atomicio.fsync_dir

    def record(directory):
        synced.append(Path(directory))
        assert script.read_bytes() == b"A = 1\n"  # 原件还没被替换
        return real_fsync_dir(directory)

    monkeypatch.setattr(atomicio, "fsync_dir", record)
    before = scriptbackup.sha256(b"A = 1\n")
    rec = scriptbackup.replace(store, "fig.py", b"A = 2\n", kind="t", expect_before=before)
    slug = rec["id"].split("/")[0]
    for base in (store.project_dir, store.mirror_dir):
        # 这次新建的整条链：<slug>/ 与它的上级（script-backups/ 或镜像根）直到原本就在的那一级
        assert base / slug in synced and base in synced
    assert script.read_bytes() == b"A = 2\n"

    def fail(directory):
        raise atomicio.AtomicWriteError("dir_fsync_failed", "boom", directory)

    monkeypatch.setattr(atomicio, "fsync_dir", fail)
    with pytest.raises(scriptbackup.ScriptEditError) as err:
        scriptbackup.replace(
            store, "fig.py", b"A = 3\n", kind="t", expect_before=scriptbackup.sha256(b"A = 2\n")
        )
    assert err.value.code == scriptbackup.ERROR_BACKUP_FAILED
    assert script.read_bytes() == b"A = 2\n"


def test_load_only_accepts_backup_ids_it_issued(store):
    for bad in ("../x/y", "fig.py", "a/../b", "a/b/c", "", None):
        with pytest.raises(scriptbackup.ScriptEditError) as err:
            scriptbackup.load(store, bad)
        assert err.value.code == scriptbackup.ERROR_BACKUP_UNKNOWN


# --------------------------------------------------------------- HTTP


@pytest.fixture
def app_project(tmp_path):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    root = tmp_path / "figs"
    root.mkdir()
    m.open_project(str(root))
    yield m, root
    engine_pool.shutdown_all(str(root), wait=True)
    for pid in [p for p, ctx in list(m.PROJECTS.items()) if str(ctx.path) == str(root)]:
        m.close_project(pid, wait=True)


EXISTS_SCRIPT = (
    "import os\n"
    'if not os.path.exists("{old}/data/values.txt"):\n'
    '    raise SystemExit("找不到数据")\n'
    "import matplotlib.pyplot as plt\n"
    'value = float(open("{old}/data/values.txt").read())\n'
    "fig, ax = plt.subplots()\n"
    'print(f"[value] v={{value:g}}")\n'
    'fig.savefig("fig.png")\n'
)


def _preview(client, **body):
    return client.post("/api/script-edit/input-path/preview", json=body)


def test_http_preview_commit_history_and_restore(app_project, tmp_path):
    m, root = app_project
    client = m.app.test_client()
    original = EXISTS_SCRIPT.format(old=OLD).encode("utf-8")
    (root / "fig.py").write_bytes(original)
    chosen = _touch(tmp_path / "moved" / "data" / "values.txt", "3\n")
    resp = _preview(
        client,
        script="fig.py",
        entry=f"{OLD}/data/values.txt",
        chosen=str(chosen.parent.parent),
        chosen_kind="auto",
    )
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert [r["line"] for r in body["rows"]] == [2, 5]  # exists 与连带的 open 同一份 diff
    assert (root / "fig.py").read_bytes() == original  # 预览一个字节都不改
    token = body["token"]
    resp = client.post("/api/script-edit/commit", json={"token": token})
    assert resp.status_code == 200, resp.get_json()
    edited = (root / "fig.py").read_bytes()
    assert edited != original and str(chosen).replace("\\", "/").encode() in edited
    # 令牌单次
    again = client.post("/api/script-edit/commit", json={"token": token})
    assert again.status_code == 409
    assert again.get_json()["code"] == scriptedit.ERROR_TOKEN_INVALID
    hist = client.get("/api/script-backups", query_string={"script": "fig.py"}).get_json()
    assert hist["backups"][0]["state"] == scriptbackup.STATE_CURRENT
    assert hist["backups"][0]["pristine"] is True
    backup_id = hist["backups"][0]["id"]
    assert (root / "tavottofile" / "script-backups" / backup_id / "original.py").read_bytes() == (
        original
    )
    # 之后用户又改了别处：只撤销那几处路径，别的修改保留
    (root / "fig.py").write_bytes(edited.replace(b"subplots()", b"subplots(dpi=90)"))
    resp = client.post(
        "/api/script-backups/restore", json={"backup_id": backup_id, "mode": "undo_edits"}
    )
    assert resp.status_code == 200, resp.get_json()
    assert (root / "fig.py").read_bytes() == original.replace(b"subplots()", b"subplots(dpi=90)")
    # 整份复原：先把此刻的版本另存了一份
    resp = client.post("/api/script-backups/restore", json={"backup_id": backup_id, "mode": "full"})
    assert resp.status_code == 200
    assert (root / "fig.py").read_bytes() == original
    assert len(client.get("/api/script-backups?script=fig.py").get_json()["backups"]) == 3
    everything = client.get("/api/script-backups").get_json()["backups"]
    assert [b["script"] for b in everything] == ["fig.py"] * 3


def test_http_commit_refuses_when_the_script_changed_after_the_preview(app_project, tmp_path):
    m, root = app_project
    client = m.app.test_client()
    (root / "fig.py").write_text(EXISTS_SCRIPT.format(old=OLD), encoding="utf-8")
    chosen = _touch(tmp_path / "moved" / "data" / "values.txt", "3\n")
    token = _preview(
        client,
        script="fig.py",
        entry=f"{OLD}/data/values.txt",
        chosen=str(chosen),
        chosen_kind="file",
    ).get_json()["token"]
    changed = (root / "fig.py").read_bytes() + b"# edited in my editor\n"
    (root / "fig.py").write_bytes(changed)
    resp = client.post("/api/script-edit/commit", json={"token": token})
    assert resp.status_code == 409
    assert resp.get_json()["code"] == scriptbackup.ERROR_SCRIPT_CHANGED
    assert (root / "fig.py").read_bytes() == changed


def test_http_needs_the_ui_session_when_session_auth_is_on(app_project, tmp_path):
    from tavotto import security
    from tavotto.engine import session_client

    m, root = app_project
    (root / "fig.py").write_text(EXISTS_SCRIPT.format(old=OLD), encoding="utf-8")
    chosen = _touch(tmp_path / "moved" / "data" / "values.txt", "3\n")
    state = security.SessionState("n", api_secret="s3cret")
    cookie = state.redeem("n")
    m.app.config[security.STATE_KEY] = state
    try:
        client = m.app.test_client()
        body = {
            "script": "fig.py",
            "entry": f"{OLD}/data/values.txt",
            "chosen": str(chosen),
            "chosen_kind": "file",
        }
        # 只凭本机进程凭据（MCP / CLI 那条）：认证放行，但拿不到令牌
        resp = client.post(
            "/api/script-edit/input-path/preview",
            json=body,
            headers={session_client.AUTH_HEADER: "s3cret"},
        )
        assert resp.status_code == 403
        assert resp.get_json()["code"] == scriptedit.ERROR_NEEDS_UI
        client.set_cookie(security.COOKIE_NAME, cookie)
        resp = client.post("/api/script-edit/input-path/preview", json=body)
        assert resp.status_code == 200, resp.get_json()
        token = resp.get_json()["token"]
        # 同一枚令牌，换一个已认证的浏览器会话：绑定对不上
        other = m.app.test_client()
        other.set_cookie(security.COOKIE_NAME, state.redeem(state.issue_nonce()))
        resp = other.post("/api/script-edit/commit", json={"token": token})
        assert resp.status_code == 409
        assert resp.get_json()["code"] == scriptedit.ERROR_TOKEN_INVALID
        assert (root / "fig.py").read_text(encoding="utf-8") == EXISTS_SCRIPT.format(old=OLD)
    finally:
        m.app.config.pop(security.STATE_KEY, None)


def test_http_refuses_runtime_assets_and_scripts_an_agent_is_editing(
    app_project, tmp_path, monkeypatch
):
    from tavotto.engine import ai_bridge

    m, root = app_project
    client = m.app.test_client()
    (root / "fig.py").write_text(EXISTS_SCRIPT.format(old=OLD), encoding="utf-8")
    chosen = _touch(tmp_path / "moved" / "data" / "values.txt", "3\n")
    body = {"entry": f"{OLD}/data/values.txt", "chosen": str(chosen), "chosen_kind": "file"}
    resp = _preview(client, script="runtime:abc", **body)
    assert resp.get_json()["code"] == scriptbackup.ERROR_SCRIPT_NOT_FOUND
    monkeypatch.setitem(
        ai_bridge.SESSIONS, "s1", {"status": "running", "script_path": str(root / "fig.py")}
    )
    resp = _preview(client, script="fig.py", **body)
    assert resp.status_code == 409 and resp.get_json()["code"] == scriptbackup.ERROR_SCRIPT_BUSY


def test_http_commit_rechecks_the_agent_under_the_script_lock_and_replaces_inside_it(
    app_project, tmp_path, monkeypatch
):
    """锁外那次 `script_busy` 只是快照（Codex 评 #730 P1）：Agent 在快照之后、替换之前登记——提交在
    脚本锁里再判一次、让路；替换本身也在同一把锁里（Agent 的登记段拿的是同一把）。"""
    from tavotto.engine import ai_bridge

    m, root = app_project
    client = m.app.test_client()
    original = EXISTS_SCRIPT.format(old=OLD).encode("utf-8")
    (root / "fig.py").write_bytes(original)
    chosen = _touch(tmp_path / "moved" / "data" / "values.txt", "3\n")
    body = {"entry": f"{OLD}/data/values.txt", "chosen": str(chosen), "chosen_kind": "file"}
    token = _preview(client, script="fig.py", **body).get_json()["token"]
    calls: list[bool] = []

    def busy_after_the_snapshot(path):
        calls.append(
            ai_bridge._SCRIPT_LOCKS.get(ai_bridge._script_key(path), None) is not None
            and ai_bridge._SCRIPT_LOCKS[ai_bridge._script_key(path)].locked()
        )
        return len(calls) > 1  # 第一次（锁外快照）没人；锁里再判时 Agent 已经登记了

    monkeypatch.setattr(ai_bridge, "script_busy", busy_after_the_snapshot)
    resp = client.post("/api/script-edit/commit", json={"token": token})
    assert resp.status_code == 409 and resp.get_json()["code"] == scriptbackup.ERROR_SCRIPT_BUSY
    assert calls == [False, True]  # 第二次是在脚本锁里判的
    assert (root / "fig.py").read_bytes() == original
    # 没有 Agent：替换发生在脚本锁里
    monkeypatch.setattr(ai_bridge, "script_busy", lambda path: False)
    real_replace = scriptbackup.replace
    held: list[bool] = []

    def replace_under_lock(store, script, *a, **k):
        held.append(ai_bridge._SCRIPT_LOCKS[ai_bridge._script_key(root / script)].locked())
        return real_replace(store, script, *a, **k)

    monkeypatch.setattr(scriptbackup, "replace", replace_under_lock)
    token = _preview(client, script="fig.py", **body).get_json()["token"]
    resp = client.post("/api/script-edit/commit", json={"token": token})
    assert resp.status_code == 200, resp.get_json()
    backup_id = resp.get_json()["backup"]["id"]
    resp = client.post("/api/script-backups/restore", json={"backup_id": backup_id, "mode": "full"})
    assert resp.status_code == 200, resp.get_json()
    assert held == [True, True]  # 提交与复原各一次


# --------------------------------------------------------------- 真 worker


@needs_worker
def test_a_probed_absolute_path_is_rewritten_and_the_figure_reads_the_true_data(
    app_project, tmp_path
):
    m, root = app_project
    client = m.app.test_client()
    (root / "fig.py").write_text(EXISTS_SCRIPT.format(old=OLD), encoding="utf-8")
    # 诱饵：项目里放一份同名不同值的——改写只按用户指认的位置，不就近找
    _touch(root / "data" / "values.txt", "999\n")
    workdir.set_mode(root, workdir.MODE_SANDBOX)
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.build("fig.py", str(root), "__main__")
    assert err.value.code == engine_pool.SCRIPT_EXITED_CODE
    offer = err.value.missing_input
    assert offer is not None
    assert {o["path"]: o["via"] for o in offer["others"]} == {
        f"{OLD}/data/values.txt": inputremap.VIA_PROBE
    }
    chosen = _touch(tmp_path / "moved" / "data" / "values.txt", "3\n")
    body = _preview(
        client,
        script="fig.py",
        entry=f"{OLD}/data/values.txt",
        chosen=str(chosen),
        chosen_kind="file",
    ).get_json()
    assert client.post("/api/script-edit/commit", json={"token": body["token"]}).status_code == 200
    worker, resp = engine_pool.build("fig.py", str(root), "__main__")
    assert "fig" in (resp.get("stems") or {})
    log = worker._log_tail()
    assert "[value] v=3" in log and "v=999" not in log


NATIVE_SCRIPT = (
    "import errno\n"
    "def h5py_file(path):\n"
    "    # h5py 的形状：C 层打开，FileNotFoundError 不带 filename，路径只在消息里\n"
    "    import os\n"
    "    if not os.path.isfile(path):\n"
    '        raise FileNotFoundError(errno.ENOENT, f"Unable to synchronously open file '
    "(unable to open file: name = '{{path}}', errno = 2)\")\n"
    "    with open(path) as f:\n"
    "        return float(f.read())\n"
    'value = h5py_file("{old}/run/x.h5")\n'
    "import matplotlib.pyplot as plt\n"
    "fig, ax = plt.subplots()\n"
    'print(f"[value] v={{value:g}}")\n'
    'fig.savefig("fig.png")\n'
)


@needs_worker
def test_a_native_reader_miss_offers_the_dialog_and_the_rewrite_fixes_it(app_project, tmp_path):
    m, root = app_project
    client = m.app.test_client()
    (root / "fig.py").write_text(NATIVE_SCRIPT.format(old=OLD), encoding="utf-8")
    workdir.set_mode(root, workdir.MODE_SANDBOX)
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.build("fig.py", str(root), "__main__")
    assert err.value.code == "script_error"  # 码不变
    offer = err.value.missing_input
    assert offer is not None and offer["via"] == inputremap.VIA_NATIVE
    assert offer["requested"] == f"{OLD}/run/x.h5"
    chosen = _touch(tmp_path / "moved" / "run" / "x.h5", "7\n")
    body = _preview(
        client, script="fig.py", entry=offer["requested"], chosen=str(chosen), chosen_kind="file"
    ).get_json()
    assert client.post("/api/script-edit/commit", json={"token": body["token"]}).status_code == 200
    worker, resp = engine_pool.build("fig.py", str(root), "__main__")
    assert "[value] v=7" in worker._log_tail()


@needs_worker
def test_an_unrelated_missing_file_stays_a_plain_script_error(app_project):
    m, root = app_project
    (root / "fig.py").write_text(
        # 路径在运行时拼出来：脚本里没有哪串常量对得上它
        "import errno\np = '/nonexistent-tavotto-' + str(42) + '/y.h5'\n"
        "raise FileNotFoundError(errno.ENOENT, 'gone', p)\n",
        encoding="utf-8",
    )
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.build("fig.py", str(root), "__main__")
    assert err.value.code == "script_error" and err.value.missing_input is None


# --------------------------------------------------------------- 结构门禁


_COMMIT_PATHS = ("/api/script-edit/commit", "/api/script-backups/restore")
_AGENT_SIDE = [
    *sorted((ROOT / "codex-plugin").rglob("*.py")),
    *sorted((ROOT / "src" / "tavotto" / "engine").glob("ai_*.py")),
    ROOT / "src" / "tavotto" / "engine" / "specfix.py",
    ROOT / "src" / "tavotto" / "engine" / "project_refresh.py",
]


def test_agent_side_code_never_names_the_commit_or_restore_endpoints():
    """「Agent 不能替用户改脚本」写成判据（ADR 0094 §七.4 / 0110 §六）：Codex 插件 / MCP、编码 Agent 桥、
    规范修图、刷新代码里，任何字符串常量都不许含提交 / 复原端点，也不许调 `TOKENS.redeem`。"""
    assert len(_AGENT_SIDE) > 5  # 目标文件真的在（空门禁比没有门禁更坏）
    offenders = []
    for path in _AGENT_SIDE:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if any(p in node.value for p in _COMMIT_PATHS):
                    offenders.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.Attribute) and node.attr == "redeem":
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
