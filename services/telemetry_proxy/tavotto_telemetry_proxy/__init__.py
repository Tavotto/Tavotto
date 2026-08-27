"""Tavotto 匿名遥测代理：校验 → 规范化 → 转发给分析后端。

业务逻辑全在 `core.py`（纯标准库、与平台无关），提供商特有的 JSON 全在
`posthog.py`，对外只有 `wsgi.py` 一个入口——本地、测试、Vercel 跑的是同一个
`application`，没有第二条路径可以悄悄坏掉。
"""

from .core import handle

__all__ = ["handle"]
