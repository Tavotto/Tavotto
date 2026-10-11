"""流程图调框的真实公共入口：单脚本导入、编辑、保存、服务冷重开、PDF/PNG 导出。

语料是独立的最小流程图，不包含用户上传脚本或研究内容。几何真值同时量
manifest 的未裁剪外框、导出 PDF 的矢量路径和 PNG 的实际描边，避免只验证
override 原样回显。新服务复用磁盘文档，不能复用旧 worker 的内存状态。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageChops

from support import foundation_app as fa
from tavotto.engine import figcapture, pool

ROOT = Path(__file__).resolve().parent.parent
try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT = """\
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

fig, ax = plt.subplots(figsize=(6, 4), dpi=180)
ax.set_xlim(0, 100)
ax.set_ylim(0, 100)
ax.axis("off")
ax.add_patch(FancyBboxPatch((10, 60), 32, 25, boxstyle="round,pad=0.4,rounding_size=1",
                           facecolor="white", edgecolor="#145A86", linewidth=1.6))
ax.text(12, 78, "First stage", va="center", fontsize=11)
ax.text(12, 68, "Independent text", va="center", fontsize=9)
ax.add_patch(FancyBboxPatch((62, 60), 28, 25, boxstyle="round,pad=0.4,rounding_size=1",
                           facecolor="white", edgecolor="#765A20", linewidth=1.4))
ax.text(64, 75, "Next stage", va="center", fontsize=10)
ax.annotate("", xy=(62, 72), xytext=(42, 72),
            arrowprops=dict(arrowstyle="->", color="#374151", lw=1.4))
ax.add_patch(FancyBboxPatch((10, 10), 80, 30, boxstyle="round,pad=0.4,rounding_size=1",
                           facecolor="#F0FDF4", edgecolor="#16804A", linewidth=1.6))
ax.add_patch(FancyBboxPatch((14, 14), 34, 20, boxstyle="round,pad=0.3,rounding_size=0.8",
                           facecolor="white", edgecolor="#53725C", linewidth=1.2))
