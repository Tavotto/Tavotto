"""机理图评测（`scripts/eval/mechanism/`，ADR 0220 6.D）的题库与判据单测：不叫模型、不要高级版组件。

盯的是：`corpus.json` 与生成脚本一致、每题自洽；每条判据既有正例也有反例（判据有牙齿）；`harness.py` 的对照题（无授权）语义。
真组件那一圈（`harness.py run Aref / Mut`）要本地高级版源码，不进 CI；有 `EVAL_PRO_SRC` 时下面最后一条会一起跑。"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1] / "scripts" / "eval" / "mechanism"
sys.path.insert(0, str(EVAL))

import build_corpus  # noqa: E402
import predicates as P  # noqa: E402


def _rect_obj(oid, name, x, y, w, h, *, glyph=None, rot=0.0):
    nodes = [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}, {"p": [0, 1]}]
    o = {"id": oid, "type": "path", "name": name, "x": x, "y": y, "w": w, "h": h,
         "subpaths": [{"closed": True, "nodes": nodes}], "rotationDeg": rot}  # fmt: skip
    if glyph:
        o["glyph"] = {"kind": glyph}
    return o


def _scene(**over):
    base = [
        {"id": "f", "type": "shape", "shape": "rect", "name": "示意图框 · t", "x": 0, "y": 0, "w": 80, "h": 60},
        _rect_obj("m", "双层膜", 5, 25, 60, 6, glyph="membrane"),
        _rect_obj("r1", "受体 1", 20, 12, 6, 30, glyph="receptor_rtk"),
        _rect_obj("r2", "受体 2", 40, 12, 6, 30, glyph="receptor_rtk"),
        {"id": "t1", "type": "text", "name": "标签 · 受体", "text": "受体", "x": 28, "y": 14, "w": 8, "h": 4},
        {"id": "a1", "type": "arrow", "name": "箭头", "x": 23, "y": 4, "w": 0, "h": 8, "head": "end",
         "start": {"rx": 0, "ry": 0}, "end": {"rx": 0, "ry": 1}},
    ]  # fmt: skip
    return [{**o, **over.get(o["id"], {})} for o in base]


BEFORE: list = []


def _ev(preds, after):
    return P.evaluate({"predicates": preds}, BEFORE, after)


def test_corpus_json_matches_the_generator():
    assert build_corpus.main.__module__  # 模块可导入
    expect = json.dumps(build_corpus.build(), ensure_ascii=False, indent=1) + "\n"
    assert (EVAL / "corpus.json").read_text(encoding="utf-8") == expect


def test_every_task_is_self_consistent():
    c = build_corpus.build()
    names = set()
    for t in c["tasks"]:
        assert t["id"] not in names
        names.add(t["id"])
        assert t["instruction"] and t["reference"]
        item_names = [it["name"] for it in t["reference"]]
        assert len(item_names) == len(set(item_names)), t["id"]
        assert sum(it["kind"] == "frame" for it in t["reference"]) == 1  # 一个 add 最多一个框
        assert len(t["reference"]) <= 24  # 含机理图条目的 add 上限
        for spec in t["predicates"]:
            assert spec["t"] in P.PREDICATES, (t["id"], spec["t"])
    assert any(t.get("expect") == "pro_required" for t in c["tasks"])


def test_each_predicate_passes_a_good_scene_and_fails_a_bad_one():
    good = _scene()
    allp = [{"t": k} for k in P.PREDICATES if k != "counts"]
    allp.append(
        {"t": "counts", "frames": 1, "membranes": 1, "proteins": 2, "texts": 1, "arrows": 1}
    )
    allp[allp.index({"t": "required_texts"})] = {"t": "required_texts", "contains": ["受体"]}
    r = _ev(allp, good)
    assert r["ok"], r
    cases = {
        # 蛋白漂出膜（竖直不横跨膜）
        "proteins_in_membrane": _scene(r1={"y": 0, "h": 20}),
        # 两个蛋白真实轮廓交叠
        "no_overlap": _scene(r2={"x": 23}),
        # 有东西落在框外
        "inside_frame": _scene(t1={"x": 90}),
        # 缺要求的文字
        "required_texts": _scene(t1={"text": "别的"}),
        # 标签离所有对象太远（框在 0..80，放到右下角空处）
        "labels_adjacent": _scene(t1={"x": 70, "y": 50}),
        # 箭头悬在空中
        "arrows_reach": _scene(a1={"x": 70, "y": 50, "h": 4}),
        # 数量不够
        "counts": [o for o in good if o["id"] != "t1"],
    }
    for name, bad in cases.items():
        res = _ev(allp, bad)
        assert not next(c for c in res["checks"] if c["t"] == name)["ok"], (name, res)


def test_labels_must_not_sit_on_a_protein_outline():
    on_protein = _scene(t1={"x": 20, "y": 14, "w": 6, "h": 4})  # 文字中心落在受体 1 的轮廓里
    res = _ev([{"t": "labels_adjacent"}], on_protein)
    assert not res["ok"] and "压在" in res["checks"][0]["detail"]


def test_rotated_proteins_are_judged_by_their_rotated_outline():
    # 两个竖直蛋白并排不交叠；旋转 90° 后横过来就交叠
    side = _scene(r2={"x": 30})
    assert _ev([{"t": "no_overlap"}], side)["ok"]
    turned = _scene(r1={"rotationDeg": 90.0}, r2={"x": 30})
    assert not _ev([{"t": "no_overlap"}], turned)["ok"]


def test_originals_kept_notices_a_changed_or_deleted_original():
    keep = [{"id": "u", "type": "text", "x": 1, "y": 1, "w": 5, "h": 3, "text": "Fig. 1"}]
    assert P.evaluate({"predicates": [{"t": "originals_kept"}]}, keep, [*keep, *_scene()])["ok"]
    moved = [{**keep[0], "x": 2}]
    assert not P.evaluate({"predicates": [{"t": "originals_kept"}]}, keep, moved)["ok"]
    assert not P.evaluate({"predicates": [{"t": "originals_kept"}]}, keep, [])["ok"]


def test_the_reference_batches_land_with_the_real_component_when_it_is_around():
    src = os.environ.get("EVAL_PRO_SRC")
    if not src or not Path(src, "tavotto_pro").is_dir():
        pytest.skip("没有本地高级版组件源码（设 EVAL_PRO_SRC）")
    if importlib.util.find_spec("tavotto") is None:  # pragma: no cover
        pytest.skip("tavotto 不可导入")
    import harness  # noqa: PLC0415

    corpus = harness.load_corpus()
    for t in corpus["tasks"]:
        rec = harness.judge(t, corpus["canvas"], t["reference"])
        assert rec["success"], (t["id"], rec.get("predicate_detail"), rec.get("errors"))


def test_the_plugin_reference_example_batch_is_a_valid_add_with_the_real_component():
    """`references/mechanism-diagram.md` 里的示例批必须真能落地（有本地高级版组件时跑）。"""
    import re

    src = os.environ.get("EVAL_PRO_SRC")
    if not src or not Path(src, "tavotto_pro").is_dir():
        pytest.skip("没有本地高级版组件源码（设 EVAL_PRO_SRC）")
    md = (
        Path(__file__).resolve().parents[1]
        / "codex-plugin/skills/tavotto-figure/references/mechanism-diagram.md"
    ).read_text(encoding="utf-8")
    block = re.search(r"```json\n(.*?)\n```", md, re.S).group(1)
    ops = json.loads(block)
    assert ops[0]["op"] == "add"
    import harness  # noqa: PLC0415

    res = harness.propose(
        harness.load_corpus()["canvas"] | {"objects": []}, ops[0]["items"], licensed=True
    )
    assert res["ok"], res.get("errors") or res.get("failed")
