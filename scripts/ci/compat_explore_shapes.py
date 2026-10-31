"""CG-08 脚本写法与输出写法变换：入口形态（保型）与输出调用（改轴）。

候选的权威不变：仍然是 `父本 + 变换链 + seed`，本模块的变换登记在 `compat_explore_transforms.TRANSFORMS`，
全部是 AST 编辑，结果必须能 `compile`，参数由 `_rng(seed, 父本规范化 AST 签名, 变换名)` 派生。

入口形态（family `entry_form`，保型：原生图数与标题必须等于父本，数据指纹与可编辑字段由 CG-06/07 的结构层比）
* `class_wrapper`：绘图代码移进 `class Plotter: def run(self)`，入口调用它（轴 entry，与 `wrap_main` 互斥）；
* `return_fig`：savefig 之前的绘图语句移进 `_cg_draw()` 并 `return fig`，调用方 savefig（轴 entry）；
* `sys_argv_out`：保存路径来自 `sys.argv[1] if len(sys.argv) > 1 else <原路径>`（轴 args，与 `argparse_out` 互斥）；
* `cell_markers`：在模块级语句之间插入 `# %%` / `# %% [markdown]` 注释块，模拟 notebook 导出的 .py（轴 cells）。
  注释不在 AST 里：变换先在树里放**哨兵语句**（一个独占一行的裸名字 `CG_CELL_CODE` / `CG_CELL_MD_<n>`），
  `ast.unparse` 之后由 `finalize_source` 把**整行等于哨兵**的行换成注释行。它是序列化的一部分，不是对
  已有源码的字符串替换；注释不进规范化 AST 签名。

输出调用（family `output_call`，改轴：只能与自己的原生比；轴 output_call，与 show_only / show_close 互斥）
* `multi_format`（同一 stem 存 png + pdf + svg）、`pdfpages`（`PdfPages` 多页）、`bytesio_close`（savefig 到
  `io.BytesIO` 后 close，#749）、`same_stem_two_figs`（两张不同的图写同一 stem 不同格式，后写的那张随后被 close）、
  `bbox_tight_dpi`（`bbox_inches="tight", dpi=600`）。

新增 import（io / sys / matplotlib.backends.backend_pdf）都不在 `_UNCERTAIN_IMPORTS` 里，也没有放宽那张表。
这个模块只在 explore 进程里运行，不碰产品代码。
"""

from __future__ import annotations

import ast
import copy
import os
from dataclasses import dataclass

CODE_MARK = "CG_CELL_CODE"
MD_MARK = "CG_CELL_MD_"


def _xt():
    import compat_explore_transforms as XT

    return XT


# ---------------------------------------------------------------- serialization of cell markers


def finalize_source(source: str) -> str:
    """把独占一行的哨兵换成 notebook 单元注释；其余各行原样保留。"""
    out: list[str] = []
    for line in source.split("\n"):
        if line == CODE_MARK:
            out.append("# %%")
        elif line.startswith(MD_MARK) and line[len(MD_MARK) :].isdigit():
            out += ["# %% [markdown]", f"# ## Step {line[len(MD_MARK) :]}", "# %%"]
        else:
            out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------- shared helpers


def _names(nodes, ctx_type) -> set[str]:
    found: set[str] = set()
    for node in nodes:
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ctx_type):
                found.add(n.id)
            elif ctx_type is ast.Store and isinstance(n, (ast.Import, ast.ImportFrom)):
                found.update((a.asname or a.name).split(".")[0] for a in n.names)
    return found


def _entry_function(ctx):
    return next(
        (n for n in ctx.tree.body if isinstance(n, ast.FunctionDef) and n.name == ctx.entry), None
    )


def _leaks(ctx, inside: list, allow: frozenset = frozenset()) -> bool:
    """`inside` 里赋值的名字，是否被它**外面**的代码读到（搬进函数后会变成局部、外面读不到）。

    入口是函数时，只有该函数体内的其它语句看得到它的局部；入口是 `__main__` 时是整个模块。
    """
    scope = ctx.tree if ctx.entry == "__main__" else _entry_function(ctx)
    if scope is None:
        return True
    skip = {id(n) for s in inside for n in ast.walk(s)}
    outside = {
        n.id
        for n in ast.walk(scope)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and id(n) not in skip
    }
    return bool((_names(inside, ast.Store) & outside) - allow)


