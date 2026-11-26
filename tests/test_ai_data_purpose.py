# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""看图目的（`answers.purpose`）：按目的重排规则已给出的备选 + 非推荐备选的结构化「为什么不是它」（ADR 0178 修订 2026-11-25）。

判据的主语：**候选的集合与顺序、响应里除新增键以外的一切**——
* 重排是纯排列：不新增、不复活被规则拿掉的备选（41 条考卷，带任一目的与不带时画法集合相同）；没有 `answers.purpose` 时顺序与从前逐项相同；
* 原因码是闭集，每个码引用的 G 编号在 `sciviz_guidance` 里存在且不是品味条目；`answers_other_purpose` 只在用户点的目的下才出；
* `purpose` 进 `_ANSWER_KEYS`、不进 `_user_took_over`、进幂等键；合画成对的两张重排后仍相邻、`variants` 标记不丢。
"""

from __future__ import annotations

import itertools
import socket
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))

import data_figure_recommend as RC  # noqa: E402

from tavotto.engine import (  # noqa: E402
    ai_data_propose as P,
    ai_data_purpose as PU,
    ai_data_recipe as R,
    sciviz_guidance as G,
)
from tavotto.engine.agent_runner import wires  # noqa: E402
from tests.test_ai_data_merge import _arms  # noqa: E402
from tests.test_ai_data_propose import project, propose, put  # noqa: E402, F401

CASES = RC.load_recommend_cases()
BY_ID = {c["id"]: c for c in CASES}

#: 没有目的时规则第一名本来就是 `forbidden` 的用例：它们的任务（`agreement` / `paired_change`）不在界面让选的四个里、
#: 目标画法（`scatter_identity` / `paired_lines`）还没有模板，这一期重排救不了。带目的的臂不许让任何别的用例新增违规。
KNOWN_FORBIDDEN_TOP = {"RC-P03B", "RC-H01"}
#: 没有目的时第一名是 `forbidden`，但它的任务在四个里、点对目的就修好了：只要求「点对目的后不再违规」，别的目的下不比从前差
FIXED_BY_ITS_PURPOSE = {"RC-P04B": "relationship"}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("network touched")

    monkeypatch.setattr(wires, "post_json", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setattr(P, "CANDIDATE_ROOT", tmp_path / "propose")


def cand(code: str, family: str) -> R.Candidate:
    return R.Candidate(code, SimpleNamespace(family=family))  # type: ignore[arg-type]


FAMILY = {
    **{c: "trend" for c in ("line_units", "line_points", "line_band", "line_err")},
    **{c: "relationship" for c in ("scatter_fit", "scatter")},
    **{c: "comparison" for c in ("points_mean", "box_points", "violin_points", "bar_error")},
    "points_err": "comparison",
    "ecdf_groups": "distribution",
}


def mk(*codes: str) -> list[R.Candidate]:
    return [cand(c, FAMILY[c]) for c in codes]


# ---------------------------------------------------------------- 纯函数
def test_reorder_is_a_stable_permutation_for_every_subset_and_purpose():
    """任意规则给出的画法组合（至多 4 个）、任一目的：结果是同一批对象的排列，且 `bar_error` 不会跑到含原始点的比较之前。"""
    codes = list(FAMILY)
    for k in range(1, 5):
        for combo in itertools.combinations(codes, k):
            items = mk(*combo)
            for p in PU.PURPOSES:
                out = PU.reorder(items, p)
                assert sorted(map(id, out)) == sorted(map(id, items)), (combo, p)
                if "bar_error" in combo and "points_mean" in combo:
                    ci = [c.code for c in out]
                    before = [c.code for c in items]
                    # 相对顺序只在「被提到前面」时才变：bar_error 与 points_mean 的相对先后不变
                    assert (ci.index("bar_error") > ci.index("points_mean")) == (
                        before.index("bar_error") > before.index("points_mean")
                    ), (combo, p)


def test_no_purpose_leaves_the_order_untouched_whatever_the_first_family_is():
    for combo in itertools.permutations(
        ["line_points", "scatter_fit", "points_mean", "box_points", "ecdf_groups", "bar_error"], 4
    ):
        items = mk(*combo)
        got = PU.resolve(items, None)
        assert got.items == tuple(items) and got.source == "data"
        assert got.code == FAMILY_TO_PURPOSE[FAMILY[combo[0]]]


FAMILY_TO_PURPOSE = PU.FAMILY_PURPOSE


def test_options_only_list_purposes_some_candidate_answers_and_hide_a_single_row():
    assert PU.options(mk("points_mean", "box_points", "bar_error")) == ()
    assert PU.options(mk("line_points", "scatter_fit")) == ("trend", "relationship")
    assert PU.options(mk("line_points", "scatter_fit", "points_mean", "ecdf_groups")) == PU.PURPOSES
    assert PU.options([]) == ()


def test_distribution_only_lifts_box_and_violin_when_the_rules_kept_them():
    few = mk("points_mean", "ecdf_groups", "bar_error")  # 每组点太少：规则已拿掉箱线（P02A 的形状）
    assert PU.reorder(few, "distribution") == tuple(few)
    enough = mk("points_mean", "box_points", "ecdf_groups", "bar_error")
    assert [c.code for c in PU.reorder(enough, "distribution")][0] == "box_points"


def test_a_requested_purpose_nothing_can_answer_is_ignored():
    items = mk("line_points", "scatter_fit")
    got = PU.resolve(items, "distribution")
    assert (got.code, got.source, got.items) == ("trend", "data", tuple(items))
    assert PU.resolve(items, "paired_change").source == "data"  # 闭集外：当没给


def test_every_reason_code_cites_existing_non_taste_guidance():
    assert set(PU.WHY_NOT) == {
        "summary_hides_spread",
        "line_needs_order",
        "order_prefers_line",
        "answers_other_purpose",
    }
    for code, (ids, tier) in PU.WHY_NOT.items():
        assert tier in ("data_fact", "perceptual", "purpose"), code
        assert (tier == "purpose") == (not ids), code
        for gid in ids:
            e = G.entry(gid)  # 不存在 → KeyError
            assert e["basis"] != "taste", (code, gid)


def test_why_not_only_gives_the_user_purpose_reason_to_a_confirmed_purpose():
    # 有 G 依据的三类：假设的目的也给
    for src in ("data", "user"):
        assert PU.why_not("bar_error", "compare_groups", src)["code"] == "summary_hides_spread"
        assert PU.why_not("bar_error", "distribution", src)["code"] == "summary_hides_spread"
        assert PU.why_not("line_band", "relationship", src)["code"] == "line_needs_order"
        assert PU.why_not("scatter", "trend", src)["code"] == "order_prefers_line"
    assert PU.why_not("ecdf_groups", "compare_groups", "data") is None
    got = PU.why_not("ecdf_groups", "compare_groups", "user")
    assert got == {"code": "answers_other_purpose", "guidance": [], "tier": "purpose"}
    # 这个目的也回答的画法不给「回答的是另一个问题」
    assert PU.why_not("points_mean", "distribution", "user") is None
    assert PU.why_not("box_points", "compare_groups", "user") is None
    assert PU.why_not("line_band", "trend", "user") is None
    assert PU.why_not("points_mean", None, "user") is None


def test_pairs_stay_adjacent_and_the_objects_are_moved_not_rebuilt():
    """合画成对的两张（同一个码、相邻）重排后仍相邻；`Candidate` 原样搬动，所以按 `id(recipe)` 记的标记不会丢。"""
    a, a2 = cand("line_points", "trend"), cand("line_points", "trend")
    s = cand("scatter_fit", "relationship")
    m, m2 = cand("points_mean", "comparison"), cand("points_mean", "comparison")
    items = [a, a2, s, m, m2]
    for p in PU.PURPOSES:
        out = PU.reorder(items, p)
        for x, y in ((a, a2), (m, m2)):
            pos = {id(o): k for k, o in enumerate(out)}  # 两张成对的相等（dataclass），按对象身份找
            assert pos[id(y)] == pos[id(x)] + 1, p
        assert all(any(o is i for o in out) for i in items)


# ---------------------------------------------------------------- 请求层
def test_purpose_is_an_answer_key_but_not_a_takeover():
    assert "purpose" in P._ANSWER_KEYS
    ans = P.parse_answers({"purpose": "relationship"})
    assert ans == {"purpose": "relationship"}
    assert not P._user_took_over(ans)
    assert P.parse_answers({"purpose": "paired_change"}) == {}  # 闭集外的值丢弃
    with pytest.raises(P.ProposeError):
        P.parse_answers({"purpose": 3})


def _propose(case_root: Path, case: dict, **extra) -> dict:
    RC.materialize(case, case_root)
    return P.propose(
        case_root,
        project=RC.PROJECT,
        folder=case["id"],
        hint=case.get("hint"),
        render="lazy",
        answers={**RC.answers_of(case), **extra.pop("answers", {})},
        **extra,
    )


def test_purpose_is_part_of_the_idempotency_key(tmp_path):
    case = BY_ID["RC-N05"]
    first = _propose(tmp_path, case, client_request_id="r1")
    assert _propose(tmp_path, case, client_request_id="r1") is first
    other = _propose(tmp_path, case, client_request_id="r1", answers={"purpose": "relationship"})
    assert other is not first
    assert other["purpose"]["source"] == "user" and first["purpose"]["source"] == "data"


def test_the_response_says_which_purpose_and_who_picked_it(tmp_path):
    body = _propose(tmp_path, BY_ID["RC-N05"])
    assert body["purpose"] == {
        "code": "trend",
        "source": "data",
        "options": ["compare_groups", "trend", "relationship"],
    }
    body = _propose(tmp_path, BY_ID["RC-N05"], answers={"purpose": "compare_groups"})
    assert body["purpose"]["code"] == "compare_groups" and body["purpose"]["source"] == "user"
    assert body["items"][0]["recommended"] and "why_not" not in body["items"][0]
    assert {it["why_not"]["code"] for it in body["items"][1:] if "why_not" in it} == {
        "answers_other_purpose"
    }


def test_a_family_squeezed_out_by_the_four_card_cap_is_not_offered(tmp_path):
    """已知限制：规则先截到 4 张再重排——被截掉的族（RC-N05 里的「看分布」）不出现在可选项里，点它也不会让它回来。"""
    base = _propose(tmp_path, BY_ID["RC-N05"])
    assert "distribution" not in base["purpose"]["options"]
    forced = _propose(tmp_path, BY_ID["RC-N05"], answers={"purpose": "distribution"})
    assert forced["purpose"]["source"] == "data"
    assert [i["title_code"] for i in forced["items"]] == [i["title_code"] for i in base["items"]]


def test_a_single_family_table_shows_no_purpose_row(tmp_path):
    body = _propose(tmp_path, BY_ID["RC-N06"])  # 只有分布类
    assert body["purpose"]["options"] == []


def test_a_summarised_table_has_one_candidate_and_no_why_not(tmp_path):
    body = _propose(tmp_path, BY_ID["RC-P06A"])
    assert body["purpose"]["options"] == [] and not any("why_not" in i for i in body["items"])


def test_merged_pairs_keep_their_variant_marks_after_the_reorder(project):
    m, client, figs, _ = project
    rows = _arms(lambda i, k: "p" if k % 2 == 0 else "q")
    for i, n in enumerate(("a", "b")):
        put(figs, f"data/{n}.csv", rows(i))
    tabs = [{"path": "a.csv"}, {"path": "b.csv"}]

    def run(**extra):
        body = propose(
            client, tables=tabs, render="lazy", answers={"y": "resp_mV", **extra}
        ).get_json()
        assert body["kind"] == "candidates", body
        return body["items"]

    base = run()
    for p in PU.PURPOSES:
        items = run(purpose=p)
        assert sorted(i["title_code"] for i in items) == sorted(i["title_code"] for i in base)
        kinds = [(i["title_code"], (i["variant"] or {}).get("kind")) for i in items]
        assert sorted(map(str, kinds)) == sorted(
            map(str, [(i["title_code"], (i["variant"] or {}).get("kind")) for i in base])
        ), p
        for k, it in enumerate(items):
            if (it["variant"] or {}).get("kind") == "split":
                assert items[k + 1]["variant"] == {"kind": "by_file"}
                assert items[k + 1]["title_code"] == it["title_code"]


# ---------------------------------------------------------------- 41 条考卷
@pytest.fixture(scope="module")
def sweep():
    """每条用例：不带目的 / 带四个目的各跑一遍（懒模式，不调模型）。"""
    out: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="purpose-sweep-") as tmp:
        root = Path(tmp)
        for c in CASES:
            cid = c["id"]
            out[cid] = {
                None: RC.rules_run(c, root / cid / "none", raw=True),
                **{p: RC.rules_run(c, root / cid / p, raw=True, purpose=p) for p in PU.PURPOSES},
            }
    return out


@pytest.mark.parametrize("cid", [c["id"] for c in CASES])
def test_any_purpose_gives_the_same_candidate_set(sweep, cid):
    runs = sweep[cid]
    base = runs[None]
    for p in PU.PURPOSES:
        assert sorted(map(str, runs[p]["methods"])) == sorted(map(str, base["methods"])), (cid, p)
        assert runs[p]["kind"] == base["kind"]


@pytest.mark.parametrize("cid", [c["id"] for c in CASES])
def test_without_a_purpose_the_response_is_the_old_one_plus_the_new_keys(sweep, cid, monkeypatch):
    """对拍：把 `resolve` 换成「恒等、不加键」的旧行为，响应剥掉新键（`purpose` / `why_not`）与随机的 id 后逐项相同。"""
    case = BY_ID[cid]
    new = sweep[cid][None]["raw_items"]
    monkeypatch.setattr(
        PU,
        "resolve",
        lambda items, req: PU.Resolved(None, "data", (), tuple(items)),
    )
    with tempfile.TemporaryDirectory(prefix="purpose-old-") as tmp:
        old = RC.rules_run(case, Path(tmp), raw=True)["raw_items"]

    def strip(it):
        return {k: v for k, v in it.items() if k not in ("why_not", "id", "thumbnail_url")}

    assert [strip(i) for i in new] == [strip(i) for i in old]
    assert [i["recommended"] for i in new] == [i["recommended"] for i in old]


def test_no_purpose_is_always_a_data_hypothesis_and_names_the_first_candidate_family(sweep):
    for cid, runs in sweep.items():
        base = runs[None]
        if base["kind"] != "candidates":
            continue
        assert base["purpose"]["source"] == "data", cid
        first = base["raw_items"][0]["values"]["family"]
        assert base["purpose"]["code"] == PU.FAMILY_PURPOSE.get(first), cid


def test_forbidden_methods_never_become_the_recommendation_through_a_purpose(sweep):
    """带目的的臂不会让任何用例新增 `forbidden_recommended`；没有目的时本来就违规的两条是有名字的已知例外，且两边保持一致。"""
    base_bad = {
        cid
        for cid, runs in sweep.items()
        if runs[None]["recommended"] in {f["method"] for f in BY_ID[cid].get("forbidden", [])}
    }
    assert base_bad == KNOWN_FORBIDDEN_TOP | set(FIXED_BY_ITS_PURPOSE)
    for cid, runs in sweep.items():
        forb = {f["method"] for f in BY_ID[cid].get("forbidden", [])}
        for p in PU.PURPOSES:
            if cid in KNOWN_FORBIDDEN_TOP | set(FIXED_BY_ITS_PURPOSE):
                # 例外：不许比从前差（要么推荐没动，要么不再是违规画法）
                assert (
                    runs[p]["recommended"] == runs[None]["recommended"]
                    or runs[p]["recommended"] not in forb
                ), (cid, p)
            else:
                assert runs[p]["recommended"] not in forb, (cid, p)
    # 它自己的目的点对了就修好
    for cid, p in FIXED_BY_ITS_PURPOSE.items():
        forb = {f["method"] for f in BY_ID[cid]["forbidden"]}
        assert sweep[cid][p]["recommended"] not in forb


def test_the_purpose_arm_raises_the_rules_baseline_without_losing_any_case(tmp_path):
    """回放等于「用户点了对的目的」：用例标准答案里的 `task`（只取界面让选的四个）当 `answers.purpose`。
    `acceptable_top` / `forbidden` 一个字不改；只比 `rules_top_ok`，不碰 `expressible`。"""
    plain = RC.run_all(CASES)
    arm = RC.run_all(CASES, purpose_arm=True)
    ok_plain = {c["id"] for c, m in plain if m["rules_top_ok"]}
    ok_arm = {c["id"] for c, m in arm if m["rules_top_ok"]}
    assert ok_plain <= ok_arm, sorted(ok_plain - ok_arm)
    assert ok_arm - ok_plain == {"RC-P02B", "RC-P04B", "RC-N03"}
    assert (len(ok_plain), len(ok_arm)) == (28, 31)
    assert sum(1 for c in CASES if RC.purpose_of(c)) == 30
