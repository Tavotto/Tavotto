"""GitHub Actions 表达式的一个**极小**求值器——只为把 ci.yml 里的 `if:` / `${{ }}` 当真值表跑。

为什么要有它：`tests/test_merge_queue_workflows.py` 原来只能用子串钉「表达式里出现了
`contains(...labels..., 'full-ci')`」——那是判**写了什么**，不是判**算出什么**。事件表
（CI01 §2）真正要回答的是「`unlabeled(full-ci)` 那个 run 里 Gate 用哪一档」，这只有把
表达式对着合成的 `github` 上下文算一遍才答得出。子串判据还会被自己的注释咬到。

**刻意只支持 ci.yml 今天用到的子集**，遇到别的一律抛 `UnsupportedExpression`——
一个「认不出就当 false」的求值器会让判据在它看不懂的表达式上恒绿：

* 运算：`||`、`&&`、`!`、`==`、`!=`、括号；
* 字面量：`'单引号字符串'`（`''` 转义）、整数 / 小数、`true` / `false` / `null`；
* 函数：只有 `contains(search, item)`；
* 上下文路径：`a.b.c`、对象过滤 `a.*.name`（GitHub 的 `.*` 对数组逐项取字段）。

语义按 GitHub 文档「Evaluate expressions in workflows and actions」：

* 路径取不到 → `null`（merge_group payload 里没有 `pull_request`，读它就是 null，不是错）；
* `==` 两侧同为字符串时**忽略大小写**；类型不同时先按 GitHub 的规则转成数字比
  （`null` → 0、`true` → 1、`''` → 0、其它字符串 → NaN，NaN 与任何值都不相等）；
* `&&` / `||` 返回操作数本身（与 JS 相同），真值：`false` / `null` / `0` / `''` 为假；
* `contains(数组, x)`：任一元素 `== x`；`contains(字符串, x)`：忽略大小写的子串；
  `contains(null, x)`：按空字符串算 → false。

`${{ … }}` 外壳可带可不带；折叠块（`if: >-`）先把行拼成一行再传进来。
"""

from __future__ import annotations

import math
import re
from typing import Any

__all__ = ["UnsupportedExpression", "evaluate", "render", "truthy"]


class UnsupportedExpression(ValueError):
    """表达式里出现了本求值器不认的东西——判据要红着提醒人来扩，不能猜。"""


