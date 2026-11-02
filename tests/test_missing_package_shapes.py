"""「这个缺包怎么认」的失败形态向量表（`tests/golden/missing_package_shapes.json`）。

缺包的认法已经修过五轮，每一轮都冒出一个新边界（#1418 / #1426 项目内同名包遮蔽已装包、#1494 惰性 import、
#1564 私有依赖文件夹的 Python 版本不对、#1582 脚本把目录存进变量再加进 sys.path、#1507 读不了的文件夹）。
这里把每一种形态收成一条向量：真实最小夹具 -> 现有的公共判定入口 -> 期望结论。新的缺包工单**先在表里加一条**
再改产品代码（见 `docs/rules/backend/dependency-repair-and-packages.md`「缺包形态向量表」）。

主语：**判定入口给出的结论**（包名 / 是否可装 / 失败码 / 本地模块缺失），不是入口的内部实现；入口不变、结论变了才红。
元测试钉住表本身：id 唯一、都有来源、形态落在 `SHAPES` 闭集里、入口落在 `DRIVERS` 里（表里写了却没人驱动 = 空转）。
"""

from __future__ import annotations

import json
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

from tavotto.engine import (
    bridgeboot,
    depplan,
    deprepair,
    importscan,
    importshadow,
    pool as engine_pool,
)

GOLDEN = json.loads(
    (Path(__file__).resolve().parent / "golden" / "missing_package_shapes.json").read_text("utf-8")
)
VECTORS = GOLDEN["vectors"]

#: 缺包失败形态的闭集；新增一种形态 = 先在这里加一个名字，再加向量
SHAPES = frozenset(
    {
        "plain_missing",
        "project_package_shadow",
        "runtime_lazy_import",
        "foreign_abi_private_folder",
        "sys_path_dir_variable",
        "unreadable_import_folder",
        "install_failure_kind",
    }
)

HERE = tuple(sys.version_info[:2])
FOREIGN_ABI = f"{HERE[0]}{HERE[1] + 1 if HERE[1] < 90 else HERE[1] - 1}"


# --------------------------------------------------------------------------- 夹具
def _subst(value, root: Path, pkg: str):
    if isinstance(value, str):
        return (
            value.replace("{root}", root.as_posix())
            .replace("{pkg}", pkg)
            .replace("{foreign_abi}", FOREIGN_ABI)
        )
    if isinstance(value, list):
        return [_subst(v, root, pkg) for v in value]
    if isinstance(value, dict):
        return {_subst(k, root, pkg): _subst(v, root, pkg) for k, v in value.items()}
    return value


