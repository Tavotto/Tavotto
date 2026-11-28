"""受管环境检查记录的闭集投影（ADR 0221 §一）：`read_environment_checks` 与「改」组失败结果共用。

叶子模块：只依赖 `readscope` 与（延后 import 的）`envdiag`，不 import 任何别的 fixtools 工具模块，免得 observe ↔ change 成环。
"""

from __future__ import annotations

from . import readscope

ENV_CHECKS_MAX = 5
#: 自检原文尾部的上限（字节数按字符计）；与 `envdiag.SELFTEST_DETAIL_MAX` 同值
ENV_CHECK_DETAIL_MAX = 4096


def closed_value(value, depth: int = 0):
    """自检形状里的值只放行闭集 / 数字 / 布尔 / 短字符串（及它们组成的小列表 / 小字典），其余一律丢。
    不按键名白名单：`envdiag` 以后加的键（operations / fidelity）原样透传，只有「值不像闭集」的才被拦。"""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 160 else None
    if depth >= 3:
        return None
    if isinstance(value, (list, tuple)):
        return [closed_value(v, depth + 1) for v in value[:16] if v is not None]
    if isinstance(value, dict):
        return {
            str(k)[:64]: closed_value(v, depth + 1)
            for k, v in list(value.items())[:24]
            if isinstance(k, str)
        }
    return None


def selftest_shape(row: dict) -> dict | None:
    """一代记录里的自检形状（原样透传 `envdiag` 投影出的字段）；没有自检失败回 None。"""
    shape = row.get("selftest")
    return closed_value(shape) if isinstance(shape, dict) else None


def environment_check_rows(project_root, *, with_detail: bool) -> list[dict]:
    """最近 ≤5 代受管环境任务的检查记录（与诊断包 `managed_environment_jobs` 同一份记录，新的在前）。"""
    from .. import envdiag

    rows: list[dict] = []
    for row in envdiag.recent_checks(project_root)[:ENV_CHECKS_MAX]:
        item: dict = {
            "requested": closed_value(row.get("requested")),
            "outcome": row.get("outcome"),
            "error_code": row.get("error_code"),
            "selftest": selftest_shape(row),
        }
        if with_detail and item["selftest"] is not None:
            detail = row.get("selftest_detail")
            if isinstance(detail, str) and detail.strip():
                item["detail_tail"] = readscope.scrub(detail)[-ENV_CHECK_DETAIL_MAX:]
        rows.append(item)
    return rows
