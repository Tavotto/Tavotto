"""#1086：不可证明门槛（ADR 0133）——静态分析证明不了调用目标的动态分派出现时，高置信不直达 native。

判据是一条结构规则（「证明不了就不提升」），不是危险函数名清单：下面的向量覆盖的是**形状类别**
（动态名的 getattr、按名取模块、代码执行、反射面），而不是某个具体的 `os.remove`。
"""

from __future__ import annotations

import textwrap

import pytest

from tavotto.engine import runroute

ROOT = "/proj"
SCRIPT = "figs/fig.py"
MPL = "import matplotlib.pyplot as plt\n"
TAIL = 'plt.savefig("x.png")\nopen("x.png", "rb").read()\n'


def _route(src: str) -> runroute.Route:
    facts = runroute.facts_of(textwrap.dedent(src))
    return runroute.decide(facts, cwd_mode=runroute.CWD_SANDBOX, project_root=ROOT, script=SCRIPT)


def test_the_plain_readback_baseline_still_goes_native():
    route = _route(MPL + TAIL)
    assert (route.execution, route.reason) == ("native", runroute.REASON_SAVEFIG_READBACK)


UNPROVABLE = [
    # 审计 B1 / B2 的五种形状
    ("getattr_concat", 'import os\ngetattr(os, "re" + "move")("victim.txt")\n'),
    ("importlib_module", 'import importlib\nimportlib.import_module("os").remove("victim.txt")\n'),
    ("exec_string", "exec(\"import os; os.remove('victim.txt')\")\n"),
    ("dunder_import", '__import__("os").remove("victim.txt")\n'),
    ("getattr_chdir", 'import os\ngetattr(os, "ch" + "dir")("..")\n'),
    # 跨形状：先取函数再调用，调用点什么都看不出
    ("getattr_then_call", 'import os\nname = "remove"\nf = getattr(os, name)\nf("victim.txt")\n'),
    ("setattr_dynamic", "import os\nsetattr(os, NAME, lambda *a: None)\n"),
    ("eval_expr", 'eval("1 + 1")\n'),
    ("compile_builtin", 'compile("x = 1", "<s>", "exec")\n'),
    ("from_importlib", 'from importlib import import_module as im\nim("os")\n'),
    ("importlib_alias_value", 'import importlib\nimp = importlib.import_module\nimp("os")\n'),
    ("runpy", 'import runpy\nrunpy.run_path("other.py")\n'),
    ("globals_lookup", 'globals()["x"] = 1\n'),
    ("module_dict", 'import os\nos.__dict__["re" + "move"]("victim.txt")\n'),
    ("sys_modules", 'import sys\nsys.modules["os"].remove("victim.txt")\n'),
    ("getattr_passed_as_value", "import os\nfs = list(map(getattr, [os], ['re' + 'move']))\n"),
    ("methodcaller_dynamic", "import operator\noperator.methodcaller(NAME)\n"),
    ("builtins_exec", "import builtins\nbuiltins.exec('pass')\n"),
    # Codex r1：从任何模块 import 原语（不看别名）、模块别名、多名字实参
    (
        "from_builtins_alias",
        'import os\nfrom builtins import getattr as g\ng(os, NAME)("victim")\n',
    ),
    (
        "from_operator_alias",
        'import os\nfrom operator import attrgetter as ag\nag("path", NAME)(os)\n',
    ),
    ("from_operator_literal_alias", 'from operator import methodcaller as mc\nmc("x")\n'),
    ("from_builtins_vars", "from builtins import vars as v\nv()\n"),
    ("from_sys_modules", "from sys import modules as m\nm['os']\n"),
    ("module_alias_builtins", 'import builtins as b\nimport os\nb.getattr(os, "re" + "move")\n'),
    ("module_alias_builtins_exec", 'import builtins as b\nb.exec("pass")\n'),
    (
        "module_alias_operator",
        'import operator as op\nimport os\nop.attrgetter("path", NAME)(os)\n',
    ),
    ("attrgetter_second_name", 'import os, operator\noperator.attrgetter("path", NAME)(os)\n'),
    ("attrgetter_star", "import os, operator\noperator.attrgetter(*NAMES)(os)\n"),
    ("getattr_keyword_name", 'import os\ngetattr(os, **{"name": "x"})\n'),
    ("getattr_star_args", "import os\ngetattr(*ARGS)\n"),
    ("getattr_one_arg", "getattr(NAME)\n"),
    ("methodcaller_second_unused", "import operator\noperator.methodcaller(*NAMES)\n"),
    ("partial_getattr", "import functools, os\nfunctools.partial(getattr, os)(NAME)\n"),
    (
        "partial_methodcaller",
        "import functools, operator\nfunctools.partial(operator.methodcaller)\n",
    ),
    ("vars_alias_value", "v = vars\nv()\n"),
    ("builtins_dict_name", "__builtins__['ex' + 'ec']\n"),
    # 复核 r2：带实参的 vars 即 `x.__dict__`；同类反射 / 枚举入口
    ("vars_of_module", 'import os\nvars(os)["re" + "move"]("victim")\n'),
    ("vars_of_builtins", 'import builtins, os\nvars(builtins)["getattr"](os, NAME)\n'),
    ("vars_of_namespace", "import argparse\nvars(argparse.Namespace())\n"),
    ("type_dict", "type(plt).__dict__\n"),
    ("class_dict", "plt.__class__.__dict__\n"),
    ("object_getattribute", "object.__getattribute__(plt, NAME)\n"),
    ("inspect_getmembers", "import inspect, os\ninspect.getmembers(os)\n"),
    ("inspect_getattr_static", "import inspect, os\ninspect.getattr_static(os, NAME)\n"),
    ("pydoc_locate", 'import pydoc\npydoc.locate("os.remove")\n'),
    ("frame_globals", "import sys\nsys._getframe().f_globals\n"),
    ("methodcaller_name_by_keyword", "import operator\noperator.methodcaller(name=NAME)\n"),
    ("inside_function_never_called", "def helper(n):\n    return getattr(plt, n)\n"),
]


