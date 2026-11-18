#!/usr/bin/env python3
"""拖动能力普查的命令行壳——判据本身是 `scripts/ci/compat_drag.py`（CompatBench 的「拖动」阶段）。

回答一个问题：图上有哪些元素**位置不是数据**、却**不宣称可拖**也**说不出为什么**。

判据按**行为**，不读产品的判定函数（`manifest._layout_anchored_*`）：对每个子图分别把 x、y 范围
平移四分之一个跨度，看元素相对它所在子图框的位置变没变。

* 两次都没动 = 位置不随数据（排版坐标）。它要么 `draggable`、要么由外框代拖（`drag_host`）、
  要么有端点手柄（`arrow_endpoints`）、要么在 `unsupported_props` 里给了 `pos_frac` 的理由；
  都不是 → 报出来。
* 位置量的是画出来的几何（`geometry` 的外接框），没有几何才退回 `bbox`；相对子图框量，
  等比子图（imshow、地图）换范围时框本身会动。
* 整张图上的元素（`fig.*`）不属于任何子图，没有数据坐标：同一组角色直接要求同一件事。
* 每次扰动之后撤销（空列表），核对元素表逐位回到基线，回不去的 case 单独报。

用法（科学栈解释器；默认扫 `tests/compat/manifest.json` 全部 case）：

    PYTHONPATH=src python scripts/dev/drag_oracle.py               # 全部
    PYTHONPATH=src python scripts/dev/drag_oracle.py --case art_shapes --case sci_map
    PYTHONPATH=src python scripts/dev/drag_oracle.py --script fig.py --entry main
    ... --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ci"))
#: 被普查的引擎：默认本仓库；`DRAG_ORACLE_SRC` 指到另一份 src（同一把尺量改动前后）
sys.path.insert(0, os.environ.get("DRAG_ORACLE_SRC") or str(REPO / "src"))

import compat_drag  # noqa: E402 — 判据只有 scripts/ci/compat_drag.py 一份，这里只是命令行壳


def audit_stem(worker, stem: str) -> dict:
    return compat_drag.audit(worker, stem)


def _run(project: Path, script_rel: str, entry: str, stems: list[str] | None, run=None) -> dict:
    from tavotto.engine import pool

    w = pool.one_shot(script_rel, str(project), entry, run=run)
    try:
        built = w.ensure_built()
        out = {}
        for stem in stems or sorted(built.get("stems") or {}):
            try:
                out[stem] = audit_stem(w, stem)
            except Exception as exc:  # noqa: BLE001
                out[stem] = {"error": str(exc)[:300]}
        return out
    finally:
        pool.discard(w)


def audit_cases(case_ids: list[str] | None) -> dict:
    import compat_corpus as CC
    import compat_matrix as CM

    cases = CC.load_manifest()["cases"]
    if case_ids:
        cases = [c for c in cases if c["id"] in set(case_ids)]
    report = {}
    for group in CC.group_by_project(cases):
        lead = group[0]
        with tempfile.TemporaryDirectory(prefix="drag-oracle-") as tmp:
            try:
                project, script_rel, entry = CM.materialize(group, Path(tmp))
                context = CM.resolve_launch_context(project, script_rel, lead)
                run = CM.registered_launch(project, script_rel, lead, context)
                res = _run(project, script_rel, entry, [c["stem"] for c in group], run=run)
            except Exception as exc:  # noqa: BLE001
                for c in group:
                    report[c["id"]] = {"error": str(exc)[:300]}
                print(f"[drag-oracle] {lead['id']}: error {str(exc)[:120]}", file=sys.stderr)
                continue
        for c in group:
            r = res.get(c["stem"], {"error": "stem not built"})
            report[c["id"]] = r
            n = len(r.get("findings") or [])
            print(f"[drag-oracle] {c['id']}: findings={n}", file=sys.stderr)
    return report


def _utf8_streams() -> None:
    """输出里有中文（元素名、错误说明）：Windows 上被重定向时按 UTF-8 写，画不出的字符换成 `?`，
    不让一个诊断工具自己崩掉（同 `matplotlib_artist_census.py`）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def main(argv=None) -> int:
    _utf8_streams()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--case", action="append", help="只扫这些 corpus case（可重复）")
    ap.add_argument("--script", help="直接扫一份脚本（不走 corpus）")
    ap.add_argument("--entry", default="main")
    ap.add_argument("--json", help="结果写到这个文件")
    args = ap.parse_args(argv)
    if args.script:
        p = Path(args.script).resolve()
        report = {p.name: _run(p.parent, p.name, args.entry, None)}
    else:
        report = audit_cases(args.case)
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    total = 0
    for cid, r in report.items():
        stems = r.values() if args.script else [r]
        for s in stems:
            for f in s.get("findings") or []:
                total += 1
                print(f"{cid}\t{f['gid']}\t{f['role']}\t{f.get('label')}")
            for g in s.get("restore_errors") or []:
                print(f"{cid}\tRESTORE\t{g}")
    print(f"[drag-oracle] findings total={total}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