def _movable(nodes) -> bool:
    """能原样搬进函数体：没有 return / yield / global / nonlocal / 通配 import。"""
    for node in nodes:
        for n in ast.walk(node):
            if isinstance(n, (ast.Return, ast.Yield, ast.YieldFrom, ast.Global, ast.Nonlocal)):
                return False
            if isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names):
                return False
    return True


def _receiver(ctx, call) -> tuple[str, str] | None:
    """(接收者名字, 取到这张图的表达式)；接收者不是名字就不碰（重新求值可能新建一张图）。"""
    value = call.func.value
    if not isinstance(value, ast.Name):
        return None
    alias = ctx.alias()
    return (value.id, f"{alias}.gcf()" if value.id == alias else value.id)


def _plain(call) -> bool:
    return len(call.args) == 1 and not any(k.arg in (None, "format") for k in call.keywords)


def _sites(ctx) -> list[tuple[list, int, ast.Call]]:
    """区域里**直接**的 `X.savefig(...)` 语句：(owner, 下标, 调用)。"""
    region = ctx.region()
    if region is None or ctx.alias() is None:
        return []
    XT = _xt()
    owner, start, end = region
    found = []
    for i in range(start, end):
        call = XT._savefig_call(owner[i])
        if call is not None and _receiver(ctx, call) is not None:
            found.append((owner, i, call))
    return found


def _literal_root(call) -> str | None:
    """字面量路径去掉扩展名后的部分；没有字面量 / 没有扩展名 / 带 format 都不适用。"""
    if not _plain(call):
        return None
    arg = call.args[0]
    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
        return None
    root, ext = os.path.splitext(arg.value)
    return root if root and ext else None


def _first_literal_site(ctx):
    return next((s for s in _sites(ctx) if _literal_root(s[2]) is not None), None)


def _stmts(source: str) -> list:
    return ast.parse(source).body


def _savefig_to(stmt, target: str | None, fig_expr: str, **kwargs):
    """复制一条 savefig 语句：换接收者与第一个实参，附加关键字。"""
    new = copy.deepcopy(stmt)
    call = new.value
    if target is not None:
        call.func.value = ast.parse(target, mode="eval").body
    call.args[0] = ast.parse(fig_expr, mode="eval").body
    for key, value in kwargs.items():
        call.keywords = [k for k in call.keywords if k.arg != key]
        call.keywords.append(ast.keyword(arg=key, value=ast.Constant(value)))
    return new


# ---------------------------------------------------------------- entry_form (shape preserving)


def _applies_class_wrapper(ctx) -> bool:
    region = ctx.region()
    if region is None:
        return False
    owner, start, end = region
    inside = owner[start:end]
    return bool(inside) and _movable(inside) and not _leaks(ctx, inside)


def _apply_class_wrapper(ctx, rng) -> None:
    owner, start, end = ctx.region()
    cls = _stmts("class Plotter:\n    def run(self):\n        pass")[0]
    cls.body[0].body = owner[start:end]
    owner[start:end] = [cls, *_stmts("Plotter().run()")]


def _return_fig_site(ctx):
    region = ctx.region()
    if region is None or ctx.alias() is None:
        return None
    owner, start, end = region
    for owner_, i, call in _sites(ctx):
        head = owner[start:i]
        if not head or not _movable(head):
            continue
        name, expr = _receiver(ctx, call)
        allowed = {name} if name != ctx.alias() else set()
        if name != ctx.alias() and name not in _names(head, ast.Store):
            continue
        if _leaks(ctx, head, frozenset(allowed)):
            continue
        return owner, start, i, call, name, expr
    return None


