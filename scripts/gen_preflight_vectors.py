#!/usr/bin/env python3
"""生成 / 校对 `tests/golden/preflight_vectors.json`。

预检有**两个求值器**（Python 的 `engine/preflight.py` 给 MCP server，
TypeScript 的 `web/src/lib/preflight.ts` 给画布与导出对话框）——浏览器里跑不了
Python，所以第二份是必需的，不是重复。让它们不分叉的办法与 patchspec ↔ Rust
supervisor 完全一样：**同一份向量，两边各跑一遍**。

    python scripts/gen_preflight_vectors.py            # 校对（有分歧就非零退出）
    python scripts/gen_preflight_vectors.py --write    # 按 Python 侧重新生成

Python 是参考实现：`--write` 之后必须人工读一遍 diff，再让 vitest 也绿。

纯标准库。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from magplot.engine import preflight, profiles  # noqa: E402

OUT = ROOT / "tests" / "golden" / "preflight_vectors.json"


def _force_utf8() -> None:
    """把自己的 stdout/stderr 钉成 UTF-8。

    输出里全是中文，而被 subprocess 捕获（pytest 就是这么调的）或重定向时，
    Windows 上 stdout 会退回系统区域编码 cp1252/cp936——第一次 print 就
    UnicodeEncodeError 打死进程，调用方看到的是「脚本挂了」而不是那行结论。
    同 `codex-plugin/skills/magplot-figure/scripts/handoff.py` 的 `_force_utf8()`。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _el(gid: str, role: str, **props) -> dict:
    return {"gid": gid, "role": role, "label": gid, "draggable": False,
            "bbox": [0.1, 0.1, 0.2, 0.2],
            "editable": [{"prop": k, "type": "number", "value": v}
                         for k, v in props.items()]}


def _clean_manifest(stem: str = "Fig1") -> dict:
    """一张完全合规的图：封闭轴、刻度朝内、无框图例、9pt、Times、0.75/1.0pt。"""
    return {
        "stem": stem,
        "size_mm": [80.0, 60.0],
        "elements": [
            _el("axes_0", "axes", spine_top=True, spine_right=True,
                spine_bottom=True, spine_left=True, spine_linewidth=0.75),
            _el("axes_0.xticks", "ticks", direction="in", fontsize=9.0),
            _el("axes_0.yticks", "ticks", direction="in", fontsize=9.0),
            _el("axes_0.xlabel", "axis_label", text="Temperature (K)",
                fontsize=9.0, fontfamily="Times New Roman"),
            _el("axes_0.ylabel", "axis_label", text="Removal rate (mg/h)",
                fontsize=9.0, fontfamily="Times New Roman"),
            _el("axes_0.legend", "legend", frameon=False, fontsize=9.0),
            _el("axes_0.lines_0", "line", linewidth=1.0, marker="o", label="Sample A"),
            _el("axes_0.lines_1", "line", linewidth=1.0, marker="s", label="Sample B"),
        ],
    }


def _panel(pid: str, **kw) -> dict:
    base = {"id": pid, "name": pid, "kind": "pdf", "rect_mm": [0, 0, 80.0, 60.0],
            "scale": 1.0, "manifest": None, "px_w": None, "missing": False,
            "stale": False, "render_error": None, "unapplied_overrides": 0,
            "bitmap_embed": False, "hidden": False}
    base.update(kw)
    return base


def _spec(panels: list[dict], *, page=(80.0, 60.0), margin=0.0,
          texts=None, extra_objects=None) -> dict:
    objects = [{"id": p["id"], "type": "panel", "rect_mm": p["rect_mm"],
                "hidden": p["hidden"]} for p in panels]
    for t in (texts or []):
        objects.append({"id": t["id"], "type": "text", "rect_mm": t["rect_mm"],
                        "hidden": t["hidden"]})
    objects += list(extra_objects or [])
    return {"page": {"w_mm": page[0], "h_mm": page[1], "margin_mm": margin},
            "panels": panels, "texts": texts or [], "objects": objects}


def _text(tid: str, size_pt: float, text: str = "(a)", **kw) -> dict:
    base = {"id": tid, "text": text, "size_pt": size_pt, "bold": False,
            "rect_mm": [1.0, 1.0, 10.0, 5.0], "hidden": False}
    base.update(kw)
    return base


