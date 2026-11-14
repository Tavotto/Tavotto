"""PR C（ADR 0061 修订 2026-10-21）：依赖声明的读取边界、已知装不了的包、本地模块缺失、找不到的包点名。

* 声明文件只读「脚本目录往上到最近的项目标志」（.git / pyproject.toml / setup.cfg / setup.py）为止；家目录、桌面 / 文档 / 下载
  这类大文件夹不读（不越过家目录）；PEP 723 只看脚本本身；
* `ROOT` 之类 curated「不可 pip 安装」的包：计划阶段点名、不预填、不送 pip；
* 脚本用 `sys.path.append(os.path.join(os.path.dirname(__file__), "pdf"))` 加进来的目录是本地模块搜索根（只认字面量）；找不到的
  import 是「本地模块缺失」，不是缺 PyPI 包，绝不送 pip；
* pip 报「找不到」时点名：哪个包、来自哪份声明文件 / 哪个 import。
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from tavotto.engine import depplan, deprepair, depresolve, importscan

MANAGED = depplan.TARGET_MANAGED
#: 与 tmp_path 无祖先关系的家目录（放宽读边界不得越过家目录或它的祖先）
_UNRELATED_HOME = Path("/nonexistent-tavotto-test-home/u")


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def _facts() -> depplan.TargetFacts:
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
    }
    return depplan.TargetFacts(
        python="/fake/python",
        marker_env=marker_env,
        stdlib=importscan.HOST_STDLIB,
        installed={"matplotlib": "3.10.0", "numpy": "2.1.0"},
        prefix="/fake",
    )


@pytest.fixture(autouse=True)
def _clean():
    depplan.reset_cache()
    yield
    depplan.reset_cache()


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    return h


def _names(intents) -> set[str]:
    return {i.name for i in intents if i.declared}


class TestDeclarationBoundary:
    def test_desktop_root_is_not_read(self, home):
        desk = home / "Desktop"
        _write(desk, "requirements.txt", "lhcbstyle\npdf-minus-bin1-m\n")
        _write(desk, "fit_mass.py", "import numpy\n")
        assert _names(depresolve.declared_intents(desk, "fit_mass.py")) == set()

    @pytest.mark.parametrize("sub", ["桌面", "Documents", "文档", "Downloads", "下载"])
    def test_other_broad_folders(self, home, sub):
        root = home / sub
        _write(root, "requirements.txt", "junk\n")
        _write(root, "a.py", "import numpy\n")
        assert _names(depresolve.declared_intents(root, "a.py")) == set()

    def test_home_itself_is_not_read(self, home):
        _write(home, "requirements.txt", "junk\n")
        _write(home, "a.py", "x = 1\n")
        assert _names(depresolve.declared_intents(home, "a.py")) == set()

    def test_script_in_desktop_subfolder_stops_before_desktop(self, home):
        desk = home / "Desktop"
        _write(desk, "requirements.txt", "junk\n")
        _write(desk, "proj/requirements.txt", "tabulate\n")
        _write(desk, "proj/a.py", "x = 1\n")
        # 项目根是桌面、脚本在子文件夹里：读子文件夹自己的，不读桌面的
        assert _names(depresolve.declared_intents(desk, "proj/a.py")) == {"tabulate"}

    def test_pep723_still_read_on_desktop(self, home):
        desk = home / "Desktop"
        _write(
            desk,
            "a.py",
            '# /// script\n# dependencies = ["tabulate"]\n# ///\nimport tabulate\n',
        )
        assert _names(depresolve.declared_intents(desk, "a.py")) == {"tabulate"}

    @pytest.mark.parametrize("marker", [".git", "pyproject.toml", "setup.cfg", "setup.py"])
    def test_nearest_marker_is_the_boundary(self, tmp_path, home, marker):
        root = tmp_path / "work"
        _write(root, "requirements.txt", "outer-pkg\n")
        inner = root / "proj"
        if marker == ".git":
            (inner / ".git").mkdir(parents=True)
        else:
            _write(inner, marker, "[project]\nname='p'\n" if marker.endswith("toml") else "")
        _write(inner, "requirements.txt", "inner-pkg\n")
        _write(inner, "sub/a.py", "x = 1\n")
        names = _names(depresolve.declared_intents(root, "proj/sub/a.py"))
        assert "inner-pkg" in names
        assert "outer-pkg" not in names

    def test_without_marker_reads_up_to_project_root(self, tmp_path, home):
        root = tmp_path / "work"
        _write(root, "requirements.txt", "outer-pkg\n")
        _write(root, "sub/a.py", "x = 1\n")
        assert "outer-pkg" in _names(depresolve.declared_intents(root, "sub/a.py"))

    def test_no_follow_walk_uses_same_boundary(self, tmp_path, home):
        root = tmp_path / "work"
        _write(root, "requirements.txt", "outer-pkg\n")
        _write(root / "proj", "pyproject.toml", "[project]\nname='p'\ndependencies=['inner-pkg']\n")
        _write(root / "proj", "sub/a.py", "x = 1\n")
        names = _names(depresolve.declared_intents(root, "proj/sub/a.py", no_follow=True))
        assert "inner-pkg" in names and "outer-pkg" not in names


class TestNotPipInstallable:
    def test_root_is_named_and_never_sent_to_pip(self, tmp_path):
        proj = tmp_path / "p"
        _write(proj, "a.py", "import ROOT\nimport numpy\n")
        plan = depplan.plan(proj, "a.py", facts=_facts(), target_kind=MANAGED)
        assert plan.requirements == ()
        origin = next(o for o in plan.origins if o["import_name"] == "ROOT")
        assert origin["not_pip_installable"]["label"] == "ROOT"
        assert "suggested_distribution" not in origin

    def test_no_prefill_and_user_specified_rejected(self, tmp_path):
        assert depresolve.suggest_distribution("ROOT") == ""
        proj = tmp_path / "p"
        _write(proj, "a.py", "import ROOT\n")
        with pytest.raises(depplan.UserSpecifiedRejected):
            depplan.plan(
                proj, "a.py", facts=_facts(), target_kind=MANAGED, user_specified={"ROOT": "root"}
            )

    def test_declared_root_is_filtered_with_its_source(self, tmp_path):
        proj = tmp_path / "p"
        _write(proj, "a.py", "import ROOT\n")
        _write(proj, "requirements.txt", "root\ntabulate\n")
        plan = depplan.plan(proj, "a.py", facts=_facts(), target_kind=MANAGED)
        assert all(not r.startswith("root") for r in plan.requirements)
        entry = plan.to_payload()["not_pip_installable"][0]
        assert entry["name"] == "ROOT" and entry["sources"] == ["requirements.txt"]

    @pytest.mark.parametrize("module", ["artifact_tool", "ace_tools", "caas_jupyter_tools"])
    def test_ai_sandbox_modules_are_named_and_never_sent_to_pip(self, tmp_path, module):
        """TVD-NH7S：ChatGPT 生成的脚本 `import artifact_tool`，预填的 `artifact-tool` 被送进 pip、整批依赖一起失败。"""
        proj = tmp_path / "p"
        _write(proj, "a.py", f"import {module}\nimport numpy\n")
        plan = depplan.plan(proj, "a.py", facts=_facts(), target_kind=MANAGED)
        assert plan.requirements == ()
        origin = next(o for o in plan.origins if o["import_name"] == module)
        assert origin["not_pip_installable"] == {
            "label": module,
            "hint": depresolve.NOT_PIP_HINT_AI_SANDBOX,
        }
        assert "suggested_distribution" not in origin
        assert depresolve.suggest_distribution(module) == ""
        with pytest.raises(depplan.UserSpecifiedRejected):
            depplan.plan(
                proj,
                "a.py",
                facts=_facts(),
                target_kind=MANAGED,
                user_specified={module: module.replace("_", "-")},
            )

    def test_ai_sandbox_offer_names_the_hint(self, tmp_path):
        proj = tmp_path / "p"
        _write(proj, "a.py", "import artifact_tool\n")
        out = deprepair.offer(proj, "a.py", "artifact_tool")
        assert out["requirement"] is None
        assert out["not_pip_installable"]["hint"] == depresolve.NOT_PIP_HINT_AI_SANDBOX

    def test_table_is_structured_for_more_entries(self):
        assert depresolve.not_pip_installable("ROOT")["hint"] == depresolve.NOT_PIP_HINT_OWN_ENV
        assert depresolve.not_pip_installable("numpy") is None
        assert depresolve.not_pip_for_distribution("Root") is not None


FIT_MASS = """
import ROOT as rt
import lhcbStyle
import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), "pdf"))
from pdf_plus_bin1_M import pdf_plus_bin1_M
from pdf_minus_bin1_R import pdf_minus_bin1_R
"""


class TestLiteralSysPathRoots:
    def _plan(self, proj, **kw):
        return depplan.plan(proj, "fit_mass.py", facts=_facts(), target_kind=MANAGED, **kw)

    def test_pdf_dir_present_modules_are_local(self, tmp_path):
        proj = tmp_path / "p"
        _write(proj, "fit_mass.py", FIT_MASS)
        _write(proj, "pdf/pdf_plus_bin1_M.py", "pdf_plus_bin1_M = 1\n")
        _write(proj, "pdf/pdf_minus_bin1_R.py", "pdf_minus_bin1_R = 1\n")
        plan = self._plan(proj)
        assert {o["import_name"] for o in plan.origins} == {"ROOT", "lhcbStyle"}
        assert plan.requirements == ()

    def test_pdf_dir_missing_is_local_module_missing_not_pip(self, tmp_path):
        proj = tmp_path / "p"
        _write(proj, "fit_mass.py", FIT_MASS)
        plan = self._plan(proj)
        origins = {o["import_name"]: o for o in plan.origins}
        assert origins["pdf_plus_bin1_M"]["local_module_missing"] == {"search_dirs": ["pdf"]}
        assert all("suggested_distribution" not in o for o in plan.origins)
        assert plan.requirements == ()
        with pytest.raises(depplan.UserSpecifiedRejected):
            self._plan(proj, user_specified={"pdf_plus_bin1_M": "pdf-plus-bin1-m"})

    def test_without_literal_dir_prefill_still_offered(self, tmp_path):
        # 对照：没有 sys.path 字面量时，表外名的预填照旧（C1 裁决不变）
        proj = tmp_path / "p"
        _write(proj, "fit_mass.py", "import lhcbStyle\n")
        plan = self._plan(proj)
        assert plan.origins[0]["suggested_distribution"] == "lhcbstyle"

    @pytest.mark.parametrize(
        "expr",
        [
            'os.path.join(os.path.dirname(__file__), "pdf")',
            'os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf")',
            'os.path.join(os.path.dirname(os.path.realpath(__file__)), "pdf")',
            'str(Path(__file__).parent / "pdf")',
            'str(Path(__file__).resolve().parent / "pdf")',
            'os.path.dirname(__file__) + "/pdf"'
            if False
            else 'os.path.join(os.path.dirname(__file__), "pdf")',
        ],
    )
    def test_literal_forms_recognized(self, tmp_path, expr):
        proj = tmp_path / "p"
        _write(proj, "pdf/mymod.py", "x = 1\n")
        _write(
            proj,
            "a.py",
            f"import os, sys\nfrom pathlib import Path\nsys.path.insert(0, {expr})\nimport mymod\n",
        )
        scan = importscan.scan(proj, "a.py")
        cls = next(c for c in scan.classes if c.module == "mymod")
        assert cls.bucket == importscan.BUCKET_LOCAL
        assert any(r["tag"] == "sys_path_literal" for r in scan.search_roots)

    def test_parent_of_script_dir_and_subdir_script(self, tmp_path):
        proj = tmp_path / "p"
        _write(proj, "libs/helper.py", "x = 1\n")
        _write(
            proj,
            "scripts/a.py",
            'import os, sys\nsys.path.append(os.path.join(os.path.dirname(__file__), "..", "libs"))\nimport helper\n',
        )
        scan = importscan.scan(proj, "scripts/a.py")
        assert (
            next(c for c in scan.classes if c.module == "helper").bucket == importscan.BUCKET_LOCAL
        )

    @pytest.mark.parametrize(
        "expr",
        [
            "os.environ['X']",
            "some_var",
            'os.path.join(base, "pdf")',
            '"pdf"',
            "os.path.join(os.path.dirname(__file__), name)",
            'os.path.join("/abs", "pdf")',
        ],
    )
    def test_non_literal_forms_ignored(self, tmp_path, expr):
        proj = tmp_path / "p"
        _write(proj, "pdf/mymod.py", "x = 1\n")
        _write(proj, "a.py", f"import os, sys\nsys.path.append({expr})\nimport mymod\n")
        scan = importscan.scan(proj, "a.py")
        assert not any(r["tag"] == "sys_path_literal" for r in scan.search_roots)
        assert (
            next(c for c in scan.classes if c.module == "mymod").bucket != importscan.BUCKET_LOCAL
        )

    def test_escape_outside_project_is_a_bounded_root(self, tmp_path, monkeypatch):
        """维护者裁决 2026-11-02（TVD-22EY）：由 `__file__` 推出的项目外目录也认，读的边界有界放宽
        （上限与家目录 / 大文件夹的拒绝见 `TestLiteralDirOutsideProject`）；此前这里断言「越界不认」。"""
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: _UNRELATED_HOME))
        proj = tmp_path / "p"
        _write(tmp_path, "outside/mymod.py", "x = 1\n")
        _write(
            proj,
            "a.py",
            'import os, sys\nsys.path.append(os.path.join(os.path.dirname(__file__), "..", "outside"))\nimport mymod\n',
        )
        scan = importscan.scan(proj, "a.py")
        assert any(r["tag"] == "sys_path_literal" for r in scan.search_roots)
        assert next(c for c in scan.classes if c.module == "mymod").bucket == (
            importscan.BUCKET_LOCAL
        )

    @pytest.mark.parametrize(
        "wrap",
        [
            "if False:\n    {stmt}",
            "if FLAG:\n    pass\nelse:\n    {stmt}",
            "try:\n    {stmt}\nexcept Exception:\n    pass",
            "for _ in range(0):\n    {stmt}",
            "def f():\n    {stmt}",
        ],
    )
    def test_conditional_or_deferred_mutation_is_not_a_root(self, tmp_path, wrap):
        """Codex #1276 P2：条件分支 / 函数体里的 sys.path 修改可能根本没执行，不能当搜索根。"""
        proj = tmp_path / "p"
        _write(proj, "pdf/mymod.py", "x = 1\n")
        stmt = 'sys.path.append(os.path.join(os.path.dirname(__file__), "pdf"))'
        _write(
            proj,
            "a.py",
            "import os, sys\nFLAG = 1\n" + wrap.format(stmt=stmt) + "\nimport mymod\n",
        )
        scan = importscan.scan(proj, "a.py")
        assert not any(r["tag"] == "sys_path_literal" for r in scan.search_roots)
        assert (
            next(c for c in scan.classes if c.module == "mymod").bucket != importscan.BUCKET_LOCAL
        )

    @pytest.mark.parametrize(
        "expr",
        [
            'os.path.join(os.path.dirname(__file__), "/plugins")',
            'os.path.join(os.path.dirname(__file__), "\\\\plugins")',
            'os.path.join(os.path.dirname(__file__), "C:plugins")',
            'Path(__file__).parent / "/plugins"',
        ],
    )
    def test_absolute_component_discards_prefix_so_not_a_root(self, tmp_path, expr):
        """Codex #1276 P2：后续分量是绝对路径时 Python 丢掉前缀；不能拼成 `<项目>/plugins` 去扫。"""
        proj = tmp_path / "p"
        _write(proj, "plugins/mymod.py", "x = 1\n")
        _write(
            proj,
            "a.py",
            f"import os, sys\nfrom pathlib import Path\nsys.path.append({expr})\nimport mymod\n",
        )
        scan = importscan.scan(proj, "a.py")
        assert not any(r["tag"] == "sys_path_literal" for r in scan.search_roots)
        assert (
            next(c for c in scan.classes if c.module == "mymod").bucket != importscan.BUCKET_LOCAL
        )

    def test_literal_roots_are_bounded(self):
        calls = "\n".join(
            f'sys.path.append(os.path.join(os.path.dirname(__file__), "d{i}"))' for i in range(20)
        )
        tree = ast.parse("import os, sys\n" + calls)
        assert tree  # 语法可解析；上限由 _MAX_LITERAL_ROOTS 保证
        assert importscan._MAX_LITERAL_ROOTS == 8


