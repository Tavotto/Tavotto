"""投稿核对的编排 `run()`（ADR 0216 §九）：假渲染器按覆盖改字号 / 字体 / 图幅，specfix 事务是真的。"""

from __future__ import annotations

import copy

import pytest

from tavotto.engine import license as engine_license, submissioncheck as sc
from test_submissioncheck_plan import _manifest


@pytest.fixture(autouse=True)
def licensed(monkeypatch):
    monkeypatch.setattr(engine_license, "has", lambda feature: feature == sc.FEATURE)


def _fake_render(base_manifest):
    calls = []

    def render(patches):
        calls.append(list(patches))
        m = copy.deepcopy(base_manifest)
        by_gid = {e["gid"]: e for e in m["elements"]}
        for p in patches:
            if p["gid"] == "figure" and p["prop"] == "size_mm":
                m["size_mm"] = list(p["value"])
                continue
            el = by_gid.get(p["gid"])
            if el is None:
                continue
            for f in el.get("editable") or []:
                if f["prop"] == p["prop"]:
                    f["value"] = p["value"]
                    break
            else:
                el.setdefault("editable", []).append({"prop": p["prop"], "value": p["value"]})
        return {"manifest": m}

    render.calls = calls
    return render


def _canvas(manifest=None, box=None, page=None):
    m = manifest or _manifest()
    w, h = m["size_mm"]
    fig = sc.Figure(
        key="p1", rel_id="fig_a.py", base=[], box=box or [0, 0, w, h], render=_fake_render(m)
    )
    return sc.Canvas(key="L1", page=page or {"w": w, "h": h}, figures=[fig], only_figure=True)


PROFILE = {
    "severity": {},
    "min_effective_font_size_pt": 5,
    "absolute_min_font_size_pt": 4,
    "font_family": {"latin": "Arial", "latin_accepted": ["Arial"]},
}


def _solver_with(make):
    seen = {}

    def solver(payload):
        seen["payload"] = payload
        return make(payload)

    solver.seen = seen
    return solver


def _row_ref(payload, check, value):
    return next(
        r["ref"] for r in payload["requirements"] if r["check"] == check and r["value"] == value
    )


def test_judged_width_lands_by_itself_and_unaccepted_confirm_does_not():
    m = _manifest()
    m["size_mm"] = [81.3, 54.8]
    c = _canvas(m)

    def make(payload):
        width = _row_ref(payload, "width_final", 89)
        fmax = _row_ref(payload, "font_size_max", 7)
        body = [
            t["ref"]
            for t in payload["canvases"][0]["texts"]
            if t["size_pt"] > 7 and not t["panel_label"]
        ]
        return {
            "requirements": [
                {
                    "ref": width,
                    "state": "fixable",
                    "per_canvas": [{"canvas": "c1", "state": "fixable", "ops": ["o1"]}],
                },
                {
                    "ref": fmax,
                    "state": "fixable",
                    "per_canvas": [{"canvas": "c1", "state": "fixable", "ops": ["o2"]}],
                },
            ],
            "ops": [
                {"ref": "o1", "op": "canvas.width", "canvas": "c1", "width_mm": 89},
                {"ref": "o2", "op": "text.size", "canvas": "c1", "texts": body, "size_pt": 7},
            ],
        }

    out = sc.run([c], journal_id="nature", profile=PROFILE, solver=_solver_with(make))
    (canvas,) = out["canvases"]
    assert canvas["page"]["w"] == 89
    patches = canvas["figures"][0]["patches"]
    assert {"gid": "figure", "prop": "size_mm", "value": [89.0, pytest.approx(60.0, abs=0.01)]} in [
        {**p, "value": [p["value"][0], pytest.approx(p["value"][1], abs=0.01)]}
        if p["prop"] == "size_mm"
        else p
        for p in patches
    ]
    assert not any(p["prop"] == "fontsize" for p in patches)  # 7 pt 上限是 confirm，没点就不改
    by_id = {r["id"]: r for r in out["requirements"]}
    assert by_id["v1:page_columns#single"]["state"] == "fixed"
    assert by_id["v1:max_font_size_pt"]["state"] == "fixable"


