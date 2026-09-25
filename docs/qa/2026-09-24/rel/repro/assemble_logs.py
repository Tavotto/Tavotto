#!/usr/bin/env python3
"""把 scratch 里各次运行的原始日志（run_logged.py 产出，已做头 40 + 尾 150 截断）按 case 拼进
docs/qa/2026-09-24/rel/logs/<CASE_ID>.log，并打印每份的 sha256。

用法：python3 assemble_logs.py <raw 目录>
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE.parent / "logs"

PARTS = {
    "REL-01": [
        "REL-01-chromium.log",
        "REL-01-chromium-r2.log",
        "REL-01-webkit.log",
        "REL-01-webkit-geometry.log",
        "REL-01-desktop.log",
        "REL-01-desktop-identity.log",
        "REL-01-pyc-repro.log",
        "REL-01-pyc-repro-mutated.log",
        "REL-01-pyc-repro-restored.log",
    ],
    "REL-02": ["REL-02-upgrade.log"],
    "REL-03": [
        "REL-03-build.log",
        "REL-03-build2.log",
        "REL-03-acceptance.log",
        "REL-03-acceptance-released.log",
        "REL-03-acceptance-head.log",
    ],
    "REL-04": [
        "REL-04-dpr.log",
        "REL-04-isolate.log",
        "REL-04-missingdep-noenv.log",
        "REL-04-mutation-red.log",
        "REL-04-mutation-restored.log",
    ],
    "REL-05": [
        "REL-05-pytest.log",
        "REL-05-probe.log",
        "REL-05-leak-repro.log",
        "REL-05-mutation-red.log",
        "REL-05-mutation-restored.log",
        "REL-05-homemut-pytest.log",
        "REL-05-homemut-e2e.log",
        "REL-05-homemut-restored.log",
    ],
}


def main() -> int:
    raw = Path(sys.argv[1])
    LOGS.mkdir(parents=True, exist_ok=True)
    for case, parts in PARTS.items():
        chunks = []
        for name in parts:
            body = (raw / name).read_text(encoding="utf-8")
            chunks.append(f"########## part: {name} ##########\n{body}")
        out = LOGS / f"{case}.log"
        out.write_text("\n".join(chunks), encoding="utf-8")
        digest = hashlib.sha256(out.read_bytes()).hexdigest()
        print(f"{case}\t{out.relative_to(HERE.parents[4])}\t{digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
