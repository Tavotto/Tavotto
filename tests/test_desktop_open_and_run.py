"""V2 PR4 验收：前端「打开并运行」发出的**那串真实请求**，经真 `--desktop-sidecar` 服务 + 真用户 Python + 真文件，一次点击走到可编辑图。

装置同 `test_execution_fallback_e2e.py`（真服务子进程 + 会话认证 + 启动 nonce 换 cookie；独立证据只来自脚本自己写在项目外的文件）。
这里钉的是**前端契约**（`web/src/store/projectPreparationStore.ts`、`openAndRun.test.ts` 是它的另一半）：

* 打开 = `POST /api/engine/preparation-sessions {script, reuse_last_success}`，**没有 `execution`**；
* 检查完报告里有后端生成的 `run_when_ready`，认领它只交 `{action_id, expected_config_revision, impact_digest}`——摘要是动作自己给的；
* safe 被自己的守卫拦下后，**同一会话**接上第二个 attempt（后端的事，前端什么都不发）；报告 `completed`，`captured` 恰好一张图，
  它的资产 id 就是 `runtime:<脚本>#<图名>`——**不含执行语义**，所以前端「进入编辑」复用同一块面板、不会多开标签页；
* 脚本共执行 2 次（safe 被拦一次 + native 一次），之后进入编辑不再执行。

**这里不是真桌面窗口**：窗口 / Tauri 壳 / WebView / Windows 真机归 P10。
"""

from __future__ import annotations

import json

from support import nativekit
from tavotto.engine import figcapture
from test_execution_fallback_e2e import (
    ENV,
    REPLACE_SCRIPT,
    SESSIONS,
    _native_sessions,
    _project,
    _runs,
    _settled,
    sidecar_app,
)

pytestmark = nativekit.needs_user_python


def test_one_click_open_and_run_falls_back_in_the_same_session_with_one_stable_figure_identity(
    tmp_path,
):
    proj, log = _project(tmp_path, REPLACE_SCRIPT)
    with sidecar_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        # ▶ 发的第一个请求：只读检查。带 reuse_last_success，从不带 execution
        body = {"script": "fig.py", "reuse_last_success": True}
        assert "execution" not in body
        status, created = app.call(SESSIONS, body, timeout=120)
        assert status == 201, created
        assert created["phase"] == "ready_to_run"
        assert _runs(log) == 0  # 检查不执行
        (arm,) = [a for a in created["actions"] if a["kind"] == "run_when_ready"]
        digest = arm["impact"]["impact_digest"]
        assert digest, "授权摘要由动作自己给，前端只回显"

        # 第二个请求：认领 run_when_ready，只交动作 id + 看到的修订 + 回显的摘要
        claim = {
            "action_id": arm["id"],
            "expected_config_revision": created["config_revision"],
            "impact_digest": digest,
        }
        assert "execution" not in claim
        status, claimed = app.call(f"{SESSIONS}/{created['session_id']}/actions", claim, timeout=60)
        assert status == 202 and claimed["claimed"] is True, claimed

        final = _settled(app, created["session_id"])
        assert final["session_id"] == created["session_id"], "回退不新建会话"
        assert final["phase"] == "completed", final
        assert _runs(log) == 2, "safe 一次（被拦）+ native 一次"
        assert (proj / "cache.csv").read_text() == "2"  # native 里 os.replace 真的发生了
        assert [h["profile"] for h in final["attempt_history"]] == ["safe", "native"]

        # 前端自动进入编辑用的就是这份 captured：恰好一张图、资产 id 不含执行语义
        (fig,) = final["captured"]
        assert fig["execution_profile"] == "native"
        assert fig["asset_id"] == figcapture.runtime_asset_id("fig.py", fig["stem"])
        assert "native" not in fig["asset_id"] and "safe" not in fig["asset_id"]

        # 进入编辑不再执行用户脚本；同一个桌面拥有的进程停在屏障上等编辑
        live = [s for s in _native_sessions(app, proj) if s["state"] not in ("ended", "failed")]
        assert len(live) == 1 and live[0]["owner"] == "desktop"
        # 界面把「停止运行」对到这次运行靠 launch_id（= 当前尝试的计划号）
        assert live[0]["launch_id"] == final["provider"]["attempt_id"]
        assert _runs(log) == 2
        # 公开报告里没有路径 / 参数原文
        assert str(tmp_path) not in json.dumps(final["attempt_history"])
