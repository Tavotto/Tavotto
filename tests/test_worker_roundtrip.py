"""worker 协议 round-trip：build → override → 全量列表还原 → export 保真。

本进程不 import matplotlib——spawn pool.find_worker_python() 找到的科学栈
解释器跑 engine/worker.py，走真实 stdin/stdout JSON 协议。覆盖：
  * 拦截 savefig 捕获 Figure（合成脚本走 paper_style.save 方言）
  * override 全量列表语义：缺失 key 自动恢复原值（undo 的基础）
  * export 应用 patches 后的 PDF 矢量文字保真
"""
import json
import os
import subprocess
import threading
import time
import sys
from pathlib import Path

import pymupdf
import pytest

from magplot.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（MM_WORKER_PYTHON）")

FIG_SCRIPT = """\
import matplotlib.pyplot as plt
from paper_style import save

def main():
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([0, 1, 2], [1, 0, 2], label="series-a")
    ax.set_title("Original Title")
    ax.set_xlabel("x label")
    ax.set_ylabel("Signal (cm$^{-1}$)")
    ax.legend()
    save(fig, "TestFig_a")

    fig3d = plt.figure(figsize=(3, 2))
    ax3 = fig3d.add_subplot(projection="3d")
    ax3.plot([0, 1], [0, 1], [0, 1])
    ax3.set_title("3D Title")
    ax3.set_zlabel("z depth")
    save(fig3d, "TestFig_3d")

    fig2, ax2 = plt.subplots(figsize=(3, 2))
    ax2.scatter([0, 1, 2], [2, 1, 0], label="pts")
    ax2.plot([0, 1, 2], [0, 1, 2], label="ln-b")
    ax2.plot([0, 1, 2], [2, 2, 2], label="ln-c")
    ax2.legend()
    save(fig2, "TestFig_sc")
"""

PAPER_STYLE_STUB = """\
def save(fig, stem, outdir="figures"):
    fig.savefig(f"{stem}.pdf")
"""


def _drain(proc, timeout=10) -> str:
    """把 worker 已经写出的 stderr 收上来（进程已死时会立刻读到 EOF）。"""
    box: list = []
    t = threading.Thread(target=lambda: box.append(proc.stderr.read()), daemon=True)
    t.start()
    t.join(timeout)
    return (box[0] if box else "") or "（无 stderr 输出）"


def _rpc(proc, obj, timeout=120):
    """发一条命令读一行回应。

    读取放在线程里加超时，而不是 `select.select`——Windows 的 select 只接受
    socket，对管道会直接 WinError 10038。产品侧的 pool.py 本来就是阻塞
    readline，这里只是给测试补一个卡死时的保险丝。
    """
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()

    box: list = []
    reader = threading.Thread(target=lambda: box.append(proc.stdout.readline()),
                              daemon=True)
    reader.start()
    reader.join(timeout)
    assert not reader.is_alive(), f"worker 超时（{timeout}s）: {obj.get('cmd')}"
    line = box[0] if box else ""
    # 空行 = stdout 到了 EOF = worker 死了。光断言 `assert ''` 什么也看不出来，
    # 把子进程的 stderr 一并带上——worker 的 traceback 全在那儿。
    assert line, f"worker 无响应: {obj.get('cmd')}\n--- worker stderr ---\n{_drain(proc)}"
    resp = json.loads(line)
    assert resp.get("ok"), f"{resp.get('error', resp)}\n{resp.get('traceback', '')}"
    return resp


def _text_value(manifest, gid):
    for el in manifest["elements"]:
        if el["gid"] == gid:
            for f in el.get("editable", []):
                if f["prop"] == "text":
                    return f["value"]
    raise AssertionError(f"manifest 中找不到 {gid} 的 text 字段")


def _spawn(script: Path, figs: Path, tmp_path: Path, entry: str = "main"):
    return subprocess.Popen(
        [WORKER_PY, str(pool.WORKER_PY),
         "--script", str(script),
         "--figures-dir", str(figs),
         "--out-dir", str(tmp_path / "out"),
         "--sandbox", str(tmp_path / "sandbox"),
         "--entry", entry],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1,
        encoding="utf-8", errors="replace")   # 同 pool.py：管道钉死 UTF-8


@pytest.fixture
def worker(tmp_path):
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "paper_style.py").write_text(PAPER_STYLE_STUB, encoding="utf-8")
    (figs / "fig_test.py").write_text(FIG_SCRIPT, encoding="utf-8")
    proc = _spawn(figs / "fig_test.py", figs, tmp_path)
    yield proc, tmp_path / "out", tmp_path
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


