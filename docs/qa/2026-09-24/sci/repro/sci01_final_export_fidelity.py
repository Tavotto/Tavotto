"""SCI-01 补充探针：CompatBench 的 fidelity 比的是「原生 PNG vs worker.render_png」（两边都是 matplotlib Agg），
不碰**最终导出物**。这里补两条到产物的腿，用与 RenderCore 不同源的 PyMuPDF 读取 / 栅格化复核：

  A. 原生 `savefig` PDF  vs  Tavotto 零 override 经 worker 重新序列化的 PDF（`/api/export` scope=original，
     overrides=[] + rerender 由 EPS 触发不合适，这里走 `pool.one_shot().export`——写回 staging 同一条）；
  B. 原生 PDF  vs  把它 1:1 摆上画布后经 RenderCore 合成的 PDF（`/api/export` scope=canvas）。

每条比：页面盒（pt，±0.05）、文字层（逐行相同）、字体名集合（子集前缀去掉后相同）、150 dpi 栅格逐像素
（PyMuPDF 渲染两侧，changed_pixel_ratio ≤ 0.004 且 mean_abs_diff ≤ 1.2，与 CompatBench fidelity 同一组阈值）。
用法（worktree 根目录）：bash docs/qa/2026-09-24/sci/repro/runpy.sh docs/qa/2026-09-24/sci/repro/sci01_final_export_fidelity.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[5]
CASES = {
    "cases/core_artists/ca_basic_series.py": ["art_plot"],
    "cases/core_artists/ca_legend_colorbar.py": ["art_legend", "art_colorbar"],
    "cases/axes_layout/al_grids.py": ["ax_subplots"],
    "cases/scientific_stack/sci_typography.py": ["sci_mathtext"],
}
TOL = {"changed_pixel_ratio": 0.004, "mean_abs_diff": 1.2}


def _facts(pdf: Path) -> dict:
    with pymupdf.open(pdf) as doc:
        page = doc[0]
        pix = page.get_pixmap(dpi=150, alpha=False)
        fonts = sorted({f[3].split("+", 1)[-1] for f in page.get_fonts()})
        return {
            "box": (round(page.rect.width, 2), round(page.rect.height, 2)),
            "text": page.get_text().split("\n"),
            "fonts": fonts,
            "pix": (pix.width, pix.height, bytes(pix.samples)),
        }


def _diff(a: dict, b: dict) -> dict:
    (wa, ha, sa), (wb, hb, sb) = a["pix"], b["pix"]
    if (wa, ha) != (wb, hb):
        return {"size_mismatch": [(wa, ha), (wb, hb)]}
    changed = total = 0
    acc = 0
    for i in range(0, len(sa), 3):
        d = max(abs(sa[i] - sb[i]), abs(sa[i + 1] - sb[i + 1]), abs(sa[i + 2] - sb[i + 2]))
        acc += d
        total += 1
        changed += d > 16
    return {"changed_pixel_ratio": changed / total, "mean_abs_diff": acc / total}


def _compare(native: dict, other: dict) -> dict:
    px = _diff(native, other)
    ok = (
        abs(native["box"][0] - other["box"][0]) <= 0.05
        and abs(native["box"][1] - other["box"][1]) <= 0.05
        and native["text"] == other["text"]
        and native["fonts"] == other["fonts"]
        and "size_mismatch" not in px
        and px["changed_pixel_ratio"] <= TOL["changed_pixel_ratio"]
        and px["mean_abs_diff"] <= TOL["mean_abs_diff"]
    )
    text_diff = []
    if native["text"] != other["text"]:
        import difflib

        text_diff = list(difflib.unified_diff(native["text"], other["text"], lineterm="", n=0))[2:]
    return {
        "ok": ok,
        "text_diff": text_diff,
        "box": [native["box"], other["box"]],
        "text_equal": native["text"] == other["text"],
        "fonts": [native["fonts"], other["fonts"]],
        "pixels": px,
    }


def main() -> int:
    from tavotto import app as m
    from tavotto.engine import exportjob, pool, project_watch

    worker_py = os.environ["TAVOTTO_WORKER_PYTHON"]
    work = Path(tempfile.mkdtemp(prefix="sci01-"))
    results: dict = {}
    all_ok = True
    m.app.config["TESTING"] = True
    m.EXPORT_DIR = work / "exports"
    try:
        for rel, stems in CASES.items():
            src = ROOT / "tests" / "compat" / rel
            native_dir = work / ("native-" + src.stem)
            proj = work / ("proj-" + src.stem)
            native_dir.mkdir()
            proj.mkdir()
            shutil.copy2(src, native_dir / src.name)
            shutil.copy2(src, proj / src.name)
            subprocess.run(
                [worker_py, "-c", f"import {src.stem} as s; s.main()"],
                cwd=native_dir,
                env={**os.environ, "MPLBACKEND": "Agg", "PYTHONPATH": str(native_dir)},
                check=True,
                capture_output=True,
                timeout=300,
            )
            for pdf in native_dir.glob("*.pdf"):
                shutil.copy2(pdf, proj / pdf.name)  # 图库里的原件 = 原生产物
            (proj / "tavotto_registry.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "scripts": {
                            src.name: {
                                "entry": "main",
                                "cost": "light",
                                "notes": "",
                                "stems": sorted(p.stem for p in native_dir.glob("*.pdf")),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            m.reset_projects()
            m.open_project(str(proj))
            exportjob.reset_for_tests()
            client = m.app.test_client()
            w = pool.one_shot(src.name, str(proj), "main")
            try:
                for stem in stems:
                    native = _facts(native_dir / f"{stem}.pdf")
                    w.override(stem, [])
                    redraw = work / f"{stem}.tavotto.pdf"
                    w.export(stem, [], str(redraw), fmt="pdf", dpi=600)
                    leg_a = _compare(native, _facts(redraw))
                    keep = os.environ.get("SCI01_KEEP")
                    if keep:
                        Path(keep).mkdir(parents=True, exist_ok=True)
                        shutil.copy2(native_dir / f"{stem}.pdf", Path(keep) / f"{stem}.native.pdf")
                        shutil.copy2(redraw, Path(keep) / f"{stem}.tavotto.pdf")
                    wpt, hpt = native["box"]
                    r = client.post(
                        "/api/export",
                        json={
                            "scope": "canvas",
                            "filename": f"{stem}-canvas",
                            "formats": ["pdf"],
                            "canvas": {
                                "page_w_mm": wpt / 72 * 25.4,
                                "page_h_mm": hpt / 72 * 25.4,
                                "objects": [
                                    {
                                        "type": "panel",
                                        "id": f"{stem}.pdf",
                                        "x_mm": 0,
                                        "y_mm": 0,
                                        "w_mm": wpt / 72 * 25.4,
                                        "h_mm": hpt / 72 * 25.4,
                                    }
                                ],
                            },
                        },
                    )
                    body = r.get_json()
                    out = next(o for o in body["outputs"] if o["format"] == "pdf")
                    leg_b = _compare(native, _facts(Path(body["export_dir"]) / out["name"]))
                    results[stem] = {"A_worker_redraw": leg_a, "B_rendercore_canvas": leg_b}
                    all_ok &= leg_a["ok"] and leg_b["ok"]
            finally:
                pool.discard(w)
                pool.shutdown_all(figures_dir=str(proj), wait=True)
    finally:
        m.reset_projects()
        project_watch.stop()
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(results, ensure_ascii=False, indent=1))
    print("SCI-01 final-export fidelity VERDICT:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