class TestLiteralDirThroughModuleName:
    """反馈包 TVD-705N：`ROOT = Path(__file__).resolve().parent` 再 `sys.path.insert(0, str(ROOT / "source"))`。
    名字在模块层无条件地只绑定一次、值本身就是字面量目录时，按那个值认；`source/` 缺了是本地模块缺失，不预填包名。"""

    SCRIPT = (
        "from pathlib import Path\n"
        "import sys\n"
        "ROOT = Path(__file__).resolve().parent\n"
        "sys.path.insert(0, str(ROOT / 'source'))\n"
        "import config as c\n"
        "from yard_map import YardMap\n"
    )

    def test_present_dir_modules_are_local(self, tmp_path):
        proj = tmp_path / "p"
        _write(proj, "gen.py", self.SCRIPT)
        _write(proj, "source/config.py", "X = 1\n")
        _write(proj, "source/yard_map.py", "class YardMap: ...\n")
        scan = importscan.scan(proj, "gen.py")
        assert any(r["tag"] == "sys_path_literal" for r in scan.search_roots)
        for name in ("config", "yard_map"):
            cls = next(c for c in scan.classes if c.module == name)
            assert cls.bucket == importscan.BUCKET_LOCAL

    def test_missing_dir_after_runtime_failure_is_local_module_missing(self, tmp_path):
        proj = tmp_path / "p"
        _write(proj, "gen.py", self.SCRIPT)
        plan = depplan.plan(
            proj,
            "gen.py",
            facts=_facts(),
            target_kind=MANAGED,
            extra_imports=("config", "yard_map"),
        )
        origins = {o["import_name"]: o for o in plan.origins}
        assert origins["config"]["local_module_missing"] == {"search_dirs": ["source"]}
        assert origins["yard_map"]["local_module_missing"] == {"search_dirs": ["source"]}
        assert all("suggested_distribution" not in o for o in plan.origins)
        assert plan.requirements == ()
        with pytest.raises(depplan.UserSpecifiedRejected):
            depplan.plan(
                proj,
                "gen.py",
                facts=_facts(),
                target_kind=MANAGED,
                extra_imports=("config",),
                user_specified={"config": "config"},
            )

    @pytest.mark.parametrize(
        "binding",
        [
            "ROOT = Path(__file__).parent",
            "ROOT = os.path.dirname(os.path.abspath(__file__))",
            "HERE = Path(__file__).resolve().parent\nROOT = HERE",
        ],
    )
    def test_binding_forms_recognized(self, tmp_path, binding):
        proj = tmp_path / "p"
        _write(proj, "source/mymod.py", "x = 1\n")
        _write(
            proj,
            "a.py",
            "import os, sys\nfrom pathlib import Path\n"
            + binding
            + "\nsys.path.append(os.path.join(ROOT, 'source'))\nimport mymod\n",
        )
        scan = importscan.scan(proj, "a.py")
        assert (
            next(c for c in scan.classes if c.module == "mymod").bucket == importscan.BUCKET_LOCAL
        )

    @pytest.mark.parametrize(
        "body",
        [
            # 绑定两次（任何一处再绑定都不认，含函数体里的 global）
            "ROOT = Path(__file__).parent\nROOT = Path('/elsewhere')\n{call}",
            "ROOT = Path(__file__).parent\ndef f():\n    global ROOT\n    ROOT = 1\n{call}",
            "ROOT = Path(__file__).parent\nfor ROOT in []:\n    pass\n{call}",
            "ROOT = Path(__file__).parent\nimport x as ROOT\n{call}",
            # 条件绑定、绑定在调用之后、值不是字面量目录
            "if FLAG:\n    ROOT = Path(__file__).parent\n{call}",
            "{call}\nROOT = Path(__file__).parent",
            "ROOT = Path(os.getcwd())\n{call}",
            "ROOT = Path(__file__).parent\nROOT /= 'x'\n{call}",
            # 星号导入 / 动态命名空间改写能悄悄换掉 ROOT（Codex #1582 P2）
            "ROOT = Path(__file__).parent\nfrom settings import *\n{call}",
            "ROOT = Path(__file__).parent\nglobals()['ROOT'] = Path('/elsewhere')\n{call}",
            'ROOT = Path(__file__).parent\nexec("ROOT = 1")\n{call}',
        ],
    )
    def test_rebound_or_non_literal_binding_ignored(self, tmp_path, body):
        proj = tmp_path / "p"
        _write(proj, "source/mymod.py", "x = 1\n")
        call = "sys.path.append(str(ROOT / 'source'))"
        _write(
            proj,
            "a.py",
            "import os, sys\nfrom pathlib import Path\nFLAG = 1\n"
            + body.format(call=call)
            + "\nimport mymod\n",
        )
        scan = importscan.scan(proj, "a.py")
        assert not any(r["tag"] == "sys_path_literal" for r in scan.search_roots)


