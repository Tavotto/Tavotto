"""导出里的「修改后的 .py」（ADR 0171）：生成器单测与走真 `/api/export` 的用例。

判据的主语分三段：

* 生成器（纯字节）：块外一个字节不动，编码 / 换行 / BOM / shebang 都不坏；
* 导出链路（真 worker + 真 `/api/export`）：原脚本逐字节不变，项目里不冒新文件，验证过不了就不交付，
  图片格式不受牵连；
* **用户拿到的那个文件**：不带 Tavotto 的纯 matplotlib 子进程跑它，画出来的 PDF 与导出的 PDF 逐像素一致。

整图扫描与覆盖断言在 `test_script_adjust_sweep.py`，扫描放不下的键按组单独扫在 `test_script_adjust_heldout_*.py`；
共用的夹具与辅助在 `support/scriptadjust_harness.py`。
"""

from __future__ import annotations

import ast
import codecs
import hashlib
import io
import json
import os
import subprocess
from pathlib import Path

import pymupdf
import pytest

from support.scriptadjust_harness import (
    WORKER_PY,
    _library_worker,
    real_worker,
)
from tavotto.engine import (
    exportjob,
    exportreq,
    exportroute,
    figcapture,
    pool,
    project_watch,
    scriptadjust,
)

pytest_plugins = ("support.scriptadjust_harness",)


MAN = {
    "elements": [
        {"gid": "axes_0.title", "role": "title", "label": "标题", "editable": []},
        {"gid": "axes_0", "role": "axes", "label": "子图 1", "editable": []},
    ]
}


COLOR = {"gid": "axes_0.title", "prop": "color", "value": "#123456"}


def _build(src: bytes, patches=(COLOR,)) -> scriptadjust.Adjusted:
    return scriptadjust.build(src, "Fig1", list(patches), MAN, version="9.9")


def _without_block(data: bytes, encoding: str, newline: str) -> bytes:
    text = data.decode(encoding)
    start = text.index(scriptadjust.BEGIN)
    tail = scriptadjust.END + newline
    end = text.index(tail) + len(tail)
    two = newline * 2
    if text[start - len(two) : start] == two:
        start -= len(two)
    if text[end : end + len(two)] == two:
        end += len(two)
    return (text[:start] + text[end:]).encode(encoding)


# ---------------------------------------------------------------------- 字节
@pytest.mark.parametrize(
    "src, encoding, newline",
    [
        (b"import os\nx = 1\n", "utf-8", "\n"),
        (b"import os\r\nx = 1\r\n", "utf-8", "\r\n"),
        (codecs.BOM_UTF8 + b"import os\r\nx = 1\r\n", "utf-8-sig", "\r\n"),
        ("# -*- coding: gbk -*-\nimport os\n# 中文\nx = 1\n".encode("gbk"), "gbk", "\n"),
        (b"import os\nif 1:\n\tx = 1\n", "utf-8", "\n"),
        (b'"""doc"""\nfrom __future__ import annotations\nimport os\nx = 1\n', "utf-8", "\n"),
    ],
    ids=["lf", "crlf", "bom-crlf", "gbk", "tabs", "future"],
)
def test_nothing_outside_the_block_changes(src, encoding, newline):
    out = _build(src)
    assert out.written == 1
    ast.parse(out.data.decode(encoding))
    assert _without_block(out.data, encoding, newline) == src


def test_mixed_line_endings_survive_byte_for_byte():
    src = b"import os\r\nimport sys\nx = 1\r\ny = 2\n"
    assert _without_block(_build(src).data, "utf-8", "\r\n") == src  # 主导换行 = CRLF


def test_last_line_without_a_newline():
    src = b"import os"
    out = _build(src)
    ast.parse(out.data.decode())
    assert out.data.startswith(src + b"\n")


@pytest.mark.parametrize(
    "head",
    [b"#!/usr/bin/env python\n", b"# -*- coding: utf-8 -*-\n", b"#!/bin/py\n# coding: utf-8\n"],
)
def test_block_stays_below_shebang_and_coding_line(head):
    """它们只在头两行才算数：块插到文件头，声明就失效了（GBK 脚本当场读不出来）。"""
    out = _build(head + b"x = 1\n")
    assert out.data.startswith(head)
    assert out.data[len(head) :].lstrip(b"\n").startswith(scriptadjust.BEGIN.encode())


def test_block_goes_after_the_leading_imports():
    src = b"import os\nimport sys\n\nx = 1\n"
    data = _build(src).data
    assert (
        data.index(b"import sys") < data.index(scriptadjust.BEGIN.encode()) < data.index(b"x = 1")
    )


def test_value_the_file_encoding_cannot_hold_is_escaped():
    src = "# -*- coding: latin-1 -*-\nimport os\n".encode("latin-1")
    out = _build(src, [{"gid": "axes_0.title", "prop": "text", "value": "温度"}])
    assert b"\\u6e29\\u5ea6" in out.data
    ast.parse(out.data.decode("latin-1"))


def test_readable_value_stays_readable():
    out = _build(b"import os\n", [{"gid": "axes_0.title", "prop": "text", "value": "温度"}])
    assert "温度" in out.data.decode()


def test_second_export_keeps_the_first_block():
    """用户把导出的脚本换回去继续改：之前的调整不能丢，新的一段排在它后面、编号不撞。"""
    first = _build(b"import os\nx = 1\n").data
    second = _build(first, [{"gid": "axes_0", "prop": "facecolor", "value": "#eeeeee"}]).data
    text = second.decode()
    assert text.count(scriptadjust.BEGIN) == 2
    assert text.index("axes_0.title.color") < text.index("axes_0.facecolor")
    assert "}, 0, {})" in text and "}, 1, {})" in text


def test_frame_comes_from_the_manifest_and_only_when_active():
    frame = {"active": True, "savefig_in": [0.1, 0.2, 3.1, 2.2]}
    assert scriptadjust._frame_inches({"frame": frame}) == (0.1, 0.2, 3.1, 2.2)
    assert scriptadjust._frame_inches({}) is None
    assert scriptadjust._frame_inches({"frame": {**frame, "active": False}}) is None
    assert scriptadjust._frame_inches({"frame": {"active": True}}) is None


def _font_line(value, gid="axes_0.title", tail=("DejaVu Sans", "Noto Sans CJK SC")) -> str:
    man = {**MAN, "font_fallback_tail": list(tail)}
    man["elements"] = [
        *MAN["elements"],
        {"gid": "axes_0.xticks", "role": "ticks", "label": "x 刻度", "editable": []},
    ]
    p = {"gid": gid, "prop": "fontfamily", "value": value}
    out = scriptadjust.build(b"import os\n", "Fig1", [p], man, version="9.9")
    assert out.written == 1
    return out.data.decode()


def test_font_chain_carries_the_machine_fallback_tail():
    """只设一个名字时缺的字形画成方框：族链要和引擎一样带上本机的回退尾巴。"""
    text = _font_line("Times New Roman")
    assert "['Times New Roman', 'DejaVu Sans', 'Noto Sans CJK SC'], 'Times New Roman')" in text


def test_font_already_in_the_tail_is_not_repeated():
    assert "['DejaVu Sans', 'Noto Sans CJK SC'], 'DejaVu Sans')" in _font_line("DejaVu Sans")


def test_font_list_value_uses_its_first_family():
    assert "'Arial')" in _font_line(["Arial", "Helvetica"])


def test_font_selection_keeps_primary_and_fallback():
    text = _font_line({"primary": "Arial", "fallback": "SimSun"})
    assert "_font_pick(_ax(fig, 0).title, 'Arial', 'SimSun')" in text


def test_tick_fonts_go_through_tick_params():
    text = _font_line("Arial", gid="axes_0.xticks")
    assert "_tick_font(_TickSet(_ax(fig, 0), 'x'), ['Arial'" in text
    assert "labelfontfamily=" in text


def test_block_only_carries_the_helpers_it_uses():
    out = _build(b"import os\n").data.decode()
    assert "_legend_rebuild" not in out and "_title_to" not in out
    assert "def _install" in out


def test_block_never_imports_tavotto():
    tree = ast.parse(_build(b"import os\n").data.decode())
    roots = {
        (n.names[0].name if isinstance(n, ast.Import) else (n.module or "")).split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
    }
    assert "tavotto" not in roots


def test_block_runtime_is_ascii():
    """任何 PEP 263 编码都放得下；非 ASCII 只可能来自用户自己的值。"""
    assert scriptadjust._RUNTIME.isascii()


def test_unreadable_script_is_refused_not_guessed():
    with pytest.raises(scriptadjust.ScriptAdjustError) as e:
        _build(b"# coding: utf-8\nx = '\xff'\n")
    assert e.value.code == "script_export_unreadable"
    with pytest.raises(scriptadjust.ScriptAdjustError):
        _build(b"def (:\n", [])


def test_duplicate_patch_keeps_the_last_value():
    """与 `effectiveOverride` 同一口径：重复 (gid, prop) 取最后一条。"""
    out = _build(b"import os\n", [COLOR, {**COLOR, "value": "#abcdef"}])
    assert out.written == 1
    text = out.data.decode()
    assert "#abcdef" in text and "#123456" not in text


def test_unwritable_override_is_reported_not_guessed():
    p = {"gid": "axes_0", "prop": "aspect", "value": "equal"}
    out = _build(b"import os\n", [COLOR, p])
    assert out.written == 1
    assert out.residual == [p]
    assert out.skipped == [{"gid": "axes_0", "prop": "aspect", "label": "子图 1"}]


def test_nothing_writable_returns_the_original_bytes():
    src = b"import os\n"
    p = {"gid": "axes_0", "prop": "aspect", "value": "equal"}
    assert _build(src, [p]).data == src


def test_narrow_entry_stays_with_its_residual_broadcast():
    """留作 override 的广播条目重放时排在窄条目前面，窄条目写进脚本就被它盖掉（ADR 0094 §五.3）。"""
    man = {
        "elements": [
            {"gid": "axes_0.legend", "role": "legend", "editable": []},
            {"gid": "axes_0.legend.texts_0", "role": "legend_text", "editable": []},
        ]
    }
    broad = {"gid": "axes_0.legend", "prop": "fontsize", "value": 7}  # 缺整份图例参数 → 写不了
    narrow = {"gid": "axes_0.legend.texts_0", "prop": "fontsize", "value": 9}
    man["elements"][0]["editable"] = None  # 让图例字号这一条走进 residual
    out = scriptadjust.build(b"import os\n", "Fig1", [broad, narrow], man, version="9.9")
    assert out.written == 0
    assert {p["gid"] for p in out.residual} == {broad["gid"], narrow["gid"]}


_AX0 = "axes_0"


_FRAME_MAN = {"elements": [{"gid": _AX0, "role": "axes", "editable": []}]}


@pytest.mark.parametrize("broadcast_first", [True, False], ids=["broadcast-first", "narrow-first"])
def test_spine_broadcast_is_written_before_its_narrow_entries(broadcast_first):
    """「全部」档在列表里排哪儿都一样：脚本里它先落、四边各自的后落（窄的盖广播，与引擎同序）。"""
    broad = {"gid": _AX0, "prop": "spine_color", "value": "#112233"}
    narrow = {"gid": _AX0, "prop": "spine_top_color", "value": "#aa0000"}
    wide = {"gid": _AX0, "prop": "spine_linewidth", "value": 2.0}
    thin = {"gid": _AX0, "prop": "spine_left_linewidth", "value": 0.5}
    edits = [broad, narrow, wide, thin] if broadcast_first else [thin, narrow, wide, broad]
    out = scriptadjust.build(b"import os\n", "Fig1", edits, _FRAME_MAN, version="9.9")
    assert out.written == 4 and not out.residual
    text = out.data.decode()
    assert text.index("#112233") < text.index("#aa0000")
    assert text.index("set_linewidth(2.0)") < text.index("set_linewidth(0.5)")


def test_narrow_spine_entries_stay_with_a_residual_broadcast(monkeypatch):
    """广播写不进脚本留作重放时，窄条目也一起留下（重放的广播会盖掉写进脚本的窄条目）；别的边框键不受牵连。"""
    monkeypatch.delitem(scriptadjust.WRITES, ("axes", "spine_color"))
    broad = {"gid": _AX0, "prop": "spine_color", "value": "#112233"}
    narrow = {"gid": _AX0, "prop": "spine_top_color", "value": "#aa0000"}
    other = {"gid": _AX0, "prop": "spine_top_linewidth", "value": 2.0}
    out = scriptadjust.build(
        b"import os\n", "Fig1", [narrow, other, broad], _FRAME_MAN, version="9.9"
    )
    assert out.written == 1
    assert {p["prop"] for p in out.residual} == {"spine_color", "spine_top_color"}


