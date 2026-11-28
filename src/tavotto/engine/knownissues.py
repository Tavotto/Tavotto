"""已知问题表的匹配器（ADR 0221 §二）：随版本内置的闭集条目，只按结构化字段匹配。

`resources/known_issues.json` 与 `fixtools/hints.py` 分工不同：hints 是「这类失败通常怎么办」的通用提示（按失败形状），
这里是「某个版本 / 平台上、某一步已知有缺陷」的条目（按错误码 + 自检阶段 + worker 码 + 平台 + 版本区间）。
纯标准库加 `packaging`（Flask 侧本来就有）；不联网、不读自由文本。
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from importlib import resources

#: 条目 `match` 里允许出现的键（schema 测试钉住）
MATCH_KEYS = frozenset({"error_code", "selftest_stage", "worker_code", "os", "versions"})


@lru_cache(maxsize=1)
def _table() -> tuple[dict, ...]:
    try:
        raw = (
            resources.files("tavotto").joinpath("resources", "known_issues.json").read_text("utf-8")
        )
        issues = json.loads(raw).get("issues")
    except Exception:  # noqa: BLE001 — 表读不到只是认不出已知问题，不拖垮修复会话
        return ()
    return tuple(i for i in issues if isinstance(i, dict)) if isinstance(issues, list) else ()


def os_name() -> str:
    """闭集平台名：`windows` / `macos` / `linux`（与条目 `match.os` 同一套）。"""
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")


def _parse(text: object):
    from packaging.version import InvalidVersion, Version

    try:
        return Version(str(text))
    except InvalidVersion:
        return None


def _version_ok(spec: object, version: object) -> bool:
    if not isinstance(spec, dict) or not spec:
        return True
    current = _parse(version) if version is not None else None
    if current is None:
        return False  # 条目限定了版本而这边读不出版本：不猜
    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None and (_parse(lo) is None or current < _parse(lo)):
        return False
    return not (hi is not None and (_parse(hi) is None or current > _parse(hi)))


def match(
    error_code: object,
    *,
    selftest: dict | None = None,
    os_name: str | None = None,
    version: str | None = None,
) -> list[dict]:
    """(错误码, 自检闭集形状, 平台, 版本) → 命中的条目（原样，表里的顺序）。

    条目里写了的条件都要满足；没写的不限。`selftest` 缺失时，要求自检阶段 / worker 码的条目不命中。"""
    code = error_code if isinstance(error_code, str) else ""
    shape = selftest if isinstance(selftest, dict) else {}
    hits = []
    for entry in _table():
        m = entry.get("match") or {}
        if not code or m.get("error_code") != code:
            continue
        if "selftest_stage" in m and shape.get("stage") != m["selftest_stage"]:
            continue
        if "worker_code" in m and shape.get("worker_code") != m["worker_code"]:
            continue
        if "os" in m and m["os"] != os_name:
            continue
        if not _version_ok(m.get("versions"), version):
            continue
        hits.append(entry)
    return hits


def fact(entry: dict, language: str = "en") -> dict:
    """给工具结果的形状 `{id, summary, fixed_in, workaround{tools, user_steps}}`；语言 `zh` 用中文，其余英文。"""
    zh = language == "zh"
    work = entry.get("workaround") or {}
    return {
        "id": entry["id"],
        "summary": entry.get("summary_zh" if zh else "summary_en", ""),
        "fixed_in": entry.get("fixed_in"),
        "workaround": {
            "tools": list(work.get("tools") or []),
            "user_steps": work.get("user_steps_zh" if zh else "user_steps_en", ""),
        },
    }


def language_of(locale: object) -> str:
    """界面语言 → `zh` / `en`（与 `fixsession.language_rule` 同一个前缀判据）。"""
    if isinstance(locale, str) and locale.strip().lower().replace("_", "-").split("-")[0] == "zh":
        return "zh"
    return "en"


def lookup(
    error_code: object,
    project_root,
    *,
    locale: object = None,
    selftest: dict | None = None,
) -> dict | None:
    """修复工具用的一站式入口：补上本机平台与当前版本；没给自检形状就取项目里最近一条同码受管环境任务的。

    命中回 `fact(...)`，没命中回 None。`read_environment_checks`（ADR 0221 §一）可直接以它取 `known_issue`。"""
    if not isinstance(error_code, str) or not error_code:
        return None
    if selftest is None and project_root is not None:
        try:
            from . import envdiag

            for row in envdiag.recent(project_root):
                if row.get("error_code") == error_code:
                    selftest = row.get("selftest")
                    break
        except Exception:  # noqa: BLE001
            selftest = None
    from .. import __version__

    hits = match(error_code, selftest=selftest, os_name=os_name(), version=__version__)
    return fact(hits[0], language_of(locale)) if hits else None


__all__ = ["fact", "language_of", "lookup", "match", "os_name"]
