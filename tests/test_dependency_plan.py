"""统一实施包 U04（ADR 0061）第一层：无损解析 → import 分类 → 联合计划（纯逻辑，不装任何东西）。

三个模块各一节：`depresolve` 的 intent 读法（成熟解析器、有界 include、PEP 723 / 735、
unsupported 闭集、3.10 无 tomllib 的分支）、`importscan`（stdlib / 本地 / 第三方 / 未知 ×
六种上下文、本地模块有界跟进）、`depplan`（目标事实、按组与 marker 选择、缺什么、交给安装器
的集合、blocked 的四种理由、身份不含路径）。负例多于正例：每一条「不装」都要有用例钉着。
"""

from __future__ import annotations

import builtins
import dataclasses
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

from tavotto.engine import depplan, depresolve, distmeta, importscan

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "foundation" / "dependency_declarations"

U = depresolve.INTENT_KIND_UNSUPPORTED
R = depresolve.INTENT_KIND_REQUIREMENT
C = depresolve.INTENT_KIND_CONSTRAINT
UNK = depresolve.INTENT_KIND_UNKNOWN


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def _kinds(intents):
    return [(i.kind, i.reason, i.name, i.raw) for i in intents]


# ===========================================================================
# depresolve：intent 的无损读法
# ===========================================================================
class TestIntentGrammar:
    @pytest.mark.parametrize(
        "line,name,spec,extras,marker,hashes",
        [
            ("six", "six", "", (), "", ()),
            ("Sorted_Containers>=2, <3", "sorted-containers", "<3,>=2", (), "", ()),
            (
                'tabulate[widechars,x]==0.9.0 ; python_version >= "3.8"',
                "tabulate",
                "==0.9.0",
                ("widechars", "x"),
                'python_version >= "3.8"',
                (),
            ),
            (
                "six==1.17.0 --hash=sha256:" + "a" * 64 + " --hash=sha256:" + "b" * 64,
                "six",
                "==1.17.0",
                (),
                "",
                ("sha256:" + "a" * 64, "sha256:" + "b" * 64),
            ),
            ("pkg===1.0", "pkg", "===1.0", (), "", ()),
            ("pkg~=1.4", "pkg", "~=1.4", (), "", ()),
        ],
    )
    def test_pep508_shapes_are_kept_losslessly(self, line, name, spec, extras, marker, hashes):
        it = depresolve.parse_intent(line)
        assert it.kind == R
        assert (it.name, it.specifier, it.extras, it.marker, it.hashes) == (
            name,
            spec,
            extras,
            marker,
            hashes,
        )
        assert it.raw == line

    @pytest.mark.parametrize(
        "line,reason",
        [
            ("-e .", "editable_install"),
            ("--editable ./pkg", "editable_install"),
            ("--index-url https://pypi.example/simple", "option_line"),
            ("-i https://pypi.example/simple", "option_line"),
            ("--find-links ./wheels", "option_line"),
            ("--pre", "option_line"),
            ("pkg @ git+https://github.com/x/y", "direct_url"),
            ("git+https://github.com/x/y#egg=pkg", "direct_url"),
            ("https://files.example/pkg-1.0-py3-none-any.whl", "direct_url"),
            ("./vendor/pkg", "local_path"),
            ("../sibling", "local_path"),
            ("/abs/path/pkg", "local_path"),
            ("dist/pkg-1.0-py3-none-any.whl", "local_path"),
            ("pkg-1.0.tar.gz", "local_path"),
            ("pkg==1.0 --global-option=x", "per_requirement_option"),
            ("pkg==1.0 --config-settings=a=b", "per_requirement_option"),
            ("${PRIVATE}==1.0", "environment_variable"),
        ],
    )
    def test_recognised_but_unsupported_constructs_are_named_not_dropped(self, line, reason):
        it = depresolve.parse_intent(line)
        assert it.kind == U, line
        assert it.reason == reason
        assert it.raw == line
        assert not it.declared

    @pytest.mark.parametrize("line", ["foo ^1.2", "-r other.txt", "--bogus-option", "a b c"])
    def test_truly_unparseable_lines_are_unknown(self, line):
        it = depresolve.parse_intent(line)
        assert it.kind == UNK and it.raw == line and not it.declared

    def test_bad_hash_shape_is_not_a_hash(self):
        it = depresolve.parse_intent("pkg==1 --hash=md5:abc")
        assert it.kind == U and it.reason == "per_requirement_option"

    def test_line_continuation_joins_logical_lines(self):
        text = "six==1.17.0 \\\n    --hash=sha256:" + "c" * 64 + "\n# comment \\\ntabulate\n"
        intents = depresolve.parse_intents_text(text)
        assert [(i.name, len(i.hashes)) for i in intents] == [("six", 1), ("tabulate", 0)]

    def test_requirement_string_is_reserialised_from_the_parsed_structure(self):
        it = depresolve.parse_intent('Tabulate[ WideChars , x ] >= 0.9 , <1 ; os_name == "nt"')
        assert depresolve.requirement_string(it) == "tabulate[widechars,x]<1,>=0.9"
        assert (
            depresolve.requirement_string(it, with_marker=True)
            == 'tabulate[widechars,x]<1,>=0.9; os_name == "nt"'
        )
        for bad in ("-e .", "foo ^1.2", "pkg @ https://x/y"):
            with pytest.raises(ValueError):
                depresolve.requirement_string(depresolve.parse_intent(bad))

    def test_intent_invariants(self):
        with pytest.raises(ValueError):
            depresolve.DependencyIntent(name="", kind=R)
        with pytest.raises(ValueError):
            depresolve.DependencyIntent(name="", kind=U, reason="made_up")
        with pytest.raises(ValueError):
            depresolve.DependencyIntent(name="x", kind=R, reason="direct_url")
        assert set(depresolve.UNSUPPORTED_REASONS) >= {"direct_url", "poetry_constraint"}


class TestBoundedIncludes:
    def test_nested_include_entries_belong_to_the_including_group(self, tmp_path):
        _write(tmp_path, "requirements.txt", "-r base.txt\nsix==1.17.0\n-c constraints/pins.txt\n")
        _write(tmp_path, "base.txt", "tabulate==0.9.0\n--requirement=deep/more.txt\n")
        _write(tmp_path, "deep/more.txt", "sortedcontainers\n")
        _write(tmp_path, "constraints/pins.txt", "numpy<2\n")
        intents = depresolve.declared_intents(tmp_path)
        assert _kinds(intents) == [
            (R, "", "tabulate", "tabulate==0.9.0"),
            (R, "", "sortedcontainers", "sortedcontainers"),
            (R, "", "six", "six==1.17.0"),
            (C, "", "numpy", "numpy<2"),
        ]
        assert {i.group for i in intents} == {"requirements.txt"}
        assert [i.source for i in intents] == [
            "base.txt",
            "deep/more.txt",
            "requirements.txt",
            "constraints/pins.txt",
        ]

    def test_cycle_missing_and_outside_are_each_named_in_place(self, tmp_path):
        outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
        outside.write_text("evil\n", encoding="utf-8")
        proj = tmp_path / "proj"
        _write(
            proj, "requirements.txt", "-r a.txt\n-r missing.txt\n-r ../{}\n".format(outside.name)
        )
        _write(proj, "a.txt", "-r b.txt\n")
        _write(proj, "b.txt", "-r a.txt\nsix\n")
        intents = depresolve.declared_intents(proj)
        assert _kinds(intents) == [
            (U, "include_cycle", "", "-r a.txt"),
            (R, "", "six", "six"),
            (U, "include_missing", "", "-r missing.txt"),
            (U, "include_outside_project", "", f"-r ../{outside.name}"),
        ]
        assert "evil" not in json.dumps([i.to_payload() for i in intents])

    def test_self_include_is_a_cycle(self, tmp_path):
        _write(tmp_path, "requirements.txt", "-r requirements.txt\nsix\n")
        intents = depresolve.declared_intents(tmp_path)
        assert _kinds(intents)[0] == (U, "include_cycle", "", "-r requirements.txt")

    @pytest.mark.skipif(os.name == "nt", reason="符号链接需要特权")
    def test_symlink_escaping_the_project_is_outside(self, tmp_path):
        target = tmp_path / "elsewhere.txt"
        target.write_text("evil\n", encoding="utf-8")
        proj = tmp_path / "proj"
        _write(proj, "requirements.txt", "-r link.txt\n")
        os.symlink(target, proj / "link.txt")
        intents = depresolve.declared_intents(proj)
        assert _kinds(intents) == [(U, "include_outside_project", "", "-r link.txt")]

    def test_file_limit_is_reported_not_silently_truncated(self, tmp_path):
        n = depresolve.MAX_DECL_FILES + 3
        _write(tmp_path, "requirements.txt", "-r r1.txt\n")
        for i in range(1, n):
            _write(tmp_path, f"r{i}.txt", f"pkg{i}\n-r r{i + 1}.txt\n")
        _write(tmp_path, f"r{n}.txt", "last\n")
        intents = depresolve.declared_intents(tmp_path)
        limited = [i for i in intents if i.kind == U and i.reason == "include_limit"]
        assert limited, "超限必须留下一条 unsupported"
        assert not any(i.name == "last" for i in intents)

    def test_diamond_includes_read_a_file_once(self, tmp_path):
        _write(tmp_path, "requirements.txt", "-r a.txt\n-r b.txt\n")
        _write(tmp_path, "a.txt", "-r common.txt\n")
        _write(tmp_path, "b.txt", "-r common.txt\n")
        _write(tmp_path, "common.txt", "six\n")
        intents = depresolve.declared_intents(tmp_path)
        assert _kinds(intents) == [(R, "", "six", "six")]

    def test_the_same_file_included_as_another_group_or_as_a_constraint_counts_again(
        self, tmp_path
    ):
        """Codex #459 P1：`requirements.txt -r common.txt` 之后 `constraints.txt -c common.txt`
        （或另一组再 include）不能因为「读过了」就少掉——那是另一组的条目 / 另一种 kind。"""
        _write(tmp_path, "requirements.txt", "-r common.txt\n")
        _write(tmp_path, "requirements-dev.txt", "-r common.txt\n")
        _write(tmp_path, "constraints.txt", "-c common.txt\n")
        _write(tmp_path, "common.txt", "six==1.17.0\n")
        intents = depresolve.declared_intents(tmp_path)
        assert [(i.kind, i.group, i.source) for i in intents] == [
            (R, "requirements.txt", "common.txt"),
            (R, "requirements-dev.txt", "common.txt"),
            (C, "constraints.txt", "common.txt"),
        ]

    def test_unreadable_or_oversized_declaration_files_are_unsupported_not_empty(
        self, tmp_path, monkeypatch
    ):
        """Codex #459 P2：读不了 / 超过上限的声明文件不是「没有依赖」。"""
        _write(tmp_path, "requirements.txt", "-r big.txt\nsix\n")
        _write(tmp_path, "big.txt", "x" * 64)
        _write(tmp_path, "pyproject.toml", "[project]\ndependencies = ['six']\n")
        monkeypatch.setattr(depresolve, "MAX_DECL_BYTES", 32)
        intents = depresolve.declared_intents(tmp_path)
        assert _kinds(intents) == [
            (U, "unreadable", "", "big.txt"),
            (R, "", "six", "six"),
            (U, "unreadable", "", "pyproject.toml"),
        ]
        assert intents[2].group == "pyproject.toml:project.dependencies"


