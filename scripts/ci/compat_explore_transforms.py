"""CG-02 deterministic candidate generation: reviewed AST transforms over reviewed parents.

A candidate is `parent + chain of transforms + seed`, nothing else. It is always
re-derivable from those three inputs (`build`), and the executor only ever accepts those
three inputs, so a candidate cannot carry its own authority: the trust level of a
candidate is that of its committed parent, and only because the derivation is a fixed,
deterministic, reviewed template (this file, covered by CODEOWNERS).

Transform classes
* shape_preserving: the native output (figure count, titles, written file names) must
  equal the parent's. The executor checks this against a native run of the parent, so a
  buggy template is caught as an invalid candidate instead of a fake Tavotto defect.
* axis_changing: output legitimately differs from the parent; the candidate can only be
  compared with its own native run.

One axis is moved at a time; two-factor chains combine different axes and are sampled.
No string replacement: every change is an AST edit and the result must `compile`.
"""

from __future__ import annotations

import ast
import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

import compat_guardian as cg

#: 父用例的 `__main__` 守卫的规范文本。放成常量：这个模块只**识别**守卫，自己不是入口脚本，
#: 不能让入口扫描（tests/test_windows_regressions.py）把这里的判断误认作 `if __name__ == ...`
MAIN_GUARD = '__name__ == "__main__"'

VERSION = "1.0.0"
#: Reviewed executable parents (engine corpus) and static-only parents (V2 fixtures).
ENGINE_PARENTS = (
    "shape_oo_savefig",
    "shape_plt_savefig",
    "shape_toplevel_imperative",
    "shape_entry_main",
    "shape_relative_csv",
    "shape_local_helper_import",
    "shape_dunder_main",
    "shape_custom_entry",
)
V2_STATIC_PARENTS = ("s01_plain",)
PAIRS_PER_PARENT = 3

# ---------------------------------------------------------------- parents


@dataclass
class Parent:
    suite: str
    case: str
    category: str
    files: dict[str, bytes]
    script: str
    entry: str
    executable: bool
    provenance: dict = field(default_factory=dict)


def load_parent(suite: str, case_id: str) -> Parent:
    """Parent files exactly as committed (checked against HEAD by the CG-01 machinery)."""
    case = cg.registered(suite)[case_id]
    hashes = cg.fixture_files(suite, case)
    files: dict[str, bytes] = {}
    if suite == "compat_v2":
        base = cg.ROOT / "tests/fixtures/compat_v2" / case_id
        for rel in hashes:
            source = cg.ROOT / rel
            files[source.relative_to(base).as_posix()] = source.read_bytes()
        script, entry, category, executable = "script.py", "__main__", "v2_first_figure", False
    else:
        script = case.get("script_dest") or Path(case["script"]).name
        files[script] = (cg.CC.COMPAT_DIR / case["script"]).read_bytes()
        for dest, src in (case.get("extra_files") or {}).items():
            files[dest] = (cg.CC.COMPAT_DIR / src).read_bytes()
        for asset in case.get("assets", []):
            files[(Path(script).parent / asset).as_posix()] = (
                cg.CC.ASSETS_DIR / asset
            ).read_bytes()
        entry, category, executable = case.get("entry", "main"), case["category"], True
    provenance = {
        "suite": suite,
        "case": case_id,
        "files": hashes,
        "repository_license_declaration": "AGPL-3.0-only",
        "individual_upstream_license_verified": False,
    }
    return Parent(suite, case_id, category, files, script, entry, executable, provenance)


def default_parents() -> list[Parent]:
    return [load_parent("compat_engine", c) for c in ENGINE_PARENTS] + [
        load_parent("compat_v2", c) for c in V2_STATIC_PARENTS
    ]


# ---------------------------------------------------------------- AST helpers


