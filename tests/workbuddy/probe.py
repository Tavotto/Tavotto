"""WorkBuddy P0 spike 的协议层证据采集（真 server、真引擎、真 matplotlib）。

    python tests/workbuddy/probe.py <scenario> [--python PY] [--out DIR]

scenario:
  handshake    initialize / tools/list / resources/list / resources/read / health（无任何宿主能力）
  no-authority 宿主不声明 roots 也不声明 elicitation，cwd = 连接器目录：open 必须 fail-closed
  deny         宿主声明 elicitation，用户拒绝：不得建会话
  roots        宿主声明 roots（roots/list 给 corpus 目录）：完整编辑闭环
  full         宿主声明 elicitation，用户批准：完整编辑闭环（open → apply → preflight →
               export PDF → verify_replay → session_state → refresh → normalize → close）
  large        大图（>300 元素）：open 的体积预算与 session_state 取件
  no-engine    不给 TAVOTTO_MCP_PYTHON：本机自管 venv 过旧时的降级 server
  no-widget    TAVOTTO_MCP_WIDGET 指向不存在的文件：工具照常、画布如实报缺

它是冒充宿主的——**不能**替代真 WorkBuddy 验收，只证明 server 一侧的行为。
每个场景把原始结构化结果写进 ``--out``，并在 stdout 打一行结论 JSON。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stdio_client import REPO, StdioHost  # noqa: E402

CORPUS = REPO / "tests" / "acceptance" / "corpus"
LARGE = Path(__file__).resolve().parent / "fixtures" / "large"
DEV_PYTHON = os.environ.get("WB_SPIKE_PYTHON") or str(REPO / ".venv" / "bin" / "python")


def sc(res: dict) -> dict:
    return (res.get("result") or {}).get("structuredContent") or {}


def text(res: dict) -> str:
    return "\n".join(c.get("text", "") for c in (res.get("result") or {}).get("content") or [])


def workspace(src: Path) -> Path:
    """每次跑都复制一份：导出会往项目目录写文件，不碰仓库里的 corpus。"""
    dst = Path(tempfile.mkdtemp(prefix="wb-spike-")) / src.name
    shutil.copytree(
        src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pdf", "*.png", "*.svg")
    )
    return dst.resolve()


def dump(out: Path, name: str, obj: object) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(
        json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def handshake(host: StdioHost, out: Path, caps: dict) -> dict:
    init = host.initialize(caps)
    tools = host.request("tools/list")
    res_list = host.request("resources/list")
    names = [t["name"] for t in tools["result"]["tools"]]
    ui_tools = {
        t["name"]: (t.get("_meta") or {}).get("ui", {}).get("resourceUri")
        for t in tools["result"]["tools"]
        if (t.get("_meta") or {}).get("ui")
    }
    resources = (res_list.get("result") or {}).get("resources") or []
    read = None
    if resources:
        r = host.request("resources/read", {"uri": resources[0]["uri"]})
        c = r["result"]["contents"][0]
        read = {
            "uri": c["uri"],
            "mimeType": c["mimeType"],
            "text_bytes": len(c["text"].encode("utf-8")),
        }
    health = sc(host.call("tavotto_health"))
    dump(out, "initialize", init)
    dump(out, "tools_list", tools)
    dump(out, "resources_list", res_list)
    dump(out, "health", health)
    return {
        "server": init["result"]["serverInfo"],
        "protocol": init["result"]["protocolVersion"],
        "server_caps": init["result"]["capabilities"],
        "tools": names,
        "ui_tools": ui_tools,
        "resources": [(r["uri"], r["mimeType"]) for r in resources],
        "resource_read": read,
        "health_ok": health.get("ok"),
        "root_source": (health.get("root_authority") or {}).get("source"),
        "roots": (health.get("root_authority") or {}).get("roots"),
        "authorization": (health.get("root_authority") or {}).get("authorization"),
    }


def edit_loop(host: StdioHost, out: Path, proj: Path, stem: str) -> dict:
    steps: dict = {}
    opened = host.call("tavotto_open_figure", {"project_path": str(proj), "stem": stem})
    body = sc(opened)
    dump(out, "open", body)
    steps["open"] = {
        "ok": body.get("ok"),
        "session_id": body.get("session_id"),
        "elements": len((body.get("manifest") or {}).get("elements") or []),
        "svg_bytes": len(body.get("svg") or ""),
        "canvas_ui": body.get("canvas_ui"),
        "meta_ui": ((opened["result"].get("_meta") or {}).get("ui") or {}).get("resourceUri"),
        "error": body.get("code"),
    }
    sid = body.get("session_id")
    if not sid:
        return steps
    legend = next(
        e for e in body["manifest"]["elements"] if e.get("role") == "legend" and e.get("draggable")
    )
    patch = {
        "gid": legend["gid"],
        "prop": legend.get("drag_prop", "loc_frac"),
        "value": [0.55, 0.2],
    }
    applied = sc(host.call("tavotto_apply_overrides", {"session_id": sid, "patches": [patch]}))
    dump(out, "apply", applied)
    moved = next(e for e in applied["manifest"]["elements"] if e["gid"] == legend["gid"])
    steps["apply"] = {
        "ok": applied.get("ok"),
        "patch": patch,
        "patch_hash_changed": applied.get("patch_hash") != body.get("patch_hash"),
        "render_revision": [body.get("render_revision"), applied.get("render_revision")],
        "legend_bbox": [legend["bbox"], moved["bbox"]],
        "svg_changed": applied.get("svg") != body.get("svg"),
        "rejected": applied.get("rejected"),
    }
    pf = sc(host.call("tavotto_preflight", {"session_id": sid}))
    dump(out, "preflight", pf)
    steps["preflight"] = {"counts": pf.get("counts"), "blocking": pf.get("blocking")}
    exp = sc(
        host.call(
            "tavotto_export",
            {
                "session_id": sid,
                "formats": ["pdf"],
                "explicit_confirm": True,
                "out_dir": str(proj / "exports"),
            },
        )
    )
    dump(out, "export", exp)
    files = exp.get("files") or []
    steps["export"] = {
        "ok": exp.get("ok"),
        "files": [
            {
                "path": f.get("path"),
                "bytes": Path(f["path"]).stat().st_size
                if f.get("path") and Path(f["path"]).exists()
                else None,
                "head": Path(f["path"]).read_bytes()[:5].decode("latin-1")
                if f.get("path") and Path(f["path"]).exists()
                else None,
                "verdict": (f.get("manifest") or {}).get("verdict"),
            }
            for f in files
        ],
        "error": exp.get("code"),
    }
    vr = sc(host.call("tavotto_verify_replay", {"session_id": sid}))
    dump(out, "verify_replay", vr)
    steps["verify_replay"] = {"ok": vr.get("ok"), "equal": vr.get("equal"), "code": vr.get("code")}
    st = sc(host.call("tavotto_session_state", {"session_id": sid}))
    steps["session_state"] = {
        "ok": st.get("ok"),
        "patches": st.get("patches"),
        "elements": len((st.get("manifest") or {}).get("elements") or []),
    }
    rf = sc(host.call("tavotto_refresh_project", {"session_id": sid}))
    dump(out, "refresh", rf)
    steps["refresh_project"] = {
        "ok": rf.get("ok"),
        "delivered": rf.get("delivered"),
        "code": rf.get("code"),
    }
    cl = sc(host.call("tavotto_close_session", {"session_id": sid}))
    steps["close"] = {"ok": cl.get("ok")}
    after = sc(host.call("tavotto_session_state", {"session_id": sid}))
    steps["state_after_close"] = {"ok": after.get("ok"), "code": after.get("code")}
    # 规范化走独立会话：它挂合同，不和上面的编辑闭环混在一起
    reopened = sc(host.call("tavotto_open_figure", {"project_path": str(proj), "stem": stem}))
    nm = sc(
        host.call(
            "tavotto_normalize_figure",
            {"session_id": reopened.get("session_id"), "width_mm": 85},
        )
    )
    dump(out, "normalize", nm)
    steps["normalize"] = {"ok": nm.get("ok"), "status": nm.get("status"), "code": nm.get("code")}
    host.call("tavotto_close_session", {"session_id": reopened.get("session_id")})
    return steps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--python", default=DEV_PYTHON)
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "wb-spike-evidence"))
    a = ap.parse_args()
    out = Path(a.out) / a.scenario
    # **不要**往这里加 PYTHONPATH：它同样对启动器自己的解释器生效，resolver 会把
    # 「当前解释器」（可能缺 pymupdf 这类必需依赖）选成引擎——2026-09-23 本 spike
    # 因此量到一批假的 artifact_rejected。引擎源码由 TAVOTTO_MCP_PYTHON 那个环境决定。
    env = {"TAVOTTO_MCP_PYTHON": a.python}
    summary: dict = {"scenario": a.scenario}

    if a.scenario == "handshake":
        host = StdioHost(env_extra=env)
        summary.update(handshake(host, out, {}))
    elif a.scenario == "no-authority":
        proj = workspace(CORPUS)
        host = StdioHost(env_extra=env)
        summary["handshake"] = handshake(host, out, {})
        r = sc(host.call("tavotto_open_figure", {"project_path": str(proj), "stem": "c01_line"}))
        dump(out, "open", r)
        summary["open"] = {k: r.get(k) for k in ("ok", "code", "disposition", "session_id")}
        summary["server_requests"] = [x["method"] for x in host.server_requests]
    elif a.scenario == "deny":
        proj = workspace(CORPUS)
        host = StdioHost(env_extra=env, policy={"elicitation": "decline"})
        host.initialize({"elicitation": {}})
        r = sc(host.call("tavotto_open_figure", {"project_path": str(proj), "stem": "c01_line"}))
        dump(out, "open", r)
        h = sc(host.call("tavotto_health"))
        summary["open"] = {k: r.get(k) for k in ("ok", "code", "disposition", "session_id")}
        summary["elicitation_request"] = host.server_requests[:1]
        summary["sessions_after"] = h.get("sessions")
        summary["root_source_after"] = (h.get("root_authority") or {}).get("source")
    elif a.scenario in ("full", "roots"):
        proj = workspace(CORPUS)
        if a.scenario == "full":
            host = StdioHost(env_extra=env, policy={"elicitation": "accept"})
            host.initialize({"elicitation": {}})
        else:
            host = StdioHost(env_extra=env, policy={"roots": [str(proj)]})
            host.initialize({"roots": {"listChanged": False}})
        summary["steps"] = edit_loop(host, out, proj, "c01_line")
        h = sc(host.call("tavotto_health"))
        summary["root_source"] = (h.get("root_authority") or {}).get("source")
        summary["server_requests"] = [x["method"] for x in host.server_requests]
    elif a.scenario == "large":
        proj = workspace(LARGE)
        host = StdioHost(env_extra=env, policy={"elicitation": "accept"})
        host.initialize({"elicitation": {}})
        opened = host.call("tavotto_open_figure", {"project_path": str(proj), "stem": "big_fig"})
        body = sc(opened)
        dump(out, "open", body)
        sid = body.get("session_id")
        st = host.call("tavotto_session_state", {"session_id": sid})
        stb = sc(st)
        summary["open"] = {
            "ok": body.get("ok"),
            "bytes": host.log[-2]["bytes"],
            "ms": host.log[-2]["ms"],
            "elided": body.get("elided"),
            "has_manifest": "manifest" in body,
            "meta_ui": ((opened["result"].get("_meta") or {}).get("ui") or {}).get("resourceUri"),
        }
        summary["session_state"] = {
            "ok": stb.get("ok"),
            "bytes": host.log[-1]["bytes"],
            "ms": host.log[-1]["ms"],
            "elements": len((stb.get("manifest") or {}).get("elements") or []),
            "svg_bytes": len(stb.get("svg") or ""),
        }
        gid = next(e for e in stb["manifest"]["elements"] if e.get("role") == "title")["gid"]
        ap_ = host.call(
            "tavotto_apply_overrides",
            {
                "session_id": sid,
                "patches": [{"gid": gid, "prop": "pos_frac", "value": [0.6, 0.05]}],
            },
        )
        summary["apply"] = {
            "ok": sc(ap_).get("ok"),
            "bytes": host.log[-1]["bytes"],
            "ms": host.log[-1]["ms"],
        }
        host.call("tavotto_close_session", {"session_id": sid})
    elif a.scenario == "no-engine":
        host = StdioHost(env_extra={}, drop_env=("TAVOTTO_WORKER_PYTHON",))
        init = host.initialize({"elicitation": {}})
        tools = host.request("tools/list")
        res = host.request("resources/list")
        health = sc(host.call("tavotto_health"))
        op = host.call("tavotto_open_figure", {"project_path": str(CORPUS), "stem": "c01_line"})
        dump(out, "health", health)
        summary.update(
            {
                "serverInfo": init["result"]["serverInfo"],
                "capabilities": init["result"]["capabilities"],
                "tools": [t["name"] for t in tools["result"]["tools"]],
                "resources": res.get("result", res.get("error")),
                "health": {k: health.get(k) for k in ("ok", "mode", "code")},
                "open": {
                    "isError": op["result"].get("isError"),
                    "code": sc(op).get("code"),
                    "text": text(op)[:240],
                },
            }
        )
    elif a.scenario == "no-widget":
        proj = workspace(CORPUS)
        host = StdioHost(
            env_extra={**env, "TAVOTTO_MCP_WIDGET": "/nonexistent/canvas.html"},
            policy={"elicitation": "accept"},
        )
        init = host.initialize({"elicitation": {}})
        tools = host.request("tools/list")
        res = host.request("resources/read", {"uri": "ui://tavotto/canvas/v1.html"})
        op = host.call("tavotto_open_figure", {"project_path": str(proj), "stem": "c01_line"})
        b = sc(op)
        summary.update(
            {
                "capabilities": init["result"]["capabilities"],
                "ui_tools": [
                    t["name"] for t in tools["result"]["tools"] if (t.get("_meta") or {}).get("ui")
                ],
                "resources_read": res.get("error"),
                "open": {
                    "ok": b.get("ok"),
                    "session_id": b.get("session_id"),
                    "canvas_ui": b.get("canvas_ui"),
                },
                "open_meta_ui": (op["result"].get("_meta") or {}).get("ui"),
            }
        )
    else:
        ap.error(f"未知场景 {a.scenario}")
    summary["calls"] = host.log
    host.close()
    dump(out, "summary", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
