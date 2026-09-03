"""项目文档的格式判据 —— schema 版本、结构校验、收纳目录里谁不是用户文档，
以及读回一份文档时的非有限数闸（`loads_document`）。

这里只放**判据**，不放 I/O：落盘一律走 `atomicio`，HTTP 形状留在 `app.py`。
前端的同一套判据在 `web/src/types/document.ts`（`migrateToProject`）与
`web/src/lib/migrate.ts`（`normalizeLayout`）——两侧都只认 schema 2 / 3，
新增版本必须同时改，否则新版本写出去的文档旧构建打不开。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: 单画布文档（`FigureDocument`）。
SCHEMA_FIGURE = 2
#: 项目文档（`ProjectDocument`，一个项目多张画布）。见 docs/adr/0001。
SCHEMA_PROJECT = 3
#: 能读能写的 schema 版本。**更高的版本一律拒绝**——旧构建对新字段的语义一无所知，
#: 「尽力打开」等于用旧规则重写用户的新数据。
SUPPORTED_SCHEMAS = (SCHEMA_FIGURE, SCHEMA_PROJECT)
SCHEMA_CURRENT = SCHEMA_PROJECT

# ---------------------------------------------------------------------------
# 收纳目录（数据目录 `layouts/` 与项目 `tavottofile/`）里 Tavotto 自己的东西。
#
# **枚举而不是前缀规则。** 「下划线开头 = 内部文件」看着更省事，但画布名会
# 过一遍 `[^\w\-一-鿿]+ → _` 的净化：`（图一）` 会变成 `_图一_`，前缀规则
# 会把它从用户的文档列表里**藏起来**。所以这里只认我们自己创建的那几个名字，
# 新增内部文件时在这张表里加一行——加不了的东西就不该放进收纳目录。
# ---------------------------------------------------------------------------
#: 样式预设的**旧位置**（0.12 之前跨文档共享的样式表就放在收纳目录里）。
#: 现在样式与规范都在 `engine/profilestore.py` 管的用户数据目录 `profiles/` 下，
#: 首次访问时一次性迁走。这个名字仍然留在保留表里：老装机上那份文件可能还在，
#: 而「画布列表 = 对目录 glob("*.json")」——不剔掉的话它会作为一份叫 `_styles`
#: 的画布出现在用户的「打开」列表里，还能被同名画布整个盖掉。
STYLES_FILENAME = "_styles.json"
#: 文档自动保存槽位目录。
AUTOSAVE_DIRNAME = "_autosave"
#: 布局版本时间线目录（旧位置；新写入进项目 `tavottofile/versions/`）。
VERSIONS_DIRNAME = "_versions"

#: 收纳目录里**不是用户文档**的 `*.json` 文件名（不含目录，目录本来就不会被
#: `glob("*.json")` 命中）。
RESERVED_DOCUMENT_FILENAMES = frozenset({STYLES_FILENAME})
RESERVED_DOCUMENT_STEMS = frozenset(Path(n).stem for n in RESERVED_DOCUMENT_FILENAMES)


def is_user_document_stem(stem: str) -> bool:
    """这个文件名（不带扩展名）代表一份用户文档吗？"""
    return stem not in RESERVED_DOCUMENT_STEMS


def require_user_document_stem(stem: str) -> None:
    """文档 API 的入口守卫：撞上 Tavotto 自己的文件名就 409。"""
    if not is_user_document_stem(stem):
        raise DocumentError("reserved_name", "这个名称已被 Tavotto 占用", status=409)


class DocumentError(ValueError):
    """文档结构不合法。`code` 是稳定枚举，给 HTTP 层直接映射。

    `status` 让「载荷本身不合法」（400）与「名字被占用」（409）分开——
    前者要用户改内容，后者要用户改名字，前端的出口完全不同。
    """

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def as_payload(self) -> dict[str, str]:
        return {"error": self.message, "code": self.code}


#: 非有限数的**读侧**入口。写侧是 `atomicio.dumps_json(allow_nan=False)`。
#:
#: 两侧不是两份权威，是**同一条规则的两个边界**——与 ADR 0023 §3.2 里
#: `patchspec` 和 `atomicio` 的关系同形：规则只有一条（NaN / Infinity 不是
#: JSON，浏览器的 `JSON.parse` 读不动），出口有两个，各自要在自己那一侧
#: 响亮地失败。少了读侧这一半，外部工具往 `tavottofile/*.json` 写一个 NaN
#: 之后，后端一路读得动（Python 的 `json.loads` 默认认这三个字面量），
#: 而每一份经过后端交给浏览器的字节都会在 `JSON.parse` 上炸掉——用户看到的
#: 是「这份文档打不开」，磁盘上那个文件看起来好端端的。
#:
#: **code 与写侧刻意不同名。** 两边告诉用户的事不一样：写侧是「你这次保存
#: 没写进去，磁盘上那份一字未动」，读侧是「磁盘上那份是坏的，Tavotto 没有
#: 动它」——用户的下一步动作也不同（改文档 vs 去查是谁写坏了那个文件）。
def _reject_non_finite(literal: str) -> Any:
    raise DocumentError(
        "non_finite_on_disk",
        f"这份文档里含有 {literal}——它不是合法的 JSON，浏览器读不出来（Tavotto 没有改动这个文件）",
    )


def loads_document(data: str | bytes) -> Any:
    """解析一份文档的 JSON —— **读侧唯一的入口**，非有限数在这里被拒。

    `parse_constant` 只在解析器撞见 `NaN` / `Infinity` / `-Infinity` 这三个
    **非标准字面量**时被调用，常规载荷一次都不进这个回调，所以这道闸在热路径
    （版本时间线整份读回）上是零开销的。

    刻意**不**接管 `parse_float`：`1e400` 这类合法 JSON 数字在 Python 与浏览器
    里都会溢出成 `Infinity`，两侧读到的是同一个值——它不属于本条要挡的那种
    「后端读得动、前端读不动」的不对称，而给每一个浮点数套一层 Python 回调
    会把整条读路径拖慢一大截。它仍然过不了写侧的 `allow_nan=False`。

    结构判据在 `validate_document`：这里只回答「这段字节能不能变成一份
    所有读者都读得回来的 JSON」。
    """
    return json.loads(data, parse_constant=_reject_non_finite)


def validate_document(raw: Any) -> dict:
    """校验并原样返回文档载荷；不合法时抛 `DocumentError`。

    **只查骨架，不查每个字段。** 判据要挡住的是「这不是一份 Tavotto 文档」
    和「这个版本我读不了」；再往下逐字段较真会把**用户真实的编辑**挡在保存
    之外——那比存下一份有点怪的文档坏得多（共享规则 §2 的第一条优先级）。

    唯一的例外是非有限数，它由序列化那一步（`atomicio.dumps_json`）挡下：
    那不是"有点怪"，那是写出去谁都读不回来。
    """
    if not isinstance(raw, dict):
        raise DocumentError("invalid_document", "无效的文档（需要一个 JSON 对象）")

    schema = raw.get("schema")
    if schema not in SUPPORTED_SCHEMAS:
        if isinstance(schema, int) and schema > SCHEMA_CURRENT:
            raise DocumentError(
                "schema_too_new",
                f"这份文档来自更新的 Tavotto（schema {schema}），请升级后再打开",
            )
        raise DocumentError(
            "invalid_document",
            f"无效的文档（需要 schema {' 或 '.join(str(s) for s in SUPPORTED_SCHEMAS)}）",
        )

    if schema == SCHEMA_PROJECT:
        canvases = raw.get("canvases")
        if not isinstance(canvases, list) or not canvases:
            raise DocumentError("invalid_document", "项目文档至少要有一张画布")

    return raw
