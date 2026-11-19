"""包里的脚本用相对导入时按模块运行（ADR 0196）：真 sidecar + 真用户 Python + 真文件，穿过公共入口（准备会话）。

`plots/__init__.py` + `plots/helpers.py` + `plots/fig.py`（`from .helpers import tidy`）：直接 `python plots/fig.py`
必然 `ImportError: attempted relative import with no known parent package`，Tavotto 以前同样失败（`script_error`）。

* safe 路径：准备会话 `completed`、出图；图的身份仍是项目相对的 `plots/fig.py`（描述符 / 资产 id 用的 target），原文件一字节不变，
  项目里不出现 `__pycache__`；
* safe 被拦（`savefig` 之后 `os.stat` 读回）后同一会话回退 native：同样按模块运行并出图（脚本共执行 2 次）；
* 对照：没有相对导入的子目录脚本照旧按脚本路径跑，不受影响。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from support import foundation_app as fa, nativekit
from test_execution_fallback_e2e import (
    ENV,
    SESSIONS,
    _native_sessions,
    _runs,
    _settled,
    _wait,
)

pytestmark = nativekit.needs_user_python

sidecar_app = fa.sidecar_app

HELPERS = "def tidy(ax):\n    ax.set_xlabel('x')\n"
FIG = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from .helpers import tidy

open({log!r}, "a").write("x")
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1], [0, 1])
tidy(ax)
"""
#: safe 的守卫拦下 `savefig` 之后的 `os.stat` 读回（见 test_execution_fallback_e2e.REPLACE_SCRIPT）：同一会话回退 native
BLOCKED_FIG = """\
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from .helpers import tidy

with open({log!r}, "a") as f:
    f.write("x")
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1], [0, 1])
tidy(ax)
fig.savefig(os.path.join(os.path.dirname(os.path.abspath(__file__)), "out.png"))
os.stat(os.path.join(os.path.dirname(os.path.abspath(__file__)), "out.png"))
"""


def _package_project(tmp_path: Path, body: str):
    proj = tmp_path / "proj"
    (proj / "plots").mkdir(parents=True)
    out = tmp_path / "out"  # 观测文件放项目外
    out.mkdir()
    log = out / "compute.log"
    (proj / "plots" / "__init__.py").write_text("", encoding="utf-8")
    (proj / "plots" / "helpers.py").write_text(HELPERS, encoding="utf-8")
    (proj / "plots" / "fig.py").write_text(body.format(log=str(log)), encoding="utf-8")
    return proj, log


def _snapshot(proj: Path) -> dict:
    return {
        p.relative_to(proj).as_posix(): p.read_bytes()
        for p in sorted(proj.rglob("*"))
        if p.is_file()
    }


def _create(app, script: str) -> dict:
    status, report = app.call(SESSIONS, {"script": script}, timeout=120)
    assert status == 201, report
    return report


def _run(app, report: dict) -> str:
    (run,) = [a for a in report["actions"] if a["kind"] == "run"]
    status, claimed = app.call(
        f"{SESSIONS}/{report['session_id']}/actions",
        {"action_id": run["id"], "expected_config_revision": report["config_revision"]},
        timeout=60,
    )
    assert status == 202 and claimed["claimed"] is True, claimed
    return report["session_id"]


def test_a_package_script_with_relative_imports_runs_and_keeps_its_identity(tmp_path):
    proj, log = _package_project(tmp_path, FIG)
    before = _snapshot(proj)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        first = _create(app, "plots/fig.py")
        sid = _run(app, first)
        final = _settled(app, sid)

        assert final["phase"] == "completed", final
        assert final["facts"]["figure_captured"] is True
        assert _runs(log) == 1, "按模块运行一次，没有先失败再重跑"
        # 身份：图挂在原脚本（项目相对路径）名下，不是 `plots.fig`
        blob = json.dumps(final)
        assert "plots/fig.py" in blob
        assert "plots.fig" not in blob, "模块名不进公开身份"
        assert final["attempt_history"][0]["profile"] == "safe"
    after = _snapshot(proj)
    assert not [p for p in after if "__pycache__" in p], "项目里不出现字节码目录"
    assert {k: after[k] for k in before} == before, "原文件一字节不变"
    # 注册表条目（身份）：键是脚本的项目相对路径，图名是脚本 stem——与不按模块运行时一致
    scripts = json.loads(after["tavotto_registry.json"])["scripts"]
    assert list(scripts) == ["plots/fig.py"] and scripts["plots/fig.py"]["stems"] == ["fig"], (
        scripts
    )


