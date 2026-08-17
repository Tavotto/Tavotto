"""从「写回原始文件」的 PDF 反推 figure 锚定 override 的真实位置，修复错位文档。

背景（2026-08-17 FigS3 事故）：overrides.apply 曾按「最后修改顺序」应用且值不变
即跳过，pos_frac/loc_frac/endpoints_frac 在应用那一刻换算进 artist 本地坐标，
后续再挪子图/改图幅时 artist 跟着几何漂移——写回的 PDF 定格的是漂移后的样子，
文档里存的分数位置却对不上。引擎已修（应用顺序规范化 + 几何变动重放），
但事故期间保存的文档需要把这些 override 改写成写回 PDF 里的真实位置。

做法（对文档里每个有风险的面板）：
  1. 起隔离数据目录的 magplot 实例，用文档 overrides 全量重放，拿 manifest
     （修复后的引擎里，重放位置 == 文档声明位置）；
  2. 读写回 PDF 的**矢量文字层**（get_text 的行 bbox，精确坐标、无栅格误差）；
  3. 按归一化文本内容配对（mathtext 标记剥掉）；重复文字（两个 "100 W"）靠
     「同一子图的文字漂移量一致」这一物理约束解开——漂移只来自子图移动，
     同轴成员必然同步平移；
  4. 配不上的一律不动并逐条报告——绝不猜。

用法：
  .venv/bin/python scripts/recover_frac_positions.py \
      --figures <项目目录> --doc <文档.json> --out <修正后.json> [--port 5188]

输出的修正文档**另存**，绝不覆盖输入；把它 POST 成一个布局版本、再从界面的
版本历史恢复（可撤销）：
  curl -X POST "http://127.0.0.1:5089/api/versions/<docId>?pj=<pj>" \
       -H 'Content-Type: application/json' \
       -d '{"name": "文字位置恢复", "doc": <文件内容>}'
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
FRAC_PROPS = {"pos_frac", "loc_frac", "endpoints_frac"}
GEOM_PROPS = {"position", "size_mm"}
DELTA_TOL = 0.006      # 同轴成员漂移一致性的容差（figure 分数）
MIN_DELTA = 0.002      # 小于它视作没漂，不改写


def start_server(figures: Path, port: int, scratch: Path) -> subprocess.Popen:
    env = dict(os.environ,
               MAGPLOT_DATA_DIR=str(scratch / "data"),
               MAGPLOT_CONFIG_DIR=str(scratch / "cfg"))
    proc = subprocess.Popen(
        [str(ROOT / ".venv/bin/magplot"), "--figures", str(figures),
         "--no-browser", "--port", str(port)],
        env=env, cwd=str(ROOT),
        stdout=open(scratch / "srv.log", "w"), stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            urllib.request.urlopen(base + "/api/panels", timeout=5).read()
            return proc
        except Exception:
            time.sleep(0.3)
    proc.kill()
    raise SystemExit("隔离实例没起来，见 " + str(scratch / "srv.log"))


def req(base: str, path: str, body=None, timeout=900):
    r = urllib.request.Request(base + path)
    if body is not None:
        r.add_header("Content-Type", "application/json")
        r.data = json.dumps(body).encode()
    return urllib.request.urlopen(r, timeout=timeout)


_TEX_JUNK = re.compile(r"[\\${}^_\s]|mathrm|mathit|mathbf|text")
_DASHES = str.maketrans({"−": "-", "–": "-", "—": "-"})


def norm_text(s: str) -> str:
    """mathtext 源码与 PDF 抽取文本都归一到可比形态。"""
    return _TEX_JUNK.sub("", str(s)).translate(_DASHES)


def pdf_text_lines(src: Path) -> list[dict]:
    """写回 PDF 的文字行：{"norm", "bbox"}（行框，figure 分数、y 向下）。
    行框只用来圈定墨迹搜索区——metric 框（含上下伸部、受 mathtext 下标影响）
    与 matplotlib 的布局框互相都有系统偏差，真正的对应点用两边**墨迹紧框
    中心**：字形完全相同，紧框中心无偏。"""
    out = []
    with pymupdf.open(src) as doc:
        page = doc[0]
        W, H = page.rect.width, page.rect.height
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = "".join(s["text"] for s in spans)
                if not text.strip() or not spans:
                    continue
                x0, y0, x1, y1 = line["bbox"]
                out.append({"norm": norm_text(text),
                            "bbox": (x0 / W, y0 / H,
                                     (x1 - x0) / W, (y1 - y0) / H)})
    return out


def tight_ink_center(gray: "Gray", fx: float, fy: float, fw: float, fh: float,
                     pad: int = 3) -> tuple[float, float] | None:
    """区域内墨迹紧框的中心（px）。"""
    x0 = max(0, int(fx * gray.w) - pad)
    y0 = max(0, int(fy * gray.h) - pad)
    x1 = min(gray.w, int((fx + fw) * gray.w) + pad)
    y1 = min(gray.h, int((fy + fh) * gray.h) + pad)
    ix0 = iy0 = 10 ** 9
    ix1 = iy1 = -1
    for y in range(y0, y1):
        row = gray.data[y * gray.stride: y * gray.stride + gray.w]
        for x in range(x0, x1):
            if 255 - row[x] > 40:
                if x < ix0: ix0 = x
                if x > ix1: ix1 = x
                if y < iy0: iy0 = y
                if y > iy1: iy1 = y
    if ix1 < 0:
        return None
    return ((ix0 + ix1) / 2, (iy0 + iy1) / 2)


class Gray:
    """灰度栅格 + 像素访问（墨迹为正）。"""

    def __init__(self, pix: pymupdf.Pixmap):
        g = pymupdf.Pixmap(pymupdf.csGRAY, pix)
        if g.alpha:
            g = pymupdf.Pixmap(g, 0)
        self.w, self.h, self.stride = g.width, g.height, g.stride
        self.data = bytes(g.samples)

    def ink(self, x: int, y: int) -> int:
        return 255 - self.data[y * self.stride + x]


RASTER_W = 1600
REFINE_R = 10


def refine_delta(replay: Gray, baked: Gray, bbox_px: tuple[int, int, int, int],
                 dx_px: int, dy_px: int) -> tuple[int, int] | None:
    """基线对基线的 Δ 已经很准；非默认对齐（ha/va）的小残差在 ±REFINE_R px
    内按墨迹 SAD 精配掉。"""
    x0, y0, bw, bh = bbox_px
    pts = [(x, y, replay.ink(x, y))
           for y in range(max(0, y0), min(replay.h, y0 + bh))
           for x in range(max(0, x0), min(replay.w, x0 + bw))
           if replay.ink(x, y) > 40]
    if len(pts) < 30:
        return None
    if len(pts) > 1200:  # 大块文字抽稀，够定位就行
        pts = pts[:: len(pts) // 1200 + 1]

    def sad_at(ox: int, oy: int) -> int:
        sad = 0
        for x, y, v in pts:
            bx2, by2 = x + ox, y + oy
            if 0 <= bx2 < baked.w and 0 <= by2 < baked.h:
                sad += abs(v - baked.ink(bx2, by2))
            else:
                sad += v
        return sad

    best = None
    for oy in range(dy_px - REFINE_R, dy_px + REFINE_R + 1):
        for ox in range(dx_px - REFINE_R, dx_px + REFINE_R + 1):
            sad = sad_at(ox, oy)
            if best is None or sad < best[0]:
                best = (sad, ox, oy)
    if best is None:
        return None
    # 精配滑到搜索边界 = 没找到可信的墨迹极小值（背景太花或本就对不上），
    # 弃用精配、保留基线对基线的文本层值
    if abs(best[1] - dx_px) >= REFINE_R or abs(best[2] - dy_px) >= REFINE_R:
        return None
    return (best[1], best[2])


def panels_of(doc: dict):
    for canvas in (doc.get("canvases") or [doc]):
        for o in canvas.get("objects", []):
            if o.get("type") == "panel":
                yield o


def at_risk(panel: dict) -> bool:
    props = {p.get("prop") for p in panel.get("overrides", [])}
    return bool(props & FRAC_PROPS) and bool(props & GEOM_PROPS)


def axes_group(gid: str) -> str:
    return gid.split(".", 1)[0] if "." in gid else gid


def recover_panel(base: str, figures: Path, panel: dict,
                  verbose: bool = False) -> tuple[int, list[str]]:
    """就地修正 panel['overrides'] 里的 frac 锚定值。返回 (改写条数, 报告)。"""
    rel_id = panel["fileId"]
    src = figures / rel_id
    report: list[str] = []
    if not src.is_file():
        return 0, [f"{rel_id}: 源文件不存在，跳过"]

    patches = panel["overrides"]
    resp = json.load(req(base, "/api/engine/render",
                         {"id": quote(rel_id, safe='/'), "patches": patches}))
    elements = {e["gid"]: e for e in resp["manifest"]["elements"]}
    lines = pdf_text_lines(src)

    # 局部精配用的两张栅格：重放（引擎 PNG）与写回 PDF，同一像素尺度
    png = req(base, f"/api/engine/png?id={quote(rel_id, safe='/')}&w={RASTER_W}").read()
    replay = Gray(pymupdf.Pixmap(png))
    with pymupdf.open(src) as pdf:
        z = replay.w / pdf[0].rect.width
        baked = Gray(pdf[0].get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False))
    W, H = replay.w, replay.h

    # 每条待修 override：重放中心（== 文档声明的落位）+ 文本内容 → 候选行
    items = []
    for p in patches:
        if p.get("prop") not in FRAC_PROPS:
            continue
        el = elements.get(p["gid"])
        if el is None:
            report.append(f"{p['gid']}: 重放 manifest 里找不到，未改动")
            continue
        text = next((f.get("value") for f in el.get("editable", [])
                     if f.get("prop") == "text"), None)
        if not text:
            report.append(f"{p['gid']}: 非文字元素（{p['prop']}），此工具不处理")
            continue
        bx, by, bw, bh = el["bbox"]
        norm = norm_text(text)
        rc = tight_ink_center(replay, bx, by, bw, bh)
        if rc is None:
            report.append(f"{p['gid']}: 重放里取不到墨迹，未改动")
            continue
        cands = []
        for i, ln in enumerate(lines):
            if ln["norm"] != norm:
                continue
            bc = tight_ink_center(baked, *ln["bbox"])
            if bc is None:
                continue
            cands.append(((bc[0] - rc[0]) / W, (bc[1] - rc[1]) / H, i))
        items.append({"patch": p, "gid": p["gid"], "cands": cands,
                      "group": axes_group(p["gid"]),
                      "bbox_px": (int(bx * W) - 2, int(by * H) - 2,
                                  int(bw * W) + 4, int(bh * H) + 4)})
        if verbose:
            print(f"  ? {p['gid']} 「{norm}」 cands="
                  + "; ".join(f"({dx:+.4f},{dy:+.4f})" for dx, dy, _ in cands))
        if not cands:
            report.append(f"{p['gid']}: 写回 PDF 里找不到文本「{text}」，未改动")

    # 分配：按「最小候选漂移」从小到大处理——没怎么动的文字最可信，先让它们
    # 认领各自的行；一行只许被认领一次，重复文字靠认领排除 + 同轴已定成员的
    # Δ 参考解开。注意同轴成员的漂移**可以不同**（挪轴后又单独拖过的文字），
    # 所以这里不做「同组必同 Δ」的硬约束。
    def mag(c):
        return (c[0] ** 2 + c[1] ** 2) ** 0.5

    claimed: set[int] = set()
    changed = 0
    sibling_deltas: dict[str, list[tuple[float, float]]] = {}
    order = sorted((it for it in items if it["cands"]),
                   key=lambda it: min(mag(c) for c in it["cands"]))
    for m in order:
        free = sorted((c for c in m["cands"] if c[2] not in claimed), key=mag)
        pick = None
        if not free:
            report.append(f"{m['gid']}: 候选行已被别的元素认领，未改动")
            continue
        if len(free) == 1 or mag(free[1]) >= mag(free[0]) + 0.05:
            # 唯一候选，或与次近候选拉开显著差距（重复标签在别的列/行，
            # 间距远大于真实漂移）
            pick = free[0]
        else:
            near = [c for c in free
                    if any(abs(c[0] - sx) < DELTA_TOL and abs(c[1] - sy) < DELTA_TOL
                           for sx, sy in sibling_deltas.get(m["group"], []))]
            if len(near) == 1:
                pick = near[0]
        if pick is None or mag(pick) > 0.35:
            report.append(f"{m['gid']}: 候选分不开或漂移离谱，未改动")
            continue
        dx, dy, idx = pick
        claimed.add(idx)
        sibling_deltas.setdefault(m["group"], []).append((dx, dy))
        # 文本层只到行框中心精度：±REFINE_R px 内按墨迹精配，
        # 顺带把「本来没漂、只是行框中心偏差」的项归零
        raw = (dx, dy)
        refined = refine_delta(replay, baked, m["bbox_px"],
                               round(dx * W), round(dy * H))
        if refined is not None:
            dx, dy = refined[0] / W, refined[1] / H
        if verbose:
            print(f"  = {m['gid']} 文本层Δ({raw[0]:+.4f},{raw[1]:+.4f})"
                  f" 精配Δ({dx:+.4f},{dy:+.4f})")
        if abs(dx) < MIN_DELTA and abs(dy) < MIN_DELTA:
            continue  # 本来就没漂
        p = m["patch"]
        v = p["value"]
        if p["prop"] == "endpoints_frac":
            p["value"] = [round(v[0] + dx, 4), round(v[1] + dy, 4),
                          round(v[2] + dx, 4), round(v[3] + dy, 4)]
        else:
            p["value"] = [round(v[0] + dx, 4), round(v[1] + dy, 4)]
        changed += 1
    return changed, report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--figures", required=True)
    ap.add_argument("--doc", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=5188)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    figures = Path(args.figures).expanduser().resolve()
    doc = json.loads(Path(args.doc).read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory(prefix="magplot_recover_") as td:
        scratch = Path(td)
        srv = start_server(figures, args.port, scratch)
        base = f"http://127.0.0.1:{args.port}"
        total = 0
        try:
            for panel in panels_of(doc):
                if not at_risk(panel):
                    continue
                changed, report = recover_panel(base, figures, panel,
                                                verbose=args.verbose)
                total += changed
                print(f"[{panel['fileId']}] 改写 {changed} 条")
                for line in report:
                    print("  -", line)
        finally:
            srv.terminate()
            try:
                srv.wait(timeout=10)
            except subprocess.TimeoutExpired:
                srv.kill()

    Path(args.out).write_text(json.dumps(doc, ensure_ascii=False),
                              encoding="utf-8")
    print(f"共改写 {total} 条，修正文档已另存：{args.out}")


if __name__ == "__main__":
    main()
