#!/usr/bin/env python3
"""重出 `tests/golden/agent_object_vectors.json`（ADR 0208 §五）：Agent 新增对象的校验结果与导出载荷。

两侧怎么用：
- pytest（`tests/test_agent_objects.py`）：每条用例重新校验、比错误码；通过的那些转成载荷交给
  `rendercore.plan.compile_page` 真编译一遍（独立的消费者：画不出来就不算「合格」）。
- vitest（`web/src/lib/agentObjects.golden.test.ts`）：通过的那些先过前端 `isKnownObject`，再交给前端
  **真实的** `toExportObjects`，结果必须与这里存的 `export`（后端 `agentobjects.to_export_objects`）逐字段相同。

生成的 id 是随机的，写进文件前换成稳定的占位（`<前缀>_0000000N`），只为让向量可读、可比。

用法：`python scripts/gen_agent_object_vectors.py --write`，改了任一侧都要重跑并人工读 diff。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tavotto.engine import agentobjects  # noqa: E402

OUT = ROOT / "tests" / "golden" / "agent_object_vectors.json"


def _force_utf8() -> None:
    """把自己的 stdout/stderr 钉成 UTF-8（输出是中文；Windows 上被捕获时会退回系统区域编码）。
    同 `scripts/gen_preflight_vectors.py` 的 `_force_utf8()`。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

