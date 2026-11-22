"""第 1 步「读懂数据」的用例集与评分（ADR 0178 修订 2026-11-19）。用例在 `tests/fixtures/data_figure_cases/roles_cases.json`。

和 `data_figure_cases`（配方排序用例）分开：这里量的是**列判断带来的默认选择**——规则单独跑（A 臂）与规则 + 模型（B 臂）各给出的默认 x / y / 分组，
以及模型给的每列角色对不对。同一次 propose 的第一份响应就是 A 臂；B 臂是前端会做的那件事：等 `understanding` 就绪，把建议的 `answers` / `header_rows` 并进请求再来一次。

**用例字段**：`id` / `title` / `tags`；`tables`（`{文件名: CSV 文本}`，单表）；`hint`；`roles`（每列的标准角色）；`expect_default`（`{x: 列|null, y: [列], group: 列|null}`，
null = 不该有）；`model_answer`（录好的理想回答，假模型用）；可选 `header`（`expect_header_row` + `model_header_answer`）、`expect_cells`（应被采纳处理规则的列）；
`control: true` = 规则本来就对，用来核「模型不能把对的改坏」；可选 `also_ok`（科学上同样说得通的第二种默认）；可选 `tier`（`plain` 普通表 / `hard` 难例，留出集用）。

**用例集**：`main`（`roles_cases.json`，调提示词时用过——**已被调优污染，只能当回归，不能当结论**）、`holdout`（`roles_holdout.json`，标准答案先写定、不依据规则 / 模型输出调整、
提示词不许照它改）、`all`（两者合并）。

**「完成」的定义**（`completed`）：模型任务 `state == ready`，且并进建议之后的默认横轴 / 纵轴 / 分组与标准答案（或 `also_ok` 里任一份）三项**全部**一致。
没起任务（`off`）或失败（`failed`）一律不算完成。另报：`not_worse_than_rules`（没有出现「规则本来对、模型把它改错」）与 `rules_broken`（把规则本来对的改坏的次数 / 用例数，**任何用例集都算，不只 control**）。
"""

from __future__ import annotations

import json
import math
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from data_figure_cases import FakeTransport, reply_body  # noqa: I001

from tavotto.engine import (
    ai_data_figure as adf,
    ai_data_propose as P,
    ai_data_recipe as recipe_mod,
    ai_data_roles as R,
    ai_structured,
)

ROOT = Path(__file__).resolve().parents[2]
ROLE_CASES_FILE = ROOT / "tests" / "fixtures" / "data_figure_cases" / "roles_cases.json"
HOLDOUT_CASES_FILE = ROOT / "tests" / "fixtures" / "data_figure_cases" / "roles_holdout.json"
CASE_SETS = ("main", "holdout", "all")
PROJECT = "roles-bench-project"


def load_role_cases(path: Path = ROLE_CASES_FILE) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case id")
    return cases


def load_case_set(which: str = "main") -> list[dict]:
    """`main` / `holdout` / `all`（主用例集在前）。合并后 id 必须仍然唯一。"""
    if which not in CASE_SETS:
        raise ValueError(f"unknown case set: {which}")
    main = (
        [dict(c, set="main") for c in load_role_cases(ROLE_CASES_FILE)]
        if which in ("main", "all")
        else []
    )
    hold = (
        [dict(c, set="holdout") for c in load_role_cases(HOLDOUT_CASES_FILE)]
        if which in ("holdout", "all")
        else []
    )
    cases = main + hold
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("duplicate case id across sets")
    return cases


def check_case_shape(case: dict, root: Path) -> None:
    """一条用例自证（两个用例集共用）：表读得出、`roles` 与表的列逐一对上、角色在闭集里、标准答案与 `also_ok` 引用的列都存在、录好的回答自己过形状校验。"""
    from tavotto.engine import ai_data_figure as adf

    folder = materialize(case, root / "proj")
    entry = adf.profile_folder(root / "proj" / folder).tables[0]
    cols = set(entry.summary.columns)
    if case.get("header"):  # 嗅探的表头不是真表头：列名取真表头读出来的
        tt = adf.read_text_table(
            root / "proj" / folder / next(iter(case["tables"])),
            header_row=case["header"]["expect_header_row"],
        )
        cols = set(tt.summary.columns)
    cid = case["id"]
    assert set(case["roles"]) == cols, cid
    assert all(r in R.ROLES for r in case["roles"].values()), cid
    for exp in (case["expect_default"], *case.get("also_ok", ())):
        assert set(exp) == {"x", "y", "group"}, cid
        assert {c for c in (exp["x"], exp["group"], *exp["y"]) if c} <= cols, cid
    assert case.get("tier", "plain") in ("plain", "hard"), cid
    R.validate_roles_shape(case["model_answer"])


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