def _cb_man(*, mappable="axes_0.images_0", mapped_props=("cmap", "vmin", "vmax"), **cb_over):
    """图像 + 一条色条的 manifest（只留 `build` 读的那几个字段）。"""
    cb = {
        "gid": "axes_1.colorbar",
        "role": "colorbar",
        "editable": [{"prop": "orientation", "value": "vertical"}],
        **({"mappable_gid": mappable} if mappable else {}),
        **cb_over,
    }
    im = {
        "gid": "axes_0.images_0",
        "role": "image",
        "editable": [{"prop": p, "value": 0} for p in mapped_props],
    }
    return {"elements": [im, cb]}


def _cb_patch(prop, value, gid="axes_1.colorbar"):
    return {"gid": gid, "prop": prop, "value": value}


def _cb_build(man, patches):
    return scriptadjust.build(b"import os\n", "Fig1", patches, man, version="9.9")


def test_colorbar_orientation_and_extend_are_never_written():
    """只能靠私有 API 的两个键永远留作重放（ADR 0094 Q10）。"""
    edits = [_cb_patch("orientation", "horizontal"), _cb_patch("extend", "both")]
    out = _cb_build(_cb_man(), edits)
    assert out.written == 0 and out.residual == edits


def test_colorbar_without_a_reachable_mappable_keeps_its_color_keys_for_replay():
    """独立 `ScalarMappable` 的色条（没有 `mappable_gid`）：公开 API 够不着 mappable，cmap / vmin / vmax
    留作重放；标签、刻度、轮廓这些落在色条轴上的照写。"""
    man = _cb_man(mappable=None)
    color = [_cb_patch("cmap", "viridis"), _cb_patch("vmin", 0.2), _cb_patch("vmax", 0.8)]
    axis = [
        _cb_patch("label", "x"),
        _cb_patch("tick_color", "#112233"),
        _cb_patch("outline_width", 2),
    ]
    out = _cb_build(man, color + axis)
    assert out.residual == color and out.written == 3


def test_colorbar_of_a_decoded_raster_keeps_its_color_keys_for_replay():
    """`mappable_gid` 指的是反解出来的 RGB 位图（它自己没有 cmap / vmin / vmax 字段）：色条上色的其实是
    另一个对象，写在位图上什么也不会发生。"""
    man = _cb_man(mapped_props=())
    edits = [_cb_patch("cmap", "viridis"), _cb_patch("vmin", 0.2)]
    out = _cb_build(man, edits)
    assert out.written == 0 and out.residual == edits


def test_colorbar_cmap_with_scale_siblings_stays_with_the_image_cmap_for_replay():
    """色阶兄弟的 cmap 引擎逐个换，写不出来；这条留作重放时，同一个 mappable 的 cmap 一起留下
    （重放永远排在脚本之后，会盖掉写进脚本的那条）。vmin / vmax 走共用的 norm，照写。"""
    man = _cb_man(scale_gids=["axes_0.images_1"])
    cb_cmap, im_cmap = _cb_patch("cmap", "viridis"), _cb_patch("cmap", "cividis", "axes_0.images_0")
    vmin = _cb_patch("vmin", 0.2)
    out = _cb_build(man, [im_cmap, cb_cmap, vmin])
    assert {(p["gid"], p["prop"]) for p in out.residual} == {
        ("axes_1.colorbar", "cmap"),
        ("axes_0.images_0", "cmap"),
    }
    assert out.written == 1


def test_cmap_group_with_a_colorbar_of_another_mappable_stays_for_replay():
    """`cmap_group_gids` 里有别的 mappable 的色条（独立 `ScalarMappable` 与登记图元共用 norm）：引擎一并换，
    脚本只换得了一个对象，色条与图像两侧的 cmap 都留给重放；同一个 mappable 的两条色条不算。"""
    man = _cb_man(cmap_group_gids=["axes_0.images_0", "axes_2.colorbar"])
    man["elements"][0]["cmap_group_gids"] = ["axes_1.colorbar", "axes_2.colorbar"]
    man["elements"].append(
        {
            "gid": "axes_2.colorbar",
            "role": "colorbar",
            "editable": [],
        }  # 独立 mappable：没有 mappable_gid
    )
    edits = [_cb_patch("cmap", "viridis"), _cb_patch("cmap", "cividis", "axes_0.images_0")]
    out = _cb_build(man, edits)
    assert out.written == 0 and out.residual == edits
    man["elements"][2]["mappable_gid"] = "axes_0.images_0"  # 同一个 mappable
    assert _cb_build(man, edits).written == 2


def test_colorbar_clim_stays_with_its_narrower_image_entry_when_left_for_replay(monkeypatch):
    """色条 vmin 是广播、图像自己的 vmin 是窄的：广播留作重放，窄的也一起留下；别的键不受牵连。"""
    monkeypatch.delitem(scriptadjust.WRITES, ("colorbar", "vmin"))
    edits = [
        _cb_patch("vmin", 0.2),
        _cb_patch("vmin", 0.1, "axes_0.images_0"),
        _cb_patch("vmax", 0.9, "axes_0.images_0"),
    ]
    out = _cb_build(_cb_man(), edits)
    assert {(p["gid"], p["prop"]) for p in out.residual} == {
        ("axes_1.colorbar", "vmin"),
        ("axes_0.images_0", "vmin"),
    }
    assert out.written == 1


def test_colorbar_tick_keys_stay_with_their_narrower_tick_group_when_left_for_replay(monkeypatch):
    monkeypatch.delitem(scriptadjust.WRITES, ("colorbar", "tick_fontsize"))
    edits = [
        _cb_patch("tick_fontsize", 9),
        _cb_patch("fontsize", 6, "axes_1.yticks"),
        _cb_patch("color", "#112233", "axes_1.yticks"),
    ]
    man = _cb_man()
    man["elements"].append({"gid": "axes_1.yticks", "role": "ticks", "editable": []})
    out = _cb_build(man, edits)
    assert {(p["gid"], p["prop"]) for p in out.residual} == {
        ("axes_1.colorbar", "tick_fontsize"),
        ("axes_1.yticks", "fontsize"),
    }
    assert out.written == 1


def test_colorbar_label_goes_on_the_script_original_long_axis():
    """长轴看脚本原样的方向（方向被改了留作重放，重放时标签搬过去）；方向认不准（多宿主色条不发
    `orientation`）时不写。"""
    horiz = _cb_man()
    horiz["elements"][1]["editable"] = [
        {"prop": "orientation", "value": "vertical", "value_original": "horizontal"}
    ]
    assert ".set_xlabel('t')" in _cb_build(horiz, [_cb_patch("label", "t")]).data.decode()
    assert ".set_ylabel('t')" in _cb_build(_cb_man(), [_cb_patch("label", "t")]).data.decode()
    blind = _cb_man()
    blind["elements"][1]["editable"] = []
    out = _cb_build(blind, [_cb_patch("label", "t")])
    assert out.written == 0


def test_hiding_a_labelled_colorbar_is_left_for_replay():
    """带标签的色条藏起来：热态里标签在藏起来之前排过位置，重放里轴一开始就不可见、标签没排过——
    画面一样、几何对不上，所以留作重放。没有标签的照写。"""
    man = _cb_man()
    man["elements"].append(
        {"gid": "axes_1.ylabel", "role": "axis_label", "editable": [{"prop": "text", "value": "s"}]}
    )
    hide = _cb_patch("visible", False)
    assert _cb_build(man, [hide]).residual == [hide]
    assert _cb_build(_cb_man(), [hide]).written == 1


def test_colorbar_and_image_cmap_with_the_same_text_are_not_merged():
    """两条色条（同一个 mappable）与图像的 cmap 写出来是同一句话；只合并同一句时，中间隔着另一个值的
    那句会落空（A、B、A 变成 A、B）。"""
    man = _cb_man()
    man["elements"].append(
        {
            "gid": "axes_2.colorbar",
            "role": "colorbar",
            "mappable_gid": "axes_0.images_0",
            "editable": [{"prop": "orientation", "value": "vertical"}],
        }
    )
    edits = [
        _cb_patch("cmap", "viridis"),
        _cb_patch("cmap", "cividis", "axes_2.colorbar"),
        _cb_patch("cmap", "viridis", "axes_0.images_0"),
    ]
    out = _cb_build(man, edits)
    assert out.written == 3
    text = out.data.decode()
    assert text.count(".set_cmap('viridis')") == 2
    assert text.rindex(".set_cmap('viridis')") > text.index(".set_cmap('cividis')")


def test_colorbar_mappable_may_be_a_generic_collection():
    man = _cb_man(mappable="axes_0.collections_3")
    man["elements"][0].update(gid="axes_0.collections_3", role="collection")
    text = _cb_build(man, [_cb_patch("vmin", 0.2)]).data.decode()
    assert "_ax(fig, 0).collections, 3).set_clim(vmin=0.2)" in text


def test_block_stem_matches_the_engine():
    """严格同源对：块里的 `_stem` ↔ `figcapture.savefig_stem`。两边不一致，调整就落不到那张图上。"""
    tree = ast.parse(scriptadjust._RUNTIME)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_stem")
    ns: dict = {"_os": os}
    exec(compile(ast.Module([node], []), "<block>", "exec"), ns)  # noqa: S102
    for fname in (
        "Fig1.pdf",
        "a/b/Fig 1.png",
        "a.tar.gz",
        "noext",
        Path("out") / "f.pdf",
        b"x.pdf",
    ):
        assert ns["_stem"](fname) == figcapture.savefig_stem(fname), fname
    assert ns["_stem"](io.BytesIO()) == figcapture.savefig_stem(io.BytesIO()) == ""


_BBOX_PAIR = """\
import json, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import overrides as o

ns = {}
exec(sys.argv[1], ns)  # the block's `_bbox`
SAMPLES = {
    "visible": True, "facecolor": "#ff0000", "edgecolor": "#00ff00", "linewidth": 2.5,
    "alpha": 0.4, "pad": 0.9, "rounded": True,
}

def look(t):
    p = t.get_bbox_patch()
    if p is None:
        return None
    bs = p.get_boxstyle()
    return [p.get_visible(), list(p.get_facecolor()), list(p.get_edgecolor()), p.get_linewidth(),
            p.get_alpha(), type(bs).__name__, getattr(bs, "pad", None)]

out = {}
fig, ax = plt.subplots()
for prop, value in SAMPLES.items():
    a, b = ax.text(0.5, 0.5, "x"), ax.text(0.5, 0.5, "x")
    o.HANDLERS[("text", "bbox_" + prop)][1](a, value)
    ns["_bbox"](b, prop, value)
    out[prop] = [look(a), look(b)]
# 「关」在没有框的文字上是空操作，两边都不许凭空造出一个框
a, b = ax.text(0.5, 0.5, "x"), ax.text(0.5, 0.5, "x")
o.HANDLERS[("text", "bbox_visible")][1](a, False)
ns["_bbox"](b, "visible", False)
out["off"] = [look(a), look(b)]
print(json.dumps(out))
"""


@real_worker
def test_block_bbox_matches_the_engine():
    """严格同源对：块里的 `_bbox`（现建的框不可见、只有 visible 显示、默认值）↔ `overrides` 的 bbox_* handler。"""
    tree = ast.parse(scriptadjust._RUNTIME)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_bbox")
    src = ast.get_source_segment(scriptadjust._RUNTIME, node)
    env = {**os.environ, "PYTHONPATH": str(Path(exportjob.__file__).parent)}
    done = subprocess.run(
        [WORKER_PY, "-c", _BBOX_PAIR, src],
        capture_output=True,
        encoding="utf-8",
        check=True,
        env=env,
    )
    got = json.loads(done.stdout.strip().splitlines()[-1])
    for prop, (engine, block) in got.items():
        assert engine == block, prop
    assert got["off"] == [None, None]


# ---------------------------------------------------------------------- 表态
def _handler_keys() -> set[tuple[str, str]]:
    code = "import json\nimport overrides as o\nprint(json.dumps(sorted(o.HANDLERS)))\n"
    # overrides.py 只在 worker 进程里按平铺模块 import（engine/ 目录进 sys.path）
    env = {**os.environ, "PYTHONPATH": str(Path(exportjob.__file__).parent)}
    out = subprocess.run(
        [WORKER_PY, "-c", code],
        capture_output=True,
        encoding="utf-8",
        check=True,
        env=env,
    )
    return {tuple(k) for k in json.loads(out.stdout.strip().splitlines()[-1])}


