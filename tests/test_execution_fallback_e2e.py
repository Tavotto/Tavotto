"""V2 PR1 验收：真服务子进程 + 真用户 Python + 真文件，穿过**真实公共入口**（准备会话）的 Safe→Native 执行回退（ADR 0127）。

独立可观测事实只来自脚本自己写在项目外的文件（`compute.log` 的执行次数、`cache.csv` 是否真被 `os.replace` 写出），
不来自被测接口的回显。**这里不是真桌面窗口**：窗口 / Tauri 壳 / Windows 真机归 P10。

* 路径写成运行前路由读不出的动态形态（V2 PR2 起字面量形态会被路由直接送去 native，见 `test_run_routing_e2e.py`）；
  脚本 `os.replace` 覆盖缓存再读回（safe 的守卫拦下并报 `safe_file_operation_blocked`）→ **同一会话**接上 native，
  脚本共执行 2 次，最终出图，`attempt_history` 两条；
* 除零 / 缺包 → 只执行 1 次，不回退；
* `safe_only` 项目、`TAVOTTO_EXECUTION_FALLBACK=0` → 不回退，如实报 safe 的限制；
* 取消在回退前发生 → 不再起 native。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from support import foundation_app as fa, nativekit
from tavotto.engine import execpolicy

ROOT = Path(__file__).resolve().parent.parent

pytestmark = nativekit.needs_user_python

ENV = {"TAVOTTO_USER_ENV_DISCOVERY": "0", "PYTHONPATH": str(ROOT / "src")}
sidecar_app = fa.sidecar_app  # 真 sidecar 夹具在 support/foundation_app.py（别的 e2e 也要桌面来源）


SESSIONS = "/api/engine/preparation-sessions"
SETTLED = (
    "completed",
    "partial",
    "action_required",
    "cancelled",
    "awaiting_configuration",
    "awaiting_confirmation",
)

#: 审计 EX03 的形状：写临时缓存 → `os.replace` 覆盖真实图库里的缓存 → 读回算值画图。safe 的守卫拦下 replace，
#: 读回拿到的不是直接 Python 会得到的值（safe 报 blocked）；native 里 replace 真实发生，值是 2。
REPLACE_SCRIPT = """\
import os
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

with open({log!r}, "a") as f:   # 执行次数：脚本每跑一遍写一个字符
    f.write("x")
