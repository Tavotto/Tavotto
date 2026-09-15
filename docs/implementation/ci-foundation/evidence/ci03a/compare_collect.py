"""collect-only 的 nodeid 集合对比：full == s1 ∪ s2，s1 ∩ s2 == ∅。退出码 0/1。"""

import sys
from pathlib import Path


def ids(p):
    return {ln.strip() for ln in Path(p).read_text(encoding="utf-8").splitlines() if "::" in ln}


full, s1, s2 = (ids(p) for p in sys.argv[1:4])
union = s1 | s2
inter = s1 & s2
print(f"full={len(full)} s1={len(s1)} s2={len(s2)} union={len(union)} inter={len(inter)}")
print(f"missing={sorted(full - union)[:5]} extra={sorted(union - full)[:5]}")
ok = union == full and not inter and s1 and s2
print("VERDICT:", "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
