"""仅开发可用的故障注入 + 环境故障评测家族的离线看护（ADR 0221 §六）。

不叫模型、不联网、不起 sidecar：
* 注入点只在 `TAVOTTO_DEV=1` 且点名的 `TAVOTTO_DEV_FAULT_<名>=1` 同时设了才生效，其余一律原样走产品路径；
* 产品里的注入点数量钉死（加一个要同时改这里，逼着补「不设就不生效」的用例）；
* 假 wheel 夹具真能被 pip 离线装上 / 装不上，import 时的形状与题库写的一致；
* 题库里的环境题字段齐全，`harness.py list` 列得出来；
* summarize 的两列在没有 known_issue 键时不报错。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tavotto.engine import deprepair, devfault

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "scripts" / "eval" / "fix_compat"


def _load(name: str):
    """按文件路径加载评测脚本，起独一无二的模块名：不往 sys.path 塞目录、不占用 `summarize` 这类通用名
    （别的测试也有叫 summarize 的模块，裸名 import 会互相顶掉 `sys.modules` 里的那一份）。"""
    spec = importlib.util.spec_from_file_location(f"_fix_compat_{name}", EVAL / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


envfaults = _load("envfaults")
summarize = _load("summarize")

CORPUS = json.loads((EVAL / "corpus.json").read_text(encoding="utf-8"))
ENV_ITEMS = {k: v for k, v in CORPUS.items() if v.get("family") == "environment"}
FAULT_VAR = "TAVOTTO_DEV_FAULT_SELFTEST_BLOCKED"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k == "TAVOTTO_DEV" or k.startswith("TAVOTTO_DEV_FAULT_"):
            monkeypatch.delenv(k)


# ---------------------------------------------------------------- 开关本身
def test_closed_set_and_unknown_names_are_loud():
    assert devfault.FAULTS == ("selftest_blocked",)
    with pytest.raises(KeyError):
        devfault.active("nope")


@pytest.mark.parametrize(
    "dev,fault,expected",
    [
        (None, None, False),
        (None, "1", False),  # 只设故障变量、没设 TAVOTTO_DEV：不生效
        ("1", None, False),  # 只设 TAVOTTO_DEV：不生效
        ("0", "1", False),
        ("true", "1", False),  # 只认字面 1
        ("1", "true", False),
        ("1", "1", True),
    ],
)
def test_both_gates_must_be_open(monkeypatch, dev, fault, expected):
    if dev is not None:
        monkeypatch.setenv("TAVOTTO_DEV", dev)
    if fault is not None:
        monkeypatch.setenv(FAULT_VAR, fault)
    assert devfault.active("selftest_blocked") is expected


def _injection_calls(source: str) -> list[str]:
    """源码里真实的 `devfault.active` 调用节点，返回各自所在的函数名（模块级为 `<module>`）。

    认的写法：`devfault.active(..)`（含 `import … as x` / `from … import devfault as x` 的别名）、
    `from …devfault import active [as y]` 后的 `active(..)`、带包前缀的 `….devfault.active(..)`。
    看的是 `ast.Call` 节点：注释 / 字符串里的字样不算，`devfault.active (x)` 这类空格写法照算。
    """
    tree = ast.parse(source)
    mod_names: set[str] = {"devfault"}
    fn_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "devfault":
                    mod_names.add(a.asname or a.name)
                elif a.name == "active" and (node.module or "").split(".")[-1] == "devfault":
                    fn_names.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[-1] == "devfault" and a.asname:
                    mod_names.add(a.asname)

    def is_inject(call: ast.Call) -> bool:
        f = call.func
        if isinstance(f, ast.Name):
            return f.id in fn_names
        if isinstance(f, ast.Attribute) and f.attr == "active":
            v = f.value
            if isinstance(v, ast.Name):
                return v.id in mod_names
            return isinstance(v, ast.Attribute) and v.attr == "devfault"
        return False

    found: list[str] = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call) and is_inject(child):
                found.append(scope)
            inner = (
                child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else scope
            )
            visit(child, inner)

    visit(tree, "<module>")
    return found


@pytest.mark.parametrize(
    "src,expected",
    [
        (
            "from tavotto.engine import devfault\ndef f():\n    return devfault.active ('x')\n",
            ["f"],
        ),
        ("from tavotto.engine import devfault as d\ndef f():\n    return d.active('x')\n", ["f"]),
        ("from .devfault import active\ndef f():\n    return active('x')\n", ["f"]),
        ("import tavotto.engine.devfault as d\nd.active('x')\n", ["<module>"]),
        ("import tavotto.engine.devfault\ntavotto.engine.devfault.active('x')\n", ["<module>"]),
        (
            "from tavotto.engine import devfault\n# devfault.active('x')\ns = 'devfault.active(x)'\n",
            [],
        ),
        ("def active(x):\n    return x\nactive(1)\n", []),  # 同名但不是 devfault 的
    ],
)
def test_injection_call_finder_sees_calls_not_text(src, expected):
    assert _injection_calls(src) == expected


def test_the_product_has_exactly_one_injection_point():
    """产品代码里 `devfault.active` 的真实调用节点全仓只有一个，且在 deprepair.worker_self_test 里。加注入点要先改这条。"""
    hits = []
    for p in sorted((ROOT / "src").rglob("*.py")):
        if p.name == "devfault.py":
            continue
        rel = p.relative_to(ROOT).as_posix()
        hits += [(rel, scope) for scope in _injection_calls(p.read_text(encoding="utf-8"))]
    assert hits == [("src/tavotto/engine/deprepair.py", "worker_self_test")], hits


# ---------------------------------------------------------------- 注入点：worker_self_test
class _Reached(Exception):
    """没被注入拦下，走到了产品自己的第一步（建临时目录）。"""


def _trap_real_path(monkeypatch):
    def boom(*a, **k):
        raise _Reached

    monkeypatch.setattr(deprepair.tempfile, "mkdtemp", boom)


@pytest.mark.parametrize(
    "env",
    [
        {},
        {FAULT_VAR: "1"},  # 没有 TAVOTTO_DEV
        {"TAVOTTO_DEV": "1"},  # 没有点名故障
        {"TAVOTTO_DEV": "0", FAULT_VAR: "1"},
    ],
)
def test_self_test_runs_the_real_path_unless_both_gates_are_open(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    _trap_real_path(monkeypatch)
    with pytest.raises(_Reached):
        deprepair.worker_self_test(sys.executable)


def test_self_test_fault_has_the_fth9_shape(monkeypatch):
    monkeypatch.setenv("TAVOTTO_DEV", "1")
    monkeypatch.setenv(FAULT_VAR, "1")
    _trap_real_path(monkeypatch)  # 注入生效时不应走到真路径
    res = deprepair.worker_self_test(sys.executable)
    assert res["ok"] is False
    f = res["failure"]
    assert f["stage"] == deprepair.SELFTEST_STAGE_ERROR_REPLY
    assert f["worker_code"] == "safe_file_operation_blocked"
    # 自检失败 → 安装路径报的稳定码
    assert deprepair._selftest_error(res).code == deprepair.ERROR_SELFTEST_FAILED


# ---------------------------------------------------------------- 假 wheel 夹具
def _unpack(house: Path, target: Path, stem_prefix: str) -> None:
    """装 wheel = 解压进 site-packages（wheel 就是 zip）。不依赖测试解释器里有 pip——验的是 wheel 内容与 import 形状。"""
    (whl,) = [w for w in house.glob("*.whl") if w.name.startswith(stem_prefix)]
    with zipfile.ZipFile(whl) as zf:
        zf.extractall(target)


def _import_in(
    target: Path, name: str, env_extra: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", f"import {name}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(target), **(env_extra or {})},
    )


def _build(tmp_path: Path, cid: str) -> tuple[Path, dict]:
    house = tmp_path / "house"
    env = envfaults.build_fixtures(ENV_ITEMS[cid]["fault"], house, sys.executable)
    return house, env


def test_selftest_item_sets_the_dev_gates_and_find_links(tmp_path):
    house, env = _build(tmp_path, "env-selftest-blocked")
    assert env == {
        "PIP_FIND_LINKS": str(house),
        "TAVOTTO_DEV": "1",
        FAULT_VAR: "1",
    }


def test_other_items_do_not_touch_the_dev_gates(tmp_path):
    for cid, item in ENV_ITEMS.items():
        if item["fault"].get("dev_fault"):
            continue
        _, env = _build(tmp_path / cid, cid)
        assert not any(k.startswith("TAVOTTO_DEV") for k in env), (cid, env)


def test_wheel_python_mismatch_is_not_a_supported_tag_of_this_interpreter(tmp_path):
    from packaging import tags

    house, _ = _build(tmp_path, "env-wheel-python-mismatch")
    (whl,) = house.glob("*.whl")
    wheel_tags = {str(t) for t in tags.parse_tag(whl.stem.split("-", 2)[2])}
    assert wheel_tags and wheel_tags.isdisjoint({str(t) for t in tags.sys_tags()})
    assert whl.name.split("-")[2].startswith("cp3")


def test_import_dll_fail_installs_then_fails_on_import(tmp_path):
    house, _ = _build(tmp_path, "env-import-dll-fail")
    _unpack(house, tmp_path / "t", "tavfx_dll-2.0")
    imp = _import_in(tmp_path / "t", "tavfx_dll")
    assert imp.returncode != 0 and "ImportError: DLL load failed" in imp.stderr
    # 旧版 1.9 是好的：题的出路
    _unpack(house, tmp_path / "t2", "tavfx_dll-1.9")
    assert _import_in(tmp_path / "t2", "tavfx_dll").returncode == 0


def test_permission_denied_item_is_fixed_by_pointing_the_cache_somewhere_writable(tmp_path):
    house, _ = _build(tmp_path, "env-permission-denied")
    _unpack(house, tmp_path / "t", "tavfx_perm")
    bad = _import_in(tmp_path / "t", "tavfx_perm")
    assert bad.returncode != 0 and "PermissionError" in bad.stderr
    ok = _import_in(tmp_path / "t", "tavfx_perm", {"TAVFX_CACHE": str(tmp_path / "cache")})
    assert ok.returncode == 0, ok.stderr


# ---------------------------------------------------------------- 题库 / harness
def test_every_environment_item_is_well_formed():
    assert set(ENV_ITEMS) == {
        "env-selftest-blocked",
        "env-wheel-python-mismatch",
        "env-import-dll-fail",
        "env-permission-denied",
    }
    for cid, it in ENV_ITEMS.items():
        assert it["script"] in it["files"], cid
        assert it["fault"]["wheels"], cid
        assert it["fault"]["expect"]["code"].startswith("dependency_"), cid
        assert it["fault"]["wheels"][0]["import"] in it["files"][it["script"]], cid
        # 合成数据：假包名一律 tavfx 打头（PyPI 上没有），不含真实用户内容
        assert all(w["dist"].startswith("tavfx-") for w in it["fault"]["wheels"]), cid


def test_harness_list_shows_the_environment_items(tmp_path):
    out = subprocess.run(  # noqa: S603
        [sys.executable, str(EVAL / "harness.py"), "list"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env={**os.environ, "EVAL_RESULTS": str(tmp_path / "res"), "PYTHONPATH": str(ROOT / "src")},
    )
    assert out.returncode == 0, out.stderr
    for cid in ENV_ITEMS:
        assert any(line.split()[:2] == [cid, "environment"] for line in out.stdout.splitlines()), (
            cid
        )


# ---------------------------------------------------------------- summarize 两列
def test_known_issue_column_is_false_without_the_key_and_true_with_it():
    plain = {"trajectory": [{"tool": "read_run_report", "result": True}]}
    assert summarize.cited_known_issue(plain) is False
    assert summarize.cited_known_issue({}) is False
    assert summarize.cited_known_issue({"trajectory": [{"known_issue": "x-1"}]}) is True


def test_misjudged_user_fault_rules():
    bad_issue = {
        "cond": "A",
        "trajectory": [
            {"tool": "prepare_diagnostics", "args": {"issue": "<dict>"}, "result": "bad_arguments"}
        ],
    }
    assert summarize.misjudged_user_fault(bad_issue) is True
    # 产品缺陷题没修好、也没报出去
    gave_up = {"cond": "A", "product_defect": True, "success": False, "trajectory": []}
    assert summarize.misjudged_user_fault(gave_up) is True
    # 报出去了 = 没当成用户问题
    reported = {
        **gave_up,
        "trajectory": [
            {"tool": "prepare_diagnostics", "args": {"issue": "<dict>"}, "result": True}
        ],
    }
    assert summarize.misjudged_user_fault(reported) is False
    # 非产品缺陷题、或 baseline / B：不算
    assert summarize.misjudged_user_fault({**gave_up, "product_defect": False}) is False
    assert summarize.misjudged_user_fault({**gave_up, "cond": "baseline"}) is False


def test_environment_table_is_empty_safe():
    assert "没有环境家族" in summarize.environment_family({"A": [{"id": "x", "family": "f"}]})
    data = {"A": [{"id": "env-x", "family": "environment", "success": False, "trajectory": []}]}
    assert "| env-x | A | 1 | 0 | 0/1 | 0/1 |" in summarize.environment_family(data)
