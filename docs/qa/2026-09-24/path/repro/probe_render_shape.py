"""探针：D1 夹具经真实入口首开，打印 prepare / render 响应的形状（找全点序列能从哪儿独立读到）。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(HERE))

import make_d1  # noqa: E402

from support import foundation_app as fa  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="qa-path-probe-"))
truth = make_d1.build(tmp, decoy=False)
make_d1.native_reference(
    tmp / "paper", "/opt/homebrew/opt/python@3.13/libexec/bin/python3", tmp / "mpl"
)
with fa.running_app(tmp / "paper", tmp / "work") as app:
    _, panels = app.call("/api/panels", timeout=30)
    print(json.dumps([{k: p.get(k) for k in ("id", "script")} for p in panels["panels"]]))
    pid = next(p["id"] for p in panels["panels"] if p["id"] == "entry.pdf")
    st = app.prepare(pid)
    print(
        "status",
        st["result"]["status"],
        st["result"].get("required_input", {}) and st["result"]["required_input"].get("reason"),
    )
    app.call("/api/engine/workdir", {"mode": "project_root"}, method="PATCH")
    st = app.prepare(pid)
    print("status2", st["result"]["status"])
    print("receipt keys", sorted(st["result"]["receipt"]))
    print("inputs", json.dumps(st["result"]["receipt"]["inputs"], ensure_ascii=False))
    r = app.render(pid)
    print("render keys", sorted(r))
    for e in r["manifest"]["elements"]:
        print(e.get("gid"), e.get("role"), sorted(e)[:20])
    line = next(e for e in r["manifest"]["elements"] if e["role"] == "line")
    print(json.dumps(line["geometry"])[:1500])
    print(
        "preview keys",
        sorted(r["preview"]) if isinstance(r["preview"], dict) else type(r["preview"]),
    )
    ax = next(e for e in r["manifest"]["elements"] if e["role"] == "axes")
    print(
        "axes bbox",
        ax["bbox"],
        [(f["prop"], f["value"]) for f in ax["editable"] if f["prop"] in ("xlim", "ylim")],
    )
    _, body = app.call(
        "/api/export",
        {
            "scope": "original",
            "filename": "d1probe",
            "formats": ["pdf"],
            "overwrite": "replace",
            "original": {
                "figure_id": pid,
                "overrides": [],
                "source_kind": "figure",
                "w_mm": 81.28,
                "h_mm": 60.96,
            },
        },
        timeout=300,
    )
    print(
        "export",
        body.get("status"),
        body.get("export_dir"),
        [o.get("name") for o in body.get("outputs", [])],
    )

    pdf = (Path(body["export_dir"]) / body["outputs"][0]["name"]).read_bytes()
    print(len(pdf), pdf[:9])
    sys.path.insert(0, str(ROOT / "tests"))
    from support import pdfread

    objs = pdfread.objects(pdf)
    for num, (head, data) in objs.items():
        if data and (b" l" in data):
            print(num, head[:120], data[:600])
print(tmp)
