"""统一实施包 U04（ADR 0061）第一层：无损解析 → import 分类 → 联合计划（纯逻辑，不装任何东西）。

三个模块各一节：`depresolve` 的 intent 读法（成熟解析器、有界 include、PEP 723 / 735、
unsupported 闭集、3.10 无 tomllib 的分支）、`importscan`（stdlib / 本地 / 第三方 / 未知 ×
六种上下文、本地模块有界跟进）、`depplan`（目标事实、按组与 marker 选择、缺什么、交给安装器
的集合、blocked 的四种理由、身份不含路径）。负例多于正例：每一条「不装」都要有用例钉着。
"""

from __future__ import annotations

import builtins
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

from tavotto.engine import depplan, depresolve, importscan

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
        proj = self._project(tmp_path, "import six\n", f"six==1.17.0 {h}\ntabulate==0.9.0 {h}\n")
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.status == "ready" and plan.require_hashes
        assert plan.requirements == ("six==1.17.0", "tabulate==0.9.0")
        assert plan.hashes == {
            "six==1.17.0": ("sha256:" + "a" * 64,),
            "tabulate==0.9.0": ("sha256:" + "a" * 64,),
        }
        assert plan.constraints == ()
        proj2 = self._project(tmp_path / "b", "import six\n", f"six==1.17.0 {h}\ntabulate==0.9.0\n")
        plan2 = depplan.plan(proj2, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan2.status == "blocked"
        assert plan2.blocked[0]["code"] == "dependency_hashes_incomplete"
        assert plan2.blocked[0]["lines"] == ["tabulate==0.9.0"]

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