def _materialize(vec: dict, root: Path, pkg: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in _subst(vec.get("files", {}), root, pkg).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")


def _facts() -> depplan.TargetFacts:
    return depplan.TargetFacts(
        python="/fake/python",
        marker_env={
            "implementation_name": "cpython",
            "implementation_version": "3.12.4",
            "os_name": "posix",
            "platform_machine": "arm64",
            "platform_release": "",
            "platform_system": "Darwin",
            "platform_version": "",
            "python_full_version": "3.12.4",
            "platform_python_implementation": "CPython",
            "python_version": "3.12",
            "sys_platform": "darwin",
        },
        stdlib=importscan.HOST_STDLIB,
        installed={"matplotlib": "3.10.0", "numpy": "2.1.0", "pandas": "2.2.0"},
        prefix="/fake",
    )


# --------------------------------------------------------------------------- 入口驱动：返回「结论」的规范化字典
def _drive_shadow(vec, root, pkg, mp):
    inp = vec["input"]
    text = _subst(inp["text"], root, pkg)
    fact = importshadow.detect(
        text,
        [root / r for r in inp["roots"]],
        late_roots=[root / r for r in inp["late_roots"]],
    )
    if not fact:
        return {"shadow": False}
    return {
        "shadow": True,
        "search_order": fact["search_order"],
        "project_path": fact["project_path"],
        "do_not_install": "Do not install it" in fact["note"],
    }


def _drive_scan(vec, root, pkg, mp):
    scan = importscan.scan(root, vec["input"]["script"])
    named = set(
        vec["expect"]["buckets"]
    )  # 只比向量点名的模块（扫描结果里还有标准库 / 第三方的其它名字）
    return {
        "buckets": {c.module: c.bucket for c in scan.classes if c.module in named},
        "sys_path_literal_root": any(r["tag"] == "sys_path_literal" for r in scan.search_roots),
    }


def _drive_plan(vec, root, pkg, mp):
    depplan.reset_cache()
    inp = vec["input"]
    plan = depplan.plan(
        root,
        inp["script"],
        facts=_facts(),
        target_kind=depplan.TARGET_MANAGED,
        dists=None,
        extra_imports=tuple(inp.get("extra_imports", ())),
    )
    return {
        "status": plan.status,
        "requirements": [r.split(">")[0].split("=")[0] for r in plan.requirements],
        "local_module_missing": {
            o["import_name"]: o["local_module_missing"]["search_dirs"]
            for o in plan.origins
            if o.get("local_module_missing")
        },
        "prefilled": [o["import_name"] for o in plan.origins if o.get("suggested_distribution")],
    }


def _drive_worker_error(vec, root, pkg, mp):
    inp = vec["input"]
    err = engine_pool._worker_error(
        inp["message"], inp["code"], inp["traceback"], inp.get("extra"), project=str(root)
    )
    return {"code": err.code, "module": getattr(err, "module", None) or None}


def _drive_foreign_abi(vec, root, pkg, mp):
    inp = _subst(vec["input"], root, pkg)
    for rel in inp["sys_path"]:
        mp.syspath_prepend(str(root / rel))
    err = ImportError("cannot import name '_native'", path=str(root / inp["error_path"]))
    fact, _private = bridgeboot.foreign_abi_facts(
        err, [], [], [str(root / r) for r in inp["project_roots"]]
    )
    return {"folder": fact.get("folder"), "foreign": bool(fact)}


def _drive_unreadable_fact(vec, root, pkg, mp):
    inp = vec["input"]
    baseline = list(sys.path)
    for rel in inp["sys_path_added"]:
        (root / rel).mkdir(parents=True, exist_ok=True)
        mp.syspath_prepend(str(root / rel))
    roots = [root / r for r in inp["project_roots"]]
    for r in roots:
        r.mkdir(parents=True, exist_ok=True)
    blocked = bridgeboot._norm(str(root / inp["blocked_file"]))
    failing = str(root / inp.get("error_file", inp["blocked_file"]))
    err = PermissionError(13, "Permission denied", failing)
    fact = bridgeboot.unreadable_folder_fact(err, baseline, [str(r) for r in roots], (), {blocked})
    return {"fact": fact}


def _drive_pip(vec, root, pkg, mp):
    return {"code": deprepair.classify_pip_failure(vec["input"]["text"])}


DRIVERS = {
    "importshadow.detect": _drive_shadow,
    "importscan.scan": _drive_scan,
    "depplan.plan": _drive_plan,
    "pool._worker_error": _drive_worker_error,
    "bridgeboot.foreign_abi_facts": _drive_foreign_abi,
    "bridgeboot.unreadable_folder_fact": _drive_unreadable_fact,
    "deprepair.classify_pip_failure": _drive_pip,
}


@pytest.fixture(autouse=True)
def _clean_plan_cache():
    depplan.reset_cache()
    yield
    depplan.reset_cache()


@pytest.mark.parametrize("vec", VECTORS, ids=[v["id"] for v in VECTORS])
def test_shape_vector_matches_the_current_decision(vec, tmp_path, monkeypatch):
    root = tmp_path / "proj"
    pkg = "shapepkg_" + uuid.uuid4().hex[:10]
    _materialize(vec, root, pkg)
    got = DRIVERS[vec["entry"]](vec, root, pkg, monkeypatch)
    assert got == _subst(vec["expect"], root, pkg), f"{vec['id']}（{vec['source']}）"


# --------------------------------------------------------------------------- 元测试
def test_table_is_a_closed_set_with_unique_ids_and_sources():
    ids = [v["id"] for v in VECTORS]
    assert VECTORS, "表是空的：判据空转"
    assert len(ids) == len(set(ids)), "id 重复"
    for v in VECTORS:
        assert v["shape"] in SHAPES, f"{v['id']}: 形态 {v['shape']!r} 不在 SHAPES 闭集里"
        assert v["entry"] in DRIVERS, f"{v['id']}: 入口 {v['entry']!r} 没有驱动"
        src = v.get("source")
        assert isinstance(src, dict) and "pr" in src and src.get("ticket"), f"{v['id']}: 缺来源"
        assert v.get("summary") and "expect" in v and "input" in v


def test_every_shape_and_every_historical_fix_has_a_vector():
    assert {v["shape"] for v in VECTORS} == SHAPES, "有形态没有向量（或向量用了闭集外的形态）"
    assert {1418, 1426, 1494, 1507, 1564, 1582} <= {v["source"]["pr"] for v in VECTORS}
    assert {v["entry"] for v in VECTORS} == set(DRIVERS), "有入口没有向量：驱动白写"
