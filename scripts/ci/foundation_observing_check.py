#!/usr/bin/env python3
"""nightly `foundation-observing` 的收尾判据：点名的 observing 用例**跑过且没有 skip**。

用法：`python scripts/ci/foundation_observing_check.py <junit.xml> <用例名前缀>...`

判据的主语是 junit 里名字以给定前缀开头的 testcase：一条都没有 → 红（用例被改名 / 没收集到，
判据没有主语）；任一条 skipped → 红（skip 不是绿：那说明 runner 没把对应解释器交到位，
是基础设施问题，不是产品结论）；failure / error **不在这里判**——observing 的失败要保留在
pytest 自己的退出码里（03_CI_POLICY §3：观察中的失败保留，不进 Gate），这一步只管「有没有真跑」。
纯标准库。
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def check(junit: Path, prefixes: list[str]) -> list[str]:
    problems: list[str] = []
    try:
        root = ET.parse(junit).getroot()
    except (OSError, ET.ParseError) as exc:
        return [f"读不到 junit: {junit}: {exc}"]
    cases = list(root.iter("testcase"))
    for prefix in prefixes:
        mine = [c for c in cases if (c.get("name") or "").startswith(prefix)]
        if not mine:
            problems.append(f"{prefix}*: junit 里一条都没有（用例改名了 / 没收集到）——判据没有主语")
            continue
        for c in mine:
            skipped = c.find("skipped")
            if skipped is not None:
                problems.append(f"{c.get('name')}: skipped —— {skipped.get('message', '')}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    junit = Path(argv[0])
    problems = check(junit, argv[1:])
    if problems:
        print("observing 用例没有真的跑起来：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"observing 用例都跑过且没有 skip：{', '.join(argv[1:])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
