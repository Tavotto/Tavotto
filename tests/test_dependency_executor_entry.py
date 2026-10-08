"""执行器入口的结构守卫：谁能让 pip 跑起来，谁就必须拿到**调用方**回显的影响摘要。

同一类缺口已被抓到五次（r4217232854 联合端点、r4217992305 单包端点与 MCP、r4218802478 准备会话）。前几次的守卫
都在查「端点清单」，新的入口一多就又漏一个。这里改成查**执行器的入口**：

* `install_async` / `install` / `prepare_async` / `prepare` / `start_confirmed` 的摘要参数没有默认值（必须显式传），
  且缺 / 空时 `dependency_impact_required`、零副作用——不存在「None = 旧客户端、不绑定」；
* 全仓库（后端 + MCP 桥）对这些函数的每一处调用，摘要实参必须是一个**名字**（形参，或请求体里取出来的局部变量），
  不能是 `plan.impact_digest` / `action.impact_digest` / 缓存里取的值——服务端自己持有的摘要不得代替调用方回显；
* 调用点清单是写死的：新增一处执行入口，这个测试就红，逼作者回来核它的摘要从哪来。
"""

from __future__ import annotations

import ast
import inspect
import threading
from pathlib import Path

import pytest

from tavotto.engine import deprepair

ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOTS = (ROOT / "src" / "tavotto", ROOT / "codex-plugin" / "mcp")
#: 会执行安装的入口 -> 摘要实参的位置（关键字名；start_confirmed 是第 3 个位置参数）
EXECUTORS = {
    "install_async": "confirmed_impact",
    "install": "confirmed_impact",
    "prepare_async": "confirmed_impact",
    "prepare": "confirmed_impact",
    "start_confirmed": "confirmed_digest",
}
RECEIVERS = {"deprepair", "engine_deprepair"}

#: (文件, 所在函数, 被调函数) —— 逐个核过摘要来源
EXPECTED_CALL_SITES = {
    ("src/tavotto/app.py", "start", "start_confirmed"),
    ("src/tavotto/app.py", "api_dependency_install", "install_async"),
    ("src/tavotto/app.py", "api_dependencies_prepare", "prepare_async"),
    ("codex-plugin/mcp/tavotto_mcp/bridge.py", "_answer_prepare_dependencies", "prepare"),
    ("src/tavotto/engine/deprepair.py", "_install_guarded", "install"),
    ("src/tavotto/engine/deprepair.py", "_prepare_guarded", "prepare"),
}


def _own_nodes(fn):
    """函数自己的节点（不含嵌套函数里的）——调用点归属最内层函数。"""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def _calls():
    for base in SCAN_ROOTS:
        for path in sorted(base.rglob("*.py")):
            tree = ast.parse(path.read_text("utf-8"))
            rel = path.relative_to(ROOT).as_posix()
            inside = rel == "src/tavotto/engine/deprepair.py"
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                params = {a.arg for a in (*fn.args.args, *fn.args.kwonlyargs, *fn.args.posonlyargs)}
                assigned = {
                    t.id: n.value
                    for n in _own_nodes(fn)
                    if isinstance(n, ast.Assign)
                    for t in n.targets
                    if isinstance(t, ast.Name)
                }
                for node in _own_nodes(fn):
                    if not isinstance(node, ast.Call):
                        continue
                    f = node.func
                    if (
                        isinstance(f, ast.Attribute)
                        and isinstance(f.value, ast.Name)
                        and f.value.id in RECEIVERS
                        and f.attr in EXECUTORS
                    ):
                        name = f.attr
                    elif inside and isinstance(f, ast.Name) and f.id in EXECUTORS:
                        name = f.id
                    else:
                        continue
                    if inside and name in ("install", "prepare") and fn.name in EXECUTORS:
                        continue  # 执行器自己的定义里的递归 / 自引用不是调用点
                    yield rel, fn.name, name, node, params, assigned


def _digest_arg(node: ast.Call, name: str):
    for kw in node.keywords:
        if kw.arg == EXECUTORS[name]:
            return kw.value
    if name == "start_confirmed" and len(node.args) >= 3:
        return node.args[2]
    return None


