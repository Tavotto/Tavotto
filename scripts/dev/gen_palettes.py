"""重新生成推荐配色目录（ADR 0210）：`src/tavotto/profiles/palettes.json` 与 `palettes_copyleft.json`。

目录里存的是**色值本身**，运行时不依赖下面任何一个包（免得热态、导出、Agent 三处读到不同版本）。
这份脚本只在维护者升级来源版本时手动跑一次，CI 不跑它（这些包不在开发依赖里）：

    uv venv /tmp/pal && uv pip install --python /tmp/pal/bin/python \\
        cmcrameri==1.10 tol-colors==2.2.0 matplotlib pypalettes
    /tmp/pal/bin/python -I scripts/dev/gen_palettes.py --write

来源与许可（全文在 `THIRD_PARTY_NOTICES.md`）：

- Okabe–Ito：色值是公开的事实性资料（Okabe & Ito 2008），仓库里早已内置（`web/src/lib/swatches.ts`）。
- Paul Tol：tol-colors（BSD-3-Clause），方案出自 SRON 技术说明。
- ColorBrewer：取 matplotlib 内置的那份（ColorBrewer 本身 Apache-2.0，须带致谢句）。
- Crameri 科学色图：cmcrameri 1.10 带的数据（MIT，Copyright Fabio Crameri）；分类 S 系列。
- ggsci 期刊风格（NPG、AAAS、NEJM、Lancet、JAMA、JCO）：GPL-3.0-or-later，**单独**写进
  `palettes_copyleft.json`，闭源高级版组件不许读那份（ADR 0210 §三）。色值经 pypalettes 的数据表转录
  （那张表的来源列写的是「The R package: {ggsci}」），与 ggsci 原包逐值一致。

Crameri 的 S 系列每套 100 色、按区分度排好序，但里面混着很浅的颜色（对白底对比度不到 2:1），
画成细线和小点几乎看不见。目录只收**对白底对比度 ≥ 3:1** 的那些（保留原顺序），每套最多 12 个；
够不上 6 个的那一套不收。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Windows 上流被重定向成管道时退回系统区域编码，中文报错会丢（tests/test_windows_regressions.py 看护）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "src" / "tavotto" / "profiles"

MAX_COLORS = 12
MIN_COLORS = 6
MIN_CONTRAST = 3.0
NEUTRAL = "#A6A6A6"


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def contrast_on_white(hex_: str) -> float:
    r, g, b = (int(hex_[i : i + 2], 16) / 255 for i in (1, 3, 5))
    y = 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)
    return 1.05 / (y + 0.05)


def _hex(rgb) -> str:
    return "#" + "".join(f"{round(float(v) * 255):02X}" for v in rgb[:3])


def crameri() -> list[dict]:
    import cmcrameri

    cmaps = Path(cmcrameri.__file__).parent / "cmaps"
    out = []
    for path in sorted(cmaps.glob("*S.txt")):
        name = path.stem
        if name == "grayCS":  # 中性灰阶：只当对照色的来源，不是一套分类色板
            continue
        rows = [line.split() for line in path.read_text().splitlines() if line.strip()]
        colors = [_hex([float(v) for v in row]) for row in rows]
        usable = [c for c in colors if contrast_on_white(c) >= MIN_CONTRAST][:MAX_COLORS]
        if len(usable) < MIN_COLORS:
            continue
        out.append(
            {
                "id": f"crameri.{name}",
                "name": f"{name[:-1]} S",
                "source": "crameri",
                "kind": "categorical",
                "colors": usable,
            }
        )
    return out


def tol() -> list[dict]:
    import tol_colors as tc

    picks = {
        "bright": ("Bright", "grey"),
        "vibrant": ("Vibrant", "grey"),
        "muted": ("Muted", "pale_grey"),
        "high_contrast": ("High contrast", None),
    }
    out = []
    for key, (label, grey) in picks.items():
        items = list(tc.colorsets[key]._asdict().items())
        colors = [v.upper() for k, v in items if k not in (grey, "black", "white")]
        entry = {
            "id": f"tol.{key.replace('_', '-')}",
            "name": f"Tol {label}",
            "source": "tol",
            "kind": "categorical",
            "colors": colors,
        }
        # 方案自带的灰（bright / vibrant 的 #BBBBBB）就是作者给的中性色；muted 的 pale_grey 是「缺失数据」色，不当对照
        if grey == "grey":
            entry["neutral"] = dict(items)[grey].upper()
        out.append(entry)
    return out


def colorbrewer() -> list[dict]:
    import matplotlib as mpl

    out = []
    for name in ("Set1", "Set2", "Dark2", "Paired"):
        colors = [mpl.colors.to_hex(c).upper() for c in mpl.colormaps[name].colors]
        out.append(
            {
                "id": f"colorbrewer.{name.lower()}",
                "name": name,
                "source": "colorbrewer",
                "kind": "categorical",
                "colors": colors,
            }
        )
    return out


def okabe_ito() -> list[dict]:
    # 与 web/src/lib/swatches.ts 的 OKABE_ITO 同值同序（黑色放最后：科研图里它多半是轴与文字）
    colors = [
        "#E69F00",
        "#56B4E9",
        "#009E73",
        "#F0E442",
        "#0072B2",
        "#D55E00",
        "#CC79A7",
        "#000000",
    ]
    return [
        {
            "id": "okabe-ito",
            "name": "Okabe–Ito",
            "source": "okabe-ito",
            "kind": "categorical",
            "colors": colors,
        }
    ]


def ggsci() -> list[dict]:
    import ast
    import csv

    import pypalettes

    table = Path(pypalettes.__file__).parent / "palettes.csv"
    with table.open(encoding="utf-8") as fh:
        rows = {r["name"]: r for r in csv.DictReader(fh)}
    picks = [
        ("nrc_npg", "npg", "NPG", "Nature Publishing Group"),
        ("default_aaas", "aaas", "AAAS", "Science (AAAS)"),
        ("default_nejm", "nejm", "NEJM", "The New England Journal of Medicine"),
        ("lanonc_lancet", "lancet", "Lancet", "The Lancet Oncology"),
        ("default_jama", "jama", "JAMA", "Journal of the American Medical Association"),
        ("default_jco", "jco", "JCO", "Journal of Clinical Oncology"),
    ]
    out = []
    for src, key, label, style_of in picks:
        row = rows[src]
        if "{ggsci}" not in row["source"]:
            raise SystemExit(f"{src}: 来源不是 ggsci（{row['source']}）")
        colors = [c[:7].upper() for c in ast.literal_eval(row["palette"])]
        out.append(
            {
                "id": f"ggsci.{key}",
                "name": label,
                "style_of": style_of,
                "source": "ggsci",
                "kind": "categorical",
                "colors": colors,
            }
        )
    return out


SOURCES = {
    "okabe-ito": {
        "name": "Okabe & Ito, Color Universal Design",
        "license": "LicenseRef-Factual",
        "url": "https://jfly.uni-koeln.de/color/",
        "cite": "Okabe M, Ito K (2008). Color Universal Design (CUD): how to make figures and presentations that are friendly to colorblind people.",
    },
    "tol": {
        "name": "Paul Tol, SRON colour schemes",
        "license": "BSD-3-Clause",
        "url": "https://personal.sron.nl/~pault/",
        "version": "tol-colors 2.2.0",
        "cite": "Tol P (2021). Colour Schemes. SRON Technical Note SRON/EPS/TN/09-002, issue 3.2.",
    },
    "colorbrewer": {
        "name": "ColorBrewer",
        "license": "Apache-2.0",
        "url": "https://colorbrewer2.org/",
        "attribution": "This product includes color specifications and designs developed by Cynthia Brewer (http://colorbrewer.org/).",
    },
    "crameri": {
        "name": "Scientific colour maps (Fabio Crameri)",
        "license": "MIT",
        "url": "https://www.fabiocrameri.ch/colourmaps/",
        "version": "8 (cmcrameri 1.10)",
        "doi": "10.5281/zenodo.1243862",
        "cite": "Crameri F, Shephard GE, Heron PJ (2020). The misuse of colour in science communication. Nature Communications 11, 5444.",
    },
}

COPYLEFT_SOURCES = {
    "ggsci": {
        "name": "ggsci (Nan Xiao)",
        "license": "GPL-3.0-or-later",
        "url": "https://nanx.me/ggsci/",
        "cite": "Xiao N. ggsci: Scientific Journal and Sci-Fi Themed Color Palettes for ggplot2.",
        "note": "Colours inspired by the named journals' figure styles; not endorsed by any of them.",
    }
}


def build() -> tuple[dict, dict]:
    permissive = {
        "$comment": "推荐配色目录（ADR 0210）。只收许可证宽松的方案；GPL 的期刊风格色板在 palettes_copyleft.json。"
        "重新生成：scripts/dev/gen_palettes.py。前端经 @palettes 别名整份 import，引擎经 engine/palettes.py 读，两边是同一个文件。",
        "version": 1,
        "neutral": NEUTRAL,
        "sources": SOURCES,
        "palettes": okabe_ito() + tol() + colorbrewer() + crameri(),
    }
    copyleft = {
        "$comment": "GPL-3.0-or-later 的期刊风格色板（ADR 0210 §三）：与 AGPL 的开源版兼容，但闭源高级版组件不许读这份文件。",
        "version": 1,
        "copyleft": True,
        "sources": COPYLEFT_SOURCES,
        "palettes": ggsci(),
    }
    return permissive, copyleft


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="写回 src/tavotto/profiles/")
    args = ap.parse_args(argv)
    permissive, copyleft = build()
    for name, data in (("palettes.json", permissive), ("palettes_copyleft.json", copyleft)):
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if args.write:
            (OUT / name).write_text(text, encoding="utf-8")
            print(f"wrote {os.path.relpath(OUT / name, ROOT)} ({len(data['palettes'])} palettes)")
        else:
            sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
