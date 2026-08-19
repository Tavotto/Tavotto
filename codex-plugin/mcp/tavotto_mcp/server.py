"""MCP server 主体：initialize / tools/list / tools/call / resources/*。

**五个工具在没有 UI 的 host 里就足以走完整条流程**（open → apply → preflight →
export → close），这是硬要求：Codex CLI 与任何不渲染 iframe 的 surface 都得能用。
UI 只挂在真正需要画布的两个工具上（open / apply），见 `widget.tool_meta`。

工具返回**双份**：`content` 里一段人类可读的文字（Codex 念给用户听），
`structuredContent` 里机器可读的全量 JSON（Codex 拿来继续干活、widget 拿来
更新画布）。失败一律 `isError: true` + 机器可读 `code`，**不吞**。

manifest / SVG 这类大字段只进 `structuredContent`，不进 `content` 文本——
把一整份 SVG 塞进模型上下文既没用又贵。
"""
from __future__ import annotations

import json
import sys
import traceback

from . import bridge, widget
from .rpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    RpcError,
    StdioConnection,
)

SERVER_NAME = "tavotto"

#: 我们认得的 MCP 协议版本（新→旧）。客户端要的版本在这里面就原样回它，
#: 不在就回我们最新的那个——版本协商本来就是这么定的，猜一个客户端没提过的
#: 版本只会让握手在别的地方失败。
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


def _version() -> str:
    try:
        import tavotto
        return tavotto.__version__
    except Exception:                       # noqa: BLE001 — 版本读不到不该拖垮握手
        return "0"


# ------------------------------- 工具定义 -----------------------------------
def _tools() -> list[dict]:
    ui = widget.available()
    tools = [
        {
            "name": "tavotto_open_figure",
            "title": "打开一张 Tavotto 图",
            "description": (
                "打开一个 Tavotto 图库里的 matplotlib figure，起一个引擎会话并渲染一次。"
                "返回 session_id、manifest（可编辑元素清单）、预览 SVG、canonical patch "
                "hash 与出版规范。之后用 tavotto_apply_overrides 改图、tavotto_preflight "
                "体检、tavotto_export 出图。"
                "路径给产物（.pdf/.png）、脚本（.py）或图库目录都行。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string",
                                     "description": "图库目录（含 tavotto_registry.json）"},
                    "script_path": {"type": "string",
                                    "description": "脚本或产物的路径；与 project_path 二选一"},
                    "stem": {"type": "string",
                             "description": "产物文件名主干（一个项目多张图时必须点名）"},
                    "profile_id": {"type": "string",
                                   "description": "出版规范 id，缺省用规范文件里的 default"},
                    "journal": {"type": "object",
                                "description": "期刊自定义覆盖，如 "
                                               "{\"widths_mm\": {\"double\": 178}}"},
                    "include_png": {"type": "boolean",
                                    "description": "顺带回一张 base64 位图预览（默认否，体积大）"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "tavotto_apply_overrides",
            "title": "应用图内修改并重渲染",
            "description": (
                "把一组 override 应用到会话里的 figure 上并重渲染。"
                "**patches 是全量列表语义**：列表里没有的 (gid, prop) 会自动恢复成脚本"
                "原始值，所以每次都要发完整的一份，不要发增量。"
                "返回新的 manifest、SVG、patch hash、warnings 与被拒条目。"
                "不会改用户的 Python 源码。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "patches": {
                        "type": "array",
                        "description": "全量 override 列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "gid": {"type": "string",
                                        "description": "manifest 元素的 gid"},
                                "prop": {"type": "string",
                                         "description": "该元素 editable 里的 prop"},
                                "value": {"description": "新值（类型见 editable 的 type）"},
                            },
                            "required": ["gid", "prop", "value"],
                        },
                    },
                    "preview_dpi": {"type": "integer",
                                    "description": "预览 SVG 里内嵌位图的 dpi（含 imshow 的图才有意义）"},
                },
                "required": ["session_id", "patches"],
                "additionalProperties": False,
            },
        },
        {
            "name": "tavotto_preflight",
            "title": "出版规范预检",
            "description": (
                "按出版规范给当前会话的图做体检：尺寸/比例、最终有效字号、字体与中文 "
                "fallback、刻度朝向、封闭坐标轴、图例边框、线宽档位、色系、DPI 等。"
                "结果分四档：errors（默认阻止导出）、warnings（放行但要展示）、"
                "not_verifiable（查不了，需人工确认）、suggestions（建议）。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "profile_id": {"type": "string"},
                    "journal": {"type": "object", "description": "期刊自定义覆盖"},
                },
                "required": ["session_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "tavotto_export",
            "title": "导出成图（先预检）",
            "description": (
                "导出当前会话的图。**先跑一遍预检**：有 error 且没有 explicit_confirm 时"
                "一张图都不出。PDF/SVG 是真矢量，PNG 按给定 dpi 栅格化。"
                "同时写一份 proof report（规范身份 + 全部检查结果 + 是否强制导出）。"
                "不会用浏览器打开文件，也不会改用户的源码。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "formats": {"type": "array", "items": {"type": "string",
                                                           "enum": list(bridge.EXPORT_FORMATS)}},
                    "dpi": {"type": "integer", "description": "PNG 的分辨率，默认 600"},
                    "stem": {"type": "string", "description": "输出文件名主干"},
                    "out_dir": {"type": "string",
                                "description": "输出目录；缺省 <项目>/tavottofile/export/"},
                    "profile_id": {"type": "string"},
                    "journal": {"type": "object"},
                    "explicit_confirm": {
                        "type": "boolean",
                        "description": "用户明确要求「带着这些问题也要导出」时才置 true",
                    },
                    "proof": {"type": "boolean", "description": "写 proof report，默认 true"},
                },
                "required": ["session_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "tavotto_verify_replay",
            "title": "自检：热态 == 全新 worker 全量重放",
            "description": (
                "起一个一次性 worker 从零重放同一组 patches，把两份 manifest 逐元素比"
                "几何。用来证明「你现在看到的」== 「重开这张图会得到的」。"
                "导出前想彻底确认时调它（会重跑一遍脚本，heavy 的图是分钟级）。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "tavotto_close_session",
            "title": "关闭会话",
            "description": "释放引擎会话。用户的项目数据一个字节都不动。",
            "inputSchema": {
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
                "additionalProperties": False,
            },
        },
    ]
    if ui:
        by_name = {t["name"]: t for t in tools}
        by_name["tavotto_open_figure"]["_meta"] = widget.tool_meta(
            invoking="正在打开 Tavotto 画布…", invoked="Tavotto 画布已就绪")
        by_name["tavotto_apply_overrides"]["_meta"] = widget.tool_meta(
            invoking="正在重渲染…", invoked="已更新")
    return tools