class TestPyprojectAndPep723:
    def test_dependency_groups_with_include_group_are_expanded_into_the_outer_group(self, tmp_path):
        _write(
            tmp_path,
            "pyproject.toml",
            """
            [project]
            dependencies = ["six==1.17.0"]
            [project.optional-dependencies]
            plot = ["seaborn>=0.13"]
            [dependency-groups]
            test = ["pytest", {include-group = "lint"}]
            lint = ["ruff", {include-group = "test"}]
            train = [{include-group = "nope"}, 42]
            """,
        )
        intents = depresolve.declared_intents(tmp_path)
        by_group: dict[str, list] = {}
        for it in intents:
            by_group.setdefault(it.group, []).append((it.kind, it.reason, it.name or it.raw))
        assert by_group["pyproject.toml:project.dependencies"] == [(R, "", "six")]
        assert by_group["pyproject.toml:optional-dependencies.plot"] == [(R, "", "seaborn")]
        assert by_group["pyproject.toml:dependency-groups.test"] == [
            (R, "", "pytest"),
            (R, "", "ruff"),
            (U, "include_cycle", "{include-group = 'test'}"),
        ]
        assert by_group["pyproject.toml:dependency-groups.train"] == [
            (U, "include_missing", "{include-group = 'nope'}"),
            (UNK, "", "42"),
        ]

    def test_poetry_table_only_keeps_what_pep440_can_express(self, tmp_path):
        _write(
            tmp_path,
            "pyproject.toml",
            """
            [tool.poetry.dependencies]
            python = "^3.10"
            caret = "^1.2"
            tilde = "~1.2"
            compat = "~=1.4"
            exact = "2.13.0"
            range = ">=1,<2"
            star = "*"
            table = {version = "^1.0", optional = true}
            """,
        )
        intents = depresolve.declared_intents(tmp_path)
        assert _kinds(intents) == [
            (U, "poetry_constraint", "caret", "caret = '^1.2'"),
            (U, "poetry_constraint", "tilde", "tilde = '~1.2'"),
            (R, "", "compat", "compat = '~=1.4'"),
            (R, "", "exact", "exact = '2.13.0'"),
            (R, "", "range", "range = '>=1,<2'"),
            (R, "", "star", "star = '*'"),
            (U, "poetry_constraint", "table", "table = {'version': '^1.0', 'optional': True}"),
        ]
        assert [i.specifier for i in intents if i.kind == R] == ["~=1.4", "==2.13.0", "<2,>=1", ""]

    def test_pep723_block_is_the_scripts_own_group(self, tmp_path):
        _write(
            tmp_path,
            "plot.py",
            """
            # /// script
            # requires-python = ">=3.10"
            # dependencies = [
            #   "tabulate==0.9.0",
            #   "six; sys_platform == 'never'",
            # ]
            # ///
            import tabulate
            """,
        )
        intents = depresolve.declared_intents(tmp_path, "plot.py")
        assert [(i.group, i.name, i.marker) for i in intents] == [
            ("pep723:plot.py", "tabulate", ""),
            ("pep723:plot.py", "six", 'sys_platform == "never"'),
        ]
        assert depresolve.default_group("pep723:plot.py")

    def test_broken_pep723_toml_is_unsupported_not_empty(self, tmp_path):
        _write(tmp_path, "plot.py", "# /// script\n# dependencies = [\n# ///\n")
        intents = depresolve.declared_intents(tmp_path, "plot.py")
        assert _kinds(intents) == [(U, "toml_invalid", "", "# /// script")]

    def test_no_pep723_block_is_genuinely_nothing(self, tmp_path):
        _write(tmp_path, "plot.py", "import os\n")
        assert depresolve.declared_intents(tmp_path, "plot.py") == []

    def test_invalid_pyproject_is_unsupported_not_empty(self, tmp_path):
        _write(tmp_path, "pyproject.toml", "[project\ndependencies = [\n")
        assert _kinds(depresolve.declared_intents(tmp_path)) == [
            (U, "toml_invalid", "", "<pyproject.toml>")
        ]

    def test_manager_lock_files_are_named_as_unsupported(self, tmp_path):
        _write(tmp_path, "pixi.lock", "version: 5\n")
        _write(tmp_path, "poetry.lock", "# poetry\n")
        intents = depresolve.declared_intents(tmp_path)
        assert {(i.kind, i.reason, i.raw) for i in intents} == {
            (U, "manager_lock", "poetry.lock"),
            (U, "manager_lock", "pixi.lock"),
        }

    def test_without_a_toml_parser_pyproject_and_pep723_are_unsupported(
        self, tmp_path, monkeypatch
    ):
        """3.10 没有 tomllib（也没装 tomli）的分支：**不是没有依赖**。任何版本上都模拟一次。"""
        _write(tmp_path, "pyproject.toml", '[project]\ndependencies = ["six"]\n')
        _write(tmp_path, "plot.py", '# /// script\n# dependencies = ["six"]\n# ///\n')
        real_import = builtins.__import__

        def _no_toml(name, *args, **kwargs):
            if name in ("tomllib", "tomli"):
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _no_toml)
        intents = depresolve.declared_intents(tmp_path, "plot.py")
        assert _kinds(intents) == [
            (U, "toml_parser_unavailable", "", "# /// script"),
            (U, "toml_parser_unavailable", "", "<pyproject.toml>"),
        ]

    @pytest.mark.skipif(sys.version_info >= (3, 11), reason="3.10 才没有 tomllib")
    def test_on_python_310_the_branch_is_real(self, tmp_path):
        _write(tmp_path, "pyproject.toml", '[project]\ndependencies = ["six"]\n')
        intents = depresolve.declared_intents(tmp_path)
        try:
            import tomli  # noqa: F401

            assert _kinds(intents) == [(R, "", "six", "six")]
        except ImportError:
            assert _kinds(intents) == [(U, "toml_parser_unavailable", "", "<pyproject.toml>")]


class TestConflicts:
    @pytest.mark.parametrize(
        "a,b,expect",
        [
            ("six==1.16.0", "six==1.17.0", True),
            ("six==1.17.0", "six<1.17", True),
            ("six>=2", "six<1", True),
            ("six>=1", "six<3", False),
            ("six>=1.16", "six!=1.17", False),
            ("six==1.*", "six==1.2", False),  # 通配 pin 不判（交给安装器）
            ("six>=1", "six<=1", False),  # 闭区间的单点交集不是空（Codex #459 P2）
            ("six>=1", "six<1", True),
            ("six>1", "six<=1", True),
        ],
    )
    def test_only_definite_contradictions_are_reported(self, a, b, expect):
        intents = [depresolve.parse_intent(a), depresolve.parse_intent(b)]
        found = depresolve.conflicts(intents)
        assert bool(found) is expect
        if found:
            assert found[0]["name"] == "six" and found[0]["reasons"]

    def test_default_group_predicate(self):
        assert depresolve.default_group("requirements.txt")
        assert depresolve.default_group("scripts/requirements.txt")
        assert depresolve.default_group("pyproject.toml:project.dependencies")
        assert depresolve.default_group("pep723:a/b.py")
        assert not depresolve.default_group("requirements-dev.txt")
        assert not depresolve.default_group("requirements/train.txt")
        assert not depresolve.default_group("pyproject.toml:optional-dependencies.report")
        assert not depresolve.default_group("pyproject.toml:dependency-groups.test")
        assert not depresolve.default_group("constraints.txt")
        assert not depresolve.default_group("")


# ===========================================================================
# importscan：分类与上下文
# ===========================================================================
_CONTEXT_SCRIPT = """
import os
import numpy
from scipy import stats
import importlib
try:
    import optional_dep
except ImportError:
    import fallback_dep
if TYPE_CHECKING:
    import typing_only
else:
    import runtime_half
if os.name == "nt":
    import winonly
def f():
    import deferred_dep
class K:
    import class_dep
from contextlib import suppress
with suppress(ImportError):
    import suppressed_dep
with open("x") as fh:
    import with_dep
if __name__ == "__main__":
    import main_dep
importlib.import_module("literal_dep")
name = "x"
importlib.import_module(name)
__import__("dunder_dep")
__import__(name + "y")
from . import sibling
"""


class TestImportContexts:
    def test_every_context_is_classified(self, tmp_path):
        _write(tmp_path, "s.py", _CONTEXT_SCRIPT)
        res = importscan.scan(tmp_path, "s.py", declared={"numpy": "", "scipy": ""})
        ctx = {c.module: c.context for c in res.classes}
        assert ctx["os"] == "unconditional"
        assert ctx["numpy"] == "unconditional" and ctx["scipy"] == "unconditional"
        assert ctx["optional_dep"] == "optional"
        assert ctx["fallback_dep"] == "conditional"
        assert ctx["typing_only"] == "type_checking"
        assert ctx["runtime_half"] == "unconditional"
        assert ctx["winonly"] == "conditional"
        assert ctx["deferred_dep"] == "deferred" and ctx["class_dep"] == "deferred"
        assert ctx["suppressed_dep"] == "optional"
        assert ctx["with_dep"] == "unconditional"
        assert ctx["main_dep"] == "unconditional"
        assert ctx["literal_dep"] == "unconditional" and ctx["dunder_dep"] == "unconditional"
        assert "sibling" not in ctx  # 相对 import 不是依赖
        assert [d.full for d in res.dynamic] == ["name", "name + 'y'"]
        buckets = {c.module: c.bucket for c in res.classes}
        assert buckets["os"] == "stdlib" and buckets["contextlib"] == "stdlib"
        assert buckets["numpy"] == "third_party" and buckets["scipy"] == "third_party"
        assert buckets["optional_dep"] == "unknown"
        needed = {c.module for c in res.needed}
        assert needed == {"numpy", "scipy"}  # unknown 的不算 needed（映射不到就不装）

    def test_strongest_context_wins_when_a_name_is_imported_twice(self, tmp_path):
        _write(
            tmp_path,
            "s.py",
            "try:\n    import numpy\nexcept ImportError:\n    pass\nimport numpy\n",
        )
        res = importscan.scan(tmp_path, "s.py", declared={"numpy": ""})
        assert res.classes[0].context == "unconditional"


class TestImportBuckets:
    def test_local_modules_in_script_dir_and_project_root_are_never_third_party(self, tmp_path):
        _write(
            tmp_path,
            "scripts/plot.py",
            "import lab_utils\nimport helpers\nimport nspkg\nimport ext\nimport numpy\n",
        )
        _write(tmp_path, "scripts/lab_utils.py", "import h5py\n")
        _write(tmp_path, "helpers/__init__.py", "")
        _write(tmp_path, "nspkg/inner.py", "")
        (tmp_path / "scripts" / "ext.cpython-313-darwin.so").write_bytes(b"\x00")
        res = importscan.scan(tmp_path, "scripts/plot.py", declared={"numpy": "", "lab-utils": ""})
        got = {c.module: (c.bucket, c.local_path) for c in res.classes}
        assert got["lab_utils"] == ("local", "scripts/lab_utils.py")
        assert got["helpers"] == ("local", "helpers/__init__.py")
        assert got["nspkg"] == ("local", "nspkg")
        assert got["ext"] == ("local", "scripts/ext.cpython-313-darwin.so")
        assert got["numpy"][0] == "third_party"
        # 本地模块里的第三方 import 跟进到了，并且记了 via
        assert got["h5py"][0] == "third_party"
        h5 = next(c for c in res.classes if c.module == "h5py")
        assert h5.via == ("scripts/lab_utils.py",) and h5.needed
        # 项目声明了同名 `lab-utils`，本地模块仍然是本地（不装）
        assert "lab_utils" not in {c.module for c in res.needed}

    def test_context_through_a_local_module_is_the_weaker_of_the_two(self, tmp_path):
        _write(tmp_path, "s.py", "try:\n    import lab\nexcept ImportError:\n    lab = None\n")
        _write(tmp_path, "lab.py", "import h5py\n")
        res = importscan.scan(tmp_path, "s.py")
        h5 = next(c for c in res.classes if c.module == "h5py")
        assert h5.context == "optional" and not h5.needed

    def test_mapping_priority_declared_then_curated_then_unknown(self):
        assert importscan.map_distribution("PIL", {"pillow": ">=10"}) == (
            "pillow",
            "project_declared",
        )
        assert importscan.map_distribution("PIL", {}) == ("pillow", "curated")
        assert importscan.map_distribution("mylab_tools", {"mylab-tools": ""}) == (
            "mylab-tools",
            "project_declared",
        )
        assert importscan.map_distribution("mylab_tools", {}) == ("", "")
        assert importscan.map_distribution("not valid", {}) == ("", "")

    def test_target_stdlib_list_overrides_the_host(self, tmp_path):
        _write(tmp_path, "s.py", "import tomllib\nimport zoneinfo\n")
        res = importscan.scan(tmp_path, "s.py", stdlib=frozenset({"zoneinfo"}))
        got = {c.module: c.bucket for c in res.classes}
        assert got == {"tomllib": "unknown", "zoneinfo": "stdlib"}

    def test_local_follow_up_is_bounded_and_problems_are_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(importscan, "MAX_LOCAL_MODULES", 2)
        _write(tmp_path, "s.py", "import a\nimport b\nimport c\nimport bad\n")
        for name in "abc":
            _write(tmp_path, f"{name}.py", f"import third_{name}\n")
        _write(tmp_path, "bad.py", "def (:\n")
        res = importscan.scan(tmp_path, "s.py")
        assert res.truncated
        assert any(p["kind"] == "syntax" for p in res.problems) or res.truncated
        assert {c.bucket for c in res.classes if c.module in "abc"} == {"local"}

    def test_fixture_script_matches_its_truth(self):
        truth = json.loads((FIXTURE / "truth.json").read_text("utf-8"))["script_unknown_local.py"]
        declared = {
            i.name: i.specifier
            for i in depresolve.declared_intents(FIXTURE, "script_unknown_local.py")
            if i.declared
        }
        res = importscan.scan(FIXTURE, "script_unknown_local.py", declared=declared)
        got = {c.module: c.bucket for c in res.classes}
        assert got["labtools_local"] == "local"
        assert got["tabulate"] == "third_party"
        assert got["zzz_not_a_real_distribution_u00"] == "unknown"
        assert set(truth["imports"]) == {
            "labtools_local",
            "tabulate",
            "zzz_not_a_real_distribution_u00",
        }