@real_worker
def test_every_override_key_has_taken_a_side():
    """`HANDLERS` 新增键忘了表态就红：要么写得进脚本，要么明说不写。"""
    keys = _handler_keys()
    refused = {(c, p) for c, props in scriptadjust.NOT_WRITABLE.items() for p in props.split()}
    assert not (keys - set(scriptadjust.WRITES) - refused)
    assert not (set(scriptadjust.WRITES) & refused)
    assert not (set(scriptadjust.WRITES) - keys), "表里有引擎不认识的键"
    assert not (refused - keys), "NOT_WRITABLE 里有引擎不认识的键"


# ---------------------------------------------------------------------- 请求
def _spec(**over) -> dict:
    return {
        "scope": "original",
        "filename": "Fig1",
        "formats": ["pdf"],
        "original": {"figure_id": "Fig1.pdf", "source_kind": "vector", "overrides": []},
        **over,
    }


def test_request_may_ask_only_for_the_script():
    req = exportreq.normalize(_spec(formats=[], include_edited_script=True))
    assert req.include_script and req.formats == ()


def test_request_without_formats_or_script_is_still_refused():
    with pytest.raises(exportreq.ExportRequestError) as e:
        exportreq.normalize(_spec(formats=[]))
    assert e.value.code == "no_format"


def test_canvas_export_cannot_carry_a_script():
    with pytest.raises(exportreq.ExportRequestError) as e:
        exportreq.normalize(
            {
                "scope": "canvas",
                "filename": "x",
                "formats": ["pdf"],
                "include_edited_script": True,
                "canvas": {"page_w_mm": 10, "page_h_mm": 10, "objects": []},
            }
        )
    assert e.value.code == "script_export_not_for_canvas"


def test_legacy_requests_do_not_get_a_script():
    req = exportreq.normalize(
        {
            "stem": "x",
            "formats": ["pdf"],
            "page_w_mm": 1,
            "page_h_mm": 1,
            "include_edited_script": 1,
        }
    )
    assert not req.include_script


@pytest.mark.parametrize(
    "scope, has_script, how",
    [
        ("original", True, exportroute.HOW_REDRAW),
        ("original", False, exportroute.HOW_UNAVAILABLE),
        ("canvas", True, exportroute.HOW_UNAVAILABLE),
    ],
)
def test_route_offers_the_script_regardless_of_formats(scope, has_script, how):
    route = exportroute.panel_route(
        scope=scope, formats=["pdf"], file_kind="pdf", overrides=[], has_script=has_script
    )
    assert route["offers"]["script"]["how"] == how


# ---------------------------------------------------------------------- 真链路
SCRIPT = """\
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    with open("points.csv", encoding="utf-8") as fh:
        ys = [float(v) for v in fh.read().split(",")]
    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot(range(len(ys)), ys, label="signal")
    ax.set_xlabel("Time (s)")
    ax.set_title("Raw")
    ax.legend(loc="upper left")
    fig.subplots_adjust(left=0.2, bottom=0.22)
    fig.savefig("Fig1.pdf")


if __name__ == "__main__":
    main()
"""


REGISTRY = json.dumps(
    {
        "version": 1,
        "scripts": {"fig1.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["Fig1"]}},
    }
)


EDITS = [
    {"gid": "axes_0.lines_0", "prop": "color", "value": "#d6278f"},
    {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 3.0},
    {"gid": "axes_0.title", "prop": "fontsize", "value": 15},
    {"gid": "axes_0.legend", "prop": "fontsize", "value": 7},
]


#: 图像 + 色条（色条轴是 `axes_1`，图像是 `axes_0.images_0`）：色条的组合用例用它。
CBAR_SCRIPT = """\
import matplotlib.pyplot as plt
import numpy as np


def main():
    fig, ax = plt.subplots(figsize=(3.6, 2.8))
    im = ax.imshow(np.linspace(0, 1, 144).reshape(12, 12), cmap="magma")
    cb = fig.colorbar(im, ax=ax)
    cb.set_label("signal")
    fig.savefig("Fig1.pdf")


if __name__ == "__main__":
    main()
"""


def _open_project(tmp_path, monkeypatch, script: str):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig1.py").write_text(script, encoding="utf-8")
    (figs / "points.csv").write_text("0,9,2,10,4,7.5", encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(REGISTRY, encoding="utf-8")
    subprocess.run([WORKER_PY, "fig1.py"], cwd=figs, check=True)
    m.open_project(str(figs))
    exportjob.reset_for_tests()
    return m, figs


def _close_project(m, figs):
    m.reset_projects()
    pool.shutdown_all(figures_dir=str(figs), wait=True)
    project_watch.stop()


@pytest.fixture
def project(tmp_path, monkeypatch):
    m, figs = _open_project(tmp_path, monkeypatch, SCRIPT)
    try:
        yield m.app.test_client(), figs
    finally:
        _close_project(m, figs)


@pytest.fixture
def cbar_project(tmp_path, monkeypatch):
    m, figs = _open_project(tmp_path, monkeypatch, CBAR_SCRIPT)
    try:
        yield m.app.test_client(), figs
    finally:
        _close_project(m, figs)


#: 柱系列 + 误差棒 + 茎叶 + 散点 + 图例：容器的 gid 按 `ax.containers` 的顺序编号（柱系列 0、误差棒 1、茎叶 2）
SERIES_SCRIPT = """\
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    with open("points.csv", encoding="utf-8") as fh:
        ys = [float(v) for v in fh.read().split(",")]
    fig, ax = plt.subplots(figsize=(3.6, 2.6))
    ax.bar(range(len(ys)), ys, label="bars")
    ax.errorbar(range(len(ys)), ys, yerr=0.8, capsize=3, label="spread")
    ax.stem(range(len(ys)), [v / 2 for v in ys], label="stems")
    ax.scatter(range(len(ys)), ys, s=20, label="dots")
    ax.legend(loc="upper left")
    fig.savefig("Fig1.pdf")


if __name__ == "__main__":
    main()
"""


@pytest.fixture
def series_project(tmp_path, monkeypatch):
    m, figs = _open_project(tmp_path, monkeypatch, SERIES_SCRIPT)
    try:
        yield m.app.test_client(), figs
    finally:
        _close_project(m, figs)


#: 同一张图再加两根箭头：一根纯箭头注释（gid `axes_0.texts_0.arrow`）、一根独立箭头（`axes_0.arrows_0`）
ARROW_SCRIPT = SCRIPT.replace(
    "import matplotlib.pyplot as plt\n",
    "import matplotlib.pyplot as plt\nfrom matplotlib.patches import FancyArrowPatch\n",
    1,
).replace(
    '    ax.legend(loc="upper left")\n',
    '    ax.annotate("", xy=(3.0, 9.0), xytext=(1.5, 4.0),'
    ' arrowprops=dict(arrowstyle="->", color="#444444"))\n'
    '    ax.add_patch(FancyArrowPatch((3.5, 3.0), (4.6, 6.0), arrowstyle="-|>",'
    ' mutation_scale=14, color="#335588"))\n'
    '    ax.legend(loc="upper left")\n',
    1,
)


assert "FancyArrowPatch((3.5" in ARROW_SCRIPT and "annotate" in ARROW_SCRIPT


#: aspect=1 的子图（按纵横比在画的时候收缩）：文字（数据坐标）与纯箭头注释在上面。
ASPECT_SCRIPT = SCRIPT.replace(
    '    ax.legend(loc="upper left")\n',
    "    ax.set_aspect(1)\n"
    '    ax.text(1.0, 5.0, "note")\n'
    '    ax.annotate("", xy=(3.0, 9.0), xytext=(1.5, 4.0), arrowprops=dict(arrowstyle="->"))\n'
    '    ax.legend(loc="upper left")\n',
    1,
)


assert "set_aspect(1)" in ASPECT_SCRIPT


@pytest.fixture
def aspect_project(tmp_path, monkeypatch):
    m, figs = _open_project(tmp_path, monkeypatch, ASPECT_SCRIPT)
    try:
        yield m.app.test_client(), figs
    finally:
        _close_project(m, figs)


@pytest.fixture
def arrow_project(tmp_path, monkeypatch):
    m, figs = _open_project(tmp_path, monkeypatch, ARROW_SCRIPT)
    try:
        yield m.app.test_client(), figs
    finally:
        _close_project(m, figs)


def _export(client, patches, **over):
    spec = {
        "scope": "original",
        "filename": "Fig1",
        "formats": ["pdf"],
        "include_edited_script": True,
        "overwrite": "replace",
        "original": {"figure_id": "Fig1.pdf", "source_kind": "vector", "overrides": patches},
        **over,
    }
    return client.post("/api/export", json=spec).get_json()


def _script_output(body) -> dict:
    return next(o for o in body["outputs"] if o["format"] == exportjob.SCRIPT_KEY)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(root: Path) -> set[str]:
    return {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and "tavottofile" not in p.parts and "__pycache__" not in p.parts
    }


def _largest_channel_diff(a: Path, b: Path) -> int:
    """两份 PDF 渲染后逐字节比，最大的一处差。"""
    with pymupdf.open(a) as da, pymupdf.open(b) as db:
        pa, pb = da[0].get_pixmap(dpi=150), db[0].get_pixmap(dpi=150)
    assert (pa.w, pa.h) == (pb.w, pb.h)
    return max(abs(x - y) for x, y in zip(pa.samples, pb.samples))


#: 导出按 600 dpi 重画、脚本的 savefig 用默认 dpi：矢量里坐标框的直线吸附差一点点，
#: 抗锯齿边缘差到 20 / 255 上下（没有修改的图导出是原件拷贝，逐字节相同）。这条线上下是两回事：
#: 换一种颜色、挪一段文字都远超它。
_SNAP_NOISE = 32


@real_worker
def test_plain_matplotlib_reproduces_the_exported_figure(project, tmp_path):
    """用户拿到 `Fig1_edited.py`，放到原脚本旁边，纯 matplotlib 跑：画出来就是导出的那张。"""
    client, figs = project
    before_tree, before_sha = _tree(figs), _sha(figs / "fig1.py")
    body = _export(client, EDITS)
    assert body["status"] == "done", body
    out = _script_output(body)
    assert out["name"] == "Fig1_edited.py"
    report = out["script_report"]
    assert report["written"] == len(EDITS) and report["skipped"] == []
    # 原脚本与项目目录零改动
    assert _sha(figs / "fig1.py") == before_sha and _tree(figs) == before_tree

    exported = Path(body["export_dir"])
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "points.csv").write_text("0,9,2,10,4,7.5", encoding="utf-8")
    (run_dir / "edited.py").write_bytes((exported / "Fig1_edited.py").read_bytes())
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    subprocess.run([WORKER_PY, "edited.py"], cwd=run_dir, check=True, env=env)
    assert _largest_channel_diff(run_dir / "Fig1.pdf", exported / "Fig1.pdf") <= _SNAP_NOISE
    # 判据是活的：这份导出与没改过的原图差得远，不是两张白纸在比
    assert _largest_channel_diff(figs / "Fig1.pdf", exported / "Fig1.pdf") > _SNAP_NOISE


@real_worker
def test_tight_crop_keeps_the_page_the_editor_shows(project, tmp_path):
    """`bbox_inches="tight"` 量的是脚本画出来的样子：字号变大不能让页面跟着重裁。

    Tavotto 的图幅在脚本跑完时定下，块要是先改再裁，改字号这种最常见的编辑就 `script_export_diverged`。
    """
    client, figs = project
    (figs / "fig1.py").write_text(
        SCRIPT.replace('fig.savefig("Fig1.pdf")', 'fig.savefig("Fig1.pdf", bbox_inches="tight")'),
        encoding="utf-8",
    )
    subprocess.run([WORKER_PY, "fig1.py"], cwd=figs, check=True)
    pool.shutdown_all(figures_dir=str(figs), wait=True)
    edit = [{"gid": "axes_0.xlabel", "prop": "fontsize", "value": 16}]
    body = _export(client, edit)
    assert body["status"] == "done", body
    exported = Path(body["export_dir"])
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "points.csv").write_text("0,9,2,10,4,7.5", encoding="utf-8")
    (run_dir / "edited.py").write_bytes((exported / "Fig1_edited.py").read_bytes())
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    subprocess.run([WORKER_PY, "edited.py"], cwd=run_dir, check=True, env=env)
    with pymupdf.open(run_dir / "Fig1.pdf") as a, pymupdf.open(exported / "Fig1.pdf") as b:
        assert abs(a[0].rect.height - b[0].rect.height) < 0.1
        assert abs(a[0].rect.width - b[0].rect.width) < 0.1