def test_a_blocked_safe_run_falls_back_to_native_and_also_runs_as_a_module(tmp_path):
    proj, log = _package_project(tmp_path, BLOCKED_FIG)
    before = _snapshot(proj)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        first = _create(app, "plots/fig.py")
        sid = _run(app, first)
        final = _settled(app, sid)

        assert final["phase"] == "completed", final
        assert final["facts"]["figure_captured"] is True
        assert _runs(log) == 2, "safe 一次（被拦）+ native 一次"
        safe, native = final["attempt_history"]
        assert (safe["profile"], safe["outcome"]) == ("safe", "blocked")
        assert (native["profile"], native["outcome"]) == ("native", "succeeded")
        live = [s for s in _native_sessions(app, proj) if s["state"] not in ("ended", "failed")]
        assert len(live) == 1 and live[0]["owner"] == "desktop"
        blob = json.dumps(final)
        assert "plots/fig.py" in blob and "plots.fig" not in blob
    after = _snapshot(proj)
    assert {k: after[k] for k in before} == before, "原文件一字节不变"
    assert "plots/out.png" in after, "native 透传 savefig：脚本自己写的文件照常落盘"
    assert list(json.loads(after["tavotto_registry.json"])["scripts"]) == ["plots/fig.py"]


def test_a_plain_subdirectory_script_is_unaffected(tmp_path):
    proj = tmp_path / "proj"
    (proj / "plots").mkdir(parents=True)
    out = tmp_path / "out"
    out.mkdir()
    log = out / "compute.log"
    (proj / "plots" / "fig.py").write_text(
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        f"open({str(log)!r}, 'a').write('x')\n"
        "fig, ax = plt.subplots(figsize=(3, 2))\nax.plot([0, 1], [0, 1])\n",
        encoding="utf-8",
    )
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        sid = _run(app, _create(app, "plots/fig.py"))
        final = _settled(app, sid)
        assert final["phase"] == "completed", final
        assert final["facts"]["figure_captured"] is True
        assert _runs(log) == 1


def test_the_relative_import_failure_is_still_reported_when_the_package_is_not_provable(tmp_path):
    """命名空间包（没有 `__init__.py`）：判据判「不改」，行为与以前一字不差——仍是脚本报错，不静默换跑法。"""
    proj, log = _package_project(tmp_path, FIG)
    (proj / "plots" / "__init__.py").unlink()
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        report = _create(app, "plots/fig.py")
        runnable = [a for a in report["actions"] if a["kind"] == "run"]
        if runnable:
            final = _settled(app, _run(app, report))
        else:
            final = report
        _wait(lambda: True, "占位")
        assert final["phase"] != "completed"


@pytest.mark.parametrize("blocked", [False, True], ids=["safe", "fallback-native"])
def test_a_project_folder_that_is_itself_a_package(tmp_path, blocked):
    """项目文件夹本身也是包（项目根有 `__init__.py`，维护者 2026-11-19 裁决：也支持）：按 `proj.plots.fig` 运行，
    `sys.path[0]` 是项目的上一级；safe 出图、safe 被拦回退 native 也出图，身份仍是 `plots/fig.py`，原文件不变。"""
    proj, log = _package_project(tmp_path, BLOCKED_FIG if blocked else FIG)
    (proj / "__init__.py").write_text("", encoding="utf-8")
    before = _snapshot(proj)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        final = _settled(app, _run(app, _create(app, "plots/fig.py")))
        assert final["phase"] == "completed", final
        assert final["facts"]["figure_captured"] is True
        profiles = [h["profile"] for h in final["attempt_history"]]
        assert profiles == (["safe", "native"] if blocked else ["safe"]), profiles
        blob = json.dumps(final)
        assert "plots/fig.py" in blob and "proj.plots.fig" not in blob
    after = _snapshot(proj)
    assert {k: after[k] for k in before} == before, "原文件一字节不变"
