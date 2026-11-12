"""期刊目录服务（线协议 v1）：独立部署的服务端，不属于 Tavotto 包。

结构照 `services/telemetry_proxy/`：纯标准库、与平台无关的 `core` + 薄 WSGI 入口
（`wsgi.py`）。不进 wheel/sdist（pyproject 已整体排除 `services/**`）。
"""
