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
import os
import queue
import sys
import threading
import time
import traceback
from collections import deque

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

# 只有 client 在 initialize 里明确声明 roots capability 才会走这条兼容请求。
# 用 reader pump 做有界等待：host 声明了却不响应，也不能把整个 MCP server 锁死。
ROOTS_REQUEST_TIMEOUT_S = 2.0
# 工作区授权需要真人看清路径再点选，不能沿用 roots 探针的 2 秒预算。
ELICITATION_REQUEST_TIMEOUT_S = 300.0


def _version() -> str:
    try:
        import tavotto

        return tavotto.__version__
    except Exception:  # noqa: BLE001 — 版本读不到不该拖垮握手
        return "0"


# ------------------------------- 工具定义 -----------------------------------
def _tools() -> list[dict]:
    ui = widget.available()
    tools = [
        {
            "name": "tavotto_health",
            "title": "Tavotto 健康检查",
            "description": (
                "确认 Tavotto 图形能力就绪：引擎版本、内嵌画布资源在不在、"
                "允许的项目根。**准备出图/改图前先调它**——能力缺口在这里"
                "暴露，比画完图才发现便宜得多。可选 probe_worker=true 顺带"
                "探测渲染解释器（要花几秒）。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "probe_worker": {
                        "type": "boolean",
                        "description": "顺带探测渲染解释器（matplotlib 在哪个环境），较慢",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "tavotto_open_figure",
            "title": "打开一张 Tavotto 图",
            "description": (
                "打开一个 Tavotto 图库里的 matplotlib figure，起一个引擎会话并渲染一次。"
                "返回 session_id、manifest（可编辑元素清单）、预览 SVG、canonical patch "
                "hash 与出版规范。之后用 tavotto_apply_overrides 改图、tavotto_preflight "
                "体检、tavotto_export 出图。"
                "路径给产物（.pdf/.png）、脚本（.py）或图库目录都行。"
                "**默认不回预检明细**，只回一行计数——只是打开看看的时候，"
                "每次都糊一屏重复的规范建议是噪声；要逐条就 preflight=true "
                "或事后调 tavotto_preflight。"
                "一个脚本出好几张独立图时用 stems（或 discover_stems=true）"
                "一次全开，拿回 N 个各自可编辑的会话：某一张失败**不影响其余**，"
                "失败那张带自己的 code 与 stem 名；结局看 status"
                "（done / partial / failed 三档）。"
                "若宿主支持 MCP elicitation 且没有传工作区根，第一次请传绝对路径；"
                "Tavotto 会让宿主显示精确目录并请用户确认，本次连接内有效。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": "图库目录（含 tavotto_registry.json）",
                    },
                    "script_path": {
                        "type": "string",
                        "description": "脚本或产物的路径；与 project_path 二选一",
                    },
                    "stem": {
                        "type": "string",
                        "description": "产物文件名主干（一个项目多张图时必须点名）",
                    },
                    "stems": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "一次打开多张独立的图，每张一个可独立编辑的会话。"
                            "与 stem 互斥；批量只回每张的摘要与 session_id"
                            "（要 manifest/SVG 或内嵌画布就对那个 stem 单独调一次）"
                        ),
                    },
                    "discover_stems": {
                        "type": "boolean",
                        "description": (
                            "从注册表自动发现可参数化且产物在磁盘上的图，全部打开。"
                            "与 stem / stems 互斥；不猜没登记的产物，也不跑脚本"
                        ),
                    },
                    "profile_id": {
                        "type": "string",
                        "description": "出版规范 id，缺省用规范文件里的 default",
                    },
                    "journal": {
                        "type": "object",
                        "description": '期刊自定义覆盖，如 {"widths_mm": {"double": 178}}',
                    },
                    "include_png": {
                        "type": "boolean",
                        "description": "顺带回一张 base64 位图预览（默认否，体积大）",
                    },
                    "preflight": {
                        "type": "boolean",
                        "description": (
                            "回完整的出版规范预检明细（默认否：只回计数。"
                            "想看逐条建议就调 tavotto_preflight）"
                        ),
                    },
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
                                "gid": {"type": "string", "description": "manifest 元素的 gid"},
                                "prop": {
                                    "type": "string",
                                    "description": "该元素 editable 里的 prop",
                                },
                                "value": {"description": "新值（类型见 editable 的 type）"},
                            },
                            "required": ["gid", "prop", "value"],
                        },
                    },
                    "preview_dpi": {
                        "type": "integer",
                        "description": "预览 SVG 里内嵌位图的 dpi（含 imshow 的图才有意义）",
                    },
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
                    "formats": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(bridge.EXPORT_FORMATS)},
                    },
                    "dpi": {"type": "integer", "description": "PNG 的分辨率，默认 600"},
                    "stem": {"type": "string", "description": "输出文件名主干"},
                    "out_dir": {
                        "type": "string",
                        "description": "输出目录；缺省 <项目>/tavottofile/export/",
                    },
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
            "name": "tavotto_refresh_project",
            "title": "刷新项目（改过绘图脚本之后）",
            "description": (
                "修改、新建、重命名或删除绘图脚本之后调用一次，让 Tavotto 重新读这个项目："
                "静态合并脚本注册表、比对素材、更新每张图的接入状态，并把结果推给正在运行的"
                " Tavotto 界面——用户不需要手动刷新、也不需要重启。返回哪些脚本 / 图变了、"
                "哪些图现在可编辑（readiness.panels[].status）。"
                "**这不是运行脚本的工具**：不 probe、不执行任何用户代码。"
                "status 为 needs_probe 的图要用户在 Tavotto 里点「试运行并连接」，不要猜；"
                "conflict 的不要自动裁决，把候选脚本告诉用户。"
                "项目来自当前授权：优先传 session_id（tavotto_open_figure 回来的），"
                "或传一个已授权工作区内的 project_path；两者都不传时用唯一有会话的项目。"
                "delivered=app 表示运行中的 Tavotto 已同步；local 表示 Tavotto 没开着，"
                "刷新已在本地完成，下次打开项目时自动生效。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "tavotto_open_figure 返回的会话 id（首选）",
                    },
                    "project_path": {
                        "type": "string",
                        "description": "图库目录 / 脚本 / 产物的绝对路径，必须在已授权工作区内",
                    },
                    "reason": {
                        "type": "string",
                        "enum": ["codex"],
                        "description": "固定为 codex；其它值不接受",
                    },
                },
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
            invoking="正在打开 Tavotto 画布…", invoked="Tavotto 画布已就绪"
        )
        by_name["tavotto_apply_overrides"]["_meta"] = widget.tool_meta(
            invoking="正在重渲染…", invoked="已更新"
        )
    return tools


