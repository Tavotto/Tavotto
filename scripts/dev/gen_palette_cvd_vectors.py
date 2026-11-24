"""生成 tests/golden/palette_cvd_vectors.json：推荐配色的色差与色觉模拟对拍向量（ADR 0210 §五）。

前端 `web/src/lib/palette/cvd.ts` 自己实现 sRGB → CAM02-UCS（CIECAM02，colorspacious 的默认观察条件）
与 Machado 2009（严重度 100、模拟后夹回 sRGB 色域）的三种色觉模拟；这份向量由参考实现 colorspacious 算出，前端逐值对拍。
只在改算法或升级 colorspacious 时手动跑一次（colorspacious 不在开发依赖里）：

    uv venv /tmp/cs && uv pip install --python /tmp/cs/bin/python colorspacious numpy
    /tmp/cs/bin/python -I scripts/dev/gen_palette_cvd_vectors.py --write
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import colorspacious as cs
import numpy as np

# Windows 上流被重定向成管道时退回系统区域编码，中文报错会丢（tests/test_windows_regressions.py 看护）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "tests" / "golden" / "palette_cvd_vectors.json"
PALETTES = ROOT / "src" / "tavotto" / "profiles" / "palettes.json"

VISIONS = ("normal", "protanomaly", "deuteranomaly", "tritanomaly")

#: 极端色 + 中性灰：覆盖色域角与「模拟后出 sRGB 色域」的情形
EXTRA = ["#000000", "#FFFFFF", "#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#00FFFF", "#FF00FF", "#A6A6A6", "#BBBBBB"]

#: 拿来对拍「最小色差」的几组（ADR 0210 §五 的实测：batlowS 前三色 + 灰 = 21.4）
SETS = {
    "crameri.batlowS 3 + 灰": ["#011959", "#226061", "#C09036", "#A6A6A6"],
    "okabe-ito 前 4": ["#E69F00", "#56B4E9", "#009E73", "#F0E442"],
    "okabe-ito 绿 + 50% 灰": ["#009E73", "#808080"],
    "tab10 前 4": ["#1F77B4", "#FF7F0E", "#2CA02C", "#D62728"],
}


def _rgb(h: str) -> np.ndarray:
    return np.array([int(h[i : i + 2], 16) / 255 for i in (1, 3, 5)])


def ucs(h: str, vision: str) -> list[float]:
    rgb = _rgb(h)
    if vision != "normal":
        space = {"name": "sRGB1+CVD", "cvd_type": vision, "severity": 100}
        # 模拟结果可能出 sRGB 色域；屏幕上看到的是夹回 [0, 1] 的那个，色差也按它算
        rgb = np.clip(cs.cspace_convert(rgb, space, "sRGB1"), 0, 1)
    return [float(v) for v in cs.cspace_convert(rgb, "sRGB1", "CAM02-UCS")]


def min_delta(colors: list[str]) -> dict:
    out = {}
    for v in VISIONS:
        pts = {c: np.array(ucs(c, v)) for c in colors}
        out[v] = min(float(np.linalg.norm(pts[a] - pts[b])) for a, b in itertools.combinations(colors, 2))
    return out


def build() -> dict:
    catalog = json.loads(PALETTES.read_text(encoding="utf-8"))
    picked: list[str] = []
    for p in catalog["palettes"]:
        if p["id"] in {"okabe-ito", "tol.bright", "tol.muted", "colorbrewer.set1", "crameri.batlowS", "crameri.hawaiiS"}:
            picked += p["colors"][:6]
    colors = list(dict.fromkeys(picked + EXTRA))
    return {
        "$comment": "由 scripts/dev/gen_palette_cvd_vectors.py 用 colorspacious "
        f"{cs.__version__} 生成；前端 web/src/lib/palette/cvd.golden.test.ts 对拍。不要手改。",
        "reference": f"colorspacious {cs.__version__}: CAM02-UCS（默认观察条件）；sRGB1+CVD（Machado 2009，severity 100）后 np.clip 到 [0, 1]",
        "colors": [{"hex": c, **{v: ucs(c, v) for v in VISIONS}} for c in colors],
        "sets": [{"name": n, "colors": cols, "min_delta_e": min_delta(cols)} for n, cols in SETS.items()],
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    text = json.dumps(build(), ensure_ascii=False, indent=1) + "\n"
    if args.write:
        DEST.write_text(text, encoding="utf-8")
        print(f"wrote {DEST.relative_to(ROOT)}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
