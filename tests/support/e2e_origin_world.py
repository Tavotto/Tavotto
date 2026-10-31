"""Import Origin 验收场景的 e2e 世界：造出 `paper/` 项目、本地 wheelhouse 与（可选的）项目 `.venv`。

供 `web/e2e/import-origin.spec.ts` 经 `python tests/support/e2e_origin_world.py <根目录> <场景>` 调用，
**打印一行 JSON**（项目路径 / 运行日志 / wheelhouse / 期望值）。不依赖 pytest（CI 的 posix-e2e 没装）。

项目布局就是维护者计划里的最终验收场景：

    paper/{scripts/{figure1.py,figure2.py,utils.py}, data/results.csv, requirements.txt, .venv/}

`figure1.py` 里是 `import numpy as np; import matplotlib.pyplot as plt; import docx; from utils import load_results`。
脚本自己把「谁在跑它」写进项目**之外**的日志（解释器 / prefix / cwd / `docx` 从哪来 / 画出的 y），
e2e 的判据以这份日志为准，不以界面文案为准。

场景：

* `existing`：项目自带 `.venv`，里面已装 python-docx（离线 wheelhouse）——内置运行环境缺 docx，`.venv` 装齐；
* `missing`：没有 `.venv`；内置缺 docx，wheelhouse 里有 python-docx——走受管环境安装；
* `unknown`：脚本 import 了一个表外的名字 `mystery_lib`，哪里都没有它，也不在任何映射里——只能预填、不能自动装；
* `orphan`：脚本 import 的 `orphan_lib` 在 `PYTHONPATH` 上的某个目录里，**没有任何发行包元数据**——来源无法验证。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from support import offline_world, venvfixture  # noqa: E402
from tavotto.engine import pool as engine_pool  # noqa: E402

DOCX_DIST = "python-docx"
DOCX_VERSION = "1.1.2"

UTILS = '''\
import csv


def load_results(path):
    """按**当前工作目录**读数据：脚本在 scripts/ 下，只有运行目录 = 项目根才读得到。"""
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))[1:]
    return [(float(a), float(b)) for a, b in rows]
'''

RESULTS = "x,y\n1,2\n2,3.5\n3,1.25\n4,4\n"


def figure1(log: str, scenario: str) -> str:
    """`existing` / `missing`：验收场景原样（numpy / matplotlib / docx / 本地 utils）；`unknown` / `orphan`：把 docx 换成
    那个来源有问题的库，其余不变——这两条要看的是「来源不明时 Tavotto 做不做、做什么」，不该再夹一个可信包的安装。"""
    lib = {"unknown": "mystery_lib", "orphan": "orphan_lib"}.get(scenario)
    third = f"import {lib}\n" if lib else "import docx\n"
    probe = (
        f'"lib": {lib}.__file__, "lib_value": {lib}.answer() if hasattr({lib}, "answer") else {lib}.VALUE,'
        if lib
        else '"docx": docx.__file__,'
    )
    return f"""\
import json
import os
import sys

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
{third}from utils import load_results

LOG = {json.dumps(log)}

rows = load_results("data/results.csv")
x = np.array([r[0] for r in rows])
y = np.array([r[1] for r in rows])
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot(x, y)
ax.set_title("Figure 1")
fig.savefig("figure1.pdf")
with open(LOG, "a", encoding="utf-8") as f:
    f.write(json.dumps({{
        "executable": sys.executable,
        "prefix": sys.prefix,
        "cwd": os.getcwd(),
        {probe}
        "y": [float(v) for v in y],
    }}) + "\\n")
"""


FIGURE2 = """\
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1], [1, 0])
fig.savefig("figure2.pdf")
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build(root: Path, scenario: str) -> dict:
    parent = root / "我的 论文"
    project = parent / "paper"
    log = parent / "origin_exec_log.jsonl"
    wheelhouse = root / "wheelhouse"
    extra = root / "pythonpath_extra"

    _write(project / "scripts" / "figure1.py", figure1(str(log), scenario))
    _write(project / "scripts" / "figure2.py", FIGURE2)
    _write(project / "scripts" / "utils.py", UTILS)
    _write(project / "data" / "results.csv", RESULTS)
    _write(project / "requirements.txt", f"{DOCX_DIST}\n")

    offline_world.build_wheel(
        wheelhouse,
        name=DOCX_DIST,
        import_name="docx",
        version=DOCX_VERSION,
        body="def Document():\n    return object()\n",
    )
    if scenario == "unknown":
        # 用户确认包名之后才会装它：wheelhouse 里有，但 Tavotto 在用户确认之前没有任何理由去找它
        offline_world.build_wheel(
            wheelhouse,
            name="mystery-lib",
            import_name="mystery_lib",
            version="0.3",
            body="def answer():\n    return 42\n",
        )
        # 用户填错包名的那一档：这个包能装上，脚本要的 `mystery_lib` 也 import 得到（它把 mystery-lib 当传递依赖拉了进来），
        # 但**用户确认的那个包自己不提供它**——装后「发行包与版本」核验必须拦住，而不是因为 import 成功就放行
        offline_world.build_wheel(
            wheelhouse,
            name="other-lib",
            import_name="other_lib",
            version="1.0",
            requires=("mystery-lib",),
        )
    if scenario == "orphan":
        _write(extra / "orphan_lib" / "__init__.py", "VALUE = 7\n")

    pip_env = {
        **os.environ,
        "PIP_FIND_LINKS": str(wheelhouse),
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    }
    venv_python = None
    if scenario == "existing":
        base = engine_pool.find_worker_python()
        venv = venvfixture.make_project_venv(project, ".venv", python=base)
        venv_python = venvfixture.interpreter_of(venv)
        out = subprocess.run(
            # --ignore-installed：开发机的基础解释器 user site 里可能真装着 python-docx，
            # venv（--system-site-packages）看得见它，pip 会说「已满足」而不装 wheelhouse 里的——
            # 夹具要的是 venv 自己的那份，环境不同结果不能不同
            [venv_python, "-m", "pip", "install", "--no-input", "--ignore-installed", DOCX_DIST],
            env=pip_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if out.returncode != 0:
            raise SystemExit(f"在项目 .venv 里装 {DOCX_DIST} 失败：{out.stdout}\n{out.stderr}")

    return {
        "root": str(root),
        "project": str(project),
        "log": str(log),
        "wheelhouse": str(wheelhouse),
        "extra_path": str(extra),
        "venv_python": venv_python,
        "docx_dist": DOCX_DIST,
        "docx_version": DOCX_VERSION,
    }


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[2] not in ("existing", "missing", "unknown", "orphan"):
        raise SystemExit("usage: e2e_origin_world.py <root> existing|missing|unknown|orphan")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(build(Path(sys.argv[1]), sys.argv[2]), ensure_ascii=False))
