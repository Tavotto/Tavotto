"""patch 规范化的唯一权威实现（engine/patchspec.py）+ golden vectors。

这套断言的存在意义是**跨语言对齐**：Rust supervisor（magplot-workerd）要
逐字节复现同一份 canonical JSON 与哈希。任何一侧改了规则，另一侧算出来的
patch 身份就静默变了——表现是缓存永远不命中、幂等重放变成重复渲染，
没人会立刻联想到「序列化细节」。所以浮点写法、键序、剔除规则全是硬断言。
"""
import json
import math
from pathlib import Path

import pytest

from magplot.engine import patchspec

GOLDEN = Path(__file__).parent / "golden" / "patch_vectors.json"


def _vectors():
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return data["_README"], data["vectors"]


# ---------------------------- canonicalize 语义 ----------------------------
def test_last_wins_on_duplicate_gid_prop():
    """同一个 (gid, prop) 写了多遍：后写的赢（与 overrides.apply 建表同语义）。"""
    canonical = patchspec.canonicalize([
        {"gid": "t", "prop": "text", "value": "一"},
        {"gid": "t", "prop": "text", "value": "二"},
        {"gid": "t", "prop": "fontsize", "value": 9},
    ])
    assert canonical == [
        {"gid": "t", "prop": "fontsize", "value": 9},
        {"gid": "t", "prop": "text", "value": "二"},
    ]


def test_sorted_by_gid_then_prop():
    canonical = patchspec.canonicalize([
        {"gid": "b", "prop": "z", "value": 1},
        {"gid": "b", "prop": "a", "value": 2},
        {"gid": "a", "prop": "m", "value": 3},
    ])
    assert [(p["gid"], p["prop"]) for p in canonical] == [
        ("a", "m"), ("b", "a"), ("b", "z")]


def test_invalid_entries_are_reported_not_silently_dropped():
    """非法条目必须**可见地**剔除：静默丢一条 = 用户「改了没生效」且无线索。"""
    canonical, dropped = patchspec.canonicalize_with_diagnostics([
        "字符串不是条目",
        {"gid": 3, "prop": "text", "value": "x"},
        {"gid": "g", "prop": "", "value": "x"},
        {"gid": "g", "prop": "text"},
        {"gid": "g", "prop": "alpha", "value": float("nan")},
        {"gid": "g", "prop": "beta", "value": math.inf},
        {"gid": "g", "prop": "gamma", "value": {1: "非字符串键"}},
        {"gid": "g", "prop": "delta", "value": (1, 2)},
        {"gid": "g", "prop": "text", "value": "留下的"},
    ])
    assert canonical == [{"gid": "g", "prop": "text", "value": "留下的"}]
    assert [d["reason"] for d in dropped] == [
        "not_an_object", "bad_gid", "bad_prop", "missing_value",
        "non_finite_float", "non_finite_float", "non_string_object_key",
        "unsupported_type:tuple",
    ]
    assert [d["index"] for d in dropped] == [0, 1, 2, 3, 4, 5, 6, 7]


def test_non_list_input_is_a_diagnostic_not_a_crash():
    """脏 body（前端发了个对象）不该把渲染请求打成 500——记一条诊断按空表算。"""
    canonical, dropped = patchspec.canonicalize_with_diagnostics({"gid": "x"})
    assert canonical == []
    assert dropped == [{"index": -1, "reason": "patches_not_a_list",
                        "entry": "{'gid': 'x'}"}]
    assert patchspec.patch_hash({"gid": "x"}) == patchspec.patch_hash([])


def test_deeply_nested_value_is_rejected_instead_of_blowing_the_stack():
    deep = {"gid": "g", "prop": "p", "value": None}
    node: object = []
    deep["value"] = node
    for _ in range(64):
        inner: list = []
        node.append(inner)          # type: ignore[union-attr]
        node = inner
    _, dropped = patchspec.canonicalize_with_diagnostics([deep])
    assert [d["reason"] for d in dropped] == ["value_too_deep"]


def test_huge_int_is_dropped_to_keep_rust_on_plain_i64():
    _, dropped = patchspec.canonicalize_with_diagnostics(
        [{"gid": "g", "prop": "p", "value": 2 ** 63}])
    assert [d["reason"] for d in dropped] == ["int_out_of_range"]
    # 边界值本身合法
    assert patchspec.canonicalize(
        [{"gid": "g", "prop": "p", "value": 2 ** 63 - 1}])