def test_full_roundtrip(worker):
    proc, out, tmp = worker

    # ---- build：savefig 拦截捕获 Figure，不写真实文件 ----
    resp = _rpc(proc, {"cmd": "build"})
    assert "TestFig_a" in resp["stems"]
    assert not list((tmp / "figures").glob("*.pdf")), "build 期间不得写出真实图文件"

    man = json.loads((out / "TestFig_a.json").read_text(encoding="utf-8"))
    title_gid = next(
        el["gid"] for el in man["elements"]
        for f in el.get("editable", [])
        if f["prop"] == "text" and f["value"] == "Original Title")

    # ---- override：改标题 ----
    patch = [{"gid": title_gid, "prop": "text", "value": "Changed Title"}]
    resp = _rpc(proc, {"cmd": "override", "stem": "TestFig_a", "patches": patch})
    assert _text_value(resp["manifest"], title_gid) == "Changed Title"
    assert (out / "TestFig_a.svg").exists()  # 预览 SVG 已重出

    # ---- 全量列表语义：空列表 = 撤销全部，自动恢复原值 ----
    resp = _rpc(proc, {"cmd": "override", "stem": "TestFig_a", "patches": []})
    assert _text_value(resp["manifest"], title_gid) == "Original Title"

    # ---- export：应用 patches 全质量导出，矢量文字保真 ----
    pdf = tmp / "export.pdf"
    _rpc(proc, {"cmd": "export", "stem": "TestFig_a", "patches": patch,
                "path": str(pdf), "format": "pdf", "dpi": 600})
    with pymupdf.open(pdf) as doc:
        text = doc[0].get_text()
    assert "Changed Title" in text
    assert "series-a" in text  # 图例也在

    # ---- 未知 stem 结构化报错，进程不退出 ----
    _assert_unknown_stem(proc)


def _pos_of(manifest, gid):
    el = next(e for e in manifest["elements"] if e["gid"] == gid)
    return next(f["value"] for f in el.get("editable", []) if f["prop"] == "position")


def test_axes3d_position_roundtrip(worker):
    """3D 子图放开 position：可拖动（resizable）、可移动、全量列表恢复原位。
    Axes3D.set_position 后 matplotlib 按盒比例微调 x/w，断言按 y 落位判定。"""
    proc, out, tmp = worker
    _rpc(proc, {"cmd": "build"})
    man = json.loads((out / "TestFig_3d.json").read_text(encoding="utf-8"))
    ax = next(el for el in man["elements"] if el["role"] == "axes3d")
    assert ax.get("resizable") is True
    pos0 = _pos_of(man, ax["gid"])

    patch = [{"gid": ax["gid"], "prop": "position", "value": [0.3, 0.55, 0.4, 0.4]}]
    resp = _rpc(proc, {"cmd": "override", "stem": "TestFig_3d", "patches": patch})
    pos1 = _pos_of(resp["manifest"], ax["gid"])
    assert abs(pos1[1] - 0.55) < 0.1, pos1     # y 明显移向目标
    assert pos1[1] - pos0[1] > 0.2             # 相对原位确实动了

    resp = _rpc(proc, {"cmd": "override", "stem": "TestFig_3d", "patches": []})
    pos2 = _pos_of(resp["manifest"], ax["gid"])
    assert pos2 == pytest.approx(pos0, abs=0.05)


def _field(manifest, gid, prop):
    el = next(e for e in manifest["elements"] if e["gid"] == gid)
    return next((f for f in el.get("editable", []) if f["prop"] == prop), None)


def test_axes3d_zlabel_exposed_with_labelpad(worker):
    """3D 的 z 轴标签要可选中（此前漏注册）；不可拖（mplot3d 每帧重算位置，
    set_label_coords 无效），位置微调走 labelpad。"""
    proc, out, tmp = worker
    _rpc(proc, {"cmd": "build"})
    man = json.loads((out / "TestFig_3d.json").read_text(encoding="utf-8"))
    zl = next(e for e in man["elements"] if e["gid"].endswith(".zlabel"))
    assert zl["draggable"] is False
    assert _field(man, zl["gid"], "text")["value"] == "z depth"
    pad0 = _field(man, zl["gid"], "labelpad")["value"]

    patch = [{"gid": zl["gid"], "prop": "labelpad", "value": 25}]
    resp = _rpc(proc, {"cmd": "override", "stem": "TestFig_3d", "patches": patch})
    assert _field(resp["manifest"], zl["gid"], "labelpad")["value"] == 25

    resp = _rpc(proc, {"cmd": "override", "stem": "TestFig_3d", "patches": []})
    assert _field(resp["manifest"], zl["gid"], "labelpad")["value"] == pytest.approx(pad0)


def test_axes3d_axis_arrows_roundtrip(worker):
    """3D 轴箭头：开关 + 样式旋钮 + 全量列表还原；导出走 do_3d_projection
    的私有几何助手，matplotlib 升版时此处最先报警。"""
    proc, out, tmp = worker
    _rpc(proc, {"cmd": "build"})
    man = json.loads((out / "TestFig_3d.json").read_text(encoding="utf-8"))
    ax = next(el for el in man["elements"] if el["role"] == "axes3d")
    assert _field(man, ax["gid"], "axis_arrows")["value"] is False

    patch = [
        {"gid": ax["gid"], "prop": "axis_arrows", "value": True},
        {"gid": ax["gid"], "prop": "arrow_color", "value": "#CC0000"},
        {"gid": ax["gid"], "prop": "arrow_width", "value": 1.2},
    ]
    resp = _rpc(proc, {"cmd": "override", "stem": "TestFig_3d", "patches": patch})
    assert _field(resp["manifest"], ax["gid"], "axis_arrows")["value"] is True
    assert _field(resp["manifest"], ax["gid"], "arrow_color")["value"].lower() == "#cc0000"
    assert resp.get("warnings") == []  # 私有助手可用，没有「应用失败」

    # 带箭头导出 PDF：draw 路径（含投影现算落边）不炸即为过；文字仍矢量
    pdf = tmp / "arrows3d.pdf"
    _rpc(proc, {"cmd": "export", "stem": "TestFig_3d", "patches": patch,
                "path": str(pdf), "format": "pdf", "dpi": 600})
    with pymupdf.open(pdf) as doc:
        assert "3D Title" in doc[0].get_text()

    resp = _rpc(proc, {"cmd": "override", "stem": "TestFig_3d", "patches": []})
    assert _field(resp["manifest"], ax["gid"], "axis_arrows")["value"] is False


