"""CG-06 explore oracles: what a derived candidate is judged by beyond figure counts and titles.

Every criterion here is *borrowed*, not restated: pixels use `pixelcompare` with CompatBench's own
`FIDELITY_TOLERANCE`; edit / replay / export call `compat_matrix.stage_*`; structure compares the
`_editable_snapshot` values with `compat_matrix._same_value`. A second copy of a threshold or of a
comparison rule would let explore and CompatBench disagree about the same figure.

Pure functions where possible (inputs are paths or plain dicts); the only thing that touches a
worker is `inherited_stages`, and it only forwards to `compat_matrix`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import compat_guardian as cg
import pixelcompare

#: Stage names a candidate row reports. `structure` is explore's own (parent vs candidate manifest).
STAGES = ("fidelity", "structure", "edit", "replay", "export")

CRITERIA = {
    "fidelity": (
        "native vs Tavotto zero-patch PNG at compat_matrix.FIDELITY_WIDTH; "
        "pixelcompare.verdict with CompatBench's FIDELITY_TOLERANCE"
    ),
    "structure": (
        "shape-preserving only: every {gid.prop: value} of the parent's Tavotto manifest is "
        "present and equal (compat_matrix._same_value) in the candidate's"
    ),
    "edit": "the parent case's reviewed mutations apply, show in the manifest and revert (compat_matrix.stage_edit)",
    "replay": "hot == clear+replay == fresh worker replay (compat_matrix.stage_replay)",
    "export": "real PDF/PNG export of the parent case's formats opens and is sane (compat_matrix.stage_export)",
}

NOT_COVERED_AXIS_CHANGING = (
    "axis-changing candidate: the reviewed mutation targets are keyed to the parent's gids, "
    "and no reviewed edit target exists for the changed output"
)


def _cm():
    import compat_matrix

    return compat_matrix


def stage(name: str, status: str, note: str | None = None) -> dict:
    out = {"status": status, "criterion": CRITERIA[name]}
    if note:
        out["note"] = note
    return out


def blank_stages(status: str, note: str) -> dict:
    """All stages in one state with one reason (static-only, native failed, adapter failed...)."""
    return {name: stage(name, status, note) for name in STAGES}


def _evidence_file(src: Path, dest: Path, root: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return {"path": dest.relative_to(root).as_posix(), "sha256": cg.digest(dest.read_bytes())}


NO_NATIVE_PIXELS = (
    "the native driver renders only figures saved with savefig or still open at exit; this script "
    "leaves none (for example show() then close()), so there is no native pixel reference"
)


def unresolved(stages: dict) -> list[str]:
    """In-scope layers that did not run (`not_run`): the candidate cannot be called clean."""
    out = []
    for n, st in stages.items():
        if st["status"] == "not_run":
            out.append(f"{n}: {st.get('note') or 'not run'}")
        elif st.get("unmatched"):
            out.append(f"{n}: stems not compared: {st['unmatched']}")
    return out


def pixel_judgment(native_shots: dict, tavotto_shots: dict, out_dir: Path) -> dict:
    """Per common stem: native PNG vs Tavotto PNG against CompatBench's tolerance.

    Returns `{"status", "differences", "compared", "note"?}`. `status` is `passed`, `failed` or
    `not_run` (in scope but no conclusion: the imaging dependencies are missing, or the two sides
    share no stem) or `not_covered` (structurally no native pixel reference: the native driver
    renders figures saved with savefig or still open at exit, and this script has neither).
    """
    tol = _cm().FIDELITY_TOLERANCE
    if not native_shots:
        return {"status": "not_covered", "differences": [], "compared": 0, "note": NO_NATIVE_PIXELS}
    stems = sorted(set(native_shots) & set(tavotto_shots))
    if not stems:
        return {
            "status": "not_run",
            "differences": [],
            "compared": 0,
            "note": f"no common stem: native {sorted(native_shots)} / Tavotto {sorted(tavotto_shots)}",
        }
    diffs = []
    out_dir = Path(out_dir)
    for stem in stems:
        name = Path(stem).name
        diff_png = out_dir / "pixel" / f"{name}.diff.png"
        diff_png.parent.mkdir(parents=True, exist_ok=True)
        try:
            metrics = pixelcompare.compare(
                Path(native_shots[stem]), Path(tavotto_shots[stem]), diff_png
            )
        except pixelcompare.MissingImagingDeps as exc:
            return {"status": "not_run", "differences": [], "compared": 0, "note": str(exc)}
        good, reasons = pixelcompare.verdict(metrics, tol)
        if good:
            diff_png.unlink(missing_ok=True)
            continue
        evidence = {
            "stem": stem,
            "metrics": metrics,
            "reasons": reasons,
            "tolerance": dict(tol),
            "control": _evidence_file(
                Path(native_shots[stem]), out_dir / "pixel" / f"{name}.control.png", out_dir
            ),
            "tavotto": _evidence_file(
                Path(tavotto_shots[stem]), out_dir / "pixel" / f"{name}.tavotto.png", out_dir
            ),
        }
        if diff_png.is_file():
            evidence["diff"] = {
                "path": diff_png.relative_to(out_dir).as_posix(),
                "sha256": cg.digest(diff_png.read_bytes()),
            }
        diffs.append(
            {
                "kind": "pixel_mismatch",
                "summary": f"{stem}: native vs Tavotto render differs ({'; '.join(reasons)})",
                "category": "export_or_fidelity",
                "evidence": evidence,
            }
        )
    unmatched = sorted(set(native_shots) ^ set(tavotto_shots))
    out = {
        "status": "failed" if diffs else "passed",
        "differences": diffs,
        "compared": len(stems),
    }
    if unmatched:
        # some figure on one side was never compared: with no other difference that is no
        # conclusion; next to a mismatch it is still reported (`unmatched`), never dropped
        out["unmatched"] = unmatched
        out["note"] = f"stems present on one side only: {unmatched}"
        if not diffs:
            out["status"] = "not_run"
    return out


def structure_judgment(parent_snap: dict, cand_snap: dict) -> dict:
    """Parent vs candidate `{stem: {"gid.prop": value}}` (shape-preserving candidates only).

    A parent key missing from the candidate, or present with another value, is a mismatch. Keys
    only the candidate has are not (a transform may add an artist). A stem the candidate lacks is
    a figure-count matter that `compare` already reports.
    """
    same = _cm()._same_value
    bad = []
    for stem in sorted(set(parent_snap) & set(cand_snap)):
        p, c = parent_snap[stem], cand_snap[stem]
        for key in sorted(p):
            if key not in c:
                bad.append({"stem": stem, "key": key, "parent": p[key], "candidate": "<missing>"})
            elif not same(p[key], c[key]):
                bad.append({"stem": stem, "key": key, "parent": p[key], "candidate": c[key]})
    if not bad:
        return {"status": "passed", "differences": [], "compared": len(parent_snap)}
    return {
        "status": "failed",
        "compared": len(parent_snap),
        "differences": [
            {
                "kind": "structure_mismatch",
                "summary": f"{len(bad)} manifest field(s) differ from the parent, first {bad[0]['stem']}:{bad[0]['key']}",
                "category": "manifest_semantics",
                "evidence": {"mismatches": bad[:20], "total": len(bad)},
            }
        ],
    }


def inherited_case(parent, manifest: dict) -> dict | None:
    """The parent's reviewed edit/export targets, for shape-preserving candidates only."""
    if manifest["class"] != "shape_preserving":
        return None
    spec = parent.case_spec
    return {
        "stem": spec.get("stem"),
        "mutations": list(spec.get("mutations") or []),
        "export_formats": list(spec.get("export_formats") or ["pdf", "png"]),
    }


