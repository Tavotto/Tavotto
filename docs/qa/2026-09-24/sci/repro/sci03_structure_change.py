"""SCI-03 探针：先给曲线 A 加 override，再改源脚本的结构（重排 / 删除 / 插入曲线、增加子图），重新捕获。

判据（规范 §5 SCI-03）：旧 override 只匹配原逻辑对象；无法匹配要明确报告，绝不「成功」套给另一条曲线。

独立真值：每条曲线在脚本里带唯一 label（alpha / beta / gamma）与固定原色；override 把 A（label=alpha）
改成 #FF00FF + linewidth 4.5。重新捕获后按 **label** 找曲线（不按 gid），看洋红色落在谁身上、有没有 warning。

入口：Flask test client（与 tests/ 同一产品端点 /api/engine/render），真 matplotlib worker。
用法（worktree 根目录）：source docs/qa/2026-09-24/sci/repro/env.sh &&
    TAVOTTO_WORKER_PYTHON=$WORKER_PY "$PY" docs/qa/2026-09-24/sci/repro/sci03_structure_change.py
输出：stdout 一份 JSON（每个变体一行结论），退出码 0 = 全部符合判据，1 = 至少一个变体违反。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HEADER = (
    "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n\n\ndef main():\n"
)

BASE = """    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot([0, 1, 2, 3], [0, 9, 2, 10], color="#1f77b4", label="alpha")
    ax.plot([0, 1, 2, 3], [0, 2, 9, 10], color="#2ca02c", label="beta")
    ax.legend()
    fig.savefig("Fig1.pdf")
"""

VARIANTS = {
    # 曲线顺序对调：A 仍在，但变成第二条
    "reorder": """    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot([0, 1, 2, 3], [0, 2, 9, 10], color="#2ca02c", label="beta")
    ax.plot([0, 1, 2, 3], [0, 9, 2, 10], color="#1f77b4", label="alpha")
    ax.legend()
    fig.savefig("Fig1.pdf")
""",
    # 在 A 之前插入一条新曲线
    "insert_before": """    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot([0, 1, 2, 3], [5, 5, 5, 5], color="#d62728", label="gamma")
    ax.plot([0, 1, 2, 3], [0, 9, 2, 10], color="#1f77b4", label="alpha")
    ax.plot([0, 1, 2, 3], [0, 2, 9, 10], color="#2ca02c", label="beta")
    ax.legend()
    fig.savefig("Fig1.pdf")
""",
    # 删除 A：原逻辑对象不存在了，应明确报告
    "delete_a": """    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot([0, 1, 2, 3], [0, 2, 9, 10], color="#2ca02c", label="beta")
    ax.legend()
    fig.savefig("Fig1.pdf")
""",
    # 在前面插入一个子图：A 所在的 axes 从 axes_0 变成 axes_1
    "insert_axes": """    fig, (ax0, ax) = plt.subplots(1, 2, figsize=(5.0, 2.4))
    ax0.plot([0, 1], [1, 0], color="#9467bd", label="delta")
    ax.plot([0, 1, 2, 3], [0, 9, 2, 10], color="#1f77b4", label="alpha")
    ax.plot([0, 1, 2, 3], [0, 2, 9, 10], color="#2ca02c", label="beta")
    ax.legend()
    fig.savefig("Fig1.pdf")
