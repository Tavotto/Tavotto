"""Declared expectations for Guardian cases (the declarative replacement for id lists).

A case whose *observed* outcome is by design not a clean pass (a control for another rule,
a script that is invalid on purpose, a designed stop) is declared in
tests/compat_guardian/expectations.json with an outcome, a reason, and at least one
reference to an issue or to the test that owns the behavior. The rule is mechanical:

* observed outcome == declared outcome -> status `control_met` (not a pass, not a failure,
  listed separately in summaries);
* observed outcome != declared outcome -> status `failed`, with the difference attached.
  A better-than-declared result is a mismatch too: it needs a human to update the
  declaration, because it may be a silent behavior change.

Nothing here is a skip list: an undeclared case keeps its computed status.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import compat_guardian as cg

RELATIVE_PATH = "tests/compat_guardian/expectations.json"
SCHEMA_RELATIVE_PATH = "tests/compat_guardian/expectations.schema.json"
PATH = cg.ROOT / RELATIVE_PATH
SCHEMA_PATH = cg.ROOT / SCHEMA_RELATIVE_PATH
#: Outcomes a declaration may use. `passed` needs no declaration; `failed` and
#: `control_met` are results, never declarations.
DECLARABLE = (
    "invalid_fixture",
    "expected_limitation",
    "environment_unavailable",
    "partial_coverage",
    "not_reproduced",
)


def file_digest() -> str:
    return hashlib.sha256(PATH.read_bytes()).hexdigest()


def _check_reference(ref: dict) -> None:
    if ref["kind"] == "issue":
        if not re.fullmatch(r"#[1-9][0-9]*", ref["ref"]):
            raise cg.EvidenceError(f"issue reference must look like #123: {ref['ref']}")
        return
    path, _, name = ref["ref"].partition("::")
    target = cg.ROOT / path
    if cg.contained(cg.ROOT, path) != target or not target.is_file():
        raise cg.EvidenceError(f"test reference points at a missing file: {ref['ref']}")
    if not name:
        # 只指到文件不够：文件还在、拥有该对照的测试删了，声明仍会成立（Codex #1118 r4305976570）
        raise cg.EvidenceError(f"test reference must name a test node (path::name): {ref['ref']}")
    if not _defines(target, name.split("[")[0]):
        raise cg.EvidenceError(f"test reference names something absent from the file: {ref['ref']}")


def _defines(target: Path, name: str) -> bool:
    """`name`（pytest 节点路径 `Class::method` / `func`，不含参数化后缀）在 `target` 里是否**确有其定义**——按 AST
    逐级找 class / def，不在源码里搜子串（改名成更长的名字后旧引用不得仍然成立）。"""
    try:
        scope = ast.parse(target.read_text(encoding="utf-8")).body
    except SyntaxError:
        return False
    for part in name.split("::"):
        found = next(
            (
                node
                for node in scope
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == part
            ),
            None,
        )
        if found is None:
            return False
        scope = found.body
    return True


def load_entries(path: Path | None = None, *, check_cases: bool = True) -> list[dict]:
    """Parse and validate the declaration file; any problem is an explicit error."""
    source = Path(path) if path else PATH
    document = json.loads(source.read_text(encoding="utf-8"))
    cg.schema_check(document, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    seen = set()
    for entry in document["expectations"]:
        key = (entry["suite"], entry["case"])
        if key in seen:
            raise cg.EvidenceError(f"duplicate declaration: {key}")
        seen.add(key)
        if entry["suite"] not in cg.SUITES:
            raise cg.EvidenceError(f"unknown suite in declaration: {entry['suite']}")
        if entry["outcome"] not in DECLARABLE:
            raise cg.EvidenceError(f"outcome {entry['outcome']!r} cannot be declared")
        if check_cases and entry["case"] not in cg.registered(entry["suite"]):
            raise cg.EvidenceError(f"declaration for an unregistered case: {key}")
        for ref in entry["references"]:
            _check_reference(ref)
    return document["expectations"]


def find(entries: list[dict], suite: str, case: str) -> dict | None:
    for entry in entries:
        if entry["suite"] == suite and entry["case"] == case:
            return entry
    return None


def observed_outcome(row: dict) -> str:
    """The row's computed outcome in the declaration vocabulary.

    A failed row whose observed shape equals the recorded main baseline for its V2
    scenario is a known, pinned gap: `expected_limitation` (the baseline is itself a
    committed, tested artifact). Without a declaration that mapping has no effect.
    """
    raw = row.get("raw") or {}
    if "precondition_unmet" in raw or "missing_prerequisites" in raw:
        # The environment could not host the case: that conclusion is kept as-is, so it can
        # only ever match a declared environment_unavailable (control_met), never read as
        # a pass and never degrade into `unknown`.
        return "environment_unavailable"
    computed = cg.case_status(row)
    if computed == "failed" and (row.get("raw") or {}).get("baseline_match") is True:
        return "expected_limitation"
    return computed


def evaluate(row: dict, entry: dict) -> dict:
    observed = observed_outcome(row)
    met = observed == entry["outcome"]
    diff = None if met else f"declared {entry['outcome']} but observed {observed}"
    return {
        "status": "control_met" if met else "failed",
        "expectation": {
            "declared_outcome": entry["outcome"],
            "observed_outcome": observed,
            "result": "control_met" if met else "mismatch",
            "reason": entry["reason"],
            "references": entry["references"],
            "diff": diff,
        },
        "category": (
            {
                "category": None,
                "source_code": None,
                "basis": "declared control met: " + entry["reason"][:200],
            }
            if met
            else {
                "category": "unknown",
                "source_code": "expectation_mismatch",
                "basis": diff,
            }
        ),
    }
