"""ChartModel / Binding 契约测试（ADR 0166；SG02）。

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