FAKE_MODES = ("good", "trap", "mixed")


def noop_answer(case: dict) -> dict:
    """「模型什么都没改」的回答：每列都说 `other`、三个选择都 `unsure`——规则默认原样保留（假模式 `trap` 用，量脚本本身）。"""
    return {
        "columns": [{"name": c, "role": "other"} for c in case["roles"]],
        "picks": {"y": "unsure", "x": "unsure", "group": "unsure"},
    }


def fake_answer(case: dict, mode: str, attempt: int = 0) -> dict:
    """假模式下这一次该回什么：`good` = 录好的理想回答；`trap` = 什么都不改；`mixed` = 偶数次 good、奇数次 trap（让「N 次里对几次」有变化）。"""
    if mode == "good" or (mode == "mixed" and attempt % 2 == 0):
        return case["model_answer"]
    return noop_answer(case)


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
    #: 这一次里每一次 `call_structured` 的结果（耗时、含命中缓存的输入 token）；`job.usage` 里没有这两项，从这里取
    calls: list = field(default_factory=list)


@contextmanager
def record_calls() -> Iterator[list]:
    """把 `ai_structured.call_structured` 包一层，记下每次调用的 `StructuredResult`（只记结果对象，不碰提示词 / 回复原文）。"""
    calls: list = []
    orig = ai_structured.call_structured

    def wrapped(**kw):
        res = orig(**kw)
        calls.append(res)
        return res

    ai_structured.call_structured = wrapped
    try:
        yield calls
    finally:
        ai_structured.call_structured = orig


@contextmanager
def isolated_cache() -> Iterator[Path]:
    """这一次运行用一个全新的空缓存目录，并清掉内存里的任务 / 单飞 / 60 s 负缓存：**重复跑同一条用例必须真的再问一次模型**，
    不能读到上一次写下的缓存，也不能被上一次失败的负缓存挡成 `off`。不读不写应用自己的缓存目录。"""
    with tempfile.TemporaryDirectory(prefix="roles-bench-cache-") as d:
        orig = R.cache_dir
        R.cache_dir = lambda: Path(d)  # type: ignore[assignment]
        R.reset_for_tests()
        try:
            yield Path(d)
        finally:
            R.cache_dir = orig  # type: ignore[assignment]
            R.reset_for_tests()


def run_case(
    case: dict,
    project_root: Path,
    *,
    conn: dict | None,
    post=None,
    request_budget: list | None = None,
    fresh: bool = False,
) -> RoleRun:
    """一次 propose（lazy）→（有连接就）同步等模型 → 并进建议再 propose 一次。`conn=None` = 规则单臂（不起任务）。
    `fresh=True`：本次用独立的空缓存、清负缓存（重复跑 / 跑分用）；默认沿用当前缓存（测试要看缓存行为）。"""
    if fresh:
        with isolated_cache():
            return run_case(
                case,
                project_root,
                conn=conn,
                post=post,
                request_budget=request_budget,
                fresh=False,
            )
    with record_calls() as calls:
        run = _run_case(case, project_root, conn, post, request_budget)
    run.calls = calls
    return run


def _run_case(
    case: dict,
    project_root: Path,
    conn: dict | None,
    post,
    request_budget: list | None,
) -> RoleRun:
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
    """`expect_default` 是标准答案；用例可另给 `also_ok`（同形状的列表）：科学上同样说得通的默认（如试次序号当横轴画随试次的变化），
    整份与其中一份一致也算对。"""
    if got is None:
        return {"x": False, "y": False, "group": False, "all": False}

    def one(exp: dict) -> dict:
        hit = {
            "x": got["x"] == exp["x"],
            "y": sorted(got["y"]) == sorted(exp["y"]),
            "group": got["group"] == exp["group"],
        }
        hit["all"] = all(hit.values())
        return hit

    first = one(case["expect_default"])
    if first["all"]:
        return first
    return next((h for h in map(one, case.get("also_ok", ())) if h["all"]), first)


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
        == (
            {}  # 只是确认嗅探的表头（单位行会让嗅探 unsure）：没有要采纳的改动
            if r.case["header"].get("confirm_only")
            else {next(iter(r.case["tables"])): r.case["header"]["expect_header_row"]}
        )
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
        "rules_broken": sum(
            1 for a, b in zip(arm_a, arm_b, strict=True) if a["all"] and not b["all"]
        ),
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


