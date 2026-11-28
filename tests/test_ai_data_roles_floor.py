"""规则臂（不连 AI）的**下限**：这些用例规则已经答对，以后不许由对变错（ADR 0178 修订 2026-11-27）。

`test_ai_data_roles.py` 里「规则多半会错」的几条量的是用例集的区分度（规则变强它们就该下调），抓不住规则退化；抓退化的是这里：
逐例点名，谁由对变错谁红。规则以后又多答对了，就把那条的编号加进来——这张名单只增不减。答案与判分用的是同一份（`roles_cases.json`、`data_figure_roles.default_hits`）。
"""

from __future__ import annotations

import sys
from pathlib import Path

SUPPORT = Path(__file__).parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))

import data_figure_roles as DR  # noqa: E402

#: 2026-11-27：52 例里规则全对的 33 例
RULES_RIGHT = frozenset(
    """HO-01 HO-02 HO-03 HO-04 HO-05 HO-06 HO-07 HO-08 HO-09 HO-10 HO-11 HO-12 HO-13 HO-14 HO-15 HO-18 HO-19 HO-21 HO-23 HO-29
    RC-05 RC-08 RC-09 RC-10 RC-11 RC-12 RC-14 RC-15 RC-16 RC-19 RC-20 RC-21 RC-22""".split()
)


def test_rules_never_lose_a_case_they_already_get_right(tmp_path):
    cases = {c["id"]: c for c in DR.load_case_set("all")}
    assert RULES_RIGHT <= set(cases)
    lost = []
    for i, cid in enumerate(sorted(RULES_RIGHT)):
        run = DR.run_case(cases[cid], tmp_path / f"c{i}", conn=None)
        if not DR.default_hits(cases[cid], run.rules)["all"]:
            lost.append(cid)
    assert lost == []
