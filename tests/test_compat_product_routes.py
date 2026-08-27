"""CompatBench 产品路由的 guard（Session 6，负向反证 #2 的看护）。

「CompatBench 不得直接调用内部 probe 就代表产品 route 成功」——这两条用例
钉的是**只有真实产品面才有的副作用**：

* `route_probe_via_app` 走 `POST /api/registry/probe`：app 层会物化
  runtime cache（materialized preview + metadata）。把它改回
  `engine_probe.probe_and_register()`，cache 不会出现，这里当场红。
* `route_cli_open` 真的 spawn `python -m tavotto open --json`：payload 带
  交接协议的 `protocol` 字段（只有 CLI 输出才有）。改回进程内调用，
  这里当场红。

真执行脚本（与 test_script_probe 同一条纪律：本进程不 import matplotlib）。
"""

import json
import sys
from pathlib import Path

import pytest

from tavotto.engine import pool as engine_pool, runtimeasset

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ci"))
import compat_matrix  # noqa: E402

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SHOW_ONLY = """\
import matplotlib.pyplot as plt

plt.plot([1, 2, 3], [4, 5, 6])
plt.title("route guard")
plt.show()
"""


def _project(tmp_path) -> Path:
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "show.py").write_text(SHOW_ONLY, encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(json.dumps({"scripts": {}}), encoding="utf-8")
    return figs


@needs_worker
def test_safe_probe_route_goes_through_the_product_endpoint(tmp_path):
    """app 端点的副作用（materialized cache）必须出现——内部 probe 不产它。"""
    from tavotto import app as m

    figs = _project(tmp_path)
    res = compat_matrix.route_probe_via_app(figs, "show.py")
    pj = res.pop("pj", None)
    try:
        assert res["ok"] is True, res
        assert res["via"] == "POST /api/registry/probe"
        # 唯一由 app 层产出的副作用：runtime cache 物化（交接零重跑的前提）。
        # 把路由改回 engine_probe.probe_and_register()，这里没有 cache，红。
        from tavotto.engine import figcapture

        asset_id = figcapture.runtime_asset_id("show.py", "show")
        assert runtimeasset.load_metadata(figs, asset_id) is not None, (
            "safe_probe 路由没有产生 materialized cache——它绕过了产品端点？"
        )
    finally:
        if pj:
            m.close_project(pj)


@needs_worker
def test_cli_open_route_spawns_the_real_cli(tmp_path):
    """cli_open 必须是真子进程 + 真 JSON 契约（protocol 字段只有 CLI 有）。"""
    figs = _project(tmp_path)
    res = compat_matrix.route_cli_open(figs, "show.py")
    assert res["ok"] is True, res
    payload = res["payload"]
    # 交接协议版本只在 handoff CLI 的 JSON 输出里：进程内调 probe 拿不到它
    assert isinstance(payload.get("protocol"), int)
    assert payload["stem"] == "show"
    assert payload["probe"]["performed"] is True
    # 真 argv：spawn 的是 `-m tavotto open`，不是进程内函数
    assert res["argv"][1:4] == ["-m", "tavotto", "open"]
