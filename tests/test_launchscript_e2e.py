"""启动脚本（ADR 0194）：真 sidecar + 真用户 Python。原脚本要先准备数据才能画（单独运行没有图）；启动脚本补上胶水后，
对**原脚本**的「打开并运行」出图，图挂在原脚本名下，原脚本与用户文件夹不变。safe 被拦时 native 回退同样执行启动脚本。"""

from __future__ import annotations

import hashlib

from support import foundation_app as fa, nativekit
from tavotto.engine import launchscript
from test_execution_fallback_e2e import ENV, SESSIONS, _run, _settled

pytestmark = nativekit.needs_user_python
sidecar_app = fa.sidecar_app

#: 画图函数要传入数据；文件里没有任何调用（真入口在仓库别处）
NEEDS_DATA = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def load():
    return [3, 1, 2]


def plot(rows, readback=False):
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot(rows)
    fig.savefig("trend.png")
    if readback:
        import os

        os.stat("trend.png")  # safe 拦下 → native 回退
    plt.close(fig)
"""


def _digest(proj):
    return {
        p.relative_to(proj).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(proj.rglob("*"))
        if p.is_file() and "tavottofile" not in p.parts and p.name != "tavotto_registry.json"
    }


def _go(tmp_path, launcher_code):
    proj = tmp_path / "proj"
    (proj / "code").mkdir(parents=True)
    (proj / "code" / "climate.py").write_text(NEEDS_DATA, encoding="utf-8")
    if launcher_code is not None:
        launchscript.write(proj, "code/climate.py", launcher_code)
    before = _digest(proj)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        status, report = app.call(SESSIONS, {"script": "code/climate.py"}, timeout=120)
        assert status == 201, report
        final = _settled(app, _run(app, report))
    return proj, before, final


def test_without_a_launcher_the_script_draws_nothing(tmp_path):
    _, _, final = _go(tmp_path, None)
    assert final["facts"].get("figure_captured") is not True


def test_a_launcher_makes_the_scripts_own_figure_appear(tmp_path):
    proj, before, final = _go(tmp_path, "from climate import load, plot\nplot(load())\n")
    assert final["phase"] == "completed", final
    assert final["facts"]["figure_captured"] is True
    after = _digest(proj)
    assert {k: v for k, v in after.items() if k in before} == before  # 原有文件一个字节不动
    assert set(after) - set(before) <= {
        "code/trend.png"
    }  # 用户文件夹里只多出脚本自己存的图（native 才落盘）


def test_blocked_safe_run_falls_back_to_native_that_also_runs_the_launcher(tmp_path):
    proj, _, final = _go(tmp_path, "from climate import load, plot\nplot(load(), readback=True)\n")
    profiles = [h["profile"] for h in final["attempt_history"]]
    assert profiles[0] == "safe" and profiles[-1] == "native", final["attempt_history"]
    assert final["phase"] == "completed", final
    assert (proj / "code" / "trend.png").exists()  # native 透传 savefig：执行的确实是启动脚本
