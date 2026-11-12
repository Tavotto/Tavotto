"""ChartModel / Binding 契约测试（ADR 0181；SG02）。

看护：合法模型往返逐字节稳定；错误版本 / 未知键 / 非有限数 / 重复 id /
错数据摘要 / 错场景摘要 / 绑定代际 / 缺节点 / 引擎身份不匹配分别被真实
消费方拒绝；身份摘要在数据变化时必须改变。前端镜像
`web/src/types/chartModel.ts` 与本文件共同消费
`tests/golden/chart_model_vectors.json`。
"""

from __future__ import annotations

import json

import pytest

from tavotto.engine import chartmodel as cm


def minimal_model(**overrides):
    model = {
        "schema": cm.MODEL_SCHEMA,
        "rev": 1,
        "axes": [
            {
                "id": "ax1",
                "rect": [51.5, 26.1, 297.6, 183.7],
                "xlim": [0.0, 10.0],
                "ylim": [0.0, 100.0],
                "xscale": "linear",
                "yscale": "linear",
                "invert_x": False,
                "invert_y": False,
            }
        ],
        "series": [
            {
                "id": "s1",
                "kind": "line",
                "axes": "ax1",
                "node": "line2d_13",
                "x": "x",
                "y": "y1",
                "style": {"color": "#1f77b4", "linewidth": 2.0},
            }
        ],
        "dataset": {"columns": {"x": [0.0, 1.0, 2.0], "y1": [0.0, 1.0, 4.0]}},
        "scene_digest": "a" * 64,
        "data_digest": cm.digest_of({"columns": {"x": [0.0, 1.0, 2.0], "y1": [0.0, 1.0, 4.0]}}),
        "engine": {"matplotlib": "3.11.2", "numpy": "2.1.0", "tavotto": "0.19.2"},
    }
    model.update(overrides)
    return model


def minimal_binding(model):
    return {
        "model_rev": model["rev"],
        "scene_digest": model["scene_digest"],
        "entries": [{"object": "series:s1", "node": "line2d_13"}],
    }


# ---------------------------------------------------------------------------
# 往返与摘要
# ---------------------------------------------------------------------------


def test_valid_model_roundtrips_byte_stable():
    model = minimal_model()
    text = cm.dumps_model(model)
    again = cm.loads_model(text)
    assert cm.dumps_model(again) == text
    assert cm.digest_of(model) == cm.digest_of(again)


def test_identity_changes_when_data_changes():
    a = minimal_model()
    b = minimal_model()
    b["dataset"]["columns"]["y1"] = [0.0, 2.0, 4.0]
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_model(b)
    assert ei.value.code == "chart_data_changed"
    # 身份必须在数据被修正记账后仍然区分两个版本
    b["data_digest"] = cm.digest_of({"columns": b["dataset"]["columns"]})
    cm.validate_model(b)
    assert cm.digest_of(a) != cm.digest_of(b)


# ---------------------------------------------------------------------------
# 拒绝面（每个错误码至少一条真实消费方路径）
# ---------------------------------------------------------------------------


def test_rejects_newer_schema_major():
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_model(minimal_model(schema="tavotto.chart-model/2"))
    assert ei.value.code == "chart_model_unsupported"


def test_rejects_unknown_keys_everywhere():
    bad = minimal_model()
    bad["script"] = "import os"
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_model(bad)
    assert ei.value.code == "chart_model_invalid"
    bad2 = minimal_model()
    bad2["series"][0]["style"]["onclick"] = "alert(1)"
    with pytest.raises(cm.ChartModelError):
        cm.validate_model(bad2)


def test_rejects_non_finite_literals_and_values():
    with pytest.raises(cm.ChartModelError) as ei:
        cm.loads_model(json.dumps(minimal_model()).replace('"rev": 1', '"rev": NaN'))
    assert ei.value.code == "chart_model_invalid"
    bad = minimal_model()
    bad["axes"][0]["xlim"] = [0.0, float("inf")]
    with pytest.raises(cm.ChartModelError):
        cm.validate_model(bad)


def test_rejects_duplicate_ids():
    bad = minimal_model()
    bad["axes"].append(dict(bad["axes"][0]))
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_model(bad)
    assert ei.value.code == "chart_duplicate_id"
    bad2 = minimal_model()
    bad2["series"].append(dict(bad2["series"][0]))
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_model(bad2)
    assert ei.value.code == "chart_duplicate_id"