# ------------------------------- 工具实现 -----------------------------------
def _text(*lines: str) -> list[dict]:
    return [{"type": "text", "text": "\n".join(l for l in lines if l)}]


def _brief_manifest(manifest: dict | None) -> str:
    if not isinstance(manifest, dict):
        return ""
    roles: dict[str, int] = {}
    for el in manifest.get("elements") or []:
        roles[el.get("role", "?")] = roles.get(el.get("role", "?"), 0) + 1
    top = "、".join(f"{k}×{v}" for k, v in sorted(roles.items(), key=lambda kv: -kv[1])[:8])
    size = manifest.get("size_mm") or [0, 0]
    return f"{size[0]}×{size[1]} mm，{sum(roles.values())} 个可编辑元素（{top}）"


def _call_open(args: dict) -> dict:
    target = args.get("project_path") or args.get("script_path")
    if not target:
        raise RpcError(INVALID_PARAMS, "要给 project_path 或 script_path 其中之一")
    out = bridge.open_figure(str(target), stem=args.get("stem"),
                             profile_id=args.get("profile_id"),
                             journal=args.get("journal"),
                             include_png=bool(args.get("include_png")))
    checks = bridge.run_preflight(out["session_id"])
    out["preflight"] = {k: checks[k] for k in
                        ("counts", "blocking", "needs_confirm", "errors",
                         "warnings", "not_verifiable", "suggestions")}
    lines = [
        f"已打开 {out['stem']}（会话 {out['session_id']}）",
        _brief_manifest(out.get("manifest")),
        f"规范 {out['profile']['profile_id']} v{out['profile']['profile_version']}；"
        f"预检 {checks['counts']}",
    ]
    if out["registry"].get("parameterizable") is False:
        lines.append("! 这张图不可参数化（没有对应脚本），只能当素材排版")
    if out.get("warnings"):
        lines.append("worker 警告: " + "; ".join(out["warnings"][:5]))
    return {"content": _text(*lines), "structuredContent": out}


