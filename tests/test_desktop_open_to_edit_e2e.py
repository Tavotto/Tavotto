"""P07：经**真实公共入口**从「打开并运行」走到桌面拥有的 native 进程里的可编辑 Figure（AC-01 / 15 / 16 / 23）。

装置同 `test_preparation_session_e2e.py`：真服务子进程（`python -m tavotto` + 会话认证）、只有一份脚本的项目
（没有预建图、没有注册表、没有答案），用户路径是

    创建检查会话（`execution: native`，不执行）→ 认领 `run` → 脚本停在 `input()` 上（Pending 在后端）
        → 窗口回答 → **同一个进程**续跑 → 准备会话 `completed` → 对活图改一项并真实导出 → 取消 / 关闭项目真终止

可观测的独立事实全部来自脚本自己写在项目外的文件（运行次数、PID），不来自被测接口的回显。
**这里不是真桌面窗口**：窗口、Tauri 壳、WebView 都没有；Windows / macOS 真机归 P10。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pymupdf
import pytest

from support import foundation_app as fa, nativekit, procprobe
from tavotto.engine import figcapture

ROOT = Path(__file__).resolve().parent.parent

pytestmark = nativekit.needs_user_python

SECRET = "tok-NEVER-ON-DISK-91c2"
SCRIPT = """\
import getpass, json, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = {out!r}
with open(os.path.join(OUT, "compute.log"), "a") as f:   # 科学计算计数：脚本跑了几遍
    f.write("x")
pid_before = os.getpid()
state = {{"marker": object()}}
marker_id = id(state["marker"])

name = input("Your name? ")
token = getpass.getpass("API token: ")

fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1, 2], [2.0, 4.0, 8.0])
ax.set_title("hello " + name, fontsize=12)
fig.savefig(os.path.join(OUT, "stage.png"), dpi=100)   # 脚本自己的真写盘
json.dump(
    {{
        "pid_before": pid_before,
        "pid_after": os.getpid(),
        "same_object": id(state["marker"]) == marker_id,
        "token_len": len(token),
    }},
    open(os.path.join(OUT, "observed.json"), "w"),
)
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


def _alive(pid: int) -> bool:
    return procprobe.alive(pid)  # Windows 上 os.kill(pid, 0) 会杀进程（#523），探测只经 procprobe


def _native_sessions(app, root: Path) -> list[dict]:
    _, body = app.call(f"/api/native/sessions?project_root={root}", timeout=30)
    return body["sessions"]


