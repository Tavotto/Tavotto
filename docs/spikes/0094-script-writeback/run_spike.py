"""ADR 0094 spike 驱动：推荐方案（savefig 钩子块）的覆盖率与失败形态。

对每个用例脚本：
  1. 一次性 worker 跑原脚本、不带 override → baseline manifest（挑 override 目标用）；
  2. 同一个 worker 当「热态」：逐条（再加一组合并）应用 override，记热态 manifest + 探针 PNG；
  3. 把这条 / 这组 override 翻译成代码块插进脚本的**副本**，另起一次性 worker 跑它、
     **不带任何 override** → 新脚本的 manifest + 探针 PNG；
  4. 用写回事务 verify 的同一把尺（`app._compare_manifests` 几何 + `pdfbackend.compare_png`
     逐 RGBA 像素 + `app.REPLAY_PIXEL_TOL`）比两者。
  5. 字节级：插入两次 == 插入一次（幂等）；删块 == 原文件（逐字节）。

判据的主语：热态 = 「Tavotto 里用户此刻看到的图」；新脚本 = 「写回后用户在终端 / 重开
Tavotto 得到的图（override 已清零）」。两者一致才算这条 override 写回成功。

用法：PYTHONPATH=<worktree>/src TAVOTTO_DATA_DIR=<scratch> python run_spike.py [case ...]
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import emit  # noqa: E402

from tavotto import app, pdfbackend  # noqa: E402
from tavotto.engine import pool  # noqa: E402

CASES = {
    # 名字: (目录, 脚本, entry, stem)
    "fig1_kinetics": ("examples", "fig1_kinetics.py", "main", "Fig1_kinetics"),
    "fig2_correlation": ("examples", "fig2_comparison.py", "main", "Fig2_correlation"),
    "fig2_yield": ("examples", "fig2_comparison.py", "main", "Fig2_yield"),
    "pg_spectrum": ("examples", "spectrum.py", "__main__", "spectrum"),
    "pg_kinetics": ("examples", "kinetics.py", "__main__", "kinetics"),
    "pg_calibration": ("examples", "calibration.py", "__main__", "calibration"),
    # 用户真实脚本的两个用例：仓库公开，名字不入库。真实的 (目录, 脚本, entry, stem) 从
    # 环境变量 TAVOTTO_SPIKE_USER_CASES 指向的本地 JSON 读（{"user_a": [...], "user_b": [...]}），
    # 没配就只有占位、跑这两个用例会报文件不存在。
    "user_a": ("user_a", "script_a.py", "main", "stem_a"),
    "user_b": ("user_b", "script_b.py", "__main__", "stem_b"),
}

_LOCAL = os.environ.get("TAVOTTO_SPIKE_USER_CASES")
if _LOCAL:
    CASES.update({k: tuple(v) for k, v in json.loads(Path(_LOCAL).read_text("utf-8")).items()})

META = {"version": "spike", "date": datetime.date.today().isoformat(), "backup": "<spike>"}
MAGENTA = "#d6278f"


def _manifest(w, stem):
    return json.loads((Path(w.out_dir) / f"{stem}.json").read_text(encoding="utf-8"))


def _fields(el):
    return {f["prop"]: f.get("value") for f in el.get("editable", [])}


def _first(m, pred):
    return next((e for e in m["elements"] if pred(e)), None)


def pick_patches(m: dict) -> list[tuple[str, dict]]:
    """从 baseline manifest 挑 spike 的 override（类别, patch）。"""
    out: list[tuple[str, dict]] = []
    role = lambda *rs: lambda e: e.get("role") in rs  # noqa: E731
    ln = _first(m, role("line"))
    if ln:
        f = _fields(ln)
        out.append(("color", {"gid": ln["gid"], "prop": "color", "value": MAGENTA}))
        out.append(
            (
                "linewidth",
                {
                    "gid": ln["gid"],
                    "prop": "linewidth",
                    "value": round(float(f["linewidth"]) + 1.2, 3),
                },
            )
        )
    for r in ("title", "axis_label", "text"):
        t = _first(m, role(r))
        if t:
            f = _fields(t)
            out.append(
                (
                    "fontsize",
                    {
                        "gid": t["gid"],
                        "prop": "fontsize",
                        "value": round(float(f["fontsize"]) + 1.5, 3),
                    },
                )
            )
    t = _first(m, role("text"))
    if t:
        out.append(("color", {"gid": t["gid"], "prop": "color", "value": "#1b7837"}))
    tk = _first(m, lambda e: e["gid"].endswith(".xticks"))
    if tk:
        out.append(
            (
                "fontsize",
                {
                    "gid": tk["gid"],
                    "prop": "fontsize",
                    "value": round(float(_fields(tk)["fontsize"]) + 1.0, 3),
                },
            )
        )
    for r in ("text", "axis_label", "title"):
        t = _first(m, lambda e, r=r: e.get("role") == r and e.get("drag_prop") == "pos_frac")
        if t:
            ax_, ay_ = t["anchor"]
            out.append(
                (
                    "text_position",
                    {
                        "gid": t["gid"],
                        "prop": "pos_frac",
                        "value": [round(ax_ + 0.03, 4), round(ay_ - 0.02, 4)],
                    },
                )
            )
    lg = _first(m, role("legend"))
    if lg:
        f = _fields(lg)
        new_loc = "lower left" if f.get("loc") != "lower left" else "upper right"
        out.append(("legend_position", {"gid": lg["gid"], "prop": "loc", "value": new_loc}))
        x, y, w, h = lg["bbox"]
        out.append(
            (
                "legend_position",
                {
                    "gid": lg["gid"],
                    "prop": "loc_frac",
                    "value": [round(x + 0.04, 4), round(y + h + 0.03, 4)],
                },
            )
        )
        out.append(
            (
                "fontsize",
                {
                    "gid": lg["gid"],
                    "prop": "fontsize",
                    "value": round(float(f["fontsize"]) + 1.5, 3),
                },
            )
        )
        lt = _first(m, role("legend_text"))
        if lt:
            out.append(
                (
                    "fontsize",
                    {
                        "gid": lt["gid"],
                        "prop": "fontsize",
                        "value": round(float(_fields(lt)["fontsize"]) + 1.0, 3),
                    },
                )
            )
    return out


def compare(hot_w, fresh_w, stem, m_hot, m_fresh):
    diffs, n = app._compare_manifests(m_hot, m_fresh)
    hot_png = Path(hot_w.render_png(stem, app.REPLAY_PIXEL_WIDTH))
    fresh_png = Path(fresh_w.render_png(stem, app.REPLAY_PIXEL_WIDTH))
    if hot_png.read_bytes() == fresh_png.read_bytes():
        px = {"identical": True}
        px_ok = True
    else:
        metrics = pdfbackend.compare_png(hot_png, fresh_png)
        exceeded = {
            k: metrics.get(k)
            for k, tol in app.REPLAY_PIXEL_TOL.items()
            if metrics.get(k) is not None and float(metrics.get(k)) > tol
        }
        px_ok = bool(metrics.get("ok", False)) and not exceeded
        px = {
            "metrics": {
                k: metrics.get(k) for k in ("changed_pixel_ratio", "mean_abs_diff", "max_abs_diff")
            },
            "exceeded": exceeded,
        }
    return {
        "geometry_ok": not diffs,
        "geometry_diffs": diffs[:5],
        "elements": n,
        "pixels_ok": px_ok,
        "pixels": px,
    }


def run_case(name: str, naive: bool) -> dict:
    d, script, entry, stem = CASES[name]
    figs = HERE / d
    src_path = figs / script
    src = src_path.read_bytes()
    res: dict = {"case": name, "script": f"{d}/{script}", "stem": stem, "results": []}
    t0 = time.time()
    hot = pool.one_shot(script, str(figs), entry)
    try:
        hot.ensure_built()
        if stem is None:
            stem = (
                next(iter(json.loads(json.dumps(hot.last_build_descriptors)) or [{}])).get("stem")
                or ""
            )
            res["stem"] = stem
        hot.override(stem, [])
        base = _manifest(hot, stem)
        picks = pick_patches(base)
        res["build_s"] = round(time.time() - t0, 2)
        groups = [(cat, [p]) for cat, p in picks] + [("ALL", [p for _c, p in picks])]
        for cat, patches in groups:
            row = {
                "category": cat,
                "patches": [f"{p['gid']}.{p['prop']}={p['value']}" for p in patches],
            }
            try:
                resp = hot.override(stem, patches)
                row["hot_warnings"] = list(resp.get("warnings") or [])
                m_hot = _manifest(hot, stem)
                block, rep = emit.build_block(
                    {stem: patches}, {stem: base}, META, naive=naive, routes=ROUTES_ON
                )
                row["skipped"] = rep["skipped"]
                row["written"] = len(rep["written"])
                if not rep["written"]:
                    row["verdict"] = "not_emitted"
                    res["results"].append(row)
                    continue
                new_src = emit.insert_block(src, block)
                # 字节级：幂等 + 删块逐字节还原
                row["idempotent"] = emit.insert_block(new_src, block) == new_src
                row["roundtrip"] = emit.remove_block(new_src) == src
                spike_name = f"__tvspike_{script}"
                (figs / spike_name).write_bytes(new_src)
                fresh = pool.one_shot(spike_name, str(figs), entry)
                try:
                    # 没写进脚本的那几条仍是 override（部分写回）：verify 比的是
                    # 「新脚本 + 剩下的 override」与热态，产品里也是这一组
                    skipped = {(x["gid"], x["prop"]) for x in rep["skipped"]}
                    residual = [p for p in patches if (p["gid"], p["prop"]) in skipped]
                    row["residual_overrides"] = len(residual)
                    fresh_resp = fresh.override(stem, residual)
                    row["fresh_warnings"] = list(fresh_resp.get("warnings") or [])
                    m_fresh = _manifest(fresh, stem)
                    cmp = compare(hot, fresh, stem, m_hot, m_fresh)
                finally:
                    pool.discard(fresh)
                row.update(cmp)
                ok = (
                    cmp["geometry_ok"]
                    and cmp["pixels_ok"]
                    and not row["hot_warnings"]
                    and not row["fresh_warnings"]
                )
                row["verdict"] = "pass" if ok else "reject"
            except Exception as exc:  # noqa: BLE001
                row["verdict"] = "error"
                row["error"] = f"{type(exc).__name__}: {exc}"
                row["traceback"] = traceback.format_exc()[-1500:]
            res["results"].append(row)
    finally:
        pool.discard(hot)
    res["total_s"] = round(time.time() - t0, 1)
    return res


ROUTES_ON = emit.ROUTES


def main(argv):
    global ROUTES_ON
    naive = "--naive" in argv
    if "--no-routes" in argv:  # 第一轮 spike 的口径：两条覆盖率路线都不开
        ROUTES_ON = frozenset()
    names = [a for a in argv if not a.startswith("--")] or list(CASES)
    out = []
    for n in names:
        print(f"== {n}", flush=True)
        r = run_case(n, naive=naive)
        for row in r["results"]:
            print(
                f"  {row['verdict']:12} {row['category']:16} {'; '.join(row['patches'])[:110]}",
                flush=True,
            )
        out.append(r)
    dest = HERE / (
        "results_naive.json"
        if naive
        else ("results_round1.json" if not ROUTES_ON else "results.json")
    )
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("wrote", dest)


if __name__ == "__main__":
    main(sys.argv[1:])
