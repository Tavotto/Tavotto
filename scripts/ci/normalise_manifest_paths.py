#!/usr/bin/env python3
"""把各条构建腿的产物清单里的路径改写成「摊平后的同一个目录」。

每条腿（Python / Windows / macOS）在自己的 runner 上造清单，路径相对各自的
产物目录（`dist/…` 或 `out/…`）。汇总那一步把所有产物摊平到一个 `dist/` 里，
清单里的路径要跟着改，否则 `verify` 会说「文件不在」——而真实原因是
「它在，只是换了位置」。**诊断指错方向比不诊断更坏**（#63 的教训）。

只改 `path`，**不重算哈希**：哈希是产物的身份，摊平只是搬家。
verify 随后会拿改过的路径重新算一遍并与清单里那个比——
如果搬运途中内容变了，那一步会报出来，而不是被这一步掩盖掉。

    python3 scripts/ci/normalise_manifest_paths.py manifests --into dist
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def normalise(manifest_dir: Path, into: str) -> list[Path]:
    touched = []
    for f in sorted(manifest_dir.rglob("artifact-manifest-*.json")):
        m = json.loads(f.read_text(encoding="utf-8"))
        for a in m.get("artifacts", []):
            a["path"] = f"{into}/{os.path.basename(a['path'])}"
        f.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8")
        touched.append(f)
    return touched


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest_dir", type=Path)
    ap.add_argument("--into", default="dist")
    a = ap.parse_args(argv)

    touched = normalise(a.manifest_dir, a.into)
    if not touched:
        # **一条都没找到必须报错。** 静默成功会让下一步的 merge 拿到空集合，
        # 而 merge 对空集合的抱怨（「没有可合并的清单」）指向的是另一个问题。
        print(f"::error::{a.manifest_dir} 下一个 artifact-manifest-*.json 都没有——"
              f"上游的清单步骤没跑，或者 artifact 名字变了", file=sys.stderr)
        return 1
    for f in touched:
        print(f"normalised {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
