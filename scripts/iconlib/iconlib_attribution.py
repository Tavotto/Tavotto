"""署名句拼接（ADR 0222 §六）。界面怎么给由维护者另定，这里只出文字。

CC BY 的要求（https://creativecommons.org/licenses/by/4.0/deed.en 脚注）：创作者姓名、许可声明与链接、是否修改；
3.0 及更早另要求给出标题。CC0 / Public Domain 默认不出句。同来源 + 同许可 + 同创作者 + 同「是否修改」的合并成一条。
"""

from __future__ import annotations

from iconlib_license import LICENSES, NO_ATTRIBUTION

_WORDS = {
    "en": {"icons": "Icons: ", "by": "by", "modified": "modified", "sep": "; "},
    "zh": {"icons": "图标：", "by": "作者", "modified": "已修改", "sep": "；"},
}


def _title_required(license_id: str) -> bool:
    return license_id.startswith("CC-BY-") and license_id < "CC-BY-4.0"  # 3.0 起步版本都要标题


def attribution_line(
    records: list[dict], *, lang: str = "en", include_public_domain: bool = False
) -> str:
    """一批图标记录 → 一句署名。没有需要署名的（全是 CC0 / PD）返回空串。

    记录字段：`license_id`、`source`、`creator`、`title`、`modified`（缺省按已修改，保守）、
    可选 `copyright_notice`。"""
    w = _WORDS["zh" if lang == "zh" else "en"]
    groups: dict[tuple, list[str]] = {}
    notices: dict[tuple, str] = {}
    for r in records:
        lid = r.get("license_id")
        if lid not in LICENSES:
            continue  # 不在白名单的不该走到这里；不替它编一句
        if lid in NO_ATTRIBUTION and not include_public_domain:
            continue
        key = (r.get("source") or "", lid, r.get("creator") or "", bool(r.get("modified", True)))
        titles = groups.setdefault(key, [])
        t = (r.get("title") or "").strip()
        if t and t not in titles:
            titles.append(t)
        if r.get("copyright_notice"):
            notices[key] = r["copyright_notice"]
    parts: list[str] = []
    for key in sorted(groups):
        source, lid, creator, modified = key
        short, url = LICENSES[lid]
        who = ", ".join(x for x in dict.fromkeys([creator, source]) if x)
        head = ""
        if _title_required(lid) and groups[key]:
            head = ", ".join(f"“{t}”" for t in sorted(groups[key])) + f" {w['by']} "
        text = f"{head}{who}, {short}" + (f" ({url})" if url else "")
        if key in notices:
            text += f", {notices[key]}"
        if modified:
            text += f", {w['modified']}"
        parts.append(text)
    return w["icons"] + w["sep"].join(parts) if parts else ""
