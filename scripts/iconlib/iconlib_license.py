"""开源图标库的许可白名单与过滤（ADR 0222 §二）。

纯函数、纯标准库。**未知 = 丢弃**：不认识的许可串、带 SA / NC / ND 的、软件许可（MIT / BSD，Bioicons 里是品牌标志）一律不收，
并在审计文件里写明原因，方便事后复核「为什么这个没进库」。
"""

from __future__ import annotations

#: 白名单。键是内部统一的许可号，值是 (署名里写的短名, 许可全文链接)。
LICENSES: dict[str, tuple[str, str]] = {
    "CC0-1.0": ("CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "PD": ("Public Domain", ""),
    "CC-BY-3.0": ("CC BY 3.0", "https://creativecommons.org/licenses/by/3.0/"),
    "CC-BY-4.0": ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"),
}

#: 导出时不必写署名句的（公有领域类）
NO_ATTRIBUTION = frozenset({"CC0-1.0", "PD"})

_ALIASES = {
    "cc0": "CC0-1.0",
    "cc-0": "CC0-1.0",
    "cc0-1.0": "CC0-1.0",
    "public-domain": "PD",
    "pd": "PD",
    "cc-by-3.0": "CC-BY-3.0",
    "cc-by-4.0": "CC-BY-4.0",
    "cc-by": None,  # 没写版本：不认（版本决定署名写法）
}

_RESTRICTED_TOKENS = frozenset({"sa", "nc", "nd"})


def normalize_license(raw: object) -> str | None:
    """各库自己的许可串 → 白名单里的许可号；不在白名单返回 None。"""
    if not isinstance(raw, str):
        return None
    key = raw.strip().lower().replace("_", "-").replace(" ", "-")
    if _RESTRICTED_TOKENS & set(key.split("-")):
        return None
    got = _ALIASES.get(key)
    return got if got in LICENSES else None


def reject_reason(raw: object) -> str:
    """许可串为什么没进白名单（给审计文件用）。"""
    if not isinstance(raw, str) or not raw.strip():
        return "license_missing"
    toks = set(raw.strip().lower().replace("_", "-").replace(" ", "-").split("-"))
    if _RESTRICTED_TOKENS & toks:
        return "license_restricted"  # SA / NC / ND
    if toks & {"mit", "bsd", "apache", "gpl"}:
        return "license_software"
    return "license_unknown"


def filter_records(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """记录里的 `license_raw` 过白名单。返回 (保留的，已写入 `license_id`；审计条目)。不改入参。"""
    kept: list[dict] = []
    audit: list[dict] = []
    for r in records:
        lid = normalize_license(r.get("license_raw"))
        if lid is None:
            audit.append(
                {
                    "id": r.get("id"),
                    "source": r.get("source"),
                    "license_raw": r.get("license_raw"),
                    "stage": "license",
                    "reason": reject_reason(r.get("license_raw")),
                }
            )
            continue
        kept.append({**r, "license_id": lid})
    return kept, audit