@real_worker
@pytest.mark.parametrize(
    "patch",
    [
        {"gid": "axes_0.xlabel", "prop": "pos_frac", "value": [0.4, 0.9]},
        {"gid": "axes_0.title", "prop": "pos_frac", "value": [0.35, 0.08]},
        {"gid": "axes_0.legend", "prop": "loc_frac", "value": [0.5, 0.3]},
    ],
    ids=["axis-label", "title", "legend"],
)
def test_moves_on_a_tight_cropped_figure_land_where_the_editor_shows(project, tmp_path, patch):
    """`bbox_inches="tight"` 的图，界面上的位置是**裁出来那一页**里的比例（ADR 0098）：
    块按整张 figsize 换算就差出裁掉的那一圈，挪个文字就 `script_export_diverged`（一份用户反馈）。"""
    client, figs = project
    (figs / "fig1.py").write_text(
        SCRIPT.replace('fig.savefig("Fig1.pdf")', 'fig.savefig("Fig1.pdf", bbox_inches="tight")'),
        encoding="utf-8",
    )
    subprocess.run([WORKER_PY, "fig1.py"], cwd=figs, check=True)
    pool.shutdown_all(figures_dir=str(figs), wait=True)
    body = _export(client, [patch])
    assert body["status"] == "done", body
    assert _script_output(body)["script_report"]["written"] == 1
    exported = Path(body["export_dir"])
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "points.csv").write_text("0,9,2,10,4,7.5", encoding="utf-8")
    (run_dir / "edited.py").write_bytes((exported / "Fig1_edited.py").read_bytes())
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    subprocess.run([WORKER_PY, "edited.py"], cwd=run_dir, check=True, env=env)
    assert _largest_channel_diff(run_dir / "Fig1.pdf", exported / "Fig1.pdf") <= _SNAP_NOISE


@real_worker
@pytest.mark.parametrize(
    "patch",
    [
        {"gid": "axes_0.title", "prop": "fontfamily", "value": "serif"},
        {"gid": "axes_0.xlabel", "prop": "fontfamily", "value": "DejaVu Serif"},
        {"gid": "axes_0.xticks", "prop": "fontfamily", "value": "serif"},
        {"gid": "axes_0.legend.texts_0", "prop": "fontfamily", "value": "serif"},
        {
            "gid": "axes_0.title",
            "prop": "fontfamily",
            "value": {"primary": "DejaVu Serif", "fallback": "DejaVu Sans"},
        },
    ],
    ids=["title", "axis-label", "ticks", "legend-entry", "selection"],
)
def test_font_edits_replay_identically(project, patch):
    client, _figs = project
    body = _export(client, [patch], formats=[])
    assert body["status"] == "done", body
    assert _script_output(body)["script_report"]["written"] == 1


_LEG = "axes_0.legend"


_ENTRY = "axes_0.legend.texts_0"


@real_worker
@pytest.mark.parametrize(
    "patches",
    [
        # 重建之后再拖：拖动的点落在重建出来的那个图例上
        [
            {"gid": _LEG, "prop": "borderpad", "value": 1.6},
            {"gid": _LEG, "prop": "loc_frac", "value": [0.5, 0.3]},
        ],
        # 先拖再给锚点：拖动清锚框（位置模型的优先级），与列表顺序无关
        [
            {"gid": _LEG, "prop": "loc_frac", "value": [0.5, 0.3]},
            {"gid": _LEG, "prop": "loc_anchor", "value": [0.9, 0.9]},
        ],
        # 示意线先断开再改曲线：示意线不跟着曲线变
        [
            {"gid": _ENTRY, "prop": "handle_color", "value": "#ff00ff"},
            {"gid": "axes_0.lines_0", "prop": "color", "value": "#00aa00"},
        ],
        [
            {"gid": _ENTRY, "prop": "binding", "value": "custom"},
            {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 4.0},
        ],
        # 图例字号与间距同一次重建
        [
            {"gid": _LEG, "prop": "fontsize", "value": 6.0},
            {"gid": _LEG, "prop": "handlelength", "value": 4.0},
            {"gid": _ENTRY, "prop": "color", "value": "#aa0000"},
        ],
    ],
    ids=["rebuild-then-drag", "drag-wins-anchor", "detached-key", "binding-custom", "one-rebuild"],
)
def test_legend_edits_combine_like_the_editor(project, patches):
    client, _figs = project
    body = _export(client, patches, formats=[])
    assert body["status"] == "done", body
    assert _script_output(body)["script_report"]["written"] == len(patches)


_AXES = "axes_0"


@real_worker
@pytest.mark.parametrize(
    "patches",
    [
        # 广播在前 / 在后：窄的都要盖住广播，与列表顺序无关
        [
            {"gid": _AXES, "prop": "spine_color", "value": "#2255aa"},
            {"gid": _AXES, "prop": "spine_top_color", "value": "#cc3300"},
            {"gid": _AXES, "prop": "spine_linewidth", "value": 2.2},
            {"gid": _AXES, "prop": "spine_left_linewidth", "value": 0.4},
        ],
        [
            {"gid": _AXES, "prop": "spine_left_linewidth", "value": 0.4},
            {"gid": _AXES, "prop": "spine_top_color", "value": "#cc3300"},
            {"gid": _AXES, "prop": "spine_linewidth", "value": 2.2},
            {"gid": _AXES, "prop": "spine_color", "value": "#2255aa"},
        ],
        # 网格没开时先改样式，再打开
        [
            {"gid": _AXES, "prop": "grid_color", "value": "#cc3300"},
            {"gid": _AXES, "prop": "grid_linestyle", "value": "--"},
            {"gid": _AXES, "prop": "grid_linewidth", "value": 1.4},
            {"gid": _AXES, "prop": "grid_alpha", "value": 0.5},
            {"gid": _AXES, "prop": "grid_y", "value": True},
        ],
        # 先开网格再改样式；刻度线四边开关与边框显隐同改
        [
            {"gid": _AXES, "prop": "grid_x", "value": True},
            {"gid": _AXES, "prop": "grid_color", "value": "#2255aa"},
            {"gid": _AXES, "prop": "ticks_bottom", "value": False},
            {"gid": _AXES, "prop": "ticks_top", "value": True},
            {"gid": _AXES, "prop": "ticks_right", "value": True},
            {"gid": _AXES, "prop": "spine_right", "value": False},
        ],
    ],
    ids=["broadcast-first", "narrow-first", "style-then-open", "open-then-style-ticks"],
)
def test_grid_and_spine_edits_combine_like_the_editor(project, patches):
    client, _figs = project
    body = _export(client, patches, formats=[])
    assert body["status"] == "done", body
    assert _script_output(body)["script_report"]["written"] == len(patches)


_CB, _IM, _CB_YTICKS = "axes_1.colorbar", "axes_0.images_0", "axes_1.yticks"


_BARS = "axes_0.barseries_0"


_ERR = "axes_0.errorbar_1"


_STEMS = "axes_0.stemseries_2"


_DOTS = "axes_0.scatter_2"  # 误差棒的竖线、茎的竖线也在 ax.collections 里，占了 0、1 号


@real_worker
@pytest.mark.parametrize(
    ("patches", "residual"),
    [
        # 色条与图像的 cmap 互相广播（ADR 0135），文档序靠后的赢：两种列表顺序
        (
            [
                {"gid": _CB, "prop": "cmap", "value": "viridis"},
                {"gid": _IM, "prop": "cmap", "value": "cividis"},
            ],
            0,
        ),
        (
            [
                {"gid": _IM, "prop": "cmap", "value": "cividis"},
                {"gid": _CB, "prop": "cmap", "value": "viridis"},
            ],
            0,
        ),
        # vmin / vmax：图像自己的窄、色条的广播，窄的盖广播，与列表顺序无关
        (
            [
                {"gid": _CB, "prop": "vmin", "value": 0.3},
                {"gid": _IM, "prop": "vmin", "value": 0.1},
                {"gid": _CB, "prop": "vmax", "value": 0.7},
            ],
            0,
        ),
        (
            [
                {"gid": _IM, "prop": "vmin", "value": 0.1},
                {"gid": _CB, "prop": "vmax", "value": 0.7},
                {"gid": _CB, "prop": "vmin", "value": 0.3},
            ],
            0,
        ),
        # 刻度字号 / 颜色：色条的广播、刻度组自己的窄，两种列表顺序
        (
            [
                {"gid": _CB, "prop": "tick_fontsize", "value": 11},
                {"gid": _CB_YTICKS, "prop": "fontsize", "value": 6},
                {"gid": _CB, "prop": "tick_color", "value": "#2255aa"},
                {"gid": _CB_YTICKS, "prop": "color", "value": "#cc3300"},
            ],
            0,
        ),
        (
            [
                {"gid": _CB_YTICKS, "prop": "color", "value": "#cc3300"},
                {"gid": _CB_YTICKS, "prop": "fontsize", "value": 6},
                {"gid": _CB, "prop": "tick_color", "value": "#2255aa"},
                {"gid": _CB, "prop": "tick_fontsize", "value": 11},
            ],
            0,
        ),
        # 标签、轮廓、色条的显隐：同一个标签的两个入口（色条标签与轴标签文字）按列表序后者赢
        (
            [
                {"gid": _CB, "prop": "label", "value": "intensity"},
                {"gid": "axes_1.ylabel", "prop": "text", "value": "flux"},
                {"gid": _CB, "prop": "outline_width", "value": 2.5},
                {"gid": _CB, "prop": "outline_visible", "value": True},
            ],
            0,
        ),
        (
            [
                {"gid": "axes_1.ylabel", "prop": "text", "value": "flux"},
                {"gid": _CB, "prop": "label", "value": "intensity"},
                {"gid": _CB, "prop": "visible", "value": False},
            ],
            1,  # 带标签的色条藏起来留作重放（见 `_statement`）
        ),
        # 方向 / 延伸三角留作重放，其余写进脚本（重放时标签搬去新的长轴）
        (
            [
                {"gid": _CB, "prop": "orientation", "value": "horizontal"},
                {"gid": _CB, "prop": "extend", "value": "both"},
                {"gid": _CB, "prop": "label", "value": "intensity"},
                {"gid": _CB, "prop": "tick_color", "value": "#2255aa"},
                {"gid": _CB, "prop": "cmap", "value": "viridis"},
            ],
            2,
        ),
    ],
    ids=[
        "cmap-colorbar-first",
        "cmap-image-first",
        "clim-broadcast-first",
        "clim-narrow-first",
        "ticks-broadcast-first",
        "ticks-narrow-first",
        "label-outline",
        "label-then-hide",
        "reorient-and-extend",
    ],
)
def test_colorbar_edits_combine_like_the_editor(cbar_project, patches, residual):
    client, _figs = cbar_project
    body = _export(client, patches, formats=[])
    assert body["status"] == "done", body
    assert _script_output(body)["script_report"]["written"] == len(patches) - residual


