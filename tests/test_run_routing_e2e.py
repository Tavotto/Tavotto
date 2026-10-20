"""V2 PR2 验收：真服务子进程 + 真用户 Python + 真文件，穿过**真实公共入口**（准备会话）的运行前路由（ADR 0133）。

独立可观测事实只来自脚本自己写在项目外的文件（`compute.log`：脚本每执行一遍写一个字符）与项目里真实发生的文件操作，
不来自被测接口的回显。**这里不是真桌面窗口**：窗口 / Tauri 壳 / Windows 真机归 P10。

* 普通绘图 → safe 一次成功；
* `savefig` → `Image.open` 读回 → 直达 native，脚本只执行 1 次（safe 不白跑）；
* 临时文件 → `os.replace` → 读回：含守卫操作，伤害闸门留在 safe，被拦后同会话回退 native，共 2 次，`os.replace` 最终真实发生；
* 动态路径（路由看不出）→ safe 被拦 → 同会话回退 native，共 2 次；
* `safe_only` 项目 → 不走 native；
* 上次 native 成功的脚本再次运行 → 沿用 native、只执行 1 次（bridge 观察过 helper / 数据，#1047）；helper / 数据 / 脚本
  任一变了 → 回到静态路由；成功收口在 `show()` 屏障上同样沿用（依据只到屏障为止），沿用的那次运行停在第一个 `show()`
  上、不许放行；脚本跑完后的那次 build 把依据补成全部。
"""

from __future__ import annotations

import time

import pytest

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


