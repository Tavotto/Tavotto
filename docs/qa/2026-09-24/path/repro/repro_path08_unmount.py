"""PATH-08（macOS）：项目外数据放在一块**真实挂载**的磁盘映像上（`hdiutil`，不需要 root），脚本用绝对路径读。
就绪 → 卸载（detach）→ 用户重建：必须明确失败、不发布、不改读项目里的同名文件；重新挂载 → 回到真值。

期望全部成立 exit 0；任一不成立 exit 1；不是 macOS / hdiutil 不可用 exit 3（not_run）。

    env PYTHONPATH=<worktree>/src python docs/qa/2026-09-24/path/repro/repro_path08_unmount.py <worker-python>
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
sys.path.insert(0, str(ROOT / "tests"))

import test_first_open_paths_d1 as t  # noqa: E402
from support import foundation_app as fa  # noqa: E402

if sys.platform != "darwin" or not shutil.which("hdiutil"):
    print("not_run: 需要 macOS hdiutil")
    sys.exit(3)
t.WORKER_PY = sys.argv[1] if len(sys.argv) > 1 else t.WORKER_PY
tmp = Path(tempfile.mkdtemp(prefix="qa-path-p08mnt-"))
img = tmp / "ext.dmg"
mnt = tmp / "mnt"
mnt.mkdir()
subprocess.run(
    ["hdiutil", "create", "-size", "4m", "-fs", "HFS+", "-volname", "QAPATH08", str(img)],
    check=True,
    capture_output=True,
)


def attach() -> None:
    subprocess.run(
        ["hdiutil", "attach", "-nobrowse", "-mountpoint", str(mnt), str(img)],
        check=True,
        capture_output=True,
    )


def detach() -> None:
    subprocess.run(["hdiutil", "detach", str(mnt)], check=True, capture_output=True)


attach()
(mnt / "points.csv").write_bytes(t._csv(t.EXTERNAL_Y))
paper, truth = t._d1(tmp)
(paper / "points.csv").write_bytes(t._csv(t.DECOY_Y))  # 项目根同名：不许被拿来顶替
(paper / "scripts" / "ext.py").write_text(
    t._plot_script(f"_read_csv({str(mnt / 'points.csv')!r})", "ext.pdf"), encoding="utf-8"
)
t._native(paper, "scripts/ext.py", tmp)
steps: list[dict] = []
ok = True
try:
    with fa.running_app(paper, tmp / "work") as app:
        pid = t._panel(app, "ext.pdf")["id"]
        st = app.prepare(pid)
        y = t._plotted_y(app.render(pid))
        good = st["result"]["status"] == "ready" and all(
            abs(p - q) < 0.02 for p, q in zip(y, t.EXTERNAL_Y)
        )
        steps.append({"step": "mounted", "status": st["result"]["status"], "y": y, "ok": good})
        ok &= good
        detach()
        app.call("/api/engine/invalidate", {"id": pid})
        st = app.prepare(pid)
        try:
            app.render(pid)
            rcode = "ok"
        except fa.HttpError as exc:
            rcode = exc.body.get("code")
        good = st["result"]["status"] == "error" and rcode != "ok"
        steps.append(
            {
                "step": "detached + rebuild",
                "status": st["result"]["status"],
                "error": st["result"]["error"],
                "render": rcode,
                "ok": good,
            }
        )
        ok &= good
        attach()
        app.call("/api/engine/invalidate", {"id": pid})
        st = app.prepare(pid)
        y = t._plotted_y(app.render(pid))
        good = st["result"]["status"] == "ready" and all(
            abs(p - q) < 0.02 for p, q in zip(y, t.EXTERNAL_Y)
        )
        steps.append({"step": "re-mounted", "status": st["result"]["status"], "y": y, "ok": good})
        ok &= good
finally:
    subprocess.run(["hdiutil", "detach", str(mnt)], capture_output=True)
print(json.dumps(steps, ensure_ascii=False, indent=1))
print("RESULT:", "OK" if ok else "VIOLATED")
sys.exit(0 if ok else 1)
