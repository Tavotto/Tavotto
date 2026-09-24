"""脚本 import 了却从未用到、又没装的包不挡图（ADR 0061 §二 2026-09-24 修订）。

用户实测：`import sympy as smp` 全文件一次都没用，内置 runtime 没有 sympy——跑前的门要求先装
sympy，不装就在 import 那一行 ModuleNotFoundError。判据唯一出处 `figcapture.unused_imports`，
父进程（`importscan` → 联合计划）与 worker（占位）各调一次。四节：

* 判据本身：能证明未使用、且模块在实测过的无副作用名单里才收，判不清的一律不收（每一条「不收」
  都钉着；名单在 worker 解释器里现量一遍）；
* 联合计划：未使用的不进 needed / missing / unknown，只列在 `unused`；用到了的照旧要准备；
* 占位（真子进程，不污染 pytest 进程的 builtins）：只对脚本自己的 import、只在真缺时、
  不进 sys.modules、读属性的失败形状与原来逐字相同；
* 真 worker：未使用的缺包照常出图；用到了的缺包照旧 `No module named`。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tavotto.engine import depplan, figcapture, importscan, pool

ROOT = Path(__file__).resolve().parent.parent
ABSENT = "zz_tavotto_absent_pkg"  # 任何环境里都装不到的名字


def _unused(src: str) -> list[str]:
    return sorted(figcapture.unused_imports(ast.parse(textwrap.dedent(src))))


# ---------------------------------------------------------------- 判据


class TestPredicate:
    @pytest.mark.parametrize(
        "src",
        [
            "import sympy as smp\nimport numpy as np\nnp.zeros(1)\n",
            "import sympy as smp\ndef f():\n    import sympy as smp2\n",  # 函数里再 import 一次、仍然没读
            "import sympy as smp\nx = 'numpy'\n",
        ],
    )
    def test_provably_unused_imports_are_collected(self, src):
        assert _unused(src) == ["sympy"]

    @pytest.mark.parametrize(
        "src",
        [
            "import sympy\n",  # 裸 import 可能是为了副作用（scienceplots / cmocean）
            "import scienceplots\nimport matplotlib.pyplot as plt\nplt.style.use('science')\n",
            "import sympy as smp\nimport sympy\n",  # 同一个模块还有一处裸 import
            "import sympy as smp\nsmp.symbols('x')\n",  # 读了
            "import sympy as smp\ndel smp\n",  # 删也算出现
            "import sympy as smp\nsmp = 1\n",  # 重新绑定也算出现
            "import sympy as smp\nfrom sympy import symbols\n",  # from-import 就是在用
            "import sympy.abc as abc\n",  # 带点的要真装载子模块
            "import sympy as smp\nimport sympy.abc as abc\n",
            "try:\n    import sympy as smp\n    HAVE = True\nexcept ImportError:\n    HAVE = False\n",
            "from contextlib import suppress\nwith suppress(ImportError):\n    import sympy as smp\n",
            "import sympy as smp\nprint(globals()['smp'])\n",  # 读不清
            "import sympy as smp\nprint(vars())\n",
            "import sympy as smp\neval('smp')\n",
            "import sympy as smp\nexec('print(smp)')\n",
            "import sys\nimport sympy as smp\nsys.modules['sympy']\n",  # 字符串里点名模块
            "import sympy as s\n__all__ = ['s']\n",  # 字符串里点名绑定
            "import sympy as smp\nimport importlib\nimportlib.import_module('sympy.abc')\n",
            "import sympy as smp\ndef f(smp):\n    return 1\n",  # 形参同名
            "import sympy as smp\nfrom other import smp\n",  # 别的 import 同名绑定
            "import sympy as smp\nobj.smp\n",  # 属性名同名（宁可多判用到）
            "import sympy as smp\nmatch 1:\n    case smp:\n        pass\n",  # match 捕获
            # 评审 #555 P1：起了别名、从没读，也可能只为副作用——不在实测名单里的一律不收
            "import cmocean as cm\nimport matplotlib.pyplot as plt\nplt.imshow([[1]], cmap='cmo.thermal')\n",
            "import scienceplots as _sp\nimport matplotlib.pyplot as plt\nplt.style.use('science')\n",
            "import lmfit as lm\n",  # import 时就装 matplotlib（实测），不收
            "import requests as _requests\n",  # 改 warnings 过滤器、装 logging handler（实测），不收
            f"import {ABSENT} as a\n",  # 不认识的名字
        ],
    )
    def test_anything_short_of_proof_is_not_collected(self, src):
        assert _unused(src) == []

    def test_the_users_script_shape(self):
        """用户脚本的形状：一行 import 别名、全文件没读过，旁边的包都在用。"""
        src = """\
        import numpy as np
        import sympy as smp
        import scipy.special as sp
        from scipy.integrate import quad
        import matplotlib.pyplot as plt
        plt.plot(np.arange(3), sp.gamma(np.arange(1, 4)))
        quad(lambda t: t, 0, 1)
        """
        assert _unused(src) == ["sympy"]


# ---------------------------------------------------------------- 联合计划


def _facts(installed: dict | None = None) -> depplan.TargetFacts:
    marker_env = {
        "implementation_name": "cpython",
        "implementation_version": "3.13.0",
        "os_name": "posix",
        "platform_machine": "arm64",
        "platform_release": "",
        "platform_system": "Darwin",
        "platform_version": "",
        "python_full_version": "3.13.0",
        "platform_python_implementation": "CPython",
        "python_version": "3.13",
        "sys_platform": "darwin",
    }
    return depplan.TargetFacts(
        python="/fake/python",
        marker_env=marker_env,
        stdlib=importscan.HOST_STDLIB,
        installed={"matplotlib": "3.10.0", "numpy": "2.0.0", **(installed or {})},
        prefix="/fake",
    )


def _project(tmp_path: Path, script: str, **files) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "plot.py").write_text(textwrap.dedent(script), encoding="utf-8")
    for rel, text in files.items():
        (proj / rel).parent.mkdir(parents=True, exist_ok=True)
        (proj / rel).write_text(text, encoding="utf-8")
    return proj


class TestPlan:
    def test_unused_missing_import_needs_nothing(self, tmp_path):
        proj = _project(tmp_path, "import numpy as np\nimport sympy as smp\nnp.zeros(1)\n")
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.status == depplan.STATUS_NOTHING_NEEDED
        assert plan.missing == () and plan.requirements == ()
        assert plan.unused == ("sympy",)
        assert plan.to_payload()["unused"] == ["sympy"]
        cls = {c["module"]: c for c in plan.scan["classes"]}
        assert cls["sympy"]["unused"] is True and cls["sympy"]["needed"] is False

    def test_used_missing_import_still_needs_preparation(self, tmp_path):
        """反向：读了一次就照旧要准备——修复不能把真依赖也放过去。"""
        proj = _project(tmp_path, "import sympy as smp\nsmp.symbols('x')\n")
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.status == depplan.STATUS_READY
        assert [m["distribution"] for m in plan.missing] == ["sympy"]
        assert plan.unused == ()

    def test_unused_unknown_import_is_not_reported_as_unknown(self, tmp_path, monkeypatch):
        # 名单里现在全是 curated 表里的名字；用例临时加一个映射不到 distribution 的，落在 unknown 桶
        monkeypatch.setattr(
            figcapture, "SIDE_EFFECT_FREE_IMPORTS", figcapture.SIDE_EFFECT_FREE_IMPORTS | {ABSENT}
        )
        proj = _project(tmp_path, f"import {ABSENT} as a\n")
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.unknown == () and plan.unused == (ABSENT,)

    def test_side_effect_import_with_an_alias_still_needs_preparation(self, tmp_path):
        """反向（评审 #555 P1）：`import cmocean as cm` 只为注册色图——缺了照旧要准备，
        不能让它变成之后一句「色图 cmo.thermal 不存在」。"""
        proj = _project(
            tmp_path,
            "import cmocean as cm\nimport matplotlib.pyplot as plt\n"
            "plt.imshow([[1]], cmap='cmo.thermal')\n",
        )
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.status == depplan.STATUS_READY
        assert [m["distribution"] for m in plan.missing] == ["cmocean"]
        assert plan.unused == ()

    def test_unmapped_side_effect_import_stays_unknown(self, tmp_path):
        """scienceplots 不在 curated 表：照旧列在 unknown（让用户指定），不被当成「没用到」吞掉。"""
        proj = _project(
            tmp_path,
            "import scienceplots as _sp\nimport matplotlib.pyplot as plt\nplt.style.use('science')\n",
        )
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.unknown == ("scienceplots",) and plan.unused == ()

    def test_import_through_a_local_module_is_still_needed(self, tmp_path):
        """脚本自己没读，但本地模块也 import 了它：那边的绑定判不了，照旧按上下文判。"""
        proj = _project(
            tmp_path,
            "import sympy as smp\nimport lab_utils\n",
            **{"lab_utils.py": "import sympy\nX = sympy.Symbol('x')\n"},
        )
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.status == depplan.STATUS_READY
        assert [m["distribution"] for m in plan.missing] == ["sympy"]


class TestBorrowedThroughMain:
    """评审 #555 P2：本地模块可以借走脚本的绑定——`from __main__ import smp` 之后 `smp.Symbol(...)`。
    脚本自己没读 ≠ 没用到；任何被跟进的本地模块能够到脚本命名空间，脚本的 `unused` 一律作废。"""

    @pytest.mark.parametrize(
        "helper",
        [
            "from __main__ import smp\nX = smp.Symbol('x')\n",  # 评审原例
            "import __main__\nX = __main__.smp.Symbol('x')\n",  # import __main__ 后访问属性
            "import __main__ as m\nX = getattr(m, 'smp')\n",
            "import sys\nX = sys.modules['__main__'].smp\n",
            "import sys\nX = sys._getframe(1).f_globals['smp']\n",
            "import inspect\nX = inspect.stack()[1].frame.f_globals\n",
            "from plot import smp\n",  # entry 不是 __main__ 时脚本按 stem 作为模块被 import
            # 同一条路，不经 import：只有「字符串里点名脚本的 stem」看得见（`from plot import …` 另有
            # 「跟进到 plot.py、sympy 有 via」那条兜着）
            "import sys\nX = sys.modules['plot'].smp\n",
        ],
    )
    def test_a_local_module_that_can_borrow_the_alias_keeps_it_needed(self, tmp_path, helper):
        proj = _project(tmp_path, "import sympy as smp\nimport helper\n", **{"helper.py": helper})
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.status == depplan.STATUS_READY, plan.to_payload()
        assert [m["distribution"] for m in plan.missing] == ["sympy"]
        assert plan.unused == ()

    @pytest.mark.parametrize(
        "helper",
        [
            # 入口守卫里的 "__main__" 不算够到脚本——否则带入口守卫的本地模块全都会误判
            "def f():\n    return 1\nif __name__ == '__main__':\n    f()\n",
            "import numpy as np\nX = np.stack([np.zeros(1)])\n",  # np.stack 不是 inspect.stack
        ],
    )
    def test_ordinary_local_modules_do_not_void_the_verdict(self, tmp_path, helper):
        proj = _project(tmp_path, "import sympy as smp\nimport helper\n", **{"helper.py": helper})
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.unused == ("sympy",)
        assert plan.status == depplan.STATUS_NOTHING_NEEDED

    @pytest.mark.parametrize(
        "script, files",
        [
            # 本地模块读不了（语法错）：看不见它借没借
            ("import sympy as smp\nimport helper\n", {"helper.py": "def (:\n"}),
            # 非字面量的动态 import：可能装进一个没扫过、借用 __main__ 的本地模块
            (
                "import sympy as smp\nimport importlib\nname = 'hel' + 'per'\nimportlib.import_module(name)\n",
                {},
            ),
        ],
    )
    def test_what_cannot_be_seen_voids_the_verdict(self, tmp_path, script, files):
        proj = _project(tmp_path, script, **files)
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.unused == ()
        assert [m["distribution"] for m in plan.missing] == ["sympy"]


class TestBorrowedInsideAPackage:
    """评审 #555 P2 第二条：借用可以藏在包的子模块里——`helper/__init__.py` 里 `from . import inner`，
    `inner.py` 里 `from __main__ import smp`。`_module_files` 只给 `__init__.py`、`_Visitor` 丢掉相对导入，
    `reaches_main` 看不到 inner.py。现在跟进到的包里全部 .py 都交给它看，相对导入逐条解析，解析不到就算看不全。"""

    BORROW = "from __main__ import smp\nX = smp.Symbol('x')\n"

    def _plan(self, tmp_path, script="import sympy as smp\nimport helper\n", **files):
        proj = _project(tmp_path, script, **files)
        return depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")

    @pytest.mark.parametrize(
        "script, files",
        [
            # 评审原例
            (None, {"helper/__init__.py": "from . import inner\n", "helper/inner.py": BORROW}),
            # from .inner import something
            (None, {"helper/__init__.py": "from .inner import X\n", "helper/inner.py": BORROW}),
            # from .. import：子包回到上一层
            (
                None,
                {
                    "helper/__init__.py": "from . import sub\n",
                    "helper/sub/__init__.py": "from .. import inner\n",
                    "helper/inner.py": BORROW,
                },
            ),
            # 绝对的子模块 import：只有「扫整个包」看得见（相对导入解析帮不上）
            (
                "import sympy as smp\nimport helper.inner\n",
                {"helper/__init__.py": "", "helper/inner.py": BORROW},
            ),
        ],
    )
    def test_a_borrow_inside_the_package_keeps_it_needed(self, tmp_path, script, files):
        plan = self._plan(tmp_path, **({"script": script} if script else {}), **files)
        assert plan.unused == ()
        assert [m["distribution"] for m in plan.missing] == ["sympy"]

    @pytest.mark.parametrize(
        "files",
        [
            # 相对导入解析不到
            {"helper/__init__.py": "from .missing import x\n"},
            # 指到编译扩展：没有源码可扫（只有相对导入解析看得见，包扫描只认 .py）
            {
                "helper/__init__.py": "from ._fast import f\n",
                "helper/_fast.cpython-313-darwin.so": "",
            },
            # 越出项目根
            {"helper/__init__.py": "from ... import x\n"},
            # 包扫描扫到、没被 import 的子模块里指向编译扩展（第二遍里的相对导入解析）
            {
                "helper/__init__.py": "",
                "helper/inner.py": "from ._fast import f\n",
                "helper/_fast.cpython-313-darwin.so": "",
            },
        ],
    )
    def test_what_the_package_hides_voids_the_verdict(self, tmp_path, files):
        plan = self._plan(tmp_path, **files)
        assert plan.unused == ()
        assert [m["distribution"] for m in plan.missing] == ["sympy"]

    def test_an_ordinary_package_with_relative_imports_keeps_the_verdict(self, tmp_path):
        """对照：相对导入都解析得到、谁也没碰 __main__——仍判未使用，修复没有把包一律判成看不全。"""
        plan = self._plan(
            tmp_path,
            **{
                "helper/__init__.py": "from . import inner\nfrom .sub import y\n",
                "helper/inner.py": "from .sub import y\nZ = y + 1\n",
                "helper/sub/__init__.py": "from .. import inner as _i\ny = 1\n",
            },
        )
        assert plan.unused == ("sympy",)
        assert plan.status == depplan.STATUS_NOTHING_NEEDED


# ---------------------------------------------------------------- 占位（真子进程）

_PLACEHOLDER_DRIVER = """\
import ast, json, os, runpy, sys
sys.path.insert(0, sys.argv[1])
import figcapture
script = sys.argv[2]
sys.path.insert(0, os.path.dirname(script))  # 与 worker 一样：脚本目录在最前
# 用例专用：把两个造出来的名字临时加进名单（真名单里的包在这台机器上可能装着，测不了「缺」）
figcapture.SIDE_EFFECT_FREE_IMPORTS = figcapture.SIDE_EFFECT_FREE_IMPORTS | {
    "zz_tavotto_absent_pkg",
    "brokenpkg",
}
names = figcapture.unused_imports(ast.parse(open(script, encoding="utf-8").read()))
figcapture.install_unused_import_placeholders(script, names)
out = {"names": sorted(names)}
try:
    ns = runpy.run_path(script, run_name="__main__")
    out["ran"] = True
    out["result"] = ns.get("RESULT")
