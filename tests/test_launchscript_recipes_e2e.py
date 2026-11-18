"""启动脚本的四种范式（ADR 0194 §二）：真 sidecar + 真用户 Python，对照评测里内置 AI 修不好的四类（`scripts/eval/fix_compat`）。

每个启动脚本都用同一个骨架：先做胶水（中和写死的交互后端 / 预先回答 `input()` / 设环境变量 / 声明所在的包），
再用 `open(__file__)` 读**原脚本**的字节、在同一份全局里 `exec`——safe 只把「装载主脚本」那一次换成启动脚本的字节，
`open()` 读到的是用户的原件；native 同理。原脚本一个字节不动、图挂在原脚本名下。
"""

from __future__ import annotations

import hashlib

import pytest

from support import foundation_app as fa, nativekit
from tavotto.engine import launchscript
from test_execution_fallback_e2e import ENV, SESSIONS, _run, _settled

pytestmark = nativekit.needs_user_python
sidecar_app = fa.sidecar_app

RUN_ORIGINAL = """
with open(__file__, "rb") as _f:
    _code = compile(_f.read(), __file__, "exec")
exec(_code, globals())
"""

CASES = {
    "backend": (
        {
            "fig.py": 'import matplotlib\nmatplotlib.use("TkAgg")\nimport matplotlib.pyplot as plt\n\n'
            "fig, ax = plt.subplots(figsize=(4, 3))\nax.plot([0, 1, 2], [0, 1, 4])\nplt.show()\n"
        },
        "fig.py",
        "import matplotlib\n\nmatplotlib.use = lambda *args, **kwargs: None  # 写死的交互后端：保留 Tavotto 的\n"
        + RUN_ORIGINAL,
    ),
    "stdin": (
        {
            "fig.py": 'import matplotlib\nmatplotlib.use("Agg")\nimport matplotlib.pyplot as plt\n\n'
            'n = int(input("how many points? "))\nfig, ax = plt.subplots(figsize=(4, 3))\n'
            "ax.plot(range(n), [i * i for i in range(n)])\n"
        },
        "fig.py",
        "import builtins\n\n_answers = iter(['20'])\nbuiltins.input = lambda prompt='': next(_answers)\n"
        + RUN_ORIGINAL,
    ),
    "env": (
        {
            "fig.py": "import csv\nimport os\nimport matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n\n"
            "root = os.environ['LAB_DATA_DIR']\n"
            "rows = list(csv.DictReader(open(os.path.join(root, 'series.csv'))))\n"
            "fig, ax = plt.subplots(figsize=(4, 3))\n"
            "ax.plot([float(r['x']) for r in rows], [float(r['y']) for r in rows])\n",
            "data/series.csv": "x,y\n0,1.0\n1,2.1\n2,3.9\n",
        },
        "fig.py",
        "import os\n\nos.environ.setdefault('LAB_DATA_DIR', os.path.join(os.path.dirname(__file__), 'data'))\n"
        + RUN_ORIGINAL,
    ),
    "relative-import": (
        {
            "plots/__init__.py": "",
            "plots/helpers.py": "def tidy(ax):\n    ax.set_xlabel('x')\n",
            "plots/fig.py": "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
            "from .helpers import tidy\n\nfig, ax = plt.subplots(figsize=(4, 3))\nax.plot([1, 2, 3], [3, 1, 2])\ntidy(ax)\n",
        },
        "plots/fig.py",
        "import os\nimport sys\n\n"
        "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
        "__package__ = 'plots'  # 它本来是包里的模块：相对导入按这个包解析\n" + RUN_ORIGINAL,
    ),
}


def _digest(proj):
    return {
        p.relative_to(proj).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(proj.rglob("*"))
        if p.is_file() and "tavottofile" not in p.parts and p.name != "tavotto_registry.json"
    }


def _run_case(tmp_path, files, script, launcher):
    proj = tmp_path / "proj"
    for rel, text in files.items():
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    if launcher is not None:
        launchscript.write(proj, script, launcher)
    before = _digest(proj)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        status, report = app.call(SESSIONS, {"script": script}, timeout=120)
        assert status == 201, report
        final = _settled(app, _run(app, report))
    after = _digest(proj)
    return final, before, after


@pytest.mark.parametrize("case", sorted(CASES))
def test_recipe_makes_the_original_scripts_figure_appear(tmp_path, case):
    files, script, launcher = CASES[case]
    final, before, after = _run_case(tmp_path, files, script, launcher)
    assert final["phase"] == "completed", (case, final.get("outcome"), final.get("result"))
    assert final["facts"]["figure_captured"] is True
    assert {k: v for k, v in after.items() if k in before} == before  # 原有文件一个字节不动


@pytest.mark.parametrize("case", sorted(set(CASES) - {"backend"}))
def test_without_the_launcher_the_case_really_fails(tmp_path, case):
    """对照：同一份项目不写启动脚本，不出图（证明上面的成功来自启动脚本）。

    `backend` 不在对照里：写死 TkAgg 只在没有 tkinter 的环境里失败（评测机上报 `missing_dependency`），装了 tkinter 的开发机照样出图。"""
    files, script, _ = CASES[case]
    final, _, _ = _run_case(tmp_path, files, script, None)
    assert final["facts"].get("figure_captured") is not True, case
