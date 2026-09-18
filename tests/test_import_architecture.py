"""`src/tavotto` 的增量架构门禁：不新增 import 环、旧环不扩大、不新增跨层反向边、动态
import 逐条登记。图由 `tests/support/importgraph.py` 用 `ast` 建（不 import 产品模块，
不需要 matplotlib），基线在 `tests/import_architecture_baseline.json`。

判据的主语：**生产模块之间的运行时边**——普通 import、平铺 import（worker / browser /
manifest 那种先塞 `sys.path` 再裸 `import manifest`，静态解析按「同目录有这个文件」补上）、
登记过的动态 import；`if TYPE_CHECKING:` 里的不算。环 = 强连通分量（≥2 节点）；「旧环
扩大」= 成员多了或分量内部的边多了——**延后到函数里再 import 也是逻辑依赖**，照样算边，
只是在报告里标成「逻辑」而不是「加载期」。

基线是登记表不是豁免表：每个环带 reason / since / remove_when；环没了要把它删掉（旧
豁免会安静地变成盲区），所以「基线里有、图里没有」同样红。解析不了的动态 import 不会
被当成「没有」：不登记就红。

2026-09-17 审计任务书点名的两处环之一（manifest ↔ overrides，反向那步经
`_sibling("manifest")` 动态取）在基线里；PR D 拆掉它之后把那条登记删掉，这里会替你确认。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))

import importgraph  # noqa: E402

BASELINE = importgraph.load_baseline()
GRAPH = importgraph.build(BASELINE)
FOUND = importgraph.cycles(GRAPH)


def _key(members) -> str:
    return " | ".join(sorted(members))


def _edge_key(e) -> str:
    return f"{e[0]} -> {e[1]}"


# ---------------------------------------------------------------- 前提


def test_the_graph_is_actually_there():
    """判据的前提：真的扫到了一批模块与边；平铺 import 被补上了；type-only 分得出来。"""
    assert len(GRAPH.nodes) >= 80, f"只扫到 {len(GRAPH.nodes)} 个模块"
    runtime = GRAPH.runtime_edges()
    assert len(runtime) >= 200, f"只扫到 {len(runtime)} 条运行时边"
    flat = [e for e in runtime if e.kind == "flat"]
    assert any(
        e.src.endswith("engine/manifest.py") and e.dst.endswith("engine/overrides.py") for e in flat
    ), (
        "manifest 的平铺 `from overrides import …` 没被解析成边——平铺 import 一旦看不见，worker 侧九个模块在图上是孤岛"
    )


def test_every_import_resolves_or_is_external():
    """相对 / 包内 import 都要解析得开；解析不开 = 图上缺边，不是「没有依赖」。"""
    assert GRAPH.unresolved == [], GRAPH.unresolved


def test_dynamic_imports_are_registered():
    """`importlib.import_module(<非字面量>)` 之类逐条登记（目标是仓库模块 / 用户代码 / 标准库）。"""
    assert GRAPH.unknown_dynamic == [], (
        "这些动态 import 没登记（在 tests/import_architecture_baseline.json 的 dynamic_calls 里给出目标或说明）:\n  "
        + "\n  ".join(f"{u['from']}:{u['line']}  {u['call']}" for u in GRAPH.unknown_dynamic)
    )


def test_registered_dynamic_calls_still_exist():
    """登记表里的动态 import 也得还在——否则那条登记就是过期的。"""
    calls = set()
    for path in importgraph._module_files():
        rel = importgraph._rel(path)
        for d in BASELINE["dynamic_calls"]:
            if d["from"] == rel and d["call"] in " ".join(path.read_text(encoding="utf-8").split()):
                calls.add((d["from"], d["call"]))
    stale = [
        (d["from"], d["call"])
        for d in BASELINE["dynamic_calls"]
        if (d["from"], d["call"]) not in calls
    ]
    assert stale == [], f"这些登记的动态 import 在源码里已经没有了，删掉登记: {stale}"


def _bridge_runner_phases() -> dict[str, set[str]]:
    """`bridge_runner._PHASE1` / `_PHASE2` 两个字面量元组（真正决定装什么进用户进程的清单）。"""
    tree = ast.parse((importgraph.PKG / "engine" / "bridge_runner.py").read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in ("_PHASE1", "_PHASE2") for t in node.targets
        ):
            assert isinstance(node.value, ast.Tuple)
            out[node.targets[0].id] = {  # type: ignore[attr-defined]
                f"tavotto/engine/{e.value}.py"
                for e in node.value.elts
                if isinstance(e, ast.Constant)
            }
    assert set(out) == {"_PHASE1", "_PHASE2"}, (
        "用例前提：bridge_runner 里确实有 _PHASE1/_PHASE2 两批装载清单"
    )
    return out


@pytest.mark.parametrize("phase,scope", [("_PHASE1", "module"), ("_PHASE2", "function")])
def test_bridge_runner_load_lists_match_the_registered_extra_edges(phase, scope):
    """`extra_edges` 是「登记推出的边」：bridge_runner 经 bridgeboot 装进用户进程的每个模块一条。
    装载清单多了一个模块而登记没跟上，图上那条边就悄悄没了——往 `_PHASE2` 加族模块（PR D
    第二步每切一族加一个）时这里会替你要求补登记；反过来登记了清单里没有的也红。"""
    listed = _bridge_runner_phases()[phase]
    registered = {
        e["to"]
        for e in BASELINE["extra_edges"]
        if e["from"] == "tavotto/engine/bridge_runner.py" and phase in e.get("via", "")
    }
    assert registered == listed, (
        f"bridge_runner.{phase} 与 extra_edges 的登记对不上：清单有而没登记 {sorted(listed - registered)}，"
        f"登记了而清单没有 {sorted(registered - listed)}"
    )
    scopes = {
        e["scope"]
        for e in BASELINE["extra_edges"]
        if e["from"] == "tavotto/engine/bridge_runner.py" and phase in e.get("via", "")
    }
    assert scopes == {scope}, (
        f"{phase} 那批边的 scope 应全是 {scope}（第一阶段在模块层装、第二阶段在函数里装）：{scopes}"
    )


# ---------------------------------------------------------------- 环只减不增


def test_no_cycle_outside_the_baseline():
    """与任何基线环都不相干的强连通分量 = 新环。拆掉它，或登记并写清 reason / remove_when。"""
    novel = [
        c
        for c in FOUND
        if not any(m in b["members"] for b in BASELINE["cycles"] for m in c["members"])
    ]
    assert novel == [], "新出现的 import 环:\n  " + "\n  ".join(
        " ↔ ".join(c["members"]) for c in novel
    )


@pytest.mark.parametrize("known", BASELINE["cycles"], ids=lambda c: _key(c["members"]))
def test_known_cycle_did_not_grow(known):
    """旧环的成员与内部边都必须是基线的子集。"""
    matches = [c for c in FOUND if any(m in known["members"] for m in c["members"])]
    assert matches, (
        f"基线里的环 {_key(known['members'])} 在图上已经没有了——把它从基线删掉（过期的登记是盲区）"
    )
    for c in matches:
        extra_members = sorted(set(c["members"]) - set(known["members"]))
        extra_edges = sorted(
            {_edge_key(e) for e in c["edges"]} - {_edge_key(e) for e in known["edges"]}
        )
        assert extra_members == [], f"环 {_key(known['members'])} 多出成员: {extra_members}"
        assert extra_edges == [], f"环 {_key(known['members'])} 多出内部边: {extra_edges}"


@pytest.mark.parametrize("known", BASELINE["cycles"], ids=lambda c: _key(c["members"]))
def test_known_cycle_is_documented(known):
    """每个已知环都写了理由与删除条件，且「加载期与否」与图上量到的一致。"""
    assert known.get("reason") and known.get("remove_when"), _key(known["members"])
    match = next(c for c in FOUND if set(c["members"]) == set(known["members"]))
    assert match["load_time"] == known["load_time"], (
        f"环 {_key(known['members'])} 的 load_time 与图上量到的不一致（基线 {known['load_time']}，图 {match['load_time']}）"
    )


# ---------------------------------------------------------------- 跨层


def test_no_reverse_edge_into_the_entry_layer():
    """底层不许反向依赖入口层（`app.py` / `cli_entry.py` / `__main__.py`）——任务书点名要挡的那一类。"""
    known = {(v["from"], v["to"]) for v in BASELINE["layer_violations"]}
    novel = [v for v in importgraph.layer_violations(GRAPH) if (v["from"], v["to"]) not in known]
    assert novel == [], "新增的跨层反向边:\n  " + "\n  ".join(
        f"{v['from']}:{v['line']} → {v['to']}（{v['rule']}）" for v in novel
    )


def test_no_engine_module_imports_flask():
    """Flask 父进程 import 链上的 engine 模块必须纯标准库这条边界另有其主；这里只钉一条最便宜的：
    `engine/` 里没有任何模块 import flask / werkzeug（那是 app.py 的事）。"""
    offenders = sorted(
        src
        for src, names in GRAPH.externals.items()
        if src.startswith("tavotto/engine/") and names & {"flask", "werkzeug"}
    )
    assert offenders == [], offenders