def test_scatter_marker_roundtrip(worker):
    """散点 marker 可整体替换（set_paths），全量列表语义可恢复原始路径。"""
    proc, out, tmp = worker
    _rpc(proc, {"cmd": "build"})
    man = json.loads((out / "TestFig_sc.json").read_text(encoding="utf-8"))
    sc = next(e for e in man["elements"] if e["role"] == "scatter")
    f = _field(man, sc["gid"], "marker")
    assert f is not None and f["value"] == "original"
    assert "s" in f["options"]

    patch = [{"gid": sc["gid"], "prop": "marker", "value": "s"}]
    resp = _rpc(proc, {"cmd": "override", "stem": "TestFig_sc", "patches": patch})
    assert _field(resp["manifest"], sc["gid"], "marker")["value"] == "s"

    resp = _rpc(proc, {"cmd": "override", "stem": "TestFig_sc", "patches": []})
    assert _field(resp["manifest"], sc["gid"], "marker")["value"] == "original"


def test_legend_entry_order_roundtrip(worker):
    """图例条目顺序：按原始序号排列重建图例盒；空列表恢复原序。"""
    proc, out, tmp = worker
    _rpc(proc, {"cmd": "build"})
    man = json.loads((out / "TestFig_sc.json").read_text(encoding="utf-8"))
    leg = next(e for e in man["elements"] if e["role"] == "legend")
    f = _field(man, leg["gid"], "entry_order")
    assert f is not None and f["value"] == [0, 1, 2]
    labels0 = list(f["options"])
    assert len(labels0) == 3

    patch = [{"gid": leg["gid"], "prop": "entry_order", "value": [2, 0, 1]}]
    resp = _rpc(proc, {"cmd": "override", "stem": "TestFig_sc", "patches": patch})
    f2 = _field(resp["manifest"], leg["gid"], "entry_order")
    assert f2["value"] == [2, 0, 1]
    assert f2["options"] == [labels0[2], labels0[0], labels0[1]]

    resp = _rpc(proc, {"cmd": "override", "stem": "TestFig_sc", "patches": []})
    f3 = _field(resp["manifest"], leg["gid"], "entry_order")
    assert f3["value"] == [0, 1, 2]
    assert f3["options"] == labels0

    # 重排后导出的 PDF 图例文字仍是矢量（重建型属性不破坏导出）
    pdf = tmp / "order.pdf"
    _rpc(proc, {"cmd": "export", "stem": "TestFig_sc", "patches": patch,
                "path": str(pdf), "format": "pdf", "dpi": 600})
    with pymupdf.open(pdf) as doc:
        text = doc[0].get_text()
    for lab in labels0:
        assert lab in text


def _assert_unknown_stem(proc):
    """未知 stem 要回结构化错误而不是让 worker 退出。

    这里手写协议（而非走 _rpc）是因为 _rpc 断言 ok=True，正好是本例要否定的。
    """
    proc.stdin.write(json.dumps({"cmd": "override", "stem": "nope", "patches": []}) + "\n")
    proc.stdin.flush()
    box: list = []
    reader = threading.Thread(target=lambda: box.append(proc.stdout.readline()),
                              daemon=True)
    reader.start()
    reader.join(30)
    assert not reader.is_alive() and box and box[0], "worker 对未知 stem 无回应"
    resp = json.loads(box[0])
    assert resp["ok"] is False and "nope" in resp["error"]
    assert proc.poll() is None

    _rpc(proc, {"cmd": "ping"})


