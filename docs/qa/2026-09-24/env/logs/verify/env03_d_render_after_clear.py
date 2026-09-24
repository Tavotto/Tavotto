"""复核补充：ENV-03 (d') 清掉失效的项目手选后，prepare ready 的来源是 current_process——
那么真正渲染用户脚本能否成功（脚本 import qa_probe_pkg，只装在项目 .venv A 里）？端口 5403。"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPRO = Path(sys.argv[1])
E1 = Path(sys.argv[2])
OUT = Path(sys.argv[3])
sys.path.insert(0, str(REPRO))
import e1_http_probe as p  # noqa

p.fa.free_port = lambda: 5403
rec = p.load_e1(E1)
A = rec["envs"]["A"]
proj = E1 / "proj"
p.native_reference(rec["pythons"]["A"], proj, OUT)
site_a = Path(A["pkg_file"]).parent.parent
p.reset_project_state(proj)
work = OUT / "d_extra"
shutil.rmtree(work, ignore_errors=True)
ddir = OUT / "d_extra_env"
shutil.rmtree(ddir, ignore_errors=True)
subprocess.run(["uv", "venv", "--python", rec["base"], str(ddir)], check=True, capture_output=True)
site_d = next((ddir / "lib").glob("python3*/site-packages"))
(site_d / "_qa_link_a.pth").write_text(str(site_a) + "\n")
with p.app_for(proj, work, {}) as app:
    panel = p.panel_of(app, "figure.pdf")
    st, body = app.call(
        "/api/engine/environment",
        {"python": p.py_of(ddir), "scope": "project"},
        method="PATCH",
        timeout=180,
    )
    s = p.prepare(app, panel["id"])
    print("D chosen:", s["result"]["status"], s["plan"]["environment"].get("source"))
    (site_d / "_qa_link_a.pth").unlink()
with p.app_for(proj, work, {}) as app:
    panel = p.panel_of(app, "figure.pdf")
    s = p.prepare(app, panel["id"])
    print("D broken:", s["result"]["status"], (s["result"].get("error") or {}).get("code"))
    app.call(
        "/api/engine/environment", {"python": "", "scope": "project"}, method="PATCH", timeout=120
    )
    _, envst = app.call("/api/engine/environment", timeout=60)
    print(
        "environment.project after clear:",
        json.dumps(envst.get("project"), ensure_ascii=False, default=str)[:1500],
    )
    s = p.prepare(app, panel["id"])
    print(
        "prepare after clear:",
        s["result"]["status"],
        json.dumps(s["plan"].get("environment"), ensure_ascii=False, default=str)[:1500],
    )
    try:
        r = p.render(app, panel["id"])
        print("render after clear OK; values:", p.values_from(r), "ident:", p.ident_from(r))
        ok = p.values_from(r) == A["value"]
        rc = r.get("receipt") or {}
        print(
            "render receipt:", {k: rc.get(k) for k in ("python_source", "generation", "pid_check")}
        )
        _, envst2 = app.call("/api/engine/environment", timeout=60)
        pj = envst2.get("project") or {}
        print(
            "environment.project after render:",
            {k: pj.get(k) for k in ("source", "trigger", "automatic", "python")},
        )
        lg = app.server_log()
        print("server log mentions missing_dependency:", "missing_dependency" in lg)
        label_ok = pj.get("source") == "project_venv"
        print("status label consistent with actual interpreter:", label_ok)
    except p.fa.HttpError as exc:
        print(
            "render after clear FAILED:",
            json.dumps(exc.body, ensure_ascii=False, default=str)[:1500],
        )
        ok = False
shutil.rmtree(ddir, ignore_errors=True)
print("RESULT", "PASS" if ok else "FAIL (清除后未回到项目 .venv / 渲染未成功)")
sys.exit(0 if ok else 1)
