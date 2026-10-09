"""导入来源解析器里「跟随链接的调用」全量排查的守门（Codex 第四轮 P1，importscan.py 列目录）。

病根：`DirEntry.is_dir()` / `is_file()` 默认 `follow_symlinks=True`，在我们判断这一项是不是链接 / junction
**之前**就 stat 了它的目标——不受信项目里指向 UNC / WebDAV 的链接，光列父目录就可能联网。排查结论是一张
「会触碰文件系统元数据的调用」清单：能不跟随的一律不跟随（`entry.stat(follow_symlinks=False)` / `os.lstat`），
必须跟随的只准出现在经审定的白名单函数里（那里的路径已被 `_confined_path` 逐级 `lstat` 确认没有越界链接）。

两半：
* 结构性（AST）：扫 importscan.py，跟随式调用只许在白名单；不可静态判定的写法直接红；白名单不许有过期项。
* 行为：项目里放指向项目外的目录 / 文件的符号链接，以及用假 stat 模拟的 Windows junction，在
  `no_follow=True` 与默认模式下扫描；间谍断言从未对项目外目标发起跟随式 `stat` / `scandir` / `DirEntry` 判断。
"""

from __future__ import annotations

import ast
import os
import stat as stat_mod
from pathlib import Path

import pytest

from tavotto.engine import importscan

SRC = Path(importscan.__file__)

# ----------------------------------------------------------------------------------- 结构性

# 不触碰文件系统的纯字符串 / 类型成员
_PURE_OS = {
    "os.path.abspath",
    "os.path.normcase",
    "os.path.normpath",
    "os.path.relpath",
    "os.path.join",
    "os.path.dirname",
    "os.path.isabs",
    "os.path.splitext",
    "os.fspath",
    "os.fsdecode",
    "os.sep",
    "os.pardir",
    "os.stat_result",
}

# 会触碰文件系统元数据的调用。键 = 点分名（os.* / projectenv.* / scanbudget.*）或 ".方法名"（任意接收者）。
# 值 = {函数限定名: 理由}。不在这里出现的跟随式写法一律红。
_ALLOW: dict[str, dict[str, str]] = {
    "os.lstat": {
        "_confined_path": "逐级 lstat，cur 是已确认无链接的前缀；链接立即停下，不 stat 目标",
        "_kind": "先过 _confined_path，路径已无链接，lstat 等价于 stat",
        "_Finder.dir_guard": "p = 已守卫目录 d / name；只为取 reparse 位，不跟随",
        "_Scanner._read": "no_follow 时先过 _confined_path；默认模式用的是 contained_path 净化后的真实路径",
    },
    "os.stat": {
        "_Scanner._read": "仅默认模式，且路径是 _confined_path + contained_path 净化后的项目内真实路径",
    },
    "os.scandir": {
        "_Finder.listing": "入口先 _confined_path(d)；目录项只取 entry.stat(follow_symlinks=False)",
        "_package_files.walk": "pkg_dir 先 _kind 判过；目录项只取 entry.stat(follow_symlinks=False)",
        "_as_module": "target 先 _kind 判为 dir；只取名字，再逐个过 _kind",
    },
    "os.readlink": {
        "_confined_path": "只读链接文本（不 stat 目标），随后词法判定目标是否在项目内",
    },
    "os.path.realpath": {
        "_confined_path": "参数是用户自己打开的项目根，不是项目派生路径",
        "_Scanner._read": "参数是用户自己打开的项目根，不是项目派生路径",
    },
    "projectenv.within": {
        "_inside": "只在 _confined_path 确认路径无越界链接之后调用（resolve 不会碰到项目外）",
    },
    "projectenv.contained_path": {
        "_Scanner._read": "默认模式：已先过 _confined_path，路径里没有指向项目外的链接",
    },
    "scanbudget.read_regular_text": {
        "_Scanner._read": "逐级 lstat + O_NOFOLLOW（no_follow）；默认模式读的是净化后的真实路径",
    },
    "scanbudget.redirected_component": {
        "_Finder.guard": "只做逐级 lstat",
    },
}
# 带 follow_symlinks 关键字的方法：必须显式 False（不论接收者是什么，宁可错杀）
_KW_METHODS = {"is_dir", "is_file", "stat"}
# 其它「碰元数据 / 内容」的方法：任何接收者都只许白名单
_ATTR_ONLY = {
    "exists",
    "resolve",
    "glob",
    "rglob",
    "iterdir",
    "samefile",
    "readlink",
    "open",
    "read_text",
    "read_bytes",
    "lstat",
    "is_mount",
    "expanduser",
}
_BARE_FORBIDDEN = {"open", "eval", "exec", "__import__", "vars", "globals", "locals"}
# getattr 用在 AST 节点上是正常的；用在这些对象上就是把调用点藏进字符串
_FS_OBJECTS = {"os", "Path", "projectenv", "scanbudget", "shutil", "glob", "pathlib", "entry"}
_TRACKED_MODULES = ("projectenv", "scanbudget")


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


