"""生成 tests/golden/svgimport/*.json：样例 SVG（tests/fixtures/svgimport）导入后的对象快照（ADR 0185 M5）。

后端 `tests/test_svgimport.py` 核导入结果与快照逐字段相同；前端 `lib/svgImport.golden.test.ts` 读同一份快照，核前端落位
（换 id、平移到落点）后每个对象都是本构建认识、导出载荷收得下的对象。改了导入规则要重新生成并在 PR 里说明差异。

    PYTHONPATH=$PWD/src python scripts/dev/gen_svgimport_golden.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    from tavotto.rendercore.svgimport import import_svg

    out_dir = ROOT / "tests" / "golden" / "svgimport"
    out_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted((ROOT / "tests" / "fixtures" / "svgimport").glob("*.svg")):
        res = import_svg(src.read_text(encoding="utf-8"))
        dest = out_dir / (src.stem + ".json")
        dest.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(
            f"wrote {dest.relative_to(ROOT)} ({len(res['objects'])} objects, unsupported={res['unsupported']})"
        )


if __name__ == "__main__":
    main()