def cases() -> list[dict]:
    """向量清单。每条都盯着一个**具体会出错的地方**，不写凑数的用例。"""
    out: list[dict] = []

    # 1. 完全合规：一条问题都不该报（4:3 的 80×60）
    out.append({"name": "clean-single-column", "profile_id": "lab-publication-v1",
                "spec": _spec([_panel("p1", manifest=_clean_manifest())])})

    # 2. 字号：8.2pt 低于 8.5 下限；8.0pt 撞绝对下限（两条等级不同）
    m = _clean_manifest()
    m["elements"][1]["editable"][1]["value"] = 8.2      # xticks fontsize
    m["elements"][2]["editable"][1]["value"] = 8.0      # yticks fontsize
    out.append({"name": "font-below-thresholds", "profile_id": "lab-publication-v1",
                "spec": _spec([_panel("p1", manifest=m)])})

    # 3. 缩放：原生 9pt 的图缩到 80%，读者量到的是 7.2pt。
    #    **只看原始 fontsize 会全部放行**——这条就是为它存在的。
    out.append({"name": "scaled-panel-shrinks-fonts", "profile_id": "lab-publication-v1",
                "spec": _spec([_panel("p1", manifest=_clean_manifest(), scale=0.8,
                                      rect_mm=[0, 0, 64.0, 48.0])],
                              page=(80.0, 60.0))})

    # 4. 位图面板：低 DPI + 内部文字无法核验
    out.append({"name": "raster-panel", "profile_id": "lab-publication-v1",
                "spec": _spec([_panel("p1", kind="raster", px_w=600,
                                      manifest=None)])})

    # 5. 矢量面板没有 manifest：如实报 not_verifiable，不假装通过
    out.append({"name": "vector-without-manifest", "profile_id": "lab-publication-v1",
                "spec": _spec([_panel("p1")])})

    # 6. 版式违规大合集：图例带框、刻度朝外、轴不封闭、线宽不在档位
    m = _clean_manifest()
    m["elements"][0]["editable"][1]["value"] = False    # spine_right
    m["elements"][1]["editable"][0]["value"] = "out"    # xticks direction
    m["elements"][5]["editable"][0]["value"] = True     # legend frameon
    m["elements"][5]["editable"][1]["value"] = 8.0      # legend fontsize
    m["elements"][6]["editable"][0]["value"] = 1.2      # line linewidth
    out.append({"name": "layout-policy-violations", "profile_id": "lab-publication-v1",
                "spec": _spec([_panel("p1", manifest=m)])})

    # 7. 画布层：小字号标注 + 越界 + 重叠 + 隐藏 + 越过安全区
    texts = [_text("t-small", 7.0), _text("t-floor", 8.0, rect_mm=[2, 2, 10, 5])]
    panels = [
        _panel("p1", manifest=_clean_manifest(), rect_mm=[0, 0, 60.0, 40.0],
               scale=0.75),
        _panel("p2", manifest=_clean_manifest(), rect_mm=[50.0, 30.0, 60.0, 40.0],
               scale=0.75),
        _panel("p3", manifest=_clean_manifest(), rect_mm=[5.0, 5.0, 20.0, 15.0],
               hidden=True),
    ]
    out.append({"name": "canvas-geometry", "profile_id": "lab-publication-v1",
                "spec": _spec(panels, page=(80.0, 60.0), margin=3.0, texts=texts)})

    # 8. 页宽/比例：150 是双栏（宽度过），但 150×60 的比例哪个都不是
    out.append({"name": "double-column-bad-aspect", "profile_id": "lab-publication-v1",
                "spec": _spec([_panel("p1", manifest=_clean_manifest(),
                                      rect_mm=[0, 0, 150.0, 60.0], scale=1.0)],
                              page=(150.0, 60.0))})

    # 9. journal 覆盖：178mm 双栏（Nature 式）——覆盖后 178 应当放行
    out.append({"name": "journal-width-override", "profile_id": "lab-publication-v1",
                "journal": {"widths_mm": {"double": 178.0}},
                "spec": _spec([_panel("p1", manifest=_clean_manifest(),
                                      rect_mm=[0, 0, 178.0, 100.125], scale=1.0)],
                              page=(178.0, 100.125))})

    # 9b. 同一份 178mm 的输入**不带覆盖**：必须报页宽不符（证明覆盖真的起了作用，
    #     而不是这条检查本来就不会响）
    out.append({"name": "journal-width-without-override", "profile_id": "lab-publication-v1",
                "spec": _spec([_panel("p1", manifest=_clean_manifest(),
                                      rect_mm=[0, 0, 178.0, 100.125], scale=1.0)],
                              page=(178.0, 100.125))})

    # 9c. journal 把容差覆盖成 **0**（「页宽必须精确等于规范值」）：80.3mm 必须报。
    #     这一对用例守的是「显式 0 ≠ 没表态」——Python 侧一度写成
    #     `_num(...) or 0.5`，把 0 当成缺省又换回 0.5mm，于是偷偷放行了
    #     不合规的页宽，而 TS 侧用 `??`、忠实执行 0，两条链路结论相反。
    out.append({"name": "journal-zero-width-tolerance", "profile_id": "lab-publication-v1",
                "journal": {"widths_mm": {"tolerance_mm": 0.0}},
                "spec": _spec([_panel("p1", manifest=_clean_manifest(),
                                      rect_mm=[0, 0, 80.3, 60.0])],
                              page=(80.3, 60.0))})

    # 9d. 同一份 80.3mm **不带覆盖**：默认 0.5mm 容差盖得住，不该报页宽
    #     （证明上一条的 error 真的来自那个 0，而不是这张图本来就不合规）
    out.append({"name": "journal-zero-width-tolerance-baseline",
                "profile_id": "lab-publication-v1",
                "spec": _spec([_panel("p1", manifest=_clean_manifest(),
                                      rect_mm=[0, 0, 80.3, 60.0])],
                              page=(80.3, 60.0))})

    # 9e. journal 把绝对字号下限覆盖成 **0**（「不设下限」）：4pt 不该再触发
    #     font-below-absolute-floor（font-too-small 仍会响，那是另一条规则）。
    m0 = _clean_manifest()
    m0["elements"][1]["editable"][1]["value"] = 4.0        # xticks fontsize
    out.append({"name": "journal-zero-font-floor", "profile_id": "lab-publication-v1",
                "journal": {"absolute_min_font_size_pt": 0.0},
                "spec": _spec([_panel("p1", manifest=m0)])})

    # 10. free-form profile：同一份输入，等级整体降一档（severity 表说了算）。
    #     4pt 才低于它的 6pt 下限——同样一张图在严格 profile 下是 error。
    m = _clean_manifest()
    m["elements"][1]["editable"][1]["value"] = 4.0
    m["elements"][5]["editable"][0]["value"] = True     # legend frameon（free-form 不管）
    m["elements"][6]["editable"][0]["value"] = 1.2      # 线宽（free-form 没有档位）
    out.append({"name": "free-form-downgrades", "profile_id": "free-form-v1",
                "spec": _spec([_panel("p1", manifest=m)], page=(123.0, 60.0))})

    # 11. 中文 fallback：中文轴标签配的是 DejaVu，导出必然是方框
    m = _clean_manifest()
    m["elements"][3]["editable"] = [
        {"prop": "text", "type": "text", "value": "温度 (K)"},
        {"prop": "fontsize", "type": "number", "value": 9.0},
        {"prop": "fontfamily", "type": "enum", "value": "DejaVu Sans"},
    ]
    out.append({"name": "cjk-without-fallback", "profile_id": "lab-publication-v1",
                "spec": _spec([_panel("p1", manifest=m)])})

    # 12. 数据语义建议：柱状图无误差棒 + 拟合无置信带 + jet 色谱
    m = _clean_manifest()
    m["elements"] = [
        m["elements"][0], m["elements"][1], m["elements"][2],
        _el("axes_0.barseries_0", "bar_series", linewidth=0.75, label="Yield"),
        _el("axes_0.lines_0", "line", linewidth=1.0, marker="None", label="linear fit"),
        _el("axes_0.lines_1", "line", linewidth=1.0, marker="None", label="raw"),
        _el("axes_0.images_0", "image", cmap="jet"),
    ]
    out.append({"name": "data-semantics-suggestions", "profile_id": "lab-publication-v1",
                "spec": _spec([_panel("p1", manifest=m)])})

    # 13. 面板状态：缺素材 / 渲染失败 / 过期 / override 没画上 / 位图嵌入
    out.append({"name": "panel-states", "profile_id": "lab-publication-v1",
                "spec": _spec([_panel("p1", manifest=_clean_manifest(), missing=True,
                                      stale=True, render_error="boom",
                                      unapplied_overrides=3, bitmap_embed=True)])})

    # 13b. 字重策略与轴标题格式：ticklabel 该加粗、轴标题该是「Title (unit)」。
    #      两条都只是建议——但**必须真的会响**：manifest 里的角色名是 ticklabel
    #      而不是 tick_label，键写错的话它永远沉默，看起来像一直通过。
    m = _clean_manifest()
    m["elements"][3]["editable"][0]["value"] = "Temperature"      # 缺单位括号
    m["elements"].append({"gid": "axes_0.xticklabels_0", "role": "ticklabel",
                          "label": "刻度", "draggable": True,
                          "bbox": [0.1, 0.9, 0.05, 0.03],
                          "editable": [
                              {"prop": "text", "type": "text", "value": "100"},
                              {"prop": "fontsize", "type": "number", "value": 9.0},
                              {"prop": "weight", "type": "enum", "value": "normal"},
                          ]})
    out.append({"name": "weight-and-label-format", "profile_id": "lab-publication-v1",
                "spec": _spec([_panel("p1", manifest=m)])})

    # 13c. 中文 fallback 被期刊覆盖清空：画布中文标注也要报（这条检查唯一的触发路径）
    out.append({"name": "cjk-fallback-cleared-by-journal",
                "profile_id": "lab-publication-v1",
                "journal": {"cjk_fallback": {"required": True, "accepted": []}},
                "spec": _spec([_panel("p1", manifest=_clean_manifest())],
                              texts=[_text("t-cn", 9.0, "图 1 反应动力学")])})

    # 13d. 字号上限：标题 24pt 远大于正文字号
    m = _clean_manifest()
    m["elements"].append(_el("axes_0.title", "title", fontsize=24.0, text="Kinetics"))
    out.append({"name": "font-too-large", "profile_id": "lab-publication-v1",
                "spec": _spec([_panel("p1", manifest=m)])})

    # 14. 刻度标签超过 10 个（规范建议控制在 10 以内）
    m = _clean_manifest()
    m["elements"] += [_el(f"axes_0.xticklabels_{i}", "ticklabel", fontsize=9.0)
                      for i in range(12)]
    out.append({"name": "too-many-tick-labels", "profile_id": "lab-publication-v1",
                "spec": _spec([_panel("p1", manifest=m)])})

    return out


