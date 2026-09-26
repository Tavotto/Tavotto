"""ADR 0094 spike：把一组 override 翻译成「Tavotto 调整」代码块，插进脚本。

这是**设计验证用的原型**，不是产品代码：它只覆盖 spike 挑的几类 override，用来量
推荐方案（方案 A：savefig 钩子块）的覆盖率与失败形态。产品实现见 ADR 0094 §实施计划。

生成的块只依赖 matplotlib 公开 API + 标准库——写回后的脚本脱离 Tavotto 也能跑
（用户的「可复现运行包」要能交给期刊 / 合作者）。

用法（库）：
    block, report = build_block(stem_patches, manifests, meta)
    new_bytes = insert_block(old_bytes, block)
"""

from __future__ import annotations

import ast
import codecs
import io
import re
import tokenize

BEGIN = "# >>> Tavotto 调整 >>>"
END = "# <<< Tavotto 调整 <<<"
BEGIN_ASCII = "# >>> Tavotto adjustments >>>"
END_ASCII = "# <<< Tavotto adjustments <<<"

# ---------------------------------------------------------------------------
# 可写回的 (role, prop) 表：每一格是「这条 override 翻译成哪几行公开 API」。
# 不在表里的 = 这一版不写回，进报告，绝不猜。
# ---------------------------------------------------------------------------

_TEXT_ROLES = ("text", "title", "axis_label", "legend_text")

SIMPLE_SETTERS: dict[tuple[str, str], str] = {}
for _r in _TEXT_ROLES:
    SIMPLE_SETTERS.update(
        {
            (_r, "fontsize"): "{o}.set_fontsize({v})",
            (_r, "color"): "{o}.set_color({v})",
            (_r, "weight"): "{o}.set_fontweight({v})",
            (_r, "style"): "{o}.set_fontstyle({v})",
            (_r, "rotation"): "{o}.set_rotation({v})",
            (_r, "alpha"): "{o}.set_alpha({v})",
            (_r, "visible"): "{o}.set_visible({v})",
            (_r, "text"): "{o}.set_text({v})",
        }
    )
SIMPLE_SETTERS.update(
    {
        ("line", "color"): "_with_legend({o}, lambda _a: _a.set_color({v}))",
        ("line", "linewidth"): "_with_legend({o}, lambda _a: _a.set_linewidth({v}))",
        ("line", "alpha"): "{o}.set_alpha({v})",
        ("line", "visible"): "{o}.set_visible({v})",
        ("line", "markersize"): "{o}.set_markersize({v})",
        ("line", "zorder"): "{o}.set_zorder({v})",
        ("axes", "facecolor"): "{o}.set_facecolor({v})",
        ("figure", "facecolor"): "{o}.set_facecolor({v})",
        ("legend", "frameon"): "{o}.set_frame_on({v})",
        ("legend", "visible"): "{o}.set_visible({v})",
    }
)

#: spike 刻意放进来的「看似简单、其实不等价」的一格：图例整体字号在 Tavotto 里是
#: `legend(fontsize=…)` 的原生语义（重排整个盒），公开 API 只能逐条改字。验证门必须
#: 把它拦下来——它是本 spike 对「verify 真的会拒」的反证。
NAIVE_SETTERS = {
    ("legend", "fontsize"): "for _t in {o}.get_texts():\n    _t.set_fontsize({v})",
}


def _ident(value) -> str:
    return repr(value)


class Unsupported(Exception):
    """这条 override 不写回（原因进报告）。"""


# ---------------------------------------------------------------------------
# gid → 表达式。gid 语法与 manifest.instrument 同一套（axes_i 取 ordered_axes 序）。
# ---------------------------------------------------------------------------

_AX = re.compile(r"^axes_(\d+)(?:\.(.+))?$")