""",
}

REGISTRY = {
    "version": 1,
    "scripts": {"fig1.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["Fig1"]}},
}


def _placeholder_pdf(path: Path) -> None:
    """面板列表按磁盘产物扫：先放一张占位 PDF（真实图库里它由脚本跑出来）。"""
    import pikepdf

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 100))
    pdf.save(path)


def _lines(manifest: dict) -> dict[str, dict]:
    """{label: {gid, color, linewidth}}——按 label（独立真值）找曲线，不信 gid。"""
    out = {}
    for el in manifest["elements"]:
        ed = {f["prop"]: f.get("value") for f in el.get("editable", [])}
        if el.get("role") == "line" or ({"color", "linewidth", "label"} <= set(ed)):
            label = ed.get("label")
            if label and not str(label).startswith("_"):
                out[str(label)] = {
                    "gid": el["gid"],
                    "color": str(ed.get("color", "")).lower(),
                    "linewidth": ed.get("linewidth"),
                }
    return out


def main() -> int:
    from tavotto import app as m
    from tavotto.engine import pool, project_watch

    root = Path(tempfile.mkdtemp(prefix="sci03-"))
    m.app.config["TESTING"] = True
    m.BAKED_DIR = root / "_baked"
    m.BAKED_PATH = root / "_legacy_baked.json"
    m.CACHE_DIR = root / "_cache"
    results = {}
    failed = False
    try:
        for name, body in VARIANTS.items():
            figs = root / name
            figs.mkdir()
            (figs / "fig1.py").write_text(HEADER + BASE, encoding="utf-8")
            (figs / "tavotto_registry.json").write_text(json.dumps(REGISTRY), encoding="utf-8")
            _placeholder_pdf(figs / "Fig1.pdf")
            m.reset_projects()
            m.open_project(str(figs))
            c = m.app.test_client()
            r = c.post("/api/engine/render", json={"id": "Fig1.pdf", "patches": []})
            assert r.status_code == 200, r.get_json()
            before = _lines(r.get_json()["manifest"])
            a_gid = before["alpha"]["gid"]
            patches = [
                {"gid": a_gid, "prop": "color", "value": "#ff00ff"},
                {"gid": a_gid, "prop": "linewidth", "value": 4.5},
            ]
            r = c.post("/api/engine/render", json={"id": "Fig1.pdf", "patches": patches})
            hot = _lines(r.get_json()["manifest"])
            assert hot["alpha"]["color"] == "#ff00ff", hot

            # 改源脚本 → 等 watcher（2s 轮询）→ 重新捕获；再用一次性全新 worker 独立复核
            time.sleep(1.1)
            (figs / "fig1.py").write_text(HEADER + body, encoding="utf-8")
            time.sleep(3.0)
            r2 = c.post("/api/engine/render", json={"id": "Fig1.pdf", "patches": patches})
            j2 = r2.get_json() or {}
            after_http = _lines(j2.get("manifest") or {"elements": []})
            fresh = pool.one_shot("fig1.py", str(figs), "main")
            try:
                fr = fresh.override("Fig1", patches)
                after_fresh = _lines(
                    json.loads((Path(fresh.out_dir) / "Fig1.json").read_text(encoding="utf-8"))
                )
                fresh_warn = list(fr.get("warnings") or [])
            finally:
                pool.discard(fresh)

            def verdict(lines, warns):
                magenta = sorted(k for k, v in lines.items() if v["color"] == "#ff00ff")
                if magenta == ["alpha"] or (not magenta and warns):
                    ok = True
                else:
                    ok = False
                return {"magenta_on": magenta, "warnings": warns, "ok": ok}

            v_http = verdict(after_http, list(j2.get("warnings") or []))
            v_fresh = verdict(after_fresh, fresh_warn)
            results[name] = {
                "original_gid_of_alpha": a_gid,
                "http_status": r2.status_code,
                "http": v_http,
                "fresh_worker": v_fresh,
                "lines_after_fresh": after_fresh,
            }
            failed |= not (v_http["ok"] and v_fresh["ok"])
            pool.shutdown_all(figures_dir=str(figs), wait=True)
    finally:
        m.reset_projects()
        project_watch.stop()
        shutil.rmtree(root, ignore_errors=True)
    print(json.dumps(results, ensure_ascii=False, indent=1))
    print("SCI-03 VERDICT:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    os.environ.setdefault("TAVOTTO_NO_TELEMETRY", "1")
    sys.exit(main())
