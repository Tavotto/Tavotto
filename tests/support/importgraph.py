"""`src/tavotto` 的**运行时 import 图**：用 `ast` 读，不 import 任何产品模块。

回答三个问题：谁依赖谁（含平铺 import 与登记过的动态 import）、哪里有环、哪些边跨了层。
`tests/test_import_architecture.py` 拿它做增量门禁；`python tests/support/importgraph.py`
打印报告。判据的主语写在每条边上：

* `kind`：`static`（普通 import）/ `flat`（平铺 import——worker、browser、manifest 这些
  模块先把 engine 目录塞进 `sys.path` 再裸 `import manifest`，静态解析必须按「同目录有
  这个文件」补上，否则 worker 侧九个模块在图上是孤岛）/ `dynamic`（`importlib.import_module`
  之类，目标只能由登记表给出）/ `type`（`if TYPE_CHECKING:` 里的，不是运行时边）。
* `scope`：`module`（模块层，import 那一刻就发生）/ `function`（写在函数体里，调用时
  才发生——延后的 import 是 Python 里拆加载期环的常用手法，但**逻辑依赖仍在**，所以照样
  算边，只是标出来）。

解析不了的动态 import 不会被当作「没有」：它们进 `unknown_dynamic`，门禁要求逐条登记
（给出目标或说明目标是用户代码 / 标准库）。登记表在 `tests/import_architecture_baseline.json`。
"""

from __future__ import annotations

import ast
import json
import sys
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# 报告里全是中文，而它会被 `subprocess.run(capture_output=True)` 调起来：Windows 管道下
# stdout 退回系统区域编码，第一条中文就 UnicodeEncodeError（tests/test_windows_regressions.py 钉着）。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src"
PKG = SRC / "tavotto"
BASELINE = ROOT / "tests" / "import_architecture_baseline.json"

#: 只有这些目录里的模块会被当作「平铺 import 的兄弟」（运行时真的被塞进 sys.path 的目录）。
FLAT_DIRS = (PKG / "engine",)


@dataclass(frozen=True)
class Edge:
    src: str  # 相对 src/ 的 posix 路径，如 tavotto/engine/manifest.py
    dst: str
    kind: str  # static | flat | dynamic | type
    scope: str  # module | function
    line: int


@dataclass
class Graph:
    nodes: set[str] = field(default_factory=set)
    edges: list[Edge] = field(default_factory=list)
    externals: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    unknown_dynamic: list[dict] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)

    def runtime_edges(self) -> list[Edge]:
        return [e for e in self.edges if e.kind != "type"]

    def adjacency(self, *, runtime_only: bool = True) -> dict[str, set[str]]:
        adj: dict[str, set[str]] = {n: set() for n in self.nodes}
        for e in self.runtime_edges() if runtime_only else self.edges:
            if e.src != e.dst:
                adj.setdefault(e.src, set()).add(e.dst)
        return adj


def _rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def _module_files() -> list[Path]:
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def _resolve_absolute(dotted: str) -> str | None:
    """`tavotto.engine.pool` → 文件；找不到就 None（外部依赖）。"""
    parts = dotted.split(".")
    if parts[0] != "tavotto":
        return None
    cand = SRC.joinpath(*parts)
    if cand.with_suffix(".py").is_file():
        return _rel(cand.with_suffix(".py"))
    if (cand / "__init__.py").is_file():
        return _rel(cand / "__init__.py")
    # `from tavotto.engine import pool` 这种：最后一段是属性还是子模块，交给调用方
    return None