# ------------------------------- 工具实现 -----------------------------------
def _text(*lines: str) -> list[dict]:
    return [{"type": "text", "text": "\n".join(ln for ln in lines if ln)}]


def _brief_manifest(manifest: dict | None) -> str:
    if not isinstance(manifest, dict):
        return ""
    roles: dict[str, int] = {}
    for el in manifest.get("elements") or []:
        roles[el.get("role", "?")] = roles.get(el.get("role", "?"), 0) + 1
    top = "、".join(f"{k}×{v}" for k, v in sorted(roles.items(), key=lambda kv: -kv[1])[:8])
    size = manifest.get("size_mm") or [0, 0]
    return f"{size[0]}×{size[1]} mm，{sum(roles.values())} 个可编辑元素（{top}）"


def _batch_request(args: dict) -> dict | None:
    """这次 open 是不是批量的？是的话返回清单来源，不是返回 None。

    参数**互斥而不是「优先级」**：同时给了 stem 与 stems，谁也说不清用户想要
    哪一种，静默挑一个的代价是「我明明写了 stems，它只开了一张」。
    """
    raw = args.get("stems")
    discover = bool(args.get("discover_stems"))
    if raw is None and not discover:
        return None
    if raw is not None and discover:
        raise RpcError(
            INVALID_PARAMS, "stems 与 discover_stems 二选一：要么点名，要么让 Tavotto 发现"
        )
    if args.get("stem"):
        raise RpcError(INVALID_PARAMS, "stem 是单图那一路；批量用 stems 或 discover_stems")
    if args.get("include_png"):
        # 悄悄忽略一个用户显式设过的参数，等于让他以为自己的参数生效了。
        raise RpcError(INVALID_PARAMS, "批量不回位图预览（include_png 只在单图那一路上有意义）")
    if discover:
        return {"stems": None, "discover": True}
    if not isinstance(raw, list) or not raw:
        raise RpcError(INVALID_PARAMS, "stems 要是一个非空的字符串数组")
    cleaned = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise RpcError(INVALID_PARAMS, "stems 里的每一项都要是非空字符串")
        cleaned.append(item.strip())
    return {"stems": cleaned, "discover": False}


def _sum_counts(into: dict, counts: dict) -> None:
    for key, value in (counts or {}).items():
        into[key] = into.get(key, 0) + int(value or 0)


