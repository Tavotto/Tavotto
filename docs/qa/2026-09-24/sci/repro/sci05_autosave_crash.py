"""SCI-05 探针：自动保存 / 另存为在「写临时文件后、提交前被杀」「提交后、回响应前被杀」「磁盘满」「外部版本冲突」下的行为。

入口：Flask test client 打 `PUT /api/autosave/<id>` 与 `POST /api/layouts/<name>`（产品端点）。
进程被杀用**真子进程 + os._exit(137)**（与 SIGKILL 同形：不跑 finally / atexit，临时文件原样留在盘上）。
磁盘满用 `os.fsync` 抛 ENOSPC 注入（atomicio 的第 3 步；写入内容还在页缓存里时掉电/满盘的同形）。

判据（规范 §5 SCI-05）：
  K1 提交前被杀：磁盘上仍是已确认的 v1（字节相同），半文件不被任何读取/列表端点认成文档；
  K2 提交后回响应前被杀：盘上是**完整合法**的 v2（客户端没收到成功，不存在「谎报已保存」）；
  D1 磁盘满：响应不是 2xx、带结构化 code，v1 字节不变，无 .tmp 残留；
  C1 外部改动冲突：带过期 base_revision 的写 409，盘上内容不被覆盖、基线不推进。
用法（worktree 根目录）：bash docs/qa/2026-09-24/sci/repro/runpy.sh docs/qa/2026-09-24/sci/repro/sci05_autosave_crash.py
"""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DOC = {
    "schema": 3,
    "project": {"id": "p", "name": "n"},
    "canvases": [
        {"id": "c1", "name": "Fig 1", "page": {"w": 10, "h": 10}, "objects": [], "guides": []}
    ],
    "activeCanvasId": "c1",
    "createdAt": 0,
    "updatedAt": 1,
}

CHILD = r"""
import json, os, sys
from pathlib import Path
from tavotto import app as m
from tavotto.engine import atomicio
root = Path(sys.argv[1]); phase = sys.argv[2]; doc = json.loads(sys.argv[3])
m.LAYOUT_DIR = root; m.AUTOSAVE_DIR = root / "_autosave"; m.app.config["TESTING"] = True
if phase == "before_commit":
    atomicio.os.replace = lambda *a, **k: os._exit(137)
elif phase == "after_commit":
    atomicio._fsync_dir = lambda *a, **k: os._exit(137)
c = m.app.test_client()
r = c.put("/api/autosave/d1", json=doc)
print("CHILD-RESPONDED", r.status_code, flush=True)
"""


def _client(root: Path):
    from tavotto import app as m

    m.LAYOUT_DIR = root
    m.AUTOSAVE_DIR = root / "_autosave"
    m.app.config["TESTING"] = True
    return m, m.app.test_client()


def _kill_phase(root: Path, phase: str, doc: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", CHILD, str(root), phase, json.dumps(doc)],
        capture_output=True,
        text=True,
        timeout=120,
        env=os.environ.copy(),
    )
    return {"rc": proc.returncode, "responded": "CHILD-RESPONDED" in proc.stdout}


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="sci05-"))
    m, c = _client(root)
    slot = root / "_autosave" / "d1.json"
    out: dict = {}
    ok_all = True

    v1 = {**DOC, "updatedAt": 100}
    r = c.put("/api/autosave/d1", json=v1)
    assert r.status_code == 200, r.get_json()
    v1_bytes = slot.read_bytes()

    # K1：写好临时文件、os.replace 之前被杀
    k1 = _kill_phase(root, "before_commit", {**DOC, "updatedAt": 200})
    tmps = sorted(p.name for p in slot.parent.glob("*.tmp"))
    g = c.get("/api/autosave/d1")
    listing = c.get("/api/layouts")
    names = json.dumps(listing.get_json(), ensure_ascii=False) if listing.is_json else ""
    k1.update(
        slot_is_v1=slot.read_bytes() == v1_bytes,
        get_status=g.status_code,
        get_updatedAt=(g.get_json() or {}).get("updatedAt"),
        orphan_tmp=tmps,
        tmp_listed_as_document=any(t in names for t in tmps),
    )
    k1["ok"] = (
        k1["rc"] == 137
        and not k1["responded"]
        and k1["slot_is_v1"]
        and k1["get_updatedAt"] == 100
        and not k1["tmp_listed_as_document"]
    )
    out["K1_kill_before_commit"] = k1

    # K2：os.replace 之后、目录 fsync / 回响应之前被杀
    k2 = _kill_phase(root, "after_commit", {**DOC, "updatedAt": 300})
    g = c.get("/api/autosave/d1")
    k2.update(get_status=g.status_code, get_updatedAt=(g.get_json() or {}).get("updatedAt"))
    k2["ok"] = k2["rc"] == 137 and not k2["responded"] and k2["get_updatedAt"] == 300
    out["K2_kill_after_commit"] = k2
    confirmed = slot.read_bytes()

    # D1：磁盘满（fsync ENOSPC）
    from tavotto.engine import atomicio

    real_fsync = atomicio.os.fsync

    def enospc(fd):
        raise OSError(errno.ENOSPC, "No space left on device (injected)")

    atomicio.os.fsync = enospc
    try:
        r = c.put("/api/autosave/d1", json={**DOC, "updatedAt": 400})
    finally:
        atomicio.os.fsync = real_fsync
    tmps_after = sorted(p.name for p in slot.parent.glob("*.tmp") if p.name not in tmps)
    d1 = {
        "status": r.status_code,
        "code": (r.get_json() or {}).get("code") if r.is_json else None,
        "slot_unchanged": slot.read_bytes() == confirmed,
        "new_tmp_leftovers": tmps_after,
    }
    d1["ok"] = d1["status"] >= 400 and bool(d1["code"]) and d1["slot_unchanged"] and not tmps_after
    out["D1_disk_full"] = d1

    # C1：外部改动（同步盘/编辑器）后，拿旧 revision 写
    rev = c.get("/api/autosave/d1").headers.get("X-Tavotto-Revision") or ""
    if not rev:
        rev = atomicio.content_revision(slot)
    external = {**DOC, "updatedAt": 300, "canvases": [{**DOC["canvases"][0], "name": "外部改的"}]}
    slot.write_text(json.dumps(external, ensure_ascii=False), encoding="utf-8")
    ext_bytes = slot.read_bytes()
    r = c.put(f"/api/autosave/d1?base_revision={rev}", json={**DOC, "updatedAt": 500})
    c1 = {
        "status": r.status_code,
        "code": (r.get_json() or {}).get("code"),
        "disk_kept_external": slot.read_bytes() == ext_bytes,
    }
    c1["ok"] = c1["status"] == 409 and c1["disk_kept_external"]
    out["C1_external_conflict"] = c1

    for v in out.values():
        ok_all &= bool(v["ok"])
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print("SCI-05 autosave VERDICT:", "PASS" if ok_all else "FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