except BaseException as exc:
    out["ran"] = False
    out["error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(out))
"""


def _run_placeholder(tmp_path: Path, script: str, **files) -> dict:
    proj = tmp_path / "p"
    proj.mkdir(exist_ok=True)
    (proj / "s.py").write_text(textwrap.dedent(script), encoding="utf-8")
    for rel, text in files.items():
        (proj / rel).write_text(textwrap.dedent(text), encoding="utf-8")
    driver = tmp_path / "driver.py"
    driver.write_text(_PLACEHOLDER_DRIVER, encoding="utf-8")
    engine = ROOT / "src" / "tavotto" / "engine"
    proc = subprocess.run(
        [sys.executable, str(driver), str(engine), str(proj / "s.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(proj),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestPlaceholder:
    def test_unused_missing_import_runs(self, tmp_path):
        res = _run_placeholder(
            tmp_path,
            f"""\
            import sys
            import {ABSENT} as a
            # 不能写成 `"<名字>" in sys.modules`：字符串里点名模块会让判据放弃（本来就该放弃）
            RESULT = {{"in_sys_modules": any(k.startswith("zz_tavotto") for k in sys.modules)}}
            """,
        )
        assert res["names"] == [ABSENT]
        assert res["ran"] is True, res
        # 占位不进 sys.modules：别处再 import 仍是真实的失败
        assert res["result"] == {"in_sys_modules": False}

    def test_library_side_import_of_the_same_name_still_fails(self, tmp_path):
        """库里的 `try: import X except ImportError` 永远看不到占位。"""
        res = _run_placeholder(
            tmp_path,
            f"""\
            import {ABSENT} as a
            import helper
            RESULT = helper.HAVE
            """,
            **{
                "helper.py": f"""\
                try:
                    import {ABSENT}
                    HAVE = True
                except ImportError:
                    HAVE = False
                """
            },
        )
        assert res["ran"] is True, res
        assert res["result"] is False

    def test_reading_the_placeholder_fails_exactly_like_the_missing_import(self, tmp_path):
        """判据若错了（这里用 getattr 绕开它来模拟），失败形状与原来逐字相同。"""
        res = _run_placeholder(
            tmp_path,
            f"""\
            import sys
            import {ABSENT} as a
            RESULT = None
            sys.modules["__main__"].__dict__
            """,
        )
        # `__dict__` 让判据整份放弃：没有占位，import 那一行照旧失败
        assert res["names"] == []
        assert res["ran"] is False
        assert res["error"] == f"ModuleNotFoundError: No module named '{ABSENT}'"

        placeholder_driver = f"""\
        import ast, sys
        sys.path.insert(0, {str(ROOT / "src" / "tavotto" / "engine")!r})
        import figcapture
        script = sys.argv[1]
        figcapture.install_unused_import_placeholders(script, {{{ABSENT!r}}})
        import runpy
        runpy.run_path(script, run_name="__main__")
        """
        s = tmp_path / "reads.py"
        s.write_text(f"import {ABSENT} as a\na.anything\n", encoding="utf-8")
        d = tmp_path / "d2.py"
        d.write_text(textwrap.dedent(placeholder_driver), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(d), str(s)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert proc.returncode != 0
        assert proc.stderr.rstrip().splitlines()[-1] == (
            f"ModuleNotFoundError: No module named '{ABSENT}'"
        )
        assert pool._MISSING_RE.search(proc.stderr).group(1) == ABSENT

    def test_a_findable_module_raising_its_own_name_is_not_swallowed(self, tmp_path):
        """评审 #555 P2：`exc.name == X` 只说明异常这么写着。项目里的 `sympy.py` 先做了一件事（写一个
        标记文件），再自己抛 `ModuleNotFoundError(name="sympy")`——import 系统找得到它，失败照常抛出，
        不给占位，脚本不继续。"""
        res = _run_placeholder(
            tmp_path,
            "import sympy as smp\nRESULT = 'continued'\n",
            **{
                "sympy.py": """\
                open("side_effect.txt", "w").write("ran")
                raise ModuleNotFoundError("No module named 'sympy'", name="sympy")
                """
            },
        )
        assert res["names"] == ["sympy"]  # 判据照样收它（绑定没读）；拦它的是 find_spec
        assert res["ran"] is False, res
        assert res["error"] == "ModuleNotFoundError: No module named 'sympy'"
        assert (tmp_path / "p" / "side_effect.txt").read_text(encoding="utf-8") == "ran"

    def test_a_finder_that_errors_is_not_read_as_absent(self, tmp_path):
        """`find_spec` 自己抛错 = 判不清，不是「没找到」：照常抛出，不给占位。"""
        res = _run_placeholder(
            tmp_path,
            "import blocker\nimport sympy as smp\nRESULT = 'continued'\n",
            **{
                "blocker.py": """\
                import sys

                class _Finder:
                    def find_spec(self, name, path=None, target=None):
                        if name == "sympy":
                            raise ModuleNotFoundError("No module named 'sympy'", name="sympy")
                        return None

                sys.meta_path.insert(0, _Finder())
                """
            },
        )
        assert res["names"] == ["sympy"]
        assert res["ran"] is False, res
        assert res["error"] == "ModuleNotFoundError: No module named 'sympy'"

    def test_installed_package_with_a_broken_dependency_is_not_hidden(self, tmp_path):
        """X 装了、但它 import 的依赖缺：缺的不是 X 本身，照常报。"""
        res = _run_placeholder(
            tmp_path,
            "import brokenpkg as b\n",
            **{"brokenpkg.py": f"import {ABSENT}\n"},
        )
        assert res["names"] == ["brokenpkg"]
        assert res["ran"] is False
        assert res["error"] == f"ModuleNotFoundError: No module named '{ABSENT}'"


# ---------------------------------------------------------------- 真 worker

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

_needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


def _missing_listed_module() -> str | None:
    """名单里在 worker 解释器上**真的找不到**的一个名字（`find_spec` 为 None）；全装着就 None。

    不用「import 时抛 `ModuleNotFoundError(name=X)` 的影子包」来模拟缺包：那正是评审 #555 P2 要求
    占位**不能**相信的形状（找得到的同名模块自己抛了错），修复之后它理应照常失败。"""
    code = (
        "import importlib.util, sys\n"
        "print(next((n for n in sys.argv[1:] if importlib.util.find_spec(n) is None), ''))\n"
    )
    out = subprocess.run(
        [WORKER_PY, "-c", code, *sorted(figcapture.SIDE_EFFECT_FREE_IMPORTS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    ).stdout.strip()
    return out or None


def _build(tmp_path: Path, script: str) -> dict:
    figs = tmp_path / "figures"
    figs.mkdir()
    return _build_in(figs, tmp_path, script)


def _build_in(figs: Path, tmp_path: Path, script: str) -> dict:
    s = figs / "fig_unused.py"
    s.write_text(textwrap.dedent(script), encoding="utf-8")
    proc = subprocess.Popen(
        [
            WORKER_PY,
            str(pool.WORKER_PY),
            "--script",
            str(s),
            "--figures-dir",
            str(figs),
            "--out-dir",
            str(tmp_path / "out"),
            "--sandbox",
            str(tmp_path / "sandbox"),
            "--entry",
            "__main__",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "TAVOTTO_NO_TELEMETRY": "1"},
    )
    try:
        out, _err = proc.communicate(json.dumps({"cmd": "build"}) + "\n", timeout=120)
    finally:
        if proc.poll() is None:
            proc.kill()
    return json.loads(out.strip().splitlines()[0])


_PLOT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
ax.plot([0, 1], [1, 0])
fig.savefig("l2_error_convergence.png")
"""


@_needs_worker
def test_worker_renders_a_script_whose_missing_import_is_unused(tmp_path):
    missing = _missing_listed_module()
    if missing is None:
        pytest.skip("名单上的包在 worker 解释器里全装着，造不出「真缺」")
    resp = _build(tmp_path, f"import {missing} as unused_alias\n" + _PLOT)
    assert resp.get("ok", True) is not False, resp
    assert "l2_error_convergence" in resp["stems"], resp


@_needs_worker
def test_worker_still_fails_when_the_missing_import_is_used(tmp_path):
    missing = _missing_listed_module()
    if missing is None:
        pytest.skip("名单上的包在 worker 解释器里全装着，造不出「真缺」")
    resp = _build(tmp_path, f"import {missing} as used_alias\nused_alias.anything\n" + _PLOT)
    assert "stems" not in resp, resp
    assert f"No module named '{missing}'" in json.dumps(resp, ensure_ascii=False), resp


@_needs_worker
def test_worker_does_not_swallow_a_findable_module_that_raises_its_own_name(tmp_path):
    """评审 #555 P2：项目里的同名 `sympy.py` 执行到一半自己抛 `ModuleNotFoundError(name="sympy")`——
    import 系统找得到它，不是「没装」；失败照常报，脚本不能拿着占位继续。"""
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "sympy.py").write_text(
        "raise ModuleNotFoundError(\"No module named 'sympy'\", name='sympy')\n", encoding="utf-8"
    )
    resp = _build_in(figs, tmp_path, "import sympy as smp\n" + _PLOT)
    assert "stems" not in resp, resp
    assert "No module named 'sympy'" in json.dumps(resp, ensure_ascii=False), resp


