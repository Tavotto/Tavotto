"""MCP App UI 资源 —— Codex 内嵌的那块画布。

画布本体是 **Tavotto 前端那一份代码**（`web/src/canvas/*` + 现有 stores），
由 `python scripts/build_mcp_widget.py` 打成一个自包含的单文件 HTML 落在
`codex-plugin/mcp/widget/canvas.html`。这里只负责把它当成 MCP 资源交出去。

为什么是「自包含单文件」而不是指向一个 localhost 页面：

* iframe 的 CSP 由 `_meta.ui.csp` 声明，而 Tavotto sidecar 的端口是**动态**的
  （`127.0.0.1:0`），根本没法提前写进白名单；
* 更要紧的是**不许用「开个浏览器」冒充内嵌画布**。widget 与后端之间的一切往来
  都走 MCP 的 `tools/call` 回到本 server，因此 `connectDomains` 是空的——
  这块画布不发任何跨源请求。

产物缺席时（源码检出但没跑过构建）：**不声明资源、也不给工具挂 `_meta.ui`**，
五个工具照常可用。假装有 UI 只会让用户对着一个白框等。
"""

from __future__ import annotations

import os
from pathlib import Path

#: MCP Apps 的资源 MIME（`@modelcontextprotocol/ext-apps` 的 RESOURCE_MIME_TYPE）
RESOURCE_MIME_TYPE = "text/html;profile=mcp-app"
#: 资源 URI。改它等于换一块画布，host 侧的缓存也按它索引。
RESOURCE_URI = "ui://tavotto/canvas/v1.html"
#: `_meta` 里 MCP Apps 标准键之外的兼容别名（ext-apps 与 ChatGPT 各认一个）
RESOURCE_URI_META_KEY = "ui/resourceUri"
OPENAI_TEMPLATE_KEY = "openai/outputTemplate"

#: 构建产物位置（可用环境变量指到别处，方便边改边试）
WIDGET_ENV = "TAVOTTO_MCP_WIDGET"
_DEFAULT = Path(__file__).resolve().parent.parent / "widget" / "canvas.html"


def widget_path() -> Path:
    override = (os.environ.get(WIDGET_ENV) or "").strip()
    return Path(override).expanduser() if override else _DEFAULT


def available() -> bool:
    try:
        return widget_path().is_file() and widget_path().stat().st_size > 0
    except OSError:
        return False


def missing_reason() -> str:
    """产物为什么不可用——给 host / 用户一句能行动的话，不是一个白框。"""
    path = widget_path()
    override = (os.environ.get(WIDGET_ENV) or "").strip()
    where = f"{WIDGET_ENV} 指向的 {path}" if override else str(path)
    return (
        f"画布产物不存在或为空：{where}。源码态跑一次 "
        "`python scripts/build_mcp_widget.py`；装好的插件里缺这个文件"
        "说明安装不完整，重装插件即可。工具本身照常可用（改图走 "
        "tavotto_apply_overrides，产出 manifest 与 SVG）。"
    )


def html() -> str:
    return widget_path().read_text(encoding="utf-8")


def resource_meta() -> dict:
    """资源与工具共用的 `_meta`。

    `csp` 全空是有意的：画布不连任何外部域，数据一律经 `tools/call` 取。
    """
    csp = {"connectDomains": [], "resourceDomains": []}
    return {
        "ui": {
            "resourceUri": RESOURCE_URI,
            "prefersBorder": False,
            "csp": csp,
        },
        RESOURCE_URI_META_KEY: RESOURCE_URI,
        OPENAI_TEMPLATE_KEY: RESOURCE_URI,
        "openai/widgetDescription": "Tavotto 交互画布：拖图例与图内文字、改字号线宽刻度、跑出版规范预检、导出矢量 PDF。",
        "openai/widgetPrefersBorder": False,
        "openai/widgetCSP": {"connect_domains": [], "resource_domains": []},
    }


def tool_meta(*, invoking: str, invoked: str) -> dict:
    """挂在**需要画布**的工具上的 `_meta`（open / apply）。

    预检、导出、关会话不挂——每个工具调用都拖一块 iframe 出来，用户看到的是
    画布不停重建，而那三个工具的产出本来就是文字与文件。
    """
    meta = dict(resource_meta())
    meta["ui"] = {**meta["ui"], "visibility": ["model", "app"]}
    meta["openai/widgetAccessible"] = True
    meta["openai/toolInvocation/invoking"] = invoking
    meta["openai/toolInvocation/invoked"] = invoked
    return meta


def resource_descriptor() -> dict:
    return {
        "uri": RESOURCE_URI,
        "name": "tavotto-canvas",
        "title": "Tavotto 画布",
        "description": "Codex 内嵌的 Tavotto 交互画布（拖拽、字号线宽、预检、导出）。",
        "mimeType": RESOURCE_MIME_TYPE,
        "_meta": resource_meta(),
    }


def resource_contents() -> dict:
    return {
        "uri": RESOURCE_URI,
        "mimeType": RESOURCE_MIME_TYPE,
        "text": html(),
        "_meta": resource_meta(),
    }