_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<or>\|\|)
  | (?P<and>&&)
  | (?P<eq>==)
  | (?P<ne>!=)
  | (?P<not>!)
  | (?P<lp>\()
  | (?P<rp>\))
  | (?P<comma>,)
  | (?P<dot>\.)
  | (?P<star>\*)
  | (?P<str>'(?:[^']|'')*')
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_-]*)
  | (?P<bad>.)
    """,
    re.X,
)


def _tokenize(src: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in _TOKEN.finditer(src):
        kind = m.lastgroup
        assert kind is not None
        if kind == "ws":
            continue
        if kind == "bad":
            raise UnsupportedExpression(f"认不出的字符 {m.group(0)!r}，位置 {m.start()}：{src!r}")
        out.append((kind, m.group(0)))
    return out


def _strip_wrapper(src: str) -> str:
    s = " ".join(line.strip() for line in src.strip().splitlines())
    m = re.fullmatch(r"\$\{\{(.*)\}\}", s, re.S)
    if m:
        s = m.group(1)
    if "${{" in s:
        raise UnsupportedExpression(f"表达式里嵌着第二个 `${{{{`（多段插值不支持）：{src!r}")
    return s.strip()


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], ctx: dict[str, Any], src: str):
        self.toks = tokens
        self.i = 0
        self.ctx = ctx
        self.src = src

    # ---- 工具 ----
    def _peek(self) -> tuple[str, str] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _take(self, kind: str) -> str:
        tok = self._peek()
        if tok is None or tok[0] != kind:
            raise UnsupportedExpression(f"期待 {kind}，见到 {tok!r}：{self.src!r}")
        self.i += 1
        return tok[1]

    def _accept(self, kind: str) -> bool:
        tok = self._peek()
        if tok is not None and tok[0] == kind:
            self.i += 1
            return True
        return False

    # ---- 文法（优先级由低到高）----
    def parse(self) -> Any:
        value = self._or()
        if self._peek() is not None:
            raise UnsupportedExpression(f"表达式尾部有多余 token {self._peek()!r}：{self.src!r}")
        return value

    def _or(self) -> Any:
        left = self._and()
        while self._accept("or"):
            right = self._and()
            left = left if truthy(left) else right
        return left

    def _and(self) -> Any:
        left = self._eq()
        while self._accept("and"):
            right = self._eq()
            left = right if truthy(left) else left
        return left

    def _eq(self) -> Any:
        left = self._unary()
        tok = self._peek()
        if tok is not None and tok[0] in ("eq", "ne"):
            self.i += 1
            right = self._unary()
            same = _equals(left, right)
            return same if tok[0] == "eq" else not same
        return left

    def _unary(self) -> Any:
        if self._accept("not"):
            return not truthy(self._unary())
        return self._primary()

    def _primary(self) -> Any:
        tok = self._peek()
        if tok is None:
            raise UnsupportedExpression(f"表达式意外结束：{self.src!r}")
        kind, text = tok
        if kind == "lp":
            self.i += 1
            value = self._or()
            self._take("rp")
            return value
        if kind == "str":
            self.i += 1
            return text[1:-1].replace("''", "'")
        if kind == "num":
            self.i += 1
            return float(text) if "." in text else int(text)
        if kind == "ident":
            self.i += 1
            if text in ("true", "false", "null"):
                return {"true": True, "false": False, "null": None}[text]
            if self._peek() == ("lp", "("):
                return self._call(text)
            return self._path(text)
        raise UnsupportedExpression(f"这里不该出现 {text!r}：{self.src!r}")

    def _call(self, name: str) -> Any:
        self._take("lp")
        args: list[Any] = []
        if not self._accept("rp"):
            args.append(self._or())
            while self._accept("comma"):
                args.append(self._or())
            self._take("rp")
        if name != "contains":
            raise UnsupportedExpression(f"不支持的函数 {name}()：{self.src!r}")
        if len(args) != 2:
            raise UnsupportedExpression(f"contains() 要两个参数，给了 {len(args)}：{self.src!r}")
        return _contains(args[0], args[1])

    def _path(self, head: str) -> Any:
        value: Any = self.ctx.get(head)
        if head not in self.ctx:
            raise UnsupportedExpression(f"未知的上下文根 `{head}`：{self.src!r}")
        while self._accept("dot"):
            if self._accept("star"):
                if isinstance(value, dict):
                    value = list(value.values())
                elif isinstance(value, list):
                    value = list(value)
                else:
                    value = []
                self._take("dot")
                field = self._take("ident")
                value = [_get(item, field) for item in value]
                continue
            field = self._take("ident")
            value = _get(value, field)
        return value


def _get(obj: Any, field: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(field)
    return None


def _to_number(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        if value.strip() == "":
            return 0.0
        try:
            return float(value)
        except ValueError:
            return math.nan
    return math.nan


def _equals(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.casefold() == b.casefold()
    if type(a) is type(b) and not isinstance(a, (list, dict)):
        return a == b
    na, nb = _to_number(a), _to_number(b)
    if math.isnan(na) or math.isnan(nb):
        return False
    return na == nb


def _contains(search: Any, item: Any) -> bool:
    if isinstance(search, list):
        return any(_equals(x, item) for x in search)
    if search is None:
        search = ""
    if isinstance(search, str):
        return str(item if item is not None else "").casefold() in search.casefold()
    raise UnsupportedExpression(f"contains() 的第一个参数既不是数组也不是字符串：{search!r}")


def truthy(value: Any) -> bool:
    """GitHub 的真值规则：false / null / 0 / '' 为假，其余为真（含空数组——与 JS 一致）。"""
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        return value != ""
    return True


def evaluate(expression: str, github: dict[str, Any]) -> Any:
    """对着一份合成的 `github` 上下文求值**一段**表达式；返回 GitHub 会得到的**值**（不是布尔）。

    `if:` 的取舍用 `truthy(evaluate(...))`；带多段插值的字符串（concurrency 的 `group:`）
    用 `render()`。
    """
    src = _strip_wrapper(expression)
    if not src:
        raise UnsupportedExpression("空表达式")
    return _Parser(_tokenize(src), {"github": github}, src).parse()


def _to_string(value: Any) -> str:
    """GitHub 把插值结果拼进字符串时的写法：null → ''、布尔 → 小写、数字原样。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render(template: str, github: dict[str, Any]) -> str:
    """把一个带零到多段 `${{ … }}` 的字符串渲染成 GitHub 会得到的字符串。

    `env:` 的值、`concurrency.group` 都是这种形状；`if:` 不是（它是一段表达式，走 `evaluate`）。
    """
    parts: list[str] = []
    pos = 0
    for m in re.finditer(r"\$\{\{(.*?)\}\}", template, re.S):
        parts.append(template[pos : m.start()])
        parts.append(_to_string(evaluate(m.group(1), github)))
        pos = m.end()
    parts.append(template[pos:])
    return "".join(parts)
