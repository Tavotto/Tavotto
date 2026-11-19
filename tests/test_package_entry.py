"""包里的脚本用相对导入时按模块运行（ADR 0196）：判据与 spec / argv 的模型契约。

不起任何子进程——验的是 AST 判据（`engine/pkgentry.resolve`）与它在 `execspec` 里的装配：

* 判据：一层包 / 多层包 / 包根越出项目 / 缺某一层 `__init__.py` / 没有相对导入 / 函数入口 / 语法错误 / 嵌套的相对导入 /
  `level` 超过包层数 / 符号链接 / 非法标识符；判不出一律「不改」（返回 None）。
* 装配：`safe_spec` / `desktop_native_spec` 自己调判据，**身份字段（target / target_kind / stable_payload）一字不变**，
  只多 `module_name`；终端的 `native_spec`（`tavotto run`）永远不带；没有相对导入的脚本 argv 与 golden 逐字节相同。

真进程的对拍与端到端见 `test_package_entry_parity.py` / `test_package_entry_e2e.py`。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tavotto.engine import bridgeboot, execspec, pkgentry

REL = "from .helpers import tidy\n"


def _write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _pkg(root: Path, *dirs: str, body: str = REL, name: str = "fig.py") -> str:
    """`root/<dirs…>/` 每一级都放 `__init__.py`，最里面放 `name`；返回脚本的项目相对路径。"""
    cur = root
    for d in dirs:
        cur = cur / d
        _write(cur / "__init__.py")
    _write(cur / name, body)
    return "/".join([*dirs, name])


class TestCriterion:
    def test_one_level_package(self, tmp_path):
        script = _pkg(tmp_path, "plots")
        got = pkgentry.resolve(tmp_path, script)
        assert got is not None and got.module == "plots.fig"
        assert got.root == str(tmp_path)  # 包根的上一级 = sys.path[0]

    def test_multi_level_package_names_the_whole_chain(self, tmp_path):
        script = _pkg(tmp_path, "paper", "figs", "panels")
        got = pkgentry.resolve(tmp_path, script)
        assert got is not None and got.module == "paper.figs.panels.fig"
        assert got.root == str(tmp_path)

    def test_the_package_may_live_below_the_project_root(self, tmp_path):
        """`src/plots/fig.py`：包根的上一级是 `src/`，不是项目根。"""
        script = _pkg(tmp_path, "src", "plots", name="fig.py")  # src 也有 __init__ → 包根是 src
        assert pkgentry.resolve(tmp_path, script).module == "src.plots.fig"
        (tmp_path / "src" / "__init__.py").unlink()  # src 不是包 → 包根 plots，sys.path[0] = src
        got = pkgentry.resolve(tmp_path, script)
        assert got.module == "plots.fig" and got.root == str(tmp_path / "src")

    def test_the_topmost_init_decides_the_root(self, tmp_path):
        """中间缺一层 `__init__.py`：包根是缺口之上最近的那一层（`python -m` 能解析到的最长链）。"""
        script = _pkg(tmp_path, "a", "b", "c")
        (tmp_path / "a" / "b" / "__init__.py").unlink()  # a 有、b 缺、c 有
        got = pkgentry.resolve(tmp_path, script)
        assert got.module == "c.fig" and got.root == str(tmp_path / "a" / "b")

    def test_the_script_directory_must_itself_be_a_package(self, tmp_path):
        script = _pkg(tmp_path, "plots")
        (tmp_path / "plots" / "__init__.py").unlink()  # 命名空间包：不改
        assert pkgentry.resolve(tmp_path, script) is None

    def test_a_script_without_relative_imports_is_not_touched(self, tmp_path):
        script = _pkg(tmp_path, "plots", body="import os\nfrom helpers import tidy\n")
        assert pkgentry.resolve(tmp_path, script) is None

    def test_a_function_entry_is_not_touched(self, tmp_path):
        script = _pkg(tmp_path, "plots")
        assert pkgentry.resolve(tmp_path, script, "main") is None
        assert pkgentry.resolve(tmp_path, script, "__main__") is not None
        assert (
            pkgentry.resolve(tmp_path, script, None) is not None
        )  # native：整份脚本，没有登记入口

    def test_a_syntax_error_is_not_touched(self, tmp_path):
        script = _pkg(tmp_path, "plots", body="from .helpers import (\n")
        assert pkgentry.resolve(tmp_path, script) is None

    def test_an_unreadable_or_missing_script_is_not_touched(self, tmp_path):
        _write(tmp_path / "plots" / "__init__.py")
        assert pkgentry.resolve(tmp_path, "plots/nope.py") is None

    def test_the_project_folder_itself_can_be_the_top_package(self, tmp_path):
        """项目文件夹本身就是包（维护者 2026-11-19 裁决：也支持）：包链把项目文件夹名算进去，`sys.path[0]` 是项目的上一级。"""
        proj = tmp_path / "labproj"
        _write(proj / "__init__.py")
        _write(proj / "fig.py", REL)  # 脚本就在项目根
        got = pkgentry.resolve(proj, "fig.py")
        assert got is not None and got.module == "labproj.fig" and got.root == str(tmp_path)
        script = _pkg(proj, "plots")  # 项目根是包、脚本在子包里，整条链都有 __init__.py
        got = pkgentry.resolve(proj, script)
        assert got is not None and got.module == "labproj.plots.fig" and got.root == str(tmp_path)

    def test_never_climbs_above_the_project_folder(self, tmp_path):
        """项目上一级也有 `__init__.py`：包根仍止于项目文件夹，不往外爬。"""
        _write(tmp_path / "__init__.py")
        proj = tmp_path / "labproj"
        _write(proj / "__init__.py")
        script = _pkg(proj, "plots")
        got = pkgentry.resolve(proj, script)
        assert got is not None and got.module == "labproj.plots.fig" and got.root == str(tmp_path)

    def test_a_project_folder_name_that_is_not_an_identifier_is_not_touched(self, tmp_path):
        proj = tmp_path / "my-project"
        _write(proj / "__init__.py")
        _write(proj / "fig.py", REL)
        assert pkgentry.resolve(proj, "fig.py") is None

    def test_a_script_at_the_project_root_without_a_package_is_not_touched(self, tmp_path):
        _write(tmp_path / "fig.py", REL)
        assert pkgentry.resolve(tmp_path, "fig.py") is None

    def test_a_package_inside_a_project_root_package_stops_below_the_root_when_the_chain_breaks(
        self, tmp_path
    ):
        _write(tmp_path / "__init__.py")
        script = _pkg(tmp_path, "src", "plots")
        (tmp_path / "src" / "__init__.py").unlink()
        got = pkgentry.resolve(tmp_path, script)
        assert got is not None and got.module == "plots.fig"

    def test_a_script_directly_in_the_project_root_is_not_touched(self, tmp_path):
        _write(tmp_path / "fig.py", REL)
        assert pkgentry.resolve(tmp_path, "fig.py") is None

    def test_only_direct_top_level_statements_count(self, tmp_path):
        """`try: from .h import x / except ImportError: from h import x` 今天能跑；按模块跑会丢掉脚本目录这个
        `sys.path[0]`——只在 `python fig.py` 本来就会失败的形状上生效。"""
        guarded = "try:\n    from .helpers import tidy\nexcept ImportError:\n    from helpers import tidy\n"
        for body in (
            guarded,
            "if __package__:\n    from .helpers import tidy\n",
            "def f():\n    from .helpers import tidy\n",
            "import os\nif os.name:\n    from . import helpers\n",
        ):
            script = _pkg(tmp_path / "t", "plots", body=body)
            assert pkgentry.resolve(tmp_path / "t", script) is None, body

    def test_the_relative_import_forms(self, tmp_path):
        for body in (
            "from . import helpers\n",
            "from .helpers import tidy as t, other\n",
            '"""doc"""\nfrom __future__ import annotations\nimport os\nfrom .helpers import tidy\n',
        ):
            script = _pkg(tmp_path / "t", "plots", body=body)
            assert pkgentry.resolve(tmp_path / "t", script) is not None, body

    def test_a_level_beyond_the_package_depth_fails_under_dash_m_too(self, tmp_path):
        """`plots.fig` 只有一层包：`from .. import x` 在 `python -m plots.fig` 下同样是 ImportError——不改。"""
        script = _pkg(tmp_path, "plots", body="from .. import helpers\n")
        assert pkgentry.resolve(tmp_path, script) is None
        script = _pkg(tmp_path / "deep", "a", "b", body="from .. import helpers\n")
        assert pkgentry.resolve(tmp_path / "deep", script).module == "a.b.fig"
        script = _pkg(tmp_path / "mixed", "a", "b", body="from . import x\nfrom ... import y\n")
        assert pkgentry.resolve(tmp_path / "mixed", script) is None

    def test_illegal_module_names_are_not_touched(self, tmp_path):
        for dirs, name in (
            (("my-plots",), "fig.py"),
            (("plots",), "fig.v2.py"),
            (("2d",), "fig.py"),
        ):
            script = _pkg(tmp_path / "".join(dirs) / name, *dirs, name=name)
            assert pkgentry.resolve(tmp_path / "".join(dirs) / name, script) is None

    def test_an_init_script_or_a_main_script_is_not_touched(self, tmp_path):
        """`python -m plots` 跑的是 `plots.__main__`，不是 `__init__.py`：这两个名字不是「这份脚本」的模块名。"""
        for name in ("__init__.py", "__main__.py"):
            script = _pkg(tmp_path / name, "plots", name=name)
            assert pkgentry.resolve(tmp_path / name, script) is None

    def test_a_non_python_or_absolute_or_escaping_target_is_not_touched(self, tmp_path):
        script = _pkg(tmp_path, "plots")
        assert pkgentry.resolve(tmp_path, "plots/fig.txt") is None
        assert pkgentry.resolve(tmp_path, str(tmp_path / script)) is None
        assert pkgentry.resolve(tmp_path, "../" + script) is None

    @pytest.mark.skipif(os.name == "nt", reason="符号链接在 Windows 上要特权")
    def test_symlinks_are_never_followed(self, tmp_path):
        outside = tmp_path / "outside"
        _pkg(outside, "plots")
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "plots").symlink_to(outside / "plots", target_is_directory=True)
        assert pkgentry.resolve(proj, "plots/fig.py") is None  # 包目录是链接：越界风险，不改
        real = tmp_path / "proj2"
        script = _pkg(real, "plots")
        (real / "plots" / "fig.py").unlink()
        (real / "plots" / "fig.py").symlink_to(outside / "plots" / "fig.py")
        assert pkgentry.resolve(real, script) is None  # 脚本本身是链接
        real3 = tmp_path / "proj3"
        script = _pkg(real3, "plots")
        (real3 / "plots" / "__init__.py").unlink()
        (real3 / "plots" / "__init__.py").symlink_to(outside / "plots" / "__init__.py")
        assert pkgentry.resolve(real3, script) is None  # __init__.py 是链接：当作没有


class TestPathRoot:
    """子进程只收模块名；`sys.path[0]` 由脚本路径往上数层得出（`bridgeboot.package_path_root`，worker 与 bridge 同一个）。"""

    def test_derives_the_parent_of_the_package_root(self, tmp_path):
        script = tmp_path / "a" / "b" / "fig.py"
        assert bridgeboot.package_path_root(str(script), "a.b.fig") == str(tmp_path)
        assert bridgeboot.package_path_root(str(script), "b.fig") == str(tmp_path / "a")

    def test_a_mismatch_is_none_never_a_guess(self, tmp_path):
        script = str(tmp_path / "a" / "b" / "fig.py")
        for module in ("a.b.other", "x.b.fig", "fig", "a.b.fig.extra", "z.a.b.fig", ""):
            if module == "fig":
                assert bridgeboot.package_path_root(script, module) == str(tmp_path / "a" / "b")
                continue
            assert bridgeboot.package_path_root(script, module) is None, module


class TestSpecAssembly:
    def _project(self, tmp_path):
        script = _pkg(tmp_path, "plots")
        return str(tmp_path), script

    def _safe(self, root, script, entry="__main__", **kw):
        return execspec.safe_spec(
            script,
            root,
            entry,
            interpreter="/usr/bin/python3",
            sandbox=str(Path(root) / ".box"),
            **kw,
        )

    def test_safe_spec_runs_the_package_script_as_a_module_with_identity_unchanged(self, tmp_path):
        root, script = self._project(tmp_path)
        spec = self._safe(root, script)
        assert spec.module_name == "plots.fig"
        # 身份：仍是脚本目标 + 项目相对路径；稳定载荷（描述符 / 资产 id / 指纹的来源）与不带模块名时逐字节相同
        assert (spec.target_kind, spec.target) == (execspec.TARGET_SCRIPT, "plots/fig.py")
        plain = self._safe(root, script, entry="main")
        assert plain.module_name == ""
        assert {**spec.stable_payload(), "entry": None} == {**plain.stable_payload(), "entry": None}
        assert "module_name" not in spec.stable_payload()

    def test_a_function_entry_or_a_plain_script_gets_no_module_name(self, tmp_path):
        root, script = self._project(tmp_path)
        assert self._safe(root, script, entry="main").module_name == ""
        _write(tmp_path / "plain.py", "import os\n")
        assert self._safe(root, "plain.py").module_name == ""

    def test_worker_argv_carries_the_module_name_only_when_present(self, tmp_path):
        root, script = self._project(tmp_path)
        argv = execspec.worker_argv(
            self._safe(root, script), worker_py="/w/worker.py", out_dir="/o"
        )
        assert argv[argv.index("--as-module") + 1] == "plots.fig"
        assert argv[argv.index("--script") + 1] == str(Path(root) / "plots/fig.py")
        _write(tmp_path / "plain.py", "import os\n")
        plain = execspec.worker_argv(
            self._safe(root, "plain.py"), worker_py="/w/worker.py", out_dir="/o"
        )
        assert "--as-module" not in plain  # 没有相对导入：argv 与 golden 逐字节相同

    def test_the_payload_roundtrips(self, tmp_path):
        root, script = self._project(tmp_path)
        spec = self._safe(root, script)
        assert execspec.spec_from_payload(spec.to_payload()) == spec

    def test_desktop_native_spec_runs_as_a_module_but_the_terminal_never_does(self, tmp_path):
        root, script = self._project(tmp_path)
        target = str(Path(root) / script)
        desktop = execspec.desktop_native_spec(
            target,
            interpreter=str(Path(root) / "py"),
            cwd=root,
            project_root=root,
            env={"PYTHONUNBUFFERED": "1"},
        )
        assert desktop.module_name == "plots.fig"
        assert (desktop.target_kind, desktop.target) == (execspec.TARGET_SCRIPT, "plots/fig.py")
        argv = execspec.bridge_argv(desktop, runner_py="/r/bridge_runner.py", out_dir="/o")
        # runner 的身份参数仍是脚本；只多 `--as-module`
        assert argv[argv.index("--target-kind") + 1] == "script"
        assert argv[argv.index("--target") + 1] == target
        assert argv[argv.index("--as-module") + 1] == "plots.fig"
        # 终端（`tavotto run fig.py`）：用户自己敲的命令，结构上拿不到这个字段
        terminal = execspec.native_spec(
            target, interpreter=str(Path(root) / "py"), cwd=root, project_root=root
        )
        assert terminal.module_name == ""
        # 登记入口是函数：与 safe 一样不改
        fn = execspec.desktop_native_spec(
            target,
            interpreter=str(Path(root) / "py"),
            cwd=root,
            project_root=root,
            env={"PYTHONUNBUFFERED": "1"},
            registered_entry="main",
        )
        assert fn.module_name == ""
        targv = execspec.bridge_argv(terminal, runner_py="/r/bridge_runner.py", out_dir="/o")
        assert targv[targv.index("--target-kind") + 1] == "script"
        assert targv[targv.index("--target") + 1] == target
        assert "--as-module" not in targv

    def test_a_terminal_spec_cannot_carry_a_module_name(self, tmp_path):
        import dataclasses

        root, script = self._project(tmp_path)
        terminal = execspec.native_spec(
            str(Path(root) / script),
            interpreter=str(Path(root) / "py"),
            cwd=root,
            project_root=root,
        )
        with pytest.raises(ValueError):
            dataclasses.replace(terminal, module_name="plots.fig")

    def test_a_module_name_must_match_the_target(self, tmp_path):
        import dataclasses

        root, script = self._project(tmp_path)
        spec = self._safe(root, script)
        for bad in ("plots.other", "x.plots.fig", "plots.fig.", "plots..fig", "pl ots.fig"):
            with pytest.raises(ValueError):
                dataclasses.replace(spec, module_name=bad)

    def test_a_shadow_script_is_never_run_as_a_module(self, tmp_path, monkeypatch):
        """影子脚本（生成可编辑版本的校验运行）：项目里没有这个文件，判据读不到它 → 不改。"""
        root, script = self._project(tmp_path)
        monkeypatch.setattr(execspec.shadowscript, "lookup", lambda *a, **k: str(tmp_path / "s.py"))
        spec = self._safe(root, script, write_roots=[str(tmp_path)])
        assert spec.script_source and spec.module_name == ""


def test_spec_accepts_the_project_folder_prefix_and_nothing_else(tmp_path):
    """项目文件夹本身也是包：模块名可以多出最前面一段＝项目文件夹名；换成别的名字仍拒绝。"""
    proj = tmp_path / "labproj"
    script = _pkg(proj, "plots")
    _write(proj / "__init__.py")
    spec = execspec.safe_spec(
        script,
        str(proj),
        "__main__",
        interpreter=str(tmp_path / "py"),
        sandbox=str(tmp_path / "box"),
    )
    assert spec.module_name == "labproj.plots.fig" and spec.target == "plots/fig.py"
    import dataclasses

    with pytest.raises(ValueError):
        dataclasses.replace(spec, module_name="otherproj.plots.fig")