BOX = {"x": 10.0, "y": 12.0, "w": 30.0, "h": 8.0}
SQUARE = [{"closed": True, "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}, {"p": [0, 1]}]}]
CURVE = [
    {
        "closed": False,
        "nodes": [
            {"p": [0, 1], "out": [0.3, -0.2]},
            {"p": [1, 0], "in": [0.7, 1.2], "smooth": True},
        ],
    }
]
LINEAR = {
    "kind": "linear",
    "stops": [{"offset": 0, "color": "#ffffff"}, {"offset": 1, "color": "#0072B2", "opacity": 0.5}],
    "from": [0, 0.5],
    "to": [1, 0.5],
}


def text(i="t1", **kw):
    return {
        "id": i,
        "type": "text",
        **BOX,
        "text": "Receptor",
        "sizePt": 8,
        "bold": False,
        "color": "#000000",
        "align": "left",
        **kw,
    }


def arrow(i="a1", **kw):
    return {
        "id": i,
        "type": "arrow",
        **BOX,
        "start": {"rx": 0, "ry": 0.5},
        "end": {"rx": 1, "ry": 0.5},
        "strokePt": 1,
        "color": "#000000",
        "head": "end",
        **kw,
    }


def shape(i="s1", **kw):
    return {
        "id": i,
        "type": "shape",
        **BOX,
        "shape": "rect",
        "strokePt": 1,
        "color": "#333333",
        "fill": "#E69F00",
        **kw,
    }


def path(i="p1", **kw):
    return {
        "id": i,
        "type": "path",
        **BOX,
        "subpaths": SQUARE,
        "strokePt": 0.5,
        "color": "#333333",
        "fill": "#56B4E9",
        **kw,
    }


def cases() -> list[dict]:
    ok = [
        ("ok-minimal-each-type", [text(), arrow(), shape(), path()]),
        (
            "ok-text-all-optional",
            [
                text(
                    fontFamily="sans-serif",
                    interpretation="scientific",
                    textFormat="latex",
                    italic=True,
                    underline=True,
                    lineHeight=1.2,
                    padding=1.5,
                    bg="#FFFFFF",
                    borderColor="#000000",
                    name="标签 EGFR",
                    rotationDeg=15,
                )
            ],
        ),
        ("ok-text-border-default-width", [text(borderColor="#000000")]),
        (
            "ok-arrow-all-optional",
            [
                arrow(
                    headStart="dot",
                    headEnd="stealth",
                    headSizeStart="small",
                    headSizeEnd="large",
                    dash="dashed",
                    strokeOpacity=0.5,
                    lineCap="butt",
                    lineJoin="miter",
                    miterLimit=4,
                    dashPattern=[2, 1],
                )
            ],
        ),
        (
            "ok-arrow-defaults-not-sent",
            [
                arrow(
                    headSizeEnd="medium",
                    dash="solid",
                    lineCap="round",
                    lineJoin="round",
                    miterLimit=10,
                    strokeOpacity=1,
                )
            ],
        ),
        (
            "ok-shape-variants",
            [
                shape("s1", shape="polygon"),
                shape("s2", shape="star", sides=7, starRatio=0.5),
                shape("s3", shape="star"),
                shape(
                    "s4", shape="line", start={"rx": 0, "ry": 0}, end={"rx": 1, "ry": 1}, fill=None
                ),
                shape("s5", shape="ellipse", stroke=False, fillOpacity=0.4, fillGradient=LINEAR),
                shape("s6", cornerRadius=2, strokeAlign="outside", strokeGradient=LINEAR),
                shape("s7", shape="brace", stroke=False, fill=None, strokeAlign="outside"),
            ],
        ),
        (
            "ok-path-curve-and-heads",
            [
                path(
                    subpaths=CURVE,
                    fill=None,
                    headEnd="triangle",
                    headStart="none",
                    fillRule="evenodd",
                    strokeAlign="inside",
                    lineJoin="miter",
                )
            ],
        ),
        (
            "ok-path-no-stroke-gradient",
            [path(stroke=False, fillGradient=LINEAR, strokeGradient=LINEAR)],
        ),
        # 求解器会产出零宽 / 零高的对象（竖直箭头、水平连线、括号的横线）：要能编译、与前端同一份载荷
        (
            "ok-zero-width-and-zero-height",
            [
                arrow(w=0.0, h=10.0, start={"rx": 0.5, "ry": 0}, end={"rx": 0.5, "ry": 1}),
                path(
                    "p2",
                    h=0.0,
                    fill=None,
                    subpaths=[{"closed": False, "nodes": [{"p": [0, 0.5]}, {"p": [1, 0.5]}]}],
                    headEnd="triangle",
                ),
            ],
        ),
        (
            "ok-groups-nested-and-shared",
            [
                shape("s1", groupPath=["机理图", "细胞"], name="细胞膜"),
                path("p1", groupPath=["机理图", "细胞"], name="受体"),
                arrow("a1", groupPath=["机理图"], name="激活箭头"),
                text("t1", groupPath=["标签"], name="受体 EGFR"),
            ],
        ),
    ]
    bad = [
        ("bad-not-a-list", {"type": "text"}),
        ("bad-unknown-type", [{"id": "x", "type": "panel", **BOX}]),
        ("bad-missing-field", [{k: v for k, v in text().items() if k != "sizePt"}]),
        ("bad-unknown-field", [text(locked=True)]),
        ("bad-withheld-field", [text(sourceFont={"family": "Arial"})]),
        ("bad-reserved-field", [arrow(anchor={"to": "o_x"})]),
        (
            "bad-out-of-range",
            [text(sizePt=1), arrow(strokePt=25), shape(sides=40, shape="polygon")],
        ),
        ("bad-not-finite", [{**text(), "x": float("inf")}]),
        ("bad-color", [text(color="red"), shape(fill="#12345")]),
        ("bad-enum", [arrow(head="start"), shape(shape="hexagon")]),
        ("bad-bool-as-number", [text(bold=1)]),
        ("bad-duplicate-id", [text("same"), arrow("same")]),
        (
            "bad-group-path",
            [
                shape(groupPath=[]),
                arrow(groupPath=["a", "b", "c", "d", "e"]),
                path(groupPath=["a", "a"]),
            ],
        ),
        (
            "bad-subpaths",
            [
                path(subpaths=[]),
                path("p2", subpaths=[{"closed": True, "nodes": [{"p": [0]}]}]),
                path("p3", subpaths=[{"closed": True, "nodes": [{"p": [0, 0], "smooth": False}]}]),
            ],
        ),
        (
            "bad-ratio-too-far",
            [path(subpaths=[{"closed": False, "nodes": [{"p": [0, 0]}, {"p": [50, 0]}]}])],
        ),
        (
            "bad-dash-pattern",
            [arrow(dashPattern=[0, 0]), arrow("a2", dashPattern=list(range(1, 14)))],
        ),
        (
            "bad-gradient",
            [
                shape(fillGradient={"kind": "conic", "stops": []}),
                shape("s2", fillGradient={**LINEAR, "stops": [{"offset": 2, "color": "#fff"}]}),
            ],
        ),
        ("bad-name", [text(name=""), arrow(name="x\x01y")]),
    ]
    out = []
    for name, objs in ok:
        out.append({"name": name, "input": objs})
    for name, objs in bad:
        out.append({"name": name, "input": objs})
    return out


def _stable(objs: list[dict]) -> list[dict]:
    """把随机 id 换成稳定占位，组 id 与对象 id 一起换，保持引用关系。"""
    mapping: dict[str, str] = {}

    def stable(real: str) -> str:
        if real not in mapping:
            prefix = real.split("_", 1)[0]
            mapping[real] = f"{prefix}_{len(mapping) + 1:08d}"
        return mapping[real]

    out = []
    for o in objs:
        n = dict(o)
        n["id"] = stable(o["id"])
        if "groupId" in n:
            n["groupId"] = stable(n["groupId"])
        if "groupPath" in n:
            n["groupPath"] = [stable(g) for g in n["groupPath"]]
        out.append(n)
    return out


def build() -> dict:
    vectors = []
    for c in cases():
        r = agentobjects.validate_new_objects(c["input"])
        v = {"name": c["name"], "input": c["input"], "ok": r["ok"]}
        if r["ok"]:
            objs = _stable(r["objects"])
            v["objects"] = objs
            v["export"] = agentobjects.to_export_objects(objs)
        else:
            v["errors"] = r["errors"]
        vectors.append(v)
    return {
        "_doc": "由 scripts/gen_agent_object_vectors.py 生成，勿手改（ADR 0208 §五）",
        "error_codes": list(agentobjects.ERROR_CODES),
        "cases": vectors,
    }


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    # 「不是有限数」那条用例：JSON 没有 Infinity 字面量，写成 1e999（两侧解析都得到无穷大）
    text_out = (
        json.dumps(build(), ensure_ascii=False, indent=1, allow_nan=True).replace(
            "Infinity", "1e999"
        )
        + "\n"
    )
    if args.write:
        OUT.write_text(text_out, encoding="utf-8")
        print(f"已写入 {OUT}（{len(build()['cases'])} 条）")
        return 0
    current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    if current != text_out:
        print("agent_object_vectors.json 过期：跑 --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
