#!/usr/bin/env python3
"""品牌标志几何的唯一出处：生成 assets/brand/*.svg、assets/icon/icon.svg，
并同步 web/index.html 的 favicon、界面组件 BrandMark 的几何表、README hero
里内嵌的那一份。

标志 = Tavotto_Master_05（2026-09-11 定稿，`Tavotto_Brand_Final/master/
Tavotto_Master_05.ai` 第 1 页）：一个带「t」字横笔的圆角方框，右下角
一片被切开、挪开的弧——两条填充路径，画板 112 × 112，标志本体落在
11.5–100.5。路径是从 .ai 里原样抽出来的（三位小数），**不再分档**：
同一形从 16 px favicon 到 1024 px 应用图标都用它。第 2 页是横排组合
（标志 + 字标「Tavotto」），也一并抽出来做 tavotto-lockup.svg。

配色只有墨与浅灰两色：墨 #1b1b18，切开的那片 #d7d7cf（与界面里的
选中态 token `--color-selected` 同一个值）。**品牌里没有蓝**。

改几何只改本文件里的路径，然后重跑：

    .venv/bin/python scripts/build_brand_assets.py   # 再跑 build_icons.py 重出 .icns/.ico
    .venv/bin/python scripts/build_dmg_background.py
    .venv/bin/python scripts/build_installer_assets.py

界面内的标志由 web/src/components/ui/BrandMark.tsx 绘制，它 import 的
web/src/components/ui/brandMark.geometry.ts 由本脚本生成（色值走 CSS
token）；README hero（assets/readme/hero.svg）里的 `<g id="brand-mark">`
也由本脚本回写。这几处都不要手改。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pymupdf

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
BRAND_DIR = ROOT / "assets" / "brand"
ICON_SVG = ROOT / "assets" / "icon" / "icon.svg"
INDEX_HTML = ROOT / "web" / "index.html"
GEOMETRY_TS = ROOT / "web" / "src" / "components" / "ui" / "brandMark.geometry.ts"
HERO_SVG = ROOT / "assets" / "readme" / "hero.svg"

# ---------------------------------------------------------------------------
# 几何（画板 0 0 112 112；SVG path 语法，只用 M / L / C / Z，绝对坐标）。
# 角色：ink = 框与横笔；piece = 右下角切开挪出的那片。
# 两条路径都是 even-odd 填充、无描边。
# ---------------------------------------------------------------------------
BOX = 112.0
# 标志本体的包围盒（画板四边各留 11.5）
MARK_BOUNDS = (11.5, 11.5, 100.5, 100.5)

MARK: list[tuple[str, str]] = [
    (
        "ink",
        "M72 100.5L44.5 100.5C29.036 100.5 16.5 87.964 16.5 72.5L16.5 28.5L12.35 28.5C11.881 28.5 11.5 28.119 11.5 27.65L11.5 17.35C11.5 16.881 11.881 16.5 12.35 16.5L16.5 16.5L16.5 12.35C16.5 11.881 16.881 11.5 17.35 11.5L29.65 11.5C30.119 11.5 30.5 11.881 30.5 12.35L30.5 16.5L72.5 16.5C87.964 16.5 100.5 29.036 100.5 44.5L100.5 72L86.5 62.197L86.5 46.871C86.5 45.691 86.5 44.509 86.409 43.294C86.323 42.143 86.157 40.983 85.842 39.78C85.493 38.444 84.989 37.167 84.344 35.984C83.791 34.97 83.139 34.031 82.4 33.186C81.66 32.342 80.839 31.596 79.952 30.964C78.916 30.227 77.799 29.651 76.63 29.252C75.578 28.892 74.562 28.702 73.555 28.604C72.492 28.5 71.458 28.5 70.425 28.5L31.4 28.5C30.903 28.5 30.5 28.903 30.5 29.4L30.5 70.129C30.5 71.309 30.5 72.491 30.591 73.706C30.677 74.857 30.843 76.017 31.158 77.22C31.507 78.556 32.011 79.833 32.656 81.016C33.209 82.03 33.861 82.969 34.601 83.814C35.34 84.658 36.161 85.404 37.048 86.036C38.084 86.773 39.201 87.349 40.37 87.748C41.422 88.108 42.438 88.298 43.445 88.396C44.508 88.5 45.542 88.5 46.575 88.5L63.598 88.5L72 100.5Z",
    ),
    (
        "piece",
        "M100.336 75.548C98.926 88.576 88.577 98.925 75.547 100.337L66.993 88.118C68.027 87.958 69.074 87.73 70.145 87.41C72.082 86.83 73.933 85.995 75.649 84.926C77.119 84.01 78.48 82.93 79.705 81.704C80.93 80.479 82.011 79.119 82.926 77.648C83.995 75.933 84.831 74.081 85.41 72.145C85.932 70.399 86.207 68.717 86.35 67.049C86.385 66.634 86.412 66.223 86.433 65.812L100.336 75.548Z",
    ),
]

WORDMARK: list[str] = [
    "M271.182 24L447.325 24L447.325 56.915L378.148 56.915L378.148 241.589L340.055 241.589L340.055 56.915L271.182 56.915L271.182 24Z",
    "M570.168 206.544C570.168 210.812 570.726 213.859 571.845 215.686C572.96 217.514 575.144 218.43 578.397 218.43L582.054 218.43C583.472 218.43 585.102 218.228 586.929 217.82L586.929 241.893C585.71 242.298 584.134 242.756 582.206 243.265C580.273 243.771 578.293 244.226 576.263 244.637C574.23 245.042 572.198 245.345 570.168 245.551C568.135 245.75 566.406 245.857 564.988 245.857C557.874 245.857 551.984 244.432 547.312 241.59C542.637 238.748 539.589 233.772 538.17 226.656C531.261 233.361 522.781 238.239 512.725 241.286C502.667 244.334 492.964 245.857 483.621 245.857C476.507 245.857 469.703 244.89 463.204 242.962C456.698 241.033 450.96 238.191 445.984 234.429C441.004 230.671 437.043 225.894 434.1 220.106C431.152 214.314 429.682 207.562 429.682 199.839C429.682 190.087 431.457 182.164 435.015 176.069C438.567 169.975 443.242 165.204 449.032 161.746C454.823 158.294 461.323 155.803 468.537 154.279C475.745 152.755 483.012 151.589 490.326 150.776C496.62 149.556 502.615 148.696 508.305 148.184C513.991 147.681 519.019 146.815 523.391 145.595C527.757 144.375 531.209 142.5 533.752 139.958C536.289 137.42 537.561 133.611 537.561 128.53C537.561 124.063 536.494 120.406 534.36 117.558C532.228 114.716 529.585 112.531 526.438 111.008C523.285 109.484 519.781 108.469 515.924 107.96C512.063 107.454 508.405 107.198 504.953 107.198C495.202 107.198 487.173 109.231 480.878 113.293C474.578 117.358 471.022 123.653 470.212 132.185L435.472 132.185C436.08 122.031 438.519 113.597 442.785 106.892C447.051 100.187 452.484 94.806 459.089 90.741C465.689 86.679 473.155 83.837 481.488 82.209C489.815 80.584 498.349 79.771 507.087 79.771C514.805 79.771 522.424 80.584 529.943 82.209C537.455 83.837 544.213 86.473 550.208 90.131C556.199 93.788 561.026 98.511 564.683 104.303C568.341 110.091 570.168 117.153 570.168 125.483L570.168 206.544ZM535.427 162.66C530.142 166.118 523.642 168.198 515.924 168.909C508.201 169.623 500.481 170.689 492.763 172.108C489.107 172.718 485.549 173.585 482.097 174.698C478.64 175.817 475.593 177.34 472.955 179.269C470.312 181.203 468.232 183.742 466.708 186.887C465.184 190.039 464.422 193.848 464.422 198.315C464.422 202.178 465.537 205.431 467.775 208.068C470.007 210.71 472.697 212.791 475.85 214.314C478.997 215.838 482.45 216.907 486.211 217.514C489.969 218.124 493.373 218.43 496.421 218.43C500.277 218.43 504.444 217.924 508.915 216.907C513.381 215.892 517.599 214.163 521.562 211.725C525.523 209.288 528.824 206.193 531.466 202.431C534.105 198.672 535.427 194.051 535.427 188.563L535.427 162.66Z",
    "M594.882 84.036L632.67 84.036L672.592 205.02L673.202 205.02L711.6 84.036L747.559 84.036L691.182 241.59L652.175 241.59L594.882 84.036Z",
    "M936.182 84.036L962.39 84.036L962.39 36.801L997.131 36.801L997.131 84.036L1028.521 84.036L1028.521 109.939L997.131 109.939L997.131 194.051C997.131 197.705 997.284 200.86 997.589 203.497C997.893 206.139 998.603 208.371 999.722 210.202C1000.836 212.029 1002.512 213.401 1004.75 214.314C1006.983 215.228 1010.031 215.686 1013.892 215.686C1016.331 215.686 1018.768 215.639 1021.207 215.535C1023.644 215.433 1026.082 215.076 1028.521 214.466L1028.521 241.286C1024.659 241.691 1020.902 242.096 1017.244 242.503C1013.588 242.908 1009.827 243.113 1005.97 243.113C996.827 243.113 989.46 242.247 983.876 240.524C978.285 238.798 973.918 236.257 970.771 232.906C967.619 229.552 965.485 225.338 964.371 220.258C963.252 215.181 962.591 209.392 962.39 202.887L962.39 109.939L936.182 109.939L936.182 84.036Z",
    "M1046.582 84.036L1072.79 84.036L1072.79 36.801L1107.531 36.801L1107.531 84.036L1138.921 84.036L1138.921 109.939L1107.531 109.939L1107.531 194.051C1107.531 197.705 1107.684 200.86 1107.989 203.497C1108.293 206.139 1109.003 208.371 1110.122 210.202C1111.236 212.029 1112.912 213.401 1115.15 214.314C1117.383 215.228 1120.431 215.686 1124.292 215.686C1126.731 215.686 1129.168 215.639 1131.607 215.535C1134.044 215.433 1136.482 215.076 1138.921 214.466L1138.921 241.286C1135.059 241.691 1131.302 242.096 1127.644 242.503C1123.988 242.908 1120.227 243.113 1116.37 243.113C1107.227 243.113 1099.86 242.247 1094.276 240.524C1088.685 238.798 1084.318 236.257 1081.171 232.906C1078.019 229.552 1075.885 225.338 1074.771 220.258C1073.652 215.181 1072.991 209.392 1072.79 202.887L1072.79 109.939L1046.582 109.939L1046.582 84.036Z",
    "M812.182 106.271L871.258 106.271C882.304 106.271 891.258 116.792 891.258 129.771L891.258 183.643C891.258 203.367 875.268 219.357 855.544 219.357L812.182 219.357C801.136 219.357 792.182 208.836 792.182 195.857L792.182 129.771C792.182 116.792 801.136 106.271 812.182 106.271ZM812.182 79.771L871.258 79.771C898.872 79.771 921.258 102.157 921.258 129.771L921.258 195.857C921.258 223.471 898.872 245.857 871.258 245.857L812.182 245.857C784.568 245.857 762.182 223.471 762.182 195.857L762.182 129.771C762.182 102.157 784.568 79.771 812.182 79.771Z",
    "M1203.982 106.271L1263.058 106.271C1274.104 106.271 1283.058 116.792 1283.058 129.771L1283.058 183.643C1283.058 203.367 1267.068 219.357 1247.344 219.357L1203.982 219.357C1192.936 219.357 1183.982 208.836 1183.982 195.857L1183.982 129.771C1183.982 116.792 1192.936 106.271 1203.982 106.271ZM1203.982 79.771L1263.058 79.771C1290.672 79.771 1313.058 102.157 1313.058 129.771L1313.058 195.857C1313.058 223.471 1290.672 245.857 1263.058 245.857L1203.982 245.857C1176.368 245.857 1153.982 223.471 1153.982 195.857L1153.982 129.771C1153.982 102.157 1176.368 79.771 1203.982 79.771Z",
]

# 横排组合（第 2 页）：画板 1337.058 × 269.857，标志放大 LOCKUP_K 落在左侧，
# 字标七个字母的路径直接用画板坐标。
LOCKUP_BOX = (1337.058, 269.857)
LOCKUP_K = 2.249236
LOCKUP_MARK_OFFSET = (-1.866, 15.541)

# 配色（独立 SVG 里色值固定是可移植资产的正常需求；界面内走 CSS token）：
#   surface  任何浅底（白 / 纸色 #f2f2ef）：墨 #1b1b18，片 #d7d7cf
#   reverse  深底：整体反白 #f2f2ef，片换 #5c5c55
#   mono     单色黑（印刷）：两条路径都是纯黑
PALETTES: dict[str, dict[str, str]] = {
    "surface": {"ink": "#1b1b18", "piece": "#d7d7cf"},
    "reverse": {"ink": "#f2f2ef", "piece": "#5c5c55"},
    "mono": {"ink": "#000000", "piece": "#000000"},
}

GENERATED = "GENERATED by scripts/build_brand_assets.py — do not edit by hand"


def paths(palette: dict[str, str], indent: str = "  ") -> str:
    return "\n".join(
        f'{indent}<path d="{d}" fill="{palette[role]}" fill-rule="evenodd"/>' for role, d in MARK
    )


def mark_svg(palette_name: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {BOX:g} {BOX:g}"\n'
        f'     role="img" aria-label="Tavotto">\n'
        f"  <!-- {GENERATED} · {palette_name} -->\n"
        f"{paths(PALETTES[palette_name])}\n"
        f"</svg>\n"
    )


def lockup_svg(palette_name: str) -> str:
    palette = PALETTES[palette_name]
    w, h = LOCKUP_BOX
    ox, oy = LOCKUP_MARK_OFFSET
    word = "\n".join(
        f'  <path d="{d}" fill="{palette["ink"]}" fill-rule="evenodd"/>' for d in WORDMARK
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:g} {h:g}"\n'
        f'     role="img" aria-label="Tavotto">\n'
        f"  <!-- {GENERATED} · lockup / {palette_name} -->\n"
        f'  <g transform="translate({ox:g} {oy:g}) scale({LOCKUP_K:g})">\n'
        f"{paths(palette, indent='    ')}\n"
        f"  </g>\n"
        f"{word}\n"
        f"</svg>\n"
    )


def icon_svg() -> str:
    """桌面应用图标：标志放进现有 squircle 容器（rx 228/1024、#f2f2ef 底）。

    容器属于平台，不属于标志本体。标志本体（11.5–100.5）缩放到 688 落在
    168–857，四边留白 ≈ 1/6 边长——视觉占位与上一版图标一致。
    """
    x0, y0, x1, _ = MARK_BOUNDS
    k = 688 / (x1 - x0)
    tx = 168 - x0 * k
    ty = 168 - y0 * k
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024"\n'
        '     viewBox="0 0 1024 1024" role="img" aria-label="Tavotto">\n'
        f"  <!-- {GENERATED} -->\n"
        "  <defs>\n"
        '    <clipPath id="squircle">\n'
        '      <rect width="1024" height="1024" rx="228"/>\n'
        "    </clipPath>\n"
        "  </defs>\n"
        '  <g clip-path="url(#squircle)">\n'
        '    <rect width="1024" height="1024" fill="#f2f2ef"/>\n'
        f'    <g transform="translate({tx:.3f} {ty:.3f}) scale({k:.5f})">\n'
        f"{paths(PALETTES['surface'], indent='      ')}\n"
        "    </g>\n"
        "  </g>\n"
        "</svg>\n"
    )


def favicon_href() -> str:
    """favicon：标志本体内联 data URI，透明底、无容器。"""
    body = paths(PALETTES["surface"], indent="").replace("\n", "")
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {BOX:g} {BOX:g}'>"
        + body.replace('"', "'")
        + "</svg>"
    )
    return "data:image/svg+xml," + svg.replace("#", "%23").replace("<", "%3C").replace(">", "%3E")


def geometry_ts() -> str:
    """界面组件用的几何表：只给路径与画板，颜色由 BrandMark.tsx 按 token 填。"""
    entries = "\n".join(f"  {{ role: '{role}', d: '{d}' }}," for role, d in MARK)
    return (
        f"// {GENERATED}\n"
        "// 品牌标志的路径（画板 0 0 112 112，even-odd 填充）。改几何去 scripts/build_brand_assets.py。\n"
        "\n"
        f"export const BRAND_MARK_BOX = {BOX:g}\n"
        "\n"
        "export type BrandMarkRole = 'ink' | 'piece'\n"
        "\n"
        "export const BRAND_MARK_PATHS: ReadonlyArray<{ role: BrandMarkRole; d: string }> = [\n"
        f"{entries}\n"
        "]\n"
    )


def hero_group(size: float = 72.0) -> str:
    """README hero 里 `<g id="brand-mark">` 的内容：画板缩到 size px。"""
    k = size / BOX
    return (
        f'<g id="brand-mark" transform="translate(0 100) scale({k:.6f})" aria-hidden="true">\n'
        f"{paths(PALETTES['surface'], indent='      ')}\n"
        "    </g>"
    )


# ---------------------------------------------------------------------------
# PyMuPDF 侧的绘制（dmg 背景、安装器位图共用）。放在这里是为了让它们和 SVG
# 出自同一份路径——上一版这两个脚本各抄一遍矩形表，改几何就得改三处。
# ---------------------------------------------------------------------------
_TOKEN = re.compile(r"[MLCZ]|-?\d*\.?\d+")


def iter_subpaths(d: str):
    """把 path d（M/L/C/Z，绝对坐标）切成 [(op, (x, y), ...)] 的子路径序列。"""
    toks = _TOKEN.findall(d)
    i = 0
    cur: list[tuple] = []
    while i < len(toks):
        op = toks[i]
        i += 1
        if op == "Z":
            if cur:
                yield cur
            cur = []
            continue
        n = {"M": 2, "L": 2, "C": 6}[op]
        nums = [float(t) for t in toks[i : i + n]]
        i += n
        pts = tuple(zip(nums[0::2], nums[1::2]))
        cur.append((op, *pts))
    if cur:
        yield cur


def hex_rgb(s: str) -> tuple[float, float, float]:
    return tuple(int(s[i : i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def draw_mark(
    page: "pymupdf.Page", x: float, y: float, size: float, palette_name: str = "surface"
) -> None:
    """把标志画到 PyMuPDF 页面上：画板左上角落在 (x, y)，画板边长 = size。"""
    k = size / BOX
    palette = PALETTES[palette_name]
    shape = page.new_shape()
    for role, d in MARK:
        for sub in iter_subpaths(d):
            last = None
            for op, *pts in sub:
                pp = [(x + px * k, y + py * k) for px, py in pts]
                if op == "M":
                    last = pp[0]
                elif op == "L":
                    shape.draw_line(last, pp[0])
                    last = pp[0]
                else:
                    shape.draw_bezier(last, pp[0], pp[1], pp[2])
                    last = pp[2]
        shape.finish(color=None, fill=hex_rgb(palette[role]), even_odd=True, closePath=True)
    shape.commit()


def _sync(path: Path, pattern: str, replacement: str, label: str) -> bool:
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(pattern, lambda _m: replacement, text, flags=re.S)
    if n != 1:
        print(f"{label} 里找到 {n} 处落点（预期 1），未写入", file=sys.stderr)
        return False
    if new != text:
        path.write_text(new, encoding="utf-8")
        print(f"✓ {label}")
    else:
        print(f"· {label} 已是最新")
    return True


def main() -> int:
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        "tavotto-mark.svg": mark_svg("surface"),
        "tavotto-mark-reverse.svg": mark_svg("reverse"),
        "tavotto-mark-mono.svg": mark_svg("mono"),
        "tavotto-lockup.svg": lockup_svg("surface"),
        "tavotto-lockup-reverse.svg": lockup_svg("reverse"),
    }
    for name, text in files.items():
        (BRAND_DIR / name).write_text(text, encoding="utf-8")
        print(f"✓ assets/brand/{name}")

    ICON_SVG.write_text(icon_svg(), encoding="utf-8")
    print("✓ assets/icon/icon.svg")

    GEOMETRY_TS.write_text(geometry_ts(), encoding="utf-8")
    print("✓ web/src/components/ui/brandMark.geometry.ts")

    ok = _sync(
        INDEX_HTML,
        r'(?<=<link rel="icon" href=")[^"]*(?=" />)',
        favicon_href(),
        "web/index.html favicon",
    )
    ok = (
        _sync(HERO_SVG, r'<g id="brand-mark".*?</g>', hero_group(), "assets/readme/hero.svg") and ok
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