@dataclass
class Ctx:
    tree: ast.Module
    files: dict[str, bytes]
    script: str
    entry: str
    params: dict = field(default_factory=dict)

    def alias(self) -> str | None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    if item.name == "matplotlib.pyplot" and item.asname:
                        return item.asname
        return None

    def region(self) -> tuple[list, int, int] | None:
        """(owner statement list, start, end) of the statements that draw and save."""
        body = self.tree.body
        if self.entry != "__main__":
            for node in body:
                if isinstance(node, ast.FunctionDef) and node.name == self.entry:
                    start = 1 if _is_docstring(node.body[0]) else 0
                    return (node.body, start, len(node.body)) if len(node.body) > start else None
            return None
        for node in body:
            if isinstance(node, ast.If) and ast.unparse(node.test).replace("'", '"') == MAIN_GUARD:
                return node.body, 0, len(node.body)
        first = next(
            (
                i
                for i, n in enumerate(body)
                if not isinstance(n, (ast.Import, ast.ImportFrom)) and not _is_docstring(n)
            ),
            None,
        )
        if first is None:
            return None
        idx = [
            i
            for i in range(first, len(body))
            if not isinstance(body[i], (ast.FunctionDef, ast.ClassDef))
        ]
        if not idx or idx != list(range(idx[0], idx[-1] + 1)) or idx[-1] != len(body) - 1:
            return None
        return body, idx[0], idx[-1] + 1


def _is_docstring(node) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _savefig_call(node) -> ast.Call | None:
    if (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "savefig"
    ):
        return node.value
    return None


def _walk_region(ctx: Ctx):
    owner, start, end = ctx.region()
    for stmt in owner[start:end]:
        yield from ast.walk(stmt)


def _insert_import(tree: ast.Module, source: str) -> None:
    node = ast.parse(source).body[0]
    at = 1 if tree.body and _is_docstring(tree.body[0]) else 0
    tree.body.insert(at, node)


# ---------------------------------------------------------------- transforms


@dataclass(frozen=True)
class Transform:
    name: str
    family: str
    axis: str
    preserving: bool
    order: int
    applies: object
    apply: object
    summary: str


def _applies_show(ctx: Ctx) -> bool:
    if ctx.alias() is None or ctx.region() is None:
        return False
    owner, start, end = ctx.region()
    return any(_savefig_call(s) is not None for s in owner[start:end])


def _apply_show(ctx: Ctx, rng: random.Random, *, close: bool) -> None:
    owner, start, end = ctx.region()
    alias = ctx.alias()
    kept = [s for s in owner[start:end] if _savefig_call(s) is None]
    tail = [ast.parse(f"{alias}.show()").body[0]]
    if close:
        tail.append(ast.parse(f'{alias}.close("all")').body[0])
    owner[start:end] = kept + tail


def _applies_loop(ctx: Ctx) -> bool:
    if ctx.region() is None:
        return False
    return any(_savefig_call(n) is not None for n in _walk_region(ctx) if isinstance(n, ast.Expr))


class _TitleBeforeSavefig(ast.NodeTransformer):
    def __init__(self, alias: str | None):
        self.alias = alias

    def visit_Expr(self, node):
        call = _savefig_call(node)
        if call is None:
            return node
        receiver = ast.unparse(call.func.value)
        figure = f"{self.alias}.gcf()" if receiver == self.alias else receiver
        title = ast.parse(
            f"for _cg_ax in {figure}.axes[:1]:\n    _cg_ax.set_title(f'cg_pass_{{_cg_i}}')"
        ).body[0]
        return [title, node]


def _apply_loop(ctx: Ctx, rng: random.Random) -> None:
    count = rng.choice((2, 3, 4))
    ctx.params["passes"] = count
    owner, start, end = ctx.region()
    body = [_TitleBeforeSavefig(ctx.alias()).visit(s) for s in owner[start:end]]
    flat: list = []
    for item in body:
        flat.extend(item if isinstance(item, list) else [item])
    loop = ast.parse(f"for _cg_i in range({count}):\n    pass").body[0]
    loop.body = flat
    owner[start:end] = [loop]


class _Rename(ast.NodeTransformer):
    def __init__(self, mapping: dict[str, str]):
        self.mapping = mapping

    def visit_Name(self, node):
        if node.id in self.mapping:
            node.id = self.mapping[node.id]
        return node

    def visit_alias(self, node):
        if node.asname in self.mapping:
            node.asname = self.mapping[node.asname]
        return node


