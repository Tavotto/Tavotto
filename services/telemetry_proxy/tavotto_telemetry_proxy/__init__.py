"""Tavotto 匿名遥测代理：校验 → 规范化 → 转发给分析后端。

业务逻辑全在 `core.py`（纯标准库、与平台无关），提供商特有的 JSON 全在
`posthog.py`，部署适配层薄到可以整个换掉（`wsgi.py` / `api/index.py`）。
"""
from .core import handle

__all__ = ["handle"]
