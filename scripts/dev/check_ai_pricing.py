#!/usr/bin/env python3
"""AI 计价表体检（开发侧，只读、不联网、不改文件）。

1. 按厂商打印 `ai_pricing.TABLE`（币种、出处、每个型号的输入 / 缓存命中 / 输出，分档逐档列出）；
2. 列出目录 `ai_catalog.json` 的 `models_hint` 里没有价的型号（已在 `UNPRICED_HINTS` 写明原因的单独标出）；
3. 列出表里有、但目录已经不再推荐的型号（不一定要删：旧型号仍可手填，只是提醒复核是否还在售）；
4. 列出目录里没有官方价的厂商及原因（`NOT_PRICED`）。

用法：PYTHONPATH=src python scripts/dev/check_ai_pricing.py
退出码恒为 0（体检不当闸门；是否补价、删价由人对着官方定价页决定，运行时不联网拉价）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Windows 上被捕获 / 重定向时中文输出不能静默丢掉：两条流都钉成 UTF-8（tests/test_windows_regressions.py 看护）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from tavotto.engine import ai_catalog, ai_pricing  # noqa: E402


def _fmt(rates: dict) -> str:
    return f"in {rates['input']:g} / cached {rates['cached']:g} / out {rates['output']:g}"


def main() -> int:
    print(f"PRICING_VERSION = {ai_pricing.PRICING_VERSION}\n")
    for vendor in sorted({v for v, _ in ai_pricing.TABLE}):
        print(f"[{vendor}] {ai_pricing.SOURCES.get(vendor, '（缺出处！）')}")
        for (v, model), entry in sorted(ai_pricing.TABLE.items()):
            if v != vendor:
                continue
            cur = entry.get("currency", ai_pricing.CURRENCY)
            tiers = entry.get("tiers")
            if tiers:
                prev = 0
                for limit, rates in tiers:
                    span = f"({prev}, {limit}]" if limit else f"> {prev}"
                    print(f"  {model:<32} {cur}  {span:<18} {_fmt(rates)}")
                    prev = limit or prev
            else:
                print(f"  {model:<32} {cur}  {_fmt(entry)}")
        print()

    vendors = json.loads(ai_catalog.CATALOG_PATH.read_text(encoding="utf-8"))["vendors"]
    hinted: set[tuple[str, str]] = set()
    print("目录推荐型号里没有价的：")
    none_found = True
    for row in vendors:
        vendor = ai_pricing.vendor_of(row)
        for model in row.get("models_hint") or []:
            if vendor:
                hinted.add((vendor, model))
            if vendor and (vendor, model) in ai_pricing.TABLE:
                continue
            if not vendor and not row.get("base_url"):
                continue  # 自填 / 官方登录，没有预设端点
            none_found = False
            if vendor:
                why = ai_pricing.UNPRICED_HINTS.get((vendor, model))
                tag = f"已说明：{why}" if why else "未说明，需要补价或写进 UNPRICED_HINTS"
            else:
                tag = "厂商没有官方计价（见下）"
            print(f"  {row['id']:<20} {model:<34} {tag}")
    if none_found:
        print("  （无）")

    print("\n表里有、目录不再推荐的型号（复核是否仍在售）：")
    extra = sorted(k for k in ai_pricing.TABLE if k not in hinted)
    for vendor, model in extra:
        print(f"  {vendor:<16} {model}")
    if not extra:
        print("  （无）")

    print("\n没有官方逐型号 Token 价的厂商（NOT_PRICED）：")
    for vid, reason in sorted(ai_pricing.NOT_PRICED.items()):
        print(f"  {vid:<20} {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
