"""MCP 草稿带回（ADR 0189 第 2 条）的真链路：真 MCP 桥、真 worker、真 matplotlib。

没导出的改动在会话关闭 / 被淘汰时存成草稿（`mcp-drafts/`，另一个目录——不会被当成活会话按 id 复活），
重开同一张图（项目、stem、运行配置）时自动带回。独立可观测事实取自 manifest 里那个属性的真实值与磁盘上的脚本字节，
不只看接口回显。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto.engine import execpolicy, pool as engine_pool  # noqa: E402
from tavotto_mcp import bridge, server, sessionjournal  # noqa: E402


def _worker_python():
    try:
        return engine_pool.find_worker_python()
    except engine_pool.WorkerError:
        return None


pytestmark = pytest.mark.skipif(
    _worker_python() is None, reason="没有带科学栈的解释器，跳过真链路用例"
)

TWO_AXES = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, (ax, ax2) = plt.subplots(1, 2, figsize=(4, 2))
ax.plot([0, 1, 2], [0, 1, 4])
ax.set_title("left")
ax2.plot([0, 1, 2], [4, 1, 0])
ax2.set_title("right")
fig.savefig("fig.png")
"""

ONE_AXES = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(4, 2))
ax.plot([0, 1, 2], [0, 1, 4])
ax.set_title("left")
fig.savefig("fig.png")
"""

# safe 守卫拦下 savefig 后又读回 -> 回退真实 Python（test_mcp_native_fallback.py 同款）
READBACK = """\
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image

HERE = Path(__file__).resolve().parent
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1, 2], [0, 1, 4])
ax.set_title("t")
fig.savefig(HERE / "fig.png", dpi=50)
with Image.open(HERE / "fig.png") as im:
    im.size