def _call_apply(args: dict) -> dict:
    out = bridge.apply_overrides(str(args.get("session_id") or ""),
                                 args.get("patches"),
                                 preview_dpi=args.get("preview_dpi"))
    lines = [f"已应用 {out['applied']} 条 override（hash {out['patch_hash'][:19]}…）",
             _brief_manifest(out.get("manifest"))]
    if out["rejected"]:
        lines.append("被拒条目（形状不合法，未应用）: " +
                     "; ".join(f"#{d['index']} {d['reason']}" for d in out["rejected"][:5]))
    if out["warnings"]:
        lines.append("worker 警告: " + "; ".join(out["warnings"][:5]))
    return {"content": _text(*lines), "structuredContent": out}


def _call_preflight(args: dict) -> dict:
    out = bridge.run_preflight(str(args.get("session_id") or ""),
                               profile_id=args.get("profile_id"),
                               journal=args.get("journal"))
    return {"content": _text(out["report"]), "structuredContent": out}


def _call_export(args: dict) -> dict:
    # dpi **不能写成 `or 600`**：显式给的 0 是写错了，不是「没给」，
    # 悄悄替它换成 600 会让用户以为自己的参数生效了
    raw_dpi = args.get("dpi")
    out = bridge.export(str(args.get("session_id") or ""),
                        formats=args.get("formats") or [],
                        dpi=600 if raw_dpi is None else raw_dpi,
                        stem=args.get("stem"),
                        out_dir=args.get("out_dir"),
                        profile_id=args.get("profile_id"),
                        journal=args.get("journal"),
                        explicit_confirm=bool(args.get("explicit_confirm")),
                        proof=args.get("proof") is not False)
    lines = ["已导出：" + "、".join(f["path"] for f in out["files"])]
    if out.get("proof_path"):
        lines.append("留档：" + out["proof_path"])
    if out["forced"]:
        lines.append("注意：这次是带着阻断性问题强制导出的，已记进 proof report。")
    if out["warnings"]:
        lines.append("worker 警告: " + "; ".join(out["warnings"][:5]))
    return {"content": _text(*lines), "structuredContent": out}


def _call_verify(args: dict) -> dict:
    out = bridge.verify_replay(str(args.get("session_id") or ""))
    if out["ok"]:
        head = f"一致：热态与全新 worker 全量重放逐元素相同（比了 {out['compared_elements']} 个元素）"
    else:
        head = (f"发现 {len(out['divergence'])} 处分歧——"
                "热态所见与「重开后重放」不一样，别直接拿去投稿")
    return {"content": _text(head), "structuredContent": out}


def _call_close(args: dict) -> dict:
    out = bridge.close_session(str(args.get("session_id") or ""))
    return {"content": _text(out.get("note") or f"已关闭会话 {args.get('session_id')}"),
            "structuredContent": out}


HANDLERS = {
    "tavotto_open_figure": _call_open,
    "tavotto_apply_overrides": _call_apply,
    "tavotto_preflight": _call_preflight,
    "tavotto_export": _call_export,
    "tavotto_verify_replay": _call_verify,
    "tavotto_close_session": _call_close,
}

#: 结果里挂 UI 的工具（与 `_tools()` 的 `_meta` 一致）
UI_TOOLS = ("tavotto_open_figure", "tavotto_apply_overrides")


def call_tool(name: str, args: dict) -> dict:
    handler = HANDLERS.get(name)
    if handler is None:
        raise RpcError(METHOD_NOT_FOUND, f"没有这个工具: {name}")
    if not isinstance(args, dict):
        raise RpcError(INVALID_PARAMS, "arguments 必须是对象")
    try:
        result = handler(args)
    except bridge.BridgeError as exc:
        payload = exc.payload()
        # 失败也要机器可读：Codex 得能据此决定下一步（改路径？装 tavotto？确认导出？）
        return {"isError": True,
                "content": _text(f"[{payload['code']}] {payload['error']}"),
                "structuredContent": payload}
    if name in UI_TOOLS and widget.available():
        meta = dict(widget.resource_meta())
        # widgetData 是 host 递给 iframe 的初始负载（ChatGPT 侧的约定）；
        # MCP Apps 标准路径下 iframe 从 ui/notifications/tool-result 拿同一份。
        meta["widgetData"] = result["structuredContent"]
        result["_meta"] = meta
    return result


