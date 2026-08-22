#!/usr/bin/env python3
"""Golden 视觉回归：把 corpus 逐张渲成位图，与已审阅的基线做像素比对。

三条不可动摇的规矩，每条都是为了不让这条门禁变成摆设：

1. **基线缺失 = 失败，绝不自动创建。**
   「没有基线 → 生成一份 → 报绿」是典型的假绿：第一次跑永远通过，而它
   什么都没验证。基线只能由人显式跑 `--update-baselines` 产生，并在 code
   review 里被眼睛看过。
2. **不比 SHA256，比像素。**
   PNG 里有时间戳之类的元数据，字节比对会因为无意义差异整片变红；反过来，
   逐字节相同这个条件又过强，一次无害的压缩参数变化就会全灭。所以解码成
   像素之后按三个指标判：变化像素占比、平均绝对差、最大绝对差。
3. **失败必须能一眼看出哪里变了。**
   产出 baseline / candidate / diff 三张图 + metrics.json。只报一个数字的
   回归门禁，开发者第一反应永远是「大概是抖动吧」然后重跑。

渲染走产品自己的 `POST /api/engine/preview_png`（按 patches 出图、状态中立），
不绕过 Flask 直接调引擎——要验的是用户看到的那条链路。

用法：
    python scripts/ci/visual_regression.py --python .venv/bin/python
    python scripts/ci/visual_regression.py --update-baselines   # 只有人能跑
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))
from _common import (  # noqa: E402
    CiError, ensure_layout, materialize_corpus, run_metadata, state_root,
    summary, summary_table, write_report,
)
import pixelcompare  # noqa: E402
import smoke_app as SA  # noqa: E402

REPO = _HERE.parents[1]
CORPUS = REPO / "tests" / "acceptance" / "corpus"
MANIFEST = REPO / "tests" / "acceptance" / "manifest.json"
# 基线落**仓库里**而不是持久化根：它是需要被 review 的资产，必须随代码走。
BASELINE_DIR = REPO / "tests" / "acceptance" / "baselines"
# 固定宽度：必须命中 RENDER_BUCKETS 里的一档，否则服务端会向上取整到别的档，
# 基线与候选就会在不同分辨率下比较。
RENDER_WIDTH = 800


def load_manifest() -> dict:
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CiError("manifest_missing", f"读不到验收清单 {MANIFEST}：{exc}") from exc


def case_tolerance(manifest: dict, stem: str) -> dict:
    tol = dict(manifest["defaults"]["tolerance"])
    tol.pop("_comment", None)
    tol.update({k: v for k, v in manifest["cases"].get(stem, {}).get("tolerance", {}).items()
                if not k.startswith("_")})
    return tol


def case_wants_visual(manifest: dict, stem: str) -> bool:
    case = manifest["cases"].get(stem, {})
    return bool(case.get("visual", manifest["defaults"]["visual"]))


# ---------------------------------------------------------------- 像素比较
# 算法与判据搬进了 `pixelcompare.py`：CompatBench 的「零 patch 原生保真度」
# 要问的是另一个问题，但「两张 PNG 差多少算差」必须只有一个答案。
# 这里保留同名转发，既有调用方（含 tests/test_ci_qualification.py）不动。
def _load_pixels(path: Path):
    try:
        return pixelcompare.load_pixels(path)
    except pixelcompare.MissingImagingDeps as exc:
        raise CiError("missing_imaging_deps", str(exc)) from exc


def compare(baseline: Path, candidate: Path, diff_out: Path | None) -> dict:
    try:
        return pixelcompare.compare(baseline, candidate, diff_out)
    except pixelcompare.MissingImagingDeps as exc:
        raise CiError("missing_imaging_deps", str(exc)) from exc


def verdict(metrics: dict, tol: dict) -> tuple[bool, list[str]]:
    return pixelcompare.verdict(metrics, tol)


# ---------------------------------------------------------------- 渲染
def _post_png(base: str, stem_id: str, out: Path, timeout: int = 600) -> None:
    body = json.dumps({"id": stem_id, "patches": [], "w": RENDER_WIDTH}).encode()
    req = urllib.request.Request(f"{base}/api/engine/preview_png", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise CiError("preview_png_failed", f"{stem_id}: HTTP {resp.status}")
        data = resp.read()
    if len(data) < 500:
        raise CiError("preview_png_too_small", f"{stem_id}: 只回了 {len(data)} 字节，不像一张图")
    out.write_bytes(data)


def render_corpus(launch: list[str], workdir: Path, stems: list[str],
                  runner_python: str) -> dict[str, Path]:
    """把 corpus 逐张渲成 PNG，返回 stem → 文件路径。"""
    port = SA._free_port()
    base = f"http://127.0.0.1:{port}"
    data_dir, config_dir = workdir / "data", workdir / "config"
    shots = workdir / "shots"
    for d in (data_dir, config_dir, shots):
        d.mkdir(parents=True, exist_ok=True)

    # corpus 先复制到工作目录再跑，**不在仓库里就地生成产物**：
    # 那会往版本库里落一堆 PDF，也会让「工作目录干净」的判定失效。
    project = workdir / "corpus"
    shutil.copytree(CORPUS, project)
    produced = materialize_corpus(runner_python, project)
    print(f"corpus 产物 {len(produced)} 个：{', '.join(produced[:4])}…", flush=True)

    env = {
        **os.environ,
        "TAVOTTO_DATA_DIR": str(data_dir),
        "TAVOTTO_CONFIG_DIR": str(config_dir),
        "HOME": str(workdir / "home"),
        "USERPROFILE": str(workdir / "home"),
        "TAVOTTO_NO_UPDATE_CHECK": "1",
        "TAVOTTO_ALLOW_SHUTDOWN": "1",
        # 确定性三件套。渲染跑在 worker 子进程里，这些会经 child_env 传下去。
        "PYTHONHASHSEED": "0",
        "MPLBACKEND": "Agg",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": "1700000000",
    }
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)

    cmd = [*launch, "--port", str(port), "--no-browser", "--figures", str(project)]
    print(f"$ {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    out: dict[str, Path] = {}
    try:
        SA._wait_ready(base, proc, SA.BOOT_TIMEOUT_S)
        # ADR 0008：不装凭据的话下面每一个 API 调用都是 401。
        # `_wait_ready` 打的 /api/version 是公共端点，就绪永远成立——
        # 症状会是「起来了又立刻全挂」，与真实原因隔着一层。
        SA.adopt_session_credentials(data_dir, port)
        panels = SA._get(f"{base}/api/panels")["panels"]
        by_stem = {p["id"].rsplit(".", 1)[0]: p["id"] for p in panels if p.get("script")}
        missing = [s for s in stems if s not in by_stem]
        if missing:
            raise CiError("corpus_stem_missing",
                          f"corpus 里这些 stem 没被扫出来：{missing}。"
                          f"实际扫到 {sorted(by_stem)}。检查 tavotto_registry.json")
        for stem in stems:
            dest = shots / f"{stem}.png"
            t0 = time.time()
            _post_png(base, by_stem[stem], dest)
            out[stem] = dest
            print(f"  ✓ {stem} ({dest.stat().st_size} B, {time.time() - t0:.1f}s)", flush=True)
        try:
            SA._post(f"{base}/api/shutdown", {}, timeout=60)
            proc.wait(timeout=120)
        except Exception:                                  # noqa: BLE001
            pass
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)
    return out


# ---------------------------------------------------------------- 主流程
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Golden 视觉回归")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--python", default=sys.executable)
    g.add_argument("--exe", default=None)
    ap.add_argument("--update-baselines", action="store_true",
                    help="重建基线。**CI 永远不该传这个**——基线必须经人眼 review")
    ap.add_argument("--out", default=None, help="产物目录（diff 图与 metrics）")
    ap.add_argument("--only", default=None, help="只跑某几个 stem，逗号分隔")
    args = ap.parse_args(argv)

    # 硬拦截：即使有人在 workflow 里手滑加了这个参数，也不让它在 CI 上生效。
    # 「基线不存在 → 自动创建 → 报绿」是这套门禁最容易退化成的样子。
    if args.update_baselines and os.environ.get("CI") == "true":
        print("::error::--update-baselines 不允许在 CI 环境使用："
              "基线必须由人在本地生成并经 code review", file=sys.stderr)
        return 2

    manifest = load_manifest()
    all_stems = sorted(manifest["cases"])
    stems = [s for s in (args.only.split(",") if args.only else all_stems)]
    visual_stems = [s for s in stems if case_wants_visual(manifest, s)]
    skipped = [s for s in stems if s not in visual_stems]

    root = ensure_layout()
    workdir = Path(tempfile.mkdtemp(prefix="artifact-visual-", dir=str(root / "tmp")))
    out_dir = Path(args.out) if args.out else workdir / "report"
    out_dir.mkdir(parents=True, exist_ok=True)
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)

    launch = [args.exe] if args.exe else [args.python, "-m", "tavotto"]
    rows: list[tuple[str, str, str]] = []
    results: dict[str, dict] = {}
    ok = True
    try:
        shots = render_corpus(launch, workdir, stems, args.python if not args.exe else sys.executable)

        for stem in stems:
            baseline = BASELINE_DIR / f"{stem}.png"
            if stem in skipped:
                rows.append((stem, "⏭️", manifest["cases"][stem].get("visual_skip_reason", "")[:80]))
                results[stem] = {"skipped": True,
                                 "reason": manifest["cases"][stem].get("visual_skip_reason", "")}
                continue

            if args.update_baselines:
                shutil.copy2(shots[stem], baseline)
                rows.append((stem, "📝", "基线已更新"))
                results[stem] = {"updated": True}
                continue

            if not baseline.exists():
                # 这就是那条最重要的规矩：缺基线**失败**，不自动补。
                ok = False
                rows.append((stem, "❌", "基线不存在 —— 本地跑 --update-baselines 并提交，经 review 后再合"))
                results[stem] = {"ok": False, "reason": "baseline_missing"}
                continue

            diff_path = out_dir / f"{stem}.diff.png"
            metrics = compare(baseline, shots[stem], diff_path)
            good, reasons = verdict(metrics, case_tolerance(manifest, stem))
            results[stem] = {"ok": good, "metrics": metrics, "reasons": reasons}
            if good:
                rows.append((stem, "✅",
                             f"变化 {metrics.get('changed_pixel_ratio', 0):.5f} / "
                             f"均差 {metrics.get('mean_abs_diff', 0):.2f}"))
                diff_path.unlink(missing_ok=True)          # 通过的不留 diff 图，免得 artifact 里全是噪音
            else:
                ok = False
                rows.append((stem, "❌", "；".join(reasons)[:110]))
                shutil.copy2(baseline, out_dir / f"{stem}.baseline.png")
                shutil.copy2(shots[stem], out_dir / f"{stem}.candidate.png")
                print(f"::error::视觉回归 {stem}：{'；'.join(reasons)}", file=sys.stderr)
    except CiError as exc:
        ok = False
        rows.append((exc.code, "❌", exc.message))
        print(f"::error::{exc.message}", file=sys.stderr)
    finally:
        shutil.rmtree(workdir / "data", ignore_errors=True)

    payload = {
        "ok": ok,
        "width": RENDER_WIDTH,
        "updated_baselines": bool(args.update_baselines),
        "cases": results,
        "metadata": run_metadata(),
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report("visual.json", payload, root)

    summary(f"\n### Golden 视觉回归 · {len(visual_stems)} 张\n\n" + summary_table(rows))
    if skipped:
        summary(f"\n跳过像素比对：{', '.join(skipped)}（理由见 tests/acceptance/manifest.json）\n")
    print(f"\n视觉回归：{'通过' if ok else '失败'}（产物在 {out_dir}）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
