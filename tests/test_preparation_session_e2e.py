"""T01：script-first 准备会话经**真实公共入口**走到可编辑的 Figure（真 worker、真服务、会话认证默认开）。

夹具是「只有一份脚本」的项目：没有预生成的图、没有注册表、没有 `asset_id`。用户路径是

    创建检查会话（不执行）→ 看到 `ready_to_run` 与一个后端生成的 `run` 动作
        → 确认（认领动作，起唯一一次执行）→ 轮询到 `completed`
        → 用同一次捕获的图直接进编辑请求（`/api/engine/render`），脚本不再执行第二遍

脚本每执行一次往**项目外**的计数文件追加一个字符：计数是「脚本跑了几遍」的唯一真值（请求回显 /
toast / store 里的期望值都不算）。执行次数、图内数值都来自这个独立观测，不来自被测接口自己的回显。
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from support import foundation_app as fa
from tavotto.engine import figcapture, pool as _pool

ROOT = Path(__file__).resolve().parent.parent

try:
    WORKER_PY = _pool.find_worker_python()
except Exception:  # noqa: BLE001
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

YS = [2.0, 4.0, 8.0]
SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

open({counter!r}, "a", encoding="utf-8").write("x")
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1, 2], {ys!r})
ax.set_title("script first")
# 图名在运行期才算出来：静态扫描解不出它，只有真实执行才知道这张图叫什么（script-first 的本意）
name = "".join(chr(97 + i) for i in range(3))
fig.savefig("dyn_" + name + ".pdf")
"""
ENV = {"TAVOTTO_USER_ENV_DISCOVERY": "0", "PYTHONPATH": str(ROOT / "src")}

SESSIONS = "/api/engine/preparation-sessions"
SETTLED = (
    "completed",
    "partial",
    "action_required",
    "cancelled",
    "awaiting_configuration",
    "awaiting_confirmation",
)


def _runs(counter: Path) -> int:
    return len(counter.read_text(encoding="utf-8")) if counter.exists() else 0


def _tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def _call(app: fa.RunningApp, path: str, payload=None, **kw):
    """带凭据的调用，HTTP 错误也回 `(status, body)`（用例要断言状态码）。"""
    try:
        return app.call(path, payload, **kw)
    except fa.HttpError as exc:
        return exc.status, exc.body