def test_fontfamily_override_syncs_mathtext(worker):
    """改字体必须连 $…$ 上下标一起改。set_fontfamily 只影响正文，mathtext
    仍按自己的字体集渲染——修好前正文换成衬线、上标还是 DejaVu Sans，
    同一个文字框里两种字体。几何级看护：导出 PDF 后 ylabel 区域内的
    所有字形（含上标的 −1）字体族必须一致。"""
    proc, out, tmp = worker
    _rpc(proc, {"cmd": "build"})
    man = json.loads((out / "TestFig_a.json").read_text(encoding="utf-8"))
    el = next(e for e in man["elements"]
              for f in e.get("editable", [])
              if f["prop"] == "text" and "cm$^{-1}$" in str(f["value"]))
    patch = [{"gid": el["gid"], "prop": "fontfamily", "value": "serif"}]
    _rpc(proc, {"cmd": "override", "stem": "TestFig_a", "patches": patch})

    pdf = tmp / "font_export.pdf"
    _rpc(proc, {"cmd": "export", "stem": "TestFig_a", "patches": patch,
                "path": str(pdf), "format": "pdf", "dpi": 600})
    with pymupdf.open(pdf) as doc:
        page = doc[0]
        w, h = page.rect.width, page.rect.height
        bx, by, bw, bh = el["bbox"]
        clip = pymupdf.Rect(bx * w - 2, by * h - 2, (bx + bw) * w + 2, (by + bh) * h + 2)
        fonts = {s["font"]
                 for blk in page.get_text("rawdict", clip=clip)["blocks"]
                 for ln in blk.get("lines", [])
                 for s in ln.get("spans", [])}
    assert fonts, "ylabel 区域抽不到文字"
    assert all("Serif" in f for f in fonts), f"上下标没跟着换字体: {fonts}"

    # 撤销（全量列表语义）后 math 字体集也要回到原生状态：再导出，上标回到 Sans
    _rpc(proc, {"cmd": "override", "stem": "TestFig_a", "patches": []})
    pdf2 = tmp / "font_export_undo.pdf"
    _rpc(proc, {"cmd": "export", "stem": "TestFig_a", "patches": [],
                "path": str(pdf2), "format": "pdf", "dpi": 600})
    with pymupdf.open(pdf2) as doc:
        fonts2 = {s["font"]
                  for blk in doc[0].get_text("rawdict", clip=clip)["blocks"]
                  for ln in blk.get("lines", [])
                  for s in ln.get("spans", [])}
    assert fonts2 and all("Serif" not in f for f in fonts2), f"撤销未还原 math 字体: {fonts2}"


def test_non_ascii_survives_the_pipe(worker):
    """图内文字含非 ASCII（中文 / µ / ⁻¹）时协议不能崩。

    回归看护：worker 用 ensure_ascii=False 写 JSON，Windows 的默认 stdio 编码
    跟系统区域走（cp1252/cp936），不钉死 UTF-8 就会 UnicodeEncodeError 把
    worker 打死——症状是「worker 无响应」，而不是一条能看懂的错误。
    """
    proc, out, tmp = worker
    _rpc(proc, {"cmd": "build"})
    man = json.loads((out / "TestFig_a.json").read_text(encoding="utf-8"))
    title_gid = next(
        el["gid"] for el in man["elements"]
        for f in el.get("editable", [])
        if f["prop"] == "text" and f["value"] == "Original Title")

    tricky = "反应速率 µm·h⁻¹ ±0.5 ℃"
    resp = _rpc(proc, {"cmd": "override", "stem": "TestFig_a",
                       "patches": [{"gid": title_gid, "prop": "text", "value": tricky}]})
    assert _text_value(resp["manifest"], title_gid) == tricky

    pdf = tmp / "cjk.pdf"
    _rpc(proc, {"cmd": "export", "stem": "TestFig_a",
                "patches": [{"gid": title_gid, "prop": "text", "value": tricky}],
                "path": str(pdf), "format": "pdf", "dpi": 300})
    assert pdf.is_file() and pdf.stat().st_size > 0
    assert proc.poll() is None, "worker 不该因为非 ASCII 文字退出"


PLAIN_SCRIPT = """\
import matplotlib.pyplot as plt
from pathlib import Path

OUT = Path(__file__).resolve().parent / "panels"
OUT.mkdir(parents=True, exist_ok=True)


def save_panel(fig, stem):
    # 文件名藏在变量里、stem 还是函数形参：静态扫描无从得知，只有运行时才知道
    fig.savefig(OUT / f"{stem}.pdf")
    plt.close(fig)          # 脚本自己关图；worker 的 CAPTURE 已持有引用


def main():
    for name in ("PlainFig_a", "PlainFig_b"):
        fig, ax = plt.subplots(figsize=(3, 2))
        ax.plot([0, 1], [1, 0])
        ax.set_title(name)
        save_panel(fig, name)
"""


ARROW_GRADIENT_SCRIPT = """\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import to_rgb
from matplotlib.patches import FancyArrowPatch, PathPatch
from matplotlib.path import Path as MplPath


def main():
    fig, ax = plt.subplots(figsize=(3, 2))
    x = np.linspace(0.0, 10.0, 60)
    curve = np.exp(-((x - 5.0) ** 2) / 2.0)
    ax.plot(x, curve, color="#111111")

    # 「形状渐变填充」的标准画法：imshow 竖直渐变 + PathPatch 裁剪
    verts = np.concatenate([np.column_stack((x, np.zeros_like(x))),
                            np.column_stack((x[::-1], curve[::-1]))])
    codes = np.full(len(verts), MplPath.LINETO, dtype=np.uint8)
    codes[0] = MplPath.MOVETO
    codes[-1] = MplPath.CLOSEPOLY
    clip = PathPatch(MplPath(verts, codes), transform=ax.transData,
                     facecolor="none", edgecolor="none")
    ax.add_patch(clip)
    rgb = np.asarray(to_rgb("#4C78A8"), dtype=float)
    light = rgb + (1.0 - rgb) * 0.82
    ramp = np.linspace(light, rgb, 256).reshape(256, 1, 3)
    rgba = np.concatenate([ramp, np.full((256, 1, 1), 0.82)], axis=2)
    image = ax.imshow(rgba, extent=(0.0, 10.0, 0.0, 1.0), origin="lower",
                      aspect="auto", interpolation="bicubic", zorder=1)
    image.set_clip_path(clip)

    # 独立箭头（add_patch）+ annotate 纯箭头，两条注册路径都要盖到
    arrow = FancyArrowPatch(posA=(5.0, 1.4), posB=(5.0, 1.05),
                            transform=ax.transData, arrowstyle="-|>",
                            linewidth=0.75, mutation_scale=7,
                            color="#76008A", zorder=11)
    ax.add_patch(arrow)
    ax.annotate("", xy=(3.0, 0.6), xytext=(2.0, 1.3),
                arrowprops=dict(arrowstyle="->", color="#2A6F3C"))

    ax.set_ylim(0, 1.6)
    fig.savefig("ArrowGrad.pdf")
"""


