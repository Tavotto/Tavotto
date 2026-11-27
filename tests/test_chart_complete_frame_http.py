"""HTTP 层 /2：render 与 preview_png 共享同一输出 F，scene 仍 S；canonical 页面走 F。

移植自原型 `E:/0 - Project/svg-review-20261106/n5-tick-clipping-review/prototype/
tests/test_complete_frame_http.py`（PLAN.md §2 第 1 步 / §7；产品落地名
`tests/test_chart_complete_frame_http.py`）。相对原型的调整点：

- 夹具：删掉 importlib 按绝对路径加载 `tests/test_chart_native_api.py` 的原型
  harness，改为同目录直接 import（产品既有跨测试模块引用模式，见
  `tests/test_chart_native_export.py` 的 `project as _native_project`）。
- 删除 `owned_overlay` 夹具参数（overlay 出处是原型 harness 概念；产品里
  overlay 就是产品模块）。
- 删除末尾 numpy/matplotlib `sys.modules` 隔离断言：完整套件同进程跑时先前
  用例可能已 import matplotlib，该断言会随测试顺序抖动；「Flask 父进程闭包
  无科学栈」由 `tests/test_import_architecture.py`
  （`tests/import_architecture_baseline.json`）看护。
"""

from __future__ import annotations

import base64
from xml.etree import ElementTree as ET

import pytest

from tavotto import app as m
from tavotto.engine import chartprofile
from test_chart_native_api import _bind, project as _native_project

project = _native_project


def test_actual_profile2_http_svg_png_share_output_F_but_scene_remains_S(project, monkeypatch):
    client, root = project
    source = (root / "chart.svg").read_bytes()
    chart = _bind(client)
    chart["model"]["axes"][0].update(xlabel="Sample time (seconds)", ylabel="Response (a.u.)")
    body = {
        "id": "chart.svg",
        "chart": chart,
        "patches": [],
        "render_profile": chartprofile.COMPLETE_PROFILE,
        "inline_svg": True,
    }
    response = client.post("/api/engine/render", json=body)
    assert response.status_code == 200, response.get_json()
    rendered = response.get_json()
    frame = rendered["manifest"]["render_frame"]
    assert "frame" not in rendered["manifest"]
    assert frame["source_viewport_pt"] == [100, 100]
    assert frame["rect_pt"][2] > 100 and frame["rect_pt"][3] > 100
    assert (
        list(map(float, ET.fromstring(rendered["svg"]).get("viewBox").split())) == frame["rect_pt"]
    )
    original_canonical = m._chart_canonical
    seen = []

    def canonical(snapshot, directory):
        assert snapshot.scene["w_pt"] == snapshot.scene["h_pt"] == 100
        assert snapshot.render_frame == frame
        result = original_canonical(snapshot, directory)
        from tavotto import pdfbackend

        facts = pdfbackend.probe_asset(result[0].path, "pdf")
        seen.append(facts)
        return result

    monkeypatch.setattr(m, "_chart_canonical", canonical)
    response = client.post(
        "/api/engine/preview_png", json={**body, "with_manifest": True, "w": 400}
    )
    assert response.status_code == 200, response.get_json()
    png = response.get_json()
    assert base64.b64decode(png["png"]).startswith(b"\x89PNG")
    assert png["manifest"]["render_frame"] == frame
    assert png["chart_revision"] == rendered["chart_revision"]
    assert len(seen) == 1
    assert [seen[0]["w_pt"], seen[0]["h_pt"]] == pytest.approx(frame["rect_pt"][2:])
    assert (root / "chart.svg").read_bytes() == source
