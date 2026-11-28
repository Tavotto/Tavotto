"""生成 corpus.json（机理图评测题库；以 JSON 为准入库，本脚本是出处，改题改这里再重新生成）。

    python scripts/eval/mechanism/build_corpus.py          # 写 corpus.json
    python scripts/eval/mechanism/build_corpus.py --check  # 只核对 corpus.json 与本脚本一致（单测用）

每题：一块 150 × 100 mm 的画布（只有一个用户写的小标签，用来核「原有对象原样保留」）+ 一句中文指令 + 判据 + 一份标准答案
`reference`（一个 `add` 条目批，用 ADR 0220 的 frame / glyph + 免费的 shape / arrow / text）。全部合成，不含真实用户内容。
`expect: "pro_required"` 的对照题：没有高级版授权时，整批回结构化 `pro_required`、画布不动。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "corpus.json"

PAGE = {"w": 150, "h": 100}
USER_TAG = {
    "id": "t_user0001",
    "type": "text",
    "x": 2.0,
    "y": 1.0,
    "w": 10.0,
    "h": 4.0,
    "text": "Fig. 1",
}


def A(name: str, point: str, off=(0, 0)) -> dict:
    return {"anchor": {"item": {"name": name, "point": point}}, "offset_mm": list(off)}


def frame(title: str, size=(72, 50)) -> dict:
    return {"kind": "frame", "name": "frm", "title": title, "at_mm": [5, 18], "size_mm": list(size)}


def membrane(**params) -> dict:
    return {
        "kind": "glyph",
        "name": "mem",
        "glyph": "membrane",
        "frame": "frm",
        "block": "细胞膜",
        "at_mm": [12, 45],
        "identity": "质膜",
        "params": {"length_mm": 58, **params},
    }


def receptor(name: str, **params) -> dict:
    return {
        "kind": "glyph",
        "name": name,
        "glyph": "receptor_rtk",
        "frame": "frm",
        "block": "细胞膜",
        "embed": {"membrane": "mem"},
        "identity": "EGFR",
        "params": params,
    }


def text(name: str, s: str, at: dict, attach: str = "w") -> dict:
    return {
        "kind": "text",
        "name": name,
        "text": s,
        "at": at,
        "attach": attach,
        "frame": "frm",
        "sizePt": 7,
    }


def arrow(name: str, a: dict, b: dict, **kw) -> dict:
    return {"kind": "arrow", "name": name, "from": a, "to": b, "frame": "frm", **kw}


def dot(name: str, at: dict, d: float = 3.0) -> dict:
    return {
        "kind": "shape",
        "name": name,
        "shape": "ellipse",
        "at": at,
        "size_mm": [d, d],
        "attach": "center",
        "frame": "frm",
    }


def bind(n: str, r: str) -> list[dict]:
    """配体 + 指向受体胞外区的箭头 + 「EGF」文字。"""
    return [
        dot(f"lig{n}", A(r, "n", (0, -8)), 4.0),
        arrow(f"bind{n}", A(f"lig{n}", "s"), A(r, "n")),
        text(f"t_lig{n}", "EGF", A(f"lig{n}", "e", (1.5, 0))),
    ]


TASKS = [
    {
        "id": "single-receptor",
        "instruction": "在图旁边画一个受体插在细胞膜上，标出「受体」和「细胞膜」。",
        "reference": [
            frame("受体与细胞膜"),
            membrane(),
            receptor("r1", dimer=False),
            text("t_rec", "受体", A("r1", "ne", (1, 4))),
            text("t_mem", "细胞膜", A("mem", "sw", (0, 3)), "nw"),
        ],
        "predicates": [
            {"t": "counts", "frames": 1, "membranes": 1, "proteins": 1, "texts": 2},
            {"t": "proteins_in_membrane"},
            {"t": "no_overlap"},
            {"t": "inside_frame"},
            {"t": "required_texts", "contains": ["受体", "细胞膜"]},
            {"t": "labels_adjacent"},
            {"t": "originals_kept"},
        ],
    },
    {
        "id": "ligand-binding",
        "instruction": "画 EGF 配体结合到膜上受体的胞外区：一个受体、一个配体、一支箭头，标出配体。",
        "reference": [
            frame("配体结合"),
            membrane(length_mm=40),
            receptor("r1", dimer=False),
            *bind("1", "r1"),
        ],
        "predicates": [
            {"t": "counts", "frames": 1, "membranes": 1, "proteins": 1, "arrows": 1, "texts": 1},
            {"t": "proteins_in_membrane"},
            {"t": "no_overlap"},
            {"t": "inside_frame"},
            {"t": "required_texts", "contains": ["EGF"]},
            {"t": "labels_adjacent"},
            {"t": "arrows_reach"},
            {"t": "originals_kept"},
        ],
    },
    {
        "id": "three-step",
        "instruction": "画「配体结合 → 二聚化 → 胞内磷酸化」的最小机理图：细胞膜上两个受体，各结合一个 EGF，两个受体靠拢，胞内侧各标一个磷酸（P），再把这一步写成「胞内磷酸化」。",
        "reference": [
            frame("EGFR 信号"),
            membrane(),
            receptor("r1", dimer=False),
            receptor("r2", dimer=False),
            *bind("1", "r1"),
            *bind("2", "r2"),
            arrow("dimerize", A("r1", "ne", (0, 1)), A("r2", "nw", (0, 1))),
            text("t_dim", "二聚化", A("dimerize", "n", (0, -1)), "s"),
            text("t_p1", "P", A("r1", "s", (0, 1)), "n"),
            text("t_p2", "P", A("r2", "s", (0, 1)), "n"),
            text("t_phos", "胞内磷酸化", A("t_p1", "s", (0, 1)), "n"),
        ],
        "predicates": [
            {"t": "counts", "frames": 1, "membranes": 1, "proteins": 2, "arrows": 3, "texts": 6},
            {"t": "proteins_in_membrane"},
            {"t": "no_overlap"},
            {"t": "inside_frame"},
            {"t": "required_texts", "contains": ["EGF", "二聚", "磷酸", "P"]},
            {"t": "labels_adjacent"},
            {"t": "arrows_reach"},
            {"t": "originals_kept"},
        ],
    },
    {
        "id": "two-dimers",
        "instruction": "细胞膜上并排画两个已经二聚化的受体（二聚体），在膜下方标出「激酶域」。",
        "reference": [
            frame("受体二聚体"),
            membrane(),
            receptor("r1"),
            receptor("r2"),
            text("t_kd", "激酶域", A("r1", "s", (0, 1)), "n"),
        ],
        "predicates": [
            {"t": "counts", "frames": 1, "membranes": 1, "proteins": 2, "texts": 1},
            {"t": "proteins_in_membrane"},
            {"t": "no_overlap"},
            {"t": "inside_frame"},
            {"t": "required_texts", "contains": ["激酶域"]},
            {"t": "labels_adjacent"},
            {"t": "originals_kept"},
        ],
    },
    {
        "id": "curved-membrane",
        "instruction": "画一段向上弯的细胞膜（像细胞的一小段表面），上面插两个受体，标出「细胞膜」。",
        "reference": [
            frame("弯曲的膜"),
            membrane(curvature=0.2),
            receptor("r1", dimer=False),
            receptor("r2", dimer=False),
            text("t_mem", "细胞膜", A("mem", "sw", (0, 3)), "nw"),
        ],
        "predicates": [
            {"t": "counts", "frames": 1, "membranes": 1, "proteins": 2, "texts": 1},
            {"t": "proteins_in_membrane"},
            {"t": "no_overlap"},
            {"t": "inside_frame"},
            {"t": "required_texts", "contains": ["细胞膜"]},
            {"t": "labels_adjacent"},
            {"t": "originals_kept"},
        ],
    },
    {
        "id": "free-tier-control",
        "instruction": "（对照，没有高级版授权）画一个受体插在细胞膜上。",
        "expect": "pro_required",
        "reference": [frame("受体与细胞膜"), membrane(), receptor("r1", dimer=False)],
        "predicates": [{"t": "originals_kept"}],
    },
]


def build() -> dict:
    return {
        "version": 1,
        "canvas": {"id": "c_main0001", "name": "图版 1", "page": PAGE, "objects": [USER_TAG]},
        "tasks": TASKS,
    }


def main() -> int:
    text_ = json.dumps(build(), ensure_ascii=False, indent=1) + "\n"
    if "--check" in sys.argv:
        return 0 if OUT.read_text(encoding="utf-8") == text_ else 1
    OUT.write_text(text_, encoding="utf-8")
    print(f"写出 {OUT}（{len(TASKS)} 题）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