def _selector(gid: str, el: dict | None) -> tuple[str, str]:
    """(前置语句, 对象表达式)。前置语句里可以带守卫（找不到 → 跳过并告警）。"""
    if gid == "figure":
        return "", "fig"
    m = _AX.match(gid)
    if not m:
        raise Unsupported(f"gid 形状不认识: {gid}")
    i, rest = int(m.group(1)), m.group(2)
    ax = f"_ax(fig, {i})"
    if rest is None:
        return "", ax
    if rest in ("xlabel", "ylabel"):
        return "", f"_AxisLabel({ax}, {rest[0]!r})"
    if rest == "title":
        return "", f"{ax}.title"
    if rest in ("xticks", "yticks"):
        return "", f"_TickSet({ax}, {rest[0]!r})"
    if rest == "legend":
        return "", f"_legend({ax})"
    mm = re.fullmatch(r"legend\.texts_(\d+)", rest)
    if mm:
        return "", f"_legend({ax}).get_texts()[{int(mm.group(1))}]"
    mm = re.fullmatch(r"lines_(\d+)", rest)
    if mm:
        j = int(mm.group(1))
        label = _script_label(el)
        # 有显式 label 的按 label 认（ADR 0083 的同一条身份），没有的按位置
        return "", f"_line({ax}, {j}, {label!r})"
    mm = re.fullmatch(r"texts_(\d+)", rest)
    if mm:
        return "", f"{ax}.texts[{int(mm.group(1))}]"
    raise Unsupported(f"这一类元素这一版不写回: {rest.split('_')[0]}")


def _script_label(el: dict | None) -> str | None:
    if not el:
        return None
    for f in el.get("editable", []):
        if f.get("prop") == "label":
            lab = str(f.get("value_original", f.get("value")) or "")
            return lab if lab and not lab.startswith("_") else None
    return None


#: 覆盖率路线（ADR 0094「覆盖率路线」一节）。第一轮 spike 两条都没开。
#:   title          标题拖动：公开的 `ax.set_title(..., y=...)` 显式给 y 就关掉自动定位
#:   legend_rebuild 图例整体字号：用公开 API 按 manifest 的整份图例参数重建图例
ROUTES = frozenset({"title", "legend_rebuild"})


def _statement(
    el: dict | None, gid: str, prop: str, value, *, naive: bool, routes=ROUTES, patches=()
) -> str:
    role = (el or {}).get("role") or ("figure" if gid == "figure" else "")
    m = _AX.match(gid)
    if "title" in routes and prop == "pos_frac" and m and m.group(2) == "title":
        fx, fy = float(value[0]), float(value[1])
        return f"_title_to(_ax(fig, {int(m.group(1))}), fig, {fx!r}, {fy!r})"
    if "legend_rebuild" in routes and role == "legend" and prop == "fontsize" and m:
        spec = {f["prop"]: f.get("value") for f in (el or {}).get("editable", [])}
        # 同一个图例上别的**可以放进 legend() 参数**的编辑一起进 spec
        for p in patches:
            if p["gid"] == gid and p["prop"] in spec:
                spec[p["prop"]] = p["value"]
        spec["fontsize"] = value
        keep = (
            "loc",
            "loc_anchor",
            "fontsize",
            "frameon",
            "visible",
            "title",
            "title_fontsize",
            "facecolor",
            "framealpha",
            "edgecolor",
            "ncol",
            "borderpad",
            "labelspacing",
            "handlelength",
            "handletextpad",
            "columnspacing",
            "frame_linewidth",
            "frame_rounded",
        )
        spec = {k: spec.get(k) for k in keep}
        return f"_legend_refont(_ax(fig, {int(m.group(1))}), {spec!r})"
    pre, obj = _selector(gid, el)
    v = _ident(value)
    if prop == "pos_frac" and role in ("text", "axis_label"):
        fx, fy = float(value[0]), float(value[1])
        if role == "axis_label":
            return f"_label_to({obj}, fig, {fx!r}, {fy!r})"
        return f"_text_to({obj}, fig, {fx!r}, {fy!r})"
    if prop == "pos_frac":
        raise Unsupported("标题拖动需要关自动定位（私有 API），这一版不写回")
    if role == "legend" and prop == "loc":
        return f"{obj}.set_loc({v})"
    if role == "legend" and prop == "loc_frac":
        fx, fy = float(value[0]), float(value[1])
        return f"_legend_to({obj}, fig, {fx!r}, {fy!r})"
    if role == "ticks" and prop in ("fontsize", "color", "rotation"):
        kw = {"fontsize": "labelsize", "color": "labelcolor", "rotation": "labelrotation"}[prop]
        return f"{obj}.params({kw}={v})"
    key = (role, prop)
    tmpl = SIMPLE_SETTERS.get(key) or (NAIVE_SETTERS.get(key) if naive else None)
    if tmpl is None:
        raise Unsupported(f"{role}.{prop} 这一版不写回")
    return (pre + "\n" if pre else "") + tmpl.format(o=obj, v=v)


