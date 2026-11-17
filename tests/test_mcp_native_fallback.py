"""MCP 打开图时 safe → native 回退（ADR 0127 修订 2026-11-17）的真链路：真 MCP 桥、真 worker、真用户 Python。

脚本 `savefig("fig.png")` 后又 `Image.open("fig.png")` 读回：safe 守卫拦下不落盘的 savefig → `safe_file_operation_blocked`；
桥在同一次 `open_figure` 里换真实 Python 跑一次，之后 apply / 导出都落在那张 native 活图上。独立可观测事实来自
脚本自己写在磁盘上的文件（`fig.png` 真被写出、`runs.log` 的执行次数），不只看接口回显。

* 默认：回退、回包带 `execution: native` 与一句如实的说明；apply 照常；关会话后进程被收掉；
* `safe_only` 项目 / `TAVOTTO_EXECUTION_FALLBACK=0` → 不回退，如实报 blocked 并带 `native_fallback_declined`；
* 脚本自己的异常（除零）→ 不回退；
* native 会话不能冷重放（`verify_replay` 明确拒绝）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto.engine import execpolicy, nativefallback, pool as engine_pool  # noqa: E402
from tavotto_mcp import bridge  # noqa: E402


def _worker_python():
    try:
        return engine_pool.find_worker_python()
    except engine_pool.WorkerError:
        return None


WORKER_PY = _worker_python()
pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="没有带科学栈的解释器，跳过真链路用例")

READBACK = """\
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image

HERE = Path(__file__).resolve().parent
with open(HERE / "runs.log", "a", encoding="utf-8") as fh:
    fh.write("run\\n")
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1, 2], [0, 1, 4], label="y")
ax.set_xlabel("x")
fig.savefig(HERE / "fig.png", dpi=50)
with Image.open(HERE / "fig.png") as im:
    width = im.size[0]
ax.set_title(f"w={width}")
"""

DIVIDE = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
fig.savefig("fig.png")
1 / 0
"""


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setenv("TAVOTTO_USER_ENV_DISCOVERY", "0")
    # native 子进程里的 bridge runner 要 import 到这份源码
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    monkeypatch.delenv(execpolicy.FALLBACK_ENV, raising=False)
    bridge.reset_root_authority()
    bridge.sessions().clear()
    yield
    bridge.shutdown_all()
    bridge.reset_root_authority()


def _project(tmp_path: Path, script: str) -> Path:
    figures = tmp_path / "fig"
    figures.mkdir()
    (figures / "fig.py").write_text(script, encoding="utf-8")
    (figures / "tavotto_registry.json").write_text(
        json.dumps(
            {"scripts": {"fig.py": {"entry": "__main__", "cost": "light", "stems": ["fig"]}}}
        ),
        encoding="utf-8",
    )
    return figures


def _runs(figures: Path) -> int:
    log = figures / "runs.log"
    return len(log.read_text("utf-8").splitlines()) if log.exists() else 0


def test_a_script_that_reads_back_its_savefig_opens_through_a_real_python(tmp_path):
    figures = _project(tmp_path, READBACK)
    out = bridge.open_figure(str(figures), stem="fig")
    assert out["ok"] is True
    assert out["execution"] == "native"
    assert out["fallback"] == {"from": "safe", "reason": "safe_file_operation_blocked"}
    assert "不受授权目录限制" in out["execution_note"]
    # 独立事实：native 那一次 savefig 真的写了盘，脚本一共执行了两次（safe 一次 + native 一次）
    assert (figures / "fig.png").is_file()
    assert _runs(figures) == 2
    session = bridge.sessions()[out["session_id"]]
    assert session.native is not None and session.native.alive()

    # 标题是脚本从读回的 PNG 里量出来的宽度：3 in × 50 dpi
    title = next(e for e in out["manifest"]["elements"] if e["gid"] == "axes_0.title")
    text = next(p["value"] for p in title["editable"] if p["prop"] == "text")
    assert text == "w=150"
    applied = bridge.apply_overrides(
        out["session_id"], [{"gid": "axes_0.title", "prop": "fontsize", "value": 14}]
    )
    assert applied["execution"] == "native"
    title = next(e for e in applied["manifest"]["elements"] if e["gid"] == "axes_0.title")
    assert next(p["value"] for p in title["editable"] if p["prop"] == "fontsize") == 14
    assert bridge.session_state(out["session_id"])["execution"] == "native"

    with pytest.raises(bridge.BridgeError) as replay:
        bridge.verify_replay(out["session_id"])
    assert replay.value.code == bridge.NATIVE_UNSUPPORTED

    native = session.native
    bridge.close_session(out["session_id"])
    assert session.native is None
    assert native.session.wait_for_state((), 20) in ("ended", "detached", "failed")