def test_accepted_max_size_shrinks_body_text_but_never_panel_labels():
    c = _canvas()

    def make(payload):
        fmax = _row_ref(payload, "font_size_max", 7)
        texts = payload["canvases"][0]["texts"]
        body = [t["ref"] for t in texts if t["size_pt"] > 7 and not t["panel_label"]]
        return {
            "requirements": [
                {
                    "ref": fmax,
                    "state": "fixable",
                    "per_canvas": [{"canvas": "c1", "state": "fixable", "ops": ["o1"]}],
                }
            ],
            "ops": [{"ref": "o1", "op": "text.size", "canvas": "c1", "texts": body, "size_pt": 7}],
        }

    out = sc.run(
        [c],
        journal_id="nature",
        profile=PROFILE,
        accept=["v1:max_font_size_pt"],
        solver=_solver_with(make),
    )
    patches = out["canvases"][0]["figures"][0]["patches"]
    touched = {p["gid"] for p in patches if p["prop"] == "fontsize"}
    assert "axes_0.xlabel" in touched
    assert not touched & {"text_0", "text_1"}  # 分图标签一个字号都不动
    by_id = {r["id"]: r for r in out["requirements"]}
    assert by_id["v1:max_font_size_pt"]["state"] == "fixed"
    assert by_id["v1:max_font_size_pt"]["per_canvas"] == [{"canvas": "L1", "state": "fixed"}]


def test_sidecar_never_sees_text_content_quotes_or_paths():
    c = _canvas()
    solver = _solver_with(lambda p: {"requirements": [], "ops": []})
    sc.run([c], journal_id="nature", profile=PROFILE, solver=solver)
    import json

    blob = json.dumps(solver.seen["payload"], ensure_ascii=False)
    for leak in ("Time", "fig_a.py", "nature.com", "Maximum text size", "snapshots/"):
        assert leak not in blob


def test_invalid_component_result_raises_and_changes_nothing():
    c = _canvas()
    with pytest.raises(sc.SubmissionCheckError) as e:
        sc.run(
            [c],
            journal_id="nature",
            profile=PROFILE,
            solver=lambda p: {"requirements": 12},
        )
    assert e.value.code == "invalid_plan"


def test_not_licensed_is_refused_before_any_render(monkeypatch):
    monkeypatch.setattr(engine_license, "has", lambda feature: False)
    c = _canvas()
    with pytest.raises(sc.SubmissionCheckError) as e:
        sc.run([c], journal_id="nature", profile=PROFILE, solver=lambda p: {})
    assert e.value.code == "not_licensed"
    assert c.figures[0].render.calls == []