# ===========================================================================
# depplan：目标事实、选择、计划
# ===========================================================================
def _facts(installed: dict | None = None, **env) -> depplan.TargetFacts:
    marker_env = {
        "implementation_name": "cpython",
        "implementation_version": "3.12.4",
        "os_name": "posix",
        "platform_machine": "arm64",
        "platform_release": "",
        "platform_system": "Darwin",
        "platform_version": "",
        "python_full_version": "3.12.4",
        "platform_python_implementation": "CPython",
        "python_version": "3.12",
        "sys_platform": "darwin",
        **env,
    }
    return depplan.TargetFacts(
        python="/fake/python",
        marker_env=marker_env,
        stdlib=importscan.HOST_STDLIB,
        installed=dict(installed or {}),
        prefix="/fake",
    )


class TestTargetFacts:
    def test_real_interpreter_facts(self):
        depplan.reset_cache()
        facts = depplan.target_facts(sys.executable)
        assert facts is not None
        assert facts.python_version == ".".join(
            str(x) for x in sys.version_info[:3]
        ) or facts.python_version.startswith(f"{sys.version_info[0]}.{sys.version_info[1]}")
        assert facts.marker_env["sys_platform"] == sys.platform
        assert "os" in facts.stdlib and "packaging" in facts.installed
        assert Path(facts.executable).exists()
        # 缓存：第二次是同一个对象；reset 后重新量
        assert depplan.target_facts(sys.executable) is facts
        depplan.reset_cache(sys.executable)
        assert depplan.target_facts(sys.executable) is not facts

    def test_unusable_interpreter_gives_none(self, tmp_path):
        assert depplan.target_facts(str(tmp_path / "nope")) is None

    def test_payload_has_no_path(self):
        payload = _facts({"six": "1.17.0"}).to_payload()
        assert "/fake" not in json.dumps(payload)
        assert payload["installed_count"] == 1 and payload["digest"]


class TestSelect:
    def test_markers_are_evaluated_against_the_target_not_the_host(self):
        intents = depresolve.parse_intents_text(
            'six==1.17.0; sys_platform == "win32"\ntabulate; python_version >= "3.12"\n'
            'sortedcontainers; python_version < "3.0"\n',
            group="requirements.txt",
            source="requirements.txt",
        )
        sel = depplan.select(intents, marker_env=_facts(sys_platform="win32").marker_env)
        assert [i.name for i in sel.requirements] == ["six", "tabulate"]
        assert [i.name for i in sel.skipped_marker] == ["sortedcontainers"]
        sel2 = depplan.select(intents, marker_env=_facts().marker_env)
        assert [i.name for i in sel2.requirements] == ["tabulate"]
        # 目标事实拿不到：marker 不求值，全部按选中（宁可多列让用户看见）
        sel3 = depplan.select(intents, marker_env=None)
        assert [i.name for i in sel3.requirements] == ["six", "tabulate", "sortedcontainers"]

    def test_only_default_and_named_groups_are_selected_constraints_always(self):
        intents = depresolve.declared_intents(FIXTURE)
        sel = depplan.select(intents, marker_env=_facts().marker_env)
        assert sel.selected_groups == ("pyproject.toml:project.dependencies", "requirements.txt")
        assert "requirements-extra.txt" in sel.unselected_groups
        assert [i.name for i in sel.constraints] == ["tabulate"]
        names = [(i.name, i.extras) for i in sel.requirements]
        assert ("tabulate", ("widechars",)) in names  # pyproject 主依赖带 extra
        assert all(i.group != "requirements-extra.txt" for i in sel.requirements)
        sel2 = depplan.select(
            intents, marker_env=_facts().marker_env, groups=["requirements-extra.txt"]
        )
        assert "requirements-extra.txt" in sel2.selected_groups
        assert any(i.group == "requirements-extra.txt" for i in sel2.requirements)

    def test_unsupported_counts_only_when_its_group_is_selected(self):
        a = depresolve.parse_intent("-e .", group="requirements.txt", source="requirements.txt")
        b = depresolve.parse_intent(
            "-e .", group="requirements-dev.txt", source="requirements-dev.txt"
        )
        c = depresolve.parse_intent(
            "git+https://x/y", group="constraints.txt", source="constraints.txt", kind=C
        )
        sel = depplan.select([a, b, c], marker_env=_facts().marker_env)
        assert [(i.group, i.reason) for i in sel.unsupported] == [
            ("requirements.txt", "editable_install"),
            ("constraints.txt", "direct_url"),
        ]


