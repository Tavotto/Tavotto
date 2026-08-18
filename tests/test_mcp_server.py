"""Codex MCP server 的看护。

盯的是这几件「坏了不报错、只是悄悄不生效」的事：

* **协议层**：initialize 的版本协商、tools/list 的形状、tools/call 的双份返回
  （人类可读 + 机器可读）。任何一条坏了，Codex 里表现为「插件没有工具」。
* **路径范围**：Codex 传来的路径可能是模型推断的，越界必须当场拒。
* **没有 UI 也能干活**：五个工具在不渲染 iframe 的 host 里要走完整条流程。
* **stdout 独占**：协议帧写错到 stderr 上，症状是「服务器不回消息」且没有任何报错。

需要 matplotlib 的全链路在 `tests/test_mcp_roundtrip.py`（自己 spawn worker）。
这里用假 worker，跑在 .venv 里。
"""
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "codex-plugin"
sys.path.insert(0, str(PLUGIN / "mcp"))

from magplot_mcp import bridge, rpc, server, widget  # noqa: E402


# ------------------------------ 假 worker -----------------------------------
class FakeWorker:
    """只回声不渲染：协议层的用例不该依赖 matplotlib。"""

    def __init__(self) -> None:
        self.rev = 0
        self.generation = 1
        self.calls: list[tuple] = []
        self.exported: list[tuple] = []

    def _manifest(self, patches):
        size = [80.0, 60.0]
        elements = [
            {"gid": "figure", "role": "figure", "label": "整图", "draggable": False,
             "bbox": [0, 0, 1, 1], "editable": []},
            {"gid": "axes_0", "role": "axes", "label": "子图", "draggable": False,
             "bbox": [0.1, 0.1, 0.8, 0.8],
             "editable": [{"prop": "spine_top", "value": True},
                          {"prop": "spine_right", "value": True},
                          {"prop": "spine_bottom", "value": True},
                          {"prop": "spine_left", "value": True},
                          {"prop": "spine_linewidth", "value": 0.75}]},
            {"gid": "axes_0.xticks", "role": "ticks", "label": "x 刻度",
             "draggable": False, "bbox": [0.1, 0.9, 0.8, 0.05],
             "editable": [{"prop": "direction", "value": "in"},
                          {"prop": "fontsize",
                           "value": next((p["value"] for p in patches
                                          if p["gid"] == "axes_0.xticks"
                                          and p["prop"] == "fontsize"), 9.0)}]},
        ]
        return {"stem": "Fig1", "size_mm": size, "elements": elements}

    def override(self, stem, patches, preview_dpi=None, inline_svg=False):
        self.calls.append(("override", stem, list(patches), preview_dpi, inline_svg))
        self.rev += 1
        out = {"manifest": self._manifest(patches), "warnings": [], "timings": {"total_ms": 1}}
        if inline_svg:
            out["svg"] = "<svg width='1pt' height='1pt'></svg>"
        return out

    def export(self, stem, patches, path, fmt="pdf", dpi=600):
        self.exported.append((stem, list(patches), path, fmt, dpi))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"%PDF-fake\n" if fmt == "pdf" else b"fake")
        return {"path": path, "warnings": []}

    def preview_png(self, stem, patches, width, tag):
        raise AssertionError("协议层用例不该走到位图预览")