# --------------------------------------------------------------------------- 真链路：真 matplotlib、真 worker
def test_real_worker_width_then_accepted_max_size(tmp_path, monkeypatch):
    """81 mm 宽、刻度与轴标题 8–9 pt 的图：判过的栏宽自动改到 89 mm；点了 7 pt 上限后正文缩到 ≤ 7 pt，
    改完对真实渲染重新量过（不信组件给的数）。"""
    test_specfix_real = pytest.importorskip("test_specfix_real")
    if test_specfix_real.WORKER_PY is None:
        pytest.skip("没有带科学栈的解释器")
    proc, render = test_specfix_real._spawn_render(
        tmp_path, test_specfix_real.SCRIPT.replace("80 / 25.4", "81 / 25.4"), "Kin"
    )
    try:
        b0 = render([])["manifest"]
        w, h = b0["size_mm"]
        fig = sc.Figure(key="p1", rel_id="fig.py", base=[], box=[0, 0, w, h], render=render)
        c = sc.Canvas(key="L1", page={"w": w, "h": h}, figures=[fig], only_figure=True)

        def make(payload):
            width = _row_ref(payload, "width_final", 89)
            fmax = _row_ref(payload, "font_size_max", 7)
            body = [
                t["ref"]
                for t in payload["canvases"][0]["texts"]
                if t["size_pt"] > 7 and not t["panel_label"] and t["editable"]
            ]
            return {
                "requirements": [
                    {
                        "ref": width,
                        "state": "fixable",
                        "per_canvas": [{"canvas": "c1", "state": "fixable", "ops": ["o1"]}],
                    },
                    {
                        "ref": fmax,
                        "state": "fixable",
                        "per_canvas": [{"canvas": "c1", "state": "fixable", "ops": ["o2"]}],
                    },
                ],
                "ops": [
                    {"ref": "o1", "op": "canvas.width", "canvas": "c1", "width_mm": 89},
                    {"ref": "o2", "op": "text.size", "canvas": "c1", "texts": body, "size_pt": 7},
                ],
            }

        out = sc.run(
            [c],
            journal_id="nature",
            profile=PROFILE,
            accept=["v1:max_font_size_pt"],
            solver=_solver_with(make),
        )
        patches = out["canvases"][0]["figures"][0]["patches"]
        final = render(patches)["manifest"]
        assert final["size_mm"][0] == pytest.approx(89, abs=0.5)
        texts, _, _ = sc.measure_figure(final, 1.0, "x")
        assert max(t["size_pt"] for t in texts) <= 7.05
        by_id = {r["id"]: r for r in out["requirements"]}
        assert by_id["v1:page_columns#single"]["state"] == "fixed"
        assert by_id["v1:max_font_size_pt"]["state"] == "fixed"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def _one_op(check, value, op, state="fixable"):
    def make(payload):
        ref = _row_ref(payload, check, value)
        texts = payload["canvases"][0]["texts"]
        body = [t["ref"] for t in texts if t["editable"] and not t["panel_label"]]
        o = {"ref": "o1", "canvas": "c1", **op}
        if o["op"] != "canvas.width":
            o["texts"] = body
        return {
            "requirements": [
                {
                    "ref": ref,
                    "state": state,
                    "per_canvas": [{"canvas": "c1", "state": state, "ops": ["o1"]}],
                }
            ],
            "ops": [o],
        }

    return make


def test_a_clicked_fix_that_did_not_take_goes_back_to_confirm(monkeypatch):
    from tavotto.engine import specfix

    monkeypatch.setattr(specfix, "transaction", lambda *a, **k: {"ok": False, "patches": []})
    out = sc.run(
        [_canvas()],
        journal_id="nature",
        profile=PROFILE,
        accept=["v1:max_font_size_pt"],
        solver=_solver_with(_one_op("font_size_max", 7, {"op": "text.size", "size_pt": 7})),
    )
    by_id = {r["id"]: r for r in out["requirements"]}
    assert by_id["v1:max_font_size_pt"]["state"] == "confirm"  # 不再给「可一键改」的按钮
    assert out["canvases"][0]["figures"][0]["patches"] == []