def test_rejects_series_referencing_unknown_axes_or_columns():
    bad = minimal_model()
    bad["series"][0]["axes"] = "nope"
    with pytest.raises(cm.ChartModelError):
        cm.validate_model(bad)
    bad2 = minimal_model()
    bad2["series"][0]["y"] = "ghost"
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_model(bad2)
    assert ei.value.code == "chart_data_invalid"


def test_rejects_empty_scale_range_and_bad_style_values():
    bad = minimal_model()
    bad["axes"][0]["xlim"] = [5.0, 5.0]
    with pytest.raises(cm.ChartModelError):
        cm.validate_model(bad)
    bad2 = minimal_model()
    bad2["series"][0]["style"]["alpha"] = 1.5
    with pytest.raises(cm.ChartModelError):
        cm.validate_model(bad2)
    bad3 = minimal_model()
    bad3["axes"][0]["xscale"] = "squircle"
    with pytest.raises(cm.ChartModelError):
        cm.validate_model(bad3)


def test_error_codes_are_closed_and_raise_safe():
    assert "chart_model_unsupported" in cm.ERROR_CODES
    with pytest.raises(AssertionError):
        cm.ChartModelError("not_a_code")


# ---------------------------------------------------------------------------
# Binding：代际与场景一致性是真实消费方的门
# ---------------------------------------------------------------------------


def test_binding_stale_and_scene_mismatch_are_refused():
    model = minimal_model()
    binding = minimal_binding(model)
    cm.validate_binding(binding, model)
    binding["model_rev"] = 2
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_binding(binding, model)
    assert ei.value.code == "chart_binding_stale"
    binding["model_rev"] = 1
    binding["scene_digest"] = "b" * 64
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_binding(binding, model)
    assert ei.value.code == "chart_source_mismatch"


def test_verify_scene_refuses_changed_scene_and_missing_nodes():
    model = minimal_model()
    binding = minimal_binding(model)
    with pytest.raises(cm.ChartModelError) as ei:
        cm.verify_scene(binding, "c" * 64, {"line2d_13"})
    assert ei.value.code == "chart_source_mismatch"
    with pytest.raises(cm.ChartModelError) as ei:
        cm.verify_scene(binding, model["scene_digest"], {"something_else"})
    assert ei.value.code == "chart_node_missing"
    cm.verify_scene(binding, model["scene_digest"], {"line2d_13", "other"})


def test_engine_identity_must_match_on_replay():
    e1 = cm.engine_identity("3.11.2", "2.1.0", "0.19.2")
    e2 = cm.engine_identity("3.11.3", "2.1.0", "0.19.2")
    assert cm.engine_digest(e1) != cm.engine_digest(e2)
    assert cm.engine_digest(e1) == cm.engine_digest(cm.engine_identity("3.11.2", "2.1.0", "0.19.2"))


def test_panel_identity_distinguishes_kinds_models_and_bindings():
    scene = "d" * 64
    model = minimal_model()
    binding = minimal_binding(model)
    fig_id = cm.panel_identity(cm.SOURCE_FIGURE, scene, None)
    dec_id = cm.panel_identity(cm.SOURCE_DECLARATIVE, scene, model, binding)
    other_model = cm.panel_identity(
        cm.SOURCE_DECLARATIVE,
        scene,
        minimal_model(rev=2),
        {**binding, "model_rev": 2},
    )
    assert fig_id != dec_id != other_model
    # 同一模型绑到不同节点（N3）：绑定不同 = 不同版本
    other_binding = cm.panel_identity(
        cm.SOURCE_DECLARATIVE,
        scene,
        model,
        {**binding, "entries": [{"object": "series:s1", "node": "line2d_99"}]},
    )
    assert other_binding != dec_id
    # declarative 缺绑定 = 合同错误
    with pytest.raises(cm.ChartModelError):
        cm.panel_identity(cm.SOURCE_DECLARATIVE, scene, model, None)
    with pytest.raises(cm.ChartModelError):
        cm.panel_identity("mystery", scene, None)


# ---------------------------------------------------------------------------
# N4 legend 合同：binding.legend 的闭式字段集与判据（validate_legend_spec 唯一出处）
# ---------------------------------------------------------------------------


def legend_item(**overrides):
    item = {
        "id": "leg1",
        "node": "legend_region",
        "rect": [10.0, 10.0, 40.0, 20.0],
        "entries": [
            {"index": 0, "label": "series one", "series": "s1"},
            {"index": 1, "label": "note only"},
        ],
    }
    item.update(overrides)
    return item


