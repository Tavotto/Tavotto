"""导出里的「修改后的 .py」（ADR 0171）。

判据的主语分三段：

* 生成器（纯字节）：块外一个字节不动，编码 / 换行 / BOM / shebang 都不坏；
* 导出链路（真 worker + 真 `/api/export`）：原脚本逐字节不变，项目里不冒新文件，验证过不了就不交付，
  图片格式不受牵连；
* **用户拿到的那个文件**：不带 Tavotto 的纯 matplotlib 子进程跑它，画出来的 PDF 与导出的 PDF 逐像素一致。
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

from support.invariant_figures import (
    ENTRY as INV_ENTRY,
    LIBRARY as INV_LIBRARY,
    SCRIPT_NAME as INV_SCRIPT,
    STEMS,
)
from support.overridesample import _ENABLERS, _SAMPLE_OVERRIDE, _sample_value
from support.scriptadjust_figures import (
    ENTRY as ADJ_ENTRY,
    LIBRARY as ADJ_LIBRARY,
    SCRIPT_NAME as ADJ_SCRIPT,
    STEMS as ADJ_STEMS,
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

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

real_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

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


#: `visible` 关掉之后别的字段量不到；翻转坐标轴 + 改图像 + 改色条标签 va 三者同时才会偏（真出现时是
#: `script_export_diverged`，不交付），翻转单独一条用例（`test_inverted_axes_replay_identically`）。
#: 对数轴 + 改刻度字体同理：`tick_params` 没有公式字体的键，`10^4` 这种标签的字体只盖得住已有的那几条。
_NOT_IN_THE_SWEEP = {"visible", "invert_x", "invert_y", "xscale", "yscale"}
#: 3D 图逐字段单独都等价，所有字段一次全改才超像素阈值（max 22 / 255），同样由验证拦下。
_FLAT_STEMS = [s for s in STEMS if not s.startswith("Inv3D")]

#: 扫描的图：(图库, stem)。`invariant_figures` 是全套不变式共用的那份（不往里加图，别的用例写死了编号），
#: 导出脚本自己要的图（箭头、带框文字、多列图例……）加在 `scriptadjust_figures` 里。
_LIBRARIES = {
    "inv": (INV_LIBRARY, INV_SCRIPT, INV_ENTRY),
    "adj": (ADJ_LIBRARY, ADJ_SCRIPT, ADJ_ENTRY),
}
_SWEEP = [("inv", s) for s in _FLAT_STEMS] + [("adj", s) for s in ADJ_STEMS]


#: 结构化字段（pair / order / number_list）`_sample_value` 挑不出值，在这里按 prop 给一个明显不同的值。
#: 写进 `WRITES` 的结构化键要么在这里有采样，要么在 `_DEDICATED` 里点名专用用例。
def _widen(field):
    lo, hi = (float(v) for v in field["value"])
    return [lo - (hi - lo) * 0.25, hi + (hi - lo) * 0.1]


_STRUCTURED: dict[str, object] = {
    "xlim": _widen,
    "ylim": _widen,
    # 锚点：图例的右上角钉在子图里的一个点上（预设 loc 是 upper right / left，两种都看得出挪动）
    "loc_anchor": [0.8, 0.7],
    # 网格透明度：默认 1.0，`_sample_value` 只挑 0.9，细网格线上低于像素阈值，看不出写没写
    "grid_alpha": 0.25,
    # 线型默认 "-" 与 `_sample_value` 挑的第一个选项相同，给一个看得出的
    "grid_linestyle": "--",
}

#: **颜色采样不能全用同一个值**：`_sample_value` 对每个颜色字段都给 `#123456`，于是图底、子图底和所有前景
#: 一个颜色，前景色译错了（甚至写死成一个常量）像素比对也看不出来。底色给两个浅色，前景按序号轮一组
#: 彼此分得开的饱和色，并且避开字段当前值。
_BACKGROUND = {("figure", "facecolor"): "#f6f2dc", ("axes", "facecolor"): "#e6eefb"}
_FOREGROUND = (
    "#d62728",
    "#1f77b4",
    "#2ca02c",
    "#9467bd",
    "#ff7f0e",
    "#17becf",
    "#e377c2",
    "#8c564b",
)


def _sample_color(key, field, k: int) -> str:
    if key in _BACKGROUND:
        return _BACKGROUND[key]
    cur = str(field.get("value")).lower()
    for i in range(len(_FOREGROUND)):
        c = _FOREGROUND[(k + i) % len(_FOREGROUND)]
        if c != cur:
            return c
    return "#000000"


#: 不进整图扫描的键（`_NOT_IN_THE_SWEEP`），按组单独扫：同组一起改，组与组之间不叠。
#: 坐标范围（pair）放进整图扫描会被 scale 的副作用搅乱，所以也自成一组。
_HELD_OUT = {
    "visible": {"visible"},
    "scale": {"xscale", "yscale"},
    "lim": {"xlim", "ylim"},
    # 边框颜色 / 线宽：整图扫描里四条边同时被关掉、画不出来，所以单独扫；「全部」与四边各自分开两组，
    # 否则四边各自的设定把「全部」整个盖住，广播那条等于没验
    "spine_all": {"spine_color", "spine_linewidth"},
    "spine_sides": {
        f"spine_{s}_{k}" for s in ("top", "right", "bottom", "left") for k in ("color", "linewidth")
    },
}
_HELD_OUT_PROPS = set().union(*_HELD_OUT.values())


def _every_other(field):
    """固定刻度：现有主刻度隔一个取一个（视区内的位置，少几条、看得出变了）。"""
    vals = [float(v) for v in field["value"]]
    return vals[::2] if len(vals) > 2 else None


#: 按 **(cls, prop)** 划的组，整图扫描里放不下：值是这一组里的采样值（None = 照常采，函数吃 manifest 字段）。
#: - 单条刻度文字冻结整条轴，序号按定位**之后**的刻度认——与 `major_*` 同扫，序号会越界；
#: - `major_values` 只在 `major_mode=fixed` 时生效，整图扫描里 mode 采到的是 step，它在那里是空转。
_HELD_OUT_KEYED: dict[str, dict[tuple[str, str], object]] = {
    "tick_text": {("ticklabel", "text"): None},
    "tick_fixed": {("ticks", "major_mode"): "fixed", ("ticks", "major_values"): _every_other},
}
_HELD_OUT_KEYS = {("ticklabel", "text"), ("ticks", "major_values")}
_GROUPS: dict[str, tuple[set[str] | None, dict | None]] = {
    **{g: (props, None) for g, props in _HELD_OUT.items()},
    **{g: (None, keyed) for g, keyed in _HELD_OUT_KEYED.items()},
}
#: 整图扫描里采样值要换的 prop：次刻度格式默认的下一档是 auto（= 脚本原样，空转），换成真改格式的那一档；
#: 次刻度显隐钉成 True：脚本原样已有次刻度的图上「取反」会把它关掉，随后的次刻度模式 / 步长就是空转。
_LOCAL_SAMPLES = {"minor_format": "%.1f", "minor_visible": True}

#: 扫描采不到（或刻意不进扫描）的写得进脚本的键 → 证明它等价的专用用例名。
#: 覆盖断言（`test_every_writable_key_is_swept_or_named`）要求 `WRITES` 的每个键二者居一。
_DEDICATED: dict[tuple[str, str], str] = {
    ("text", "pos_frac"): "test_moves_on_a_tight_cropped_figure_land_where_the_editor_shows",
    ("legend", "loc_frac"): "test_moves_on_a_tight_cropped_figure_land_where_the_editor_shows",
    ("text", "fontfamily"): "test_font_edits_replay_identically",
    ("legend_text", "fontfamily"): "test_font_edits_replay_identically",
    ("ticks", "fontfamily"): "test_font_edits_replay_identically",
    ("axes", "invert_x"): "test_inverted_axes_replay_identically",
    ("axes", "invert_y"): "test_inverted_axes_replay_identically",
}


def _sweep_patches(
    man: dict,
    only: set[tuple[str, str]] | None = None,
    props: set[str] | None = None,
    keyed: dict | None = None,
):
    """(patches, 采到的 (cls, prop))：图上每个写得进脚本的字段挑一个不同的值，带上它的使能项。

    `only` 只采这几个键（定位用：`TAVOTTO_SWEEP_ONLY=legend.ncol,axes.grid_color`）；
    `props` 给了就只采这些 prop（`_HELD_OUT` 的一组），`keyed` 给了就只采这些 (cls, prop)
    并用它给的采样值（`_HELD_OUT_KEYED` 的一组），都不给就采整图扫描的全部。
    """
    patches, covered = [], set()
    for el in man["elements"]:
        cls = scriptadjust._ROLE_CLS.get(el.get("role"))
        advertised = {f["prop"] for f in el.get("editable") or []}
        for field in el.get("editable") or []:
            key = (cls, field["prop"])
            if key not in scriptadjust.WRITES:
                continue
            if keyed is not None:
                if key not in keyed:
                    continue
            elif props is None:
                if field["prop"] in (_NOT_IN_THE_SWEEP | _HELD_OUT_PROPS) or key in _HELD_OUT_KEYS:
                    continue
            elif field["prop"] not in props:
                continue
            if only is not None and key not in only:
                continue
            prop = field["prop"]
            if keyed is not None and keyed[key] is not None:
                value = keyed[key]
                value = value(field) if callable(value) else value
            elif prop in _STRUCTURED:
                value = _STRUCTURED[prop]
                value = value(field) if callable(value) else value
            elif field.get("type") == "color":
                value = _sample_color(key, field, len(patches))
            else:
                value = _LOCAL_SAMPLES.get(prop, _SAMPLE_OVERRIDE.get(prop, _sample_value(field)))
            if value is None:
                continue
            for ep, ev in _ENABLERS.get(prop, ()):
                if ep != prop and ep in advertised:
                    patches.append({"gid": el["gid"], "prop": ep, "value": ev})
            patches.append({"gid": el["gid"], "prop": prop, "value": value})
            covered.add(key)
    return patches, covered


def _sweep_only() -> set[tuple[str, str]] | None:
    raw = os.environ.get("TAVOTTO_SWEEP_ONLY", "").strip()
    if not raw:
        return None
    return {tuple(k.strip().split(".", 1)) for k in raw.split(",") if k.strip()}


def _library_worker(tmp_path: Path, lib: str):
    text, name, entry = _LIBRARIES[lib]
    (tmp_path / name).write_text(text, encoding="utf-8")
    worker = pool.one_shot(name, str(tmp_path), entry)
    worker.ensure_built()
    return worker, name


def _replays_identically(worker, name: str, tmp_path: Path, stem: str, patches: list[dict]):
    from tavotto import app

    assert not (worker.override(stem, patches).get("warnings") or [])
    hot = app._hot_manifest(worker, stem, patches)
    src = (tmp_path / name).read_bytes()
    adjusted = scriptadjust.build(src, stem, patches, hot, version="9.9")
    assert adjusted.written
    stage = tmp_path / f"_stage_{stem}"
    stage.mkdir()
    target = stage / "edited.py"
    target.write_bytes(adjusted.data)
    assert app._verify_edited_script(worker, stem, target, adjusted, hot, stage) == [], stem
    worker.override(stem, [])


@real_worker
@pytest.mark.parametrize(("lib", "stem"), _SWEEP, ids=[f"{lib}-{s}" for lib, s in _SWEEP])
def test_every_writable_field_replays_identically(tmp_path, lib, stem):
    """夹具里每张图上所有写得进脚本的字段**一起**改，新脚本重放必须与热态几何 + 像素一致。"""
    worker, name = _library_worker(tmp_path, lib)
    try:
        man = worker.override(stem, [])["manifest"]
        patches, _covered = _sweep_patches(man, _sweep_only())
        if not patches:
            pytest.skip("这张图上没有写得进脚本的字段")
        _replays_identically(worker, name, tmp_path, stem, patches)
    finally:
        pool.discard(worker)


#: 对数轴要严格为正的数据，那张图不在整图扫描里（其余几张的 y 轴跨 0，log 会被夹住）。
_HELD_OUT_STEMS = {"scale": [("inv", "InvScale")]}


@real_worker
@pytest.mark.parametrize("group", sorted(_GROUPS))
def test_held_out_fields_replay_identically(tmp_path, group):
    """整图扫描里刻意不放的键（`_HELD_OUT` / `_HELD_OUT_KEYED`）：每张图上这一组一起改，照样要等价。"""
    props, keyed = _GROUPS[group]
    seen = False
    for lib in _LIBRARIES:
        stems = [s for lb, s in _SWEEP + _HELD_OUT_STEMS.get(group, []) if lb == lib]
        if not stems:
            continue
        sub = tmp_path / lib
        sub.mkdir()
        worker, name = _library_worker(sub, lib)
        try:
            for stem in stems:
                man = worker.override(stem, [])["manifest"]
                patches, _covered = _sweep_patches(man, _sweep_only(), props, keyed)
                if patches:
                    seen = True
                    _replays_identically(worker, name, sub, stem, patches)
        finally:
            pool.discard(worker)
    assert seen, f"{group} 这一组在任何图上都没采到"


def _written_keys(
    worker, script: Path, stem: str, man: dict, props, keyed=None
) -> set[tuple[str, str]]:
    """这张图上采到、而且**真写进了脚本**的键（被 `_Unsupported` 挡下留作重放的不算验证过）。"""
    from tavotto import app

    patches, _covered = _sweep_patches(man, props=props, keyed=keyed)
    if not patches:
        return set()
    worker.override(stem, patches)
    hot = app._hot_manifest(worker, stem, patches)
    worker.override(stem, [])
    assert hot is not None, stem
    adjusted = scriptadjust.build(script.read_bytes(), stem, patches, hot, version="9.9")
    residual = {(p["gid"], p["prop"]) for p in adjusted.residual}
    roles = {e["gid"]: e.get("role") for e in man["elements"]}
    return {
        (scriptadjust._ROLE_CLS.get(roles.get(p["gid"])), p["prop"])
        for p in patches
        if (p["gid"], p["prop"]) not in residual
    }


@real_worker
def test_every_writable_key_is_swept_or_named(tmp_path):
    """`WRITES` 的每个键都在某张图上真被采样验证过，或者点名了证明它的专用用例。

    没有这一条，进了 `WRITES` 却在任何夹具上都采不到值的键（结构化字段、夹具里没有的元素）
    会被扫描静默放过，「逐键验证过」就是一句空话。
    """
    covered: set[tuple[str, str]] = set()
    for lib in _LIBRARIES:
        stems = {s for lb, s in _SWEEP + _HELD_OUT_STEMS["scale"] if lb == lib}
        sub = tmp_path / lib
        sub.mkdir()
        worker, _name = _library_worker(sub, lib)
        try:
            for stem in stems:
                man = worker.override(stem, [])["manifest"]
                for props, keyed in [(None, None), *_GROUPS.values()]:
                    covered |= _written_keys(worker, sub / _name, stem, man, props, keyed)
        finally:
            pool.discard(worker)
    missing = set(scriptadjust.WRITES) - covered - set(_DEDICATED)
    assert not missing, f"写进了脚本、却既没被扫描采到也没点名专用用例：{sorted(missing)}"
    for key, test in _DEDICATED.items():
        assert callable(globals().get(test)), f"{key} 点名的用例 {test} 不存在"
        assert key in scriptadjust.WRITES, f"{key} 已不在 WRITES，从 _DEDICATED 删掉"


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


@pytest.fixture
def project(tmp_path, monkeypatch):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig1.py").write_text(SCRIPT, encoding="utf-8")
    (figs / "points.csv").write_text("0,9,2,10,4,7.5", encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(REGISTRY, encoding="utf-8")
    subprocess.run([WORKER_PY, "fig1.py"], cwd=figs, check=True)
    m.open_project(str(figs))
    exportjob.reset_for_tests()
    try:
        yield m.app.test_client(), figs
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()


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
    """标签全关掉之后这一组量不出几何，manifest 里没有它：按 gid 形状认出是刻度组，x 管下边、y 管左边。"""
    els = [{"gid": "axes_0", "role": "axes", "editable": []}]
    edits = [
        {"gid": "axes_0.xticks", "prop": "visible", "value": False},
        {"gid": "axes_0.yticks", "prop": "visible", "value": False},
    ]
    out = scriptadjust.build(b"import os\n", "Fig1", edits, {"elements": els}, version="9.9")
    text = out.data.decode()
    assert out.written == 2 and not out.residual
    assert "_TickSet(_ax(fig, 0), 'x').params(labelbottom=False)" in text
    assert "_TickSet(_ax(fig, 0), 'y').params(labelleft=False)" in text


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
        scriptadjust._rank("xscale", "_ax(fig, 0).set_xscale('log')"),
        scriptadjust._rank("xlim", "_ax(fig, 0).set_xlim(1, 9)"),
        scriptadjust._rank("major_step", "_tick_model(_ax(fig, 0), 'x', {})"),
        scriptadjust._rank("text", "_tick_text(_ax(fig, 0), 'x', 1, 'a')"),
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