@real_worker
@pytest.mark.parametrize(
    "patches",
    [
        # 柱系列在前 / 单根柱在前：窄的（单根柱）都要盖住广播（柱系列），与列表顺序无关
        [
            {"gid": _BARS, "prop": "facecolor", "value": "#2ca02c"},
            {"gid": f"{_BARS}.bar_1", "prop": "facecolor", "value": "#d62728"},
        ],
        [
            {"gid": f"{_BARS}.bar_1", "prop": "facecolor", "value": "#d62728"},
            {"gid": _BARS, "prop": "facecolor", "value": "#2ca02c"},
        ],
        # 第一根柱是图例色块的来源：改它，图例里的色块跟着变
        [
            {"gid": f"{_BARS}.bar_0", "prop": "facecolor", "value": "#9467bd"},
            {"gid": f"{_BARS}.bar_0", "prop": "hatch", "value": "//"},
            {"gid": _BARS, "prop": "edgecolor", "value": "#222222"},
            {"gid": _BARS, "prop": "bar_width", "value": 0.5},
        ],
        # 误差棒改色 / 横杠，图例里那一格跟着变（引擎从源重新派生；脚本里靠整份重排，不用图例自己改任何东西）
        [
            {"gid": _ERR, "prop": "color", "value": "#d62728"},
            {"gid": _ERR, "prop": "capsize", "value": 7.0},
        ],
        # 同时改图例间距（整份重排）：误差棒的修改在重排之前、重排只落一回，不论列表里谁先谁后
        [
            {"gid": _ERR, "prop": "color", "value": "#d62728"},
            {"gid": "axes_0.legend", "prop": "borderpad", "value": 1.5},
        ],
        [
            {"gid": "axes_0.legend", "prop": "borderpad", "value": 1.5},
            {"gid": _ERR, "prop": "linewidth", "value": 3.0},
            {"gid": _ERR, "prop": "alpha", "value": 0.5},
        ],
        # 茎叶、散点的示意线一格不止一个 artist / 大小现算：同样靠整份重排
        [
            {"gid": _STEMS, "prop": "color", "value": "#9467bd"},
            {"gid": _STEMS, "prop": "markersize", "value": 10.0},
            {"gid": _DOTS, "prop": "size", "value": 90.0},
        ],
        [
            {"gid": _DOTS, "prop": "marker", "value": "s"},
            {"gid": _STEMS, "prop": "linestyle", "value": "--"},
            {"gid": _ERR, "prop": "cap_thickness", "value": 2.5},
        ],
        # 改名：系列的名字挪到最后落，不影响前面按名字找图例色块的语句
        [
            {"gid": _BARS, "prop": "label", "value": "renamed"},
            {"gid": _BARS, "prop": "facecolor", "value": "#2ca02c"},
            {"gid": _ERR, "prop": "visible", "value": False},
        ],
        # 茎叶改名带改色：改色要重排图例（从源按名字收集），改名得排在重排之后
        [
            {"gid": _STEMS, "prop": "label", "value": "renamed"},
            {"gid": _STEMS, "prop": "color", "value": "#9467bd"},
        ],
    ],
    ids=[
        "series-then-bar",
        "bar-then-series",
        "first-bar-feeds-the-legend",
        "errorbar-alone-legend-follows",
        "errorbar-then-legend",
        "legend-then-errorbar",
        "stem-and-scatter-legend-follow",
        "scatter-marker-stem-style",
        "rename-last",
        "stem-rename-with-restyle",
    ],
)
def test_series_edits_combine_like_the_editor(series_project, patches):
    client, _figs = series_project
    body = _export(client, patches, formats=[])
    assert body["status"] == "done", body
    assert _script_output(body)["script_report"]["written"] == len(patches)


_TITLE = "axes_0.title"


_ARR_ANN = "axes_0.texts_0.arrow"


_ARR_ALONE = "axes_0.arrows_0"


@real_worker
@pytest.mark.parametrize(
    "patches",
    [
        # 先改底框颜色、再打开底框（现建的框不可见，样式不改显隐）
        [
            {"gid": _TITLE, "prop": "bbox_facecolor", "value": "#ffd0d0"},
            {"gid": _TITLE, "prop": "bbox_edgecolor", "value": "#aa0000"},
            {"gid": _TITLE, "prop": "bbox_linewidth", "value": 1.5},
            {"gid": _TITLE, "prop": "bbox_visible", "value": True},
        ],
        # 先打开、再改颜色与圆角 / 内边距
        [
            {"gid": _TITLE, "prop": "bbox_visible", "value": True},
            {"gid": _TITLE, "prop": "bbox_facecolor", "value": "#ffd0d0"},
            {"gid": _TITLE, "prop": "bbox_rounded", "value": True},
            {"gid": _TITLE, "prop": "bbox_pad", "value": 0.8},
            {"gid": _TITLE, "prop": "bbox_alpha", "value": 0.5},
        ],
        # 只改底色、从没打开：脚本里也不能凭空多出一个框
        [{"gid": _TITLE, "prop": "bbox_facecolor", "value": "#ffd0d0"}],
        # 描边三条分着改：开关、颜色、线宽合成一次路径效果；图例项的文字同理
        [
            {"gid": _TITLE, "prop": "stroke_color", "value": "#3355ff"},
            {"gid": _TITLE, "prop": "stroke_width", "value": 3.0},
            {"gid": _TITLE, "prop": "stroke_enabled", "value": True},
            {"gid": "axes_0.legend.texts_0", "prop": "stroke_enabled", "value": True},
            {"gid": "axes_0.legend.texts_0", "prop": "bbox_visible", "value": True},
            {"gid": "axes_0.legend.texts_0", "prop": "bbox_facecolor", "value": "#ccffcc"},
        ],
        # 描边与底框同一段文字、再改行距
        [
            {"gid": _TITLE, "prop": "stroke_enabled", "value": True},
            {"gid": _TITLE, "prop": "bbox_visible", "value": True},
            {"gid": _TITLE, "prop": "bbox_facecolor", "value": "#ffeeaa"},
            {"gid": _TITLE, "prop": "text", "value": "two\nlines"},
            {"gid": _TITLE, "prop": "linespacing", "value": 2.0},
        ],
    ],
    ids=[
        "style-then-open",
        "open-then-style",
        "style-never-opened",
        "stroke-legend",
        "stroke-bbox",
    ],
)
def test_text_look_edits_combine_like_the_editor(project, patches):
    client, _figs = project
    body = _export(client, patches, formats=[])
    assert body["status"] == "done", body
    assert _script_output(body)["script_report"]["written"] == len(patches)


@real_worker
@pytest.mark.parametrize(
    "patches",
    [
        # 拖纯箭头注释的端点 + 改文字（标题加底框与描边）
        [
            {"gid": _ARR_ANN, "prop": "endpoints_frac", "value": [0.35, 0.55, 0.62, 0.3]},
            {"gid": _TITLE, "prop": "bbox_visible", "value": True},
            {"gid": _TITLE, "prop": "stroke_enabled", "value": True},
            {"gid": _TITLE, "prop": "fontsize", "value": 12},
        ],
        # 拖独立箭头的端点、改线型与样式（只虚杆、头保持实线）
        [
            {"gid": _ARR_ALONE, "prop": "endpoints_frac", "value": [0.4, 0.6, 0.7, 0.35]},
            {"gid": _ARR_ALONE, "prop": "linestyle", "value": "--"},
            {"gid": _ARR_ALONE, "prop": "arrowstyle", "value": "<->"},
            {"gid": _ARR_ALONE, "prop": "linewidth", "value": 2.0},
        ],
        # 端点按 figure 分数落在最终的坐标范围上：改 ylim 在列表里排前排后，两根箭头落点都不变
        [
            {"gid": _ARR_ANN, "prop": "endpoints_frac", "value": [0.35, 0.55, 0.62, 0.3]},
            {"gid": _ARR_ALONE, "prop": "endpoints_frac", "value": [0.4, 0.6, 0.7, 0.35]},
            {"gid": "axes_0", "prop": "ylim", "value": [-6.0, 14.0]},
        ],
        [
            {"gid": "axes_0", "prop": "ylim", "value": [-6.0, 14.0]},
            {"gid": _ARR_ANN, "prop": "endpoints_frac", "value": [0.35, 0.55, 0.62, 0.3]},
            {"gid": _ARR_ALONE, "prop": "endpoints_frac", "value": [0.4, 0.6, 0.7, 0.35]},
        ],
        # 两根箭头的样式一起改，注释箭头先改颜色再改线型
        [
            {"gid": _ARR_ANN, "prop": "color", "value": "#cc3300"},
            {"gid": _ARR_ANN, "prop": "linestyle", "value": "--"},
            {"gid": _ARR_ANN, "prop": "arrowstyle", "value": "-|>"},
            {"gid": _ARR_ALONE, "prop": "visible", "value": False},
        ],
    ],
    ids=[
        "drag-annotation-and-text",
        "drag-standalone-dashed",
        "drag-then-ylim",
        "ylim-then-drag",
        "style-both",
    ],
)
def test_arrow_edits_combine_like_the_editor(arrow_project, patches):
    client, _figs = arrow_project
    body = _export(client, patches, formats=[])
    assert body["status"] == "done", body
    assert _script_output(body)["script_report"]["written"] == len(patches)


def test_endpoints_of_a_text_annotation_stay_with_the_engine():
    """有字的注释上端点是失效的（manifest 不发 `arrow_endpoints`）：脚本里照落会把文字箭头拖走，留给引擎裁决。"""
    gid = "axes_0.texts_0.arrow"
    patch = {"gid": gid, "prop": "endpoints_frac", "value": [0.2, 0.3, 0.6, 0.4]}
    live = {
        "elements": [
            {"gid": gid, "role": "arrow_patch", "editable": [], "arrow_endpoints": [[0, 0], [1, 1]]}
        ]
    }
    dead = {"elements": [{"gid": gid, "role": "arrow_patch", "editable": []}]}
    out = scriptadjust.build(b"import os\n", "Fig1", [patch], live, version="9.9")
    assert out.written == 1 and not out.residual and "_ann_to(" in out.data.decode()
    out = scriptadjust.build(b"import os\n", "Fig1", [patch], dead, version="9.9")
    assert out.written == 0 and out.residual == [patch]


@real_worker
@pytest.mark.parametrize(
    ("patches", "held"),
    [
        # 挪子图（`position` 留作重放）+ 拖数据坐标箭头的端点：端点按落笔时的子图换算，重放再挪子图箭头就离位
        (
            [
                {"gid": "axes_0", "prop": "position", "value": [0.3, 0.35, 0.55, 0.5]},
                {"gid": _ARR_ANN, "prop": "endpoints_frac", "value": [0.4, 0.6, 0.6, 0.4]},
            ],
            {"position", "endpoints_frac"},
        ),
        (
            [
                {"gid": _ARR_ALONE, "prop": "endpoints_frac", "value": [0.4, 0.6, 0.7, 0.35]},
                {"gid": "axes_0", "prop": "position", "value": [0.3, 0.35, 0.55, 0.5]},
            ],
            {"position", "endpoints_frac"},
        ),
    ],
    ids=["position-then-drag-arrow", "drag-arrow-then-position"],
)
def test_arrow_drag_follows_a_replayed_subplot_move(arrow_project, patches, held):
    client, _figs = arrow_project
    body = _export(client, patches, formats=[])
    assert body["status"] == "done", body
    report = _script_output(body)["script_report"]
    assert {s["prop"] for s in report["skipped"]} == held


@real_worker
def test_title_drag_follows_a_replayed_subplot_move(project):
    """既有版本的同一个问题：`pos_frac`（`_title_to`）也按落笔时的子图换算。"""
    client, _figs = project
    patches = [
        {"gid": "axes_0", "prop": "position", "value": [0.3, 0.35, 0.55, 0.5]},
        {"gid": "axes_0.title", "prop": "pos_frac", "value": [0.5, 0.2]},
    ]
    body = _export(client, patches, formats=[])
    assert body["status"] == "done", body
    assert {s["prop"] for s in _script_output(body)["script_report"]["skipped"]} == {
        "position",
        "pos_frac",
    }


def test_subplot_move_does_not_hold_back_a_drag_on_another_subplot():
    """`position` 只动它自己的子图：别的子图上的拖动照写（单元：只看 `build` 的分流）。"""
    man = {
        "elements": [
            {"gid": "axes_0", "role": "axes", "editable": []},
            {
                "gid": "axes_1.title",
                "role": "title",
                "editable": [],
            },
        ]
    }
    move = {"gid": "axes_0", "prop": "position", "value": [0.3, 0.3, 0.5, 0.5]}
    drag = {"gid": "axes_1.title", "prop": "pos_frac", "value": [0.5, 0.2]}
    out = scriptadjust.build(b"import os\n", "Fig1", [move, drag], man, version="9.9")
    assert out.written == 1 and [p["prop"] for p in out.residual] == ["position"]


_NOTE, _NOTE_ARR = "axes_0.texts_0", "axes_0.texts_1.arrow"


_TITLE_DRAG = {"gid": "axes_0.title", "prop": "pos_frac", "value": [0.5, 0.2]}


_NOTE_DRAG = {"gid": _NOTE, "prop": "pos_frac", "value": [0.5, 0.4]}


_ARR_DRAG = {"gid": _NOTE_ARR, "prop": "endpoints_frac", "value": [0.4, 0.6, 0.6, 0.4]}


_INVERT_X = {"gid": "axes_0", "prop": "invert_x", "value": True}


_YLIM = {"gid": "axes_0", "prop": "ylim", "value": [-3.0, 14.0]}


_MOVE = {"gid": "axes_0", "prop": "position", "value": [0.3, 0.35, 0.55, 0.5]}


