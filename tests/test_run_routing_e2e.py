"""V2 PR2 验收：真服务子进程 + 真用户 Python + 真文件，穿过**真实公共入口**（准备会话）的运行前路由（ADR 0133）。

独立可观测事实只来自脚本自己写在项目外的文件（`compute.log`：脚本每执行一遍写一个字符）与项目里真实发生的文件操作，
不来自被测接口的回显。**这里不是真桌面窗口**：窗口 / Tauri 壳 / Windows 真机归 P10。

* 普通绘图 → safe 一次成功；
* `savefig` → `Image.open` 读回 → 直达 native，脚本只执行 1 次（safe 不白跑）；
* 临时文件 → `os.replace` → 读回：含守卫操作，伤害闸门留在 safe，被拦后同会话回退 native，共 2 次，`os.replace` 最终真实发生；
* 动态路径（路由看不出）→ safe 被拦 → 同会话回退 native，共 2 次；
* `safe_only` 项目 → 不走 native；
* 上次 native 成功的脚本再次运行 → 不沿用 native（记录没观察过 helper / 数据），回到静态路由。
"""

from __future__ import annotations

import time

from support import foundation_app as fa, nativekit
from tavotto.engine import execpolicy
from test_execution_fallback_e2e import (
    ENV,
    SESSIONS,
    _create,
    _native_sessions,
    _run,
    _runs,
    _settled,
    sidecar_app,
)

pytestmark = nativekit.needs_user_python

#: 执行次数：脚本每跑一遍写一个字符。写文件的是计数仪器而不是被测脚本形状——用 getattr 取 open，免得伤害闸门
#: （脚本里出现写入就不直达 native）把每个 e2e 脚本都拦成 safe；动态取名与非字面 mode（`chr(97)` = "a"）本来就在闸门的静态可见范围之外（#1086）
COUNT = """\
with getattr(__import__("builtins"), "op" + "en")({log!r}, chr(97)) as f:
    f.write("x")
"""
HEAD = """\
import os
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image
here = Path(__file__).resolve().parent
"""
FIG = """
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1], [0, 1])
"""

PLAIN = COUNT + HEAD + FIG + 'fig.savefig(here / "plain.png")\n'
READBACK = COUNT + HEAD + FIG + 'fig.savefig("stage.png")\nImage.open("stage.png").load()\n'
REPLACE = (
    COUNT
    + HEAD
    + """
tmp, final = here / "cache.tmp", here / "cache.csv"
tmp.write_text("2")
os.replace(tmp, final)
value = int(final.read_text())
"""
    + FIG
)
#: 同样的读回，但文件名来自运行时（路由读不出）：safe 实跑才被拦，再由 PR1 的同会话回退接手
DYNAMIC = (
    COUNT
    + HEAD
    + FIG
    + """\
name = os.environ.get("STAGE_NAME", "stage") + ".png"
fig.savefig(name)
Image.open(name).load()
"""
)


def _project(tmp_path, body: str):
    proj = tmp_path / "proj"
    proj.mkdir()
    out = tmp_path / "out"  # 观测文件放项目外
    out.mkdir()
    log = out / "compute.log"
    (proj / "fig.py").write_text(body.format(log=str(log)), encoding="utf-8")
    return proj, log


def _reopen(app) -> dict:
    """同一脚本再次打开：会话还在，回 200 与它重新检查后的报告。"""
    status, report = app.call(SESSIONS, {"script": "fig.py"}, timeout=120)
    assert status in (200, 201), report
    return report


def _history(final):
    return [(h["profile"], h["outcome"]) for h in final["attempt_history"]]


def test_an_ordinary_plot_runs_once_in_safe(tmp_path):
    proj, log = _project(tmp_path, PLAIN)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        first = _create(app)
        route = first["plan"]["route"]
        assert (route["execution"], route["reason"]) == ("safe", "ordinary_plot")
        final = _settled(app, _run(app, first))
        assert final["phase"] == "completed" and final["facts"]["figure_captured"] is True
        time.sleep(2)
        assert _runs(log) == 1
        assert _history(final) == [("safe", "succeeded")]
        assert _native_sessions(app, proj) == []


def test_savefig_then_image_open_goes_straight_to_native_and_executes_once(tmp_path):
    proj, log = _project(tmp_path, READBACK)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        first = _create(app)
        route = first["plan"]["route"]
        assert (route["execution"], route["reason"], route["confidence"]) == (
            "native",
            "savefig_readback",
            "high",
        )
        assert route["rules"] == ["R1"] and route["elapsed_ms"] > 0
        final = _settled(app, _run(app, first))
        assert final["session_id"] == first["session_id"]
        assert final["phase"] == "completed" and final["facts"]["figure_captured"] is True
        time.sleep(2)
        assert _runs(log) == 1, "safe 不白跑：脚本只执行 1 次"
        assert _history(final) == [("native", "succeeded")]
        assert (proj / "stage.png").is_file(), "native 里 savefig 真落了盘（读回读得到）"
        live = [s for s in _native_sessions(app, proj) if s["state"] not in ("ended", "failed")]
        assert len(live) == 1 and live[0]["owner"] == "desktop"