def _field_value(manifest, gid, prop):
    el = next(e for e in manifest["elements"] if e["gid"] == gid)
    return next(f["value"] for f in el["editable"] if f["prop"] == prop)


def test_arrowpatch_and_gradient_fill_editable(tmp_path):
    """独立箭头（FancyArrowPatch / annotate）与单色渐变填充要能改。

    用户的 XPS 谱图脚本正是这两种画法：add_patch 的峰位箭头 + 「imshow 渐变
    + PathPatch 裁剪」的组分填充。此前两者都不在 manifest 里，界面上根本
    选不中。看护：manifest 暴露 → override 换色生效 → 空列表还原原值。
    """
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig_ag.py").write_text(ARROW_GRADIENT_SCRIPT, encoding="utf-8")
    proc = _spawn(figs / "fig_ag.py", figs, tmp_path)
    try:
        _rpc(proc, {"cmd": "build"})
        resp = _rpc(proc, {"cmd": "override", "stem": "ArrowGrad", "patches": []})
        man = resp["manifest"]
        arrows = [e for e in man["elements"] if e["role"] == "arrow_patch"]
        # add_patch 的独立箭头 + annotate 的标注箭头都在
        assert {e["gid"] for e in arrows} >= {"axes_0.arrows_1", "axes_0.texts_0.arrow"}, \
            [e["gid"] for e in arrows]
        for e in arrows:
            assert e.get("bbox"), f"{e['gid']} 没有 bbox，画布上选不中"
        assert _field_value(man, "axes_0.arrows_1", "color").lower() == "#76008a"
        # 渐变位图暴露基色；真数据 imshow 不会有这个字段（检测为单色渐变才给）
        assert _field_value(man, "axes_0.images_0", "gradient_color").lower() == "#4c78a8"

        # 换色：箭头改红、渐变基色改绿
        patches = [
            {"gid": "axes_0.arrows_1", "prop": "color", "value": "#D00000"},
            {"gid": "axes_0.images_0", "prop": "gradient_color", "value": "#2A9D8F"},
        ]
        resp = _rpc(proc, {"cmd": "override", "stem": "ArrowGrad", "patches": patches})
        assert resp.get("warnings") in (None, []), resp.get("warnings")
        man = resp["manifest"]
        assert _field_value(man, "axes_0.arrows_1", "color").lower() == "#d00000"
        assert _field_value(man, "axes_0.images_0", "gradient_color").lower() == "#2a9d8f"

        # 全量列表语义：空列表 = 全部还原
        resp = _rpc(proc, {"cmd": "override", "stem": "ArrowGrad", "patches": []})
        man = resp["manifest"]
        assert _field_value(man, "axes_0.arrows_1", "color").lower() == "#76008a"
        assert _field_value(man, "axes_0.images_0", "gradient_color").lower() == "#4c78a8"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def test_arrowpatch_endpoints_and_style_roundtrip(tmp_path):
    """独立箭头可自由挪动与改样式；annotate 箭头端点归注释机制、不放出来。

    - 独立箭头（add_patch）manifest 带 arrow_endpoints（figure 分数、y 向下），
      endpoints_frac override 拖到哪端点就落到哪；
    - arrowstyle / linestyle 可换、可还原（全量列表语义）；
    - annotate 的 arrow_patch 每次 draw 被注释机制重定位，绝不能出端点，
      否则用户拖完下一帧就弹回去。
    """
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig_ae.py").write_text(ARROW_GRADIENT_SCRIPT, encoding="utf-8")
    proc = _spawn(figs / "fig_ae.py", figs, tmp_path)
    try:
        _rpc(proc, {"cmd": "build"})
        resp = _rpc(proc, {"cmd": "override", "stem": "ArrowGrad", "patches": []})
        man = resp["manifest"]

        standalone = next(e for e in man["elements"] if e["gid"] == "axes_0.arrows_1")
        annotate = next(e for e in man["elements"] if e["gid"] == "axes_0.texts_0.arrow")
        pts = standalone.get("arrow_endpoints")
        assert pts and len(pts) == 2, standalone
        # 脚本里 posA=(5,1.4) 在 posB=(5,1.05) 上方：top-origin 下 A 的 fy 更小
        assert pts[0][1] < pts[1][1]
        assert all(0.0 <= v <= 1.0 for p in pts for v in p)
        assert "arrow_endpoints" not in annotate
        assert _field_value(man, "axes_0.arrows_1", "arrowstyle") == "-|>"
        assert _field_value(man, "axes_0.texts_0.arrow", "arrowstyle") == "->"
        assert _field_value(man, "axes_0.arrows_1", "linestyle") == "-"

        # 整体右移 0.1（figure 分数）+ 换样式
        moved = [round(pts[0][0] + 0.1, 4), pts[0][1], round(pts[1][0] + 0.1, 4), pts[1][1]]
        patches = [
            {"gid": "axes_0.arrows_1", "prop": "endpoints_frac", "value": moved},
            {"gid": "axes_0.arrows_1", "prop": "arrowstyle", "value": "->"},
            {"gid": "axes_0.arrows_1", "prop": "linestyle", "value": "--"},
        ]
        resp = _rpc(proc, {"cmd": "override", "stem": "ArrowGrad", "patches": patches})
        assert resp.get("warnings") in (None, []), resp.get("warnings")
        man = resp["manifest"]
        el = next(e for e in man["elements"] if e["gid"] == "axes_0.arrows_1")
        got = el["arrow_endpoints"]
        for want, have in zip([moved[:2], moved[2:]], got):
            assert abs(want[0] - have[0]) < 0.01 and abs(want[1] - have[1]) < 0.01, \
                (moved, got)
        assert _field_value(man, "axes_0.arrows_1", "arrowstyle") == "->"
        assert _field_value(man, "axes_0.arrows_1", "linestyle") == "--"

        # 全量列表语义：空列表 = 端点与样式全部还原
        resp = _rpc(proc, {"cmd": "override", "stem": "ArrowGrad", "patches": []})
        man = resp["manifest"]
        el = next(e for e in man["elements"] if e["gid"] == "axes_0.arrows_1")
        for want, have in zip(pts, el["arrow_endpoints"]):
            assert abs(want[0] - have[0]) < 0.01 and abs(want[1] - have[1]) < 0.01
        assert _field_value(man, "axes_0.arrows_1", "arrowstyle") == "-|>"
        assert _field_value(man, "axes_0.arrows_1", "linestyle") == "-"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def test_closed_figure_still_builds(tmp_path):
    """脚本 `savefig` 完就 `plt.close(fig)` 时仍要能起来。

    matplotlib 3.11 起 plt.close 会把 canvas 退回 FigureCanvasBase——它没有
    get_renderer，量包围盒时直接 AttributeError，整张图一个元素都出不来。
    「存完就 close」是极常见写法（examples/figures/paper_style.py 就这么写），
    manifest 必须自己补 Agg canvas，不能指望脚本把 figure 留成什么样。
    这条用例在旧版 matplotlib 上恒过，只有装了新版才有意义——CI 特意装最新版。
    """
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig_closed.py").write_text(PLAIN_SCRIPT, encoding="utf-8")

    proc = _spawn(figs / "fig_closed.py", figs, tmp_path)
    try:
        resp = _rpc(proc, {"cmd": "build"})
        stem = sorted(resp["stems"])[0]
        # manifest 真的建出来了（元素非空），而不是只回了个 stem 列表
        resp = _rpc(proc, {"cmd": "override", "stem": stem, "patches": []})
        roles = {e["role"] for e in resp["manifest"]["elements"]}
        assert "title" in roles, roles
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def test_build_without_paper_style(tmp_path):
    """图库里没有 paper_style.py 也必须能起来。

    paper_style 是某些图库的私有方言，不是引擎的依赖。曾经 worker 无保护地
    `import paper_style`，于是任何不带这个模块的图库（比如论文的
    supporting_information 目录）都以 ModuleNotFoundError 开局，一张图也渲染
    不了。顺带覆盖裸 savefig + 动态文件名 + 脚本自己 plt.close 的写法。
    """
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig_plain.py").write_text(PLAIN_SCRIPT, encoding="utf-8")
    assert not (figs / "paper_style.py").exists()

    proc = _spawn(figs / "fig_plain.py", figs, tmp_path)
    try:
        resp = _rpc(proc, {"cmd": "build"})
        assert sorted(resp["stems"]) == ["PlainFig_a", "PlainFig_b"]
        # 拦截仍然生效：真实文件一个都没写出去
        assert not list((figs / "panels").glob("*.pdf"))
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


