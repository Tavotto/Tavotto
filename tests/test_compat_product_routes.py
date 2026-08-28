"""CompatBench 产品路由的 guard（Session 6，负向反证 #2 的看护）。

「CompatBench 不得直接调用内部 probe 就代表产品 route 成功」——这两条用例
钉的是**只有真实产品面才有的副作用**：

* `route_probe_via_app` 走 `POST /api/registry/probe`：app 层会物化
  runtime cache（materialized preview + metadata）。把它改回
  `engine_probe.probe_and_register()`，cache 不会出现，这里当场红。
* `route_cli_open` 真的 spawn `python -m tavotto open --json`：payload 带
  交接协议的 `protocol` 字段（只有 CLI 输出才有）。改回进程内调用，
  这里当场红。
* `route_native_run`（Session 9）真的跑 `tavotto run`：它必须留下一份
  **一次性 handoff descriptor 的墓碑**，那是产品控制面独有的副作用。
  直接构造 `bridge.BridgeSession`（ADR 0020 的内部 spike 入口）跑通同一个
  脚本，descriptor 目录里什么都不会出现——这里当场红。

真执行脚本（与 test_script_probe 同一条纪律：本进程不 import matplotlib）。
"""

import json
import pathlib
import re
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


@needs_worker
def test_native_run_route_goes_through_the_product_control_plane(tmp_path, monkeypatch):
    """native_run 必须走**产品控制面**，不许直接调内部 spike（ADR 0021）。

    判据挑的是产品面独有的副作用：`tavotto run` 的会话产物落在
    **`<data_dir>/cache/native/<32 位十六进制 native_id>/`** —— 那个目录名
    是 CLI 按 handoff descriptor 的 ID 算出来的，也就是说它存在**就证明**
    这一趟走过了 descriptor 那一层。直接构造 `bridge.BridgeSession`
    （ADR 0020 的内部 spike 入口）跑同一个脚本，产物会落在调用方随手给的
    某个临时目录里，这条当场红。

    （descriptor 文件本身不能当判据：CLI 退出时会把墓碑一起清掉，那是
    资源治理要求的——"跑完之后目录里什么都没有"恰恰是对的。）

    顺带钉住阶段账本：报告里 `reached` 要逐段记，而不是一个笼统的"通过了"。
    """
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    from tavotto.engine import config

    figs = _project(tmp_path)
    res = compat_matrix.route_native_run(figs, "show.py", ["show"])
    assert res["ok"] is True, res
    assert res["via"] == "tavotto run"
    reached = res["detail"]["reached"]
    for stage in ("invocation_parse", "desktop_handoff", "attach", "barrier", "capture"):
        assert reached.get(stage), f"阶段 {stage} 没走到: {reached}"
    for stage in ("edit", "replay", "export"):
        assert reached.get(stage), f"阶段 {stage} 没走到: {reached}"

    cache = pathlib.Path(config.data_dir()) / "cache" / "native"
    sessions = [d for d in cache.glob("*") if d.is_dir() and re.fullmatch(r"[0-9a-f]{32}", d.name)]
    assert sessions, f"没有 native 会话产物目录（{cache}）——它绕过了产品控制面？"
    assert any((d / "show.svg").is_file() for d in sessions), (
        f"会话目录里没有这次跑出来的预览: {[sorted(p.name for p in d.iterdir()) for d in sessions]}"
    )

    # **结构性守卫**：路由实现里不许出现内部 spike 的名字。
    src = pathlib.Path(compat_matrix.__file__).read_text(encoding="utf-8")
    body = src.split("def route_native_run", 1)[1].split("\ndef _pending_native_ids", 1)[0]
    body = body.split('"""', 2)[-1]  # 函数 docstring 里恰恰会解释"为什么不用它"
    for forbidden in ("BridgeSession", "bridge_spike", "bridge.spec_for"):
        assert forbidden not in body, f"native_run 路由里出现了内部 spike 入口: {forbidden}"


@needs_worker
def test_the_native_stage_ledger_is_the_declared_closed_set():
    """`NATIVE_STAGES` 是报告里那张表的唯一出处——加一段就要出现在报告里。"""
    assert compat_matrix.NATIVE_STAGES[:3] == ("invocation_parse", "desktop_handoff", "attach")
    assert "export" in compat_matrix.NATIVE_STAGES
    assert len(set(compat_matrix.NATIVE_STAGES)) == len(compat_matrix.NATIVE_STAGES)
