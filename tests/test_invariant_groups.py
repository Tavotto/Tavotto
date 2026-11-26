"""CI invariants 一步按图分三组、同机并行（2026-11-25）的静态合同：拆开之后一条都不能漏。

每个进程只能看到自己那组，看不见别的组有没有跑——所以「合起来 == 全集」只能静态钉：
1. 三组的图两两不交、并集 == 用例实际用到的全部图（STEMS ∪ InvScale ∪ REMOVAL_CASES 的图）；
2. test_invariants_engine.py 的每个用例要么按图参数化（随组走），要么带 `invariant_rest`（只在 c 组跑）；
3. ci.yml 的那一步：三组各起一个进程、逐个 wait、任一非零整步非零；a / b 排掉 rest，c 不排。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from support import invariant_figures as F

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "tests" / "test_invariants_engine.py"
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def _stem_parametrizes() -> list[tuple[str, str]]:
    """(用例名, 给 stem 提供值的那个 parametrize 的取值表达式源码)。"""
    src = ENGINE.read_text(encoding="utf-8")
    out = []
    for node in ast.parse(src).body:
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test_")):
            continue
        for d in node.decorator_list:
            if isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "parametrize":
                names = ast.literal_eval(d.args[0])
                if "stem" in [x.strip() for x in names.split(",")]:
                    out.append((node.name, ast.get_source_segment(src, d.args[1])))
    assert out, "一个按图参数化的用例都没找到——判据没有主语"
    return out


def _figures_used() -> set[str]:
    """用例实际用到的全部图：STEMS、InvScale，加上每个按图参数化引用的表（未过滤的原始值）里的图名。"""
    src = ENGINE.read_text(encoding="utf-8")
    tables = {
        n.targets[0].id: ast.get_source_segment(src, n.value)
        for n in ast.parse(src).body
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
    }
    used = set(F.STEMS) | {"InvScale"}
    for _, expr in _stem_parametrizes():
        used |= set(re.findall(r'"(Inv\w+)"', expr))
        for name in re.findall(r"\b([A-Z_][A-Z0-9_]+)\b", expr):
            used |= set(re.findall(r'"(Inv\w+)"', tables.get(name, "")))
    return used


def test_groups_are_disjoint_and_cover_every_figure_the_cases_use():
    groups = F.INVARIANT_GROUPS
    assert sorted(groups) == ["a", "b", "c"]
    seen: dict[str, str] = {}
    for g, stems in groups.items():
        for st in stems:
            assert st not in seen, f"{st} 同时在 {seen[st]} 组和 {g} 组"
            seen[st] = g
    universe = _figures_used()
    assert set(seen) == universe, (
        f"组的并集与用例用到的图不一致：漏了 {sorted(universe - set(seen))}，多了 {sorted(set(seen) - universe)}"
    )


def test_every_per_figure_parametrize_is_filtered_by_group():
    """给 stem 提供值的表都要按组过滤（in_group / GROUP_STEMS*），否则这个用例会在三组里各跑一遍。"""
    loose = [
        name
        for name, expr in _stem_parametrizes()
        if "in_group(" not in expr and expr not in ("GROUP_STEMS", "GROUP_STEMS_WITH_SCALE")
    ]
    assert not loose, f"这些按图参数化的用例没有按组过滤：{loose}"


def test_every_engine_case_is_either_per_figure_or_marked_rest():
    tree = ast.parse(ENGINE.read_text(encoding="utf-8"))
    loose = []
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test_")):
            continue
        per_figure = "stem" in [a.arg for a in node.args.args]
        marked = any(isinstance(d, ast.Name) and d.id == "rest" for d in node.decorator_list)
        assert not (per_figure and marked), f"{node.name} 按图参数化又标了 rest：会在 c 组重复跑"
        if not per_figure and not marked:
            loose.append(node.name)
    assert not loose, f"这些用例既不按图参数化也没标 @rest，分组后 a / b / c 都不跑：{loose}"


def test_unknown_group_name_fails_loudly(monkeypatch):
    monkeypatch.setenv(F.GROUP_ENV, "d")
    with pytest.raises(ValueError, match="不是已知的组"):
        F.selected_group()
    monkeypatch.delenv(F.GROUP_ENV)
    assert F.selected_group() is None and F.in_group("InvMix")


def _structural_step() -> str:
    m = re.search(r"(?ms)^      - name: 结构性不变式\n(.*?)(?=^      - name: )", CI)
    assert m, "ci.yml 里找不到「结构性不变式」这一步"
    return m.group(1)


def test_ci_runs_every_group_once_and_propagates_each_exit_code():
    step = _structural_step()
    assert "if:" not in step.split("run:", 1)[0], "必需步骤不许带 if:"
    m = re.search(r"for g in ([a-z ]+); do", step)
    assert m and m.group(1).split() == sorted(F.INVARIANT_GROUPS), (
        f"循环要恰好覆盖所有组：{m and m.group(1)}"
    )
    assert 'TAVOTTO_INVARIANT_GROUP="$g"' in step
    assert "tests/test_invariants_engine.py" in step
    assert 'wait "$pid"' in step and 'exit "$fail"' in step, "每个进程的退出码都要进控制流"
    assert "|| true" not in step
    # a / b 排掉 rest，且仍排掉 slow（-m 会覆盖 pytest.ini 里的 addopts）；c 不排 rest
    assert '"not slow and not invariant_rest"' in step
    assert re.search(r'\[ "\$g" = c \]', step), "c 组的条件写法认不出"
    for f in (
        "test_docs_references.py",
        "test_artist_families.py",
        "test_artist_census_tool.py",
        "test_patchspec.py",
        "test_preflight.py",
    ):
        assert f"tests/{f}" in step, f"{f} 没进 c 组"
    assert '--basetemp="$RUNNER_TEMP/' in step and "-p no:cacheprovider" in step, (
        "并行进程要各自的临时目录、不写共享缓存"
    )