ARGV_SCRIPT = """\
import sys
from pathlib import Path

import matplotlib.pyplot as plt


def main():
    # 按命令行参数命名输出的脚本；不给参数就用默认名
    names = sys.argv[1:] or ["ArgvFig_default"]
    for name in names:
        fig, ax = plt.subplots(figsize=(2, 1.5))
        ax.plot([0, 1], [0, 1])
        fig.savefig(Path(f"{name}.pdf"))
        plt.close(fig)
"""


def test_script_sees_its_own_argv_not_the_workers(tmp_path):
    """脚本读到的 sys.argv 必须是它自己的，不能是 worker 的内部参数。

    worker 是 `python worker.py --script … --out-dir … --entry main` 起来的，
    不重置 argv 的话 `sys.argv[1:]` 会拿到那一串开关；按参数命名输出的脚本
    于是存出一堆叫 "--entry"/"--out-dir" 的图（试运行探测时当场撞见过，
    注册表会被这些垃圾 stem 灌满）。真跑 `python fig.py` 时 argv 只有脚本自己。
    """
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig_argv.py").write_text(ARGV_SCRIPT, encoding="utf-8")

    proc = _spawn(figs / "fig_argv.py", figs, tmp_path)
    try:
        resp = _rpc(proc, {"cmd": "build"})
        assert sorted(resp["stems"]) == ["ArgvFig_default"]
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


