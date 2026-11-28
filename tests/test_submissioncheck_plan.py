"""投稿核对：量值、组件结果校验、要落哪些操作、交给 specfix 的输入（ADR 0216 §九）。纯函数，不起 worker。"""

from __future__ import annotations

import copy

import pytest

from tavotto.engine import submissioncheck as sc


def _text(gid, role, size, text="10", family="DejaVu Sans", **extra):
    return {
        "gid": gid,
        "role": role,
        "bbox": extra.pop("bbox", [0.4, 0.9, 0.5, 0.95]),
        "editable": [
            {"prop": "fontsize", "value": size},
            {"prop": "text", "value": text},
            {"prop": "fontfamily", "value": family},
        ],
        **extra,
    }


def _manifest(*, labels=True, stray_letter=False):
    els = [
        {"gid": "figure", "role": "figure", "bbox": [0, 0, 1, 1]},
        {"gid": "axes_0", "role": "axes", "bbox": [0.1, 0.1, 0.35, 0.8], "editable": []},
        {"gid": "axes_1", "role": "axes", "bbox": [0.6, 0.1, 0.35, 0.8], "editable": []},
        _text("axes_0.xlabel", "xlabel", 8, "Time"),
        _text("axes_0.ticks", "ticks", 6),
    ]
    if labels:
        for i, (x, letter) in enumerate([(0.02, "a"), (0.52, "b")]):
            els.append(
                _text(
                    f"text_{i}",
                    "text",
                    8,
                    letter,
                    bbox=[x, 0.0, 0.03, 0.05],
                    drag_prop="pos_frac",
                    anchor=[x, 0.0],
                )
            )
    if stray_letter:
        els.append(_text("axes_1.title", "title", 9, "c"))
    return {"size_mm": [89.0, 60.0], "elements": els}


def test_measure_reports_page_sizes_and_recognised_panel_labels():
    texts, refs, known = sc.measure_figure(_manifest(), scale=0.5, prefix="c1")
    by_gid = {refs[t["ref"]]: t for t in texts}
    assert by_gid["axes_0.xlabel"]["size_pt"] == 4.0  # 8 pt × 0.5
    assert by_gid["text_0"]["panel_label"] and by_gid["text_1"]["panel_label"]
    assert not by_gid["axes_0.ticks"]["panel_label"]
    assert known is True
    assert all("text" not in t for t in texts)  # 不给文字内容


def test_a_letter_that_is_not_a_recognised_label_makes_labels_unknown():
    _, _, known = sc.measure_figure(_manifest(stray_letter=True), scale=1.0, prefix="c1")
    assert known is False


def test_canvas_texts_are_measured_but_never_editable():
    out = sc.canvas_texts([{"size_pt": 8, "panel_label": True, "family": "Arial"}], "c1")
    assert out == [
        {
            "ref": "c1k1",
            "role": "canvas-text",
            "size_pt": 8.0,
            "family": "Arial",
            "panel_label": True,
            "editable": False,
        }
    ]


def _payload():
    texts, refs, known = sc.measure_figure(_manifest(), scale=1.0, prefix="c1")
    texts += sc.canvas_texts([{"size_pt": 8, "panel_label": True}], "c1")
    rows = [
        {
            "ref": "r1",
            "check": "font_size_max",
            "relation": "lte",
            "value": 7,
            "unit": "pt",
            "source_state": "confirm",
            "conditions": {"text_role": "other_text"},
            "id": "v2:fmax",
            "aliases": [],
        },
        {
            "ref": "r2",
            "check": "width_final",
            "relation": "eq",
            "value": 89,
            "unit": "mm",
            "source_state": "preflight",
            "conditions": {"column": "single"},
            "id": "v1:page_columns#single",
            "aliases": ["v2:w1"],
        },
        {
            "ref": "r3",
            "check": "font_family",
            "relation": "in",
            "value": ["Helvetica", "Arial"],
            "unit": None,
            "source_state": "confirm",
            "conditions": {},
            "id": "v1:font_family.latin_accepted",
            "aliases": [],
        },
    ]
    payload = {
        "requirements": rows,
        "canvases": [
            {
                "ref": "c1",
                "width_mm": 81.3,
                "height_mm": 60,
                "width_adjustable": True,
                "texts": texts,
                "panel_labels_known": known,
            }
        ],
    }
    return payload, rows, refs


