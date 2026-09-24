"""只改外观不改科学：颜色 / 字号 / 线宽 / 图例位置 / 图幅这类 override 之后，数据点、刻度数值、单位与源文件原样。

QA 2026-09-24 §5 SCI-02。manifest 不带数据数组（它只报几何与可编辑字段），所以真值从**产物**里量：
worker 按 override 重新序列化出的 PDF，用 PyMuPDF（与 matplotlib / RenderCore 都不同源）抽出数据曲线的
折线顶点，按同一张图里坐标区底色矩形（测试脚本给它一个独有的颜色）归一化成 axes 分数坐标。外观改动
（包括把图幅从 81×61 mm 改成 120×70 mm）之后，这组归一化顶点必须与零 override 时逐点一致——
它们只由数据与坐标范围决定。带单位的轴标签用文字层比（刻度个数随图幅由 locator 重选，不比）；源脚本与数据文件比 sha256，
写回原件之后再比一次（写回只许动图库里的 PDF/PNG）。

判据的主语：**产物里那条曲线**（不是 manifest、不是请求）。反证：把 patch 换成改 `ylim` 的非外观 patch，
归一化顶点必然变——`test_the_ruler_sees_a_real_data_change` 钉住尺子是活的。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

from tavotto.engine import exportjob, pool, project_watch

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

AX_FACE = "#f4f1e8"  # 坐标区底色：独有，用来在 PDF 里找坐标区矩形
LINE0 = "#1f77b4"

SCRIPT = f"""\
import matplotlib.pyplot as plt


def main():
    with open("points.csv", encoding="utf-8") as fh:
        ys = [float(v) for v in fh.read().split(",")]
    xs = list(range(len(ys)))
    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.set_facecolor("{AX_FACE}")
    ax.plot(xs, ys, color="{LINE0}", label="signal")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Voltage (mV)")
    ax.set_title("Raw")
    ax.legend(loc="upper left")
    # 3.2×2.4 in 的默认边距会把 xlabel 裁到页面外（原生 matplotlib 同样如此），留出边距
    fig.subplots_adjust(left=0.2, bottom=0.22)
    fig.savefig("Fig1.pdf")
