"""日志参数的「明文放行」标记：只有**结构上不可能含用户内容**的值才在诊断包里明文出现。

`diagnostics.ExportLogFormatter` 写诊断包那份日志（`cache/diagnostics.log`）时，参数默认一律
换成哈希（报错文字、路径、脚本名、查询串在写入那一刻就不出门，REL-05）。代价是
`PDF 后端: str:…`、`解释器来源=str:…` 这种**本来就只能是几个固定值之一**的参数也读不出来了。
这里给出几个放行口，判据是值的**出处**（源码里的常量集合、路由对象），不是值长什么样：

* `known(value, allowed)`：值必须是 `allowed` 里的成员——`allowed` 是 Tavotto 源码里的常量集合
  （解释器来源、PDF 后端名、导出状态……）。不在集合里的值原样交回去，照样被哈希；
* `route(request.url_rule)`：Flask 路由规则对象（只由源码里的装饰器注册）；
* `version(value)`：版本号——只由 ASCII 数字、点与 PEP 440 的固定后缀词组成，没有地方放用户的字。

数、布尔、None 本来就明文，不需要标记。`Plain` 不能在别处直接构造（构造要一枚本模块私有的令牌），
`tests/test_diagnostics_log_privacy.py` 另用 AST 看护：`known` 的 `allowed` 必须是模块常量或字符串
字面量组成的集合，不许在调用点现拼（`known(x, {x})` 就是后门）。

本模块只用标准库，Flask 进程与 pool / deprepair 都能 import，不引入环。
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable

_TOKEN = object()

#: 版本号：`3`、`3.10.8`、`2.0.0rc1`、`1.26.4.post1`、`0.15.0.dev3`、`v1.2`。只认 ASCII 数字与固定词。
_VERSION = re.compile(r"^v?[0-9]{1,6}(?:\.[0-9]{1,6}){0,3}(?:(?:a|b|rc|\.post|\.dev)[0-9]{1,6})?$")

#: HTTP 方法：闭集。
HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


class Plain:
    """已经确认可以明文出门的参数。只由本模块的 `known` / `known_each` / `route` / `version` 构造。"""

    __slots__ = ("text",)

    def __init__(self, text: str, token: object) -> None:
        if token is not _TOKEN:
            raise TypeError("Plain 只能由 logsafe 的 known / known_each / route / version 构造")
        self.text = text

    def __str__(self) -> str:
        return self.text

    __repr__ = __str__


def known(value, allowed: Collection[str]):
    """`value` 是 `allowed`（源码里的常量集合）的成员 → 明文；否则原样交回（诊断包里照样哈希）。

    本机的完整日志（app.log）不受影响：`Plain` 打印出来就是原值。"""
    if isinstance(value, str) and value in allowed:
        return Plain(value, _TOKEN)
    return value


def known_each(values: Iterable, allowed: Collection[str]):
    """一组值全是 `allowed` 的成员 → 明文 `a, b`；有一个不是就整组原样交回。"""
    items = list(values)
    if all(isinstance(v, str) and v in allowed for v in items):
        return Plain(", ".join(items), _TOKEN)
    return values


def route(url_rule):
    """Flask 请求命中的路由规则（`request.url_rule`）→ 明文 `/api/native/sessions/<session_id>`。

    判的是**类型**：只收 werkzeug 的 `Rule` 对象——它只由源码里的 `@app.get(...)` 装饰器注册，
    规则字符串是代码写的，不含请求里的具体值。没命中路由（`None`）→ `<no-route>`。"""
    if url_rule is None:
        return Plain("<no-route>", _TOKEN)
    cls = type(url_rule)
    if cls.__module__.startswith("werkzeug.routing") and isinstance(
        getattr(url_rule, "rule", None), str
    ):
        return Plain(url_rule.rule, _TOKEN)
    return url_rule


def version(value):
    """长得是版本号 → 明文；否则原样交回。"""
    if isinstance(value, str) and _VERSION.match(value):
        return Plain(value, _TOKEN)
    return value