class TestNotFoundNamed:
    def test_parses_name_and_source_file(self, tmp_path):
        proj = tmp_path / "p"
        _write(proj, "a.py", "import numpy\n")
        _write(proj, "requirements.txt", "tabulate\nghost-pkg>=1\n")
        out = (
            "ERROR: Could not find a version that satisfies the requirement Ghost_Pkg>=1 (from versions: none)\n"
            "ERROR: No matching distribution found for Ghost_Pkg>=1\n"
        )
        detail = deprepair.not_found_detail(
            out, [{"import_name": "ghost", "distribution": "ghost-pkg"}], proj, "a.py"
        )
        assert detail == {"name": "ghost-pkg", "import_name": "ghost", "source": "requirements.txt"}

    def test_unrecognized_output_gives_nothing(self, tmp_path):
        assert deprepair.not_found_detail("boom", [], tmp_path, "a.py") == {}

    def test_extra_only_for_not_found(self, tmp_path):
        out = "ERROR: No matching distribution found for foo"
        assert deprepair._not_found_extra(deprepair.ERROR_CONFLICT, out, [], tmp_path, "a.py") == {}
        got = deprepair._not_found_extra(deprepair.ERROR_NOT_FOUND, out, [], tmp_path, "a.py")
        assert got["not_found"]["name"] == "foo"


