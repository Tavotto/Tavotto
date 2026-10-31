"""0.19.0 Windows 验收（QA-WIN-14 / 18 / 20 / 21 / 22 / 28）六条缺陷的真窗口复测。

缺陷原文与现场步骤：Codex 验收包 `tavotto-0.19-acceptance/缺陷记录.md`（QA-WIN-xx 各一节）。修复各自
在 #1545 / #1546 / #1547 / #1549 / #1551。这里不重述修法，只钉**用户能看见的判据**——被测动作都走真实界面
（CDP 进渲染进程的点、拖、键入、快捷键，没有全局输入），读事实可以读页面 / 磁盘 / 导出文件：

* QA-WIN-28：画布内就地编辑文字后点空白 → 文字是新值（对象文字与落盘都是）；
* QA-WIN-14：改完文字再画一根箭头，一次 Ctrl+Z 只撤箭头；再一次才撤文字（画布内与属性栏两个入口各一条）；
* QA-WIN-18：撞名确认出来后改文件名 → 确认作废，被改成的那个既有文件字节不变；
* QA-WIN-20：单图（原图口径）导出带上画布上叠加的文字标注；PNG 尺寸不变；
* QA-WIN-22：另一画布上已有同 stem 的重建图时，在当前画布重建 PNG → 不跳画布，当前画布多出新面板；
* QA-WIN-21：移动图例、撤销、保存、关窗重开 → 冷画布与导出预览是撤销后的位置。

每条缺陷一条（或两条）用例，用例名带缺陷号。不降断言、不 skip：产品没修好就让它红。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from _canvas import autosave_dir, object_ids, place_figure, rect
from _webdriver import KEY_CONTROL, KEY_ENTER, KEY_ESCAPE

KEY_BACKSPACE = "\ue003"
KEY_END = "\ue010"
KEY_ARROW_RIGHT = "\ue014"

TEXT_EDITOR = '[data-testid="canvas-text-editor"] .ProseMirror'
INSPECTOR_TEXT = '[data-testid="text-object-content"] .ProseMirror'
LEGEND_GID = "axes_0.legend"

#: 按稳定 `data-*` 取可见对象（别的画布标签整层 display:none，数对象只数看得见的）
VISIBLE_OBJECTS = """
return [...document.querySelectorAll('[data-canvas-stage] [data-object-id]')]
  .filter(e => e.getBoundingClientRect().width > 0)
  .map(e => e.getAttribute('data-object-id'))