def test_ops_keep_their_gids_when_remeasuring_renumbers_texts():
    """改宽度后多出一个刻度文字：后面的字号操作仍落在原来那几个 gid 上（不按重编后的短号找）。"""
    m = _manifest()
    m["size_mm"] = [81.3, 54.8]
    base_render = _fake_render(m)

    def render(patches):
        resp = base_render(patches)
        if any(p["prop"] == "size_mm" for p in patches):
            from test_submissioncheck_plan import _text

            resp["manifest"]["elements"].insert(3, _text("axes_0.ticks2", "ticks", 9))
        return resp

    render.calls = base_render.calls
    fig = sc.Figure(key="p1", rel_id="f.py", base=[], box=[0, 0, 81.3, 54.8], render=render)
    c = sc.Canvas(key="L1", page={"w": 81.3, "h": 54.8}, figures=[fig], only_figure=True)

    def make(payload):
        width = _row_ref(payload, "width_final", 89)
        fmax = _row_ref(payload, "font_size_max", 7)
        (xlabel,) = [t["ref"] for t in payload["canvases"][0]["texts"] if t["role"] == "xlabel"]
        return {
            "requirements": [
                {
                    "ref": width,
                    "state": "fixable",
                    "per_canvas": [{"canvas": "c1", "state": "fixable", "ops": ["o1"]}],
                },
                {
                    "ref": fmax,
                    "state": "fixable",
                    "per_canvas": [{"canvas": "c1", "state": "fixable", "ops": ["o2"]}],
                },
            ],
            "ops": [
                {"ref": "o1", "op": "canvas.width", "canvas": "c1", "width_mm": 89},
                {"ref": "o2", "op": "text.size", "canvas": "c1", "texts": [xlabel], "size_pt": 7},
            ],
        }

    out = sc.run(
        [c],
        journal_id="nature",
        profile=PROFILE,
        accept=["v1:max_font_size_pt"],
        solver=_solver_with(make),
    )
    sized = {
        p["gid"] for p in out["canvases"][0]["figures"][0]["patches"] if p["prop"] == "fontsize"
    }
    assert sized == {"axes_0.xlabel"}


def test_a_judged_row_that_no_longer_holds_after_resizing_is_not_pass():
    """组件说 300 dpi / 字号「符合」也不算数：最终画布上预检还报，就退回 confirm。"""
    m = _manifest()
    m["size_mm"] = [81.3, 54.8]
    c = _canvas(m)
    prof = {**PROFILE, "min_effective_font_size_pt": 9, "severity": {"font-too-small": "warn"}}

    def make(payload):
        ref = next(
            r["ref"]
            for r in payload["requirements"]
            if r["source_state"] == "preflight" and r["check"] == "raster_dpi_min"
        )
        width = _row_ref(payload, "width_final", 89)
        return {
            "requirements": [
                {"ref": ref, "state": "pass", "per_canvas": [{"canvas": "c1", "state": "pass"}]},
                {"ref": width, "state": "pass", "per_canvas": [{"canvas": "c1", "state": "pass"}]},
            ],
            "ops": [],
        }

    out = sc.run([c], journal_id="nature", profile=prof, solver=_solver_with(make))
    by_id = {r["id"]: r for r in out["requirements"]}
    # 81.3 mm 不等于 89：组件说 pass 也退回 confirm
    assert by_id["v1:page_columns#single"]["state"] == "confirm"
    # 300 dpi 这张图没有位图，预检不报 → 重新判过仍符合
    assert by_id["v1:min_raster_dpi"]["state"] == "pass"


def test_a_canvas_that_cannot_render_is_skipped_and_the_others_go_on():
    good = _canvas()

    def broken(patches):
        raise RuntimeError("script error")

    bad = sc.Canvas(
        key="L2",
        page={"w": 89, "h": 60},
        figures=[sc.Figure(key="p9", rel_id="bad.py", base=[], box=[0, 0, 89, 60], render=broken)],
    )
    out = sc.run(
        [bad, good],
        journal_id="nature",
        profile=PROFILE,
        solver=lambda p: {"requirements": [], "ops": []},
    )
    assert out["skipped"] == [{"key": "L2", "reason": "render_failed"}]
    assert [c["key"] for c in out["canvases"]] == ["L1"]


def test_width_is_not_adjustable_when_the_canvas_holds_other_objects_or_is_scaled():
    seen = _solver_with(lambda p: {"requirements": [], "ops": []})
    c = _canvas()
    c.only_figure = False
    sc.run([c], journal_id="nature", profile=PROFILE, solver=seen)
    assert seen.seen["payload"]["canvases"][0]["width_adjustable"] is False
    c = _canvas(box=[0, 0, 178, 120], page={"w": 178, "h": 120})  # 89 mm 的图放大一倍显示
    sc.run([c], journal_id="nature", profile=PROFILE, solver=seen)
    assert seen.seen["payload"]["canvases"][0]["width_adjustable"] is False
