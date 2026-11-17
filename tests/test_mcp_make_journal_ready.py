"""`tavotto_make_journal_ready`（ADR 0189 第 1 条）的**真链路**：真 matplotlib、真 worker、真产物。

只编排不新写逻辑，所以这里盯的是编排本身：

* 成功：一步走到导出，磁盘上真有文件、页面尺寸等于期刊栏宽，会话不关、之后还能 apply_overrides；
* 任一步没过就停在那步、**不导出**：栏位不唯一 / fix 失败停在 fix，预检要确认停在 preflight，都带 `next`；
* 绝不替用户确认（不传 explicit_confirm）；dry_run 不出文件、会话回到改前；
* 带回草稿时回包有 `restored_draft`；
* 开关关着：不在 tools/list，调用回 `journal_catalog_disabled`，且不留会话。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto import pdfbackend  # noqa: E402
from tavotto.engine import journals, pool as engine_pool  # noqa: E402
from tavotto_mcp import bridge, server, usage  # noqa: E402
from test_mcp_fix_to_journal import SCRIPT, _journal  # noqa: E402

TOOL = "tavotto_make_journal_ready"


def _worker_python():
    try:
        return engine_pool.find_worker_python()
    except engine_pool.WorkerError:
        return None


WORKER_PY = _worker_python()
pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="没有带科学栈的解释器，跳过真链路用例")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    bridge.reset_root_authority()
    bridge.sessions().clear()
    yield
    bridge.sessions().clear()
    bridge.reset_root_authority()


@pytest.fixture(scope="module", autouse=True)
def _shutdown_workers():
    yield
    bridge.shutdown_all()


@pytest.fixture
def figures(tmp_path) -> Path:
    d = tmp_path / "fig"
    d.mkdir()
    (d / "fig.py").write_text(SCRIPT, encoding="utf-8")
    (d / "tavotto_registry.json").write_text(
        json.dumps({"scripts": {"fig.py": {"entry": "main", "cost": "light", "stems": ["Kin"]}}}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [WORKER_PY, str(d / "fig.py")], capture_output=True, text=True, cwd=str(d)
    )
    assert proc.returncode == 0, proc.stderr
    return d


def _run(figures: Path, **extra) -> dict:
    args = {"project_path": str(figures), "stem": "Kin", "journal": _journal(), "previews": False}
    args.update(extra)
    return server.call_tool(TOOL, args)


def _body(res: dict) -> dict:
    return res["structuredContent"]


def _pdf_width_mm(path: str) -> float:
    return pdfbackend.probe_asset(Path(path), "pdf")["w_pt"] / 72 * 25.4


def _exported_files(out_dir: Path) -> list[Path]:
    return sorted(out_dir.iterdir()) if out_dir.exists() else []


# ------------------------------ 成功 ----------------------------------------
def test_one_call_opens_fixes_and_exports_a_file_at_the_column_width(figures, tmp_path):
    out_dir = tmp_path / "out"
    res = _run(figures, formats=["pdf"], out_dir=str(out_dir))
    body = _body(res)
    assert not res.get("isError") and body["ok"] is True, json.dumps(body, ensure_ascii=False)[
        :3000
    ]
    assert body["stage"] == "done" and body["fix_exit"] == "done"
    # 独立事实：文件真在磁盘上、页面宽度等于单栏 110 mm（图是 120 mm 开始的）
    (entry,) = body["exported"]
    assert entry["status"] == "done" and Path(entry["path"]).is_file()
    assert Path(entry["path"]).parent == out_dir
    assert _pdf_width_mm(entry["path"]) == pytest.approx(110.0, abs=0.2)
    assert body["size"]["from_mm"][0] == 120.0 and body["size"]["column"] == "single"
    assert body["changes_total"] > 0 and body["changes"] and body["fixed"] > 0
    assert "已导出：" in res["content"][0]["text"] and entry["path"] in res["content"][0]["text"]
    # 结果体积：不带逐条改动与整份 manifest
    assert len(json.dumps(res, ensure_ascii=False).encode()) < 24 * 1024
    assert "manifest" not in body and "patches" not in body


def test_session_stays_open_after_success_and_fine_grained_tools_still_work(figures, tmp_path):
    body = _body(_run(figures, formats=["pdf"], out_dir=str(tmp_path / "out")))
    sid = body["session_id"]
    session = bridge.get_session(sid)  # 没关
    assert session.exported_hash == session.patch_hash()
    patches = list(session.patches)
    gid = next(el["gid"] for el in session.manifest["elements"] if el.get("role") == "title")
    again = bridge.apply_overrides(sid, patches + [{"gid": gid, "prop": "fontsize", "value": 12.0}])
    assert again["patch_hash"] != session.exported_hash


def test_default_formats_and_out_dir_follow_tavotto_export(figures):
    body = _body(_run(figures))
    assert body["ok"] and body["stage"] == "done"
    files = body["exported"]
    assert files and all(Path(f["path"]).is_file() for f in files)
    # 缺省目录 = 项目的导出目录（与 tavotto_export 同一处）
    session = bridge.get_session(body["session_id"])
    from tavotto.engine import config as engine_config

    want = Path(engine_config.project_export_dir(session.project))
    assert all(Path(f["path"]).parent == want for f in files)


# ------------------------------ 停在 fix -------------------------------------
def test_ambiguous_column_stops_at_fix_asks_and_exports_nothing(figures, tmp_path):
    out_dir = tmp_path / "out"
    res = _run(figures, journal=_journal(double=125.0), formats=["pdf"], out_dir=str(out_dir))
    body = _body(res)
    assert not res.get("isError")
    assert body["ok"] is False and body["stage"] == "fix"
    assert body["fix_exit"] == "column_choice_required"
    assert {c["id"] for c in body["columns"]} == {"single", "double"}
    assert body["next"]["action"] == "ask_user" and body["next"]["options"]
    assert _exported_files(out_dir) == []
    # 会话留着、没改动，可以带 column 接着来
    assert bridge.get_session(body["session_id"]).patches == []
    ok = _body(
        _run(
            figures,
            journal=_journal(double=125.0),
            column="single",
            formats=["pdf"],
            out_dir=str(out_dir),
        )
    )
    assert ok["ok"] and ok["session_id"] == body["session_id"] and ok["stage"] == "done"
    assert ok["size"]["column"] == "single"


def test_unknown_column_is_an_error_that_names_the_stage_and_the_session(figures):
    res = _run(figures, column="triple")
    body = _body(res)
    assert res["isError"] is True and body["code"] == "unknown_column"
    assert body["stage"] == "fix" and body["session_id"] in bridge.sessions()
    assert body["next"]["action"] == "ask_user"


def test_open_failure_names_the_open_stage_and_has_next(tmp_path):
    res = server.call_tool(TOOL, {"project_path": str(tmp_path / "nope"), "journal": _journal()})
    body = _body(res)
    assert res["isError"] is True and body["stage"] == "open" and "session_id" not in body
    assert body["next"]


# ------------------------------ 停在 preflight（要确认）-------------------------
def test_blocked_preflight_stops_before_export_and_never_self_confirms(figures, tmp_path):
    # 剩下的 palette-line-markers 是脚本问题：升成 error，导出的预检门就要确认
    j = _journal(severity={"palette-line-markers": "error"})
    out_dir = tmp_path / "out"
    res = _run(figures, journal=j, formats=["pdf"], out_dir=str(out_dir))
    body = _body(res)
    assert not res.get("isError")
    assert body["ok"] is False and body["stage"] == "preflight"
    assert any(i["id"] == "palette-line-markers" for i in body["blocking"])
    assert _exported_files(out_dir) == []
    assert body["next"]["action"] == "ask_user"
    assert any("explicit_confirm=true" in o for o in body["next"]["options"])
    assert "没有导出" in res["content"][0]["text"]
    # 改动留在会话里（fix 那一步是过了的），没丢
    session = bridge.get_session(body["session_id"])
    assert session.patches and body["changes_total"] > 0
    assert session.exported_hash != session.patch_hash()


def test_an_explicit_confirm_argument_is_ignored_not_honoured(figures, tmp_path):
    j = _journal(severity={"palette-line-markers": "error"})
    out_dir = tmp_path / "out"
    res = _run(figures, journal=j, formats=["pdf"], out_dir=str(out_dir), explicit_confirm=True)
    body = _body(res)
    assert body["ok"] is False and body["stage"] == "preflight"
    assert _exported_files(out_dir) == []


# ------------------------------ dry_run / 草稿 --------------------------------
def test_dry_run_changes_nothing_and_writes_no_file(figures, tmp_path):
    out_dir = tmp_path / "out"
    res = _run(figures, formats=["pdf"], out_dir=str(out_dir), dry_run=True)
    body = _body(res)
    assert body["ok"] is True and body["stage"] == "fix" and body["dry_run"] is True
    assert body["changes_total"] > 0 and "exported" not in body
    assert _exported_files(out_dir) == []
    session = bridge.get_session(body["session_id"])
    assert session.patches == [] and session.manifest["size_mm"][0] == pytest.approx(
        120.0, abs=0.01
    )


def test_restored_draft_is_reported(figures, tmp_path):
    first = bridge.open_figure(str(figures), stem="Kin")
    session = bridge.get_session(first["session_id"])
    gid = next(el["gid"] for el in session.manifest["elements"] if el.get("role") == "title")
    bridge.apply_overrides(first["session_id"], [{"gid": gid, "prop": "fontsize", "value": 11.0}])
    bridge.close_session(first["session_id"])
    body = _body(_run(figures, formats=["pdf"], out_dir=str(tmp_path / "out")))
    assert body["ok"] and body["restored_draft"]["patches"] == 1
    assert body["restored_draft"]["from_session"] == first["session_id"]


# ------------------------------ 开关 / 遥测 / 参数 -----------------------------
def test_tool_follows_the_journal_switch_and_leaves_no_session_when_off(figures, monkeypatch):
    names = [t["name"] for t in server._tools()]
    assert TOOL in names
    tool = next(t for t in server._tools() if t["name"] == TOOL)
    assert tool["inputSchema"]["additionalProperties"] is False
    assert "explicit_confirm" not in tool["inputSchema"]["properties"]
    assert {"journal_id", "column", "formats", "out_dir", "dry_run", "stem"} <= set(
        tool["inputSchema"]["properties"]
    )
    assert TOOL in server.HANDLERS and TOOL not in usage.TOOLS
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", False)
    assert TOOL not in [t["name"] for t in server._tools()]
    res = _run(figures)
    assert res["isError"] is True and _body(res)["code"] == "journal_catalog_disabled"
    assert bridge.sessions() == {}


def test_a_journal_is_required_and_only_one_way_to_name_it(figures):
    with pytest.raises(server.RpcError):
        server.call_tool(TOOL, {"project_path": str(figures), "stem": "Kin"})
    with pytest.raises(server.RpcError):
        server.call_tool(
            TOOL,
            {"project_path": str(figures), "journal": _journal(), "journal_id": "nature"},
        )
    with pytest.raises(server.RpcError):
        server.call_tool(
            TOOL, {"project_path": str(figures), "journal": _journal(), "stems": ["Kin"]}
        )
    assert bridge.sessions() == {}


def test_previews_come_as_image_blocks_outside_structured_content(figures, tmp_path):
    res = server.call_tool(
        TOOL,
        {
            "project_path": str(figures),
            "stem": "Kin",
            "journal": _journal(),
            "formats": ["pdf"],
            "out_dir": str(tmp_path / "out"),
        },
    )
    images = [b for b in res["content"] if b.get("type") == "image"]
    assert len(images) == 2 and "iVBOR" not in json.dumps(_body(res))


def test_description_and_instructions_say_the_rules():
    tool = next(t for t in server._tools() if t["name"] == TOOL)
    for word in ("停在那一步", "不导出", "不关", "草稿"):
        assert word in tool["description"]
    import io

    from tavotto_mcp import rpc

    s = server.Server(rpc.StdioConnection(io.BytesIO(), io.BytesIO()))
    instructions = s.dispatch("initialize", {"protocolVersion": "2025-06-18"})["instructions"]
    assert "优先用 tavotto_make_journal_ready" in instructions