def test_tmp_write_replace_then_read_is_gated_to_safe_and_falls_back_to_native(tmp_path):
    """伤害闸门（ADR 0133）：含守卫操作（`os.replace`）的脚本运行前不直达 native；safe 被拦后由 0127 同会话静默回退，
    `os.replace` 最终在 native 里真实发生。"""
    proj, log = _project(tmp_path, REPLACE)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        first = _create(app)
        route = first["plan"]["route"]
        assert (route["execution"], route["reason"]) == ("safe", "guarded_file_ops_present")
        final = _settled(app, _run(app, first))
        assert final["phase"] == "completed", final
        time.sleep(2)
        assert _runs(log) == 2, "safe 一次（被拦）+ native 一次"
        assert _history(final) == [("safe", "blocked"), ("native", "succeeded")]
        assert (proj / "cache.csv").read_text(encoding="utf-8") == "2" and not (
            proj / "cache.tmp"
        ).exists()


def test_a_dynamic_path_runs_safe_first_then_falls_back_to_native(tmp_path):
    proj, log = _project(tmp_path, DYNAMIC)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        first = _create(app)
        route = first["plan"]["route"]
        assert (route["execution"], route["confidence"]) == ("safe", "low")
        assert route["dynamic_paths"] >= 1
        final = _settled(app, _run(app, first))
        assert final["phase"] == "completed", final
        assert _runs(log) == 2, "safe 一次（被拦）+ native 一次"
        assert _history(final) == [("safe", "blocked"), ("native", "succeeded")]
        assert final["attempt_history"][0]["code"] == "safe_file_operation_blocked"


def test_a_safe_only_project_never_goes_native(tmp_path, monkeypatch):
    proj, log = _project(tmp_path, READBACK)
    work = tmp_path / "work"
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(work / "config"))
    (work / "config").mkdir(parents=True)
    execpolicy.set_mode(proj, execpolicy.MODE_SAFE_ONLY)  # 与被测服务共用同一份项目设置
    with sidecar_app(proj, work, env_overrides=ENV) as app:
        first = _create(app)
        route = first["plan"]["route"]
        assert (route["execution"], route["reason"]) == ("safe", "user_explicit_profile")
        final = _settled(app, _run(app, first))
        time.sleep(2)
        assert _runs(log) == 1
        assert final["outcome"]["code"] == "safe_file_operation_blocked"
        assert _native_sessions(app, proj) == []


def test_a_native_success_is_not_reused_while_its_helper_inputs_were_never_observed(tmp_path):
    """V2 PR2（Codex #1034 r6）：native 成功的记录 `basis_observed:false`，与 #982 同样保守——重开不沿用 native，回到静态路由
    （动态路径 → safe 被拦 → 回退 native），而不是静默直接 native。"""
    proj, log = _project(tmp_path, DYNAMIC)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        first = _create(app)
        final = _settled(app, _run(app, first))
        assert _history(final) == [("safe", "blocked"), ("native", "succeeded")]
        assert _runs(log) == 2

        again = _reopen(app)
        route = again["plan"]["route"]
        assert (route["execution"], route["reason"]) == ("safe", "ordinary_plot")
        _run(app, again)
        deadline = time.time() + 150
        while _runs(log) < 4 and time.time() < deadline:
            time.sleep(0.3)
        time.sleep(3)
        assert _runs(log) == 4, "这一次又是 safe 一次（被拦）+ native 一次"
        settled = _settled(app, again["session_id"])
        assert [h["profile"] for h in settled["attempt_history"]][-2:] == ["safe", "native"]

        # 改了脚本：照旧回到静态判断
        (proj / "fig.py").write_text(
            (proj / "fig.py").read_text(encoding="utf-8") + "\n# edit\n", encoding="utf-8"
        )
        assert _reopen(app)["plan"]["route"]["reason"] == "ordinary_plot"


def test_a_browser_mode_session_is_never_routed_to_native(tmp_path):
    """非桌面来源（这里：浏览器模式的服务，无桌面窗口 cookie）不能被路由到 native：授权边界与回退一致。"""
    proj, log = _project(tmp_path, READBACK)
    with fa.running_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        final = _settled(app, _run(app, _create(app)))
        time.sleep(2)
        assert _runs(log) == 1
        assert _history(final) == [("safe", "blocked")]
        assert final["plan"]["route"]["declined"] == "not_desktop_initiated"
        assert _native_sessions(app, proj) == []


#: #1034 第五轮：这两个脚本里的 savefig→读回都不能算高置信——前者名字取决于 rcParams，后者根本走不到
EXTENSIONLESS_READBACK = COUNT + HEAD + FIG + 'fig.savefig("stage")\nopen("stage", "rb").read()\n'
UNREACHABLE_READBACK = (
    COUNT
    + HEAD
    + FIG
    + 'Path("victim.txt").unlink()\nraise SystemExit\nfig.savefig("stage.png")\nImage.open("stage.png").load()\n'
)


def test_ambiguous_or_unreachable_readbacks_stay_in_safe_before_running(tmp_path):
    for body in (EXTENSIONLESS_READBACK, UNREACHABLE_READBACK):
        sub = tmp_path / str(abs(hash(body)))
        sub.mkdir()
        proj, _log = _project(sub, body)
        (proj / "victim.txt").write_text("keep", encoding="utf-8")
        with sidecar_app(proj, sub / "work", env_overrides=ENV) as app:
            route = _create(app)["plan"]["route"]
            assert route["execution"] == "safe", route
            assert "R1" not in route["rules"], route
        assert (proj / "victim.txt").read_text(encoding="utf-8") == "keep"
