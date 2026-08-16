"""worker 协议 round-trip：build → override → 全量列表还原 → export 保真。

本进程不 import matplotlib——spawn pool.find_worker_python() 找到的科学栈
解释器跑 engine/worker.py，走真实 stdin/stdout JSON 协议。覆盖：
  * 拦截 savefig 捕获 Figure（合成脚本走 paper_style.save 方言）
  * override 全量列表语义：缺失 key 自动恢复原值（undo 的基础）
  * export 应用 patches 后的 PDF 矢量文字保真
"""
import json
import select
import subprocess
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


def _rpc(proc, obj, timeout=120):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    assert ready, f"worker 超时（{timeout}s）: {obj.get('cmd')}"
    line = proc.stdout.readline()
    assert line, f"worker 无响应: {obj.get('cmd')}"
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


@pytest.fixture
def worker(tmp_path):
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "paper_style.py").write_text(PAPER_STYLE_STUB, encoding="utf-8")
    (figs / "fig_test.py").write_text(FIG_SCRIPT, encoding="utf-8")
    out = tmp_path / "out"
    proc = subprocess.Popen(
        [WORKER_PY, str(pool.WORKER_PY),
         "--script", str(figs / "fig_test.py"),
         "--figures-dir", str(figs),
         "--out-dir", str(out),
         "--sandbox", str(tmp_path / "sandbox"),
         "--entry", "main"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1)
    yield proc, out, tmp_path
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
    proc.stdin.write(json.dumps({"cmd": "override", "stem": "nope", "patches": []}) + "\n")
    proc.stdin.flush()
    ready, _, _ = select.select([proc.stdout], [], [], 30)
    assert ready
    resp = json.loads(proc.stdout.readline())
    assert resp["ok"] is False and "nope" in resp["error"]
    assert proc.poll() is None

    _rpc(proc, {"cmd": "ping"})