def _call_open_batch(target: str, args: dict, plan: dict) -> dict:
    """一次调用打开 N 张图。**预检按 #102 第 4 条走：默认只回汇总与阻断项。**

    单图那一路每开一张已经只回一行计数了；批量再逐张展开就是把同一个噪声乘以
    N。所以这里默认只给一份合计 + 「哪几张有阻断项」，逐条留给
    `tavotto_preflight`（要全展开仍然是 `preflight=true`）。
    """
    out = bridge.open_figures(
        target,
        stems=plan["stems"],
        discover=plan["discover"],
        profile_id=args.get("profile_id"),
        journal=args.get("journal"),
    )
    detailed = bool(args.get("preflight"))
    total: dict = {}
    blocking_stems: list[str] = []
    unknown_stems: list[str] = []
    reports: list[str] = []
    for entry in out["opened"]:
        checks = _safe_preflight(entry["session_id"])
        try:
            entry["preflight"] = {
                "counts": checks["counts"],
                "blocking": checks["blocking"],
                "needs_confirm": checks["needs_confirm"],
            }
            _sum_counts(total, checks["counts"])
        except Exception as exc:  # noqa: BLE001 — 见 `_safe_preflight` 的 docstring
            # 预检没跑出结论 ≠ 通过：并进合计里那几个 0 会让一张没体检过的图看
            # 起来是干净的。而**这一整批已经开好的 session_id 更不能跟着丢**。
            entry["preflight"] = (
                checks
                if "code" in checks
                else {"code": "preflight_crashed", "error": f"{type(exc).__name__}: {exc}"}
            )
            unknown_stems.append(entry["stem"])
            continue
        if checks["blocking"]:
            blocking_stems.append(entry["stem"])
        if detailed:
            reports.append(checks["report"])
    out["preflight"] = {
        "counts": total,
        "blocking_stems": blocking_stems,
        "unknown_stems": unknown_stems,
        "detailed_text": detailed,
    }

    counts = out["counts"]
    lines = [
        f"批量打开 {counts['requested']} 张（来源：{'注册表发现' if out['source'] == 'discover' else '点名'}）"
        f"：{counts['opened']} 张已打开、{counts['failed']} 张失败、"
        f"{counts['skipped']} 张未尝试 —— status={out['status']}",
    ]
    for entry in out["opened"]:
        size = entry.get("size_mm") or [0, 0]
        lines.append(
            f"  ✓ {entry['stem']}  会话 {entry['session_id']}  "
            f"{size[0]}×{size[1]} mm，{entry['elements']} 个可编辑元素"
        )
    # **code 只进 `structuredContent`**：这几行是念给用户听的那一份。念一个
    # `session_budget_exhausted` 出来，用户既不知道发生了什么也不知道下一步。
    for entry in out["failed"]:
        lines.append(f"  ✗ {entry['stem']}  {entry['error'].splitlines()[0]}")
    for entry in out["skipped"]:
        lines.append(f"  · {entry['stem']}  未尝试：{entry['error'].splitlines()[0]}")
    if out["failed"] or out["skipped"]:
        # 「其余照常打开」要说出口：模型看到一条失败就整批重来的话，已经开好的
        # 那几个会话会被晾在账本里没人关。
        lines.append("失败/未尝试的那几张不影响已打开的会话，各自重试即可。")
    summary = "、".join(f"{k}×{v}" for k, v in sorted(total.items())) or "无"
    lines.append(
        f"预检合计 {summary}"
        + ("" if detailed else "（逐条建议未展开；要就 preflight=true 或调 tavotto_preflight）")
    )
    if blocking_stems:
        lines.append("! 有阻断项，会挡住导出: " + "、".join(blocking_stems))
    if unknown_stems:
        lines.append("? 预检没跑出结论（不等于通过）: " + "、".join(unknown_stems))
    lines.extend(reports)
    return {"content": _text(*lines), "structuredContent": out}