"""
OBJECT_TEXT = """
const el = document.querySelector('[data-object-id="' + CSS.escape(arguments[0]) + '"]');
return el ? el.innerText.trim() : null
"""


# ---------------------------------------------------------------- 输入小工具（都只进被测 WebView）


def type_text(d, text: str) -> None:
    """逐字的 keyDown / keyUp（CDP 进渲染进程）。"""
    acts = []
    for ch in text:
        acts += [{"type": "keyDown", "value": ch}, {"type": "keyUp", "value": ch}]
    d.perform({"type": "key", "id": "kbd", "actions": acts})


def press(d, key: str, times: int = 1) -> None:
    for _ in range(times):
        d.chord(key)


def double_click_at(d, x: float, y: float) -> None:
    d.perform(
        {
            "type": "pointer",
            "id": "mouse",
            "parameters": {"pointerType": "mouse"},
            "actions": [
                {
                    "type": "pointerMove",
                    "duration": 0,
                    "origin": "viewport",
                    "x": round(x),
                    "y": round(y),
                },
                {"type": "pointerDown", "button": 0},
                {"type": "pointerUp", "button": 0},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerUp", "button": 0},
            ],
        }
    )


def replace_input_text(d, css: str, text: str) -> None:
    """点进输入框、清空（End + 退格，不依赖全选键）、打字。"""
    d.click(d.find(css))
    d.chord(KEY_END)
    cur = d.js("return document.querySelector(arguments[0]).value", css)
    press(d, KEY_BACKSPACE, len(cur) + 2)
    assert d.js("return document.querySelector(arguments[0]).value", css) == "", f"{css} 没清空"
    type_text(d, text)
    assert d.js("return document.querySelector(arguments[0]).value", css) == text


def sheet_rect(d) -> dict:
    """纸面（当前可见那张）的视口矩形。"""
    r = d.wait_js(
        "const r = [...document.querySelectorAll('[data-page-sheet]')].map(e => e.getBoundingClientRect())"
        ".filter(b => b.width > 0)[0]; return r ? {x: r.x, y: r.y, w: r.width, h: r.height} : null",
        timeout=60,
        what="画布纸面",
    )
    return r


def new_ids(d, before: set[str]) -> list[str]:
    return sorted(set(object_ids(d)) - before)


def object_text(d, oid: str) -> str | None:
    return d.js(OBJECT_TEXT, oid)


def add_text(d, x: float, y: float, typed: str) -> str:
    """T 工具 + 点一下 → 新文字进入就地编辑；打字（选区是全选，替掉默认文字）。回新对象 id。"""
    before = set(object_ids(d))
    d.chord("t")
    d.click_at(x, y)
    oid = d.wait_js(
        "const s = new Set(arguments[0]);"
        " const n = [...document.querySelectorAll('[data-object-id]')].map(e => e.getAttribute('data-object-id'))"
        "   .filter(i => !s.has(i)); return n.length === 1 ? n[0] : null",
        sorted(before),
        timeout=30,
        what="T 工具点一下之后多出一个对象",
    )
    d.wait_js(
        "const e = document.querySelector(arguments[0]); return !!e && e.contains(document.activeElement)",
        TEXT_EDITOR,
        timeout=15,
        what="新文字进入就地编辑且编辑器拿到焦点",
    )
    type_text(d, typed)
    return oid


def draw_arrow(d, x0: float, y0: float, x1: float, y1: float) -> str:
    before = set(object_ids(d))
    d.chord("a")
    d.drag(x0, y0, x1, y1)
    return d.wait_js(
        "const s = new Set(arguments[0]);"
        " const n = [...document.querySelectorAll('[data-object-id]')].map(e => e.getAttribute('data-object-id'))"
        "   .filter(i => !s.has(i)); return n.length === 1 ? n[0] : null",
        sorted(before),
        timeout=15,
        what="箭头工具拖一下之后多出一个对象",
    )


def disk_object(data_dir: Path, oid: str) -> dict | None:
    """自动保存里这个对象的记录（任意一份文档、任意一张画布）。"""
    root = autosave_dir(data_dir)
    for f in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stack = [doc]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                if cur.get("id") == oid and "type" in cur:
                    return cur
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)
    return None


def wait_disk_text(data_dir: Path, oid: str, want: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last = None
    while True:
        o = disk_object(data_dir, oid)
        last = None if o is None else o.get("text")
        if last == want:
            return
        if time.monotonic() > deadline:
            raise AssertionError(
                f"{timeout:.0f}s 内自动保存里对象 {oid} 的文字没变成 {want!r}（最后读到 {last!r}）"
            )
        time.sleep(0.3)


# ---------------------------------------------------------------- QA-WIN-28


def test_qa_win_28_inline_text_survives_click_on_blank(desktop_app, project_dir):
    """QA-WIN-28（#1546）：画布内直接编辑文字后点画布空白，输入不能丢。

    判据：新建文字、键入新值（不按 Enter）、点空白——对象文字是新值，自动保存落盘的也是新值；
    再双击已有文字、键入另一个值、点空白——同样保留（缺陷记录里「双击已有文字」那一组）。
    """
    d = desktop_app.launch("--open", str(project_dir))
    sh = sheet_rect(d)
    blank = (sh["x"] + sh["w"] * 0.85, sh["y"] + sh["h"] * 0.85)

    oid = add_text(d, sh["x"] + sh["w"] * 0.2, sh["y"] + sh["h"] * 0.25, "inlineprobeone")
    d.click_at(*blank)
    d.wait_js(
        "return !document.querySelector(arguments[0])",
        TEXT_EDITOR,
        timeout=15,
        what="点空白后就地编辑收起",
    )
    assert object_text(d, oid) == "inlineprobeone", (
        f"点空白后对象文字是 {object_text(d, oid)!r}，输入的是 'inlineprobeone'"
    )
    wait_disk_text(desktop_app.data_dir, oid, "inlineprobeone")

    # 已有文字：双击进入编辑、键入新值（进入时全选，键入即替换）、点空白
    r = rect(d, oid)
    double_click_at(d, r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)
    d.wait_js(
        "const e = document.querySelector(arguments[0]); return !!e && e.contains(document.activeElement)",
        TEXT_EDITOR,
        timeout=15,
        what="双击已有文字后进入就地编辑",
    )
    type_text(d, "inlineprobetwo")
    d.click_at(*blank)
    d.wait_js(
        "return !document.querySelector(arguments[0])", TEXT_EDITOR, timeout=15, what="第二次也收起"
    )
    assert object_text(d, oid) == "inlineprobetwo", (
        f"双击已有文字、键入、点空白后对象文字是 {object_text(d, oid)!r}，输入的是 'inlineprobetwo'"
    )
    wait_disk_text(desktop_app.data_dir, oid, "inlineprobetwo")


# ---------------------------------------------------------------- QA-WIN-14


def _undo_boundary_scenario(d, text_oid: str, probe: str) -> None:
    """文字已改成含 `probe` 的值、焦点已离开：画箭头 → Ctrl+Z → 再 Ctrl+Z，逐步核对。"""
    sh = sheet_rect(d)
    blank = (sh["x"] + sh["w"] * 0.85, sh["y"] + sh["h"] * 0.85)
    d.click_at(*blank)  # 取消选择（若文字框还开着，这一下也是「点空白」）
    arrow = draw_arrow(
        d,
        sh["x"] + sh["w"] * 0.55,
        sh["y"] + sh["h"] * 0.55,
        sh["x"] + sh["w"] * 0.8,
        sh["y"] + sh["h"] * 0.7,
    )
    assert probe in (object_text(d, text_oid) or ""), "画箭头之前文字就已经不是新值"

    d.chord(KEY_CONTROL, "z")
    d.wait_js(
        "return !document.querySelector('[data-object-id=\"' + CSS.escape(arguments[0]) + '\"]')",
        arrow,
        timeout=10,
        what="一次 Ctrl+Z 之后箭头没了",
    )
    after_first = object_text(d, text_oid)
    assert after_first is not None and probe in after_first, (
        f"一次 Ctrl+Z 把先前的文字也撤回了：现在是 {after_first!r}，应当仍含 {probe!r}"
    )

    d.chord(KEY_CONTROL, "z")
    d.wait_js(
        "const el = document.querySelector('[data-object-id=\"' + CSS.escape(arguments[0]) + '\"]');"
        " return !!el && !el.innerText.includes(arguments[1])",
        text_oid,
        probe,
        timeout=10,
        what="第二次 Ctrl+Z 才把文字改动撤回",
    )


def test_qa_win_14_inline_text_then_arrow_undo_only_removes_arrow(desktop_app, project_dir):
    """QA-WIN-14（#1546）画布内入口：文字改成 X → 画箭头 → Ctrl+Z → 箭头没了、文字仍是 X；再 Ctrl+Z 才回旧文字。"""
    d = desktop_app.launch("--open", str(project_dir))
    sh = sheet_rect(d)
    oid = add_text(d, sh["x"] + sh["w"] * 0.2, sh["y"] + sh["h"] * 0.25, "undoprobeinline")
    _undo_boundary_scenario(d, oid, "undoprobeinline")