def _resolve_relative(src_file: Path, level: int, module: str | None) -> Path | None:
    base = src_file.parent
    for _ in range(level - 1):
        base = base.parent
    if module:
        base = base.joinpath(*module.split("."))
    if base.with_suffix(".py").is_file() and base.suffix == "":
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def _flat_sibling(src_file: Path, name: str) -> Path | None:
    if src_file.parent in FLAT_DIRS and "." not in name:
        cand = src_file.parent / f"{name}.py"
        if cand.is_file():
            return cand
    return None


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: Path, graph: Graph, registry: dict[tuple[str, str], dict]):
        self.path = path
        self.rel = _rel(path)
        self.graph = graph
        self.registry = registry
        self.func_depth = 0
        self.type_depth = 0

    # -- 作用域 -------------------------------------------------------------
    def visit_FunctionDef(self, node):
        self.func_depth += 1
        self.generic_visit(node)
        self.func_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_If(self, node):
        if _is_type_checking(node.test):
            self.type_depth += 1
            for n in node.body:
                self.visit(n)
            self.type_depth -= 1
            for n in node.orelse:
                self.visit(n)
            return
        self.generic_visit(node)

    def _scope(self) -> str:
        return "function" if self.func_depth else "module"

    def _kind(self, default: str) -> str:
        return "type" if self.type_depth else default

    def _add(self, dst: Path, kind: str, line: int) -> None:
        self.graph.edges.append(Edge(self.rel, _rel(dst), self._kind(kind), self._scope(), line))

    # -- import 语句 ---------------------------------------------------------
    def visit_Import(self, node):
        for alias in node.names:
            name = alias.name
            target = _resolve_absolute(name)
            if target:
                self.graph.edges.append(
                    Edge(self.rel, target, self._kind("static"), self._scope(), node.lineno)
                )
                continue
            flat = _flat_sibling(self.path, name)
            if flat:
                self._add(flat, "flat", node.lineno)
                continue
            self.graph.externals[self.rel].add(name.split(".")[0])

    def visit_ImportFrom(self, node):
        module = node.module
        if node.level:
            base = _resolve_relative(self.path, node.level, module)
            if base is None:
                self.graph.unresolved.append(
                    {
                        "from": self.rel,
                        "line": node.lineno,
                        "text": f"from {'.' * node.level}{module or ''} import …",
                    }
                )
                return
            # `from . import x` / `from .pkg import sub`：名字可能是子模块
            for alias in node.names:
                sub = base.parent / f"{alias.name}.py" if base.name == "__init__.py" else None
                if sub is not None and sub.is_file():
                    self._add(sub, "static", node.lineno)
                else:
                    self._add(base, "static", node.lineno)
            return
        assert module is not None
        target = _resolve_absolute(module)
        if target:
            for alias in node.names:
                sub = _resolve_absolute(f"{module}.{alias.name}")
                self.graph.edges.append(
                    Edge(self.rel, sub or target, self._kind("static"), self._scope(), node.lineno)
                )
            return
        flat = _flat_sibling(self.path, module)
        if flat:
            self._add(flat, "flat", node.lineno)
            return
        self.graph.externals[self.rel].add(module.split(".")[0])

    # -- 动态 import ---------------------------------------------------------
    def visit_Call(self, node):
        callee = _callee_name(node.func)
        if callee in {"importlib.import_module", "__import__", "import_module"}:
            arg = node.args[0] if node.args else None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                target = _resolve_absolute(arg.value)
                if target:
                    self.graph.edges.append(
                        Edge(self.rel, target, self._kind("dynamic"), self._scope(), node.lineno)
                    )
                else:
                    self.graph.externals[self.rel].add(arg.value.split(".")[0])
            else:
                text = ast.get_source_segment(self.path.read_text(encoding="utf-8"), node) or callee
                text = " ".join(text.split())
                reg = self.registry.get((self.rel, text))
                if reg is None:
                    self.graph.unknown_dynamic.append(
                        {"from": self.rel, "line": node.lineno, "call": text}
                    )
                else:
                    for t in reg.get("targets", []):
                        self.graph.edges.append(
                            Edge(self.rel, t, "dynamic", self._scope(), node.lineno)
                        )
        self.generic_visit(node)


def _callee_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = _callee_name(func.value)
        return f"{base}.{func.attr}" if base else func.attr
    return ""