@pytest.fixture(autouse=True)
def _clean_sessions():
    bridge.sessions().clear()
    yield
    bridge.sessions().clear()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """一个最小图库：脚本 + 产物 + 注册表，落在允许的范围内。"""
    figures = tmp_path / "figures"
    figures.mkdir()
    (figures / "fig1.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (figures / "Fig1.pdf").write_bytes(b"%PDF-1.4\n")
    (figures / "mm_registry.json").write_text(json.dumps(
        {"scripts": {"fig1.py": {"entry": "main", "cost": "light", "stems": ["Fig1"]}}}),
        encoding="utf-8")
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    return figures


@pytest.fixture
def fake_pool(monkeypatch):
    worker = FakeWorker()
    monkeypatch.setattr(bridge.engine_pool, "get", lambda *a, **k: worker)
    return worker


def _call(name: str, args: dict) -> dict:
    return server.call_tool(name, args)


def _body(result: dict) -> dict:
    return result["structuredContent"]


# ------------------------------- 协议层 -------------------------------------
def test_initialize_echoes_a_version_we_support():
    s = server.Server(rpc.StdioConnection(io.BytesIO(), io.BytesIO()))
    res = s.dispatch("initialize", {"protocolVersion": "2025-06-18"})
    assert res["protocolVersion"] == "2025-06-18"
    assert res["capabilities"]["tools"] == {"listChanged": False}
    assert res["serverInfo"]["name"] == "magplot"
    assert "magplot_open_figure" in res["instructions"]


def test_initialize_falls_back_to_our_latest_for_unknown_versions():
    s = server.Server(rpc.StdioConnection(io.BytesIO(), io.BytesIO()))
    res = s.dispatch("initialize", {"protocolVersion": "1999-01-01"})
    assert res["protocolVersion"] == server.SUPPORTED_PROTOCOL_VERSIONS[0]


def test_tools_list_shape():
    tools = server.Server(rpc.StdioConnection(io.BytesIO(), io.BytesIO())) \
        .dispatch("tools/list", {})["tools"]
    names = [t["name"] for t in tools]
    assert names == ["magplot_open_figure", "magplot_apply_overrides",
                     "magplot_preflight", "magplot_export",
                     "magplot_verify_replay", "magplot_close_session"]
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"


def test_only_canvas_tools_carry_the_ui_resource():
    """每个工具调用都拖一块 iframe 出来 = 画布不停重建。"""
    if not widget.available():
        pytest.skip("画布产物未构建")
    tools = {t["name"]: t for t in server._tools()}
    for name in server.UI_TOOLS:
        assert tools[name]["_meta"]["ui"]["resourceUri"] == widget.RESOURCE_URI
    for name in ("magplot_preflight", "magplot_export", "magplot_close_session"):
        assert "_meta" not in tools[name]


def test_unknown_method_is_a_clean_error():
    s = server.Server(rpc.StdioConnection(io.BytesIO(), io.BytesIO()))
    with pytest.raises(rpc.RpcError) as exc:
        s.dispatch("nope/nope", {})
    assert exc.value.code == rpc.METHOD_NOT_FOUND


def test_notifications_are_never_answered():
    """通知不能回响应——回了就是协议错误，host 侧会当成野消息。"""
    out = io.BytesIO()
    s = server.Server(rpc.StdioConnection(io.BytesIO(), out))
    s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert out.getvalue() == b""


def test_handler_exceptions_do_not_kill_the_connection(monkeypatch):
    out = io.BytesIO()
    s = server.Server(rpc.StdioConnection(io.BytesIO(), out))
    monkeypatch.setattr(server, "_tools", lambda: 1 / 0)
    s.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    msg = json.loads(out.getvalue())
    assert msg["id"] == 7 and msg["error"]["code"] == rpc.INTERNAL_ERROR


def test_protocol_owns_the_real_stdout(monkeypatch):
    """`hijack_stdout` 之后再取 `sys.stdout.buffer` 拿到的是 stderr。

    这条曾经真的写错过：协议帧全落到 stderr 上，host 那边表现为
    「initialize 永远等不到响应」，而且没有任何报错。
    """
    real = io.BytesIO()
    fake_stdout = io.TextIOWrapper(real, encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    monkeypatch.setattr(rpc, "_REAL_STDOUT", None)
    rpc.StdioConnection.hijack_stdout()
    assert sys.stdout is sys.stderr          # 后续 print 一律去 stderr
    conn = rpc.StdioConnection(io.BytesIO())
    conn.result(1, {"ok": True})
    assert b'"ok"' in real.getvalue()        # 协议帧仍走真正的 stdout


# ------------------------------ 路径范围 ------------------------------------
def test_paths_outside_the_allowed_roots_are_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope("/etc")
    assert exc.value.code == "path_out_of_scope"
    # 越界绝不「就近找一个能用的」
    assert "/etc" in exc.value.payload()["path"]


def test_allowed_roots_default_to_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv(bridge.ROOTS_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    assert bridge.allowed_roots() == [str(Path(tmp_path).resolve())]


def test_open_refuses_out_of_scope(project, fake_pool):
    res = _call("magplot_open_figure", {"project_path": "/etc"})
    assert res["isError"] and _body(res)["code"] == "path_out_of_scope"


# ------------------------------ 会话生命周期 ---------------------------------
def test_open_apply_preflight_export_close_without_any_ui(project, fake_pool, tmp_path):
    """**没有 UI 的 host 里这条链必须完整**——这就是 fallback 的验收。"""
    res = _call("magplot_open_figure", {"project_path": str(project)})
    assert not res.get("isError"), _body(res)
    opened = _body(res)
    sid = opened["session_id"]
    assert opened["stem"] == "Fig1"
    assert opened["manifest"]["size_mm"] == [80.0, 60.0]
    assert opened["patch_hash"].startswith("sha256:")
    assert opened["profile"]["profile_id"] == "lab-publication-v1"
    assert "counts" in opened["preflight"]
    assert res["content"][0]["text"]

    patches = [{"gid": "axes_0.xticks", "prop": "fontsize", "value": 7.0}]
    applied = _body(_call("magplot_apply_overrides",
                          {"session_id": sid, "patches": patches}))
    assert applied["applied"] == 1 and applied["rejected"] == []
    # worker 拿到的是**过滤后仍保持原始顺序**的那份，与 Flask 走的完全一样
    assert fake_pool.calls[-1][2] == patches

    checks = _body(_call("magplot_preflight", {"session_id": sid}))
    assert checks["blocking"] is True          # 7pt 撞绝对下限
    assert any(i["id"] == "font-below-absolute-floor" for i in checks["errors"])
    assert "阻断" in checks["report"]

    out_dir = tmp_path / "out"
    blocked = _call("magplot_export", {"session_id": sid, "formats": ["pdf"],
                                       "out_dir": str(out_dir)})
    assert blocked["isError"] and _body(blocked)["code"] == "preflight_blocked"
    assert not out_dir.exists(), "被阻断时一张图都不该出"

    done = _body(_call("magplot_export",
                       {"session_id": sid, "formats": ["pdf", "png"], "dpi": 300,
                        "out_dir": str(out_dir), "explicit_confirm": True}))
    assert [f["format"] for f in done["files"]] == ["pdf", "png"]
    assert done["forced"] is True
    assert [f["vector"] for f in done["files"]] == [True, False]
    assert done["files"][1]["dpi"] == 300

    closed = _body(_call("magplot_close_session", {"session_id": sid}))
    assert closed["closed"] is True
    # 重复关闭不算错（Codex 可能重试）
    assert _body(_call("magplot_close_session", {"session_id": sid}))["closed"] is False


def test_unknown_session_says_which_ones_exist(project, fake_pool):
    res = _call("magplot_apply_overrides", {"session_id": "s-nope", "patches": []})
    assert res["isError"] and _body(res)["code"] == "unknown_session"


def test_patches_are_a_full_list_and_dirty_entries_are_reported(project, fake_pool):
    sid = _body(_call("magplot_open_figure", {"project_path": str(project)}))["session_id"]
    patches = [
        {"gid": "axes_0.xticks", "prop": "fontsize", "value": 9.0},
        {"gid": "", "prop": "x", "value": 1},                    # 坏 gid
        {"gid": "a", "prop": "b", "value": float("inf")},        # 非有限浮点
    ]
    body = _body(_call("magplot_apply_overrides",
                       {"session_id": sid, "patches": patches}))
    # 脏条目**不静默丢**：连同原因一起交出来
    assert [d["reason"] for d in body["rejected"]] == ["bad_gid", "non_finite_float"]
    assert body["applied"] == 1
    assert fake_pool.calls[-1][2] == [patches[0]]


def test_patch_hash_is_the_canonical_one(project, fake_pool):
    from magplot.engine import patchspec
    sid = _body(_call("magplot_open_figure", {"project_path": str(project)}))["session_id"]
    a = [{"gid": "g2", "prop": "p", "value": 1}, {"gid": "g1", "prop": "p", "value": 2}]
    b = [{"gid": "g1", "prop": "p", "value": 2}, {"gid": "g2", "prop": "p", "value": 1}]
    ha = _body(_call("magplot_apply_overrides", {"session_id": sid, "patches": a}))["patch_hash"]
    hb = _body(_call("magplot_apply_overrides", {"session_id": sid, "patches": b}))["patch_hash"]
    # 顺序不同、内容相同 → 同一个身份（与 patchspec 的规范化一致）
    assert ha == hb == patchspec.patch_hash(a)


def test_export_defaults_to_the_project_export_dir(project, fake_pool, monkeypatch):
    """与画布导出同一条规则（engine/config.project_export_dir），不另写一份。"""
    from magplot.engine import config as engine_config
    sid = _body(_call("magplot_open_figure", {"project_path": str(project)}))["session_id"]
    _call("magplot_apply_overrides", {"session_id": sid, "patches": []})
    done = _body(_call("magplot_export", {"session_id": sid, "formats": ["pdf"],
                                          "explicit_confirm": True}))
    assert Path(done["export_dir"]) == engine_config.project_export_dir(str(project))
    assert Path(done["export_dir"]).name == "export"


def test_export_writes_a_proof_report(project, fake_pool, tmp_path):
    from magplot.engine.brand import PROOF_KIND
    sid = _body(_call("magplot_open_figure", {"project_path": str(project)}))["session_id"]
    _call("magplot_apply_overrides",
          {"session_id": sid,
           "patches": [{"gid": "axes_0.xticks", "prop": "fontsize", "value": 7.0}]})
    done = _body(_call("magplot_export",
                       {"session_id": sid, "formats": ["pdf"], "explicit_confirm": True,
                        "out_dir": str(tmp_path / "out")}))
    proof = json.loads(Path(done["proof_path"]).read_text(encoding="utf-8"))
    assert proof["kind"] == PROOF_KIND and proof["version"] == 2
    assert proof["source"] == "codex-mcp"
    assert proof["profile"]["profile_id"] == "lab-publication-v1"
    assert proof["profile"]["profile_version"]
    assert proof["forced"] is True and proof["acknowledged"]
    assert proof["patch_hash"] == done["patch_hash"]
    assert any(c["id"] == "font-below-absolute-floor" for c in proof["checks"])
    assert "not_verifiable" in proof


def test_journal_override_reaches_preflight(project, fake_pool):
    sid = _body(_call("magplot_open_figure", {"project_path": str(project)}))["session_id"]
    base = _body(_call("magplot_preflight", {"session_id": sid}))
    assert not any(i["id"] == "page-width" for i in base["errors"])
    tight = _body(_call("magplot_preflight",
                        {"session_id": sid,
                         "journal": {"widths_mm": {"single": 55.0, "double": 120.0}}}))
    assert any(i["id"] == "page-width" for i in tight["errors"])
    assert tight["profile"]["journal"]["widths_mm"]["double"] == 120.0


def test_bad_format_and_dpi_are_refused(project, fake_pool):
    sid = _body(_call("magplot_open_figure", {"project_path": str(project)}))["session_id"]
    assert _body(_call("magplot_export", {"session_id": sid, "formats": ["docx"]}))["code"] \
        == "bad_format"
    assert _body(_call("magplot_export", {"session_id": sid, "dpi": 0}))["code"] == "bad_dpi"


def test_a_directory_without_a_registry_is_a_clear_error(tmp_path, monkeypatch, fake_pool):
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    empty = tmp_path / "empty"
    empty.mkdir()
    res = _call("magplot_open_figure", {"project_path": str(empty)})
    assert res["isError"]
    assert _body(res)["code"] in ("no_registry", "no_figure")


def test_session_eviction_keeps_the_lid_on(project, fake_pool, monkeypatch):
    monkeypatch.setattr(bridge, "MAX_SESSIONS", 2)
    ids = [_body(_call("magplot_open_figure", {"project_path": str(project)}))["session_id"]
           for _ in range(4)]
    assert len(bridge.sessions()) == 2
    assert ids[-1] in bridge.sessions()


# ------------------------------ 画布产物 ------------------------------------
def test_widget_artifact_is_in_sync_with_the_frontend():
    """产物过期 = 用户装到的是旧画布。指纹算法纯 Python，CI 不需要 Node。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_mcp_widget
    have = build_mcp_widget.current_fingerprint()
    if have is None:
        pytest.skip("画布产物未构建（源码检出后跑一次 scripts/build_mcp_widget.py）")
    assert have == build_mcp_widget.source_fingerprint(), (
        "codex-plugin/mcp/widget/canvas.html 与 web/src 不同步，"
        "跑一次 python scripts/build_mcp_widget.py")


def test_widget_resource_is_self_contained():
    """CSP 声明的是空 connectDomains：画布不许发任何跨源请求。"""
    if not widget.available():
        pytest.skip("画布产物未构建")
    meta = widget.resource_meta()
    assert meta["ui"]["csp"] == {"connectDomains": [], "resourceDomains": []}
    assert meta["ui"]["resourceUri"] == widget.RESOURCE_URI
    assert meta["openai/outputTemplate"] == widget.RESOURCE_URI
    html = widget.html()
    # 单文件才塞得进 MCP 资源：外链的 <script src> / <link href> 在 iframe 里
    # 根本没有可寻址的来源（我们也刻意把 resourceDomains 留空）。
    # 只看**标签**，不看正文——压缩过的 JS 里满地都是 `src=`。
    import re as _re
    externals = [m.group(0) for m in
                 _re.finditer(r"<(?:script|link)\b[^>]*>", html, _re.I)
                 if _re.search(r'\b(?:src|href)\s*=\s*"(?!data:)', m.group(0), _re.I)]
    assert not externals, f"画布 HTML 里有外链: {externals[:3]}"
    assert "<script" in html and "<style" in html
    contents = widget.resource_contents()
    assert contents["mimeType"] == "text/html;profile=mcp-app"


def test_server_degrades_cleanly_without_the_widget(monkeypatch):
    """没有 UI 产物时**不声明资源、不挂 _meta**，五个工具照常可用。"""
    monkeypatch.setattr(widget, "available", lambda: False)
    s = server.Server(rpc.StdioConnection(io.BytesIO(), io.BytesIO()))
    assert s.dispatch("resources/list", {})["resources"] == []
    assert "resources" not in s.dispatch("initialize", {})["capabilities"]
    assert all("_meta" not in t for t in s.dispatch("tools/list", {})["tools"])
