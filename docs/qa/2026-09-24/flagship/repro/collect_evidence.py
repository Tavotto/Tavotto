"""把一次驱动运行（flagship_driver.cjs 的 run 目录）里值得入库的证据挑出来，脱敏后拷进仓库。

用法：python collect_evidence.py <run_dir> <fixture_dir> <dest_evidence_dir>

* 所有 manifest / document / 渲染请求（小）全拷；SVG 与截图只拷关键阶段（体积）；
* 导出物（PDF / pdftoppm PNG / 独立读取 JSON / 作业 JSON）全拷；
* 服务端日志里一次性落地 nonce（`#dnonce=`）替换成 `<redacted>`；
* 不拷 storage-state.json（含会话 cookie）与 data/session/（会话凭据）。
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

SVG_KEEP = [
    "s0-first-open",
    "s2-multi-drag",
    "s3-group-scale",
    "s6-distribute",
    "s9-reopened",
    "s11-snapshot-edit",
    "s12-recomputed",
]
PNG_KEEP = [
    "P2-workdir-dialog",
    "s0-first-open",
    "s2-multi-drag",
    "s3-group-scale",
    "s6-distribute",
    "s9-reopened",
    "s12-recomputed",
    "E0-export-dialog",
    "P17-context-menu",
]


def main() -> int:
    run, fixture, dest = (Path(a) for a in sys.argv[1:4])
    (dest / "snap").mkdir(parents=True, exist_ok=True)
    (dest / "exports").mkdir(parents=True, exist_ok=True)
    for p in sorted((run / "snap").iterdir()):
        keep = p.name.endswith((".manifest.json", ".document.json", ".request.json"))
        keep |= p.suffix == ".svg" and p.stem in SVG_KEEP
        keep |= p.suffix == ".png" and p.stem in PNG_KEEP
        if keep:
            shutil.copy2(p, dest / "snap" / p.name)
    for p in sorted((run / "exports").iterdir()):
        shutil.copy2(p, dest / "exports" / p.name)
    shutil.copy2(run / "report.json", dest / "report.json")
    shutil.copy2(fixture / "truth.json", dest / "fixture-truth.json")
    for tag in ("S1", "S2"):
        src = run / f"server-{tag}.log"
        if src.is_file():
            text = src.read_text(encoding="utf-8", errors="replace")
            text = re.sub(r"#dnonce=[A-Za-z0-9_-]+", "#dnonce=<redacted>", text)
            (dest / f"server-{tag}.log").write_text(text, encoding="utf-8")
    leaked = [
        p
        for p in dest.rglob("*")
        if p.is_file()
        and p.suffix in (".log", ".json")
        and re.search(r"dnonce=[A-Za-z0-9]", p.read_text(encoding="utf-8", errors="replace"))
    ]
    if leaked:
        print("nonce still present in:", leaked)
        return 1
    print("copied to", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