# ---------------------------------------------------------------------------
# 块模板：运行期辅助（只用公开 API）+ 每张图一个函数 + savefig 钩子。
# ---------------------------------------------------------------------------

_RUNTIME = """\
import os as _os
import warnings as _warnings

import matplotlib.figure as _mfig


def _axes_in_order(fig):
    # 与 Tavotto 的 axes 编号同一个顺序：fig.axes，然后逐层的 child_axes，再寄生轴
    out = list(fig.axes)
    seen = {id(a) for a in out}

    def absorb(frontier, attrs):
        while frontier:
            nxt = []
            for parent in frontier:
                for attr in attrs:
                    for kid in getattr(parent, attr, None) or []:
                        if id(kid) not in seen:
                            seen.add(id(kid))
                            out.append(kid)
                            nxt.append(kid)
            frontier = nxt

    absorb(list(out), ("child_axes",))
    absorb(list(out), ("parasites", "child_axes"))
    return out


class _Skip(Exception):
    pass


def _ax(fig, i):
    axes = _axes_in_order(fig)
    if i >= len(axes):
        raise _Skip(f"子图 {i + 1} 不存在")
    return axes[i]


def _line(ax, j, label):
    if label is not None:
        hits = [ln for ln in ax.lines if ln.get_label() == label]
        if len(hits) != 1:
            raise _Skip(f"找不到唯一一条名为 {label!r} 的曲线")
        return hits[0]
    if j >= len(ax.lines):
        raise _Skip(f"曲线 {j + 1} 不存在")
    return ax.lines[j]


def _with_legend(line, setter):
    setter(line)
    label = line.get_label()
    ax = line.axes
    for leg in [ax.get_legend()] + list(line.get_figure().legends):
        if leg is None:
            continue
        for handle, text in zip(leg.legend_handles, leg.get_texts()):
            if text.get_text() == label and hasattr(handle, "get_linestyle"):
                setter(handle)


def _legend(ax):
    leg = ax.get_legend()
    if leg is None:
        raise _Skip("这个子图没有图例")
    return leg


class _TickSet:
    def __init__(self, ax, which):
        self.ax, self.which = ax, which

    def params(self, **kw):
        self.ax.tick_params(axis=self.which, **kw)


def _disp(fig, fx, fy):
    # figure 分数（左上角为原点）→ 屏幕坐标
    return fx * fig.bbox.width, (1.0 - fy) * fig.bbox.height


def _text_to(t, fig, fx, fy):
    t.set_position(tuple(t.get_transform().inverted().transform(_disp(fig, fx, fy))))


class _AxisLabel:
    # 轴标签的 Text 不认得自己的子图（Text.axes 是 None），带着子图一起传
    def __init__(self, ax, which):
        self.ax, self.axis = ax, getattr(ax, which + "axis")

    def __getattr__(self, name):
        return getattr(self.axis.label, name)


def _label_to(lab, fig, fx, fy):
    x, y = lab.ax.transAxes.inverted().transform(_disp(fig, fx, fy))
    lab.axis.set_label_coords(float(x), float(y))


def _legend_to(leg, fig, fx, fy):
    parent = leg.axes.transAxes if leg.axes is not None else fig.transFigure
    leg.set_bbox_to_anchor(None)
    leg.set_loc(tuple(parent.inverted().transform(_disp(fig, fx, fy))))


def _title_to(ax, fig, fx, fy):
    # 公开 API 关自动定位：set_title 显式给 y（matplotlib 3.8 / 3.11 源码同一段：
    # `if y is None: … else: self._autotitlepos = False`）。set_title 会把字号 / 字重 /
    # 对齐 / 标题间距重置成 rcParams，所以把此刻的样子作为参数原样带回去，最后按
    # 与 Tavotto 同一个算法落到 figure 分数上。
    t = ax.title
    look = dict(
        fontproperties=t.get_fontproperties().copy(),
        color=t.get_color(),
        horizontalalignment=t.get_horizontalalignment(),
        verticalalignment=t.get_verticalalignment(),
        rotation=t.get_rotation(),
        alpha=t.get_alpha(),
    )
    ax.set_title(t.get_text(), loc="center", y=t.get_position()[1], **look)
    t.set_position(tuple(t.get_transform().inverted().transform(_disp(fig, fx, fy))))


def _legend_refont(ax, spec):
    # 公开 API 重建图例（= 原生 legend(fontsize=…) 语义）：条目取 get_legend_handles_labels
    # 里按文字唯一匹配到的源对象；显式传进 legend() 的代理 handle 没有公开的读取口，取不回来就不做。
    old = _legend(ax)
    old_texts = old.get_texts()
    labels = [t.get_text() for t in old_texts]
    found = {}
    for h, lab in zip(*ax.get_legend_handles_labels()):
        found.setdefault(lab, []).append(h)
    if any(len(found.get(lab, [])) != 1 for lab in labels):
        raise _Skip("图例条目不是按 label 自动收集的，取不回原来的 handle")
    kw = dict(
        loc=spec["loc"],
        fontsize=spec["fontsize"],
        ncols=int(spec["ncol"]),
        frameon=spec["frameon"],
        framealpha=spec["framealpha"],
        facecolor=spec["facecolor"],
        edgecolor=spec["edgecolor"],
        fancybox=spec["frame_rounded"],
        borderpad=spec["borderpad"],
        labelspacing=spec["labelspacing"],
        handlelength=spec["handlelength"],
        handletextpad=spec["handletextpad"],
        columnspacing=spec["columnspacing"],
        title=spec["title"] or None,
        title_fontsize=spec["title_fontsize"],
        borderaxespad=old.borderaxespad,
        markerscale=old.markerscale,
        numpoints=old.numpoints,
        scatterpoints=old.scatterpoints,
        shadow=old.shadow,
    )
    if spec["loc_anchor"] is not None:
        kw["bbox_to_anchor"] = tuple(spec["loc_anchor"])
    new = ax.legend([found[lab][0] for lab in labels], labels, **kw)
    new.get_frame().set_linewidth(spec["frame_linewidth"])
    new.set_zorder(old.get_zorder())
    new.set_visible(spec["visible"])
    for n, o in zip(new.get_texts(), old_texts):
        # 只搬「样子」：Artist.update_from 连 transform 一起抄，文字会落到旧图例的坐标上
        n.set_fontproperties(o.get_fontproperties().copy())
        n.set_color(o.get_color())
        n.set_alpha(o.get_alpha())
        n.set_fontsize(spec["fontsize"])


def _stem(fname):
    if not isinstance(fname, (str, _os.PathLike)):
        return ""
    return _os.path.splitext(_os.path.basename(_os.fspath(fname)))[0]


def _run(fig, stem, steps):
    for what, step in steps:
        try:
            step(fig)
        except Exception as exc:  # 调整失败只告警，绝不打断脚本本身
            _warnings.warn(f"Tavotto 调整未应用（{stem} · {what}）：{exc}", stacklevel=3)


def _install(table):
    real = _mfig.Figure.savefig
    if getattr(real, "_tavotto_adjust", False):
        return

    def savefig(self, fname, *args, **kwargs):
        stem = _stem(fname)
        steps = table.get(stem)
        if steps is not None and getattr(self, "_tavotto_adjusted", None) != stem:
            self._tavotto_adjusted = stem
            _run(self, stem, steps)
        return real(self, fname, *args, **kwargs)

    savefig._tavotto_adjust = True
    _mfig.Figure.savefig = savefig
"""


