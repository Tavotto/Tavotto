import sys, subprocess
WT="/private/tmp/claude-501/-Volumes-Projects-Tavotto/47aff04a-e334-4d42-be51-2f032b92dc64/scratchpad/qa/verify-geo"
f, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
p = f"{WT}/{f}"; s = open(p).read()
assert s.count(old) == 1, s.count(old)
open(p, "w").write(s.replace(old, new)); print("mutated", f)