class TestPlan:
    def _project(self, tmp_path, script: str, requirements: str = "", **files) -> Path:
        proj = tmp_path / "proj"
        _write(proj, "plot.py", script)
        if requirements:
            _write(proj, "requirements.txt", requirements)
        for rel, text in files.items():
            _write(proj, rel, text)
        return proj

    def test_nothing_needed_when_the_target_has_everything(self, tmp_path):
        proj = self._project(tmp_path, "import os\nimport six\n", "six==1.17.0\n")
        plan = depplan.plan(
            proj, "plot.py", facts=_facts({"six": "1.17.0"}), target_kind="tavotto_managed"
        )
        assert plan.status == "nothing_needed"
        assert plan.missing == () and plan.requirements == ()
        assert plan.satisfied[0]["installed_version"] == "1.17.0"
        assert plan.satisfied[0]["matches_declared"] is True

    def test_ready_plan_installs_only_what_is_needed_and_constrains_the_rest(self, tmp_path):
        proj = self._project(
            tmp_path,
            "import tabulate\nimport lab_utils\nimport zzz_private\n",
            "six==1.17.0\ntabulate[widechars]==0.9.0\nsortedcontainers==2.4.0\n",
            **{"lab_utils.py": "import sortedcontainers\n", "constraints.txt": "wcwidth<1\n"},
        )
        plan = depplan.plan(
            proj, "plot.py", facts=_facts({"six": "1.17.0"}), target_kind="tavotto_managed"
        )
        assert plan.status == "ready", plan.blocked
        assert [m["distribution"] for m in plan.missing] == ["sortedcontainers", "tabulate"]
        assert plan.requirements == ("sortedcontainers==2.4.0", "tabulate[widechars]==0.9.0")
        assert plan.constraints == ("six==1.17.0", "wcwidth<1")
        assert plan.adapter == depplan.ADAPTER_REQUIREMENTS
        assert plan.unknown == ("zzz_private",)
        assert "zzz_private" not in " ".join(plan.requirements)
        assert "lab-utils" not in " ".join(plan.requirements)
        assert plan.require_hashes is False and plan.hashes == {}
        # 公开身份不含路径
        assert str(proj) not in json.dumps(plan.to_payload())
        assert plan.scan["counts"]["local"] == 1

    def test_project_venv_target_does_not_carry_adapter_constraints(self, tmp_path):
        proj = self._project(tmp_path, "import six\n", "six\n")
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="project_venv")
        assert plan.status == "ready" and plan.adapter == () and plan.requirements == ("six",)

    def test_curated_import_without_declaration_gets_a_bare_name(self, tmp_path):
        proj = self._project(tmp_path, "import PIL\nimport yaml\n")
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.status == "ready"
        assert plan.requirements == ("pillow", "pyyaml")
        assert {m["resolution_source"] for m in plan.missing} == {"curated"}

    def test_version_mismatch_is_reported_not_fixed(self, tmp_path):
        proj = self._project(tmp_path, "import six\n", "six==1.17.0\n")
        plan = depplan.plan(
            proj, "plot.py", facts=_facts({"six": "1.16.0"}), target_kind="project_venv"
        )
        assert plan.status == "nothing_needed"
        assert plan.satisfied[0]["matches_declared"] is False
        assert plan.requirements == ()

    def test_blocked_by_conflict_keeps_the_conflict_visible(self):
        plan = depplan.plan(
            FIXTURE, "script_unknown_local.py", facts=_facts(), target_kind="tavotto_managed"
        )
        assert plan.status == "blocked"
        assert [b["code"] for b in plan.blocked] == ["dependency_conflict"]
        assert plan.blocked[0]["conflicts"][0]["name"] == "tabulate"
        assert [m["distribution"] for m in plan.missing] == ["tabulate"]
        assert plan.unknown == ("zzz_not_a_real_distribution_u00",)

    def test_blocked_by_unsupported_declaration_in_a_selected_group(self, tmp_path):
        proj = self._project(tmp_path, "import six\n", "six\n-e .\n")
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.status == "blocked"
        assert plan.blocked[0]["code"] == "dependency_declaration_unsupported"
        assert plan.blocked[0]["declarations"][0]["reason"] == "editable_install"
        # 不会「把认不出的那行剥掉偷偷继续」：requirements 仍算好了，但 status 不是 ready
        assert plan.requirements == ("six",) and not plan.actionable

    def test_blocked_wins_even_when_nothing_is_missing(self, tmp_path):
        """Codex #459 P2：什么都不缺也不能把选中的 `-e .` 咽下去——计划不完整就是 blocked。"""
        proj = self._project(tmp_path, "import six\n", "six\n-e .\n")
        plan = depplan.plan(
            proj, "plot.py", facts=_facts({"six": "1.17.0"}), target_kind="tavotto_managed"
        )
        assert plan.status == "blocked" and plan.missing == ()
        assert plan.blocked[0]["code"] == "dependency_declaration_unsupported"

    def test_unsupported_in_an_unselected_group_does_not_block(self, tmp_path):
        proj = self._project(
            tmp_path, "import six\n", "six\n", **{"requirements-dev.txt": "-e .\n"}
        )
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.status == "ready"

    def test_marker_false_declarations_are_neither_installed_nor_missing(self, tmp_path):
        proj = self._project(
            tmp_path, "import six\n", 'six; sys_platform == "never_os"\nsix==1.17.0\n'
        )
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.requirements == ("six==1.17.0",)
        assert plan.selection["skipped_marker"][0]["marker"] == 'sys_platform == "never_os"'

    def test_hash_mode_installs_the_whole_closure_and_requires_every_hash(self, tmp_path):
        h = "--hash=sha256:" + "a" * 64
        lock = f"six==1.17.0 {h}\ntabulate==0.9.0 {h}\nmatplotlib==3.10.0 {h}\nnumpy==2.2.0 {h}\n"
        proj = self._project(tmp_path, "import six\n", lock)
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.status == "ready" and plan.require_hashes, plan.blocked
        assert plan.requirements == (  # 声明顺序，整份锁
            "six==1.17.0",
            "tabulate==0.9.0",
            "matplotlib==3.10.0",
            "numpy==2.2.0",
        )
        assert set(plan.hashes) == set(plan.requirements)
        assert plan.hashes["six==1.17.0"] == ("sha256:" + "a" * 64,)
        assert plan.constraints == ()
        assert plan.adapter == depplan.ADAPTER_REQUIREMENTS  # 身份里仍有它；需求文件里由锁兑现
        proj2 = self._project(tmp_path / "b", "import six\n", f"six==1.17.0 {h}\ntabulate==0.9.0\n")
        plan2 = depplan.plan(proj2, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan2.status == "blocked"
        assert plan2.blocked[0]["code"] == "dependency_hashes_incomplete"
        assert plan2.blocked[0]["lines"] == ["tabulate==0.9.0"]

    def test_hash_mode_on_the_managed_target_requires_the_lock_to_pin_the_adapter(self, tmp_path):
        """`--require-hashes` 下 adapter 那两条给不出 hash：锁必须已经把它们钉住、且在范围内；否则
        blocked（不是偷偷不带 hash 装一条）。用户 venv 目标没有 adapter，不受此限（Codex #461 P1）。"""
        h = "--hash=sha256:" + "a" * 64
        # 锁没钉 matplotlib / numpy
        proj = self._project(tmp_path, "import six\n", f"six==1.17.0 {h}\n")
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.status == "blocked"
        assert [b["code"] for b in plan.blocked] == ["dependency_hashes_incomplete"]
        assert plan.blocked[0]["adapter"] == ["matplotlib", "numpy"]
        venv_plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="project_venv")
        assert venv_plan.status == "ready" and venv_plan.adapter == ()
        # 钉了、但在 adapter 范围外
        lock = f"six==1.17.0 {h}\nmatplotlib==3.5.0 {h}\nnumpy==2.2.0 {h}\n"
        proj2 = self._project(tmp_path / "b", "import six\n", lock)
        plan2 = depplan.plan(proj2, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert [b["code"] for b in plan2.blocked] == ["dependency_conflict"]
        conflict = plan2.blocked[0]["conflicts"][0]
        assert conflict["name"] == "matplotlib" and "adapter" in conflict["sources"]
        assert conflict["specifiers"] == ["==3.5.0", "<3.12,>=3.8"]
        # `>=` 不是钉住
        lock3 = f"six==1.17.0 {h}\nmatplotlib>=3.8 {h}\nnumpy==2.2.0 {h}\n"
        proj3 = self._project(tmp_path / "c", "import six\n", lock3)
        plan3 = depplan.plan(proj3, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan3.blocked[0]["adapter"] == ["matplotlib"]

    def test_install_facts_measure_the_set_against_the_target_not_the_current_interpreter(
        self, tmp_path
    ):
        """缺什么按此刻会跑脚本的解释器量（门），装什么按目标量：目标是新的一代（已装为空）时 needed
        的全装、marker / stdlib 按目标的 Python（Codex #461 P1）。"""
        proj = self._project(
            tmp_path,
            "import six\nimport tabulate\nimport tomllib\nimport matplotlib\n",
            'six==1.17.0\ntabulate==0.9.0\nsortedcontainers==2.4.0; python_version < "3.11"\n',
        )
        current = _facts({"six": "1.17.0"}, python_version="3.12", python_full_version="3.12.4")
        fresh = depplan.TargetFacts(
            python="/fresh/python",
            marker_env={
                **current.marker_env,
                "python_version": "3.10",
                "python_full_version": "3.10.14",
            },
            stdlib=frozenset(importscan.HOST_STDLIB - {"tomllib"}),
            installed={"matplotlib": ""},  # adapter 必然带上的：算已有、版本未知
        )
        plan = depplan.plan(
            proj, "plot.py", facts=current, target_kind="tavotto_managed", install_facts=fresh
        )
        assert plan.status == "ready", plan.blocked
        # 门：此刻缺 matplotlib 与 tabulate（选中的解释器没有）
        assert [m["distribution"] for m in plan.missing] == ["matplotlib", "tabulate"]
        # 装：新的一代 six / tabulate 都要；matplotlib 由 adapter 带上，不进集合
        assert plan.requirements == ("six==1.17.0", "tabulate==0.9.0")
        assert plan.constraints == ("sortedcontainers==2.4.0",)  # marker 按目标的 3.10 求值：选中
        assert plan.unknown == ("tomllib",)  # stdlib 按目标的名字表
        assert plan.facts["install"]["installed_count"] == 1  # 只有 adapter 带上的那一样
        assert (
            plan.identity
            != depplan.plan(proj, "plot.py", facts=current, target_kind="tavotto_managed").identity
        )  # 身份含目标 Python 的 minor
        # 目标就是当前解释器（用户 venv）时集合按它量：只装缺的（matplotlib 没声明 → 裸名）
        same = depplan.plan(proj, "plot.py", facts=current, target_kind="project_venv")
        assert same.requirements == ("matplotlib", "tabulate==0.9.0")
        assert same.constraints == ("six==1.17.0",)

    def test_fresh_venv_facts_provide_the_adapter_and_nothing_else(self, monkeypatch):
        assert depplan.adapter_distributions() == ("matplotlib", "numpy")
        monkeypatch.setattr(
            depplan, "target_facts", lambda python, use_cache=True: _facts({"six": "1"})
        )
        fresh = depplan.fresh_venv_facts("/base", provided=depplan.adapter_distributions())
        assert fresh.installed == {
            "matplotlib": "",
            "numpy": "",
        }  # base 里的 six 不算：新 venv 是空的
        assert fresh.marker_env == _facts().marker_env and fresh.prefix == ""
        monkeypatch.setattr(depplan, "target_facts", lambda python, use_cache=True: None)
        assert depplan.fresh_venv_facts("/base") is None

    def test_no_target_facts_is_blocked(self, tmp_path):
        proj = self._project(tmp_path, "import six\n", "six\n")
        plan = depplan.plan(proj, "plot.py", facts=None, target_kind="tavotto_managed")
        assert plan.status == "blocked"
        assert plan.blocked[0]["code"] == "dependency_target_unavailable"

    def test_possible_imports_are_listed_but_never_installed(self, tmp_path):
        proj = self._project(
            tmp_path,
            "try:\n    import seaborn\nexcept ImportError:\n    seaborn = None\ndef f():\n    import pandas\n",
            "seaborn\npandas\n",
        )
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.status == "nothing_needed"
        assert sorted(p["module"] for p in plan.possible) == ["pandas", "seaborn"]
        assert plan.requirements == ()

    def test_identity_depends_on_intent_not_on_paths(self, tmp_path):
        a = self._project(tmp_path / "a", "import six\n", "six==1.17.0\n")
        b = self._project(tmp_path / "b", "import six\n", "six==1.17.0\n")
        pa = depplan.plan(a, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        pb = depplan.plan(b, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert pa.identity == pb.identity
        pc = depplan.plan(
            a, "plot.py", facts=_facts(python_version="3.11"), target_kind="tavotto_managed"
        )
        assert pc.identity != pa.identity
        pd = depplan.plan(a, "plot.py", facts=_facts(), target_kind="project_venv")
        assert pd.identity != pa.identity

    def test_plan_classifies_stdlib_by_the_target_interpreter_not_the_host(self, tmp_path):
        """脚本会在目标解释器里跑：目标的标准库表说了算。目标表里没有 `zoneinfo`（比如更老的解释器）
        → 它是 unknown；目标表里多一个宿主没有的名字 → 它是 stdlib，不装。"""
        proj = self._project(tmp_path, "import zoneinfo\nimport fakemod_from_target\n")
        facts = _facts()
        narrow = depplan.TargetFacts(
            python=facts.python,
            marker_env=facts.marker_env,
            stdlib=frozenset({"os", "fakemod_from_target"}),
            installed={},
        )
        plan = depplan.plan(proj, "plot.py", facts=narrow, target_kind="tavotto_managed")
        assert plan.unknown == ("zoneinfo",)
        assert plan.scan["counts"]["stdlib"] == 1 and plan.requirements == ()

    def test_bad_target_kind_is_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            depplan.plan(tmp_path, "plot.py", facts=_facts(), target_kind="bundled")

    def test_adapter_requirements_mirror_the_worker_extra(self):
        """`ADAPTER_REQUIREMENTS` ↔ `pyproject.toml` 的 `worker` extra：同源对。"""
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines() if ln.startswith("worker = ["))
        declared = json.loads(line.split("=", 1)[1].strip())
        assert tuple(declared) == depplan.ADAPTER_REQUIREMENTS

    def test_selected_groups_setting_reads_the_project_settings(self, tmp_path):
        from tavotto.engine import config

        assert depplan.selected_groups_setting(tmp_path) == []
        config.set_project_settings(
            str(tmp_path), {depplan.SETTINGS_KEY: ["requirements-dev.txt", 3]}
        )
        assert depplan.selected_groups_setting(tmp_path) == ["requirements-dev.txt"]


# ===========================================================================
# 来源状态（Import Origin Resolver PR4）：多发行包 / editable / 无元数据 / 导入失败 不进 missing
# ===========================================================================
SP_REL = "lib/python3.12/site-packages"


def _env(tmp_path: Path, name: str = "venv") -> tuple[Path, Path]:
    prefix = tmp_path / name
    site = prefix / SP_REL
    site.mkdir(parents=True)
    _write(prefix, "pyvenv.cfg", "home = /usr/bin\nversion = 3.12.1\n")
    return prefix, site


def _dist(
    site: Path,
    name: str,
    version: str,
    *,
    top: str,
    record: list[str] | None = None,
    direct_url: dict | None = None,
) -> None:
    d = site / f"{name.replace('-', '_')}-{version}.dist-info"
    d.mkdir(parents=True)
    (d / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\nBODY\n", encoding="utf-8"
    )
    (d / "top_level.txt").write_text(top, encoding="utf-8")
    if record is not None:
        rows = [f"{p},sha256=abc,10" for p in record] + [f"{d.name}/METADATA,,"]
        (d / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")
    if direct_url is not None:
        (d / "direct_url.json").write_text(json.dumps(direct_url), encoding="utf-8")


def _facts_at(prefix: Path, installed: dict) -> depplan.TargetFacts:
    """目标解释器的事实，前缀指向 tmp_path 里造的真环境（静态索引读它；解释器本身一次也不起）。"""
    base = _facts(installed)
    return depplan.TargetFacts(
        python=str(prefix / "bin" / "python"),
        marker_env=base.marker_env,
        stdlib=base.stdlib,
        installed=dict(installed),
        prefix=str(prefix),
    )


def _health(**detail) -> dict:
    return {"ok": True, "modules_detail": dict(detail), "modules_ok": {}}


class TestPlanOrigins:
    """`missing` 只剩「确实该装、且有可信安装名」的。来源未定的进 `unknown` + `origins`，不进 `requirements`。"""

    @pytest.fixture(autouse=True)
    def _clean(self):
        depplan.reset_cache()
        yield
        depplan.reset_cache()

    def _project(self, tmp_path: Path, script: str, requirements: str = "") -> Path:
        proj = tmp_path / "proj"
        _write(proj, "plot.py", script)
        if requirements:
            _write(proj, "requirements.txt", requirements)
        return proj

    def _plan(self, proj, facts, **kw):
        return depplan.plan(proj, "plot.py", facts=facts, target_kind="project_venv", **kw)

    # ---- M5（BASELINE §2-Q3）----------------------------------------------------------------
    def test_m5_cv2_with_the_headless_build_installed_is_not_missing(self, tmp_path):
        """审计时的复现：已装 `opencv-python-headless`，计划却报 `missing=['opencv-python']`、要装一个重复的 cv2。"""
        proj = self._project(tmp_path, "import cv2\n")
        plan = self._plan(proj, _facts({"opencv-python-headless": "4.9.0"}))
        assert plan.status == "nothing_needed"
        assert plan.missing == () and plan.requirements == ()
        (entry,) = plan.satisfied
        assert entry["import_name"] == "cv2"
        assert entry["distribution"] == "opencv-python-headless"  # 装着的那个，不是 curated 的那个
        assert entry["mapped_distribution"] == "opencv-python"
        assert entry["installed_version"] == "4.9.0"
        assert plan.unknown == () and plan.origins == ()

    def test_cv2_with_nothing_installed_keeps_the_curated_default_but_says_it_is_ambiguous(
        self, tmp_path
    ):
        """没有任何备选装着：照旧缺 `opencv-python`（用户确认摘要里看得见），但条目带上「多发行包」的来源状态。"""
        proj = self._project(tmp_path, "import cv2\n")
        plan = self._plan(proj, _facts({"numpy": "2.0.0"}))
        assert plan.status == "ready"
        assert plan.requirements == ("opencv-python",)
        (entry,) = plan.missing
        assert entry["distribution"] == "opencv-python"
        assert entry["distribution_status"] == "module_origin_ambiguous"

    def test_two_cv2_builds_installed_is_ambiguous_and_nothing_is_installed(self, tmp_path):
        proj = self._project(tmp_path, "import cv2\n")
        plan = self._plan(
            proj, _facts({"opencv-python-headless": "4.9.0", "opencv-contrib-python": "4.9.0"})
        )
        assert plan.missing == () and plan.satisfied == () and plan.requirements == ()
        assert plan.unknown == ("cv2",)
        (origin,) = plan.origins
        assert origin["reason"] == "module_origin_ambiguous"
        assert origin["candidates"] == [
            "opencv-contrib-python",
            "opencv-contrib-python-headless",
            "opencv-python",
            "opencv-python-headless",
        ]

    def test_the_declared_name_wins_when_it_is_installed(self, tmp_path):
        proj = self._project(tmp_path, "import cv2\n", "opencv-python-headless>=4\n")
        plan = self._plan(
            proj, _facts({"opencv-python-headless": "4.9.0", "opencv-contrib-python": "4.9.0"})
        )
        (entry,) = plan.satisfied
        assert entry["distribution"] == "opencv-python-headless"
        assert entry["declared"] is True and entry["matches_declared"] is True
        assert plan.origins == ()

    # ---- editable / 本地 / Conda / 无元数据：静态索引的来源 --------------------------------------
    def test_an_editable_provider_is_not_missing_and_never_becomes_a_pip_name(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(
            site,
            "my-yaml-fork",
            "0.1",
            top="yaml\n",
            record=["yaml/__init__.py"],
            direct_url={"url": "file:///src/fork", "dir_info": {"editable": True}},
        )
        proj = self._project(tmp_path, "import yaml\n")
        plan = self._plan(proj, _facts_at(prefix, {"my-yaml-fork": "0.1"}))
        assert plan.status == "nothing_needed"
        assert plan.missing == () and plan.requirements == ()  # 不装 pyyaml 去盖它
        assert plan.unknown == ("yaml",)
        (origin,) = plan.origins
        assert origin["reason"] == "editable_dependency_not_reproducible"
        assert origin["distribution_status"] == "editable_dependency_not_reproducible"
        assert origin["provenance"] == "editable"
        assert origin["candidates"] == ["my-yaml-fork", "pyyaml"] or origin["candidates"] == [
            "my-yaml-fork"
        ]

    @pytest.mark.parametrize(
        "direct_url,reason",
        [
            (
                {"url": "file:///wheels/fork.whl", "archive_info": {}},
                "installed_source_not_reproducible",
            ),
            (
                {"url": "https://example.invalid/x.git", "vcs_info": {"vcs": "git"}},
                "installed_source_not_reproducible",
            ),
            ({"url": "file:///src/fork", "dir_info": {}}, "installed_source_not_reproducible"),
        ],
        ids=["local-wheel", "vcs", "local-dir"],
    )
    def test_local_and_vcs_providers_are_not_missing_either(self, tmp_path, direct_url, reason):
        prefix, site = _env(tmp_path)
        _dist(site, "fork", "1.0", top="yaml\n", record=["yaml/__init__.py"], direct_url=direct_url)
        proj = self._project(tmp_path, "import yaml\n")
        plan = self._plan(proj, _facts_at(prefix, {"fork": "1.0"}))
        assert plan.missing == () and plan.requirements == ()
        assert [(o["import_name"], o["reason"]) for o in plan.origins] == [("yaml", reason)]

    def test_a_module_in_site_packages_without_any_metadata_is_not_missing(self, tmp_path):
        prefix, site = _env(tmp_path)
        _write(site, "yaml/__init__.py", "")
        proj = self._project(tmp_path, "import yaml\n")
        plan = self._plan(proj, _facts_at(prefix, {}))
        assert plan.missing == () and plan.requirements == ()
        (origin,) = plan.origins
        assert origin["import_name"] == "yaml" and origin["reason"] == "unverified"

    def test_a_package_that_really_is_absent_is_still_missing(self, tmp_path):
        """静态索引读得到、也读全了，但没有谁声称提供它：照旧缺（负例：来源收紧不能把真缺的也吞掉）。"""
        prefix, site = _env(tmp_path)
        _dist(site, "numpy", "2.0.0", top="numpy\n", record=["numpy/__init__.py"])
        proj = self._project(tmp_path, "import yaml\nimport numpy\n")
        plan = self._plan(proj, _facts_at(prefix, {"numpy": "2.0.0"}))
        assert plan.status == "ready"
        assert [m["distribution"] for m in plan.missing] == ["pyyaml"]
        assert plan.requirements == ("pyyaml",)
        assert plan.origins == ()

    def test_a_conda_package_without_pip_metadata_is_never_given_a_pip_name(self, tmp_path):
        prefix, site = _env(tmp_path)
        conda = prefix / "conda-meta"
        conda.mkdir()
        (conda / "pyyaml-6.0-py312.json").write_text(
            json.dumps(
                {"name": "pyyaml", "version": "6.0", "files": [f"{SP_REL}/yaml/__init__.py"]}
            ),
            encoding="utf-8",
        )
        _write(site, "yaml/__init__.py", "")
        proj = self._project(tmp_path, "import yaml\n")
        plan = self._plan(proj, _facts_at(prefix, {}))
        assert plan.missing == () and plan.requirements == ()
        assert [o["reason"] for o in plan.origins] == ["conda_package_not_pypi"]

    # ---- 包在、导入失败（PR3 覆盖度缓存）-----------------------------------------------------
    def test_a_module_that_imports_with_an_error_is_not_fixed_by_installing_its_package(
        self, tmp_path
    ):
        proj = self._project(tmp_path, "import yaml\n")
        seen: list[tuple] = []

        def cached(names):
            seen.append(names)
            return _health(yaml="import_error")

        plan = self._plan(proj, _facts({}), coverage=cached)
        assert plan.missing == () and plan.requirements == ()
        assert plan.unknown == ("yaml",)
        (origin,) = plan.origins
        assert origin["reason"] == depplan.ORIGIN_IMPORT_FAILED
        assert origin["coverage"] == "import_error"
        assert seen == [("yaml",)]  # 只读查询，用的是依赖门 / 检测同一组名字

    @pytest.mark.parametrize(
        "health",
        [
            None,
            {"ok": False, "modules_detail": {"yaml": "import_error"}},  # 环境本身不健康：不看模块
            _health(yaml="not_found"),
            _health(yaml="deferred"),
            _health(),
        ],
        ids=["not-measured", "unusable-env", "not-found", "deferred", "no-detail"],
    )
    def test_only_a_measured_import_error_changes_the_verdict(self, tmp_path, health):
        proj = self._project(tmp_path, "import yaml\n")
        plan = self._plan(proj, _facts({}), coverage=lambda names: health)
        assert [m["distribution"] for m in plan.missing] == ["pyyaml"]
        assert plan.requirements == ("pyyaml",) and plan.origins == ()

    def test_an_import_error_for_one_module_does_not_spare_the_others(self, tmp_path):
        proj = self._project(tmp_path, "import yaml\nimport tabulate\n")
        plan = self._plan(proj, _facts({}), coverage=lambda names: _health(yaml="import_error"))
        assert [m["distribution"] for m in plan.missing] == ["tabulate"]
        assert plan.requirements == ("tabulate",)
        assert plan.unknown == ("yaml",)

    # ---- 不变量 ------------------------------------------------------------------------------
    def test_unknown_names_are_exactly_the_origins_names(self, tmp_path):
        proj = self._project(tmp_path, "import zzz_private\nimport cv2\nimport yaml\n")
        plan = self._plan(
            proj,
            _facts({"opencv-python-headless": "1", "opencv-contrib-python": "1"}),
            coverage=lambda names: _health(yaml="import_error"),
        )
        assert set(plan.unknown) == {o["import_name"] for o in plan.origins}
        assert {o["reason"] for o in plan.origins} <= set(depplan.ORIGIN_REASONS)
        assert dict((o["import_name"], o["reason"]) for o in plan.origins)["zzz_private"] == (
            "distribution_unresolved"
        )
        assert plan.to_payload()["origins"] == [dict(o) for o in plan.origins]

    def test_origin_facts_never_carry_paths_or_file_contents(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(
            site,
            "my-yaml-fork",
            "0.1",
            top="yaml\n",
            record=["yaml/__init__.py"],
            direct_url={"url": "file:///SECRET/fork", "dir_info": {"editable": True}},
        )
        proj = self._project(tmp_path, "import yaml\n")
        plan = self._plan(proj, _facts_at(prefix, {"my-yaml-fork": "0.1"}))
        text = json.dumps(plan.to_payload())
        assert str(tmp_path) not in text and "SECRET" not in text

    def test_a_plan_with_nothing_unusual_is_unchanged_by_the_static_index(self, tmp_path):
        """回归（BASELINE §6）：没有来源问题的脚本，读不读静态索引，需求 / 约束 / 身份 / 输入指纹逐字相同——
        受管环境的代目录名与「第二次打开不重装」不受影响。"""
        prefix, site = _env(tmp_path)
        _dist(site, "six", "1.17.0", top="six\n", record=["six.py"])
        proj = self._project(tmp_path, "import six\nimport yaml\n", "six==1.17.0\n")
        facts = _facts_at(prefix, {"six": "1.17.0"})
        with_index = self._plan(proj, facts)
        without = self._plan(proj, facts, dists=None)
        for field in ("status", "requirements", "constraints", "identity", "inputs_digest"):
            assert getattr(with_index, field) == getattr(without, field), field
        assert [m["distribution"] for m in with_index.missing] == ["pyyaml"]
        assert [m["distribution"] for m in without.missing] == ["pyyaml"]

    def test_reading_the_static_index_for_a_plan_executes_nothing(self, tmp_path, monkeypatch):
        """零执行：目标环境里放一个 `__init__` / `.pth` / `sitecustomize`，出计划之后没有哨兵文件；起进程 / 联网 /
        `importlib.metadata` / `find_spec` 全换成调用即失败的桩。"""
        import importlib.metadata
        import importlib.util
        import socket
        import subprocess

        sentinel = tmp_path / "SIDE_EFFECT"
        body = f"open({str(sentinel)!r}, 'w').write('ran')\n"
        prefix, site = _env(tmp_path)
        _write(site, "dangerous_package/__init__.py", body)
        _write(site, "evil.pth", f"import os; open({str(sentinel)!r}, 'w').write('pth')\n")
        _write(site, "sitecustomize.py", body)
        _dist(
            site,
            "dangerous-package",
            "1.0",
            top="dangerous_package\n",
            record=["dangerous_package/__init__.py"],
            direct_url={"url": "file:///src/d", "dir_info": {"editable": True}},
        )

        def boom(name):
            def _boom(*a, **k):
                raise AssertionError(f"{name} 不许在出计划时被调用")

            return _boom

        for target, attr in (
            (subprocess, "Popen"),
            (subprocess, "run"),
            (os, "system"),
            (socket, "socket"),
            (importlib.metadata, "distributions"),
            (importlib.util, "find_spec"),
        ):
            monkeypatch.setattr(target, attr, boom(f"{target.__name__}.{attr}"))
        proj = self._project(tmp_path, "import dangerous_package\n")
        facts = _facts_at(prefix, {"dangerous-package": "1.0"})
        plan = self._plan(proj, facts)
        assert plan.missing == () and plan.requirements == ()
        assert not sentinel.exists()

    def test_the_observed_provider_never_becomes_an_install_name(self, tmp_path):
        """已装元数据是证据不是授权：运行环境里 `cv2` 由 headless 提供，要新建一代时装的仍是表 / 声明里的名字，
        不是观测到的那个（`selected_distribution` / `observed_distribution` 只用来判「已经有了」）。"""
        proj = self._project(tmp_path, "import cv2\n")
        running = _facts({"opencv-python-headless": "4.9.0"})
        fresh = _facts({})  # 新一代装之前：什么都没有
        plan = depplan.plan(
            proj, "plot.py", facts=running, target_kind="tavotto_managed", install_facts=fresh
        )
        assert plan.missing == ()  # 此刻的环境里有，不缺
        assert plan.requirements == ("opencv-python",)
        assert "opencv-python-headless" not in " ".join(plan.requirements)

    def test_the_index_lives_and_dies_with_the_facts(self, tmp_path, monkeypatch):
        """索引与事实同一刻读、同一个 `reset_cache` 清：装上了但验证没过的包不会 `reset_cache`，事实停在装之前，
        索引也必须停在装之前（否则同一个包一边说没装、一边说装着，FO22 的门就不再问了）。"""
        prefix, site = _env(tmp_path)
        facts = _facts_at(prefix, {})
        first = depplan.static_index(facts)
        assert first is not None and depplan.static_index(facts) is first  # 缓存着
        _dist(
            site, "late", "1.0", top="late\n", record=["late/__init__.py"]
        )  # 事实之后才出现在磁盘上
        assert depplan.static_index(facts) is first  # 不偷偷换成更新的一份
        depplan.reset_cache(facts.python)
        fresh = depplan.static_index(facts)
        assert fresh is not first and fresh.lookup("late").candidates

    def test_measuring_the_facts_reads_the_index_at_the_same_moment(self, monkeypatch):
        calls: list[str] = []
        real = distmeta.index_environment

        def counting(prefix, **kw):
            calls.append(str(prefix))
            return real(prefix, **kw)

        monkeypatch.setattr(distmeta, "index_environment", counting)
        facts = depplan.target_facts(sys.executable, use_cache=False)
        assert facts is not None and facts.prefix
        assert calls == [facts.prefix]  # 量事实的同时读了索引
        depplan.static_index(facts)
        assert calls == [facts.prefix]  # 出计划时不再读第二遍
        # 重新量事实（环境可能变了）= 索引一起重读，不留着上一份
        again = depplan.target_facts(sys.executable, use_cache=False)
        assert again is not None and calls == [facts.prefix, facts.prefix]

    def test_a_plan_without_a_prefix_reads_no_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            distmeta,
            "index_environment",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该读")),
        )
        proj = self._project(tmp_path, "import yaml\n")
        plan = self._plan(proj, dataclasses.replace(_facts({}), prefix=""))  # 替身事实：没有前缀
        assert [m["distribution"] for m in plan.missing] == ["pyyaml"]

    def test_the_closed_sets_do_not_grow_blocked(self):
        """C3：来源未定不是新的 `blocked` 理由——`BLOCK_REASONS` 闭集原样。"""
        assert depplan.BLOCK_REASONS == (
            "dependency_declaration_unsupported",
            "dependency_conflict",
            "dependency_hashes_incomplete",
            "dependency_target_unavailable",
            "dependency_scope_conflict",
        )
        assert not set(depplan.ORIGIN_REASONS) & set(depplan.BLOCK_REASONS)

    def test_the_import_failed_reason_is_the_coverage_detail_code(self):
        from tavotto.engine import envadvice

        assert depplan.ORIGIN_IMPORT_FAILED == envadvice.DETAIL_IMPORT_FAILED_TARGET
        known = set(distmeta.STATUSES) | {
            depplan.ORIGIN_UNRESOLVED,
            depplan.ORIGIN_IMPORT_FAILED,
        }
        assert set(depplan.ORIGIN_REASONS) <= known

    def test_a_scan_cut_short_says_so_in_the_plan_payload(self, tmp_path):
        proj = tmp_path / "proj"
        _write(proj, "plot.py", "import m0\n")
        for i in range(importscan.MAX_DEPTH + 2):
            _write(proj, f"m{i}.py", f"import m{i + 1}\n")
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="project_venv")
        assert plan.scan["truncated"] is True and plan.scan["search_complete"] is False


# ===========================================================================
# Codex #920 评审跟进：P1-1 目标解释器看得见的每一层都要读；P1-2 安装目标单独判
# ===========================================================================
_EDITABLE = {"url": "file:///src/fork", "dir_info": {"editable": True}}
#: 候选 (import 名, 表里的发行包名)：测试要用「宿主解释器里没装」的那一个，否则名字级就已经判成「有」，用例是空的
_CANDIDATES = (("yaml", "pyyaml"), ("docx", "python-docx"), ("tabulate", "tabulate"))


def _free_import(installed: dict) -> tuple[str, str]:
    for module, dist in _CANDIDATES:
        if dist not in installed:
            return module, dist
    pytest.skip("宿主解释器把候选包都装了，无法构造「名字级未装」的前提")


def _sys_site_venv(tmp_path: Path, name: str = "sysvenv") -> tuple[Path, Path]:
    """真 venv，`include-system-site-packages = true`（基础解释器的 site-packages 与 user site 都在 sys.path 上）。"""
    import subprocess

    venv = tmp_path / name
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", "--system-site-packages", str(venv)],
        check=True,
        capture_output=True,
        encoding="utf-8",
        timeout=300,
    )
    cfg = (venv / "pyvenv.cfg").read_text(encoding="utf-8").lower().replace(" ", "")
    assert "include-system-site-packages=true" in cfg
    from support import envworld

    return venv, envworld.venv_python(venv)


def _venv_site(python: Path) -> str:
    import subprocess

    out = subprocess.run(
        [str(python), "-c", "import site;print(site.getsitepackages()[0])"],
        check=True,
        capture_output=True,
        encoding="utf-8",
        timeout=120,
    )
    return out.stdout.strip()


class TestEveryVisibleLayer:
    """P1-1：`static_index` 读目标解释器看得见的**每一层**，不止前缀内的 site-packages。别的层里的替代提供者被漏掉，
    计划就会往用户现有环境里装 `pyyaml` 去遮蔽它。某一层没读成 = 索引不完整 = 不当「缺」。"""

    @pytest.fixture(autouse=True)
    def _clean(self):
        depplan.reset_cache()
        yield
        depplan.reset_cache()

    def _project(self, tmp_path: Path, script: str) -> Path:
        proj = tmp_path / "proj"
        _write(proj, "plot.py", script)
        return proj

    def _plan(self, proj, facts, **kw):
        return depplan.plan(proj, "plot.py", facts=facts, target_kind="project_venv", **kw)

    def _facts(self, python: Path, monkeypatch, *, extra_path: Path | None = None, userbase=None):
        if extra_path is not None:
            old = os.environ.get("PYTHONPATH", "")
            monkeypatch.setenv(
                "PYTHONPATH", os.pathsep.join(p for p in (str(extra_path), old) if p)
            )
        if userbase is not None:
            monkeypatch.setenv("PYTHONUSERBASE", str(userbase))
            monkeypatch.delenv("PYTHONNOUSERSITE", raising=False)
        facts = depplan.target_facts(str(python), use_cache=False)
        assert facts is not None
        return facts

    def test_the_facts_report_the_base_layer_of_an_include_system_site_packages_venv(
        self, tmp_path, monkeypatch
    ):
        venv, python = _sys_site_venv(tmp_path)
        base = (
            tmp_path / "base" / "site-packages"
        )  # 基础解释器那一层的替身：sys.path 上、叫 site-packages
        base.mkdir(parents=True)
        facts = self._facts(python, monkeypatch, extra_path=base)
        own = os.path.normcase(os.path.realpath(_venv_site(python)))
        reported = [os.path.normcase(os.path.realpath(p)) for p in facts.site_roots]
        assert own in reported
        assert os.path.normcase(os.path.realpath(base)) in reported
        # 沿 sys.path 的先后：PYTHONPATH 在 site 之前
        assert reported.index(os.path.normcase(os.path.realpath(base))) < reported.index(own)

    def test_an_editable_provider_in_the_base_layer_is_not_missing(self, tmp_path, monkeypatch):
        venv, python = _sys_site_venv(tmp_path)
        base = tmp_path / "base" / "site-packages"
        base.mkdir(parents=True)
        facts = self._facts(python, monkeypatch, extra_path=base)
        module, dist = _free_import(facts.installed)
        _dist(
            base,
            "my-fork",
            "0.1",
            top=f"{module}\n",
            record=[f"{module}/__init__.py"],
            direct_url=_EDITABLE,
        )
        facts = self._facts(python, monkeypatch, extra_path=base)  # 元数据放好之后重新量
        plan = self._plan(self._project(tmp_path, f"import {module}\n"), facts)
        assert plan.missing == () and plan.requirements == ()  # 不往用户环境里装表里的名字去遮蔽它
        assert plan.unknown == (module,)
        (origin,) = plan.origins
        assert origin["reason"] == "editable_dependency_not_reproducible"

    def test_an_editable_provider_in_the_user_site_is_not_missing(self, tmp_path, monkeypatch):
        import subprocess

        venv, python = _sys_site_venv(tmp_path)
        userbase = tmp_path / "userbase"
        monkeypatch.setenv("PYTHONUSERBASE", str(userbase))
        monkeypatch.delenv("PYTHONNOUSERSITE", raising=False)
        user_site = subprocess.run(
            [str(python), "-c", "import site;print(site.getusersitepackages())"],
            check=True,
            capture_output=True,
            encoding="utf-8",
            timeout=120,
        ).stdout.strip()
        Path(user_site).mkdir(parents=True)
        probe = depplan.target_facts(str(python), use_cache=False)
        assert probe is not None
        module, dist = _free_import(probe.installed)
        _dist(
            Path(user_site),
            "my-fork",
            "0.1",
            top=f"{module}\n",
            record=[f"{module}/__init__.py"],
            direct_url=_EDITABLE,
        )
        facts = depplan.target_facts(str(python), use_cache=False)
        assert facts is not None
        assert os.path.normcase(os.path.realpath(user_site)) in [
            os.path.normcase(os.path.realpath(p)) for p in facts.site_roots
        ]
        plan = self._plan(self._project(tmp_path, f"import {module}\n"), facts)
        assert plan.missing == () and plan.requirements == ()
        assert [o["reason"] for o in plan.origins] == ["editable_dependency_not_reproducible"]

    def test_a_layer_that_cannot_be_read_is_never_a_reason_to_install(self, tmp_path):
        """control + 三种读不成：消失了 / 是符号链接 / 目录读不动。读不成的那一层里可能正有提供者。"""
        prefix, site = _env(tmp_path)
        proj = self._project(tmp_path, "import tabulate\n")
        good = dataclasses.replace(_facts_at(prefix, {}), site_roots=(str(site),))
        control = self._plan(proj, good)
        assert [m["distribution"] for m in control.missing] == ["tabulate"]  # 读全了：照旧缺
        gone = tmp_path / "vanished" / "site-packages"
        broken = {"vanished": str(gone)}
        if hasattr(os, "symlink") and os.name != "nt":
            real = tmp_path / "real_layer"
            real.mkdir()
            link = tmp_path / "linked" / "site-packages"
            link.parent.mkdir()
            os.symlink(real, link)
            broken["symlink"] = str(link)
        for label, layer in broken.items():
            facts = dataclasses.replace(good, site_roots=(str(site), layer))
            depplan.reset_cache()
            index = depplan.static_index(facts)
            assert index is not None and index.complete is False, label
            assert str(tmp_path) not in json.dumps(index.to_payload()), label  # 账里没有绝对路径
            plan = self._plan(proj, facts)
            assert plan.missing == () and plan.requirements == (), label
            assert plan.status == "nothing_needed", label
            (origin,) = plan.origins
            assert origin["import_name"] == "tabulate", label
            assert origin["reason"] == "unverified", label
            assert origin["distribution_status"] == "environment_not_checked", label

    def test_an_unreadable_metadata_file_of_one_distribution_does_not_hide_the_layer(
        self, tmp_path
    ):
        """Homebrew 的 site-packages 里 pip / wheel 的 METADATA 是符号链接：那只让这一个发行包的元数据读不全（`complete=False`），
        这一层里有哪些名字是看全了的（`names_complete`），不能因此把整个环境的「没查到」都改判成来源未定。"""
        if os.name == "nt":
            pytest.skip("符号链接在 Windows 上需要特权")
        prefix, site = _env(tmp_path)
        _dist(site, "pip", "25.3", top="pip\n", record=["pip/__init__.py"])
        real = tmp_path / "elsewhere.txt"
        real.write_text("Metadata-Version: 2.1\nName: pip\nVersion: 25.3\n", encoding="utf-8")
        meta = site / "pip-25.3.dist-info" / "METADATA"
        meta.unlink()
        os.symlink(real, meta)
        facts = dataclasses.replace(_facts_at(prefix, {"pip": "25.3"}), site_roots=(str(site),))
        index = depplan.static_index(facts)
        assert index is not None and index.complete is False and index.names_complete is True
        plan = self._plan(self._project(tmp_path, "import tabulate\n"), facts)
        assert [m["distribution"] for m in plan.missing] == ["tabulate"]  # 照旧缺

    def test_a_symlinked_package_directory_is_a_name_nobody_saw(self, tmp_path):
        """层里有个符号链接的包目录 `yaml/`：Python 会跟进它、我们不跟——这个名字没被看到，不许当「没装」。"""
        if os.name == "nt":
            pytest.skip("符号链接在 Windows 上需要特权")
        prefix, site = _env(tmp_path)
        target = tmp_path / "elsewhere" / "yaml"
        target.mkdir(parents=True)
        (target / "__init__.py").write_text("", encoding="utf-8")
        os.symlink(target, site / "yaml")
        facts = dataclasses.replace(_facts_at(prefix, {}), site_roots=(str(site),))
        index = depplan.static_index(facts)
        assert index is not None and index.names_complete is False
        plan = self._plan(self._project(tmp_path, "import yaml\n"), facts)
        assert plan.missing == () and plan.requirements == ()
        assert [o["reason"] for o in plan.origins] == ["unverified"]

    def test_an_unreadable_directory_is_not_a_missing_package(self, tmp_path):
        if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
            pytest.skip("目录权限在 Windows / root 下不起作用")
        prefix, site = _env(tmp_path)
        locked = tmp_path / "locked" / "site-packages"
        locked.mkdir(parents=True)
        locked.chmod(0o000)
        try:
            facts = dataclasses.replace(_facts_at(prefix, {}), site_roots=(str(site), str(locked)))
            plan = self._plan(self._project(tmp_path, "import tabulate\n"), facts)
        finally:
            locked.chmod(0o700)
        assert plan.missing == () and plan.requirements == ()
        assert [o["reason"] for o in plan.origins] == ["unverified"]

    def test_the_layers_are_read_in_sys_path_order_and_the_first_regular_provider_wins(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        other = tmp_path / "other" / "site-packages"
        other.mkdir(parents=True)
        _dist(
            other, "my-fork", "0.1", top="yaml\n", record=["yaml/__init__.py"], direct_url=_EDITABLE
        )
        facts = dataclasses.replace(_facts_at(prefix, {}), site_roots=(str(other), str(site)))
        index = depplan.static_index(facts)
        assert index is not None and index.complete
        _dist(
            site, "pyyaml", "6.0", top="yaml\n", record=["yaml/__init__.py"]
        )  # 后一层的同名提供者
        facts = dataclasses.replace(facts, site_roots=(str(other), str(site)))
        depplan.reset_cache()
        index = depplan.static_index(facts)
        assert index is not None and index.complete
        live = {c.distribution: not c.shadowed for c in index.lookup("yaml").candidates}
        assert live == {"my-fork": True, "pyyaml": False}  # 前一层胜出，后一层被遮蔽

    def test_facts_without_reported_layers_keep_the_prefix_only_reading(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(site, "fork", "1.0", top="yaml\n", record=["yaml/__init__.py"], direct_url=_EDITABLE)
        facts = _facts_at(prefix, {"fork": "1.0"})  # 替身事实：没有 site_roots
        assert facts.site_roots == ()
        plan = self._plan(self._project(tmp_path, "import yaml\n"), facts)
        assert plan.missing == () and [o["reason"] for o in plan.origins] == [
            "editable_dependency_not_reproducible"
        ]

    def test_the_layer_list_is_not_a_plan_input(self, tmp_path):
        """`site_roots` 与 `builtin` 同理：不进 `digest()` / `to_payload()`，不改 `identity` / `inputs_digest`。"""
        prefix, site = _env(tmp_path)
        a = _facts_at(prefix, {"six": "1"})
        b = dataclasses.replace(a, site_roots=(str(site), str(tmp_path / "x")))
        assert a.digest() == b.digest() and a.to_payload() == b.to_payload()
        proj = self._project(tmp_path, "import six\n")
        pa, pb = self._plan(proj, a, dists=None), self._plan(proj, b, dists=None)
        assert (pa.identity, pa.inputs_digest) == (pb.identity, pb.inputs_digest)


class TestExtraPathLayers:
    """P1-1 的剩余缺口：`sys.path` 上不叫 site-packages 的目录（`PYTHONPATH` 的 `--target` 目录、`.pth` 路径行加进来的 src 目录）
    里的提供者。名字级扫描：模块名出现在那里 = 别处已有它，不判缺；读不了的目录 = 名字没读全，不判缺；没有它时照样报缺。"""

    @pytest.fixture(autouse=True)
    def _clean(self):
        depplan.reset_cache()
        yield
        depplan.reset_cache()

    def _plan(self, tmp_path, facts, script, **kw):
        proj = tmp_path / "proj"
        _write(proj, "plot.py", script)
        return depplan.plan(proj, "plot.py", facts=facts, target_kind="project_venv", **kw)

    def test_a_pip_target_directory_on_pythonpath_hides_nothing(self, tmp_path, monkeypatch):
        venv, python = _sys_site_venv(tmp_path)
        target = tmp_path / "vendor"  # `pip install --target` 式：目录名不是 site-packages
        target.mkdir()
        probe = depplan.target_facts(str(python), use_cache=False)
        module, dist = _free_import(probe.installed)
        _write(target, f"{module}/__init__.py", "")
        old = os.environ.get("PYTHONPATH", "")
        monkeypatch.setenv("PYTHONPATH", os.pathsep.join(p for p in (str(target), old) if p))
        facts = depplan.target_facts(str(python), use_cache=False)
        assert str(target) in facts.extra_roots
        plan = self._plan(tmp_path, facts, f"import {module}\n")
        assert plan.missing == () and plan.requirements == ()
        assert [o["reason"] for o in plan.origins] == ["unverified"]

    def test_a_pth_path_line_to_a_source_directory_hides_nothing(self, tmp_path):
        venv, python = _sys_site_venv(tmp_path)
        probe = depplan.target_facts(str(python), use_cache=False)
        module, dist = _free_import(probe.installed)
        src = tmp_path / "oldstyle" / "src"
        _write(src, f"{module}.py", "")
        _write(Path(_venv_site(python)), "legacy-editable.pth", f"{src}\n")
        depplan.reset_cache()
        facts = depplan.target_facts(str(python), use_cache=False)
        assert str(src) in facts.extra_roots
        index = depplan.static_index(facts)
        assert index is not None and index.uncovered_paths == 0  # 路径行指向的目录已作为层读过
        plan = self._plan(tmp_path, facts, f"import {module}\n")
        assert plan.missing == () and plan.requirements == ()

    def test_extra_directories_without_the_module_still_report_it_missing(self, tmp_path):
        prefix, site = _env(tmp_path)
        other = tmp_path / "vendor"
        _write(other, "unrelated/__init__.py", "")
        facts = dataclasses.replace(
            _facts_at(prefix, {}), site_roots=(str(site),), extra_roots=(str(other),)
        )
        plan = self._plan(tmp_path, facts, "import tabulate\n")
        assert [m["distribution"] for m in plan.missing] == [
            "tabulate"
        ]  # 老式 editable 的 venv 里真缺的包照样自动准备

    @pytest.mark.parametrize("kind", ["gone", "symlink", "locked"])
    def test_an_extra_directory_that_cannot_be_read_is_not_a_missing_package(self, tmp_path, kind):
        if kind != "gone" and os.name == "nt":
            pytest.skip("Windows 上没有这种权限 / 链接")
        if kind == "locked" and hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("root 不受目录权限限制")
        prefix, site = _env(tmp_path)
        bad = tmp_path / "vendor"
        if kind == "symlink":
            (tmp_path / "real").mkdir()
            os.symlink(tmp_path / "real", bad)
        elif kind == "locked":
            bad.mkdir()
            bad.chmod(0o000)
        try:
            facts = dataclasses.replace(
                _facts_at(prefix, {}), site_roots=(str(site),), extra_roots=(str(bad),)
            )
            plan = self._plan(tmp_path, facts, "import tabulate\n")
        finally:
            if kind == "locked":
                bad.chmod(0o700)
        assert plan.missing == () and plan.requirements == ()
        assert [o["reason"] for o in plan.origins] == ["unverified"]

    def test_too_many_extra_directories_is_not_complete(self, tmp_path):
        prefix, site = _env(tmp_path)
        many = []
        for i in range(distmeta.MAX_SITE_PATHS + 1):
            d = tmp_path / f"e{i}"
            d.mkdir()
            many.append(str(d))
        facts = dataclasses.replace(
            _facts_at(prefix, {}), site_roots=(str(site),), extra_roots=tuple(many)
        )
        index = depplan.static_index(facts)
        assert index is not None and index.names_complete is False

    def test_a_pth_path_line_to_a_directory_the_facts_did_not_report_stays_uncovered(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        known, unknown = tmp_path / "known", tmp_path / "unknown"
        known.mkdir()
        unknown.mkdir()
        _write(site, "a.pth", f"{known}\n")
        _write(site, "b.pth", f"{unknown}\n")
        _write(site, "c.pth", "import os\n")
        facts = dataclasses.replace(
            _facts_at(prefix, {}), site_roots=(str(site),), extra_roots=(str(known),)
        )
        index = depplan.static_index(facts)
        assert index is not None and index.uncovered_paths == 2  # unknown 的路径行 + import 行

    def test_the_new_generation_is_not_spared_by_an_extra_path_provider_of_the_running_one(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        other = tmp_path / "vendor"
        _write(other, "tabulate.py", "")
        running = dataclasses.replace(
            _facts_at(prefix, {}), site_roots=(str(site),), extra_roots=(str(other),)
        )
        proj = tmp_path / "proj"
        _write(proj, "plot.py", "import tabulate\n")
        plan = depplan.plan(
            proj, "plot.py", facts=running, target_kind="tavotto_managed", install_facts=_facts({})
        )
        assert plan.missing == ()  # 当前环境里别处已有
        assert plan.requirements == ("tabulate",)  # 新代没有那个目录：照常装

    def test_the_probe_leaves_out_the_standard_library_and_the_reported_site_layers(self):
        import sysconfig

        facts = depplan.target_facts(sys.executable, use_cache=False)
        assert facts is not None
        norm = [os.path.normcase(p) for p in facts.extra_roots]
        for stdlib in (
            os.path.normcase(
                os.path.dirname(os.path.abspath(os.__file__))
            ),  # 真标准库目录（venv 里 sysconfig 不可靠）
            os.path.normcase(os.path.abspath(sysconfig.get_path("stdlib"))),
        ):
            assert all(p != stdlib and not p.startswith(stdlib + os.sep) for p in norm), stdlib
        assert all(os.path.basename(p).lower() not in ("lib-dynload", "dlls") for p in norm)
        assert not set(norm) & {os.path.normcase(p) for p in facts.site_roots}

    def test_the_extra_directories_are_not_a_plan_input(self, tmp_path):
        prefix, site = _env(tmp_path)
        a = _facts_at(prefix, {"six": "1"})
        b = dataclasses.replace(a, extra_roots=(str(tmp_path / "x"),))
        assert a.digest() == b.digest() and a.to_payload() == b.to_payload()


class TestInstallTargetIsJudgedSeparately:
    """P1-2：来源判决只抑制**当前环境**的 missing；装到另一个环境时，当前解释器里的 editable / 本地 / Conda 提供者与缓存的
    import_error 在那里都不存在，按目标的已装集合单独判，否则新一代 import 失败、验证失败。"""

    @pytest.fixture(autouse=True)
    def _clean(self):
        depplan.reset_cache()
        yield
        depplan.reset_cache()

    def _running(self, tmp_path: Path):
        prefix, site = _env(tmp_path)
        _dist(
            site,
            "my-yaml-fork",
            "0.1",
            top="yaml\n",
            record=["yaml/__init__.py"],
            direct_url=_EDITABLE,
        )
        _dist(
            site,
            "myprivate-pkg",
            "0.1",
            top="myprivate\n",
            record=["myprivate/__init__.py"],
            direct_url=_EDITABLE,
        )
        return _facts_at(prefix, {"my-yaml-fork": "0.1", "myprivate-pkg": "0.1"})

    def _project(self, tmp_path: Path, script: str) -> Path:
        proj = tmp_path / "proj"
        _write(proj, "plot.py", script)
        return proj

    def test_a_provider_that_only_exists_in_the_running_interpreter_does_not_spare_the_new_one(
        self, tmp_path
    ):
        proj = self._project(tmp_path, "import yaml\nimport tabulate\n")
        plan = depplan.plan(
            proj,
            "plot.py",
            facts=self._running(tmp_path),
            target_kind="tavotto_managed",
            install_facts=_facts({}),
        )
        assert plan.status == "ready"
        assert [m["distribution"] for m in plan.missing] == ["tabulate"]  # 当前环境缺的只有它
        assert {"pyyaml", "tabulate"} <= set(plan.requirements)  # 新代两个都要装
        assert [o["import_name"] for o in plan.origins] == ["yaml"]  # 对当前环境的来源说明照旧

    def test_a_name_the_trusted_resolution_cannot_give_is_reported_not_guessed(self, tmp_path):
        proj = self._project(tmp_path, "import myprivate\nimport tabulate\n")
        plan = depplan.plan(
            proj,
            "plot.py",
            facts=self._running(tmp_path),
            target_kind="tavotto_managed",
            install_facts=_facts({}),
        )
        assert set(plan.requirements) == {"tabulate"}
        assert "myprivate" not in " ".join(plan.requirements)  # 不从 unknown 猜 PyPI 名
        assert "my-private" not in " ".join(plan.requirements)
        assert plan.unknown == ("myprivate",)
        (origin,) = plan.origins
        assert (
            origin["reason"] == "editable_dependency_not_reproducible"
        )  # 新环境里无法满足，如实标出

    def test_a_cached_import_error_of_the_running_interpreter_does_not_apply_to_the_new_one(
        self, tmp_path
    ):
        proj = self._project(tmp_path, "import yaml\nimport tabulate\n")
        plan = depplan.plan(
            proj,
            "plot.py",
            facts=_facts({}),
            target_kind="tavotto_managed",
            install_facts=_facts({}),
            coverage=lambda names: _health(yaml="import_error"),
        )
        assert [m["distribution"] for m in plan.missing] == ["tabulate"]
        assert {"pyyaml", "tabulate"} <= set(plan.requirements)
        assert [o["reason"] for o in plan.origins] == ["module_import_failed_in_target_environment"]

    def test_what_the_target_already_has_by_name_is_still_not_installed(self, tmp_path):
        proj = self._project(tmp_path, "import yaml\nimport tabulate\n")
        plan = depplan.plan(
            proj,
            "plot.py",
            facts=self._running(tmp_path),
            target_kind="tavotto_managed",
            install_facts=_facts({"pyyaml": "6.0"}),
        )
        assert "pyyaml" not in plan.requirements and "tabulate" in plan.requirements

    def test_ambiguous_cv2_in_the_running_interpreter_installs_the_trusted_name_in_the_new_one(
        self, tmp_path
    ):
        proj = self._project(tmp_path, "import cv2\nimport tabulate\n")
        plan = depplan.plan(
            proj,
            "plot.py",
            facts=_facts({"opencv-python-headless": "4", "opencv-contrib-python": "4"}),
            target_kind="tavotto_managed",
            install_facts=_facts({}),
        )
        assert {"opencv-python", "tabulate"} <= set(plan.requirements)

    def test_an_incomplete_index_of_the_running_interpreter_does_not_spare_the_new_one(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        running = dataclasses.replace(
            _facts_at(prefix, {}),
            site_roots=(str(site), str(tmp_path / "vanished" / "site-packages")),
        )
        proj = self._project(tmp_path, "import tabulate\n")
        plan = depplan.plan(
            proj, "plot.py", facts=running, target_kind="tavotto_managed", install_facts=_facts({})
        )
        assert plan.missing == ()  # 当前环境读不全：不当缺
        assert plan.requirements == ("tabulate",)  # 新代是全新环境：照常装

    def test_installing_into_the_running_interpreter_itself_is_unchanged(self, tmp_path):
        running = self._running(tmp_path)
        proj = self._project(tmp_path, "import yaml\nimport myprivate\nimport tabulate\n")
        base = depplan.plan(proj, "plot.py", facts=running, target_kind="project_venv")
        same = depplan.plan(
            proj, "plot.py", facts=running, target_kind="project_venv", install_facts=running
        )
        assert base.requirements == ("tabulate",)  # editable 的 yaml / myprivate 不装
        for field in ("status", "requirements", "constraints", "identity", "unknown", "origins"):
            assert getattr(base, field) == getattr(same, field), field


# ===========================================================================
# 同源对：depplan 的闭集 ↔ web/src/lib/api.ts ↔ 文案（docs/rules/repo/same-origin-pairs.md）
# ===========================================================================
class TestClosedSetsMirrorTheFrontend:
    API = ROOT / "web" / "src" / "lib" / "api.ts"
    DIALOG = ROOT / "web" / "src" / "components" / "DependencyPrepareDialog.tsx"

    def _api(self) -> str:
        return self.API.read_text(encoding="utf-8")

    def test_block_reasons_are_the_same_on_both_sides_in_the_same_order(self):
        from support.tsconst import exported_string_array

        assert exported_string_array(self._api(), "JOINT_BLOCK_CODES") == list(
            depplan.BLOCK_REASONS
        )

    def test_origin_reasons_are_the_same_on_both_sides_in_the_same_order(self):
        from support.tsconst import exported_string_array

        assert exported_string_array(self._api(), "JOINT_ORIGIN_REASONS") == list(
            depplan.ORIGIN_REASONS
        )

    def test_every_block_reason_has_text_in_both_languages_and_a_dialog_entry(self):
        dialog = self.DIALOG.read_text(encoding="utf-8")
        for lang in ("zh-CN", "en-US"):
            errors = json.loads(
                (ROOT / "web" / "src" / "i18n" / "locales" / lang / "errors.json").read_text(
                    encoding="utf-8"
                )
            )
            for code in depplan.BLOCK_REASONS:
                assert errors["engine"][f"dependencyBlocked_{code}"].strip(), (lang, code)
        for code in depplan.BLOCK_REASONS:
            assert f"{code}: 'engine.dependencyBlocked_{code}'" in dialog, code

    def test_origin_reasons_are_not_published_error_codes(self):
        from tavotto.engine import deprepair

        published = {
            v for k, v in vars(deprepair).items() if k.startswith("ERROR_") and isinstance(v, str)
        }
        assert not set(depplan.ORIGIN_REASONS) & published
        assert not set(depplan.ORIGIN_REASONS) & set(depplan.BLOCK_REASONS)

    def test_the_guard_notices_a_drifted_mirror(self):
        """看护自己要能红：删掉镜像里的一项，比较必须不相等（不是永真断言）。"""
        from support.tsconst import exported_string_array

        drifted = self._api().replace("  'conda_package_not_pypi',\n", "", 1)
        assert exported_string_array(drifted, "JOINT_ORIGIN_REASONS") != list(
            depplan.ORIGIN_REASONS
        )


class TestInputsDigest:
    """`JointPlan.inputs_digest`：规划输入的指纹——与事实无关，只随声明与脚本（及跟进的本地模块）变（Codex #475 P1）。"""

    def _project(self, tmp_path: Path) -> Path:
        _write(tmp_path, "requirements.txt", "numpy\n")
        _write(tmp_path, "helpers.py", "import os\n")
        _write(tmp_path, "plot.py", "import numpy\nimport helpers\n")
        return tmp_path

    def test_digest_is_independent_of_the_target_facts(self, tmp_path):
        proj = self._project(tmp_path)
        standin = _facts()
        real = depplan.TargetFacts(
            python="/other/python",
            marker_env={**standin.marker_env, "python_full_version": "3.13.7"},
            stdlib=frozenset(importscan.HOST_STDLIB) | {"tomllib"},
            installed={"numpy": "2.0.0"},
        )
        a = depplan.plan(proj, "plot.py", facts=standin, target_kind="tavotto_managed")
        b = depplan.plan(proj, "plot.py", facts=real, target_kind="tavotto_managed")
        assert a.inputs_digest and a.inputs_digest == b.inputs_digest
        assert a.to_payload()["inputs_digest"] == a.inputs_digest
        assert a.scan["counts"] and set(importscan.scan(proj, "plot.py").files) == {
            "plot.py",
            "helpers.py",
        }

    @pytest.mark.parametrize(
        "change",
        [
            ("requirements.txt", "numpy\nscipy\n"),
            ("plot.py", "import numpy\nimport helpers\nimport h5py\n"),
            ("helpers.py", "import os\nimport lmfit\n"),
            ("pyproject.toml", '[project]\nname = "x"\ndependencies = ["pandas"]\n'),
            (
                "plot.py",
                "# /// script\n# dependencies = ['xarray']\n# ///\nimport numpy\nimport helpers\n",
            ),
        ],
        ids=["requirements", "script-import", "local-module", "pyproject", "pep723"],
    )
    def test_each_kind_of_input_change_moves_the_digest(self, tmp_path, change):
        proj = self._project(tmp_path)
        before = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        _write(proj, *change)
        after = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert after.inputs_digest != before.inputs_digest

    def test_a_byte_change_without_an_import_change_still_moves_the_digest(self, tmp_path):
        """指纹按字节，不按解析结果：用户看到计划之后改了脚本，哪怕只是注释，也该重新看一眼。"""
        proj = self._project(tmp_path)
        before = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        _write(proj, "plot.py", "import numpy\nimport helpers\n# comment\n")
        after = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert after.inputs_digest != before.inputs_digest

    def test_unrelated_files_do_not_move_the_digest(self, tmp_path):
        proj = self._project(tmp_path)
        before = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        _write(proj, "README.md", "notes\n")
        _write(proj, "unused.py", "import h5py\n")  # 没人 import 它：不是这份计划的输入
        after = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert after.inputs_digest == before.inputs_digest