def build_block(
    stems: dict[str, list[dict]],
    manifests: dict[str, dict],
    meta: dict,
    *,
    naive: bool = False,
    routes=ROUTES,
    ascii_only: bool = False,
    indent: str = "    ",
) -> tuple[str, dict]:
    """(块文本, 报告)。块内换行一律 '\\n'，插入时再换成文件自己的换行。"""
    report: dict = {"written": [], "skipped": []}
    fn_lines: list[str] = []
    table_items: list[str] = []
    for n, (stem, patches) in enumerate(sorted(stems.items())):
        els = {e["gid"]: e for e in manifests[stem]["elements"]}
        steps: list[tuple[str, str]] = []
        for p in patches:
            gid, prop, value = p["gid"], p["prop"], p["value"]
            try:
                stmt = _statement(
                    els.get(gid), gid, prop, value, naive=naive, routes=routes, patches=patches
                )
            except Unsupported as exc:
                report["skipped"].append(
                    {"stem": stem, "gid": gid, "prop": prop, "reason": str(exc)}
                )
                continue
            steps.append((f"{gid}.{prop}", stmt))
            report["written"].append({"stem": stem, "gid": gid, "prop": prop})
        # 部分写回的连带：留作 override 的「广播」条目（图例整体字号 → 每条图例文字）在 Tavotto
        # 重放时排在窄条目之前、会盖掉写进脚本的窄条目——同一 prop、gid 是它子孙的，一起留下。
        residual = {(x["gid"], x["prop"]) for x in report["skipped"] if x["stem"] == stem}
        kept = []
        for what, stmt in steps:
            gid, prop = what.rsplit(".", 1)
            parent = next((g for g, pr in residual if pr == prop and gid.startswith(g + ".")), None)
            if parent is not None:
                report["skipped"].append(
                    {
                        "stem": stem,
                        "gid": gid,
                        "prop": prop,
                        "reason": f"它的广播条目 {parent}.{prop} 留作 override，一起留下",
                    }
                )
                report["written"] = [
                    w
                    for w in report["written"]
                    if not (w["stem"] == stem and w["gid"] == gid and w["prop"] == prop)
                ]
                continue
            kept.append((what, stmt))
        # 重建图例会换掉图例对象：它排在同一个图例的其余步骤（位置、单条文字）之前
        steps = sorted(
            kept,
            key=lambda s: 0 if "_legend_refont(" in s[1] else (1 if ".legend" in s[0] else 2),
        )
        if not steps:
            continue
        for k, (what, stmt) in enumerate(steps):
            fn_lines.append(f"def _tavotto_{n}_{k}(fig):  # {what}")
            fn_lines.extend("    " + ln for ln in stmt.splitlines())
            fn_lines.append("")
        table_items.append(
            f"    {stem!r}: [\n"
            + "".join(
                f"        ({what!r}, _tavotto_{n}_{k}),\n" for k, (what, _s) in enumerate(steps)
            )
            + "    ],"
        )
    begin, end = (BEGIN_ASCII, END_ASCII) if ascii_only else (BEGIN, END)
    note = (
        "# Written by Tavotto {version} on {date}. Delete this whole block to restore the script.\n"
        "# Backup of the original: {backup}\n"
        if ascii_only
        else "# 由 Tavotto {version} 于 {date} 写入。整段删除即恢复原样；原脚本备份：{backup}\n"
        "# 本段只在 savefig 的那一刻修改对应的图，其余代码一行未动。\n"
    ).format(**meta)
    runtime = _RUNTIME
    if ascii_only:
        runtime = (
            "\n".join(
                re.sub(r"\s+#[^\n]*[^\x00-\x7f][^\n]*$", "", ln)
                for ln in runtime.splitlines()
                if not ln.lstrip().startswith("#")
            )
            + "\n"
        )
        runtime = runtime.replace("子图 {i + 1} 不存在", "axes {i} missing")
        runtime = re.sub(r'f?"[^"\n]*[^\x00-\x7f][^"\n]*"', '"not applied"', runtime)
    inner = (
        runtime + "\n" + "\n".join(fn_lines) + "\n_install({\n" + "\n".join(table_items) + "\n})\n"
    )
    inner = inner.replace("    ", indent)
    body = "".join((indent + ln if ln.strip() else "") + "\n" for ln in inner.splitlines())
    text = f"{begin}\n{note}def _tavotto_adjust():\n{body}\n\n_tavotto_adjust()\ndel _tavotto_adjust\n{end}\n"
    return text, report