# ------------------------------- 协议主循环 ---------------------------------
class Server:
    def __init__(self, conn: StdioConnection | None = None) -> None:
        self.conn = conn if conn is not None else StdioConnection()
        self.initialized = False

    def handle(self, msg: dict) -> None:
        method = msg.get("method")
        rid = msg.get("id")
        if method is None:              # 响应（我们不发请求，所以只可能是噪音）
            return
        if rid is None:                 # 通知：不回，永远不回
            return
        try:
            self.conn.result(rid, self.dispatch(method, msg.get("params") or {}))
        except RpcError as exc:
            self.conn.error(rid, exc)
        except Exception as exc:        # noqa: BLE001 — 任何异常都不许打死连接
            print("tavotto-mcp: 未处理异常\n" + traceback.format_exc(), file=sys.stderr)
            self.conn.error(rid, RpcError(INTERNAL_ERROR, f"内部错误: {exc}"))

    def dispatch(self, method: str, params: dict) -> dict:
        if method == "initialize":
            return self.initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": _tools()}
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str):
                raise RpcError(INVALID_PARAMS, "tools/call 缺少 name")
            return call_tool(name, params.get("arguments") or {})
        if method == "resources/list":
            return {"resources": [widget.resource_descriptor()] if widget.available() else []}
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        if method == "resources/read":
            uri = params.get("uri")
            if uri != widget.RESOURCE_URI or not widget.available():
                raise RpcError(INVALID_PARAMS, f"没有这个资源: {uri}")
            return {"contents": [widget.resource_contents()]}
        if method == "prompts/list":
            return {"prompts": []}
        raise RpcError(METHOD_NOT_FOUND, f"不支持的方法: {method}")

    def initialize(self, params: dict) -> dict:
        want = params.get("protocolVersion")
        version = want if want in SUPPORTED_PROTOCOL_VERSIONS \
            else SUPPORTED_PROTOCOL_VERSIONS[0]
        self.initialized = True
        caps: dict = {"tools": {"listChanged": False}}
        if widget.available():
            caps["resources"] = {"listChanged": False, "subscribe": False}
        return {
            "protocolVersion": version,
            "capabilities": caps,
            "serverInfo": {"name": SERVER_NAME, "title": "Tavotto",
                           "version": _version()},
            "instructions": (
                "Tavotto 负责结构化图表编辑：改的是 override（gid + prop + value），"
                "**不会动用户的 Python 源码**。流程：tavotto_open_figure 打开 → "
                "tavotto_apply_overrides 改（patches 永远发全量列表）→ "
                "tavotto_preflight 体检 → tavotto_export 出图。"
                "数据本身、坐标范围、加删曲线/子图、colorbar 方向这些必须回代码改。"
            ),
        }

    def serve_forever(self) -> int:
        while True:
            try:
                msg = self.conn.read()
            except RpcError as exc:
                self.conn.error(None, exc)
                continue
            except (OSError, ValueError) as exc:
                self.conn.error(None, RpcError(PARSE_ERROR, str(exc)))
                continue
            if msg is None:             # stdin EOF = host 走了，收摊
                return 0
            self.handle(msg)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--self-check" in argv:
        return _self_check()
    server = Server()
    try:
        return server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        bridge.shutdown_all()


def _self_check() -> int:
    """`python -m tavotto_mcp --self-check`：不连 host 也能确认这层是活的。"""
    report = {
        "server": SERVER_NAME,
        "version": _version(),
        "protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
        "tools": [t["name"] for t in _tools()],
        "widget_ui": widget.available(),
        "widget_path": str(widget.widget_path()),
        "allowed_roots": bridge.allowed_roots(),
    }
    try:
        from tavotto.engine import pool
        report["worker_python"] = list(pool.select_worker_python())
    except Exception as exc:            # noqa: BLE001 — 探测失败也要如实说
        report["worker_python_error"] = str(exc)
    print(json.dumps(report, ensure_ascii=False, indent=1), file=sys.stderr)
    return 0