class TestCodexReview1276:
    """Codex #1276：P1 装不了的包要出整份可执行计划；P2 字面量根要看顺序与执行上下文；P2 no_follow 不解引用标志。"""

    def test_p1_not_pip_import_is_not_left_in_missing(self, tmp_path):
        proj = tmp_path / "p"
        _write(proj, "a.py", "import ROOT\n")
        _write(proj, "requirements.txt", "root\n")
        plan = depplan.plan(proj, "a.py", facts=_facts(), target_kind=MANAGED)
        assert plan.missing == ()
        assert plan.requirements == ()
        assert plan.status == depplan.STATUS_NOTHING_NEEDED
        assert plan.to_payload()["not_pip_installable"][0]["distribution"] == "root"

    def test_p1_hash_mode_does_not_reintroduce_not_pip(self, tmp_path):
        proj = tmp_path / "p"
        _write(proj, "a.py", "import ROOT\nimport tabulate\n")
        _write(
            proj,
            "requirements.txt",
            "root==6.0 --hash=sha256:"
            + "a" * 64
            + "\ntabulate==0.9.0 --hash=sha256:"
            + "b" * 64
            + "\n",
        )
        plan = depplan.plan(proj, "a.py", facts=_facts(), target_kind=MANAGED)
        assert all(not r.startswith("root") for r in plan.requirements)
        assert not any(k.startswith("root") for k in plan.hashes)
        assert not any("root" in str(b.get("lines", "")) for b in plan.blocked)

    def test_p2_literal_dir_added_after_import_does_not_apply(self, tmp_path):
        proj = tmp_path / "p"
        _write(proj, "pdf/helper.py", "x = 1\n")
        _write(
            proj,
            "a.py",
            'import helper\nimport sys, os\nsys.path.append(os.path.join(os.path.dirname(__file__), "pdf"))\n',
        )
        scan = importscan.scan(proj, "a.py")
        cls = next(c for c in scan.classes if c.module == "helper")
        assert cls.bucket != importscan.BUCKET_LOCAL

    def test_p2_literal_dir_inside_function_does_not_apply(self, tmp_path):
        proj = tmp_path / "p"
        _write(proj, "pdf/helper.py", "x = 1\n")
        _write(
            proj,
            "a.py",
            "import sys, os\n"
            "def setup():\n"
            '    sys.path.append(os.path.join(os.path.dirname(__file__), "pdf"))\n'
            "import helper\n",
        )
        scan = importscan.scan(proj, "a.py")
        assert not any(r["tag"] == "sys_path_literal" for r in scan.search_roots)
        cls = next(c for c in scan.classes if c.module == "helper")
        assert cls.bucket != importscan.BUCKET_LOCAL

    @pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="no symlink")
    def test_p2_no_follow_marker_is_not_dereferenced(self, tmp_path, home, monkeypatch):
        proj = tmp_path / "p"
        _write(proj, "sub/a.py", "x = 1\n")
        outside = tmp_path / "outside"
        outside.mkdir()
        try:
            (proj / ".git").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("cannot symlink")
        seen: list[str] = []
        real_exists = Path.exists

        def spy(self, *a, **k):
            seen.append(str(self))
            return real_exists(self, *a, **k)

        monkeypatch.setattr(Path, "exists", spy)
        dirs = depresolve._decl_dirs(proj, "sub/a.py", no_follow=True)
        assert not any(s.endswith(".git") for s in seen)
        assert dirs and dirs[-1].name == "sub" or proj.name in {d.name for d in dirs}


