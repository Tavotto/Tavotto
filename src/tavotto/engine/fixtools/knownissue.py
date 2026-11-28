"""修复工具结果里的「已知问题」一段（ADR 0221 §二）：`read_run_report`、「改」组失败结果、`prepare_diagnostics` 共用。

叶子模块：只依赖 `base` 与（延后 import 的）`engine.knownissues`，不 import 别的 fixtools 工具模块，免得 change ↔ runreport 成环。
"""

from __future__ import annotations

import json

from .base import FixContext


def known_issue_of(ctx: FixContext, code: object, selftest: dict | None = None) -> dict | None:
    """这个错误码是不是 Tavotto 已知的问题（ADR 0221 §二）：命中回 `{id, summary, fixed_in, workaround}`，语言按会话界面语言。"""
    from .. import knownissues

    return knownissues.lookup(code, ctx.project_root, locale=ctx.locale, selftest=selftest)


def known_issue_text(ctx: FixContext, code: object, selftest: dict | None = None) -> str:
    """给「改」组失败文本末尾追加的一段；没命中回空串。"""
    fact = known_issue_of(ctx, code, selftest)
    if fact is None:
        return ""
    return "\nknown_issue: " + json.dumps(fact, ensure_ascii=False)
