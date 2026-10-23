#!/usr/bin/env python3
"""Compatibility Guardian runner (CG-01): observation-only evidence reports.

Reuses CompatBench, its native driver, and the V2/Foundation authenticated server
harness. There is no API for running an arbitrary script: `run` takes registry
case ids only.

What is and is not guaranteed (see docs/ci/compat-guardian.md):

* Guaranteed by this code: every executed fixture is a registry entry whose bytes
  equal the bytes committed at the checked-out HEAD, and the whole source tree is
  unchanged between start and end of the run. A run on a dirty tree says so in
  its report and cannot validate against a clean checkout.
* NOT guaranteed by this code: that HEAD was reviewed. That is a property of how
  HEAD got onto the branch (merge to main under CODEOWNERS), not something a
  script can attest about itself.
* Directory/environment isolation here is not OS isolation. No dependency
  installation, no promotion, no baseline writes.
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
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import compat_corpus as CC  # noqa: E402
import compat_guardian_taxonomy as TX  # noqa: E402
import package_smoke  # noqa: E402
from _common import write_report  # noqa: E402
from guardian_exec_observer import actual_environment  # noqa: E402

SCHEMA_VERSION = "1.0.0"
#: Run outcome states (brief, "Runner contract"); separate from CompatBench's six
#: product classifications.
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
#: Row-level extra state: every exercised layer passed with valid proof, but the
#: adapter does not exercise at least one required layer. Not a pass, not a failure.
#: `control_met` is assigned only by a committed expectation (tests/compat_guardian/
#: expectations.json) whose declared outcome equals the observed one. Not a pass.
ROW_STATUSES = (*STATUSES, "partial_coverage", "control_met")
GREEN_STATUSES = (
    "passed",
    "expected_limitation",
    "partial_coverage",
    "not_reproduced",
    "control_met",
)
#: Stage-level extra state: this adapter does not drive that layer at all (distinct
#: from `not_run`, which means the layer was in scope but did not execute).
STAGE_STATUSES = (*STATUSES, "not_covered")
#: Rows with these statuses assert something about the product and need full proof.
PROOF_STATUSES = ("passed", "failed", "expected_limitation", "partial_coverage")
#: Process exit codes of `run`. Only 0 is a clean pass. 4 is the explicitly defined
#: "decided, no failure, not a clean pass" outcome (partial_coverage or declared
#: expected_limitation rows) and is never mapped to 0.
EXIT_PASSED, EXIT_FAILED, EXIT_USAGE, EXIT_UNKNOWN, EXIT_PARTIAL = 0, 1, 2, 3, 4
SUITES = ("compat_engine", "compat_v2")
#: Output suffixes whose readability decides whether native produced a usable figure.
FIGURE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".bmp",
    ".gif",
    ".webp",
    ".pdf",
    ".svg",
}
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


class AdapterTimeout(EvidenceError):
    """The adapter exceeded its budget; the process group was reaped."""


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


def science_python() -> str:
    """Interpreter that runs scripts and workers (ADR process boundary: the scientific stack is not
    Tavotto's own venv). `TAVOTTO_SCIENCE_PYTHON` selects a separate research venv; unset = this one."""
    return os.environ.get("TAVOTTO_SCIENCE_PYTHON") or sys.executable


@dataclass(frozen=True)
class ScienceInterpreter:
    """The scientific interpreter, probed once: usable + its facts, or unusable + the reason."""

    python: str
    usable: bool
    reason: str | None = None
    facts: dict | None = None
    missing: tuple[str, ...] = ()


#: Modules the scripts/workers need in the scientific interpreter (see GUARDIAN_MODULES for the other side).
SCIENCE_MODULES = ("matplotlib", "numpy")
_PROBE = (
    "import importlib.util as u, json, sys; sys.path.insert(0, sys.argv[1]);"
    "from guardian_exec_observer import actual_environment;"
    "print(json.dumps({'env': actual_environment(),"
    " 'missing': [m for m in sys.argv[2:] if u.find_spec(m) is None]}))"
)
_SCIENCE_CACHE: dict[str, ScienceInterpreter] = {}


def reset_science_cache() -> None:
    _SCIENCE_CACHE.clear()


def science_interpreter() -> ScienceInterpreter:
    """The one place that starts the scientific interpreter to learn about it (cached per path).

    Environment fingerprint, preflight and `_execute()` all read this; none starts its own checked
    subprocess, so a missing / non-executable / failing interpreter becomes `usable=False` with a
    reason instead of an exception raised before preflight can report it.
    """
    python = science_python()
    cached = _SCIENCE_CACHE.get(python)
    if cached is not None:
        return cached
    if python == sys.executable:
        result = ScienceInterpreter(
            python,
            True,
            facts=actual_environment(),
            missing=tuple(m for m in SCIENCE_MODULES if importlib.util.find_spec(m) is None),
        )
    else:
        try:
            done = subprocess.run(
                [python, "-I", "-c", _PROBE, str(HERE), *SCIENCE_MODULES],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if done.returncode != 0:
                result = ScienceInterpreter(
                    python, False, f"exit {done.returncode}: {done.stderr.strip()[-300:]}"
                )
            else:
                data = json.loads(done.stdout)
                result = ScienceInterpreter(
                    python, True, facts=data["env"], missing=tuple(data["missing"])
                )
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            result = ScienceInterpreter(python, False, f"{type(exc).__name__}: {str(exc)[-300:]}")
    _SCIENCE_CACHE[python] = result
    return result


def science_distribution_file(distribution: str, relative: str) -> str:
    """Where a distribution's file lives in the scientific interpreter (for `verify-environment`)."""
    science = science_interpreter()
    if not science.usable:
        raise EvidenceError(f"scientific interpreter unusable: {science.reason}")
    done = subprocess.run(
        [
            science.python,
            "-I",
            "-c",
            "import importlib.metadata as m, sys;"
            "print(m.distribution(sys.argv[1]).locate_file(sys.argv[2]))",
            distribution,
            relative,
        ],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise EvidenceError(f"cannot locate {distribution} in the scientific interpreter")
    return done.stdout.strip()


def environment() -> dict:
    """Identity of the environment the measurement ran in; never raises for a bad interpreter.

    With a separate scientific interpreter both are bound: the top-level facts are the scientific
    interpreter's (where scripts and workers run) and `facts.guardian` is Tavotto's own, so a change
    on either side changes the fingerprint. An unusable interpreter is recorded as such.
    """
    science = science_interpreter()
    if science.python == sys.executable:
        return science.facts
    base = (
        science.facts["facts"]
        if science.facts is not None
        else {"science_interpreter": {"usable": False, "reason": science.reason}}
    )
    facts = {**base, "guardian": actual_environment()["facts"]}
    return {"fingerprint": canonical(facts), "facts": facts}


def fonts_dir() -> Path:
    """Same rule as rendercore.fonts.fonts_dir(): TAVOTTO_FONTS_DIR overrides exclusively."""
    override = os.environ.get("TAVOTTO_FONTS_DIR")
    return Path(override) if override else ROOT / "src/tavotto/resources/fonts"


#: What each side really imports. The Guardian process uses numpy/Pillow for the pixel comparison
#: (`pixelcompare`) and RenderCore for the product; scripts and workers need matplotlib/numpy.
GUARDIAN_MODULES = tuple(m for m in REQUIRED_MODULES if m != "matplotlib")


def preflight() -> list[str]:
    science = science_interpreter()
    if science.python == sys.executable:
        return [f"module:{m}" for m in REQUIRED_MODULES if importlib.util.find_spec(m) is None] + (
            _font_gaps()
        )
    remote = (
        [f"module:{m}@science" for m in science.missing]
        if science.usable
        else ["science_python:unusable"]
    )
    local = [
        f"module:{m}@guardian" for m in GUARDIAN_MODULES if importlib.util.find_spec(m) is None
    ]
    return local + remote + _font_gaps()


#: Modules whose absence makes the native leg meaningless (scripts/workers need matplotlib/numpy; the
#: pixel comparison needs numpy/Pillow in the Guardian process). Either side blocks execution.
BLOCKING_MODULES = ("matplotlib", "numpy", "PIL")


def parse_prerequisite(item: str) -> tuple[str, str, str | None]:
    """`module:numpy@science` -> ("module", "numpy", "science"); `font:x` -> ("font", "x", None)."""
    kind, _, rest = item.partition(":")
    name, _, side = rest.partition("@")
    return kind, name, side or None


def blocks_execution(item: str) -> bool:
    """Single authority: does this `preflight()` item make running the native leg pointless?"""
    kind, name, _side = parse_prerequisite(item)
    return (kind == "module" and name in BLOCKING_MODULES) or item == "science_python:unusable"


def _font_gaps() -> list[str]:
    missing: list[str] = []
    allowlist = json.loads(
        (ROOT / "src/tavotto/rendercore/fonts_allowlist.json").read_text(encoding="utf-8")
    )
    base = fonts_dir()
    for face in allowlist["faces"].values():
        path = base / face["file"]
        if not path.is_file() or digest(path.read_bytes()) != face["sha256"]:
            missing.append("font:" + face["file"])
    return missing


def matches_head(root: Path, rel: str, path: Path | None = None) -> bool:
    """工作区里的 `rel` 与 HEAD 里评审过的那份是否同一内容——按 git 自己的换行 / 过滤规则比
    （`git hash-object --path` 对工作区文件做与提交时相同的规范化，再与 `HEAD:rel` 的 blob id 比）。
    直接比字节在 Windows 上恒不等：checkout 按 `core.autocrlf` 把 LF 写成 CRLF。"""
    path = path or contained(root, rel)
    head = subprocess.check_output(["git", "rev-parse", f"HEAD:{rel}"], cwd=root, text=True).strip()
    work = subprocess.check_output(
        ["git", "hash-object", f"--path={rel}", "--", str(path)], cwd=root, text=True
    ).strip()
    return work == head


def staged_source_hashes(root: Path, fixture: dict[str, str], script: str, case=None) -> list[str]:
    """执行观测器（`CG_EXEC_HASHES`）要认的入口脚本摘要：按**实际拷进沙盒的工作区字节**算，不用 `fixture` 里按 HEAD blob
    记的语料身份——materialize / V2 staging 原样拷工作区文件，Windows `core.autocrlf` checkout 下是 CRLF，两者不同
    （Codex #1118 r4310318506）。内容与 HEAD 等价已由 `fixture_files` 核过。"""
    return [
        digest(fixture_bytes(root, k, case)) for k in fixture if Path(k).name == Path(script).name
    ]


def fixture_path(root: Path, relative: str, case: dict | None = None) -> Path:
    if case and case.get("research_project_id"):
        import compat_research_sources as sources

        return sources.source_file(case, relative, root=root)
    return contained(root, relative)


def fixture_bytes(root: Path, relative: str, case: dict | None = None) -> bytes:
    if case and case.get("research_project_id"):
        import compat_research_sources as sources

        return sources.source_bytes(case, relative, root=root)
    return contained(root, relative).read_bytes()


#: Executable code in a fixture inventory: a plain script, or the archived `<name>.py.source` form the
#: research corpus stores upstream scripts under (kept un-importable inside the repository).
SOURCE_SUFFIXES = (".py", ".py.source")


def fixture_digests(files: dict[str, str]) -> dict:
    """The one split of a fixture inventory into code and data, used when producing and validating."""
    code = {k: v for k, v in files.items() if k.endswith(SOURCE_SUFFIXES)}
    data = {k: v for k, v in files.items() if not k.endswith(SOURCE_SUFFIXES)}
    return {"files": files, "source_sha256": canonical(code), "data_sha256": canonical(data)}


def registered(suite: str) -> dict[str, dict]:
    metadata = (
        ["tests/compat/manifest.json"]
        if suite == "compat_engine"
        else ["tests/support/compat_v2_scenarios.py", "tests/support/compatbench_v2.py"]
    )
    metadata = [*metadata, EX.RELATIVE_PATH, EX.SCHEMA_RELATIVE_PATH]
    for rel in metadata:
        if not matches_head(ROOT, rel):
            raise EvidenceError(f"execution metadata differs from reviewed HEAD: {rel}")
    if suite == "compat_engine":
        return {c["id"]: c for c in CC.load_manifest()["cases"]}
    if suite == "compat_v2":
        from support import compat_v2_scenarios as cs

        return {sid: {"id": sid} for sid in cs.SCENARIOS}
    raise EvidenceError(f"unregistered suite: {suite}")


def selected_registry(suite: str, case_ids) -> dict[str, dict]:
    cases = registered(suite)
    if suite == "compat_engine":
        if any(cid.startswith("research_") for cid in set(case_ids) - cases.keys()):
            import compat_guardian_research as research
            import compat_research_sources as sources

            for rel in (sources.COHORT_REL, sources.MANIFEST_REL):
                if not matches_head(ROOT, rel):
                    raise EvidenceError(f"research registration differs from reviewed HEAD: {rel}")
            research.load_cohort(root=ROOT)
            external = {c["id"]: c for c in research.load_cases(ROOT)}
            cases.update({cid: external[cid] for cid in case_ids if cid in external})
    return cases


def fixture_files(suite: str, case: dict) -> dict[str, str]:
    if suite == "compat_engine" and case.get("research_project_id"):
        import compat_research_sources as sources

        project = sources.project_for_case(case, root=ROOT)
        result = {
            rel: digest(sources.source_bytes(case, rel, root=ROOT))
            for rel in project["staged_files"]
        }
        # Reuse the ordinary case validator after resolving the pinned external bytes.
        resolved = {
            **case,
            "script": str(fixture_path(ROOT, "tests/compat/" + case["script"], case)),
        }
        resolved["extra_files"] = {
            dest: str(fixture_path(ROOT, "tests/compat/" + rel, case))
            for dest, rel in case.get("extra_files", {}).items()
        }
        CC._validate_case(resolved, CC.COMPAT_DIR)
        return result
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
        if not matches_head(ROOT, rel, checked):
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
            "TAVOTTO_FONTS_DIR",  # a read-only approved-font location, not a secret
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
            "from guardian_exec_observer import install\ninstall()\n", encoding="utf-8"
        )
        roots.insert(0, str(bootstrap))
        env.update(
            CG_EXEC_ROOT=str(work),
            CG_EXEC_LOG=str(audit_log),
            CG_EXEC_HASHES=json.dumps(list(hashes)),
        )
    env["PYTHONPATH"] = os.pathsep.join(roots)
    # Explicit user-selected test interpreter; never let native/worker choose different stacks.
    env["TAVOTTO_WORKER_PYTHON"] = science_python()
    for key in ("TAVOTTO_RESEARCH_CACHE", "TAVOTTO_SCIENCE_PYTHON"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def stage(status: str, criterion: str) -> dict:
    return {"status": status, "criterion": criterion}


UNVERIFIED_BASE = (
    "real desktop shell",
    "save occurrence snapshots",
    "arbitrary user scripts",
    "scientific values beyond registered oracle",
)


def new_row(cid: str, layer: str, before: dict | None = None) -> dict:
    """A case row with every required field present and nothing claimed."""
    env = before or {}
    return {
        "id": cid,
        "status": "unknown",
        "layer": layer,
        "profile": "safe",
        "desktop_shell_verified": False,
        "stages": {s: stage("not_run", "not reached") for s in CC.STAGES},
        "executions": {
            "native": None,
            "tavotto": None,
            "basis": "python_exec_audit",
            "attempt_history": None,
            "attempt_history_agrees": None,
        },
        "native": {"status": "not_run", "result": {}},
        "comparable": False,
        "figures": {"native": None, "tavotto_captured": None, "tavotto_valid": None},
        "timings_s": {
            "total": 0.0,
            "native": None,
            "tavotto": None,
            "stages": {s: None for s in CC.STAGES},
        },
        "needs_authorization": {
            "required": None,
            "basis": "not determined: the case did not reach a point where this is known",
        },
        "failure_category": {"category": None, "source_code": None, "basis": "not attributed"},
        "expectation": None,
        "environment_before": env,
        "environment_after": env,
        "product_classification": None,
        "raw": {},
        "unverified": list(UNVERIFIED_BASE),
    }


def finalize_row(row: dict) -> dict:
    """Derive status and failure category once all evidence is in the row."""
    row["status"] = case_status(row)
    row["failure_category"] = TX.attribute(row)
    return row


#: Product-side refusals decided before anything is launched (a capability boundary, not a crash).
REFUSAL_CODES = ("launch_context_unsupported",)


def refused_before_launch(row: dict) -> bool:
    """The product leg failed at discover with a refusal code and, by construction, never executed.

    Such a row has native child records but no Tavotto ones; that absence is the evidence, so it is
    judged on the native leg's proof plus "Tavotto executed zero times", not on a full launch pair.
    """
    raw = row.get("raw") or {}
    code = (((raw.get("detail") or {}).get("discover")) or {}).get("code")
    return (
        code in REFUSAL_CODES
        and row["stages"].get("discover", {}).get("status") == "failed"
        and row["executions"].get("tavotto") in (None, 0)
        and not (raw.get("launch_environments") or {}).get("tavotto")
        and (row["executions"].get("native") or 0) > 0
        and row["environment_before"] == row["environment_after"]
    )


def native_eligible(row: dict) -> bool:
    """The one test for "this row belongs in the native-qualified denominator".

    Native passed with a real figure and an execution proof, and the two legs are comparable (which
    already means the environment did not move) -- or the product refused before launching (`refused_before_launch`),
    where a comparable pair cannot exist but the refusal is itself the product outcome.
    """
    return bool(
        row["native"]["status"] == "passed"
        and (row["figures"]["native"] or 0) > 0
        and (row["executions"]["native"] or 0) > 0
        and (row["comparable"] or refused_before_launch(row))
    )


def case_status(row: dict) -> str:
    native = row["native"]["status"]
    if native != "passed":
        return native
    raw = row.get("raw") or {}
    if "runner_error" in raw:
        return "unknown"  # a crashed runner is never evidence about the product
    if refused_before_launch(row):
        return "failed"
    if not row["comparable"] or not (row["executions"]["native"] or 0):
        return "unknown"
    statuses = [s["status"] for s in row["stages"].values()]
    if any(
        r.get("status") == "fail" for r in (raw.get("routes") or {}).values() if isinstance(r, dict)
    ):
        return "failed"
    if isinstance(raw.get("browser"), dict) and raw["browser"].get("ok") is False:
        return "failed"
    if "failed" in statuses:
        return "failed"
    native_figs, captured = row["figures"]["native"], row["figures"]["tavotto_captured"]
    if native_figs == 0 and type(captured) is int and captured > 0:
        return "failed"  # Tavotto shows figures the native run never produced
    if row.get("product_classification") == "product_bug":
        return "unknown"  # a legacy label alone is not independent failure evidence
    if row["executions"]["tavotto"] in (None, 0):
        return "unknown"
    if all(s == "passed" for s in statuses):
        return "passed"
    if all(s in ("passed", "expected_limitation", "not_covered") for s in statuses):
        if "expected_limitation" in statuses:
            return "expected_limitation"
        # Exercised layers all passed; the rest are layers this adapter does not drive.
        return "partial_coverage"
    return "unknown"


def verdict(rows: list[dict]) -> str:
    if not rows:
        return "unknown"
    statuses = [r["status"] for r in rows]
    if "failed" in statuses:
        return "failed"
    if all(s == "passed" for s in statuses):
        return "passed"
    decided = ("passed", "partial_coverage", "expected_limitation", "control_met")
    if all(s in decided for s in statuses):
        # Decided, nothing failed, not a clean pass. Report the weakest claim present.
        for weakest in ("partial_coverage", "expected_limitation", "control_met"):
            if weakest in statuses:
                return weakest
    if all(s == "environment_unavailable" for s in statuses):
        return "environment_unavailable"
    if all(s == "invalid_fixture" for s in statuses):
        return "invalid_fixture"
    return "unknown"


def exit_code_for(outcome: str) -> int:
    return {
        "passed": EXIT_PASSED,
        "failed": EXIT_FAILED,
        "partial_coverage": EXIT_PARTIAL,
        "expected_limitation": EXIT_PARTIAL,
        "control_met": EXIT_PARTIAL,
    }.get(outcome, EXIT_UNKNOWN)


def coverage_table(rows: list[dict]) -> dict:
    """Per stage: how many cases ended in each stage status (names the uncovered)."""
    table: dict[str, dict[str, int]] = {}
    for name in CC.STAGES:
        counts: dict[str, int] = {}
        for row in rows:
            state = row["stages"][name]["status"]
            counts[state] = counts.get(state, 0) + 1
        table[name] = counts
    return table


def _native_status(result: dict) -> str:
    """Judge the native reference (compat_driver `native_ref`) on its own facts.

    A failing, empty, or unreadable native control is a fixture/environment problem
    and never enters the Tavotto-defect population.
    """
    if result.get("ok") is not True:
        tb = result.get("traceback", "")
        # Only a missing module is evidence of a missing prerequisite. A driver that crashed,
        # printed nothing or timed out proves nothing about the environment: it stays `unknown`
        # and can never satisfy a declared environment_unavailable (control_met).
        if "ModuleNotFoundError:" in tb:
            return "environment_unavailable"
        if result.get("code") == "native_script_error":
            return "invalid_fixture"
        return "unknown"
    if type(result.get("exit_status")) is not int:
        return "unknown"
    if result["exit_status"] != 0:
        tb = (result.get("error") or {}).get("traceback", "")
        return "environment_unavailable" if "ModuleNotFoundError:" in tb else "invalid_fixture"
    if any(
        a.get("readable") is not True
        for a in result.get("artifacts", [])
        if Path(a.get("path", "")).suffix.lower() in FIGURE_SUFFIXES
    ):
        return "invalid_fixture"
    if type(result.get("figure_count")) is not int:
        return "unknown"
    # Zero figures is a valid native outcome (a script that draws nothing). Whether Tavotto
    # agrees is judged against it: see the disagreement rule in case_status().
    return "passed"


def v2_stages(
    observed: dict,
    native: dict,
    ideal_missing: list,
    designed_stop: bool = False,
    designed_partial: bool = False,
) -> dict:
    """Project actual outcomes, keeping invocation counts distinct from success.

    The V2 adapter drives scan/check/run through the authenticated server only. It does
    not drive edit, replay, export or fidelity; those are `not_covered`, never `not_run`
    (not run) and never passed.
    """
    stages = {s: stage("not_run", "not reached") for s in CC.STAGES}
    for name in V2_UNCOVERED:
        stages[name] = stage("not_covered", "layer not exercised by the V2 session adapter")
    stages["discover"] = stage(
        "passed", "authenticated scan/check; fixture log proves zero pre-run executions"
    )
    invoked = observed["ran"] and observed["exec_count"] > 0
    if not invoked:
        return stages
    # `partial` + `succeeded` is the honest projection of a run that finished but kept only
    # some figures (G3): the execution itself finished; the missing figures are a capture fact.
    finished = (
        observed["final_phase"] in ("completed", "partial")
        and observed["outcome_kind"] == "succeeded"
        and observed["facts"].get("facts", {}).get("execution_finished") is True
    )
    execution = (
        "passed" if finished else "failed" if observed["outcome_kind"] == "failed" else "unknown"
    )
    stages["execute"] = stage(
        execution, "actual script invocation AND succeeded terminal execution facts"
    )
    captured, opened = observed["figures_captured"] > 0, observed["figures_valid"] > 0
    native_figs = native.get("figure_count")
    fewer = captured and type(native_figs) is int and observed["figures_captured"] < native_figs
    # more figures than native is no more a capture pass than fewer: the counts must agree
    agrees = type(native_figs) is not int or observed["figures_captured"] == native_figs
    if fewer and designed_partial and not ideal_missing:
        # The V2 partial oracle was met exactly (phase=partial, the uncaptured figures named):
        # fewer figures than native is reported honestly, a declared limitation, never a pass.
        stages["capture"] = stage(
            "expected_limitation",
            "designed partial: fewer figures than native, reported as partial with the "
            "uncaptured figures named (V2 partial oracle met)",
        )
    elif finished or captured:
        stages["capture"] = stage(
            "passed" if captured and agrees else "failed",
            "actual captured figure count against the native figure count",
        )
    if captured:
        # every captured figure must render: one valid figure among several captured is not "open"
        all_open = observed["figures_valid"] == observed["figures_captured"]
        stages["open"] = stage(
            "passed" if all_open else "failed",
            "actual rendered manifest: every captured figure renders",
        )
    if opened:
        titles = [
            title
            for value in native.get("semantics", {}).values()
            for title in value["titles"]
            if title
        ]
        good = not ideal_missing and sorted(observed["titles"]) == sorted(titles)
        stages["semantic"] = stage(
            "passed" if good else "failed",
            "V2 explicit scientific title/shape/count oracle AND native title comparison",
        )
    if designed_stop and invoked and not ideal_missing:
        # The V2 oracle for a stop scenario was met exactly (one execution, explicit reason
        # code, no figure, no fallback): Tavotto stopped by design, which is a declared
        # limitation of the script's outcome, not a pass and not a defect.
        why = "designed stop: the V2 stop oracle was met exactly (reason code, no figure, no fallback)"
        for name in ("execute", "capture", "open", "semantic"):
            stages[name] = stage("expected_limitation", why)
    return stages


#: Layers the V2 session adapter does not drive (docs/ci/compat-guardian.md, "Coverage").
V2_UNCOVERED = ("edit", "replay", "export", "fidelity")


def _trim_native(result: dict) -> dict:
    """Bound free-text fields so reports stay public-log-safe; judge before trimming."""
    trimmed = dict(result)
    for key in ("log", "stderr", "stdout", "traceback"):
        if isinstance(trimmed.get(key), str):
            trimmed[key] = trimmed[key][-1200:]
    if isinstance(trimmed.get("error"), dict):
        trimmed["error"] = {
            k: (v[-1200:] if isinstance(v, str) else v) for k, v in trimmed["error"].items()
        }
    return trimmed


def line_data_agreement(native_line_xy, result: dict, native_incomparable=None) -> dict:
    """Read-only comparison of curve data: native `Line2D.get_xydata()` vs the product manifest.

    Both sides are digests of shape + little-endian float64 (`manifest.canonical_digest`); duplicates
    are kept. The product opens only the target figure, so it is compared with every native figure
    and the closest one is reported. Diagnostic only: it never changes a stage verdict.
    """
    opened = (result.get("detail") or {}).get("open") or {}
    product = opened.get("line_xy")
    if not isinstance(native_line_xy, list) or product is None:
        return {"status": "not_observed"}

    def summary(digests):
        return {
            "lines": len(digests),
            "sha256": hashlib.sha256("\n".join(digests).encode()).hexdigest(),
        }

    target = Counter(product)
    scored = []
    for index, digests in enumerate(native_line_xy):
        if digests is None:
            continue
        have = Counter(digests)
        gap = sum((have - target).values()) + sum((target - have).values())
        # Smallest curve-set difference first (an exact match wins), then most shared, then order.
        scored.append((gap, -sum((have & target).values()), index, digests))
    if not scored:
        return {"status": "not_observed"}
    _gap, _shared, index, digests = min(scored)
    only_native = sum((Counter(digests) - target).values())
    only_product = sum((target - Counter(digests)).values())
    incomparable_product = opened.get("line_xy_incomparable") or 0
    incomparable_native = (
        native_incomparable[index]
        if isinstance(native_incomparable, list) and index < len(native_incomparable)
        else 0
    ) or 0
    unobserved_product = opened.get("line_xy_unobserved") or 0
    unobserved_native = sum(1 for d in native_line_xy if d is None)
    if unobserved_product or unobserved_native or incomparable_product or incomparable_native:
        # Only plain Line2D curves on both sides are comparable; an unmeasured or differently stored
        # curve (errorbar, stem, 3D, ...) can neither agree nor differ: say so, never a false "different".
        status = "unknown"
    else:
        status = "equal" if not only_native and not only_product else "different"
    return {
        "status": status,
        "incomparable_product_lines": incomparable_product,
        "incomparable_native_curves": incomparable_native,
        "unobserved_product_lines": unobserved_product,
        "unobserved_native_figures": unobserved_native,
        "native_figures": [d if d is None else summary(d) for d in native_line_xy],
        "closest_native_figure": index,
        "product": summary(product),
        "only_native": only_native,
        "only_product": only_product,
    }


def _count(path: Path) -> int | None:
    """Lines in an append-only counter; None when the counter was never opened."""
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else None


def _authorization(suite: str, cid: str, scenario_absent: tuple, facts: dict) -> dict:
    installs = (facts or {}).get("dependency_installs")
    if scenario_absent or installs:
        return {
            "required": True,
            "basis": "scenario depends on packages absent from the interpreter ("
            + ",".join(scenario_absent or ())
            + "): a user flow needs an install decision; Guardian never installs",
        }
    return {
        "required": False,
        "basis": "reviewed fixture; Safe profile with already-installed dependencies; "
        "no install and no Native escalation was needed or performed",
    }


def _execute(request: dict) -> dict:
    """Private adapter, launched only with a request created by run()."""
    import compat_matrix as cm

    t_total = time.monotonic()
    suite, cid = request["suite"], request["case"]
    case = selected_registry(suite, (cid,))[cid]
    work = Path(request["work"])
    evidence = Path(request["evidence"])
    before = environment()
    row = new_row(
        cid, "engine" if suite == "compat_engine" else "authenticated_server_safe", before
    )
    scenario_absent: tuple = ()
    missing = preflight()
    if any(blocks_execution(item) for item in missing):
        row["native"]["status"] = "environment_unavailable"
        row["raw"] = {"missing_prerequisites": missing}
        science = science_interpreter()
        if not science.usable:
            row["raw"]["science_python_unusable"] = science.reason
        row["timings_s"]["total"] = round(time.monotonic() - t_total, 3)
        return finalize_row(row)
    native_root = work / "native-control"
    native_root.mkdir()
    if suite == "compat_engine":
        project, script, entry = cm.materialize([case], native_root)
    else:
        from support import compat_v2_scenarios as cs, compatbench_v2 as cb

        project, script, entry = cb.stage(cid, native_root), "script.py", "__main__"
        scenario_absent = tuple(cs.NEEDS_ABSENT.get(cid, ()))
    os.environ["CG_EXEC_LOG"] = str(work / "native-execs.jsonl")
    # The already installed audit hook opened its file at process start; the child
    # native driver receives this separate leg's counter via sitecustomize.
    t0 = time.monotonic()
    native_request = {"project": str(project), "script": str(project / script), "entry": entry}
    if "reference_invocation" in case:
        native_request["reference_invocation"] = case["reference_invocation"]
    native = cm.run_driver(
        science_python(),
        "native_ref",
        native_request,
    )
    row["timings_s"]["native"] = round(time.monotonic() - t0, 3)
    native_line_xy = native.get("line_xy")
    native_incomparable = native.get("line_xy_incomparable")
    row["native"] = {
        "status": _native_status(native),
        "result": {
            k: v
            for k, v in _trim_native(native).items()
            if k not in ("line_xy", "line_xy_incomparable")
        },
    }
    row["executions"]["native"] = _count(work / "native-execs.jsonl")
    row["figures"]["native"] = native.get("figure_count") if native.get("ok") else None
    # Native fixture invalidity never enters the product-defect population.
    if row["native"]["status"] != "passed":
        row["timings_s"]["total"] = round(time.monotonic() - t_total, 3)
        return finalize_row(row)
    if missing:
        row["raw"] = {"missing_prerequisites": missing}
        row["timings_s"]["total"] = round(time.monotonic() - t_total, 3)
        row["status"] = "environment_unavailable"
        row["failure_category"] = TX.attribute(row)
        return row
    os.environ["CG_EXEC_LOG"] = str(work / "tavotto-execs.jsonl")
    t0 = time.monotonic()
    if suite == "compat_engine":
        # Matrix's diagnostic native/census runs are real script executions but
        # are not Tavotto executions. Keep each role in a separate event log.
        original_driver = cm.run_driver

        def role_driver(python, mode, request, timeout=900):
            previous = os.environ["CG_EXEC_LOG"]
            os.environ["CG_EXEC_LOG"] = str(work / f"diagnostic-{mode}-execs.jsonl")
            try:
                if mode in {"native_ref", "native", "census"} and "reference_invocation" in case:
                    request = {**request, "reference_invocation": case["reference_invocation"]}
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
        try:
            result = cm.run_group(
                [engine_case],
                python=science_python(),
                root=work,
                out_dir=evidence,
                want_fidelity=True,
                want_browser=False,
            )[cid]
        except Exception:  # noqa: BLE001 - a crashed runner is evidence about the runner
            row["raw"] = {"runner_error": traceback.format_exc()[-1500:]}
            row["timings_s"]["tavotto"] = round(time.monotonic() - t0, 3)
            row["timings_s"]["total"] = round(time.monotonic() - t_total, 3)
            return finalize_row(row)
        row["raw"] = result
        row["raw"]["line_data"] = line_data_agreement(native_line_xy, result, native_incomparable)
        if "runner_error" in (result.get("detail") or {}):
            row["raw"]["runner_error"] = str(result["detail"]["runner_error"])[-1500:]
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
        row["executions"]["tavotto"] = _count(work / "tavotto-execs.jsonl")
        row["unverified"].append(
            "fidelity metric is existing grayscale comparison; not RGBA qualification"
        )
        row["unverified"].append(
            "product routes not_run in engine adapter; existing dedicated route tests remain authoritative"
        )
        row["raw"]["diagnostic_execution_counts"] = {
            p.name: len(p.read_text(encoding="utf-8").splitlines())
            for p in work.glob("diagnostic-*-execs.jsonl")
        }
        classification = result.get("classification")
        row["product_classification"] = (
            None if classification == CC.RUNNER_ERROR else classification
        )
        capture = (result.get("detail") or {}).get("capture") or {}
        got = capture.get("got_figures")
        row["figures"]["tavotto_captured"] = got if type(got) is int else None
        opened = row["stages"]["open"]["status"]
        row["figures"]["tavotto_valid"] = (
            row["figures"]["tavotto_captured"]
            if opened == "passed"
            else 0
            if opened == "failed"
            else None
        )
        ran = (result.get("detail") or {}).get("execute") or {}
        if type(ran.get("script_exec_ms")) in (int, float):
            row["timings_s"]["stages"]["execute"] = round(ran["script_exec_ms"] / 1000, 3)
        row["needs_authorization"] = _authorization(suite, cid, (), {})
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
            row["raw"] = {"precondition_unmet": str(exc)}
            row["timings_s"]["tavotto"] = round(time.monotonic() - t0, 3)
            row["timings_s"]["total"] = round(time.monotonic() - t_total, 3)
            row["status"] = "environment_unavailable"
            row["failure_category"] = TX.attribute(row)
            return row
        except Exception:  # noqa: BLE001 - harness crash is not a product observation
            row["raw"] = {"runner_error": traceback.format_exc()[-1500:]}
            row["timings_s"]["tavotto"] = round(time.monotonic() - t0, 3)
            row["timings_s"]["total"] = round(time.monotonic() - t_total, 3)
            return finalize_row(row)
        row["raw"] = {
            "observed": obs.to_json(),
            "ideal_missing": cs.IDEAL[cid](obs),
            "historical_baseline": cs.SCENARIOS[cid].main_baseline,
        }
        cb.assert_no_execution_before_run(obs)
        row["executions"]["tavotto"] = obs.exec_count
        row["executions"]["basis"] = "fixture_external_exec_log"
        row["executions"]["attempt_history"] = obs.attempts
        row["executions"]["attempt_history_agrees"] = (
            None if obs.attempts is None else obs.attempts == obs.exec_count
        )
        baseline = cs.SCENARIOS[cid].main_baseline
        match = None if baseline is None else cs.baseline_shape(obs) == baseline
        row["raw"]["baseline_match"] = match
        row["stages"] = v2_stages(
            row["raw"]["observed"],
            native,
            row["raw"]["ideal_missing"],
            designed_stop=cs.SCENARIOS[cid].ideal_outcome == "stop_no_fallback",
            designed_partial=cs.SCENARIOS[cid].ideal_outcome == "partial_figure",
        )
        row["figures"]["tavotto_captured"] = obs.figures_captured
        row["figures"]["tavotto_valid"] = obs.figures_valid
        t = obs.timings_s
        if "scan" in t and "check" in t:
            row["timings_s"]["stages"]["discover"] = round(t["scan"] + t["check"], 3)
        if "run_to_terminal" in t:
            row["timings_s"]["stages"]["execute"] = t["run_to_terminal"]
        row["needs_authorization"] = _authorization(suite, cid, scenario_absent, obs.facts)
        row["unverified"].append(
            "V2 adapter does not drive edit/replay/export/fidelity (reported not_covered)"
        )
    row["timings_s"]["tavotto"] = round(time.monotonic() - t0, 3)
    row["environment_after"] = environment()
    launches = {}
    for role in ("native", "tavotto"):
        log = work / f"{role}-execs.jsonl"
        launches[role] = (
            [
                json.loads(line)["environment"]
                for line in log.read_text(encoding="utf-8").splitlines()
            ]
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
    row["timings_s"]["total"] = round(time.monotonic() - t_total, 3)
    return finalize_row(row)


def preserve_execution_logs(work: Path, evidence: Path) -> None:
    """Retain partial/negative counters before the parent removes its workspace."""
    for path in work.glob("*-execs.jsonl"):
        shutil.copy2(path, evidence / path.name)
    fixture_log = work / "v2" / "exec_log.jsonl"
    if fixture_log.is_file():
        shutil.copy2(fixture_log, evidence / "fixture-execs.jsonl")


def evidence_refs(root: Path) -> list[dict]:
    refs = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(
                root
            ).as_posix()  # 报告里的路径一律 posix：Windows 的 `\` 会被 contained() 判成不安全
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
        "description",
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


def check_row_proof(row: dict, mismatch: bool) -> None:
    """Row-level proof invariants that need only the row itself (no checkout, no evidence files).

    Shared by `validate_report` and by `summarize`, so an aggregator never counts a row whose
    status the row's own facts cannot support (e.g. `passed` with a zero Tavotto execution count).
    """
    if row["status"] == "control_met" and row.get("expectation") is None:
        raise EvidenceError("control_met without an expectation block")
    category = row["failure_category"]["category"]
    if category is not None and category not in TX.CATEGORIES:
        raise EvidenceError("unknown failure category")
    if (category is None) != (row["status"] in GREEN_STATUSES):
        raise EvidenceError("failure category must be set exactly on non-green rows")
    if row["status"] == "partial_coverage" and not any(
        s["status"] == "not_covered" for s in row["stages"].values()
    ):
        raise EvidenceError("partial_coverage without a not_covered layer")
    proof = row["status"] in PROOF_STATUSES and not mismatch
    if proof and _native_status(row["native"]["result"]) != "passed":
        raise EvidenceError("native reference does not support a product verdict")
    if row["native"]["status"] != _native_status(row["native"]["result"]) and row["native"][
        "status"
    ] in ("passed", "invalid_fixture"):
        raise EvidenceError("native status does not follow from native facts")
    for key in ("native", "tavotto"):
        count = row["executions"][key]
        if count is not None and (type(count) is not int or count < 0):
            raise EvidenceError("invalid execution count")
    if proof and row["status"] != case_status(row):
        raise EvidenceError("verdict lacks native/count/environment/stage proof")
    if proof:
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
        refused = refused_before_launch(row)
        for role, records in launches.items():
            if refused and role == "tavotto":
                continue  # nothing was launched on the product leg; its absence is checked above
            if not records or len(records) != row["executions"][role]:
                raise EvidenceError("execution count does not match actual child records")
            for item in records:
                if item.get("fingerprint") != canonical(item.get("facts")):
                    raise EvidenceError("child environment fingerprint mismatch")
                fps.add(item["fingerprint"])
        if len(fps) != 1:
            raise EvidenceError("native and Tavotto launch environments differ")
    if row["status"] == "failed" and not mismatch and row["native"]["status"] != "passed":
        raise EvidenceError("native failure is not a Tavotto defect")


def validate_report(report: dict, evidence_root: Path, expected: dict | None = None) -> None:
    schema_check(report, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
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
        "coverage",
        "expectations_sha256",
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
    registry = selected_registry(report["suite"], ids)
    entries = EX.load_entries()
    if report["expectations_sha256"] != EX.file_digest():
        raise EvidenceError("expectations digest does not match the committed declaration file")
    fixture_map = {}
    for row in rows:
        if row["id"] not in registry:
            raise EvidenceError("case does not belong to registered suite")
        files = fixture_files(report["suite"], registry[row["id"]])
        fixture = fixture_digests(files)
        if row["fixture"] != fixture:
            raise EvidenceError("fixture/source/data digest mismatch")
        fixture_map[row["id"]] = files
        if row["status"] not in ROW_STATUSES or set(row["stages"]) != set(CC.STAGES):
            raise EvidenceError("missing stage/unknown status")
        if any(
            s.get("status") not in STAGE_STATUSES or not s.get("criterion")
            for s in row["stages"].values()
        ):
            raise EvidenceError("missing stage status/criterion")
        entry = EX.find(entries, report["suite"], row["id"])
        resolved = None if entry is None else EX.evaluate(row, entry)
        if row["expectation"] != (None if resolved is None else resolved["expectation"]):
            raise EvidenceError("expectation block does not follow from the committed declaration")
        if resolved is not None and (
            row["status"] != resolved["status"] or row["failure_category"] != resolved["category"]
        ):
            raise EvidenceError("row status does not follow from its declared expectation")
        mismatch = resolved is not None and resolved["status"] == "failed"
        if row["status"] == "control_met" and resolved is None:
            raise EvidenceError("control_met without a committed declaration")
        check_row_proof(row, mismatch)
    if report["verdict"] != verdict(rows):
        raise EvidenceError("incorrect aggregate verdict")
    if report["coverage"] != coverage_table(rows):
        raise EvidenceError("coverage table does not follow from the case stages")
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
    process = package_smoke.run_isolated_command(
        command, env=env, cwd=work, evidence=evidence, timeout=timeout
    )
    (evidence / "process.json").write_text(json.dumps(process), encoding="utf-8")
    if process["cleanup"].get("group_gone") is False:
        raise EvidenceError("adapter cleanup uncertain: process group remains")
    if process["timed_out"]:
        raise AdapterTimeout("adapter timeout; retained stdout/stderr/process evidence")
    if process["exit_code"] != 0:
        raise EvidenceError("adapter failure; retained stdout/stderr/process evidence")


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
    registry = selected_registry(args.suite, args.case.split(",") if args.case else ())
    entries = EX.load_entries()
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
            (work / "request.json").write_text(json.dumps(req), encoding="utf-8")
            # 观测器认的是**实际拷进沙盒的字节**（materialize / V2 staging 原样拷工作区文件）：Windows checkout 下是 CRLF，
            # 与报告里按 HEAD blob 记的语料身份（跨平台一致）不同。内容与 HEAD 等价已由 fixture_files 核过（Codex r4310318506）
            source_hashes = staged_source_hashes(
                ROOT, fixtures[cid], registry[cid].get("script", "script.py"), registry[cid]
            )
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
                row = json.loads((dest / "result.json").read_text(encoding="utf-8"))
            except (subprocess.TimeoutExpired, EvidenceError, OSError, ValueError) as exc:
                row = new_row(cid, "runner")
                row["native"] = {"status": "unknown", "result": {}}
                row["stages"] = {
                    s: stage("not_run", "adapter did not produce valid terminal evidence")
                    for s in CC.STAGES
                }
                row["executions"]["basis"] = "unobserved"
                row["raw"] = (
                    {"adapter_timeout": str(exc)[:300]}
                    if isinstance(exc, AdapterTimeout)
                    else {"runner_error": str(exc)[:300]}
                )
                row["unverified"].append("adapter did not complete")
                finalize_row(row)
            finally:
                preserve_execution_logs(work, dest)
            row["fixture"] = fixture_digests(fixtures[cid])
            entry = EX.find(entries, args.suite, cid)
            if entry is not None:
                resolved = EX.evaluate(row, entry)
                row.update(
                    expectation=resolved["expectation"],
                    status=resolved["status"],
                    failure_category=resolved["category"],
                )
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
        "coverage": coverage_table(rows),
        "expectations_sha256": EX.file_digest(),
        "duration_s": round(time.monotonic() - started, 3),
        "unverified": [
            "real WebView2/WKWebView",
            "unreviewed/external scripts",
            "not user success rate",
            "no automatic promotion/baseline update",
        ],
    }
    try:
        validate_report(report, evidence, initial)
    except EvidenceError:
        # Keep what was observed for diagnosis, under a name `validate` never accepts.
        write_report("guardian.unvalidated.json", report, root=output)
        raise
    path = write_report("guardian.json", report, root=output)
    summary_path = output / "reports" / "summary.md"
    summary_path.write_text(SM.case_markdown(report), encoding="utf-8")
    print(json.dumps({"report": str(path), "run_id": run_id, "verdict": report["verdict"]}))
    return exit_code_for(report["verdict"])


def SM_families() -> list[dict]:
    import compat_triage

    return compat_triage.load_families()


def positive_int(text: str) -> int:
    """argparse type: a count of one or more (0 and negatives select nothing, never 'all')."""
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return value


def positive_seconds(text: str) -> float:
    """argparse type: a finite, positive number of seconds."""
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"must be a finite positive number, got {text}")
    return value


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
    summarizer = subs.add_parser("summarize", help="aggregate reports (never invents a rate)")
    summarizer.add_argument("reports", nargs="+")
    summarizer.add_argument("--json-out")
    summarizer.add_argument("--markdown-out")
    summarizer.add_argument(
        "--require-suites",
        help="comma-separated suites that must each have a report, else the comparison is not_computed",
    )
    summarizer.add_argument(
        "--allow-partial-shards",
        action="store_true",
        help="accept an incomplete set of explore shards (labelled 'M of N completed')",
    )
    summarizer.add_argument("--previous", help="previous summary JSON for fixed-set comparison")
    promoter = subs.add_parser("promote", help="emit a promotion candidate record (proposal only)")
    promoter.add_argument("report")
    promoter.add_argument("--case", required=True)
    promoter.add_argument("--out", required=True)
    explorer = subs.add_parser(
        "explore", help="derive candidates from reviewed parents and run them"
    )
    explorer.add_argument("--seed", type=int, required=True)
    explorer.add_argument("--out", required=True)
    explorer.add_argument(
        "--limit", type=positive_int, help="family-balanced cap on executed candidates"
    )
    explorer.add_argument(
        "--shard",
        type=ES.parse_shard,
        metavar="K/N",
        help="run only slice K of N (1 <= K <= N <= 16) of the deduplicated candidates; "
        "applied after --parent/--families and before --limit",
    )
    explorer.add_argument("--parent", help="comma-separated parent case ids")
    explorer.add_argument("--static-only", action="store_true")
    explorer.add_argument("--candidates", help="comma-separated candidate ids to run")
    explorer.add_argument("--families", help="comma-separated transform families to restrict to")
    explorer.add_argument(
        "--confirm-differences",
        type=int,
        default=0,
        help="re-run up to N differing candidates once",
    )
    explorer.add_argument(
        "--max-seconds",
        type=positive_seconds,
        help="time budget for the whole explore: candidates not started when it is spent are "
        "reported as not_run (partial coverage, exit 4), instead of the job being killed with no report",
    )
    explorer.add_argument("--expected-sha")
    explorer.add_argument("--timeout", type=float, default=240)
    triager = subs.add_parser(
        "triage", help="attribute and cluster findings from run/explore reports"
    )
    triager.add_argument("reports", nargs="+")
    triager.add_argument("--out", required=True)
    triager.add_argument("--markdown-out")
    triager.add_argument(
        "--allow-partial-shards",
        action="store_true",
        help="accept an incomplete set of explore shards (labelled 'M of N completed')",
    )
    minimizer = subs.add_parser(
        "minimize", help="bounded, re-verified reduction of one explore finding"
    )
    minimizer.add_argument("--report", help="explore report containing the candidate")
    minimizer.add_argument("--candidate")
    minimizer.add_argument(
        "--replay", help="repro.json to re-run (instead of --report/--candidate)"
    )
    minimizer.add_argument("--out", required=True)
    minimizer.add_argument("--max-runs", type=int, default=40)
    minimizer.add_argument("--max-seconds", type=float, default=1500)
    minimizer.add_argument("--timeout", type=float, default=240)
    minimizer.add_argument(
        "--repeat", type=int, default=2, help="extra confirmation runs of the final result"
    )
    hidden = subs.add_parser("_explore_execute", help=argparse.SUPPRESS)
    hidden.add_argument("request")
    private = subs.add_parser("_execute", help=argparse.SUPPRESS)
    private.add_argument("request")
    args = ap.parse_args(argv)
    if args.command == "minimize":
        # Two exclusive modes: reduce a finding, or replay a recorded reproduction.
        if args.replay and (args.report or args.candidate):
            ap.error("minimize: --replay cannot be combined with --report/--candidate")
        if not args.replay and not (args.report and args.candidate):
            ap.error("minimize needs --report and --candidate, or --replay")
    try:
        if args.command == "run":
            if not math.isfinite(args.timeout) or args.timeout <= 0:
                raise EvidenceError("timeout must be positive")
            return run(args)
        if args.command == "triage":
            import compat_triage

            return compat_triage.run(args)
        if args.command == "minimize":
            import compat_minimize

            return compat_minimize.run(args)
        if args.command == "explore":
            import compat_explore

            if not math.isfinite(args.timeout) or args.timeout <= 0:
                raise EvidenceError("timeout must be positive")
            return compat_explore.explore(args)
        if args.command == "_explore_execute":
            import compat_explore

            return compat_explore.private_main(args.request)
        if args.command == "summarize":
            import compat_guardian_trend as TR

            kinds = [(TR.classify(Path(r))[0], Path(r)) for r in args.reports]
            run_paths = [p for k, p in kinds if k == "run"]
            explore_paths = [p for k, p in kinds if k == "explore"]
            summary = (
                SM.summarize([SM.load_report(p) for p in run_paths])
                if run_paths
                else {"groups": [], "incomparable": False, "incomparable_reasons": [], "note": ""}
            )
            previous = (
                json.loads(Path(args.previous).read_text(encoding="utf-8"))
                if args.previous and Path(args.previous).is_file()
                else None
            )
            required = tuple(filter(None, (args.require_suites or "").split(",")))
            unknown = set(required) - set(SUITES)
            if unknown:
                raise EvidenceError(f"--require-suites names an unknown suite: {sorted(unknown)}")
            shards = ES.check_set(
                [TR.classify(p)[1] for p in explore_paths], bool(args.allow_partial_shards)
            )
            trend = TR.build(
                run_paths, explore_paths, previous, SM_families(), required, shards=shards
            )
            summary["trend"] = trend
            markdown = (
                (SM.render_markdown(summary) if run_paths else "")
                + "\n"
                + TR.render_markdown(trend)
            )
            if args.json_out:
                Path(args.json_out).write_text(
                    json.dumps(summary, indent=2) + "\n", encoding="utf-8"
                )
            if args.markdown_out:
                Path(args.markdown_out).write_text(markdown, encoding="utf-8")
            print(markdown)
            return 0
        if args.command == "promote":
            candidate = SM.promote(SM.load_report(Path(args.report)), args.case, Path(args.out))
            print(json.dumps({"candidate": args.out, "eligible": candidate["eligible_for_review"]}))
            return 0
        if args.command == "validate":
            path = Path(args.report)
            validate_report(
                json.loads(path.read_text(encoding="utf-8")),
                path.parent.parent / "evidence",
                source_state(),
            )
            print("valid current-checkout evidence")
            return 0
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
        fixture_files(
            request["suite"],
            selected_registry(request["suite"], (request["case"],))[request["case"]],
        )
        row = _execute(request)
        (Path(request["evidence"]) / "result.json").write_text(
            json.dumps(row, indent=2), encoding="utf-8"
        )
        return 0
    except (EvidenceError, KeyError, ValueError, OSError) as exc:
        print(f"guardian: {exc}", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        return 2


import compat_explore_shards as ES  # noqa: E402
import compat_guardian_expectations as EX  # noqa: E402
import compat_guardian_summary as SM  # noqa: E402  (circular by design; bottom import)

if __name__ == "__main__":
    # Run through the importable module so helper modules share one EvidenceError class.
    import compat_guardian

    raise SystemExit(compat_guardian.main())