def _good_result(refs):
    body = [r for r, g in refs.items() if g in ("axes_0.xlabel",)]
    return {
        "requirements": [
            {
                "ref": "r1",
                "state": "fixable",
                "per_canvas": [{"canvas": "c1", "state": "fixable", "ops": ["o2"]}],
            },
            {
                "ref": "r2",
                "state": "fixable",
                "per_canvas": [{"canvas": "c1", "state": "fixable", "ops": ["o1"]}],
            },
            {
                "ref": "r3",
                "state": "fixable",
                "per_canvas": [{"canvas": "c1", "state": "fixable", "ops": ["o3"]}],
            },
        ],
        "ops": [
            {"ref": "o1", "op": "canvas.width", "canvas": "c1", "width_mm": 89},
            {"ref": "o2", "op": "text.size", "canvas": "c1", "texts": body, "size_pt": 7},
            {"ref": "o3", "op": "text.family", "canvas": "c1", "texts": body, "family": "Arial"},
        ],
    }


def test_a_well_formed_result_validates():
    payload, _, refs = _payload()
    checked = sc.validate_result(_good_result(refs), payload)
    assert set(checked["ops"]) == {"o1", "o2", "o3"}


def _label_ref(refs):
    return next(r for r, g in refs.items() if g == "text_0")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda res, refs: res["ops"].append(dict(res["ops"][0])),  # 重复的 op ref
        lambda res, refs: res["ops"][0].update(width_mm=5000),
        lambda res, refs: res["ops"][1].update(size_pt=0.2),
        lambda res, refs: res["ops"][1].update(texts=["nope"]),
        lambda res, refs: res["ops"][1].update(texts=["c1k1"]),  # 画布级文字不许改
        lambda res, refs: res["ops"][1].update(texts=[_label_ref(refs)]),  # 正文上限不碰分图标签
        lambda res, refs: res["ops"][2].update(family="Comic Sans"),  # 不在名单里
        lambda res, refs: res["ops"][0].update(op="figure.delete"),
        lambda res, refs: res["requirements"][0].update(state="pass"),  # confirm 行不许说判过
        lambda res, refs: res["requirements"][0].update(ref="r99"),
        lambda res, refs: res["requirements"][1]["per_canvas"][0].update(ops=["o9"]),
        lambda res, refs: res.update(requirements=12),  # 集合类型不对
        lambda res, refs: res["ops"][1].update(texts=[{}]),  # ref 不是字符串
        lambda res, refs: res["requirements"][0].update(per_canvas=[{"canvas": ["c1"]}]),
        lambda res, refs: res["requirements"][0]["per_canvas"][0].update(ops="o2"),
    ],
)
def test_any_bad_item_voids_the_whole_result(mutate):
    payload, _, refs = _payload()
    res = _good_result(refs)
    mutate(res, refs)
    with pytest.raises(sc.SubmissionCheckError) as e:
        sc.validate_result(res, payload)
    assert e.value.code == "invalid_plan"


def test_width_on_a_multi_figure_canvas_and_max_size_with_unknown_labels_are_refused():
    payload, _, refs = _payload()
    payload["canvases"][0]["width_adjustable"] = False
    with pytest.raises(sc.SubmissionCheckError):
        sc.validate_result(_good_result(refs), payload)
    payload, _, refs = _payload()
    payload["canvases"][0]["panel_labels_known"] = False
    with pytest.raises(sc.SubmissionCheckError):
        sc.validate_result(_good_result(refs), payload)


def test_judged_fixes_land_by_themselves_and_others_only_when_accepted():
    payload, rows, refs = _payload()
    checked = sc.validate_result(_good_result(refs), payload)
    assert [o["ref"] for o in sc.chosen_ops(checked, rows, [])] == ["o1"]  # 只有判过的栏宽
    picked = sc.chosen_ops(checked, rows, ["v2:fmax"])
    assert [o["op"] for o in picked] == ["canvas.width", "text.size"]  # 图宽先落
    # 并进来的 v2 别名同样认
    rows2 = copy.deepcopy(rows)
    rows2[1]["source_state"] = "confirm"
    assert [o["ref"] for o in sc.chosen_ops(checked, rows2, ["v2:w1"])] == ["o1"]


