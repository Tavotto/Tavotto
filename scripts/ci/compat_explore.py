"""CG-02 `explore`: derive candidates from reviewed parents, run them, record differences.

Trust model (see docs/ci/compat-guardian.md, "Explore"):
* Candidates are `parent + reviewed deterministic transform chain + seed`; the executor
  accepts only those three inputs and re-derives the files itself. There is no API that
  runs a path or a source string.
* A candidate has its parent's trust level, but only while it is deterministic and
  self-contained; anything uncertain (randomness, time, network, subprocess) is
  static-only and never executed.
* Execution reuses CG-01's isolation (private HOME/config/data, env allowlist, process-group
  timeout, out-of-project exec counters) and still is not OS isolation: hosted ephemeral
  runners or an isolated local directory only.
* Zero calls to dependency authorization/installation endpoints; nothing is installed.
* A found difference is a *candidate failure record*. Nothing here edits the baseline or the
  manifest, opens issues, or promotes anything.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path

import compat_explore_oracles as OR
import compat_explore_transforms as XT
import compat_guardian as cg
import compat_guardian_taxonomy as TX
from _common import write_report

SCHEMA = "tavotto-guardian-explore/1"
EXIT_CLEAN, EXIT_DIFFERENCES = cg.EXIT_PASSED, cg.EXIT_FAILED
#: Known product gaps the first transforms exist to find (GAP-REPORT G1/G2). AC-05 asks that
#: exploration finds them *by itself*; the report states whether it did.
KNOWN_GAPS = {
    "G1": {
        "transform": "show_close",
        "kind": "zero_figures",
        "meaning": "show() then close(): the user saw a figure, Tavotto captures none",
    },
    "G2": {
        "transform": "same_path_loop",
        "kind": "same_path_content_mismatch",
        "meaning": "several figures saved to one path: disk keeps the last, Tavotto keeps the first",
    },
}


# ---------------------------------------------------------------- Tavotto-side observation


def _titles(manifest: dict) -> list[str]:
    out = []
    for el in manifest.get("elements", []):
        if el.get("role") == "title":
            for field in el.get("editable", []):
                if field.get("prop") == "text" and field.get("value"):
                    out.append(str(field["value"]))
    return out


def tavotto_observe(project: Path, script: str, deep: dict | None = None) -> dict:
    """What the product's own discovery + worker path produces for this project.

    `deep` (CG-06) additionally takes, on the same worker, a manifest snapshot and a zero-patch
    PNG per stem, and runs the parent's reviewed edit / replay / export when `deep["case"]` is set.
    """
    from tavotto.engine import discover, pool, probe

    obs: dict = {"stems": {}, "error": None, "entry": None, "route": None}
    try:
        cfg, report, _changes = discover.merge(project)
        discover.write_config(project, cfg)
        info = report["scripts"].get(script) or {}
        entry, route = info.get("entry"), "static"
        if not entry or not info.get("stems"):
            result = probe.probe_and_register(project, script)
            route, entry = "probe", result.get("entry")
            if result.get("error") or not result.get("stems"):
                err = result.get("error") or {}
                obs.update(
                    route=route,
                    error={
                        "code": err.get("code") or "no_stems",
                        "message": str(err.get("message", ""))[:300],
                    },
                )
                return obs
        obs.update(route=route, entry=entry)
        worker = pool.one_shot(script, str(project), entry)
        try:
            built = worker.ensure_built()
            manifests = {}
            for stem in built.get("stems") or {}:
                manifest = worker.override(stem, [])["manifest"]
                manifests[stem] = manifest
                obs["stems"][stem] = {"titles": _titles(manifest)}
            if deep:
                _observe_deep(obs, worker, manifests, deep, project, script, entry)
        finally:
            pool.discard(worker)
    except Exception as exc:  # noqa: BLE001 - product failure is an observation, recorded
        code = getattr(exc, "code", None) or type(exc).__name__
        obs["error"] = {"code": str(code), "message": str(exc)[:300]}
    return obs


def _observe_deep(obs, worker, manifests, deep, project, script, entry) -> None:
    """Snapshots + zero-patch PNGs first (before any edit: zero patch means untouched)."""
    import compat_matrix as cm

    shots_dir = Path(deep["shots_dir"])
    shots_dir.mkdir(parents=True, exist_ok=True)
    obs["snapshots"], obs["shots"], obs["shot_errors"] = {}, {}, {}
    for stem, manifest in manifests.items():
        obs["snapshots"][stem] = OR.editable_snapshot(manifest)
        if not deep.get("render", True):
            continue
        try:
            dest = shots_dir / f"{Path(stem).name}.png"
            shutil.copy2(Path(worker.render_png(stem, cm.FIDELITY_WIDTH)), dest)
            obs["shots"][stem] = str(dest)
        except Exception as exc:  # noqa: BLE001
            obs["shot_errors"][stem] = str(exc)[:300]
    case = deep.get("case")
    if not case:
        return
    stem = case.get("stem")
    if stem not in manifests:
        obs["inherited"] = {
            "stages": {},
            "differences": [],
            "note": f"parent case stem {stem!r} not captured",
        }
        return
    obs["inherited"] = OR.inherited_stages(
        worker,
        lambda: cm._fresh_worker(script, project, entry),
        stem,
        case,
        manifests[stem],
        Path(deep["work"]),
    )


# ---------------------------------------------------------------- comparison (pure)


def native_facts(result: dict) -> dict:
    return {
        "ok": result.get("ok") is True and result.get("exit_status") == 0,
        "exit_status": result.get("exit_status"),
        "figure_count": result.get("figure_count"),
        "figures_shown": result.get("figures_shown"),
        "titles": [t for v in (result.get("semantics") or {}).values() for t in v["titles"]],
        "last_writer": result.get("savefig_last_writer") or {},
        "artifacts": sorted(a["path"] for a in result.get("artifacts", [])),
    }


def compare(
    candidate_class: str,
    native: dict,
    tavotto: dict,
    parent_native: dict | None,
    extra: list[dict] | None = None,
) -> dict:
    """Pure judgment of one executed candidate. Never decides anything about the baseline.

    `extra` are the CG-06 oracle differences (pixels, structure, edit, replay, export), appended
    after the figure-level ones.
    """
    nat = native_facts(native)
    if not nat["ok"]:
        status = cg._native_status(native)
        return {"outcome": status if status != "passed" else "invalid_fixture", "differences": []}
    if candidate_class == "shape_preserving":
        if parent_native is None or not native_facts(parent_native)["ok"]:
            return {
                "outcome": "unknown",
                "differences": [],
                "note": "no parent native to check preservation",
            }
        par = native_facts(parent_native)
        wanted = {k: par[k] for k in ("figure_count", "titles")}
        got = {k: nat[k] for k in ("figure_count", "titles")}
        if wanted != got:
            return {
                "outcome": "invalid_candidate",
                "differences": [],
                "note": f"transform changed native output: parent {wanted} / candidate {got}",
            }
    diffs = []
    n, stems = nat["figure_count"], tavotto.get("stems") or {}
    if tavotto.get("error") and not stems:
        if n:
            code = tavotto["error"]["code"]
            diffs.append(
                _diff(
                    "zero_figures",
                    f"native {n} figure(s) / Tavotto 0 figures (Tavotto: {code})",
                    "figure_capture",
                    {"native_figures": n, "tavotto_figures": 0, "tavotto_error": code},
                )
            )
    else:
        if 0 < len(stems) < n:
            diffs.append(
                _diff(
                    "fewer_figures",
                    f"native {n} figures / Tavotto {len(stems)} figure(s)",
                    "partial_capture",
                    {"native_figures": n, "tavotto_figures": len(stems)},
                )
            )
        if len(stems) > n:
            # 多捕获同样是差异（入口变换让绘图跑了两遍、兜底多收了一张……），不能因标题仍在而判 clean
            diffs.append(
                _diff(
                    "extra_figures",
                    f"native {n} figure(s) / Tavotto {len(stems)} figures",
                    "figure_capture",
                    {"native_figures": n, "tavotto_figures": len(stems)},
                )
            )
        if n and not stems:
            diffs.append(
                _diff(
                    "zero_figures",
                    f"native {n} figure(s) / Tavotto 0 figures",
                    "figure_capture",
                    {"native_figures": n, "tavotto_figures": 0},
                )
            )
        for path, writer in sorted(nat["last_writer"].items()):
            stem = Path(path).stem
            native_titles = [t for t in writer["titles"] if t]
            # An untitled figure carries no content to compare; [""] vs [] is not a difference.
            if stem in stems and native_titles and stems[stem]["titles"] != native_titles:
                diffs.append(
                    _diff(
                        "same_path_content_mismatch",
                        f"native last figure written to {path}: {native_titles} / "
                        f"Tavotto kept {stems[stem]['titles']}",
                        "figure_capture",
                        {
                            "path": path,
                            "native_titles": native_titles,
                            "tavotto_titles": stems[stem]["titles"],
                        },
                    )
                )
        seen = [v["titles"] for v in stems.values()]
        for title in nat["titles"]:
            if title and stems and not any(title in titles for titles in seen):
                diffs.append(
                    _diff(
                        "title_missing",
                        f"native figure title {title!r} absent from every Tavotto figure",
                        "manifest_semantics",
                        {"title": title},
                    )
                )
    diffs.extend(extra or [])
    if not diffs and tavotto.get("error") and stems:
        # 观察到一部分图之后 Tavotto 报错（后面的图没观察到）：不是「无差异」，是没比完（Codex #1135 r4310366048）
        return {
            "outcome": "unknown",
            "differences": [],
            "note": f"Tavotto error after partial observation: {tavotto['error'].get('code')}",
        }
    return {"outcome": "difference_found" if diffs else "no_difference", "differences": diffs}


def _diff(kind: str, summary: str, category: str, evidence: dict) -> dict:
    assert category in TX.CATEGORIES
    return {"kind": kind, "summary": summary, "category": category, "evidence": evidence}


# ---------------------------------------------------------------- the private adapter


def _write_project(root: Path, files: dict[str, bytes]) -> Path:
    project = root / "project"
    for rel, data in files.items():
        dest = cg.contained(project, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return project


def derive(
    parent_spec: dict, chain: list[str], seed: int, candidate_id: str, reduction: dict | None = None
):
    """The only way to obtain runnable candidate files: re-derive and verify the identity.

    A reduction (CG-03) is a set of statement deletions/unwraps applied to the re-derived
    candidate, so a reduced script is still a deterministic subset of a committed parent.
    """
    parent = XT.load_parent(parent_spec["suite"], parent_spec["case"])
    manifest, files = XT.build(parent, tuple(chain), seed)
    if manifest["candidate_id"] != candidate_id:
        raise cg.EvidenceError("candidate identity does not follow from (parent, chain, seed)")
    if reduction:
        manifest, files = XT.build_reduced(parent, tuple(chain), seed, reduction)
        manifest = {**manifest, "class": "axis_changing", "candidate_id": candidate_id}
    if manifest["execution_plan"]["status"] != "executable":
        raise cg.EvidenceError(
            "static-only candidate must not be executed: "
            + "; ".join(manifest["execution_plan"]["reasons"])
        )
    return parent, manifest, files


def audited_judgment(candidate_class: str, judgment: dict, executions: dict) -> dict:
    """「无差异」要有审计到的子进程执行（python_exec_audit）作证；计数缺失 = 观测器失效，不是 clean。"""
    audited = ("native", "native_pixels", "tavotto") + (
        ("parent_native",) if candidate_class == "shape_preserving" else ()
    )
    missing = [k for k in audited if not (executions.get(k) or 0) > 0]
    if judgment["outcome"] != "no_difference" or not missing:
        return judgment
    return {
        "outcome": "unknown",
        "differences": [],
        "note": "no audited child execution for: " + ", ".join(missing),
    }


def _count(path: Path) -> int | None:
    return cg._count(path)


def _native_pixels(cm, project: Path, script: str, entry: str, work: Path) -> dict:
    """Native PNGs at the CompatBench fidelity width (same request as `stage_fidelity`)."""
    return cm.run_driver(
        sys.executable,
        "native",
        {
            "script": str(project / script),
            "project": str(project),
            "engine_dir": str(cm.ENGINE_DIR),
            "entry": entry,
            "out_dir": str(work / "native-shots"),
            "width": cm.FIDELITY_WIDTH,
        },
    )


def oracle_layers(
    candidate_class: str,
    tavotto: dict,
    native_shots: dict,
    parent_snapshot: dict | None,
    evidence: Path,
    native_note: str | None,
) -> dict:
    """Judge the CG-06 layers of one executed candidate: `{"stages", "differences"}`."""
    stages = OR.blank_stages("not_run", "Tavotto observed nothing to judge")
    diffs: list[dict] = []
    if not tavotto.get("stems") and not native_shots:
        # no figure on either side: nothing for any layer to judge
        return {
            "stages": OR.blank_stages("not_covered", "no figure on either side"),
            "differences": diffs,
        }
    if not tavotto.get("stems") or "shots" not in tavotto:
        return {"stages": stages, "differences": diffs}
    # fidelity
    if native_note:
        stages["fidelity"] = OR.stage("fidelity", "not_run", native_note)
    else:
        pix = OR.pixel_judgment(
            native_shots,
            tavotto["shots"],
            evidence,
        )
        stages["fidelity"] = OR.stage("fidelity", pix["status"], pix.get("note"))
        if pix.get("unmatched"):
            stages["fidelity"]["unmatched"] = pix["unmatched"]
        diffs += pix["differences"]
    for stem, err in sorted(tavotto.get("shot_errors", {}).items()):
        stages["fidelity"] = OR.stage("fidelity", "failed", err)
        diffs.append(
            {
                "kind": "render_failed",
                "summary": f"{stem}: Tavotto could not render the zero-patch PNG ({err})",
                "category": "export_or_fidelity",
                "evidence": {"stem": stem, "error": err},
            }
        )
    # structure + inherited edit / replay / export
    if candidate_class == "shape_preserving":
        if parent_snapshot is None:
            stages["structure"] = OR.stage(
                "structure", "not_run", "parent snapshot unavailable (not a difference)"
            )
        else:
            st = OR.structure_judgment(parent_snapshot, tavotto["snapshots"])
            stages["structure"] = OR.stage("structure", st["status"])
            diffs += st["differences"]
        inherited = tavotto.get("inherited")
        if inherited is not None:
            for name, stage in inherited["stages"].items():
                stages[name] = stage
            for name in ("edit", "replay", "export"):
                if name not in inherited["stages"]:
                    stages[name] = OR.stage(name, "not_run", inherited.get("note", "not run"))
            diffs += inherited["differences"]
    else:
        for name in ("structure", "edit", "replay", "export"):
            stages[name] = OR.stage(name, "not_covered", OR.NOT_COVERED_AXIS_CHANGING)
    return {"stages": stages, "differences": diffs}


def native_pixel_note(native: dict, pixels: dict) -> str | None:
    """Why the native pixel reference did not run, or None when it ran (or is structurally absent).

    「没有原生像素参照」只在脚本确实没用 savefig 存过图时才是结构上不适用（`not_covered`）；
    存过图却没渲染出来是没跑成，要让候选落到 unknown。
    """
    if not pixels.get("ok"):
        return f"native render failed: {pixels.get('code')}"
    written = sorted(native_facts(native)["last_writer"])  # savefig 写过的路径，不含别的产出文件
    if written and not pixels.get("shots"):
        return f"native wrote {written} but the pixel run rendered no figure"
    return None


def apply_unresolved(judgment: dict, stages: dict) -> tuple[dict, list[str]]:
    """An in-scope layer that did not run (`not_run`) leaves a clean candidate without a verdict.

    A found difference stays a difference (the missing layers are reported next to it);
    `not_covered` layers are structurally inapplicable and never downgrade anything.
    """
    missing = OR.unresolved(stages)
    if missing and judgment["outcome"] == "no_difference":
        judgment = {
            "outcome": "unknown",
            "differences": [],
            "note": "layer(s) did not run: " + "; ".join(missing),
        }
    return judgment, missing


def execute_request(request: dict) -> dict:
    """Runs inside the isolated adapter process (see launch in `explore`)."""
    import compat_matrix as cm

    work, evidence = Path(request["work"]), Path(request["evidence"])
    if request.get("mode") == "parent_snapshot":
        return _parent_snapshot_request(request, work)
    parent, manifest, files = derive(
        request["parent"],
        request["chain"],
        request["seed"],
        request["candidate_id"],
        request.get("reduction"),
    )
    result: dict = {"candidate_id": manifest["candidate_id"], "stage_errors": []}
    t0 = time.monotonic()
    os.environ["CG_EXEC_LOG"] = str(work / "native-execs.jsonl")
    native_project = _write_project(work / "native", files)
    native = cm.run_driver(
        sys.executable,
        "native_ref",
        {
            "project": str(native_project),
            "script": str(native_project / parent.script),
            "entry": manifest["entry"],
        },
    )
    result["native"] = cg._trim_native(native)
    result["timings_s"] = {"native": round(time.monotonic() - t0, 3)}
    parent_native = None
    if manifest["class"] == "shape_preserving":
        os.environ["CG_EXEC_LOG"] = str(work / "parent-execs.jsonl")
        parent_project = _write_project(work / "parent", parent.files)
        parent_native = cm.run_driver(
            sys.executable,
            "native_ref",
            {
                "project": str(parent_project),
                "script": str(parent_project / parent.script),
                "entry": parent.entry,
            },
        )
        result["parent_native"] = cg._trim_native(parent_native)
    layers = None
    if native_facts(native)["ok"]:
        # 出像素这次原生运行单独记审计：「原生参照执行次数」只数 native_ref 那一次
        os.environ["CG_EXEC_LOG"] = str(work / "native-pixels-execs.jsonl")
        pixels = _native_pixels(cm, native_project, parent.script, manifest["entry"], work)
        native_note = native_pixel_note(native, pixels)
        os.environ["CG_EXEC_LOG"] = str(work / "tavotto-execs.jsonl")
        t1 = time.monotonic()
        tavotto_project = _write_project(work / "tavotto", files)
        deep = {
            "shots_dir": str(work / "tavotto-shots"),
            "work": str(work),
            "case": OR.inherited_case(parent, manifest),
        }
        result["tavotto"] = tavotto_observe(tavotto_project, parent.script, deep)
        result["timings_s"]["tavotto"] = round(time.monotonic() - t1, 3)
        layers = oracle_layers(
            manifest["class"],
            result["tavotto"],
            pixels.get("shots") or {},
            request.get("parent_snapshot") if not request.get("reduction") else None,
            evidence,
            native_note,
        )
    else:
        result["tavotto"] = {"stems": {}, "error": None, "skipped": "native reference failed"}
    result["executions"] = {
        "native": _count(work / "native-execs.jsonl"),
        "parent_native": _count(work / "parent-execs.jsonl"),
        "native_pixels": _count(work / "native-pixels-execs.jsonl"),
        "tavotto": _count(work / "tavotto-execs.jsonl"),
        "basis": "python_exec_audit",
    }
    result["judgment"] = compare(
        manifest["class"],
        native,
        result["tavotto"],
        parent_native,
        layers["differences"] if layers else None,
    )
    result["judgment"] = audited_judgment(
        manifest["class"], result["judgment"], result["executions"]
    )
    judged = layers and result["judgment"]["outcome"] in ("difference_found", "no_difference")
    if judged:
        result["judgment"], result["unresolved_layers"] = apply_unresolved(
            result["judgment"], layers["stages"]
        )
    result["stages"] = (
        layers["stages"]
        if judged
        else OR.blank_stages("not_run", f"outcome {result['judgment']['outcome']}")
    )
    # the layer evidence belongs with the candidate; the tavotto-side shots are copied by the
    # pixel judgment only when they differ
    for name in ("native", "native-pixels", "parent", "tavotto"):
        log = work / f"{name}-execs.jsonl"
        if log.exists():
            shutil.copy2(log, evidence / f"{name}-execs.jsonl")
    return result


def _parent_snapshot_request(request: dict, work: Path) -> dict:
    """Tavotto's own manifest snapshot of an unmodified parent (computed once per explore run)."""
    parent = XT.load_parent(request["parent"]["suite"], request["parent"]["case"])
    os.environ["CG_EXEC_LOG"] = str(work / "tavotto-execs.jsonl")
    project = _write_project(work / "parent-snapshot", parent.files)
    obs = tavotto_observe(
        project,
        parent.script,
        {"render": False, "shots_dir": str(work / "unused-shots"), "case": None},
    )
    log = work / "tavotto-execs.jsonl"
    if log.exists():
        shutil.copy2(log, Path(request["evidence"]) / "tavotto-execs.jsonl")
    return {"snapshots": obs.get("snapshots"), "error": obs.get("error")}