def test_qa_win_14_inspector_text_then_arrow_undo_only_removes_arrow(desktop_app, project_dir):
    """QA-WIN-14（#1546）属性栏入口（W-195 的诊断轨迹：文字事务在画箭头时仍开着）：

    选中已有文字，在右栏文字框里键入 X，直接点画布空白，画箭头，Ctrl+Z——只撤箭头；再一次才撤文字。
    """
    d = desktop_app.launch("--open", str(project_dir))
    sh = sheet_rect(d)
    oid = add_text(d, sh["x"] + sh["w"] * 0.2, sh["y"] + sh["h"] * 0.25, "seedtext")
    d.chord(
        KEY_ENTER
    )  # 底文用 Enter 提交（缺陷记录里不丢字的对照路径），让这条只量右栏事务这一件事
    d.wait_js(
        "return !document.querySelector(arguments[0])", TEXT_EDITOR, timeout=15, what="就地编辑收起"
    )
    assert object_text(d, oid) == "seedtext"

    r = rect(d, oid)
    d.click_at(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)  # 选中它，右栏出文字框
    d.wait_js(
        "return !!document.querySelector(arguments[0])",
        INSPECTOR_TEXT,
        timeout=30,
        what="选中文字后右栏出现文字框",
    )
    d.click(d.find(INSPECTOR_TEXT))
    type_text(d, "inspectorprobe")
    d.wait_js(
        "const el = document.querySelector('[data-object-id=\"' + CSS.escape(arguments[0]) + '\"]');"
        " return !!el && el.innerText.includes('inspectorprobe')",
        oid,
        timeout=10,
        what="在右栏键入后画布上的文字跟着变",
    )
    _undo_boundary_scenario(d, oid, "inspectorprobe")


# ---------------------------------------------------------------- QA-WIN-18


EXPORT_DIALOG = '[data-dialog="export"]'


def _open_export_dialog(d) -> str:
    """Ctrl+E 打开导出对话框，回导出目录（位置行 title 就是完整绝对路径）。"""
    d.chord(KEY_CONTROL, "e")
    d.wait_js(f"return !!document.querySelector('{EXPORT_DIALOG}')", timeout=30, what="导出对话框")
    return d.wait_js(
        f"const e = document.querySelector('{EXPORT_DIALOG} [data-export-location]');"
        " return e && e.getAttribute('title') ? e.getAttribute('title') : null",
        timeout=30,
        what="导出位置行的完整路径",
    )


def _click_start(d) -> None:
    """「导出」键（预检跑完才可点）：等它可点再点。"""
    css = f"{EXPORT_DIALOG} [data-export-start]"
    d.wait_js(
        "const e = document.querySelector(arguments[0]); return !!e && !e.disabled",
        css,
        timeout=120,
        what="导出键可点（[data-export-start] 未禁用）",
    )
    d.click(d.find(css))


def _set_formats(d, wanted: set[str]) -> None:
    """只勾 `wanted` 里的格式（真点复选框）。"""
    for fmt in ("pdf", "png", "eps", "tiff", "svg"):
        css = f'{EXPORT_DIALOG} [data-export-format="{fmt}"] input'
        state = d.js(
            "const e = document.querySelector(arguments[0]); return e ? [e.checked, e.disabled] : null",
            css,
        )
        if state is None:
            continue
        checked, disabled = state
        if (fmt in wanted) != checked and not disabled:
            d.click(d.find(css))
    got = {
        f
        for f in ("pdf", "png", "eps", "tiff", "svg")
        if d.js(
            "const e = document.querySelector(arguments[0]); return !!e && e.checked",
            f'{EXPORT_DIALOG} [data-export-format="{f}"] input',
        )
    }
    assert got == wanted, f"导出格式勾选是 {got}，要的是 {wanted}"


