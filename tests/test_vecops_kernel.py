"""`engine/vecops`（ADR 0212 第 3 期）：后端用 wasmtime 跑**前端同一份** `vecops.wasm`。

严格同源对：前端 `web/src/lib/vecops/kernel.ts` ↔ `engine/vecops.py`（同一个 ABI、同一份二进制）。
金向量由 Node 跑同一份 wasm 生成（`scripts/dev/gen_pathfinder_vectors.mjs`）：`pathfinder_vectors.json`（前端
`pathfinder.test.ts` 也比）与 `vecops_kernel_vectors.json`（偏移 / 轮廓 / 简化）；Python 这边必须**逐数相等**——
同一份二进制，位级一致，不是「差不多」。"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tavotto.engine import vecops

GOLDEN = Path(__file__).parent / "golden"


def _cases(name: str) -> list[dict]:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))["cases"]


@pytest.fixture(autouse=True)
def _fresh():
    vecops._reset_for_tests()
    yield
    vecops._reset_for_tests()


def test_kernel_is_available_and_finds_a_wasm():
    assert vecops.available()
    assert vecops.wasm_path() is not None and vecops.wasm_path().suffix == ".wasm"


@pytest.mark.parametrize("case", _cases("pathfinder_vectors.json"), ids=lambda c: c["name"])
def test_pathfinder_matches_the_node_golden_number_for_number(case):
    out = vecops.pathfinder(case["op"], case["input"])
    assert out == case["output"]  # 逐数相等（含符号位 / 浮点位），不是近似


@pytest.mark.parametrize("case", _cases("vecops_kernel_vectors.json"), ids=lambda c: c["name"])
def test_offset_outline_simplify_match_the_node_golden_number_for_number(case):
    fn = getattr(vecops, case["fn"])
    out = fn(case["input"], *case["args"])
    # 前端 kernel.ts 把 0 个输出当「没有结果」= null；这里同样 None
    assert (out or []) == case["output"]


def test_a_bad_input_is_none_not_a_crash():
    assert vecops.pathfinder(4, [1, 1, 0]) is None
    assert vecops.pathfinder(99, [0]) is None  # 不认识的 op
    assert vecops.offset([1, 1, 1], 3, 1, 10) is None
    assert vecops.simplify([], 0.5) is None


def test_non_finite_numbers_never_reach_the_kernel():
    assert vecops.offset([1, 0, 0], math.nan, 1, 10) is None
    assert vecops.pathfinder(0, [1, 0, math.inf]) is None
    assert vecops.simplify([1, 0, 0], math.inf) is None
    assert vecops._inst is None  # 连实例都没起：在进内核之前就挡掉了


def test_unavailable_kernel_returns_none_everywhere(monkeypatch):
    monkeypatch.setattr(vecops, "wasm_path", lambda: None)
    assert vecops.available() is False
    case = _cases("pathfinder_vectors.json")[0]
    assert vecops.pathfinder(case["op"], case["input"]) is None
    assert vecops.offset([1, 0, 0], 1, 1, 10) is None
    assert vecops.outline([1, 0, 0], 1, 0, 0, 10) is None
    assert vecops.simplify([1, 0, 0], 0.5) is None


def test_wasmtime_missing_is_unavailable_not_an_error(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "wasmtime", None)  # `import wasmtime` → ImportError
    assert vecops.available() is False
    assert vecops.pathfinder(0, [0]) is None


def test_abi_mismatch_disables_the_kernel(monkeypatch):
    monkeypatch.setattr(vecops, "VECOPS_ABI", 2)
    assert vecops.available() is False
    case = _cases("pathfinder_vectors.json")[0]
    assert vecops.pathfinder(case["op"], case["input"]) is None


def test_a_trap_returns_none_then_the_next_call_works_on_a_fresh_instance(monkeypatch):
    case = _cases("pathfinder_vectors.json")[0]
    assert vecops.pathfinder(case["op"], case["input"]) == case["output"]
    first = vecops._inst
    assert first is not None
    monkeypatch.setattr(vecops, "_FUEL", 50)  # 燃料耗尽 = trap
    assert vecops.pathfinder(case["op"], case["input"]) is None
    assert vecops._inst is None  # 线性内存不可信：实例扔掉
    monkeypatch.setattr(vecops, "_FUEL", 8_000_000_000)
    assert vecops.pathfinder(case["op"], case["input"]) == case["output"]
    assert vecops._inst is not None and vecops._inst is not first  # 新实例


def test_repeated_calls_do_not_leak_or_drift():
    case = _cases("pathfinder_vectors.json")[0]
    for _ in range(200):
        assert vecops.pathfinder(case["op"], case["input"]) == case["output"]