class TestSinglePackageFallbackSkipsNotPip:
    """Codex #1276 P1：联合计划摘掉 ROOT 后，`offer` / `create_plan` 兜底路径同样不把它当可装的包。"""

    def _project(self, tmp_path):
        proj = tmp_path / "p"
        _write(proj, "a.py", "import ROOT\n")
        _write(proj, "requirements.txt", "root\n")
        return proj

    def test_offer_names_own_env_and_gives_no_requirement(self, tmp_path):
        proj = self._project(tmp_path)
        out = deprepair.offer(proj, "a.py", "ROOT")
        assert out["requirement"] is None
        assert out["code"] == deprepair.ERROR_UNRESOLVED
        assert out["not_pip_installable"] == {
            "label": "ROOT",
            "hint": depresolve.NOT_PIP_HINT_OWN_ENV,
        }
        assert not any(t["kind"] == deprepair.TARGET_MANAGED for t in out["targets"])

    def test_create_plan_refuses_with_same_naming(self, tmp_path, monkeypatch):
        proj = self._project(tmp_path)
        monkeypatch.setattr(deprepair, "_refuse_if_pinned", lambda: None)
        with pytest.raises(deprepair.RepairError) as ei:
            deprepair.create_plan(proj, "a.py", "ROOT", target_kind=deprepair.TARGET_MANAGED)
        assert ei.value.code == deprepair.ERROR_UNRESOLVED
        assert ei.value.extra["not_pip_installable"]["label"] == "ROOT"

    def test_user_typed_root_is_refused_too(self, tmp_path, monkeypatch):
        proj = self._project(tmp_path)
        monkeypatch.setattr(deprepair, "_refuse_if_pinned", lambda: None)
        with pytest.raises(deprepair.RepairError) as ei:
            deprepair.create_plan(
                proj, "a.py", "ROOT", target_kind=deprepair.TARGET_MANAGED, user_distribution="root"
            )
        assert ei.value.extra["not_pip_installable"]["label"] == "ROOT"

    def test_ordinary_missing_package_still_offered(self, tmp_path):
        proj = tmp_path / "p"
        _write(proj, "a.py", "import lmfit\n")
        out = deprepair.offer(proj, "a.py", "lmfit")
        assert out["requirement"] is not None and "not_pip_installable" not in out