@real_worker
@pytest.mark.parametrize(
    ("patches", "held"),
    [
        # 前提核对：aspect=1 的子图（画的时候才收缩）上只拖也不能写进脚本——落笔时的子图与画完之后的不同，
        # 去掉 `_sized_at_draw` 这一支三条都 diverged（实测）
        ([_TITLE_DRAG], {"pos_frac"}),
        ([_NOTE_DRAG], {"pos_frac"}),
        ([_ARR_DRAG], {"endpoints_frac"}),
        # 拖 → 反转 / 反转 → 拖：拖动留作重放时，排在它后面的反转也一起留下，保住引擎里的先后
        ([_NOTE_DRAG, _INVERT_X], {"pos_frac", "invert_x"}),
        ([_INVERT_X, _NOTE_DRAG], {"pos_frac"}),
        ([_ARR_DRAG, _INVERT_X, _YLIM], {"endpoints_frac", "invert_x", "ylim"}),
    ],
    ids=[
        "title",
        "text",
        "arrow",
        "drag-then-invert",
        "invert-then-drag",
        "arrow-then-invert-ylim",
    ],
)
def test_placed_drag_keeps_its_order_with_later_coordinate_edits(aspect_project, patches, held):
    client, _figs = aspect_project
    body = _export(client, patches, formats=[])
    assert body["status"] == "done", body
    assert {s["prop"] for s in _script_output(body)["script_report"]["skipped"]} == held


def test_residual_keeps_the_original_order():
    """留作重放的 patch 按原始列表顺序交出去（引擎同档按列表序），不按各条规则追加的次序。"""
    man = {
        "elements": [
            {"gid": "axes_0", "role": "axes", "editable": [{"prop": "aspect", "value": "equal"}]},
            {"gid": _NOTE, "role": "text", "editable": []},
            {"gid": "axes_0.title", "role": "title", "editable": []},
        ]
    }
    patches = [_NOTE_DRAG, _INVERT_X, _TITLE_DRAG]
    out = scriptadjust.build(b"import os\n", "Fig1", patches, man, version="9.9")
    assert out.residual == patches


_YTICKS_FIXED = {"gid": "axes_0.yticks", "prop": "major_mode", "value": "fixed"}


@real_worker
def test_fixed_ticks_follow_a_replayed_ylim_on_an_aspect_subplot(aspect_project):
    """真导出：拖标题（留作重放）→ ylim（跟着留下）+ 同轴 y 刻度「固定」不给值（靠落笔刻度）。"""
    client, _figs = aspect_project
    body = _export(client, [_TITLE_DRAG, _YLIM, _YTICKS_FIXED], formats=[])
    assert body["status"] == "done", body
    assert {s["prop"] for s in _script_output(body)["script_report"]["skipped"]} == {
        "pos_frac",
        "ylim",
        "major_mode",
    }


def test_ticks_that_snapshot_stay_with_a_replayed_ylim():
    """坐标范围留作重放时，同轴靠落笔刻度的语句也留下（引擎顺序：范围 -> 刻度模型）。
    定位留作重放的原因这里用「选择器认不出」（不依赖几何键），范围排在它后面所以跟着留下，刻度再跟着留下。"""
    man = {
        "elements": [
            {"gid": "axes_0", "role": "axes", "editable": []},
            {"gid": "axes_0.yticks", "role": "ticks", "editable": []},
            {"gid": "axes_0.artists_0.texts_0", "role": "text", "editable": []},
        ]
    }
    drag = {"gid": "axes_0.artists_0.texts_0", "prop": "pos_frac", "value": [0.5, 0.2]}
    ylim = {"gid": "axes_0", "prop": "ylim", "value": [-3.0, 14.0]}
    fixed = {"gid": "axes_0.yticks", "prop": "major_mode", "value": "fixed"}
    out = scriptadjust.build(b"import os\n", "Fig1", [drag, ylim, fixed], man, version="9.9")
    assert out.written == 0
    assert {p["prop"] for p in out.residual} == {"pos_frac", "ylim", "major_mode"}
    out = scriptadjust.build(b"import os\n", "Fig1", [ylim, drag, fixed], man, version="9.9")
    assert {p["prop"] for p in out.residual} == {"pos_frac"}  # 范围排在定位之前：照写


@real_worker
@pytest.mark.parametrize(
    ("patches", "held"),
    [
        ([_MOVE, _TITLE_DRAG, _YLIM], {"position", "pos_frac", "ylim"}),
        ([_MOVE, _YLIM, _TITLE_DRAG], {"position", "pos_frac"}),
    ],
    ids=["move-drag-ylim", "move-ylim-drag"],
)
def test_replayed_position_keeps_the_order_of_a_title_drag_and_ylim(project, patches, held):
    client, _figs = project
    body = _export(client, patches, formats=[])
    assert body["status"] == "done", body
    assert {s["prop"] for s in _script_output(body)["script_report"]["skipped"]} == held


def test_one_rebuild_per_legend():
    """同一个图例上几条构建期参数只合成一次 `ax.legend(...)`。"""
    man = {
        "elements": [
            {
                "gid": _LEG,
                "role": "legend",
                "editable": [
                    {"prop": "loc", "value": "upper left"},
                    {"prop": "loc_anchor", "value": None},
                    {"prop": "ncol", "value": 1},
                ],
            }
        ]
    }
    edits = [
        {"gid": _LEG, "prop": "ncol", "value": 2},
        {"gid": _LEG, "prop": "borderpad", "value": 1.2},
        {"gid": _LEG, "prop": "fontsize", "value": 7},
    ]
    out = scriptadjust.build(b"import os\n", "Fig1", edits, man, version="9.9")
    assert out.written == 3
    assert out.data.decode().count("_legend_rebuild(_ax(fig, 0)") == 1


def test_legend_with_an_unreadable_anchor_is_not_rebuilt():
    """锚点表达不出来（manifest 不发 `loc_anchor`）时重建会把它弄丢：留给引擎重放。"""
    man = {
        "elements": [
            {"gid": _LEG, "role": "legend", "editable": [{"prop": "loc", "value": "best"}]}
        ]
    }
    edit = {"gid": _LEG, "prop": "ncol", "value": 2}
    assert scriptadjust.build(b"import os\n", "Fig1", [edit], man, version="9.9").residual == [edit]


_XT = "axes_0.xticks"


@real_worker
@pytest.mark.parametrize(
    "patches",
    [
        # 改步长 + 改坐标范围
        [
            {"gid": "axes_0", "prop": "xlim", "value": [-1.0, 7.0]},
            {"gid": _XT, "prop": "major_mode", "value": "step"},
            {"gid": _XT, "prop": "major_step", "value": 0.5},
        ],
        # 切到固定间隔而没给步长：取脚本原样的刻度在**落下之后的视区**里的间距（xlim 放宽到 40，间距是 5 而不是 1），
        # 步长相同的写法验不出先后，这一条才验得出
        [
            {"gid": "axes_0", "prop": "xlim", "value": [-3.0, 40.0]},
            {"gid": _XT, "prop": "major_mode", "value": "step"},
        ],
        # 同理，固定刻度没给值：取放宽之后原样刻度的位置
        [
            {"gid": "axes_0", "prop": "xlim", "value": [0.0, 40.0]},
            {"gid": _XT, "prop": "major_mode", "value": "fixed"},
        ],
        # 单条文字冻结的是**落下之后**的视区里的刻度，序号也按那一组认
        [
            {"gid": "axes_0", "prop": "xlim", "value": [0.0, 40.0]},
            {"gid": "axes_0.xticklabels_2", "prop": "text", "value": "ten"},
        ],
        # 改格式 + 改对数轴：换刻度类型会把 locator / formatter 整套换掉，格式要落在换完之后的那一套上
        [
            {"gid": "axes_0.yticks", "prop": "format", "value": "%.1f"},
            {"gid": "axes_0.yticks", "prop": "minor_visible", "value": True},
            {"gid": "axes_0.yticks", "prop": "minor_format", "value": "%.0f"},
            {"gid": "axes_0", "prop": "yscale", "value": "log"},
        ],
        # 固定刻度之后改单条文字：文字冻结的是定位之后的那组刻度
        [
            {"gid": _XT, "prop": "major_mode", "value": "fixed"},
            {"gid": _XT, "prop": "major_values", "value": [0.0, 2.0, 4.0]},
            {"gid": "axes_0.xticklabels_1", "prop": "text", "value": "two"},
        ],
        # 科学计数与逐档格式：整图扫描只采到每个枚举的第一个不同值，这里补 sci 和次刻度标数字
        [
            {"gid": "axes_0.yticks", "prop": "format", "value": "sci"},
            {"gid": _XT, "prop": "format", "value": "%g"},
            {"gid": _XT, "prop": "minor_visible", "value": True},
            {"gid": _XT, "prop": "minor_format", "value": "%.2f"},
        ],
        # 次刻度：长度、线宽与步长一起
        [
            {"gid": _XT, "prop": "minor_visible", "value": True},
            {"gid": _XT, "prop": "minor_mode", "value": "step"},
            {"gid": _XT, "prop": "minor_step", "value": 0.25},
            {"gid": _XT, "prop": "minor_length", "value": 5.0},
            {"gid": _XT, "prop": "minor_width", "value": 1.2},
        ],
    ],
    ids=[
        "step-and-xlim",
        "step-without-a-step-after-xlim",
        "fixed-without-values-after-xlim",
        "text-after-xlim",
        "format-on-log",
        "fixed-then-text",
        "sci-and-minor-labels",
        "minor-ticks",
    ],
)
def test_tick_edits_combine_like_the_editor(project, patches):
    client, _figs = project
    body = _export(client, patches, formats=[])
    assert body["status"] == "done", body
    assert _script_output(body)["script_report"]["written"] == len(patches)


@pytest.mark.parametrize(
    ("axes_extra", "ax_aspect", "left"),
    [
        ([], "1.0", True),  # 按纵横比在画的时候收缩的轴
        ([{"gid": "axes_0.colorbar", "role": "colorbar", "editable": []}], "auto", True),  # 色条轴
        ([], "auto", False),  # 画之前大小就定了的轴：照写
    ],
    ids=["aspect", "colorbar-axes", "fixed-size"],
)
def test_ticks_read_off_at_write_time_wait_when_the_axes_is_sized_at_draw(
    axes_extra, ax_aspect, left
):
    """轴的大小要画出来才定（`aspect` 不是 auto、色条轴）：落笔时还没画过，自动刻度按「还没定的大小」算，
    与热态画完之后的不同（实测 `InvCbar2` x 轴 6 个 → 5 个，单条文字验证不过）。靠落笔刻度的几条（单条文字、
    固定刻度没给值）与同一条轴上其余的定位 / 格式一起留给重放；不靠落笔刻度的键（线宽、标签显隐）不受牵连。"""
    els = [
        {"gid": "axes_0", "role": "axes", "editable": [{"prop": "aspect", "value": ax_aspect}]},
        {"gid": "axes_0.xticks", "role": "ticks", "editable": []},
        {"gid": "axes_0.xticklabels_1", "role": "ticklabel", "editable": []},
        *axes_extra,
    ]
    text_edit = {"gid": "axes_0.xticklabels_1", "prop": "text", "value": "two"}
    fixed = {"gid": "axes_0.xticks", "prop": "major_mode", "value": "fixed"}
    fmt = {"gid": "axes_0.xticks", "prop": "format", "value": "%.1f"}
    size = {"gid": "axes_0.xticks", "prop": "fontsize", "value": 9}
    out = scriptadjust.build(
        b"import os\n", "Fig1", [text_edit, fixed, fmt, size], {"elements": els}, version="9.9"
    )
    held = {(p["gid"], p["prop"]) for p in out.residual}
    if left:
        assert held == {(e["gid"], e["prop"]) for e in (text_edit, fixed, fmt)}
        assert out.written == 1
    else:
        assert not held and out.written == 4


