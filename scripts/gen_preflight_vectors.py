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

from tavotto.engine import preflight, profiles  # noqa: E402

OUT = ROOT / "tests" / "golden" / "preflight_vectors.json"


def _force_utf8() -> None:
    """把自己的 stdout/stderr 钉成 UTF-8。

    输出里全是中文，而被 subprocess 捕获（pytest 就是这么调的）或重定向时，
    Windows 上 stdout 会退回系统区域编码 cp1252/cp936——第一次 print 就
    UnicodeEncodeError 打死进程，调用方看到的是「脚本挂了」而不是那行结论。
    同 `codex-plugin/skills/tavotto-figure/scripts/handoff.py` 的 `_force_utf8()`。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _el(gid: str, role: str, **props) -> dict:
    # 占位几何是**零面积**的点：这些向量量的是规范（字号 / 线宽 / 页面…），不是版面。
    # 预检接进干涉检测（V-011）之后，旧的同一个 0.2×0.2 占位框会让每两段文字都「重叠」；
    # 要量干涉的向量（`interference-*`）显式给框。
    return {
        "gid": gid,
        "role": role,
        "label": gid,
        "draggable": False,
        "bbox": [0.1, 0.1, 0.0, 0.0],
        "editable": [{"prop": k, "type": "number", "value": v} for k, v in props.items()],
    }


def _clean_manifest(stem: str = "Fig1") -> dict:
    """一张完全合规的图：封闭轴、刻度朝内、无框图例、9pt、Times、0.75/1.0pt。"""
    return {
        "stem": stem,
        "size_mm": [80.0, 60.0],
        "elements": [
            _el(
                "axes_0",
                "axes",
                spine_top=True,
                spine_right=True,
                spine_bottom=True,
                spine_left=True,
                spine_linewidth=0.75,
            ),
            _el("axes_0.xticks", "ticks", direction="in", fontsize=9.0),
            _el("axes_0.yticks", "ticks", direction="in", fontsize=9.0),
            _el(
                "axes_0.xlabel",
                "axis_label",
                text="Temperature (K)",
                fontsize=9.0,
                fontfamily="Times New Roman",
            ),
            _el(
                "axes_0.ylabel",
                "axis_label",
                text="Removal rate (mg/h)",
                fontsize=9.0,
                fontfamily="Times New Roman",
            ),
            _el("axes_0.legend", "legend", frameon=False, fontsize=9.0),
            _el("axes_0.lines_0", "line", linewidth=1.0, marker="o", label="Sample A"),
            _el("axes_0.lines_1", "line", linewidth=1.0, marker="s", label="Sample B"),
            # 图例里画出来的条目文字：`legend-font-size` 量的是它，不是图例的设置器。
            # 放在最后，别的向量按下标改前面那些元素不受影响
            _el("axes_0.legend.texts_0", "legend_text", fontsize=9.0),
        ],
    }


def _panel(pid: str, **kw) -> dict:
    base = {
        "id": pid,
        "name": pid,
        "kind": "pdf",
        "rect_mm": [0, 0, 80.0, 60.0],
        "scale": 1.0,
        "manifest": None,
        "px_w": None,
        "missing": False,
        "stale": False,
        "render_error": None,
        "unapplied_overrides": 0,
        "hidden": False,
    }
    base.update(kw)
    return base


def _spec(
    panels: list[dict], *, page=(80.0, 60.0), margin=0.0, texts=None, extra_objects=None
) -> dict:
    objects = [
        {"id": p["id"], "type": "panel", "rect_mm": p["rect_mm"], "hidden": p["hidden"]}
        for p in panels
    ]
    for t in texts or []:
        objects.append(
            {"id": t["id"], "type": "text", "rect_mm": t["rect_mm"], "hidden": t["hidden"]}
        )
    objects += list(extra_objects or [])
    return {
        "page": {"w_mm": page[0], "h_mm": page[1], "margin_mm": margin},
        "panels": panels,
        "texts": texts or [],
        "objects": objects,
    }


def _text(tid: str, size_pt: float, text: str = "(a)", **kw) -> dict:
    # `font_family` 是**生效**的族（前端由 `effectiveCanvasFamily()` 算出来），
    # 缺省就是衬线——所以老向量里那些没写这一维的文字与这里的缺省一致，
    # 加这一维不改任何一条既有向量的结论。
    base = {
        "id": tid,
        "text": text,
        "size_pt": size_pt,
        "bold": False,
        "font_family": "serif",
        "rect_mm": [1.0, 1.0, 10.0, 5.0],
        "hidden": False,
    }
    base.update(kw)
    return base


def cases() -> list[dict]:
    """向量清单。每条都盯着一个**具体会出错的地方**，不写凑数的用例。"""
    out: list[dict] = []

    # 1. 完全合规：一条问题都不该报（4:3 的 80×60）
    out.append(
        {
            "name": "clean-single-column",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=_clean_manifest())]),
        }
    )

    # 2. 字号：7.9pt 低于绝对下限；正好 8.0pt 通过（下限含等号，ADR 0029 修订 2026-10-26）
    m = _clean_manifest()
    m["elements"][1]["editable"][1]["value"] = 8.0  # xticks fontsize
    m["elements"][2]["editable"][1]["value"] = 7.9  # yticks fontsize
    out.append(
        {
            "name": "font-below-thresholds",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 3. 缩放：原生 9pt 的图缩到 80%，读者量到的是 7.2pt。
    #    **只看原始 fontsize 会全部放行**——这条就是为它存在的。
    out.append(
        {
            "name": "scaled-panel-shrinks-fonts",
            "profile_id": "lab-publication-v1",
            "spec": _spec(
                [_panel("p1", manifest=_clean_manifest(), scale=0.8, rect_mm=[0, 0, 64.0, 48.0])],
                page=(80.0, 60.0),
            ),
        }
    )

    # 4. 位图面板：低 DPI + 内部文字无法核验
    out.append(
        {
            "name": "raster-panel",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", kind="raster", px_w=600, manifest=None)]),
        }
    )

    # 5. 矢量面板没有 manifest：如实报 not_verifiable，不假装通过
    out.append(
        {
            "name": "vector-without-manifest",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1")]),
        }
    )

    # 6. 版式违规大合集：图例带框、刻度朝外、轴不封闭、线宽不在档位
    m = _clean_manifest()
    m["elements"][0]["editable"][1]["value"] = False  # spine_right
    m["elements"][1]["editable"][0]["value"] = "out"  # xticks direction
    m["elements"][5]["editable"][0]["value"] = True  # legend frameon
    m["elements"][-1]["editable"][0]["value"] = 8.0  # legend 条目 fontsize（图例字号量的是它）
    m["elements"][6]["editable"][0]["value"] = 1.2  # line linewidth
    out.append(
        {
            "name": "layout-policy-violations",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 7. 画布层：小字号标注 + 越界 + 重叠 + 隐藏 + 越过安全区
    texts = [
        _text("t-small", 7.0),
        _text("t-floor", 8.0, rect_mm=[2, 2, 10, 5]),
        _text("t-noise", 7.9996, rect_mm=[2, 12, 10, 5]),  # 容差内：通过
        _text("t-short", 7.98, rect_mm=[2, 22, 10, 5]),  # 超出容差：违规
    ]  # 正好 8.0 的通过
    panels = [
        _panel("p1", manifest=_clean_manifest(), rect_mm=[0, 0, 60.0, 40.0], scale=0.75),
        _panel("p2", manifest=_clean_manifest(), rect_mm=[50.0, 30.0, 60.0, 40.0], scale=0.75),
        _panel("p3", manifest=_clean_manifest(), rect_mm=[5.0, 5.0, 20.0, 15.0], hidden=True),
    ]
    out.append(
        {
            "name": "canvas-geometry",
            "profile_id": "lab-publication-v1",
            "spec": _spec(panels, page=(80.0, 60.0), margin=3.0, texts=texts),
        }
    )

    # 8. 页宽/比例：150 是双栏（宽度过），但 150×60 的比例哪个都不是
    out.append(
        {
            "name": "double-column-bad-aspect",
            "profile_id": "lab-publication-v1",
            "spec": _spec(
                [_panel("p1", manifest=_clean_manifest(), rect_mm=[0, 0, 150.0, 60.0], scale=1.0)],
                page=(150.0, 60.0),
            ),
        }
    )

    # 9. journal 覆盖：178mm 双栏（Nature 式）——覆盖后 178 应当放行
    out.append(
        {
            "name": "journal-width-override",
            "profile_id": "lab-publication-v1",
            "journal": {"widths_mm": {"double": 178.0}},
            "spec": _spec(
                [
                    _panel(
                        "p1", manifest=_clean_manifest(), rect_mm=[0, 0, 178.0, 100.125], scale=1.0
                    )
                ],
                page=(178.0, 100.125),
            ),
        }
    )

    # 9b. 同一份 178mm 的输入**不带覆盖**：必须报页宽不符（证明覆盖真的起了作用，
    #     而不是这条检查本来就不会响）
    out.append(
        {
            "name": "journal-width-without-override",
            "profile_id": "lab-publication-v1",
            "spec": _spec(
                [
                    _panel(
                        "p1", manifest=_clean_manifest(), rect_mm=[0, 0, 178.0, 100.125], scale=1.0
                    )
                ],
                page=(178.0, 100.125),
            ),
        }
    )

    # 9c. journal 把容差覆盖成 **0**（「页宽必须精确等于规范值」）：80.3mm 必须报。
    #     这一对用例守的是「显式 0 ≠ 没表态」——Python 侧一度写成
    #     `_num(...) or 0.5`，把 0 当成缺省又换回 0.5mm，于是偷偷放行了
    #     不合规的页宽，而 TS 侧用 `??`、忠实执行 0，两条链路结论相反。
    out.append(
        {
            "name": "journal-zero-width-tolerance",
            "profile_id": "lab-publication-v1",
            "journal": {"widths_mm": {"tolerance_mm": 0.0}},
            "spec": _spec(
                [_panel("p1", manifest=_clean_manifest(), rect_mm=[0, 0, 80.3, 60.0])],
                page=(80.3, 60.0),
            ),
        }
    )

    # 9d. 同一份 80.3mm **不带覆盖**：默认 0.5mm 容差盖得住，不该报页宽
    #     （证明上一条的 error 真的来自那个 0，而不是这张图本来就不合规）
    out.append(
        {
            "name": "journal-zero-width-tolerance-baseline",
            "profile_id": "lab-publication-v1",
            "spec": _spec(
                [_panel("p1", manifest=_clean_manifest(), rect_mm=[0, 0, 80.3, 60.0])],
                page=(80.3, 60.0),
            ),
        }
    )

    # 9e. journal 把绝对字号下限覆盖成 **0**（「不设下限」）：4pt 不该再触发
    #     font-below-absolute-floor（font-too-small 仍会响，那是另一条规则）。
    m0 = _clean_manifest()
    m0["elements"][1]["editable"][1]["value"] = 4.0  # xticks fontsize
    out.append(
        {
            "name": "journal-zero-font-floor",
            "profile_id": "lab-publication-v1",
            "journal": {"absolute_min_font_size_pt": 0.0},
            "spec": _spec([_panel("p1", manifest=m0)]),
        }
    )

    # 9f. 期刊底 journal-base-v1：栏宽是 null（没规定），任何页宽都不报 page-width
    #     ——不是拿占位数字去比（ADR 0155）。
    out.append(
        {
            "name": "journal-base-null-widths-no-page-width",
            "profile_id": "journal-base-v1",
            "spec": _spec(
                [_panel("p1", manifest=_clean_manifest(), rect_mm=[0, 0, 123.0, 80.0])],
                page=(123.0, 80.0),
            ),
        }
    )

    # 9g. 同一底，期刊只写了双栏宽 170：123mm 要报，且只列出写了的那一栏
    out.append(
        {
            "name": "journal-base-only-double-width",
            "profile_id": "journal-base-v1",
            "journal": {"widths_mm": {"double": 170.0}},
            "spec": _spec(
                [_panel("p1", manifest=_clean_manifest(), rect_mm=[0, 0, 123.0, 80.0])],
                page=(123.0, 80.0),
            ),
        }
    )

    # 9h. 期刊自有栏位（Science 式 1 / 2 / 3 栏）：页宽命中任一 exact 栏位即通过，不套单栏 / 双栏
    def _col(cid: str, zh: str, en: str, w: float, qualifier: str = "exact") -> dict:
        return {"id": cid, "label": {"zh": zh, "en": en}, "width_mm": w, "qualifier": qualifier}

    sci = {
        "page_columns": [
            _col("col1", "1 栏", "1 column", 57.0),
            _col("col2", "2 栏", "2 columns", 121.0),
            _col("col3", "3 栏", "3 columns", 184.0),
        ],
        "widths_mm": {"single": 57.0, "double": 184.0},
    }
    for name, width in (("journal-columns-middle-hit", 121.0), ("journal-columns-miss", 100.0)):
        out.append(
            {
                "name": name,
                "profile_id": "journal-base-v1",
                "journal": sci,
                "spec": _spec(
                    [_panel("p1", manifest=_clean_manifest(), rect_mm=[0, 0, width, 80.0])],
                    page=(width, 80.0),
                ),
            }
        )
    # 只写了一档 exact（另一档 typical 不参与判据）：120mm 要报，提示只列 exact 那一档
    out.append(
        {
            "name": "journal-columns-one-exact-one-typical",
            "profile_id": "journal-base-v1",
            "journal": {
                "page_columns": [
                    _col("narrow", "窄栏", "Narrow", 90.0),
                    _col("wide", "宽栏", "Wide", 150.0, "typical"),
                ],
                "widths_mm": {"single": 90.0, "double": None},
            },
            "spec": _spec(
                [_panel("p1", manifest=_clean_manifest(), rect_mm=[0, 0, 150.0, 80.0])],
                page=(150.0, 80.0),
            ),
        }
    )

    # 9h2. 只写了最大宽度（max）的期刊（JACS 式：单栏 max 84.7、双栏 max 177.8）：页宽不得超过最大的那档 + 容差；
    #      exact 与 max 并存时两种都算合格；typical / range 仍不参与。
    maxj = {
        "page_columns": [
            _col("single", "单栏", "Single column", 84.7, "max"),
            _col("double", "双栏", "Double column", 177.8, "max"),
        ],
        "widths_mm": {"single": 84.7, "double": 177.8},
    }
    for name, width in (
        ("journal-max-over", 228.6),
        ("journal-max-at-limit", 177.8),
        ("journal-max-within-tolerance", 178.3),
        ("journal-max-just-over-tolerance", 178.4),
        ("journal-max-narrow-ok", 60.0),
    ):
        out.append(
            {
                "name": name,
                "profile_id": "journal-base-v1",
                "journal": maxj,
                "spec": _spec(
                    [_panel("p1", manifest=_clean_manifest(), rect_mm=[0, 0, width, 80.0])],
                    page=(width, 80.0),
                ),
            }
        )
    mixed = {
        "page_columns": [
            _col("col1", "1 栏", "1 column", 90.0),
            _col("cap", "最大", "Maximum", 140.0, "max"),
            _col("typ", "常用", "Typical", 200.0, "typical"),
        ],
        "widths_mm": {"single": 90.0, "double": None},
    }
    for name, width in (
        ("journal-exact-and-max-exact-hit", 90.0),
        ("journal-exact-and-max-under-max", 120.0),
        ("journal-exact-and-max-over", 150.0),
    ):
        out.append(
            {
                "name": name,
                "profile_id": "journal-base-v1",
                "journal": mixed,
                "spec": _spec(
                    [_panel("p1", manifest=_clean_manifest(), rect_mm=[0, 0, width, 80.0])],
                    page=(width, 80.0),
                ),
            }
        )

    # 9i. 期刊「说明」的字号建议 -> suggestion（带期刊名与原话），不是判据；底的字号下限为 0
    def _adv(role: str, value: float, quote: str) -> dict:
        return {
            "check": "font-size-recommended",
            "role": role,
            "value_pt": value,
            "qualifier": "recommended",
            "journal": "Elsevier",
            "quote": quote,
        }

    body = _adv("body_min", 7.0, "7 pt for normal text")
    script = _adv(
        "script_min", 6.0, "no smaller than 6 pt for subscript and superscript characters"
    )
    m65 = _clean_manifest()
    m65["elements"][1]["editable"][1]["value"] = 6.5  # xticks fontsize
    m55 = _clean_manifest()
    m55["elements"][1]["editable"][1]["value"] = 5.5
    for name, adv, manifest in (
        ("journal-advice-body-and-script", [body, script], m65),
        ("journal-advice-script-only", [script], m55),
        ("journal-advice-satisfied", [body, script], _clean_manifest()),
    ):
        out.append(
            {
                "name": name,
                "profile_id": "journal-base-v1",
                "journal": {"advisories": adv},
                "spec": _spec([_panel("p1", manifest=manifest)]),
            }
        )

    # 9j. 数据线线宽下限（`min_line_width_pt`，ADR 0155 §14 修订）：只判数据线——折线 / 线集 / 误差棒 /
    #     有可见描边的柱；坐标轴脊线、刻度、图例示意线、只画点的线、散点 / 填充（manifest 分不出有没有描边）不判。
    #     按最终有效值（线宽 × 面板缩放）比，等于下限不报
    def _lw_case(name: str, floor, manifest: dict, *, scale: float = 1.0) -> dict:
        return {
            "name": name,
            "profile_id": "journal-base-v1",
            "journal": {} if floor is None else {"min_line_width_pt": floor},
            "spec": _spec([_panel("p1", manifest=manifest, scale=scale)]),
        }

    def _el_with(manifest: dict, gid: str, **props) -> None:
        el = next(e for e in manifest["elements"] if e["gid"] == gid)
        for k, v in props.items():
            for f in el["editable"]:
                if f["prop"] == k:
                    f["value"] = v
                    break
            else:
                el["editable"].append({"prop": k, "type": "number", "value": v})

    out.append(_lw_case("journal-min-line-width-thin-lines-reported", 2.0, _clean_manifest()))
    out.append(_lw_case("journal-min-line-width-not-set-no-report", None, _clean_manifest()))
    out.append(_lw_case("journal-min-line-width-zero-no-report", 0, _clean_manifest()))
    m = _clean_manifest()  # 脊线 0.75、刻度、图例都细，数据线够粗：不报
    _el_with(m, "axes_0.lines_0", linewidth=2.5)
    _el_with(m, "axes_0.lines_1", linewidth=3.0)
    m["elements"][-1]["editable"].append(
        {"prop": "handle_linewidth", "type": "number", "value": 0.5}
    )
    out.append(_lw_case("journal-min-line-width-only-axes-and-ticks-thin", 2.0, m))
    m = _clean_manifest()
    _el_with(m, "axes_0.lines_0", linewidth=2.0)  # 正好等于下限
    _el_with(m, "axes_0.lines_1", linewidth=2.0)
    out.append(_lw_case("journal-min-line-width-equal-to-floor", 2.0, m))
    m = _clean_manifest()
    _el_with(m, "axes_0.lines_0", linewidth=1.25)  # 1.25 x 0.4 = 0.5，缩放后正好等于下限
    _el_with(m, "axes_0.lines_1", linewidth=1.0)  # 1.0 x 0.4 = 0.4，低于
    out.append(_lw_case("journal-min-line-width-judged-after-scale", 0.5, m, scale=0.4))
    m = _clean_manifest()
    _el_with(m, "axes_0.lines_0", linestyle="None")  # 只画点：线宽是设置器，不判
    out.append(_lw_case("journal-min-line-width-marker-only-line-skipped", 2.0, m))
    bar_manifest = _clean_manifest()
    bar_manifest["elements"] += [
        _el("axes_0.barseries_0", "bar_series", linewidth=0.5, edgecolor="#000000"),
        _el("axes_0.barseries_0.bar_0", "bar", linewidth=0.5, edgecolor="#000000"),
        _el("axes_0.barseries_1", "bar_series", linewidth=1.0, edgecolor="none"),
        _el("axes_0.barseries_1.bar_0", "bar", linewidth=1.0, edgecolor="none"),
        _el("axes_0.scatter_0", "scatter", linewidth=0.5, edgecolor="#000000"),
        _el("axes_0.fill_0", "fill", linewidth=0.5, edgecolor="#000000"),
        _el("axes_0.errorbar_0", "errorbar", linewidth=0.5),
        _el("axes_0.linecoll_0", "linecoll", linewidth=0.5),
        _el("axes_0.annotation_0", "arrow_patch", linewidth=0.5),
    ]
    for e in bar_manifest["elements"]:  # 两条折线先放粗，只看后加的图元
        if e["gid"] in ("axes_0.lines_0", "axes_0.lines_1"):
            e["editable"][0]["value"] = 3.0
    out.append(_lw_case("journal-min-line-width-bar-scatter-errorbar-linecoll", 2.0, bar_manifest))
    m = _clean_manifest()  # 隐藏的细线不在导出里：不报
    _el_with(m, "axes_0.lines_0", visible=False)
    out.append(_lw_case("journal-min-line-width-hidden-line-skipped", 2.0, m))

    # 9j'. 画布上的箭头 / 形状 / 路径（ADR 0208 §四 第 7 条，「都管」）：同一条 `line-width-below-min`，
    #      读 spec.objects 的 `stroke_pt`（页面绝对 pt，不乘缩放）；缺席 = 没有描边，不判；等于下限不报；
    #      隐藏的不判；文字 / 面板不判。数据线够粗，只看画布对象。
    def _canvas_lw_case(name: str, floor, objs: list[dict]) -> dict:
        m = _clean_manifest()
        _el_with(m, "axes_0.lines_0", linewidth=3.0)
        _el_with(m, "axes_0.lines_1", linewidth=3.0)
        base = _lw_case(name, floor, m)
        base["spec"] = _spec([_panel("p1", manifest=m)], extra_objects=objs)
        return base

    def _obj(oid: str, typ: str, stroke_pt=None, hidden=False) -> dict:
        o = {"id": oid, "type": typ, "rect_mm": [5.0, 5.0, 10.0, 6.0], "hidden": hidden}
        if stroke_pt is not None:
            o["stroke_pt"] = stroke_pt
        return o

    out.append(
        _canvas_lw_case(
            "canvas-line-width-thin-objects-reported",
            0.5,
            [_obj("a1", "arrow", 0.25), _obj("s1", "shape", 0.4), _obj("q1", "path", 0.1)],
        )
    )
    out.append(
        _canvas_lw_case(
            "canvas-line-width-equal-hidden-nostroke-skipped",
            0.5,
            [
                _obj("a1", "arrow", 0.5),  # 正好等于下限
                _obj("s1", "shape", 0.2, hidden=True),  # 隐藏
                _obj("s2", "shape"),  # 没有描边（字段缺席）
                _obj("t1", "text", 0.1),  # 文字不在判的类型里
            ],
        )
    )
    out.append(
        _canvas_lw_case(
            "canvas-line-width-floor-not-set-no-report", None, [_obj("a1", "arrow", 0.1)]
        )
    )
    m = _clean_manifest()  # 数据线与画布对象都细：一条检查，最糟的那次（画布 0.1）决定文案与 detail
    _el_with(m, "axes_0.lines_0", linewidth=0.3)
    mixed = _lw_case("canvas-line-width-mixed-with-data-line", 0.5, m)
    mixed["spec"] = _spec([_panel("p1", manifest=m)], extra_objects=[_obj("q1", "path", 0.1)])
    out.append(mixed)

    # 9k. V-001 对数轴上有 ≤ 0 的值（`log-axis-nonpositive`）：事实只读 axes 元素的 `scale_domain`
    #     （`engine/manifest.scale_domain`）。那一维 `status == "ok"` 且两个计数之和 > 0 才报；
    #     超预算 / 量不了 / 缺 status / 计数不是非负整数一律不判。每个（axes, 维）一次命中，x 先于 y，
    #     最糟（合计最大）的那次决定 detail。三张 severity 表都是 warn（漏登记会兜底成 warn，
    #     所以 severity 本身测不出漏登记——那一条由 `tests/test_preflight.py` 直接读表看护）
    def _log_case(name: str, domains: dict, *, profile_id: str = "lab-publication-v1") -> dict:
        m = _clean_manifest()
        for gid, domain in domains.items():
            el = next((e for e in m["elements"] if e["gid"] == gid), None)
            if el is None:
                el = _el(gid, "axes")
                m["elements"].append(el)
            el["scale_domain"] = domain
        return {"name": name, "profile_id": profile_id, "spec": _spec([_panel("p1", manifest=m)])}

    def _ok(points, low=0) -> dict:
        return {"status": "ok", "nonpositive": points, "errorbar_low_nonpositive": low}

    out.append(_log_case("log-axis-nonpositive-points", {"axes_0": {"y": _ok(2)}}))
    out.append(_log_case("log-axis-errorbar-low-only", {"axes_0": {"y": _ok(0, 1)}}))
    out.append(_log_case("log-axis-all-positive-no-report", {"axes_0": {"y": _ok(0, 0)}}))
    out.append(
        _log_case(
            "log-axis-not-measured-no-report",
            {
                "axes_0": {
                    # 超预算却带着计数：不许冒充判过
                    "x": {"status": "over_budget", "nonpositive": 5},
                    "y": {"status": "unavailable", "nonpositive": 3, "errorbar_low_nonpositive": 1},
                }
            },
        )
    )
    out.append(
        _log_case("log-axis-status-missing-no-report", {"axes_0": {"y": {"nonpositive": 3}}})
    )
    out.append(
        _log_case(
            "log-axis-bad-counts-no-report",
            {
                "axes_0": {
                    "x": {"status": "ok", "nonpositive": 1.5, "errorbar_low_nonpositive": -2},
                    "y": {"status": "ok", "nonpositive": "3", "errorbar_low_nonpositive": True},
                }
            },
        )
    )
    out.append(_log_case("log-axis-integral-float-count", {"axes_0": {"y": _ok(2.0)}}))
    out.append(_log_case("log-axis-not-a-dict-no-report", {"axes_0": {"y": [2, 0]}}))
    out.append(
        _log_case(
            "log-axis-two-dims-two-axes",
            {
                "axes_0": {"x": _ok(1), "y": _ok(3, 1)},
                "axes_1": {"y": _ok(2)},
            },
        )
    )
    m_case = _log_case("log-axis-hidden-axes-no-report", {"axes_0": {"y": _ok(2)}})
    m_case["spec"]["panels"][0]["manifest"]["elements"][0]["editable"].append(
        {"prop": "visible", "type": "bool", "value": False}
    )
    out.append(m_case)
    out.append(
        _log_case(
            "log-axis-nonpositive-free-form", {"axes_0": {"y": _ok(2)}}, profile_id="free-form-v1"
        )
    )
    out.append(
        _log_case(
            "log-axis-nonpositive-journal-base",
            {"axes_0": {"y": _ok(2)}},
            profile_id="journal-base-v1",
        )
    )

    # 9l. V-002 线性轴上柱的共同基线被截掉（`bar-baseline-truncated`）：事实只读 axes 元素的 `bar_baseline`
    #     （`engine/manifest.bar_baseline`，只有基线），范围与尺度从同一元素的 `[xy]lim` / `[xy]scale` 读。
    #     那一维 `status == "ok"`、尺度**正是** linear、范围两个有限数且 lo < hi、基线落在闭区间外才报；
    #     每个（axes, 维）一次命中，x 先于 y，截得最远的基线决定 detail，跨轴按「截掉的距离 / 跨度」比最糟。
    #     message params 是 `_axis_num` ↔ `axisNum` 写出来的字符串，量级与正好一半的舍入在下面几条里各压一次
    def _axes_el(gid: str, *, xlim=None, ylim=None, xscale=None, yscale=None) -> dict:
        el = _el(gid, "axes")
        for prop, value, typ in (
            ("xlim", xlim, "pair"),
            ("ylim", ylim, "pair"),
            ("xscale", xscale, "enum"),
            ("yscale", yscale, "enum"),
        ):
            if value is not None:
                el["editable"].append({"prop": prop, "type": typ, "value": value})
        return el

    def _bar_case(name: str, axes: list[tuple[dict, dict]], *, profile_id="lab-publication-v1"):
        m = _clean_manifest()
        m["elements"] = [e for e in m["elements"] if e["gid"] != "axes_0"]
        for el, facts in axes:
            el["bar_baseline"] = facts
            m["elements"].insert(0, el) if el["gid"] == "axes_0" else m["elements"].append(el)
        return {"name": name, "profile_id": profile_id, "spec": _spec([_panel("p1", manifest=m)])}

    def _bok(*entries) -> dict:
        return {"status": "ok", "baselines": [{"value": v, "bars": n} for v, n in entries]}

    def _vy(gid="axes_0", ylim=(4.0, 9.0), yscale="linear", **kw) -> dict:
        return _axes_el(gid, ylim=list(ylim) if ylim is not None else None, yscale=yscale, **kw)

    out.append(_bar_case("bar-baseline-cut-y", [(_vy(), {"y": _bok((0.0, 2))})]))
    out.append(
        _bar_case(
            "bar-baseline-cut-x-barh",
            [(_axes_el("axes_0", xlim=[4.0, 9.0], xscale="linear"), {"x": _bok((0.0, 3))})],
        )
    )
    out.append(
        _bar_case("bar-baseline-visible-no-report", [(_vy(ylim=(0.0, 8.4)), {"y": _bok((0.0, 2))})])
    )
    out.append(
        _bar_case(
            "bar-baseline-on-the-edge-no-report",
            # 自动范围下 sticky edge 让下端恰好等于基线（闭区间）；统一的非 0 基线（指数柱 100）同理
            [(_vy(ylim=(100.0, 150.0)), {"y": _bok((100.0, 4))})],
        )
    )
    out.append(
        _bar_case("bar-baseline-nonzero-cut", [(_vy(ylim=(120.0, 150.0)), {"y": _bok((100.0, 4))})])
    )
    out.append(
        _bar_case("bar-baseline-inverted-lim", [(_vy(ylim=(9.0, 4.0)), {"y": _bok((0.0, 2))})])
    )
    out.append(
        _bar_case(
            "bar-baseline-above-the-top",
            # 负值柱从 0 往下，范围却停在 -2：越过的是上端
            [(_vy(ylim=(-9.0, -2.0)), {"y": _bok((0.0, 2))})],
        )
    )
    out.append(
        _bar_case(
            "bar-baseline-nonlinear-or-missing-scale-no-report",
            [
                (_vy("axes_0", yscale="log"), {"y": _bok((0.0, 2))}),
                (_vy("axes_1", yscale="symlog"), {"y": _bok((0.0, 2))}),
                (_vy("axes_2", yscale="logit"), {"y": _bok((0.0, 2))}),
                (_vy("axes_3", yscale="asinh"), {"y": _bok((0.0, 2))}),
                (_vy("axes_4", yscale=None), {"y": _bok((0.0, 2))}),
                (_vy("axes_5", ylim=None), {"y": _bok((0.0, 2))}),
            ],
        )
    )
    out.append(
        _bar_case(
            "bar-baseline-bad-lim-no-report",
            [
                (_vy("axes_0", ylim=(5.0, 5.0)), {"y": _bok((0.0, 2))}),
                (_axes_el("axes_1", ylim=[4.0], yscale="linear"), {"y": _bok((0.0, 2))}),
                (_axes_el("axes_2", ylim=[4.0, "9"], yscale="linear"), {"y": _bok((0.0, 2))}),
                (_axes_el("axes_3", ylim=[True, 9.0], yscale="linear"), {"y": _bok((0.0, 2))}),
            ],
        )
    )
    out.append(
        _bar_case(
            "bar-baseline-not-measured-no-report",
            [
                (
                    _vy("axes_0"),
                    {"y": {"status": "over_budget", "baselines": [{"value": 0, "bars": 2}]}},
                ),
                (
                    _vy("axes_1"),
                    {"y": {"status": "unavailable", "baselines": [{"value": 0, "bars": 2}]}},
                ),
                (_vy("axes_2"), {"y": {"baselines": [{"value": 0, "bars": 2}]}}),
                (_vy("axes_3"), {"y": [0, 2]}),
                (_vy("axes_4"), []),
            ],
        )
    )
    out.append(
        _bar_case(
            "bar-baseline-bad-entries-no-report",
            [
                (_vy("axes_0"), {"y": {"status": "ok", "baselines": {"value": 0, "bars": 2}}}),
                (
                    _vy("axes_1"),
                    {
                        "y": {
                            "status": "ok",
                            "baselines": [
                                {"value": "0", "bars": 2},
                                {"value": True, "bars": 2},
                                {"value": 0, "bars": 0},
                                {"value": 0, "bars": 1.5},
                                {"value": 0, "bars": True},
                                {"bars": 2},
                                [0, 2],
                            ],
                        }
                    },
                ),
                (_vy("axes_2"), {"y": {"status": "ok", "baselines": []}}),
            ],
        )
    )
    out.append(
        _bar_case(
            "bar-baseline-wrong-dim-no-report",
            # 竖柱的事实只在 y：x 范围截不截与它无关
            [
                (
                    _axes_el(
                        "axes_0", xlim=[4.0, 9.0], xscale="linear", ylim=[0.0, 9.0], yscale="linear"
                    ),
                    {"y": _bok((0.0, 2))},
                )
            ],
        )
    )
    out.append(
        _bar_case(
            "bar-baseline-worst-wins",
            [
                # 同一维两个基线：截得最远的（0，离 120 有 120）决定 detail
                (_vy("axes_0", ylim=(120.0, 200.0)), {"y": _bok((100.0, 1), (0.0, 3))}),
                # 另一根轴截得比例更大（(9 - 0) / 1 = 9 倍跨度 vs 1.5 倍）：它决定整条的 detail
                (_vy("axes_1", ylim=(9.0, 10.0)), {"y": _bok((0.0, 2))}),
                (_axes_el("axes_2", xlim=[1.0, 2.0], xscale="linear"), {"x": _bok((0.0, 5))}),
            ],
        )
    )
    out.append(
        _bar_case(
            "bar-baseline-hidden-axes-no-report",
            [(_axes_el("axes_0", ylim=[4.0, 9.0], yscale="linear"), {"y": _bok((0.0, 2))})],
        )
    )
    out[-1]["spec"]["panels"][0]["manifest"]["elements"][0]["editable"].append(
        {"prop": "visible", "type": "bool", "value": False}
    )
    for i, (lim, base) in enumerate(
        (
            ((123456.7, 1e7), 0.0),
            ((1e-7, 3e-7), 0.0),
            ((0.1 + 0.2, 0.9), 0.0),
            ((1000.5, 2000.0), 12345.5),
            ((2.0005, 3.0), 0.0),
            ((-0.0000004, -0.0000001), 0.0),
        )
    ):
        out.append(
            _bar_case(f"bar-baseline-number-format-{i}", [(_vy(ylim=lim), {"y": _bok((base, 1))})])
        )
    out.append(
        _bar_case(
            "bar-baseline-free-form",
            [(_vy(), {"y": _bok((0.0, 2))})],
            profile_id="free-form-v1",
        )
    )
    out.append(
        _bar_case(
            "bar-baseline-journal-base",
            [(_vy(), {"y": _bok((0.0, 2))})],
            profile_id="journal-base-v1",
        )
    )

    # 10. free-form profile：同一份输入，等级整体降一档（severity 表说了算）。
    #     4pt 才低于它的 6pt 下限——同样一张图在严格 profile 下是 error。
    m = _clean_manifest()
    m["elements"][1]["editable"][1]["value"] = 4.0
    m["elements"][5]["editable"][0]["value"] = True  # legend frameon（free-form 不管）
    m["elements"][6]["editable"][0]["value"] = 1.2  # 线宽（free-form 没有档位）
    out.append(
        {
            "name": "free-form-downgrades",
            "profile_id": "free-form-v1",
            "spec": _spec([_panel("p1", manifest=m)], page=(123.0, 60.0)),
        }
    )

    # 11. 中文 fallback：中文轴标签配的是 DejaVu，导出必然是方框
    m = _clean_manifest()
    m["elements"][3]["editable"] = [
        {"prop": "text", "type": "text", "value": "温度 (K)"},
        {"prop": "fontsize", "type": "number", "value": 9.0},
        {"prop": "fontfamily", "type": "enum", "value": "DejaVu Sans"},
    ]
    out.append(
        {
            "name": "cjk-without-fallback",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 11b. 回退链接住了（ADR 0045）：汉字由白名单里的脸画出——中日韩那条**不响**
    # （正文族 DejaVu Sans 仍然会被拉丁白名单那条报，那是另一条规则）
    m = _clean_manifest()
    m["elements"][3]["editable"] = [
        {"prop": "text", "type": "text", "value": "温度 (K)"},
        {"prop": "fontsize", "type": "number", "value": 9.0},
        {"prop": "fontfamily", "type": "enum", "value": "DejaVu Sans"},
    ]
    m["elements"][3]["cjk_family"] = "PingFang SC"
    out.append(
        {
            "name": "cjk-drawn-by-accepted-fallback-face",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 11c. 回退链接住了，但那张脸不在白名单里：报的是**那张脸**，不是「会是方框」
    m = _clean_manifest()
    m["elements"][3]["editable"] = [
        {"prop": "text", "type": "text", "value": "温度 (K)"},
        {"prop": "fontsize", "type": "number", "value": 9.0},
        {"prop": "fontfamily", "type": "enum", "value": "Times New Roman"},
    ]
    m["elements"][3]["cjk_family"] = "Some Other CJK Face"
    out.append(
        {
            "name": "cjk-drawn-by-unaccepted-fallback-face",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 12. 数据语义建议：柱状图无误差棒 + 拟合无置信带 + jet 色谱
    m = _clean_manifest()
    m["elements"] = [
        m["elements"][0],
        m["elements"][1],
        m["elements"][2],
        _el("axes_0.barseries_0", "bar_series", linewidth=0.75, label="Yield"),
        _el("axes_0.lines_0", "line", linewidth=1.0, marker="None", label="linear fit"),
        _el("axes_0.lines_1", "line", linewidth=1.0, marker="None", label="raw"),
        _el("axes_0.images_0", "image", cmap="jet"),
    ]
    out.append(
        {
            "name": "data-semantics-suggestions",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 12b. 两条曲线都没有 marker，但线型一实一虚：本来就分得开，marker 那条建议
    #      **不响**（教程 Fig1 就是这种图；上一条里两条线共用默认线型才响）。
    #      三条线里两条共用线型 → 仍响：判据是「线型有重复」，不是「有两种线型」
    m = _clean_manifest()
    m["elements"] = [
        m["elements"][0],
        m["elements"][1],
        m["elements"][2],
        _el("axes_0.lines_0", "line", linewidth=1.0, marker="None", linestyle="-", label="a"),
        _el("axes_0.lines_1", "line", linewidth=1.0, marker="None", linestyle="--", label="b"),
    ]
    out.append(
        {
            "name": "distinct-linestyles-without-markers",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )
    m = _clean_manifest()
    m["elements"] = [
        m["elements"][0],
        m["elements"][1],
        m["elements"][2],
        _el("axes_0.lines_0", "line", linewidth=1.0, marker="None", linestyle="-", label="a"),
        _el("axes_0.lines_1", "line", linewidth=1.0, marker="None", linestyle="--", label="b"),
        _el("axes_0.lines_2", "line", linewidth=1.0, marker="None", linestyle="-", label="c"),
    ]
    out.append(
        {
            "name": "repeated-linestyle-without-markers",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 13. 面板状态：缺素材 / 渲染失败 / 过期 / override 没画上 / 位图嵌入
    out.append(
        {
            "name": "panel-states",
            "profile_id": "lab-publication-v1",
            "spec": _spec(
                [
                    _panel(
                        "p1",
                        manifest=_clean_manifest(),
                        missing=True,
                        stale=True,
                        render_error="boom",
                        unapplied_overrides=3,
                    )
                ]
            ),
        }
    )

    # 13b. 字重策略与轴标题格式：轴标题该加粗（2026-08-24 定版，其余角色一律
    #      常规字重）、轴标题该是「Title (unit)」。两条都只是建议——但**必须
    #      真的会响**，而且两个方向都要有：axis_label 是「该粗没粗」，
    #      ticklabel 是「不该粗却粗了」——只测一个方向的话，比较逻辑写反
    #      （want/got 颠倒）时向量照样全绿。另外 manifest 里的角色名是
    #      ticklabel 而不是 tick_label，键写错的话它永远沉默，看起来像一直通过。
    m = _clean_manifest()
    m["elements"][3]["editable"][0]["value"] = "Temperature"  # 缺单位括号
    m["elements"][3]["editable"].append(
        {"prop": "weight", "type": "enum", "value": "normal"}
    )  # 该粗没粗
    m["elements"].append(
        {
            "gid": "axes_0.xticklabels_0",
            "role": "ticklabel",
            "label": "刻度",
            "draggable": True,
            "bbox": [0.1, 0.9, 0.05, 0.03],
            "editable": [
                {"prop": "text", "type": "text", "value": "100"},
                {"prop": "fontsize", "type": "number", "value": 9.0},
                {"prop": "weight", "type": "enum", "value": "bold"},
            ],
        }
    )  # 不该粗却粗了
    out.append(
        {
            "name": "weight-and-label-format",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 13c. 中文 fallback 被期刊覆盖清空：画布中文标注也要报（这条检查唯一的触发路径）
    out.append(
        {
            "name": "cjk-fallback-cleared-by-journal",
            "profile_id": "lab-publication-v1",
            "journal": {"cjk_fallback": {"required": True, "accepted": []}},
            "spec": _spec(
                [_panel("p1", manifest=_clean_manifest())],
                texts=[_text("t-cn", 9.0, "图 1 反应动力学")],
            ),
        }
    )

    # 13d. 字号上限：标题 24pt 远大于正文字号
    m = _clean_manifest()
    m["elements"].append(_el("axes_0.title", "title", fontsize=24.0, text="Kinetics"))
    out.append(
        {
            "name": "font-too-large",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 13e. 画布文字的字体族：规范只认衬线那一族，无衬线是「已知的替代品」、
    #      等宽连替代品都算不上——**两条走不同的 message key**，而两侧必须
    #      给出同一个答案。这条向量是「标注能设字体」这个新能力的跨语言看护。
    out.append(
        {
            "name": "canvas-text-font-family",
            "profile_id": "lab-publication-v1",
            "spec": _spec(
                [_panel("p1", manifest=_clean_manifest())],
                texts=[
                    _text("t-serif", 9.0, "(a)", font_family="serif"),
                    _text("t-sans", 9.0, "(b)", font_family="sans-serif"),
                ],
            ),
        }
    )
    out.append(
        {
            "name": "canvas-text-font-family-unknown",
            "profile_id": "lab-publication-v1",
            "spec": _spec(
                [_panel("p1", manifest=_clean_manifest())],
                texts=[_text("t-mono", 9.0, "(a)", font_family="monospace")],
            ),
        }
    )

    # 13e'. 排不出来的公式（ADR 0152 §七）：事实由 spec 的 `math_errors` 带进来——
    #       ① 失败的公式 → math-unrenderable（warn）；② 空列表 / 缺席 → 不报；
    #       ③ 隐藏的文字 → 不报；④ 两条失败 → 一条问题、count 2。
    out.append(
        {
            "name": "canvas-text-math-unrenderable",
            "profile_id": "lab-publication-v1",
            "spec": _spec(
                [_panel("p1", manifest=_clean_manifest())],
                texts=[
                    _text(
                        "t-bad",
                        9.0,
                        "$\\notacommand{x}$ and $\\alpha$",
                        text_format="latex",
                        math_errors=[{"src": "\\notacommand{x}", "why": "unknown symbol"}],
                    ),
                    _text("t-ok", 9.0, "$\\alpha$", text_format="latex", math_errors=[]),
                    _text(
                        "t-hidden",
                        9.0,
                        "$\\bad$",
                        text_format="latex",
                        hidden=True,
                        math_errors=[{"src": "\\bad", "why": "x"}],
                    ),
                    _text(
                        "t-two",
                        9.0,
                        "$a_$ $\\frac{1}$",
                        text_format="latex",
                        math_errors=[
                            {"src": "a_", "why": "Expected end of text\nline two"},
                            {"src": "\\frac{1}", "why": "x" * 100},
                        ],
                    ),
                ],
            ),
        }
    )

    # 13e''. 原图字体（ADR 0164）：事实由 spec 的 `source_font_missing` / `source_font_in_use` 带进来——
    #        ① 用不了 → source-font-unavailable（按原因选文案 key，名字保留）；② 能用（in_use）→ 不报它，
    #        且批准字体的覆盖表不适用：一个批准集合里没有的字符不报 glyph-missing；③ 隐藏的文字照常被
    #        跳过；④ 未知原因 → 落到 unreadable 的文案（不抛）。
    out.append(
        {
            "name": "canvas-text-source-font",
            "profile_id": "lab-publication-v1",
            "spec": _spec(
                [_panel("p1", manifest=_clean_manifest())],
                texts=[
                    _text(
                        "t-gone",
                        9.0,
                        "Hello",
                        source_font_missing={"family": "Arial", "reason": "not_found"},
                    ),
                    _text(
                        "t-restricted",
                        9.0,
                        "Hello",
                        source_font_missing={
                            "family": "Vendor Sans",
                            "reason": "embedding_restricted",
                        },
                    ),
                    _text(
                        "t-odd",
                        9.0,
                        "Hello",
                        source_font_missing={"family": "X", "reason": "something_new"},
                    ),
                    _text("t-in-use", 9.0, "\U0001d538", source_font_in_use=True),
                    _text("t-approved", 9.0, "\U0001d538"),
                ],
            ),
        }
    )

    # 13f. 字形覆盖（Prompt 14）。四条各盯一个具体的错法：
    #      ① 画布文字里有谁都画不出的字符 → glyph-missing（导出上是方框）；
    #      ② 画布文字里的 `⁵`：U10 之前它退到 PyMuPDF 自己挑的回退脸 → glyph-substituted；批准字体集合
    #         （ADR 0060 / 0072）没有回退层、Liberation 自带 `⁵`，所以**期望是空**——这条向量从此守的是
    #         「不再有替换建议」（D07：换脸不是缺陷，报一条改不动的限制只会训练用户忽略面板）；
    #      ③ 同一段文字选 `scientific` 之后**一条都不该报**——判据必须量渲染表示，量原文的话这条会假红；
    #      ④ 图内文字的两张单子直接来自 manifest（产生者只有引擎一处；worker 侧的 matplotlib 回退脸仍存在）。
    out.append(
        {
            "name": "canvas-text-glyph-missing",
            "profile_id": "lab-publication-v1",
            "spec": _spec(
                [_panel("p1", manifest=_clean_manifest())],
                texts=[_text("t-arabic", 9.0, "T\u061f = 5")],
            ),
        }
    )
    out.append(
        {
            "name": "canvas-text-glyph-substituted",
            "profile_id": "lab-publication-v1",
            "spec": _spec(
                [_panel("p1", manifest=_clean_manifest())],
                texts=[_text("t-sup", 9.0, "\u00d710\u2075")],
            ),
        }
    )
    out.append(
        {
            "name": "canvas-text-glyph-scientific-mode",
            "profile_id": "lab-publication-v1",
            "spec": _spec(
                [_panel("p1", manifest=_clean_manifest())],
                texts=[_text("t-sup", 9.0, "\u00d710\u2075", interpretation="scientific")],
            ),
        }
    )
    m = _clean_manifest()
    m["elements"][3]["glyphs_missing"] = ["\u4e2d", "\u6587"]
    m["elements"][4]["glyphs_fallback"] = ["\u2075", "\u207b"]
    out.append(
        {
            "name": "figure-text-glyph-coverage",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 15. 图例项的示意线（ADR 0034）：自定义的一项线宽脱档要报、定位到那一项；
    #     跟随源的一项即使示意线宽脱档也不报（源那条规则已经管着，源本身合规）
    m = _clean_manifest()
    m["elements"] += [
        {
            **_el("axes_0.legend.texts_0", "legend_text", fontsize=9.0, handle_linewidth=1.2),
            "legend_entry": {
                "index": 0,
                "source_gid": "axes_0.lines_0",
                "binding_default": "custom",
            },
        },
        {
            **_el("axes_0.legend.texts_1", "legend_text", fontsize=9.0, handle_linewidth=1.2),
            "legend_entry": {
                "index": 1,
                "source_gid": "axes_0.lines_1",
                "binding_default": "follow_source",
            },
        },
    ]
    for j, binding in ((0, "custom"), (1, "follow_source")):
        m["elements"][-2 + j]["editable"].append(
            {"prop": "binding", "type": "enum", "value": binding}
        )
    out.append(
        {
            "name": "legend-entry-custom-handle-width",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 14. 刻度标签超过 10 个（规范建议控制在 10 以内）
    m = _clean_manifest()
    m["elements"] += [_el(f"axes_0.xticklabels_{i}", "ticklabel", fontsize=9.0) for i in range(12)]
    out.append(
        {
            "name": "too-many-tick-labels",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 17. 元素超出图幅（审计 T14）：数字取自教程 Fig1_kinetics 的真实 manifest
    #     （figsize 80 × 57.6，脚本存盘用 bbox_inches="tight" 才没被裁）。
    #     x 轴标题底边 1.0425 → 探出 2.45 mm，y 轴标题左边 −0.007 → 0.56 mm，
    #     两条都报、同一条问题、文案与 detail 属于最糟的那条；图例只探出
    #     0.12 mm（descender 留白那一档）→ 容差之内不报。
    m = _clean_manifest()
    m["size_mm"] = [80.0, 57.6]
    m["elements"][3]["bbox"] = [0.3152, 0.9874, 0.3946, 0.0551]  # xlabel
    m["elements"][4]["bbox"] = [-0.007, 0.3224, 0.0397, 0.3652]  # ylabel
    m["elements"][5]["bbox"] = [0.6, -0.002, 0.3, 0.05]  # legend：0.12 mm
    out.append(
        {
            "name": "figure-element-clipped",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 17b. 被坐标轴裁住的离群数据**不算超出图幅**（评审 P1）。曲线 / 散点的
    #      bbox 是**未裁剪的整个数据范围**：`scatter(x=[…, 1e3])` 配 `xlim=(0,1)`
    #      能把它撑到图幅的几百倍，而 matplotlib 在 axes patch 处就切掉了，图幅
    #      边界处一点内容都没丢。数字取自本机 matplotlib 3.10.8 的真实 manifest
    #      （figsize 4×3 in / 100 dpi = 101.6 × 76.2 mm，子图 [50,33]–[360,264]）。
    #      同一张图上放一条 `clip_on=False` 的标注（没有 clip_bbox）：它真的会
    #      画到图幅外，必须照旧报——**两个方向都要在同一条向量里**，否则「不报」
    #      也可能是这条规则整个不响了。
    m = _clean_manifest()
    m["size_mm"] = [101.6, 76.2]
    axes_clip = [0.125, 0.12, 0.775, 0.77]  # 子图框折成 figure 分数（top-origin）
    scatter = _el("axes_0.scatter_0", "scatter", label="pts")
    scatter["bbox"] = [0.203, 0.505, 774.923, 0.308]  # 离群点把包围盒撑到 774 倍宽
    scatter["clip_bbox"] = axes_clip
    line = _el("axes_0.lines_2", "line", linewidth=1.0, marker="None", label="raw")
    line["bbox"] = [0.203, 0.197, 774.923, 0.539]
    line["clip_bbox"] = axes_clip
    noclip = _el("axes_0.texts_0", "text", text="outside", fontsize=9.0)
    noclip["bbox"] = [0.512, -0.379, 0.128, 0.047]  # clip_on=False：真画到图幅上方
    # 四边都探出、整个被子图裁住：**四条边分别**都得钉住，只造「右边探出」的话
    # 另外三条边的取大/取小写反了都不会有任何用例变红
    allsides = _el("axes_0.fill_0", "fill", linewidth=0.75)
    allsides["bbox"] = [-2.0, -2.0, 4.0, 4.0]
    allsides["clip_bbox"] = axes_clip
    # 裁剪框与元素都在图幅左侧之外且不相交：不跳过空交集的话会报 20.32 mm
    gone = _el("axes_0.patches_0", "patch", linewidth=0.75)
    gone["bbox"] = [-0.5, 0.3, 0.2, 0.2]
    gone["clip_bbox"] = [-0.2, 0.12, 0.1, 0.77]
    # 读不懂的 clip_bbox 当作「不裁」——盲区宁可多报，绝不静默放行（两侧同一口径）
    broken = _el("axes_0.texts_1", "text", text="broken clip", fontsize=9.0)
    broken["bbox"] = [0.512, -0.05, 0.128, 0.047]  # 只探出 3.81 mm，压不过上面那条
    broken["clip_bbox"] = ["x", None, 1, 1]
    m["elements"] += [scatter, line, noclip, allsides, gone, broken]
    out.append(
        {
            "name": "figure-element-clipped-to-axes",
            "profile_id": "lab-publication-v1",
            # 页面用合规的双栏 150 × 112.5（4:3），免得页宽/比例的噪音盖住这条
            "spec": _spec(
                [_panel("p1", manifest=m, rect_mm=[0, 0, 101.6, 76.2])],
                page=(150.0, 112.5),
            ),
        }
    )

    # 18. 字号只量**画出来的文字元素**（容器上的设置器不量）与隐藏图例不报。
    #     探针取自真 worker 跑出来的 manifest：图例 `legend(title=…, title_fontsize=5,
    #     fontsize=7)`、`set_visible(False)` 的带标题图例、`labelsize=5` 的色条。
    #     四条向量各盯一个：标题只报一次且挂 `.title`；逐条改好条目后图例级不再报；
    #     隐藏图例（条目仍自报 visible=True）整体不报；色条刻度只报一次（挂 `ticks`）。
    def _legend_probe(
        gid: str,
        *,
        container: float,
        title: float | None,
        entries: list[float],
        family: str = "Times New Roman",
        **leg,
    ) -> list[dict]:
        els = [_el(gid, "legend", frameon=False, fontsize=container, title_fontsize=title, **leg)]
        if title is not None:
            els.append(_el(f"{gid}.title", "legend_text", fontsize=title, fontfamily=family))
        els += [
            _el(f"{gid}.texts_{j}", "legend_text", fontsize=size) for j, size in enumerate(entries)
        ]
        return els

    def _swap_legend(m: dict, probe: list[dict]) -> list[dict]:
        """用探针图例换掉干净图例（下标 5）及其末尾那条默认条目，其余元素原样保留"""
        return m["elements"][:5] + probe + m["elements"][6:-1]

    # 18a. 图例标题 4.5pt：只在 `.title` 上报一次（旧行为：图例本体 title_fontsize 再报一次）
    m = _clean_manifest()
    m["elements"] = _swap_legend(
        m, _legend_probe("axes_0.legend", container=9.0, title=4.5, entries=[9.0])
    )
    out.append(
        {
            "name": "legend-title-small-reported-once-on-title",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 18b. 逐条改好条目（9pt）后，图例级设置器仍是创建时的 7 / 标题 7：什么都不该报
    m = _clean_manifest()
    m["elements"] = _swap_legend(
        m, _legend_probe("axes_0.legend", container=7.0, title=9.0, entries=[9.0, 8.5])
    )
    out.append(
        {
            "name": "legend-entries-fixed-container-setter-silent",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 18c. `legend-font-size` 量条目：两条出界（5pt 离 8 区间 3、11pt 离 9 区间 2）取最远的 5pt；
    #      隐藏的条目（visible=False）不参与
    m = _clean_manifest()
    m["elements"] = _swap_legend(
        m, _legend_probe("axes_0.legend", container=9.0, title=None, entries=[5.0, 11.0, 4.0])
    )
    m["elements"][8]["editable"].append({"prop": "visible", "type": "bool", "value": False})
    out.append(
        {
            "name": "legend-font-size-measures-visible-entries",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 18d. 隐藏图例：条目与标题仍报 visible=True，但整体没画——字号 / 字体族 / 无框 /
    #      示意线宽 / 图例字号一律不报
    m = _clean_manifest()
    hidden = _legend_probe(
        "axes_0.legend", container=5.0, title=4.5, entries=[5.0], family="Arial", visible=False
    )
    hidden[0]["editable"][0]["value"] = True  # frameon：隐藏的图例带框也不报
    hidden[-1]["editable"].append({"prop": "handle_linewidth", "type": "number", "value": 1.234})
    m["elements"] = _swap_legend(m, hidden)
    out.append(
        {
            "name": "hidden-legend-reports-nothing",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 18e. 色条 `labelsize=5`：色条元素的 tick_fontsize 不量，色条轴自己的刻度组
    #      （真实字号）只报一次
    m = _clean_manifest()
    m["elements"] += [
        _el("axes_1.colorbar", "colorbar", tick_fontsize=5.0),
        _el("axes_1.yticks", "ticks", direction="in", fontsize=5.0),
    ]
    out.append(
        {
            "name": "colorbar-ticks-reported-once",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 18f. 柱形系列：容器 linewidth 是设置器（广播到每根柱），柱各自是 `bar` 元素。
    #      数字取自真 worker 探针：容器与三根柱都是 0.3pt（不在档位）——只报在柱上，
    #      容器不报（茎叶 / 误差棒系列的线宽规则另有出处，不在此列）
    m = _clean_manifest()
    m["elements"] += [
        _el("axes_1.barseries_0", "bar_series", linewidth=0.3),
        *(_el(f"axes_1.barseries_0.bar_{k}", "bar", linewidth=0.3) for k in range(3)),
    ]
    out.append(
        {
            "name": "bar-series-container-linewidth-not-measured",
            "profile_id": "lab-publication-v1",
            "spec": _spec([_panel("p1", manifest=m)]),
        }
    )

    # 19. 干涉检测接进常规预检（V-011）：判据是 `engine/interference.detect`（逐对向量在
    #     `tests/golden/interference_vectors.json`），这里看的是**折进 _Sink 之后**的聚合投影——
    #     同一 id 的多对并成一条、最糟的一对决定文案与 detail；图例「确定」恒压过「风险」；
    #     压到别的子图只把伸出去的文字当主语（被压的子图进 detail.against）。几何用二进制上精确的数。
    def _box(gid: str, role: str, bbox: list, **props) -> dict:
        el = _el(gid, role, **props)
        el["bbox"] = bbox
        return el

    def _pos(el: dict, rect: list) -> dict:
        el["editable"].append({"prop": "position", "type": "rect", "value": rect})
        return el

    def _geo(el: dict, *points) -> dict:
        el["geometry"] = {
            "kind": "polyline",
            "paths": [{"points": [list(p) for p in points], "closed": False}],
            "fill": False,
            "stroke": True,
        }
        return el

    def _icase(name: str, elements: list, *, profile_id: str = "lab-publication-v1", **kw) -> dict:
        m = _clean_manifest()
        m["elements"] += elements
        return {
            "name": name,
            "profile_id": profile_id,
            "spec": _spec([_panel("p1", manifest=m, **kw)]),
        }

    u = 1 / 512
    three_texts = [
        _box("axes_0.title", "title", [0.25, 0.25, 0.125, 0.0625]),
        _box("axes_0.texts_0", "text", [0.25 + 0.125 - 8 * u, 0.25, 0.125, 0.0625]),
        _box("axes_0.texts_1", "text", [0.25 + 0.25 - 16 * u, 0.25, 0.125, 0.0625]),
    ]
    out.append(_icase("interference-text-overlap-aggregates-pairs", three_texts))
    out.append(
        _icase(
            "interference-text-over-axes-subject-is-the-text",
            [
                _pos(
                    _box("axes_5", "axes", [0.125, 0.125, 0.25, 0.75]), [0.125, 0.125, 0.25, 0.75]
                ),
                _pos(_box("axes_6", "axes", [0.5, 0.125, 0.25, 0.75]), [0.5, 0.125, 0.25, 0.75]),
                _box("axes_6.ylabel", "axis_label", [0.375 - 6 * u, 0.375, 8 * u, 0.125]),
            ],
        )
    )
    out.append(
        _icase(
            "interference-legend-certain-beats-risk",
            [
                _box("axes_7.legend", "legend", [0.5, 0.125, 0.25, 0.25]),
                _geo(
                    _box("axes_7.lines_0", "line", [0.125, 0.125, 0.75, 0.75]),
                    (0.125, 0.875),
                    (0.875, 0.125),
                ),
                _box("axes_8.legend", "legend", [0.5, 0.625, 0.25, 0.25]),
                *(
                    _box(f"axes_8.collections_{k}", "collection", [0.125, 0.5, 0.75, 0.5])
                    for k in range(3)
                ),
            ],
        )
    )
    out.append(
        _icase(
            "interference-legend-risk-only",
            [
                _box("axes_8.legend", "legend", [0.5, 0.625, 0.25, 0.25]),
                _box("axes_8.collections_0", "collection", [0.125, 0.5, 0.75, 0.5]),
            ],
        )
    )
    out.append(_icase("interference-hidden-panel-skipped", three_texts, hidden=True))
    out.append(_icase("interference-free-form", three_texts, profile_id="free-form-v1"))
    out.append(_icase("interference-journal-base", three_texts, profile_id="journal-base-v1"))
    return out


def evaluate(case: dict) -> list[dict]:
    profile = profiles.load(case.get("profile_id"), case.get("journal"))
    # 与 TS 的 runSpec 同一份输出：干涉检测（V-011）折进来（Python 生产调用方默认不折，见 `preflight.run`）
    issues = preflight.run(case["spec"], profile, with_interference=True)
    # 比**判据**与**可翻译描述符**（message = key + params，issue #30：MCP 画布
    # 按 locale 渲染靠它），不比成文措辞：措辞是界面的事，两侧措辞各自演进。
    return [
        {
            "id": i["id"],
            "severity": i["severity"],
            "message": i["message"],
            "object_ids": i["object_ids"],
            "gids": i["gids"],
            "detail": i["detail"],
        }
        for i in issues
    ]


def build() -> dict:
    return {
        "comment": "预检向量：pytest（engine/preflight.py）与 vitest"
        "（web/src/lib/preflight.ts）各跑一遍同一份输入。"
        "比 id/severity/message(key+params)/object_ids/gids/detail"
        " —— 成文措辞归界面。"
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
        print(
            f"{OUT} 与当前 Python 实现不一致（--write 重新生成后人工过一遍 diff）", file=sys.stderr
        )
        return 1
    print(f"{OUT} 与 Python 实现一致（{len(fresh['cases'])} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
