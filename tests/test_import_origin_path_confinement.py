"""importscan 的路径围栏（CodeQL py/path-injection #224–#226 的跟进）。

主语：`scan()` 的 `script` 来自请求体（经 deprepair → depplan.plan），`Entry.cwd` 来自调用方。
这两个值在进入 importscan 之前没有 CodeQL 认得的围栏，所以 importscan 自己在边界上做真实约束：
`entry.cwd` 只收项目内相对目录；读文件前路径必须在项目根内；拼进路径的名字必须是合法标识符。
判据是「文件系统调用有没有碰到项目外的路径」（用 os.stat / os.lstat / os.scandir /
read_regular_text 的间谍看），不是「结果长什么样」。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tavotto.engine import importscan, scanbudget


def _project(tmp_path: Path) -> tuple[Path, Path]:
    proj = tmp_path / "proj"
    outside = tmp_path / "outside"
    proj.mkdir()
    outside.mkdir()
    (proj / "s.py").write_text("import outside_mod\n", encoding="utf-8")
    (outside / "outside_mod.py").write_text("import yaml\n", encoding="utf-8")
    (proj / "sub").mkdir()
    (proj / "sub" / "inner_mod.py").write_text("import yaml\n", encoding="utf-8")
    return proj, outside


class _Spy:
    def __init__(self, monkeypatch, outside: Path):
        self.outsides = {os.path.abspath(outside), os.path.realpath(outside)}
        self.calls: list[str] = []
        self.ops: list[str] = []
        for name in ("stat", "lstat", "scandir"):
            real = getattr(os, name)
            monkeypatch.setattr(os, name, self._wrap(name, real))
        real_read = scanbudget.read_regular_text

        def read(base, *parts, **kw):
            self._note(os.path.join(os.fspath(base), *parts), "read")
            return real_read(base, *parts, **kw)

        monkeypatch.setattr(scanbudget, "read_regular_text", read)

    def _note(self, p, op: str) -> None:
        self.calls.append(os.path.abspath(os.fsdecode(os.fspath(p))))
        self.ops.append(op)

    def _wrap(self, op, real):
        def f(path=".", *a, **kw):
            if isinstance(path, (str, bytes, os.PathLike)):
                self._note(path, op)
            return real(path, *a, **kw)

        return f

    def touched_outside(self, ops=("stat", "lstat", "scandir", "read")) -> list[str]:
        return [
            c
            for c, op in zip(self.calls, self.ops)
            if op in ops and any(c == o or c.startswith(o + os.sep) for o in self.outsides)
        ]


@pytest.mark.parametrize(
    "cwd",
    [
        "../outside",
        "sub/../../outside",
        "..",
        "/etc",
        "C:\\x",
        "C:/x",
        "\\\\server\\share",
        "\\outside",
    ],
)
def test_bad_cwd_is_not_a_search_root_and_nothing_outside_is_touched(tmp_path, monkeypatch, cwd):
    proj, outside = _project(tmp_path)
    if cwd == "/etc":
        cwd = str(outside)  # 绝对路径：指到真实存在的项目外目录
    spy = _Spy(monkeypatch, outside)
    entry = importscan.Entry(kind="module", module="outside_mod", cwd_mode="project_root", cwd=cwd)
    res = importscan.scan(proj, "", entry=entry)
    assert res.search_roots == ()
    assert res.search_complete is False
    assert any(w["code"] == "cwd_outside_project" for w in res.warnings)
    assert spy.touched_outside() == []


def test_cwd_that_dotdots_back_into_the_project_is_normalised(tmp_path):
    proj, _ = _project(tmp_path)
    entry = importscan.Entry(
        kind="module", module="inner_mod", cwd_mode="project_root", cwd="sub/../sub"
    )
    res = importscan.scan(proj, "", entry=entry)
    assert [r["path"] for r in res.search_roots] == ["sub"]
    assert not any(w["code"] == "cwd_outside_project" for w in res.warnings)
    assert "inner_mod" in res.files[0] or res.files


def test_cwd_symlink_pointing_outside_is_refused(tmp_path, monkeypatch):
    proj, outside = _project(tmp_path)
    try:
        (proj / "ln").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    spy = _Spy(monkeypatch, outside)
    entry = importscan.Entry(kind="module", module="outside_mod", cwd_mode="project_root", cwd="ln")
    res = importscan.scan(proj, "", entry=entry)
    assert res.search_roots == () and res.search_complete is False
    # `within` 为判定会 realpath（lstat 链接本身）——这不是读；目录清单与文件内容绝不读
    assert spy.touched_outside(("scandir", "read")) == []


def test_read_refuses_a_path_outside_the_project_without_a_filesystem_call(tmp_path, monkeypatch):
    proj, outside = _project(tmp_path)
    scanner = importscan._Scanner(
        proj,
        "s.py",
        declared={},
        stdlib=None,
        tree=None,
        entry=importscan.Entry(),
        builtin=None,
        no_follow=False,
        budget=None,
    )
    spy = _Spy(monkeypatch, outside)
    for bad in (
        outside / "outside_mod.py",
        proj / ".." / "outside" / "outside_mod.py",
        Path(os.path.sep) / "etc" / "hosts",
    ):
        text, problem = scanner._read(bad, "x.py")
        assert text is None and problem == {"path": "x.py", "kind": "outside_project"}
    assert spy.calls == []
    text, problem = scanner._read(
        proj / "sub" / ".." / "s.py", "s.py"
    )  # 绕回项目内：按规范化结果读
    assert problem is None and "outside_mod" in (text or "")


@pytest.mark.parametrize("name", ["../outside", "a/b", "..", "a b", "", "1x", "x.y", "x\\y"])
def test_import_names_that_are_not_identifiers_never_become_paths(tmp_path, monkeypatch, name):
    proj, outside = _project(tmp_path)
    finder = importscan._Finder(proj, no_follow=False, budget=scanbudget.Budget())
    spy = _Spy(monkeypatch, outside)
    assert finder.find_in(proj, name) is None
    assert spy.calls == []


def test_relative_import_with_non_identifier_segments_is_not_followed(tmp_path):
    import ast

    proj, _ = _project(tmp_path)
    tree = ast.parse("from .a import b\n")
    tree.body[0].module = "../../outside"  # AST 正常不会产生；直接构造
    assert importscan._relative_targets(tree, proj / "s.py", proj) is None
