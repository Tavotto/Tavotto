"""转换候选工作区的长路径安全（issue #1623）：helper 纯函数 + 候选建在深目录 + 路径过长的闭集失败码。"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from tavotto.engine import ai_convert, ai_figure_edit, convertcontext, longpath


# --------------------------------------------------------------------------- helper 纯函数
def test_extended_drive_path_gets_prefix_and_is_normalised():
    assert longpath.extended("C:\\a\\b\\..\\c/d.csv", windows=True) == "\\\\?\\C:\\a\\c\\d.csv"


def test_extended_unc_path_uses_unc_prefix():
    assert (
        longpath.extended("\\\\srv\\share\\dir\\f.csv", windows=True)
        == "\\\\?\\UNC\\srv\\share\\dir\\f.csv"
    )


@pytest.mark.parametrize(
    "text",
    [
        "\\\\?\\C:\\already\\long",  # 已带前缀
        "\\\\?\\UNC\\srv\\share\\x",
        "\\\\.\\COM1",  # 设备路径
        "relative\\dir\\f.csv",  # 相对路径加前缀会改变含义
        "\\rooted\\without\\drive",
    ],
)
def test_extended_leaves_other_shapes_alone(text):
    assert longpath.extended(text, windows=True) == text


def test_extended_is_identity_off_windows():
    assert longpath.extended("C:\\a\\b", windows=False) == "C:\\a\\b"
    assert longpath.extended("/tmp/a/b", windows=False) == "/tmp/a/b"


def test_long_returns_path_unchanged_on_this_platform(tmp_path):
    assert longpath.long(tmp_path) == tmp_path
    assert isinstance(longpath.long(str(tmp_path)), Path)


def test_is_too_long_recognises_winerror_206_and_enametoolong():
    win = OSError(2, "文件名或扩展名太长。")
    win.winerror = 206
    assert longpath.is_too_long(win)
    assert longpath.is_too_long(OSError(errno.ENAMETOOLONG, "File name too long"))
    assert not longpath.is_too_long(OSError(errno.ENOENT, "missing"))
    assert not longpath.is_too_long(ValueError("x"))


# --------------------------------------------------------------------------- 候选工作区
@pytest.fixture
def deep(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_convert, "CONVERT_DIR", tmp_path / "convert")
    figs = tmp_path / "figs"
    # 项目相对路径 > 260 字符（每段 < 255，POSIX 上本来就能建）
    rel_dir = Path(*[("实验数据目录" + str(i)) * 6 for i in range(6)])
    (figs / rel_dir).mkdir(parents=True)
    data = figs / rel_dir / "measurement.csv"
    data.write_bytes(b"x,y\n1,2\n")
    script = figs / "fig.R"
    script.write_bytes(f'd <- read.csv("{(rel_dir / "measurement.csv").as_posix()}")\n'.encode())
    assert len(str(rel_dir / "measurement.csv")) > 200
    return figs, script, rel_dir


def test_candidate_workspace_copies_deep_project_relative_data(deep):
    figs, script, rel_dir = deep
    cand = ai_convert.create_candidate(
        "0123456789ab", script, figs, "fig.R", library=None, source_language="r"
    )
    copied = cand.dir / convertcontext.DIRECTORY / rel_dir / "measurement.csv"
    assert copied.read_bytes() == b"x,y\n1,2\n"
    assert ai_figure_edit._tree(cand.dir)  # 遍历深目录不抛
    assert convertcontext.unchanged(figs, cand.context, snapshot_dir=cand.dir)
    # 交给 Codex 的路径保持普通形式
    assert not str(cand.dir).startswith("\\\\?\\")
    ai_convert.remove_dir(cand.dir)
    assert not cand.dir.exists()


def test_candidate_file_operations_go_through_the_long_path_helper(deep, monkeypatch):
    figs, script, _ = deep
    seen: list[str] = []
    real = longpath.long

    def spy(path):
        seen.append(str(path))
        return real(path)

    monkeypatch.setattr(longpath, "long", spy)
    cand = ai_convert.create_candidate(
        "0123456789ab", script, figs, "fig.R", library=None, source_language="r"
    )
    ai_convert.remove_dir(cand.dir)
    assert any(s.endswith("measurement.csv") for s in seen), "project_context 的写入没走 helper"
    assert any(s == str(cand.dir) for s in seen), "候选目录的创建/删除没走 helper"


def test_too_long_path_failure_is_a_closed_code_and_leaves_no_directory(deep, monkeypatch):
    figs, script, _ = deep

    def boom(*a, **k):
        raise OSError(errno.ENAMETOOLONG, "File name too long")

    monkeypatch.setattr(convertcontext, "snapshot", boom)
    with pytest.raises(ai_convert.ConvertError) as got:
        ai_convert.create_candidate(
            "0123456789ab", script, figs, "fig.R", library=None, source_language="r"
        )
    assert got.value.code == "workspace_path_too_long"
    assert got.value.status == 400
    assert not (ai_convert.CONVERT_DIR / "0123456789ab").exists()


def test_other_os_errors_are_not_relabelled(deep, monkeypatch):
    figs, script, _ = deep

    def boom(*a, **k):
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(convertcontext, "snapshot", boom)
    with pytest.raises(PermissionError):
        ai_convert.create_candidate(
            "0123456789ab", script, figs, "fig.R", library=None, source_language="r"
        )
