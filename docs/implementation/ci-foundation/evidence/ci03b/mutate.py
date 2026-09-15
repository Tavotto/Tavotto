"""变异反证：先断言目标串恰好一次 → 变异 → 清 __pycache__ → pytest（退出码判）→ 还原核 md5。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/Volumes/Projects/tavotto-wt/ci-foundation")
PY = ROOT / ".venv" / "bin" / "python"


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def clear_pycache():
    for d in (
        list(ROOT.glob("tests/__pycache__"))
        + list(ROOT.glob("tests/support/__pycache__"))
        + list(ROOT.glob("scripts/ci/__pycache__"))
        + list(ROOT.glob("scripts/__pycache__"))
    ):
        shutil.rmtree(d, ignore_errors=True)


def run(mutations: list[dict], out_path: Path, label: str) -> int:
    results = []
    for m in mutations:
        f = ROOT / m["file"]
        original = f.read_text(encoding="utf-8")
        before = md5(f)
        n = original.count(m["old"])
        if n != 1:
            results.append(
                {
                    **{k: m[k] for k in ("id", "file", "why")},
                    "verdict": "NOT_APPLIED",
                    "occurrences": n,
                }
            )
            print(f"{m['id']}: NOT_APPLIED (occurrences={n})")
            continue
        mutated = original.replace(m["old"], m["new"])
        assert mutated != original
        f.write_text(mutated, encoding="utf-8")
        assert md5(f) != before, "变异没落到文件上"
        clear_pycache()
        t0 = time.time()
        try:
            proc = subprocess.run(
                [
                    str(PY),
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "--no-header",
                    "-rf",
                    *m["tests"],
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                timeout=900,
            )
            rc = proc.returncode
            tail = "\n".join(
                [ln for ln in proc.stdout.splitlines() if ln.startswith("FAILED ")][:8]
                + proc.stdout.splitlines()[-2:]
            )
        finally:
            f.write_text(original, encoding="utf-8")
            assert md5(f) == before, f"{m['id']}: 还原后 md5 不一致！"
            clear_pycache()
        verdict = "KILLED" if rc != 0 else "SURVIVED"
        failed = [ln.split(" ")[1] for ln in proc.stdout.splitlines() if ln.startswith("FAILED ")]
        results.append(
            {
                "id": m["id"],
                "file": m["file"],
                "why": m["why"],
                "pytest_rc": rc,
                "verdict": verdict,
                "failed": failed[:6],
                "seconds": round(time.time() - t0, 1),
                "tail": tail,
            }
        )
        print(f"{m['id']}: {verdict} rc={rc} {failed[:3]}")
        # 变异期间可能留下孤儿桩
        subprocess.run(["pkill", "-f", "stub_http_server.py"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "time.sleep"], capture_output=True)
    killed = sum(r["verdict"] == "KILLED" for r in results)
    summary = {"label": label, "killed": killed, "total": len(results), "results": results}
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"== {label}: {killed}/{len(results)} KILLED")
    return 0 if killed == len(results) else 1


if __name__ == "__main__":
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    sys.exit(run(spec["mutations"], Path(sys.argv[2]), spec["label"]))