"""

TITLE = "axes_0.title"
RIGHT = "axes_1.title"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setenv("TAVOTTO_USER_ENV_DISCOVERY", "0")
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    monkeypatch.delenv(execpolicy.FALLBACK_ENV, raising=False)
    bridge.reset_root_authority()
    bridge.sessions().clear()
    yield
    bridge.shutdown_all()
    bridge.reset_root_authority()


def _project(tmp_path: Path, script: str = TWO_AXES) -> Path:
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


def _drafts() -> Path:
    return Path(os.environ["TAVOTTO_DATA_DIR"]) / sessionjournal.DRAFTS_DIRNAME


def _draft_files() -> list[Path]:
    return sorted(_drafts().glob("s-*.json")) if _drafts().is_dir() else []


def _prop(manifest: dict, gid: str, prop: str):
    el = next(e for e in manifest["elements"] if e["gid"] == gid)
    return next(p["value"] for p in el["editable"] if p["prop"] == prop)


def _fontsize(patch_value: float, gid: str = TITLE) -> list[dict]:
    return [{"gid": gid, "prop": "fontsize", "value": patch_value}]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _edit_and_close(figures: Path, value: float = 23.0) -> str:
    out = bridge.open_figure(str(figures), stem="fig")
    bridge.apply_overrides(out["session_id"], _fontsize(value))
    closed = bridge.close_session(out["session_id"])
    assert closed["unexported_changes"] is True and closed["draft_saved"] is True
    return out["session_id"]


# ------------------------------- 带回 ---------------------------------------
def test_unexported_edits_come_back_when_the_same_figure_is_reopened(tmp_path):
    figures = _project(tmp_path)
    before = _sha(figures / "fig.py")
    old = _edit_and_close(figures)
    assert len(_draft_files()) == 1
    # 关了就是关了：旧 id 不能复活成活会话，草稿也不在会话记录目录里
    with pytest.raises(bridge.BridgeError) as gone:
        bridge.get_session(old)
    assert gone.value.code == "unknown_session"
    assert bridge.sessionjournal.load(bridge._journal_dir(), old) is None

    out = bridge.open_figure(str(figures), stem="fig")
    assert out["restored_draft"]["patches"] == 1
    assert out["restored_draft"]["from_session"] == old
    assert "dropped" not in out["restored_draft"]
    assert out["session_id"] != old
    # 独立事实：manifest 里标题字号真是改后的值，会话里也有这条 patch
    assert _prop(out["manifest"], TITLE, "fontsize") == 23.0
    assert bridge.get_session(out["session_id"]).patches == _fontsize(23.0)
    assert bridge.unexported_changes(bridge.get_session(out["session_id"])) is True
    # 草稿并进了活会话：不留第二份；脚本一字节没动
    assert _draft_files() == []
    assert _sha(figures / "fig.py") == before


def test_the_server_text_says_the_draft_came_back(tmp_path):
    figures = _project(tmp_path)
    _edit_and_close(figures)
    res = server.call_tool("tavotto_open_figure", {"project_path": str(figures), "stem": "fig"})
    body = res["structuredContent"]
    assert body["restored_draft"]["patches"] == 1
    assert "已带回上次没导出的 1 条修改" in res["content"][0]["text"]


def test_an_exported_session_leaves_no_draft(tmp_path):
    figures = _project(tmp_path)
    out = bridge.open_figure(str(figures), stem="fig")
    sid = out["session_id"]
    bridge.apply_overrides(sid, _fontsize(19.0))
    session = bridge.get_session(sid)
    session.exported_hash = session.patch_hash()  # 与真 export 一样：出了文件就在内存里记下这一版
    bridge.commit_export(sid)
    closed = bridge.close_session(sid)
    assert "unexported_changes" not in closed
    assert _draft_files() == []
    again = bridge.open_figure(str(figures), stem="fig")
    assert "restored_draft" not in again
    assert _prop(again["manifest"], TITLE, "fontsize") != 19.0


def test_exporting_after_a_restore_clears_the_draft_made_since(tmp_path):
    """草稿存在、又出了文件：这张图的草稿都清掉（导出成功即清草稿）。"""
    figures = _project(tmp_path)
    _edit_and_close(figures, 21.0)
    restored = bridge.open_figure(str(figures), stem="fig")
    sid = restored["session_id"]
    bridge.close_session(sid)  # 又存了一份草稿（带回的那条没导出）
    assert len(_draft_files()) == 1
    third = bridge.open_figure(str(figures), stem="fig")
    session = bridge.get_session(third["session_id"])
    session.exported_hash = session.patch_hash()
    bridge.commit_export(third["session_id"])
    assert _draft_files() == []


def test_a_session_without_edits_leaves_nothing_behind(tmp_path):
    figures = _project(tmp_path)
    out = bridge.open_figure(str(figures), stem="fig")
    closed = bridge.close_session(out["session_id"])
    assert "unexported_changes" not in closed
    assert _draft_files() == []


def test_discard_draft_opens_from_the_script_and_deletes_the_draft(tmp_path):
    figures = _project(tmp_path)
    _edit_and_close(figures)
    res = server.call_tool(
        "tavotto_open_figure", {"project_path": str(figures), "stem": "fig", "discard_draft": True}
    )
    body = res["structuredContent"]
    assert "restored_draft" not in body and body["discarded_draft"] is True
    assert _prop(body["manifest"], TITLE, "fontsize") != 23.0
    assert bridge.get_session(body["session_id"]).patches == []
    assert _draft_files() == []
    bridge.close_session(body["session_id"])
    assert "restored_draft" not in bridge.open_figure(str(figures), stem="fig")


def test_the_server_schema_and_descriptions_know_about_drafts():
    tools = {t["name"]: t for t in server._tools()}
    opener = tools["tavotto_open_figure"]
    assert opener["inputSchema"]["properties"]["discard_draft"]["type"] == "boolean"
    assert "discard_draft" in opener["description"] and "草稿" in opener["description"]
    assert "草稿" in tools["tavotto_close_session"]["description"]
    assert "草稿" in bridge.UNEXPORTED_NOTE and "重新打开" in bridge.UNEXPORTED_NOTE


# ------------------------------- 淘汰 / 进程换了 ------------------------------
def test_an_evicted_session_with_edits_becomes_a_draft(tmp_path, monkeypatch):
    figures = _project(tmp_path)
    out = bridge.open_figure(str(figures), stem="fig")
    sid = out["session_id"]
    bridge.apply_overrides(sid, _fontsize(17.0))
    limit = bridge.MAX_SESSIONS
    monkeypatch.setattr(bridge, "MAX_SESSIONS", 0)
    assert bridge._evict_if_needed() == [sid]
    monkeypatch.setattr(bridge, "MAX_SESSIONS", limit)
    assert sid not in bridge.sessions()
    assert bridge.sessionjournal.load(bridge._journal_dir(), sid) is None  # 活会话记录没了
    assert len(_draft_files()) == 1
    again = bridge.open_figure(str(figures), stem="fig")
    assert again["restored_draft"]["from_session"] == sid
    assert _prop(again["manifest"], TITLE, "fontsize") == 17.0


def test_closing_after_a_process_switch_still_saves_the_draft(tmp_path):
    figures = _project(tmp_path)
    out = bridge.open_figure(str(figures), stem="fig")
    sid = out["session_id"]
    bridge.apply_overrides(sid, _fontsize(15.0))
    bridge.sessions().clear()  # server 进程换了：会话只在落盘记录里
    closed = bridge.close_session(sid)
    assert closed["unexported_changes"] is True and closed["draft_saved"] is True
    again = bridge.open_figure(str(figures), stem="fig")
    assert again["restored_draft"]["patches"] == 1
    assert _prop(again["manifest"], TITLE, "fontsize") == 15.0


def test_a_failed_draft_save_says_the_edits_are_lost(tmp_path, monkeypatch):
    figures = _project(tmp_path)
    out = bridge.open_figure(str(figures), stem="fig")
    bridge.apply_overrides(out["session_id"], _fontsize(15.0))

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(bridge.sessionjournal, "save", boom)
    closed = bridge.close_session(out["session_id"])
    assert closed["closed"] is True and closed["draft_saved"] is False
    assert closed["note"] == bridge.UNEXPORTED_LOST_NOTE


# ------------------------------- 部分用不了 -----------------------------------
def test_patches_the_script_no_longer_supports_are_listed_not_fatal(tmp_path):
    figures = _project(tmp_path)
    out = bridge.open_figure(str(figures), stem="fig")
    sid = out["session_id"]
    bridge.apply_overrides(sid, _fontsize(23.0) + _fontsize(11.0, RIGHT))
    bridge.close_session(sid)
    (figures / "fig.py").write_text(ONE_AXES, encoding="utf-8")  # 右边那张子图没了
    bridge.shutdown_all()  # 池里的 worker 还抱着旧代码（mtime 轮询有窗口）：与「改了脚本重开」同一个收摊
    again = bridge.open_figure(str(figures), stem="fig")
    rd = again["restored_draft"]
    assert rd["patches"] == 1
    assert rd["dropped"] == [{"gid": RIGHT, "prop": "fontsize", "reason": "element_missing"}]
    assert "跳过" in rd["note"]
    assert _prop(again["manifest"], TITLE, "fontsize") == 23.0
    # 会话里只留真落下的那一条（画面与 patches 配对）
    assert bridge.get_session(again["session_id"]).patches == _fontsize(23.0)


def test_a_draft_that_cannot_render_falls_back_to_the_script_and_keeps_the_draft(
    tmp_path, monkeypatch
):
    figures = _project(tmp_path)
    _edit_and_close(figures)
    real = bridge._render

    def flaky(session, patches, **kw):
        if patches:
            raise bridge.BridgeError("boom", code="render_failed")
        return real(session, patches, **kw)

    monkeypatch.setattr(bridge, "_render", flaky)
    out = bridge.open_figure(str(figures), stem="fig")
    assert "restored_draft" not in out
    assert out["draft_restore_failed"]["code"] == "render_failed"
    assert len(_draft_files()) == 1  # 草稿还在
    monkeypatch.setattr(bridge, "_render", real)
    bridge.close_session(out["session_id"])
    retry = bridge.open_figure(str(figures), stem="fig")
    assert retry["restored_draft"]["patches"] == 1


def test_when_even_the_plain_render_fails_the_draft_survives(tmp_path):
    figures = _project(tmp_path)
    _edit_and_close(figures)
    (figures / "fig.py").write_text("raise RuntimeError('broken')\n", encoding="utf-8")
    bridge.shutdown_all()
    with pytest.raises(bridge.BridgeError):
        bridge.open_figure(str(figures), stem="fig")
    assert len(_draft_files()) == 1
    assert not bridge.sessions()


# ------------------------------- 越界 / 过期 ----------------------------------
def test_a_draft_outside_the_current_roots_is_never_rendered(tmp_path, monkeypatch):
    figures = _project(tmp_path)
    _edit_and_close(figures)
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv(bridge.ROOTS_ENV, str(other))
    bridge.reset_root_authority()
    renders = []
    real = bridge._render
    monkeypatch.setattr(bridge, "_render", lambda *a, **k: renders.append(1) or real(*a, **k))
    with pytest.raises(bridge.BridgeError) as err:
        bridge.open_figure(str(figures), stem="fig")
    assert err.value.code in ("workspace_root_changed", "path_outside_roots", "outside_roots") or (
        "工作区" in str(err.value)
    )
    assert renders == [], "越界项目不许渲染（= 执行脚本）"
    assert len(_draft_files()) == 1
    # 草稿本身也不会被直接带回：越界的草稿 `_find_draft` 当作没有
    assert bridge._find_draft(str(figures.resolve()), "fig", None) is None


def test_an_expired_draft_is_not_restored(tmp_path):
    figures = _project(tmp_path)
    _edit_and_close(figures)
    (path,) = _draft_files()
    record = json.loads(path.read_text(encoding="utf-8"))
    record["saved_at"] = record["saved_at"] - sessionjournal.TTL_SECONDS - 10
    path.write_text(json.dumps(record), encoding="utf-8")
    out = bridge.open_figure(str(figures), stem="fig")
    assert "restored_draft" not in out


def test_a_live_session_for_the_figure_is_not_replaced_by_a_draft(tmp_path):
    figures = _project(tmp_path)
    _edit_and_close(figures)
    first = bridge.open_figure(str(figures), stem="fig")  # 带回草稿，成了活会话
    assert first["restored_draft"]["patches"] == 1
    second = bridge.open_figure(str(figures), stem="fig")
    assert "restored_draft" not in second
    assert len({first["session_id"], second["session_id"]}) == 2  # 改过的会话不沿用（旧规矩）
    assert len(_draft_files()) == 0


# ------------------------------- 批量 -----------------------------------------
def test_batch_open_brings_the_draft_back_per_figure(tmp_path):
    figures = _project(tmp_path)
    (figures / "fig.png").write_bytes(
        b"x"
    )  # 批量 discover 要求产物在盘上；stems 点名不需要，但无害
    _edit_and_close(figures, 22.0)
    out = bridge.open_figures(str(figures), stems=["fig"])
    entry = out["opened"][0]
    assert entry["restored_draft"]["patches"] == 1
    assert _prop(bridge.get_session(entry["session_id"]).manifest, TITLE, "fontsize") == 22.0
    assert _draft_files() == []


# ------------------------------- native ---------------------------------------
def test_a_native_session_draft_comes_back_through_the_normal_open(tmp_path):
    figures = _project(tmp_path, READBACK)
    out = bridge.open_figure(str(figures), stem="fig")
    assert out["execution"] == "native"
    bridge.apply_overrides(out["session_id"], _fontsize(13.0))
    bridge.close_session(out["session_id"])
    assert len(_draft_files()) == 1
    again = bridge.open_figure(str(figures), stem="fig")
    assert again["execution"] == "native"  # 带回走正常 open：safe 被拦后同样回退 native
    assert again["restored_draft"]["patches"] == 1
    assert _prop(again["manifest"], TITLE, "fontsize") == 13.0


# ------------------------------- 一份 / 隔离 ----------------------------------
def test_only_the_latest_draft_per_figure_is_kept(tmp_path):
    figures = _project(tmp_path)
    _edit_and_close(figures, 23.0)
    out = bridge.open_figure(str(figures), stem="fig", discard_draft=True)
    bridge.apply_overrides(out["session_id"], _fontsize(9.0))
    bridge.close_session(out["session_id"])
    assert len(_draft_files()) == 1
    again = bridge.open_figure(str(figures), stem="fig")
    assert _prop(again["manifest"], TITLE, "fontsize") == 9.0


# ------------------------------- 边角 -----------------------------------------
def _stale_draft_beside_a_live_session(figures: Path):
    """活会话 A 改过（已带 patch），再手工存一份它的草稿（模拟别的进程 / 更早留下的陈旧草稿）。"""
    live = bridge.open_figure(str(figures), stem="fig")
    bridge.apply_overrides(live["session_id"], _fontsize(12.0))
    session = bridge.get_session(live["session_id"])
    assert bridge._save_draft(bridge._session_record(session))
    assert len(_draft_files()) == 1
    return live, session


def test_exporting_clears_a_draft_that_is_still_lying_around(tmp_path):
    figures = _project(tmp_path)
    live, session = _stale_draft_beside_a_live_session(figures)
    session.exported_hash = session.patch_hash()
    bridge.commit_export(live["session_id"])
    assert _draft_files() == []


def test_a_stale_draft_is_not_restored_over_a_live_session(tmp_path):
    figures = _project(tmp_path)
    live, _ = _stale_draft_beside_a_live_session(figures)
    other = bridge.open_figure(str(figures), stem="fig")  # 改过的会话不沿用，新建一个——但不套草稿
    assert other["session_id"] != live["session_id"]
    assert "restored_draft" not in other
    assert len(_draft_files()) == 1  # 草稿没被动


def test_closing_two_sessions_of_one_figure_keeps_only_the_latest_draft(tmp_path):
    figures = _project(tmp_path)
    live, _ = _stale_draft_beside_a_live_session(figures)
    other = bridge.open_figure(str(figures), stem="fig")
    bridge.apply_overrides(other["session_id"], _fontsize(8.0))
    bridge.close_session(live["session_id"])  # 第一份草稿（12）
    bridge.close_session(other["session_id"])  # 第二份（8）替掉它
    assert len(_draft_files()) == 1
    again = bridge.open_figure(str(figures), stem="fig")
    assert _prop(again["manifest"], TITLE, "fontsize") == 8.0


def test_the_normalize_contract_comes_back_with_a_whole_draft_but_not_a_partial_one(tmp_path):
    figures = _project(tmp_path)
    out = bridge.open_figure(str(figures), stem="fig")
    sid = out["session_id"]
    bridge.apply_overrides(sid, _fontsize(23.0))
    session = bridge.get_session(sid)
    session.contract = {"contract_id": "c-1", "allowed": [], "allowed_adjust": []}
    session.normalized = {"patch_hash": session.patch_hash()}
    bridge.close_session(sid)
    whole = bridge.open_figure(str(figures), stem="fig")
    assert bridge.get_session(whole["session_id"]).contract["contract_id"] == "c-1"

    bridge.apply_overrides(  # 合同还在：改约定要授权
        whole["session_id"], _fontsize(23.0) + _fontsize(11.0, RIGHT), user_authorized=True
    )
    again = bridge.get_session(whole["session_id"])
    again.contract = {
        "contract_id": "c-2",
        "allowed": [],
        "allowed_adjust": [],
    }  # 授权解除后重新套一份
    bridge.close_session(whole["session_id"])
    (figures / "fig.py").write_text(ONE_AXES, encoding="utf-8")
    bridge.shutdown_all()
    partial = bridge.open_figure(str(figures), stem="fig")
    assert partial["restored_draft"]["dropped"]
    assert bridge.get_session(partial["session_id"]).contract is None


def test_discard_draft_reaches_the_batch_path_through_the_server(tmp_path):
    figures = _project(tmp_path)
    _edit_and_close(figures)
    res = server.call_tool(
        "tavotto_open_figure",
        {"project_path": str(figures), "stems": ["fig"], "discard_draft": True},
    )
    entry = res["structuredContent"]["opened"][0]
    assert "restored_draft" not in entry
    assert _draft_files() == []


# --------------------- 进程直接退出（没经 close）的会话记录 --------------------
def _orphan(figures: Path, value: float = 23.0) -> str:
    out = bridge.open_figure(str(figures), stem="fig")
    bridge.apply_overrides(out["session_id"], _fontsize(value))
    bridge.shutdown_all()  # 进程退出：没 close，活会话记录还在 mcp-sessions/
    assert bridge.sessionjournal.load(bridge._journal_dir(), out["session_id"]) is not None
    assert _draft_files() == []
    return out["session_id"]


def test_a_session_lost_without_close_is_brought_back_by_open(tmp_path):
    figures = _project(tmp_path)
    old = _orphan(figures)
    again = bridge.open_figure(str(figures), stem="fig")
    assert again["restored_draft"]["from_session"] == old
    assert _prop(again["manifest"], TITLE, "fontsize") == 23.0
    # 旧记录删了：旧 id 不会再复活出第二份
    assert bridge.sessionjournal.load(bridge._journal_dir(), old) is None
    with pytest.raises(bridge.BridgeError) as gone:
        bridge.get_session(old)
    assert gone.value.code == "unknown_session"


def test_an_unedited_or_exported_orphan_record_is_not_brought_back(tmp_path):
    figures = _project(tmp_path)
    out = bridge.open_figure(str(figures), stem="fig")
    bridge.apply_overrides(out["session_id"], _fontsize(19.0))
    session = bridge.get_session(out["session_id"])
    session.exported_hash = session.patch_hash()
    bridge.commit_export(out["session_id"])
    bridge.shutdown_all()
    assert "restored_draft" not in bridge.open_figure(str(figures), stem="fig")


def test_discard_draft_also_drops_an_orphan_record(tmp_path):
    figures = _project(tmp_path)
    old = _orphan(figures)
    out = bridge.open_figure(str(figures), stem="fig", discard_draft=True)
    assert "restored_draft" not in out and out["discarded_draft"] is True
    assert bridge.sessionjournal.load(bridge._journal_dir(), old) is None


def test_a_session_alive_in_this_process_is_never_taken_as_an_orphan(tmp_path):
    figures = _project(tmp_path)
    live = bridge.open_figure(str(figures), stem="fig")
    bridge.apply_overrides(live["session_id"], _fontsize(12.0))
    other = bridge.open_figure(str(figures), stem="fig")
    assert "restored_draft" not in other
    assert bridge.sessionjournal.load(bridge._journal_dir(), live["session_id"]) is not None


def test_an_expired_orphan_record_is_not_brought_back(tmp_path):
    figures = _project(tmp_path)
    old = _orphan(figures)
    path = bridge._journal_dir() / f"{old}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["saved_at"] -= sessionjournal.TTL_SECONDS + 10
    path.write_text(json.dumps(record), encoding="utf-8")
    assert "restored_draft" not in bridge.open_figure(str(figures), stem="fig")


def test_discard_draft_never_deletes_the_record_of_a_session_alive_here(tmp_path):
    figures = _project(tmp_path)
    live = bridge.open_figure(str(figures), stem="fig")
    bridge.apply_overrides(live["session_id"], _fontsize(12.0))
    bridge.open_figure(str(figures), stem="fig", discard_draft=True)
    assert bridge.sessionjournal.load(bridge._journal_dir(), live["session_id"]) is not None