def test_tick_keys_of_one_axis_make_one_call_in_the_editors_order():
    """同一条轴上的定位与格式合成一次 `_tick_model`；先后：刻度类型 → 坐标范围 → 定位 → 单条文字。"""
    els = [
        {"gid": "axes_0", "role": "axes", "editable": []},
        {"gid": "axes_0.xticks", "role": "ticks", "editable": []},
        {"gid": "axes_0.yticks", "role": "ticks", "editable": []},
        {"gid": "axes_0.xticklabels_1", "role": "ticklabel", "editable": []},
    ]
    edits = [
        {"gid": "axes_0.xticklabels_1", "prop": "text", "value": "two"},
        {"gid": "axes_0.xticks", "prop": "major_step", "value": 2},
        {"gid": "axes_0", "prop": "xlim", "value": [0, 9]},
        {"gid": "axes_0.xticks", "prop": "major_mode", "value": "step"},
        {"gid": "axes_0.xticks", "prop": "format", "value": "%.1f"},
        {"gid": "axes_0.yticks", "prop": "minor_visible", "value": True},
        {"gid": "axes_0", "prop": "xscale", "value": "linear"},
    ]
    out = scriptadjust.build(b"import os\n", "Fig1", edits, {"elements": els}, version="9.9")
    text = out.data.decode()
    assert out.written == len(edits) and not out.residual
    calls = [ln.strip() for ln in text.splitlines() if "_tick_model(_ax(fig" in ln]
    assert len(calls) == 2, calls  # x 一次、y 一次
    assert "'major_mode': 'step', 'major_step': 2.0" in calls[0] and "'format': '%.1f'" in calls[0]
    order = [
        text.index(k)
        for k in (
            "set_xscale('linear')",
            "set_xlim(0, 9)",
            "_tick_model(_ax(fig, 0), 'x'",
            "_tick_text(_ax",
        )
    ]
    assert order == sorted(order), order


@real_worker
@pytest.mark.parametrize(
    "tick_edit",
    [
        {"gid": "axes_0.xticklabels_1", "prop": "text", "value": "two"},
        {"gid": _XT, "prop": "major_mode", "value": "fixed"},
        {"gid": _XT, "prop": "major_mode", "value": "step"},
    ],
    ids=["text", "fixed-without-values", "step-without-a-step"],
)
def test_resized_page_with_view_dependent_ticks_still_delivers(project, tick_edit):
    """改了图幅（留给重放）又改靠落笔时刻度算的几条：不能因为两边各在不同的轴长上算刻度而整份不交付。"""
    client, _figs = project
    resize = {"gid": "figure", "prop": "size_mm", "value": [120.0, 60.0]}
    keep = {
        "gid": "axes_0.yticks",
        "prop": "minor_visible",
        "value": True,
    }  # 另一条轴：照常写进脚本
    body = _export(client, [resize, tick_edit, keep], formats=[])
    assert body["status"] == "done", body
    report = _script_output(body)["script_report"]
    assert report["written"] == 1  # minor_visible 写进脚本，其余留给重放


_RANK_DRIVER = """\
import json, sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure
import overrides, tickmodel

ax = Figure().add_subplot()
items = [
    ("xscale", ax, "axes_0"),
    ("xlim", ax, "axes_0"),
    ("major_step", tickmodel.TickSet(ax, "x"), "axes_0.xticks"),
    ("text", tickmodel.TickLabel(ax, "x", 1), "axes_0.xticklabels_1"),
]
print(json.dumps([overrides._apply_rank(p, a, g) for p, a, g in items]))
"""


def test_label_visibility_of_a_tick_set_the_manifest_dropped_is_still_written():
    """标签全关掉之后这一组量不出几何，manifest 里没有它：按 gid 形状认出是刻度组，两侧一起关（色条 / twinx 的字在右边）。"""
    els = [{"gid": "axes_0", "role": "axes", "editable": []}]
    edits = [
        {"gid": "axes_0.xticks", "prop": "visible", "value": False},
        {"gid": "axes_0.yticks", "prop": "visible", "value": False},
    ]
    out = scriptadjust.build(b"import os\n", "Fig1", edits, {"elements": els}, version="9.9")
    text = out.data.decode()
    assert out.written == 2 and not out.residual
    assert "_TickSet(_ax(fig, 0), 'x').params(labelbottom=False, labeltop=False)" in text
    assert "_TickSet(_ax(fig, 0), 'y').params(labelleft=False, labelright=False)" in text


def test_class_of_a_tick_set_the_manifest_dropped_is_ticks():
    """类别只在 `_cls_of` 推断：刻度组缺席时 `_statement`、档位、去重问的是同一处，刻度定位排在坐标范围之后（Codex P2）。"""
    assert scriptadjust._cls_of("axes_0.xticks", None) == "ticks"
    assert scriptadjust._cls_of("axes_0.title", None) is None
    els = [{"gid": "axes_0", "role": "axes", "editable": []}]
    edits = [
        {"gid": "axes_0.xticks", "prop": "major_mode", "value": "fixed"},
        {"gid": "axes_0", "prop": "xlim", "value": [0, 9]},
    ]
    out = scriptadjust.build(b"import os\n", "Fig1", edits, {"elements": els}, version="9.9")
    text = out.data.decode()
    assert out.written == 2 and text.index("set_xlim") < text.index("_tick_model(_ax(fig, 0)")


@real_worker
def test_frozen_ticks_follow_the_final_view_when_their_group_left_the_manifest(project):
    """先设 fixed 不给值、隐藏全部刻度文字（刻度组量不出几何、从 manifest 里掉了）、再改 xlim、开着网格：
    定位必须排在 xlim 之后，否则按旧范围冻结，网格线对不上（Codex P2）。"""
    client, _figs = project
    patches = [
        {"gid": "axes_0.xticks", "prop": "major_mode", "value": "fixed"},
        {"gid": "axes_0.xticks", "prop": "visible", "value": False},
        {"gid": "axes_0", "prop": "xlim", "value": [-3.0, 9.0]},
        {"gid": "axes_0", "prop": "grid_x", "value": True},
    ]
    body = _export(client, patches, formats=[])
    assert body["status"] == "done", body


@real_worker
def test_script_order_matches_the_engines_apply_order():
    """脚本里的先后与 `overrides._apply_rank` 对账：刻度类型 -> 坐标范围 -> 刻度定位 -> 单条刻度文字。

    引擎那一侧要 matplotlib，父进程 import 不了，在 worker 的解释器里问。
    """
    engine_dir = Path(scriptadjust.__file__).resolve().parent
    out = subprocess.run(
        [WORKER_PY, "-c", _RANK_DRIVER, str(engine_dir)],
        capture_output=True,
        encoding="utf-8",
        timeout=120,
        check=True,
    )
    engine = json.loads(out.stdout.strip().splitlines()[-1])
    script = [
        scriptadjust._rank("xscale", None),
        scriptadjust._rank("xlim", None),
        scriptadjust._rank("major_step", "tick_model"),
        scriptadjust._rank("text", "tick_text"),
    ]
    assert engine == sorted(set(engine)) and script == sorted(set(script)), (engine, script)


def test_ticks_read_off_at_write_time_wait_for_a_resized_page():
    """图幅 / 子图大小留给重放时，靠落笔那一刻的刻度算的几条一起留下：自动刻度的个数跟轴长有关。"""
    els = [
        {"gid": "figure", "role": "figure", "editable": []},
        {"gid": "axes_0.xticks", "role": "ticks", "editable": []},
        {"gid": "axes_0.xticklabels_1", "role": "ticklabel", "editable": []},
    ]
    resize = {"gid": "figure", "prop": "size_mm", "value": [120, 60]}
    text = {"gid": "axes_0.xticklabels_1", "prop": "text", "value": "two"}
    # x 轴：固定刻度没给值（取落笔时的刻度）；同一轴上的格式跟着一起留下
    fixed = [
        {"gid": "axes_0.xticks", "prop": "major_mode", "value": "fixed"},
        {"gid": "axes_0.xticks", "prop": "format", "value": "%.1f"},
    ]
    # y 轴：值都给了，不依赖落笔时的刻度
    given = [
        {"gid": "axes_0.yticks", "prop": "major_mode", "value": "step"},
        {"gid": "axes_0.yticks", "prop": "major_step", "value": 2},
    ]
    els.append({"gid": "axes_0.yticks", "role": "ticks", "editable": []})

    def run(*edits):
        return scriptadjust.build(
            b"import os\n", "Fig1", list(edits), {"elements": els}, version="9.9"
        )

    out = run(resize, text, *fixed, *given)
    assert out.residual == [resize, text, *fixed] and out.written == 2
    out = run(text, *fixed)  # 没改大小：照常写进脚本
    assert not out.residual and out.written == 3
    out = run(resize, *given)
    assert out.residual == [resize] and out.written == 2


def test_tick_width_and_label_text_on_a_3d_axes_stay_with_the_replay():
    """3D 轴：刻度线宽每次 draw 都被私有的 `_axinfo` 盖回，公开 API 留不住；单条刻度文字在曲面图上
    两边标签条数对不上（实测 Inv3DS 的 y 轴：热态 5 条、脚本重放 9 条）。都不写。"""
    els = [
        {"gid": "axes_0", "role": "axes3d", "editable": []},
        {"gid": "axes_0.xticks", "role": "ticks", "editable": []},
        {"gid": "axes_0.xticklabels_1", "role": "ticklabel", "editable": []},
    ]
    edits = [
        {"gid": "axes_0.xticks", "prop": "width", "value": 2.0},
        {"gid": "axes_0.xticklabels_1", "prop": "text", "value": "two"},
    ]
    out = scriptadjust.build(b"import os\n", "Fig1", edits, {"elements": els}, version="9.9")
    assert out.residual == edits


@real_worker
def test_inverted_axes_replay_identically(project):
    client, _figs = project
    body = _export(client, [{"gid": "axes_0", "prop": "invert_y", "value": True}], formats=[])
    assert body["status"] == "done", body


@real_worker
def test_script_only_export(project):
    client, _figs = project
    body = _export(client, EDITS[:1], formats=[])
    assert body["status"] == "done", body
    assert [o["format"] for o in body["outputs"]] == [exportjob.SCRIPT_KEY]


@real_worker
def test_unedited_figure_exports_a_plain_copy(project):
    client, figs = project
    body = _export(client, [])
    out = _script_output(body)
    assert out["script_report"]["written"] == 0
    assert (Path(body["export_dir"]) / out["name"]).read_bytes() == (figs / "fig1.py").read_bytes()


@real_worker
def test_unwritable_edit_is_listed_and_the_rest_still_ships(project):
    client, _figs = project
    aspect = {"gid": "axes_0", "prop": "aspect", "value": "equal"}
    body = _export(client, [*EDITS[:1], aspect])
    assert body["status"] == "done", body
    report = _script_output(body)["script_report"]
    assert report["written"] == 1
    assert [(s["gid"], s["prop"]) for s in report["skipped"]] == [("axes_0", "aspect")]


@real_worker
def test_a_wrong_translation_never_ships(project, monkeypatch):
    """反证验证是活的：把翻译改错，脚本不交付，PDF 照常交付。"""
    client, _figs = project
    monkeypatch.setitem(scriptadjust.WRITES, ("line", "color"), "{o}.set_color('#00ff00')")
    body = _export(client, EDITS[:1])
    assert body["status"] == "partial", body
    out = _script_output(body)
    assert out["status"] == "failed" and out["error"]["code"] == "script_export_diverged"
    assert not (Path(body["export_dir"]) / "Fig1_edited.py").exists()
    assert next(o for o in body["outputs"] if o["format"] == "pdf")["status"] == "done"


SHOW_ONLY = """\
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

for k in range(2):
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([0, 1, 2], [k, 2, 1], label="signal")
    ax.set_title("Raw")
plt.show()
"""