#: 动态路径 + 本地 helper 模块 + 项目内数据文件：native 成功记录的依据（#1047）要覆盖后两样
DYNAMIC_INPUTS = (
    COUNT
    + HEAD
    + 'import helper\nscale = helper.SCALE * float((here / "data.csv").read_text())\n'
    + DYNAMIC[len(COUNT + HEAD) :]
)
#: 同样的动态路径，最后 `plt.show()`：bridge 停在 show 屏障上时脚本还没跑完
DYNAMIC_SHOW = DYNAMIC + "plt.show()\n"
#: 同上，`show()` 之后再写一笔到项目外的 tail.log：屏障之后的代码有没有跑过，独立可观测
DYNAMIC_SHOW_TAIL = (
    DYNAMIC_SHOW
    + """\
_tail = __import__("os").path.join(__import__("os").path.dirname({log!r}), "tail.log")
with getattr(__import__("builtins"), "op" + "en")(_tail, chr(97)) as f:
    f.write("y")
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


def _wait_runs(log, n: int, timeout: float = 150.0) -> None:
    deadline = time.time() + timeout
    while _runs(log) < n and time.time() < deadline:
        time.sleep(0.3)
    time.sleep(3)  # 给「偷偷再跑」留出发生的时间


def test_a_native_success_with_observed_inputs_is_reused_until_a_helper_or_data_file_changes(
    tmp_path,
):
    """#1047：桌面拥有的 bridge 观察 helper 导入与数据读取（与 safe 同口径），native 成功记录 `basis_observed:true`——
    重开直达 native、脚本只执行 1 次；helper / 数据 / 脚本任一变了，记录对不上，回到静态路由（动态路径 → safe 先跑）。"""
    proj, log = _project(tmp_path, DYNAMIC_INPUTS)
    (proj / "helper.py").write_text("SCALE = 1.0\n", encoding="utf-8")
    (proj / "data.csv").write_text("2\n", encoding="utf-8")
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        first = _create(app)
        assert first["plan"]["route"]["execution"] == "safe"
        final = _settled(app, _run(app, first))
        assert _history(final) == [("safe", "blocked"), ("native", "succeeded")]
        assert _runs(log) == 2

        again = _reopen(app)
        route = again["plan"]["route"]
        assert (route["execution"], route["reason"]) == ("native", "previous_successful_profile")
        _run(app, again)
        _wait_runs(log, 3)
        assert _runs(log) == 3, "沿用 native：safe 不白跑，只执行 1 次"
        settled = _settled(app, again["session_id"])
        assert _history(settled)[-1] == ("native", "succeeded")

        helper = proj / "helper.py"
        helper.write_text("SCALE = 3.0\n", encoding="utf-8")
        assert _reopen(app)["plan"]["route"]["reason"] == "ordinary_plot", "helper 变了"
        helper.write_text("SCALE = 1.0\n", encoding="utf-8")
        assert _reopen(app)["plan"]["route"]["reason"] == "previous_successful_profile"

        (proj / "data.csv").write_text("5\n", encoding="utf-8")
        assert _reopen(app)["plan"]["route"]["reason"] == "ordinary_plot", "数据变了"
        (proj / "data.csv").write_text("2\n", encoding="utf-8")
        assert _reopen(app)["plan"]["route"]["reason"] == "previous_successful_profile"

        # 改了脚本：照旧回到静态判断
        (proj / "fig.py").write_text(
            (proj / "fig.py").read_text(encoding="utf-8") + "\n# edit\n", encoding="utf-8"
        )
        assert _reopen(app)["plan"]["route"]["reason"] == "ordinary_plot"


def _tail(log) -> int:
    tail = log.parent / "tail.log"
    return len(tail.read_text(encoding="utf-8")) if tail.exists() else 0


def _live(app, proj, exclude=()):
    return [
        s
        for s in _native_sessions(app, proj)
        if s["state"] not in ("ended", "failed") and s["session_id"] not in exclude
    ]


def _continue(app, sid):
    return app.call(f"/api/native/sessions/{sid}/continue", {}, timeout=60)


def test_a_show_barrier_success_is_reused_and_the_reused_run_never_passes_the_first_show(tmp_path):
    """#1047 / #1202：成功收口在 `plt.show()` 屏障上，依据是屏障之前读过的全部。重开直达 native、只执行 1 次——不管旧运行之后
    有没有被放行。沿用的那次运行停在第一个屏障上：放行被拒（`native_barrier_final`），屏障之后的代码一行不跑。"""
    proj, log = _project(tmp_path, DYNAMIC_SHOW_TAIL)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        final = _settled(app, _run(app, _create(app)))
        assert _history(final) == [("safe", "blocked"), ("native", "succeeded")]
        assert _runs(log) == 2
        (old,) = _live(app, proj)
        assert old["barrier_reason"] == "show"

        # show 屏障上的 build 只到屏障为止：不许把记录补成全部（否则下面沿用的那次运行就不受第一个屏障约束了）
        app.call(f"/api/native/sessions/{old['session_id']}/build", {}, timeout=60)
        # 旧运行（不是沿用来的）照常可以放行：它跑过屏障、写了 tail，也不影响「屏障之前」的依据
        _continue(app, old["session_id"])
        deadline = time.time() + 60
        while _tail(log) < 1 and time.time() < deadline:
            time.sleep(0.2)
        assert _tail(log) == 1

        again = _reopen(app)
        route = again["plan"]["route"]
        assert (route["execution"], route["reason"]) == ("native", "previous_successful_profile")
        _run(app, again)
        _wait_runs(log, 3)
        assert _runs(log) == 3, "沿用 native：safe 不白跑，只执行 1 次"
        assert _history(_settled(app, again["session_id"]))[-1] == ("native", "succeeded")

        (reused,) = _live(app, proj, exclude={old["session_id"]})
        assert reused["barrier_reason"] == "show"
        with pytest.raises(fa.HttpError) as refused:
            _continue(app, reused["session_id"])
        assert (refused.value.status, refused.value.body.get("code")) == (
            409,
            "native_barrier_final",
        )
        time.sleep(2)
        assert _tail(log) == 1, "沿用的那次运行不许跑过 show()"


def test_a_show_barrier_success_stays_reusable_after_the_run_was_stopped(tmp_path):
    """停止运行之后照样沿用：依据只替屏障之前作保，与旧运行后来的去向无关（#1202 换结构）。"""
    proj, log = _project(tmp_path, DYNAMIC_SHOW)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        _settled(app, _run(app, _create(app)))
        (live,) = _live(app, proj)
        app.call(f"/api/native/sessions/{live['session_id']}/cancel", {}, timeout=60)
        deadline = time.time() + 60
        while _live(app, proj) and time.time() < deadline:
            time.sleep(0.2)
        assert not _live(app, proj)
        route = _reopen(app)["plan"]["route"]
        assert (route["execution"], route["reason"]) == ("native", "previous_successful_profile")


@pytest.mark.parametrize(
    "variant",
    [
        # 用户线程在屏障期间接着跑：停住的不是全部用户代码
        DYNAMIC_SHOW.replace(
            "plt.show()",
            "import threading\nthreading.Thread(target=__import__('time').sleep, args=(60,), daemon=True).start()\nplt.show()",
        ),
        # 是否停下取决于运行时
        DYNAMIC_SHOW.replace("plt.show()", 'plt.show(block=os.environ.get("BLOCK", "1") == "1")'),
    ],
    ids=["thread", "conditional_block"],
)
def test_a_show_that_may_not_stop_everything_next_time_is_not_reused(tmp_path, variant):
    """Codex #1202 P1：屏障范围的依据只在「下次必然停在同一个 show()、停住后没有别的用户代码在跑」时成立，否则不沿用。"""
    proj, _log = _project(tmp_path, variant)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        final = _settled(app, _run(app, _create(app)))
        assert _history(final) == [("safe", "blocked"), ("native", "succeeded")]
        assert _reopen(app)["plan"]["route"]["reason"] == "ordinary_plot"


def test_a_run_that_finished_after_its_show_upgrades_the_record_to_the_full_basis(tmp_path):
    """旧运行被放行、跑到结束屏障，在那里 build 一次：依据补成全部（`basis_observed: true`）。之后沿用的那次运行不再受
    「停在第一个屏障」约束——放行它不再被拒。show 屏障上的 build 不算（只到屏障为止）。"""
    proj, log = _project(tmp_path, DYNAMIC_SHOW)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        _settled(app, _run(app, _create(app)))
        (old,) = _live(app, proj)
        sid = old["session_id"]
        app.call(f"/api/native/sessions/{sid}/build", {}, timeout=60)  # show 屏障上的 build：不补全
        _continue(app, sid)

        def _at_script_end():
            (sess,) = [s for s in _native_sessions(app, proj) if s["session_id"] == sid]
            return sess if sess["barrier_reason"] == "script_end" else None

        deadline = time.time() + 60
        while not _at_script_end() and time.time() < deadline:
            time.sleep(0.2)
        assert _at_script_end(), "脚本没有跑到结束屏障"
        app.call(f"/api/native/sessions/{sid}/build", {}, timeout=60)

        again = _reopen(app)
        assert again["plan"]["route"]["reason"] == "previous_successful_profile"
        _run(app, again)
        _wait_runs(log, 3)
        assert _runs(log) == 3
        _settled(app, again["session_id"])
        (reused,) = _live(app, proj, exclude={sid})
        _continue(app, reused["session_id"])  # 全部依据：不受第一个屏障约束


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
