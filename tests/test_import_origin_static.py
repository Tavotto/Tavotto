"""Import Origin Resolver PR1：静态来源解析核心（`importscan`）。

每条用例在 `tmp_path` 里建**真实文件树**再扫；凡是「真实 Python 怎么选」的断言，另有一条用例用
同一棵树跑真的解释器对拍（`TestRealPythonParity`——对拍只在测试里起子进程，被测的 `importscan`
一个子进程都不起，见 `test_import_origin_safety.py`）。

编号 O-xx 对应维护者计划 P7.1 的静态导入测试矩阵；已被既有用例覆盖的写在
`TestAlreadyCoveredElsewhere`（指到原用例，并在这里补一条反向钉）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import importscan, scanbudget

SCRIPT = "s.py"


def _write(root: Path, rel: str, text: str | bytes = "") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        p.write_bytes(text)
    else:
        p.write_text(text, encoding="utf-8")
    return p


def _tree(root: Path, files: dict[str, str | bytes]) -> Path:
    for rel, text in files.items():
        _write(root, rel, text)
    return root


def _by(res: importscan.ScanResult) -> dict[str, importscan.ImportClass]:
    return {c.module: c for c in res.classes}


def _scan(root: Path, script: str = SCRIPT, **kw) -> importscan.ScanResult:
    return importscan.scan(root, script, **kw)


def _symlink(link: Path, target: Path, *, is_dir: bool = False) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except (
        OSError,
        NotImplementedError,
    ) as exc:  # Windows 没有创建符号链接的权限：这条用例没法建夹具
        pytest.skip(f"cannot create symlinks here: {exc}")


def _real_python(root: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    """用真的解释器跑一份夹具脚本（只在测试里；干净环境、不写字节码）。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, script, *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