SIDE_EFFECTS = ROOT / "tests" / "support" / "import_side_effects.py"


def _measure(python: str, name: str, extra_path: Path | None = None) -> dict:
    """在全新解释器（`-I`）里用那份快照量一个包——名单用例与实测脚本同一份判据。"""
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(SIDE_EFFECTS.parent)!r})\n"
        + (f"sys.path.insert(0, {str(extra_path)!r})\n" if extra_path else "")
        + "import import_side_effects as m\n"
        "print(json.dumps(m.measure(sys.argv[1])))\n"
    )
    out = subprocess.run(
        [python, "-I", "-c", code, name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        cwd=str(ROOT.parent),
    )
    assert out.returncode == 0, out.stderr[-500:]
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    "body, expect",
    [
        ("import logging\nlogging.getLogger('zz').addHandler(logging.NullHandler())\n", "logging"),
        ("import logging\nlogging.getLogger().setLevel(logging.ERROR)\n", "logging"),
        (
            "import warnings\nwarnings.simplefilter('ignore', DeprecationWarning)\n",
            "warnings.filters",
        ),
        ("import atexit\natexit.register(print)\n", "atexit"),
        ("import os\nos.environ['ZZ_TAVOTTO'] = '1'\n", "environ"),
        ("import sys\nsys.meta_path.append(object())\n", "sys.meta_path"),
        ("import sys\nsys.path.append('/zz')\n", "sys.path"),
        ("import sys\nsys.excepthook = lambda *a: None\n", "sys.excepthook"),
        ("import signal\nsignal.signal(signal.SIGTERM, lambda *a: None)\n", "signals"),
        ("import builtins\nbuiltins.zz_tavotto = 1\n", "builtins"),
        ("import codecs\ncodecs.register(lambda name: None)\n", "codecs.register"),
        ("import matplotlib\n", "matplotlib"),
        ("x = 1\n", None),  # 什么都不改：量出来必须是空的
    ],
)
def test_the_side_effect_snapshot_sees_each_kind(tmp_path, body, expect):
    """尺子是活的：每一类进程级副作用各造一个模块，快照都得看见；什么都不改的模块必须量出空。"""
    if expect == "matplotlib" and WORKER_PY is None:
        pytest.skip("没有装 matplotlib 的解释器")
    (tmp_path / "zz_side_effect_mod.py").write_text(body, encoding="utf-8")
    python = WORKER_PY if expect == "matplotlib" else sys.executable
    changed = _measure(python, "zz_side_effect_mod", tmp_path)["changed"]
    if expect is None:
        assert changed == []
    else:
        assert any(c.startswith(expect) for c in changed), changed