def _apply_return_fig(ctx, rng) -> None:
    owner, start, i, call, name, expr = _return_fig_site(ctx)
    func = _stmts("def _cg_draw():\n    pass")[0]
    func.body = [*owner[start:i], *_stmts(f"return {expr}")]
    if name == ctx.alias():
        call.func.value = ast.Name(id="_cg_fig", ctx=ast.Load())
        bind = "_cg_fig = _cg_draw()"
    else:
        bind = f"{name} = _cg_draw()"
    owner[start:i] = [func, *_stmts(bind)]
    ctx.params["figure"] = name if name != ctx.alias() else "gcf"


def _applies_sys_argv(ctx) -> bool:
    return ctx.region() is not None and _xt()._first_savefig_literal(ctx) is not None


def _apply_sys_argv(ctx, rng) -> None:
    call = _xt()._first_savefig_literal(ctx)
    default = call.args[0].value
    call.args[0] = ast.parse(
        f"_cg_sys.argv[1] if len(_cg_sys.argv) > 1 else {default!r}", mode="eval"
    ).body
    _xt()._insert_import(ctx.tree, "import sys as _cg_sys")


def _gaps(ctx) -> list[int]:
    """模块级可以放单元标记的位置：语句之前（docstring 之前不放，以免它不再是 docstring）。"""
    XT = _xt()
    body = ctx.tree.body
    first = 1 if body and XT._is_docstring(body[0]) else 0
    return list(range(first, len(body)))


def _applies_cell_markers(ctx) -> bool:
    return len(_gaps(ctx)) >= 2


def _apply_cell_markers(ctx, rng) -> None:
    gaps = _gaps(ctx)
    count = min(len(gaps), rng.choice((2, 3, 4)))
    at = sorted(rng.sample(gaps, count))
    kinds = [rng.choice(("code", "markdown")) for _ in at]
    for n, (pos, kind) in enumerate(reversed(list(zip(at, kinds, strict=True)))):
        name = CODE_MARK if kind == "code" else f"{MD_MARK}{count - n}"
        ctx.tree.body.insert(pos, ast.Expr(value=ast.Name(id=name, ctx=ast.Load())))
    ctx.params["cells"] = [
        {"before_statement": p, "kind": k} for p, k in zip(at, kinds, strict=True)
    ]


# ---------------------------------------------------------------- output_call (axis changing)


def _applies_literal_site(ctx) -> bool:
    return _first_literal_site(ctx) is not None


def _apply_multi_format(ctx, rng) -> None:
    owner, i, call = _first_literal_site(ctx)
    root = _literal_root(call)
    formats = rng.sample(("png", "pdf", "svg"), 3)
    owner[i : i + 1] = [_savefig_to(owner[i], None, repr(f"{root}.{fmt}")) for fmt in formats]
    ctx.params.update({"root": root, "formats": formats})


def _apply_pdfpages(ctx, rng) -> None:
    owner, i, call = _first_literal_site(ctx)
    root = _literal_root(call)
    pages = rng.choice((2, 3))
    _name, fig = _receiver(ctx, call)
    block = _stmts(f"with _cg_PdfPages({root + '.pdf'!r}) as _cg_pdf:\n    pass")[0]
    block.body = [_savefig_to(owner[i], "_cg_pdf", fig) for _ in range(pages)]
    owner[i : i + 1] = [block]
    _xt()._insert_import(
        ctx.tree, "from matplotlib.backends.backend_pdf import PdfPages as _cg_PdfPages"
    )
    ctx.params.update({"root": root, "pages": pages})


def _applies_plain_site(ctx) -> bool:
    return any(_plain(c) for _o, _i, c in _sites(ctx))


def _apply_bytesio(ctx, rng) -> None:
    owner, i, call = next(s for s in _sites(ctx) if _plain(s[2]))
    fmt = rng.choice(("png", "svg", "pdf"))
    _name, fig = _receiver(ctx, call)
    owner[i : i + 1] = [
        *_stmts("_cg_buf = _cg_io.BytesIO()"),
        _savefig_to(owner[i], None, "_cg_buf", format=fmt),
        *_stmts(f"{ctx.alias()}.close({fig})"),
    ]
    _xt()._insert_import(ctx.tree, "import io as _cg_io")
    ctx.params["format"] = fmt