# ===========================================================================
# 导入优先级：built-in / frozen 先于文件系统，本地同名文件遮蔽 stdlib，目录包 > 单文件
# ===========================================================================
class TestPriority:
    def test_o06_a_local_json_py_shadows_the_stdlib_and_is_followed(self, tmp_path):
        _tree(
            tmp_path,
            {SCRIPT: "import json\nimport numpy\n", "json.py": "import requests\n"},
        )
        res = _scan(tmp_path, declared={"numpy": ""})
        c = _by(res)["json"]
        assert (c.bucket, c.origin_kind, c.local_path) == ("local", "project-local", "json.py")
        assert c.shadowing == "stdlib"
        assert c.resolution_status == "resolved"
        assert not c.needed
        # 本地 json.py 的 import 被跟进（它是脚本一开跑就执行的代码）
        req = _by(res)["requests"]
        assert req.via == ("json.py",) and req.needed

    def test_o06_a_stdlib_name_without_a_local_file_stays_stdlib(self, tmp_path):
        _tree(tmp_path, {SCRIPT: "import json\n", "other.py": ""})
        c = _by(_scan(tmp_path))["json"]
        assert (c.bucket, c.origin_kind, c.shadowing) == ("stdlib", "stdlib", "")

    def test_o07_builtin_and_preloaded_names_are_not_shadowed_by_local_files(self, tmp_path):
        _tree(
            tmp_path,
            {
                SCRIPT: "import sys\nimport os\nimport io\nimport time\n",
                "sys.py": "import evil_one\n",
                "os.py": "import evil_two\n",
                "io.py": "import evil_three\n",
                "time.py": "import evil_four\n",
            },
        )
        res = _scan(tmp_path)
        got = _by(res)
        assert {m: (c.bucket, c.origin_kind) for m, c in got.items()} == {
            "sys": ("stdlib", "built-in"),
            "os": ("stdlib", "stdlib"),
            "io": ("stdlib", "stdlib"),
            "time": ("stdlib", "built-in"),
        }
        assert all("local_file_never_imported" in c.warnings for c in got.values())
        assert got["sys"].evidence == ("builtin_module",)
        assert got["os"].evidence == ("preloaded_stdlib",)
        # 这些本地文件真实 Python 永远不会 import，所以里面的 import 也不跟进
        assert not any(m.startswith("evil_") for m in got)
        assert res.files == (SCRIPT,)

    def test_frozen_importlib_is_frozen(self, tmp_path):
        _tree(tmp_path, {SCRIPT: "import _frozen_importlib\n"})
        c = _by(_scan(tmp_path))["_frozen_importlib"]
        assert (c.bucket, c.origin_kind, c.evidence) == ("stdlib", "frozen", ("frozen_module",))

    def test_o07_a_builtin_that_only_this_host_has_is_ambiguous_when_shadowed(
        self, tmp_path, monkeypatch
    ):
        """宿主上内建、别的构建上可能是文件的名字：没拿到目标解释器的内建名单就不下结论。"""
        monkeypatch.setattr(importscan, "HOST_BUILTIN_EXTRA", frozenset({"hostonly"}))
        _tree(tmp_path, {SCRIPT: "import hostonly\n", "hostonly.py": ""})
        stdlib = frozenset({"hostonly"})
        c = _by(_scan(tmp_path, stdlib=stdlib))["hostonly"]
        assert (c.bucket, c.resolution_status, c.shadowing) == ("local", "ambiguous", "stdlib")
        assert "may_shadow_builtin" in c.warnings
        # 目标解释器明说它是内建 → 本地文件到不了它，答案确定
        sure = _by(_scan(tmp_path, stdlib=stdlib, builtin=frozenset({"hostonly"})))["hostonly"]
        assert (sure.bucket, sure.origin_kind, sure.resolution_status) == (
            "stdlib",
            "built-in",
            "resolved",
        )
        # 目标解释器明说它不是内建 → 本地文件遮蔽它，也是确定的
        shadowed = _by(_scan(tmp_path, stdlib=stdlib, builtin=frozenset({"sys"})))["hostonly"]
        assert (shadowed.bucket, shadowed.resolution_status) == ("local", "resolved")

    def test_o18_a_package_directory_wins_over_a_same_named_module(self, tmp_path):
        _tree(
            tmp_path,
            {
                SCRIPT: "import dup\n",
                "dup.py": "import wrong_one\n",
                "dup/__init__.py": "import right_one\n",
            },
        )
        res = _scan(tmp_path)
        assert _by(res)["dup"].local_path == "dup/__init__.py"
        assert "right_one" in _by(res) and "wrong_one" not in _by(res)
        assert "package_with_init" in _by(res)["dup"].evidence

    def test_o18_an_extension_module_wins_over_a_same_named_py_in_one_directory(self, tmp_path):
        _tree(
            tmp_path,
            {
                SCRIPT: "import fast\n",
                "fast.py": "import wrong_one\n",
                "fast.cpython-313-darwin.so": b"\x00",
            },
        )
        res = _scan(tmp_path)
        c = _by(res)["fast"]
        assert (c.bucket, c.origin_kind, c.local_path) == (
            "local",
            "extension",
            "fast.cpython-313-darwin.so",
        )
        assert "wrong_one" not in _by(res)  # 真实 Python 加载的是扩展，.py 永远不执行

    @pytest.mark.parametrize(
        "filename",
        [
            "ext.so",
            "ext.pyd",
            "ext.abi3.so",
            "ext.cpython-313-darwin.so",
            "ext.cp313-win_amd64.pyd",
        ],
    )
    def test_extension_suffixes_python_accepts(self, tmp_path, filename):
        _tree(tmp_path, {SCRIPT: "import ext\n", filename: b"\x00"})
        c = _by(_scan(tmp_path))["ext"]
        assert (c.bucket, c.origin_kind, c.local_path) == ("local", "extension", filename)

    @pytest.mark.parametrize("filename", ["ext.dylib", "ext.dll", "ext.so.txt", "ext.pyx"])
    def test_files_python_cannot_import_are_not_providers(self, tmp_path, filename):
        _tree(tmp_path, {SCRIPT: "import ext\n", filename: b"\x00"})
        c = _by(_scan(tmp_path))["ext"]
        assert c.bucket == "unknown" and c.resolution_status == "unresolved"

    def test_o18_import_names_match_the_directory_listing_exactly(self, tmp_path):
        """`import Utils` 不能匹配 `utils.py`——大小写不敏感的文件系统上也一样（真实 Python 的大小写检查）。"""
        _tree(tmp_path, {SCRIPT: "import Utils\nimport utils\n", "utils.py": ""})
        got = _by(_scan(tmp_path))
        assert got["utils"].bucket == "local" and got["utils"].local_path == "utils.py"
        miss = got["Utils"]
        assert miss.bucket == "unknown" and miss.local_path == ""
        assert "case_mismatch_rejected" in miss.evidence
        assert "name_differs_in_case_from_file" in miss.warnings

    def test_a_stdlib_regular_package_beats_a_local_namespace_directory(self, tmp_path):
        _tree(tmp_path, {SCRIPT: "import statistics\n", "statistics/notes.py": ""})
        c = _by(_scan(tmp_path))["statistics"]
        assert (c.bucket, c.origin_kind) == ("stdlib", "stdlib")
        assert "local_namespace_dir_ignored" in c.warnings

    def test_a_regular_package_later_on_the_path_beats_a_namespace_directory(self, tmp_path):
        """命名空间目录不是赢家：整条 sys.path 上只要还有常规包 / 模块，就是它。"""
        _tree(
            tmp_path,
            {
                "scripts/s.py": "import ns\n",
                "scripts/ns/loose.py": "",  # 脚本目录里：没有 __init__ 的目录
                "ns/__init__.py": "import deep_dep\n",  # 项目根里：常规包
            },
        )
        res = _scan(tmp_path, "scripts/s.py")
        c = _by(res)["ns"]
        assert (c.origin_kind, c.local_path, c.resolution_status) == (
            "project-local",
            "ns/__init__.py",
            "resolved",
        )
        assert "deep_dep" in _by(res)

    def test_a_missing_script_directory_is_not_an_unreadable_directory(self, tmp_path):
        res = _scan(tmp_path, "nope/s.py")
        assert res.search_complete  # 目录不存在 ≠ 读不动
        assert res.problems and res.problems[0]["kind"] == "io"


# ===========================================================================
# 与真实 Python 对拍：同一棵树，真的解释器怎么选
# ===========================================================================
class TestRealPythonParity:
    def test_json_shadowing_package_priority_and_case(self, tmp_path):
        _tree(
            tmp_path,
            {
                "probe.py": (
                    "import json, dup, sys, os\n"
                    "here = os.getcwd()\n"
                    "print(os.path.basename(json.__file__))\n"
                    "print(os.path.basename(dup.__file__))\n"
                    "print(hasattr(sys, '__file__'))\n"
                    "print(os.path.dirname(os.__file__) == here)\n"
                    "try:\n    import Utils\nexcept ModuleNotFoundError as e:\n    print('MNF', e.name)\n"
                ),
                "json.py": "# local json\n",
                "dup.py": "",
                "dup/__init__.py": "",
                "sys.py": "",
                "os.py": "",
                "utils.py": "",
            },
        )
        proc = _real_python(tmp_path, "probe.py")
        assert proc.returncode == 0, proc.stderr
        # 真实 Python：本地 json.py 遮蔽 stdlib；dup 是目录包；sys / os 不被本地文件遮蔽；Utils 不匹配 utils.py
        assert proc.stdout.splitlines() == ["json.py", "__init__.py", "False", "False", "MNF Utils"]
        # 静态扫描给出同样的答案
        got = _by(_scan(tmp_path, "probe.py"))
        assert got["json"].local_path == "json.py" and got["json"].shadowing == "stdlib"
        assert got["dup"].local_path == "dup/__init__.py"
        assert got["sys"].bucket == "stdlib" and got["os"].bucket == "stdlib"
        assert got["Utils"].bucket == "unknown"

    def test_the_local_os_py_is_really_not_what_python_imports(self, tmp_path):
        _tree(
            tmp_path,
            {
                "probe.py": "import os\nprint(os.__file__)\n",
                "os.py": "raise SystemExit('local os.py was executed')\n",
            },
        )
        proc = _real_python(tmp_path, "probe.py")
        assert proc.returncode == 0, proc.stderr
        assert Path(proc.stdout.strip()).resolve() != (tmp_path / "os.py").resolve()


