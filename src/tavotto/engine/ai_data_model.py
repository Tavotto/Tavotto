"""「从数据到图」的模型调用点（ADR 0178 修订 2026-11-19「接模型的地基」）：数据文件夹的受控摘要 → 模型 → 一组**过了闭集校验**的配方。

**本步只有函数，没有调用方**：`propose` 与任何端点都不调用它（开关打开时用户可见行为不变）。后续步骤（读懂每列、重排 / 替换候选）从这里接。

流水：`build_request`（摘要 + `data_figure_guidance()` + 本次调用的补充 + 可选的一句用户提示）→ `ai_structured.call_structured`（无工具、单轮、
只要 JSON）→ 形状校验（`{"recipes": [对象, ...]}`）→ 逐条 `FigureRecipe.from_dict`（未知键 / 未知取值 / 层数 / `why` 不合格当场丢）→ 不是现在有模板能画的族的丢
→ `ai_data_recipe.enforce_order`（`validate_recipe`：列不存在 / 类型对不上 / 确认过的事实对不上 / 取值类规则；去重；柱不排在原始点前面）。
丢弃只记**原序号 + 拒绝码**（`REJECTIONS` 的闭集），不记配方原文、不记模型的话。

不可信输入：表里的文字（列名、单元格样本）只作为 JSON 里的字符串进 `<data>` 块，`<` 一律转义成 `\\u003c`，所以里面写什么都伪造不出块的结尾；
提示词里明说它是数据不是指令（`ai_prompt_pack.data_figure_oneshot_notes`）。模型只能交闭集配方，配方里没有任何自由文本会进脚本（ADR 0178 §边界）。

输入预算：系统提示约 18 KB 固定；摘要超出 `Limits.max_input_chars` 时按序收缩——每列文字样本 20 → 8 → 3 → 0 条，再从末尾起少发表（至少留 1 张）。
仍超就交给 `call_structured` 报 `over_budget`。被收缩掉的在 `ModelRecipes.tables_omitted` / `sample_cap` 里留数，不静默。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import ai_data_figure as adf, ai_data_recipe as r, ai_prompt_pack, ai_structured

#: 摘要里最多发几张表（同选表选择题的上限）；每列文字样本不超过 `adf.TEXT_SAMPLES` 条、每条不超过 `MAX_SAMPLE_CHARS` 字
MAX_PROMPT_TABLES = r.MAX_TABLE_OPTIONS
MAX_SAMPLE_CHARS = 60
MAX_NAME_CHARS = r.MAX_NAME
#: 用户那一句提示的上限（字）
MAX_HINT_CHARS = 200
#: 模型一次最多交几份配方；多出来的不解析、记 `too_many_candidates`
MAX_RAW_RECIPES = 8
#: 文字样本的收缩阶梯
SAMPLE_CAPS = (adf.TEXT_SAMPLES, 8, 3, 0)

_CONTROL = re.compile(r"[\x00-\x1f\x7f  ]+")


def _clip(text: object, limit: int) -> str:
    """不可信的文字 → 单行、去控制字符、截断。"""
    return _CONTROL.sub(" ", str(text)).strip()[:limit]


def _column(name: str, c: adf.ColumnSummary, sample_cap: int) -> dict:
    out: dict = {
        "name": _clip(name, MAX_NAME_CHARS),
        "kind": c.kind,
        "numeric": c.numeric,
        "empty": c.empty,
        "nan": c.nan,
        "text_count": c.text_count,
    }
    if sample_cap:
        out["text_samples"] = list(
            dict.fromkeys(_clip(t, MAX_SAMPLE_CHARS) for t in c.text[:sample_cap])
        )  # 摘要里的样本有重复；去重不丢信息
    return out


def _table(t: adf.TableEntry, sample_cap: int) -> dict:
    s = t.summary
    out: dict = {
        "table": _clip(t.path, MAX_NAME_CHARS),
        "sheet": None if t.sheet is None else _clip(t.sheet, MAX_NAME_CHARS),
        "rows": s.row_count if s.row_count is not None else len(s.rows),
        "layout": s.layout,
        "groups": {_clip(k, MAX_NAME_CHARS): v for k, v in s.groups.items()},
        "id_like": [_clip(c, MAX_NAME_CHARS) for c in s.id_like],
        "repeats": s.repeats,
        "columns": [
            _column(n, s.per_column[n], sample_cap) for n in s.columns if n in s.per_column
        ],
    }
    return out


def _dumps(obj: object) -> str:
    # `<` 转义：表里的文字写 `</data>` 也伪造不出块的结尾（转义后仍是合法 JSON）
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def _user_text(tables: list[dict], hint: str | None) -> str:
    parts = [
        "下面是用户数据文件夹里各张表的摘要。请按系统提示给出备选画法。",
        "<data>",
        _dumps({"tables": tables}),
        "</data>",
    ]
    clean = _clip(hint, MAX_HINT_CHARS) if hint else ""
    if clean:
        parts += ["<user_hint>", _dumps(clean), "</user_hint>"]
    return "\n".join(parts)


@dataclass(frozen=True)
class Request:
    system: str
    user: str
    #: 实际发出的表数 / 被收缩掉的表数 / 文字样本每列条数上限（`SAMPLE_CAPS` 的一档）
    tables_sent: int
    tables_omitted: int
    sample_cap: int


def system_prompt() -> str:
    """固定的系统提示（稳定、吃提示缓存）：选图准则 + 本次调用的补充。"""
    return ai_prompt_pack.data_figure_guidance() + "\n" + ai_prompt_pack.data_figure_oneshot_notes()


def build_request(
    profile: adf.FolderProfile,
    *,
    hint: str | None = None,
    max_input_chars: int = ai_structured.DEFAULT_LIMITS.max_input_chars,
) -> Request:
    """受控摘要 → 请求文字。摘要只含 路径 / 工作表 / 行数 / 布局 / 分组 / 编号线索 / 每列的名字、类型、计数与少量文字样本——
    不含行数据、哈希、大小、绝对路径。隐藏工作表不发。"""
    system = system_prompt()
    entries = [t for t in profile.tables if not t.hidden][:MAX_PROMPT_TABLES]
    skipped_by_cap = max(0, len([t for t in profile.tables if not t.hidden]) - len(entries))
    chosen_cap = SAMPLE_CAPS[-1]
    for cap in SAMPLE_CAPS:
        chosen_cap = cap
        tables = [_table(t, cap) for t in entries]
        if len(system) + len(_user_text(tables, hint)) <= max_input_chars:
            return Request(system, _user_text(tables, hint), len(tables), skipped_by_cap, cap)
    tables = [_table(t, chosen_cap) for t in entries]
    while len(tables) > 1 and len(system) + len(_user_text(tables, hint)) > max_input_chars:
        tables.pop()
    return Request(
        system,
        _user_text(tables, hint),
        len(tables),
        skipped_by_cap + len(entries) - len(tables),
        chosen_cap,
    )


# --------------------------------------------------------------------------- 回复的形状
def validate_shape(obj: dict) -> dict:
    """`{"recipes": [对象, ...]}`；别的键、非数组、数组里有非对象 → `ValueError`。数组可以为空（模型认为没有合格画法）；
    只取前 `MAX_RAW_RECIPES` 条，多出的个数记在 `extra`。每条配方内部的对错留给 `FigureRecipe.from_dict`（一条坏了不连累别的）。"""
    if set(obj) != {"recipes"} or not isinstance(obj["recipes"], list):
        raise ValueError("shape")
    items = obj["recipes"]
    if not all(isinstance(x, dict) for x in items):
        raise ValueError("shape")
    return {"recipes": items[:MAX_RAW_RECIPES], "extra": max(0, len(items) - MAX_RAW_RECIPES)}


@dataclass(frozen=True)
class DroppedRecipe:
    """被丢的一份：模型给的第几份（0 起）+ 拒绝码（`ai_data_recipe.REJECTIONS` 的键；`not_renderable` 是本模块另加的一个码）。"""

    index: int
    codes: tuple[str, ...]


NOT_RENDERABLE = "not_renderable"


@dataclass(frozen=True)
class ModelRecipes:
    """`call.ok` 为假 = 模型没给出可用回复（`call.reason` 是 `ai_structured.REASONS` 的闭集码），`recipes` 为空，调用方退回规则结果。
    `ok` 为真时 `recipes` 可以为空（模型没有合格画法 / 全被丢弃）——调用方同样退回。`recipes` 已按 `enforce_order` 排好。"""

    call: ai_structured.StructuredResult
    recipes: tuple[r.FigureRecipe, ...] = ()
    proposed: int = 0
    dropped: tuple[DroppedRecipe, ...] = ()
    #: 实际发出的表数 / 因预算或上限没发的表数 / 文字样本每列条数
    tables_sent: int = 0
    tables_omitted: int = 0
    sample_cap: int = adf.TEXT_SAMPLES
    moves: tuple[r.Problem, ...] = field(default=())

    @property
    def dropped_count(self) -> int:
        return len(self.dropped)

    @property
    def ok(self) -> bool:
        return self.call.ok


def parse_recipes(
    profile: adf.FolderProfile,
    value: dict,
    *,
    confirmed: r.Confirmed,
    values: adf.DataSummary | None = None,
) -> tuple[tuple[r.FigureRecipe, ...], tuple[DroppedRecipe, ...], tuple[r.Problem, ...]]:
    """`validate_shape` 之后的 dict → `(通过的配方, 丢弃记录, 被调序的记录)`。逐条：`from_dict` 不过 → 丢；没有模板能画的族 → 丢；
    其余交 `enforce_order`（校验 + 去重 + 柱不排在原始点前）。丢弃记录的序号都是模型原序号。纯函数，不联网。"""
    dropped: list[DroppedRecipe] = []
    parsed: list[tuple[int, r.FigureRecipe]] = []
    for i, raw in enumerate(value["recipes"]):
        try:
            rec = r.FigureRecipe.from_dict(raw)
        except r.RecipeError as exc:
            dropped.append(DroppedRecipe(i, (exc.code,)))
            continue
        if rec.err is not None or rec.err_kind is not None:
            # 现成误差列只由规则按列名配出来（`default_candidates`）：模型写不出这两个键——它说的「这是 SD」没有任何依据，绝不替它当真
            dropped.append(DroppedRecipe(i, ("recipe_unknown_key",)))
            continue
        if not r.renderable(rec.family):
            dropped.append(DroppedRecipe(i, (NOT_RENDERABLE,)))
            continue
        parsed.append((i, rec))
    for k in range(int(value.get("extra", 0))):
        dropped.append(DroppedRecipe(MAX_RAW_RECIPES + k, ("too_many_candidates",)))
    result = r.enforce_order(
        profile, [rec for _, rec in parsed], confirmed=confirmed, values=values
    )
    for d in result.dropped:
        dropped.append(DroppedRecipe(parsed[d.index][0], tuple(p.code for p in d.problems)))
    dropped.sort(key=lambda d: d.index)
    return result.kept, tuple(dropped), result.moves


def propose_recipes(
    profile: adf.FolderProfile,
    *,
    hint: str | None = None,
    confirmed: r.Confirmed | None = None,
    values: adf.DataSummary | None = None,
    limits: ai_structured.Limits = ai_structured.DEFAULT_LIMITS,
    conn: dict | None = None,
    post=None,
    max_requests: int | None = None,
) -> ModelRecipes:
    """向模型要一组配方（**不接进 `propose`**）。永不抛：模型调用的任何失败都在 `ModelRecipes.call.reason` 里，`recipes` 为空。

    `confirmed` 默认「什么都没确认」（模型不许凭列名当个体编号 / 配对）；`values` 给了才判取值类规则（见 `validate_recipe`）。
    `conn` / `post` 只给测试与手动基准用（默认：当前连接 + 真传输）。"""
    try:
        req = build_request(profile, hint=hint, max_input_chars=limits.max_input_chars)
        call = ai_structured.call_structured(
            system=req.system,
            user=req.user,
            validate=validate_shape,
            limits=limits,
            conn=conn,
            post=post,
            max_requests=max_requests,
        )
        base = {
            "tables_sent": req.tables_sent,
            "tables_omitted": req.tables_omitted,
            "sample_cap": req.sample_cap,
        }
        if not call.ok or call.value is None:
            return ModelRecipes(call, **base)
        kept, dropped, moves = parse_recipes(
            profile, call.value, confirmed=confirmed or r.Confirmed(), values=values
        )
        proposed = len(call.value["recipes"]) + int(call.value.get("extra", 0))
        return ModelRecipes(call, kept, proposed, dropped, moves=moves, **base)
    except Exception:  # noqa: BLE001 - 契约：永不抛；这里只可能是本模块自己的缺陷
        return ModelRecipes(
            ai_structured.StructuredResult(False, reason="transport", detail="internal")
        )
