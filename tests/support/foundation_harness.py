"""FirstOpenBench / RenderBench 共享的结果装配（统一实施包 U01，ADR 0053 §五）。

三样东西，各自一个问题，**互不冒充**：

* **台账**（`docs/implementation/tavotto-foundation/enrollment.json`）——每个 case 处于
  planned / observing / enforced / later 哪一档、归哪条 lane、哪个阶段负责、enforced 的
  指向哪条真实 pytest 用例。它是工程事实，不是测试成绩：全部 planned 的台账是完整的，
  但**没有一条是「通过」**。
* **预期实例集合**（`expected_instances()`）——在执行**之前**由台账 + lane + 本次环境的
  绑定（源码 SHA / 产物 / 平台 / 应用 Python / runtime 锁 / fixture 身份 / 入口 / 能力版本）
  生成。实例 id 由绑定派生：换一个 SHA、换一台平台、换一份 fixture 就是另一个实例。
* **结果记录**（`ResultRecord`，由 enforced 用例在跑完那一刻写进结果目录）——
  `product_outcome`（产品做到了什么：automatic / guided / safe_stop / failed / not_run）
  与 `test_verdict`（用例怎么判：pass / product_failure / infra_error / safe_stop / not_run）
  **分开记**；observed 里是回执与产物身份。

`validate()` 只回答一个问题：**预期实例集合 == 已提交的有效结果集合**——无重复、无缺失、
无错 SHA、无错产物，且每条有效结果 `test_verdict == pass`。空的预期集合、空的结果目录
都是红：**空集合永远不是通过**（`03_CI_POLICY.md` §5）。台账里 planned 的 case 在报告里
单列，与「已通过」分开。

报告三份（`write_reports()`）：JSON（机器）、JUnit（接现有 `--junitxml` 上传）、摘要 md
（人）。纯标准库；不 import 任何产品模块——校验器不该依赖它要验的东西。

CLI：

    python tests/support/foundation_harness.py expected --lane pr --out expected.json
    python tests/support/foundation_harness.py validate --expected expected.json \
        --results <dir> --report-dir <dir>
    python tests/support/foundation_harness.py ledger-check
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
PACK = ROOT / "docs" / "implementation" / "tavotto-foundation"
LEDGER_PATH = PACK / "enrollment.json"
REGISTRY_PATH = PACK / "registry.json"
RUNTIME_LOCK = ROOT / "packaging" / "runtime-lock.json"

SCHEMA_VERSION = 1

ENROLLMENT_PLANNED = "planned"
ENROLLMENT_OBSERVING = "observing"
ENROLLMENT_ENFORCED = "enforced"
ENROLLMENT_LATER = "later"
ENROLLMENTS = (ENROLLMENT_PLANNED, ENROLLMENT_OBSERVING, ENROLLMENT_ENFORCED, ENROLLMENT_LATER)

LANES = ("pr", "integration", "nightly", "release")

#: 产品做到了什么（与 registry 的 expected_product_outcome 同一套词 + 两档失败）。
OUTCOMES = ("automatic", "guided", "safe_stop", "failed", "not_run")
#: 用例怎么判。`safe_stop` 的 pass **不进**兼容成功分子（05 §7）。
VERDICTS = ("pass", "product_failure", "infra_error", "safe_stop", "not_run")

#: 结果目录由谁指定：enforced 用例读它写记录；CI 的校验步读它验。没设 = 本地跑，
#: 记录写进临时目录，不参与任何校验。
RESULTS_ENV = "TAVOTTO_FOUNDATION_RESULTS"


# ---------------------------------------------------------------- 台账


def load_ledger(path: Path = LEDGER_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def ledger_errors(ledger: dict, registry: dict | None = None) -> list[str]:
    """台账自身的结构 + 与 registry 的一致性。回错误列表（空 = 好）。"""
    errors: list[str] = []
    if ledger.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version 必须是 {SCHEMA_VERSION}")
    if not isinstance(ledger.get("capability_version"), str) or not ledger["capability_version"]:
        errors.append("capability_version 缺失")
    cases = ledger.get("cases")
    if not isinstance(cases, list) or not cases:
        return errors + ["cases 必须是非空数组"]
    seen: set[str] = set()
    for c in cases:
        cid = c.get("case_id")
        if not isinstance(cid, str) or not cid:
            errors.append(f"case 缺 case_id: {c!r}")
            continue
        if cid in seen:
            errors.append(f"重复的 case_id: {cid}")
        seen.add(cid)
        if c.get("enrollment") not in ENROLLMENTS:
            errors.append(f"{cid}: enrollment 非法 {c.get('enrollment')!r}")
        if c.get("lane") not in LANES:
            errors.append(f"{cid}: lane 非法 {c.get('lane')!r}")
        if not isinstance(c.get("stage"), str) or not c["stage"]:
            errors.append(f"{cid}: stage 缺失")
        refs = c.get("scenario_refs")
        if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
            errors.append(f"{cid}: scenario_refs 必须是字符串数组")
        test = c.get("test")
        if c.get("enrollment") == ENROLLMENT_ENFORCED:
            if not isinstance(test, str) or "::" not in test:
                errors.append(f"{cid}: enforced 的 case 必须指向一条 pytest 用例（文件::函数）")
            else:
                file_part, func = test.split("::", 1)
                path = ROOT / file_part
                if not path.is_file():
                    errors.append(f"{cid}: 用例文件不存在 {file_part}")
                elif f"def {func}(" not in path.read_text(encoding="utf-8"):
                    errors.append(f"{cid}: {file_part} 里没有 def {func}(")
            if not c.get("fixture"):
                errors.append(f"{cid}: enforced 的 case 必须指明 fixture")
        elif test is not None:
            errors.append(
                f"{cid}: 只有 enforced 的 case 才能有 test（现在是 {c.get('enrollment')}）"
            )
    if registry is not None:
        by_id = {c["case_id"]: c for c in cases if isinstance(c.get("case_id"), str)}
        for e in registry.get("entries", []):
            if e.get("kind") != "scenario":
                continue
            sid = e["id"]
            row = by_id.get(sid)
            if row is None:
                errors.append(f"registry 场景 {sid} 不在台账里")
                continue
            if row.get("enrollment") != e.get("enrollment"):
                errors.append(
                    f"{sid}: 台账 enrollment {row.get('enrollment')!r} 与 registry {e.get('enrollment')!r} 不一致"
                )
            if row.get("stage") != e.get("implementation_stage"):
                errors.append(
                    f"{sid}: 台账 stage {row.get('stage')!r} 与 registry {e.get('implementation_stage')!r} 不一致"
                )
    return errors


# ---------------------------------------------------------------- 绑定与预期集合


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture_hash(path: Path) -> str:
    """一个 fixture 目录的内容身份：相对路径 + 字节，逐文件哈希（不含 `__pycache__` /
    `.venv` / 产物）。同一份 fixture 在任何机器上算出同一个值。"""
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        rel = p.relative_to(path).as_posix()
        if any(part in ("__pycache__", ".venv") for part in p.parts) or not p.is_file():
            continue
        h.update(rel.encode("utf-8") + b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def source_sha(root: Path = ROOT) -> str:
    """本次源码的 SHA：CI 上是 `GITHUB_SHA`（merge_group 的组合提交），本地 `git rev-parse HEAD`。"""
    env = os.environ.get("GITHUB_SHA", "").strip()
    if env:
        return env
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    return out.stdout.strip() if out.returncode == 0 else "unknown"


def binding_from_environment(
    *,
    ledger: dict,
    entry: str,
    fixture: str | None,
    root: Path = ROOT,
) -> dict:
    """本次执行的绑定：实例 id 由它派生，用例与校验器**各自**从同一份环境算一遍。

    `artifact` 在源码树上跑就是源码树本身（`kind=source_tree`）；U11 起对 wheel /
    安装包会是那份产物的 sha256。
    """
    sha = source_sha(root)
    lock = _sha256_bytes(RUNTIME_LOCK.read_bytes()) if RUNTIME_LOCK.is_file() else ""
    fx = fixture_hash(root / fixture) if fixture else ""
    return {
        "source_sha": sha,
        "artifact": {"kind": "source_tree", "identity": sha},
        "platform": f"{sys.platform}-{platform.machine()}",
        "app_python": platform.python_version(),
        "runtime_lock_sha256": lock,
        "fixture": fixture or "",
        "fixture_sha256": fx,
        "entry": entry,
        "capability_version": ledger["capability_version"],
    }


def instance_id(case_id: str, binding: dict) -> str:
    canon = json.dumps(
        {"case_id": case_id, "binding": binding}, sort_keys=True, separators=(",", ":")
    )
    return f"{case_id}@{hashlib.sha256(canon.encode('utf-8')).hexdigest()[:16]}"


def expected_instances(ledger: dict, lane: str, *, root: Path = ROOT) -> dict:
    """执行前生成：这条 lane 上 enforced 的每个 case 一条实例。

    没有 enforced case 的 lane 回空集合——**那不是「全部通过」**，`validate()` 会红。
    planned / observing / later 的 case 单列在 `planned_cases`，只报告不计分。
    """
    if lane not in LANES:
        raise ValueError(f"lane 非法: {lane!r}（可选 {LANES}）")
    instances = []
    planned = []
    for c in ledger["cases"]:
        if c["lane"] != lane and c["enrollment"] == ENROLLMENT_ENFORCED:
            continue
        if c["enrollment"] == ENROLLMENT_ENFORCED:
            binding = binding_from_environment(
                ledger=ledger, entry=c.get("entry", "http"), fixture=c.get("fixture"), root=root
            )
            instances.append(
                {
                    "instance_id": instance_id(c["case_id"], binding),
                    "case_id": c["case_id"],
                    "test": c["test"],
                    "binding": binding,
                }
            )
        else:
            planned.append(
                {
                    "case_id": c["case_id"],
                    "enrollment": c["enrollment"],
                    "lane": c["lane"],
                    "stage": c["stage"],
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "lane": lane,
        "generated_at": time.time(),
        "instances": instances,
        "planned_cases": planned,
    }


# ---------------------------------------------------------------- 结果记录


@dataclasses.dataclass(frozen=True)
class ResultRecord:
    case_id: str
    binding: dict
    product_outcome: str
    test_verdict: str
    observed: dict
    evidence: tuple[str, ...] = ()
    recorded_at: float = dataclasses.field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.product_outcome not in OUTCOMES:
            raise ValueError(f"product_outcome 非法: {self.product_outcome!r}")
        if self.test_verdict not in VERDICTS:
            raise ValueError(f"test_verdict 非法: {self.test_verdict!r}")
        if self.test_verdict == "pass" and self.product_outcome not in ("automatic", "guided"):
            raise ValueError(
                "test_verdict=pass 的产品结果只能是 automatic / guided（safe_stop 另记）"
            )

    @property
    def instance_id(self) -> str:
        return instance_id(self.case_id, self.binding)

    def to_payload(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "instance_id": self.instance_id,
            "case_id": self.case_id,
            "binding": dict(self.binding),
            "product_outcome": self.product_outcome,
            "test_verdict": self.test_verdict,
            "observed": self.observed,
            "evidence": list(self.evidence),
            "recorded_at": self.recorded_at,
        }


def results_dir() -> Path | None:
    """enforced 用例往哪写：环境变量指定的目录；没设 = None（本地跑，不参与校验）。"""
    raw = os.environ.get(RESULTS_ENV, "").strip()
    return Path(raw) if raw else None


def write_result(record: ResultRecord, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record.instance_id.replace('@', '__')}.json"
    path.write_text(json.dumps(record.to_payload(), ensure_ascii=False, indent=1), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 校验


def _load_results(directory: Path) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    out: list[dict] = []
    if not directory.is_dir():
        return out, [f"结果目录不存在: {directory}"]
    for p in sorted(directory.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"结果文件读不出来 {p.name}: {exc}")
            continue
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"结果文件 schema 不对: {p.name}")
            continue
        data["_file"] = p.name
        out.append(data)
    return out, errors


def validate(expected: dict, results: Path) -> dict:
    """预期实例集合 == 已提交有效结果集合。回报告 dict（`ok` 是唯一结论）。"""
    problems: list[dict] = []
    want = {i["instance_id"]: i for i in expected.get("instances", [])}
    if not want:
        problems.append(
            {"kind": "empty_expected_set", "detail": "这条 lane 没有 enforced 实例——空集合不是通过"}
        )
    submitted, load_errors = _load_results(results)
    for e in load_errors:
        problems.append({"kind": "unreadable", "detail": e})
    if want and not submitted:
        problems.append({"kind": "empty_report", "detail": f"结果目录里一条记录都没有: {results}"})

    seen: dict[str, str] = {}
    valid: dict[str, dict] = {}
    for r in submitted:
        iid = r.get("instance_id", "")
        # 记录自称的 id 必须能从它自己的绑定重新算出来——改了绑定不改 id 的记录是坏的
        derived = instance_id(r.get("case_id", ""), r.get("binding") or {})
        if iid != derived:
            problems.append(
                {
                    "kind": "identity_mismatch",
                    "instance_id": iid,
                    "detail": f"{r['_file']}: 记录的 id 与其绑定派生的不一致",
                }
            )
            continue
        if iid in seen:
            problems.append(
                {
                    "kind": "duplicate",
                    "instance_id": iid,
                    "detail": f"{r['_file']} 与 {seen[iid]} 是同一个实例",
                }
            )
            continue
        seen[iid] = r["_file"]
        exp = want.get(iid)
        if exp is None:
            problems.append(
                {
                    "kind": "unexpected",
                    "instance_id": iid,
                    "detail": f"{r['_file']}: 不在预期集合里（错 SHA / 错平台 / 错 fixture / 错 lane 都长这样）",
                }
            )
            continue
        if r.get("binding") != exp["binding"]:
            problems.append(
                {
                    "kind": "binding_mismatch",
                    "instance_id": iid,
                    "detail": f"{r['_file']}: 绑定与预期不同",
                }
            )
            continue
        verdict = r.get("test_verdict")
        if verdict not in VERDICTS or r.get("product_outcome") not in OUTCOMES:
            problems.append(
                {
                    "kind": "bad_verdict",
                    "instance_id": iid,
                    "detail": f"{r['_file']}: verdict/outcome 非法",
                }
            )
            continue
        observed = r.get("observed") or {}
        gen_receipt = (observed.get("receipt") or {}).get("generation")
        gen_artifacts = {
            a.get("generation")
            for a in observed.get("artifacts") or []
            if a.get("origin") == "execution"
        }
        if gen_artifacts and gen_artifacts != {gen_receipt}:
            problems.append(
                {
                    "kind": "wrong_generation",
                    "instance_id": iid,
                    "detail": f"{r['_file']}: 产物的 generation {sorted(gen_artifacts)} 与回执的 {gen_receipt} 对不上",
                }
            )
            continue
        if verdict != "pass":
            problems.append(
                {
                    "kind": verdict,
                    "instance_id": iid,
                    "detail": f"{r['_file']}: test_verdict={verdict}, product_outcome={r.get('product_outcome')}",
                }
            )
            continue
        valid[iid] = r
    for iid in want:
        if iid not in seen:
            problems.append(
                {"kind": "missing", "instance_id": iid, "detail": f"预期实例没有结果: {iid}"}
            )

    by_verdict: dict[str, int] = {}
    for r in submitted:
        v = r.get("test_verdict", "invalid")
        by_verdict[v] = by_verdict.get(v, 0) + 1
    ok = not problems and set(valid) == set(want) and bool(want)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "foundation_harness_validation",
        "ok": ok,
        "lane": expected.get("lane"),
        "expected_count": len(want),
        "submitted_count": len(submitted),
        "valid_count": len(valid),
        "problems": problems,
        "by_verdict": by_verdict,
        "passed_instances": sorted(valid),
        "planned_cases": expected.get("planned_cases", []),
        "note": "planned / observing / later 的 case 只登记，不是通过；空集合不是通过",
    }


# ---------------------------------------------------------------- 报告


def _junit(report: dict, expected: dict) -> str:
    cases = []
    problems_by_iid: dict[str, list[dict]] = {}
    for p in report["problems"]:
        problems_by_iid.setdefault(p.get("instance_id", ""), []).append(p)
    for inst in expected.get("instances", []):
        iid = inst["instance_id"]
        probs = problems_by_iid.get(iid, [])
        body = ""
        if probs:
            msg = escape("; ".join(f"{p['kind']}: {p['detail']}" for p in probs))
            body = f'<failure message="{msg}"/>'
        cases.append(
            f'<testcase classname="foundation.{expected.get("lane")}" name="{escape(iid)}">{body}</testcase>'
        )
    global_probs = problems_by_iid.get("", [])
    body = ""
    if global_probs or not report["ok"]:
        msg = escape(
            "; ".join(f"{p['kind']}: {p['detail']}" for p in global_probs)
            or "closed-set check failed"
        )
        body = f'<failure message="{msg}"/>'
    cases.append(
        f'<testcase classname="foundation.{expected.get("lane")}" name="closed_set_check">{body}</testcase>'
    )
    failures = sum(1 for c in cases if "<failure" in c)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<testsuite name="foundation-harness" tests="{len(cases)}" failures="{failures}">\n'
        + "\n".join(cases)
        + "\n</testsuite>\n"
    )


def _summary_md(report: dict) -> str:
    lines = [
        f"# Foundation harness · lane `{report['lane']}` · {'OK' if report['ok'] else 'FAILED'}",
        "",
        f"- 预期实例 {report['expected_count']} · 提交 {report['submitted_count']} · 有效通过 {report['valid_count']}",
        f"- 按 verdict：{json.dumps(report['by_verdict'], ensure_ascii=False)}",
        "",
        "## 通过的实例（enforced）",
        "",
    ]
    lines += [f"- `{i}`" for i in report["passed_instances"]] or ["- （无）"]
    lines += ["", "## 问题", ""]
    lines += [
        f"- **{p['kind']}** {p.get('instance_id', '')} — {p['detail']}" for p in report["problems"]
    ] or ["- （无）"]
    lines += ["", "## 台账里尚未纳入的 case（不是通过）", ""]
    lines += [
        f"- {c['case_id']} · {c['enrollment']} · {c['lane']} · {c['stage']}"
        for c in report["planned_cases"]
    ] or ["- （无）"]
    return "\n".join(lines) + "\n"


def write_reports(report: dict, expected: dict, report_dir: Path) -> dict[str, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": report_dir / "foundation-harness.json",
        "junit": report_dir / "foundation-harness-junit.xml",
        "summary": report_dir / "foundation-harness-summary.md",
    }
    paths["json"].write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    paths["junit"].write_text(_junit(report, expected), encoding="utf-8")
    paths["summary"].write_text(_summary_md(report), encoding="utf-8")
    return paths


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("expected", help="执行前生成预期实例集合")
    e.add_argument("--ledger", default=str(LEDGER_PATH))
    e.add_argument("--lane", required=True, choices=LANES)
    e.add_argument("--out", required=True)
    v = sub.add_parser("validate", help="预期集合 == 已提交有效结果集合")
    v.add_argument("--expected", required=True)
    v.add_argument("--results", required=True)
    v.add_argument("--report-dir", required=True)
    lc = sub.add_parser("ledger-check", help="台账结构 + 与 registry 一致")
    lc.add_argument("--ledger", default=str(LEDGER_PATH))
    lc.add_argument("--registry", default=str(REGISTRY_PATH))
    args = ap.parse_args(argv)

    if args.cmd == "expected":
        ledger = load_ledger(Path(args.ledger))
        errs = ledger_errors(ledger)
        if errs:
            print("\n".join(errs), file=sys.stderr)
            return 2
        exp = expected_instances(ledger, args.lane)
        Path(args.out).write_text(json.dumps(exp, ensure_ascii=False, indent=1), encoding="utf-8")
        print(
            f"expected: lane={args.lane} instances={len(exp['instances'])} planned={len(exp['planned_cases'])}"
        )
        return 0
    if args.cmd == "validate":
        expected = json.loads(Path(args.expected).read_text(encoding="utf-8"))
        report = validate(expected, Path(args.results))
        paths = write_reports(report, expected, Path(args.report_dir))
        print(_summary_md(report))
        print(f"reports: {', '.join(str(p) for p in paths.values())}")
        return 0 if report["ok"] else 1
    if args.cmd == "ledger-check":
        errs = ledger_errors(
            load_ledger(Path(args.ledger)),
            json.loads(Path(args.registry).read_text(encoding="utf-8")),
        )
        print("\n".join(errs) if errs else "ledger ok")
        return 1 if errs else 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