# --------------------------------------------------------------------------- 完成率 / 稳定性 / 耗时 / 花费（重复跑）
def completed(run: RoleRun) -> bool:
    """「完成」：模型任务 `ready` 且并进建议后的默认 x / y / 分组与标准答案（或 `also_ok`）全部一致。没起任务 / 失败都不算。"""
    return run.state == "ready" and default_hits(run.case, run.model)["all"]


def broke_rules(run: RoleRun) -> bool:
    """规则单臂本来全对、并进模型建议之后反而不对（模型没就绪 = 回到规则结果，不算改坏）。"""
    after = run.model if run.model is not None else run.rules
    return default_hits(run.case, run.rules)["all"] and not default_hits(run.case, after)["all"]


def percentile(values: Sequence[float], q: float) -> float | None:
    """最近邻（nearest-rank）分位数；空 = None。"""
    if not values:
        return None
    v = sorted(values)
    return v[max(0, math.ceil(q / 100 * len(v)) - 1)]


def _spread(values: Sequence[float]) -> dict:
    return {
        "n": len(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "max": max(values) if values else None,
    }


def tier_of(case: dict) -> str:
    return case.get("tier") or ("control" if case.get("control") else "main")


def summarize_repeats(runs: Sequence[RoleRun]) -> dict:
    """每条用例跑了 N 次（`runs` 可以是任意顺序的平铺列表）的统计：完成率、每条 N 次里对几次、N 次全对的用例占比、不比规则差、改坏规则本来对的、
    耗时分位、请求 / token / 花费（含每张表平均花费）。**一次运行 = 一张表一次「读懂数据」**，所以「每张表」是按运行数平均。"""
    by_case: dict[str, list[RoleRun]] = {}
    for r in runs:
        by_case.setdefault(r.case["id"], []).append(r)
    n_runs = len(runs) or 1
    done = [completed(r) for r in runs]
    per_case = {
        cid: {
            "tier": tier_of(rs[0].case),
            "runs": len(rs),
            "done": sum(completed(r) for r in rs),
            "rules_ok": default_hits(rs[0].case, rs[0].rules)["all"],  # 规则是确定的，各次相同
            "broke_rules": sum(broke_rules(r) for r in rs),
            "states": {
                st: sum(1 for r in rs if r.state == st) for st in sorted({r.state for r in rs})
            },
        }
        for cid, rs in by_case.items()
    }
    tiers: dict[str, dict] = {}
    for cid, row in per_case.items():
        t = tiers.setdefault(row["tier"], {"cases": 0, "runs": 0, "done": 0, "rules_ok_cases": 0})
        t["cases"] += 1
        t["runs"] += row["runs"]
        t["done"] += row["done"]
        t["rules_ok_cases"] += int(row["rules_ok"])
    for t in tiers.values():
        t["completion_rate"] = t["done"] / max(1, t["runs"])
    calls = [c for r in runs for c in r.calls]
    table_ms = [sum(c.elapsed_ms for c in r.calls) for r in runs if r.calls]
    usage = [r.usage for r in runs if r.usage]
    priced = [r.usage for r in runs if r.usage and "cost_usd" in r.usage]
    cost = sum(u["cost_usd"] for u in priced) if priced else None
    broken = sum(broke_rules(r) for r in runs)
    return {
        "definition": "完成 = 模型任务 ready，且默认 x / y / 分组与标准答案（或 also_ok）三项全部一致",
        "cache": "每次运行都用独立的空缓存目录并清负缓存：重复跑每次都真的再问一次模型；不读不写应用缓存",
        "cases": len(by_case),
        "runs": len(runs),
        "repeats": max((len(rs) for rs in by_case.values()), default=0),
        "completion_rate": sum(done) / n_runs,
        "rules_completion_rate": sum(default_hits(r.case, r.rules)["all"] for r in runs) / n_runs,
        "all_runs_correct_share": sum(1 for row in per_case.values() if row["done"] == row["runs"])
        / max(1, len(per_case)),
        "not_worse_than_rules_rate": (len(runs) - broken) / n_runs,
        "rules_broken_runs": broken,
        "rules_broken_cases": sorted(cid for cid, row in per_case.items() if row["broke_rules"]),
        "failures": {
            reason: sum(1 for r in runs if r.reason == reason)
            for reason in sorted({r.reason for r in runs if r.reason})
        },
        "not_ready_runs": sum(1 for r in runs if r.state != "ready"),
        "per_case": per_case,
        "tiers": tiers,
        "latency_ms": {
            "per_call": _spread([c.elapsed_ms for c in calls]),
            "per_table": _spread(table_ms),
        },
        "requests": sum(u.get("requests", 0) for u in usage),
        "requests_per_table": sum(u.get("requests", 0) for u in usage) / n_runs,
        "input_tokens": sum(u.get("input_tokens", 0) for u in usage),
        "output_tokens": sum(u.get("output_tokens", 0) for u in usage),
        "cached_input_tokens": sum(c.cached_input_tokens for c in calls),
        "usage_reported_all": all(u.get("usage_reported", True) for u in usage),
        "cost_usd": cost,
        "cost_per_table_usd": (cost / len(priced)) if priced else None,
        "cost_unknown_tables": len(usage) - len(priced),
    }


# --------------------------------------------------------------------------- 「规则已经很确定就不问模型」的门（只测量，不接进产品）
GATE_VERSIONS = ("strict", "medium", "loose")
GATE_DOC = {
    "strict": "严：恰好 1 个数值列、无日期列、至多 1 个分组列；无编号 / 混合格 / 自由文字列；没有「全为整数且取值 ≤12 个或每行不同」的数值列；表头无疑点；无提示语；规则不提问",
    "medium": "中：在「严」之上多允许 1 个横轴列（懒模式默认答 unsure）——表里恰好 1 个测量列 + 1 个横轴列，横轴列排第一、单调、（日期 / 非整数 / 列名带单位后缀），"
    "测量列不单调，且规则默认恰好是「第一列做横轴、另一列做纵轴」；其余同「严」",
    "loose": "松：「中」放行的，加上规则不提问（`resolve_roles` 直接落定）且没有「全为整数且取值 ≤12 个或每行不同」的数值列的（数值列个数不限）；编号 / 混合格 / 自由文字列、表头疑点、提示语一样不放行",
}


def gate_inputs(case: dict, root: Path) -> tuple[adf.TableEntry, adf.DataSummary, str]:
    """跑一条用例的门需要的三样：`profile_folder` 的表条目（无行数据）、带行数据的摘要、读表状态（`ok` / `unsure` / `unreadable`）。"""
    folder = materialize(case, root)
    entry = adf.profile_folder(root / folder).tables[0]
    path = root / folder / entry.path
    values = adf.inspect_data_source(path, entry.sheet, v2=True, allow_large=True)
    status = "ok"
    if path.suffix.lower() in adf.TEXT_SUFFIXES + adf.CSV_SUFFIXES + adf.TSV_SUFFIXES:
        status = adf.read_text_table(path).status
    return entry, values, status


def gate_facts(entry: adf.TableEntry, values: adf.DataSummary) -> dict:
    """门要看的结构事实（全部来自摘要与本地聚合，和发给模型的是同一批线索）。"""
    s = entry.summary
    ids = set(s.id_like)
    cols = list(s.columns)
    kind = {c: s.per_column[c].kind for c in cols}
    nums = [c for c in cols if c not in ids and recipe_mod.numeric_like(s.per_column[c])]
    dates = [c for c in cols if c not in ids and kind[c] == "date"]
    cats = [c for c in cols if c not in ids and c in s.groups]
    stats = {c: R.column_stats(values, c) for c in (*nums, *dates)}
    n_rows = len(values.rows)
    risky_int = [
        c
        for c in nums
        if stats[c].get("all_int")
        and (stats[c]["n_unique"] <= adf.MAX_GROUP_LEVELS or stats[c]["n_unique"] >= n_rows)
    ]
    return {
        "cols": cols,
        "nums": nums,
        "dates": dates,
        "cats": cats,
        "id_like": sorted(ids),
        "mixed": [c for c in cols if kind[c] == "mixed"],
        "free_text": [c for c in cols if kind[c] == "text" and c not in s.groups and c not in ids],
        "risky_int": risky_int,
        "stats": stats,
        "layout": s.layout,
    }


def rules_unambiguous(
    version: str,
    entry: adf.TableEntry,
    values: adf.DataSummary,
    hint: str | None,
    header_status: str = "ok",
) -> bool:
    """规则对这张表的默认（横轴 / 纵轴 / 分组）是否无歧义——可以不问模型（**只在评测里测量，没有接进产品**）。保守：任何一项拿不准都返回 False。版本见 `GATE_DOC`。"""
    if version not in GATE_VERSIONS:
        raise ValueError(version)
    if (hint or "").strip() or header_status != "ok":
        return False
    f = gate_facts(entry, values)
    if f["id_like"] or f["mixed"] or f["free_text"] or entry.summary.layout == "matrix":
        return False
    res = recipe_mod.resolve_roles(entry, hint, {})
    asked = not isinstance(res, recipe_mod.Roles)  # 规则要问（选值 / 选分组 / 选横轴）或画不了
    if version == "loose" and not asked and not f["risky_int"]:
        return True
    if len(f["cats"]) > 1:
        return False
    quant = [*f["nums"], *f["dates"]]
    if version == "strict":
        return not asked and len(f["nums"]) == 1 and not f["dates"] and not f["risky_int"]
    # medium
    if not f["nums"] or len(quant) > 2:
        return False
    if len(quant) == 1:
        return not asked and not f["risky_int"]
    axis, other = (c for c in f["cols"] if c in quant)
    if other not in f["nums"]:
        return False
    sa, so = f["stats"][axis], f["stats"][other]
    if sa.get("monotonic") not in ("increasing", "decreasing"):
        return False
    if so.get("monotonic") in ("increasing", "decreasing") or other in f["risky_int"]:
        return False
    if not (axis in f["dates"] or not sa.get("all_int") or recipe_mod._unit_of(axis) is not None):
        return False
    # 规则两个数值列会问「哪个是测的量」：懒模式下默认答 `unsure`（最后一个数值列）。默认必须恰好是「第一列做横轴、另一列做纵轴」
    lazy = _lazy_roles(entry, hint)
    return lazy is not None and tuple(lazy.ys) == (other,) and lazy.x_ord == axis


def _lazy_roles(entry: adf.TableEntry, hint: str | None) -> recipe_mod.Roles | None:
    """规则在懒模式（没人回答）下的默认：每个问题都答 `unsure`，一路往下走到落定。落不定 = None。"""
    picks: dict = {}
    for _ in range(4):
        res = recipe_mod.resolve_roles(entry, hint, picks)
        if isinstance(res, recipe_mod.Roles):
            return res
        key = R._QUESTION_KEY.get(getattr(res, "question_code", ""))  # noqa: SLF001
        if key is None or key in picks:
            return None
        picks[key] = "unsure"
    return None


def gate_measure(cases: Sequence[dict], root: Path) -> dict:
    """规则臂（不连模型）上量每个版本的门：跳过几条、被跳过的里规则实际错了几条（**硬指标：必须 0**）、被跳过的占比。按用例集（`main` / `holdout`）与合计分列。"""
    rows = []
    for c in cases:
        entry, values, status = gate_inputs(c, root / "gate")
        run = run_case(c, root / "rules", conn=None)
        rows.append(
            (
                c,
                default_hits(c, run.rules)["all"],
                {
                    v: rules_unambiguous(v, entry, values, c.get("hint"), status)
                    for v in GATE_VERSIONS
                },
            )
        )
    out: dict = {"versions": {}, "rules_right": {}}
    sets = sorted({c.get("set", "main") for c, _, _ in rows}) if rows else []
    tiers = sorted(
        {f"{c['set']}-{c['tier']}" for c, _, _ in rows if c.get("tier") and c.get("set")}
    )
    for scope in (*sets, *tiers, "all"):

        def in_scope(c, scope=scope) -> bool:
            if scope == "all":
                return True
            return scope == c.get("set", "main") or scope == f"{c.get('set')}-{c.get('tier')}"

        sub = [(c, ok, g) for c, ok, g in rows if in_scope(c)]
        out["rules_right"][scope] = {"cases": len(sub), "rules_right": sum(ok for _, ok, _ in sub)}
        for v in GATE_VERSIONS:
            skipped = [(c, ok) for c, ok, g in sub if g[v]]
            out["versions"].setdefault(v, {})[scope] = {
                "cases": len(sub),
                "skipped": len(skipped),
                "skipped_share": len(skipped) / max(1, len(sub)),
                "skipped_but_rules_wrong": sum(1 for _, ok in skipped if not ok),
                "wrong_ids": [c["id"] for c, ok in skipped if not ok],
                "skipped_ids": [c["id"] for c, _ in skipped],
            }
    return out
