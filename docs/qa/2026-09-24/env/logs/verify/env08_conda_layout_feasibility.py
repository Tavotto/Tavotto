"""复核补充（ENV-08 可行性）：ADR 0079 的发现「只读磁盘记录、不问 CLI」，所以不装 conda 也能在隔离 HOME
里摆出 conda 的**目录布局**（~/miniforge3 = base，~/miniforge3/envs/lab = 具名环境），里面放**真实**解释器
（uv venv）。base 只有 matplotlib；lab 有 matplotlib + qa_probe_pkg。项目没有 .venv。
判据：产品最终在哪个解释器里跑了脚本（脚本自报 sys.prefix）、点序列、环境状态的来源/标签；不能是 base。
注意：这不是真实 conda 环境（没有 conda-meta / conda 装的包），只证明发现+采用链路在真解释器上走得通。端口 5403。"""

import json
import shutil
import sys
from pathlib import Path

REPRO = Path(sys.argv[1])
E1 = Path(sys.argv[2])
OUT = Path(sys.argv[3])
sys.path.insert(0, str(REPRO))
import e1_http_probe as p  # noqa
import make_e1 as m  # noqa

p.fa.free_port = lambda: 5403
rec = p.load_e1(E1)
base = rec["base"]
d = OUT / "ENV-08"
shutil.rmtree(d, ignore_errors=True)
proj = d / "proj"
proj.mkdir(parents=True)
(proj / "figure.py").write_text(m.FIGURE_PY, encoding="utf-8")
work = d / "work"
home = work / "home"
conda_base = home / "miniforge3"
lab = conda_base / "envs" / "lab"
whl = E1 / "wheels" / "qa_probe_pkg-1.0.0-py3-none-any.whl"
m.make_venv(conda_base, base, m.MPL_SPEC)
m.make_venv(lab, base, m.MPL_SPEC, str(whl))
(home / ".conda").mkdir(parents=True, exist_ok=True)
(home / ".conda" / "environments.txt").write_text(f"{conda_base}\n{lab}\n")
(proj / "environment.yml").write_text("name: lab\ndependencies:\n  - matplotlib\n")
p.native_reference(p.py_of(lab), proj, d)
ok = True
with p.app_for(proj, work, {}) as app:
    panel = p.panel_of(app, "figure.pdf")
    st = p.prepare(app, panel["id"])
    print(
        "prepare:",
        st["result"]["status"],
        json.dumps(st["plan"].get("environment"), ensure_ascii=False, default=str)[:1500],
    )
    (OUT / "ENV-08" / "prepare_state.json").write_text(
        json.dumps(st, ensure_ascii=False, indent=1, default=str)
    )
    print(
        "prepare result error:",
        json.dumps(st["result"].get("error"), ensure_ascii=False, default=str)[:800],
    )
    _, dep = app.call("/api/engine/dependencies?script=figure.py", timeout=120)
    (OUT / "ENV-08" / "dependencies.json").write_text(
        json.dumps(dep, ensure_ascii=False, indent=1, default=str)
    )
    ofr = (dep or {}).get("offer") or {}
    print(
        "offer.user_environments:",
        json.dumps(ofr.get("user_environments"), ensure_ascii=False, default=str)[:1200],
    )
    print(
        "offer.plan.missing/unknown:",
        json.dumps(
            {k: (ofr.get("plan") or {}).get(k) for k in ("missing", "unknown")},
            ensure_ascii=False,
            default=str,
        )[:800],
    )
    offer = st["plan"].get("dependency_preparation") or st["result"].get("required_input")
    print("prep/required_input:", json.dumps(offer, ensure_ascii=False, default=str)[:1500])
    try:
        r = p.render(app, panel["id"])
        ident = p.ident_from(r)
        vals = p.values_from(r)
        print("render OK values:", vals, "prefix:", ident and ident.get("prefix"))
        in_lab = bool(ident) and p.same_path(ident.get("prefix"), str(lab))
        in_base = bool(ident) and p.same_path(ident.get("prefix"), str(conda_base))
        print("ran in named env lab:", in_lab, "| ran in base:", in_base)
        ok = in_lab and vals == [0, 9, 2, 10]
    except p.fa.HttpError as exc:
        print("render FAILED:", json.dumps(exc.body, ensure_ascii=False, default=str)[:1500])
        ok = False
    _, envst = app.call("/api/engine/environment", timeout=60)
    pj = envst.get("project") or {}
    print(
        "environment.project:",
        {k: pj.get(k) for k in ("source", "source_label", "trigger", "automatic", "python")},
    )
print("RESULT", "PASS" if ok else "FAIL/NOT-ADOPTED")
sys.exit(0 if ok else 1)