SUBDIR_SCRIPT = """\
from pathlib import Path

import matplotlib.pyplot as plt

import shared_data


def main():
    fig, ax = plt.subplots(figsize=(2, 1.5))
    ax.plot(shared_data.YS)
    fig.savefig((Path("out") / "SubFig_1").with_suffix(".pdf"))
    plt.close(fig)
"""


def test_script_in_subdirectory_can_import_neighbours(tmp_path):
    """面板脚本放子目录（panels/）时，同目录的模块要 import 得到。

    只把图库根加进 sys.path 的话，子目录脚本 import 隔壁模块直接
    ModuleNotFoundError——而「脚本放子目录」正是静态扫描新支持的写法。
    """
    figs = tmp_path / "figures"
    (figs / "panels").mkdir(parents=True)
    (figs / "panels" / "shared_data.py").write_text("YS = [1, 2, 3]\n", encoding="utf-8")
    (figs / "panels" / "fig_sub.py").write_text(SUBDIR_SCRIPT, encoding="utf-8")

    proc = _spawn(figs / "panels" / "fig_sub.py", figs, tmp_path)
    try:
        resp = _rpc(proc, {"cmd": "build"})
        assert sorted(resp["stems"]) == ["SubFig_1"]
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


HANG_SCRIPT = """\
import time


def main():
    # build 阶段永远不返回——死循环 / 极慢计算的脚本对 worker 来说就是这个样子
    time.sleep(600)
"""


def test_shutdown_all_kills_hung_worker(tmp_path, monkeypatch):
    """脚本卡死时 `shutdown_all(wait=True)` 必须真把子进程杀掉，不留僵尸。

    `request()` 在持 `w.lock` 的状态下无超时阻塞 readline，`shutdown()` 抢同一把
    锁也跟着卡死，永远走不到它 finally 里的 `proc.kill()`；旧实现里 join 超时只是
    不再等，卡死的渲染子进程于是成为孤儿，在用户机器上继续跑死循环占 CPU。

    这里走 pool 的真实退出路径（`/api/shutdown` 与 reset_projects 用的就是它），
    而不是 `_spawn` 出来的裸子进程。
    """
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig_hang.py").write_text(HANG_SCRIPT, encoding="utf-8")

    monkeypatch.setattr(pool, "_SHUTDOWN_JOIN_TIMEOUT", 0.5)  # 否则用例要干等 10 秒
    w = pool.get("fig_hang.py", str(figs), "main")
    pid = w.proc.pid

    def hold_the_lock():
        try:
            w.ensure_built()      # 前端的渲染请求；卡在 readline 上不返回
        except Exception:  # noqa: BLE001 — 子进程被 kill 后这里必然抛，与断言无关
            pass

    try:
        threading.Thread(target=hold_the_lock, daemon=True).start()
        deadline = time.time() + 30
        while not w.lock.locked() and time.time() < deadline:
            time.sleep(0.05)
        assert w.lock.locked(), "请求线程没占住 worker 锁，用例前提不成立"

        pool.shutdown_all(figures_dir=str(figs), wait=True)

        assert w.proc.wait(timeout=10) is not None   # 已被 kill 并回收
        if os.name == "posix":
            # 再从 OS 层确认 PID 已消失（没留僵尸）。Windows 上做不了这条探测：
            # Popen 还握着进程句柄时 PID 不会被回收，os.kill(pid, 0) 对
            # 已终止的进程照样成功（死 PID 则抛 EINVAL 而非 ProcessLookupError），
            # 那边 wait() 返回本身就等价于「已终止且句柄可回收」。
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    finally:
        if w.proc.poll() is None:                    # 兜底：绝不在测试机上留僵尸
            w.proc.kill()
            w.proc.wait(timeout=10)


REPLAY_SCRIPT = """\
import matplotlib.pyplot as plt

def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1, 2], [1, 0, 2], label="s")
    ax.text(0.2, 0.3, "note", transform=ax.transAxes)
    ax.legend()
    fig.savefig("ReplayFig.pdf")

    # aspect="equal"（imshow 默认）：draw 时才 apply_aspect，几何变更后
    # 不刷新布局的话 figure 锚定换算会拿旧 transform 落错位置
    fig2, ax2 = plt.subplots(figsize=(4, 3))
    ax2.imshow([[0, 1], [1, 0]])
    ax2.text(0.2, 0.3, "eqnote", transform=ax2.transAxes)
    fig2.savefig("ReplayEq.pdf")
"""


def _anchor_of(manifest, gid):
    el = next(e for e in manifest["elements"] if e["gid"] == gid)
    return el.get("anchor")


def _bbox_of(manifest, gid):
    el = next(e for e in manifest["elements"] if e["gid"] == gid)
    return el["bbox"]


