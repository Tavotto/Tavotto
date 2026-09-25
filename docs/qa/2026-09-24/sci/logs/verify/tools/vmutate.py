"""vmutate.py CASE LABEL target old new count -- pytest args  (verify worktree root)"""

import subprocess
import sys
from pathlib import Path

W = Path(
    "/private/tmp/claude-501/-Volumes-Projects-Tavotto/47aff04a-e334-4d42-be51-2f032b92dc64/scratchpad/qa/verify-sci"
)
V = "/private/tmp/claude-501/-Volumes-Projects-Tavotto/47aff04a-e334-4d42-be51-2f032b92dc64/scratchpad/qa/vrun.sh"
case, label, target, old, new, count, sep, *args = sys.argv[1:]
assert sep == "--"
p = W / target
src = p.read_text(encoding="utf-8")
assert src.count(old) == int(count), src.count(old)
assert not subprocess.run(
    ["git", "status", "--porcelain", "--", target], cwd=W, capture_output=True, text=True
).stdout.strip()
cmd = "bash docs/qa/2026-09-24/sci/repro/pytest.sh " + " ".join(args)
p.write_text(src.replace(old, new), encoding="utf-8")
try:
    subprocess.run(
        ["bash", V, case, f"mutation-{label}", "--", "git --no-pager diff; " + cmd], cwd=W
    )
finally:
    subprocess.run(["git", "checkout", "--", target], cwd=W, check=True)
assert p.read_text(encoding="utf-8") == src
subprocess.run(["bash", V, case, f"mutation-{label}-restored", "--", cmd], cwd=W)
print(
    "status:",
    subprocess.run(
        ["git", "status", "--porcelain"], cwd=W, capture_output=True, text=True
    ).stdout.strip()
    or "clean",
)
