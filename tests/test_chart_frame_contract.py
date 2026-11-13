"""chartframe.validate_frame ↔ 前端 validComputedFrame 的同源对金向量腿。

同源对（PLAN.md 第 1 步对 1）：/2 render_frame 的闭式形状判据只有一份出处，
后端 ``engine/chartframe.validate_frame`` 与前端
``web/src/lib/chartFrame.ts validComputedFrame()`` 各自消费同一份
``tests/golden/computed_frame_vectors.json``，不读对方源码。前端那一侧是
``web/src/lib/chartFrame.test.ts`` 的「computed render frame 闭式判据（同源对）」
describe 块；向量格式与判据全文见该 JSON 的 ``_comment``。

落地：本文件放 ``tests/test_chart_frame_contract.py``，JSON 放
``tests/golden/computed_frame_vectors.json``（本目录下同名 JSON 是同一份
移植前副本，落地时以它为准）。判据主语是「这一次 /2 实际 SVG draw 的外框
声明」——不是 savefig provenance，也不是 source viewBox。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavotto.engine import chartframe

FRAME_GOLDEN = json.loads(
    (Path(__file__).resolve().parent / "golden" / "computed_frame_vectors.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("vector", FRAME_GOLDEN["accept"], ids=lambda v: v["id"])
def test_computed_frame_accept_is_the_golden_pair(vector):
    assert chartframe.validate_frame(vector["frame"]) == vector["frame"]


@pytest.mark.parametrize("vector", FRAME_GOLDEN["reject"], ids=lambda v: v["id"])
def test_computed_frame_reject_is_the_golden_pair(vector):
    with pytest.raises(ValueError):
        chartframe.validate_frame(vector["frame"])


def test_computed_frame_returns_a_defensive_copy():
    """validate_frame 返回深拷贝：进 manifest 的 F 不与调用方共享可变内档。"""
    frame = FRAME_GOLDEN["accept"][0]["frame"]
    validated = chartframe.validate_frame(frame)
    validated["rect_pt"][0] = 999
    assert chartframe.validate_frame(frame)["rect_pt"] == frame["rect_pt"]


def test_nonfinite_numbers_are_rejected_on_both_legs():
    """非有限数 JSON 表达不了，金向量装不下，两端各单列一条（前端腿在
    chartFrame.test.ts 同名测试里）。"""
    frame = dict(FRAME_GOLDEN["accept"][0]["frame"])
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            chartframe.validate_frame({**frame, "rect_pt": [bad, 0, 100, 100]})
        with pytest.raises(ValueError):
            chartframe.validate_frame({**frame, "source_viewport_pt": [bad, 100]})
