"""出版规范 profile —— 规则的**唯一权威来源**是那份 canonical JSON。

    src/tavotto/profiles/publication.json

Python（预检、MCP server）与 TypeScript（画布预检、导出对话框）读的是**同一个
文件**：TS 侧经 `@profiles` 别名把它整份 import 进 bundle（web/vite.config.ts 与
vitest 各配一次别名），Python 侧走本模块。规则常量绝不能在两侧各写一份——那样
「双栏 150mm」改一处、另一处照旧放行，用户看到的是两个互相矛盾的体检结论。

本模块**只做加载 / 校验 / 合并 journal 覆盖**，不做任何检查逻辑（那在
`engine/preflight.py`）。纯标准库：Flask 父进程与 MCP server 都要 import 它，
worker 子进程用不到。

journal 覆盖（期刊自定义尺寸）是**浅合并 + 三个白名单子对象的深合并**：
`widths_mm` / `legend_policy` / `axis_policy` 里只覆盖点名的键，其余继承。
覆盖后的 profile 带 `derived_from` 与 `journal`，proof report 里据实写出来
——「这张图是按哪套规矩过的检」必须能从留档里读回来。
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

#: 规范文件名（包内与仓库内同名，别处不得再放第二份）
PROFILE_FILE = "publication.json"
#: 覆盖整份规范文件的位置（企业/期刊自带一套时用）
PROFILE_ENV = "TAVOTTO_PROFILES_FILE"

#: 检查项在 profile 的 severity 表里没登记时的兜底等级。
#: **不允许静默降级成 suggestion**：新加的检查项忘了登记，用户会以为它通过了。
DEFAULT_SEVERITY = "warn"

#: 合法等级。error 默认阻止导出；warn 放行但必须展示；not_verifiable 需要
#: 显式确认并写进 proof；suggestion 只是建议。
SEVERITIES = ("error", "warn", "not_verifiable", "suggestion")

#: profile 里**没写**字号下限时的兜底（pt）。与默认规范里那个数同值，而且
#: 全仓库只有这一处 —— 两个求值器都从这里取，界面一个字都不许自己写。
#: 严格同源对：`web/src/lib/profile.ts` 的同名常量，看护
#: `tests/test_profile_store.py::test_font_floor_fallback_is_one_number_on_both_sides`。
#:
#: **它不是「规范的下限」**：规范的下限在 profile 里（`min_effective_font_size_pt`
#: 与 `absolute_min_font_size_pt`）。这一条只在那两个键缺席时兜底，而缺席的
#: profile 走不过 `_REQUIRED` —— 也就是说它只可能被**没过校验的外来 spec**
#: 用到（MCP 直接喂进来的 dict）。那时宁可按默认规范判，也不许当作"没有下限"。
FALLBACK_MIN_FONT_SIZE_PT = 8.0

#: journal 覆盖里允许深合并的子对象（其余键整体替换）
_DEEP_KEYS = (
    "widths_mm",
    "legend_policy",
    "axis_policy",
    "font_family",
    "cjk_fallback",
    "severity",
    "preferred_formats",
)

_REQUIRED = (
    "profile_id",
    "version",
    "widths_mm",
    "allowed_aspect_ratios",
    "font_family",
    "cjk_fallback",
    "default_font_size_pt",
    "min_effective_font_size_pt",
    "absolute_min_font_size_pt",
    "min_raster_dpi",
    "preferred_formats",
    "line_widths_pt",
    "axis_policy",
    "legend_policy",
    "palette_policy",
    "severity",
)


class ProfileError(ValueError):
    """规范文件坏了 / 要的 profile 不存在。调用方转成 4xx，绝不静默兜底。"""


def profiles_path() -> Path:
    """canonical JSON 的位置：环境变量覆盖 → 包内 → 源码树。

    包内那条走 `importlib.resources`：装成 wheel 之后 `__file__` 的上级是
    site-packages，源码树的相对路径不存在（开发态能跑、装完就崩是这类 bug
    的经典形态）。
    """
    override = (os.environ.get(PROFILE_ENV) or "").strip()
    if override:
        return Path(override)
    try:
        from importlib.resources import files

        cand = Path(str(files("tavotto").joinpath("profiles", PROFILE_FILE)))
        if cand.is_file():
            return cand
    except (ImportError, ModuleNotFoundError, TypeError, OSError):
        pass
    # 开发态兜底：engine/ 的上一级就是包目录
    return Path(__file__).resolve().parent.parent / "profiles" / PROFILE_FILE


def _load_document() -> dict:
    path = profiles_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileError(f"出版规范文件读不到: {path}") from exc
    except ValueError as exc:
        raise ProfileError(f"出版规范文件不是合法 JSON（{path}）: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
        raise ProfileError(f"出版规范文件缺少 profiles 对象: {path}")
    return data


def _validate(profile: dict, pid: str) -> None:
    missing = [k for k in _REQUIRED if k not in profile]
    if missing:
        raise ProfileError(f"profile {pid} 缺少字段: {', '.join(missing)}")
    if profile.get("profile_id") != pid:
        raise ProfileError(
            f"profile 的键与 profile_id 不一致: {pid} != {profile.get('profile_id')}"
        )
    for key, value in (profile.get("severity") or {}).items():
        if value not in SEVERITIES:
            raise ProfileError(f"profile {pid} 的 severity[{key}] 不是合法等级: {value!r}")
    widths = profile.get("widths_mm") or {}
    for key in ("single", "double"):
        if not isinstance(widths.get(key), (int, float)) or widths[key] <= 0:
            raise ProfileError(f"profile {pid} 的 widths_mm.{key} 必须是正数")


def validate_spec(profile: object, pid: str | None = None) -> dict:
    """校验一份**任意来源**的规范（用户自建 / 导入 / 期刊覆盖后的结果）。

    内置规范走 `load()` 时已经过同一条判据；这里是给 `profilestore` 用的
    公开入口——用户自己编的规范必须过**同一套**校验，否则「内置严、自建松」
    会让同一张图在两条路上得到不同结论。
    """
    if not isinstance(profile, dict):
        raise ProfileError("规范必须是对象")
    got = profile.get("profile_id")
    target = pid if pid is not None else (got if isinstance(got, str) and got else "custom")
    if not isinstance(got, str) or not got:
        profile = {**profile, "profile_id": target}
    _validate(profile, target)
    return profile


def merge_journal(base: dict, journal: dict) -> dict:
    """期刊覆盖的**唯一合并实现**（浅合并 + 白名单子对象深合并）。"""
    return _deep_merge(base, journal)


def list_profiles() -> list[dict]:
    """全部可用 profile 的摘要（界面下拉用）。"""
    doc = _load_document()
    out = []
    for pid, profile in doc["profiles"].items():
        out.append(
            {
                "profile_id": pid,
                "version": profile.get("version", ""),
                "label": profile.get("label", pid),
                "source": profile.get("source", ""),
            }
        )
    return sorted(out, key=lambda p: p["profile_id"] != doc.get("default_profile"))


def default_profile_id() -> str:
    doc = _load_document()
    pid = doc.get("default_profile")
    if pid not in doc["profiles"]:
        raise ProfileError(f"default_profile 指向不存在的 profile: {pid!r}")
    return pid


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if key in _DEEP_KEYS and isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = copy.deepcopy(value)
    return out


def load(profile_id: str | None = None, journal: dict | None = None) -> dict:
    """取一份可直接喂给 preflight 的 profile。

    `journal` 是期刊自定义覆盖（如 `{"widths_mm": {"double": 178.0}}`）。
    合并结果带 `derived_from` / `journal`，proof report 会原样写出去。
    """
    doc = _load_document()
    pid = profile_id or doc.get("default_profile")
    if pid not in doc["profiles"]:
        raise ProfileError(
            f"没有这个出版规范: {pid!r}（可用: {', '.join(sorted(doc['profiles']))}）"
        )
    profile = copy.deepcopy(doc["profiles"][pid])
    _validate(profile, pid)
    if not journal:
        return profile
    if not isinstance(journal, dict):
        raise ProfileError("journal 覆盖必须是对象")
    merged = _deep_merge(profile, journal)
    # 覆盖不许换身份：profile_id/version 永远是被覆盖的那一份的
    merged["profile_id"] = profile["profile_id"]
    merged["version"] = profile["version"]
    merged["derived_from"] = pid
    merged["journal"] = copy.deepcopy(journal)
    _validate(merged, pid)
    return merged


def severity_of(profile: dict, check_id: str) -> str:
    """检查项的等级；没登记的按 DEFAULT_SEVERITY（不是静默通过）。"""
    value = (profile.get("severity") or {}).get(check_id)
    return value if value in SEVERITIES else DEFAULT_SEVERITY


def stamp(profile: dict) -> dict:
    """proof report / MCP 响应里的 profile 身份戳。"""
    out = {
        "profile_id": profile.get("profile_id"),
        "profile_version": profile.get("version"),
        "label": profile.get("label", ""),
    }
    if profile.get("journal"):
        out["journal"] = profile["journal"]
        out["derived_from"] = profile.get("derived_from")
    return out
