"""「哪些 axes 存在」这个判断只能有一处出处（不变式 5 的机械化那一半）。

`ax.inset_axes()` 与 `ax.secondary_[xy]axis()` 建出来的 axes 挂在
`ax.child_axes` 上，**`in fig.axes` 为 False**。`axestraversal.ordered_axes` 是
把它们收进来的唯一权威（并且给出稳定的 `axes_i` 编号）。它 2026-09-17 之前叫
`manifest._ordered_axes`，`overrides` 只能经 `_sibling("manifest")` 延后反取——
manifest 在模块层 import overrides，反过来在模块层 import 会成环。提成独立的底层
模块（只依赖标准库，不 import manifest 也不 import overrides）之后，两边都在模块层
平铺 import 它，那个环没了；本文件同步把「权威在哪」的判据挪过去，判据本身没变：
**`fig.axes` 在引擎里只许出现在权威那一处**。

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
起来、也快。真实行为（inset / secondary / parasite / 色条 / gid 稳定性）由
`test_parasite_axes.py`、`test_invariants_engine.py`、`test_colorbar_orientation.py`
在真 matplotlib 上看护——这里只钉「谁是权威」。

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
#: `fig.axes` 里的那些」？除了 `ordered_axes` 自己，答案几乎总是前者。
#:
#: **表里只剩一条是有意的。** `colorbar_maps` / `follow_map` 一度带着
#: `axes=None → fig.axes` 的兜底，于是这张表得按**函数**放行它们整个函数体
#: ——而实测：把函数里另一处改回 `fig.axes`，这条看护照样绿。放行整函数的豁免
#: 挡不住函数内部的回归，所以那两个兜底被删掉了（`axes` 改成必填），
#: 而不是把豁免写得更细。**能删掉豁免就别把豁免写精细。**
_ALLOWED = {
    (
        "axestraversal.py",
        "ordered_axes",
    ): "遍历权威本身：它就是那个把 fig.axes 与 child_axes / parasites 合起来的函数",
}

#: 扫描范围：worker 侧会碰 Figure 对象图的全部模块。以前只扫 manifest / overrides 两个
#: 文件——`preview_complexity` 那份遍历当时是从 manifest 借的，没人抄；现在权威独立
#: 出来了，谁都可能「顺手」自己走一遍 `fig.axes`，所以把会拿到 fig 的模块都扫上。
SCANNED = (
    "axestraversal.py",
    "spinemodel.py",
    "manifest.py",
    "overrides.py",
    "preview_complexity.py",
    "preview_hybrid.py",
    "figsession.py",
    "pathgeom.py",
    "browser.py",
    "worker.py",
    "figcapture.py",
)


def _fig_axes_sites(path: str) -> list[tuple[str, int]]:
    """(所在函数, 行号) —— 源码里每一处 `<something>.fig.axes` / `fig.axes`。

    走 `ast` 而不是 grep：注释与 docstring 里提到 `fig.axes` 是**在讲这件事**
    （本仓库的注释密度下这类提及很多），拿正则去数会把说明文字当成违规。
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    sites: list[tuple[str, int]] = []
    stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):  # noqa: N802
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

        def visit_Attribute(self, node):  # noqa: N802
            if node.attr == "axes":
                base = node.value
                name = getattr(base, "id", None) or getattr(base, "attr", None)
                if name == "fig":
                    sites.append((stack[-1] if stack else "<module>", node.lineno))
            self.generic_visit(node)

    Visitor().visit(tree)
    return sites


@pytest.mark.parametrize("fname", SCANNED)
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
        "`axestraversal.ordered_axes(fig)[0]`（或由调用方传进来），别在这里再抄一遍"
        "遍历：\n  " + "\n  ".join(offenders)
    )


def test_the_allowlist_has_no_dead_entries():
    """豁免表不许留着已经不存在的条目。

    一条指向不存在位置的豁免，读起来像「这里有个有据可查的例外」，实际什么都
    没豁免——与本轮反复在收的那种空门禁是同一个形状，只是长在豁免表里。
    """
    live = {(f, func) for f in SCANNED for func, _ in _fig_axes_sites(os.path.join(ENGINE, f))}
    dead = sorted(k for k in _ALLOWED if k not in live)
    assert not dead, f"豁免表里这几条已经没有对应的代码了，删掉：{dead}"


def _module_level_imports(path: str) -> set[str]:
    """模块层（不在任何函数 / 类体里）的 `import x` / `from x import …` 的 x。"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module)
    return out


def test_the_authority_itself_is_a_leaf():
    """权威模块只依赖标准库：不 import matplotlib，更不 import manifest / overrides。

    它是 manifest 与 overrides 共同的底层——反向 import 任一个，那个环就回来了
    （PR B 的 import 图门禁也会红，这里是它在本文件的镜像：判据同一个，离权威更近）。
    """
    tree = ast.parse(open(os.path.join(ENGINE, "axestraversal.py"), encoding="utf-8").read())
    imported = {
        (a.name if isinstance(n, ast.Import) else n.module or "")
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [None])
    }
    assert imported <= {"__future__"}, f"axestraversal.py 不该 import 别的东西：{sorted(imported)}"


@pytest.mark.parametrize("fname", ["manifest.py", "overrides.py", "preview_complexity.py"])
def test_consumers_take_the_traversal_from_the_authority(fname):
    """三个消费者都在**模块层**平铺 import `axestraversal`，且自己没有 `ordered_axes` 的定义。

    模块层而不是延后：延后到函数里的 import 在 native bridge 里会在用户代码跑起来之后
    执行，裸名会命中用户项目里的同名文件（`_sibling` 存在过的全部理由）。装载期解析
    与 `import pathgeom` 同一条路，bridgeboot 的两张表由 `test_bridge_namespace.py`
    从 AST 反推校验。
    """
    path = os.path.join(ENGINE, fname)
    assert "axestraversal" in _module_level_imports(path), (
        f"{fname} 没在模块层 import axestraversal"
    )
    tree = ast.parse(open(path, encoding="utf-8").read())
    own = [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name in ("ordered_axes", "_ordered_axes")
    ]
    assert own == [], f"{fname} 自己定义了 {own}——遍历权威只许有一份"
