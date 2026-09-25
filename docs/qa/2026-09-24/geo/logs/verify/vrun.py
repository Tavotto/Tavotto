import datetime
import os
import subprocess
import sys

WT = "/private/tmp/claude-501/-Volumes-Projects-Tavotto/47aff04a-e334-4d42-be51-2f032b92dc64/scratchpad/qa/verify-geo"
LOGS = os.path.join(WT, "docs/qa/2026-09-24/geo/logs/verify")
os.makedirs(LOGS, exist_ok=True)
name, cmd = sys.argv[1], sys.argv[2]
append = "--append" in sys.argv
sha = subprocess.run(
    ["git", "-C", WT, "rev-parse", "HEAD"], capture_output=True, text=True
).stdout.strip()
env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PWD=WT)
t0 = datetime.datetime.now()
p = subprocess.run(cmd, shell=True, cwd=WT, env=env, capture_output=True, text=True)
lines = [ln for ln in (p.stdout + p.stderr).splitlines() if "INFO werkzeug:" not in ln]
with open(os.path.join(LOGS, name + ".log"), "a" if append else "w", encoding="utf-8") as f:
    f.write(
        f"# verify: {name}\n# time: {t0.isoformat(timespec='seconds')} (+{(datetime.datetime.now() - t0).total_seconds():.0f}s)\n# HEAD: {sha}\n# cwd: <verify worktree root>\n$ {cmd}\n"
    )
    f.write("\n".join(lines) + "\n")
    f.write(f"[exit={p.returncode}]\n\n")
tail = [
    ln
    for ln in lines
    if any(
        k in ln
        for k in ("passed", "failed", "Tests ", "Test Files", "skipped", "BAD", "error", "Error")
    )
]
print(f"{name}: exit={p.returncode}")
print("\n".join(tail[-12:]))