def _apply_alias(ctx: Ctx, rng: random.Random) -> None:
    suffix = rng.randrange(1, 9)
    mapping = {ctx.alias(): f"cg_plt{suffix}"}
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.name == "numpy" and item.asname == "np":
                    mapping["np"] = f"cg_np{suffix}"
    ctx.params["aliases"] = dict(sorted(mapping.items()))
    _Rename(mapping).visit(ctx.tree)


def _applies_wrap_main(ctx: Ctx) -> bool:
    if ctx.entry != "__main__" or ctx.region() is None:
        return False
    names = {n.name for n in ctx.tree.body if isinstance(n, ast.FunctionDef)}
    guarded = any(
        isinstance(n, ast.If) and "__name__" in ast.unparse(n.test) for n in ctx.tree.body
    )
    return "main" not in names and not guarded


def _apply_wrap_main(ctx: Ctx, rng: random.Random) -> None:
    owner, start, end = ctx.region()
    func = ast.parse("def main():\n    pass").body[0]
    func.body = owner[start:end]
    guard = ast.parse('if __name__ == "__main__":\n    main()').body[0]
    owner[start:end] = [func, guard]


def _first_savefig_literal(ctx: Ctx):
    for node in _walk_region(ctx):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "savefig"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return node
    return None


def _applies_argparse(ctx: Ctx) -> bool:
    return ctx.region() is not None and _first_savefig_literal(ctx) is not None


def _apply_argparse(ctx: Ctx, rng: random.Random) -> None:
    call = _first_savefig_literal(ctx)
    default = call.args[0].value
    call.args[0] = ast.parse("_cg_args.cg_out", mode="eval").body
    _insert_import(ctx.tree, "import argparse as _cg_argparse")
    owner, start, _end = ctx.region()
    owner[start:start] = ast.parse(
        "_cg_parser = _cg_argparse.ArgumentParser()\n"
        f"_cg_parser.add_argument('--cg-out', default={default!r})\n"
        "_cg_args = _cg_parser.parse_args()"
    ).body


def _numeric_list(node) -> list | None:
    if not isinstance(node, ast.List) or len(node.elts) < 2:
        return None
    values = []
    for item in node.elts:
        if (
            isinstance(item, ast.Constant)
            and type(item.value) in (int, float)
            and not isinstance(item.value, bool)
        ):
            values.append(item.value)
        else:
            return None
    return values


def _inline_target(ctx: Ctx):
    if ctx.region() is None:
        return None
    for node in _walk_region(ctx):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("plot", "scatter", "bar", "step")
        ):
            for i, arg in enumerate(node.args):
                if _numeric_list(arg) is not None:
                    return node, i
    return None


def _apply_inline(ctx: Ctx, rng: random.Random) -> None:
    call, i = _inline_target(ctx)
    values = _numeric_list(call.args[i])
    name = "cg_data.json"
    rel = (Path(ctx.script).parent / name).as_posix()
    ctx.files[rel] = (json.dumps(values) + "\n").encode()
    call.args[i] = ast.parse(f"_cg_json.load(open({name!r}, encoding='utf-8'))", mode="eval").body
    _insert_import(ctx.tree, "import json as _cg_json")
    ctx.params["data_file"] = rel


def _applies_alias_present(ctx: Ctx) -> bool:
    return ctx.alias() is not None and ctx.region() is not None


def _apply_rcparams(ctx: Ctx, rng: random.Random) -> None:
    width = rng.choice((1.5, 2.0, 2.5, 3.0))
    ctx.params["linewidth"] = width
    owner, start, _end = ctx.region()
    owner[start:start] = ast.parse(f'{ctx.alias()}.rcParams["lines.linewidth"] = {width}').body


def _apply_style(ctx: Ctx, rng: random.Random) -> None:
    style = rng.choice(("ggplot", "bmh", "grayscale", "fast"))
    ctx.params["style"] = style
    owner, start, _end = ctx.region()
    owner[start:start] = ast.parse(f"{ctx.alias()}.style.use({style!r})").body


