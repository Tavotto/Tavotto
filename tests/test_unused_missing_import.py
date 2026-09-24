"""脚本 import 了却从未用到、又没装的包不挡图（ADR 0061 §二 2026-09-24 修订）。

用户实测：`import sympy as smp` 全文件一次都没用，内置 runtime 没有 sympy——跑前的门要求先装
sympy，不装就在 import 那一行 ModuleNotFoundError。判据唯一出处 `figcapture.unused_imports`，
父进程（`importscan` → 联合计划）与 worker（占位）各调一次。四节：

* 判据本身：能证明未使用的收，判不清的一律不收（每一条「不收」都钉着）；
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

    def test_unused_unknown_import_is_not_reported_as_unknown(self, tmp_path):
        proj = _project(tmp_path, f"import {ABSENT} as a\n")
        plan = depplan.plan(proj, "plot.py", facts=_facts(), target_kind="tavotto_managed")
        assert plan.unknown == () and plan.unused == (ABSENT,)

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


# ---------------------------------------------------------------- 占位（真子进程）

_PLACEHOLDER_DRIVER = """\
import ast, json, os, runpy, sys
sys.path.insert(0, sys.argv[1])
import figcapture
script = sys.argv[2]
sys.path.insert(0, os.path.dirname(script))  # 与 worker 一样：脚本目录在最前
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


def _build(tmp_path: Path, script: str) -> dict:
    figs = tmp_path / "figures"
    figs.mkdir()
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
    resp = _build(tmp_path, f"import {ABSENT} as unused_alias\n" + _PLOT)
    assert resp.get("ok", True) is not False, resp
    assert "l2_error_convergence" in resp["stems"], resp


@_needs_worker
def test_worker_still_fails_when_the_missing_import_is_used(tmp_path):
    resp = _build(tmp_path, f"import {ABSENT} as used_alias\nused_alias.go()\n" + _PLOT)
    assert "stems" not in resp, resp
    assert f"No module named '{ABSENT}'" in json.dumps(resp, ensure_ascii=False), resp