def test_qa_win_18_conflict_confirmation_is_void_after_renaming_the_target(
    desktop_app, project_dir
):
    """QA-WIN-18（#1549）：撞名确认弹出后改了文件名，再点「覆盖」，被覆盖的是确认框没列的文件。

    判据：导出目录里先放好已存在的 A、B 两个文件（字节各不同）；对 A 开始导出 → 确认框列出 A；
    把文件名改成 B → 针对 A 的确认作废（不再有「覆盖」键）；若界面仍给出「覆盖」，按用户会做的点下去——
    无论如何 A、B 两个文件的字节都不许变（B 没被重新确认过）。再对 B 开始导出时，出来的是针对 B 的新确认。
    """
    d = desktop_app.launch("--open", str(project_dir))
    place_figure(d, "Fig2_yield.pdf")
    export_dir = Path(_open_export_dialog(d))
    export_dir.mkdir(parents=True, exist_ok=True)
    a, b = export_dir / "qa18conflicta.pdf", export_dir / "qa18conflictb.pdf"
    a.write_bytes(b"SENTINEL-A-before")
    b.write_bytes(b"SENTINEL-B-before")
    b_mtime = b.stat().st_mtime_ns
    _set_formats(d, {"pdf"})

    replace_btn = f"{EXPORT_DIALOG} [data-export-conflict-replace]"
    conflict = f"{EXPORT_DIALOG} [data-export-conflict]"
    replace_input_text(d, f"{EXPORT_DIALOG} [data-export-filename]", "qa18conflicta")
    _click_start(d)
    listed = d.wait_js(
        f"const e = document.querySelector('{conflict}'); return e ? e.innerText : null",
        timeout=120,
        what="撞名确认（[data-export-conflict]）",
    )
    assert "qa18conflicta.pdf" in listed, f"撞名确认列的不是 A：{listed!r}"

    replace_input_text(d, f"{EXPORT_DIALOG} [data-export-filename]", "qa18conflictb")
    time.sleep(1.5)  # 让「目标变了就作废确认」的响应落地；不靠它也不会漏判——下面读的是最终事实
    stale_replace = d.js("return !!document.querySelector(arguments[0])", replace_btn)
    stale_listing = d.js(
        "const e = document.querySelector(arguments[0]); return e ? e.innerText : null", conflict
    )
    if stale_replace:
        # 缺陷现场的下一步：确认框还摆着，用户点「覆盖」
        d.click(d.find(replace_btn))
        time.sleep(5)

    assert a.read_bytes() == b"SENTINEL-A-before", "A 被改动了"
    assert b.read_bytes() == b"SENTINEL-B-before" and b.stat().st_mtime_ns == b_mtime, (
        "B 在没有针对 B 的确认时被覆盖了（缺陷原样：确认框列的是 A，覆盖的是 B）"
    )
    assert not stale_replace, (
        f"文件名已改成 B，针对 A 的「覆盖」仍摆在界面上（确认框写着 {stale_listing!r}）"
    )

    # 对照：对 B 开始导出 → 新的确认，列的是 B；此时 B 依然没动
    _click_start(d)
    listed_b = d.wait_js(
        f"const e = document.querySelector('{conflict}'); return e ? e.innerText : null",
        timeout=120,
        what="对 B 开始导出后的撞名确认",
    )
    assert "qa18conflictb.pdf" in listed_b and "qa18conflicta.pdf" not in listed_b, (
        f"针对 B 的确认列的不对：{listed_b!r}"
    )
    assert b.read_bytes() == b"SENTINEL-B-before"


# ---------------------------------------------------------------- QA-WIN-20


def _export_once(d, name: str, formats: set[str]) -> Path:
    """（单图或整版）对话框走一遍：命名、勾格式、开始导出、等结果、关对话框。回导出目录。"""
    export_dir = Path(_open_export_dialog(d))
    _set_formats(d, formats)
    replace_input_text(d, f"{EXPORT_DIALOG} [data-export-filename]", name)
    _click_start(d)
    d.wait_js(
        f"return !!document.querySelector('{EXPORT_DIALOG} [data-export-result]')",
        timeout=180,
        what=f"导出 {name} 的结果区（[data-export-result]）",
    )
    d.chord(KEY_ESCAPE)
    d.wait_js(
        f"return !document.querySelector('{EXPORT_DIALOG}')", timeout=15, what="导出对话框关闭"
    )
    return export_dir


def _pdf_text_and_render(path: Path, scale: float = 1.4):
    """第一页的文字与渲染（pypdfium2：产品自己的硬依赖，Lab venv 一定有）。"""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        page = doc[0]
        text = page.get_textpage().get_text_range()
        img = page.render(scale=scale).to_pil().convert("RGB")
        return text, img.width, img.height, img.tobytes()
    finally:
        doc.close()