def test_a_filter_for_the_packages_own_warning_class_is_the_only_exemption(tmp_path):
    """唯一的豁免（sympy 的形状）：新加的过滤器只管包**自己定义的**警告类——包不在，这个类就不存在。
    同一个包再加一条管别人类别的过滤器，照样算变化。"""
    pkg = tmp_path / "zz_own_warn"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "import warnings\nclass OwnWarning(DeprecationWarning):\n    pass\n"
        "warnings.simplefilter('once', OwnWarning)\n",
        encoding="utf-8",
    )
    assert _measure(sys.executable, "zz_own_warn", tmp_path)["changed"] == []
    (pkg / "__init__.py").write_text(
        (pkg / "__init__.py").read_text(encoding="utf-8")
        + "warnings.simplefilter('ignore', UserWarning)\n",
        encoding="utf-8",
    )
    assert _measure(sys.executable, "zz_own_warn", tmp_path)["changed"] == ["warnings.filters"]


@_needs_worker
def test_the_side_effect_free_list_still_holds_in_the_worker_interpreter():
    """名单是实测出来的，版本会变：在 worker 解释器里对装了的那些用同一份快照现量一遍，任何一项变了就红。
    一个都没装就 skip（尺子本身是活的由上面两条钉着）。"""
    measured, touched = [], []
    for name in sorted(figcapture.SIDE_EFFECT_FREE_IMPORTS):
        res = _measure(WORKER_PY, name)
        if res.get("absent"):
            continue
        measured.append(name)
        if res["changed"]:
            touched.append(f"{name}: {res['changed']}")
    if not measured:
        pytest.skip("worker 解释器里名单上的包一个都没装，量不了")
    assert not touched, touched