@pytest.mark.parametrize("name,body", UNPROVABLE, ids=[c[0] for c in UNPROVABLE])
def test_unprovable_dynamic_dispatch_never_goes_native(name, body):
    route = _route(MPL + body + TAIL)
    assert route.execution == "safe", route
    assert route.reason == runroute.REASON_UNPROVABLE_DYNAMIC, route
    assert route.confidence == runroute.CONFIDENCE_MEDIUM


PROVABLE = [
    # 字面属性名：调用目标是静态可读的，不受影响
    ("getattr_literal", 'fig = plt.figure()\nlab = getattr(fig, "get_label")()\n'),
    ("getattr_literal_default", 'x = getattr(plt, "rcParams", None)\n'),
    ("hasattr_variable", "ok = hasattr(plt, NAME)\n"),
    ("re_compile", 'import re\nre.compile("a+")\n'),
    ("pandas_eval_method", "import pandas as pd\ndf = pd.DataFrame()\ndf.eval('a + b')\n"),
    ("from_operator_itemgetter", "from operator import itemgetter\nitemgetter(1)\n"),
    ("attrgetter_all_literal", 'import operator\noperator.attrgetter("a", "b.c")\n'),
    ("methodcaller_literal", 'import operator\noperator.methodcaller("get_label")\n'),
    ("import_builtins_plain", "import builtins\nbuiltins.print('x')\n"),
    (
        "methodcaller_with_call_kwargs",
        'import operator\noperator.methodcaller("set_title", "X", loc="left")\n',
    ),
    ("methodcaller_with_call_args", 'import operator\noperator.methodcaller("set_xlim", 0, 1)\n'),
]


@pytest.mark.parametrize("name,body", PROVABLE, ids=[c[0] for c in PROVABLE])
def test_literal_attribute_names_and_unrelated_lookalikes_are_not_affected(name, body):
    route = _route(MPL + body + TAIL)
    assert (route.execution, route.reason) == ("native", runroute.REASON_SAVEFIG_READBACK), route


def test_the_rule_only_makes_routing_more_conservative():
    """不扩权：门槛只在本会升 high 时生效；没有 R1 / R3 证据的脚本路由与有无该事实无关。"""
    import dataclasses

    src = MPL + 'import os\ngetattr(os, "re" + "move")\nplt.savefig("x.png")\n'
    facts = runroute.facts_of(src)
    assert facts.dynamic_dispatch
    plain = dataclasses.replace(facts, dynamic_dispatch=False)
    kw = dict(cwd_mode=runroute.CWD_SANDBOX, project_root=ROOT, script=SCRIPT)
    assert runroute.decide(facts, **kw) == runroute.decide(plain, **kw)
    assert runroute.decide(plain, **kw).execution == "safe"


def test_reason_is_in_the_closed_set():
    assert runroute.REASON_UNPROVABLE_DYNAMIC in runroute.REASONS