TRANSFORMS: tuple[Transform, ...] = (
    Transform(
        "wrap_main",
        "entry_form",
        "entry",
        True,
        10,
        _applies_wrap_main,
        _apply_wrap_main,
        "move top-level drawing into main() under a __main__ guard",
    ),
    Transform(
        "inline_to_file",
        "data_source",
        "data_source",
        True,
        20,
        lambda c: _inline_target(c) is not None,
        _apply_inline,
        "read the first literal data list from a relative JSON file",
    ),
    Transform(
        "argparse_out",
        "argparse_defaults",
        "args",
        True,
        30,
        _applies_argparse,
        _apply_argparse,
        "take the savefig path from an argparse option whose default is the original path",
    ),
    Transform(
        "rcparams",
        "style_rc",
        "style",
        False,
        40,
        _applies_alias_present,
        _apply_rcparams,
        "set rcParams lines.linewidth before drawing",
    ),
    Transform(
        "style_use",
        "style_rc",
        "style",
        False,
        41,
        _applies_alias_present,
        _apply_style,
        "apply a bundled matplotlib style before drawing",
    ),
    Transform(
        "same_path_loop",
        "same_path_multi_figure",
        "output_name",
        False,
        50,
        _applies_loop,
        _apply_loop,
        "draw N figures in a loop that all savefig to the same path (G2)",
    ),
    Transform(
        "show_only",
        "show_close",
        "output_call",
        False,
        60,
        _applies_show,
        lambda c, r: _apply_show(c, r, close=False),
        "drop savefig; end with show()",
    ),
    Transform(
        "show_close",
        "show_close",
        "output_call",
        False,
        61,
        _applies_show,
        lambda c, r: _apply_show(c, r, close=True),
        "drop savefig; end with show() then close('all') (G1)",
    ),
    Transform(
        "import_alias",
        "import_alias",
        "import_alias",
        True,
        90,
        _applies_alias_present,
        _apply_alias,
        "rename the pyplot (and numpy) import aliases",
    ),
)
BY_NAME = {t.name: t for t in TRANSFORMS}


# ---------------------------------------------------------------- build / signature