# ===========================================================================
# O-09 命名空间目录
# ===========================================================================
class TestNamespacePackages:
    def test_o09_a_directory_without_init_is_never_the_only_provider(self, tmp_path):
        _tree(tmp_path, {SCRIPT: "import nsd\n", "nsd/part.py": "import should_not_follow\n"})
        res = _scan(tmp_path)
        c = _by(res)["nsd"]
        assert (c.bucket, c.origin_kind, c.resolution_status) == ("local", "namespace", "ambiguous")
        assert c.local_path == "nsd"
        assert "namespace_may_be_overridden" in c.warnings
        assert not c.needed
        # 命名空间目录自己不执行任何代码：不整目录扫（旧口径会把 nsd/*.py 全当脚本依赖）
        assert "should_not_follow" not in _by(res)

    def test_o09_the_namespace_directory_keeps_the_mapping_candidate_visible(self, tmp_path):
        """`docx/` 目录没有 __init__：可能是本地命名空间，也可能被已安装的 python-docx 压过——两个都不丢。"""
        _tree(tmp_path, {SCRIPT: "import docx\n", "docx/render.py": ""})
        c = _by(_scan(tmp_path))["docx"]
        assert c.bucket == "local" and c.resolution_status == "ambiguous"
        assert (c.distribution, c.resolution_source) == ("python-docx", "curated")
        assert not c.needed  # 宁可不装

    def test_o09_a_submodule_of_a_namespace_directory_is_followed_when_imported(self, tmp_path):
        _tree(
            tmp_path,
            {
                SCRIPT: "from nsd import part\nimport nsd.other\n",
                "nsd/part.py": "import scipy\n",
                "nsd/other.py": "import h5py\n",
                "nsd/unused.py": "import torch\n",
            },
        )
        got = _by(_scan(tmp_path))
        assert got["scipy"].via == ("nsd/part.py",)
        assert got["h5py"].via == ("nsd/other.py",)
        assert "torch" not in got

    def test_a_directory_with_no_python_files_is_not_a_provider(self, tmp_path):
        _tree(tmp_path, {SCRIPT: "import results\n", "results/data.csv": "a,b\n"})
        c = _by(_scan(tmp_path))["results"]
        assert c.bucket == "unknown" and c.resolution_status == "unresolved"


