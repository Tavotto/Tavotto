"""`tests/support/shard.py`（CI03a 的按文件分片）的看护。

分片选择器坏掉的两个方向都**不会有用例红**：漏掉一个文件，CI 上两片各自全绿；
两片重叠，只是慢一点。所以这里钉的是选择器自己的合同——主语是 **nodeid 集合**：

* 确定性：同一份 collection 在任何机器上得到同一份分配；
* 不拆文件：一个文件的全部 nodeid 落在同一片；
* 并集 == 全集、两两不交、每片非空、无重复——由 `verify()` 判，任一条不成立抛
  `ShardError`（conftest 转成 rc 4）；
* 权重表里没有的文件按 p75 估重，不当成零、不漏掉；
* 负例：K > N、N = 0、空 item 列表、重复 nodeid、某片为空（N > 文件数）、
  权重表坏 JSON / 负数 / 布尔 / 非 .py 键 / 缺出处 → 全部抛异常。

末尾几条起真实子进程跑 `python -m pytest --shard …`，钉的是 conftest 那一层：
错参数是 rc 4（不是静默跑全集、也不是静默跑空集），不带 `--shard` 时 collection 不变。
每条负例写完都做过一次变异反证（删掉对应的检查 → 该条红），记录在
docs/implementation/ci-foundation/CI03A_PYTEST_SHARDS.md。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from support import shard

ROOT = Path(__file__).resolve().parents[1]

W = {
    "tests/test_a.py": 100.0,
    "tests/test_b.py": 10.0,
    "tests/test_c.py": 10.0,
    "tests/test_d.py": 10.0,
    "tests/test_e.py": 1.0,
}


def _items(spec: dict[str, int]) -> dict[str, list[str]]:
    """`{文件: 用例数}` → `{文件: [nodeid…]}`，nodeid 形状与 pytest 的一致。"""
    return {f: [f"{f}::test_{i}" for i in range(n)] for f, n in spec.items()}


def _all(items: dict[str, list[str]]) -> list[str]:
    return [n for ids in items.values() for n in ids]


# ---------------------------------------------------------------- 分配


def test_assignment_is_deterministic_and_greedy_by_weight():
    items = _items({f: 3 for f in W})
    p1 = shard.plan(items, W, 1, 2)
    p2 = shard.plan(items, W, 1, 2)
    assert p1 == p2
    # 最重的 a（100）独占一片，其余四个（31）在另一片：贪心放进当前最轻的
    assert p1.files == (("tests/test_a.py",), tuple(sorted(f for f in W if f != "tests/test_a.py")))
    assert p1.estimated_seconds == (100.0, 31.0)


def test_assignment_does_not_depend_on_collection_order():
    items = _items({f: 2 for f in W})
    reversed_items = dict(reversed(list(items.items())))
    a = shard.plan(items, W, 2, 2)
    b = shard.plan(reversed_items, W, 2, 2)
    assert a.files == b.files
    assert a.estimated_seconds == b.estimated_seconds
    assert [set(s) for s in a.nodeids] == [set(s) for s in b.nodeids]


def test_ties_go_to_the_lower_shard_then_by_path():
    weights = {"tests/test_x.py": 5.0, "tests/test_y.py": 5.0, "tests/test_z.py": 5.0}
    items = _items({f: 1 for f in weights})
    p = shard.plan(items, weights, 1, 2)
    # 三个同重：x → 片 1（同轻按片号小的），y → 片 2，z → 片 1（片 1 与片 2 同重，片号小的）
    assert p.files == (("tests/test_x.py", "tests/test_z.py"), ("tests/test_y.py",))


def test_a_file_is_never_split_across_shards():
    items = _items({f: 7 for f in W})
    p = shard.plan(items, W, 1, 3)
    for f, ids in items.items():
        homes = {i for i, s in enumerate(p.nodeids) if set(ids) & set(s)}
        assert len(homes) == 1, f"{f} 被拆到了 {homes}"
        assert set(ids) <= set(p.nodeids[next(iter(homes))])


def test_nodeids_inside_a_shard_keep_collection_order():
    items = _items({"tests/test_b.py": 3, "tests/test_a.py": 2, "tests/test_c.py": 3})
    p = shard.plan(items, W, 1, 1)
    assert list(p.nodeids[0]) == _all(items)


def test_union_equals_full_set_and_shards_are_disjoint():
    items = _items({f: 4 for f in W})
    for shards in (1, 2, 3, 5):
        p = shard.plan(items, W, 1, shards)
        shard.verify(p, _all(items))  # 不抛
        union = [n for s in p.nodeids for n in s]
        assert sorted(union) == sorted(_all(items))
        assert len(set(union)) == len(union)
        assert all(p.nodeids), "有空片"


# ---------------------------------------------------------------- 默认权重


def test_default_weight_is_the_75th_percentile_nearest_rank():
    assert shard.default_weight({"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0}) == 3.0
    assert shard.default_weight({"a": 5.0}) == 5.0
    assert shard.default_weight({str(i): float(i) for i in range(1, 101)}) == 75.0


def test_unknown_files_get_the_default_weight_not_zero():
    items = _items({**{f: 1 for f in W}, "tests/test_new_heavy.py": 1})
    p = shard.plan(items, W, 1, 2)
    assert p.unknown_files == ("tests/test_new_heavy.py",)
    assert p.default_weight == 10.0  # p75 of [1, 10, 10, 10, 100]
    # 新文件按 10 估：a（100）独占；其余 31 + 10 = 41
    assert p.estimated_seconds == (100.0, 41.0)
    assert "tests/test_new_heavy.py" in p.files[1]


def test_default_weight_of_an_empty_table_is_an_error():
    with pytest.raises(shard.ShardError, match="为空"):
        shard.default_weight({})


# ---------------------------------------------------------------- 负例：参数


@pytest.mark.parametrize(
    "spec", ["3/2", "0/2", "1/0", "0/0", "abc", "", "1/", "/2", "1/2/3", "-1/2"]
)
def test_bad_shard_spec_is_rejected(spec):
    with pytest.raises(shard.ShardError):
        shard.parse_spec(spec)


@pytest.mark.parametrize("spec,expected", [("1/1", (1, 1)), ("2/2", (2, 2)), (" 1 / 3 ", (1, 3))])
def test_good_shard_spec(spec, expected):
    assert shard.parse_spec(spec) == expected


def test_zero_shards_is_rejected_by_assign_and_plan():
    with pytest.raises(shard.ShardError):
        shard.assign(["tests/test_a.py"], W, 0, 1.0)
    with pytest.raises(shard.ShardError):
        shard.plan(_items({"tests/test_a.py": 1}), W, 1, 0)


def test_shard_index_out_of_range_is_rejected_by_plan():
    with pytest.raises(shard.ShardError):
        shard.plan(_items({"tests/test_a.py": 1}), W, 3, 2)


# ---------------------------------------------------------------- 负例：自验


def test_empty_collection_is_an_error_not_an_empty_green_run():
    p = shard.plan({}, W, 1, 2)
    with pytest.raises(shard.ShardError, match="为空"):
        shard.verify(p, [])


def test_more_shards_than_files_is_an_error():
    items = _items({"tests/test_a.py": 5, "tests/test_b.py": 5})
    p = shard.plan(items, W, 1, 3)
    with pytest.raises(shard.ShardError, match=r"第 3/3 片为空"):
        shard.verify(p, _all(items))


def test_duplicate_nodeids_in_the_collection_are_rejected():
    items = _items({"tests/test_a.py": 2, "tests/test_b.py": 2})
    p = shard.plan(items, W, 1, 2)
    with pytest.raises(shard.ShardError, match="重复 nodeid"):
        shard.verify(p, _all(items) + ["tests/test_a.py::test_0"])


def test_a_plan_that_drops_a_file_fails_the_union_check():
    items = _items({f: 2 for f in W})
    p = shard.plan(items, W, 1, 2)
    dropped = shard.Plan(
        shard=p.shard,
        shards=p.shards,
        files=(p.files[0], tuple(f for f in p.files[1] if f != "tests/test_e.py")),
        nodeids=(
            p.nodeids[0],
            tuple(n for n in p.nodeids[1] if not n.startswith("tests/test_e.py")),
        ),
        estimated_seconds=p.estimated_seconds,
        default_weight=p.default_weight,
        unknown_files=p.unknown_files,
    )
    with pytest.raises(shard.ShardError, match="漏掉 2 条"):
        shard.verify(dropped, _all(items))


def test_a_plan_with_an_extra_nodeid_fails_the_union_check():
    items = _items({f: 2 for f in W})
    p = shard.plan(items, W, 1, 2)
    extra = shard.Plan(
        shard=p.shard,
        shards=p.shards,
        files=p.files,
        nodeids=(p.nodeids[0] + ("tests/test_ghost.py::test_0",), p.nodeids[1]),
        estimated_seconds=p.estimated_seconds,
        default_weight=p.default_weight,
        unknown_files=p.unknown_files,
    )
    with pytest.raises(shard.ShardError, match="多出 1 条"):
        shard.verify(extra, _all(items))


def test_overlapping_shards_fail_the_disjointness_check():
    items = _items({f: 2 for f in W})
    p = shard.plan(items, W, 1, 2)
    overlapping = shard.Plan(
        shard=p.shard,
        shards=p.shards,
        files=p.files,
        nodeids=(p.nodeids[0] + p.nodeids[1][:1], p.nodeids[1]),
        estimated_seconds=p.estimated_seconds,
        default_weight=p.default_weight,
        unknown_files=p.unknown_files,
    )
    # 计数上多了一条，但「并集 == 全集」本身仍成立——只有不交那条抓得到它
    with pytest.raises(shard.ShardError, match="交集"):
        shard.verify(overlapping, _all(items))


# ---------------------------------------------------------------- 负例：权重表


def _table(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "w.json"
    p.write_text(body, encoding="utf-8")
    return p


_BAD_TABLES = {
    "bad_json": '{"source": "s", "measured": "m", "weights": {',
    "not_an_object": "[]",
    "no_provenance": '{"weights": {"tests/test_a.py": 1}}',
    "empty": '{"source": "s", "measured": "m", "weights": {}}',
    "weights_not_an_object": '{"source": "s", "measured": "m", "weights": []}',
    "negative": '{"source": "s", "measured": "m", "weights": {"tests/test_a.py": -1}}',
    "bool": '{"source": "s", "measured": "m", "weights": {"tests/test_a.py": true}}',
    "string": '{"source": "s", "measured": "m", "weights": {"tests/test_a.py": "1"}}',
    "nan": '{"source": "s", "measured": "m", "weights": {"tests/test_a.py": NaN}}',
    "inf": '{"source": "s", "measured": "m", "weights": {"tests/test_a.py": Infinity}}',
    "not_py": '{"source": "s", "measured": "m", "weights": {"tests/test_a": 1}}',
    "backslash": '{"source": "s", "measured": "m", "weights": {"tests\\\\test_a.py": 1}}',
    "absolute": '{"source": "s", "measured": "m", "weights": {"/abs/test_a.py": 1}}',
}


@pytest.mark.parametrize("why", list(_BAD_TABLES))
def test_bad_weight_tables_are_rejected(tmp_path, why):
    with pytest.raises(shard.ShardError):
        shard.load_weights(_table(tmp_path, _BAD_TABLES[why]))


def test_missing_weight_table_is_rejected(tmp_path):
    with pytest.raises(shard.ShardError, match="读不到"):
        shard.load_weights(tmp_path / "nope.json")


def test_a_good_weight_table_loads_as_floats(tmp_path):
    p = _table(
        tmp_path,
        '{"source": "s", "measured": "m", "weights": {"tests/test_a.py": 3, "tests/b/test_b.py": 0.5}}',
    )
    assert shard.load_weights(p) == {"tests/test_a.py": 3.0, "tests/b/test_b.py": 0.5}


def test_the_real_weight_table_loads_and_says_where_it_came_from():
    table = shard.load_weights()
    assert len(table) >= 100, "真实权重表薄得可疑"
    doc = json.loads(shard.WEIGHTS_PATH.read_text(encoding="utf-8"))
    assert "durations_by_file.csv" in doc["source"] or "junit" in doc["source"]
    assert doc["measured"]
    assert shard.default_weight(table) > 0


# ---------------------------------------------------------------- 从 junit 重算


def test_weights_from_junit_sums_time_per_file_and_maps_classnames(tmp_path):
    (tmp_path / "tests" / "pkg").mkdir(parents=True)
    (tmp_path / "tests" / "test_one.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "pkg" / "test_two.py").write_text("", encoding="utf-8")
    xml = tmp_path / "junit.xml"
    xml.write_text(
        '<testsuites><testsuite><testcase classname="tests.test_one" name="a" time="1.5"/>'
        '<testcase classname="tests.test_one.TestK" name="b" time="0.25"/>'
        '<testcase classname="tests.pkg.test_two" name="c" time="2"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    assert shard.weights_from_junit([xml], tmp_path) == {
        "tests/pkg/test_two.py": 2.0,
        "tests/test_one.py": 1.75,
    }


def test_weights_from_junit_refuses_a_classname_that_maps_to_no_file(tmp_path):
    xml = tmp_path / "junit.xml"
    xml.write_text(
        '<testsuites><testsuite><testcase classname="tests.test_gone" name="a" time="1"/></testsuite></testsuites>',
        encoding="utf-8",
    )
    with pytest.raises(shard.ShardError, match="对不回"):
        shard.weights_from_junit([xml], tmp_path)


def test_weights_from_junit_refuses_an_empty_junit(tmp_path):
    xml = tmp_path / "junit.xml"
    xml.write_text("<testsuites><testsuite/></testsuites>", encoding="utf-8")
    with pytest.raises(shard.ShardError, match="一条 testcase 都没有"):
        shard.weights_from_junit([xml], tmp_path)


# ---------------------------------------------------------------- conftest 那一层（真子进程）

_TWO_FILES = ("tests/test_aggregate_gate.py", "tests/test_pixel_compare.py")


def _pytest(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=str(ROOT),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )


@pytest.mark.parametrize("spec", ["3/2", "0/2", "abc"])
def test_a_bad_shard_spec_is_a_usage_error_not_a_silent_full_run(spec):
    out = _pytest("--shard", spec, "--collect-only", *_TWO_FILES)
    assert out.returncode == pytest.ExitCode.USAGE_ERROR, out.stdout + out.stderr
    assert f"--shard {spec}" in out.stderr


def test_more_shards_than_files_is_a_usage_error_not_an_empty_green_run():
    out = _pytest("--shard", "1/3", "--collect-only", *_TWO_FILES)
    assert out.returncode == pytest.ExitCode.USAGE_ERROR, out.stdout + out.stderr
    assert "片为空" in out.stderr


def test_a_manifest_without_a_shard_is_a_usage_error(tmp_path):
    out = _pytest("--shard-manifest", str(tmp_path / "m.json"), "--collect-only", *_TWO_FILES)
    assert out.returncode == pytest.ExitCode.USAGE_ERROR, out.stdout + out.stderr
    assert not (tmp_path / "m.json").exists()


def test_two_shards_partition_the_collection_and_write_manifests(tmp_path):
    full = _pytest("--collect-only", *_TWO_FILES)
    assert full.returncode == 0, full.stdout + full.stderr
    full_ids = {ln for ln in full.stdout.splitlines() if "::" in ln}
    assert len(full_ids) > 2
    parts: list[set[str]] = []
    for k in (1, 2):
        out = _pytest(
            "--shard",
            f"{k}/2",
            "--shard-manifest",
            str(tmp_path / f"m{k}.json"),
            "--collect-only",
            *_TWO_FILES,
        )
        assert out.returncode == 0, out.stdout + out.stderr
        parts.append({ln for ln in out.stdout.splitlines() if "::" in ln})
        m = json.loads((tmp_path / f"m{k}.json").read_text(encoding="utf-8"))
        assert (m["shard"], m["shards"]) == (k, 2)
        assert m["nodeids_total"] == len(full_ids)
        assert m["files_total"] == 2 and m["files_selected"] == 1
        assert m["nodeids_selected"] == len(parts[-1])
        assert len(m["git_head"]) == 40 or m["git_head"] == "unknown"
        assert m["weights_source"] == "tests/support/shard_weights.json"
    assert parts[0] | parts[1] == full_ids
    assert not (parts[0] & parts[1])
    assert parts[0] and parts[1]