def _is_type_checking(test: ast.AST) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def load_baseline(path: Path = BASELINE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build(baseline: dict | None = None) -> Graph:
    baseline = baseline if baseline is not None else load_baseline()
    registry = {(d["from"], d["call"]): d for d in baseline.get("dynamic_calls", [])}
    graph = Graph()
    files = _module_files()
    graph.nodes = {_rel(p) for p in files}
    for path in files:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        _Visitor(path, graph, registry).visit(tree)
    # 一条 `from x import (a, b, c)` 只算一条边
    graph.edges = list(dict.fromkeys(graph.edges))
    for extra in baseline.get("extra_edges", []):
        graph.edges.append(
            Edge(extra["from"], extra["to"], "dynamic", extra.get("scope", "function"), 0)
        )
    for e in graph.edges:
        if e.dst not in graph.nodes:
            graph.unresolved.append(
                {"from": e.src, "line": e.line, "text": f"→ {e.dst}（登记的目标不存在）"}
            )
    return graph


# ---------------------------------------------------------------- 环（Tarjan，迭代式）


def strongly_connected_components(adj: dict[str, set[str]]) -> list[set[str]]:
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    out: list[set[str]] = []
    for root in sorted(adj):
        if root in indices:
            continue
        work = [(root, iter(sorted(adj.get(root, ()))))]
        indices[root] = low[root] = index
        index += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in indices:
                    indices[nxt] = low[nxt] = index
                    index += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(adj.get(nxt, ())))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], indices[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == indices[node]:
                comp = set()
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.add(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    out.append(comp)
    return sorted(out, key=lambda c: sorted(c))


def cycles(graph: Graph) -> list[dict]:
    """每个 ≥2 节点的强连通分量 = 一个环；带上分量内部的边（判「扩大」用）。"""
    adj = graph.adjacency(runtime_only=True)
    out = []
    for comp in strongly_connected_components(adj):
        inside = [
            e for e in graph.runtime_edges() if e.src in comp and e.dst in comp and e.src != e.dst
        ]
        internal = sorted({(e.src, e.dst) for e in inside})
        # 一对节点之间只要有一条「模块层的 static / flat」实例，这一步就在加载期发生；
        # 环里每一步都在加载期发生，这个环才是加载期的（会不会真的报错另说）。
        load_time = all(
            any(
                e.scope == "module" and e.kind in ("static", "flat")
                for e in inside
                if (e.src, e.dst) == pair
            )
            for pair in internal
        )
        out.append(
            {"members": sorted(comp), "edges": [list(x) for x in internal], "load_time": load_time}
        )
    return out


# ---------------------------------------------------------------- 分层


#: 层 = 路径前缀 / 文件名集合。顺序无意义；规则在 `LAYER_RULES`。
LAYERS: dict[str, tuple[str, ...]] = {
    "entry": ("tavotto/app.py", "tavotto/cli_entry.py", "tavotto/__main__.py"),
    "worker": (
        "tavotto/engine/worker.py",
        "tavotto/engine/figsession.py",
        "tavotto/engine/manifest.py",
        "tavotto/engine/overrides.py",
        "tavotto/engine/axestraversal.py",
        "tavotto/engine/spinemodel.py",
        "tavotto/engine/tickmodel.py",
        "tavotto/engine/colorbarmodel.py",
        "tavotto/engine/legendmodel.py",
        "tavotto/engine/wireproto.py",
        "tavotto/engine/pathgeom.py",
        "tavotto/engine/preview_complexity.py",
        "tavotto/engine/preview_hybrid.py",
        "tavotto/engine/browser.py",
    ),
    "bridge": ("tavotto/engine/bridge_runner.py", "tavotto/engine/bridgeboot.py"),
    "pdfbackend": ("tavotto/pdfbackend/",),
    # RenderCore（统一实施包 U06，ADR 0059）：纯模型那几份先列（`layer_of` 按顺序首个命中），
    # 包里其余的（native 适配 / 入口）落到下面那条目录前缀。
    "rendercore_model": (
        "tavotto/rendercore/__init__.py",
        "tavotto/rendercore/ir.py",
        "tavotto/rendercore/geometry.py",
        "tavotto/rendercore/typography.py",
        "tavotto/rendercore/sources.py",
        "tavotto/rendercore/plan.py",
    ),
    "rendercore_native": ("tavotto/rendercore/",),
}

#: (from 层, to 层) 不许有运行时边。每条都要说得出理由（在 baseline 的 `layer_rules` 里）。
LAYER_RULES: tuple[tuple[str, str], ...] = (
    ("*", "entry"),  # 入口层只被入口层 import：底层反向依赖 app.py 就是任务书点名要挡的
    ("worker", "entry"),
    ("bridge", "entry"),
    # 新核心零 pymupdf（D03：先限制新核心不借旧库）——整包不许有边进 pdfbackend
    ("rendercore_model", "pdfbackend"),
    ("rendercore_native", "pdfbackend"),
    # 纯模型不许依赖 native 适配层（候选包只能从适配层进来），也不许碰 worker 侧（科学栈）
    ("rendercore_model", "rendercore_native"),
    ("rendercore_model", "worker"),
    ("rendercore_native", "worker"),
    # 反方向：旧后端 / worker 也不许反过来 import 新核心（默认通道一字不变，U08 之前两边不接）
    ("pdfbackend", "rendercore_model"),
    ("pdfbackend", "rendercore_native"),
    ("worker", "rendercore_model"),
    ("worker", "rendercore_native"),
)


def layer_of(node: str) -> str | None:
    for layer, patterns in LAYERS.items():
        for pat in patterns:
            if node == pat or (pat.endswith("/") and node.startswith(pat)):
                return layer
    return None


def layer_violations(graph: Graph) -> list[dict]:
    out = []
    for e in graph.runtime_edges():
        a, b = layer_of(e.src), layer_of(e.dst)
        if b is None:
            continue
        for src_rule, dst_rule in LAYER_RULES:
            if dst_rule != b:
                continue
            if src_rule == "*" and a != b:
                out.append(
                    {"from": e.src, "to": e.dst, "rule": f"* → {b}", "line": e.line, "kind": e.kind}
                )
            elif src_rule == a and a != b:
                out.append(
                    {
                        "from": e.src,
                        "to": e.dst,
                        "rule": f"{a} → {b}",
                        "line": e.line,
                        "kind": e.kind,
                    }
                )
    # 去重（同一条边可能同时命中 `*` 与具名规则）
    seen = set()
    uniq = []
    for v in out:
        key = (v["from"], v["to"])
        if key not in seen:
            seen.add(key)
            uniq.append(v)
    return uniq


# ---------------------------------------------------------------- 报告


def fan(graph: Graph, node: str) -> tuple[list[str], list[str]]:
    ins = sorted({e.src for e in graph.runtime_edges() if e.dst == node and e.src != node})
    outs = sorted({e.dst for e in graph.runtime_edges() if e.src == node and e.dst != node})
    return ins, outs


def report(graph: Graph, focus: tuple[str, ...] = ()) -> str:
    lines = []
    lines.append(
        f"模块 {len(graph.nodes)}，运行时边 {len(graph.runtime_edges())}"
        f"（type-only {len(graph.edges) - len(graph.runtime_edges())}），"
        f"外部依赖 {len({x for s in graph.externals.values() for x in s})} 个名字"
    )
    cyc = cycles(graph)
    lines.append(f"\n== 环（强连通分量）：{len(cyc)}")
    for c in cyc:
        tag = "加载期" if c["load_time"] else "逻辑（含延后 / 动态边）"
        lines.append(f"  [{tag}] " + " ↔ ".join(c["members"]))
        for a, b in c["edges"]:
            kinds = sorted(
                {
                    f"{e.kind}/{e.scope}:{e.line}"
                    for e in graph.runtime_edges()
                    if (e.src, e.dst) == (a, b)
                }
            )
            lines.append(f"      {a} → {b}  ({', '.join(kinds)})")
    viol = layer_violations(graph)
    lines.append(f"\n== 跨层反向边：{len(viol)}")
    for v in viol:
        lines.append(f"  {v['from']}:{v['line']} → {v['to']}  违反 {v['rule']}")
    lines.append(f"\n== 未登记的动态 import：{len(graph.unknown_dynamic)}")
    for u in graph.unknown_dynamic:
        lines.append(f"  {u['from']}:{u['line']}  {u['call']}")
    if graph.unresolved:
        lines.append(f"\n== 解析不了的 import：{len(graph.unresolved)}")
        for u in graph.unresolved:
            lines.append(f"  {u['from']}:{u['line']}  {u['text']}")
    for node in focus:
        ins, outs = fan(graph, node)
        lines.append(f"\n== {node}：入 {len(ins)} / 出 {len(outs)}")
        lines.append("  入 ← " + ", ".join(ins))
        lines.append("  出 → " + ", ".join(outs))
    return "\n".join(lines)


DEFAULT_FOCUS = (
    "tavotto/app.py",
    "tavotto/engine/manifest.py",
    "tavotto/engine/overrides.py",
    "tavotto/engine/figsession.py",
    "tavotto/engine/pool.py",
)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="src/tavotto 的运行时 import 图报告")
    ap.add_argument(
        "--focus", nargs="*", default=list(DEFAULT_FOCUS), help="要列出入 / 出依赖的模块"
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    g = build()
    if args.json:
        print(
            json.dumps(
                {
                    "nodes": sorted(g.nodes),
                    "edges": [e.__dict__ for e in g.edges],
                    "cycles": cycles(g),
                    "layer_violations": layer_violations(g),
                    "unknown_dynamic": g.unknown_dynamic,
                    "unresolved": g.unresolved,
                },
                ensure_ascii=False,
                indent=1,
            )
        )
    else:
        print(report(g, tuple(args.focus)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
