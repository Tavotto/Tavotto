"""成品字号检查（`checks.text_size`，ADR 0203）在**真实导出路由**上的端到端读数（真 matplotlib worker）。

主语是 `/api/export` 交付的那份 PDF 的回执 manifest：脚本是 7 pt 的轴标签 `$R^2$`，matplotlib 的 mathtext
把上标排成约 0.7 倍的另一段字。面板经 worker 重画后作为 Form 嵌进版面，检查器量的是成品内容流里
`Tf × 文字矩阵 × 累计 CTM`——期望值按「字号 × 上下标比例 × 版面缩放」算，不写死。

阈值只从出版规范来：strict + `free-form-v1`（下限 6 pt）时正文 7 pt 过、上标不过 → failed，且**不拦**导出
（两档政策都不把它当必需项）；standard 没有规范 → not_applicable（`no_threshold`：没被要求检查，不让整份降成「未核验」）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavotto.engine import exportjob, pool, profiles, project_watch

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（not_run，不是绿）"
)

#: mathtext 的上下标缩放（matplotlib `_mathtext.SHRINK_FACTOR`）
SCRIPT_SHRINK = 0.7
BODY_PT = 7.0

SCRIPT = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot([1, 2, 3], [1, 4, 9])
    ax.set_xlabel("$R^2$", fontsize=7)
    ax.set_ylabel("signal", fontsize=7)
    ax.set_title("Original Title", fontsize=7)
    ax.tick_params(labelsize=7)
    fig.savefig("Fig1.pdf")
"""

REGISTRY = json.dumps(
    {
        "version": 1,
        "scripts": {"fig1.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["Fig1"]}},
    }
)


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
    (figs / "tavotto_registry.json").write_text(REGISTRY, encoding="utf-8")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(230.4, 172.8))
    pdf.save(figs / "Fig1.pdf")
    m.open_project(str(figs))
    exportjob.reset_for_tests()
    try:
        yield m, m.app.test_client(), figs
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(figs), wait=True)
        project_watch.stop()


def _title_patch(client) -> list:
    r = client.post("/api/engine/render", json={"id": "Fig1.pdf", "patches": []})
    assert r.status_code == 200, r.get_json()
    gid = next(
        el["gid"]
        for el in r.get_json()["manifest"]["elements"]
        for f in el.get("editable", [])
        if f["prop"] == "text" and f.get("value") == "Original Title"
    )
    return [{"gid": gid, "prop": "text", "value": "Edited"}]


def _export(
    client, name: str, patches: list, *, scale: float, inspection=None, extra=()
) -> tuple[dict, dict]:
    spec = {
        "scope": "canvas",
        "filename": name,
        "formats": ["pdf"],
        "overwrite": "replace",
        "canvas": {
            "page_w_mm": 130,
            "page_h_mm": 100,
            "objects": [
                {
                    "type": "panel",
                    "id": "Fig1.pdf",
                    "x_mm": 5,
                    "y_mm": 5,
                    "w_mm": 81.28 * scale,
                    "h_mm": 60.96 * scale,
                    "overrides": patches,
                },
                *extra,
            ],
        },
    }
    if inspection is not None:
        spec["inspection"] = inspection
    r = client.post("/api/export", json=spec)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    return next(o for o in body["outputs"] if o["format"] == "pdf"), body


STRICT_FREE_FORM = {"mode": "strict", "profile_id": "free-form-v1"}


def test_a_superscript_below_the_profile_floor_is_failed_but_still_delivered(project):
    """7 pt 的正文过 6 pt 下限，4.9 pt 的上标不过 → failed；strict 下照样交付（不是必需项）。"""
    _m, client, figs = project
    patches = _title_patch(client)
    floor = profiles.load("free-form-v1")["min_effective_font_size_pt"]
    assert BODY_PT * SCRIPT_SHRINK < floor - profiles.FONT_FLOOR_TOL_PT < BODY_PT  # 用例有区分度
    out, body = _export(client, "Strict", patches, scale=1.0, inspection=STRICT_FREE_FORM)
    mf = out["manifest"]
    print("E2E strict ×1.0:", json.dumps({k: mf[k] for k in ("checks", "verdict", "text_size")}))
    assert out["status"] == "done" and body["status"] == "done", body
    assert mf["policy"] == "strict" and mf["verdict"] == "accepted"
    assert mf["checks"]["text_size"] == "failed"
    ts = mf["text_size"]
    assert ts["reason"] == "below_min" and ts["floor_pt"] == floor
    assert ts["min_pt"] == pytest.approx(BODY_PT * SCRIPT_SHRINK, abs=0.011)
    assert "text_size" not in mf["required"]
    assert (Path(body["export_dir"]) / out["name"]).exists()


