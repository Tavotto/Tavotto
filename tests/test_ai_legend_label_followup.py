"""改图任务「标题与图例同一个字符串」的跟进建议（纯逻辑层）：label 比对 helper、图例可见判据、`agent_check` 记发现、收尾只在
「改回原文 + 图例看得见 + 成功」时才给建议。真 worker 的整条链见 `test_ai_edit_check_app.py`。"""

from __future__ import annotations

import pytest

from tavotto.engine import ai_bridge, ai_convert, ai_figure_edit


def _man(
    lines=(),
    legend_texts=None,
    legend_visible=True,
    text_visible=True,
    title=None,
    source="axes_0.lines_0",
):
    """合成 manifest：lines = [(gid, label)]；legend_texts = [text, …]（None = 没有图例）。"""
    els = [{"gid": "figure", "role": "figure", "editable": []}]
    for gid, label in lines:
        els.append({"gid": gid, "role": "line", "editable": [{"prop": "label", "value": label}]})
    if title is not None:
        els.append(
            {"gid": "axes_0.title", "role": "title", "editable": [{"prop": "text", "value": title}]}
        )
    if legend_texts is not None:
        els.append(
            {
                "gid": "axes_0.legend",
                "role": "legend",
                "editable": [{"prop": "visible", "value": legend_visible}],
            }
        )
        for j, t in enumerate(legend_texts):
            els.append(
                {
                    "gid": f"axes_0.legend.texts_{j}",
                    "role": "legend_text",
                    "legend_entry": {"index": j, "source_gid": source},
                    "editable": [
                        {"prop": "text", "value": t},
                        {"prop": "visible", "value": text_visible},
                    ],
                }
            )
    return {"elements": els}


def test_label_changes_reports_stem_gid_from_to():
    before = {"plot": _man([("axes_0.lines_0", "B"), ("axes_1.lines_0", "ctrl")])}
    after = {"plot": _man([("axes_0.lines_0", "B: treated"), ("axes_1.lines_0", "ctrl")])}
    assert ai_figure_edit.label_changes(before, after) == [
        {"stem": "plot", "gid": "axes_0.lines_0", "from": "B", "to": "B: treated"}
    ]


def test_label_changes_unchanged_and_missing_sides_are_ignored():
    same = {"plot": _man([("axes_0.lines_0", "B")])}
    assert ai_figure_edit.label_changes(same, same) == []
    assert (
        ai_figure_edit.label_changes({"plot": None}, {"plot": _man([("axes_0.lines_0", "x")])})
        == []
    )
    # 只在一侧出现的 stem / gid 不算改动
    assert ai_figure_edit.label_changes({"a": same["plot"]}, {"b": same["plot"]}) == []
    assert ai_figure_edit.label_changes(same, {"plot": _man([("axes_0.lines_9", "z")])}) == []


def test_label_changes_covers_every_stem_independently():
    before = {"a": _man([("axes_0.lines_0", "x")]), "b": _man([("axes_0.lines_0", "y")])}
    after = {"a": _man([("axes_0.lines_0", "x2")]), "b": _man([("axes_0.lines_0", "y2")])}
    got = ai_figure_edit.label_changes(before, after)
    assert [(c["stem"], c["from"], c["to"]) for c in got] == [("a", "x", "x2"), ("b", "y", "y2")]


def test_visibility_needs_a_shown_legend_entry_with_that_text():
    assert ai_figure_edit.label_visible_in_legend(
        _man(legend_texts=["B", "ctrl"]), "axes_0.lines_0", "B"
    )
    assert not ai_figure_edit.label_visible_in_legend(
        _man(legend_texts=None), "axes_0.lines_0", "B"
    )  # 没有图例
    assert not ai_figure_edit.label_visible_in_legend(
        _man(legend_texts=["ctrl"]), "axes_0.lines_0", "B"
    )
    assert not ai_figure_edit.label_visible_in_legend(
        _man(legend_texts=["B"], legend_visible=False), "axes_0.lines_0", "B"
    )
    assert not ai_figure_edit.label_visible_in_legend(
        _man(legend_texts=["B"], text_visible=False), "axes_0.lines_0", "B"
    )
    assert not ai_figure_edit.label_visible_in_legend(None, "axes_0.lines_0", "B")


def test_another_lines_legend_entry_with_the_same_text_does_not_count():
    """图例项绑定的是别的曲线：同名文字不算这条曲线画在图上。"""
    other = _man(legend_texts=["B"], source="axes_0.lines_1")
    assert not ai_figure_edit.label_visible_in_legend(other, "axes_0.lines_0", "B")
    assert ai_figure_edit.label_visible_in_legend(other, "axes_0.lines_1", "B")


