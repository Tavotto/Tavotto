"""Positive evidence of unconverted drawing; absence is not a fidelity proof.

The subject is a statically resolved Pillow drawing call in the candidate.
Reading photographs and measuring glyphs remain valid conversion operations.
"""

from __future__ import annotations

import ast
from collections import Counter

_DRAW = "<pillow-draw>"
# Methods that paint pixels, as opposed to textbbox/textlength/font metrics.
_PAINT = frozenset(
    (
        "text",
        "multiline_text",
        "line",
        "rectangle",
        "rounded_rectangle",
        "ellipse",
        "polygon",
        "regular_polygon",
        "pieslice",
        "arc",
        "chord",
        "point",
        "bitmap",
        "shape",
    )
)


def raster_drawing(tree: ast.AST) -> bool:
    # Ambiguous rebinding is not evidence. Counting across scopes deliberately
    # misses some aliases rather than mistaking an unrelated object's .text.
    bindings: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bindings[node.id] += 1
        elif isinstance(node, ast.Import):
            bindings.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            bindings.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.arg):
            bindings[node.arg] += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings[node.name] += 1

    class Scan(ast.NodeVisitor):
        def __init__(self):
            self.names: dict[str, str] = {}
            self.found = False

        def resolve(self, node: ast.AST) -> str:
            if isinstance(node, ast.Name):
                return self.names.get(node.id, "") if bindings[node.id] == 1 else ""
            if isinstance(node, ast.Attribute):
                base = self.resolve(node.value)
                return base + "." + node.attr if base else ""
            if isinstance(node, ast.Call) and self.resolve(node.func) == "PIL.ImageDraw.Draw":
                return _DRAW
            return ""

        def visit_Import(self, node: ast.Import):
            for a in node.names:
                self.names[a.asname or a.name.split(".")[0]] = (
                    a.name if a.asname else a.name.split(".")[0]
                )

        def visit_ImportFrom(self, node: ast.ImportFrom):
            for a in node.names:
                self.names[a.asname or a.name] = f"{node.module}.{a.name}" if not node.level else ""

        def bind(self, target: ast.AST, value: str):
            for n in ast.walk(target):
                if isinstance(n, ast.Name):
                    self.names[n.id] = value if n is target else ""

        def visit_Assign(self, node: ast.Assign):
            self.visit(node.value)
            value = self.resolve(node.value)
            for target in node.targets:
                self.bind(target, value)

        def visit_AnnAssign(self, node: ast.AnnAssign):
            if node.value:
                self.visit(node.value)
            self.bind(node.target, self.resolve(node.value) if node.value else "")

        def visit_FunctionDef(self, node: ast.FunctionDef):
            outer = self.names.copy()
            self.names[node.name] = ""
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                self.names[arg.arg] = ""
            for arg in (node.args.vararg, node.args.kwarg):
                if arg:
                    self.names[arg.arg] = ""
            for statement in node.body:
                self.visit(statement)
            self.names = outer
            self.names[node.name] = ""

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node: ast.Call):
            name = self.resolve(node.func)
            if name.startswith(_DRAW + ".") and name.removeprefix(_DRAW + ".") in _PAINT:
                self.found = True
            self.generic_visit(node)

    scan = Scan()
    scan.visit(tree)
    return scan.found