def test_extra_keys_do_not_change_identity():
    """条目上多出来的字段不参与身份判定（前端顺手带的 label/ts 之类）。"""
    a = [{"gid": "g", "prop": "p", "value": 1}]
    b = [{"gid": "g", "prop": "p", "value": 1, "label": "顺手带的", "ts": 3}]
    assert patchspec.patch_hash(a) == patchspec.patch_hash(b)


# ---------------------------- 确定性序列化 ----------------------------
def test_canonical_json_is_stable_across_calls():
    patches = [{"gid": "g", "prop": "text", "value": "µ 强度 ⁻¹"}]
    first = patchspec.canonical_json(patches)
    assert all(patchspec.canonical_json(patches) == first for _ in range(5))
    assert " " not in first.replace("µ 强度 ⁻¹", "")   # 分隔符不留空白
    assert "\\u" not in first                          # 非 ASCII 原样出字


def test_shuffled_input_hashes_the_same():
    base = [{"gid": f"g{i}", "prop": "text", "value": i} for i in range(8)]
    shuffled = [base[i] for i in (5, 0, 7, 2, 6, 1, 4, 3)]
    assert patchspec.patch_hash(base) == patchspec.patch_hash(shuffled)
    # 值变了就必须换 hash（否则「同一份修改」的判定形同虚设）
    changed = [dict(p) for p in base]
    changed[3]["value"] = 999
    assert patchspec.patch_hash(changed) != patchspec.patch_hash(base)


def test_int_and_float_are_different_identities():
    """JSON 的 1 与 1.0 是两个值；两边的解析器都得保住这个区别。"""
    assert (patchspec.canonical_json([{"gid": "g", "prop": "p", "value": 1}])
            != patchspec.canonical_json([{"gid": "g", "prop": "p", "value": 1.0}]))


def test_hash_prefix_and_length():
    h = patchspec.patch_hash([])
    assert h.startswith("sha256:") and len(h) == len("sha256:") + 64
    # 空表的规范形态就是 "[]"（sha256 of "[]" 是个公开可核对的常量）
    assert patchspec.canonical_json([]) == "[]"
    assert h == ("sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2"
                 "f11161202b945")


# ---------------------------- golden vectors ----------------------------
def test_golden_file_states_the_cross_language_contract():
    readme, vectors = _vectors()
    text = "\n".join(readme)
    assert "逐字节" in text and "Rust" in text
    assert "patchspec" in text and "0003" in text
    assert len(vectors) >= 8, "向量太少，覆盖不住跨语言对齐"


@pytest.mark.parametrize("vector", _vectors()[1], ids=lambda v: v["name"])
def test_golden_vector(vector):
    """逐组断言：canonical / dropped / canonical_json / hash 全部逐字节对上。"""
    canonical, dropped = patchspec.canonicalize_with_diagnostics(vector["input"])
    assert canonical == vector["canonical"], vector["name"]
    assert [{"index": d["index"], "reason": d["reason"]} for d in dropped] \
        == vector["dropped"], vector["name"]
    assert patchspec.canonical_json(vector["input"]) == vector["canonical_json"]
    assert patchspec.patch_hash(vector["input"]) == vector["hash"]


def test_golden_float_formatting_is_pinned():
    """浮点写法是跨语言最容易分叉的地方（Rust 的 ryu 默认写 1e22 / 1e-7）。

    这几个字面量就是给 Rust 侧对表用的；改了它们 = 改了协议。
    """
    _, vectors = _vectors()
    floats = next(v for v in vectors if v["name"] == "floats")
    js = floats["canonical_json"]
    for literal in ('"value":1e+22', '"value":1e-07', '"value":-0.0',
                    '"value":1.0', '"value":1}', '"value":0.30000000000000004'):
        assert literal in js, literal


def test_golden_vectors_round_trip_through_json():
    """向量文件里的 input 经 json 解析后必须仍是原来的双精度值。

    向量是靠 JSON 文本传给 Rust 侧的，input 一旦在解析时漂了，两边对的
    就不是同一组数据了。
    """
    _, vectors = _vectors()
    floats = next(v for v in vectors if v["name"] == "floats")
    values = {p["prop"]: p["value"] for p in floats["input"]}
    assert values["sum"] == 0.1 + 0.2
    assert math.copysign(1, values["neg_zero"]) == -1
    assert isinstance(values["int_one"], int)
    assert isinstance(values["float_one"], float)
