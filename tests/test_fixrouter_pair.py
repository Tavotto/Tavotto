"""智能修复 remedy 的前后端同源对（ADR 0148）：后端 `fixrouter` == 前端 `lib/fixRemedy.ts` 的闭集与字段表。

主语：**两侧的 remedy 种类闭集与每种的字段名**，加上同一份 golden（`tests/golden/fix_remedy_vectors.json`）里每条
「失败码 + 事实 -> remedy」。前端 `web/src/lib/fixRemedy.test.ts` 读同一份 golden，不读后端源码；这里读 TS 源码的
`REMEDY_FIELDS` 表（与 `test_environment_rejection_copy_pair` 同形），所以任何一侧改闭集都会让两边的用例同时红。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tavotto.engine import fixrouter

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = json.loads((ROOT / "tests" / "golden" / "fix_remedy_vectors.json").read_text("utf-8"))
TS = ROOT / "web" / "src" / "lib" / "fixRemedy.ts"


def _ts_fields() -> dict[str, list[str]]:
    body = (
        TS.read_text(encoding="utf-8")
        .split("export const REMEDY_FIELDS", 1)[1]
        .split("} as const", 1)[0]
    )
    out: dict[str, list[str]] = {}
    for kind, names in re.findall(r"^\s*([a-z_]+):\s*\[([^\]]*)\]", body, re.M):
        out[kind] = sorted(re.findall(r"'([a-z_]+)'", names))
    return out


def test_golden_kinds_are_the_backend_closed_set_in_order():
    assert GOLDEN["kinds"] == list(fixrouter.REMEDY_KINDS)


def test_frontend_field_table_mirrors_the_golden_field_table():
    ts = _ts_fields()
    assert ts, "没解析出任何种类：判据空转"
    assert list(ts) == list(fixrouter.REMEDY_KINDS)
    assert ts == {k: sorted(v) for k, v in GOLDEN["fields"].items()}


@pytest.mark.parametrize("vec", GOLDEN["vectors"], ids=lambda v: v["name"])
def test_every_vector_is_what_choose_produces(vec):
    ctx = dict(vec["ctx"])
    if "history" in ctx:
        ctx["history"] = tuple(ctx["history"])
    if "packages" in ctx:
        ctx["packages"] = tuple(ctx["packages"])
    got = fixrouter.choose(vec["failure"], fixrouter.Ctx(**ctx))
    assert got == vec["remedy"]
    # 字段闭集：除 kind 外恰是 golden 登记的那几个
    assert sorted(set(got) - {"kind"}) == sorted(GOLDEN["fields"][got["kind"]])


def test_the_vectors_cover_every_kind():
    assert {v["remedy"]["kind"] for v in GOLDEN["vectors"]} == set(fixrouter.REMEDY_KINDS)