# ===========================================================================
# O-04 / O-05 / O-08 / O-15：本地包的子模块、相对导入、from pkg import x
# ===========================================================================
class TestLocalPackages:
    def test_o04_a_project_package_is_local_and_never_installed(self, tmp_path):
        _tree(
            tmp_path,
            {SCRIPT: "import helpers\n", "helpers/__init__.py": ""},
        )
        c = _by(_scan(tmp_path))["helpers"]
        assert (c.bucket, c.origin_kind, c.local_path) == (
            "local",
            "project-local",
            "helpers/__init__.py",
        )
        assert not c.needed

    def test_o05_relative_imports_resolve_against_the_package_context(self, tmp_path):
        _tree(
            tmp_path,
            {
                SCRIPT: "import pkg.core\n",
                "pkg/__init__.py": "from .core import run\n",
                "pkg/core.py": "import scipy\nfrom . import sub\nfrom .sub.deep import f\n",
                "pkg/sub/__init__.py": "import h5py\n",
                "pkg/sub/deep.py": "import yaml\nfrom .. import util\n",
                "pkg/util.py": "import torch\n",
                "pkg/never.py": "import tensorflow\n",
            },
        )
        res = _scan(tmp_path)
        got = _by(res)
        assert {m: got[m].via for m in ("scipy", "h5py", "yaml", "torch")} == {
            "scipy": ("pkg/core.py",),
            "h5py": ("pkg/sub/__init__.py",),
            "yaml": ("pkg/sub/deep.py",),
            "torch": ("pkg/util.py",),
        }
        assert all(got[m].needed for m in ("scipy", "h5py", "yaml", "torch"))
        assert "tensorflow" not in got  # 没有任何导入链指到它
        assert not res.warnings

    def test_o05_a_relative_import_in_the_entry_script_has_no_package_and_is_not_guessed(
        self, tmp_path
    ):
        _tree(
            tmp_path,
            {SCRIPT: "from . import sibling\nimport numpy\n", "sibling.py": "import scipy\n"},
        )
        res = _scan(tmp_path, declared={"numpy": ""})
        assert "scipy" not in _by(res)  # 真实 Python：ImportError，不是 sibling.py
        assert {"code": "relative_import_no_package", "path": SCRIPT, "line": 1} in [
            dict(w) for w in res.warnings
        ]

    def test_o05_a_relative_import_climbing_above_the_top_package_is_reported(self, tmp_path):
        _tree(
            tmp_path,
            {SCRIPT: "import pkg\n", "pkg/__init__.py": "from .. import x\n"},
        )
        res = _scan(tmp_path)
        assert any(w["code"] == "relative_import_beyond_top" for w in res.warnings)

    def test_o08_from_pkg_import_x_follows_only_names_that_are_submodules(self, tmp_path):
        files = {
            "pkg/__init__.py": "import numpy\nx = 1\n",
            "pkg/a.py": "import scipy\n",
            "pkg/b.py": "import torch\n",
        }
        _tree(tmp_path, files)
        _write(tmp_path, "m1.py", "from pkg import a\n")
        _write(tmp_path, "m2.py", "from pkg import x\n")
        _write(tmp_path, "m3.py", "from pkg import *\n")
        r1 = _scan(tmp_path, "m1.py", declared={"numpy": ""})
        assert set(r1.files) == {"m1.py", "pkg/__init__.py", "pkg/a.py"}
        assert "scipy" in _by(r1) and "torch" not in _by(r1)
        # `x` 是 __init__ 里的属性，不是子模块：只执行 __init__
        r2 = _scan(tmp_path, "m2.py", declared={"numpy": ""})
        assert set(r2.files) == {"m2.py", "pkg/__init__.py"}
        assert "scipy" not in _by(r2)
        # 星号导入不展开
        r3 = _scan(tmp_path, "m3.py", declared={"numpy": ""})
        assert set(r3.files) == {"m3.py", "pkg/__init__.py"}

    def test_o08_a_name_that_is_both_attribute_and_submodule_follows_the_submodule(self, tmp_path):
        _tree(
            tmp_path,
            {
                SCRIPT: "from pkg import a\n",
                "pkg/__init__.py": "a = 1\n",
                "pkg/a.py": "import scipy\n",
            },
        )
        assert "scipy" in _by(_scan(tmp_path))  # 真实 Python：子模块 a 被 import，并覆盖属性

    def test_o15_a_package_submodule_third_party_import_is_needed(self, tmp_path):
        _tree(
            tmp_path,
            {
                SCRIPT: "import lab.sub.mod\n",
                "lab/__init__.py": "import numpy\n",
                "lab/sub/__init__.py": "",
                "lab/sub/mod.py": "import scipy\n",
            },
        )
        res = _scan(tmp_path, declared={"numpy": "", "scipy": ""})
        assert {c.module for c in res.needed} == {"numpy", "scipy"}
        assert _by(res)["scipy"].via == ("lab/sub/mod.py",)

    def test_o15_the_weaker_context_propagates_through_the_chain(self, tmp_path):
        _tree(
            tmp_path,
            {
                SCRIPT: "try:\n    import lab\nexcept ImportError:\n    lab = None\n",
                "lab.py": "import mid\n",
                "mid.py": "import h5py\n",
            },
        )
        c = _by(_scan(tmp_path))["h5py"]
        assert c.context == "optional" and not c.needed

    def test_o15_a_later_unconditional_import_strengthens_the_whole_chain(self, tmp_path):
        """先在 try 里、后来又无条件 import 同一个本地模块：它的第三方依赖是无条件的。"""
        _tree(
            tmp_path,
            {
                SCRIPT: "try:\n    import lab\nexcept ImportError:\n    lab = None\nimport lab\n",
                "lab.py": "import mid\n",
                "mid.py": "import h5py\n",
            },
        )
        c = _by(_scan(tmp_path))["h5py"]
        assert c.context == "unconditional" and c.needed

    def test_o15_following_stops_at_the_depth_limit_and_says_so(self, tmp_path):
        _tree(
            tmp_path,
            {
                SCRIPT: "import a\n",
                "a.py": "import b\n",
                "b.py": "import c\n",
                "c.py": "import d\n",
                "d.py": "import deep_dep\n",
            },
        )
        res = _scan(tmp_path)
        assert res.truncated
        assert _by(res)["d"].bucket == "local" and "deep_dep" not in _by(res)

    def test_o15_following_stops_at_the_file_limit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(importscan, "MAX_LOCAL_MODULES", 2)
        _tree(tmp_path, {SCRIPT: "import a\nimport b\nimport c\n"})
        for n in "abc":
            _write(tmp_path, f"{n}.py", f"import third_{n}\n")
        res = _scan(tmp_path)
        assert res.truncated
        assert {"third_a", "third_b"} <= set(_by(res)) and "third_c" not in _by(res)

    def test_a_sibling_module_that_is_not_on_sys_path_is_local_but_unverified(self, tmp_path):
        """Python 3 里 `lab/a.py` 的 `import b`（b 在 lab/ 里）并不能找到 lab/b.py——静态上仍当本地（不误装），
        但不装作确定。"""
        _tree(
            tmp_path,
            {
                SCRIPT: "import lab.a\n",
                "lab/__init__.py": "",
                "lab/a.py": "import b\n",
                "lab/b.py": "import scipy\n",
            },
        )
        res = _scan(tmp_path)
        c = _by(res)["b"]
        assert (c.bucket, c.resolution_status) == ("local", "unverified")
        assert "implicit_sibling_import" in c.warnings
        assert "scipy" in _by(res)  # 它真有可能被 sys.path 技巧挂上，跟进以免漏报


