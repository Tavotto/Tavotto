"""worker 协议 round-trip：build → override → 全量列表还原 → export 保真。

本进程不 import matplotlib——spawn pool.find_worker_python() 找到的科学栈
解释器跑 engine/worker.py，走真实 stdin/stdout JSON 协议。覆盖：
  * 拦截 savefig 捕获 Figure（合成脚本走 paper_style.save 方言）
  * override 全量列表语义：缺失 key 自动恢复原值（undo 的基础）
  * export 应用 patches 后的 PDF 矢量文字保真
  * 协议 v1 信封（文件末尾一节）：回显、错误 code、hash 自检、legacy 兼容
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

from magplot.engine import patchspec, pool

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


def test_export_is_state_neutral(worker):
    """export 是**一次性动作**，不得把它那组 patches 留在常驻 figure 上。

    真实后果：历史版本恢复（重放旧 patches）和画布导出（每个面板各带一套
    overrides）之后，热会话的真实状态与前端手里的 lastPatches 错位——override
    的「全量列表」语义会拿着错的 applied 表去还原，用户看到的图开始漂。
    看护方式与 preview_png 同构：导出前后的 render_png 必须逐字节相同。
    """
    proc, out, tmp = worker
    _rpc(proc, {"cmd": "build"})
    man = json.loads((out / "TestFig_a.json").read_text(encoding="utf-8"))
    title_gid = next(
        el["gid"] for el in man["elements"]
        for f in el.get("editable", [])
        if f["prop"] == "text" and f["value"] == "Original Title")

    patch = [{"gid": title_gid, "prop": "text", "value": "Hot Session Title"}]
    _rpc(proc, {"cmd": "override", "stem": "TestFig_a", "patches": patch})
    resp = _rpc(proc, {"cmd": "render_png", "stem": "TestFig_a", "width": 400})
    png_before = Path(resp["path"]).read_bytes()

    # 用另一组 patches 导出（空列表 = 脚本原始状态，与热会话状态截然不同）
    pdf = tmp / "neutral.pdf"
    _rpc(proc, {"cmd": "export", "stem": "TestFig_a", "patches": [],
                "path": str(pdf), "format": "pdf", "dpi": 200})
    with pymupdf.open(pdf) as doc:
        assert "Original Title" in doc[0].get_text()   # 导出确实用的是自己那组

    resp = _rpc(proc, {"cmd": "render_png", "stem": "TestFig_a", "width": 400})
    png_after = Path(resp["path"]).read_bytes()
    assert png_after == png_before, "export 污染了热会话状态"


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


def test_request_timeout_kills_and_rebuilds_worker(tmp_path, monkeypatch):
    """死循环脚本必须以「超时」收场，而不是把会话永久占死。

    旧实现里 `request()` 持着 `w.lock` 无超时阻塞 readline：一个死循环脚本就
    让这个 (项目, 脚本) 的会话从此谁也用不了，连 `shutdown()` 都抢不到锁。
    看护三件事：报 code=worker_timeout、进程真被杀掉、下一次 get() 能重建。
    """
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig_hang.py").write_text(HANG_SCRIPT, encoding="utf-8")

    monkeypatch.setattr(pool, "BUILD_TIMEOUT", 2.0)   # 否则用例要干等 15 分钟
    w = pool.get("fig_hang.py", str(figs), "main")
    try:
        with pytest.raises(pool.WorkerError) as e:
            w.ensure_built()
        assert e.value.code == "worker_timeout"
        assert "重试" in str(e.value)                 # 告诉用户能怎么办

        assert w.proc.wait(timeout=10) is not None    # 已被 kill 并回收
        assert not w.alive()

        # 状态未知的会话绝不复用：下一次请求原地重建一个新进程
        w2 = pool.get("fig_hang.py", str(figs), "main")
        assert w2 is not w and w2.alive()
    finally:
        pool.shutdown_all(figures_dir=str(figs), wait=True)


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


# =========================== 协议 v1（ADR 0003） ============================
# worker 双栈兼容：带 protocol_version 走 v1，不带的照旧。这一组用例是
# Rust supervisor（Phase C）接手前把契约钉在 Python 两侧的地方——信封字段、
# 回显语义、错误 code、legacy 兼容，改一条就得先改 ADR。

def _v1(cmd, *, stem=None, payload=None, rid="r-test-1",
        generation=7, revision=3, patch_hash=None):
    env = {"protocol_version": 1, "request_id": rid,
           "worker_generation": generation, "render_revision": revision,
           "cmd": cmd, "payload": payload or {}}
    if stem is not None:
        env["stem"] = stem
    if patch_hash is not None:
        env["canonical_patch_hash"] = patch_hash
    return env


def _raw(proc, env, timeout=120):
    """发一条请求读一行回应，**不断言 ok**（错误信封用例要的就是 ok=false）。"""
    proc.stdin.write(json.dumps(env, ensure_ascii=False) + "\n")
    proc.stdin.flush()
    box: list = []
    reader = threading.Thread(target=lambda: box.append(proc.stdout.readline()),
                              daemon=True)
    reader.start()
    reader.join(timeout)
    assert not reader.is_alive(), f"worker 超时（{timeout}s）: {env.get('cmd')}"
    line = box[0] if box else ""
    assert line, f"worker 无响应: {env.get('cmd')}\n--- stderr ---\n{_drain(proc)}"
    return json.loads(line)


def _ok(proc, env, timeout=120):
    resp = _raw(proc, env, timeout)
    assert resp.get("ok"), resp
    return resp


def test_v1_envelope_echoes_generation_revision_and_hash(worker):
    """v1 全链路 build → render → export，信封字段一律**原样回显**。

    generation/revision/hash 是 supervisor 的账本，worker 插手就多一个可能
    对不上的地方；回显让上层把响应对回请求（超时重建后的迟到响应靠它识别）。
    """
    proc, out, tmp = worker

    resp = _ok(proc, _v1("build", rid="r-b1", generation=2, revision=0))
    assert resp["protocol_version"] == 1
    assert resp["request_id"] == "r-b1"
    assert resp["worker_generation"] == 2 and resp["render_revision"] == 0
    assert "TestFig_a" in resp["stems"]

    man = json.loads((out / "TestFig_a.json").read_text(encoding="utf-8"))
    title_gid = next(
        el["gid"] for el in man["elements"]
        for f in el.get("editable", [])
        if f["prop"] == "text" and f["value"] == "Original Title")
    patch = [{"gid": title_gid, "prop": "text", "value": "V1 Title"}]
    h = patchspec.patch_hash(patch)

    resp = _ok(proc, _v1("render", stem="TestFig_a", payload={"patches": patch},
                         rid="r-r1", generation=2, revision=5, patch_hash=h))
    assert resp["request_id"] == "r-r1" and resp["render_revision"] == 5
    assert resp["canonical_patch_hash"] == h
    assert "hash_mismatch" not in resp          # 两侧规范化实现一致
    assert _text_value(resp["manifest"], title_gid) == "V1 Title"
    assert resp["warnings"] == []

    pdf = tmp / "v1_export.pdf"
    resp = _ok(proc, _v1("export", stem="TestFig_a",
                         payload={"patches": patch, "path": str(pdf),
                                  "format": "pdf", "dpi": 200},
                         rid="r-e1", patch_hash=h))
    assert resp["request_id"] == "r-e1" and resp["path"] == str(pdf)
    with pymupdf.open(pdf) as doc:
        assert "V1 Title" in doc[0].get_text()

    # render_png / preview_png / ping 也在 v1 命令集里
    resp = _ok(proc, _v1("render_png", stem="TestFig_a", payload={"width": 300},
                         rid="r-p1"))
    assert Path(resp["path"]).exists()
    resp = _ok(proc, _v1("preview_png", stem="TestFig_a",
                         payload={"patches": [], "width": 300, "tag": "hist1"},
                         rid="r-p2"))
    assert Path(resp["path"]).exists()
    assert _ok(proc, _v1("ping", rid="r-ping"))["request_id"] == "r-ping"


def test_v1_error_envelope_shapes(worker):
    """错误信封：code / retryable / message / traceback + 回显；进程不退出。"""
    proc, out, tmp = worker
    _ok(proc, _v1("build", rid="r-b2"))

    resp = _raw(proc, _v1("render", stem="nope", payload={"patches": []},
                          rid="r-err1"))
    assert resp["ok"] is False
    assert resp["protocol_version"] == 1 and resp["request_id"] == "r-err1"
    err = resp["error"]
    assert err["code"] == "unknown_stem" and err["retryable"] is False
    assert "nope" in err["message"]
    assert "TestFig_a" in err["known"]          # 告诉调用方有哪些 stem

    resp = _raw(proc, _v1("frobnicate", rid="r-err2"))
    assert resp["error"]["code"] == "unknown_cmd"
    assert "render" in resp["error"]["known"]

    # 信封字段缺失/类型错 → bad_request
    for bad in ({"protocol_version": 1, "cmd": "ping"},                    # 无 rid
                {"protocol_version": 1, "request_id": "r-x", "cmd": 1},    # cmd 非串
                {"protocol_version": 1, "request_id": "r-x", "cmd": "ping",
                 "payload": []},                                          # payload 非对象
                {"protocol_version": 99, "request_id": "r-x", "cmd": "ping"}):
        resp = _raw(proc, bad)
        assert resp["ok"] is False, bad
        assert resp["error"]["code"] == "bad_request", bad

    # export 缺 path 也是 bad_request（不是 internal——调用方写错了）
    resp = _raw(proc, _v1("export", stem="TestFig_a", payload={"patches": []},
                          rid="r-err3"))
    assert resp["error"]["code"] == "bad_request"

    assert proc.poll() is None                  # 全程没把 worker 打死
    _ok(proc, _v1("ping", rid="r-alive"))


def test_v1_hash_mismatch_is_flagged_but_still_executed(worker):
    """哈希对不上 = 两侧序列化分歧，**标记但照常执行**。

    当场拒绝会把一个可观测的对齐问题变成一次用户可见的渲染失败；标记 +
    stderr 警告则让上层（未来的 Rust supervisor）自己发现并去修。
    """
    proc, out, tmp = worker
    _ok(proc, _v1("build", rid="r-b3"))
    man = json.loads((out / "TestFig_a.json").read_text(encoding="utf-8"))
    title_gid = next(
        el["gid"] for el in man["elements"]
        for f in el.get("editable", [])
        if f["prop"] == "text" and f["value"] == "Original Title")
    patch = [{"gid": title_gid, "prop": "text", "value": "Mismatch Title"}]

    resp = _ok(proc, _v1("render", stem="TestFig_a", payload={"patches": patch},
                         rid="r-hm", patch_hash="sha256:" + "0" * 64))
    assert resp["hash_mismatch"] is True
    assert resp["worker_patch_hash"] == patchspec.patch_hash(patch)
    assert resp["canonical_patch_hash"] == "sha256:" + "0" * 64   # 仍原样回显
    assert _text_value(resp["manifest"], title_gid) == "Mismatch Title"

    # 等价写法（乱序 + 重复条目）算出的哈希一致，不该报 mismatch
    equivalent = [{"gid": title_gid, "prop": "text", "value": "早写的"},
                  {"gid": title_gid, "prop": "text", "value": "Mismatch Title"}]
    resp = _ok(proc, _v1("render", stem="TestFig_a",
                         payload={"patches": equivalent}, rid="r-hm2",
                         patch_hash=patchspec.patch_hash(patch)))
    assert "hash_mismatch" not in resp


def test_v1_cancel_is_an_honest_idempotent_noop(worker):
    """cancel 不假装能中断 matplotlib：串行 worker 读到它时目标早已结束。"""
    proc, out, tmp = worker
    _ok(proc, _v1("build", rid="r-b4"))

    resp = _ok(proc, _v1("cancel", payload={"request_id": "r-b4"}, rid="r-c1"))
    assert resp["cancelled"] is False and resp["seen"] is True
    assert "已执行完毕" in resp["note"]

    resp = _ok(proc, _v1("cancel", payload={"request_id": "r-未见过"}, rid="r-c2"))
    assert resp["cancelled"] is False and resp["seen"] is False

    # 幂等：再取消一次还是同一个答案
    assert _ok(proc, _v1("cancel", payload={"request_id": "r-b4"},
                         rid="r-c3"))["seen"] is True
    resp = _raw(proc, _v1("cancel", rid="r-c4"))          # 没给目标 id
    assert resp["error"]["code"] == "bad_request"


def test_legacy_envelope_keeps_the_old_response_shape(worker):
    """无 protocol_version 的老信封必须**一字不变**地按旧形状回应。

    手工 `echo '{"cmd":"build"}' | python worker.py …` 调试、以及任何还没
    切过来的调用方都靠它；双栈兼容不是「顺便也能跑」，是明确的契约。
    """
    proc, out, tmp = worker
    resp = _rpc(proc, {"cmd": "build"})
    assert "protocol_version" not in resp and "request_id" not in resp
    assert set(resp) == {"ok", "stems"}

    resp = _rpc(proc, {"cmd": "override", "stem": "TestFig_a", "patches": []})
    assert set(resp) == {"ok", "manifest", "warnings"}

    # 老的错误形状也不变：扁平 error 字符串 + known
    proc.stdin.write(json.dumps({"cmd": "override", "stem": "nope",
                                 "patches": []}) + "\n")
    proc.stdin.flush()
    box: list = []
    reader = threading.Thread(target=lambda: box.append(proc.stdout.readline()),
                              daemon=True)
    reader.start()
    reader.join(30)
    resp = json.loads(box[0])
    assert resp["ok"] is False
    assert isinstance(resp["error"], str) and "nope" in resp["error"]
    assert resp["known"] == sorted(["TestFig_a", "TestFig_3d", "TestFig_sc"])

    resp = _rpc(proc, {"cmd": "ping"})
    assert set(resp) == {"ok"}


def test_v1_script_error_is_not_retryable(tmp_path):
    """用户脚本自己炸了 → script_error / retryable=false（重试还是同一个结果）。"""
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig_boom.py").write_text(
        "def main():\n    raise ValueError('脚本自己炸了')\n", encoding="utf-8")
    proc = _spawn(figs / "fig_boom.py", figs, tmp_path)
    try:
        resp = _raw(proc, _v1("build", rid="r-boom"))
        assert resp["ok"] is False and resp["request_id"] == "r-boom"
        err = resp["error"]
        assert err["code"] == "script_error" and err["retryable"] is False
        assert "脚本自己炸了" in err["message"]
        assert "ValueError" in err["traceback"]      # 真正的原因原样带着
        assert proc.poll() is None                   # 进程不退出，可继续排障
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def test_pool_speaks_v1_end_to_end(tmp_path):
    """pool 侧真实链路：EngineWorker 发 v1、带 generation、结果照旧可用。"""
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "paper_style.py").write_text(PAPER_STYLE_STUB, encoding="utf-8")
    (figs / "fig_test.py").write_text(FIG_SCRIPT, encoding="utf-8")
    try:
        w = pool.get("fig_test.py", str(figs), "main")
        assert w.generation >= 1
        resp = w.override("TestFig_a", [])
        assert "manifest" in resp and w.rev == 1

        # 同一池键重建一次：generation 必须 +1（supervisor 靠它认代）
        w.proc.kill()
        w.proc.wait(timeout=10)
        w2 = pool.get("fig_test.py", str(figs), "main")
        assert w2 is not w and w2.generation == w.generation + 1
    finally:
        pool.shutdown_all(figures_dir=str(figs), wait=True)


def test_v1_bad_payload_args_are_bad_request_not_internal(worker):
    """width/dpi 写错类型 = 调用方的错（bad_request），不是 internal。

    这条分界不能靠「在渲染里 catch ValueError」实现——matplotlib 画图时
    自己就会抛 ValueError，那样排障会被指到完全错误的方向。
    """
    proc, out, tmp = worker
    _ok(proc, _v1("build", rid="r-b5"))
    resp = _raw(proc, _v1("render_png", stem="TestFig_a",
                          payload={"width": "很宽"}, rid="r-bad1"))
    assert resp["error"]["code"] == "bad_request"
    resp = _raw(proc, _v1("export", stem="TestFig_a",
                          payload={"patches": [], "path": str(tmp / "x.pdf"),
                                   "dpi": {"nope": 1}}, rid="r-bad2"))
    assert resp["error"]["code"] == "bad_request"
    resp = _raw(proc, _v1("render", stem="TestFig_a",
                          payload={"patches": "不是数组"}, rid="r-bad3"))
    assert resp["error"]["code"] == "bad_request"
    assert proc.poll() is None


def test_v1_timings_have_the_documented_shape(worker):
    """真 worker 的阶段计时：键齐、都是数、量级说得通。

    这些数字要拿去做「慢在哪一段」的判断（docs/perf-baseline.md），所以
    形状必须钉住：build 给 script_exec/script_build，render 给
    patch_apply/canvas_draw/manifest，export 给 patch_apply/export。
    **不给 `svg_ms`**——SVG 序列化与 draw 在 matplotlib 里分不开，合并在
    canvas_draw_ms 里（ADR 0003 §9）。
    """
    proc, out, tmp = worker

    resp = _ok(proc, _v1("build", rid="r-t1"))
    t = resp["timings"]
    assert set(t) == {"script_exec_ms", "script_build_ms"}
    assert all(isinstance(v, float) for v in t.values())
    # build = 跑脚本 + instrument + 每个 stem 的首次预览，必然 ≥ 脚本本身
    assert 0 < t["script_exec_ms"] <= t["script_build_ms"]

    resp = _ok(proc, _v1("render", stem="TestFig_a", payload={"patches": []},
                         rid="r-t2"))
    t = resp["timings"]
    assert set(t) == {"patch_apply_ms", "canvas_draw_ms", "manifest_ms"}
    assert "svg_ms" not in t
    assert all(isinstance(v, float) and v >= 0 for v in t.values())
    assert t["canvas_draw_ms"] > 0 and t["manifest_ms"] > 0

    pdf = tmp / "timed_export.pdf"
    resp = _ok(proc, _v1("export", stem="TestFig_a",
                         payload={"patches": [], "path": str(pdf),
                                  "format": "pdf", "dpi": 150}, rid="r-t3"))
    t = resp["timings"]
    assert set(t) == {"patch_apply_ms", "export_ms"} and t["export_ms"] > 0

    # 第二次 build 是 no-op：计时表为空而不是凭空冒出一个 script_build_ms
    assert _ok(proc, _v1("build", rid="r-t4"))["timings"] == {}
    # 不带计时的命令不许多出这个键
    assert "timings" not in _ok(proc, _v1("render_png", stem="TestFig_a",
                                          payload={"width": 200}, rid="r-t5"))


def test_v1_preview_dpi_is_optional_and_validated(worker):
    """按请求给预览 dpi：给了就用，写错是 bad_request（不是 internal）。

    这是 Phase E 唯一一条有数据支撑的旋钮（含 imshow 的面板上 200→100 让
    savefig 从 ~29ms 降到 ~17ms、SVG 从 827KB 降到 196KB；纯矢量图上毫无
    影响）。**前端目前不发**，交互降质归 Phase F 判断。
    """
    proc, out, tmp = worker
    _ok(proc, _v1("build", rid="r-d0"))

    # 给了就照常渲染（矢量图上产物一致，只有嵌入位图会变）
    resp = _ok(proc, _v1("render", stem="TestFig_a",
                         payload={"patches": [], "preview_dpi": 72}, rid="r-d1"))
    assert (out / "TestFig_a.svg").exists() and resp["manifest"]["elements"]

    for bad in (0, -10, "很高"):
        resp = _raw(proc, _v1("render", stem="TestFig_a",
                              payload={"patches": [], "preview_dpi": bad},
                              rid=f"r-d-{bad}"))
        assert resp["error"]["code"] == "bad_request", (bad, resp)
    assert proc.poll() is None


# ================== workerd 控制面（ADR 0004，Phase C） ==================
# 上面那些用例跑的是 Python 池（conftest 把 MAGPLOT_WORKERD 钉成 0，Python 实现
# 始终是参考实现）。这一节把**同样几件事**在 Rust supervisor 上再走一遍：
# 渲染 / 全量列表还原 / 导出状态中立 / 超时重建。两条控制面在这些语义上必须
# 逐条一致——有一条不一致，用户就会在「装没装 workerd」之间看到不同的图。

def _workerd_binary() -> str | None:
    """忽略 conftest 的默认禁用开关，只看 cargo 产物在不在。"""
    saved = os.environ.pop("MAGPLOT_WORKERD", None)
    try:
        from magplot.engine import workerd_client
        return workerd_client.find_workerd()
    finally:
        if saved is not None:
            os.environ["MAGPLOT_WORKERD"] = saved


WORKERD_EXE = _workerd_binary()
needs_workerd = pytest.mark.skipif(
    WORKERD_EXE is None,
    reason="没有 magplot-workerd 产物（先在 workerd/ 里 cargo build）")


@pytest.fixture
def workerd_figs(tmp_path, monkeypatch):
    """一个用 workerd 控制面的图库目录。"""
    from magplot.engine import workerd_client

    monkeypatch.setenv("MAGPLOT_WORKERD", WORKERD_EXE or "0")
    workerd_client.reset_client()
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "paper_style.py").write_text(PAPER_STYLE_STUB, encoding="utf-8")
    (figs / "fig_test.py").write_text(FIG_SCRIPT, encoding="utf-8")
    try:
        yield figs
    finally:
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        workerd_client.reset_client()


def _title_gid(worker, stem="TestFig_a"):
    man = json.loads((worker.out_dir / f"{stem}.json").read_text(encoding="utf-8"))
    return next(el["gid"] for el in man["elements"]
                for f in el.get("editable", [])
                if f["prop"] == "text" and f["value"] == "Original Title")


@needs_workerd
def test_workerd_render_and_full_list_restore(workerd_figs):
    """workerd 路径的 build → render → 全量列表还原，与 Python 池同语义。"""
    w = pool.get("fig_test.py", str(workerd_figs), "main")
    assert isinstance(w, pool.WorkerdWorker), "应当走 workerd 控制面"
    assert w.generation >= 1

    w.ensure_built()
    gid = _title_gid(w)
    resp = w.override("TestFig_a", [{"gid": gid, "prop": "text", "value": "Workerd Title"}])
    assert _text_value(resp["manifest"], gid) == "Workerd Title"
    assert w.svg_path("TestFig_a").exists()
    assert w.rev == 1

    # 空列表 = 撤销全部，自动恢复原值（undo 的基础）
    resp = w.override("TestFig_a", [])
    assert _text_value(resp["manifest"], gid) == "Original Title"


@needs_workerd
def test_workerd_export_is_state_neutral(workerd_figs, tmp_path):
    """导出是一次性动作，不得把它那组 patches 留在常驻 figure 上。

    与 Python 池的 `test_export_is_state_neutral` 同一条断言（导出前后的
    render_png 逐字节相同）——控制面换了，这条数据损坏级的保证不许松。
    """
    w = pool.get("fig_test.py", str(workerd_figs), "main")
    gid = _title_gid(w) if w.built else (w.ensure_built(), _title_gid(w))[1]

    w.override("TestFig_a", [{"gid": gid, "prop": "text", "value": "Hot Session Title"}])
    png_before = w.render_png("TestFig_a", 400).read_bytes()

    pdf = tmp_path / "workerd_neutral.pdf"
    w.export("TestFig_a", [], str(pdf), "pdf", 200)
    with pymupdf.open(pdf) as doc:
        assert "Original Title" in doc[0].get_text()   # 导出用的是自己那组

    assert w.render_png("TestFig_a", 400).read_bytes() == png_before, \
        "export 污染了热会话状态"


@needs_workerd
def test_workerd_export_keeps_vector_text(workerd_figs, tmp_path):
    w = pool.get("fig_test.py", str(workerd_figs), "main")
    w.ensure_built()
    gid = _title_gid(w)
    patch = [{"gid": gid, "prop": "text", "value": "Vector Title"}]
    pdf = tmp_path / "workerd_export.pdf"
    w.export("TestFig_a", patch, str(pdf), "pdf", 300)
    with pymupdf.open(pdf) as doc:
        text = doc[0].get_text()
    assert "Vector Title" in text and "series-a" in text


@needs_workerd
def test_workerd_unknown_stem_is_structured_and_the_session_survives(workerd_figs):
    """业务错误不该把会话打死（只有状态未知的失败才 kill）。"""
    w = pool.get("fig_test.py", str(workerd_figs), "main")
    w.ensure_built()
    with pytest.raises(pool.WorkerError) as e:
        w.override("nope", [])
    assert e.value.code == "unknown_stem"
    assert w.alive(), "普通业务错误不该把会话标死"
    assert "manifest" in w.override("TestFig_a", [])


@needs_workerd
def test_workerd_timeout_kills_and_rebuilds(tmp_path, monkeypatch):
    """死循环脚本必须以超时收场，且下一次 get() 能拿到一条新会话。

    对照 Python 池的 `test_request_timeout_kills_and_rebuilds_worker`：
    报 code=worker_timeout、`alive()` 转 False、`get()` 原地重建。
    """
    from magplot.engine import workerd_client

    if WORKERD_EXE is None:
        pytest.skip("没有 magplot-workerd 产物")
    monkeypatch.setenv("MAGPLOT_WORKERD", WORKERD_EXE)
    workerd_client.reset_client()
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig_hang.py").write_text(HANG_SCRIPT, encoding="utf-8")
    monkeypatch.setattr(pool, "BUILD_TIMEOUT", 3.0)   # 否则要干等 15 分钟
    try:
        w = pool.get("fig_hang.py", str(figs), "main")
        assert isinstance(w, pool.WorkerdWorker)
        with pytest.raises(pool.WorkerError) as e:
            w.ensure_built()
        assert e.value.code == "worker_timeout"
        assert "重试" in str(e.value)
        assert not w.alive()

        w2 = pool.get("fig_hang.py", str(figs), "main")
        assert w2 is not w and w2.alive()
    finally:
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        workerd_client.reset_client()


# ================== 写回事务全链路（真 matplotlib + Flask） ==================
# 上面测的是 worker 协议本身。这一节走**产品路径**：Flask 的
# `/api/engine/update_source` → 一次性 worker 干净重放 → 与热态 manifest 比几何
# → 原子替换磁盘上的原件。看护的是 ADR 里那条不变式：
#   热态所见 == 写进文件的 == 重开后重放出来的。
# 假 worker 测不到这条——那里 manifest 是我们自己造的，重放与热态天然一致。

REGISTRY = json.dumps({"version": 1, "scripts": {
    "fig_test.py": {"entry": "main", "cost": "light", "notes": "",
                    "stems": ["TestFig_a", "TestFig_3d", "TestFig_sc"]},
}})


def _write_back_project(tmp_path, monkeypatch):
    """真图库 + 真渲染的 Flask test client。返回 (app 模块, client, figs)。"""
    from magplot import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")

    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "paper_style.py").write_text(PAPER_STYLE_STUB, encoding="utf-8")
    (figs / "fig_test.py").write_text(FIG_SCRIPT, encoding="utf-8")
    (figs / "mm_registry.json").write_text(REGISTRY, encoding="utf-8")
    # 写回覆盖的是磁盘上**已有**的原件（真实图库里它由脚本跑出来），先放一张
    doc = pymupdf.open()
    doc.new_page(width=200, height=100)
    doc.save(figs / "TestFig_a.pdf")
    doc.close()
    m.open_project(str(figs))
    return m, m.app.test_client(), figs


@pytest.fixture
def write_back(tmp_path, monkeypatch):
    from magplot import app as m

    ctx = _write_back_project(tmp_path, monkeypatch)
    try:
        yield ctx
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(ctx[2]), wait=True)
        pool.stop_watcher()


def _figs3_patches(client) -> tuple[list, str]:
    """FigS3 型的一组 patch：几何（子图 position）+ figure 锚定的文字位置。

    这正是当年出事的组合——几何一变，pos_frac 这类锚在 figure 分数上的属性
    必须被重放，否则热会话的状态与全量重放对不上。
    """
    resp = client.post("/api/engine/render",
                       json={"id": "TestFig_a.pdf", "patches": []})
    assert resp.status_code == 200, resp.get_json()
    man = resp.get_json()["manifest"]
    title_gid = next(el["gid"] for el in man["elements"]
                     for f in el.get("editable", [])
                     if f["prop"] == "text" and f["value"] == "Original Title")
    return [
        {"gid": "axes_0", "prop": "position", "value": [0.22, 0.20, 0.60, 0.62]},
        {"gid": title_gid, "prop": "text", "value": "Vector Title"},
        {"gid": title_gid, "prop": "pos_frac", "value": [0.46, 0.10]},
    ], title_gid


def _run_write_back(client, figs):
    """热会话应用 patches → 写回；返回 (响应体, patches)。"""
    from magplot.engine import patchspec as ps

    patches, _gid = _figs3_patches(client)
    r = client.post("/api/engine/render",
                    json={"id": "TestFig_a.pdf", "patches": patches})
    assert r.status_code == 200, r.get_json()

    resp = client.post("/api/engine/update_source",
                       json={"id": "TestFig_a.pdf", "patches": patches,
                             "expected_mtime": int((figs / "TestFig_a.pdf")
                                                   .stat().st_mtime)})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["patch_hash"] == ps.patch_hash(patches)
    return body, patches


def test_write_back_verifies_a_clean_replay_and_keeps_vector_text(write_back):
    """真链路：热态拖过文字、挪过子图 → 写回通过干净重放校验，产物仍是矢量。"""
    m, client, figs = write_back
    body, patches = _run_write_back(client, figs)

    assert body["verification"]["replay"] == "ok", body["verification"]
    assert body["verification"]["elements"] > 0
    assert body["updated"] == ["TestFig_a.pdf"]       # 图库里只有 PDF 这一份
    assert body["warnings"] == []
    assert "post_check" not in body, "落盘后的页面尺寸该与重放 manifest 一致"
    assert body["source_sha1"]["TestFig_a.pdf"] == m._sha1_of(figs / "TestFig_a.pdf")

    # 导出保真：写回的 PDF 里文字仍是矢量（不是栅格化的一张图）
    with pymupdf.open(figs / "TestFig_a.pdf") as doc:
        text = doc[0].get_text()
    assert "Vector Title" in text and "series-a" in text
    assert "x label" in text

    # 事务收尾：图库无半成品，缓存里没留下一次性会话目录
    assert not list(figs.glob(".*updating"))
    assert not list(pool.ENGINE_CACHE.glob("_replay-*")), "一次性 worker 的目录泄漏了"

    # 版本历史带上权威 patch_hash，热会话照常可用（重放没有动它）
    ctx = m.PROJECTS[m._project_id(figs.resolve())]
    assert m.load_baked(ctx)["TestFig_a"]["versions"][-1]["patch_hash"] == body["patch_hash"]
    again = client.post("/api/engine/render",
                        json={"id": "TestFig_a.pdf", "patches": patches})
    assert again.status_code == 200, again.get_json()


def test_write_back_blocks_when_the_script_changed_mid_session(write_back):
    """脚本在会话背后被改（watcher 的 2 秒轮询窗口）→ 阻断，原件零改动。"""
    _m, client, figs = write_back
    patches, _gid = _figs3_patches(client)
    client.post("/api/engine/render", json={"id": "TestFig_a.pdf", "patches": patches})
    before = (figs / "TestFig_a.pdf").read_bytes()

    (figs / "fig_test.py").write_text(FIG_SCRIPT + "\n# touched\n", encoding="utf-8")
    resp = client.post("/api/engine/update_source",
                       json={"id": "TestFig_a.pdf", "patches": patches})
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "script_changed"
    assert (figs / "TestFig_a.pdf").read_bytes() == before
    assert not list(figs.glob(".*updating"))


@needs_workerd
def test_workerd_write_back_replays_without_leaking_a_session(tmp_path, monkeypatch):
    """workerd 路径同语义，且**一次性会话不泄漏**。

    workerd 按 spawn 规格哈希复用会话（引用计数，ADR 0004）：重放会话靠独立的
    out_dir + 一次性 salt env 拿到自己的那条。写完之后 supervisor 手里只该剩下
    热会话——泄漏的话每写回一次就多端一份整套 Figure 的内存。
    """
    from magplot import app as m
    from magplot.engine import workerd_client

    monkeypatch.setenv("MAGPLOT_WORKERD", WORKERD_EXE or "0")
    workerd_client.reset_client()
    _m, client, figs = _write_back_project(tmp_path, monkeypatch)
    try:
        worker = pool.get("fig_test.py", str(figs), "main")
        assert isinstance(worker, pool.WorkerdWorker), "应当走 workerd 控制面"

        body, _patches = _run_write_back(client, figs)
        assert body["verification"]["replay"] == "ok", body["verification"]
        with pymupdf.open(figs / "TestFig_a.pdf") as doc:
            assert "Vector Title" in doc[0].get_text()

        sessions = workerd_client.client().call("sessions", timeout=10.0)["sessions"]
        assert len(sessions) == 1, f"重放会话没被回收: {sessions}"
        assert not list(pool.ENGINE_CACHE.glob("_replay-*"))
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        pool.stop_watcher()
        workerd_client.reset_client()
