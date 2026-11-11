"""Golden vectors 侧的 pytest 腿：与 vitest 共跑同一份 chart_model_vectors.json。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavotto.engine import chartmodel as cm

VECTORS = Path(__file__).resolve().parents[1] / "tests" / "golden" / "chart_model_vectors.json"


@pytest.mark.parametrize(
    "vector", json.loads(VECTORS.read_text(encoding="utf-8"))["vectors"], ids=lambda v: v["name"]
)
def test_chart_model_vector(vector):
    model, binding, expect = vector["model"], vector["binding"], vector["expect"]

    def run_model():
        cm.validate_model(model)

    if expect.startswith("binding:"):
        # N4 legend 合同等向量：模型本身合法，判据落在 validate_binding。
        run_model()
        with pytest.raises(cm.ChartModelError) as ei:
            cm.validate_binding(binding, model)
        assert ei.value.code == expect.split(":", 1)[1], f"{vector['name']}: {ei.value.detail}"
        return
    if expect == "ok":
        run_model()
        cm.validate_binding(binding, model)
        return
    with pytest.raises(cm.ChartModelError) as ei:
        if expect == "chart_binding_stale" or expect == "chart_source_mismatch":
            # 这两条的模型本身合法；判据落在绑定代际 / 场景一致性上
            cm.validate_model(model)
            cm.validate_binding(binding, model)
        else:
            run_model()
    assert ei.value.code == expect, f"{vector['name']}: {ei.value.detail}"
