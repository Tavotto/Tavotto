"""MCP 编辑会话跨进程恢复（ADR 0078）的看护。

盯四件事：

* **能恢复**：内存里没有（= server 进程换了）而落盘记录在，同一个 `session_id` 的
  apply / session_state 在本进程重建会话，已提交的 patches、规范化合同一样不少；
* **记录不能自证权限**：项目不在**当前**允许根内就拒（`workspace_root_changed`），
  而且拒在渲染之前——一个脚本都不执行；脚本 / 入口从注册表重新读，记录里没有它们；
* **明确释放的不复活**：关闭、淘汰的会话连记录一起删；
* **落盘失败不拖垮这次编辑**，但要说出来。

用假 worker（形状取自 `test_mcp_server.py`）；真 stdio + 真 matplotlib 的两进程接力在
`test_mcp_roundtrip.py::test_a_new_server_process_resumes_the_session`。
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto_mcp import bridge, server, sessionjournal  # noqa: E402

PATCH = {"gid": "axes_0.xticks", "prop": "fontsize", "value": 7.0}


class FakeWorker:
    def __init__(self) -> None:
        self.calls: list[list] = []
        self.rev = 0

    def override(self, stem, patches, preview_dpi=None, inline_svg=False):
        self.calls.append(list(patches))
        self.rev += 1
        return {
            "manifest": {
                "stem": stem,
                "size_mm": [80.0, 60.0],
                "elements": [
                    {"gid": "figure", "role": "figure", "bbox": [0, 0, 1, 1], "editable": []},
                    {
                        "gid": "axes_0.xticks",
                        "role": "ticks",
                        "bbox": [0.1, 0.9, 0.8, 0.05],
                        "editable": [{"prop": "fontsize", "value": 9.0}],
                    },
                ],
            },
            "svg": "<svg width='1pt' height='1pt'></svg>",
            "warnings": [],
            "timings": {},
        }


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    bridge.reset_root_authority()
    bridge.sessions().clear()
    yield
    bridge.sessions().clear()
    bridge.reset_root_authority()


@pytest.fixture
def project(tmp_path, monkeypatch):
    figures = tmp_path / "ws" / "figures"
    figures.mkdir(parents=True)
    (figures / "fig1.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (figures / "Fig1.pdf").write_bytes(b"%PDF-1.4\n")
    (figures / "tavotto_registry.json").write_text(
        json.dumps({"scripts": {"fig1.py": {"entry": "main", "cost": "light", "stems": ["Fig1"]}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path / "ws"))
    return figures


@pytest.fixture
def worker(monkeypatch):
    w = FakeWorker()
    monkeypatch.setattr(bridge.engine_pool, "get", lambda *a, **k: w)
    return w


def _journal() -> Path:
    return Path(os.environ["TAVOTTO_DATA_DIR"]) / sessionjournal.DIRNAME


def _body(res: dict) -> dict:
    return res["structuredContent"]


def _new_process() -> None:
    """server 进程换了：内存里的会话账本全没了，数据目录还在。"""
    bridge.sessions().clear()


# ------------------------------- 能恢复 -----------------------------------
def test_a_session_survives_a_process_switch(project, worker):
    opened = _body(server.call_tool("tavotto_open_figure", {"project_path": str(project)}))
    sid = opened["session_id"]
    server.call_tool("tavotto_apply_overrides", {"session_id": sid, "patches": [PATCH]})
    _new_process()
    renders = len(worker.calls)

    res = server.call_tool(
        "tavotto_apply_overrides",
        {"session_id": sid, "patches": [PATCH, {**PATCH, "prop": "fontsize", "value": 8.0}]},
    )
    body = _body(res)
    assert not res.get("isError"), body
    assert body["session_id"] == sid
    assert body["restored"] is True
    assert server.RESTORED_NOTE in res["content"][0]["text"]
    # 先按记录重建（已提交的那组），再应用这次的全量列表
    assert worker.calls[renders] == [PATCH]
    assert len(worker.calls) == renders + 2
    # 标记只报一次
    state = _body(server.call_tool("tavotto_session_state", {"session_id": sid}))
    assert state["restored"] is False


def test_the_canvas_fetch_path_restores_too(project, worker):
    """画布大图的取件（`tavotto_session_state`）同样能在新进程里拿到会话。"""
    sid = _body(server.call_tool("tavotto_open_figure", {"project_path": str(project)}))[
        "session_id"
    ]
    server.call_tool("tavotto_apply_overrides", {"session_id": sid, "patches": [PATCH]})
    _new_process()
    state = _body(server.call_tool("tavotto_session_state", {"session_id": sid}))
    assert state["restored"] is True
    assert state["patches"] == [PATCH]
    assert state["manifest"]["stem"] == "Fig1"


def test_a_normalize_contract_comes_back_with_the_session(project, worker):
    """合同也落盘：换了进程不能变成一个「谁都能随便改」的会话（ADR 0051 的挡板）。"""
    sid = _body(server.call_tool("tavotto_open_figure", {"project_path": str(project)}))[
        "session_id"
    ]
    session = bridge.get_session(sid)
    session.patches = [PATCH]
    session.contract = {"contract_id": "c-1", "allowed": [], "allowed_adjust": []}
    bridge._persist(session)
    _new_process()
    res = server.call_tool("tavotto_apply_overrides", {"session_id": sid, "patches": []})
    assert res.get("isError")
    assert _body(res)["code"] == bridge.engine_normalize.EXIT_REQUIRES_AUTHORIZATION


# ------------------------- 记录不能自证权限 --------------------------------
def test_a_record_outside_the_current_roots_is_refused_before_any_render(
    project, worker, tmp_path, monkeypatch
):
    sid = _body(server.call_tool("tavotto_open_figure", {"project_path": str(project)}))[
        "session_id"
    ]
    _new_process()
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv(bridge.ROOTS_ENV, str(other))
    bridge.reset_root_authority()
    renders = len(worker.calls)

    res = server.call_tool("tavotto_session_state", {"session_id": sid})
    assert res.get("isError")
    assert _body(res)["code"] == "workspace_root_changed"
    assert len(worker.calls) == renders, "越界的记录不许触发任何渲染（= 执行脚本）"
    assert sid not in bridge.sessions()


def test_the_record_never_names_the_script_to_run(project, worker):
    sid = _body(server.call_tool("tavotto_open_figure", {"project_path": str(project)}))[
        "session_id"
    ]
    record = json.loads((_journal() / f"{sid}.json").read_text(encoding="utf-8"))
    assert "script" not in record and "entry" not in record


def test_a_stem_gone_from_the_registry_is_not_restored(project, worker):
    sid = _body(server.call_tool("tavotto_open_figure", {"project_path": str(project)}))[
        "session_id"
    ]
    _new_process()
    (project / "tavotto_registry.json").write_text(
        json.dumps(
            {"scripts": {"fig1.py": {"entry": "main", "cost": "light", "stems": ["Other"]}}}
        ),
        encoding="utf-8",
    )
    res = server.call_tool("tavotto_session_state", {"session_id": sid})
    assert res.get("isError")
    assert _body(res)["code"] == "session_restore_failed"
    assert not (_journal() / f"{sid}.json").exists()


@pytest.mark.parametrize(
    "bad", ["../../etc/passwd", "s-ZZZZZZZZZZZZ", "s-0123", "", "s-0123456789ab/.."]
)
def test_malformed_ids_never_touch_the_filesystem(bad, tmp_path):
    assert sessionjournal.load(tmp_path, bad) is None
    assert sessionjournal.delete(tmp_path, bad) is False
    with pytest.raises(ValueError):
        sessionjournal.save(tmp_path, {"id": bad})


# ------------------------- 明确释放的不复活 --------------------------------
def test_a_closed_session_stays_closed_in_the_next_process(project, worker):
    sid = _body(server.call_tool("tavotto_open_figure", {"project_path": str(project)}))[
        "session_id"
    ]
    server.call_tool("tavotto_close_session", {"session_id": sid})
    _new_process()
    res = server.call_tool("tavotto_session_state", {"session_id": sid})
    assert res.get("isError")
    assert _body(res)["code"] == "unknown_session"


def test_an_evicted_session_stays_evicted(project, worker, monkeypatch):
    monkeypatch.setattr(bridge, "MAX_SESSIONS", 1)
    first = _body(server.call_tool("tavotto_open_figure", {"project_path": str(project)}))[
        "session_id"
    ]
    # 改过的图重开会新建会话（不沿用），于是挤掉第一个
    server.call_tool("tavotto_apply_overrides", {"session_id": first, "patches": [PATCH]})
    second = _body(server.call_tool("tavotto_open_figure", {"project_path": str(project)}))
    assert second["evicted_sessions"] == [first]
    res = server.call_tool("tavotto_session_state", {"session_id": first})
    assert res.get("isError")
    assert _body(res)["code"] == "unknown_session"


# ------------------------------- 落盘本身 ----------------------------------
def test_a_failed_save_does_not_fail_the_edit(project, worker, monkeypatch, capsys):
    sid = _body(server.call_tool("tavotto_open_figure", {"project_path": str(project)}))[
        "session_id"
    ]

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(sessionjournal, "save", boom)
    res = server.call_tool("tavotto_apply_overrides", {"session_id": sid, "patches": [PATCH]})
    assert not res.get("isError")
    assert "落盘失败" in capsys.readouterr().err


def test_records_are_private_files(project, worker):
    sid = _body(server.call_tool("tavotto_open_figure", {"project_path": str(project)}))[
        "session_id"
    ]
    path = _journal() / f"{sid}.json"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert _journal().stat().st_mode & 0o777 == 0o700


def test_stale_and_surplus_records_are_pruned(tmp_path, monkeypatch):
    base = tmp_path / "j"
    now = time.time()
    rec = {"project": "/x", "stem": "s", "profile": {}, "patches": []}
    sessionjournal.save(
        base, {**rec, "id": "s-000000000000"}, now=now - sessionjournal.TTL_SECONDS - 5
    )
    assert sessionjournal.load(base, "s-000000000000", now=now) is None
    monkeypatch.setattr(sessionjournal, "MAX_RECORDS", 3)
    for i in range(1, 6):
        sessionjournal.save(base, {**rec, "id": f"s-{i:012x}"}, now=now + i)
    left = sorted(p.stem for p in base.glob("s-*.json"))
    assert left == [f"s-{i:012x}" for i in (3, 4, 5)]


def test_a_record_of_the_wrong_shape_counts_as_absent(tmp_path):
    base = tmp_path / "j"
    rec = {"id": "s-0123456789ab", "project": "/x", "stem": "s", "profile": {}, "patches": []}
    sessionjournal.save(base, rec)
    path = base / "s-0123456789ab.json"
    for mutate in (
        lambda r: {**r, "v": 999},
        lambda r: {**r, "id": "s-ffffffffffff"},
        lambda r: {**r, "patches": "nope"},
        lambda r: {**r, "contract": ["x"]},
    ):
        good = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(mutate(good)), encoding="utf-8")
        assert sessionjournal.load(base, "s-0123456789ab") is None
        path.write_text(json.dumps(good), encoding="utf-8")
    path.write_text("{half", encoding="utf-8")
    assert sessionjournal.load(base, "s-0123456789ab") is None
