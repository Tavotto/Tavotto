"""解释器在 Tavotto 自己的会话缓存里建不了目录 → `interpreter_cache_denied`（反馈包 TVD-B6MR）。

主语：**worker 进程、用户代码之前、本会话缓存目录**（`out` / `sandbox` / `out/script-input`）。那一刻被系统拒绝
与脚本无关：以前归 `script_error`、remedy 走 AI 修复，用户被引去改脚本。现在：

1. 真 worker 里得到稳定码（不是 `script_error`），用户脚本一行没跑；
2. 智能修复路由给 `change_python`，即便 AI 连着也不给 AI 修复（golden 向量另在 `fix_remedy_vectors.json`）；
3. 准备报告的 `params.switch` 指明一键换回要清哪一层选择（全局 / 项目）；Tavotto 自己的解释器不给按钮。
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path

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

# ---------------------------------------------------------------- Windows 真机：ACL 拒写（TVD-B6MR 现场）
_windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows ACL；POSIX 走上面的 chmod 用例"
)


@contextlib.contextmanager
def _deny_create_children(folder: Path):
    """给当前用户在 `folder` 上加一条 deny ACE（新建子目录 AD + 新建文件 WD），复现现场的 `[WinError 5] 拒绝访问`；
    退出时撤掉。只拦「在里面新建」，不拦读与列目录——与现场「已存在的目录照常可用」一致。

    前提反证：加完 ACE 后本进程自己在里面建目录必须被拒；建得出来说明这台机器上 ACL 不生效（例如带备份特权的
    管理员），这条用例证明不了什么，判红而不是跳过。"""
    sid = (
        subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True
        )
        .stdout.strip()
        .split(",")[-1]
        .strip('"')
    )
    subprocess.run(
        ["icacls", str(folder), "/deny", f"*{sid}:(AD,WD)"], capture_output=True, check=True
    )
    try:
        probe = folder / "acl-precondition"
        try:
            probe.mkdir()
        except PermissionError:
            pass
        else:
            probe.rmdir()
            raise AssertionError(
                "前提不成立：加了 deny ACE 仍能在里面建目录，这台机器上复现不了 WinError 5"
            )
        yield
    finally:
        subprocess.run(
            ["icacls", str(folder), "/remove:d", f"*{sid}"], capture_output=True, check=False
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


# --------------------------------------------------------------------- 体检前置拦截（#1594）
@_no_chmod
@needs_worker
@pytest.mark.parametrize("bundled", [False, True])
def test_health_probe_refuses_an_interpreter_that_cannot_create_folders_in_the_data_dir(
    tmp_path, monkeypatch, bundled
):
    """体检进程的 cwd 是 `<data_dir>/cache/probe/` 下的空目录（与会话缓存同一棵树）：在里面建不了目录
    → 用户的解释器判 `interpreter_cache_denied`；Tavotto 自己的内置 runtime 不在这里判（会被当成安装不完整）。"""
    from tavotto.engine import projectenv

    scratch = tmp_path / "probe-scratch"
    scratch.mkdir()
    scratch.chmod(0o555)
    monkeypatch.setattr(projectenv, "_probe_scratch_dir", lambda: str(scratch))
    try:
        health = projectenv._probe_environment(bridgekit.USER_PYTHON, bundled=bundled)
    finally:
        if scratch.exists():  # 体检收尾会删掉自己的 cwd
            scratch.chmod(0o755)
    assert health.get("cache_write") == "denied", health
    if bundled:
        assert health.get("code") != projectenv.ERROR_CACHE_DENIED, health
    else:
        assert health == {**health, "ok": False, "code": projectenv.ERROR_CACHE_DENIED}


@needs_worker
def test_health_probe_still_accepts_a_normal_interpreter():
    from tavotto.engine import projectenv

    health = projectenv._probe_environment(bridgekit.USER_PYTHON)
    assert health.get("cache_write") == "ok", health
    assert health.get("ok") is True, health


def test_candidate_rejected_for_the_cache_gets_its_own_reason():
    from tavotto.engine import userenvs

    assert userenvs._HEALTH_CODE_TO_REJECT["interpreter_cache_denied"] == (
        userenvs.REJECT_CACHE_DENIED
    )
    assert userenvs.REJECT_CACHE_DENIED in userenvs.REJECT_CODES


@_windows_only
@needs_worker
def test_windows_worker_denied_by_acl_gets_the_stable_code(project):
    """TVD-B6MR 现场在真 Windows 上：`out` 已存在，在里面新建 `script-input` 撞 `[WinError 5]` →
    `interpreter_cache_denied`，用户脚本一行没跑。POSIX 的 chmod 版本在 Windows 上跳过，这条补上。"""
    out = engine_pool._worker_base(str(project), "fig.py") / "out"
    out.mkdir(parents=True, exist_ok=True)
    with _deny_create_children(out):
        with pytest.raises(engine_pool.WorkerError) as err:
            engine_pool.build("fig.py", str(project), "__main__")
    assert err.value.code == engine_pool.INTERPRETER_CACHE_DENIED_CODE, err.value.code
    assert "PermissionError" in err.value.traceback_text
    assert not (project / "ran.txt").exists()


@_windows_only
@needs_worker
@pytest.mark.parametrize("bundled", [False, True])
def test_windows_health_probe_refuses_an_interpreter_denied_by_acl(tmp_path, monkeypatch, bundled):
    """体检前置拦截（#1594）在真 Windows 上：体检 cwd 里建不了目录 → 用户的解释器判 `interpreter_cache_denied`；
    内置 runtime 不在这里判。"""
    from tavotto.engine import projectenv

    scratch = tmp_path / "probe-scratch"
    scratch.mkdir()
    monkeypatch.setattr(projectenv, "_probe_scratch_dir", lambda: str(scratch))
    with _deny_create_children(scratch):
        health = projectenv._probe_environment(bridgekit.USER_PYTHON, bundled=bundled)
    assert health.get("cache_write") == "denied", health
    if bundled:
        assert health.get("code") != projectenv.ERROR_CACHE_DENIED, health
    else:
        assert health == {**health, "ok": False, "code": projectenv.ERROR_CACHE_DENIED}