def test_no_suggestion_when_only_another_lines_entry_shows_the_text(cand):
    base = _man([("axes_0.lines_0", "B")], legend_texts=["B"], source="axes_0.lines_1")
    _check(cand, {"plot": _man([("axes_0.lines_0", "C")])}, {"plot": base})
    assert cand.extras["label_findings"][("plot", "axes_0.lines_0")]["visible"] is False
    ai_figure_edit.record_final_labels(cand, {"plot": base})
    cand.committed = True
    assert ai_figure_edit.legend_label_suggestions(cand, "completed") == []


def test_a_title_with_the_same_text_is_not_a_legend_entry():
    assert not ai_figure_edit.label_visible_in_legend(_man(title="B"), "axes_0.lines_0", "B")


# ------------------------------------------------------------------ agent_check 记发现
BASE = _man([("axes_0.lines_0", "B")], legend_texts=["B"], title="B")
EDITED = _man([("axes_0.lines_0", "B: treated")], legend_texts=["B: treated"], title="B: treated")


def _cand(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    src = proj / "plot.py"
    src.write_text("x = 1\n", encoding="utf-8")
    cand = ai_figure_edit.create_candidate("lf1", src, proj, "plot.py")
    return cand


@pytest.fixture
def cand(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_figure_edit, "CANDIDATE_DIR", tmp_path / "cand")
    c = _cand(tmp_path)
    yield c
    ai_figure_edit.remove_candidate(c.dir)


def _check(cand, manifests, baseline):
    cand.target.write_text("x = 2\n", encoding="utf-8")
    cand.extras["runner"] = object()
    cand.extras["check_before"] = lambda: baseline
    return ai_figure_edit.agent_check(
        cand, lambda c, r: {"status": "ok", "stems": list(manifests), "manifests": manifests}
    )


def test_agent_check_records_a_label_change(cand):
    ok, _ = _check(cand, {"plot": EDITED}, {"plot": BASE})
    f = cand.extras["label_findings"][("plot", "axes_0.lines_0")]
    assert (f["from"], f["to"], f["visible"]) == ("B", "B: treated", True)
    assert isinstance(ok, bool)  # 记录与数据检查的结论互不依赖


def test_agent_check_without_a_label_change_records_nothing(cand):
    _check(cand, {"plot": BASE}, {"plot": BASE})
    assert not cand.extras.get("label_findings")


def test_agent_check_keeps_the_first_from_and_the_latest_to(cand):
    _check(cand, {"plot": EDITED}, {"plot": BASE})
    later = _man([("axes_0.lines_0", "B2")], legend_texts=["B2"])
    _check(cand, {"plot": later}, {"plot": BASE})
    f = cand.extras["label_findings"][("plot", "axes_0.lines_0")]
    assert (f["from"], f["to"]) == ("B", "B2")


def test_figure_without_a_legend_records_the_change_but_not_as_visible(cand):
    base = _man([("axes_0.lines_0", "B")])
    _check(cand, {"plot": _man([("axes_0.lines_0", "C")])}, {"plot": base})
    assert cand.extras["label_findings"][("plot", "axes_0.lines_0")]["visible"] is False


# ------------------------------------------------------------------ 收尾只在「改回原文 + 图例可见 + 成功」时给
def _settled(cand, final_manifests, outcome="completed", committed=True):
    _check(cand, {"plot": EDITED}, {"plot": BASE})
    ai_figure_edit.record_final_labels(cand, final_manifests)
    cand.committed = committed
    return ai_figure_edit.legend_label_suggestions(cand, outcome)


def test_suggestion_when_label_reverted_visible_and_completed(cand):
    final = {"plot": _man([("axes_0.lines_0", "B")], legend_texts=["B"], title="B: treated")}
    assert _settled(cand, final) == [
        {
            "kind": "legend_label",
            "stem": "plot",
            "gid": "axes_0.lines_0",
            "legend_gid": "axes_0.legend.texts_0",
            "from": "B",
            "to": "B: treated",
        }
    ]


def test_no_suggestion_when_the_final_label_was_really_changed(cand):
    assert _settled(cand, {"plot": EDITED}) == []


def test_no_suggestion_when_not_completed_or_not_committed(cand):
    final = {"plot": BASE}
    assert _settled(cand, final, outcome="partial") == []
    assert ai_figure_edit.legend_label_suggestions(cand, "failed") == []
    assert _settled(cand, final, committed=False) == []


def test_no_suggestion_when_the_final_render_was_not_measured(cand):
    _check(cand, {"plot": EDITED}, {"plot": BASE})
    cand.committed = True
    assert ai_figure_edit.legend_label_suggestions(cand, "completed") == []  # 没有最终值 = 不知道


def test_no_suggestion_when_no_legend_shows_the_text(cand):
    _check(
        cand, {"plot": _man([("axes_0.lines_0", "C")])}, {"plot": _man([("axes_0.lines_0", "B")])}
    )
    ai_figure_edit.record_final_labels(cand, {"plot": _man([("axes_0.lines_0", "B")])})
    cand.committed = True
    assert ai_figure_edit.legend_label_suggestions(cand, "completed") == []


def test_settle_task_exposes_suggestions_on_the_session_task(cand):
    _check(cand, {"plot": EDITED}, {"plot": BASE})
    ai_figure_edit.record_final_labels(cand, {"plot": BASE})
    cand.committed = True
    sess = {
        "status": "running",
        "task": {"task_type": "figure_edit", "outcome": None},
        "taf": {"cand": cand},
    }
    task = {
        "outcome": "completed",
        "timed_out": False,
        "error": None,
        "task_result": {"verification": {"unmet": [], "explain": {}}, "budget_used": {}},
    }
    ai_bridge._settle_task(sess, task)
    assert sess["task"]["suggestions"] == [
        {
            "kind": "legend_label",
            "stem": "plot",
            "gid": "axes_0.lines_0",
            "legend_gid": "axes_0.legend.texts_0",
            "from": "B",
            "to": "B: treated",
        }
    ]
    assert sess["task"]["outcome"] == "completed"
    # 会话详情的 DTO 白名单整体放行 task
    assert ai_bridge._session_dto(sess)["task"]["suggestions"][0]["kind"] == "legend_label"


def test_settle_task_adds_no_key_when_there_is_nothing_to_suggest(cand):
    sess = {
        "status": "running",
        "task": {"task_type": "figure_edit", "outcome": None},
        "taf": {"cand": cand},
    }
    task = {
        "outcome": "completed",
        "timed_out": False,
        "error": None,
        "task_result": {"verification": {"unmet": [], "explain": {}}, "budget_used": {}},
    }
    cand.committed = True
    ai_bridge._settle_task(sess, task)
    assert "suggestions" not in sess["task"]


def test_convert_candidates_never_get_suggestions():
    assert "label_findings" not in ai_convert.ConvertCandidate.__dataclass_fields__


def test_postcommit_render_records_the_final_label_of_each_finding(cand):
    """提交后那次渲染（`_render_checks`）必须把最终提交版里的 label 写回发现——收尾判「改回原文」靠它。"""

    class _Run:
        def charge(self, _what):
            pass

    _check(cand, {"plot": EDITED}, {"plot": BASE})
    cand.new_bytes = b"x = 3\n"
    cand.extras.update({"after": lambda _sha: {"plot": BASE}, "before_manifests": {"plot": BASE}})
    ai_figure_edit.ADAPTER._render_checks(_Run(), cand, "postcommit")
    assert cand.extras["label_findings"][("plot", "axes_0.lines_0")]["final"] == "B"
    cand.committed = True
    assert [s["from"] for s in ai_figure_edit.legend_label_suggestions(cand, "completed")] == ["B"]


def test_no_suggestion_when_the_final_figure_has_no_bound_legend_entry(cand):
    """基线里图例显示它，但最终版里没有绑定这条曲线的可见图例项（图例被去掉 / 项被隐藏）：没有可写的图例项，不给。"""
    _check(cand, {"plot": EDITED}, {"plot": BASE})
    ai_figure_edit.record_final_labels(
        cand, {"plot": _man([("axes_0.lines_0", "B")], legend_texts=["B"], text_visible=False)}
    )
    cand.committed = True
    assert ai_figure_edit.legend_label_suggestions(cand, "completed") == []


def test_the_suggestion_carries_the_legend_entry_found_by_binding_not_a_fixed_gid(cand):
    final = {
        "plot": _man([("axes_0.lines_0", "B")], legend_texts=["x", "B"])
    }  # 绑定的图例项是第 2 项
    # 两项都绑定同一条曲线时取文字等于原文的那项
    _check(cand, {"plot": EDITED}, {"plot": BASE})
    ai_figure_edit.record_final_labels(cand, final)
    cand.committed = True
    assert [
        s["legend_gid"] for s in ai_figure_edit.legend_label_suggestions(cand, "completed")
    ] == ["axes_0.legend.texts_1"]
