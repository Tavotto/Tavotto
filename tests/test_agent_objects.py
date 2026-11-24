"""Agent 新增对象的校验、id 与导出载荷（ADR 0208 §五）。

向量 `tests/golden/agent_object_vectors.json` 由 `scripts/gen_agent_object_vectors.py` 生成；前端
`web/src/lib/agentObjects.golden.test.ts` 用同一份向量对拍前端真实的 `toExportObjects`。这里另做两件事：
通过的对象交给 RenderCore 真编译一遍（独立的消费者），以及字段表的预算与它声称的来源一致。
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from tavotto.engine import agentobjects

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads((ROOT / "tests" / "golden" / "agent_object_vectors.json").read_text("utf-8"))
HAS_RC = all(importlib.util.find_spec(m) is not None for m in ("pikepdf", "uharfbuzz", "PIL"))
CASES = VECTORS["cases"]


def test_vectors_are_fresh():
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_agent_object_vectors.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert r.returncode == 0, r.stderr


def test_error_codes_closed_set():
    assert VECTORS["error_codes"] == list(agentobjects.ERROR_CODES)
    used = {e["code"] for c in CASES if not c["ok"] for e in c["errors"]}
    # 每个码至少有一条用例（not_a_list / too_many 之外都在向量里；too_many 由下面的预算用例覆盖）
    assert set(agentobjects.ERROR_CODES) - used <= {"too_many", "not_an_object"}


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_vector_validation(case):
    r = agentobjects.validate_new_objects(case["input"])
    assert r["ok"] == case["ok"]
    if not case["ok"]:
        assert r["errors"] == case["errors"]
        assert r["objects"] == []
    else:
        assert len(r["objects"]) == len(case["objects"])


@pytest.mark.parametrize(
    "case", [c for c in CASES if c["ok"]], ids=[c["name"] for c in CASES if c["ok"]]
)
def test_vector_export_is_stable(case):
    assert agentobjects.to_export_objects(case["objects"]) == case["export"]


@pytest.mark.skipif(not HAS_RC, reason="RenderCore 依赖未装（not_run，不是绿）")
@pytest.mark.parametrize(
    "case", [c for c in CASES if c["ok"]], ids=[c["name"] for c in CASES if c["ok"]]
)
def test_accepted_objects_compile(case):
    """「合格」= 字段表通过 **且** 导出管线真画得出来：交给 RenderCore 编译，不许抛错、不许丢对象。"""
    from tavotto.rendercore import facade, plan

    compiled = plan.compile_page(
        120.0,
        80.0,
        case["export"],
        sources=None,
        faces=facade.provider(),
        background=None,  # type: ignore[arg-type]
    )
    assert len(compiled.page.children) == len(case["export"])
    assert compiled.dropped_hidden == ()


def test_ids_format_and_no_collision():
    taken = {"t_aaaaaaaa"}
    objs = [
        {
            "id": "x",
            "type": "text",
            "x": 0,
            "y": 0,
            "w": 1,
            "h": 1,
            "text": "a",
            "sizePt": 8,
            "bold": False,
            "color": "#000000",
            "align": "left",
            "groupPath": ["机理图", "细胞"],
        },
        {
            "id": "y",
            "type": "arrow",
            "x": 0,
            "y": 0,
            "w": 1,
            "h": 1,
            "start": {"rx": 0, "ry": 0},
            "end": {"rx": 1, "ry": 1},
            "strokePt": 1,
            "color": "#000000",
            "head": "end",
            "groupPath": ["机理图"],
        },
    ]
    r = agentobjects.validate_new_objects(objs, taken_ids=taken)
    assert r["ok"]
    t, a = r["objects"]
    assert re.fullmatch(r"t_[a-z0-9]{8}", t["id"]) and re.fullmatch(r"a_[a-z0-9]{8}", a["id"])
    assert t["id"] not in taken and r["id_map"] == {"x": t["id"], "y": a["id"]}
    # 同一条路径前缀 = 同一个组；两层的写 groupPath，一层的只写 groupId
    assert t["groupPath"][0] == a["groupId"] and t["groupId"] == t["groupPath"][-1]
    assert "groupPath" not in a
    assert [g["path"] for g in r["group_map"]] == [["机理图"], ["机理图", "细胞"]]
    assert all(re.fullmatch(r"g_[a-z0-9]{8}", g["id"]) for g in r["group_map"])


def test_same_group_name_under_different_parents_are_two_groups():
    base = {
        "type": "shape",
        "x": 0,
        "y": 0,
        "w": 1,
        "h": 1,
        "shape": "rect",
        "strokePt": 1,
        "color": "#000000",
        "fill": None,
    }
    r = agentobjects.validate_new_objects(
        [
            {**base, "id": "a", "groupPath": ["左", "膜"]},
            {**base, "id": "b", "groupPath": ["右", "膜"]},
        ]
    )
    assert r["ok"] and r["objects"][0]["groupId"] != r["objects"][1]["groupId"]


def test_budgets_match_their_sources():
    from tavotto import app
    from tavotto.rendercore import svgimport

    b = agentobjects.table()["budgets"]
    assert b["max_objects"] == app.SVG_COPY_MAX_OBJECTS
    assert b["max_nodes"] == app.SVG_COPY_MAX_NODES
    assert b["max_text_chars"] == app.SVG_COPY_MAX_TEXT
    assert b["max_gradient_stops"] == svgimport.MAX_STOPS
    assert b["max_coord_mm"] == svgimport.MAX_VIEWPORT_MM


def test_budget_too_many_objects():
    b = agentobjects.table()["budgets"]
    r = agentobjects.validate_new_objects([{}] * (b["max_objects"] + 1))
    assert not r["ok"] and r["errors"] == [{"code": "too_many"}]


def test_not_an_object_reported():
    r = agentobjects.validate_new_objects([1])
    assert r["errors"] == [{"code": "not_an_object", "index": 0}]


def test_errors_do_not_echo_input():
    secret = "SECRET-CONTENT-12345"
    r = agentobjects.validate_new_objects([{"id": "x", "type": "text", secret: 1}])
    assert secret not in json.dumps({k: v for k, v in r.items() if k != "errors"})
    # 字段名本身会出现在错误里（它是定位信息）；字段值永远不出现
    r = agentobjects.validate_new_objects([{"id": "x", "type": "text", "text": 5, "color": secret}])
    assert secret not in json.dumps(r)


def test_group_names_with_a_slash_do_not_collide():
    """`['a/b']` 与 `['a', 'b']` 是两个组：路径按元组认，不拼字符串（Codex #2196）。"""
    base = {
        "type": "shape",
        "x": 0,
        "y": 0,
        "w": 1,
        "h": 1,
        "shape": "rect",
        "strokePt": 1,
        "color": "#000000",
        "fill": None,
    }
    r = agentobjects.validate_new_objects(
        [{**base, "id": "u", "groupPath": ["a/b"]}, {**base, "id": "v", "groupPath": ["a", "b"]}]
    )
    assert r["ok"]
    u, v = r["objects"]
    assert u["groupId"] != v["groupId"]
    assert sorted(map(tuple, (g["path"] for g in r["group_map"]))) == [("a",), ("a", "b"), ("a/b",)]


def test_huge_integer_is_rejected_not_crashing():
    """JSON 里的超大整数转 float 会溢出：回结构化拒绝，不抛 OverflowError（Codex #2196）。"""
    item = {
        "id": "t",
        "type": "text",
        "x": 10**400,
        "y": 0,
        "w": 1,
        "h": 1,
        "text": "a",
        "sizePt": 8,
        "bold": False,
        "color": "#000000",
        "align": "left",
    }
    r = agentobjects.validate_new_objects([item])
    assert r["errors"] == [{"code": "bad_value", "index": 0, "field": "x"}]
