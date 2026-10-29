"""标注公式排版子进程（ADR 0152 PR 4）：matplotlib mathtext → 矢量路径。

**独立脚本**：在带 matplotlib 的解释器里直接跑（`python -B mathtext_child.py`），不 import tavotto——
内置 runtime 里没有 tavotto 包。stdin 一份 JSON 请求，stdout 一份 JSON 回答：

    请求  {"fonts": {"serif": [Regular, Italic, Bold], "sans-serif": [...], "monospace": [...]},
           "items": [{"src": "A e^{-E/RT}", "family": "serif"}, ...]}
    回答  {"matplotlib": "3.11.2",
           "results": [{"ok": true, "width": w, "ascent": a, "descent": d, "segments": [...]}
                       | {"ok": false, "error": "…"}]}

单位是 **em**（字号 1）、基线在 y = 0、**y 向上**；段与 `rendercore.ir` 同一套 op（M / L / C / Z），
mathtext 的二次贝塞尔在这里升成三次（IR 没有 Q）。调用方乘字号、翻 y。

字体：字母与数字用批准集合里的 Liberation（与标注正文同一张脸，`mathtext.fontset = custom`），
它画不了的符号（∑ ∫ 根号……）由 matplotlib 自带的 STIX 兜底（`mathtext.fallback`）。
"""

from __future__ import annotations

import json
import sys

FALLBACK = {"serif": "stix", "sans-serif": "stixsans", "monospace": "stix"}


def _setup(fonts: dict) -> dict:
    """注册批准字体，回 {族: 字体族名}。"""
    from matplotlib import font_manager

    names = {}
    for family, files in fonts.items():
        for path in files:
            font_manager.fontManager.addfont(path)
        names[family] = font_manager.FontProperties(fname=files[0]).get_name()
    return names


def _segments(path) -> list:
    """matplotlib Path → [(op, *坐标)]；CURVE3 升成三次。"""
    from matplotlib.path import Path as MPath

    out: list = []
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    for verts, code in path.iter_segments(simplify=False, curves=True):
        pts = [(float(verts[i]), float(verts[i + 1])) for i in range(0, len(verts), 2)]
        if code == MPath.MOVETO:
            cur = start = pts[0]
            out.append(["M", *cur])
        elif code == MPath.LINETO:
            cur = pts[0]
            out.append(["L", *cur])
        elif code == MPath.CURVE3:
            (qx, qy), (x, y) = pts
            c1 = (cur[0] + 2 / 3 * (qx - cur[0]), cur[1] + 2 / 3 * (qy - cur[1]))
            c2 = (x + 2 / 3 * (qx - x), y + 2 / 3 * (qy - y))
            out.append(["C", *c1, *c2, x, y])
            cur = (x, y)
        elif code == MPath.CURVE4:
            out.append(["C", *pts[0], *pts[1], *pts[2]])
            cur = pts[2]
        elif code == MPath.CLOSEPOLY:
            out.append(["Z"])
            cur = start
    return out


def typeset(src: str, family: str, names: dict) -> dict:
    import matplotlib as mpl
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath, TextToPath

    name = names.get(family) or names.get("serif")
    rc = {
        "mathtext.fontset": "custom",
        "mathtext.rm": name,
        "mathtext.it": f"{name}:italic",
        "mathtext.bf": f"{name}:bold",
        "mathtext.bfit": f"{name}:italic:bold",
        "mathtext.sf": names.get("sans-serif", name),
        "mathtext.tt": names.get("monospace", name),
        "mathtext.fallback": FALLBACK.get(family, "stix"),
    }
    text = f"${src}$"
    try:
        with mpl.rc_context({k: v for k, v in rc.items() if k in mpl.rcParams}):
            prop = FontProperties(family=name, size=1)
            w, h, d = TextToPath().get_text_width_height_descent(text, prop, ismath=True)
            tp = TextPath((0, 0), text, size=1, prop=prop)
            segs = _segments(tp)
    except Exception as exc:  # noqa: BLE001 —— mathtext 的语法错误是 ValueError，别的也一并回给调用方
        msg = str(exc).strip().splitlines()
        return {"ok": False, "error": (msg[-1] if msg else type(exc).__name__)[:300]}
    return {
        "ok": True,
        "width": float(w),
        "ascent": float(h - d),
        "descent": float(d),
        "segments": segs,
    }


def main() -> int:
    req = json.loads(sys.stdin.read())
    import matplotlib

    matplotlib.use("agg")
    names = _setup(req.get("fonts") or {})
    results = [
        typeset(str(it.get("src", "")), str(it.get("family", "serif")), names)
        for it in req["items"]
    ]
    json.dump({"matplotlib": matplotlib.__version__, "results": results}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