def test_qa_win_20_single_figure_export_carries_canvas_annotation(desktop_app, project_dir):
    """QA-WIN-20（#1551）：单图（原图口径）导出丢了画布上叠加的标注。

    判据：双击素材卡进单图编辑；先不加标注导出 PDF+PNG（基准）；再在图上加一段文字标注、导出 PDF+PNG——
    标注版 PDF 里读得出这段文字（被轮廓化成路径时退而求其次：渲染出来与基准有明显差别）、标注版 PNG 与
    基准有明显差别，且 PNG 像素尺寸与基准一致（仍是原图尺寸）。
    """
    from PIL import Image, ImageChops

    d = desktop_app.launch("--open", str(project_dir))
    card = '[data-card="Fig1_kinetics.pdf"]'
    d.wait_js(
        f"return !!document.querySelector('{card}')", timeout=60, what="素材卡 Fig1_kinetics.pdf"
    )
    r = d.rect(d.find(card))
    double_click_at(d, r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)
    d.wait_js(
        "const e = document.querySelector('[data-workspace-mode]');"
        " return !!e && e.getAttribute('data-workspace-mode') === 'fast_edit'",
        timeout=120,
        what="双击素材卡进入单图编辑（data-workspace-mode=fast_edit）",
    )
    figure = d.wait_js(
        "const ids = [...document.querySelectorAll('[data-canvas-stage] [data-object-id]')]"
        ".map(e => e.getAttribute('data-object-id')); return ids.length === 1 ? ids[0] : null",
        timeout=60,
        what="画布上恰好一张图",
    )
    time.sleep(1.0)  # 视图适配稳定

    export_dir = _export_once(d, "qa20plain", {"pdf", "png"})

    fr = rect(d, figure)
    oid = add_text(d, fr["x"] + fr["w"] * 0.3, fr["y"] + fr["h"] * 0.1, "annotqa")
    d.chord(KEY_ESCAPE)  # Esc 提交就地编辑
    d.wait_js(
        "return !document.querySelector(arguments[0])", TEXT_EDITOR, timeout=15, what="就地编辑收起"
    )
    assert object_text(d, oid) == "annotqa"
    wait_disk_text(desktop_app.data_dir, oid, "annotqa")

    _export_once(d, "qa20annot", {"pdf", "png"})

    plain_pdf, annot_pdf = export_dir / "qa20plain.pdf", export_dir / "qa20annot.pdf"
    plain_png, annot_png = export_dir / "qa20plain.png", export_dir / "qa20annot.png"
    for p in (plain_pdf, annot_pdf, plain_png, annot_png):
        assert p.is_file(), (
            f"导出文件没生成：{p}（目录里有 {sorted(x.name for x in export_dir.iterdir())}）"
        )

    # PNG：尺寸不变（仍是原图尺寸）、像素有明显差别（标注画上去了）
    with Image.open(plain_png) as a, Image.open(annot_png) as b:
        assert a.size == b.size, (
            f"带标注的 PNG 尺寸 {b.size} 与不带标注的 {a.size} 不同（原图口径被改了）"
        )
        diff = ImageChops.difference(a.convert("RGBA"), b.convert("RGBA")).convert("L")
        changed = diff.point(lambda v: 255 if v > 40 else 0).histogram()[255]
    assert changed > 100, (
        f"带标注的 PNG 与不带标注的几乎一样（只有 {changed} 个像素明显不同）：标注没进导出"
    )

    # PDF：读得出文字；轮廓化时退到渲染比对
    text0, w0, h0, pix0 = _pdf_text_and_render(plain_pdf)
    text1, w1, h1, pix1 = _pdf_text_and_render(annot_pdf)
    assert "annotqa" not in text0, "基准 PDF 里不该有标注文字"
    if "annotqa" not in text1:
        assert (w0, h0) == (w1, h1), f"带标注 PDF 的渲染尺寸 {(w1, h1)} 与基准 {(w0, h0)} 不同"
        differing = sum(1 for i in range(0, len(pix0), 3) if abs(pix0[i] - pix1[i]) > 40)
        assert differing > 100, (
            "带标注的 PDF 里既读不出标注文字，渲染出来也与不带标注的几乎一样：标注没进导出"
        )


# ---------------------------------------------------------------- 脚本项目（QA-WIN-21 / 22）

LEGEND_SCRIPT = """\
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(4, 3))
ax.plot([0, 1, 2, 3], [0, 1, 4, 9], label="alpha")
ax.plot([0, 1, 2, 3], [0, 2, 3, 5], label="beta")
ax.legend(loc="upper left")
ax.set_title("Legend QA")
fig.savefig("fig_out.pdf")
"""

REBUILD_SCRIPT = """\
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1, 2, 3], [0.2, 0.8, 0.4, 0.9], label="Response")
    ax.legend()
    ax.set_title("Guarded source", fontsize=12)
    ax.set_xlabel("Time")
    ax.set_ylabel("Response")
    fig.subplots_adjust(left=0.18, bottom=0.20, right=0.92, top=0.82)
    fig.savefig("same.pdf", bbox_inches="tight", pad_inches=0.02)
    fig.savefig("same.png", bbox_inches=None, dpi=72, transparent=True)
    plt.close(fig)
"""


@pytest.fixture
def legend_project(tmp_path) -> Path:
    """只有一个会画图例的脚本（「打开并运行」之前没有图、没有注册表）。"""
    proj = tmp_path / "图例 项目"
    proj.mkdir()
    (proj / "fig.py").write_text(LEGEND_SCRIPT, encoding="utf-8")
    return proj


