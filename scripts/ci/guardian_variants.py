#!/usr/bin/env python3
"""CG-02 deterministic candidate generation. Static checks ONLY; never execute.

Templates derive from committed CompatBench/V2 fixtures, preserve their relative
file graph, and record provenance, seed, generator version and all content hashes.
Unreviewed candidates have no route into Compatibility Guardian's registered suites.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

import compat_guardian as cg

VERSION = "1.0.0"
TEMPLATES = {
    "entry_argparse": ("compat_engine", "shape_oo_savefig", "entry"),
    "entry_show": ("compat_engine", "shape_oo_savefig", "entry"),
    "helper_csv": ("compat_engine", "shape_relative_csv", "relative_data"),
    "file_replace": ("compat_v2", "s04_replace_readback", "file_semantics"),
    "multi_provider": ("compat_v2", "s26_import_cv2_sklearn", "providers"),
    "artist_style": ("compat_engine", "mm_twoline_oo", "artists"),
    "bitmap_only": ("compat_engine", "sci_pillow", "bitmap"),
}


def static_check(files: dict[str, bytes]) -> dict:
    if not files:
        raise cg.EvidenceError("empty candidate file graph")
    scripts = 0
    for rel, data in files.items():
        cg.contained(Path("/static-candidate"), rel)
        if rel.endswith(".py"):
            tree = ast.parse(data.decode("utf-8"), filename=rel)
            compile(tree, rel, "exec", dont_inherit=True)  # compile is not execution
            scripts += 1
    if not scripts:
        raise cg.EvidenceError("candidate has no Python source")
    return {"python_ast": "passed", "relative_paths": "passed", "python_files": scripts,
            "runtime": "not_run", "qualification": "not_run"}


def parent_files(suite: str, cid: str) -> tuple[dict, dict[str, bytes], str]:
    case = cg.registered(suite)[cid]
    hashes = cg.fixture_files(suite, case)
    files = {}
    if suite == "compat_v2":
        base = cg.ROOT / "tests/fixtures/compat_v2" / cid
        for rel in hashes:
            source = cg.ROOT / rel
            files[source.relative_to(base).as_posix()] = source.read_bytes()
        script = "script.py"
    else:
        script = case.get("script_dest") or Path(case["script"]).name
        files[script] = (cg.CC.COMPAT_DIR / case["script"]).read_bytes()
        for dest, src in (case.get("extra_files") or {}).items():
            files[dest] = (cg.CC.COMPAT_DIR / src).read_bytes()
        for asset in case.get("assets", []):
            files[(Path(script).parent / asset).as_posix()] = (cg.CC.ASSETS_DIR / asset).read_bytes()
    provenance = {"suite": suite, "case": cid, "commit": cg.git("rev-parse", "HEAD"),
                  "files": hashes, "repository_license_declaration": "AGPL-3.0-only",
                  "individual_upstream_license_verified": False}
    return provenance, files, script


class Transform(ast.NodeTransformer):
    def __init__(self, template: str, seed: int):
        self.template = template
        self.scale = seed % 3 + 1

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        if self.template == "entry_argparse" and node.name == "main":
            node.args.args.append(ast.arg(arg="scale"))
            node.args.defaults.append(ast.Constant(value=1))
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Attribute):
            if self.template == "entry_show" and node.func.attr == "savefig":
                return ast.Call(func=ast.Attribute(value=ast.Name(id="plt", ctx=ast.Load()), attr="show", ctx=ast.Load()), args=[], keywords=[])
            if self.template == "entry_argparse" and node.func.attr == "plot" and len(node.args) >= 2:
                node.args[1] = ast.ListComp(elt=ast.BinOp(left=ast.Name(id="v", ctx=ast.Load()), op=ast.Mult(), right=ast.Name(id="scale", ctx=ast.Load())), generators=[ast.comprehension(target=ast.Name(id="v", ctx=ast.Store()), iter=node.args[1], ifs=[], is_async=0)])
            if self.template == "artist_style" and node.func.attr == "plot":
                node.keywords = [k for k in node.keywords if k.arg != "linewidth"]
                node.keywords.append(ast.keyword(arg="linewidth", value=ast.Constant(value=1.0 + self.scale * 0.25)))
        return node


def candidate(template: str, seed: int) -> tuple[dict, dict[str, bytes]]:
    if template not in TEMPLATES or type(seed) is not int or not 0 <= seed < 2**32:
        raise cg.EvidenceError("unknown template or seed outside uint32")
    suite, cid, family = TEMPLATES[template]
    parent, files, script = parent_files(suite, cid)
    tree = Transform(template, seed).visit(ast.parse(files[script].decode()))
    oracle = {"status": "unverified", "kind": "parent_contract", "parent_case": cid}
    if template == "entry_argparse":
        tree.body.insert(0, ast.Import(names=[ast.alias(name="argparse")]))
        tree.body += ast.parse(f'''\nif __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=float, default={seed % 3 + 1})
    args = parser.parse_args()
    main(args.scale)
''').body
        oracle = {"status": "static_truth_only", "line_y": [v * (seed % 3 + 1) for v in (0, 1, 4, 9)], "argv": []}
    elif template == "entry_show":
        tree.body += ast.parse('\nif __name__ == "__main__":\n    main()\n').body
        oracle = {"status": "static_truth_only", "line_y": [0, 1, 4, 9], "expected_figures": 1}
    elif template == "helper_csv":
        helper = f"_cg_data_{seed:08x}"
        class ExtractReader(ast.NodeTransformer):
            def visit_With(self, node):
                # Narrow reviewed template: the sole with block reads data.csv.
                return ast.parse('rows = load_rows("data.csv")').body[0]
        tree = ExtractReader().visit(tree)
        tree.body.insert(0, ast.ImportFrom(module=helper, names=[ast.alias(name="load_rows")], level=0))
        files[(Path(script).parent / (helper + ".py")).as_posix()] = b'import csv\n\ndef load_rows(path):\n    with open(path, newline="", encoding="utf-8") as handle:\n        return list(csv.DictReader(handle))\n'
        oracle = {"status": "static_truth_only", "data_file": "data.csv", "helper": helper}
    elif template == "file_replace":
        class Paths(ast.NodeTransformer):
            def visit_Constant(self, node):
                if isinstance(node.value, str) and node.value.endswith((".tmp", ".txt")):
                    node.value = f"seed_{seed:08x}_" + node.value
                return node
        tree = Paths().visit(tree)
        oracle = {"status": "static_truth_only", "scientific_title": "value=2", "file_semantics": "real replace/readback"}
    elif template == "multi_provider":
        from tavotto.engine import depresolve
        class Aliases(ast.NodeTransformer):
            def visit_Import(self, node):
                for alias in node.names:
                    if alias.name == "cv2":
                        alias.asname = f"cg_cv2_{seed:08x}"
                return node
            def visit_ImportFrom(self, node):
                if node.module == "sklearn.cluster":
                    for alias in node.names:
                        alias.asname = f"cg_kmeans_{seed:08x}"
                return node
        tree = Aliases().visit(tree)
        oracle = {"status": "static_catalogue_only", "provider_candidates": {"cv2": list(depresolve.distribution_alternatives("cv2")), "sklearn": ["scikit-learn"]}, "installation_authorized": False,
                  "unverified": "multiple-provider environment and arbitration were not exercised"}
    elif template == "bitmap_only":
        width, height = 8 + seed % 5, 9 + seed % 7
        tree = ast.parse(f'from PIL import Image\nimage = Image.new("RGB", ({width}, {height}), (17, 43, 91))\nimage.save("bitmap.png")\n')
        files = {}
        oracle = {"status": "static_truth_only", "image_size": [width, height], "rgb": [17, 43, 91], "edit_scope": "bitmap layout only; no Artist objects"}
    ast.fix_missing_locations(tree)
    files[script] = (ast.unparse(tree) + "\n").encode()
    checks = static_check(files)
    hashes = {rel: cg.digest(data) for rel, data in sorted(files.items())}
    core = {"schema_version": VERSION, "template": template, "template_version": VERSION,
            "generator_sha256": cg.digest(Path(__file__).read_bytes()), "seed": seed,
            "family": family, "parent": parent, "script": script,
            "files": hashes, "oracle": oracle, "static_checks": checks,
            "review": {"status": "unreviewed", "content_sha256": cg.canonical(hashes)},
            "execution_status": "not_run"}
    return {"candidate_id": cg.canonical(core)[:24], **core}, files


def generate(seed: int, out: Path) -> list[dict]:
    if out.resolve().is_relative_to(cg.ROOT):
        raise cg.EvidenceError("candidate outputs must be outside source worktree")
    rows = []
    for template in TEMPLATES:
        manifest, files = candidate(template, seed)
        target = out / manifest["candidate_id"]
        target.mkdir(parents=True, exist_ok=False)
        for rel, data in files.items():
            dest = cg.contained(target / "files", rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        (target / "candidate.json").write_text(json.dumps(manifest, indent=2) + "\n")
        rows.append(manifest)
    return rows


def validate_candidate(root: Path) -> dict:
    """Static packet validation is not review approval or execution authority."""
    manifest_path = cg.contained(root, "candidate.json")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != VERSION or manifest.get("execution_status") != "not_run":
        raise cg.EvidenceError("unknown/executed candidate packet")
    if manifest.get("review", {}).get("status") != "unreviewed":
        raise cg.EvidenceError("a packet cannot self-authorize review")
    core = {k:v for k,v in manifest.items() if k != "candidate_id"}
    if cg.canonical(core)[:24] != manifest["candidate_id"]:
        raise cg.EvidenceError("candidate identity changed (including oracle/entry/seed)")
    file_root = cg.contained(root, "files")
    actual = {p.relative_to(file_root).as_posix() for p in file_root.rglob("*") if p.is_file()}
    if actual != set(manifest["files"]):
        raise cg.EvidenceError("candidate file graph differs")
    files = {rel: cg.contained(file_root, rel).read_bytes() for rel in actual}
    hashes = {rel:cg.digest(data) for rel,data in files.items()}
    if hashes != manifest["files"] or cg.canonical(hashes) != manifest["review"]["content_sha256"]:
        raise cg.EvidenceError("candidate content digest differs")
    if static_check(files) != manifest["static_checks"]:
        raise cg.EvidenceError("candidate static evidence differs")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        rows = generate(args.seed, args.out)
        print(json.dumps({"kind": "static_candidates", "seed": args.seed,
                          "candidate_ids": [r["candidate_id"] for r in rows], "execution_status": "not_run"}))
        return 0
    except (cg.EvidenceError, ValueError, OSError) as exc:
        print(f"candidate generation refused: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