here = Path(__file__).resolve().parent
name = os.environ.get("CACHE_NAME", "cache")  # 动态路径：运行前路由（ADR 0133）看不出读回，留给 safe 实跑 + 回退
tmp, final = here / (name + ".tmp"), here / (name + ".csv")
tmp.write_text("2")
os.replace(tmp, final)
value = int(final.read_text())
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1], [value, value])
"""
DIVIDE_SCRIPT = """\
open({log!r}, "a").write("x")
print(1 / 0)
"""
MISSING_SCRIPT = """\
open({log!r}, "a").write("x")
import definitely_not_installed_pkg_v2_policy  # noqa
"""
#: 同 REPLACE_SCRIPT，但在 `os.replace` 之前先算很久（忙等；safe 的守卫不替脚本跳过计算）：取消点在 safe 还没被拦下的时候。
#: 被拦发生在脚本读回的那一刻、脚本随即中止，所以「被拦之后」没有可以取消的长窗口——那个窗口由假 pool 用例钉住
SLOW_REPLACE_SCRIPT = REPLACE_SCRIPT.replace(
    "here = Path(__file__)", "n = 0\nwhile n < 10**12:\n    n += 1\nhere = Path(__file__)"
)


def _project(tmp_path, body: str):
    proj = tmp_path / "proj"
    proj.mkdir()
    out = tmp_path / "out"  # 观测文件放项目外
    out.mkdir()
    log = out / "compute.log"
    (proj / "fig.py").write_text(body.format(log=str(log)), encoding="utf-8")
    return proj, log


def _wait(predicate, what: str, timeout: float = 120.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = predicate()
        if got:
            return got
        time.sleep(0.2)
    raise AssertionError(f"{timeout}s 内没等到 {what}")


def _report(app, sid: str) -> dict:
    return app.call(f"{SESSIONS}/{sid}", timeout=30)[1]


def _settled(app, sid: str, timeout: float = 300.0) -> dict:
    def _poll():
        report = _report(app, sid)
        return report if report["phase"] in SETTLED else None

    return _wait(_poll, "准备会话落定", timeout)


def _create(app) -> dict:
    status, report = app.call(SESSIONS, {"script": "fig.py"}, timeout=120)
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


def _native_sessions(app, root: Path) -> list[dict]:
    return app.call(f"/api/native/sessions?project_root={root}", timeout=30)[1]["sessions"]


def _runs(log: Path) -> int:
    return len(log.read_text(encoding="utf-8")) if log.exists() else 0


def test_blocked_replace_falls_back_to_native_in_the_same_session_and_runs_twice(tmp_path):
    proj, log = _project(tmp_path, REPLACE_SCRIPT)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        first = _create(app)
        sid = _run(app, first)
        final = _settled(app, sid)

        assert final["session_id"] == first["session_id"], "回退不新建会话"
        assert final["phase"] == "completed", final
        assert final["facts"]["figure_captured"] is True
        assert _runs(log) == 2, "safe 一次（被拦）+ native 一次"
        # native 里 os.replace 真的发生了：safe 的守卫没拦它
        assert (proj / "cache.csv").read_text(encoding="utf-8") == "2"

        safe, native = final["attempt_history"]
        assert (safe["profile"], safe["outcome"], safe["code"]) == (
            "safe",
            "blocked",
            "safe_file_operation_blocked",
        )
        assert (native["profile"], native["cause"], native["outcome"]) == (
            "native",
            "safe_file_operation_blocked",
            "succeeded",
        )
        assert "profile" in native["changed"]
        assert final["provider"]["attempts"] == 2
        live = [s for s in _native_sessions(app, proj) if s["state"] not in ("ended", "failed")]
        assert (
            len(live) == 1 and live[0]["owner"] == "desktop"
        )  # 同一个桌面拥有的进程，停在屏障上等编辑
        # 历史里没有路径 / 解释器原文
        blob = json.dumps(final["attempt_history"])
        assert str(tmp_path) not in blob and str(proj) not in blob


def test_a_division_by_zero_script_runs_once_and_does_not_fall_back(tmp_path):
    proj, log = _project(tmp_path, DIVIDE_SCRIPT)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        sid = _run(app, _create(app))
        final = _settled(app, sid)
        time.sleep(3)  # 给「偷偷再跑」留出发生的时间
        assert _runs(log) == 1
        assert final["phase"] != "completed" and len(final["attempt_history"]) == 1
        assert final["provider"]["attempts"] == 1
        assert _native_sessions(app, proj) == []


def test_a_missing_package_does_not_fall_back(tmp_path):
    proj, log = _project(tmp_path, MISSING_SCRIPT)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        report = _create(app)
        runnable = [a for a in report["actions"] if a["kind"] == "run"]
        if runnable:
            sid = _run(app, report)
            final = _settled(app, sid)
        else:  # 依赖门在运行之前就拦下（一次都没执行）：同样不回退
            final = report
        time.sleep(3)
        assert _runs(log) <= 1
        assert final["phase"] != "completed"
        assert len(final["attempt_history"]) <= 1
        assert _native_sessions(app, proj) == []


def test_a_safe_only_project_is_respected(tmp_path, monkeypatch):
    proj, log = _project(tmp_path, REPLACE_SCRIPT)
    work = tmp_path / "work"
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(work / "config"))
    (work / "config").mkdir(parents=True)
    execpolicy.set_mode(proj, execpolicy.MODE_SAFE_ONLY)  # 与被测服务共用同一份项目设置
    with sidecar_app(proj, work, env_overrides=ENV) as app:
        sid = _run(app, _create(app))
        final = _settled(app, sid)
        time.sleep(3)
        assert _runs(log) == 1
        assert final["outcome"]["code"] == "safe_file_operation_blocked"
        assert final["attempt_history"][0]["declined"] == "policy_safe_only"
        assert _native_sessions(app, proj) == []
        assert not (proj / "cache.csv").exists(), "safe 的守卫仍然护着真实图库"


def test_the_kill_switch_disables_the_fallback(tmp_path):
    proj, log = _project(tmp_path, REPLACE_SCRIPT)
    with sidecar_app(
        proj, tmp_path / "work", env_overrides={**ENV, execpolicy.FALLBACK_ENV: "0"}
    ) as app:
        sid = _run(app, _create(app))
        final = _settled(app, sid)
        time.sleep(3)
        assert _runs(log) == 1
        assert final["outcome"]["code"] == "safe_file_operation_blocked"
        assert final["attempt_history"][0]["declined"] == "fallback_disabled"
        assert _native_sessions(app, proj) == []


def test_cancel_before_the_fallback_starts_no_native_run(tmp_path):
    proj, log = _project(tmp_path, SLOW_REPLACE_SCRIPT)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        report = _create(app)
        sid = _run(app, report)
        _wait(lambda: _runs(log) >= 1, "脚本开始执行")
        mid = _report(app, sid)
        cancel = next(a for a in mid["actions"] if a["kind"] == "cancel")
        status, _ = app.call(
            f"{SESSIONS}/{sid}/actions",
            {"action_id": cancel["id"], "expected_config_revision": mid["config_revision"]},
            timeout=60,
        )
        assert status == 200
        final = _settled(app, sid)
        assert final["phase"] == "cancelled"
        time.sleep(3)
        assert _runs(log) == 1, "取消之后不应再起 native"
        assert _native_sessions(app, proj) == [], "取消之后不应再起 native"


def test_a_browser_mode_service_never_falls_back_to_native(tmp_path):
    """反例：同一份脚本、同一个会话端点，但服务是浏览器模式（本机凭据头认证，不是桌面窗口）：safe 的限制照旧如实报告，
    一个 native 进程都不起、脚本只执行 1 次、历史里写明为什么没回退。"""
    proj, log = _project(tmp_path, REPLACE_SCRIPT)
    with fa.running_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        sid = _run(app, _create(app))
        final = _settled(app, sid)
        time.sleep(3)
        assert _runs(log) == 1
        assert final["outcome"]["code"] == "safe_file_operation_blocked"
        assert final["attempt_history"][0]["declined"] == "not_desktop_initiated"
        assert _native_sessions(app, proj) == []
        assert not (proj / "cache.csv").exists()
