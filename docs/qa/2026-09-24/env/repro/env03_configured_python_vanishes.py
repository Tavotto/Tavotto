"""最小复现（ENV-03 (c)）：设置里指定的全局解释器在**同一个后端进程**里消失后，产品报 `internal_error`
（`FileNotFoundError`）而不是合同里的 `explicit_python_unusable` / `reason=missing`；环境状态端点
仍报 `resolution_error: null`（说的还是那条已不存在的解释器）。重启后端后才按合同报 missing。

    PYTHONPATH=$PWD/src TAVOTTO_FONTS_DIR=<fonts> /Volumes/Projects/Tavotto/.venv/bin/python \\
        docs/qa/2026-09-24/env/repro/env03_configured_python_vanishes.py --e1 <scratch>/e1 --out <dir>

前提：PATCH 之后还没有起过任何 worker（没有热会话）。若先起过会话，后续 prepare / render 复用那个
仍活着的进程、照常 ready——那是「复用热态会话」的既有设计，不在本复现的范围内。

合同：ADR 0057 §一 第 2 条「设置里指定的 | 不存在 / 用不了 → explicit_python_unusable（reason = missing）」。
退出码：0 = 符合合同；1 = 复现到偏离（打印每一步的 code）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e1_http_probe as p  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--e1", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    e1, out = Path(args.e1).absolute(), Path(args.out).absolute()
    rec = p.load_e1(e1)
    proj = e1 / "proj"
    p.native_reference(rec["pythons"]["A"], proj, out)
    p.reset_project_state(proj)
    work = out / "vanish" / "work"
    shutil.rmtree(work, ignore_errors=True)
    cdir = out / "vanish" / "c_env"
    shutil.rmtree(cdir, ignore_errors=True)
    subprocess.run(
        ["uv", "venv", "--python", rec["base"], str(cdir)], check=True, capture_output=True
    )
    site_a = Path(rec["envs"]["A"]["pkg_file"]).parent.parent
    site_c = next((cdir / "lib").glob("python3*/site-packages"))
    (site_c / "_qa_link_a.pth").write_text(str(site_a) + "\n", encoding="utf-8")
    steps = {}
    with p.app_for(proj, work, {}) as app:
        panel = p.panel_of(app, "figure.pdf")
        st, _ = app.call(
            "/api/engine/environment", {"python": p.py_of(cdir)}, method="PATCH", timeout=180
        )
        steps["patch_status"] = st
        # 注意：这里**不先** prepare——没有热会话。PATCH 之后进程内已经选定（缓存）了 C。
        shutil.rmtree(cdir)  # 用户删掉了那个环境（或外置盘拔掉）
        _, envst = app.call("/api/engine/environment", timeout=60)
        steps["environment_resolution_error_after_delete"] = envst["project"].get(
            "resolution_error"
        )
        steps["environment_python_after_delete"] = envst["project"].get("python")
        s2 = p.prepare(app, panel["id"])
        steps["prepare_after_delete"] = {
            "status": s2["result"]["status"],
            "error": s2["result"].get("error"),
        }
        try:
            p.render(app, panel["id"])
            steps["render_after_delete"] = "ok"
        except p.fa.HttpError as exc:
            steps["render_after_delete"] = {"status": exc.status, "code": exc.body.get("code")}
    # 同一用户配置，重启后端：合同的形状出现
    with p.app_for(proj, work, {}) as app:
        panel = p.panel_of(app, "figure.pdf")
        s3 = p.prepare(app, panel["id"])
        steps["prepare_after_restart"] = {
            "status": s3["result"]["status"],
            "error": s3["result"].get("error"),
        }
    print(json.dumps(steps, indent=2, ensure_ascii=False))
    e = steps["prepare_after_delete"].get("error") or {}
    ok = (
        e.get("code") == "explicit_python_unusable"
        and (e.get("explicit") or {}).get("reason") == "missing"
    )
    print(
        "CONTRACT_OK"
        if ok
        else "DEVIATION: same-process prepare after the configured interpreter vanished "
        f"returned {e.get('code')!r} instead of explicit_python_unusable/missing"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
