"""图内副本（ADR 0207）gid 判据的同源对拍：引擎侧 `_copy_source_supported` / `_COPY_GID`。

前端侧 `web/src/lib/figureElementCopy.ts` 的 `copyHostOf` / `COPY_GID_RE` 读同一份向量
（`web/src/lib/figureElementCopy.test.ts`）。任何一侧改闭集而另一侧没跟，用例当场红。
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

ENGINE = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine" / "overrides.py"


def _engine_copy_judges() -> SimpleNamespace:
    """引擎 `overrides.py` 里的两处判据**原样**取出来跑：本进程不 import matplotlib 栈（`import overrides` 会拉
    `colorbarmodel` 等 worker 解释器才有的东西），所以按语法树只摘 `_COPY_GID` 与 `_copy_source_supported` 两个定义、
    在只有 `re` 的命名空间里执行——跑的是引擎那份源码本身，不是抄一份。"""
    tree = ast.parse(ENGINE.read_text(encoding="utf-8"))
    keep: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_copy_source_supported":
            keep.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_COPY_GID" for t in node.targets
        ):
            keep.append(node)
    assert len(keep) == 2, (
        "引擎里找不到 _COPY_GID / _copy_source_supported（改名了？同步前端与这份向量）"
    )
    ns: dict = {"re": re}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(ENGINE), "exec"), ns)  # noqa: S102
    return SimpleNamespace(source_ok=ns["_copy_source_supported"], copy_gid=ns["_COPY_GID"])


overrides = _engine_copy_judges()

VECTORS = json.loads(
    (Path(__file__).parent / "golden" / "figure_copy_source_vectors.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", VECTORS["source"], ids=lambda c: repr(c["gid"]))
def test_source_closed_set_matches_the_golden_vectors(case):
    gid = case["gid"]
    host = gid.split(".", 1)[0]
    # 引擎先用 `_COPY_GID` 认出宿主（`axes_\d+` / `fig`）才会问「这个源支持吗」（`_ensure_copy`）：宿主形状不对的 gid
    # 到不了 `_copy_source_supported`，前端的 `copyHostOf` 把两步并成一个函数，所以这里也按两步合起来比
    host_ok = re.fullmatch(r"axes_\d+|fig", host) is not None
    assert (host_ok and overrides.source_ok(host, gid)) is case["supported"]


@pytest.mark.parametrize("case", VECTORS["copy_gid"], ids=lambda c: repr(c["gid"]))
def test_copy_gid_shape_matches_the_golden_vectors(case):
    assert (overrides.copy_gid.match(case["gid"]) is not None) is case["copy"]
