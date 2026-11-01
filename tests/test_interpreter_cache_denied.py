"""解释器在 Tavotto 自己的会话缓存里建不了目录 → `interpreter_cache_denied`（反馈包 TVD-B6MR）。

主语：**worker 进程、用户代码之前、本会话缓存目录**（`out` / `sandbox` / `out/script-input`）。那一刻被系统拒绝
与脚本无关：以前归 `script_error`、remedy 走 AI 修复，用户被引去改脚本。现在：

1. 真 worker 里得到稳定码（不是 `script_error`），用户脚本一行没跑；
2. 智能修复路由给 `change_python`，即便 AI 连着也不给 AI 修复（golden 向量另在 `fix_remedy_vectors.json`）；
3. 准备报告的 `params.switch` 指明一键换回要清哪一层选择（全局 / 项目）；Tavotto 自己的解释器不给按钮。
"""

from __future__ import annotations

import os
import sys

import pytest

from support import bridgekit
from tavotto.engine import fixrouter, pool as engine_pool, preparation

_no_chmod = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX 权限位；root 无视权限位",
)
needs_worker = pytest.mark.skipif(
    bridgekit.USER_PYTHON is None, reason="找不到装有 matplotlib 的解释器"
)

SCRIPT = """
from pathlib import Path
Path(__file__).with_name("ran.txt").write_text("ran")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([1, 2, 3])
"""


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "fig.py").write_text(SCRIPT)
    yield root
    engine_pool.shutdown_all(str(root), wait=True)


@_no_chmod
@needs_worker
def test_worker_that_cannot_create_its_cache_folder_gets_the_stable_code(project):
    # 现场 `4-51`：`out` 早就在（上一次别的解释器建的），这一次要在里面新建 `script-input` 被拒
    out = engine_pool._worker_base(str(project), "fig.py") / "out"
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o555)
    try:
        with pytest.raises(engine_pool.WorkerError) as err:
            engine_pool.build("fig.py", str(project), "__main__")
    finally:
        out.chmod(0o755)
    assert err.value.code == engine_pool.INTERPRETER_CACHE_DENIED_CODE
    assert "PermissionError" in err.value.traceback_text
    # 用户代码之前就停了：脚本第一行的副作用不存在
    assert not (project / "ran.txt").exists()


def test_router_sends_it_to_change_python_even_with_ai_connected():
    ctx = fixrouter.Ctx(ai_connected=True, python_from="3.12.14")
    remedy = fixrouter.choose(engine_pool.INTERPRETER_CACHE_DENIED_CODE, ctx)
    assert remedy == {"kind": "change_python", "from": "3.12.14", "to": None, "env": None}
    # 已经换过一次解释器还是这样：不再重复同一种修法
    tried = fixrouter.Ctx(
        ai_connected=True, history=({"run": 1, "seq": 1, "changed": ["interpreter"]},)
    )
    assert fixrouter.choose(engine_pool.INTERPRETER_CACHE_DENIED_CODE, tried)["kind"] != (
        "change_python"
    )


@pytest.mark.parametrize(
    ("source", "switch"),
    [
        (engine_pool.SOURCE_CONFIGURED, "global"),
        (engine_pool.SOURCE_SYSTEM, "project"),
        (engine_pool.SOURCE_PROJECT_VENV, "project"),
        # Tavotto 自己的解释器 / 环境变量：清掉选择换不出别的，不给按钮
        (engine_pool.SOURCE_BUNDLED, ""),
        (engine_pool.SOURCE_MANAGED_PROJECT, ""),
        (engine_pool.SOURCE_MANAGED, ""),
        (engine_pool.SOURCE_CURRENT, ""),
        (engine_pool.SOURCE_ENV, ""),
        ("", ""),
    ],
)
def test_switch_scope_follows_where_the_failing_interpreter_was_chosen(source, switch):
    assert preparation.cache_denied_switch(source) == switch


def test_global_choice_that_is_also_the_project_choice_clears_both(tmp_path):
    """全局指定的那条同时也是项目记住的：只清全局，`resolve_worker_python` 下一档就回到它（Codex #1595）。"""
    from tavotto.engine import projectenv

    root = tmp_path / "proj"
    root.mkdir()
    python = sys.executable
    try:
        projectenv.remember(root, python, automatic=False, trigger="user_selected")
        assert (
            preparation.cache_denied_switch(
                engine_pool.SOURCE_CONFIGURED, project_root=str(root), python=python
            )
            == "both"
        )
        # 项目记住的是别的解释器：那是用户为这个项目的另一个决定，只清全局
        other = tmp_path / "other-python"
        other.write_text("")
        assert (
            preparation.cache_denied_switch(
                engine_pool.SOURCE_CONFIGURED, project_root=str(root), python=str(other)
            )
            == "global"
        )
        # 项目已经明确用默认链条：清全局就够
        projectenv.remember_default(root)
        assert (
            preparation.cache_denied_switch(
                engine_pool.SOURCE_CONFIGURED, project_root=str(root), python=python
            )
            == "global"
        )
    finally:
        projectenv.forget(root)