def test_every_executor_call_site_is_known_and_binds_a_caller_supplied_digest():
    seen = set()
    for rel, func, name, node, params, assigned in _calls():
        seen.add((rel, func, name))
        arg = _digest_arg(node, name)
        where = f"{rel}:{node.lineno} {func} -> {name}"
        assert arg is not None, f"{where}: 没有传摘要实参"
        assert isinstance(arg, ast.Name), (
            f"{where}: 摘要必须是形参或请求体里取出的局部变量，不能是表达式（{ast.unparse(arg)}）——"
            "服务端持有的摘要不得代替调用方回显"
        )
        if arg.id in params:
            continue
        src = assigned.get(arg.id)
        assert src is not None and "get('impact_digest')" in ast.unparse(src), (
            f'{where}: {arg.id} 既不是形参、也不是从请求体 `.get("impact_digest")` 取出的'
        )
    assert seen == EXPECTED_CALL_SITES, (
        f"新增 / 消失的执行入口：{sorted(seen ^ EXPECTED_CALL_SITES)}。核它的摘要来自调用方请求，再更新清单"
    )


def test_the_session_layer_hands_the_executor_the_echoed_digest_not_the_actions():
    """准备会话 `_claim_prepare` 经注入的 `prepare` 回调进执行器（名字扫不到）：单独核它传的是形参 `echoed`。"""
    path = ROOT / "src" / "tavotto" / "engine" / "prepsession.py"
    tree = ast.parse(path.read_text("utf-8"))
    (fn,) = [
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_claim_prepare"
    ]
    (call,) = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "prepare"
    ]
    (digest,) = [kw.value for kw in call.keywords if kw.arg == "digest"]
    assert isinstance(digest, ast.Name) and digest.id == "echoed"
    assert "echoed" in {a.arg for a in fn.args.args}


@pytest.mark.parametrize("name", ["install_async", "install", "prepare_async", "prepare"])
def test_the_digest_parameter_has_no_default(name):
    param = inspect.signature(getattr(deprepair, name)).parameters["confirmed_impact"]
    assert param.default is inspect.Parameter.empty and param.kind is param.KEYWORD_ONLY


def test_start_confirmed_digest_has_no_default():
    param = inspect.signature(deprepair.start_confirmed).parameters["confirmed_digest"]
    assert param.default is inspect.Parameter.empty


@pytest.mark.parametrize("missing", [None, "", 7])
def test_a_missing_digest_is_refused_before_anything_is_claimed(tmp_path, missing):
    for call in (
        lambda: deprepair.install_async("p1", confirmed_impact=missing),
        lambda: deprepair.install("p1", confirmed_impact=missing),
        lambda: deprepair.prepare_async("p1", confirmed_impact=missing),
        lambda: deprepair.prepare("p1", confirmed_impact=missing),
        lambda: deprepair.start_confirmed(tmp_path, "figure.py", missing),
    ):
        with pytest.raises(deprepair.RepairError) as err:
            call()
        assert err.value.code == deprepair.ERROR_IMPACT_REQUIRED
    assert "p1" not in deprepair._running


def test_joined_jobs_are_partitioned_by_dependency_scope(tmp_path, monkeypatch):
    """r4218802492：同项目同摘要、不同目录两次授权不能合并成一个作业（账上归属只记在第一个作用域）。"""
    gate = threading.Event()
    made: list[str] = []

    class _Plan:
        impact_digest = "same-digest"
        impact: dict = {}

        def __init__(self, script):
            self.plan_id = f"jp-{len(made)}"
            self.script = script
            made.append(self.plan_id)

    monkeypatch.setattr(deprepair, "create_joint_plan", lambda root, script, **kw: _Plan(script))
    monkeypatch.setattr(deprepair, "_prepare_guarded", lambda pid, on_event, **kw: gate.wait(10))
    try:
        a = deprepair.start_confirmed(tmp_path, "a/fig.py", "same-digest")
        b = deprepair.start_confirmed(tmp_path, "b/fig.py", "same-digest")
        assert a["started"] is True and b["started"] is True, (a, b)
        assert a["plan_id"] != b["plan_id"]
        # 同一作用域再来一次才合并
        again = deprepair.start_confirmed(tmp_path, "a/other.py", "same-digest")
        assert again["joined"] is True and again["plan_id"] == a["plan_id"]
    finally:
        gate.set()
        for pid in made:
            deprepair._release(pid)
