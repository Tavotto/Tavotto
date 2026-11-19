"""库式模块的函数入口：真 sidecar + 真用户 Python 穿过准备会话（ADR 0127 修订 2026-11-18，一份用户反馈）。

脚本只定义零参出图函数、没有守卫；保存辅助函数 `savefig` 之后 `os.stat` 读回（safe 拦下）。修复前：safe 被拦 → native 跑整份模块
→ 退出码 0、`no_figure_captured`。修复后：native 跑完模块再调同一个函数 → 出图。
"""

from __future__ import annotations

from pathlib import Path

from support import foundation_app as fa, nativekit
from test_execution_fallback_e2e import ENV, SESSIONS, _run, _settled

pytestmark = nativekit.needs_user_python
sidecar_app = fa.sidecar_app

LIBRARY = """\
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _save(fig, name):
    fig.savefig(name + ".png")
    os.stat(name + ".png")  # 读回自己刚存的文件：safe 拦下
    plt.close(fig)


def geography():
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([1, 2, 3])
    _save(fig, "geo")


def cooling():
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.bar([1, 2], [3, 4])
    _save(fig, "cool")
"""


def _session(app, script: str) -> dict:
    status, report = app.call(SESSIONS, {"script": script}, timeout=120)
    assert status == 201, report
    return report


def test_blocked_function_entry_falls_back_to_native_that_calls_the_same_function(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "climate.py").write_text(LIBRARY, encoding="utf-8")
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        first = _session(app, "climate.py")
        final = _settled(app, _run(app, first))
        profiles = [h["profile"] for h in final["attempt_history"]]
        assert profiles[0] == "safe" and profiles[-1] == "native", final["attempt_history"]
        assert final["phase"] == "completed", final
        assert final["facts"]["figure_captured"] is True
        # native 只调了被拦的那个入口函数：它的文件真落盘，另一个函数没被调用
        assert (proj / "geo.png").exists() and not (proj / "cool.png").exists()


def test_a_two_line_launcher_runs_the_library_function(tmp_path):
    """给用户的临时办法：同目录新建两行启动脚本去运行它（原脚本不改）。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "climate.py").write_text(LIBRARY, encoding="utf-8")
    (proj / "run_geo.py").write_text(
        "from climate import geography\ngeography()\n", encoding="utf-8"
    )
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        final = _settled(app, _run(app, _session(app, "run_geo.py")))
        assert final["phase"] == "completed", final
        assert final["facts"]["figure_captured"] is True
        assert Path(proj / "geo.png").exists()
