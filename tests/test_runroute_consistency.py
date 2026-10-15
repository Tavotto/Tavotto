"""V2 PR2：静态路由与运行期账本不分叉（同源对，ADR 0133）。

`runroute` 说「高置信 native」的每个形状，真 safe worker 实跑必须以 `safe_file_operation_blocked` 收场（否则静态规则在
把 safe 能跑对的脚本送去没有沙盒的 native）；`runroute` 说 safe 的几个形状（普通绘图、BytesIO 往返、沙盒内 replace、savefig 后只
`exists`），真 safe worker 实跑必须成功。真 Python 子进程 + 真文件，不是 mock。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用，参数名与导入名相同
from __future__ import annotations

import textwrap

import pytest

from tavotto.engine import pool as engine_pool, runroute, workdir
from test_worker_blocked_file_ops import CODE, figs, needs_worker  # noqa: F401

TAIL = """
fig, ax = plt.subplots(figsize=(2, 1.5))
ax.plot([0, 1], [0, 1])
"""
HEAD = """
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
"""


def _script(body: str) -> str:
    return HEAD + textwrap.dedent(body) + TAIL


# (名称, 正文, project 档?, 静态期望 execution, 实跑是否被拦)
CASES = [
    (
        "savefig_image_open",
        """
        from PIL import Image
        plt.savefig("tmp_stage.png")
        Image.open("tmp_stage.png").load()
        """,
        False,
        "native",
        True,
    ),
    (
        "savefig_open_rb_project_mode",
        """
        plt.savefig("tmp_stage.png")
        data = open("tmp_stage.png", "rb").read()
        """,
        True,
        "native",
        True,
    ),
    (
        "savefig_pathobj_read_bytes",
        """
        from pathlib import Path
        out = Path("tmp_stage.png")
        plt.savefig(out)
        out.read_bytes()
        """,
        False,
        "native",
        True,
    ),
    (
        "replace_script_relative_in_sandbox_mode",
        """
        from pathlib import Path
        here = Path(__file__).resolve().parent
        tmp, final = here / "cache.tmp", here / "cache.csv"
        tmp.write_text("2")
        os.replace(tmp, final)
        value = int(final.read_text())
        """,
        False,
        "safe",  # 伤害闸门（ADR 0133）：含 os.replace 的脚本不直达 native；真 safe worker 仍拦下，由 0127 静默回退接手
        True,
    ),
    (
        "replace_relative_in_project_mode",
        """
        with open("cache.tmp", "w") as f:
            f.write("2")
        os.replace("cache.tmp", "cache.csv")
        value = int(open("cache.csv").read())
        """,
        True,
        "safe",  # 伤害闸门（ADR 0133）：含 os.replace 的脚本不直达 native；真 safe worker 仍拦下，由 0127 静默回退接手
        True,
    ),
    (
        "plain_plot",
        """
        plt.savefig("plain.png")
        """,
        False,
        "safe",
        False,
    ),
    (
        "bytesio_roundtrip",
        """
        import io
        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        assert buf.read(4) == b"\\x89PNG"
        """,
        False,
        "safe",
        False,
    ),
    (
        "replace_inside_the_sandbox",
        """
        with open("cache.tmp", "w") as f:
            f.write("2")
        os.replace("cache.tmp", "cache2.csv")
        value = int(open("cache2.csv").read())
        """,
        False,
        "safe",
        False,
    ),
    (
        "savefig_then_exists_only",
        """
        plt.savefig("exists_only.png")
        print(os.path.exists("exists_only.png"))
        """,
        False,
        "safe",
        False,
    ),
]


@needs_worker
@pytest.mark.parametrize(
    "name,body,project_mode,expected,blocked", CASES, ids=[c[0] for c in CASES]
)
def test_static_route_agrees_with_what_the_real_safe_worker_does(
    figs, name, body, project_mode, expected, blocked
):
    source = _script(body)
    script = f"{name}.py"
    (figs / script).write_text(source, encoding="utf-8")
    mode = workdir.MODE_PROJECT if project_mode else workdir.MODE_SANDBOX
    workdir.set_mode(figs, mode)

    route = runroute.decide(
        runroute.facts_of(source), cwd_mode=mode, project_root=figs, script=script
    )
    assert route.execution == expected, route.public()

    if blocked:
        with pytest.raises(engine_pool.WorkerError) as err:
            engine_pool.build(script, str(figs), "__main__")
        assert err.value.code == CODE, (err.value.code, str(err.value))
    else:
        engine_pool.build(script, str(figs), "__main__")  # 不抛 = safe 里完整成功
