"""`importscan.scan(extra_imports=)` / `depplan.plan(extra_imports=)`：运行时观察到缺的顶层模块名并入静态扫描
（TVD-82B9：`pandas.read_excel` 惰性 import openpyxl，脚本里没有 `import openpyxl`）。真扫描、不 monkeypatch。"""

from __future__ import annotations

import ast

from tavotto.engine import importscan

SCRIPT = "import pandas as pd\nimport matplotlib.pyplot as plt\ndf = pd.read_excel('a.xlsx')\n"


def _mods(res):
    return {c.module for c in res.classes}


def test_extra_import_is_scanned_as_an_unconditional_import_of_the_entry(tmp_path):
    (tmp_path / "s.py").write_text(SCRIPT, encoding="utf-8")
    tree = ast.parse(SCRIPT)
    plain = importscan.scan(tmp_path, "s.py", tree=tree)
    assert "openpyxl" not in _mods(plain)
    extra = importscan.scan(tmp_path, "s.py", tree=tree, extra_imports=("openpyxl",))
    assert {"pandas", "openpyxl"} <= _mods(extra)
    hit = next(c for c in extra.classes if c.module == "openpyxl")
    assert hit.context == importscan.CONTEXT_UNCONDITIONAL and not hit.unused
    assert "openpyxl" not in {
        c.module for c in importscan.scan(tmp_path, "s.py", tree=tree).classes
    }


def test_extra_imports_ignore_non_identifier_names_and_do_not_mutate_the_given_tree(tmp_path):
    (tmp_path / "s.py").write_text(SCRIPT, encoding="utf-8")
    tree = ast.parse(SCRIPT)
    before = ast.dump(tree)
    res = importscan.scan(tmp_path, "s.py", tree=tree, extra_imports=("a.b", "os; evil", "class"))
    assert ast.dump(tree) == before
    assert not {"a", "evil", "class"} & _mods(res)


def test_planner_turns_the_runtime_module_into_an_install_requirement(tmp_path):
    """真联合计划：静态看不见 openpyxl（pandas 在、脚本里没 import 它）→ nothing_needed；并入运行时缺包后 ready 且要装 openpyxl。"""
    from tavotto.engine import depplan

    (tmp_path / "s.py").write_text(SCRIPT, encoding="utf-8")
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
    facts = depplan.TargetFacts(
        python=str(tmp_path / "bin" / "python"),
        marker_env=marker_env,
        stdlib=importscan.HOST_STDLIB,
        installed={"matplotlib": "3.10.0", "numpy": "2.1.0", "pandas": "2.2.0"},
        prefix=str(tmp_path),
    )
    kw = dict(facts=facts, target_kind=depplan.TARGET_PROJECT_VENV, dists=None)
    before = depplan.plan(tmp_path, "s.py", **kw)
    assert before.status == depplan.STATUS_NOTHING_NEEDED
    after = depplan.plan(tmp_path, "s.py", extra_imports=("openpyxl",), **kw)
    assert after.status == depplan.STATUS_READY
    assert [r.split(">")[0].split("=")[0] for r in after.requirements] == ["openpyxl"]