class TestLiteralDirOutsideProject:
    """反馈包 TVD-22EY：`O=Path(__file__).resolve().parent;ROOT=O.parents[1];sys.path.insert(0,str(ROOT))`——
    本地模块在项目上两级的目录里，它用着 pandas / sklearn。扫描要认出 `.parents[N]`、同一行分号连写的先后，
    并把读的边界放宽到那一级祖先（有界、不越过家目录 / 大文件夹）。"""

    SCRIPT = (
        "from pathlib import Path\n"
        "import sys\n"
        "import numpy as np\n"
        "O=Path(__file__).resolve().parent;ROOT=O.parents[1];sys.path.insert(0,str(ROOT))\n"
        "from helper_mod import load\n"
    )
    HELPER = "import pandas as pd\nfrom sklearn.cluster import KMeans\ndef load(p): return p\n"

    @staticmethod
    def _needed(scan) -> set[str]:
        return {c.module for c in scan.needed}

    def test_parents_index_on_the_same_line_inside_project(self, tmp_path):
        proj = tmp_path / "p"
        # 放在 lib/ 下：项目根本身就是 safe worker 的搜索根，放根里证明不了 `.parents[N]` 认出来了
        _write(proj, "a/b/gen.py", self.SCRIPT.replace("str(ROOT)", "str(ROOT / 'lib')"))
        _write(proj, "lib/helper_mod.py", self.HELPER)
        scan = importscan.scan(proj, "a/b/gen.py")
        assert next(c for c in scan.classes if c.module == "helper_mod").bucket == (
            importscan.BUCKET_LOCAL
        )
        assert {"pandas", "sklearn"} <= self._needed(scan)

    def test_root_two_levels_above_the_project_is_followed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: _UNRELATED_HOME))
        base = tmp_path / "Radscore"
        proj = base / "analysis_outputs" / "case_v1"
        _write(proj, "render.py", self.SCRIPT)
        _write(base, "helper_mod.py", self.HELPER)
        scan = importscan.scan(proj, "render.py")
        assert any(r["tag"] == "sys_path_literal" for r in scan.search_roots)
        assert {"pandas", "sklearn"} <= self._needed(scan)
        assert "helper_mod" not in {c.module for c in scan.by_bucket(importscan.BUCKET_UNKNOWN)}

    def test_too_far_above_the_project_is_not_followed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: _UNRELATED_HOME))
        base = tmp_path / "Radscore"
        proj = base / "a" / "b" / "c" / "case_v1"
        _write(proj, "render.py", self.SCRIPT.replace("parents[1]", "parents[3]"))
        _write(base, "helper_mod.py", self.HELPER)
        assert importscan._MAX_OUTSIDE_LEVELS == 3
        scan = importscan.scan(proj, "render.py")
        assert not ({"pandas", "sklearn"} & self._needed(scan))

    def test_home_and_its_ancestors_are_never_the_bound(self, tmp_path, monkeypatch):
        base = tmp_path / "Radscore"
        proj = base / "analysis_outputs" / "case_v1"
        # 家目录就是 Radscore：放宽到它 = 读整个家目录，不认
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: base))
        _write(proj, "render.py", self.SCRIPT)
        _write(base, "helper_mod.py", self.HELPER)
        scan = importscan.scan(proj, "render.py")
        assert not ({"pandas", "sklearn"} & self._needed(scan))
        # 家目录在 Radscore 下面：Radscore 是家目录的祖先，同样不认
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: base / "analysis_outputs"))
        scan = importscan.scan(proj, "render.py")
        assert not ({"pandas", "sklearn"} & self._needed(scan))

    def test_external_helper_adds_a_dir_relative_to_its_own_file(self, tmp_path, monkeypatch):
        """项目外的辅助模块再按它**自己**的 `__file__` 加目录：目录相对它所在处，而不是项目（Codex #1634 P2）。"""
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: _UNRELATED_HOME))
        base = tmp_path / "Radscore"
        proj = base / "analysis_outputs" / "case_v1"
        _write(proj, "render.py", self.SCRIPT)
        _write(
            base,
            "helper_mod.py",
            "import sys\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).parent / 'deps'))\n"
            "import deep_dep\ndef load(p): return p\n",
        )
        _write(base, "deps/deep_dep.py", "import pandas as pd\n")
        # 项目里同名的 deps/ 是诱饵：按项目解析就会读到它而漏掉 pandas
        _write(proj, "deps/deep_dep.py", "x = 1\n")
        scan = importscan.scan(proj, "render.py")
        assert "pandas" in self._needed(scan)