@pytest.fixture
def png_rebuild_project(tmp_path) -> Path:
    """脚本 + 注册表（stem=same）+ 一张**不是**脚本画出来的合成 PNG（来源对不上 → 给「重建为可编辑图」）。"""
    from PIL import Image

    proj = tmp_path / "重建 项目"
    proj.mkdir()
    (proj / "figure.py").write_text(REBUILD_SCRIPT, encoding="utf-8")
    (proj / "tavotto_registry.json").write_text(
        json.dumps(
            {"scripts": {"figure.py": {"entry": "main", "cost": "light", "stems": ["same"]}}},
            indent=1,
        ),
        encoding="utf-8",
    )
    img = Image.new("RGB", (288, 216), (255, 255, 255))
    px = img.load()
    for x in range(288):
        for y in range(216):
            px[x, y] = (x % 256, (y * 3) % 256, 120)
    img.save(proj / "same.png")
    return proj


# ---------------------------------------------------------------- QA-WIN-22

CANVAS_TABS = """
return [...document.querySelectorAll('[data-canvas-tab]')].map(e => ({
  id: e.getAttribute('data-canvas-tab'), selected: e.getAttribute('aria-selected') === 'true'}))
"""


def _click_rail(d, name: str) -> None:
    d.click(d.find(f'[data-rail="{name}"]'))


def _saved_canvases(data_dir: Path) -> list[dict]:
    """自动保存里最近那份文档的各张画布（含 id 与对象表）。"""
    root = autosave_dir(data_dir)
    files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime) if root.is_dir() else []
    for f in reversed(files):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc.get("canvases"), list) and doc["canvases"]:
            return doc["canvases"]
    return []


def test_qa_win_22_png_rebuild_stays_on_the_current_canvas(desktop_app, png_rebuild_project):
    """QA-WIN-22（#1545）：PNG「重建为可编辑图」跳到另一画布上同 stem 的旧面板，却提示重画成功。

    场景（缺陷记录 W-122）：画布 1 上已有同 stem 的运行时图（脚本「打开并运行」得到）；新开画布 2，加入同 stem 的
    PNG，点「改图里的内容」→ 安全提示里的「重建为可编辑图」（走脚本重画，不调 AI 模型）。
    判据：重建完成后当前画布仍是画布 2、画布 2 上出现新面板（连同原 PNG 共两个）；画布 1 仍是原来那一个面板。
    """
    d = desktop_app.launch("--open", str(png_rebuild_project))
    # 画布 1：打开并运行脚本，得到运行时图（asset id = runtime:figure.py#same）
    d.wait_js(
        "return !!document.querySelector('[data-script-run=\"figure.py\"]')",
        timeout=60,
        what="素材库里 figure.py 的运行键",
    )
    d.click(d.find('[data-script-run="figure.py"]'))
    d.wait_js(
        "return !!document.querySelector('[data-element-svg] svg')",
        timeout=240,
        what="脚本运行完自动进入图内编辑",
    )
    runtime_panel = d.wait_js(
        "const ids = [...document.querySelectorAll('[data-canvas-stage] [data-object-id]')]"
        ".map(e => e.getAttribute('data-object-id')); return ids.length === 1 ? ids[0] : null",
        timeout=60,
        what="画布 1 上恰好一个运行时面板",
    )
    # 画布 2：从左抽屉的「画布」页新建（单图编辑时页签条不画），把同 stem 的 PNG 加进来
    _click_rail(d, "canvases")
    d.click(d.find("[data-canvas-new]"))
    c2 = d.wait_js(
        "const t = [...document.querySelectorAll('[data-canvas-tab]')];"
        " const sel = t.find(e => e.getAttribute('aria-selected') === 'true');"
        " return t.length === 2 && sel ? sel.getAttribute('data-canvas-tab') : null",
        timeout=30,
        what="新建画布 2 并切到它（两个画布页签、第二个选中）",
    )
    if not d.js("return !!document.querySelector('[data-card=\"same.png\"]')"):
        _click_rail(d, "assets")
    png_panel = place_figure(d, "same.png")
    r = rect(d, png_panel)
    d.click_at(r["x"] + r["w"] / 2, r["y"] + r["h"] / 2)
    d.chord(KEY_ENTER)  # Enter = 进入图内编辑
    d.wait_js(
        "return !!document.querySelector('[data-toast-action]')",
        timeout=120,
        what="PNG 对不上来源时的提示及其「重建为可编辑图」键（[data-toast-action]）",
    )
    d.click(d.find("[data-toast-action]"))
    # 重建结束的信号：成功提示；没有它也往下走——后面的断言会说清落在了哪（缺陷形态是「跳去别的画布」）
    try:
        d.wait_js(
            "const s = document.querySelector('[data-status-live]');"
            " return !!s && s.getAttribute('data-status-key') === 'artifact.rebuilt'",
            timeout=150,
            what="重建完成的提示（data-status-key=artifact.rebuilt）",
        )
    except TimeoutError:
        pass
    time.sleep(2.0)
    status = d.js(
        "const s = document.querySelector('[data-status-live]'); return s && s.getAttribute('data-status-key')"
    )

    selected = d.js(CANVAS_TABS)
    sel = [t["id"] for t in selected if t["selected"]]
    assert sel == [c2], (
        f"重建之后当前画布是 {sel}，应当仍是发起重建的画布 2（{c2}）——跳去了别的画布"
    )
    visible = d.js(VISIBLE_OBJECTS)
    assert len(visible) == 2 and png_panel in visible, (
        f"画布 2 上看得见的对象是 {visible}：应有原 PNG {png_panel} 与一个新面板"
    )
    assert runtime_panel not in visible, "画布 2 上出现的是画布 1 的旧面板（复用了同 stem 的旧图）"
    deadline = time.monotonic() + 30
    while True:
        canvases = {c["id"]: c for c in _saved_canvases(desktop_app.data_dir)}
        n2 = len(canvases.get(c2, {}).get("objects", [])) if c2 in canvases else None
        if n2 == 2 or time.monotonic() > deadline:
            break
        time.sleep(0.5)
    assert n2 == 2, f"落盘的画布 2 有 {n2} 个对象（应 2）：{list(canvases)}"
    others = [c for cid, c in canvases.items() if cid != c2]
    assert len(others) == 1, f"落盘的画布应恰有画布 2 之外的一张，实际 {list(canvases)}"
    assert [o["id"] for o in others[0]["objects"]] == [runtime_panel], (
        f"画布 1 的对象不再是原来那一个运行时面板：{[o['id'] for o in others[0]['objects']]}"
    )
    assert status == "artifact.rebuilt", (
        f"没有出现「已重画出可编辑图」的提示（最后的状态键是 {status!r}）"
    )