def _apply_same_stem(ctx, rng) -> None:
    owner, i, call = _first_literal_site(ctx)
    root = _literal_root(call)
    ext = os.path.splitext(call.args[0].value)[1]
    other = ".png" if ext != ".png" else rng.choice((".pdf", ".svg"))
    alias = ctx.alias()
    owner[i + 1 : i + 1] = _stmts(
        f"_cg_fig2, _cg_ax2 = {alias}.subplots()\n"
        "_cg_ax2.plot([0, 1], [1, 0])\n"
        "_cg_ax2.set_title('cg_second')\n"
        f"_cg_fig2.savefig({root + other!r})\n"
        f"{alias}.close(_cg_fig2)"
    )
    ctx.params.update({"root": root, "first": ext, "second": other})


_TIGHT = {"bbox_inches": "tight", "dpi": 600}


def _applies_tight(ctx) -> bool:
    for _o, _i, call in _sites(ctx):
        have = {
            k.arg: k.value.value
            for k in call.keywords
            if isinstance(k.value, ast.Constant) and k.arg in _TIGHT
        }
        if have != _TIGHT:
            return True
    return False


def _apply_tight(ctx, rng) -> None:
    for _owner, _i, call in _sites(ctx):
        call.keywords = [k for k in call.keywords if k.arg not in _TIGHT]
        call.keywords += [ast.keyword(arg=k, value=ast.Constant(v)) for k, v in _TIGHT.items()]
    ctx.params.update(_TIGHT)


@dataclass(frozen=True)
class Spec:
    name: str
    family: str
    axis: str
    preserving: bool
    order: int
    applies: object
    apply: object
    summary: str


#: order：入口形态里 class_wrapper / return_fig 排在输出变换**之后**、import_alias 之前——它们要把前面各步
#: 改出来的区域整体搬进函数；cell_markers 最后（只是序列化时的注释）。
SPECS = (
    Spec(
        "class_wrapper",
        "entry_form",
        "entry",
        True,
        80,
        _applies_class_wrapper,
        _apply_class_wrapper,
        "move the drawing code into class Plotter: def run(self) and call it from the entry",
    ),
    Spec(
        "return_fig",
        "entry_form",
        "entry",
        True,
        81,
        lambda c: _return_fig_site(c) is not None,
        _apply_return_fig,
        "draw inside _cg_draw() that returns the figure; the caller saves it",
    ),
    Spec(
        "sys_argv_out",
        "entry_form",
        "args",
        True,
        31,
        _applies_sys_argv,
        _apply_sys_argv,
        "take the savefig path from sys.argv[1] when given, else the original path",
    ),
    Spec(
        "cell_markers",
        "entry_form",
        "cells",
        True,
        99,
        _applies_cell_markers,
        _apply_cell_markers,
        "insert # %% and # %% [markdown] cell comments between module-level statements",
    ),
    Spec(
        "multi_format",
        "output_call",
        "output_call",
        False,
        62,
        _applies_literal_site,
        _apply_multi_format,
        "save the same figure to one stem as png + pdf + svg",
    ),
    Spec(
        "pdfpages",
        "output_call",
        "output_call",
        False,
        63,
        _applies_literal_site,
        _apply_pdfpages,
        "save through matplotlib.backends.backend_pdf.PdfPages with several pages",
    ),
    Spec(
        "bytesio_close",
        "output_call",
        "output_call",
        False,
        64,
        _applies_plain_site,
        _apply_bytesio,
        "savefig into io.BytesIO, then close the figure (#749)",
    ),
    Spec(
        "same_stem_two_figs",
        "output_call",
        "output_call",
        False,
        65,
        _applies_literal_site,
        _apply_same_stem,
        "a second, different figure written to the same stem in another format, then closed",
    ),
    Spec(
        "bbox_tight_dpi",
        "output_call",
        "output_call",
        False,
        66,
        _applies_tight,
        _apply_tight,
        'savefig(..., bbox_inches="tight", dpi=600)',
    ),
)