"""

REGISTRY = json.dumps(
    {
        "version": 1,
        "scripts": {"fig1.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["Fig1"]}},
    }
)
POINTS = "0,9,2,10,4,7.5"


@pytest.fixture
def project(tmp_path, monkeypatch):
    import pikepdf

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
    (figs / "points.csv").write_text(POINTS, encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(REGISTRY, encoding="utf-8")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 100))
    pdf.save(figs / "Fig1.pdf")
    m.open_project(str(figs))
    exportjob.reset_for_tests()
    try:
        yield m, m.app.test_client(), figs
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _rgb(hexcolor: str) -> tuple[float, float, float]:
    h = hexcolor.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))


def _close(a, b, tol=0.01) -> bool:
    return a is not None and all(abs(x - y) <= tol for x, y in zip(a, b))


def _manifest(client, patches):
    r = client.post("/api/engine/render", json={"id": "Fig1.pdf", "patches": patches})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["manifest"]


def _gid(manifest, prop: str, value) -> str:
    return next(
        el["gid"]
        for el in manifest["elements"]
        for f in el.get("editable", [])
        if f["prop"] == prop and f.get("value") == value
    )


def _materialize(client) -> None:
    """占位原件 → 脚本真跑出来的原件（经写回事务的干净重放，零 override）。

    零 override 的原图导出取的是磁盘原件；夹具放的是占位页，不先换掉它，「Before」量的就是一张白纸。
    """
    r = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": []})
    assert r.status_code == 200, r.get_json()


def _original_pdf(client, name: str, patches: list) -> Path:
    r = client.post(
        "/api/export",
        json={
            "scope": "original",
            "filename": name,
            "formats": ["pdf"],
            "original": {"figure_id": "Fig1.pdf", "source_kind": "vector", "overrides": patches},
        },
    )
    body = r.get_json()
    assert r.status_code == 200 and body["status"] == "done", body
    out = next(o for o in body["outputs"] if o["format"] == "pdf")
    return Path(body["export_dir"]) / out["name"]


def _science(pdf: Path, line_color: str) -> dict:
    """产物里的科学事实：曲线的 axes 分数坐标顶点 + 文字层。"""
    with pymupdf.open(pdf) as doc:
        page = doc[0]
        drawings = page.get_drawings()
        text = page.get_text()
    face = [d for d in drawings if _close(d.get("fill"), _rgb(AX_FACE))]
    assert len(face) == 1, f"坐标区底色矩形应恰有一个，找到 {len(face)}"
    ax = face[0]["rect"]
    lines = [
        d
        for d in drawings
        if _close(d.get("color"), _rgb(line_color))
        and d.get("fill") is None
        and len([it for it in d["items"] if it[0] == "l"]) >= 3
    ]
    assert len(lines) == 1, f"颜色 {line_color} 的数据曲线应恰有一条，找到 {len(lines)}"
    pts = []
    for it in lines[0]["items"]:
        if it[0] == "l":
            for p in (it[1], it[2]):
                q = ((p.x - ax.x0) / ax.width, (ax.y1 - p.y) / ax.height)
                if not pts or max(abs(q[0] - pts[-1][0]), abs(q[1] - pts[-1][1])) > 1e-9:
                    pts.append(q)
    return {"points": pts, "text": text, "axes_pt": (ax.width, ax.height)}


def _assert_same_points(a, b, tol=1e-3):
    assert len(a) == len(b) == len(POINTS.split(",")), (len(a), len(b))
    for i, (p, q) in enumerate(zip(a, b)):
        assert abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol, f"第 {i} 点：{p} → {q}"


def test_appearance_only_patches_keep_points_ticks_units_and_sources(project):
    m, client, figs = project
    script_sha, data_sha = _sha(figs / "fig1.py"), _sha(figs / "points.csv")
    base = _manifest(client, [])
    _materialize(client)
    line = _gid(base, "label", "signal")
    title = _gid(base, "text", "Raw")
    patches = [
        {"gid": line, "prop": "color", "value": "#d62728"},
        {"gid": line, "prop": "linewidth", "value": 3.0},
        {"gid": title, "prop": "fontsize", "value": 15.0},
        {"gid": "axes_0.legend", "prop": "loc_frac", "value": [0.55, 0.10]},
        {"gid": "figure", "prop": "size_mm", "value": [120.0, 70.0]},
    ]
    hot = _manifest(client, patches)
    assert hot["size_mm"] == pytest.approx([120.0, 70.0], abs=0.05), "前提：图幅 patch 真的落了"

    before = _science(_original_pdf(client, "Before", []), LINE0)
    after = _science(_original_pdf(client, "After", patches), "#d62728")
    assert after["axes_pt"] != pytest.approx(before["axes_pt"], abs=1.0), "前提：坐标区确实变了尺寸"
    _assert_same_points(before["points"], after["points"])

    # 刻度**个数**随图幅由 matplotlib 的 locator 重选（坐标区变长 → 刻度变密），那不是数据变化；
    # 数据范围是否不变已由上面的归一化顶点量过（顶点 = 数据经坐标范围映射）。这里只比单位。
    assert "Time (s)" in after["text"] and "Voltage (mV)" in after["text"]

    # 写回原件：只动图库里的 PDF，脚本与数据文件逐字节不变
    r = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": patches})
    assert r.status_code == 200, r.get_json()
    assert _sha(figs / "fig1.py") == script_sha
    assert _sha(figs / "points.csv") == data_sha
    written = _science(figs / "Fig1.pdf", "#d62728")
    _assert_same_points(before["points"], written["points"])


def test_the_ruler_sees_a_real_data_change(project):
    """反证：改 y 轴范围（不是外观）时，同一把尺子必须量出顶点变了。"""
    _m, client, _figs = project
    _manifest(client, [])
    _materialize(client)
    before = _science(_original_pdf(client, "Ruler0", []), LINE0)
    moved = _science(
        _original_pdf(
            client, "Ruler1", [{"gid": "axes_0", "prop": "ylim", "value": [-20.0, 40.0]}]
        ),
        LINE0,
    )
    diff = max(abs(p[1] - q[1]) for p, q in zip(before["points"], moved["points"]))
    assert diff > 0.05, f"尺子没看见数据坐标的变化（最大差 {diff}）"