def test_frac_anchored_props_survive_geometry_moves(tmp_path):
    """figure 锚定的位置（pos_frac 等）不得随后续几何变动漂移（FigS3 错位回归）。

    真实事故：用户先拖好一批 axes 文字（pos_frac），随后又挪子图 / 改图幅。
    旧实现里 pos_frac 只在「值变了」的那一次换算进 artist 本地坐标，几何再动
    文字就跟着子图漂走——写回 PDF 定格的是漂移后的样子，重开后全量重放又
    落回声明位置，「文字全部错位」。看护三件事：
      1. 热会话里子图移动后，文字仍钉在声明的 figure 锚点上；
      2. 图幅（size_mm）变化后同样成立；
      3. 清空重放（≈冷启动重放）与热会话逐步应用收敛到同一状态。
    """
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig_replay.py").write_text(REPLAY_SCRIPT, encoding="utf-8")
    proc = _spawn(figs / "fig_replay.py", figs, tmp_path)
    try:
        _rpc(proc, {"cmd": "build"})
        resp = _rpc(proc, {"cmd": "override", "stem": "ReplayFig", "patches": []})
        man = resp["manifest"]
        txt = next(e["gid"] for e in man["elements"]
                   if e["role"] == "text" and "texts_" in e["gid"])
        axg = next(e["gid"] for e in man["elements"] if e["role"] == "axes")

        # 1) 先放文字（pos_frac 在列表里排在几何之前——与真实事故同序）
        p1 = [{"gid": txt, "prop": "pos_frac", "value": [0.3, 0.2]}]
        resp = _rpc(proc, {"cmd": "override", "stem": "ReplayFig", "patches": p1})
        a1 = _anchor_of(resp["manifest"], txt)
        assert a1 == pytest.approx([0.3, 0.2], abs=0.02), a1

        # 2) 再挪子图：文字必须钉在声明的 figure 锚点上，不随子图漂移
        p2 = p1 + [{"gid": axg, "prop": "position", "value": [0.5, 0.5, 0.42, 0.4]}]
        resp = _rpc(proc, {"cmd": "override", "stem": "ReplayFig", "patches": p2})
        a2 = _anchor_of(resp["manifest"], txt)
        assert a2 == pytest.approx([0.3, 0.2], abs=0.02), a2

        # 3) 改图幅后同样成立（分数锚点与物理尺寸无关）
        p3 = p2 + [{"gid": "figure", "prop": "size_mm", "value": [120, 80]}]
        resp = _rpc(proc, {"cmd": "override", "stem": "ReplayFig", "patches": p3})
        a3 = _anchor_of(resp["manifest"], txt)
        assert a3 == pytest.approx([0.3, 0.2], abs=0.02), a3
        bbox_live = _bbox_of(resp["manifest"], txt)

        # 4) 清空再一次性全量应用（= 冷启动重放）：与热会话状态逐位收敛
        _rpc(proc, {"cmd": "override", "stem": "ReplayFig", "patches": []})
        resp = _rpc(proc, {"cmd": "override", "stem": "ReplayFig", "patches": p3})
        bbox_replay = _bbox_of(resp["manifest"], txt)
        assert bbox_replay == pytest.approx(bbox_live, abs=0.005), \
            (bbox_live, bbox_replay)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def test_frac_anchor_exact_on_aspect_equal_axes(tmp_path):
    """aspect="equal"（imshow 方图）的子图：几何变更后 figure 锚点仍逐位可信。

    apply_aspect 只在 draw 时发生——同一次 apply 里「先挪子图、紧接着换算
    pos_frac」若不强制刷新布局，换算用的是未贴合长宽比的旧 transform，
    文字会系统性落偏（FigS3 的 AFM 方图正是这一型）。看护：挪过子图后
    文字 anchor 仍与声明值一致。
    """
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig_replay.py").write_text(REPLAY_SCRIPT, encoding="utf-8")
    proc = _spawn(figs / "fig_replay.py", figs, tmp_path)
    try:
        _rpc(proc, {"cmd": "build"})
        resp = _rpc(proc, {"cmd": "override", "stem": "ReplayEq", "patches": []})
        man = resp["manifest"]
        txt = next(e["gid"] for e in man["elements"]
                   if e["role"] == "text" and "texts_" in e["gid"])
        axg = next(e["gid"] for e in man["elements"] if e["role"] == "axes")

        p = [{"gid": txt, "prop": "pos_frac", "value": [0.35, 0.25]},
             {"gid": axg, "prop": "position", "value": [0.45, 0.4, 0.42, 0.45]}]
        resp = _rpc(proc, {"cmd": "override", "stem": "ReplayEq", "patches": p})
        a = _anchor_of(resp["manifest"], txt)
        assert a == pytest.approx([0.35, 0.25], abs=0.02), a

        # 图幅变化叠加后仍然成立
        p2 = p + [{"gid": "figure", "prop": "size_mm", "value": [130, 90]}]
        resp = _rpc(proc, {"cmd": "override", "stem": "ReplayEq", "patches": p2})
        a2 = _anchor_of(resp["manifest"], txt)
        assert a2 == pytest.approx([0.35, 0.25], abs=0.02), a2
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