def _run_exported(path: Path, tmp_path: Path, trailer: str) -> str:
    run_dir = tmp_path / "run-show"
    run_dir.mkdir()
    (run_dir / "edited.py").write_text(path.read_text(encoding="utf-8") + trailer, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    out = subprocess.run(
        [WORKER_PY, "edited.py"],
        cwd=run_dir,
        check=True,
        env=env,
        capture_output=True,
        encoding="utf-8",
    )
    return out.stdout


def _hot(worker, stem, patches):
    from tavotto import app

    return app._hot_manifest(worker, stem, patches)


@real_worker
@pytest.mark.parametrize("gid", ["axes_0.collections_0", "axes_1.collections_0"], ids=["a", "b"])
def test_shared_norm_meshes_keep_their_colour_map_for_the_replay(tmp_path, gid):
    """共用同一份 norm 的网格是一个色阶（`scale_gids`）：换色图公开 API 写不出「谁是兄弟」，与色条一侧同一条规矩，留作重放。"""
    worker, name = _library_worker(tmp_path, "inv")
    try:
        patches = [{"gid": gid, "prop": "cmap", "value": "plasma"}]
        worker.override("InvShared", patches)
        hot = _hot(worker, "InvShared", patches)
        adjusted = scriptadjust.build(
            (tmp_path / name).read_bytes(), "InvShared", patches, hot, version="9.9"
        )
        worker.override("InvShared", [])
        assert adjusted.written == 0 and adjusted.residual == patches
    finally:
        pool.discard(worker)


def _line_el(i: int, label: str) -> dict:
    return {
        "gid": f"axes_0.lines_{i}",
        "role": "line",
        "label": label,
        "editable": [{"prop": "label", "value": label, "value_original": label}],
    }


def test_chained_renames_are_one_statement():
    """A→B 与 B→C 分成两条语句时，第二条按原 label「B」找会撞上刚改好的那条（Codex P1）：合成一条，先认全再改。"""
    man = {"elements": [_line_el(0, "A"), _line_el(1, "B")]}
    patches = [
        {"gid": "axes_0.lines_0", "prop": "label", "value": "B"},
        {"gid": "axes_0.lines_1", "prop": "label", "value": "C"},
    ]
    out = scriptadjust.build(b"import os\n", "Fig1", patches, man, version="9.9")
    text = out.data.decode()
    assert out.written == 2 and "def _tavotto_0(" in text and "def _tavotto_1(" not in text
    assert "_rename(fig, [" in text


TWO_LINES_SCRIPT = """\
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot([0, 1, 2], [0, 1, 0], label="A")
    ax.plot([0, 1, 2], [1, 0, 1], label="B")
    ax.set_title("T")
    fig.savefig("Fig1.pdf")


if __name__ == "__main__":
    main()
"""


@pytest.fixture
def two_lines_project(tmp_path, monkeypatch):
    m, figs = _open_project(tmp_path, monkeypatch, TWO_LINES_SCRIPT)
    try:
        yield m.app.test_client(), figs
    finally:
        _close_project(m, figs)


@real_worker
def test_chained_renames_land_on_the_right_lines(two_lines_project, tmp_path):
    client, _figs = two_lines_project
    patches = [
        {"gid": "axes_0.lines_0", "prop": "label", "value": "B"},
        {"gid": "axes_0.lines_1", "prop": "label", "value": "C"},
    ]
    body = _export(client, patches, formats=[])
    assert body["status"] == "done", body
    out = Path(body["export_dir"]) / "Fig1_edited.py"
    trailer = (
        "\nimport matplotlib.pyplot as plt\n"
        "print([ln.get_label() for ln in plt.gcf().axes[0].lines])\n"
    )
    assert _run_exported(out, tmp_path, trailer).strip() == "['B', 'C']"


@real_worker
def test_user_text_that_looks_like_generated_code_changes_nothing(two_lines_project, tmp_path):
    """用户写的文字（改名、标题）里带着生成语句的片段（`.set_label(`、`_legend_rebuild(`）：
    不许从语句文本里切东西、也不许按语句文本判档（Codex P2）。"""
    client, _figs = two_lines_project
    name = "B.set_label(C"
    title = "x _legend_rebuild( y .set_label( z"
    patches = [
        {"gid": "axes_0.lines_0", "prop": "label", "value": name},
        {"gid": "axes_0.lines_1", "prop": "label", "value": "_tick_model("},
        {"gid": "axes_0.title", "prop": "text", "value": title},
    ]
    body = _export(client, patches, formats=[])
    assert body["status"] == "done", body
    assert _script_output(body)["script_report"]["written"] == 3
    out = Path(body["export_dir"]) / "Fig1_edited.py"
    trailer = (
        "\nimport matplotlib.pyplot as plt\n"
        "ax = plt.gcf().axes[0]\n"
        "print([ln.get_label() for ln in ax.lines], ax.get_title())\n"
    )
    assert _run_exported(out, tmp_path, trailer).strip() == f"{[name, '_tick_model(']} {title}"


@real_worker
def test_rename_of_a_line_with_a_replayed_drag_is_left_for_the_replay(two_lines_project):
    """线有 label 又被拖动（`pos_frac` 留作重放、带改名前的 `identity`）：改名写进脚本后重放认不出这条线，
    引擎拒绝拖动、整份不交付（Codex P2）。改名一起留作重放。"""
    import hashlib

    client, _figs = two_lines_project
    identity = "l1:" + hashlib.sha256(b"A").hexdigest()[:16]
    patches = [
        {"gid": "axes_0.lines_0", "prop": "label", "value": "renamed", "identity": identity},
        {"gid": "axes_0.lines_0", "prop": "pos_frac", "value": [0.4, 0.4], "identity": identity},
        {"gid": "axes_0.lines_1", "prop": "label", "value": "other"},
    ]
    body = _export(client, patches, formats=[])
    assert body["status"] == "done", body
    report = _script_output(body)["script_report"]
    assert ("axes_0.lines_0", "label") in {(i["gid"], i["prop"]) for i in report["skipped"]}
    assert report["written"] == 1  # 另一条线的改名照写


@real_worker
def test_renames_reach_the_objects(tmp_path):
    """`label` 在整图扫描里是隐形的，这里直接跑导出的脚本、读各对象最后的 label。

    同一条曲线上改名带改色：曲线靠原 label 认（`_line`），改名排在所有语句之后，颜色才落得上。
    """
    from tavotto import app

    worker, name = _library_worker(tmp_path, "inv")
    try:
        src = (tmp_path / name).read_bytes()
        for stem, patches in (
            (
                "InvMix",
                [
                    {"gid": "axes_0.lines_0", "prop": "label", "value": "new-line"},
                    {"gid": "axes_0.lines_0", "prop": "color", "value": "#d62728"},
                    {"gid": "axes_0.scatter_0", "prop": "label", "value": "new-scatter"},
                    {"gid": "axes_0.scatter_1", "prop": "label", "value": "new-mapped"},
                ],
            ),
            (
                "InvCont",
                [
                    {"gid": "axes_0.stemseries_0", "prop": "label", "value": "new-stems"},
                    {"gid": "axes_0.barseries_1", "prop": "label", "value": "new-bars"},
                ],
            ),
        ):
            assert not (worker.override(stem, patches).get("warnings") or [])
            hot = app._hot_manifest(worker, stem, patches)
            worker.override(stem, [])
            adjusted = scriptadjust.build(src, stem, patches, hot, version="9.9")
            assert adjusted.written == len(patches) and not adjusted.residual
            src = adjusted.data  # 第二张图在第一张的块之后再追加一块
    finally:
        pool.discard(worker)
    edited = tmp_path / "edited_labels.py"
    edited.write_bytes(src)
    trailer = (
        "\nmain()\nimport matplotlib.pyplot as plt\n"
        "mix, cont = (plt.figure(n).axes[0] for n in plt.get_fignums()[:2])\n"
        "print(mix.lines[0].get_label(), mix.lines[0].get_color(), mix.collections[0].get_label(),"
        " mix.collections[1].get_label(), cont.containers[0].get_label(),"
        " cont.containers[1].get_label())\n"
    )
    got = _run_exported(edited, tmp_path, trailer).split()
    assert got == ["new-line", "#d62728", "new-scatter", "new-mapped", "new-stems", "new-bars"]


@real_worker
@pytest.mark.parametrize("which", [0, 1], ids=["first", "second"])
def test_show_only_figure_is_edited_at_plt_show(tmp_path, which):
    """没有 savefig 的图：块挂在 `plt.show` 上，按引擎给 stem 的顺序认图，改的是对的那一张。"""
    from tavotto import app

    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "only.py").write_text(SHOW_ONLY, encoding="utf-8")
    worker = pool.one_shot("only.py", str(figs), "__main__")
    stem = "only" if which == 0 else "only-2"
    patches = [{"gid": "axes_0.lines_0", "prop": "color", "value": "#d6278f"}]
    try:
        worker.override(stem, patches)
        assert app._shown_index(worker, stem) == which
        hot = app._hot_manifest(worker, stem, patches)
        src = (figs / "only.py").read_bytes()
        adjusted = scriptadjust.build(src, stem, patches, hot, version="9.9", shown_index=which)
        assert adjusted.written == 1
        stage = tmp_path / "stage"
        stage.mkdir()
        target = stage / "edited.py"
        target.write_bytes(adjusted.data)
        assert app._verify_edited_script(worker, stem, target, adjusted, hot, stage) == []
    finally:
        pool.discard(worker)
    out = _run_exported(
        target,
        tmp_path,
        "\nfor n in plt.get_fignums():\n"
        "    print(matplotlib.colors.to_hex(plt.figure(n).axes[0].lines[0].get_color()))\n",
    )
    colors = out.split()
    assert colors[which] == "#d6278f"
    assert colors[1 - which] != "#d6278f", "另一张图不该被改"


@real_worker
def test_second_export_of_a_show_only_script_applies_both_blocks(tmp_path):
    """Codex #1790 P2：两段块登记同一个 stem，`plt.show` 时第二段不能被第一段的防重入挡掉。"""
    src = SHOW_ONLY.replace("range(2)", "range(1)").encode()
    man = {
        "elements": [
            {"gid": "axes_0.title", "role": "title", "label": "标题", "editable": []},
        ]
    }
    color = {"gid": "axes_0.title", "prop": "color", "value": "#123456"}
    size = {"gid": "axes_0.title", "prop": "fontsize", "value": 21}
    first = scriptadjust.build(src, "only", [color], man, version="9.9", shown_index=0).data
    second = scriptadjust.build(first, "only", [size], man, version="9.9", shown_index=0).data
    path = tmp_path / "edited.py"
    path.write_bytes(second)
    out = _run_exported(
        path,
        tmp_path,
        "\nt = plt.gcf().axes[0].title\n"
        "print(matplotlib.colors.to_hex(t.get_color()), t.get_fontsize())\n",
    )
    assert out.split() == ["#123456", "21.0"]


def test_show_hook_is_only_in_blocks_that_need_it():
    assert "_watch_show" not in _build(b"import os\n").data.decode()
    assert "matplotlib.pyplot" not in _build(b"import os\n").data.decode()
    data = scriptadjust.build(b"import os\n", "Fig1", [COLOR], MAN, version="9.9", shown_index=1)
    assert "_watch_show(0, {1: 'Fig1'})" in data.data.decode()


@real_worker
def test_figure_neither_saved_nor_shown_says_why(project):
    client, figs = project
    (figs / "fig1.py").write_text(
        SCRIPT.replace('fig.savefig("Fig1.pdf")', "pass"), encoding="utf-8"
    )
    pool.shutdown_all(figures_dir=str(figs), wait=True)
    body = _export(client, EDITS[:1], formats=[])
    assert _script_output(body)["error"]["code"] in (
        "script_export_no_savefig",
        "script_export_run_failed",
    )


@real_worker
def test_export_never_overwrites_the_source_script(project, monkeypatch):
    """Codex #1790 P1：脚本本身就叫 `Fig1_edited.py`、导出目录就是脚本目录时，覆盖不许落到它头上。"""
    from tavotto import app as m

    client, figs = project
    source = figs / "fig1.py"
    monkeypatch.setattr(m, "project_export_dir", lambda ctx=None: figs)
    alias = figs / "Fig1_edited.py"
    before = source.read_bytes()
    os.link(source, alias)  # 硬链接：同一个文件换了名字，字符串比路径比不出来
    try:
        body = _export(client, EDITS[:1], formats=[])
    finally:
        keep = alias.read_bytes()
        alias.unlink()
    assert _script_output(body)["error"]["code"] == "script_export_same_file"
    assert keep == before and source.read_bytes() == before


@real_worker
def test_lost_pixel_baseline_is_not_a_pass(project, monkeypatch):
    """Codex #1790 P1：热会话中途重开时像素没有比过，「没比」不算过。"""
    from tavotto import app as m

    client, _figs = project
    monkeypatch.setattr(m, "_replay_pixel_diff", lambda *a: (None, "hot_rebuilt"))
    body = _export(client, EDITS[:1], formats=[])
    assert _script_output(body)["error"]["code"] == "script_export_diverged"


@real_worker
def test_existing_file_goes_through_the_overwrite_policy(project):
    client, _figs = project
    first = _export(client, EDITS[:1], formats=[])
    export_dir = Path(first["export_dir"])
    body = _export(client, EDITS[:1], formats=[], overwrite="ask")
    assert body["status"] == "conflict" and body["conflicts"] == ["Fig1_edited.py"]
    body = _export(client, EDITS[:1], formats=[], overwrite="rename")
    assert _script_output(body)["name"] == "Fig1 (2)_edited.py"
    assert (export_dir / "Fig1 (2)_edited.py").is_file()
