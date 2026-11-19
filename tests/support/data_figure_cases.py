"""「从数据到图」模型调用的固定用例集：用例格式、判定、评分、假传输（ADR 0178 修订 2026-11-19）。

用例在 `tests/fixtures/data_figure_cases/cases.json`（内联小表 + 可选提问 + 应排第一的画法 + 绝不该出现的画法），与 FigureBench 的改图场景**分开**
（那份的场景数在 `06-benchmark-plan.md` 里有固定口径）。同一份用例两处用：

* pytest（`tests/test_ai_data_model.py`）：用录好的 / 手写的假回答经**真的**协议层（`wires` 的 build / parse / 状态码分类）跑通整条链，不发任何网络请求；
* 手动入口 `scripts/dev/data_figure_bench.py`：默认同样用假回答自测；`--real` 才对真模型跑（要显式开关与调用次数上限）。

**用例字段**

| 字段 | 含义 |
| --- | --- |
| `id` / `title` / `tags` | 标识；`tags` 只是分类 |
| `tables` | `{文件名: CSV 文本}`，写进临时目录后走真的 `profile_folder` |
| `hint` | 可选的一句用户提问（null = 没问） |
| `confirmed` | 可选，`{design, unit_col}`：用户确认过的事实；缺省 = 什么都没确认 |
| `first` | 判定器：排在第一的画法必须满足 |
| `never` | 判定器列表：任何一份（排名不限）满足其中一条就是违规 |
| `reference` | 一个好回答（配方列表）：必须全部通过 `validate_recipe`、第一份满足 `first`、都不满足 `never`——用例自证 |
| `trap` | 一份「踩坑」的配方：满足某条 `never`（用例自证判定器抓得到） |

**判定器**是 `{键: 值}`，所有键都要成立（与）；值是列表的键表示「任一」：`family`、`order`、`scale`、`coord`（配方字段取这些值之一）；`marks_any` / `marks_all` / `marks_none`
（所有层的图元集合）；`x` / `group` / `facet`（等于该列名，null = 没填）；`y`（纵轴列集合恰好等于）/ `y_has`（含其中任一列）/ `y_all`（含其中全部列）；`unit_set` / `paired`（布尔）。
未知键抛错，不静默当成立。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tavotto.engine import ai_data_figure as adf, ai_data_model, ai_data_recipe as r, ai_structured
from tavotto.engine.agent_runner.wires import RunnerFailure

ROOT = Path(__file__).resolve().parents[2]
CASES_FILE = ROOT / "tests" / "fixtures" / "data_figure_cases" / "cases.json"

MATCHER_KEYS = frozenset(
    {
        "family",
        "order",
        "scale",
        "coord",
        "marks_any",
        "marks_all",
        "marks_none",
        "x",
        "group",
        "facet",
        "y",
        "y_has",
        "y_all",
        "unit_set",
        "paired",
    }
)


# --------------------------------------------------------------------------- 用例
def load_cases(path: Path = CASES_FILE) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data["cases"]
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case id")
    return cases


def materialize(case: dict, root: Path) -> Path:
    """把用例的表写进 `root/<id>/`（调用方给隔离的临时目录）→ 该目录。"""
    folder = root / case["id"]
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in case["tables"].items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


def confirmed_of(case: dict) -> r.Confirmed:
    return r.Confirmed(**(case.get("confirmed") or {}))


def inputs_of(case: dict, folder: Path) -> tuple[adf.FolderProfile, adf.DataSummary | None]:
    """用例 → `(文件夹摘要, 带行数据的摘要)`。取值类规则只在用例只有一张表时判（多张表时选哪张是后续步骤的事）。"""
    profile = adf.profile_folder(folder)
    values = None
    if len(case["tables"]) == 1:
        (name,) = case["tables"]
        values = adf.inspect_data_source(folder / name, v2=True)
    return profile, values


# --------------------------------------------------------------------------- 判定
def _any_of(want, have) -> bool:
    return have in want if isinstance(want, list) else have == want


def matches(recipe: r.FigureRecipe | dict, matcher: dict) -> bool:
    rec = recipe if isinstance(recipe, r.FigureRecipe) else r.FigureRecipe.from_dict(recipe)
    unknown = set(matcher) - MATCHER_KEYS
    if unknown:
        raise ValueError(f"unknown matcher key: {sorted(unknown)}")
    marks = {layer.mark for layer in rec.layers}
    ys = set(rec.y)
    for key, want in matcher.items():
        if key in ("family", "order", "scale", "coord"):
            ok = _any_of(want, getattr(rec, key))
        elif key == "marks_any":
            ok = bool(marks & set(want))
        elif key == "marks_all":
            ok = set(want) <= marks
        elif key == "marks_none":
            ok = not marks & set(want)
        elif key in ("x", "group", "facet"):
            ok = getattr(rec, key) == want
        elif key == "y":
            ok = ys == set(want)
        elif key == "y_has":
            ok = bool(ys & set(want))
        elif key == "y_all":
            ok = set(want) <= ys
        elif key == "unit_set":
            ok = (rec.unit is not None) == want
        else:  # paired
            ok = rec.paired == want
        if not ok:
            return False
    return True


# --------------------------------------------------------------------------- 评分
OUTCOMES = ("model_failed", "empty", "scored")


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    #: model_failed = 模型调用没给可用回复（调用方会退回规则结果）；empty = 回复可用但没有一份配方通过校验；scored = 有配方可评
    outcome: str
    top1: bool = False
    any_hit: bool = False
    #: 通过校验的配方里满足任一 `never` 的份数
    violations: int = 0
    shown: int = 0

    @property
    def passed(self) -> bool:
        return self.outcome == "scored" and self.top1 and self.violations == 0

    @property
    def fell_back(self) -> bool:
        return self.outcome != "scored"


def score(case: dict, recipes: Sequence[r.FigureRecipe], *, call_ok: bool = True) -> CaseScore:
    """已过校验、已排好序的配方 → 评分。`call_ok=False` = 模型调用失败。"""
    if not call_ok:
        return CaseScore(case["id"], "model_failed")
    if not recipes:
        return CaseScore(case["id"], "empty")
    first = case["first"]
    never = case["never"]
    return CaseScore(
        case["id"],
        "scored",
        top1=matches(recipes[0], first),
        any_hit=any(matches(x, first) for x in recipes),
        violations=sum(1 for x in recipes if any(matches(x, m) for m in never)),
        shown=len(recipes),
    )


@dataclass(frozen=True)
class CaseRun:
    case: dict
    result: ai_data_model.ModelRecipes
    score: CaseScore


def run_case(case: dict, root: Path, **call_kwargs) -> CaseRun:
    """真的 `profile_folder` + `propose_recipes`（`conn` / `post` / `limits` 由调用方给）→ 评分。"""
    folder = materialize(case, root)
    profile, values = inputs_of(case, folder)
    res = ai_data_model.propose_recipes(
        profile,
        hint=case.get("hint"),
        confirmed=confirmed_of(case),
        values=values,
        **call_kwargs,
    )
    return CaseRun(case, res, score(case, res.recipes, call_ok=res.ok))


def summarize(runs: Sequence[CaseRun]) -> dict:
    """命中率与用量 / 花费估算。分母都是用例总数（退回规则结果的也算「没命中」，不并出去）。"""
    n = len(runs) or 1
    scores = [x.score for x in runs]
    calls = [x.result.call for x in runs]
    costs = [c.cost_usd for c in calls]
    known = [c for c in costs if c is not None]
    return {
        "cases": len(runs),
        "top1_rate": sum(s.top1 for s in scores) / n,
        "any_hit_rate": sum(s.any_hit for s in scores) / n,
        "violation_rate": sum(s.violations > 0 for s in scores) / n,
        "pass_rate": sum(s.passed for s in scores) / n,
        "fallback_rate": sum(s.fell_back for s in scores) / n,
        "failures": {
            reason: sum(1 for c in calls if c.reason == reason) for reason in ai_structured.REASONS
        },
        "dropped_recipes": sum(x.result.dropped_count for x in runs),
        "requests": sum(c.requests for c in calls),
        "attempts": sum(c.attempts for c in calls),
        "input_tokens": sum(c.input_tokens for c in calls),
        "output_tokens": sum(c.output_tokens for c in calls),
        "usage_reported_all": all(c.usage_reported for c in calls),
        "cost_usd": sum(known) if known else None,
        "cost_unknown_calls": len(costs) - len(known),
    }


# --------------------------------------------------------------------------- 假传输（真协议层，不联网）
FAKE_WIRES = ("anthropic", "openai-chat", "openai-responses")


def fake_conn(wire: str = "anthropic") -> dict:
    """`builtin_conn` 形状的假连接。主机是保留域名 `.invalid`：就算有代码绕过假传输去联网也解析不出来。"""
    base = {
        "anthropic": "https://fake.invalid",
        "openai-chat": "https://fake.invalid/v1",
        "openai-responses": "https://fake.invalid/v1",
    }[wire]
    return {
        "wire": wire,
        "base_url": base,
        "api_key": "sk-test-NOT-A-REAL-KEY",
        "model": "fake-model",
        "auth": "key",
        "provider_id": "fake",
    }


def reply_body(wire: str, text: str, *, in_tokens: int = 1000, out_tokens: int = 200) -> dict:
    """某协议下「模型说了一段话」的 2xx 回复体（字段按 `wires.py` 的解析写，用量带上）。"""
    if wire == "anthropic":
        return {
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": in_tokens, "output_tokens": out_tokens},
        }
    if wire == "openai-chat":
        return {
            "choices": [
                {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": in_tokens, "completion_tokens": out_tokens},
        }
    return {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": in_tokens, "output_tokens": out_tokens},
    }


def refusal_body(wire: str) -> dict:
    """服务端拒答（各协议的写法不同）。"""
    if wire == "anthropic":
        return {
            "content": [],
            "stop_reason": "refusal",
            "usage": {"input_tokens": 900, "output_tokens": 0},
        }
    if wire == "openai-chat":
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": None},
                    "finish_reason": "content_filter",
                }
            ],
            "usage": {"prompt_tokens": 900, "completion_tokens": 0},
        }
    return {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}],
        "usage": {"input_tokens": 900, "output_tokens": 0},
    }


def truncated_body(wire: str, text: str = '{"recipes": [') -> dict:
    """输出撞到最长输出被截断。"""
    if wire == "anthropic":
        return {
            "content": [{"type": "text", "text": text}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 900, "output_tokens": 3000},
        }
    if wire == "openai-chat":
        return {
            "choices": [
                {"message": {"role": "assistant", "content": text}, "finish_reason": "length"}
            ],
            "usage": {"prompt_tokens": 900, "completion_tokens": 3000},
        }
    return {
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 900, "output_tokens": 3000},
    }


@dataclass
class FakeTransport:
    """`wires.post_json` 的替身：按剧本一次回一条，记下每次收到的请求（`calls`）。

    剧本条目：`dict` = 2xx 的 JSON 回复体；`bytes` = 2xx 的原始体（例如网页）；`int` = 该状态码的空体；`Exception` = 抛出它（传输层失败）；
    `Callable` = 现算（收到请求体）。剧本用完再被调用 = 测试写错了，抛 `AssertionError`。"""

    script: list = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def __call__(self, url, headers, body, *, timeout, max_bytes=None):
        self.calls.append({"url": url, "body": body, "timeout": timeout})
        if not self.script:
            raise AssertionError("fake transport: script exhausted")
        item = self.script.pop(0)
        if callable(item) and not isinstance(item, type):
            item = item(body)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, bool) or isinstance(item, int):
            return item, {}, b"{}"
        if isinstance(item, bytes):
            return 200, {}, item
        return 200, {}, json.dumps(item).encode("utf-8")


def good_answer(case: dict) -> str:
    """用例里录好的好回答（`reference`）→ 模型会说的那段 JSON 文字。"""
    return json.dumps({"recipes": case["reference"]}, ensure_ascii=False)


def trap_answer(case: dict) -> str:
    """踩坑的回答：先给 `trap`，再给 `reference`（排名第一的是坑）。"""
    return json.dumps({"recipes": [case["trap"], *case["reference"]]}, ensure_ascii=False)


def scripted_for(
    case: dict, wire: str, mode: str = "good", *, in_tokens: int = 4000, out_tokens: int = 300
) -> FakeTransport:
    """用例 + 假回答模式 → 假传输。`mode`：`good` / `trap` / `refusal` / `not_json` / `timeout` / `unknown_column`。"""
    if mode == "good":
        script: list = [
            reply_body(wire, good_answer(case), in_tokens=in_tokens, out_tokens=out_tokens)
        ]
    elif mode == "trap":
        script = [reply_body(wire, trap_answer(case), in_tokens=in_tokens, out_tokens=out_tokens)]
    elif mode == "refusal":
        script = [refusal_body(wire)]
    elif mode == "not_json":
        script = [reply_body(wire, "抱歉，我不能这样回答。"), reply_body(wire, "仍然不是 JSON")]
    elif mode == "timeout":
        script = [RunnerFailure("network", "TimeoutError", retryable=True)]
    elif mode == "unknown_column":
        bad = json.loads(json.dumps(case["reference"]))
        for rec in bad:
            rec["y"] = ["no_such_column"]
        script = [reply_body(wire, json.dumps({"recipes": bad}, ensure_ascii=False))]
    else:
        raise ValueError(mode)
    return FakeTransport(script)