# ---------------------------------------------------------------------------
# 插入：保编码 / BOM / 换行；已有块整段替换（幂等）；插在顶部 import 段之后。
# ---------------------------------------------------------------------------


def detect(src: bytes) -> dict:
    enc, _lines = tokenize.detect_encoding(io.BytesIO(src).readline)
    bom = src.startswith(codecs.BOM_UTF8)
    text = src.decode(enc)
    crlf, lf = text.count("\r\n"), text.count("\n")
    newline = "\r\n" if crlf and crlf * 2 >= lf else "\n"
    indent = "    "
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.INDENT:
                indent = tok.string
                break
    except (tokenize.TokenError, SyntaxError):
        pass
    return {"encoding": "utf-8-sig" if bom else enc, "newline": newline, "indent": indent}


def _insert_line(text: str) -> int:
    """块插在第几行之前（0 起）：模块 docstring、__future__ 与**开头连续的 import 段**之后。"""
    tree = ast.parse(text)
    after = 0
    for node in tree.body:
        is_doc = (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and after == 0
        )
        if is_doc or isinstance(node, (ast.Import, ast.ImportFrom)):
            after = node.end_lineno
            continue
        break
    return after


def _find_block(text: str):
    """(起点, 终点) —— 连同插入时加的前后各两个换行；没有块回 None。标记行按整行认。"""
    for b, e in ((BEGIN, END), (BEGIN_ASCII, END_ASCII)):
        m = re.search(rf"^{re.escape(b)}\r?\n.*?^{re.escape(e)}(\r?\n)", text, re.S | re.M)
        if m is None:
            continue
        start, end = m.start(), m.end()
        nl = m.group(1)
        if start >= 2 * len(nl) and text[start - 2 * len(nl) : start] == nl * 2:
            start -= 2 * len(nl)
        if text[end : end + 2 * len(nl)] == nl * 2:
            end += 2 * len(nl)
        return start, end
    return None


def strip_block(text: str) -> str:
    span = _find_block(text)
    return text if span is None else text[: span[0]] + text[span[1] :]


def insert_block(src: bytes, block: str) -> bytes:
    """插入（已有块先整段去掉）。**只动插入的那一段**：文件其余字节一个不改（混合换行也原样）。"""
    info = detect(src)
    enc, nl = info["encoding"], info["newline"]
    text = strip_block(src.decode(enc))
    at = _insert_line(text.replace("\r\n", "\n"))
    # 第 at 行结束处的字符偏移（按原文的真实换行数）
    offset = 0
    for _ in range(at):
        k = text.index("\n", offset)
        offset = k + 1
    body = block.rstrip("\n").replace("\n", nl) + nl
    seg = (nl * 2 if at else "") + body + nl * 2
    if at and offset == len(text) and not text.endswith("\n"):
        seg = nl + seg  # 最后一行没有换行符
    return (text[:offset] + seg + text[offset:]).encode(enc)


def remove_block(src: bytes) -> bytes:
    info = detect(src)
    return strip_block(src.decode(info["encoding"])).encode(info["encoding"])