ax.text(18, 25, "Nested stage", fontsize=10)
plt.tight_layout()
fig.savefig("Flow.pdf")
fig.savefig("Flow.png", dpi=300)
plt.show()
"""


def _element(man: dict, gid: str) -> dict:
    return next(e for e in man["elements"] if e["gid"] == gid)


def _render(app: fa.RunningApp, asset_id: str, patches: list) -> dict:
    body = app.render(asset_id, patches)
    assert not body.get("warnings"), body.get("warnings")
    return body["manifest"]


def _assert_same(a: dict, b: dict) -> None:
    from tavotto.app import _compare_manifests

    diffs, compared = _compare_manifests(a, b)
    assert compared > 0 and not diffs, diffs


def _assert_geometry(man: dict, gid: str, size: list[float], anchor: list[float]) -> None:
    box = _element(man, gid)
    assert box["shape_bounds"][2:] == pytest.approx(size, abs=1e-6)
    assert box["anchor"] == pytest.approx(anchor, abs=1e-6)


def _assert_exports(exported: dict, man: dict, gid: str, color: str) -> None:
    assert exported["status"] == "done", exported
    assert not exported.get("warnings"), exported
    files = {
        Path(f["name"]).suffix: Path(exported["export_dir"]) / f["name"] for f in exported["files"]
    }
    bounds = _element(man, gid)["shape_bounds"]
    rgb = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))
    with pymupdf.open(files[".pdf"]) as doc:
        assert doc.page_count == 1
        page = doc[0]
        assert "Independent text" in page.get_text(), "流程图文字必须保留为矢量文字"
        assert [page.rect.width * 25.4 / 72, page.rect.height * 25.4 / 72] == pytest.approx(
            man["size_mm"], abs=0.05
        )
        outlines = [
            d
            for d in page.get_drawings()
            if d.get("color") is not None
            and d["color"] == pytest.approx([v / 255 for v in rgb], abs=1e-5)
        ]
        assert len(outlines) == 1, outlines
        rect = outlines[0]["rect"]
        actual = [
            rect.x0 / page.rect.width,
            rect.y0 / page.rect.height,
            rect.width / page.rect.width,
            rect.height / page.rect.height,
        ]
        assert actual == pytest.approx(bounds, abs=1e-5), (actual, bounds)
        assert outlines[0]["width"] == pytest.approx(1.6, abs=1e-5), "调框不能缩放描边"

    with Image.open(files[".png"]) as image:
        # 独立读取最终 PNG 的蓝色描边；邻框、文字与箭头均使用其它颜色。
        # 给抗锯齿留一个小色差，尺寸容差只含 1.6pt 描边的半宽和边缘取整。
        masks = [
            ch.point([255 if abs(value - rgb[i]) <= 12 else 0 for value in range(256)])
            for i, ch in enumerate(image.convert("RGB").split())
        ]
        mask = ImageChops.multiply(ImageChops.multiply(masks[0], masks[1]), masks[2])
        rect = mask.getbbox()
        assert rect is not None, "PNG 中找不到所编辑图框的实际描边"
        w, h = image.size
        actual = [rect[0] / w, rect[1] / h, (rect[2] - rect[0]) / w, (rect[3] - rect[1]) / h]
        assert actual == pytest.approx(bounds, abs=6 / min(w, h)), (actual, bounds)


def test_single_script_box_resize_survives_document_save_cold_restart_and_export(tmp_path):
    project, work = tmp_path / "project", tmp_path / "work"
    project.mkdir()
    script = project / "flow.py"
    script.write_text(SCRIPT, encoding="utf-8")
    source_bytes = script.read_bytes()
    asset_id = figcapture.runtime_asset_id("flow.py", "Flow")
    gid = "axes_0.patches_0"
    # 这是几何验收，明确钉住同一科学解释器，不让机器上的解释器发现结果影响比较。
    env = {
        "PYTHONPATH": str(ROOT / "src"),
        "TAVOTTO_WORKER_PYTHON": WORKER_PY,
        "TAVOTTO_USER_ENV_DISCOVERY": "0",
        "TAVOTTO_WORKERD": "0",
    }
    with fa.running_app(project, work, env_overrides=env) as app:
        status, probed = app.call("/api/registry/probe", {"script": "flow.py"}, timeout=300)
        assert status == 200 and probed["registered"], probed
        assert probed["stems"] == ["Flow"]
        base = _render(app, asset_id, [])
        box = _element(base, gid)
        x, y, w, h = box["shape_bounds"]
        size, anchor = [w * 1.4, h * 0.75], [x + 0.035, y + h - 0.025]
        pos_patch = {"gid": gid, "prop": "pos_frac", "value": anchor}
        size_patch = {"gid": gid, "prop": "size_frac", "value": size}
        # 先调尺寸 → 再拖动 → 单独撤销尺寸 → 单独撤销位置 → 恢复联合修改。
        _render(app, asset_id, [size_patch])
        edited = _render(app, asset_id, [pos_patch, size_patch])
        _assert_geometry(edited, gid, size, anchor)
        moved_only = _render(app, asset_id, [pos_patch])
        _assert_geometry(moved_only, gid, [w, h], anchor)
        resized_only = _render(app, asset_id, [size_patch])
        _assert_geometry(resized_only, gid, size, [x, y + h])
        patches = [pos_patch, size_patch]
        edited = _render(app, asset_id, patches)
        _assert_geometry(edited, gid, size, anchor)
        for other in base["elements"]:
            if other["gid"] != gid:
                now = _element(edited, other["gid"])
                assert now.get("bbox") == other.get("bbox"), other["gid"]
                assert now.get("anchor") == other.get("anchor"), other["gid"]
        width_mm, height_mm = edited["size_mm"]
        doc = {
            "schema": 3,
            "project": {"id": "resize-doc", "name": "Flowchart"},
            "canvases": [
                {
                    "id": "canvas",
                    "name": "Flowchart",
                    "page": {"w": width_mm, "h": height_mm},
                    "objects": [
                        {
                            "id": "panel",
                            "type": "panel",
                            "fileId": asset_id,
                            "fileKind": "runtime",
                            "script": "flow.py",
                            "nativeW": width_mm,
                            "nativeH": height_mm,
                            "x": 0,
                            "y": 0,
                            "w": width_mm,
                            "h": height_mm,
                            "overrides": patches,
                        }
                    ],
                    "guides": [],
                }
            ],
            "activeCanvasId": "canvas",
            "createdAt": 0,
            "updatedAt": 1,
        }
        status, _ = app.call("/api/autosave/resize-doc", doc, method="PUT")
        assert status == 200
        status, _ = app.call(
            "/api/layout-session/last", {"doc_id": "resize-doc", "name": "Flowchart"}, method="PUT"
        )
        assert status == 200
        status, _ = app.call("/api/layouts/Flowchart?target=project", doc)
        assert status == 200

    # 新服务、新 worker，只从持久化的文档取 overrides；不是清空热会话后假装重开。
    with fa.running_app(project, work, env_overrides=env) as app:
        _, session = app.call("/api/layout-session")
        assert session["last"]["doc_id"] == "resize-doc", session
        _, recovered = app.call("/api/autosave/resize-doc")
        _, saved = app.call("/api/layouts/Flowchart")
        assert recovered == saved == doc
        panel = recovered["canvases"][0]["objects"][0]
        cold = _render(app, panel["fileId"], panel["overrides"])
        _assert_geometry(cold, gid, size, anchor)
        _assert_same(edited, cold)
        _, exported = app.call(
            "/api/export",
            {
                "scope": "original",
                "filename": "resized-flow",
                "formats": ["pdf", "png"],
                "dpi": 240,
                "original": {
                    "figure_id": asset_id,
                    "source_kind": "vector",
                    "overrides": panel["overrides"],
                },
            },
            timeout=300,
        )
        _assert_exports(exported, cold, gid, "#145A86")
        _assert_same(cold, _render(app, asset_id, patches))
    assert script.read_bytes() == source_bytes, "编辑或导出不能改写用户脚本"
