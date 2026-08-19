#!/usr/bin/env python3
"""渲染性能基线：把一个真实图库整个跑一遍，逐脚本量冷启动 / 热 override / 导出。

**先测量，后优化**——这个脚本的产物是 `docs/perf-baseline.md`，任何「优化」都
必须先在这里看到数据。它刻意走**真实的 HTTP 端点**（`/api/engine/render`、
`/api/export`）而不是直接 import 引擎：用户等的是那条链路的总时间，绕过 Flask
量出来的数字好看但没用。

每个可参数化面板测三件事：

* **冷启动**：第一次 `/api/engine/render`（会触发 build，跑用户脚本）。
  响应里带 `script_build_ms` 的那次才是真冷启动——一脚本多产物时，第二个 stem
  的「第一次」其实已经是热的，表里如实标成 warm。
* **热 override**：连发 N 次，每次改同一个元素的 `fontsize`（模拟用户拖滑块，
  值每次都不同，绝不让 worker 走「什么都没变」的捷径），取**中位数**。
  取中位不取均值：偶发的一次 GC / 磁盘抖动会把均值拉走，而我们要的是
  「常态下有多快」。
* **导出**：带 override 的单面板 `/api/export`（走 worker 的全质量出图 +
  PyMuPDF 合成）。

两条控制面各跑一遍（Python 池 `TAVOTTO_WORKERD=0` / Rust supervisor），
数据目录每次都是全新的临时目录——引擎缓存留在上一轮里的话，「冷启动」量到的
就是别人的热态。

用法：
    python scripts/bench_render.py --python .venv/bin/python
    python scripts/bench_render.py --python .venv/bin/python \\
        --figures ~/papers/figures --repeat 9 --out docs/perf-baseline.md
    python scripts/bench_render.py --python .venv/bin/python --plane python
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
DEFAULT_FIGURES = REPO / "examples" / "figures"
BOOT_TIMEOUT_S = 120
RENDER_TIMEOUT_S = 900     # 冷启动可以是分钟级（heavy 脚本）

#: 表里逐列展示的计时键（顺序即列序）。`svg_ms` 不在其中——SVG 序列化与 draw
#: 在 matplotlib 里分不开，合并在 `canvas_draw_ms` 里，见 ADR 0003 §9。
TIMING_KEYS = ["worker_get_ms", "queue_wait_ms", "build_total_ms",
               "script_build_ms", "patch_apply_ms", "canvas_draw_ms",
               "manifest_ms", "total_ms"]


class BenchError(RuntimeError):
    pass


# ------------------------------- HTTP 小工具 --------------------------------
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get(url: str, timeout: float = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _req(url: str, payload: dict, method: str, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url: str, payload: dict, timeout: float = 60) -> dict:
    return _req(url, payload, "POST", timeout)


def _text(url: str, timeout: float = 60) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _patch(url: str, payload: dict, timeout: float = 30) -> dict:
    return _req(url, payload, "PATCH", timeout)


def _wait_ready(base: str, proc: subprocess.Popen, timeout: float) -> dict:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise BenchError(f"进程在就绪前退出，returncode={proc.returncode}")
        try:
            return _get(f"{base}/api/version", timeout=5)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last = exc
            time.sleep(0.5)
    raise BenchError(f"{timeout:.0f}s 内 /api/version 仍不可访问: {last}")


# ------------------------------- 测量 ---------------------------------------
def _pick_patch(manifest: dict) -> dict | None:
    """从 manifest 里挑一个「改了真会重画」的属性作为热 override 的靶子。

    优先 `fontsize`（每个图都有文字，值域连续，改一点点就必然触发一次完整
    重绘，正是用户拖滑块时发生的事）；退而求其次是线宽。一个都挑不到就回
    None，那时热测走空 patch 列表——仍然经过 apply / draw / manifest 全程，
    只是少了一次真正的属性写入。
    """
    for prop in ("fontsize", "linewidth", "lw"):
        for el in manifest.get("elements", []):
            for field in el.get("editable", []):
                if field.get("prop") == prop and field.get("type") == "number":
                    try:
                        base = float(field.get("value"))
                    except (TypeError, ValueError):
                        continue
                    return {"gid": el["gid"], "prop": prop, "value": base}
    return None


def _variant(patch: dict | None, i: int) -> list:
    """第 i 次热渲染要发的全量 patch 列表（值每次都不同）。"""
    if patch is None:
        return []
    # ±0.5pt 之间来回，永远落在 manifest 给的 min/max 里
    return [{**patch, "value": round(patch["value"] + (0.5 if i % 2 else -0.5), 2)}]


def _median(samples: list[dict], key: str) -> float | None:
    vals = [s[key] for s in samples if isinstance(s.get(key), (int, float))]
    return round(statistics.median(vals), 1) if vals else None


def bench_panel(base: str, panel: dict, repeat: int, export_stem: str,
                preview_dpi: int | None = None) -> dict:
    """单个面板：冷启动 → 热 override×repeat（取中位）→ 导出。

    `preview_dpi` 用来量「预览降质换快显」这个旋钮值不值——它只改预览 SVG
    里嵌入位图的分辨率，导出与 manifest 一律不受影响。
    """
    pid = panel["id"]
    extra = {"preview_dpi": preview_dpi} if preview_dpi else {}
    rec: dict = {"id": pid, "cost": panel.get("cost", ""),
                 "preview_dpi": preview_dpi or "(默认)"}

    t0 = time.perf_counter()
    res = _post(f"{base}/api/engine/render", {"id": pid, "patches": [], **extra},
                timeout=RENDER_TIMEOUT_S)
    cold = dict(res.get("timings") or {})
    cold["wall_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    # 只有真的跑了脚本才算冷启动（一脚本多产物时第二个 stem 是热的）
    rec["cold"] = cold
    rec["really_cold"] = "script_build_ms" in cold

    patch = _pick_patch(res.get("manifest") or {})
    rec["patch"] = f'{patch["gid"]}.{patch["prop"]}' if patch else "(空列表)"
    rev = res["rev"]                # --repeat 0 时下面的循环一次都不跑
    samples: list[dict] = []
    for i in range(repeat):
        t = time.perf_counter()
        r = _post(f"{base}/api/engine/render",
                  {"id": pid, "patches": _variant(patch, i), **extra},
                  timeout=RENDER_TIMEOUT_S)
        s = dict(r.get("timings") or {})
        s["wall_ms"] = round((time.perf_counter() - t) * 1000, 1)
        samples.append(s)
        rev = r["rev"]        # 取 SVG 要带上（服务端拿它做缓存穿透）
    rec["hot"] = {k: _median(samples, k)
                  for k in [*TIMING_KEYS, "wall_ms"]}
    rec["hot_n"] = repeat
    # 预览 SVG 的体积：前端每次渲染都要把它下载 + 解析一遍，属于「快显」的
    # 另一半（含 imshow 的面板里它是 dpi 的直接函数，纯矢量图上是常数）
    rec["svg_kb"] = round(len(_text(
        f"{base}/api/engine/svg?id={urllib.parse.quote(pid)}&rev={rev}"
    ).encode("utf-8")) / 1024, 1)

    spec = {
        "page_w_mm": 90, "page_h_mm": 70, "formats": ["pdf"],
        "stem": export_stem,
        "objects": [{"type": "panel", "id": pid, "x_mm": 5, "y_mm": 5,
                     "w_mm": 80, "h_mm": 60,
                     "overrides": _variant(patch, 0)}],
    }
    t = time.perf_counter()
    out = _post(f"{base}/api/export", spec, timeout=RENDER_TIMEOUT_S)
    rec["export_wall_ms"] = round((time.perf_counter() - t) * 1000, 1)
    rec["export_ok"] = bool(out.get("files"))
    return rec


def run_plane(launch: list[str], figures: Path, workdir: Path, plane: str,
              workerd: str | None, repeat: int, fresh_home: bool = False,
              preview_dpi: int | None = None) -> list[dict]:
    """起一次服务，测完整个图库，干净退出。`plane` 只用于命名与报告。

    **默认沿用真实的 HOME**（与 `smoke_app.py` 刻意不同）：matplotlib 的字体
    缓存放在用户目录里，重置 HOME 就等于每次冷启动都要重建一次字体缓存——
    实测在这台机器上是 **9 秒**，它会盖过所有别的数字，而真实用户一台机器
    只付一次。要量「新机器上的第一次」用 `--fresh-home`，那是另一个问题
    （首次体验），不该混进稳态基线里。
    """
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    data_dir = workdir / "data"
    config_dir = workdir / "config"
    export_dir = workdir / "exports"
    for d in (data_dir, config_dir, export_dir):
        d.mkdir(parents=True, exist_ok=True)

    env = {
        **os.environ,
        "TAVOTTO_DATA_DIR": str(data_dir),
        "TAVOTTO_CONFIG_DIR": str(config_dir),
        "TAVOTTO_NO_UPDATE_CHECK": "1",
        "TAVOTTO_ALLOW_SHUTDOWN": "1",
        # 显式指定控制面：不指定的话开发机上有没有 cargo 产物会让两次运行
        # 悄悄跑在不同的实现上——那就不是对照了
        "TAVOTTO_WORKERD": workerd or "0",
    }
    if fresh_home:
        home = workdir / "home"
        home.mkdir(parents=True, exist_ok=True)
        env["HOME"] = env["USERPROFILE"] = str(home)

    cmd = [*launch, "--port", str(port), "--no-browser", "--figures", str(figures)]
    print(f"\n=== 控制面 {plane} ===\n$ {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    try:
        _wait_ready(base, proc, BOOT_TIMEOUT_S)
        # 导出落到临时目录：默认是项目内的 tavottofile/export/，
        # 跑一次基线就往用户（或仓库的 examples/）图库里塞一堆成图
        _patch(f"{base}/api/project/settings", {"export_dir": str(export_dir)})

        panels = [p for p in _get(f"{base}/api/panels")["panels"] if p.get("script")]
        if not panels:
            raise BenchError(f"{figures} 里没有可参数化面板（注册表为空？）")
        rows = []
        for i, panel in enumerate(panels):
            print(f"  · {panel['id']} …", end="", flush=True)
            rec = bench_panel(base, panel, repeat, f"bench{i}", preview_dpi)
            rec["plane"] = plane
            rows.append(rec)
            print(f" 冷 {rec['cold']['wall_ms']:.0f}ms / "
                  f"热中位 {rec['hot']['wall_ms']:.0f}ms / "
                  f"导出 {rec['export_wall_ms']:.0f}ms", flush=True)
        return rows
    finally:
        if proc.poll() is None:
            try:
                _post(f"{base}/api/shutdown", {}, timeout=10)
                proc.wait(timeout=30)
            except (urllib.error.URLError, OSError, TimeoutError,
                    subprocess.TimeoutExpired):
                pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()


# ------------------------------- 报告 ---------------------------------------
def _cell(v) -> str:
    return "—" if v is None else (f"{v:.1f}" if isinstance(v, float) else str(v))


def markdown(rows: list[dict], meta: dict) -> str:
    out: list[str] = []
    out.append(f"机器：{meta['machine']}")
    out.append("")
    out.append(f"图库：`{meta['figures']}`　解释器：`{meta['python']}`　"
               f"热态样本：每面板 {meta['repeat']} 次取中位"
               + (f"　预览 dpi={meta['preview_dpi']}"
                  if meta.get("preview_dpi") else "")
               + ("　**--fresh-home（含字体缓存重建，非稳态）**"
                  if meta.get("fresh_home") else ""))
    out.append("")

    planes = []
    for r in rows:
        if r["plane"] not in planes:
            planes.append(r["plane"])

    for plane in planes:
        mine = [r for r in rows if r["plane"] == plane]
        out.append(f"### 控制面：{plane}")
        out.append("")
        out.append("| 面板 | cost | 冷 wall | 冷 worker_get | 冷 build 往返 | "
                   "冷 script_build | 热 wall(中位) | queue_wait | patch_apply | "
                   "canvas_draw | manifest | worker total | SVG | 导出 wall |")
        out.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in mine:
            cold, hot = r["cold"], r["hot"]
            out.append(
                f"| `{r['id']}` | {r['cost'] or '—'} | "
                f"{_cell(cold.get('wall_ms'))}{'' if r['really_cold'] else '（warm）'} | "
                f"{_cell(cold.get('worker_get_ms'))} | "
                f"{_cell(cold.get('build_total_ms'))} | "
                f"{_cell(cold.get('script_build_ms'))} | "
                f"{_cell(hot.get('wall_ms'))} | {_cell(hot.get('queue_wait_ms'))} | "
                f"{_cell(hot.get('patch_apply_ms'))} | {_cell(hot.get('canvas_draw_ms'))} | "
                f"{_cell(hot.get('manifest_ms'))} | {_cell(hot.get('total_ms'))} | "
                f"{_cell(r.get('svg_kb'))}KB | {_cell(r['export_wall_ms'])} |")
        out.append("")
    out.append("单位全部是毫秒（SVG 列除外）。`wall` 是客户端看到的整次 HTTP 往返；"
               "`worker_get` 是取（必要时 spawn）会话；`build 往返` 是父进程量到的"
               "整条 build 命令（含子解释器启动与 import matplotlib），"
               "`script_build` 是其中 worker 自己那一段——两者之差就是**进程与"
               "import 的开销**；`worker total` 是那次 render 的 worker 往返，"
               "其余各列由 worker 自报。")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--python", default=sys.executable,
                    help="跑 Tavotto 的解释器（默认当前解释器）")
    ap.add_argument("--exe", default=None, help="打包产物（与 --python 二选一）")
    ap.add_argument("--figures", default=str(DEFAULT_FIGURES))
    ap.add_argument("--repeat", type=int, default=7, help="热 override 采样次数")
    ap.add_argument("--plane", choices=["both", "python", "workerd"], default="both")
    ap.add_argument("--preview-dpi", type=int, default=None,
                    help="每条 render 都带上这个预览 dpi（量降质换快显的旋钮）")
    ap.add_argument("--fresh-home", action="store_true",
                    help="连 HOME 一起隔离：量的是「新机器上的第一次」"
                         "（含 matplotlib 字体缓存重建），不是稳态")
    ap.add_argument("--out", default=None, help="把 markdown 表写到这个文件")
    ap.add_argument("--json", default=None, help="原始测量结果（便于事后对比）")
    args = ap.parse_args(argv)

    launch = [args.exe] if args.exe else [args.python, "-m", "tavotto"]
    figures = Path(args.figures).resolve()
    if not figures.is_dir():
        print(f"图库目录不存在: {figures}", file=sys.stderr)
        return 2

    # workerd 二进制：release 优先（debug 版的 Rust 慢得没有参考价值）
    workerd = next(
        (str(p) for p in (REPO / "workerd" / "target" / "release" / "tavotto-workerd",
                          REPO / "workerd" / "target" / "debug" / "tavotto-workerd")
         if p.is_file()), None)

    planes: list[tuple[str, str | None]] = []
    if args.plane in ("both", "python"):
        planes.append(("Python 池（TAVOTTO_WORKERD=0）", None))
    if args.plane in ("both", "workerd"):
        if workerd:
            planes.append((f"workerd（{Path(workerd).parent.name}）", workerd))
        elif args.plane == "workerd":
            print("找不到 tavotto-workerd 二进制（先 cargo build --release）",
                  file=sys.stderr)
            return 2
        else:
            print("! 没有 tavotto-workerd 二进制，跳过该控制面", file=sys.stderr)

    rows: list[dict] = []
    for label, exe in planes:
        workdir = Path(tempfile.mkdtemp(prefix="tavotto-bench-"))
        try:
            rows += run_plane(launch, figures, workdir, label, exe, args.repeat,
                              args.fresh_home, args.preview_dpi)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    meta = {
        "machine": f"{platform.platform()} / {platform.processor() or platform.machine()}",
        "figures": str(figures),
        "python": launch[0],
        "repeat": args.repeat,
        "fresh_home": args.fresh_home,
        "preview_dpi": args.preview_dpi,
    }
    table = markdown(rows, meta)
    print("\n" + table)
    if args.out:
        Path(args.out).write_text(table + "\n", encoding="utf-8")
        print(f"\n已写入 {args.out}")
    if args.json:
        Path(args.json).write_text(
            json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