def test_specfix_request_targets_only_the_named_texts():
    payload, rows, refs = _payload()
    checked = sc.validate_result(_good_result(refs), payload)
    profile = {"severity": {}, "font_family": {"latin": "Times New Roman"}}
    size_op, fam_op = checked["ops"]["o2"], checked["ops"]["o3"]
    prof, only = sc.specfix_request(size_op, rows[0], refs, profile)
    assert prof["max_font_size_pt"] == 7.0 and prof["severity"]["font-too-large"] == "warn"
    assert only == [{"rule": "font-too-large", "gid": "axes_0.xlabel"}]
    prof, only = sc.specfix_request(fam_op, rows[2], refs, profile)
    assert prof["font_family"]["latin_accepted"] == ["Arial"]
    assert only == [{"rule": "font-family-substituted", "gid": "axes_0.xlabel"}]
    assert profile == {"severity": {}, "font_family": {"latin": "Times New Roman"}}  # 原规范不动


@pytest.mark.parametrize(
    ("row", "value", "ok"),
    [
        ({"check": "font_size_max", "relation": "lte", "value": 7}, 7.0, True),
        ({"check": "font_size_max", "relation": "lte", "value": 7}, 7.5, False),
        ({"check": "font_size_min", "relation": "gte", "value": 5}, 4.9, False),
        ({"check": "width_final", "relation": "eq", "value": 89}, 89.3, True),
        ({"check": "width_final", "relation": "eq", "value": 89}, 81.3, False),
        ({"check": "font_family", "relation": "in", "value": ["Arial"]}, "arial", True),
        (
            {
                "check": "width_final",
                "relation": "interval",
                "value": {"lower": 120, "upper": 136},
            },
            130,
            True,
        ),
    ],
)
def test_satisfied_judges_by_the_remeasured_value(row, value, ok):
    assert sc.satisfied(row, value) is ok


def test_open_example_extension_speaks_the_command(monkeypatch, tmp_path):
    """开源示例扩展 `pro_echo` 的 `submission_check.plan`（全部 confirm、不给操作）过得了契约校验。"""
    from pathlib import Path

    from tavotto.engine import license as engine_license, prohost

    monkeypatch.delenv(engine_license.ENV_HARD_OFF, raising=False)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv(engine_license.ENV_ENDPOINT, "http://127.0.0.1:9")
    monkeypatch.setenv("TAVOTTO_DIAG_DEV_LOOPBACK", "1")
    monkeypatch.setenv(prohost.ENV_PRO_DIR, str(Path(__file__).parent / "fixtures" / "pro_echo"))
    monkeypatch.setenv("PRO_ECHO_FEATURES", "quick-layout,submission-pack")
    monkeypatch.setattr(prohost, "_crashes", 0)
    prohost.shutdown()
    try:
        payload, rows, _ = _payload()
        checked = sc.ask(payload)
        assert {i["state"] for i in checked["requirements"].values()} == {"confirm"}
        assert sc.chosen_ops(checked, rows, ["v2:fmax"]) == []
    finally:
        prohost.shutdown()


def test_unavailable_component_is_a_quiet_failure():
    from tavotto.engine import prohost

    def boom(_payload):
        raise prohost.ProUnavailable("crashed", "x")

    payload, _, _ = _payload()
    with pytest.raises(sc.SubmissionCheckError) as e:
        sc.ask(payload, solver=boom)
    assert e.value.code == "unavailable"


def test_unresolved_choose_is_not_applied_until_the_user_picks_it():
    payload, rows, refs = _payload()
    res = _good_result(refs)
    res["requirements"][1]["state"] = "choose"
    res["requirements"][1]["per_canvas"][0]["state"] = "choose"
    checked = sc.validate_result(res, payload)
    assert sc.chosen_ops(checked, rows, []) == []  # 判过的栏宽也不自己选
    assert [o["ref"] for o in sc.chosen_ops(checked, rows, ["v1:page_columns#single"])] == ["o1"]


def test_raising_the_minimum_also_names_the_absolute_floor_rule():
    payload, rows, refs = _payload()
    row = {**rows[0], "check": "font_size_min", "relation": "gte", "value": 9}
    op = {"op": "text.size", "texts": [next(iter(refs))], "size_pt": 9}
    prof, only = sc.specfix_request(
        op, row, refs, {"severity": {}, "absolute_min_font_size_pt": 10}
    )
    assert {o["rule"] for o in only} == {"font-too-small", "font-below-absolute-floor"}
    assert prof["absolute_min_font_size_pt"] == 9.0