def _wait_settled(app: fa.RunningApp, session_id: str, *, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    while True:
        status, report = app.call(f"{SESSIONS}/{session_id}", timeout=30)
        assert status == 200, report
        if report["phase"] in SETTLED:
            return report
        assert time.time() < deadline, report
        time.sleep(0.2)


def _ylim(render: dict) -> list[float]:
    axes = next(e for e in render["manifest"]["elements"] if e["role"] == "axes")
    return next(f["value"] for f in axes["editable"] if f["prop"] == "ylim")


@pytest.fixture
def script_only(tmp_path):
    """只有一份脚本的项目（计数文件在项目外）。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    counter = tmp_path / "runs.txt"
    (proj / "fig.py").write_text(SCRIPT.format(counter=str(counter), ys=YS), encoding="utf-8")
    return proj, counter, tmp_path / "work"


@needs_worker
def test_script_only_project_reaches_an_editable_figure_through_one_session(script_only):
    proj, counter, work = script_only
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        # 夹具有效：素材库把它列为可试运行的脚本，且没有任何面板
        _, panels = app.call("/api/panels", timeout=30)
        assert panels["panels"] == []
        _, registry = app.call("/api/registry", timeout=30)
        assert [s["script"] for s in registry["all_scripts"]] == ["fig.py"]
        assert registry["all_scripts"][0]["reason"] == "dynamic_stems"  # 静态解不出图名
        assert not (registry.get("scripts") or {}).get("fig.py", {}).get("stems")
        before_check = _tree(proj)

        # ---- 检查会话：不执行任何用户代码，不写用户项目
        status, report = app.call(SESSIONS, {"script": "fig.py"}, timeout=120)
        assert status == 201, report
        assert report["phase"] == "ready_to_run"
        assert report["target"]["kind"] == "script" and report["target"]["script"] == "fig.py"
        assert _runs(counter) == 0
        assert _tree(proj) == before_check
        (run,) = [a for a in report["actions"] if a["kind"] == "run"]
        assert report["outcome"]["kind"] == "pending"

        # ---- 无认证被拒（默认拒绝）
        req = urllib.request.Request(app.base + f"{SESSIONS}/{report['session_id']}")
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(req, timeout=10)
        assert denied.value.code == 401

        # ---- 确认：认领动作，起唯一一次执行；重复点击认领的是同一次尝试
        body = {"action_id": run["id"], "expected_config_revision": report["config_revision"]}
        status, first = app.call(f"{SESSIONS}/{report['session_id']}/actions", body, timeout=60)
        assert status == 202, first
        assert first["claimed"] is True
        status, again = app.call(f"{SESSIONS}/{report['session_id']}/actions", body, timeout=60)
        assert status == 200, again
        assert again["claimed"] is False
        assert (
            again["report"]["provider"]["attempt_id"] == first["report"]["provider"]["attempt_id"]
        )

        final = _wait_settled(app, report["session_id"])
        assert final["phase"] == "completed", final
        assert final["facts"]["figure_captured"] is True
        assert final["observation_seq"] > report["observation_seq"]
        assert _runs(counter) == 1

        # ---- 同一次捕获的图直接进编辑请求：脚本不再执行
        asset_id = figcapture.runtime_asset_id("fig.py", "dyn_abc")
        rendered = app.render(asset_id)
        margin = 0.05 * (max(YS) - min(YS))
        assert _ylim(rendered) == pytest.approx([min(YS) - margin, max(YS) + margin])
        assert _runs(counter) == 1

        # ---- 已知素材走同一个会话合同：热 runtime 在，确认之后复用，不再执行
        status, known = app.call(SESSIONS, {"id": asset_id}, timeout=120)
        assert status == 201 and known["target"]["kind"] == "asset", known
        assert known["target"]["asset_id"] == asset_id and known["phase"] == "ready_to_run"
        (run2,) = [a for a in known["actions"] if a["kind"] == "run"]
        status, claimed = app.call(
            f"{SESSIONS}/{known['session_id']}/actions",
            {"action_id": run2["id"], "expected_config_revision": known["config_revision"]},
            timeout=60,
        )
        assert status == 202 and claimed["claimed"] is True
        assert _wait_settled(app, known["session_id"])["phase"] == "completed"
        assert _runs(counter) == 1

        # ---- 旧接口（按素材 id 准备）对同一张图照旧工作，且复用热 runtime 不重跑
        legacy = app.prepare(asset_id)
        assert legacy["result"]["status"] == "ready"
        assert _runs(counter) == 1


@needs_worker
def test_the_old_blocking_probe_endpoint_still_registers_the_same_script(script_only):
    """兼容：`/api/registry/probe` 的响应形状不变（T01 保留它为薄兼容入口）。"""
    proj, counter, work = script_only
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        status, result = app.call("/api/registry/probe", {"script": "fig.py"}, timeout=300)
        assert status == 200
        assert result["registered"] is True
        assert result["stems"] == ["dyn_abc"]
        assert result["error"] is None
        assert _runs(counter) == 1


@needs_worker
def test_explicit_rerun_executes_again_and_refreshes_an_unobserved_input(tmp_path):
    """os.open input is outside the observer and project watcher; only an explicit run refreshes it."""
    proj = tmp_path / "proj"
    proj.mkdir()
    counter = tmp_path / "runs.txt"
    data = tmp_path / "input.txt"
    data.write_text("2", encoding="utf-8")
    (proj / "fig.py").write_text(
        "import os\n"
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        f"open({str(counter)!r}, 'a').write('x')\n"
        f"fd = os.open({str(data)!r}, os.O_RDONLY)\n"
        "try:\n"
        "    value = float(os.read(fd, 32))\n"
        "finally:\n"
        "    os.close(fd)\n"
        "fig, ax = plt.subplots()\n"
        "ax.plot([0, 1], [0, value])\n"
        "fig.savefig('unobserved.pdf')\n",
        encoding="utf-8",
    )
    with fa.running_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        _, report = app.call(SESSIONS, {"script": "fig.py"}, timeout=120)
        sid = report["session_id"]
        first_action = next(a for a in report["actions"] if a["kind"] == "run")
        app.call(
            f"{SESSIONS}/{sid}/actions",
            {
                "action_id": first_action["id"],
                "expected_config_revision": report["config_revision"],
            },
        )
        first = _wait_settled(app, sid)
        assert first["phase"] == "completed", first
        asset_id = figcapture.runtime_asset_id("fig.py", "unobserved")
        assert _ylim(app.render(asset_id)) == pytest.approx([-0.1, 2.1])
        assert _runs(counter) == 1
        data.write_text("20", encoding="utf-8")
        run = next(a for a in first["actions"] if a["kind"] == "run")
        payload = {"action_id": run["id"], "expected_config_revision": first["config_revision"]}
        _, claim = app.call(f"{SESSIONS}/{sid}/actions", payload)
        assert claim["claimed"] is True
        second = _wait_settled(app, sid)
        assert second["phase"] == "completed", second
        assert second["provider"]["attempt_id"] != first["provider"]["attempt_id"]
        assert _runs(counter) == 2
        assert _ylim(app.render(asset_id)) == pytest.approx([-1, 21])
        _, duplicate = app.call(f"{SESSIONS}/{sid}/actions", payload)
        assert duplicate["claimed"] is False
        assert _runs(counter) == 2