def evaluate(case: dict) -> list[dict]:
    profile = profiles.load(case.get("profile_id"), case.get("journal"))
    issues = preflight.run(case["spec"], profile)
    # 只比**判据**，不比中文措辞：措辞是界面的事，两侧的数字格式化不必逐字相同
    return [{"id": i["id"], "severity": i["severity"],
             "object_ids": i["object_ids"], "gids": i["gids"],
             "detail": i["detail"]} for i in issues]


def build() -> dict:
    return {
        "comment": "预检向量：pytest（engine/preflight.py）与 vitest"
                   "（web/src/lib/preflight.ts）各跑一遍同一份输入。"
                   "只比 id/severity/object_ids/gids/detail —— 中文措辞归界面。"
                   "重新生成：python scripts/gen_preflight_vectors.py --write",
        "cases": [{**c, "expected": evaluate(c)} for c in cases()],
    }


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="按 Python 侧重新生成")
    args = ap.parse_args(argv)

    fresh = build()
    text = json.dumps(fresh, ensure_ascii=False, indent=1, sort_keys=False) + "\n"
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"已写入 {OUT}（{len(fresh['cases'])} 条）")
        return 0
    if not OUT.is_file():
        print(f"缺少 {OUT}，先跑一次 --write", file=sys.stderr)
        return 1
    if OUT.read_text(encoding="utf-8") != text:
        print(f"{OUT} 与当前 Python 实现不一致（--write 重新生成后人工过一遍 diff）",
              file=sys.stderr)
        return 1
    print(f"{OUT} 与 Python 实现一致（{len(fresh['cases'])} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
