#!/usr/bin/env python3
"""Registered Compatibility Guardian runner; no arbitrary-script execution API.

Reuses CompatBench, its native driver, and V2/Foundation authenticated server
harness. Directory/environment isolation is not OS isolation: only reviewed,
unchanged repository fixtures execute. No dependency installation or promotion.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path, PureWindowsPath

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import compat_corpus as CC  # noqa: E402
import package_smoke  # noqa: E402
from _common import write_report  # noqa: E402
from guardian_exec_observer import actual_environment  # noqa: E402

SCHEMA_VERSION = "1.0.0"
STATUSES = (
    "passed",
    "failed",
    "expected_limitation",
    "not_reproduced",
    "environment_unavailable",
    "not_run",
    "invalid_fixture",
    "unknown",
)
SUITES = ("compat_engine", "compat_v2")
SCHEMA_PATH = ROOT / "tests" / "compat" / "guardian-report.schema.json"
REQUIRED_MODULES = (
    "flask",
    "pikepdf",
    "uharfbuzz",
    "pypdfium2",
    "PIL",
    "matplotlib",
    "numpy",
    "packaging",
)


class EvidenceError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(data) -> str:
    return digest(json.dumps(data, sort_keys=True, separators=(",", ":")).encode())


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT).decode().strip()


def source_state() -> dict:
    files = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
        )
        .decode()
        .split("\0")
    )
    content = {}
    for name in sorted(set(files) - {""}):
        p = ROOT / name
        if p.is_symlink():
            content[name] = "symlink:" + os.readlink(p)
        elif p.is_file():
            content[name] = digest(p.read_bytes())
        else:
            content[name] = "missing"
    return {
        "commit": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "dirty": bool(git("status", "--porcelain")),
        "content_sha256": canonical(content),
    }


def contained(root: Path, name: str) -> Path:
    p = Path(name)
    if (
        p.is_absolute()
        or PureWindowsPath(name).is_absolute()
        or ".." in p.parts
        or "\\" in name
        or not name
    ):
        raise EvidenceError(f"unsafe relative path: {name}")
    candidate = root / p
    if candidate.is_symlink() or not candidate.resolve().is_relative_to(root.resolve()):
        raise EvidenceError(f"symlink/outside root: {name}")
    return candidate


def environment() -> dict:
    return actual_environment()


def preflight() -> list[str]:
    missing = [f"module:{m}" for m in REQUIRED_MODULES if importlib.util.find_spec(m) is None]
    allowlist = json.loads((ROOT / "src/tavotto/rendercore/fonts_allowlist.json").read_text())
    for face in allowlist["faces"].values():
        path = ROOT / "src/tavotto/resources/fonts" / face["file"]
        if not path.is_file() or digest(path.read_bytes()) != face["sha256"]:
            missing.append("font:" + face["file"])
    return missing


def registered(suite: str) -> dict[str, dict]:
    metadata = (
        ["tests/compat/manifest.json"]
        if suite == "compat_engine"
        else ["tests/support/compat_v2_scenarios.py", "tests/support/compatbench_v2.py"]
    )
    for rel in metadata:
        blob = subprocess.check_output(["git", "show", f"HEAD:{rel}"], cwd=ROOT)
        if contained(ROOT, rel).read_bytes() != blob:
            raise EvidenceError(f"execution metadata differs from reviewed HEAD: {rel}")
    if suite == "compat_engine":
        return {c["id"]: c for c in CC.load_manifest()["cases"]}
    if suite == "compat_v2":
        from support import compat_v2_scenarios as cs

        return {sid: {"id": sid} for sid in cs.SCENARIOS}
    raise EvidenceError(f"unregistered suite: {suite}")


def fixture_files(suite: str, case: dict) -> dict[str, str]:
    if suite == "compat_v2":
        base = ROOT / "tests/fixtures/compat_v2" / case["id"]
        paths = sorted(p for p in base.rglob("*") if p.is_file())
    else:
        # Validate materialization destinations as well as source paths.
        contained(Path("/guardian-project"), case.get("script_dest") or Path(case["script"]).name)
        for dest in case.get("extra_files") or {}:
            contained(Path("/guardian-project"), dest)
        for asset in case.get("assets", []):
            contained(Path("/guardian-project"), asset)
        paths = [CC.COMPAT_DIR / case["script"]]
        paths += [CC.COMPAT_DIR / p for p in (case.get("extra_files") or {}).values()]
        paths += [CC.ASSETS_DIR / p for p in case.get("assets", [])]
    result = {}
    for p in paths:
        rel = p.relative_to(ROOT).as_posix()
        checked = contained(ROOT, rel)
        # The review boundary is the committed repository corpus, not a CLI flag.
        blob = subprocess.check_output(["git", "show", f"HEAD:{rel}"], cwd=ROOT)
        if blob != checked.read_bytes():
            raise EvidenceError(f"fixture differs from reviewed HEAD: {rel}")
        result[rel] = digest(blob)
    if not result:
        raise EvidenceError("empty fixture")
    return result


def isolated_env(work: Path, audit_log: Path | None = None, hashes=()) -> dict:
    # An allowlist deliberately excludes connector/cloud credentials and personal config.
    env = {
        k: os.environ[k]
        for k in (
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "PATHEXT",
            "LD_LIBRARY_PATH",
            "DYLD_LIBRARY_PATH",
        )
        if k in os.environ
    }
    for key, sub in {
        "HOME": "home",
        "USERPROFILE": "home",
        "APPDATA": "appdata",
        "LOCALAPPDATA": "localappdata",
        "XDG_CACHE_HOME": "cache",
        "XDG_CONFIG_HOME": "config",
        "XDG_DATA_HOME": "data",
        "TAVOTTO_DATA_DIR": "data",
        "TAVOTTO_CONFIG_DIR": "config",
        "MPLCONFIGDIR": "mpl",
        "TMPDIR": "tmp",
        "TEMP": "tmp",
        "TMP": "tmp",
    }.items():
        path = work / sub
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env.update(
        TAVOTTO_NO_TELEMETRY="1",
        TAVOTTO_NO_DIAG_UPLOAD="1",
        TAVOTTO_USER_ENV_DISCOVERY="0",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUTF8="1",
        PYTHONHASHSEED="0",
        MPLBACKEND="Agg",
    )
    roots = [str(ROOT / "src"), str(ROOT / "tests"), str(HERE)]
    if audit_log is not None:
        bootstrap = work / "bootstrap"
        bootstrap.mkdir()
        (bootstrap / "sitecustomize.py").write_text(
            "from guardian_exec_observer import install\ninstall()\n"
        )
        roots.insert(0, str(bootstrap))
        env.update(
            CG_EXEC_ROOT=str(work),
            CG_EXEC_LOG=str(audit_log),
            CG_EXEC_HASHES=json.dumps(list(hashes)),
        )
    env["PYTHONPATH"] = os.pathsep.join(roots)
    # Explicit user-selected test interpreter; never let native/worker choose different stacks.
    env["TAVOTTO_WORKER_PYTHON"] = sys.executable
    return env


def stage(status: str, criterion: str) -> dict:
    return {"status": status, "criterion": criterion}


def case_status(row: dict) -> str:
    native = row["native"]["status"]
    if native != "passed":
        return native
    if not row["comparable"] or not (row["executions"]["native"] or 0):
        return "unknown"
    statuses = [s["status"] for s in row["stages"].values()]
    raw = row.get("raw") or {}
    if any(
        r.get("status") == "fail" for r in (raw.get("routes") or {}).values() if isinstance(r, dict)
    ):
        return "failed"
    if isinstance(raw.get("browser"), dict) and raw["browser"].get("ok") is False:
        return "failed"
    if "failed" in statuses:
        return "failed"
    if row.get("product_classification") == "product_bug":
        return "unknown"  # a legacy label alone is not independent failure evidence
    if row["executions"]["tavotto"] in (None, 0):
        return "unknown"
    if all(s == "passed" for s in statuses):
        return "passed"
    if "expected_limitation" in statuses:
        return "expected_limitation"
    return "unknown"


def verdict(rows: list[dict]) -> str:
    if not rows:
        return "unknown"
    statuses = [r["status"] for r in rows]
    if "failed" in statuses:
        return "failed"
    if all(s == "passed" for s in statuses):
        return "passed"
    if all(s == "environment_unavailable" for s in statuses):
        return "environment_unavailable"
    if all(s == "invalid_fixture" for s in statuses):
        return "invalid_fixture"
    return "unknown"


def _native_status(result: dict) -> str:
    if result.get("ok") is True and result.get("stems"):
        return "passed"
    tb = result.get("traceback", "")
    if "ModuleNotFoundError:" in tb or result.get("code") in ("driver_timeout", "driver_no_output"):
        return "environment_unavailable"
    if result.get("code") == "native_script_error":
        return "invalid_fixture"
    return "unknown"


def _execute(request: dict) -> dict:
    """Private adapter, launched only with a request created by run()."""
    import compat_matrix as cm

    suite, cid = request["suite"], request["case"]
    case = registered(suite)[cid]
    work = Path(request["work"])
    evidence = Path(request["evidence"])
    before = environment()
    row = {
        "id": cid,
        "status": "unknown",
        "layer": "engine" if suite == "compat_engine" else "authenticated_server_safe",
        "profile": "safe",
        "desktop_shell_verified": False,
        "stages": {s: stage("not_run", "not reached") for s in CC.STAGES},
        "executions": {"native": None, "tavotto": None, "basis": "python_exec_audit"},
        "native": {"status": "not_run", "result": {}},
        "comparable": False,
        "environment_before": before,
        "environment_after": before,
        "product_classification": None,
        "raw": {},
        "unverified": [
            "real desktop shell",
            "save occurrence snapshots",
            "arbitrary user scripts",
            "scientific values beyond registered oracle",
        ],
    }
    missing = preflight()
    if any(f"module:{name}" in missing for name in ("matplotlib", "numpy", "PIL")):
        row["status"] = "environment_unavailable"
        row["native"]["status"] = "environment_unavailable"
        row["raw"] = {"missing_prerequisites": missing}
        return row
    native_root = work / "native-control"
    native_root.mkdir()
    if suite == "compat_engine":
        project, script, entry = cm.materialize([case], native_root)
    else:
        from support import compat_v2_scenarios as cs, compatbench_v2 as cb

        project, script, entry = cb.stage(cid, native_root), "script.py", "__main__"
    os.environ["CG_EXEC_LOG"] = str(work / "native-execs.jsonl")
    # The already installed audit hook opened its file at process start; child
    # native driver receives this separate leg's counter via sitecustomize.
    native = cm.run_driver(
        sys.executable,
        "native",
        {
            "project": str(project),
            "script": str(project / script),
            "entry": entry,
            "engine_dir": str(cm.ENGINE_DIR),
            "out_dir": str(evidence / "native"),
            "observe_semantics": True,
        },
    )
    row["native"] = {"status": _native_status(native), "result": native}
    native_counter = work / "native-execs.jsonl"
    row["executions"]["native"] = (
        len(native_counter.read_text().splitlines()) if native_counter.exists() else None
    )
    # Native fixture invalidity never enters the product-defect population.
    if row["native"]["status"] != "passed":
        row["status"] = row["native"]["status"]
        return row
    if missing:
        row["status"] = "environment_unavailable"
        row["raw"] = {"missing_prerequisites": missing}
        if native_counter.exists():
            shutil.copy2(native_counter, evidence / "native-execs.jsonl")
        return row
    os.environ["CG_EXEC_LOG"] = str(work / "tavotto-execs.jsonl")
    if suite == "compat_engine":
        # Matrix's diagnostic native/census runs are real script executions but
        # are not Tavotto executions. Keep each role in a separate event log.
        original_driver = cm.run_driver

        def role_driver(python, mode, request, timeout=900):
            previous = os.environ["CG_EXEC_LOG"]
            os.environ["CG_EXEC_LOG"] = str(work / f"diagnostic-{mode}-execs.jsonl")
            try:
                return original_driver(python, mode, request, timeout)
            finally:
                os.environ["CG_EXEC_LOG"] = previous

        cm.run_driver = role_driver
        # Preserve export bytes before the existing runner clears its workspace.
        original_export = cm.stage_export

        def keep_export(worker, stem, out_dir, formats):
            response = original_export(worker, stem, out_dir, formats)
            shutil.copytree(out_dir, evidence / "exports", dirs_exist_ok=True)
            return response

        cm.stage_export = keep_export
        engine_case = {**case, "product_routes": {}}
        result = cm.run_group(
            [engine_case],
            python=sys.executable,
            root=work,
            out_dir=evidence,
            want_fidelity=True,
            want_browser=False,
        )[cid]
        row["raw"] = result
        for s in CC.STAGES:
            if s in result["stages"]:
                good = result["stages"][s]
                state = (
                    "passed"
                    if good
                    else ("failed" if case["expected"].get(s, True) else "expected_limitation")
                )
                row["stages"][s] = stage(state, "existing CompatBench stage oracle; see raw.detail")
            elif s in result.get("skipped", {}):
                row["stages"][s] = stage("not_run", result["skipped"][s])
        repeated_native = (result.get("detail", {}).get("fidelity") or {}).get("native")
        if repeated_native and repeated_native.get("ok") is not True:
            row["stages"]["fidelity"] = stage(
                "unknown", "repeated native control failed; cannot attribute difference to Tavotto"
            )
        count = work / "tavotto-execs.jsonl"
        row["executions"]["tavotto"] = (
            len(count.read_text().splitlines()) if count.exists() else None
        )
        row["unverified"].append(
            "fidelity metric is existing grayscale comparison; not RGBA qualification"
        )
        row["unverified"].append(
            "product routes not_run in engine adapter; existing dedicated route tests remain authoritative"
        )
        row["raw"]["diagnostic_execution_counts"] = {
            p.name: len(p.read_text().splitlines()) for p in work.glob("diagnostic-*-execs.jsonl")
        }
        row["product_classification"] = result.get("classification")
    else:
        case_work = work / "v2"
        case_work.mkdir()
        try:
            obs = cb.observe(
                cs.SCENARIOS[cid],
                case_work,
                needs_absent=cs.NEEDS_ABSENT.get(cid, ()),
                env_overrides={"PYTHONPATH": os.environ["PYTHONPATH"]},
            )
        except cb.PreconditionUnmet as exc:
            row["status"] = "environment_unavailable"
            row["raw"] = {"precondition_unmet": str(exc)}
            return row
        row["raw"] = {
            "observed": obs.to_json(),
            "ideal_missing": cs.IDEAL[cid](obs),
            "historical_baseline": cs.SCENARIOS[cid].main_baseline,
        }
        cb.assert_no_execution_before_run(obs)
        row["executions"]["tavotto"] = obs.exec_count
        row["executions"]["basis"] = "fixture_external_exec_log"
        row["stages"]["discover"] = stage(
            "passed", "authenticated scan/check; fixture log proves zero pre-run executions"
        )
        row["stages"]["execute"] = stage(
            "passed" if obs.ran and obs.exec_count > 0 else "not_run",
            "real run action and fixture execution log",
        )
        for s, good in (("capture", obs.figures_captured > 0), ("open", obs.figures_valid > 0)):
            row["stages"][s] = stage(
                "passed" if good else "failed", "captured count and actual rendered manifest"
            )
        titles = [
            title for v in native.get("semantics", {}).values() for title in v["titles"] if title
        ]
        good = not row["raw"]["ideal_missing"] and sorted(obs.titles) == sorted(titles)
        row["stages"]["semantic"] = stage(
            "passed" if good else "failed",
            "V2 explicit scientific title/shape/count oracle AND native title comparison",
        )
        row["unverified"].append("V2 adapter does not claim edit/replay/export/fidelity")
        log = case_work / "exec_log.jsonl"
        if log.exists():
            shutil.copy2(log, evidence / "fixture-execs.jsonl")
    row["environment_after"] = environment()
    launches = {}
    for role in ("native", "tavotto"):
        log = work / f"{role}-execs.jsonl"
        launches[role] = (
            [json.loads(line)["environment"] for line in log.read_text().splitlines()]
            if log.exists()
            else []
        )
    row["raw"]["launch_environments"] = launches
    fingerprints = {env["fingerprint"] for records in launches.values() for env in records}
    row["comparable"] = (
        row["environment_before"] == row["environment_after"]
        and all(launches.values())
        and len(fingerprints) == 1
    )
    if suite == "compat_v2" and len(launches["tavotto"]) != row["executions"]["tavotto"]:
        row["comparable"] = False
    row["status"] = case_status(row)
    for name in [
        "native-execs.jsonl",
        "tavotto-execs.jsonl",
        *[p.name for p in work.glob("diagnostic-*-execs.jsonl")],
    ]:
        p = work / name
        if p.exists():
            shutil.copy2(p, evidence / name)
    return row


def evidence_refs(root: Path) -> list[dict]:
    refs = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(root))
            checked = contained(root, rel)
            refs.append(
                {
                    "path": rel,
                    "sha256": digest(checked.read_bytes()),
                    "bytes": checked.stat().st_size,
                }
            )
    return refs


def schema_check(value, schema: dict, root: dict | None = None, path: str = "$") -> None:
    """Validate the small, closed vocabulary used by our public JSON Schema.

    No network resolver or optional jsonschema runtime dependency. Unsupported
    keywords are rejected, so a schema upgrade cannot silently weaken validation.
    The checked-in schema is also independently checked with jsonschema in tests.
    """
    root = root or schema
    if "$ref" in schema:
        schema_check(value, root["$defs"][schema["$ref"].split("/")[-1]], root, path)
        return
    supported = {
        "$schema",
        "$id",
        "$defs",
        "title",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "uniqueItems",
        "minProperties",
        "enum",
        "const",
        "pattern",
        "minimum",
        "minLength",
    }
    if set(schema) - supported:
        raise EvidenceError(f"unsupported schema keywords at {path}")
    types = {
        "object": lambda x: isinstance(x, dict),
        "array": lambda x: isinstance(x, list),
        "string": lambda x: isinstance(x, str),
        "integer": lambda x: type(x) is int,
        "number": lambda x: type(x) in (int, float),
        "boolean": lambda x: type(x) is bool,
        "null": lambda x: x is None,
    }
    wanted = schema.get("type")
    if wanted and not any(
        types[t](value) for t in (wanted if isinstance(wanted, list) else [wanted])
    ):
        raise EvidenceError(f"wrong type at {path}")
    if "enum" in schema and value not in schema["enum"]:
        raise EvidenceError(f"unknown enum at {path}")
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise EvidenceError(f"wrong constant at {path}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or (
            "pattern" in schema and re.fullmatch(schema["pattern"], value) is None
        ):
            raise EvidenceError(f"invalid string at {path}")
    if type(value) in (int, float) and (
        value != value
        or value in (float("inf"), float("-inf"))
        or value < schema.get("minimum", float("-inf"))
    ):
        raise EvidenceError(f"invalid number at {path}")
    if isinstance(value, dict):
        if set(schema.get("required", [])) - set(value) or len(value) < schema.get(
            "minProperties", 0
        ):
            raise EvidenceError(f"missing field at {path}")
        props = schema.get("properties", {})
        for key, child in value.items():
            rule = props.get(key, schema.get("additionalProperties", True))
            if rule is False:
                raise EvidenceError(f"unknown field at {path}.{key}")
            if isinstance(rule, dict):
                schema_check(child, rule, root, f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or (
            schema.get("uniqueItems") and len({canonical(v) for v in value}) != len(value)
        ):
            raise EvidenceError(f"empty/duplicate array at {path}")
        for i, child in enumerate(value):
            schema_check(child, schema.get("items", {}), root, f"{path}[{i}]")


def validate_report(report: dict, evidence_root: Path, expected: dict | None = None) -> None:
    schema_check(report, json.loads(SCHEMA_PATH.read_text()))
    required = {
        "schema_version",
        "run_id",
        "source",
        "expected_sha",
        "suite",
        "expected_cases",
        "corpus_sha256",
        "cases",
        "evidence",
        "verdict",
        "unverified",
        "duration_s",
    }
    if set(report) != required or report["schema_version"] != SCHEMA_VERSION:
        raise EvidenceError("missing/unknown report field or schema version")
    if not re.fullmatch(r"[0-9a-f]{32}", report["run_id"]):
        raise EvidenceError("missing unique run_id")
    src = report["source"]
    if (
        not re.fullmatch(r"[0-9a-f]{40}", report["expected_sha"])
        or src["commit"] != report["expected_sha"]
    ):
        raise EvidenceError("wrong/missing commit")
    if expected is not None and src != expected:
        raise EvidenceError("report does not prove current checkout content")
    ids = report["expected_cases"]
    if report["suite"] not in SUITES or not ids or len(ids) != len(set(ids)):
        raise EvidenceError("empty/duplicate/unregistered suite selection")
    rows = report["cases"]
    if sorted(r["id"] for r in rows) != sorted(ids):
        raise EvidenceError("case set is incomplete/duplicated")
    registry = registered(report["suite"])
    fixture_map = {}
    for row in rows:
        if row["id"] not in registry:
            raise EvidenceError("case does not belong to registered suite")
        files = fixture_files(report["suite"], registry[row["id"]])
        fixture = {
            "files": files,
            "source_sha256": canonical({k: v for k, v in files.items() if k.endswith(".py")}),
            "data_sha256": canonical({k: v for k, v in files.items() if not k.endswith(".py")}),
        }
        if row["fixture"] != fixture:
            raise EvidenceError("fixture/source/data digest mismatch")
        fixture_map[row["id"]] = files
        if row["status"] not in STATUSES or set(row["stages"]) != set(CC.STAGES):
            raise EvidenceError("missing stage/unknown status")
        if any(
            s.get("status") not in STATUSES or not s.get("criterion")
            for s in row["stages"].values()
        ):
            raise EvidenceError("missing stage status/criterion")
        for key in ("native", "tavotto"):
            count = row["executions"][key]
            if count is not None and (type(count) is not int or count < 0):
                raise EvidenceError("invalid execution count")
        if row["status"] in ("passed", "failed", "expected_limitation") and row[
            "status"
        ] != case_status(row):
            raise EvidenceError("verdict lacks native/count/environment/stage proof")
        if row["status"] in ("passed", "failed", "expected_limitation"):
            env = row["environment_before"]
            if (
                not env.get("fingerprint")
                or env != row["environment_after"]
                or env["fingerprint"] != canonical(env["facts"])
            ):
                raise EvidenceError("environment mismatch/unbound fingerprint")
            launches = row["raw"].get("launch_environments") or {}
            if set(launches) != {"native", "tavotto"}:
                raise EvidenceError("missing actual child environment evidence")
            fps = set()
            for role, records in launches.items():
                if not records or len(records) != row["executions"][role]:
                    raise EvidenceError("execution count does not match actual child records")
                for item in records:
                    if item.get("fingerprint") != canonical(item.get("facts")):
                        raise EvidenceError("child environment fingerprint mismatch")
                    fps.add(item["fingerprint"])
            if len(fps) != 1:
                raise EvidenceError("native and Tavotto launch environments differ")
        if row["status"] == "failed" and row["native"]["status"] != "passed":
            raise EvidenceError("native failure is not a Tavotto defect")
    if report["verdict"] != verdict(rows):
        raise EvidenceError("incorrect aggregate verdict")
    if not re.fullmatch(r"[0-9a-f]{64}", report["corpus_sha256"]):
        raise EvidenceError("missing corpus digest")
    if report["corpus_sha256"] != canonical(fixture_map):
        raise EvidenceError("corpus digest mismatch")
    if not report["evidence"]:
        raise EvidenceError("no raw evidence")
    paths = []
    for ref in report["evidence"]:
        p = contained(evidence_root, ref["path"])
        if (
            not p.is_file()
            or digest(p.read_bytes()) != ref["sha256"]
            or p.stat().st_size != ref["bytes"]
        ):
            raise EvidenceError("missing/changed evidence: " + ref["path"])
        paths.append(ref["path"])
    if len(paths) != len(set(paths)):
        raise EvidenceError("duplicate evidence reference")


def launch_adapter(
    command: list[str], *, work: Path, evidence: Path, env: dict, timeout: float
) -> None:
    """Reuse package-smoke's process-group cleanup; retain timeout output.

    Only the registered engine/server paths run here. Engine product routes that
    deliberately detach desktop processes are outside this adapter's scope.
    """
    with (
        (evidence / "stdout.txt").open("w") as stdout,
        (evidence / "stderr.txt").open("w") as stderr,
    ):
        proc = subprocess.Popen(
            command,
            env=env,
            cwd=work,
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
        )
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            cleanup = package_smoke.terminate(proc)
    process = {"exit_code": proc.returncode, "timed_out": timed_out, "cleanup": cleanup}
    (evidence / "process.json").write_text(json.dumps(process))
    if cleanup.get("group_gone") is False:
        raise EvidenceError("adapter cleanup uncertain: process group remains")
    if timed_out or proc.returncode != 0:
        raise EvidenceError("adapter timeout/failure; retained stdout/stderr/process evidence")


def run(args) -> int:
    initial = source_state()
    expected_sha = args.expected_sha or initial["commit"]
    if expected_sha != initial["commit"]:
        raise EvidenceError("expected SHA is not actual HEAD")
    if (
        os.environ.get("GITHUB_REPOSITORY") == "Tavotto/Tavotto"
        and os.environ.get("GITHUB_SHA", expected_sha) != expected_sha
    ):
        raise EvidenceError("GITHUB_SHA disagrees with actual HEAD")
    registry = registered(args.suite)
    ids = args.case.split(",") if args.case else list(registry)
    if not ids or len(set(ids)) != len(ids) or any(i not in registry for i in ids):
        raise EvidenceError("empty/duplicate/unknown case selection")
    fixtures = {cid: fixture_files(args.suite, registry[cid]) for cid in ids}
    run_id = uuid.uuid4().hex
    output = Path(args.out).resolve() / run_id
    if output.is_relative_to(ROOT):
        raise EvidenceError("--out must be outside the source worktree")
    evidence = output / "evidence"
    evidence.mkdir(parents=True)
    started = time.monotonic()
    rows = []
    for cid in ids:
        dest = evidence / cid
        dest.mkdir()
        with tempfile.TemporaryDirectory(prefix="compat-guardian-") as tmp:
            work = Path(tmp)
            req = {"suite": args.suite, "case": cid, "work": str(work), "evidence": str(dest)}
            (work / "request.json").write_text(json.dumps(req))
            source_hashes = [
                v
                for k, v in fixtures[cid].items()
                if k.endswith(".py")
                and Path(k).name == Path(registry[cid].get("script", "script.py")).name
            ]
            env = isolated_env(work, work / "adapter-execs.jsonl", source_hashes)
            try:
                launch_adapter(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "_execute",
                        str(work / "request.json"),
                    ],
                    env=env,
                    work=work,
                    evidence=dest,
                    timeout=args.timeout,
                )
                row = json.loads((dest / "result.json").read_text())
            except (subprocess.TimeoutExpired, EvidenceError, OSError, ValueError) as exc:
                row = {
                    "id": cid,
                    "status": "unknown",
                    "layer": "runner",
                    "profile": "safe",
                    "desktop_shell_verified": False,
                    "stages": {
                        s: stage("not_run", "adapter did not produce valid terminal evidence")
                        for s in CC.STAGES
                    },
                    "executions": {"native": None, "tavotto": None, "basis": "unobserved"},
                    "native": {"status": "unknown", "result": {}},
                    "comparable": False,
                    "environment_before": {},
                    "environment_after": {},
                    "product_classification": None,
                    "raw": {"runner_error": str(exc)},
                    "unverified": ["adapter did not complete"],
                }
            row["fixture"] = {
                "files": fixtures[cid],
                "source_sha256": canonical(
                    {k: v for k, v in fixtures[cid].items() if k.endswith(".py")}
                ),
                "data_sha256": canonical(
                    {k: v for k, v in fixtures[cid].items() if not k.endswith(".py")}
                ),
            }
            rows.append(row)
            print(f"{cid}: {row['status']}", flush=True)
    if source_state() != initial:
        raise EvidenceError("source content changed during run; no report published")
    report = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "source": initial,
        "expected_sha": expected_sha,
        "suite": args.suite,
        "expected_cases": ids,
        "corpus_sha256": canonical(fixtures),
        "cases": rows,
        "evidence": evidence_refs(evidence),
        "verdict": verdict(rows),
        "duration_s": round(time.monotonic() - started, 3),
        "unverified": [
            "real WebView2/WKWebView",
            "unreviewed/external scripts",
            "not user success rate",
            "no automatic promotion/baseline update",
        ],
    }
    validate_report(report, evidence, initial)
    path = write_report("guardian.json", report, root=output)
    print(json.dumps({"report": str(path), "run_id": run_id, "verdict": report["verdict"]}))
    return 0 if report["verdict"] == "passed" else 1 if report["verdict"] == "failed" else 3


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    subs = ap.add_subparsers(dest="command", required=True)
    runner = subs.add_parser("run")
    runner.add_argument("--suite", choices=SUITES, required=True)
    runner.add_argument("--case", help="comma-separated registered IDs; omitted means all")
    runner.add_argument("--out", required=True)
    runner.add_argument("--expected-sha")
    runner.add_argument("--timeout", type=float, default=600)
    checker = subs.add_parser("validate")
    checker.add_argument("report")
    private = subs.add_parser("_execute", help=argparse.SUPPRESS)
    private.add_argument("request")
    args = ap.parse_args(argv)
    try:
        if args.command == "run":
            if not math.isfinite(args.timeout) or args.timeout <= 0:
                raise EvidenceError("timeout must be positive")
            return run(args)
        if args.command == "validate":
            path = Path(args.report)
            validate_report(
                json.loads(path.read_text()), path.parent.parent / "evidence", source_state()
            )
            print("valid current-checkout evidence")
            return 0
        request = json.loads(Path(args.request).read_text())
        fixture_files(request["suite"], registered(request["suite"])[request["case"]])
        row = _execute(request)
        (Path(request["evidence"]) / "result.json").write_text(json.dumps(row, indent=2))
        return 0
    except (EvidenceError, KeyError, ValueError, OSError) as exc:
        print(f"guardian: {exc}", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