def _rng(seed: int, *parts: str) -> random.Random:
    digest = hashlib.sha256(("|".join([str(seed), *parts])).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


class _Normalize(ast.NodeTransformer):
    """Seed-dependent parameters (counts, widths, style names, alias digits) are not structure."""

    def visit_Constant(self, node):
        if type(node.value) in (int, float) and not isinstance(node.value, bool):
            return ast.Constant(value="NUM")
        if isinstance(node.value, str):
            return ast.Constant(value="STR")
        return node

    def visit_Name(self, node):
        node.id = re.sub(r"^(cg_(?:plt|np))\d+$", r"\1", node.id)
        return node

    def visit_alias(self, node):
        if node.asname:
            node.asname = re.sub(r"^(cg_(?:plt|np))\d+$", r"\1", node.asname)
        return node


def ast_signature(source: str) -> str:
    tree = _Normalize().visit(ast.parse(source))
    return hashlib.sha256(ast.dump(tree, annotate_fields=False).encode()).hexdigest()[:16]


def build(parent: Parent, chain: tuple[str, ...], seed: int) -> dict:
    """Derive one candidate. Pure and deterministic: same inputs, same bytes."""
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise cg.EvidenceError("seed must be an integer in [0, 2**32)")
    ordered = [BY_NAME[n] for n in chain]
    if [t.order for t in ordered] != sorted(t.order for t in ordered) or len(
        {t.axis for t in ordered}
    ) != len(ordered):
        raise cg.EvidenceError(f"chain must move distinct axes in canonical order: {chain}")
    files = dict(parent.files)
    ctx = Ctx(ast.parse(files[parent.script].decode("utf-8")), files, parent.script, parent.entry)
    steps = []
    for t in ordered:
        if not t.applies(ctx):
            raise cg.EvidenceError(f"{t.name} does not apply to {parent.case} at this point")
        ctx.params = {}
        t.apply(ctx, _rng(seed, parent.suite, parent.case, t.name))
        steps.append(
            {
                "name": t.name,
                "family": t.family,
                "axis": t.axis,
                "preserving": t.preserving,
                "params": ctx.params,
            }
        )
    ast.fix_missing_locations(ctx.tree)
    source = ast.unparse(ctx.tree) + "\n"
    compile(source, parent.script, "exec", dont_inherit=True)  # compile is not execution
    files[parent.script] = source.encode()
    for rel, data in files.items():
        cg.contained(Path("/candidate"), rel)
        if rel.endswith(".py"):
            compile(data.decode("utf-8"), rel, "exec", dont_inherit=True)
    hashes = {rel: cg.digest(data) for rel, data in sorted(files.items())}
    core = {
        "generator": {"version": VERSION, "sha256": generator_digest()},
        "seed": seed,
        "parent": {
            "suite": parent.suite,
            "case": parent.case,
            "category": parent.category,
            "script": parent.script,
            "entry": parent.entry,
            "files": parent.provenance["files"],
        },
        "chain": steps,
        "class": "shape_preserving" if all(s["preserving"] for s in steps) else "axis_changing",
        "axis_vector": [f"{s['axis']}:{s['name']}" for s in steps],
        "ast_signature": ast_signature(source),
        "entry": ctx.entry,
        "files": hashes,
        "trust": {
            "level": "parent",
            "basis": "deterministic reviewed-template derivation of a committed parent; "
            "no authority of its own, re-derived from (parent, chain, seed) before any run",
        },
    }
    core["static_checks"] = {"python_ast": "passed", "compile": "passed", "execution": "not_run"}
    core["execution_plan"] = execution_plan(files, parent.executable, [st["name"] for st in steps])
    return {"candidate_id": cg.canonical(core)[:20], **core}, files


def generator_digest() -> str:
    return cg.digest(Path(__file__).read_bytes())


#: Names whose use makes a script's behavior depend on more than its own bytes.
_UNCERTAIN_IMPORTS = {
    "random": "nondeterministic (random)",
    "secrets": "nondeterministic (secrets)",
    "uuid": "nondeterministic (uuid)",
    "time": "time-dependent",
    "datetime": "time-dependent",
    "socket": "network access",
    "urllib": "network access",
    "requests": "network access",
    "http": "network access",
    "subprocess": "spawns processes",
    "ctypes": "native code",
    "multiprocessing": "spawns processes",
}


#: Transforms whose effect `compat_explore.compare` cannot observe (it compares figure counts,
#: titles and written paths, never line widths, colors or other style). Executing them could only
#: ever answer `no_difference`, which would read as "style agrees"; they stay static-only.
STYLE_UNOBSERVED = ("rcparams", "style_use")


def execution_plan(
    files: dict[str, bytes], parent_executable: bool, chain: list[str] | tuple[str, ...] = ()
) -> dict:
    """Decide executable vs static-only. Uncertain or non-deterministic means static only."""
    reasons = []
    for name in chain:
        if name in STYLE_UNOBSERVED:
            reasons.append(
                f"{name}: style axis is not compared (explore observes figure counts, titles "
                "and paths only), so a run could not detect a style regression"
            )
    if not parent_executable:
        reasons.append("parent is a V2 session fixture; explore does not drive the V2 adapter")
    for rel, data in sorted(files.items()):
        if not rel.endswith(".py"):
            continue
        try:
            tree = ast.parse(data.decode("utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            reasons.append(f"{rel}: not parseable")
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in _UNCERTAIN_IMPORTS:
                    reasons.append(f"{rel}: imports {name} ({_UNCERTAIN_IMPORTS[name]})")
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "random"
                and isinstance(node.value, ast.Name)
            ):
                reasons.append(f"{rel}: uses {node.value.id}.random (nondeterministic)")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in ("input", "exec", "eval", "__import__", "compile"):
                    reasons.append(f"{rel}: calls {node.func.id}()")
    return {"status": "static_only" if reasons else "executable", "reasons": sorted(set(reasons))}


# ---------------------------------------------------------------- generation + dedup


def chains_for(parent: Parent, seed: int) -> list[tuple[str, ...]]:
    """Single-axis chains for every applicable transform, plus a seeded sample of pairs."""

    def fresh() -> Ctx:
        files = dict(parent.files)
        return Ctx(ast.parse(files[parent.script].decode()), files, parent.script, parent.entry)

    singles = []
    for t in TRANSFORMS:
        if t.applies(fresh()):
            singles.append((t.name,))
    pairs = []
    for a in TRANSFORMS:
        for b in TRANSFORMS:
            if a.order >= b.order or a.axis == b.axis:
                continue
            try:
                build(parent, (a.name, b.name), 0)
            except cg.EvidenceError:
                continue
            pairs.append((a.name, b.name))
    rng = _rng(seed, parent.suite, parent.case, "pairs")
    chosen = sorted(rng.sample(sorted(pairs), min(PAIRS_PER_PARENT, len(pairs)))) if pairs else []
    return singles + chosen


def generate(seed: int, parents: list[Parent] | None = None) -> dict:
    """All candidates for a seed, deduplicated by (axis vector, normalized AST signature)."""
    parents = parents if parents is not None else default_parents()
    seen: dict[tuple, str] = {}
    unique, dropped = [], []
    out_files: dict[str, dict[str, bytes]] = {}
    for parent in sorted(parents, key=lambda p: (p.suite, p.case)):
        for chain in chains_for(parent, seed):
            try:
                manifest, files = build(parent, chain, seed)
            except cg.EvidenceError:
                continue
            key = (tuple(manifest["axis_vector"]), manifest["ast_signature"])
            if key in seen:
                dropped.append(
                    {
                        "candidate_id": manifest["candidate_id"],
                        "duplicate_of": seen[key],
                        "axis_vector": manifest["axis_vector"],
                    }
                )
                continue
            seen[key] = manifest["candidate_id"]
            unique.append(manifest)
            out_files[manifest["candidate_id"]] = files
    return {
        "schema": "tavotto-guardian-candidates/1",
        "seed": seed,
        "generator": {"version": VERSION, "sha256": generator_digest()},
        "candidates": unique,
        "dropped_duplicates": dropped,
        "coverage": coverage(parents, unique, dropped),
        "_files": out_files,
    }


def coverage(parents: list[Parent], candidates: list[dict], dropped: list[dict]) -> dict:
    """Make the bias toward already-supported shapes visible, by family and axis."""
    manifest_cases = cg.registered("compat_engine")
    by_category_all: dict[str, int] = {}
    for case in manifest_cases.values():
        by_category_all[case["category"]] = by_category_all.get(case["category"], 0) + 1
    by_category_used: dict[str, int] = {}
    for p in parents:
        by_category_used[p.category] = by_category_used.get(p.category, 0) + 1
    families: dict[str, dict] = {}
    for t in TRANSFORMS:
        fam = families.setdefault(
            t.family,
            {"axis": t.axis, "transforms": [], "parents_applicable": set(), "candidates": 0},
        )
        fam["transforms"].append(t.name)
    for c in candidates:
        for step in c["chain"]:
            fam = families[step["family"]]
            fam["parents_applicable"].add(c["parent"]["case"])
            fam["candidates"] += 1
    for fam in families.values():
        fam["parents_applicable"] = sorted(fam["parents_applicable"])
        fam["parent_count"] = len(fam["parents_applicable"])
        fam["transforms"] = sorted(fam["transforms"])
    return {
        "by_family": dict(sorted(families.items())),
        "by_axis": {
            axis: sum(1 for c in candidates for s in c["chain"] if s["axis"] == axis)
            for axis in sorted({t.axis for t in TRANSFORMS})
        },
        "by_class": {
            k: sum(1 for c in candidates if c["class"] == k)
            for k in ("shape_preserving", "axis_changing")
        },
        "parents": {
            "used": len(parents),
            "executable": sum(1 for p in parents if p.executable),
            "used_by_manifest_category": dict(sorted(by_category_used.items())),
            "corpus_by_manifest_category": dict(sorted(by_category_all.items())),
        },
        "dedup": {
            "generated": len(candidates) + len(dropped),
            "unique": len(candidates),
            "dropped": len(dropped),
        },
        "bias_note": "All parents are corpus shapes that already pass; a candidate that agrees with "
        "its own native run says nothing about shapes outside these families.",
    }
