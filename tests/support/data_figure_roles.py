"""第 1 步「读懂数据」的用例集与评分（ADR 0178 修订 2026-11-19）。用例在 `tests/fixtures/data_figure_cases/roles_cases.json`。

和 `data_figure_cases`（配方排序用例）分开：这里量的是**列判断带来的默认选择**——规则单独跑（A 臂）与规则 + 模型（B 臂）各给出的默认 x / y / 分组，
以及模型给的每列角色对不对。同一次 propose 的第一份响应就是 A 臂；B 臂是前端会做的那件事：等 `understanding` 就绪，把建议的 `answers` / `header_rows` 并进请求再来一次。

**用例字段**：`id` / `title` / `tags`；`tables`（`{文件名: CSV 文本}`，单表）；`hint`；`roles`（每列的标准角色）；`expect_default`（`{x: 列|null, y: [列], group: 列|null}`，
null = 不该有）；`model_answer`（录好的理想回答，假模型用）；可选 `header`（`expect_header_row` + `model_header_answer`）、`expect_cells`（应被采纳处理规则的列）；
`control: true` = 规则本来就对，用来核「模型不能把对的改坏」。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from data_figure_cases import FakeTransport, reply_body  # noqa: I001

from tavotto.engine import ai_data_propose as P, ai_data_roles as R

ROOT = Path(__file__).resolve().parents[2]
ROLE_CASES_FILE = ROOT / "tests" / "fixtures" / "data_figure_cases" / "roles_cases.json"
PROJECT = "roles-bench-project"


def load_role_cases(path: Path = ROLE_CASES_FILE) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case id")
    return cases


def materialize(case: dict, project_root: Path) -> str:
    folder = project_root / case["id"]
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in case["tables"].items():
        (folder / name).write_text(text, encoding="utf-8")
    return case["id"]


def scripted(case: dict, wire: str = "anthropic", *, answer: dict | None = None) -> FakeTransport:
    """假传输：有表头问题的先回表头答案，再回列角色答案；`answer` 换掉列角色答案（降级测试用）。"""
    script: list = []
    if case.get("header"):
        script.append(reply_body(wire, json.dumps(case["header"]["model_header_answer"])))
    script.append(
        reply_body(
            wire,
            json.dumps(answer if answer is not None else case["model_answer"], ensure_ascii=False),
        )
    )
    return FakeTransport(script)


def defaults_of(resp: dict) -> dict | None:
    roles = resp.get("roles")
    if not roles:
        return None
    return {"x": roles.get("x"), "y": list(roles.get("y") or []), "group": roles.get("group")}


@dataclass
class RoleRun:
    case: dict
    #: A 臂：规则单独的默认（第一份响应）；B 臂：并进模型建议之后的默认
    rules: dict | None
    model: dict | None
    #: 模型状态：`off`（没起任务）/ `ready` / `failed`
    state: str
    payload: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    reason: str = ""


def run_case(
    case: dict,
    project_root: Path,
    *,
    conn: dict | None,
    post=None,
    request_budget: list | None = None,
) -> RoleRun:
    """一次 propose（lazy）→（有连接就）同步等模型 → 并进建议再 propose 一次。`conn=None` = 规则单臂（不起任务）。"""
    folder = materialize(case, project_root)
    extra = {"post": post} if post is not None else {}
    if request_budget is not None:
        extra["request_budget"] = request_budget
    old = R.set_overrides(conn=conn, sync=True, **extra)
    try:
        first = P.propose(
            project_root, project=PROJECT, folder=folder, hint=case.get("hint"), render="lazy"
        )
        rules = defaults_of(first)
        und = first.get("understanding")
        if und is None:
            return RoleRun(case, rules, None, "off")
        usage = R.usage_of(PROJECT, und["id"])
        if und["state"] != "ready":
            return RoleRun(
                case, rules, None, und["state"], reason=und.get("reason", ""), usage=usage
            )
        answers = und.get("answers") or {}
        header_rows = und.get("header_rows") or {}
        if not answers and not header_rows:
            return RoleRun(case, rules, rules, "ready", payload=und, usage=usage)
        second = P.propose(
            project_root,
            project=PROJECT,
            folder=folder,
            hint=case.get("hint"),
            render="lazy",
            answers=answers,
            header_rows=header_rows,
        )
        return RoleRun(case, rules, defaults_of(second), "ready", payload=und, usage=usage)
    finally:
        R.restore_overrides(old)


# --------------------------------------------------------------------------- 评分
def default_hits(case: dict, got: dict | None) -> dict:
    exp = case["expect_default"]
    if got is None:
        return {"x": False, "y": False, "group": False, "all": False}
    hit = {
        "x": got["x"] == exp["x"],
        "y": sorted(got["y"]) == sorted(exp["y"]),
        "group": got["group"] == exp["group"],
    }
    hit["all"] = all(hit.values())
    return hit


def role_accuracy(case: dict, roles: Sequence[dict]) -> tuple[int, int, int, int]:
    """→ `(全部列对的个数, 有标准角色的列数, 关键列对的个数, 关键列数)`；关键列 = 期望默认里出现的列。没给角色的列算不对。"""
    truth = case["roles"]
    got = {r["column"]: r["role"] for r in roles}
    crit = {
        c
        for c in (
            case["expect_default"]["x"],
            case["expect_default"]["group"],
            *case["expect_default"]["y"],
        )
        if c
    }
    right = sum(1 for c, r in truth.items() if got.get(c) == r)
    crit_right = sum(1 for c in crit if got.get(c) == truth.get(c))
    return right, len(truth), crit_right, len(crit)


def summarize(runs: Sequence[RoleRun]) -> dict:
    n = len(runs) or 1
    modeled = [r for r in runs if r.state == "ready"]
    arm_a = [default_hits(r.case, r.rules) for r in runs]
    arm_b = [default_hits(r.case, r.model if r.model is not None else r.rules) for r in runs]
    controls = [i for i, r in enumerate(runs) if r.case.get("control")]
    acc = [role_accuracy(r.case, r.payload.get("roles", [])) for r in modeled]
    header_cases = [r for r in runs if r.case.get("header")]
    header_ok = sum(
        1
        for r in header_cases
        if r.state == "ready"
        and r.payload.get("header_rows")
        == {next(iter(r.case["tables"])): r.case["header"]["expect_header_row"]}
    )
    cell_cases = [r for r in runs if r.case.get("expect_cells")]
    cells_ok = sum(
        1
        for r in cell_cases
        if r.state == "ready"
        and all(
            c in (r.payload.get("answers", {}).get("cells") or {}) for c in r.case["expect_cells"]
        )
    )
    usage = [r.usage for r in runs if r.usage]
    costs = [u["cost_usd"] for u in usage if "cost_usd" in u]
    return {
        "cases": len(runs),
        "model_ready": len(modeled),
        "arm_rules": {k: sum(h[k] for h in arm_a) / n for k in ("x", "y", "group", "all")},
        "arm_model": {k: sum(h[k] for h in arm_b) / n for k in ("x", "y", "group", "all")},
        "control_regressions": sum(1 for i in controls if arm_a[i]["all"] and not arm_b[i]["all"]),
        "role_accuracy_all": (sum(a[0] for a in acc) / max(1, sum(a[1] for a in acc)))
        if acc
        else None,
        "role_accuracy_critical": (sum(a[2] for a in acc) / max(1, sum(a[3] for a in acc)))
        if acc
        else None,
        "header_ok": f"{header_ok}/{len(header_cases)}",
        "cells_ok": f"{cells_ok}/{len(cell_cases)}",
        "failures": {
            reason: sum(1 for r in runs if r.reason == reason)
            for reason in sorted({r.reason for r in runs if r.reason})
        },
        "requests": sum(u.get("requests", 0) for u in usage),
        "input_tokens": sum(u.get("input_tokens", 0) for u in usage),
        "output_tokens": sum(u.get("output_tokens", 0) for u in usage),
        "usage_reported_all": all(u.get("usage_reported", True) for u in usage),
        "cost_usd": sum(costs) if costs else None,
        "cost_unknown_calls": len(usage) - len(costs),
    }