class _Auditor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.used: set[tuple[str, str]] = set()
        self.bad: list[str] = []

    def _where(self) -> str:
        return ".".join(self.stack) or "<module>"

    def _visit_scope(self, node) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _visit_scope

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            if a.name.split(".")[0] in ("os", "shutil", "glob", "pathlib") and a.asname:
                self.bad.append(
                    f"{node.lineno}: import alias {a.name} as {a.asname}（别名让调用点不可判定）"
                )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and (node.module or "").split(".")[0] in ("os", "glob", "shutil"):
            self.bad.append(f"{node.lineno}: from {node.module} import …（把调用点藏进裸名）")

    def _allowed(self, key: str, lineno: int) -> None:
        where = self._where()
        if where in _ALLOW.get(key, {}):
            self.used.add((key, where))
        else:
            self.bad.append(f"{lineno}: {key} 出现在 {where}，不在白名单")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        name = _dotted(node)
        if name and (
            name.startswith("os.") or name.startswith("shutil.") or name.startswith("glob.")
        ):
            if name == "os.path":
                pass
            elif name in _PURE_OS:
                pass
            else:
                self._allowed(name, node.lineno)
            return  # 不再下钻（os.path.realpath 里的 os.path）
        if name and name.split(".")[0] in _TRACKED_MODULES and name in _ALLOW:
            self._allowed(name, node.lineno)
        elif name and name.split(".")[0] in _TRACKED_MODULES and name.split(".")[0] == "projectenv":
            # projectenv 里除 within / contained_path 之外的成员本模块不用；用了就必须先审
            if name not in ("projectenv.within", "projectenv.contained_path"):
                self.bad.append(f"{node.lineno}: 未审定的 {name}")
        elif node.attr in _ATTR_ONLY:
            self._allowed("." + node.attr, node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        if (
            isinstance(f, ast.Attribute)
            and f.attr in _KW_METHODS
            and not (_dotted(f) or "").startswith("os.")
        ):
            kw = {k.arg: k.value for k in node.keywords}
            v = kw.get("follow_symlinks")
            if not (isinstance(v, ast.Constant) and v.value is False):
                self.bad.append(
                    f"{node.lineno}: .{f.attr}() 必须显式 follow_symlinks=False（在 {self._where()}）"
                )
        elif isinstance(f, ast.Name) and f.id in _BARE_FORBIDDEN:
            self.bad.append(f"{node.lineno}: 裸调用 {f.id}()（在 {self._where()}）")
        elif (
            isinstance(f, ast.Name)
            and f.id == "getattr"
            and node.args
            and (_dotted(node.args[0]) or "?").split(".")[0] in _FS_OBJECTS | {"?"}
        ):
            self.bad.append(f"{node.lineno}: getattr() 作用于文件系统对象或不可判定的对象")
        elif isinstance(f, (ast.Call, ast.Subscript, ast.Lambda, ast.IfExp)):
            self.bad.append(f"{node.lineno}: 被调用者不是静态名字，无法判定它碰不碰文件系统")
        self.generic_visit(node)


def _audit() -> _Auditor:
    a = _Auditor()
    a.visit(ast.parse(SRC.read_text(encoding="utf-8")))
    return a


class TestFollowingCallsAreAudited:
    def test_every_filesystem_touching_call_is_non_following_or_whitelisted(self):
        a = _audit()
        assert a.bad == []

    def test_the_whitelist_has_no_stale_entries_and_every_entry_has_a_reason(self):
        a = _audit()
        declared = {(k, fn) for k, v in _ALLOW.items() for fn in v}
        assert declared == a.used, sorted(declared ^ a.used)
        assert all(len(r) >= 8 for v in _ALLOW.values() for r in v.values())

    def test_no_direntry_follow_default_in_the_listing_functions(self):
        """正面钉住这次的病根：列目录那段只许 `entry.stat(follow_symlinks=False)`，不许 is_dir / is_file。"""
        tree = ast.parse(SRC.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name in ("listing", "_package_files", "walk"):
                for n in ast.walk(fn):
                    if isinstance(n, ast.Attribute):
                        assert n.attr not in ("is_dir", "is_file"), fn.name

    def test_the_auditor_catches_the_shapes_it_claims_to(self):
        bad = ast.parse(
            "def f(e, p):\n"
            "    e.is_dir()\n"
            "    e.stat()\n"
            "    p.exists()\n"
            "    os.stat(p)\n"
            "    os.path.isdir(p)\n"
            "    getattr(os, 'stat')(p)\n"
            "    open(p)\n"
        )
        a = _Auditor()
        a.visit(bad)
        assert len(a.bad) >= 7


# ----------------------------------------------------------------------------------- 行为


def _sym(link: Path, target: Path, is_dir: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"no symlinks: {exc}")


def _write(p: Path, text: str = "") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class _Fake:
    """假的 junction lstat 结果：reparse 位 + name-surrogate tag，不是 S_ISLNK。"""

    def __init__(self, st: os.stat_result) -> None:
        self.st_mode = st.st_mode
        self.st_size = 0
        self.st_file_attributes = 0x400 | 0x10
        self.st_reparse_tag = 0xA0000003


class _Spy:
    """记录对 `forbidden` 之下（按 realpath 判）目标发起的跟随式元数据调用 / scandir。"""

    def __init__(self, monkeypatch, forbidden: list[Path], fake_junctions: tuple[Path, ...] = ()):
        self.forbidden = [os.path.realpath(p) for p in forbidden]
        self.fake = {os.path.abspath(p) for p in fake_junctions}
        self.hits: list[str] = []
        self.calls = 0
        real_stat, real_lstat, real_scandir = os.stat, os.lstat, os.scandir
        spy = self

        def in_forbidden(path) -> bool:
            try:
                r = os.path.realpath(os.fsdecode(os.fspath(path)))
            except (TypeError, ValueError):
                return False
            return any(r == x or r.startswith(x + os.sep) for x in spy.forbidden)

        def fake_stat(path, *a, follow_symlinks=True, **kw):
            spy.calls += 1
            if follow_symlinks and in_forbidden(path):
                spy.hits.append(f"os.stat({path})")
            return real_stat(path, *a, follow_symlinks=follow_symlinks, **kw)

        def fake_lstat(path, *a, **kw):
            st = real_lstat(path, *a, **kw)
            if os.path.abspath(os.fspath(path)) in spy.fake:
                return _Fake(st)
            return st

        class Entry:
            def __init__(self, e):
                self._e = e

            def __getattr__(self, n):
                return getattr(self._e, n)

            def _chk(self, what, follow):
                spy.calls += 1
                if follow and in_forbidden(self._e.path):
                    spy.hits.append(f"DirEntry.{what}({self._e.path})")

            def is_dir(self, *, follow_symlinks=True):
                self._chk("is_dir", follow_symlinks)
                return self._e.is_dir(follow_symlinks=follow_symlinks)

            def is_file(self, *, follow_symlinks=True):
                self._chk("is_file", follow_symlinks)
                return self._e.is_file(follow_symlinks=follow_symlinks)

            def stat(self, *, follow_symlinks=True):
                self._chk("stat", follow_symlinks)
                st = self._e.stat(follow_symlinks=follow_symlinks)
                if not follow_symlinks and os.path.abspath(self._e.path) in spy.fake:
                    return _Fake(st)
                return st

        class Scan:
            def __init__(self, it):
                self._it = it

            def __enter__(self):
                self._it.__enter__()
                return self

            def __exit__(self, *a):
                return self._it.__exit__(*a)

            def __iter__(self):
                for e in self._it:
                    yield Entry(e)

        def fake_scandir(path="."):
            spy.calls += 1
            if in_forbidden(path):
                spy.hits.append(f"os.scandir({path})")
            return Scan(real_scandir(path))

        monkeypatch.setattr(os, "stat", fake_stat)
        monkeypatch.setattr(os, "lstat", fake_lstat)
        monkeypatch.setattr(os, "scandir", fake_scandir)


@pytest.mark.parametrize("no_follow", [True, False])
class TestNoFollowingStatOfOutsideTargets:
    def _world(self, tmp_path):
        proj = tmp_path / "proj"
        outside = tmp_path / "outside"
        _write(outside / "ns" / "mod.py", "import secret_dir\n")
        _write(outside / "ns" / "__init__.py")
        _write(outside / "file_target.py", "import secret_file\n")
        _write(
            proj / "s.py", "import linkdir\nimport linkfile\nimport pkg\nfrom .reldir import x\n"
        )
        _sym(proj / "linkdir", outside / "ns", True)
        _sym(proj / "linkfile.py", outside / "file_target.py", False)
        _sym(proj / "reldir", outside / "ns", True)
        _write(proj / "pkg" / "__init__.py", "")
        _sym(proj / "pkg" / "sub", outside / "ns", True)
        _sym(proj / "pkg" / "lnk.py", outside / "file_target.py", False)
        return proj, outside

    def test_symlinks_to_outside_dirs_and_files_are_never_followed(
        self, tmp_path, monkeypatch, no_follow
    ):
        proj, outside = self._world(tmp_path)
        spy = _Spy(monkeypatch, [outside])
        res = importscan.scan(proj, "s.py", no_follow=no_follow)
        assert spy.calls > 0  # 间谍确实在线
        assert spy.hits == []
        assert "secret_dir" not in {c.module for c in res.classes}
        assert "secret_file" not in {c.module for c in res.classes}

    def test_entry_cwd_symlink_to_outside_is_not_touched(self, tmp_path, monkeypatch, no_follow):
        proj, outside = self._world(tmp_path)
        _sym(proj / "cwdlink", outside / "ns", True)
        spy = _Spy(monkeypatch, [outside])
        importscan.scan(proj, "s.py", entry=importscan.Entry(cwd="cwdlink"), no_follow=no_follow)
        assert spy.hits == []

    def test_a_windows_junction_is_never_followed_or_listed(self, tmp_path, monkeypatch, no_follow):
        proj = tmp_path / "proj"
        _write(proj / "s.py", "import junc\nimport pkg\nfrom .junc2 import y\n")
        _write(proj / "junc" / "mod.py", "import secret\n")  # 「junction」的目标
        _write(proj / "junc2" / "mod.py", "import secret\n")
        _write(proj / "pkg" / "__init__.py")
        _write(proj / "pkg" / "jsub" / "m.py", "import secret\n")
        junctions = (proj / "junc", proj / "junc2", proj / "pkg" / "jsub")
        spy = _Spy(monkeypatch, list(junctions), fake_junctions=junctions)
        res = importscan.scan(proj, "s.py", no_follow=no_follow)
        assert spy.hits == []
        assert "secret" not in {c.module for c in res.classes}
        assert res.search_complete is False


class TestInsideSymlinksStillResolveByDefault:
    def test_dir_file_relative_and_package_links_inside_the_project(self, tmp_path):
        _write(tmp_path / "s.py", "import linkdir\nimport linkfile\nfrom .rel import z\n")
        _write(tmp_path / "real" / "ns" / "__init__.py")
        _write(tmp_path / "real" / "ns" / "mod.py", "import numpy\n")
        _write(tmp_path / "real" / "f.py", "import pandas\n")
        _sym(tmp_path / "linkdir", tmp_path / "real" / "ns", True)
        _sym(tmp_path / "linkfile.py", tmp_path / "real" / "f.py", False)
        _sym(tmp_path / "rel", tmp_path / "real" / "ns", True)
        res = importscan.scan(tmp_path, "s.py")
        by = {c.module: c for c in res.classes}
        assert by["linkdir"].bucket == "local" and by["linkfile"].bucket == "local"
        assert "pandas" in by  # 链接文件里的 import 被读到了
        # 同一份世界在 no_follow 下不下探
        res2 = importscan.scan(tmp_path, "s.py", no_follow=True)
        assert "pandas" not in {c.module for c in res2.classes}

    def test_a_chain_of_inside_links_is_followed_but_one_hop_out_is_not(self, tmp_path):
        proj = tmp_path / "proj"
        outside = tmp_path / "outside"
        _write(proj / "s.py", "import a\nimport b\n")
        _write(proj / "real" / "__init__.py")
        _sym(proj / "mid", proj / "real", True)
        _sym(proj / "a", proj / "mid", True)  # a -> mid -> real（都在项目内）
        _write(outside / "__init__.py")
        _sym(proj / "hop", outside, True)
        _sym(proj / "b", proj / "hop", True)  # b -> hop -> outside（第二跳出界）
        res = importscan.scan(proj, "s.py")
        by = {c.module: c for c in res.classes}
        assert by["a"].bucket == "local"
        assert by["b"].resolution_status == "unverified"


def test_the_pure_helpers_never_stat_a_link_target(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    _write(outside / "x.py")
    proj = tmp_path / "proj"
    proj.mkdir()
    _sym(proj / "l", outside, True)
    spy = _Spy(monkeypatch, [outside])
    assert importscan._confined_path(proj, proj / "l" / "x.py", no_follow=False) is None
    assert importscan._confined_path(proj, proj / "l" / "x.py", no_follow=True) is None
    assert importscan._kind(proj, proj / "l", no_follow=False) == "blocked"
    assert spy.hits == []
    assert stat_mod.S_ISDIR(os.stat(proj).st_mode)
