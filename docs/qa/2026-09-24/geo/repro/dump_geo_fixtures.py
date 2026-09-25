"""把 G1 / G2 / G2big 经真实 Tavotto worker 跑一遍，落下 manifest + SVG 快照。

用法（从 worktree 根目录）::

    TAVOTTO_DATA_DIR=<scratch>/data \\
    TAVOTTO_WORKER_PYTHON=/opt/homebrew/opt/python@3.13/libexec/bin/python3 \\
    PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python \\
        docs/qa/2026-09-24/geo/repro/dump_geo_fixtures.py <out_dir> [--web-fixture <path>]

输出：
  <out_dir>/<stem>.manifest.json、<stem>.svg、summary.json（每个 stem 的可拖元素、
  anchor、drag_prop、bbox 与 SVG 上 gid 节点的原始 transform）、以及各文件 sha256。
  ``--web-fixture`` 另写一份裁剪过的 JSON（只留几何相关字段）给 vitest 用。

本进程不 import matplotlib；worker 起在 TAVOTTO_WORKER_PYTHON 指向的科学栈里。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from tavotto.engine import pool

HERE = Path(__file__).resolve().parent
STEMS = ("G1", "G2", "G2big")
KEEP = (
    "gid",
    "role",
    "label",
    "bbox",
    "draggable",
    "resizable",
    "anchor",
    "drag_prop",
    "follow_gids",
)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _trim(el: dict) -> dict:
    out = {k: el[k] for k in KEEP if k in el}
    out["editable"] = [f for f in el.get("editable", []) if f.get("prop") == "position"]
    return out


def _shrink(svg: str) -> str:
    """与 scripts/dump_svg_fixture.py 同一种压缩：只压 d= 与 base64，id / transform 原样。"""
    svg = svg[svg.index("<svg") :]
    svg = re.sub(r"<metadata>[\s\S]*?</metadata>", "", svg)
    svg = re.sub(r'\sd="[^"]{60,}"', ' d="M 0 0 L 1 1"', svg)
    svg = re.sub(r"[ \t]+\n", "\n", svg)
    svg = re.sub(r"\n\s*\n", "\n", svg)
    seen: dict[str, str] = {}
    for raw in re.findall(r"\b([pm][0-9a-f]{10})\b", svg):
        seen.setdefault(raw, "%s%03d" % (raw[0], len(seen)))
    for raw, stable in seen.items():
        svg = re.sub(r"\b%s\b" % raw, stable, svg)
    return svg.strip()


def main() -> int:
    out = Path(sys.argv[1]).resolve()
    web_fixture = None
    if "--web-fixture" in sys.argv:
        web_fixture = Path(sys.argv[sys.argv.index("--web-fixture") + 1]).resolve()
    figs = out / "figures"
    figs.mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "geo_library.py", figs / "geo_library.py")
    w = pool.one_shot("geo_library.py", str(figs), "main")
    summary: dict = {"worker_python": pool.find_worker_python(), "stems": {}}
    trimmed: dict = {}
    try:
        w.ensure_built()
        for stem in STEMS:
            resp = w.override(stem, [], inline_svg=True)
            man = resp["manifest"]
            svg = resp.get("svg") or ""
            (out / f"{stem}.manifest.json").write_text(
                json.dumps(man, ensure_ascii=False, indent=1, sort_keys=True), "utf-8"
            )
            (out / f"{stem}.svg").write_text(svg, "utf-8")
            vb = re.search(r'viewBox="([^"]+)"', svg)
            drag = []
            for e in man["elements"]:
                if e.get("draggable") or e.get("resizable"):
                    m = re.search(r'<g id="%s"([^>]*)>' % re.escape(e["gid"]), svg)
                    tf = re.search(r'transform="([^"]*)"', m.group(1)) if m else None
                    drag.append(
                        {
                            "gid": e["gid"],
                            "role": e["role"],
                            "drag_prop": e.get("drag_prop"),
                            "anchor": e.get("anchor"),
                            "bbox": e["bbox"],
                            "resizable": bool(e.get("resizable")),
                            "svg_node": bool(m),
                            "svg_transform": tf.group(1) if tf else None,
                        }
                    )
            summary["stems"][stem] = {
                "size_mm": man.get("size_mm"),
                "viewBox": vb.group(1) if vb else None,
                "element_count": len(man["elements"]),
                "movable": drag,
                "manifest_sha256": _sha(out / f"{stem}.manifest.json"),
                "svg_sha256": _sha(out / f"{stem}.svg"),
            }
            trimmed[stem] = {
                "stem": man.get("stem", stem),
                "size_mm": man.get("size_mm"),
                "elements": [_trim(e) for e in man["elements"]],
            }
            if stem != "G2big":
                trimmed[stem]["svg"] = _shrink(svg)
    finally:
        pool.discard(w)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
    if web_fixture:
        web_fixture.parent.mkdir(parents=True, exist_ok=True)
        web_fixture.write_text(json.dumps(trimmed, ensure_ascii=False, indent=1) + "\n", "utf-8")
        print("web fixture:", web_fixture, _sha(web_fixture))
    for stem, s in summary["stems"].items():
        print(
            stem,
            s["size_mm"],
            s["viewBox"],
            "elements",
            s["element_count"],
            "movable",
            len(s["movable"]),
        )
        for d in s["movable"][:14]:
            print(
                "   ",
                d["gid"],
                d["role"],
                d["drag_prop"],
                d["anchor"],
                "resizable" if d["resizable"] else "",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
