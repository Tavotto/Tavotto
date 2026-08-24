"""静态扫描图库目录，起草 stem↔script 注册表（tavotto_registry.json 的数据来源）。

三件事：
  1. AST 识别入口：优先 main / render，其次任何「无必填参数且能走到存图调用」的
     顶层函数，最后是只有 `if __name__ == "__main__"` 的内联脚本
  2. 抽象求值 save()/savefig() 的路径参数——不再只认字符串字面量：
     模块级常量、f-string、`Path(...) / "x"`、`.with_suffix()` / `.with_name()` /
     `.joinpath()`、`os.path.join()`、`str.format()`、`%`、`+` 拼接、
     `Path(__file__)` 自命名，以及**跨函数传播**（`save_panel(fig, "Fig1")` →
     包装函数里的 `OUT / f"{stem}.pdf"`）与常量 for 循环展开都能还原。
     实在解不出的段落变成 `*`，再与磁盘上的产物比对还原成具体 stem。
  3. 同一 stem 被多个脚本认领 → 冲突显式报告，**绝不自动裁决**
     （裁决手写进 tavotto_registry.json；--write 合并时现有条目永远优先）

静态求不出来的（stem 来自命令行参数、数据文件、目录遍历……）列入
`dynamic_names` 报告，交给「试运行探测」（engine/probe.py）按真实产出登记——
绝不猜，也绝不静默跳过。cost 无法静态判断，草稿一律 "medium"。

用法：
    python -m tavotto.engine.discover <figures_dir>            # 只打印报告
    python -m tavotto.engine.discover <figures_dir> --write    # 生成/合并 tavotto_registry.json

纯标准库，Flask 父进程可安全 import。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath

from . import registry

OUT_EXTS = (".pdf", ".png", ".svg", ".jpg", ".jpeg", ".eps", ".tif", ".tiff")
# 样式模块及其副本（"paper_style 2.py"）、私有助手、测试与打包脚本
SKIP_PREFIXES = ("paper_style", "_", "test_", "conftest", "setup")
SKIP_SUFFIXES = ("_test.py",)
# 存图调用名。paper_style.save(fig, stem) 这类图库方言与 matplotlib 原生
# savefig 一起认；参数位置不假设，所有实参都试着求值。
SAVE_FUNCS = {"save", "savefig", "imsave", "write_image", "save_fig",
              "savefigure", "save_figure", "savefig_pdf", "export_fig"}
# 存图调用里可能承载文件名的关键字实参
SAVE_KWARGS = ("fname", "filename", "file", "path", "out", "outfile",
               "output", "stem", "name", "basename", "target")

# 扫描时整棵剪掉的目录（噪音 + 性能：图库旁边常年躺着工具产物与虚拟环境）
PRUNE_DIRS = {"__pycache__", "node_modules", ".venv", "venv", "env", ".git",
              "build", "dist", "site-packages", ".ipynb_checkpoints",
              ".rendered", ".mypy_cache", ".pytest_cache", ".tox", ".eggs",
              "tavottofile"}  # 项目内的 Tavotto 数据收纳目录，里面没有图表脚本
MAX_DEPTH = 4        # 图库目录层级：panels/、subfigs/ 这种一两层，给到四层
MAX_CALL_DEPTH = 6   # 跨函数传播的递归上限（防互递归与深调用链爆栈）

UNKNOWN = "*"


# --------------------------------------------------------------------------
# 路径字符串的小工具：抽象求值的结果统一是「以 / 分隔、可能含 * 的路径串」，
# 真正关心的只有最后一段（basename）——目录求不出来完全无所谓。
# --------------------------------------------------------------------------
def _norm(s: str) -> str:
    return s.replace("\\", "/")


def _join(left: str, right: str) -> str:
    """路径拼接。右侧绝对则整体替换（与 pathlib 的 `/` 语义一致）。"""
    right = _norm(right)
    if right.startswith("/") or (len(right) > 1 and right[1] == ":"):
        return right
    left = _norm(left).rstrip("/")
    return f"{left}/{right}" if left else right


def _basename(s: str) -> str:
    return _norm(s).rsplit("/", 1)[-1]


def _parent(s: str) -> str:
    s = _norm(s).rstrip("/")
    return s.rsplit("/", 1)[0] if "/" in s else ""


def _strip_ext(s: str) -> str:
    """只剥图片/PDF 扩展名——stem 之外的点（Fig1.v2）必须原样留着。"""
    low = s.lower()
    for ext in OUT_EXTS:
        if low.endswith(ext):
            return s[: -len(ext)]
    return s


def _split_suffix(s: str) -> tuple[str, str]:
    """pathlib 语义的 (无后缀部分, 后缀)——with_suffix/.stem 用，剥任意后缀。"""
    leaf = _basename(s)
    dot = leaf.rfind(".")
    if dot <= 0:
        return s, ""
    return s[: len(s) - (len(leaf) - dot)], leaf[dot:]


def _squeeze(s: str) -> str:
    """连续通配符压成一个，避免 `Fig_**_a` 这种匹配不上磁盘产物的模式。"""
    while "**" in s:
        s = s.replace("**", "*")
    return s


def _const_str(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _const_seq(node: ast.expr) -> list[str] | None:
    """字面量字符串序列（for 循环常量展开用）。"""
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return None
    out = []
    for el in node.elts:
        s = _const_str(el)
        if s is None:
            return None
        out.append(s)
    return out


def _func_name(node: ast.expr) -> str:
    """调用目标的「最后一段」名字：a.b.save → save；save → save。"""
    if isinstance(node, ast.Attribute):
        return node.attr
    return getattr(node, "id", "") or ""


# --------------------------------------------------------------------------
# 抽象求值器
# --------------------------------------------------------------------------
class _Analyzer:
    """单个脚本的抽象求值：从入口出发走语句，遇到存图调用就抽文件名模式。

    环境 env 是 {名字: 路径串}；解不出的名字直接不在环境里（区别于「解得出
    但内容未知」——后者是 `*`）。这个区分很重要：`save(fig, some_object)` 里
    的 `some_object` 不是路径，不该产出一个 `*` 模式去污染报告。
    """

    def __init__(self, tree: ast.Module, script_name: str):
        self.tree = tree
        self.script_name = script_name
        self.funcs: dict[str, ast.FunctionDef] = {
            n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.patterns: set[str] = set()
        self.sites: set[tuple[int, int]] = set()   # 走到过的存图调用位置
        self.module_env: dict[str, str] = {}
        self.seqs: dict[str, list[str]] = {}
        # 三元表达式另一分支的收集器：只在存图实参求值期间是个列表（_save_arg
        # 开、用完即关），其余时刻是 None——value() 在别的语境里也会被调用
        # （每条赋值、每次实参绑定），那些语境下的另一分支不是产物。
        self._alt_sink: list[str] | None = None

    # ---------------- 表达式 ----------------
    def value(self, node: ast.expr, env: dict[str, str]) -> str | None:
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.Name):
            return env.get(node.id)
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant):
                    parts.append(str(v.value))
                elif isinstance(v, ast.FormattedValue):
                    inner = self.value(v.value, env)
                    # 带格式说明（:0.2f / :03d）的插值即使拿到值也未必等于
                    # 最终文本，一律降级成通配符
                    parts.append(inner if inner is not None and not v.format_spec
                                 else UNKNOWN)
                else:
                    parts.append(UNKNOWN)
            return _squeeze("".join(parts))
        if isinstance(node, ast.BinOp):
            return self._binop(node, env)
        if isinstance(node, ast.Attribute):
            return self._attribute(node, env)
        if isinstance(node, ast.Call):
            return self._call_value(node, env)
        if isinstance(node, ast.IfExp):
            # 两个分支都可能发生：能求出的那个先用。另一分支**只在存图实参的
            # 求值里**才算产物（经 _alt_sink 交给 _save_arg 登记）——这里曾经
            # 无条件 _record_pattern(b)，而 value() 被每条赋值语句调用，于是
            # 任何与存图无关的三元字符串（numpy 的 dtype = "<f4" if … else
            # "<f8"、label、格式串）都会把 else 分支污染进注册表（issue #88）。
            a = self.value(node.body, env)
            b = self.value(node.orelse, env)
            if (self._alt_sink is not None and a is not None and b is not None
                    and a != b):
                self._alt_sink.append(b)
            return a if a is not None else b
        return None

    def _binop(self, node: ast.BinOp, env: dict[str, str]) -> str | None:
        if isinstance(node.op, ast.Div):
            # Path 除法：右侧必须解得出，左侧（目录）解不出无所谓
            right = self.value(node.right, env)
            if right is None:
                return None
            left = self.value(node.left, env)
            return _join(left or "", right)
        if isinstance(node.op, ast.Add):
            left, right = self.value(node.left, env), self.value(node.right, env)
            if left is None or right is None:
                return None
            return _squeeze(left + right)
        if isinstance(node.op, ast.Mod):
            # "Fig_%s.pdf" % name —— 求得出的操作数按序代入，其余变通配符
            left = self.value(node.left, env)
            if left is None:
                return None
            operands = (node.right.elts if isinstance(node.right, ast.Tuple)
                        else [node.right])
            vals = [self.value(o, env) for o in operands]
            it = iter(vals)

            def _sub(m: re.Match[str]) -> str:
                if m.group(0) == "%%":
                    return "%"
                val = next(it, None)
                # %s 之外的转换（%d/%.2f）即使拿到值也未必等于最终文本
                return val if val is not None and m.group(0).endswith("s") else UNKNOWN

            return _squeeze(re.sub(r"%%|%[-+ #0]*[\d.*]*[hlL]?[a-zA-Z]", _sub, left))
        return None

    def _attribute(self, node: ast.Attribute, env: dict[str, str]) -> str | None:
        if node.attr in ("parent", "stem", "name", "resolve", "absolute"):
            base = self.value(node.value, env)
            if base is None:
                return None
            if node.attr == "parent":
                return _parent(base)
            if node.attr == "stem":
                return _split_suffix(_basename(base))[0]
            if node.attr == "name":
                return _basename(base)
            return base
        return None

    def _call_value(self, node: ast.Call, env: dict[str, str]) -> str | None:
        name = _func_name(node.func)
        args = node.args

        if name in ("Path", "PurePath", "PosixPath", "WindowsPath"):
            vals = [self.value(a, env) for a in args]
            if not vals or vals[0] is None:
                return None
            out = vals[0]
            for v in vals[1:]:
                if v is None:
                    return None
                out = _join(out, v)
            return out
        if name in ("str", "fspath", "abspath", "realpath", "normpath",
                    "expanduser", "resolve", "absolute"):
            return self.value(args[0], env) if args else None
        if name == "join":                      # os.path.join(a, b, ...)
            vals = [self.value(a, env) for a in args]
            if not vals or any(v is None for v in vals):
                return None
            out = vals[0]
            for v in vals[1:]:
                out = _join(out, v)             # type: ignore[arg-type]
            return out
        if name == "joinpath":
            base = self.value(node.func.value, env) if isinstance(
                node.func, ast.Attribute) else None
            vals = [self.value(a, env) for a in args]
            if any(v is None for v in vals):
                return None
            out = base or ""
            for v in vals:
                out = _join(out, v)             # type: ignore[arg-type]
            return out
        if name in ("with_suffix", "with_name", "with_stem"):
            if not isinstance(node.func, ast.Attribute) or not args:
                return None
            base = self.value(node.func.value, env)
            arg = self.value(args[0], env)
            if base is None or arg is None:
                return None
            if name == "with_suffix":
                return _split_suffix(base)[0] + arg
            if name == "with_name":
                return _join(_parent(base), arg)
            return _join(_parent(base), arg + _split_suffix(base)[1])
        if name == "format":
            if not isinstance(node.func, ast.Attribute):
                return None
            base = self.value(node.func.value, env)
            if base is None:
                return None
            return self._apply_format(base, node, env)
        if name == "replace":
            if not isinstance(node.func, ast.Attribute) or len(args) < 2:
                return None
            base = self.value(node.func.value, env)
            old, new = self.value(args[0], env), self.value(args[1], env)
            if base is None or old is None or new is None:
                return None
            return _squeeze(base.replace(old, new))
        return None

    def _apply_format(self, tpl: str, node: ast.Call,
                      env: dict[str, str]) -> str:
        """"Fig_{}.pdf".format(x) —— 求得出的实参代入，求不出的变通配符。"""
        pos = [self.value(a, env) for a in node.args]
        kw = {k.arg: self.value(k.value, env) for k in node.keywords if k.arg}
        out, i, buf = [], 0, ""
        depth = 0
        for ch in tpl:
            if ch == "{":
                depth += 1
                if depth == 1:
                    buf = ""
                    continue
            if ch == "}" and depth:
                depth -= 1
                if depth == 0:
                    key = buf.split("!")[0].split(":")[0].strip()
                    if key.isdigit():
                        val = pos[int(key)] if int(key) < len(pos) else None
                    elif key:
                        val = kw.get(key)
                    else:
                        val = pos[i] if i < len(pos) else None
                        i += 1
                    out.append(val if val is not None and ":" not in buf
                               else UNKNOWN)
                    continue
            if depth:
                buf += ch
            else:
                out.append(ch)
        return _squeeze("".join(out))

    # ---------------- 语句 ----------------
    def _record_pattern(self, raw: str | None) -> None:
        if raw is None:
            return
        s = _strip_ext(_basename(raw)).strip()
        if not s or s.startswith(UNKNOWN):   # 开头即变量：无从定位
            return
        self.patterns.add(_squeeze(s))

    def _save_call(self, node: ast.Call, env: dict[str, str]) -> None:
        self.sites.add((node.lineno, node.col_offset))
        for arg in node.args:
            self._record_pattern(self._save_arg(arg, env))
        for kw in node.keywords:
            if kw.arg in SAVE_KWARGS:
                self._record_pattern(self._save_arg(kw.value, env))

    def _save_arg(self, node: ast.expr, env: dict[str, str]) -> str | None:
        """存图实参的求值：`savefig("a.pdf" if final else "b.pdf")` 两个分支
        都是真实产物，else 分支也一并登记。收集范围只有这一个语境——先赋给
        变量再传进来的三元只登记取值分支（另一分支交给试运行探测），换来的
        是赋值语句里的三元字符串绝不污染注册表。"""
        prev, self._alt_sink = self._alt_sink, []
        try:
            val = self.value(node, env)
            for alt in self._alt_sink:
                self._record_pattern(alt)
        finally:
            self._alt_sink = prev
        return val

    def _visit_call(self, node: ast.Call, env: dict[str, str],
                    depth: int, stack: tuple[str, ...]) -> None:
        name = _func_name(node.func)
        if name in SAVE_FUNCS:
            self._save_call(node, env)
            return
        # 用户自己的包装函数：把实参绑到形参上走进去（save_panel(fig, "Fig1")）
        fn = self.funcs.get(name) if isinstance(node.func, ast.Name) else None
        if fn is None or depth >= MAX_CALL_DEPTH or name in stack:
            return
        self.walk(fn.body, self._bind(fn, node, env), depth + 1, stack + (name,))

    def _bind(self, fn: ast.FunctionDef, call: ast.Call,
              env: dict[str, str]) -> dict[str, str]:
        """实参 → 形参环境（模块常量打底；解不出的形参就是「不在环境里」）。"""
        inner = dict(self.module_env)
        params = [a.arg for a in fn.args.args]
        for i, arg in enumerate(call.args):
            if i < len(params):
                val = self.value(arg, env)
                if val is not None:
                    inner[params[i]] = val
        for kw in call.keywords:
            if kw.arg:
                val = self.value(kw.value, env)
                if val is not None:
                    inner[kw.arg] = val
        # 有默认值的形参：调用方没传就用默认值
        defaults = fn.args.defaults
        for param, default in zip(params[len(params) - len(defaults):], defaults):
            if param not in inner:
                val = self.value(default, env)
                if val is not None:
                    inner[param] = val
        return inner

    def walk(self, stmts: list[ast.stmt], env: dict[str, str],
             depth: int = 0, stack: tuple[str, ...] = ()) -> None:
        """按语句结构走：复合语句显式下钻，简单语句整棵扫 Call。"""
        for st in stmts:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue                     # 定义不等于执行，被调用时才走
            if isinstance(st, ast.Assign):
                self._assign(st, env)
                self._scan_exprs([st.value], env, depth, stack)
                continue
            if isinstance(st, ast.AnnAssign) and st.value is not None:
                if isinstance(st.target, ast.Name):
                    val = self.value(st.value, env)
                    if val is not None:
                        env[st.target.id] = val
                self._scan_exprs([st.value], env, depth, stack)
                continue
            if isinstance(st, ast.For):
                self._for(st, env, depth, stack)
                continue
            if isinstance(st, (ast.If, ast.While)):
                self._scan_exprs([st.test], env, depth, stack)
                self.walk(st.body, dict(env), depth, stack)
                self.walk(st.orelse, dict(env), depth, stack)
                continue
            if isinstance(st, (ast.With, ast.AsyncWith)):
                self._scan_exprs([i.context_expr for i in st.items], env, depth, stack)
                self.walk(st.body, env, depth, stack)
                continue
            if isinstance(st, ast.Try):
                for block in (st.body, st.orelse, st.finalbody):
                    self.walk(block, dict(env), depth, stack)
                for handler in st.handlers:
                    self.walk(handler.body, dict(env), depth, stack)
                continue
            self._scan_exprs([st], env, depth, stack)

    def _assign(self, st: ast.Assign, env: dict[str, str]) -> None:
        val = self.value(st.value, env)
        seq = _const_seq(st.value)
        for target in st.targets:
            if not isinstance(target, ast.Name):
                continue
            if seq is not None:
                self.seqs[target.id] = seq
            if val is not None:
                env[target.id] = val
            else:
                env.pop(target.id, None)   # 重新赋成未知值，旧绑定必须失效

    def _for(self, st: ast.For, env: dict[str, str], depth: int,
             stack: tuple[str, ...]) -> None:
        """常量序列的 for 循环展开：`for k in ("a","b")` 每个取值各走一遍。"""
        self._scan_exprs([st.iter], env, depth, stack)
        seq = _const_seq(st.iter)
        if seq is None and isinstance(st.iter, ast.Name):
            seq = self.seqs.get(st.iter.id)
        if seq is not None and isinstance(st.target, ast.Name) and len(seq) <= 32:
            for item in seq:
                inner = dict(env)
                inner[st.target.id] = item
                self.walk(st.body, inner, depth, stack)
        else:
            inner = dict(env)
            for name in _target_names(st.target):
                inner.pop(name, None)      # 循环变量取值未知
            self.walk(st.body, inner, depth, stack)
        self.walk(st.orelse, dict(env), depth, stack)

    def _scan_exprs(self, nodes: list[ast.AST], env: dict[str, str],
                    depth: int, stack: tuple[str, ...]) -> None:
        for node in nodes:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    self._visit_call(sub, env, depth, stack)

    # ---------------- 入口 ----------------
    def run(self, entry: str) -> None:
        self.module_env = {"__file__": self.script_name}
        # 模块级常量（OUT = Path(__file__).parent / "panels" 这类）
        self.walk([s for s in self.tree.body
                   if isinstance(s, (ast.Assign, ast.AnnAssign))], self.module_env)
        if entry == "__main__":
            self.walk(self.tree.body, dict(self.module_env))
        else:
            fn = self.funcs.get(entry)
            if fn is not None:
                self.walk(fn.body, dict(self.module_env), 1, (entry,))
        if not self.sites:
            # 入口走不到存图（脚本在 import 期出图、或入口判断失手）：
            # 全模块兜底扫一遍，宁可宽松也不要交白卷
            self.walk(self.tree.body, dict(self.module_env))
            for fn in self.funcs.values():
                self.walk(fn.body, dict(self.module_env), 1, (fn.name,))


def _target_names(node: ast.expr) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [n for el in node.elts for n in _target_names(el)]
    return []


# --------------------------------------------------------------------------
# 入口方言识别
# --------------------------------------------------------------------------
def _reaches_save(fn: ast.FunctionDef, funcs: dict[str, ast.FunctionDef],
                  seen: frozenset[str] = frozenset()) -> bool:
    """函数体（含它调用的本模块函数）里是否存在存图调用。"""
    if fn.name in seen or len(seen) > MAX_CALL_DEPTH:
        return False
    seen = seen | {fn.name}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        name = _func_name(node.func)
        if name in SAVE_FUNCS:
            return True
        sub = funcs.get(name)
        if sub is not None and _reaches_save(sub, funcs, seen):
            return True
    return False


def _has_inline_main(tree: ast.Module) -> bool:
    for node in tree.body:
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"):
            return True
    return False


def _callable_without_args(fn: ast.FunctionDef) -> bool:
    """能否零参调用（worker 就是 `getattr(module, entry)()` 直接调的）。"""
    a = fn.args
    positional = len(a.posonlyargs) + len(a.args) - len(a.defaults)
    kwonly_required = sum(1 for d in a.kw_defaults if d is None)
    return positional <= 0 and kwonly_required == 0


def _entry_of(tree: ast.Module) -> str | None:
    """入口优先级：main > render > 其它无参且能走到存图的顶层函数 > __main__。

    第三档是为「按自己习惯命名入口」的图库准备的（plot() / build() /
    make_figures()）——worker 用 getattr(module, entry)() 调用，函数名本来就
    不必是 main。找不到任何函数时才退回内联脚本。
    """
    funcs = {n.name: n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for preferred in ("main", "render"):
        if preferred in funcs:
            return preferred
    inline = _has_inline_main(tree)
    candidates = [n for name, n in funcs.items()
                  if _callable_without_args(n) and _reaches_save(n, funcs)]
    if candidates:
        # 常见入口名先来，其余按定义顺序（越靠后越可能是「总装」函数）
        rank = {"plot": 0, "build": 1, "make": 2, "draw": 3, "run": 4,
                "figure": 5, "generate": 6, "all": 7}
        candidates.sort(key=lambda n: (rank.get(n.name, 99), n.lineno))
        return candidates[0].name
    return "__main__" if inline else None


# --------------------------------------------------------------------------
# 扫描
# --------------------------------------------------------------------------
def iter_scripts(figures_dir: Path) -> list[Path]:
    """图库里的候选脚本：递归但剪枝（隐藏目录、虚拟环境、缓存一律不下探）。

    只扫顶层曾经是个隐性假设——把面板脚本放 panels/ 子目录的图库（论文的
    supporting_information 就是）会被整目录漏掉。
    """
    out: list[Path] = []
    root = Path(figures_dir)

    def walk(d: Path, depth: int) -> None:
        try:
            children = sorted(d.iterdir())
        except OSError:
            return
        for child in children:
            if child.name.startswith("."):
                continue
            if child.is_dir():
                if depth < MAX_DEPTH and child.name not in PRUNE_DIRS:
                    walk(child, depth + 1)
                continue
            if child.suffix != ".py":
                continue
            if child.name.startswith(SKIP_PREFIXES) or child.name.endswith(SKIP_SUFFIXES):
                continue
            out.append(child)

    walk(root, 0)
    return out


def _rel_key(path: Path, root: Path) -> str:
    """注册表里的脚本键：图库相对路径，统一 POSIX 分隔符（跨平台一致）。"""
    try:
        return PurePosixPath(path.relative_to(root).as_posix()).as_posix()
    except ValueError:
        return path.name


def _resolve(patterns: set[str], figures_dir: Path) -> tuple[set[str], list[str]]:
    """模式 → 具体 stem。带 * 的与磁盘产物比对；无匹配进 unresolved。"""
    stems: set[str] = set()
    unresolved: list[str] = []
    for pat in sorted(patterns):
        if "*" not in pat:
            stems.add(pat)
            continue
        found = {p.stem for ext in OUT_EXTS
                 for p in figures_dir.rglob(pat + ext)}
        if found:
            stems |= found
        else:
            unresolved.append(pat)
    return stems, unresolved


def analyze_script(path: Path, figures_dir: Path) -> dict | None:
    """单个脚本 → 报告条目；不是绘图脚本（无入口 / 不存图）返回 None。"""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    entry = _entry_of(tree)
    if entry is None:
        # 顶层直写、既没有函数也没有 `if __name__` 守卫——AI 生成的
        # matplotlib 脚本最常见的形态之一：
        #
        #     import matplotlib.pyplot as plt
        #     fig, ax = plt.subplots(); ax.bar(...); fig.savefig("x.pdf")
        #
        # 以前这里直接判「不是绘图脚本」，于是整个脚本对 Tavotto 隐形，
        # 而它跑起来与 `python figure.py` 一模一样。**先按内联脚本试**，
        # 底下那道 `if not an.sites` 仍然会把真正不产图的模块挡掉
        # （工具/样式模块照旧返回 None），所以这里放宽不会引入误报。
        entry = registry.INLINE_ENTRY
    an = _Analyzer(tree, path.name)
    an.run(entry)
    if not an.sites:
        return None                       # 压根不产图（纯数据/工具模块）
    stems, unresolved = _resolve(an.patterns, figures_dir)
    return {"entry": entry, "stems": sorted(stems), "unresolved": unresolved,
            # 在存图却一个 stem 都定位不到：文件名完全来自运行期数据。
            # 这种脚本进不了草稿，必须报出来（可用「试运行探测」登记）。
            "dynamic_names": not stems,
            "save_calls": len(an.sites)}


def discover(figures_dir: str | Path) -> dict:
    """扫描图库（含子目录）里的候选脚本，返回原始报告。"""
    figures_dir = Path(figures_dir)
    scripts: dict[str, dict] = {}
    for p in iter_scripts(figures_dir):
        info = analyze_script(p, figures_dir)
        if info is not None:
            scripts[_rel_key(p, figures_dir)] = info
    claims: dict[str, list[str]] = {}
    for script, info in scripts.items():
        for s in info["stems"]:
            claims.setdefault(s, []).append(script)
    conflicts = {s: sorted(cs) for s, cs in claims.items() if len(cs) > 1}
    return {"scripts": scripts, "conflicts": conflicts}


def build_draft(figures_dir: str | Path) -> tuple[dict, dict]:
    """报告 → 注册表草稿。冲突 stem 不分配给任何脚本，留给人工裁决。"""
    rep = discover(figures_dir)
    cfg: dict[str, dict] = {}
    for script, info in sorted(rep["scripts"].items()):
        stems = [s for s in info["stems"] if s not in rep["conflicts"]]
        if stems:
            cfg[script] = {"entry": info["entry"], "cost": "medium",
                           "notes": "", "stems": stems}
    return {"version": 1, "scripts": cfg}, rep


def write_config(figures_dir: str | Path, cfg: dict) -> Path:
    """临时文件 + replace 原子落盘（同 `app._write_baked`）。

    直写会在进程被杀的那一刻把 `tavotto_registry.json` 截断成非法 JSON——桌面壳
    强退、OOM、断电，Windows 上杀毒软件写入期间短暂锁定也够。下次打开同一
    项目时 `registry.load()` 抛「注册表不是合法 JSON」，而**注册表随图库走**，
    坏掉的是用户目录里的文件，重装应用也修不回来。
    这是 tavotto_registry.json 唯一的写入函数，三条路径（首次打开起草、
    /api/registry/scan 合并、手工裁决登记）都经过它。
    """
    path = registry.registry_path(figures_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 临时文件名**每次调用都不同**。用固定的 `<name>.tmp` 的话，两个并发写
    # （Flask 是多线程的：/api/registry/scan 与 open_project 起草、MCP 那侧的
    # 登记都会走到这儿）会写同一个路径：先 replace 的那个把它搬走，后一个
    # 的 replace 直接 FileNotFoundError；更坏的情况是两份内容交错，读者拿到
    # 一个谁也没打算写出来的文件——原子写反倒成了摆设。
    # 同目录是硬要求：跨设备的 replace 不是原子操作。
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                    prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(cfg, ensure_ascii=False, indent=1))
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)             # 半成品不留在用户的图库目录里
        raise
    return path


def merge(figures_dir: str | Path) -> tuple[dict, dict, dict]:
    """草稿并入现有注册表：现有条目原样保留，只追加新脚本与未登记的 stem。

    返回 (合并后的配置, 原始报告, 变更摘要)。
    """
    draft, rep = build_draft(figures_dir)
    # 读旧名那份也算数（write_config 仍只写新名，合并结果因此自然完成搬迁）。
    path = registry.existing_registry_path(figures_dir)
    if path is None:
        changes = {"added_scripts": sorted(draft["scripts"]), "added_stems": {}}
        return draft, rep, changes

    merged = json.loads(path.read_text(encoding="utf-8"))
    # **先按 registry 的那把尺校验，再动手合并。**
    # 这里以前直接 `scripts.values()`：`{"scripts": "x"}` 这种结构合法但类型
    # 错的注册表（用户手写/误改就够）会抛 AttributeError——既不是 ValueError
    # 也不是 OSError，穿透 handoff 的 try/except、cli() 的 HandoffError 捕获与
    # 所有外层，`tavotto open --json` 于是吐一段 traceback 而不是那行契约里的
    # JSON，调用方的分诊逻辑当场失灵。
    # 复用 `Registry.load_data` 而不是另写一份校验：两份迟早分叉，而分叉的
    # 表现是「open 说没问题、打开项目却报注册表非法」。用**新实例**校验，
    # 不碰模块级默认实例的状态。
    if not isinstance(merged, dict):
        raise ValueError(f"{path}: 注册表顶层必须是对象")
    try:
        registry.Registry().load_data(merged, source=str(path))
    except RuntimeError as exc:                      # 结构/类型不对
        raise ValueError(str(exc)) from exc
    scripts = merged.setdefault("scripts", {})
    registered = {s for c in scripts.values() for s in c.get("stems", [])}
    added_scripts: list[str] = []
    added_stems: dict[str, list[str]] = {}
    for script, cfg_s in draft["scripts"].items():
        fresh = [s for s in cfg_s["stems"] if s not in registered]
        if not fresh:
            continue
        registered.update(fresh)
        if script in scripts:
            scripts[script]["stems"] = list(scripts[script].get("stems", [])) + fresh
            added_stems[script] = fresh
        else:
            scripts[script] = {**cfg_s, "stems": fresh}
            added_scripts.append(script)
    return merged, rep, {"added_scripts": added_scripts, "added_stems": added_stems}


def register(figures_dir: str | Path, script: str, stems: list[str],
             entry: str = "main", cost: str = "medium",
             notes: str = "") -> dict:
    """把一个脚本的 stem 归属写进注册表（试运行探测确认后调用）。

    同名脚本整条替换（探测结果是权威的），其它脚本里被本次认领走的 stem
    一并摘掉——否则 registry.load 会因重复 stem 直接报错。
    """
    path = registry.existing_registry_path(figures_dir)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        cfg = {}
    if not cfg:
        cfg = {"version": 1, "scripts": {}}
    scripts = cfg.setdefault("scripts", {})
    claimed = set(stems)
    for name, entry_cfg in list(scripts.items()):
        if name == script or not isinstance(entry_cfg, dict):
            continue
        kept = [s for s in entry_cfg.get("stems", []) if s not in claimed]
        if len(kept) != len(entry_cfg.get("stems", [])):
            entry_cfg["stems"] = kept
    if stems:
        prev = scripts.get(script) if isinstance(scripts.get(script), dict) else {}
        scripts[script] = {"entry": entry, "cost": cost or prev.get("cost", "medium"),
                           "notes": notes or prev.get("notes", ""),
                           "stems": sorted(claimed)}
    else:
        scripts.pop(script, None)
    write_config(figures_dir, cfg)
    return cfg


def _print_report(rep: dict) -> None:
    for script, info in sorted(rep["scripts"].items()):
        print(f"  {script}  [{info['entry']}]  {len(info['stems'])} stems")
        for pat in info["unresolved"]:
            print(f"    ? 无法与磁盘产物对上: {pat}*")
        if info.get("dynamic_names"):
            print(f"    ! {info['save_calls']} 处 save/savefig 的文件名来自运行期数据，"
                  "静态定位不到 stem —— 用「试运行探测」按真实产出登记"
                  "（tavotto 设置 → 脚本注册表，或手工写 tavotto_registry.json）")
    if rep["conflicts"]:
        print("  ⚠ 归属冲突（未分配，请在 tavotto_registry.json 手工裁决）:")
        for stem, cs in sorted(rep["conflicts"].items()):
            print(f"    {stem}: {' vs '.join(cs)}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("figures_dir")
    ap.add_argument("--write", action="store_true",
                    help="生成/合并 tavotto_registry.json（现有条目优先）")
    args = ap.parse_args(argv)
    if not Path(args.figures_dir).is_dir():
        raise SystemExit(f"目录不存在: {args.figures_dir}")

    if args.write:
        cfg, rep, changes = merge(args.figures_dir)
        path = write_config(args.figures_dir, cfg)
        print(f"已写入 {path}")
        _print_report(rep)
        if changes["added_scripts"]:
            print(f"  + 新脚本: {', '.join(changes['added_scripts'])}")
        for script, stems in changes["added_stems"].items():
            print(f"  + {script} 新增 stems: {', '.join(stems)}")
        if not changes["added_scripts"] and not changes["added_stems"]:
            print("  （无新增，现有注册表未改动）")
    else:
        draft, rep = build_draft(args.figures_dir)
        _print_report(rep)
        n = sum(len(c["stems"]) for c in draft["scripts"].values())
        print(f"  草稿共 {len(draft['scripts'])} 个脚本 / {n} 个 stem"
              f"（--write 落盘；cost 默认 medium 请按需修正）")


if __name__ == "__main__":
    main()