def binding_with_legend(model, legend):
    return {**minimal_binding(model), "legend": legend}


def test_legend_contract_accepts_follow_custom_mix():
    model = minimal_model()
    binding = binding_with_legend(model, [legend_item()])
    assert cm.validate_binding(binding, model) is binding
    # 图例关系变化 = 面板身份变化（binding digest 进 panel_identity）
    assert cm.panel_identity(cm.SOURCE_DECLARATIVE, "d" * 64, model, binding) != cm.panel_identity(
        cm.SOURCE_DECLARATIVE, "d" * 64, model, minimal_binding(model)
    )


def test_legend_rect_anchor_top_left_is_semantics_only():
    # 方案 A（区域外接矩形左上角对齐 legend 盒左上角）只影响脚手架换算，
    # 合同层只核对 rect 形状；这里钉的是负坐标/大数值仍是合法用户单位。
    model = minimal_model()
    cm.validate_binding(
        binding_with_legend(model, [legend_item(rect=[-5.0, 0.0, 40.0, 20.0])]), model
    )


def test_legend_contract_rejects_each_violation():
    model = minimal_model()
    binding = binding_with_legend(
        model, [legend_item(), legend_item(id="leg1", node="legend_other")]
    )
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_binding(binding, model)
    assert ei.value.code == "chart_duplicate_id"
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_binding(binding_with_legend(model, [legend_item(node="line2d_13")]), model)
    assert ei.value.code == "chart_duplicate_id"  # 与 entries 节点互斥
    bad = [legend_item(entries=[{"index": 0, "label": "a"}, {"index": 0, "label": "b"}])]
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_binding(binding_with_legend(model, bad), model)
    assert ei.value.code == "chart_duplicate_id"  # index 重复
    bad = [legend_item(entries=[{"index": -1, "label": "a"}])]
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_binding(binding_with_legend(model, bad), model)
    assert ei.value.code == "chart_model_invalid"  # index 负数
    bad = [legend_item(entries=[{"index": 0, "label": ""}])]
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_binding(binding_with_legend(model, bad), model)
    assert ei.value.code == "chart_model_invalid"  # label 空串
    bad = [legend_item(entries=[{"index": 0, "label": "a", "series": "ghost"}])]
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_binding(binding_with_legend(model, bad), model)
    assert ei.value.code == "chart_model_invalid"  # series 不在模型
    bad = [legend_item(rect=[10.0, 10.0, 40.0, 0.0])]
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_binding(binding_with_legend(model, bad), model)
    assert ei.value.code == "chart_model_invalid"  # rect 非正
    bad = [legend_item(script="import os")]
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_binding(binding_with_legend(model, bad), model)
    assert ei.value.code == "chart_model_invalid"  # 未知键（闭集）
    bad = [legend_item(entries=[{"index": 0, "label": "a", "visible": True}])]
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_binding(binding_with_legend(model, bad), model)
    assert ei.value.code == "chart_model_invalid"  # 条目未知键
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_binding(binding_with_legend(model, []), model)
    assert ei.value.code == "chart_model_invalid"  # legend=[]
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_binding(binding_with_legend(model, [legend_item(entries=[])]), model)
    assert ei.value.code == "chart_model_invalid"  # 只绑区域不列条目 = 半接
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_binding(binding_with_legend(model, [{"id": "leg1", "node": "n"}]), model)
    assert ei.value.code == "chart_model_invalid"  # 缺 rect/entries
    # 键缺席 = 旧文档照旧合法
    cm.validate_binding(minimal_binding(model), model)


def test_verify_scene_checks_legend_region_nodes():
    model = minimal_model()
    binding = binding_with_legend(model, [legend_item()])
    cm.verify_scene(binding, model["scene_digest"], {"line2d_13", "legend_region"})
    with pytest.raises(cm.ChartModelError) as ei:
        cm.verify_scene(binding, model["scene_digest"], {"line2d_13"})
    assert ei.value.code == "chart_node_missing"


def test_validate_legend_spec_is_single_authority_for_worker():
    # worker 侧（svgchart 不信任父进程）调用同一函数；无绑定 entries 可比，
    # 只核 legend 内部判据。
    model = minimal_model()
    assert cm.validate_legend_spec([legend_item()], model) == [legend_item()]
    with pytest.raises(cm.ChartModelError) as ei:
        cm.validate_legend_spec([legend_item(), legend_item(id="leg1", node="n2")], model)
    assert ei.value.code == "chart_duplicate_id"