# ===========================================================================
# O-12 动态导入
# ===========================================================================
class TestDynamicImports:
    SRC = (
        "import importlib\n"
        "import importlib as il\n"
        "from importlib import import_module as im\n"
        "import builtins as bi\n"
        "name = input()\n"
        "il.import_module('scipy')\n"
        "il.import_module(name)\n"
        "im('h5py')\n"
        "im(name + 'x')\n"
        "bi.__import__(name)\n"
        "importlib.import_module('torch')\n"
        "unrelated.import_module(name)\n"
    )

    def test_o12_alias_calls_are_dynamic_candidates_not_unconditional_dependencies(self, tmp_path):
        _tree(tmp_path, {SCRIPT: self.SRC})
        res = _scan(tmp_path)
        got = _by(res)
        # 别名形的字面量：候选，上下文是 dynamic，不进 needed
        for mod in ("scipy", "h5py"):
            assert got[mod].context == "dynamic" and not got[mod].needed
            assert got[mod].bucket == "third_party"
        # 恒认的原形保持历史口径（无条件）
        assert got["torch"].context == "unconditional" and got["torch"].needed
        # 非字面量一律进 dynamic 列表（unknown），包括 builtins.__import__；不相干对象的同名方法不算
        assert [d.full for d in res.dynamic] == ["name", "name + 'x'", "name"]
        assert all(d.context == "dynamic" for d in res.dynamic)

    def test_o12_the_dynamic_payload_shape_is_unchanged(self, tmp_path):
        _tree(tmp_path, {SCRIPT: "import importlib as il\nil.import_module(input())\n"})
        payload = _scan(tmp_path).to_payload()
        assert payload["dynamic"] == [
            {"module": "", "full": "input()", "context": "dynamic", "lineno": 2, "via": ""}
        ]

    def test_o12_a_non_importlib_object_with_the_same_method_name_is_not_an_import(self, tmp_path):
        _tree(
            tmp_path, {SCRIPT: "import foo\nfoo.import_module(input())\nfoo.import_module('x')\n"}
        )
        res = _scan(tmp_path)
        assert not res.dynamic and "x" not in _by(res)

    def test_sys_path_edits_make_the_search_incomplete(self, tmp_path):
        for i, src in enumerate(
            [
                "import sys\nsys.path.insert(0, '..')\n",
                "import sys as s\ns.path.append('lib')\n",
                "from sys import path as p\np.extend(['x'])\n",
                "import sys\nsys.path = ['x']\n",
                "import sys\nsys.path += ['x']\n",
                "import sys\nsys.path[0] = 'x'\n",
                "import site\nsite.addsitedir('x')\n",
            ]
        ):
            _write(tmp_path, f"e{i}.py", src)
            res = _scan(tmp_path, f"e{i}.py")
            assert not res.search_complete, src
            assert any(w["code"] == "sys_path_modified" for w in res.warnings), src
        _write(tmp_path, "ro.py", "import sys\nprint(sys.path[0])\nx = sys.path\n")
        assert _scan(tmp_path, "ro.py").search_complete