@pytest.mark.parametrize("how", ["safe_only", "kill_switch"])
def test_safe_only_and_the_kill_switch_keep_the_honest_blocked_error(tmp_path, monkeypatch, how):
    figures = _project(tmp_path, READBACK)
    if how == "safe_only":
        execpolicy.set_mode(str(figures), execpolicy.MODE_SAFE_ONLY)
        reason = execpolicy.REASON_SAFE_ONLY
    else:
        monkeypatch.setenv(execpolicy.FALLBACK_ENV, "0")
        reason = execpolicy.REASON_DISABLED
    with pytest.raises(bridge.BridgeError) as err:
        bridge.open_figure(str(figures), stem="fig")
    assert err.value.code == "safe_file_operation_blocked"
    assert err.value.extra["native_fallback_declined"] == reason
    assert _runs(figures) == 1
    assert not (figures / "fig.png").exists()


def test_the_scripts_own_exception_never_falls_back(tmp_path):
    figures = _project(tmp_path, DIVIDE)
    with pytest.raises(bridge.BridgeError) as err:
        bridge.open_figure(str(figures), stem="fig")
    assert err.value.code != "safe_file_operation_blocked"
    assert "native_fallback_declined" not in err.value.extra
    assert not bridge.sessions()


def test_decide_is_the_policy_with_the_mcp_origin(tmp_path):
    root = str(tmp_path)
    assert nativefallback.decide(root, "safe_file_operation_blocked", python=WORKER_PY).ok
    assert nativefallback.decide(root, "script_error", python=WORKER_PY).reason == "not_blocked"
    assert (
        nativefallback.decide(root, "safe_file_operation_blocked", python="").reason
        == execpolicy.REASON_PLAN_UNAVAILABLE
    )


def test_a_failed_native_launch_ends_the_process_and_reports_the_native_error(
    tmp_path, monkeypatch
):
    figures = _project(tmp_path, READBACK)
    ended = []

    class FakeSession:
        state = "barrier"
        script_error = None

        def wait_for_state(self, states, timeout):
            return "barrier"

        def ensure_built(self):
            return {"stems": {"other": {}}}

        def resume(self):
            ended.append("resume")
            return {}

    monkeypatch.setattr(nativefallback.desktopproc, "start", lambda launch, **kw: FakeSession())
    with pytest.raises(bridge.BridgeError) as err:
        bridge.open_figure(str(figures), stem="fig")
    assert err.value.code == nativefallback.CODE_STEM_MISSING
    assert ended == ["resume"]
    assert not bridge.sessions()


def test_running_the_readback_script_directly_works(tmp_path):
    """对照：同一个脚本直接用 Python 跑是能成功的（回退的理由成立）。"""
    figures = _project(tmp_path, READBACK)
    proc = subprocess.run(
        [WORKER_PY, str(figures / "fig.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(figures),
    )
    assert proc.returncode == 0, proc.stderr


def test_the_native_wrapper_keeps_method_signatures():
    """桥按 `inspect.signature(worker.export)` 判能否一次导出 PDF+SVG：外壳不能把签名抹成 `(*args, **kwargs)`。"""
    import inspect

    class Fake:
        def export(self, out_path, *, svg_path=None):
            return {"pdf": out_path, "svg": svg_path}

    wrapped = nativefallback.NativeWorker(Fake(), python=WORKER_PY)
    assert "svg_path" in inspect.signature(wrapped.export).parameters
    assert wrapped.export("a.pdf", svg_path="a.svg") == {"pdf": "a.pdf", "svg": "a.svg"}
