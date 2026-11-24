"""第 2 步用例集（`recommend_cases.json`）格式良好：闭集取值合法、准则 id 存在且 `forbidden` 只引 firm / strong、最小对结构相同、三档理由合 G-039。
只校验形状，**不**断言 `expressible`（事后量的，下一个实现功能的代理会让它变）。不联网、不调模型。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "support"))

import data_figure_recommend as RC  # noqa: E402

from tavotto.engine import ai_data_recipe as recipe_mod  # noqa: E402

CASES = RC.load_recommend_cases()


def test_the_set_has_the_promised_shape():
    tiers = [c["tier"] for c in CASES]
    assert len(CASES) == 41
    assert tiers.count("pair") >= 24 and tiers.count("plain") >= 8 and tiers.count("hard") >= 9
    assert len({c["pair"]["id"] for c in CASES if c["tier"] == "pair"}) >= 12
    assert {c["lang"] for c in CASES} == {"zh", "en", "mixed"}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_each_case_is_well_formed(case, tmp_path):
    RC.check_case_shape(case, tmp_path)


def test_pairs_have_identical_structure_and_different_answers(tmp_path):
    RC.check_pairs(CASES, tmp_path)


def test_coded_methods_are_exactly_the_default_candidate_codes():
    assert set(RC.methods_by_status("coded")) == set(recipe_mod.CANDIDATE_CODES)


def test_method_families_and_statuses_are_consistent():
    for method, (status, family, _title) in RC.METHODS.items():
        assert status in RC.COMMON_STATUSES, method
        assert family in recipe_mod.FAMILIES, method
        if status in ("coded", "legal_uncoded", "missing"):
            continue
        assert not recipe_mod.renderable(family), method  # no_template = 族有规则、没有模板
    for method in RC.methods_by_status("coded") + RC.methods_by_status("legal_uncoded"):
        assert recipe_mod.renderable(RC.METHODS[method][1]), method


def test_guidance_table_covers_the_54_entries():
    assert len(RC.GUIDANCE_STRENGTH) == 54
    assert set(RC.GUIDANCE_STRENGTH.values()) <= {"firm", "strong", "preference", "inference"}


def test_classify_maps_default_candidates_back_to_their_codes(tmp_path):
    """每个 `default_candidates` 实际给出的候选，归到的画法 id 与它的 `title_code` 一致（`classify` 不靠字符串，但对这 14 个码必须对得上）。"""
    for case in CASES:
        run = RC.rules_run(case, tmp_path / case["id"])
        for method, title in zip(run["methods"], run["titles"], strict=True):
            assert method == title, (case["id"], method, title)


def test_ideal_column_roles_remove_every_recommended_forbidden_trend_line(tmp_path):
    """G-018（第 2 步之一）：把用例标准答案里每列的角色当「理想模型」回放（真的 `interpret`，不调模型）——被标推荐的 forbidden 折线类降到 0，且只动这几条。
    规则臂（没有模型）不变：这四条仍是折线类排第一（规则单凭数据事实分不出有序横轴与独立测量值）。"""
    lines = {"line_points", "line_band", "line_points_logx", "line_band_logx", "line_units"}
    g018 = ("RC-P03B", "RC-P04B", "RC-N03", "RC-H01")
    changed = []
    for case in CASES:
        base = RC.rules_run(case, tmp_path / "a" / case["id"])
        ideal = RC.rules_run(case, tmp_path / "b" / case["id"], ideal_roles=True)
        forb = {f["method"] for f in case.get("forbidden", [])}
        if ideal["recommended"] in forb:
            assert ideal["recommended"] not in lines, case["id"]
        if base["methods"] != ideal["methods"]:
            changed.append(case["id"])
    assert changed == list(g018) or sorted(changed) == sorted(g018), changed
    for cid in g018:
        case = next(c for c in CASES if c["id"] == cid)
        assert RC.rules_run(case, tmp_path / "c" / cid)["recommended"] in lines  # 规则臂不变
        ideal = RC.rules_run(case, tmp_path / "d" / cid, ideal_roles=True)
        assert ideal["recommended"] == "scatter_fit" and not set(ideal["methods"]) & lines


def test_tally_survives_a_filtered_run_with_only_one_side_of_a_pair():
    """`--only RC-P03B` 这样只跑一对里的一边：成对的统计只算两边都在的，不崩；那一条仍计入逐条合计（Codex 评审 #2159）。"""
    one = [c for c in CASES if c["id"] == "RC-P03B"]
    assert len(one) == 1 and one[0]["tier"] == "pair"
    t = RC.tally(RC.run_all(one))
    assert t["cases"] == 1
    whole = RC.tally(RC.run_all([c for c in CASES if c["id"] in ("RC-P03A", "RC-P03B")]))
    assert whole["cases"] == 2