def test_the_layout_scale_is_part_of_the_reading(project):
    """同一张图放大 1.25 倍摆进版面：上标 7 × 0.7 × 1.25 ≈ 6.13 pt，过 6 pt 下限 → verified。
    量的是成品（面板 Form 的缩放乘在 CTM 里），不是脚本里的 fontsize。"""
    _m, client, _figs = project
    scale = 1.25
    out, _body = _export(
        client, "Big", _title_patch(client), scale=scale, inspection=STRICT_FREE_FORM
    )
    mf = out["manifest"]
    print("E2E strict ×1.25:", json.dumps({k: mf[k] for k in ("checks", "verdict", "text_size")}))
    assert mf["checks"]["text_size"] == "verified"
    assert mf["text_size"]["min_pt"] == pytest.approx(BODY_PT * SCRIPT_SHRINK * scale, abs=0.02)


def test_without_a_profile_the_reading_is_not_applicable(project):
    """standard 不带规范：量到了也只记录不判（`no_threshold`）→ not_applicable，不是 verified 也不是
    unknown；其余各项与改动前一样全 verified / n/a，整份不因这一项降成「未核验」。"""
    _m, client, _figs = project
    out, _body = _export(client, "Std", _title_patch(client), scale=1.0)
    mf = out["manifest"]
    print("E2E standard:", json.dumps({k: mf[k] for k in ("checks", "verdict", "text_size")}))
    assert out["status"] == "done"
    assert mf["checks"]["text_size"] == "not_applicable"
    assert "unknown" not in mf["checks"].values() and mf["verdict"] == "accepted"
    assert mf["text_size"]["reason"] == "no_threshold" and mf["text_size"]["floor_pt"] is None
    assert mf["text_size"]["min_pt"] == pytest.approx(BODY_PT * SCRIPT_SHRINK, abs=0.011)


def _annotation(latex: bool) -> dict:
    obj = {
        "type": "text",
        "id": "eq1",
        "text": "fit $y = a x^2$" if latex else "fit y = a x2",
        "x_mm": 90,
        "y_mm": 10,
        "w_mm": 38,
        "h_mm": 10,
        "size_pt": 10,
    }
    if latex:
        obj["text_format"] = "latex"
    return obj


def test_a_plain_text_annotation_is_measured_and_verified(project):
    """对照：同一块画布、同一处标注换成普通文字（不是公式）——量得到，verified。下一条的 unknown
    因此确实来自公式，不是来自标注本身。"""
    _m, client, _figs = project
    out, _body = _export(
        client,
        "Plain",
        _title_patch(client),
        scale=1.25,
        inspection=STRICT_FREE_FORM,
        extra=(_annotation(False),),
    )
    mf = out["manifest"]
    print("E2E plain annotation:", json.dumps({k: mf[k] for k in ("checks", "text_size")}))
    assert mf["checks"]["text_size"] == "verified"
    assert mf["text_size"]["reason"] == "ok"


def test_a_tavotto_formula_annotation_makes_the_reading_unknown(project):
    """画布上的公式标注（ADR 0152：轮廓 + 不画的文本层）：可见的字都够大也不许说 verified。"""
    _m, client, _figs = project
    formula = _annotation(True)
    out, _body = _export(
        client,
        "Formula",
        _title_patch(client),
        scale=1.25,
        inspection=STRICT_FREE_FORM,
        extra=(formula,),
    )
    mf = out["manifest"]
    print("E2E formula:", json.dumps({k: mf[k] for k in ("checks", "verdict", "text_size")}))
    assert out["status"] == "done"
    assert mf["checks"]["text_size"] == "unknown"
    assert mf["text_size"]["reason"] == "unmeasured_runs"
    assert mf["text_size"]["min_pt"] >= mf["text_size"]["floor_pt"]
