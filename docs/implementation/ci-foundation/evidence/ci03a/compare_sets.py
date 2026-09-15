"""CI03a 的集合级比对（主语是 (classname, name) 的集合，不是计数）。退出码 0 = 全部成立，1 = 有不成立。

用法：compare_sets.py <runs_dir> <out.json>
输入：full.xml s1.xml s2.xml c1.xml c2.xml m1.json m2.json c1.json c2.json
断言：s1 ∪ s2 == full；s1 ∩ s2 == ∅；c1 == s1；c2 == s2；两片非空；
      passed/skipped/failed 计数逐片相加等于 full；manifest 的 selected_files == junit 里出现的文件集合；
      manifest 的 nodeids_selected == junit 的 testcase 数、nodeids_total == full 的 testcase 数。
"""

import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


def load(path: Path):
    out = {}
    for case in ET.parse(path).iter("testcase"):
        key = (case.get("classname"), case.get("name"))
        if case.find("failure") is not None:
            o = "failed"
        elif case.find("error") is not None:
            o = "error"
        elif case.find("skipped") is not None:
            o = "skipped"
        else:
            o = "passed"
        if key in out:
            raise SystemExit(f"{path}: 重复 testcase {key}")
        out[key] = o
    return out


def files_of(cases, root: Path) -> set[str]:
    """classname → 文件（最长前缀在 root 下存在的 .py），与 shard.file_of_classname 同一规则。"""
    files = set()
    for classname, _ in cases:
        parts = classname.split(".")
        for n in range(len(parts), 0, -1):
            cand = "/".join(parts[:n]) + ".py"
            if (root / cand).is_file():
                files.add(cand)
                break
        else:
            raise SystemExit(f"classname {classname!r} 对不回文件")
    return files


runs = Path(sys.argv[1])
out_p = Path(sys.argv[2])
root = Path("/Volumes/Projects/tavotto-wt/ci-foundation")
full, s1, s2, c1, c2 = (load(runs / f"{n}.xml") for n in ("full", "s1", "s2", "c1", "c2"))
m1, m2, cm1, cm2 = (
    json.loads((runs / f"{n}.json").read_text(encoding="utf-8")) for n in ("m1", "m2", "c1", "c2")
)


def counts(d):
    return dict(Counter(d.values()))


def reds(d):
    return sorted(f"{c}::{n}" for (c, n), o in d.items() if o != "passed" and o != "skipped")


checks = {
    "s1_union_s2_equals_full": set(s1) | set(s2) == set(full),
    "s1_disjoint_s2": not (set(s1) & set(s2)),
    "both_shards_nonempty": bool(s1) and bool(s2),
    "c1_equals_s1_as_sets": set(c1) == set(s1),
    "c2_equals_s2_as_sets": set(c2) == set(s2),
    "outcome_counts_add_up": Counter(full.values()) == Counter(s1.values()) + Counter(s2.values()),
    "concurrent_outcomes_identical_to_sequential": c1 == s1 and c2 == s2,
    "m1_selected_files_equal_s1_junit_files": set(m1["selected_files"]) == files_of(s1, root),
    "m2_selected_files_equal_s2_junit_files": set(m2["selected_files"]) == files_of(s2, root),
    "m1_nodeids_selected_equals_s1_testcases": m1["nodeids_selected"] == len(s1),
    "m2_nodeids_selected_equals_s2_testcases": m2["nodeids_selected"] == len(s2),
    "manifests_nodeids_total_equals_full_testcases": m1["nodeids_total"]
    == len(full)
    == m2["nodeids_total"],
    "concurrent_manifests_identical_to_sequential": {
        k: cm1[k] for k in cm1 if k not in ("platform", "python", "git_head")
    }
    == {k: m1[k] for k in m1 if k not in ("platform", "python", "git_head")}
    and {k: cm2[k] for k in cm2 if k not in ("platform", "python", "git_head")}
    == {k: m2[k] for k in m2 if k not in ("platform", "python", "git_head")},
    "same_git_head_everywhere": len(
        {m1["git_head"], m2["git_head"], cm1["git_head"], cm2["git_head"]}
    )
    == 1,
}
report = {
    "sizes": {
        "full": len(full),
        "s1": len(s1),
        "s2": len(s2),
        "c1": len(c1),
        "c2": len(c2),
        "s1|s2": len(set(s1) | set(s2)),
        "s1&s2": len(set(s1) & set(s2)),
    },
    "outcomes": {
        "full": counts(full),
        "s1": counts(s1),
        "s2": counts(s2),
        "c1": counts(c1),
        "c2": counts(c2),
    },
    "red": {"full": reds(full), "s1": reds(s1), "s2": reds(s2), "c1": reds(c1), "c2": reds(c2)},
    "missing_from_shards": sorted(f"{c}::{n}" for c, n in set(full) - (set(s1) | set(s2)))[:20],
    "extra_in_shards": sorted(f"{c}::{n}" for c, n in (set(s1) | set(s2)) - set(full))[:20],
    "files": {
        "m1_selected": len(m1["selected_files"]),
        "s1_junit": len(files_of(s1, root)),
        "m2_selected": len(m2["selected_files"]),
        "s2_junit": len(files_of(s2, root)),
        "m1_minus_s1": sorted(set(m1["selected_files"]) - files_of(s1, root)),
        "s1_minus_m1": sorted(files_of(s1, root) - set(m1["selected_files"])),
        "m2_minus_s2": sorted(set(m2["selected_files"]) - files_of(s2, root)),
        "s2_minus_m2": sorted(files_of(s2, root) - set(m2["selected_files"])),
    },
    "manifest": {
        "git_head": m1["git_head"],
        "nodeids_total": m1["nodeids_total"],
        "files_total": m1["files_total"],
        "per_shard_estimated_seconds": m1["per_shard_estimated_seconds"],
        "per_shard_files": m1["per_shard_files"],
        "per_shard_nodeids": m1["per_shard_nodeids"],
        "default_weight": m1["default_weight"],
        "unknown_files": len(m1["unknown_files"]),
    },
    "checks": checks,
    "verdict": "OK" if all(checks.values()) else "FAIL",
}
text = json.dumps(report, ensure_ascii=False, indent=1) + "\n"
out_p.write_text(text, encoding="utf-8")
print(text)
sys.exit(0 if report["verdict"] == "OK" else 1)