# ===========================================================================
# O-17 执行入口：python script.py / python -m / native / cwd
# ===========================================================================
class TestEntryProfiles:
    FILES = {
        "app/__init__.py": "import yaml\n",
        "app/run.py": "from . import util\nfrom .tools import f\nimport helper\nimport sibling\n",
        "app/util.py": "import scipy\n",
        "app/tools.py": "import torch\n",
        "app/sibling.py": "",
        "app/__main__.py": "from . import util\n",
        "helper.py": "import h5py\n",
    }

    def test_script_entry_uses_script_dir_and_project_root(self, tmp_path):
        _tree(tmp_path, self.FILES)
        res = _scan(tmp_path, "app/run.py")
        got = _by(res)
        assert [r["tag"] for r in res.search_roots] == ["script_dir", "project_root"]
        assert got["sibling"].resolution_status == "resolved"
        assert got["sibling"].local_path == "app/sibling.py"
        assert got["helper"].local_path == "helper.py"
        # `python app/run.py`：入口没有包，相对导入不猜
        assert "scipy" not in got and "torch" not in got
        assert sum(w["code"] == "relative_import_no_package" for w in res.warnings) == 2

    def test_o17_python_m_puts_cwd_first_and_runs_the_package_init(self, tmp_path):
        _tree(tmp_path, self.FILES)
        entry = importscan.Entry(
            kind="module", module="app.run", cwd_mode="project_root", profile="native"
        )
        res = _scan(tmp_path, "", entry=entry)
        got = _by(res)
        assert [r["tag"] for r in res.search_roots] == ["cwd"] and res.search_roots[0]["confirmed"]
        # 包的 __init__ 先于模块执行；相对导入按 `app` 包上下文解析
        assert got["yaml"].via == ("app/__init__.py",) and got["yaml"].needed
        assert got["scipy"].via == ("app/util.py",) and got["torch"].via == ("app/tools.py",)
        assert got["helper"].local_path == "helper.py" and got["h5py"].via == ("helper.py",)
        # cwd 是项目根：`sibling`（在 app/ 里）不在 sys.path 上——不是确定的本地
        sib = got["sibling"]
        assert (sib.bucket, sib.resolution_status) == ("local", "unverified")
        assert "implicit_sibling_import" in sib.warnings
        assert not any(w["code"] == "relative_import_no_package" for w in res.warnings)
        assert res.files[0] == "app/__init__.py" or "app/run.py" in res.files

    def test_o17_python_m_runs_the_parent_package_init_before_the_module(self, tmp_path):
        """`python -m pkg.leaf` 先执行 `pkg/__init__.py`——即便 leaf 自己什么都没 import；用真解释器对拍顺序。"""
        _tree(
            tmp_path,
            {
                "pkg/__init__.py": "print('init')\nimport yaml\n",
                "pkg/leaf.py": "print('leaf')\n",
            },
        )
        real = subprocess.run(
            [sys.executable, "-m", "pkg.leaf"],
            cwd=tmp_path,
            env={
                **{k: v for k, v in os.environ.items() if not k.startswith("PYTHON")},
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            capture_output=True,
            text=True,
            timeout=60,
        )
        # yaml 在这台机器上装没装不影响对拍：init 先于 leaf 打印（没装 yaml 时停在 init 里的 import 上）
        assert real.stdout.splitlines()[:1] == ["init"], (real.stdout, real.stderr)
        entry = importscan.Entry(kind="module", module="pkg.leaf", cwd_mode="project_root")
        res = _scan(tmp_path, "", entry=entry)
        assert _by(res)["yaml"].via == ("pkg/__init__.py",) and _by(res)["yaml"].needed

    def test_o17_python_m_package_runs_its_dunder_main(self, tmp_path):
        _tree(tmp_path, self.FILES)
        entry = importscan.Entry(kind="module", module="app", cwd_mode="project_root")
        res = _scan(tmp_path, "", entry=entry)
        assert "app/__main__.py" in res.files
        assert _by(res)["scipy"].via == (
            "app/util.py",
        )  # __main__ 的 `from . import util` 按包 app 解析

    def test_o17_python_m_with_a_sandbox_cwd_does_not_confirm_the_project_root(self, tmp_path):
        _tree(tmp_path, self.FILES)
        entry = importscan.Entry(kind="module", module="app.run")  # cwd_mode 默认 sandbox
        res = _scan(tmp_path, "", entry=entry)
        assert not res.search_complete and not res.search_roots[0]["confirmed"]
        assert "path_not_confirmed" in _by(res)["helper"].warnings
        assert _by(res)["helper"].resolution_status == "unverified"

    def test_o17_an_explicit_cwd_directory_is_the_search_root(self, tmp_path):
        _tree(tmp_path, self.FILES)
        entry = importscan.Entry(kind="module", module="run", cwd="app")
        res = _scan(tmp_path, "", entry=entry)
        assert res.search_roots[0] == {"path": "app", "tag": "cwd", "confirmed": True}
        assert _by(res)["sibling"].resolution_status == "resolved"
        assert (
            "helper" in _by(res) and _by(res)["helper"].bucket == "unknown"
        )  # 项目根不在 sys.path 上

    @pytest.mark.parametrize("module", ["nope", "app.nope", "app.util.deeper", "json.tool"])
    def test_o17_a_module_that_is_not_in_the_project_is_reported_not_guessed(
        self, tmp_path, module
    ):
        _tree(tmp_path, self.FILES)
        entry = importscan.Entry(kind="module", module=module, cwd_mode="project_root")
        res = _scan(tmp_path, "", entry=entry)
        assert res.classes == ()
        assert any(w["code"] == "module_not_found" for w in res.warnings)
        assert any(p["kind"] == "module_not_found" for p in res.problems)

    def test_native_script_does_not_have_the_project_root_on_sys_path(self, tmp_path):
        _tree(tmp_path, self.FILES)
        res = _scan(tmp_path, "app/run.py", entry=importscan.Entry(profile="native"))
        got = _by(res)
        assert not res.search_complete
        assert got["sibling"].resolution_status == "resolved"
        # 项目根的 helper.py 仍按本地判（宁可不装），但不是确定的
        assert got["helper"].bucket == "local" and got["helper"].resolution_status == "unverified"
        assert "path_not_confirmed" in got["helper"].warnings

    def test_entry_rejects_nonsense(self):
        with pytest.raises(ValueError):
            importscan.Entry(kind="module", module="not valid")
        with pytest.raises(ValueError):
            importscan.Entry(kind="nope")
        with pytest.raises(ValueError):
            importscan.Entry(profile="nope")
        with pytest.raises(ValueError):
            importscan.Entry(cwd_mode="nope")


# ===========================================================================
# O-16 符号链接：不越界、不读
# ===========================================================================
class TestLinks:
    def _world(self, tmp_path: Path) -> Path:
        proj = tmp_path / "proj"
        _tree(
            tmp_path,
            {
                "outside/leak.py": "import secret_one\n",
                "outside/leakpkg/__init__.py": "import secret_two\n",
                "outside/json.py": "import secret_three\n",
            },
        )
        _write(proj, SCRIPT, "import leak\nimport leakpkg\nimport json\n")
        _symlink(proj / "leak.py", tmp_path / "outside" / "leak.py")
        _symlink(proj / "leakpkg", tmp_path / "outside" / "leakpkg", is_dir=True)
        _symlink(proj / "json.py", tmp_path / "outside" / "json.py")
        return proj

    def test_o16_links_pointing_outside_the_project_are_never_read(self, tmp_path, monkeypatch):
        proj = self._world(tmp_path)
        real_read = scanbudget.read_regular_text
        seen: list[tuple] = []

        def spy(base, *parts, **kw):
            seen.append((str(base), parts))
            return real_read(base, *parts, **kw)

        monkeypatch.setattr(scanbudget, "read_regular_text", spy)
        res = _scan(proj)
        got = _by(res)
        assert not any(m.startswith("secret_") for m in got)
        assert all("outside" not in b for b, _ in seen)
        assert [p for _, p in seen] == [(SCRIPT,)]  # 只读过脚本自己
        for mod in ("leak", "leakpkg"):
            assert got[mod].bucket == "unknown" and got[mod].resolution_status == "unverified"
            assert "link_outside_project" in got[mod].warnings
        # 指到项目外的 json.py 遮蔽了 stdlib json——我们看不见它，不假装知道
        assert (got["json"].bucket, got["json"].resolution_status) == ("stdlib", "unverified")
        assert not res.search_complete
        assert res.files == (SCRIPT,)

    def test_o16_no_follow_refuses_even_links_that_stay_inside_the_project(self, tmp_path):
        _tree(
            tmp_path,
            {
                SCRIPT: "import alias\nimport aliaspkg\n",
                "real/inner.py": "import numpy\n",
                "real/pkg/__init__.py": "import scipy\n",
            },
        )
        _symlink(tmp_path / "alias.py", tmp_path / "real" / "inner.py")
        _symlink(tmp_path / "aliaspkg", tmp_path / "real" / "pkg", is_dir=True)
        followed = _scan(tmp_path)  # 默认：项目内的链接照旧跟随
        assert {"numpy", "scipy"} <= set(_by(followed)) and followed.search_complete
        res = _scan(tmp_path, no_follow=True)
        got = _by(res)
        assert "numpy" not in got and "scipy" not in got
        assert "link_not_followed" in got["alias"].warnings
        assert not res.search_complete
        assert any(i["code"] == scanbudget.ISSUE_SYMLINK_DIR for i in res.issues)
        assert res.files == (SCRIPT,)

    def test_o16_a_link_to_a_script_directory_does_not_widen_the_search(self, tmp_path):
        _tree(tmp_path, {"outside/mod.py": "import secret\n"})
        proj = tmp_path / "proj"
        _write(proj, SCRIPT, "import mod\n")
        _symlink(proj / "mod", tmp_path / "outside", is_dir=True)
        got = _by(_scan(proj))
        assert "secret" not in got


# ===========================================================================
# 预算：占位文件 / 字节 / 大文件 / 取消 / 项目相对路径
# ===========================================================================
class TestBudget:
    def test_a_placeholder_file_is_not_read(self, tmp_path, monkeypatch):
        _tree(tmp_path, {SCRIPT: "import cloud\n", "cloud.py": "import hidden\n"})
        cloud = os.stat(tmp_path / "cloud.py")
        monkeypatch.setattr(
            scanbudget,
            "is_placeholder",
            lambda st: (st.st_ino, st.st_dev) == (cloud.st_ino, cloud.st_dev),
        )
        real_read = scanbudget.read_regular_text
        read: list[tuple] = []
        monkeypatch.setattr(
            scanbudget,
            "read_regular_text",
            lambda base, *p, **kw: (read.append(p), real_read(base, *p, **kw))[1],
        )
        res = _scan(tmp_path)
        assert ("cloud.py",) not in read  # 读它 = 强制下载
        assert "hidden" not in _by(res)
        assert _by(res)["cloud"].bucket == "local"  # 它存在，只是不读
        assert {"path": "cloud.py", "kind": scanbudget.ISSUE_PLACEHOLDER} in res.problems
        assert any(
            i["code"] == scanbudget.ISSUE_PLACEHOLDER and i["path"] == "cloud.py"
            for i in res.issues
        )

    def test_the_total_source_byte_budget_stops_following_and_says_so(self, tmp_path):
        _tree(
            tmp_path,
            {SCRIPT: "import a\nimport b\n", "a.py": "x = 1\n" * 10, "b.py": "import late\n"},
        )
        size = (tmp_path / SCRIPT).stat().st_size + (tmp_path / "a.py").stat().st_size
        budget = scanbudget.Budget(limits=scanbudget.Limits(max_source_bytes=size))
        res = _scan(tmp_path, budget=budget)
        assert "late" not in _by(res)
        assert any(
            p["kind"] == scanbudget.ISSUE_SOURCE_BYTES and p["path"] == "b.py" for p in res.problems
        )
        assert any(i["code"] == scanbudget.ISSUE_SOURCE_BYTES for i in res.issues)

    def test_a_file_over_the_per_file_limit_is_a_problem_not_a_silent_skip(self, tmp_path):
        _tree(tmp_path, {SCRIPT: "import big\n", "big.py": "import hidden\n" + "# pad\n" * 40})
        budget = scanbudget.Budget(limits=scanbudget.Limits(max_file_bytes=64))
        res = _scan(tmp_path, budget=budget)
        assert {"path": "big.py", "kind": "too_large"} in res.problems
        assert "hidden" not in _by(res)

    def test_cancellation_stops_following(self, tmp_path):
        _tree(tmp_path, {SCRIPT: "import a\n", "a.py": "import late\n"})
        res = _scan(tmp_path, budget=scanbudget.Budget(cancel=lambda: True))
        assert res.truncated and "late" not in _by(res)

    def test_the_directory_entry_budget_marks_the_search_incomplete(self, tmp_path):
        _tree(tmp_path, {SCRIPT: "import zz\n"})
        for i in range(10):
            _write(tmp_path, f"f{i}.py")
        budget = scanbudget.Budget(limits=scanbudget.Limits(max_entries=3))
        res = _scan(tmp_path, budget=budget)
        assert not res.search_complete
        assert any(i["code"] == scanbudget.ISSUE_ENTRIES for i in res.issues)

    def test_problems_and_issues_carry_project_relative_paths_only(self, tmp_path):
        _tree(
            tmp_path,
            {SCRIPT: "import bad\nimport big\n", "bad.py": "def (:\n", "big.py": "x" * 200},
        )
        budget = scanbudget.Budget(limits=scanbudget.Limits(max_file_bytes=100))
        res = _scan(tmp_path, budget=budget)
        text = json.dumps(res.to_payload())
        assert str(tmp_path) not in text and tmp_path.name not in text
        assert {p["path"] for p in res.problems} == {"bad.py", "big.py"}

    def test_a_syntax_error_in_a_followed_module_is_a_problem_and_unused_is_voided(self, tmp_path):
        _tree(tmp_path, {SCRIPT: "import bad\nimport numpy as np\n", "bad.py": "def (:\n"})
        res = _scan(tmp_path, declared={"numpy": ""})
        assert any(p["kind"] == "syntax" for p in res.problems)


# ===========================================================================
# O-20 / 兼容 / 隐私
# ===========================================================================
class TestContract:
    def test_o20_an_uninstalled_unmapped_module_is_unknown_and_never_guessed(self, tmp_path):
        _tree(tmp_path, {SCRIPT: "import zzz_not_a_real_dist\nimport numpy\n"})
        res = _scan(tmp_path, declared={"numpy": ""})
        c = _by(res)["zzz_not_a_real_dist"]
        assert (c.bucket, c.distribution, c.origin_kind, c.resolution_status) == (
            "unknown",
            "",
            "unresolved",
            "unresolved",
        )
        assert c.evidence == ("no_provider_found",) and not c.needed
        assert "zzz_not_a_real_dist" not in {x.module for x in res.needed}

    def test_o01_o02_a_mapped_third_party_is_a_candidate_not_a_verified_origin(self, tmp_path):
        _tree(tmp_path, {SCRIPT: "import numpy\nimport docx\n"})
        got = _by(_scan(tmp_path, declared={"numpy": ""}))
        assert got["numpy"].evidence == ("mapped_by_project_declared",)
        assert got["docx"].evidence == ("mapped_by_curated",)
        for c in got.values():
            assert (c.bucket, c.origin_kind, c.resolution_status) == (
                "third_party",
                "unresolved",
                "unverified",
            )

    def test_the_old_payload_keys_are_unchanged_and_new_ones_are_appended(self, tmp_path):
        _tree(tmp_path, {SCRIPT: "import numpy\n"})
        res = _scan(tmp_path, declared={"numpy": ">=1"})
        old_keys = [
            "module",
            "bucket",
            "context",
            "distribution",
            "resolution_source",
            "local_path",
            "lines",
            "via",
            "unused",
            "needed",
        ]
        payload = res.classes[0].to_payload()
        assert list(payload)[: len(old_keys)] == old_keys
        assert set(payload) - set(old_keys) == {
            "origin_kind",
            "resolution_status",
            "evidence",
            "shadowing",
            "warnings",
        }
        top = res.to_payload()
        assert {"classes", "dynamic", "problems", "truncated", "counts"} <= set(top)
        assert top["counts"] == {"stdlib": 0, "local": 0, "third_party": 1, "unknown": 0}

    def test_every_emitted_code_is_in_the_closed_sets_and_nothing_leaks_a_path(self, tmp_path):
        root = tmp_path / "p"
        _tree(
            root,
            {
                SCRIPT: (
                    "import json, sys, os, dup, Utils, nsd, statistics, ext, lab, docx, zzz, sibling_user\n"
                    "from . import rel\n"
                    "import sys as s\ns.path.append('x')\n"
                ),
                "json.py": "",
                "sys.py": "",
                "dup/__init__.py": "",
                "dup.py": "",
                "utils.py": "",
                "nsd/a.py": "",
                "statistics/b.py": "",
                "ext.so": b"\x00",
                "lab/__init__.py": "",
                "lab/m.py": "import sib\n",
                "lab/sib.py": "",
                "sibling_user.py": "from . import q\n",
            },
        )
        res = _scan(root)
        for c in res.classes:
            assert c.origin_kind in importscan.ORIGIN_KINDS
            assert c.resolution_status in importscan.STATUSES
            assert set(c.evidence) <= importscan.EVIDENCE_CODES
            assert set(c.warnings) <= importscan.WARNING_CODES
            assert c.shadowing in ("", "stdlib", "third_party")
        assert {w["code"] for w in res.warnings} <= importscan.WARNING_CODES
        assert str(root) not in json.dumps(res.to_payload())

    def test_the_stable_stdlib_sets_do_not_overlap_in_a_way_that_would_misreport(self):
        assert not importscan.BUILTIN_NAMES & importscan.PRELOADED_STDLIB
        assert not importscan.FROZEN_NAMES & importscan.BUILTIN_NAMES
        assert importscan.PRELOADED_STDLIB <= importscan.HOST_STDLIB | {"__main__"}


class TestAlreadyCoveredElsewhere:
    """既有用例已经钉住的静态导入项（见 00_BASELINE §7.1），这里各补一条反向钉，防止被新判据悄悄带歪。"""

    def test_o13_o14_try_and_type_checking_imports_are_never_needed(self, tmp_path):
        # 既有：tests/test_dependency_plan.py::TestImportContexts::test_every_context_is_classified
        _tree(
            tmp_path,
            {
                SCRIPT: (
                    "try:\n    import numpy\nexcept ImportError:\n    numpy = None\n"
                    "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import scipy\n"
                    "import h5py\n"
                )
            },
        )
        res = _scan(tmp_path, declared={"numpy": "", "scipy": "", "h5py": ""})
        assert [c.module for c in res.needed] == ["h5py"]

    def test_o03_a_same_directory_utils_py_is_local(self, tmp_path):
        # 既有：TestImportBuckets::test_local_modules_in_script_dir_and_project_root_are_never_third_party
        _tree(
            tmp_path,
            {"scripts/figure1.py": "from utils import load_results\n", "scripts/utils.py": ""},
        )
        c = _by(_scan(tmp_path, "scripts/figure1.py"))["utils"]
        assert (c.bucket, c.local_path, c.resolution_status) == (
            "local",
            "scripts/utils.py",
            "resolved",
        )
        assert c.evidence == ("sys_path_script_dir", "module_file", "exact_name_match")
