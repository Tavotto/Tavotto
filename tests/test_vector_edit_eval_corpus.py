"""矢量编辑评测（`scripts/eval/vector_edit/`）的题库与判据自检：不调模型、不起 sidecar。

量的是**判据本身**对不对：每题带一份标准答案（`reference`），判据在标准答案落下去之后的画布上为真、在「什么都没做」的画布上为假
（对照题反过来：什么都不做为真，带一份 `wrong_ops` 落下去为假）。题库形状、与 `build_corpus.py` 一致也在这里核。
布尔 / 偏移 / 轮廓化三题要几何内核（wasmtime），没有就跳过那三题。"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tavotto.engine import atomicio, layoutannotate as la, vecops, vectoredit as ve

EVAL = Path(__file__).resolve().parents[1] / "scripts" / "eval" / "vector_edit"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_vedit_eval_{name}", EVAL / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


predicates = _load("predicates")
build_corpus = _load("build_corpus")
CORPUS = json.loads((EVAL / "corpus.json").read_text(encoding="utf-8"))
TASKS = {t["id"]: t for t in CORPUS["tasks"]}


def test_corpus_json_is_what_the_builder_writes():
    assert (EVAL / "corpus.json").read_text(encoding="utf-8") == build_corpus.render()


def test_corpus_shape():
    tasks = CORPUS["tasks"]
    assert len(tasks) == len(TASKS) >= 20
    kinds = [t["kind"] for t in tasks]
    assert kinds.count("add") >= 6 and kinds.count("edit") >= 12 and kinds.count("control") >= 2
    for t in tasks:
        assert t["instruction"].strip() and t["predicate"], t["id"]
        ids = [o["id"] for o in t["canvas"]["objects"]]
        assert len(ids) == len(set(ids)) and "o_panel001" in ids, t["id"]
        for spec in t["predicate"]:
            assert spec["t"] in predicates.PREDICATES, (t["id"], spec)
        assert t["reference"]["kind"] in ("ops", "items", "none"), t["id"]
        assert set(t.get("selection", [])) <= set(ids), t["id"]
        assert set(t.get("may_change", [])) <= set(ids), t["id"]
        if t["kind"] == "control":
            assert t["reference"]["kind"] == "none" and t["wrong_ops"], t["id"]


def _page(t):
    return t["canvas"]["page"]


def _apply_ops(t, ops):
    canvas = copy.deepcopy(t["canvas"])
    panels, _refs, _un = la.measure_panels(canvas, None, ve.unmeasured_panel)
    return ve.apply_ops(canvas["objects"], ops, panels, _page(t))["objects"]


def _stub_measure(panel, requests):
    """x ∈ [0, 10]、y ∈ [0, 1.1] 按坐标轴矩形 [0.15, 0.15, 0.8, 0.75] 线性换算（与 build_corpus 的子图脚本同一份几何）。"""
    manifest = {
        "size_mm": [80, 60],
        "elements": [
            {"gid": "axes_0", "role": "axes", "bbox": [0.15, 0.10, 0.8, 0.75], "label": None}
        ],
    }
    pts = [
        {"status": "ok", "xy": [0.15 + 0.8 * q["x"] / 10, 0.10 + 0.75 * (1 - q["y"] / 1.1)]}
        for q in requests
    ]
    return {"draw_manifest": manifest, "data_points": pts}


def _apply_items(t, items, tmp_path):
    doc = {
        "schema": 3,
        "project": {"id": "p_test0001", "name": "t"},
        "canvases": [
            {
                "id": "c_main0001",
                "name": "图版 1",
                "page": _page(t),
                "objects": copy.deepcopy(t["canvas"]["objects"]),
                "guides": [],
            }
        ],
        "activeCanvasId": "c_main0001",
        "createdAt": 0,
        "updatedAt": 0,
    }
    store = tmp_path / "tavottofile"
    store.mkdir()
    atomicio.write_json(store / "组图.json", doc, indent=1)
    s = la.read_scene(tmp_path, "组图", _stub_measure)
    plan = la.propose(tmp_path, "组图", items, _stub_measure, base_revision=s["base_revision"])
    assert plan["ok"], plan
    out = la.commit(
        tmp_path,
        "组图",
        items,
        _stub_measure,
        base_revision=s["base_revision"],
        echo_digest_seen=plan["echo_digest"],
        fingerprint=s["scene"]["fingerprint"],
    )
    assert out["written"], out
    return la.open_layout(tmp_path, "组图").doc["canvases"][0]["objects"]


def _needs_kernel(t):
    return t.get("needs_kernel") and not vecops.available()


@pytest.mark.parametrize("tid", sorted(TASKS))
def test_reference_passes_and_doing_nothing_fails(tid, tmp_path):
    t = TASKS[tid]
    if _needs_kernel(t):
        pytest.skip("没有几何内核（wasmtime）")
    before = t["canvas"]["objects"]
    ref = t["reference"]
    if ref["kind"] == "ops":
        after = _apply_ops(t, ref["ops"])
    elif ref["kind"] == "items":
        after = _apply_items(t, ref["items"], tmp_path)
    else:
        after = copy.deepcopy(before)
    got = predicates.evaluate(t, before, after)
    assert got["ok"], got["checks"]
    nothing = predicates.evaluate(t, before, copy.deepcopy(before))
    if t["kind"] == "control":
        assert nothing["ok"], nothing["checks"]  # 什么都不做就是对的
        wrong = predicates.evaluate(t, before, _apply_ops(t, t["wrong_ops"]))
        assert not wrong["ok"], "错误补丁落下去判据却过了"
    else:
        assert not nothing["ok"], "什么都不做判据却过了"


def test_untouched_objects_must_stay_equal():
    t = TASKS["delete-note"]
    before = t["canvas"]["objects"]
    after = _apply_ops(t, [{"op": "delete", "id": "t_note"}])
    after[-1] = {**after[-1], "x": after[-1]["x"] + 5}  # 顺手挪了没点名的对象
    got = predicates.evaluate(t, before, after)
    assert not got["ok"] and any(
        c["t"] == "others_unchanged" and not c["ok"] for c in got["checks"]
    )


def test_extra_objects_are_refused_unless_the_task_allows_them():
    t = TASKS["delete-note"]
    before = t["canvas"]["objects"]
    after = _apply_ops(t, [{"op": "delete", "id": "t_note"}]) + [
        {"id": "x_new", "type": "text", "x": 0, "y": 0, "w": 1, "h": 1, "text": "?"}
    ]
    got = predicates.evaluate(t, before, after)
    assert not got["ok"] and any(
        c["t"] == "no_extra_objects" and not c["ok"] for c in got["checks"]
    )


# ------------------------------------------------------------------ Codex #2330 的三条：判据不能太松


def test_deleting_an_unrelated_object_fails_unless_the_task_says_so():
    t = TASKS["order-front"]
    before = t["canvas"]["objects"]
    good = _apply_ops(t, t["reference"]["ops"])
    assert predicates.evaluate(t, before, good)["ok"]
    only_green = [
        o for o in good if o["id"] in ("o_panel001", "s_green")
    ]  # 其余都删了，s_green 在最上面
    got = predicates.evaluate(t, before, only_green)
    assert not got["ok"] and any(c["t"] == "originals_kept" and not c["ok"] for c in got["checks"])
    # 题目明确要删 / 替换的豁免：delete-note 删 t_note 过，顺手多删 t_keep 不过
    d = TASKS["delete-note"]
    after = _apply_ops(d, d["reference"]["ops"])
    assert predicates.evaluate(d, d["canvas"]["objects"], after)["ok"]
    after2 = [o for o in after if o["id"] != "t_keep"]
    assert not predicates.evaluate(d, d["canvas"]["objects"], after2)["ok"]


def _bracket_case(**over):
    t = TASKS["add-bracket"]
    base = {
        "id": "br",
        "type": "path",
        "x": 10.0,
        "y": 18.0,
        "w": 80.0,
        "h": 1.0,
        "subpaths": [
            {
                "closed": False,
                "nodes": [
                    {"p": [0.0, 1.0]},
                    {"p": [0.0, 0.0]},
                    {"p": [1.0, 0.0]},
                    {"p": [1.0, 1.0]},
                ],
            }
        ],
    }
    base.update(over)
    before = t["canvas"]["objects"]
    return t, before, [*before, base]


def test_bracket_predicate_wants_a_real_bracket_over_the_panel_width():
    t, before, after = _bracket_case()
    assert predicates.evaluate(t, before, after)["ok"]
    bad = {
        "tiny ellipse": {
            "type": "shape",
            "shape": "ellipse",
            "w": 1.0,
            "h": 1.0,
            "x": 40.0,
            "y": 15.0,
        },
        "too narrow": {"w": 20.0},
        "far below the panel top": {"y": 60.0},
        "closed outline": {
            "subpaths": [
                {
                    "closed": True,
                    "nodes": [{"p": [0, 1]}, {"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}],
                }
            ]
        },
        "a plain line": {"subpaths": [{"closed": False, "nodes": [{"p": [0, 0]}, {"p": [1, 0]}]}]},
        "legs point away": {
            "subpaths": [
                {
                    "closed": False,
                    "nodes": [{"p": [0, 0]}, {"p": [0, 1]}, {"p": [1, 1]}, {"p": [1, 0]}],
                }
            ]
        },
    }
    for label, over in bad.items():
        t, before, after = _bracket_case(**over)
        assert not predicates.evaluate(t, before, after)["ok"], label


def test_a_b_run_only_counts_when_the_agent_finished():
    harness = _load("harness")
    ctrl = TASKS["ctrl-vague"]
    done = {"success": True, "trajectory": []}
    assert harness.b_completed(done)
    for broken in ({"timed_out": True}, {"agent_errors": ["Error: x"]}):
        assert not harness.b_completed(broken)
        # 超时 / 报错的运行：归类先看它，不会因为画布没动被当成「对照题成功」
        assert harness.classify_b({**broken, "success": True}, ctrl) in ("超时", "模型/运行报错")
    assert harness.classify_b(done, ctrl) == "none"
