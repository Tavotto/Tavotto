"""统一实施包（`docs/implementation/tavotto-foundation/`）的结构自检——**不是产品测试**。

它只跑实施包自带的 `tools/validate_plan.py`（来源哈希、220 条映射、阶段 DAG、本地链接与代码围栏）
并断言 `ok`。判据的主语是**任务书的结构**：漏一条来源、DAG 成环、链接指向不存在的文件会红；
Tavotto 的兼容性、PDF 渲染、首开链路一个字都不在它的覆盖里（`product_tests_executed` 恒 False，
`product_qualification` 恒 `not_run`，这里也钉住这两个字段不许被改成别的意思）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

DOC_ROOT = Path(__file__).resolve().parent.parent / "docs" / "implementation" / "tavotto-foundation"


def _validator():
    """装载实施包的校验器，不往 `tools/` 旁边写 `__pycache__`。"""
    was = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(
            "u00_validate_plan", DOC_ROOT / "tools" / "validate_plan.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = was
    return mod


def test_the_pack_is_where_the_brief_says_it_is():
    for name in ("plan.json", "registry.json", "sources_manifest.json", "phase_mapping.json"):
        assert (DOC_ROOT / name).is_file(), name
    assert (DOC_ROOT / "handoffs" / "U00_baseline.md").is_file()


def test_validate_plan_passes_and_claims_no_product_qualification():
    result = _validator().validate(DOC_ROOT)
    assert result["errors"] == [], "\n".join(result["errors"])
    assert result["ok"] is True
    assert result["product_tests_executed"] is False
    assert result["product_qualification"] == "not_run"


def test_u00_is_done_but_every_product_status_is_still_not_run():
    plan = json.loads((DOC_ROOT / "plan.json").read_text(encoding="utf-8"))
    u00 = next(s for s in plan["stages"] if s["id"] == "U00")
    assert u00["implementation_status"] == "done"
    assert u00["product_validation_status"] == "not_run"
    assert plan["new_default_capabilities_enabled"] == []
    registry = json.loads((DOC_ROOT / "registry.json").read_text(encoding="utf-8"))
    assert registry["product_validation_status"] == "not_run"
    assert all(e["execution_status"] == "not_run" for e in registry["entries"])
    assert len(registry["entries"]) == 220