def _safe_preflight(session_id: str) -> dict:
    """跑一次预检；**跑不出结论时也不把异常放出去**。

    open 这条路上会话已经登记了。异常一旦逃出去，`tools/call` 回的是一条错误
    结果，里头没有 session_id——用户手上就是一个开着、却谁也关不掉的会话
    （批量那一路更严重：**同一次调用里已经开好的其余几个也跟着一起丢**）。

    两档分开，因为它们不是一回事：`BridgeError` 是预期内的失败，有稳定 code；
    其余异常是**没人诊断过的**，硬套一个现成 code 会把它伪装成已知形态。
    """
    try:
        return bridge.run_preflight(session_id)
    except bridge.BridgeError as exc:
        return {"code": exc.code or "preflight_failed", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — 见 docstring
        return {"code": "preflight_crashed", "error": f"{type(exc).__name__}: {exc}"}


def _call_open(args: dict) -> dict:
    target = args.get("project_path") or args.get("script_path")
    if not target:
        raise RpcError(INVALID_PARAMS, "要给 project_path 或 script_path 其中之一")
    plan = _batch_request(args)
    if plan is not None:
        return _call_open_batch(str(target), args, plan)
    out = bridge.open_figure(
        str(target),
        stem=args.get("stem"),
        profile_id=args.get("profile_id"),
        journal=args.get("journal"),
        include_png=bool(args.get("include_png")),
    )
    # **打开与预检分离**（issue #102）：噪声在**给 agent 读的那段文字**里——
    # 每开一张图糊一屏重复的规范建议，还挤掉了 manifest 摘要那几行真正有用的东西。
    #
    # 所以裁的是文字，**结构化结果一个字段都不动**：内嵌画布从 `open.preflight`
    # 初始化自己的状态并直接展开那四个数组（`web/src/mcp/McpApp.tsx`），程序化调用
    # 方也可能在读它。裁掉结构化字段等于把画布打死——第一版就是那么写的，
    # Codex 在 PR #171 上指出。
    # **会话已经登记了，预检再怎么炸也不能把它带走。**「预期内的失败」
    # （BridgeError，有稳定 code）与「意料之外的异常」（畸形 manifest 几何抛的
    # IndexError/ValueError 那一类）是两档，都不等于「体检通过」——但两档都不该
    # 让调用方连 session_id 都拿不到：那时用户手上是一个开着、却关不掉的会话。
    checks = _safe_preflight(out["session_id"])
    detailed = bool(args.get("preflight"))
    if checks is None or "counts" not in checks:
        out["preflight"] = {"detailed_text": detailed, **(checks or {})}
    else:
        out["preflight"] = {
            k: checks[k]
            for k in (
                "counts",
                "blocking",
                "needs_confirm",
                "errors",
                "warnings",
                "not_verifiable",
                "suggestions",
            )
        }
        out["preflight"]["detailed_text"] = detailed
    if "counts" not in out["preflight"]:
        return {
            "content": _text(
                f"已打开 {out['stem']}（会话 {out['session_id']}）",
                _brief_manifest(out.get("manifest")),
                f"? 出版规范预检没跑出结论（{out['preflight'].get('error') or '见 code'}）"
                "——这**不等于通过**；会话是好的，可以改图，体检重跑 tavotto_preflight。",
            ),
            "structuredContent": out,
        }
    lines = [
        (
            f"这张已经开着，沿用同一个会话 {out['session_id']}（没有新建会话，也就没有挤掉别的图）"
            if out.get("reused")
            else f"已打开 {out['stem']}（会话 {out['session_id']}）"
        ),
        _brief_manifest(out.get("manifest")),
        f"规范 {out['profile']['profile_id']} v{out['profile']['profile_version']}；"
        f"预检 {checks['counts']}"
        + ("" if detailed else "（逐条建议未展开；要就 preflight=true 或调 tavotto_preflight）"),
    ]
    # **`blocking` 是布尔不是列表**（`engine/preflight.summarize` 里
    # `len(buckets["error"]) > 0`）——切它会当场 TypeError，而且**恰恰是在有阻断项
    # 的那些图上**炸（Codex 在 PR #171 上指出）。这里只说「有几条、会挡住导出」，
    # 逐条留给 report。
    if checks["blocking"] and not detailed:
        lines.append(f"! 有 {len(checks['errors'])} 条阻断项，会挡住导出——逐条见 tavotto_preflight")
    if detailed:
        # **文案不在这里拼。** 预检条目的 `message` 是 `{key, params}`（PR #113 的
        # 文案协议：Python 发 id+params，宿主按 locale 渲染），字符串拼接会直接
        # `str + dict` 炸掉。渲染好的那一份是 `report`，与 tavotto_preflight 用的
        # 是同一个——不在这里造第二份。
        lines.append(checks["report"])
    if out.get("evicted_sessions"):
        # 静默淘汰的表现是「我的 session_id 突然不认识了，而我什么都没做」。
        lines.append(
            f"! 会话数已达上限，挤掉了最久没用的 {len(out['evicted_sessions'])} 个"
            f"（{'、'.join(out['evicted_sessions'])}）——它们已经关掉了，要用得重新打开。"
        )
    if out["registry"].get("parameterizable") is False:
        lines.append("! 这张图不可参数化（没有对应脚本），只能当素材排版")
    if out.get("warnings"):
        lines.append("worker 警告: " + "; ".join(out["warnings"][:5]))
    return {"content": _text(*lines), "structuredContent": out}


def _call_apply(args: dict) -> dict:
    out = bridge.apply_overrides(
        str(args.get("session_id") or ""), args.get("patches"), preview_dpi=args.get("preview_dpi")
    )
    lines = [
        f"已应用 {out['applied']} 条 override（hash {out['patch_hash'][:19]}…）",
        _brief_manifest(out.get("manifest")),
    ]
    if out["rejected"]:
        lines.append(
            "被拒条目（形状不合法，未应用）: "
            + "; ".join(f"#{d['index']} {d['reason']}" for d in out["rejected"][:5])
        )
    if out["warnings"]:
        lines.append("worker 警告: " + "; ".join(out["warnings"][:5]))
    return {"content": _text(*lines), "structuredContent": out}


def _call_preflight(args: dict) -> dict:
    out = bridge.run_preflight(
        str(args.get("session_id") or ""),
        profile_id=args.get("profile_id"),
        journal=args.get("journal"),
    )
    return {"content": _text(out["report"]), "structuredContent": out}


def _call_export(args: dict) -> dict:
    # dpi **不能写成 `or 600`**：显式给的 0 是写错了，不是「没给」，
    # 悄悄替它换成 600 会让用户以为自己的参数生效了
    raw_dpi = args.get("dpi")
    out = bridge.export(
        str(args.get("session_id") or ""),
        formats=args.get("formats") or [],
        dpi=600 if raw_dpi is None else raw_dpi,
        stem=args.get("stem"),
        out_dir=args.get("out_dir"),
        profile_id=args.get("profile_id"),
        journal=args.get("journal"),
        explicit_confirm=bool(args.get("explicit_confirm")),
        proof=args.get("proof") is not False,
    )
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
        head = (
            f"一致：热态与全新 worker 全量重放逐元素相同（比了 {out['compared_elements']} 个元素）"
        )
    else:
        head = (
            f"发现 {len(out['divergence'])} 处分歧——热态所见与「重开后重放」不一样，别直接拿去投稿"
        )
    return {"content": _text(head), "structuredContent": out}


def _call_refresh(args: dict) -> dict:
    """改过脚本之后的显式刷新（ADR 0041 §1）。实现全在 `bridge.refresh_project`。"""
    out = bridge.refresh_project(
        session_id=args.get("session_id") or None,
        project_path=args.get("project_path") or None,
    )
    reg, assets, ready = out["registry"], out["assets"], out.get("readiness")
    lines = [
        "已刷新项目（经运行中的 Tavotto，界面已同步）"
        if out["delivered"] == bridge.DELIVERED_APP
        else "已刷新项目（Tavotto 未在运行，已在本地完成；下次打开项目时自动生效）",
    ]
    for label, key in (
        ("新增脚本", "added_scripts"),
        ("移除脚本", "removed_scripts"),
        ("配置变化", "changed_scripts"),
    ):
        if reg.get(key):
            lines.append(f"{label}: {', '.join(reg[key])}")
    if not any(reg.get(k) for k in ("added_scripts", "removed_scripts", "changed_scripts")):
        lines.append("脚本注册表无变化")
    if assets.get("baseline"):
        lines.append("素材基线已建立（首轮，没有可比较的上一轮）")
    else:
        parts = [
            f"{label} {len(assets[key])}"
            for label, key in (("新增", "added"), ("删除", "removed"), ("更新", "changed"))
            if assets.get(key)
        ]
        lines.append("素材: " + ("、".join(parts) if parts else "无变化"))
    if ready:
        summary = ready.get("summary") or {}
        lines.append(f"可编辑 {summary.get('editable', 0)}/{summary.get('total', 0)} 张图")
        for p in ready.get("panels") or []:
            if p.get("status") == "editable":
                continue
            lines.append(f"- {p['id']}: {p['status']}（{p.get('reason_code')}）")
        if summary.get("needs_probe"):
            lines.append("needs_probe 的图请让用户在 Tavotto 里点「试运行并连接」，不要猜。")
        if summary.get("conflict"):
            lines.append("conflict 的图不要自动裁决，把候选脚本告诉用户。")
    lines.append("不需要用户手动刷新或重启 Tavotto。")
    return {"content": _text(*lines), "structuredContent": out}


def _call_close(args: dict) -> dict:
    out = bridge.close_session(str(args.get("session_id") or ""))
    return {
        "content": _text(out.get("note") or f"已关闭会话 {args.get('session_id')}"),
        "structuredContent": out,
    }


def _call_health(args: dict) -> dict:
    """能力自检：引擎 / 画布 / 项目根，一次说清。**先体检再出图**（便宜）。"""
    import time as _time

    t0 = _time.monotonic()
    root_info = bridge.root_diagnostics()
    out: dict = {
        "ok": True,
        "mode": "engine",
        "engine": {"available": True, "version": _version()},
        "canvas": {
            "available": widget.available(),
            "resource_uri": widget.RESOURCE_URI,
            "path": str(widget.widget_path()),
        },
        "roots": root_info["roots"],
        "root_authority": root_info,
        "sessions": sorted(bridge.sessions()),
    }
    if not widget.available():
        out["canvas"]["reason"] = widget.missing_reason()
    if args.get("probe_worker"):
        try:
            from tavotto.engine import pool as _pool

            python, source = _pool.select_worker_python()
            out["worker"] = {"python": python, "source": source}
        except Exception as exc:  # noqa: BLE001 — 探测失败也要如实说
            out["worker"] = {"error": str(exc)}
    out["timings"] = {"health_ms": int((_time.monotonic() - t0) * 1000)}
    lines = [
        f"引擎就绪（tavotto {out['engine']['version']}）",
        "画布资源就绪"
        if out["canvas"]["available"]
        else "! 画布资源缺失：" + out["canvas"].get("reason", ""),
        "允许的项目根: " + (os.pathsep.join(out["roots"]) or "（未配置）"),
        "根来源: " + root_info["source"],
    ]
    return {"content": _text(*lines), "structuredContent": out}


HANDLERS = {
    "tavotto_health": _call_health,
    "tavotto_open_figure": _call_open,
    "tavotto_apply_overrides": _call_apply,
    "tavotto_preflight": _call_preflight,
    "tavotto_export": _call_export,
    "tavotto_verify_replay": _call_verify,
    "tavotto_refresh_project": _call_refresh,
    "tavotto_close_session": _call_close,
}

#: 结果里挂 UI 的工具（与 `_tools()` 的 `_meta` 一致）
UI_TOOLS = ("tavotto_open_figure", "tavotto_apply_overrides")


def _human_error(payload: dict) -> str:
    """给人看的那一份：错误 + 下一步，**不含机器码**。"""
    text = str(payload.get("error") or "")
    recovery = payload.get("recovery")
    if isinstance(recovery, str):
        step = recovery.strip()
        if step and step not in text:
            text += f"\n下一步：{step}"
    elif isinstance(recovery, (list, tuple)) and recovery:
        text += "\n恢复步骤：\n- " + "\n- ".join(str(item) for item in recovery)
    return text


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
        # 但 **`code` 只进 `structuredContent`**：`content` 是念给用户听的那一份，
        # 把机器码摆在最前面，模型多半会连着念出去——用户听到
        # 「workspace_confirmation_no_response」等于什么都没听到。同 ADR 0021
        # 的「code 稳定，文案随时可改」：稳定的是 code，给人看的是文案与下一步。
        return {
            "isError": True,
            "content": _text(_human_error(payload)),
            "structuredContent": payload,
        }
    if name in UI_TOOLS:
        if (result.get("structuredContent") or {}).get("mode") == bridge.BATCH_MODE:
            # **批量结果不挂画布，而且要把这件事说出口。**一次 tools/call 只带
            # 得出一块 iframe，而画布只认完整的单图 open 结果
            # （`web/src/mcp/main.tsx` 的 `isOpenResult` 要 session_id + manifest
            # + project + stem + script + profile 六项齐全）。把批量负载挂上去
            # 的表现是 iframe 永远停在「等待 tavotto_open_figure」——静默少一块
            # UI 正是这里最不该发生的事。
            result["structuredContent"]["canvas_ui"] = {
                "available": False,
                "code": "batch_open",
                "reason": (
                    "批量打开不带内嵌画布。要在画布里改哪一张，就用 stem 单独调一次 "
                    "tavotto_open_figure——已经开着的会话不受影响。"
                ),
            }
            result["content"][0]["text"] += (
                "\n内嵌画布：批量这一路不挂画布；要在画布里编辑某一张，"
                "单独 tavotto_open_figure 那个 stem。"
            )
        elif widget.available():
            meta = dict(widget.resource_meta())
            # widgetData 是 host 递给 iframe 的初始负载（ChatGPT 侧的约定）；
            # MCP Apps 标准路径下 iframe 从 ui/notifications/tool-result 拿同一份。
            meta["widgetData"] = result["structuredContent"]
            result["_meta"] = meta
        else:
            # 画布产物缺失：工具照常干活（manifest/SVG 都在），但**必须把
            # 「这次没有内嵌画布、为什么」说出口**——静默少一块 UI，用户看到
            # 的是「说好的画布呢」，而且没有任何线索。
            reason = widget.missing_reason()
            result["structuredContent"]["canvas_ui"] = {
                "available": False,
                "code": "widget_missing",
                "reason": reason,
            }
            note = f"! 内嵌画布不可用：{reason}"
            content = result.get("content") or []
            if content and content[0].get("type") == "text":
                content[0]["text"] += "\n" + note
            else:
                result["content"] = _text(note)
    return result


# ------------------------------- 协议主循环 ---------------------------------
class Server:
    def __init__(self, conn: StdioConnection | None = None) -> None:
        self.conn = conn if conn is not None else StdioConnection()
        self.initialized = False
        self.client_ready = False
        self._inbox: queue.Queue | None = None
        self._deferred: deque[dict] = deque()
        self._request_seq = {"roots": 0, "elicitation": 0}
        self._input_closed = False

    def handle(self, msg: dict) -> None:
        method = msg.get("method")
        rid = msg.get("id")
        if method is None:
            self._handle_response(msg)
            return
        if rid is None:  # 通知：不回，永远不回
            self._handle_notification(method)
            return
        try:
            raw_params = msg.get("params")
            if raw_params is not None and not isinstance(raw_params, dict):
                raise RpcError(INVALID_PARAMS, "params 必须是对象")
            params = raw_params or {}
            # SEP-2260 要求 server→client 请求与一个活着的 client request 关联。
            # 因而不在 initialize 后偷发 roots/list，而是在真实 tools/call 的
            # 处理窗口里嵌套请求。显式 TAVOTTO_MCP_ROOTS 则完全绕过这一步。
            if method == "tools/call" and params.get("name") != "tavotto_close_session":
                self._refresh_protocol_roots()
            if method == "tools/call" and params.get("name") == "tavotto_open_figure":
                arguments = params.get("arguments")
                if isinstance(arguments, dict):
                    self._confirm_workspace_for_open(arguments)
            self.conn.result(rid, self.dispatch(method, params))
        except RpcError as exc:
            self.conn.error(rid, exc)
        except Exception as exc:  # noqa: BLE001 — 任何异常都不许打死连接
            print("tavotto-mcp: 未处理异常\n" + traceback.format_exc(), file=sys.stderr)
            self.conn.error(rid, RpcError(INTERNAL_ERROR, f"内部错误: {exc}"))

    def _handle_notification(self, method: str) -> None:
        if method == "notifications/initialized":
            self.client_ready = True
        elif method == "notifications/roots/list_changed":
            # 不在通知处理里立即发请求（那会变成无 originating request 的野请求）。
            # 下一次 tools/call 再原子刷新。
            bridge.mark_protocol_roots_stale()

    def _handle_response(self, msg: dict) -> None:
        # 正常的 server→client response 都由 `_client_request` 在对应的活跃
        # client request 内消费。掉到主循环里的 response 已经过期或 id 不匹配，
        # 不能拿它改变授权状态。
        return

    @staticmethod
    def _response_error(msg: dict) -> str | None:
        if "error" not in msg:
            return None
        error = msg.get("error") or {}
        if isinstance(error, dict):
            return f"{error.get('code', 'error')}: {error.get('message', error)}"
        return str(error)

    def _client_request(
        self, label: str, method: str, params: dict | None, timeout: float
    ) -> tuple[dict | None, str | None]:
        """在当前 tools/call 内发一个关联的 server→client 请求并有界等待。"""
        if self._input_closed:
            return None, "宿主输入已经关闭"
        if self._inbox is None:
            return None, "当前传输没有可等待的 client response"
        self._request_seq[label] += 1
        request_id = f"tavotto-{label}-{self._request_seq[label]}"
        self.conn.request(request_id, method, params)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, f"{method} 在 {timeout:g}s 内没有响应"
            try:
                event = self._inbox.get(timeout=remaining)
            except queue.Empty:
                return None, f"{method} 在 {timeout:g}s 内没有响应"
            if isinstance(event, RpcError):
                self.conn.error(None, event)
                continue
            if event is None:
                self._input_closed = True
                return None, f"等待 {method} 时宿主断开连接"
            if event.get("method") is None and event.get("id") == request_id:
                return event, None
            # 读取线程可能已经拿到后续通知/请求。先缓存，当前 tool call 回完后
            # 仍按原顺序处理，不能吞掉或重排。
            self._deferred.append(event)

    def _refresh_protocol_roots(self) -> None:
        if not bridge.protocol_roots_needed():
            return
        response, transport_error = self._client_request(
            "roots", "roots/list", None, ROOTS_REQUEST_TIMEOUT_S
        )
        if transport_error:
            # 声明了 capability 却没把响应送回来 = 宿主接线的问题，**不是**
            # 「没配工作区」，更不是用户拒绝（issue #173）。
            bridge.fail_protocol_roots(transport_error, state="no_response")
            return
        assert response is not None
        protocol_error = self._response_error(response)
        if protocol_error:
            bridge.fail_protocol_roots(f"roots/list 被宿主拒绝：{protocol_error}")
            return
        bridge.accept_protocol_roots(response.get("result"))

    def _confirm_workspace_for_open(self, arguments: dict) -> None:
        target = arguments.get("project_path") or arguments.get("script_path")
        candidate = bridge.user_binding_candidate(target)
        if candidate is None:
            return
        message = (
            "Tavotto 请求读取并编辑下面这个本地目录中的图表文件：\n\n"
            f"{candidate}\n\n"
            "仅当它是本次任务要使用的工作区时批准。授权只在当前 Tavotto "
            "MCP 连接内有效；拒绝不会修改任何文件。"
        )
        response, transport_error = self._client_request(
            "elicitation",
            "elicitation/create",
            {
                "message": message,
                "requestedSchema": {
                    "type": "object",
                    "properties": {
                        "approve": {
                            "type": "boolean",
                            "title": "允许 Tavotto 访问这个目录",
                            "description": "请核对上方完整路径；不确定时保持关闭。",
                            "default": False,
                        },
                    },
                    "required": ["approve"],
                },
            },
            ELICITATION_REQUEST_TIMEOUT_S,
        )
        if transport_error:
            # 超时 / EOF / 没有可等待的传输：**框从没到过用户面前**。报成用户
            # 拒绝会把人送去「再点一次」，而根本没有框可点（issue #173）。
            bridge.fail_user_binding(transport_error, state="no_response")
            return
        assert response is not None
        protocol_error = self._response_error(response)
        if protocol_error:
            bridge.fail_user_binding(f"elicitation/create 被宿主拒绝：{protocol_error}")
            return
        result = response.get("result")
        if not isinstance(result, dict):
            bridge.fail_user_binding("elicitation/create 响应不是对象")
            return
        action = result.get("action")
        if action != "accept":
            state = "declined" if action == "decline" else "cancelled"
            bridge.fail_user_binding(f"用户选择 {action or 'cancel'}", state=state)
            return
        content = result.get("content")
        if not isinstance(content, dict) or content.get("approve") is not True:
            bridge.fail_user_binding("用户没有勾选目录授权", state="declined")
            return
        bridge.accept_user_binding(candidate)

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
            if uri != widget.RESOURCE_URI:
                raise RpcError(INVALID_PARAMS, f"没有这个资源: {uri}")
            if not widget.available():
                # 资源 URI 对、文件却不在：**明确报缺失与修法**，不给空 HTML
                # ——空的会渲染成一个白框，用户与 host 都拿不到任何线索。
                raise RpcError(INVALID_PARAMS, f"画布资源缺失: {widget.missing_reason()}")
            return {"contents": [widget.resource_contents()]}
        if method == "prompts/list":
            return {"prompts": []}
        raise RpcError(METHOD_NOT_FOUND, f"不支持的方法: {method}")

    def initialize(self, params: dict) -> dict:
        want = params.get("protocolVersion")
        version = want if want in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
        self.initialized = True
        bridge.observe_mcp_client(version, params.get("capabilities"), params.get("clientInfo"))
        caps: dict = {"tools": {"listChanged": False}}
        if widget.available():
            caps["resources"] = {"listChanged": False, "subscribe": False}
        return {
            "protocolVersion": version,
            "capabilities": caps,
            "serverInfo": {"name": SERVER_NAME, "title": "Tavotto", "version": _version()},
            "instructions": (
                "Tavotto 负责结构化图表编辑：改的是 override（gid + prop + value），"
                "**不会动用户的 Python 源码**。流程：tavotto_open_figure 打开 → "
                "tavotto_apply_overrides 改（patches 永远发全量列表）→ "
                "tavotto_preflight 体检 → tavotto_export 出图。"
                "数据本身、坐标范围、加删曲线/子图、colorbar 方向这些必须回代码改；"
                "改完 .py 之后调 tavotto_refresh_project（不是重跑脚本），Tavotto 界面会自己更新。"
            ),
        }

    def serve_forever(self) -> int:
        inbox: queue.Queue = queue.Queue()
        self._inbox = inbox

        def read_loop() -> None:
            while True:
                try:
                    msg = self.conn.read()
                except RpcError as exc:
                    inbox.put(exc)
                    continue
                except (OSError, ValueError) as exc:
                    inbox.put(RpcError(PARSE_ERROR, str(exc)))
                    inbox.put(None)
                    return
                inbox.put(msg)
                if msg is None:
                    return

        threading.Thread(target=read_loop, daemon=True, name="tavotto-mcp-stdio-reader").start()
        while True:
            event = self._deferred.popleft() if self._deferred else inbox.get()
            if isinstance(event, RpcError):
                self.conn.error(None, event)
                continue
            if event is None:  # stdin EOF = host 走了，收摊
                return 0
            self.handle(event)
            if self._input_closed and not self._deferred:
                return 0


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
    except Exception as exc:  # noqa: BLE001 — 探测失败也要如实说
        report["worker_python_error"] = str(exc)
    print(json.dumps(report, ensure_ascii=False, indent=1), file=sys.stderr)
    return 0
