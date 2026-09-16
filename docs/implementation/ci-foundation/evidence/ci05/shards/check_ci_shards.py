#!/usr/bin/env python3
"""CI 侧分片完整性（CI05，CIP-016 / CIP-017 的 CI 半边）。

输入是 CI 真上传的 artifact（不是本机跑的）：

* pytest：每条腿两片的 `shard-manifest.json` + `junit.xml`
  （artifact `pytest-<job>-<os>-<python>-shard<K>`）。主语是 **junit 里 (classname, name)
  的集合** 与 **manifest 里 selected_files 的集合**，不是计数：两片各漏一条再各多一条，
  计数照样对得上。
* Playwright：两片的 `playwright-shard-check-windows-shard<K>`（`list_all.txt` /
  `list_shard<K>.txt` / `check.json`）。主语是 **(project, file:line:col, title)** 集合。
  这里的解析器与 `scripts/ci/playwright_shard_check.py` **刻意不同源**（只认
  `[project] › loc › title` 一种行形状，别的行一律抛），所以「两片声称的并集 == 全集」
  在这里是第二把尺子，不是同一把尺子量两遍。

任一条不成立退出码 1；读不懂输入退出码 2。结果写成 JSON（每条 check 一个布尔 + 数字）。
纯标准库。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


class ShapeError(RuntimeError):
    pass


def junit_cases(path: Path) -> dict[tuple[str, str], str]:
    """(classname, name) → outcome。重复的 (classname, name) 直接抛：那是「一份报告充数」的形状。"""
    root = ET.parse(path).getroot()
    out: dict[tuple[str, str], str] = {}
    for tc in root.iter("testcase"):
        key = (tc.get("classname") or "", tc.get("name") or "")
        if not key[0] or not key[1]:
            raise ShapeError(f"{path}: testcase 缺 classname / name")
        if key in out:
            raise ShapeError(f"{path}: 重复的 testcase {key}")
        outcome = "passed"
        for child in tc:
            if child.tag in ("skipped", "failure", "error"):
                outcome = child.tag
        out[key] = outcome
    if not out:
        raise ShapeError(f"{path}: 一条 testcase 都没有")
    return out


def file_of(classname: str, files: set[str]) -> str:
    """`tests.bridge.test_x[.TestFoo]` → `tests/bridge/test_x.py`，只认 manifest 里出现过的文件。
    从最长前缀往回试；对不回去就抛——喂错树的 junit 不会静默变成半张表。"""
    parts = classname.split(".")
    for n in range(len(parts), 0, -1):
        cand = "/".join(parts[:n]) + ".py"
        if cand in files:
            return cand
    raise ShapeError(f"classname {classname!r} 对不回 manifest 里的任何文件")


def check_pytest_leg(leg_dir: Path, leg: str) -> dict:
    m1 = json.loads((leg_dir / f"{leg}-shard1" / "shard-manifest.json").read_text("utf-8"))
    m2 = json.loads((leg_dir / f"{leg}-shard2" / "shard-manifest.json").read_text("utf-8"))
    j1 = junit_cases(leg_dir / f"{leg}-shard1" / "junit.xml")
    j2 = junit_cases(leg_dir / f"{leg}-shard2" / "junit.xml")
    if (m1["shard"], m1["shards"], m2["shard"], m2["shards"]) != (1, 2, 2, 2):
        raise ShapeError(f"{leg}: manifest 的片号不是 1/2 与 2/2")
    f1, f2 = set(m1["selected_files"]), set(m2["selected_files"])
    all_files = f1 | f2
    k1, k2 = set(j1), set(j2)
    files_from_junit_1 = {file_of(c, all_files) for c, _ in k1}
    files_from_junit_2 = {file_of(c, all_files) for c, _ in k2}
    checks = {
        "manifests_agree_on_the_full_set": (
            m1["nodeids_total"] == m2["nodeids_total"]
            and m1["files_total"] == m2["files_total"]
            and m1["git_head"] == m2["git_head"]
            and m1["per_shard_nodeids"] == m2["per_shard_nodeids"]
            and m1["per_shard_files"] == m2["per_shard_files"]
        ),
        "selected_files_are_disjoint": not (f1 & f2),
        "selected_files_union_is_the_full_file_set": len(all_files) == m1["files_total"]
        and m1["per_shard_files"] == [len(f1), len(f2)]
        and m1["per_shard_nodeids"] == [len(j1), len(j2)],
        "junit_testcases_are_disjoint": not (k1 & k2),
        "junit_union_equals_nodeids_total": len(k1 | k2) == m1["nodeids_total"],
        "junit_count_equals_nodeids_selected_per_shard": (
            len(k1) == m1["nodeids_selected"] and len(k2) == m2["nodeids_selected"]
        ),
        "junit_files_equal_selected_files_per_shard": (
            files_from_junit_1 == f1 and files_from_junit_2 == f2
        ),
    }
    outcomes = {}
    for name, cases in (("shard1", j1), ("shard2", j2)):
        outcomes[name] = {
            o: sum(1 for v in cases.values() if v == o)
            for o in ("passed", "skipped", "failure", "error")
        }
    return {
        "leg": leg,
        "git_head": m1["git_head"],
        "platform": [m1["platform"], m2["platform"]],
        "python": [m1["python"], m2["python"]],
        "nodeids_total": m1["nodeids_total"],
        "files_total": m1["files_total"],
        "nodeids_selected": [m1["nodeids_selected"], m2["nodeids_selected"]],
        "files_selected": [m1["files_selected"], m2["files_selected"]],
        "junit_testcases": [len(k1), len(k2)],
        "junit_union": len(k1 | k2),
        "junit_intersection": len(k1 & k2),
        "outcomes": outcomes,
        "checks": checks,
        "ok": all(checks.values()),
        "nodeid_union": sorted(f"{c}::{n}" for c, n in (k1 | k2)),
    }


_PW_LINE = re.compile(r"^\s+\[([^\]]+)\] › (\S+:\d+:\d+) › (.+)$")


def playwright_entries(path: Path) -> set[tuple[str, str, str]]:
    """只认 `[project] › loc › title` 行；`Listing tests:` / `Total:` 之外的别的行一律抛。"""
    entries: set[tuple[str, str, str]] = set()
    total = None
    for raw in path.read_text("utf-8").splitlines():
        if not raw.strip() or raw.startswith("Listing tests"):
            continue
        tm = re.fullmatch(r"Total: (\d+) tests? in \d+ files?", raw.strip())
        if tm:
            total = int(tm.group(1))
            continue
        m = _PW_LINE.match(raw)
        if not m:
            raise ShapeError(f"{path}: 这一行认不出：{raw!r}")
        key = (m.group(1), m.group(2), m.group(3))
        if key in entries:
            raise ShapeError(f"{path}: 重复条目 {key}")
        entries.add(key)
    if total is None or total != len(entries):
        raise ShapeError(f"{path}: Total 行 {total} 与解析出的 {len(entries)} 条不符")
    return entries


def check_playwright(run_dir: Path) -> dict:
    d1 = run_dir / "playwright-shard-check-windows-shard1"
    d2 = run_dir / "playwright-shard-check-windows-shard2"
    all1 = playwright_entries(d1 / "list_all.txt")
    all2 = playwright_entries(d2 / "list_all.txt")
    s1 = playwright_entries(d1 / "list_shard1.txt")
    s2 = playwright_entries(d2 / "list_shard2.txt")
    c1 = json.loads((d1 / "check.json").read_text("utf-8"))
    c2 = json.loads((d2 / "check.json").read_text("utf-8"))
    projects = lambda s: {p for p, _, _ in s}  # noqa: E731
    checks = {
        "both_machines_listed_the_same_full_set": all1 == all2,
        "shards_are_disjoint": not (s1 & s2),
        "shards_union_is_the_full_set": (s1 | s2) == all1,
        "project_sets_partition_the_config": projects(s1) | projects(s2) == projects(all1)
        and not (projects(s1) & projects(s2)),
        "each_shard_self_check_said_ok": c1.get("ok") is True and c2.get("ok") is True,
        "self_check_counts_match_independent_parse": (
            c1.get("mine_total") == len(s1)
            and c2.get("mine_total") == len(s2)
            and c1.get("full_total") == len(all1)
        ),
    }
    return {
        "full_total": len(all1),
        "per_project": {p: sum(1 for q, _, _ in all1 if q == p) for p in sorted(projects(all1))},
        "shard1": {"projects": sorted(projects(s1)), "count": len(s1)},
        "shard2": {"projects": sorted(projects(s2)), "count": len(s2)},
        "checks": checks,
        "ok": all(checks.values()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--pytest-dir", help="解压后的 pytest-* artifact 所在目录")
    ap.add_argument("--pytest-run-id", type=int)
    ap.add_argument("--playwright-dir", action="append", default=[])
    ap.add_argument("--playwright-run-id", action="append", type=int, default=[])
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    report: dict = {"kind": "ci05_ci_side_shard_completeness", "pytest": None, "playwright": []}
    try:
        if args.pytest_dir:
            base = Path(args.pytest_dir)
            legs = sorted(
                {p.name[: -len("-shard1")] for p in base.iterdir() if p.name.endswith("-shard1")}
            )
            if not legs:
                raise ShapeError(f"{base} 下没有 *-shard1 目录")
            results = [check_pytest_leg(base, leg) for leg in legs]
            unions = {r["leg"]: set(r["nodeid_union"]) for r in results}
            ref_leg, ref = results[0]["leg"], unions[results[0]["leg"]]
            raw_diff = {
                leg: {"only_here": sorted(u - ref), "missing": sorted(ref - u)}
                for leg, u in unions.items()
                if u != ref
            }
            # 参数 id 里带路径分隔符的用例（WindowsPath 的 id 是反斜杠）在 Windows 腿上名字不同、
            # 用例是同一条：按分隔符归一之后再比一次。归一前的差异原样列出，不静默。
            norm = lambda s: re.sub(r"\\\\+", "/", s)  # noqa: E731
            normalised = {leg: {norm(x) for x in u} for leg, u in unions.items()}
            same_after_norm = len({frozenset(u) for u in normalised.values()}) == 1
            report["pytest"] = {
                "run_id": args.pytest_run_id,
                "legs": [{k: v for k, v in r.items() if k != "nodeid_union"} for r in results],
                "nodeid_union_size": len(ref),
                "reference_leg": ref_leg,
                "raw_union_differences_vs_reference": raw_diff,
                "nodeid_union_identical_across_all_legs_after_path_separator_normalisation": (
                    same_after_norm
                ),
                "ok": all(r["ok"] for r in results) and same_after_norm,
            }
            # 一份全集清单（各腿相同时只存一份）
            Path(args.out).with_name("nodeid_union.txt").write_text(
                "\n".join(results[0]["nodeid_union"]) + "\n", "utf-8"
            )
        for d, rid in zip(args.playwright_dir, args.playwright_run_id, strict=True):
            r = check_playwright(Path(d))
            r["run_id"] = rid
            report["playwright"].append(r)
    except ShapeError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2
    report["ok"] = (report["pytest"] is None or report["pytest"]["ok"]) and all(
        r["ok"] for r in report["playwright"]
    )
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps({k: v for k, v in report.items() if k == "ok"}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