def editable_snapshot(manifest: dict) -> dict:
    return _cm()._editable_snapshot(manifest)


def inherited_stages(worker, make_fresh, stem: str, case: dict, base_man: dict, work: Path) -> dict:
    """Run the parent's reviewed edit / replay / export on this candidate's worker.

    Returns `{"stages": {...}, "differences": [...]}`. All judging is `compat_matrix.stage_*`.
    """
    cm = _cm()
    stages: dict[str, dict] = {}
    diffs: list[dict] = []

    def fail(kind: str, category: str, name: str, summary: str, detail) -> None:
        stages[name] = stage(name, "failed", summary)
        diffs.append(
            {
                "kind": kind,
                "summary": f"{stem}: {summary}",
                "category": category,
                "evidence": {"stem": stem, "detail": cm._jsonable(detail)},
            }
        )

    patches: list = []
    try:
        edit = cm.stage_edit(worker, stem, case, base_man)
        patches = edit["full_patches"]
        if edit["ok"]:
            stages["edit"] = stage("edit", "passed")
        else:
            fail(
                "edit_failed",
                "editing_capability",
                "edit",
                "reviewed mutations did not apply / revert cleanly",
                edit["detail"],
            )
    except cm.Skip as exc:
        stages["edit"] = stage("edit", "not_covered", str(exc))
    except Exception as exc:  # noqa: BLE001 - product failure is an observation, recorded
        fail("edit_failed", "editing_capability", "edit", f"edit stage raised: {exc}"[:300], {})
    if patches:
        fresh = None
        try:
            fresh = make_fresh()
            rep = cm.stage_replay(worker, fresh, stem, patches)
            if rep["ok"]:
                stages["replay"] = stage("replay", "passed")
            else:
                fail(
                    "replay_mismatch",
                    "replay_inconsistency",
                    "replay",
                    "hot / replay / fresh-worker manifests disagree",
                    rep["detail"],
                )
        except Exception as exc:  # noqa: BLE001
            fail(
                "replay_mismatch",
                "replay_inconsistency",
                "replay",
                f"replay stage raised: {exc}"[:300],
                {},
            )
        finally:
            if fresh is not None:
                _discard(fresh)
    else:
        stages["replay"] = stage("replay", "not_covered", "no edit patches to replay")
    try:
        worker.override(stem, [])  # back to zero before exporting, as CompatBench does
        exp = cm.stage_export(worker, stem, work / "exports", case["export_formats"])
        if exp["ok"]:
            stages["export"] = stage("export", "passed")
        else:
            fail(
                "export_failed",
                "export_or_fidelity",
                "export",
                "real export failed or produced an unreadable file",
                exp["detail"],
            )
    except Exception as exc:  # noqa: BLE001
        fail(
            "export_failed",
            "export_or_fidelity",
            "export",
            f"export stage raised: {exc}"[:300],
            {},
        )
    return {"stages": stages, "differences": diffs}


def _discard(worker) -> None:
    from tavotto.engine import pool

    pool.discard(worker)