# ---------------------------------------------------------------- QA-WIN-21

#: 取这张 SVG 里图例相对整图的位置 [x, y]（0..1）。SVG 来自 <img> 的 blob / 对话框里内联的 <svg>
LEGEND_REL_OF_SVG_TEXT = """
const done = arguments[arguments.length - 1];
(async () => {
  const [selector, gid] = [arguments[0], arguments[1]];
  const img = document.querySelector(selector + ' img');
  let host, cleanup = () => {};
  if (img && img.src) {
    if (!img.complete || !img.naturalWidth) return done(null);
    const text = await (await fetch(img.src)).text();
    if (!text.includes('<svg')) return done({notSvg: true});
    host = document.createElement('div');
    host.style.cssText = 'position:fixed;left:-9999px;top:0;width:800px;height:600px;visibility:hidden';
    host.innerHTML = text;
    document.body.appendChild(host);
    cleanup = () => host.remove();
    host = host.querySelector('svg');
  } else {
    host = document.querySelector(selector + ' svg');
  }
  if (!host) return done(null);
  const n = host.querySelector('[id="' + gid + '"]');
  const h = host.getBoundingClientRect();
  const out = n && h.width > 0
    ? (r => ({x: (r.x - h.x) / h.width, y: (r.y - h.y) / h.height}))(n.getBoundingClientRect())
    : {missing: true};
  cleanup();
  done(out);
})().catch(e => done({error: String(e)}));
"""
LEGEND_REL_IN_EDIT = """
const host = document.querySelector('[data-element-svg] > svg');
const n = host && host.querySelector('[id="' + arguments[0] + '"]');
if (!n) return null;
const h = host.getBoundingClientRect(), r = n.getBoundingClientRect();
return {x: (r.x - h.x) / h.width, y: (r.y - h.y) / h.height}
"""