def _wait(predicate, what: str, timeout: float = 120.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = predicate()
        if got:
            return got
        time.sleep(0.2)
    raise AssertionError(f"{timeout}s 内没等到 {what}")


def _settled(app, session_id: str) -> dict:
    def _poll():
        _, report = app.call(f"{SESSIONS}/{session_id}", timeout=30)
        return report if report["phase"] in SETTLED else None

    return _wait(_poll, "准备会话落定", 300.0)


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    out = tmp_path / "out"  # 观测文件放项目外：项目目录里没有任何预建的图 / 注册表
    out.mkdir()
    (proj / "fig.py").write_text(SCRIPT.format(out=str(out)), encoding="utf-8")
    return proj, out, tmp_path / "work"


def _start(app, root: Path):
    status, report = app.call(SESSIONS, {"script": "fig.py", "execution": "native"}, timeout=120)
    assert status == 201, report
    assert report["phase"] == "ready_to_run", report
    (run,) = [a for a in report["actions"] if a["kind"] == "run"]
    status, claimed = app.call(
        f"{SESSIONS}/{report['session_id']}/actions",
        {"action_id": run["id"], "expected_config_revision": report["config_revision"]},
        timeout=60,
    )
    assert status == 202 and claimed["claimed"] is True, claimed
    return report["session_id"]


def _pending(app, root: Path):
    def _poll():
        for s in _native_sessions(app, root):
            if s.get("pending_input"):
                return s
        return None

    return _wait(_poll, "脚本停在提问上")


def test_open_native_run_input_edit_export_and_cancel(project):
    proj, out, work = project
    assert sorted(p.name for p in proj.iterdir()) == ["fig.py"]  # 没有预建的图 / 注册表 / 答案
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        sid = _start(app, proj)

        # ---- 脚本停在 input() 上：Pending 是后端的事实（不属于某个窗口），准备会话仍在运行
        s1 = _pending(app, proj)
        assert s1["owner"] == "desktop" and s1["pending_input"]["kind"] == "input"
        pid = s1["process_pid"]
        assert _alive(pid)
        _, mid = app.call(f"{SESSIONS}/{sid}", timeout=30)
        assert mid["phase"] not in SETTLED, mid
        assert not (out / "observed.json").exists()

        # ---- 错的 request_id 被拒（不会喂给脚本）；对的才行
        with pytest.raises(fa.HttpError) as bad:
            app.call(
                f"/api/native/sessions/{s1['session_id']}/input",
                {"request_id": "nope", "text": "x"},
            )
        assert bad.value.status == 409 and bad.value.body["code"] == "native_input_not_pending"
        status, _ = app.call(
            f"/api/native/sessions/{s1['session_id']}/input",
            {"request_id": s1["pending_input"]["request_id"], "text": "Ada"},
        )
        assert status == 200

        s2 = _wait(
            lambda: next(
                (
                    s
                    for s in _native_sessions(app, proj)
                    if s.get("pending_input") and s["pending_input"]["kind"] == "getpass"
                ),
                None,
            ),
            "口令提问",
        )
        assert s2["session_id"] == s1["session_id"] and s2["pending_input"]["masked"] is True
        app.call(
            f"/api/native/sessions/{s2['session_id']}/input",
            {"request_id": s2["pending_input"]["request_id"], "text": SECRET},
        )

        final = _settled(app, sid)
        assert final["phase"] == "completed", final
        assert final["facts"]["figure_captured"] is True

        # ---- 同一个进程续跑、同一批对象、科学计算恰好一次（脚本自己写的独立观测）
        seen = json.loads((out / "observed.json").read_text())
        assert seen["pid_before"] == seen["pid_after"] == pid
        assert seen["same_object"] is True and seen["token_len"] == len(SECRET)
        assert (out / "compute.log").read_text() == "x"
        assert _alive(pid), "进入编辑期间进程应当还活着（屏障上）"

        # ---- 口令哪儿都没有：状态 / 准备会话报告 / 服务日志 / 数据目录
        blobs = [
            json.dumps(_native_sessions(app, proj)),
            json.dumps(final),
            app.server_log(),
        ]
        for text in blobs:
            assert SECRET not in text
        for path in Path(work / "data").rglob("*"):
            if path.is_file() and path.stat().st_size < 5_000_000:
                assert SECRET.encode() not in path.read_bytes(), path

        # ---- 活图：改一项（标题字号 12→22）并真实导出；改的是活进程里的 Figure
        asset_id = figcapture.runtime_asset_id("fig.py", "stage")
        rendered = app.render(asset_id)
        title = next(e for e in rendered["manifest"]["elements"] if e.get("role") == "title")
        assert any(f["prop"] == "fontsize" and f["value"] == 12 for f in title["editable"])
        edited = app.render(
            asset_id,
            [{"gid": title["gid"], "prop": "fontsize", "value": 22}],
        )
        etitle = next(e for e in edited["manifest"]["elements"] if e.get("role") == "title")
        assert any(f["prop"] == "fontsize" and f["value"] == 22 for f in etitle["editable"])
        assert (out / "compute.log").read_text() == "x", "编辑不应触发脚本再次执行"

        # ---- 真实导出：独立读 PDF——标题是用户答的名字，字号是改后的 22pt（不是脚本原样的 12pt）
        _, exported = app.call(
            "/api/export",
            {
                "scope": "original",
                "filename": "p07_edited",
                "formats": ["pdf"],
                "overwrite": "replace",
                "original": {
                    "figure_id": asset_id,
                    "overrides": [{"gid": title["gid"], "prop": "fontsize", "value": 22}],
                    "source_kind": "figure",
                    "w_mm": 76.2,
                    "h_mm": 50.8,
                },
            },
            timeout=300,
        )
        assert exported["status"] == "done", exported
        (pdf_out,) = [o for o in exported["outputs"] if o["format"] == "pdf"]
        with pymupdf.open(Path(exported["export_dir"]) / pdf_out["name"]) as doc:
            spans = [
                sp
                for block in doc[0].get_text("dict")["blocks"]
                for line in block.get("lines", [])
                for sp in line["spans"]
                if "hello Ada" in sp["text"]
            ]
        assert spans and round(spans[0]["size"], 1) == 22.0, spans
        assert (out / "compute.log").read_text() == "x", "导出不应触发脚本再次执行"

        # ---- 桌面保留的真文件语义：脚本自己 savefig 写的 PNG 是真文件；native 路径不开放原产物写回（AC-23）
        stage = out / "stage.png"
        before_bytes = stage.read_bytes()
        with pytest.raises(fa.HttpError) as refused:
            app.call(
                "/api/engine/update_source",
                {"id": asset_id, "patches": [], "mode": "pdf_png"},
                timeout=60,
            )
        assert refused.value.status == 400
        assert refused.value.body["code"] == "runtime_asset_has_no_original_artifact"
        assert stage.read_bytes() == before_bytes, "被拒绝的写回不应动脚本自己写的文件"

        # ---- 取消：真终止进程（不是放手），会话终态、不再可编辑
        live = next(s for s in _native_sessions(app, proj) if s["process_pid"] == pid)
        status, cancelled = app.call(f"/api/native/sessions/{live['session_id']}/cancel", {})
        assert status == 200 and cancelled["confirmed"] is True
        _wait(lambda: not _alive(pid), "进程退出", 30)
        ended = next(s for s in _native_sessions(app, proj) if s["process_pid"] == pid)
        assert ended["state"] == "ended" and ended["cancelled"] is True
        assert ended["pending_input"] is None


def test_closing_the_project_terminates_the_owned_process(project):
    proj, out, work = project
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        _start(app, proj)
        s = _pending(app, proj)
        pid = s["process_pid"]
        assert _alive(pid)
        _, projects = app.call("/api/projects", timeout=30)
        pj = next(
            p["id"]
            for p in projects["projects"]
            if Path(p["figures_dir"]).resolve() == proj.resolve()
        )
        status, body = app.call("/api/projects/close", {"id": pj}, timeout=60)
        assert status == 200 and body["ok"] is True
        _wait(lambda: not _alive(pid), "关项目后进程退出", 30)


def test_cancelling_the_preparation_session_while_waiting_for_input_kills_the_process(tmp_path):
    """准备会话的取消（脚本还停在提问上、没到屏障）也是真终止，且准备会话落 `cancelled`。

    脚本故意吞掉 EOF 后继续睡：若取消只是「放手」（断开控制通道，`force_kill` 的语义），`input()` 会拿到 EOF、
    脚本照样活着——只有真终止（`cancel_owned`）能让这个进程消失。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    (proj / "fig.py").write_text(
        "import time\n"
        f"open({str(out / 'started.txt')!r}, 'w').write('1')\n"
        "try:\n"
        "    input('go? ')\n"
        "except EOFError:\n"
        "    pass\n"
        "time.sleep(600)  # 放手不会让它停下，真终止才会\n"
        f"open({str(out / 'after.txt')!r}, 'w').write('ran')\n",
        encoding="utf-8",
    )
    with fa.running_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        sid = _start(app, proj)
        s = _pending(app, proj)
        pid = s["process_pid"]
        _, report = app.call(f"{SESSIONS}/{sid}", timeout=30)
        cancel = next(a for a in report["actions"] if a["kind"] == "cancel")
        status, _ = app.call(
            f"{SESSIONS}/{sid}/actions",
            {"action_id": cancel["id"], "expected_config_revision": report["config_revision"]},
            timeout=60,
        )
        assert status == 200
        _wait(lambda: not _alive(pid), "取消后进程退出", 30)
        assert _settled(app, sid)["phase"] == "cancelled"
        assert not (out / "after.txt").exists(), "取消后脚本不应继续往下跑"


def test_safe_default_is_unchanged_when_execution_is_omitted(project):
    """负对照：不带 `execution` 的会话仍是 safe（不起桌面拥有的进程，也没有 native 会话）。"""
    proj, out, work = project
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        status, report = app.call(SESSIONS, {"script": "fig.py"}, timeout=120)
        assert status == 201
        assert _native_sessions(app, proj) == []
        with pytest.raises(fa.HttpError) as bad:
            app.call(SESSIONS, {"script": "fig.py", "execution": "bogus"}, timeout=30)
        assert bad.value.status == 400


def test_a_failing_native_script_is_not_rerun_behind_the_users_back(tmp_path):
    """AC-14：脚本先写外部计数、再缺包炸掉——native 路径如实以脚本异常收场，**不**偷偷重跑（计数仍是 1），
    也不吞异常（真实 traceback 在报告里，不是「没有图」）。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    (proj / "fig.py").write_text(
        f"open({str(out / 'count.txt')!r}, 'a').write('x')\n"
        "import definitely_not_installed_pkg_p07  # noqa\n",
        encoding="utf-8",
    )
    with fa.running_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        sid = _start(app, proj)
        final = _settled(app, sid)
        assert final["phase"] != "completed", final
        time.sleep(3)  # 给「偷偷重跑」留出发生的时间
        assert (out / "count.txt").read_text() == "x"
        blob = json.dumps(final)
        assert "definitely_not_installed_pkg_p07" in blob or "script_error" in blob
        # 失败的运行不留活进程 / 租约：没有仍在运行的桌面会话
        live = [s for s in _native_sessions(app, proj) if s["state"] not in ("ended", "failed")]
        assert live == []


def test_stopping_the_native_session_cancels_the_preparation_too(tmp_path):
    """界面在提问上点「停止」只调 native 会话的 cancel 端点:准备会话也要落 `cancelled`,不是失败
    (Codex #1022 r4267467892)。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("input('go? ')\n", encoding="utf-8")
    with fa.running_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        sid = _start(app, proj)
        s = _pending(app, proj)
        pid = s["process_pid"]
        status, body = app.call(f"/api/native/sessions/{s['session_id']}/cancel", {})
        assert status == 200 and body["confirmed"] is True
        _wait(lambda: not _alive(pid), "进程退出", 30)
        assert _settled(app, sid)["phase"] == "cancelled"
