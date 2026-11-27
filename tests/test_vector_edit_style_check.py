"""矢量编辑核对的三处修正（评测 `scripts/eval/vector_edit/` 实测出的）：

* style（字体族与图一致）只报这次编辑引入的差异：已有文字挪位置 / 改线宽不再因为它本来的字体族与图不一致而被拦；
  改了字体族 / 文字、新增的文字照旧核；没写 `fontFamily` 的按画布缺省字体族（与前端 `typography.ts` 同一个值）算；
* 故意框住整张子图的外框，对子图自己的文字豁免（`waived`），不逼外框挪歪；没把子图框全的外框照旧核；
* `box` 的 `fit` 写错时 `bad_item` 的 detail 给出期望形态。
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from tavotto.engine import layoutannotate as la, vectorcheck, vectoredit as ve, vectorscene
from test_layoutannotate import MANIFEST, PANEL, _doc, measure
from test_vectoredit import _t

ROOT = Path(__file__).resolve().parents[1]


def _facts(res, check="style"):
    return [f for f in res["facts"] if f["check"] == check]


def _txt(oid, x, y):
    return _t(oid, x, y, bold=False, align="left")


def _canvas(*texts):
    return copy.deepcopy(_doc(list(texts))["canvases"][0])


def _plan(canvas, ops):
    return ve.propose_memory(canvas, ops)


# 测试的假 manifest 里没有文字字体 → 图的字体族取缺省 sans-serif；画布文字缺省 serif：正好是「已有文字与图不一致」的情形
def test_default_family_is_the_frontend_one():
    ts = (ROOT / "web" / "src" / "lib" / "typography.ts").read_text(encoding="utf-8")
    m = re.search(r"CANVAS_TEXT_DEFAULT_FAMILY: CanvasTextFamily = '([a-z-]+)'", ts)
    assert m and m.group(1) == vectorscene.CANVAS_TEXT_DEFAULT_FAMILY


def _with_text_family():
    """让图里有文字且是无衬线：style 核对才不是 unknown。"""
    manifest = copy.deepcopy(MANIFEST)
    manifest["elements"].append(
        {
            "gid": "axes_0.xlabel",
            "role": "axis_label",
            "bbox": [0.4, 0.95, 0.2, 0.04],
            "fontfamily": "DejaVu Sans",
            "label": "x",
        }
    )
    return manifest


def _measure_sans(panel, requests):
    out = measure(panel, requests)
    out["draw_manifest"] = _with_text_family()
    return out


def _propose(canvas, ops):
    return ve._plan(canvas, ops, _measure_sans, None)  # noqa: SLF001


def test_moving_an_existing_text_is_not_blocked_by_its_old_family():
    canvas = _canvas(_txt("t1", 100, 10))
    res = _propose(canvas, [{"op": "move", "id": "t1", "by_mm": [2, 0]}])
    assert res["ok"], res["failed"]
    st = _facts(res)
    assert st and st[0]["status"] == "preexisting"


def test_changing_the_family_or_text_is_still_checked():
    canvas = _canvas(_txt("t1", 100, 10))
    res = _propose(canvas, [{"op": "set", "id": "t1", "set": {"text": "x"}}])
    assert not res["ok"] and _facts(res)[0]["status"] == "fail"
    res = _propose(canvas, [{"op": "set", "id": "t1", "set": {"fontFamily": "sans-serif"}}])
    assert res["ok"] and _facts(res)[0]["status"] == "pass"
    res = _propose(canvas, [{"op": "set", "id": "t1", "set": {"fontFamily": "monospace"}}])
    assert not res["ok"]


# ------------------------------------------------------------------ 外框豁免


def _frame_check(frame_box, tmp_path=None):
    """一个只有描边的外框 + 子图自己的文字（贴着子图下沿）：返回 text 核对。"""
    x, y, w, h = frame_box
    obj = {
        "id": "frame",
        "type": "shape",
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "shape": "rect",
        "color": "#000000",
        "fill": None,
        "strokePt": 0.5,
    }
    canvas = _canvas(obj)
    panels, _r, _u = la.measure_panels(canvas, None, lambda p, r: _measure_panel_text())
    from tavotto.engine import agentobjects

    objs = [obj]
    res = vectorcheck.check(
        {"objects": [{"id": "frame"}], "anchors": []},
        objs,
        panels,
        [{"name": "frame"}],
        page={"w": 120, "h": 80},
    )
    del agentobjects
    return _facts(res, "text")


def _measure_panel_text():
    manifest = copy.deepcopy(MANIFEST)
    # 子图自己的文字贴着子图下沿（bbox 以子图框为 1）
    manifest["elements"].append(
        {"gid": "axes_0.xlabel", "role": "axis_label", "bbox": [0.4, 0.93, 0.2, 0.07], "label": "x"}
    )
    return {"draw_manifest": manifest, "data_points": []}


def test_a_frame_that_encloses_the_panel_is_waived_for_the_panels_own_text():
    px, py, pw, ph = PANEL["x"], PANEL["y"], PANEL["w"], PANEL["h"]
    got = _frame_check((px - 0.02, py - 0.02, pw + 0.04, ph + 0.04))
    assert got and got[0]["status"] == "waived", got


def test_a_frame_that_cuts_through_the_panel_text_still_fails():
    px, py, pw, ph = PANEL["x"], PANEL["y"], PANEL["w"], PANEL["h"]
    got = _frame_check((px - 0.5, py - 0.5, pw + 1.0, ph - 1.5))  # 下沿收进子图里、没把子图框全
    assert got and got[0]["status"] == "fail", got


# ------------------------------------------------------------------ box fit 的错误提示


@pytest.mark.parametrize("fit", [["P1"], [], "P1", [{"panel": "P1"}], [{"nope": {}}]])
def test_bad_fit_names_the_expected_form(fit):
    with pytest.raises(vectorscene.SceneError) as e:
        ctx = vectorscene._Ctx({})  # noqa: SLF001
        vectorscene._box({"kind": "box", "name": "f", "fit": fit}, ctx, "f")  # noqa: SLF001
    assert e.value.code == "bad_item" and "[{" in (e.value.detail or "")
    assert "panel" in e.value.detail
