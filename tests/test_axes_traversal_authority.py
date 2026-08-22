"""「哪些 axes 存在」这个判断只能有一处出处（不变式 5 的机械化那一半）。

`ax.inset_axes()` 与 `ax.secondary_[xy]axis()` 建出来的 axes 挂在
`ax.child_axes` 上，**`in fig.axes` 为 False**。`manifest._ordered_axes` 是
把它们收进来的唯一权威（并且给出稳定的 `axes_i` 编号）。

一天之内，同一条判断在**五个**地方各被漏掉一次：

    1. `manifest.census`            插图里的 artist 在普查报告里一个字都不出现
    2. `manifest._internal_ids`     插图的结构件反过来被报成「漏掉了」
    3. `scripts/dev/...census.py`   工具自己抄了一份 `_internal_ids` 与遍历
    4. `overrides.colorbar_maps`    插图上的色条整个不被认出来，内部件泄漏进元素表
    5. `overrides.follow_map`       ↑ 修好之后，随行关系又被无声丢掉
    6. `overrides.FigState.resolve` 插图的刻度文字 gid 越界 → 「元素不存在」→ 阻断写回

第 5 条尤其说明问题：它是**第 4 条修好之后才够得着的**——色条先要被认出来，
那条关系才有机会被丢。逐个修下去只会一直有下一个。

所以这条用例不看行为，看**源码**：`fig.axes` 在引擎里只许出现在下面这张表
列出的地方。它是纯 `ast` 解析，不 import matplotlib，所以在任何环境里都跑得
起来、也快。

**这不是风格检查。** 这条判断错一次的代价已经量过：override 挂在每帧被重建的
幽灵上、写回被一条「元素不存在」阻断、拖动宿主时色条留在原地。
"""
from __future__ import annotations

import ast
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(REPO, "src", "tavotto", "engine")

#: 允许出现 `fig.axes` 的地方 —— **(文件, 函数)**，每条都要说得出理由。
#:
#: 往里加之前先问：这个函数需要的是「figure 上所有 axes」还是「matplotlib 记在
#: `fig.axes` 里的那些」？除了 `_ordered_axes` 自己，答案几乎总是前者。
#:
#: **表里只剩一条是有意的。** `colorbar_maps` / `follow_map` 一度带着
#: `axes=None → fig.axes` 的兜底，于是这张表得按**函数**放行它们整个函数体
#: ——而实测：把函数里另一处改回 `fig.axes`，这条看护照样绿。放行整函数的豁免
#: 挡不住函数内部的回归，所以那两个兜底被删掉了（`axes` 改成必填），
#: 而不是把豁免写得更细。**能删掉豁免就别把豁免写精细。**
_ALLOWED = {
    ("manifest.py", "_ordered_axes"):
        "遍历权威本身：它就是那个把 fig.axes 与 child_axes 合起来的函数",
}


def _fig_axes_sites(path: str) -> list[tuple[str, int]]:
    """(所在函数, 行号) —— 源码里每一处 `<something>.fig.axes` / `fig.axes`。

    走 `ast` 而不是 grep：注释与 docstring 里提到 `fig.axes` 是**在讲这件事**
    （本仓库的注释密度下这类提及很多），拿正则去数会把说明文字当成违规。
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    sites: list[tuple[str, int]] = []
    stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):          # noqa: N802
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

        def visit_Attribute(self, node):            # noqa: N802
            if node.attr == "axes":
                base = node.value
                name = getattr(base, "id", None) or getattr(base, "attr", None)
                if name == "fig":
                    sites.append((stack[-1] if stack else "<module>", node.lineno))
            self.generic_visit(node)

    Visitor().visit(tree)
    return sites


@pytest.mark.parametrize("fname", ["manifest.py", "overrides.py"])
def test_fig_axes_only_where_it_is_allowed(fname):
    """引擎里的 `fig.axes` 只许出现在 `_ALLOWED` 那几处。"""
    offenders = [
        f"{fname}:{lineno} 在 {func}()"
        for func, lineno in _fig_axes_sites(os.path.join(ENGINE, fname))
        if (fname, func) not in _ALLOWED
    ]
    assert not offenders, (
        "这里要的多半是**figure 上所有的 axes**，而 `fig.axes` 里没有 "
        "`inset_axes` / `secondary_[xy]axis` 建出来的那些。改用 "
        "`manifest._ordered_axes(fig)[0]`（或由调用方传进来），别在这里再抄一遍"
        "遍历：\n  " + "\n  ".join(offenders))


def test_the_allowlist_has_no_dead_entries():
    """豁免表不许留着已经不存在的条目。

    一条指向不存在位置的豁免，读起来像「这里有个有据可查的例外」，实际什么都
    没豁免——与本轮反复在收的那种空门禁是同一个形状，只是长在豁免表里。
    """
    live = {(f, func)
            for f in ("manifest.py", "overrides.py")
            for func, _ in _fig_axes_sites(os.path.join(ENGINE, f))}
    dead = sorted(k for k in _ALLOWED if k not in live)
    assert not dead, f"豁免表里这几条已经没有对应的代码了，删掉：{dead}"
