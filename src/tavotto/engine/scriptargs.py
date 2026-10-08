"""脚本参数的静态 schema（T07）：从 argparse 的**字面量声明**里读出「这个脚本收哪些参数」，给表单当建议。

argv 的权威仍是有序 token 列表（T03，`runconfig`）；本模块只回答一个**静态**问题，结论是建议与校验增强，
不是执行门槛：

* **不执行任何东西**：只 `ast.parse`，不 import 用户模块、不 eval / `literal_eval` 任何节点、不跑 parser、
  不调 `--help`、不打开 `FileType` 的文件（`FileType('w')` 的扫描不会创建或截断输出）。
* **只认可证明的字面量**：`add_argument` 的 flags 全是字符串常量，`action` / `nargs` / `required` /
  `default` / `choices` / `type` / `help` / `metavar` / `dest` 是常量或明确的 argparse 名字。其余一律如实标
  `partial` 并写出理由（闭集 `REASONS`）：循环 / 推导式里加的参数、`**kwargs`、自定义 `Action`、子命令、
  `parents=`、`prefix_chars`、`fromfile_prefix_chars`、`parse_args([...])` 写死列表、`parse_known_args`、
  多个 parser、找不到解析调用……识别不全的脚本照样按原始 token 运行（不判、不拦）。
* **四种状态分开**：`required` 只来自显式 `required=True` 或位置参数的 argparse 规则；`default` 只是展示
  （绝不注入成 token，也不替代 required）；空串是一个值；开关「关」与「用脚本默认」由表单按 token 区分。
* **角色只来自类型**：`type=argparse.FileType('r'|'w'…)` 才有 `input_file` / `output_file`；名字叫
  `x` / `time` / `range` / `data` 不决定任何物理单位、列或计算参数。

表单 ↔ token 的转换在前端（`web/src/lib/scriptArgsForm.ts`），两侧共用 `tests/golden/script_args_form_vectors.json`：
Python 这边钉 schema 与「token → 真 argparse 的 Namespace」，TS 那边钉「表单编辑 → token」。

纯标准库；Flask 父进程侧（`prepsession` 与 `/api/engine/script-arguments` 用）。
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import threading
import tokenize
from collections import OrderedDict
from pathlib import Path

SCHEMA_VERSION = 1

#: 读源码的上限：超了不读（`unknown` + `too_large`），不截断后半段去猜
MAX_SOURCE_BYTES = 1024 * 1024
#: 一个 parser 最多收多少个参数（超了 `partial` + `too_many_arguments`）
MAX_ARGUMENTS = 128
MAX_CHOICES = 64
MAX_HELP_CHARS = 300
MAX_TEXT_CHARS = 120
_CACHE_SIZE = 64

STATUS_NONE = "none"  # 没有 argparse 证据（也可能是手写 sys.argv / click：见 reasons）
STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_UNKNOWN = "unknown"  # 读不了 / 语法错 / 太大：说不出话，不是「没有参数」
STATUSES = (STATUS_NONE, STATUS_COMPLETE, STATUS_PARTIAL, STATUS_UNKNOWN)

#: 识别不全的理由（闭集；前端按码换文案）。`FORM_BLOCKING` 里的那些让表单整体只读：
#: 表单往 token 里写一项都可能被解析成别的意思（子命令把后面的 token 交给子 parser、REMAINDER 吞掉其后一切、
#: 写死的列表根本不读 argv……），只剩原始 token 编辑。
REASONS = (
    "syntax_error",
    "too_large",
    "unreadable",
    "dynamic_add_argument",  # 循环 / 推导式里、非常量 flags、*args / **kwargs
    "conditional_argument",  # if / try / match 分支里加的参数：可能不存在
    "custom_action",
    "dynamic_value",  # action / nargs / required / choices / type 不是可证明的字面量
    "subcommands",
    "remainder",
    "parents",
    "prefix_chars",
    "fromfile",
    "explicit_parse_args",  # parse_args([...]) / parse_args(some_list)：不读命令行
    "parse_known_args",  # 不认识的 token 照样放行
    "multiple_parsers",
    "unresolved_parse_call",
    "no_parse_call",
    "too_many_arguments",
    "reads_sys_argv",  # 脚本自己也直接读 sys.argv
    "other_cli",  # click / typer / docopt / fire / optparse / getopt
)
FORM_BLOCKING = frozenset(
    {
        "subcommands",
        "remainder",
        "parents",
        "prefix_chars",
        "fromfile",
        "explicit_parse_args",
        "multiple_parsers",
        "unresolved_parse_call",
        "no_parse_call",
    }
)

_KNOWN_ACTIONS = {
    "store": "store",
    "store_const": "store_const",
    "store_true": "store_true",
    "store_false": "store_false",
    "append": "append",
    "append_const": "append_const",
    "extend": "extend",
    "count": "count",
    "help": "help",
    "version": "version",
}
_PARSE_CALLS = (
    "parse_args",
    "parse_known_args",
    "parse_intermixed_args",
    "parse_known_intermixed_args",
)
_OTHER_CLI = frozenset({"click", "typer", "docopt", "fire", "optparse", "getopt"})
_SIMPLE_TYPES = {"int": "int", "float": "float", "str": "str", "complex": "complex"}


# ---------------------------------------------------------------- 字面量读取（不 eval）


def _clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _literal(node: ast.AST | None):
    """常量 / 负数 / 常量组成的 list / tuple / set → `(True, 值)`；别的一律 `(False, None)`。

    手写而不是 `ast.literal_eval`：后者虽然不执行代码，也会构造任意大小的容器、接受 `1+2j` 这类运算——
    这里只要「明摆着写在源码里的那个值」。"""
    if isinstance(node, ast.Constant) and isinstance(
        node.value, (str, int, float, bool, type(None))
    ):
        return True, node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.USub, ast.UAdd))
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
        and not isinstance(node.operand.value, bool)
    ):
        value = node.operand.value
        return True, -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        if len(node.elts) > MAX_CHOICES:
            return False, None
        out = []
        for elt in node.elts:
            ok, value = _literal(elt)
            if not ok or isinstance(value, (list, tuple)):
                return False, None
            out.append(value)
        return True, out
    return False, None


def _token_text(value) -> str:
    """一个字面量在命令行上会被写成哪个 token（只用于 choices / 展示）。"""
    if isinstance(value, bool):
        return str(value)
    if value is None:
        return "None"
    return str(value)


def _display(value) -> str:
    return _clip(repr(value))


# ---------------------------------------------------------------- 名字解析


class _Names:
    """`argparse` 在这个脚本里叫什么：`import argparse [as ap]` / `from argparse import X [as Y]`。"""

    def __init__(self, tree: ast.AST) -> None:
        self.modules: set[str] = set()
        self.direct: dict[str, str] = {}  # 本地名 → argparse 里的名字
        self.pathlib: set[str] = set()
        self.path_names: set[str] = set()
        self.other_cli = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if alias.name == "argparse":
                        self.modules.add(alias.asname or "argparse")
                    elif alias.name == "pathlib":
                        self.pathlib.add(alias.asname or "pathlib")
                    elif top in _OTHER_CLI:
                        self.other_cli = True
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                mod = node.module or ""
                if mod == "argparse":
                    for alias in node.names:
                        self.direct[alias.asname or alias.name] = alias.name
                elif mod == "pathlib":
                    for alias in node.names:
                        if alias.name in ("Path", "PurePath", "PosixPath", "WindowsPath"):
                            self.path_names.add(alias.asname or alias.name)
                elif mod.split(".")[0] in _OTHER_CLI:
                    self.other_cli = True

    def argparse_name(self, node: ast.AST) -> str | None:
        """`argparse.X` / 导入过的 `X` → "X"；别的 → None。"""
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in self.modules:
                return node.attr
            return None
        if isinstance(node, ast.Name):
            return self.direct.get(node.id)
        return None

    def is_path_type(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.path_names
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in self.pathlib
            and node.attr in ("Path", "PurePath", "PosixPath", "WindowsPath")
        )

    @property
    def any(self) -> bool:
        return bool(self.modules or self.direct)


# ---------------------------------------------------------------- 主体


class _Parser:
    def __init__(self, name: str, line: int, node: ast.Call, names: _Names, sub: bool) -> None:
        self.name = name
        self.line = line
        self.sub = sub  # 子命令的 parser（`subparsers.add_parser(...)`）
        self.arguments: list[dict] = []
        self.groups: list[dict] = []
        self.reasons: set[str] = set()
        self.parse_calls: list[str] = []
        self.subcommands: dict | None = None
        for kw in node.keywords:
            if kw.arg is None:
                self.reasons.add("dynamic_value")
            elif kw.arg == "parents":
                self.reasons.add("parents")
            elif kw.arg == "prefix_chars":
                ok, value = _literal(kw.value)
                if not (ok and value == "-"):
                    self.reasons.add("prefix_chars")
            elif kw.arg == "fromfile_prefix_chars":
                ok, value = _literal(kw.value)
                if not (ok and value is None):
                    self.reasons.add("fromfile")


def _is_main_guard(test: ast.expr) -> bool:
    """恰好是 `__name__ == "__main__"` 或 `"__main__" == __name__`；`!=` / `is` / `in` / 链式比较都不是。"""
    if not (
        isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
    ):
        return False
    pair = (test.left, test.comparators[0])
    return any(
        isinstance(name, ast.Name)
        and name.id == "__name__"
        and isinstance(lit, ast.Constant)
        and lit.value == "__main__"
        for name, lit in (pair, pair[::-1])
    )


class _Scanner(ast.NodeVisitor):
    def __init__(self, names: _Names) -> None:
        self.names = names
        self.parsers: dict[str, _Parser] = {}  # 变量名 → parser（同名再赋一次 = multiple_parsers）
        self.order: list[_Parser] = []
        self.groups: dict[str, tuple[_Parser, dict | None]] = {}  # 组变量 → (parser, 互斥组 | None)
        self.subparsers: dict[str, _Parser] = {}  # add_subparsers() 的变量 → 父 parser
        self.reasons: set[str] = set()
        self.loop_depth = 0
        self.branch_depth = 0
        self.unresolved_calls: list[ast.Call] = []
        self.functions: dict[str, ast.FunctionDef] = {}
        self.reads_sys_argv = False

    # ---- 上下文 ----
    def _loop(self, node: ast.AST) -> None:
        self.loop_depth += 1
        self.generic_visit(node)
        self.loop_depth -= 1

    visit_For = visit_AsyncFor = visit_While = _loop
    visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = _loop

    def _branch(self, node: ast.AST) -> None:
        self.branch_depth += 1
        self.generic_visit(node)
        self.branch_depth -= 1

    def visit_If(self, node: ast.If) -> None:
        # `if __name__ == "__main__":` 不是条件分支——那就是脚本被 `python x.py` 跑时走的路
        is_main = _is_main_guard(node.test)
        if is_main:
            for stmt in node.body:
                self.visit(stmt)
            self.branch_depth += 1
            for stmt in node.orelse:
                self.visit(stmt)
            self.branch_depth -= 1
            self.visit(node.test)
            return
        self._branch(node)

    visit_Try = visit_Match = visit_IfExp = _branch
    visit_TryStar = _branch

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.setdefault(node.name, node)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "argv" and isinstance(node.value, ast.Name) and node.value.id == "sys":
            self.reads_sys_argv = True
        self.generic_visit(node)

    # ---- 赋值：parser / 组 / 子命令 ----
    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            return
        self._bind(node.targets[0].id, node.value, node.lineno)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.generic_visit(node)
        if isinstance(node.target, ast.Name) and node.value is not None:
            self._bind(node.target.id, node.value, node.lineno)

    def _bind(self, name: str, value: ast.AST, line: int) -> None:
        if not isinstance(value, ast.Call):
            return
        func = value.func
        if self.names.argparse_name(func) == "ArgumentParser":
            if name in self.parsers or self.loop_depth:
                self.reasons.add("multiple_parsers")
            parser = _Parser(name, line, value, self.names, sub=False)
            self.parsers[name] = parser
            self.order.append(parser)
            return
        if not isinstance(func, ast.Attribute):
            return
        owner = self._resolve(func.value)
        if func.attr == "add_parser" and isinstance(func.value, ast.Name):
            parent = self.subparsers.get(func.value.id)
            if parent is not None:
                sub = _Parser(name, line, value, self.names, sub=True)
                self.parsers[name] = sub
                return
        if owner is None:
            return
        parser, _group = owner
        if func.attr == "add_mutually_exclusive_group":
            self.groups[name] = (parser, self._new_group(parser, value))
        elif func.attr == "add_argument_group":
            self.groups[name] = (parser, None)
        elif func.attr == "add_subparsers":
            self.subparsers[name] = parser

    def _new_group(self, parser: _Parser, call: ast.Call) -> dict:
        required = False
        for kw in call.keywords:
            if kw.arg == "required":
                ok, value = _literal(kw.value)
                if ok and isinstance(value, bool):
                    required = value
                else:
                    parser.reasons.add("dynamic_value")
        group = {"id": f"g{len(parser.groups)}", "required": required, "members": []}
        parser.groups.append(group)
        return group

    def _resolve(self, node: ast.AST) -> tuple[_Parser, dict | None] | None:
        """`parser` / 组变量 / `parser.add_mutually_exclusive_group()`（链式）→ (parser, 互斥组)。"""
        if isinstance(node, ast.Name):
            if node.id in self.parsers:
                return self.parsers[node.id], None
            return self.groups.get(node.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            inner = self._resolve(node.func.value)
            if inner is None:
                return None
            parser = inner[0]
            if node.func.attr == "add_mutually_exclusive_group":
                return parser, self._new_group(parser, node)
            if node.func.attr == "add_argument_group":
                return parser, inner[1]
        return None

    # ---- 调用：add_argument / add_subparsers / parse_args ----
    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)
        func = node.func
        if not isinstance(func, ast.Attribute):
            return
        if func.attr == "add_argument":
            owner = self._resolve(func.value)
            if owner is not None:
                self._add_argument(owner[0], owner[1], node)
        elif func.attr == "add_subparsers":
            owner = self._resolve(func.value)
            if owner is not None:
                self._add_subparsers(owner[0], node)
        elif func.attr == "add_parser" and isinstance(func.value, ast.Name):
            parent = self.subparsers.get(func.value.id)
            if parent is not None and parent.subcommands is not None:
                ok, value = _literal(node.args[0]) if node.args else (False, None)
                names = parent.subcommands["choices"]
                if ok and isinstance(value, str):
                    if value not in names and len(names) < MAX_CHOICES:
                        names.append(value)
                else:
                    parent.subcommands["dynamic"] = True
        elif func.attr in _PARSE_CALLS:
            self._parse_call(node)

    def _add_subparsers(self, parser: _Parser, node: ast.Call) -> None:
        parser.reasons.add("subcommands")
        required = False
        dest = None
        for kw in node.keywords:
            ok, value = _literal(kw.value)
            if kw.arg == "required" and ok and isinstance(value, bool):
                required = value
            elif kw.arg == "dest" and ok and isinstance(value, str):
                dest = value
        if parser.subcommands is None:
            parser.subcommands = {
                "dest": dest,
                "required": required,
                "choices": [],
                "dynamic": False,
            }

    def _parse_call(self, node: ast.Call) -> None:
        receiver = node.func.value
        kind = node.func.attr
        parser = None
        if isinstance(receiver, ast.Name) and receiver.id in self.parsers:
            parser = self.parsers[receiver.id]
        elif isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Name):
            # `build_parser().parse_args()`：在同一模块里定义、`return` 一个 parser 变量的函数
            fn = self.functions.get(receiver.func.id)
            returned = _returned_name(fn) if fn is not None else None
            parser = self.parsers.get(returned) if returned else None
        if parser is None:
            if isinstance(receiver, (ast.Name, ast.Call)):
                self.unresolved_calls.append(node)
            return
        explicit = False
        if node.args:
            explicit = not _is_sys_argv_tail(node.args[0])
        for kw in node.keywords:
            if kw.arg == "args":
                ok, value = _literal(kw.value)
                if not (ok and value is None) and not _is_sys_argv_tail(kw.value):
                    explicit = True
            elif kw.arg is None:
                explicit = True
        if explicit:
            parser.reasons.add("explicit_parse_args")
        if "known" in kind:
            parser.reasons.add("parse_known_args")
        parser.parse_calls.append(kind)

    def _add_argument(self, parser: _Parser, group: dict | None, node: ast.Call) -> None:
        if self.loop_depth or any(isinstance(a, ast.Starred) for a in node.args):
            parser.reasons.add("dynamic_add_argument")
            return
        flags: list[str] = []
        for arg in node.args:
            ok, value = _literal(arg)
            if not ok or not isinstance(value, str) or not value:
                parser.reasons.add("dynamic_add_argument")
                return
            flags.append(value)
        if not flags or any(kw.arg is None for kw in node.keywords):
            parser.reasons.add("dynamic_add_argument")
            return
        if len(parser.arguments) >= MAX_ARGUMENTS:
            parser.reasons.add("too_many_arguments")
            return
        positional = not flags[0].startswith("-")
        if positional and len(flags) > 1:
            parser.reasons.add("dynamic_add_argument")  # argparse 自己会报错；这里不替它猜
            return
        if self.branch_depth:
            parser.reasons.add("conditional_argument")
        key = tuple(flags)
        if any(tuple(a["flags"]) == key for a in parser.arguments):
            return  # if / else 两支加了同一个参数：留第一份
        kws = {kw.arg: kw.value for kw in node.keywords}
        arg = _argument(flags, positional, kws, self.names, parser.reasons)
        arg["id"] = f"a{len(parser.arguments)}"
        arg["line"] = node.lineno
        arg["conditional"] = bool(self.branch_depth)
        if group is not None:
            arg["group"] = group["id"]
            group["members"].append(arg["id"])
        parser.arguments.append(arg)


def _returned_name(fn: ast.FunctionDef) -> str | None:
    names = {
        n.value.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Name)
    }
    return next(iter(names)) if len(names) == 1 else None


def _is_sys_argv_tail(node: ast.AST) -> bool:
    """`sys.argv[1:]`：与不传参数是同一件事。"""
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "argv"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "sys"
        and isinstance(node.slice, ast.Slice)
        and isinstance(node.slice.lower, ast.Constant)
        and node.slice.lower.value == 1
        and node.slice.upper is None
        and node.slice.step is None
    )


def _dest_of(flags: list[str], positional: bool) -> str:
    """argparse 的 dest 规则：位置参数就是名字；选项取第一个长选项（没有就第一个短选项），去前缀、`-` 换 `_`。"""
    if positional:
        return flags[0]
    long = next((f for f in flags if f.startswith("--") and len(f) > 2), None)
    chosen = long or flags[0]
    return chosen.lstrip("-").replace("-", "_")


def _argument(flags, positional, kws, names: _Names, reasons: set[str]) -> dict:
    out: dict = {
        "flags": list(flags),
        "positional": positional,
        "action": "store",
        "nargs": None,
        "required": False,
        "required_source": None,
        "default": None,
        "const": None,
        "choices": None,
        "choices_dynamic": False,
        "type": None,
        "role": None,
        "help": None,
        "hidden": False,
        "metavar": None,
        "group": None,
    }
    action = kws.get("action")
    if action is not None:
        ok, value = _literal(action)
        if ok and isinstance(value, str) and value in _KNOWN_ACTIONS:
            out["action"] = _KNOWN_ACTIONS[value]
        elif names.argparse_name(action) == "BooleanOptionalAction":
            out["action"] = "boolean_optional"
        else:
            out["action"] = "custom"
            reasons.add("custom_action")
    nargs = kws.get("nargs")
    if nargs is not None:
        ok, value = _literal(nargs)
        if ok and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out["nargs"] = value
        elif ok and value in ("?", "*", "+"):
            out["nargs"] = value
        elif (ok and value == "...") or names.argparse_name(nargs) == "REMAINDER":
            out["nargs"] = "..."
            reasons.add("remainder")
        else:
            name = names.argparse_name(nargs)
            mapped = {"OPTIONAL": "?", "ZERO_OR_MORE": "*", "ONE_OR_MORE": "+"}.get(name or "")
            if mapped:
                out["nargs"] = mapped
            else:
                out["nargs"] = "dynamic"
                reasons.add("dynamic_value")
    required = kws.get("required")
    if positional:
        out["required"] = out["nargs"] not in ("?", "*", "...")
        out["required_source"] = "positional"
    elif required is not None:
        ok, value = _literal(required)
        if ok and isinstance(value, bool):
            out["required"] = value
            out["required_source"] = "explicit"
        else:
            out["required"] = None
            reasons.add("dynamic_value")
    if "default" in kws:
        ok, value = _literal(kws["default"])
        if ok:
            out["default"] = {"kind": "literal", "display": _display(value)}
        elif names.argparse_name(kws["default"]) == "SUPPRESS":
            out["default"] = {"kind": "suppress"}
        else:
            out["default"] = {"kind": "dynamic"}
    if "const" in kws:
        ok, value = _literal(kws["const"])
        out["const"] = (
            {"kind": "literal", "display": _display(value)} if ok else {"kind": "dynamic"}
        )
    if "choices" in kws:
        ok, value = _literal(kws["choices"])
        if ok and isinstance(value, list) and value:
            out["choices"] = [_token_text(v) for v in value]
        else:
            out["choices_dynamic"] = True
    typ = kws.get("type")
    if typ is not None:
        out["type"], out["role"] = _type_of(typ, names)
    help_node = kws.get("help")
    if help_node is not None:
        ok, value = _literal(help_node)
        if ok and isinstance(value, str):
            out["help"] = _clip(value, MAX_HELP_CHARS)
        elif names.argparse_name(help_node) == "SUPPRESS":
            out["hidden"] = True
    meta = kws.get("metavar")
    if meta is not None:
        ok, value = _literal(meta)
        if ok and isinstance(value, str):
            out["metavar"] = _clip(value)
    dest = kws.get("dest")
    out["dest"] = _dest_of(flags, positional)
    if dest is not None:
        ok, value = _literal(dest)
        if ok and isinstance(value, str):
            out["dest"] = value
    out["arity"] = _arity(out)
    out["editable"] = _editable(out)
    return out


def _type_of(node: ast.AST, names: _Names) -> tuple[str, str | None]:
    if isinstance(node, ast.Name) and node.id in _SIMPLE_TYPES:
        return _SIMPLE_TYPES[node.id], None
    if names.is_path_type(node):
        return "path", None  # 是路径，但读还是写不知道：不给角色
    if isinstance(node, ast.Call) and names.argparse_name(node.func) == "FileType":
        mode_node = (
            node.args[0]
            if node.args
            else next((kw.value for kw in node.keywords if kw.arg == "mode"), None)
        )
        mode = "r"
        if mode_node is not None:
            ok, value = _literal(mode_node)
            if not ok or not isinstance(value, str):
                return "file", None
            mode = value
        writes = any(c in mode for c in "wax+")
        return ("file_write", "output_file") if writes else ("file_read", "input_file")
    return "custom", None


def _arity(arg: dict) -> int | str:
    """一次出现吃几个值 token：0 / 正整数 / "?" / "*" / "+" / "..." / "unknown"。"""
    action = arg["action"]
    if action in (
        "store_true",
        "store_false",
        "store_const",
        "append_const",
        "count",
        "help",
        "version",
    ):
        return 0
    if action == "boolean_optional":
        return 0
    if action == "custom":
        return "unknown"
    nargs = arg["nargs"]
    if nargs is None:
        return 1
    if nargs == "dynamic":
        return "unknown"
    return nargs


def _editable(arg: dict) -> bool:
    """表单能不能编辑这一项（不能的仍然展示，改用原始 token）。"""
    if arg["hidden"] or arg["action"] in ("custom", "help", "version", "count", "append_const"):
        return False
    arity = arg["arity"]
    if arg["positional"]:
        return isinstance(arity, int) and arity >= 1
    if arg["action"] in ("store_true", "store_false", "store_const", "boolean_optional"):
        return True
    # append / extend 的「每次出现一份」与 nargs ? * + 的可变长度，表单说不清就不碰：展示，改用原始 token
    return arg["action"] == "store" and isinstance(arity, int) and arity >= 1


# ---------------------------------------------------------------- 对外


def analyze(source: str) -> dict:
    """源码 → schema。纯函数，只做 `ast.parse`。"""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return _empty(STATUS_UNKNOWN, ["syntax_error"])
    names = _Names(tree)
    scanner = _Scanner(names)
    scanner.visit(tree)
    reasons: set[str] = set(scanner.reasons)
    if names.other_cli:
        reasons.add("other_cli")
    if not names.any or not scanner.order:
        info = sorted(r for r in reasons if r in ("other_cli",))
        if scanner.reads_sys_argv:
            info.append("reads_sys_argv")
        return _empty(STATUS_NONE, info)
    # 主 parser：有解析调用的那个；一个都没有就第一个（并如实标 no_parse_call）
    with_calls = [p for p in scanner.order if p.parse_calls]
    if len(with_calls) > 1:
        reasons.add("multiple_parsers")
    parser = with_calls[0] if with_calls else scanner.order[0]
    if not with_calls:
        reasons.add("unresolved_parse_call" if scanner.unresolved_calls else "no_parse_call")
    if len(scanner.order) > 1 and len(with_calls) != 1:
        reasons.add("multiple_parsers")
    reasons |= parser.reasons
    if scanner.reads_sys_argv and _reads_argv_outside_parse(source):
        reasons.add("reads_sys_argv")
    negative_like = any(
        f.startswith("-") and _looks_negative_number(f)
        for a in parser.arguments
        for f in a["flags"]
    )
    negative_like_extended = any(
        _looks_negative_number_extended(f)
        for a in parser.arguments
        for f in a["flags"]
        if f.startswith("-")
    )
    partial = bool(reasons)
    return {
        "version": SCHEMA_VERSION,
        "source": "static_ast",
        "framework": "argparse",
        "status": STATUS_PARTIAL if partial else STATUS_COMPLETE,
        "reasons": sorted(reasons),
        "form_enabled": not (reasons & FORM_BLOCKING),
        "parser_line": parser.line,
        "parse_call": parser.parse_calls[0] if parser.parse_calls else None,
        "negative_number_options": negative_like,
        # 3.14 起 argparse 的负数 token 文法更宽（`-1e3` / `-.5` / `-1.`）：声明了这类选项名的要单独标出
        "negative_number_options_extended": negative_like_extended,
        "arguments": parser.arguments,
        "exclusive_groups": [g for g in parser.groups if g["members"]],
        "subcommands": parser.subcommands,
    }


def _reads_argv_outside_parse(source: str) -> bool:
    """`sys.argv` 除了 `parse_args(sys.argv[1:])` 之外还被直接读过。"""
    tree = ast.parse(source)
    parse_args_tails = set()
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in _PARSE_CALLS
        ):
            for a in [*n.args, *(kw.value for kw in n.keywords)]:
                if isinstance(a, ast.Subscript) and _is_sys_argv_tail(a):
                    parse_args_tails.add(id(a.value))
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Attribute)
            and n.attr == "argv"
            and isinstance(n.value, ast.Name)
            and n.value.id == "sys"
        ):
            if id(n) in parse_args_tails:
                continue
            if isinstance(n.ctx, ast.Store):
                continue
            return True
    return False


#: argparse 的 `_negative_number_matcher`，按 CPython 源码逐字（`re.compile(...).match(token)`）：
#: * 3.13.13 / 3.12.12 / 3.11.14（本机实测 `argparse.ArgumentParser()._negative_number_matcher.pattern`）：
#:   `'^-\\d+$|^-\\d*\\.\\d+$'`
#: * 3.14.7：`'-\\.?\\d'`（`Lib/argparse.py` `ArgumentParser.__init__`；`-1e3` / `-.5` / `-1.` / `-1_0` / `-1j` 都算
#:   负数 token；没有结尾锚，`-1abc` 也算）
#: 前端 `web/src/lib/scriptArgsForm.ts` 的 `NEGATIVE_LEGACY` / `NEGATIVE_EXTENDED` 与这两条同形。
NEGATIVE_NUMBER_LEGACY = re.compile(r"^-\d+$|^-\d*\.\d+$")
NEGATIVE_NUMBER_EXTENDED = re.compile(r"-\.?\d")


def _looks_negative_number(text: str) -> bool:
    """3.14 之前的规则（`negative_number_options` 的判据；保持原字段语义）。"""
    return NEGATIVE_NUMBER_LEGACY.match(text) is not None


def _looks_negative_number_extended(text: str) -> bool:
    """3.14 起的规则。"""
    return NEGATIVE_NUMBER_EXTENDED.match(text) is not None


def _empty(status: str, reasons: list[str]) -> dict:
    return {
        "version": SCHEMA_VERSION,
        "source": "static_ast",
        "framework": None,
        "status": status,
        "reasons": sorted(set(reasons)),
        "form_enabled": False,
        "parser_line": None,
        "parse_call": None,
        "negative_number_options": False,
        "negative_number_options_extended": False,
        "arguments": [],
        "exclusive_groups": [],
        "subcommands": None,
    }


_cache: OrderedDict[tuple, dict] = OrderedDict()
_cache_lock = threading.Lock()


def analyze_file(path: str | os.PathLike) -> dict:
    """读一份脚本（有界）→ schema。按 (realpath, mtime_ns, size) 缓存：报告每次轮询都要它，不重复解析。"""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return _empty(STATUS_UNKNOWN, ["unreadable"])
    if st.st_size > MAX_SOURCE_BYTES:
        return _empty(STATUS_UNKNOWN, ["too_large"])
    key = (os.path.realpath(p), st.st_mtime_ns, st.st_size)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    try:
        with open(p, "rb") as fh:
            raw = fh.read(MAX_SOURCE_BYTES + 1)
    except OSError:
        return _empty(STATUS_UNKNOWN, ["unreadable"])
    if len(raw) > MAX_SOURCE_BYTES:
        return _empty(STATUS_UNKNOWN, ["too_large"])
    source = decode_source(raw)
    if source is None:
        # 编码声明坏了 / 声明的编码解不开：说不出话（unknown），不是「没有参数」，更不拿 U+FFFD 去猜 choices
        return _cache_put(key, _empty(STATUS_UNKNOWN, ["unreadable"]))
    return _cache_put(key, analyze(source))


def decode_source(raw: bytes) -> str | None:
    """按 Python 自己的源码编码规则解码（PEP 263 / PEP 3120：UTF-8 BOM、`# coding: xxx` 声明，默认 UTF-8）。
    声明坏了 / 解不开 → None（调用方按「未知」安全回退，不用 replacement 字符糊过去）。"""
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        return raw.decode(encoding)
    except (SyntaxError, LookupError, UnicodeDecodeError, ValueError):
        return None


def _cache_put(key: tuple, schema: dict) -> dict:
    with _cache_lock:
        _cache[key] = schema
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return schema


def source_revision(path: str | os.PathLike) -> str:
    """脚本的「源码修订」：字节摘要 + 解析出的 schema 摘要。准备计划把它记下来，认领 / 执行前再算一次——
    脚本在检查之后被改了（哪怕只是把一个普通参数改成 `FileType('w')`），当初披露的影响就不再是真的。
    不走 mtime 缓存的字节部分：这是安全门，宁可多读一次小文件。读不到 = `unreadable`（与检查时同样读不到则相等）。"""
    p = Path(path)
    digest = hashlib.sha256()
    try:
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return "unreadable"
    schema = json.dumps(_without_lines(analyze_file(p)), sort_keys=True, default=str).encode(
        "utf-8"
    )
    return f"{digest.hexdigest()}:{hashlib.sha256(schema).hexdigest()[:16]}"


def _without_lines(node):
    """行号不是披露的内容：在参数声明上方加一行注释不算「声明变了」。"""
    if isinstance(node, dict):
        return {k: _without_lines(v) for k, v in node.items() if k not in ("line", "parser_line")}
    if isinstance(node, list):
        return [_without_lines(v) for v in node]
    return node


def summary(schema: dict) -> dict:
    """会话检查项 / 影响摘要用的计数（不含参数名与 help 原文）。"""
    args = schema.get("arguments") or []
    return {
        "schema": schema.get("status"),
        "arguments": len(args),
        "required": sum(1 for a in args if a.get("required") is True),
        "output_files": sum(1 for a in args if a.get("role") == "output_file"),
        "form_enabled": bool(schema.get("form_enabled")),
    }