def _poll_async(d, script: str, *args, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    last = None
    while True:
        last = d.js_async(script, *args)
        if isinstance(last, dict) and "x" in last:
            return last
        if time.monotonic() > deadline:
            raise TimeoutError(f"{timeout:.0f}s 内没等到 {what}；最后一次 {last!r}")
        time.sleep(0.5)


def _legend_undo_save_restart(desktop_app, project: Path) -> dict:
    """脚本运行 → 选中图例、方向键右移 → 撤销 → 退出图内编辑 → 存进项目 → 关窗 → 重开。

    回 {"base": 撤销后（= 移动前）图例在编辑图里的相对位置, "moved": 移动后的位置}；重开后的 `d` 在 desktop_app.driver。
    """
    d = desktop_app.launch("--open", str(project))
    d.wait_js(
        "return !!document.querySelector('[data-script-run=\"fig.py\"]')",
        timeout=60,
        what="素材库里 fig.py 的运行键",
    )
    d.click(d.find('[data-script-run="fig.py"]'))
    d.wait_js(
        f"return !!document.querySelector('[data-element-svg] [id=\"{LEGEND_GID}\"]')",
        timeout=240,
        what="脚本运行完进入图内编辑、图例出现",
    )
    time.sleep(2.0)
    base = d.js(LEGEND_REL_IN_EDIT, LEGEND_GID)
    box = d.js(
        "const r = document.querySelector('[data-element-svg] [id=\"' + arguments[0] + '\"]').getBoundingClientRect();"
        " return {x: r.x + r.width / 2, y: r.y + r.height / 2}",
        LEGEND_GID,
    )
    d.click_at(box["x"], box["y"])
    time.sleep(0.5)
    press(d, KEY_ARROW_RIGHT, 20)  # 一键 0.5 mm：这一段把图例移开约 10 mm
    moved = d.wait_js(
        "const host = document.querySelector('[data-element-svg] > svg');"
        " const n = host && host.querySelector('[id=\"' + arguments[0] + '\"]');"
        " if (!n) return null; const h = host.getBoundingClientRect(), r = n.getBoundingClientRect();"
        " const x = (r.x - h.x) / h.width; return x - arguments[1] > 0.05 ? {x, y: (r.y - h.y) / h.height} : null",
        LEGEND_GID,
        base["x"],
        timeout=90,
        what="方向键之后图例向右移开（重渲染落地）",
    )
    time.sleep(
        6.0
    )  # 缺陷现场在移动与撤销之间等了 10 秒：让这一段方向键的收尾（权威重渲染换上画布）走完
    moved = d.js(LEGEND_REL_IN_EDIT, LEGEND_GID)
    d.chord(KEY_CONTROL, "z")
    d.wait_js(
        "const host = document.querySelector('[data-element-svg] > svg');"
        " const n = host && host.querySelector('[id=\"' + arguments[0] + '\"]');"
        " if (!n) return null; const h = host.getBoundingClientRect(), r = n.getBoundingClientRect();"
        " return Math.abs((r.x - h.x) / h.width - arguments[1]) < 0.005",
        LEGEND_GID,
        base["x"],
        timeout=90,
        what="Ctrl+Z 之后图例回到移动前的位置（热态）",
    )
    time.sleep(2.0)  # 撤销之后的一段静默：缺陷现场等了 10 秒；这里足够让任何后台落盘走完
    d.chord(KEY_ESCAPE)
    d.chord(KEY_ESCAPE)
    time.sleep(0.5)
    d.chord(KEY_CONTROL, "s")
    d.wait_js(
        "return !!document.querySelector('[data-layout-save]')", timeout=60, what="存进项目对话框"
    )
    d.click(d.find("[data-layout-save]"))
    d.wait_js(
        "return !document.querySelector('[data-layout-save]')",
        timeout=60,
        what="存进项目对话框关闭",
    )
    time.sleep(2.0)

    code = desktop_app.close_window()
    assert code == 0, f"WM_CLOSE 之后壳的退出码是 {code}"
    desktop_app.launch(expect="project")
    return {"base": base, "moved": moved}


def _assert_same_position(got: dict, want: dict, moved: dict, where: str) -> None:
    assert "x" in got, f"{where}：读不到图例的位置 {got!r}"
    near = abs(got["x"] - want["x"]) < 0.01 and abs(got["y"] - want["y"]) < 0.01
    assert near, (
        f"{where}：图例在 ({got['x']:.4f}, {got['y']:.4f})，撤销后应在 ({want['x']:.4f}, {want['y']:.4f})；"
        f"撤销前被移到了 ({moved['x']:.4f}, {moved['y']:.4f})"
        + ("——显示的是撤销前的位置" if abs(got["x"] - moved["x"]) < 0.01 else "")
    )


def test_qa_win_21_cold_canvas_shows_the_state_after_undo(desktop_app, legend_project):
    """QA-WIN-21（#1547）冷画布：移动图例 → 撤销 → 保存 → 关窗重开，画布上的图是撤销后的图例位置。

    判据：重开后不进图内编辑，直接读画布面板里显示的预览（`<img>` 的 SVG）里图例的位置，与移动前一致。
    """
    facts = _legend_undo_save_restart(desktop_app, legend_project)
    d = desktop_app.driver
    d.wait_js(
        "return !!document.querySelector('[data-canvas-stage] [data-object-id] img')",
        timeout=120,
        what="重开后画布上的运行时面板",
    )
    got = _poll_async(
        d,
        LEGEND_REL_OF_SVG_TEXT,
        "[data-canvas-stage] [data-object-id]",
        LEGEND_GID,
        timeout=120,
        what="冷画布预览里的图例位置",
    )
    _assert_same_position(got, facts["base"], facts["moved"], "重开后的冷画布")


def test_qa_win_21_export_preview_shows_the_state_after_undo(desktop_app, legend_project):
    """QA-WIN-21（#1547）导出预览：同上的流程，重开后打开导出对话框，左栏预览里的图例是撤销后的位置。"""
    facts = _legend_undo_save_restart(desktop_app, legend_project)
    d = desktop_app.driver
    d.wait_js(
        "return !!document.querySelector('[data-canvas-stage] [data-object-id]')",
        timeout=120,
        what="重开后画布上的运行时面板",
    )
    time.sleep(3.0)  # 冷画布先取到自己的预览
    d.chord(KEY_CONTROL, "e")
    d.wait_js(f"return !!document.querySelector('{EXPORT_DIALOG}')", timeout=30, what="导出对话框")
    got = _poll_async(
        d,
        LEGEND_REL_OF_SVG_TEXT,
        "[data-export-preview-stage]",
        LEGEND_GID,
        timeout=120,
        what="导出预览里的图例位置",
    )
    _assert_same_position(got, facts["base"], facts["moved"], "重开后的导出预览")
