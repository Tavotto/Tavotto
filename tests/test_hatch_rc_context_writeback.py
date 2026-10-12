"""#874：alpha 编辑撤销之后写回——花纹色在 rc_context 里建的图不许被 `replay_divergence` 拦下。

热会话改过 alpha 又撤销，账本上热态是 `[]`；修复前 3.11 上花纹色被环境值重解析、停在 `edge`，
而写回 verify 的全新 worker 只见 `[]`——像素门报 409 `replay_divergence`（一次正当的编辑序列被拦）。
修复后写回成功，且写进 PNG 的是脚本原样。≤3.10 的同形状是 `fill`（`set_fill(True)` 回不去 `_hatch_color`）。

判据的主语：**写回响应的状态码**（修复前 409 / 修复后 200）与 verification.pixels，以及
**热会话此刻的像素 == 全新 worker 只见 `[]` 的像素**（前提，免得写回成功是因为根本没分叉）。
"""

from __future__ import annotations

import json

import pytest

from support import rc_hatch_figure as rc, rc_hatch_text_legend_figure as rctl
from tavotto.engine import pool, project_watch

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


def _registry(fig):
    return json.dumps(
        {
            "version": 1,
            "scripts": {
                fig.SCRIPT_NAME: {
                    "entry": fig.ENTRY,
                    "cost": "light",
                    "notes": "",
                    "stems": [fig.STEM],
                }
            },
        }
    )


_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc0f01f0005000201"
    "a5f6d2a30000000049454e44ae426082"
)


@pytest.fixture
def project(tmp_path, monkeypatch, request):
    import pikepdf

    fig = getattr(request, "param", rc)  # 默认 #874 那张图；#956 的用例间接参数化成另一张

    from tavotto import app as m

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / fig.SCRIPT_NAME).write_text(fig.LIBRARY, encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(_registry(fig), encoding="utf-8")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 100))
    pdf.save(figs / f"{fig.STEM}.pdf")
    (figs / f"{fig.STEM}.png").write_bytes(_PNG_1X1)
    m.open_project(str(figs))
    try:
        yield m, m.app.test_client(), figs
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()


def _render(client, patches, stem=rc.STEM):
    r = client.post("/api/engine/render", json={"id": f"{stem}.pdf", "patches": patches})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["manifest"]


@pytest.mark.parametrize("name", ["bar", "rect"])
def test_alpha_edit_then_undo_then_write_back_is_not_a_replay_divergence(project, name):
    m, client, figs = project
    gid, _props = rc.TARGETS[name]
    _render(client, [])
    _render(client, [{"gid": gid, "prop": "alpha", "value": 0.5}])
    _render(client, [])  # 撤销：热会话账本上是 []

    r = client.post("/api/engine/update_source", json={"id": f"{rc.STEM}.pdf", "patches": []})
    body = r.get_json()
    assert r.status_code == 200, f"撤销 alpha 之后写回被拦：{body.get('code')} {body.get('diffs')}"
    assert (body.get("verification") or {}).get("pixels") == "ok", body


@pytest.mark.parametrize("project", [rctl], indirect=True)
@pytest.mark.parametrize("name", list(rctl.TARGETS))
def test_text_legend_alpha_edit_then_undo_then_write_back_is_not_a_replay_divergence(project, name):
    """#956：文本背景框 / 图例框 / 线集合——修复前 409 `replay_divergence`，修复后 200。"""
    m, client, figs = project
    gid, prop = rctl.TARGETS[name]
    _render(client, [], rctl.STEM)
    _render(client, [{"gid": gid, "prop": prop, "value": 0.5}], rctl.STEM)
    _render(client, [], rctl.STEM)  # 撤销：热会话账本上是 []

    r = client.post("/api/engine/update_source", json={"id": f"{rctl.STEM}.pdf", "patches": []})
    body = r.get_json()
    assert r.status_code == 200, (
        f"{name}：撤销 alpha 之后写回被拦：{body.get('code')} {body.get('diffs')}"
    )
    assert (body.get("verification") or {}).get("pixels") == "ok", body