# ---------------------------------------------------------------- orchestration


def select(candidates: list[dict], limit: int | None) -> list[dict]:
    """Deterministic, family-balanced selection so a small limit still spans every axis."""
    if limit is not None and limit < 1:
        raise cg.EvidenceError(f"--limit must be a positive integer, got {limit}")
    if limit is None or limit >= len(candidates):
        return candidates
    buckets: dict[str, list[dict]] = {}
    for c in candidates:
        buckets.setdefault(c["axis_vector"][0], []).append(c)
    chosen: list[dict] = []
    keys = sorted(buckets)
    while len(chosen) < limit and any(buckets.values()):
        for key in keys:
            if buckets[key] and len(chosen) < limit:
                chosen.append(buckets[key].pop(0))
    return sorted(chosen, key=lambda c: candidates.index(c))


def write_candidates(generated: dict, out: Path) -> Path:
    """Deterministic artifacts: same seed gives byte-identical files and candidates.json."""
    listing = {k: v for k, v in generated.items() if k != "_files"}
    for cid, files in generated["_files"].items():
        for rel, data in files.items():
            dest = cg.contained(out / "candidates" / cid / "files", rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    path = out / "candidates.json"
    path.write_text(json.dumps(listing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _adapter_run(request: dict, hashes: list[str], evidence: Path, timeout: float) -> dict:
    """One isolated adapter process; returns its `result.json`. Raises like `launch_adapter`."""
    with tempfile.TemporaryDirectory(prefix="compat-explore-") as tmp:
        work = Path(tmp)
        request = {**request, "work": str(work), "evidence": str(evidence)}
        (work / "request.json").write_text(json.dumps(request), encoding="utf-8")
        env = cg.isolated_env(work, work / "adapter-execs.jsonl", hashes)
        try:
            cg.launch_adapter(
                [
                    sys.executable,
                    str(Path(cg.__file__).resolve()),
                    "_explore_execute",
                    str(work / "request.json"),
                ],
                work=work,
                evidence=evidence,
                env=env,
                timeout=timeout,
            )
            return json.loads((evidence / "result.json").read_text(encoding="utf-8"))
        finally:
            for log in work.glob("*-execs.jsonl"):
                shutil.copy2(log, evidence / log.name)


#: Per explore run (keyed by its output dir): the unmodified parent's Tavotto manifest snapshot,
#: computed once per parent, however many candidates derive from it.
_PARENT_SNAPSHOTS: dict[tuple, dict] = {}


def parent_snapshot(parent_spec: dict, out: Path, timeout: float) -> dict:
    """`{"snapshots": {stem: {gid.prop: value}} | None, "error": ...}`; failure is data, not a raise."""
    key = (str(out), parent_spec["suite"], parent_spec["case"])
    if key not in _PARENT_SNAPSHOTS:
        parent = XT.load_parent(parent_spec["suite"], parent_spec["case"])
        evidence = out / "evidence" / f"parent-{parent_spec['case']}"
        evidence.mkdir(parents=True)
        try:
            record = _adapter_run(
                {"mode": "parent_snapshot", "parent": dict(parent_spec)},
                [cg.digest(parent.files[parent.script])],
                evidence,
                timeout,
            )
        except (cg.AdapterTimeout, cg.EvidenceError, OSError, ValueError) as exc:
            record = {
                "snapshots": None,
                "error": {"code": "runner_error", "message": str(exc)[:200]},
            }
        if record.get("error") or not record.get("snapshots"):
            record = {"snapshots": None, "error": record.get("error") or {"code": "no_stems"}}
        _PARENT_SNAPSHOTS[key] = record
    return _PARENT_SNAPSHOTS[key]


def run_one(
    manifest: dict,
    seed: int,
    out: Path,
    timeout: float,
    reduction: dict | None = None,
    tag: str | None = None,
) -> dict:
    cid = manifest["candidate_id"]
    evidence = out / "evidence" / (f"{cid}-{tag}" if tag else cid)
    evidence.mkdir(parents=True)
    request = {
        "parent": {"suite": manifest["parent"]["suite"], "case": manifest["parent"]["case"]},
        "chain": [s["name"] for s in manifest["chain"]],
        "seed": seed,
        "candidate_id": cid,
    }
    if reduction:
        request["reduction"] = reduction
    parent = XT.load_parent(request["parent"]["suite"], request["parent"]["case"])
    script_hash = manifest["files"][parent.script]
    if reduction:
        script_hash = XT.build_reduced(parent, tuple(request["chain"]), seed, reduction)[0][
            "files"
        ][parent.script]
    hashes = [script_hash, cg.digest(parent.files[parent.script])]
    if manifest["class"] == "shape_preserving" and not reduction:
        request["parent_snapshot"] = parent_snapshot(request["parent"], out, timeout)["snapshots"]
    try:
        record = _adapter_run(request, hashes, evidence, timeout)
    except (cg.AdapterTimeout, cg.EvidenceError, OSError, ValueError) as exc:
        kind = "adapter_timeout" if isinstance(exc, cg.AdapterTimeout) else "runner_error"
        record = {
            "candidate_id": cid,
            "judgment": {
                "outcome": "unknown",
                "differences": [],
                "note": f"{kind}: {str(exc)[:200]}",
            },
            "executions": {
                "native": None,
                "parent_native": None,
                "native_pixels": None,
                "tavotto": None,
                "basis": "unobserved",
            },
            "stages": OR.blank_stages("not_run", kind),
        }
    return record


#: 生成 / 执行派生候选、采集证据的代码都在这里（driver、matrix、执行观测器、变换……）：
#: 执行前整个目录必须与 HEAD 一致（含未跟踪文件），不靠逐个列名单（Codex #1135 r4312053946）
EXECUTION_CODE = "scripts/ci"


def dirty_execution_code() -> list[str]:
    out = cg.git("status", "--porcelain", "--untracked-files=all", "--", EXECUTION_CODE)
    # `cg.git` strips the output, so the first line may have lost its leading status space
    return sorted(line.split(maxsplit=1)[1] for line in out.splitlines() if line.strip())


def explore(args) -> int:
    initial = cg.source_state()
    expected = args.expected_sha or initial["commit"]
    if expected != initial["commit"]:
        raise cg.EvidenceError("expected SHA is not actual HEAD")
    if not args.static_only:
        # 候选标「评审过的模板派生、继承父用例信任」，前提是生成 / 执行它的代码就是 HEAD 那份：
        # 脏工作区里改过的变换会被当成已评审代码执行（Codex #1135 r4311941518）
        dirty = dirty_execution_code()
        if dirty:
            raise cg.EvidenceError(
                f"execution code under {EXECUTION_CODE}/ differs from reviewed HEAD: {dirty}; "
                "commit them or run --static-only"
            )
    parents = XT.default_parents()
    if args.parent:
        requested = args.parent.split(",")
        known = {p.case for p in parents}
        bad = sorted({r for r in requested if r not in known})
        if bad or len(requested) != len(set(requested)):
            raise cg.EvidenceError(
                "--parent must be distinct, non-empty parent ids; "
                + (f"unknown: {bad}" if bad else "duplicate entry")
            )
        parents = [p for p in parents if p.case in set(requested)]
    if getattr(args, "families", None):
        unknown = set(args.families.split(",")) - {t.family for t in XT.TRANSFORMS}
        if unknown:
            raise cg.EvidenceError(f"unknown transform family: {sorted(unknown)}")
    generated = XT.generate(args.seed, parents)
    run_id = uuid.uuid4().hex
    out = Path(args.out).resolve() / run_id
    if out.is_relative_to(cg.ROOT):
        raise cg.EvidenceError("--out must be outside the source worktree")
    out.mkdir(parents=True)
    write_candidates(generated, out)
    pool = generated["candidates"]
    if getattr(args, "families", None):
        wanted_families = set(args.families.split(","))
        known = {t.family for t in XT.TRANSFORMS}
        if not wanted_families <= known:
            raise cg.EvidenceError(f"unknown transform family: {sorted(wanted_families - known)}")
        # 每一步都要在所选族里：混入未选族的两因子链会让差异来自操作者排除掉的变换（Codex #1139 r4311734919）
        pool = [c for c in pool if all(s["family"] in wanted_families for s in c["chain"])]
    chosen = select(pool, args.limit)
    if getattr(args, "candidates", None):
        wanted_ids = set(args.candidates.split(","))
        chosen = [c for c in generated["candidates"] if c["candidate_id"] in wanted_ids]
        if len(chosen) != len(wanted_ids):
            raise cg.EvidenceError("--candidates names an id this seed does not generate")
    if not chosen:
        # An empty selection observed nothing; `all([])` must never read as a clean run.
        raise cg.EvidenceError("no candidates selected (not_run); no report published")
    started = time.monotonic()
    rows = []
    by_id: dict[str, dict] = {}
    for manifest in chosen:
        plan = manifest["execution_plan"]
        if args.static_only or plan["status"] != "executable":
            judgment = {"outcome": "static_only", "differences": []}
            record = {
                "judgment": judgment,
                "executions": {
                    "native": 0,
                    "parent_native": 0,
                    "native_pixels": 0,
                    "tavotto": 0,
                    "basis": "not_executed",
                },
            }
            record["static_only_reasons"] = plan["reasons"] or ["--static-only"]
        else:
            record = run_one(manifest, args.seed, out, args.timeout)
        row = {
            "candidate_id": manifest["candidate_id"],
            "parent": manifest["parent"]["case"],
            "suite": manifest["parent"]["suite"],
            "chain": [s["name"] for s in manifest["chain"]],
            "axis_vector": manifest["axis_vector"],
            "class": manifest["class"],
            "seed": manifest["seed"],
            "ast_signature": manifest["ast_signature"],
            "trust": manifest["trust"],
            "outcome": record["judgment"]["outcome"],
            "differences": record["judgment"]["differences"],
            "note": record["judgment"].get("note")
            or "; ".join(record.get("static_only_reasons", [])),
            "executions": record["executions"],
            "unresolved_layers": record.get("unresolved_layers", []),
            "stages": record.get("stages")
            or OR.blank_stages(
                "not_run", "; ".join(record.get("static_only_reasons", [])) or "not executed"
            ),
            "tavotto_route": (record.get("tavotto") or {}).get("route"),
            "evidence": evidence_summary(record),
            "timings_s": record.get("timings_s"),
        }
        rows.append(row)
        by_id[row["candidate_id"]] = manifest
        flag = f"  <- {row['differences'][0]['summary']}" if row["differences"] else ""
        print(
            f"{row['candidate_id']} {row['parent']} {'+'.join(row['chain'])}: {row['outcome']}{flag}",
            flush=True,
        )
    confirm_differences(rows, by_id, args, out)
    if cg.source_state() != initial:
        raise cg.EvidenceError("source content changed during explore; no report published")
    report = {
        "schema": SCHEMA,
        "run_id": run_id,
        "seed": args.seed,
        "source": initial,
        "expected_sha": expected,
        "generator": generated["generator"],
        "candidates": rows,
        "coverage": generated["coverage"],
        "known_gaps": known_gap_findings(rows),
        "outcome_counts": counts(rows),
        "stage_counts": stage_counts(rows),
        "oracles": {
            "pixel_tolerance": dict(_fidelity_tolerance()),
            "pixel_tolerance_source": "compat_matrix.FIDELITY_TOLERANCE (same as CompatBench)",
            "parent_snapshots": sum(1 for k in _PARENT_SNAPSHOTS if k[0] == str(out)),
        },
        "duration_s": round(time.monotonic() - started, 3),
        "unverified": [
            "candidates are derived from corpus shapes that already pass; agreement says nothing about other shapes",
            "native is plain `python script.py` in a private temp dir, not the user's environment",
            "differences are candidate failure records, not baseline changes and not issues",
            "V2 parents are static-only (the V2 session adapter is not driven by explore)",
            "pixels use CompatBench's tolerance, so agreement is 'within that tolerance', not identical",
            "edit / replay / export / structure run only for shape-preserving candidates, on the parent's reviewed targets; axis-changing candidates report them as not_covered",
        ],
    }
    (out / "reports").mkdir()
    path = write_report("explore-report.json", report, root=out)
    (out / "reports" / "explore-summary.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"report": str(path), "run_id": run_id, "outcomes": report["outcome_counts"]}))
    if any(r["outcome"] == "difference_found" for r in rows):
        return EXIT_DIFFERENCES
    # clean 要求真比对过：全是 static_only（没有一次原生 / Tavotto 对照）不是「无差异」（AC-11，Codex #1135 r4306059452）
    if all(r["outcome"] in ("no_difference", "static_only") for r in rows) and any(
        r["outcome"] == "no_difference" for r in rows
    ):
        return EXIT_CLEAN
    return cg.EXIT_UNKNOWN


def evidence_summary(record: dict) -> dict | None:
    """The facts triage needs, trimmed: no raw logs, no full traces."""
    native, tavotto = record.get("native"), record.get("tavotto")
    if not native:
        return None
    return {
        "native": {
            "exit_status": native.get("exit_status"),
            "figure_count": native.get("figure_count"),
            "figures_shown": native.get("figures_shown"),
            "figures_saved": native.get("figures_saved"),
            "savefig_calls": native.get("savefig_calls"),
            "artifacts": sorted(a["path"] for a in native.get("artifacts", [])),
            "last_writer": native.get("savefig_last_writer") or {},
            "same_path_distinct_figures": native.get("same_path_distinct_figures") or [],
            "titles": [t for v in (native.get("semantics") or {}).values() for t in v["titles"]],
        },
        "tavotto": {
            "route": (tavotto or {}).get("route"),
            "error": (tavotto or {}).get("error"),
            "stems": {k: v["titles"] for k, v in ((tavotto or {}).get("stems") or {}).items()},
        },
    }


def confirm_differences(rows: list[dict], by_id: dict, args, out: Path) -> None:
    """Re-run up to N differing candidates (one per distinct finding shape) to see if they reproduce.

    Only a confirmed difference may raise a maintainer alert; the rerun is the same trusted,
    re-derived execution, not a different path.
    """
    budget = getattr(args, "confirm_differences", 0) or 0
    for row in rows:
        row["confirmation"] = None
    import compat_triage as TG

    rules = TG.load_rules()

    def source_of(row: dict) -> str | None:
        script = ((by_id.get(row["candidate_id"]) or {}).get("parent") or {}).get("script")
        path = out / "candidates" / row["candidate_id"] / "files" / (script or "")
        return path.read_text(encoding="utf-8") if script and path.is_file() else None

    seen: set[tuple] = set()
    for row in rows:
        if budget <= 0:
            break
        if row["outcome"] != "difference_found":
            continue
        # The same behavior identity triage clusters on (attribution, relation, call site), so two
        # candidates triage would keep apart are never collapsed into one confirmation.
        source = source_of(row)
        identity = TG.explore_identity(row, source, rules)
        if identity in seen:
            continue
        seen.add(identity)
        budget -= 1
        again = run_one(by_id[row["candidate_id"]], args.seed, out, args.timeout, None, "confirm")
        rerun_row = {
            **row,
            "outcome": again["judgment"]["outcome"],
            "differences": again["judgment"]["differences"],
            "evidence": evidence_summary(again),
        }
        row["confirmation"] = {
            "reproduced": again["judgment"]["outcome"] == "difference_found"
            and TG.explore_identity(rerun_row, source, rules) == identity,
            "runs": 1,
        }
        print(
            f"{row['candidate_id']} confirm: {'reproduced' if row['confirmation']['reproduced'] else 'NOT reproduced'}",
            flush=True,
        )


def _fidelity_tolerance() -> dict:
    import compat_matrix

    return compat_matrix.FIDELITY_TOLERANCE


def stage_counts(rows: list[dict]) -> dict:
    """`{stage: {status: n}}` over the executed rows, so a layer that never ran is visible."""
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        for name, st in (r.get("stages") or {}).items():
            bucket = out.setdefault(name, {})
            bucket[st["status"]] = bucket.get(st["status"], 0) + 1
    return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}


def counts(rows: list[dict]) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        out[r["outcome"]] = out.get(r["outcome"], 0) + 1
    return dict(sorted(out.items()))


def known_gap_findings(rows: list[dict]) -> dict:
    """AC-05: did exploration itself find the two audited gaps (not a hard-coded check)?"""
    result = {}
    for gap, spec in KNOWN_GAPS.items():
        hits = [
            r["candidate_id"]
            for r in rows
            if spec["transform"] in r["chain"]
            and any(d["kind"] == spec["kind"] for d in r["differences"])
        ]
        result[gap] = {**spec, "found": bool(hits), "candidates": hits}
    return result


def render_markdown(report: dict) -> str:
    lines = [
        f"### Guardian explore seed {report['seed']} run `{report['run_id'][:12]}`",
        "outcomes: " + ", ".join(f"{k}={v}" for k, v in report["outcome_counts"].items()),
        "layers: "
        + "; ".join(
            f"{k}: " + ", ".join(f"{s}={n}" for s, n in v.items())
            for k, v in report["stage_counts"].items()
        ),
        "",
    ]
    for gap, info in report["known_gaps"].items():
        lines.append(
            f"- {gap} ({info['meaning']}): {'FOUND by ' + ', '.join(info['candidates'][:3]) if info['found'] else 'not found'}"
        )
    lines += [
        "",
        "| candidate | parent | chain | outcome | difference |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in report["candidates"]:
        diff = r["differences"][0]["summary"] if r["differences"] else ""
        lines.append(
            f"| {r['candidate_id']} | {r['parent']} | {'+'.join(r['chain'])} | {r['outcome']} | {diff} |"
        )
    lines += [
        "",
        "Differences are candidate failure records only; no baseline or manifest was changed.",
    ]
    return "\n".join(lines) + "\n"


def private_main(request_path: str) -> int:
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    try:
        result = execute_request(request)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2
    (Path(request["evidence"]) / "result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return 0
