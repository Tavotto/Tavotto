"""Execution Fidelity（V2 PR3a，safe 侧）：由守卫造成的偏差，分「确定 / 疑似 / 未观测」三档如实说。

审计 D 的实测：`savefig(p)` 在 safe 里不落盘，所以直接运行里必然存在的 `p` 在这里不存在——
之后脚本对它做 `os.replace` / `getsize` / `Path.stat` / `os.open`，以前得到 `script_error(FileNotFoundError)`，
还挂出 `missing_input` 弹窗让用户去「指认」脚本自己的输出。现在：

* 确定偏差：这些异常到达终局，归到 `safe_file_operation_blocked`（`fidelity.level=deviated`），不弹 `missing_input`；
* 疑似偏差：`exists()` 校验、被吞掉的失败、`listdir` / `glob` 计数……成功照旧，回执标 `fidelity.level=suspect`；
* 未观测：什么都没看到（`none_observed`），盲区（子进程 / 原生 I/O）如实列在 `unobserved`。

全部用真 safe worker 子进程与真文件；脚本形状取自审计 D 的 20 个探针。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tavotto.engine import figcapture, pool as engine_pool, receipt, workdir

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

CODE = "safe_file_operation_blocked"
HDR = (
    "import os, sys, glob, shutil\n"
    "import matplotlib\n"
    "matplotlib.use('Agg')\n"
    "import matplotlib.pyplot as plt\n"
    "fig, ax = plt.subplots(); ax.plot([0, 1])\n"
)
MODES = ("sandbox", "project")


@pytest.fixture
def figs(tmp_path):
    root = tmp_path / "figs"
    root.mkdir()
    (root / "s.png").write_bytes(b"stale")
    yield root
    engine_pool.shutdown_all(str(root), wait=True)
    workdir.set_mode(root, workdir.MODE_SANDBOX)


def _run(figs: Path, source: str, mode: str):
    (figs / "script.py").write_text(source, encoding="utf-8")
    workdir.set_mode(figs, workdir.MODE_PROJECT if mode == "project" else workdir.MODE_SANDBOX)
    return engine_pool.build("script.py", str(figs), "__main__")


def _deviated(figs, source, mode) -> engine_pool.WorkerError:
    with pytest.raises(engine_pool.WorkerError) as err:
        _run(figs, source, mode)
    return err.value


def _succeeded(figs, source, mode):
    worker, resp = _run(figs, source, mode)
    rcpt = receipt.from_worker(worker, resp, control_plane="python_pool", grant=None)
    return resp, rcpt


def _assert_attributed_to_the_guard(e: engine_pool.WorkerError) -> None:
    assert e.code == CODE, (e.code, str(e))
    # 以前这里会挂 `missing_input` 弹窗，让用户去指认脚本自己写的输出
    assert getattr(e, "missing_input", None) is None
    assert e.extra["required_execution"] == "preserve_file_semantics"
    fid = e.extra["fidelity"]
    assert fid["level"] == "deviated" and fid["blocked"]
    # 不含路径 / 文件名
    blob = json.dumps(e.extra) + str(e)
    assert "o.png" not in blob and "final.png" not in blob and "t.png" not in blob


# ============================================================ 确定偏差：不再误归因
DEVIATED = {
    "tmp_replace": (
        "fig.savefig('t.png.tmp', format='png')\nos.replace('t.png.tmp', 'final.png')\n",
        ("sandbox",),
    ),
    "getsize": ("fig.savefig('o.png')\nn = os.path.getsize('o.png')\n", MODES),
    "path_stat": (
        "import pathlib\nfig.savefig('o.png')\nn = pathlib.Path('o.png').stat().st_size\n",
        MODES,
    ),
    "os_open": (
        "fig.savefig('o.png')\nfd = os.open('o.png', os.O_RDONLY)\nos.close(fd)\n",
        MODES,
    ),
    "exists_then_exit": (
        "fig.savefig('o.png')\nif not os.path.exists('o.png'):\n    sys.exit(3)\n",
        MODES,
    ),
    "getsize_via_chain": (
        "fig.savefig('o.png')\ntry:\n    os.path.getsize('o.png')\n"
        "except OSError as exc:\n    raise RuntimeError('输出没生成') from exc\n",
        MODES,
    ),
    # 不同保存格式（含显式 format= 的无后缀名字面写出）
    "svg": ("fig.savefig('o.svg')\nos.path.getsize('o.svg')\n", MODES),
    "pdf": ("fig.savefig('o.pdf')\nos.path.getsize('o.pdf')\n", MODES),
    "format_kw": ("fig.savefig('o', format='png')\nos.path.getsize('o')\n", MODES),
    # 关闭 Figure 之后再回读
    "closed_figure": ("fig.savefig('o.png')\nplt.close(fig)\nos.path.getsize('o.png')\n", MODES),
    # 动态保存路径（循环里拼名字）
    "dynamic_paths": (
        "for i in range(3):\n    fig.savefig(f'out_{i}.png')\nos.path.getsize('out_2.png')\n",
        MODES,
    ),
    # 多 Figure：第二张的输出被回读
    "multi_figure": (
        "fig2, ax2 = plt.subplots(); ax2.plot([1, 0])\n"
        "fig.savefig('a.png'); fig2.savefig('b.png')\nos.path.getsize('b.png')\n",
        MODES,
    ),
}


@needs_worker
@pytest.mark.parametrize(
    ("name", "mode"),
    [(n, m) for n, (_src, modes) in DEVIATED.items() for m in modes],
)
def test_a_failure_derived_from_an_intercepted_savefig_is_attributed_to_the_guard(figs, name, mode):
    e = _deviated(figs, HDR + DEVIATED[name][0], mode)
    _assert_attributed_to_the_guard(e)


@needs_worker
def test_the_message_no_longer_claims_the_desktop_has_no_way_and_stays_honest():
    err = _blocked_error_for_text()
    text = str(err)
    assert "尚未接入" not in text and "桌面界面里的一键" not in text
    assert "tavotto run" in text
    # 自动回退没有接通：这个值保持 false，不承诺
    assert err.extra["adapter_available"] is False


def _blocked_error_for_text():
    from tavotto.engine import worker

    return worker._blocked_operations_error([{"op": "savefig", "kind": "savefig", "count": 1}])


@needs_worker
def test_an_unrelated_missing_file_is_still_a_script_error_or_missing_input(figs):
    """正对照：与被拦截目标无关的 FileNotFoundError 不被归到守卫。"""
    e = _deviated(figs, HDR + "fig.savefig('o.png')\nopen('absent_data.csv').read()\n", "sandbox")
    assert e.code != CODE


# ============================================================ 疑似偏差：成功，但回执标注
SUSPECT = {
    "exists_branch": (
        "fig.savefig('o.png')\nif not os.path.exists('o.png'):\n    ax.set_title('NOT SAVED')\n",
        MODES,
        "stat",
    ),
    "listdir_count": (
        "fig.savefig('o.png')\nax.set_title(str(len(os.listdir('.'))))\n",
        MODES,
        "listing",
    ),
    "glob_count": (
        "fig.savefig('o.png')\nax.set_title(str(len(glob.glob('o*.png'))))\n",
        MODES,
        "listing",
    ),
    "getmtime_swallowed": (
        "fig.savefig('o.png')\ntry:\n    t = os.path.getmtime('o.png')\n"
        "except OSError:\n    t = 0\nax.set_title(str(t))\n",
        MODES,
        "probe_failed",
    ),
    "replace_swallowed": (
        "fig.savefig('t.png')\ntry:\n    os.replace('t.png', 'final.png')\n"
        "except OSError:\n    pass\n",
        ("sandbox",),
        "move_failed",
    ),
    # project 模式：守卫静默跳过了源就是 savefig 目标的改名，`final.png` 永远不会出现
    "replace_skipped_in_project": (
        "fig.savefig('t.png')\nos.replace('t.png', 'final.png')\n",
        ("project",),
        "rename_src",
    ),
}


@needs_worker
@pytest.mark.parametrize(
    ("name", "mode"),
    [(n, m) for n, (_src, modes, _via) in SUSPECT.items() for m in modes],
)
def test_weak_signals_mark_the_result_suspect_without_blocking_it(figs, name, mode):
    source, _modes, via = SUSPECT[name]
    resp, rcpt = _succeeded(figs, HDR + source, mode)
    assert resp["stems"]  # 普通绘图脚本不被阻断
    fid = resp["runtime"]["fidelity"]
    assert fid["level"] == "suspect" and fid["blocked"] == []
    assert via in {s["via"] for s in fid["signals"]}, fid
    # 进回执（公开事实与完整投影），经过滤后仍是闭集
    assert rcpt.fidelity["level"] == "suspect"
    assert rcpt.public_facts()["fidelity"]["level"] == "suspect"
    assert rcpt.to_payload()["fidelity"]["signals"]
    # 疑似偏差不是另一次执行：公开身份与没有 fidelity 时一致
    plain = receipt.ExecutionReceipt(**{**_fields(rcpt), "runtime": _without_fidelity(rcpt)})
    assert plain.public_identity() == rcpt.public_identity()


def _fields(rcpt):
    import dataclasses

    return {f.name: getattr(rcpt, f.name) for f in dataclasses.fields(rcpt)}


def _without_fidelity(rcpt):
    return {k: v for k, v in rcpt.runtime.items() if k != "fidelity"}


# ============================================================ 未观测：没看到，也不宣称干净
NONE_OBSERVED = {
    "bytesio": "import io\nb = io.BytesIO(); fig.savefig(b); ax.set_title(str(len(b.getvalue())))\n",
    "close_after_save": "fig.savefig('o.png'); plt.close(fig)\n",
    "outside_project_replace": (
        "import tempfile\nd = tempfile.mkdtemp()\nopen(d + '/a', 'w').write('2')\n"
        "os.replace(d + '/a', d + '/b')\nax.set_title(open(d + '/b').read())\n"
    ),
    "two_figures": (
        "fig2, ax2 = plt.subplots(); ax2.plot([1, 0])\nfig2.savefig('b.png'); fig.savefig('a.png')\n"
    ),
    # 盲区：子进程里发生的事账本看不到——不是"没偏差"，是"没观测"
    "subprocess_read": (
        "import subprocess\nfig.savefig('o.png')\n"
        "subprocess.run([sys.executable, '-c', 'print(1)'], capture_output=True)\n"
    ),
}


@needs_worker
@pytest.mark.parametrize("name", sorted(NONE_OBSERVED))
def test_nothing_observed_is_reported_as_such_with_the_blind_spots_named(figs, name):
    resp, rcpt = _succeeded(figs, HDR + NONE_OBSERVED[name], "sandbox")
    fid = rcpt.fidelity
    assert fid["level"] == "none_observed" and fid["signals"] == [] and fid["blocked"] == []
    assert "subprocess" in fid["unobserved"]


@needs_worker
def test_a_script_without_any_figure_still_reports_its_fidelity(figs):
    resp, rcpt = _succeeded(figs, "print('hi')\n", "sandbox")
    assert resp["stems"] == {} or not resp["stems"]
    assert rcpt.fidelity["level"] == "none_observed"


@needs_worker
def test_a_failed_build_leaves_a_closed_set_record_without_paths_or_argv(figs):
    e = _deviated(figs, HDR + "fig.savefig('o.png')\nos.path.getsize('o.png')\n", "sandbox")
    fid = e.extra["fidelity"]
    assert set(fid) == {"level", "blocked", "intercepted", "signals", "unobserved"}
    assert {o["kind"] for o in fid["intercepted"]} <= {
        "rename",
        "delete",
        "delete_dir",
        "write",
        "savefig",
    }
    assert all(isinstance(o["count"], int) for o in fid["intercepted"])
    assert "o.png" not in json.dumps(fid)


# ============================================================ 账本单元：钩子装卸、闭集、不泄路径
@pytest.fixture
def ledger(tmp_path):
    led = figcapture.BlockedFileOps()
    yield led
    led.uninstall()


def test_hooks_are_installed_and_fully_restored(tmp_path, ledger):
    import os
    import pathlib

    before = (os.open, os.listdir, os.scandir, os.stat, pathlib.Path.exists, os.path.exists)
    ledger.install()
    assert os.listdir is not before[1] and os.open is not before[0]
    ledger.uninstall()
    after = (os.open, os.listdir, os.scandir, os.stat, pathlib.Path.exists, os.path.exists)
    assert before == after


def test_explains_only_fires_for_the_intercepted_targets(tmp_path, ledger):
    target = tmp_path / "o.png"
    ledger.blocked("savefig", ledger.KIND_SAVEFIG, str(target))
    other = FileNotFoundError(2, "No such file", str(tmp_path / "data.csv"))
    assert ledger.explains(other) is False and ledger.material() == []
    mine = FileNotFoundError(2, "No such file", str(target))
    wrapped = RuntimeError("boom")
    wrapped.__cause__ = mine
    assert ledger.explains(wrapped) is True
    assert [(o["op"], o["kind"]) for o in ledger.material()] == [("savefig", "savefig")]
    assert ledger.summary()["level"] == "deviated"


def test_summary_is_counts_and_enums_only(tmp_path, ledger):
    target = tmp_path / "secret_name.png"
    ledger.blocked("savefig", ledger.KIND_SAVEFIG, str(target))
    ledger.install()
    try:
        import os

        os.path.exists(str(target))  # 探路：弱信号，不是 stat 失败
        os.listdir(tmp_path)
    finally:
        ledger.uninstall()
    summary = ledger.summary()
    assert summary["level"] == "suspect"
    assert {(s["kind"], s["via"]) for s in summary["signals"]} == {
        ("savefig", "stat"),
        ("savefig", "listing"),
    }
    assert "secret_name" not in json.dumps(summary) and str(tmp_path) not in json.dumps(summary)


def test_a_stopped_ledger_records_no_signals(tmp_path, ledger):
    import os

    ledger.blocked("savefig", ledger.KIND_SAVEFIG, str(tmp_path / "o.png"))
    ledger.install()
    ledger.stop()
    try:
        os.listdir(tmp_path)
        os.path.exists(str(tmp_path / "o.png"))
    finally:
        ledger.uninstall()
    assert ledger.suspects() == []


# ============================================================ 回执：自报是不可信输入
def test_the_receipt_keeps_only_closed_set_fidelity_fields():
    rcpt = receipt.ExecutionReceipt(
        profile="safe",
        control_plane="python_pool",
        python_source="",
        generation=1,
        source_revision="",
        spec_stable={},
        launch_context={},
        interpreter="",
        project_root="",
        runtime={
            "fidelity": {
                "level": "suspect",
                "blocked": [{"op": "os.replace", "kind": "rename", "count": 1, "path": "/x/y"}],
                "intercepted": [{"op": "savefig", "kind": "/etc/passwd", "count": 1}],
                "signals": [
                    {"kind": "savefig", "via": "stat", "count": 2},
                    {"kind": "savefig", "via": "/home/u/secret", "count": 1},
                ],
                "unobserved": ["subprocess", 7],
                "extra": "/home/u/secret",
            }
        },
    )
    fid = rcpt.fidelity
    assert fid["blocked"] == [{"op": "os.replace", "kind": "rename", "count": 1}]
    assert fid["intercepted"] == []  # kind 不在闭集
    assert fid["signals"] == [{"kind": "savefig", "via": "stat", "count": 2}]
    assert fid["unobserved"] == ["subprocess"]
    assert "secret" not in json.dumps(rcpt.to_payload()) and "/x/y" not in json.dumps(
        rcpt.public_facts()
    )
    # 非法 level / 没报：None，不是半截
    for bad in ({"level": "exact"}, "suspect", None):
        r = receipt.ExecutionReceipt(**{**_fields(rcpt), "runtime": {"fidelity": bad}})
        assert r.fidelity is None


# ============================================================ Codex #1024 评审三条
def test_the_receipt_drops_operation_names_outside_the_closed_set():
    """P1：`op` 不能是开放字符串，否则不可信 worker 能把机器路径塞进公开投影。"""
    rcpt = receipt.ExecutionReceipt(
        profile="safe",
        control_plane="python_pool",
        python_source="",
        generation=1,
        source_revision="",
        spec_stable={},
        launch_context={},
        interpreter="",
        project_root="",
        runtime={
            "fidelity": {
                "level": "deviated",
                "blocked": [
                    {"op": "/Users/u/secret.png", "kind": "savefig", "count": 1},
                    {"op": "os.replace", "kind": "rename", "count": 1},
                ],
                "intercepted": [{"op": "C:\\data\\x", "kind": "rename", "count": 1}],
            }
        },
    )
    fid = rcpt.fidelity
    assert fid["blocked"] == [{"op": "os.replace", "kind": "rename", "count": 1}]
    assert fid["intercepted"] == []
    assert "secret" not in json.dumps(rcpt.public_facts())
    assert "savefig" in receipt.FIDELITY_OPS and "os.replace" in receipt.FIDELITY_OPS


def test_every_op_the_worker_records_is_in_the_closed_set():
    import re

    src = (Path(__file__).parent.parent / "src/tavotto/engine/worker.py").read_text("utf-8")
    recorded = set(re.findall(r'ledger\.blocked\(\s*"([^"]+)"', src)) | set(
        re.findall(r'_guard_delete\(\s*"[^"]+",\s*[\w.]+,\s*"([^"]+)"', src)
    )
    recorded.add("savefig")
    assert recorded and recorded <= set(receipt.FIDELITY_OPS), recorded - set(receipt.FIDELITY_OPS)


def test_one_path_probe_is_counted_once(tmp_path, ledger):
    """P2：POSIX 上 `os.path.exists` 先被 `watched_check` 观察、内部又走 `os.stat`——一次探路只算一次。"""
    import os
    import pathlib

    target = tmp_path / "o.png"
    ledger.blocked("savefig", ledger.KIND_SAVEFIG, str(target))
    ledger.install()
    try:
        os.path.exists(str(target))
        pathlib.Path(target).exists()
        os.path.isfile(str(target))
    finally:
        ledger.uninstall()
    counts = {(s["kind"], s["via"]): s["count"] for s in ledger.suspects()}
    assert counts == {("savefig", "stat"): 3}, counts


EXIT_AFTER_UNRELATED = (
    "fig.savefig('o.png')\nprint(os.path.exists('o.png'))\n"
    "import argparse\np = argparse.ArgumentParser(); p.add_argument('--need', required=True)\n"
    "p.parse_args()\n"
)


@needs_worker
def test_an_unrelated_nonzero_exit_keeps_its_own_classification(figs):
    """P2：查过 savefig 目标、之后因为别的原因退出（缺参数 / 之后又做了别的事）不是守卫造成的。"""
    e = _deviated(figs, HDR + EXIT_AFTER_UNRELATED, "sandbox")
    assert e.code == "script_needs_arguments", (e.code, str(e))
    e = _deviated(
        figs,
        HDR + "fig.savefig('o.png')\nos.path.exists('o.png')\nopen(__file__).read()\nsys.exit(3)\n",
        "sandbox",
    )
    assert e.code != CODE, e.code


# ============================================================ 第三轮：唯一的 fidelity 投影 + 敌对载荷
def _rcpt_with(fidelity):
    return receipt.ExecutionReceipt(
        profile="safe",
        control_plane="python_pool",
        python_source="",
        generation=1,
        source_revision="",
        spec_stable={},
        launch_context={},
        interpreter="",
        project_root="",
        runtime={"fidelity": fidelity},
    )


PATHY = "/Users/alice/private.csv"
GOOD = {
    "level": "suspect",
    "blocked": [{"op": "os.replace", "kind": "rename", "count": 1}],
    "intercepted": [{"op": "savefig", "kind": "savefig", "count": 2}],
    "signals": [{"kind": "savefig", "via": "stat", "count": 3}],
    "unobserved": ["subprocess"],
}


def _hostile_variants():
    """对每个字段塞：路径、非列表、超长、嵌套、错类型。"""
    row_fields = {
        "blocked": ("op", "kind"),
        "intercepted": ("op", "kind"),
        "signals": ("kind", "via"),
    }
    out = []
    for key in GOOD:
        for bad in (
            PATHY,
            {},
            [],
            7,
            None,
            True,
            ["x"] * 500,
            [[PATHY]],
            {"a": PATHY},
            "x" * 100000,
        ):
            out.append({**GOOD, key: bad})
    for key, fields in row_fields.items():
        for field in (*fields, "count"):
            for bad in (PATHY, {"p": PATHY}, [PATHY], 10**30, -1, True, None, 1.5, "9"):
                row = {**GOOD[key][0], field: bad}
                out.append({**GOOD, key: [row]})
        out.append({**GOOD, key: [{**GOOD[key][0], "path": PATHY}]})  # 未知键
        out.append({**GOOD, key: [PATHY] * 100})  # 行不是对象
        out.append({**GOOD, key: [dict(GOOD[key][0]) for _ in range(5000)]})  # 过长
    out.append({**GOOD, "unobserved": [PATHY, "subprocess", {"p": 1}]})
    out.append({**GOOD, "extra": PATHY, PATHY: PATHY})
    return out


def _all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _all_strings(k)
            yield from _all_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _all_strings(v)


def _closed_strings():
    b = figcapture.BlockedFileOps
    return {
        *receipt.FIDELITY_LEVELS,
        *receipt.FIDELITY_OPS,
        *receipt.FIDELITY_KINDS,
        *receipt.FIDELITY_VIAS,
        *figcapture.INPUT_OBSERVER_UNOBSERVED,
        *b.LEVELS,
        # 投影本身的键名
        "level", "blocked", "intercepted", "signals", "unobserved", "op", "kind", "via", "count",
    }  # fmt: skip


@pytest.mark.parametrize("variant", _hostile_variants())
def test_hostile_worker_payloads_never_reach_the_public_projections(variant):
    rcpt = _rcpt_with(variant)
    closed = _closed_strings()
    # 畸形输入不许抛：public_facts / to_payload 在准备接口里被直接调用
    for projection in (
        rcpt.fidelity,
        rcpt.public_facts()["fidelity"],
        rcpt.to_payload()["fidelity"],
    ):
        if projection is None:
            continue
        leaked = {s for s in _all_strings(projection) if s not in closed}
        assert not leaked, leaked
        for key in ("blocked", "intercepted", "signals", "unobserved"):
            assert isinstance(projection[key], list) and len(projection[key]) <= 32
            for row in projection[key]:
                if isinstance(row, dict):
                    assert isinstance(row["count"], int) and 0 <= row["count"] <= 9999
    payload = rcpt.to_payload()
    assert PATHY not in json.dumps(payload) and "x" * 1000 not in json.dumps(payload)


def test_a_malformed_top_level_fidelity_is_absent_not_an_error():
    for bad in ([], "suspect", 3, None, {"level": ["suspect"]}, {"level": {}}):
        r = _rcpt_with(bad)
        assert r.fidelity is None and r.public_facts()["fidelity"] is None
        assert r.to_payload()["fidelity"] is None


def test_the_one_projection_function_is_the_only_public_path():
    assert receipt.project_fidelity(GOOD) == _rcpt_with(GOOD).fidelity
    assert receipt.project_fidelity("nope") is None


# ---- 退出归因：纯函数 ----
PRE = "import os, sys, pathlib\nfrom pathlib import Path\no = 'o.png'\nfig.savefig(o)\n"
#: (源码，退出是否由"落空的那次探测"决定)。`# EXIT` 标出退出所在的行。
EXIT_CASES = {
    "return_after_probe": (PRE + "os.path.exists(o)\n", False),
    "unrelated_exit_after_probe": (PRE + "os.path.exists(o)\nsys.exit(7)  # EXIT\n", False),
    "if_not_exists_exit": (PRE + "if not os.path.exists(o):\n    sys.exit(3)  # EXIT\n", True),
    "const_path_matches_variable": (
        PRE + "if not os.path.exists('o.png'):\n    sys.exit(3)  # EXIT\n",
        True,
    ),
    "path_object_probe": (PRE + "if not Path(o).exists():\n    sys.exit(3)  # EXIT\n", True),
    "probe_in_function_branch": (
        PRE + "def main():\n    if not os.path.isfile(o):\n        print('x')\n"
        "        sys.exit(2)  # EXIT\nmain()\n",
        True,
    ),
    "or_chain_after_positive_probe": (PRE + "os.path.exists(o) or sys.exit(4)  # EXIT\n", True),
    "and_chain_after_positive_probe": (PRE + "os.path.exists(o) and sys.exit(4)  # EXIT\n", False),
    "necessary_conjunct": (
        PRE + "flag = True\nif not os.path.exists(o) and flag:\n    sys.exit(3)  # EXIT\n",
        True,
    ),
    # Codex #1024 r4267638142：条件里有探测，但探测不是进入分支的必要条件
    "or_with_independent_disjunct": (
        PRE
        + "must_abort = True\nif not os.path.exists(o) or must_abort:\n    sys.exit(7)  # EXIT\n",
        False,
    ),
    "conjunction_without_a_failed_probe": (
        PRE + "a = b = True\nif a and b:\n    sys.exit(3)  # EXIT\n",
        False,
    ),
    "conjunction_with_a_positive_probe": (
        PRE + "a = True\nif a and os.path.exists(o):\n    sys.exit(3)  # EXIT\n",
        False,
    ),
    # Codex #1024 r4267844405：savefig 之后在真实文件语义下仍为 False 的谓词，退出不是守卫造成的
    "isdir_stays_false_after_savefig": (
        PRE + "if not os.path.isdir(o):\n    sys.exit(3)  # EXIT\n",
        False,
    ),
    "islink_stays_false_after_savefig": (
        PRE + "if not os.path.islink(o):\n    sys.exit(3)  # EXIT\n",
        False,
    ),
    "path_is_dir_stays_false_after_savefig": (
        PRE + "if not Path(o).is_dir():\n    sys.exit(3)  # EXIT\n",
        False,
    ),
    "path_is_symlink_stays_false_after_savefig": (
        PRE + "if not Path(o).is_symlink():\n    sys.exit(3)  # EXIT\n",
        False,
    ),
    "ismount_stays_false_after_savefig": (
        PRE + "if not os.path.ismount(o):\n    sys.exit(3)  # EXIT\n",
        False,
    ),
    # Codex #1024 r4267917227：探路调用必须解析到 os.path / os.stat / pathlib.Path 的方法
    "custom_class_exists": (
        "import sys\nclass Backend:\n    @staticmethod\n    def exists(p):\n        return False\n"
        "o = 'o.png'\nfig.savefig(o)\nif not Backend.exists(o):\n    sys.exit(7)  # EXIT\n",
        False,
    ),
    "user_defined_exists_function": (
        "import sys\ndef exists(p):\n    return False\n"
        "o = 'o.png'\nfig.savefig(o)\nif not exists(o):\n    sys.exit(7)  # EXIT\n",
        False,
    ),
    "user_function_shadows_the_imported_name": (
        "import sys\nfrom os.path import exists\ndef exists(p):\n    return False\n"
        "o = 'o.png'\nfig.savefig(o)\nif not exists(o):\n    sys.exit(7)  # EXIT\n",
        False,
    ),
    "method_on_an_unknown_object": (
        PRE + "class B:\n    def exists(self):\n        return False\n"
        "b = B()\nif not b.exists():\n    sys.exit(7)  # EXIT\n",
        False,
    ),
    "from_import_of_os_path_exists": (
        "import sys\nfrom os.path import exists\no = 'o.png'\nfig.savefig(o)\n"
        "if not exists(o):\n    sys.exit(7)  # EXIT\n",
        True,
    ),
    "os_path_alias": (
        "import sys\nimport os.path as osp\no = 'o.png'\nfig.savefig(o)\n"
        "if not osp.exists(o):\n    sys.exit(7)  # EXIT\n",
        True,
    ),
    "from_os_import_path": (
        "import sys\nfrom os import path\no = 'o.png'\nfig.savefig(o)\n"
        "if not path.isfile(o):\n    sys.exit(7)  # EXIT\n",
        True,
    ),
    "os_stat_probe": (
        PRE + "if not os.stat(o):\n    sys.exit(7)  # EXIT\n",
        True,
    ),
    "path_object_variable": (
        "import sys\nimport pathlib\nout = pathlib.Path('o.png')\nfig.savefig(out)\n"
        "if not out.exists():\n    sys.exit(7)  # EXIT\n",
        True,
    ),
    "probe_of_a_different_file": (
        PRE + "if not os.path.exists('data.csv'):\n    sys.exit(3)  # EXIT\n",
        False,
    ),
    "probe_of_a_different_variable": (
        PRE + "other = 'x.csv'\nif not os.path.exists(other):\n    sys.exit(3)  # EXIT\n",
        False,
    ),
    "else_branch_of_positive_probe": (
        PRE + "if os.path.exists(o):\n    pass\nelse:\n    sys.exit(3)  # EXIT\n",
        True,
    ),
    "else_branch_of_negated_probe": (
        PRE + "if not os.path.exists(o):\n    pass\nelse:\n    sys.exit(3)  # EXIT\n",
        False,
    ),
    "exit_under_unrelated_if": (
        PRE + "os.path.exists(o)\nif len(sys.argv) > 5:\n    sys.exit(2)  # EXIT\n",
        False,
    ),
    "probe_only_in_body": (
        PRE + "if len(sys.argv) > 5:\n    os.path.exists(o)\n    sys.exit(2)  # EXIT\n",
        False,
    ),
    "result_stored_first_is_not_proven": (
        PRE + "ok = os.path.exists(o)\nif not ok:\n    sys.exit(3)  # EXIT\n",
        False,
    ),
    "unparseable": ("def (:  # EXIT\n", False),
}


def _exit_line(source: str):
    for i, line in enumerate(source.splitlines(), 1):
        if "# EXIT" in line:
            return i
    return None


@pytest.mark.parametrize("name", sorted(EXIT_CASES))
def test_exit_is_attributed_only_when_the_failed_probe_decides_the_branch(name):
    source, expected = EXIT_CASES[name]
    assert figcapture.exit_controlled_by_probe(source, _exit_line(source)) is expected


def test_exit_attribution_needs_a_savefig_target_to_match():
    src = "import os, sys\nif not os.path.exists('o.png'):\n    sys.exit(3)\n"
    assert figcapture.exit_controlled_by_probe(src, 3) is False  # 源码里没有 savefig 目标可对


def test_listing_the_affected_directory_itself_or_below_it_is_a_signal(tmp_path):
    import os

    for listed in ("cache", "cache/sub"):
        (tmp_path / "cache" / "sub").mkdir(parents=True, exist_ok=True)
        led = figcapture.BlockedFileOps()
        led.blocked("shutil.rmtree", led.KIND_DELETE_DIR, str(tmp_path / "cache"))
        led.install()
        try:
            os.listdir(tmp_path / listed)
        finally:
            led.uninstall()
        assert {(x["kind"], x["via"]) for x in led.suspects()} == {("delete_dir", "listing")}, (
            listed
        )
        assert led.material() == []  # 只是 suspect，不升级


def test_listing_an_unrelated_directory_is_not_a_signal(tmp_path):
    import os

    (tmp_path / "other").mkdir()
    led = figcapture.BlockedFileOps()
    led.blocked("shutil.rmtree", led.KIND_DELETE_DIR, str(tmp_path / "cache"))
    led.install()
    try:
        os.listdir(tmp_path / "other")
    finally:
        led.uninstall()
    assert led.suspects() == []


def test_explain_exit_needs_both_the_last_missing_probe_and_a_controlled_branch(tmp_path):
    def ledger_with_miss():
        led = figcapture.BlockedFileOps()
        led.blocked("savefig", led.KIND_SAVEFIG, str(tmp_path / "o.png"))
        led._seq = 5
        for ev in led._events:
            ev["miss_seq"] = 5
        return led

    assert ledger_with_miss().explain_exit(controlled=True) is True
    assert ledger_with_miss().explain_exit(controlled=False) is False
    late = ledger_with_miss()
    late._seq = 6
    assert late.explain_exit(controlled=True) is False


@needs_worker
def test_an_unconditional_exit_after_a_final_probe_keeps_script_exited(figs):
    e = _deviated(
        figs, HDR + "fig.savefig('o.png')\nos.path.exists('o.png')\nsys.exit(7)\n", "sandbox"
    )
    assert e.code == "script_exited", e.code


posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows 没有 dir_fd，也不能用 os.open 打开目录拿 fd（PermissionError）；这些用例钉的是 POSIX 的 fd 相对路径语义",
)


# ============================================================ 第五轮：dir_fd / 直接传 fd 不可靠解析 → 不归因
def _dirfd_ledger(tmp_path, monkeypatch):
    here, other = tmp_path / "here", tmp_path / "other"
    here.mkdir()
    other.mkdir()
    monkeypatch.chdir(here)
    led = figcapture.BlockedFileOps()
    led.blocked("savefig", led.KIND_SAVEFIG, "o.png")  # 解析成 <here>/o.png
    led.install()
    return led, other


@posix_only
@pytest.mark.parametrize("call", ["stat", "open"])
def test_a_dir_fd_call_is_never_attributed_to_the_cwd_relative_target(tmp_path, monkeypatch, call):
    import os

    led, other = _dirfd_ledger(tmp_path, monkeypatch)
    fd = os.open(str(other), os.O_RDONLY)
    caught = None
    try:
        try:
            if call.startswith("stat"):
                os.stat("o.png", dir_fd=fd)
            else:
                os.open("o.png", os.O_RDONLY, dir_fd=fd)
        except FileNotFoundError as exc:  # 直接运行与 safe 里都会失败：和守卫无关
            caught = exc
    finally:
        led.uninstall()
        os.close(fd)
    assert caught is not None
    assert led.explains(caught) is False and led.material() == []
    assert led.suspects() == []
    assert led.explain_exit(controlled=True) is False


@posix_only
def test_the_same_calls_without_dir_fd_are_still_attributed(tmp_path, monkeypatch):
    import os

    led, _other = _dirfd_ledger(tmp_path, monkeypatch)
    try:
        with pytest.raises(FileNotFoundError) as err:
            os.stat("o.png")
    finally:
        led.uninstall()
    assert led.explains(err.value) is True
    assert ("savefig", "probe_failed") in {(s["kind"], s["via"]) for s in led.suspects()}


@posix_only
def test_a_stat_of_a_bare_file_descriptor_is_not_a_path_observation(tmp_path, monkeypatch):
    import os

    led, other = _dirfd_ledger(tmp_path, monkeypatch)
    fd = os.open(str(other), os.O_RDONLY)
    try:
        os.stat(fd)
    finally:
        led.uninstall()
        os.close(fd)
    assert led.suspects() == [] and led.material() == []


@posix_only
@needs_worker
def test_a_dir_fd_failure_in_a_real_script_stays_a_script_error(figs):
    src = (
        HDR
        + "import tempfile\nd = os.open(tempfile.mkdtemp(), os.O_RDONLY)\n"
        + "fig.savefig('o.png')\nos.stat('o.png', dir_fd=d)\n"
    )
    e = _deviated(figs, src, "sandbox")
    assert e.code != CODE, e.code


@posix_only
def test_open_with_a_custom_opener_is_not_a_cwd_relative_read(tmp_path, monkeypatch):
    import os

    led, other = _dirfd_ledger(tmp_path, monkeypatch)
    fd = os.open(str(other), os.O_RDONLY)
    try:
        with pytest.raises(FileNotFoundError):
            open("o.png", "rb", opener=lambda p, flags: os.open(p, flags, dir_fd=fd))
    finally:
        led.uninstall()
        os.close(fd)
    assert led.material() == [] and led.suspects() == []


# ---- Codex #1024 r4267917234：改名失败先确认是源路径、目标目录可用 ----
def test_a_rename_failure_needs_a_usable_destination_to_be_blamed_on_the_guard(
    tmp_path, monkeypatch
):
    import os

    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()

    def attributed(dest: str) -> bool:
        led = figcapture.BlockedFileOps()
        led.blocked("savefig", led.KIND_SAVEFIG, "o.png")
        exc = FileNotFoundError(2, "No such file or directory", "o.png", None, dest)
        return led.explains(exc)

    assert attributed("sub/final.png") is True  # 目标目录在：缺的只可能是被拦的源
    assert attributed("missing/final.png") is False  # 直接运行也会因为目标目录不存在而失败
    assert attributed("final.png") is True  # 当前目录
    # 失败的是目标（filename2）不是源：不拿它去对账
    led = figcapture.BlockedFileOps()
    led.blocked("savefig", led.KIND_SAVEFIG, "o.png")
    other = FileNotFoundError(2, "No such file", "x.tmp", None, "o.png")
    assert led.explains(other) is False
    assert os.path.isdir("sub")


@needs_worker
def test_a_real_rename_into_a_missing_directory_keeps_its_script_error(figs):
    e = _deviated(
        figs, HDR + "fig.savefig('o.png')\nos.replace('o.png', 'missing/final.png')\n", "sandbox"
    )
    assert e.code != CODE, e.code
    e = _deviated(
        figs,
        HDR + "os.mkdir('sub')\nfig.savefig('o.png')\nos.replace('o.png', 'sub/final.png')\n",
        "sandbox",
    )
    assert e.code == CODE, e.code


# ---- Codex #1024 r4268068602：被拦截的保存本身在真实语义下就会失败 → 后续失败不算守卫的 ----
@needs_worker
@pytest.mark.parametrize(
    ("script", "attributed"),
    [
        ("fig.savefig('missing/o.png')\nos.path.getsize('missing/o.png')\n", False),
        ("fig.savefig('o.notaformat')\nos.path.getsize('o.notaformat')\n", False),
        ("os.mkdir('sub')\nfig.savefig('sub/o.png')\nos.path.getsize('sub/o.png')\n", True),
        ("fig.savefig('o.svg')\nos.path.getsize('o.svg')\n", True),
    ],
)
def test_a_save_that_could_not_have_succeeded_is_not_blamed_on_the_guard(figs, script, attributed):
    e = _deviated(figs, HDR + script, "sandbox")
    assert (e.code == CODE) is attributed, (e.code, str(e)[:120])


def test_blocked_savefig_records_only_a_viable_destination(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    fmts = {"png", "pdf", "svg"}

    def recorded(name, fmt=None, supported=fmts):
        led = figcapture.BlockedFileOps()
        led.blocked_savefig([name], fmt, supported)
        return bool(led._events)

    assert recorded("o.png") and recorded("sub/o.png") and recorded("o", fmt="svg")
    assert not recorded("missing/o.png")
    assert not recorded("o.xyz")
    assert not recorded("o.png", supported=None)  # 确认不了就不记


def test_the_artifact_inspection_whitelist_keeps_every_public_receipt_fact():
    """r4268068611：`public_facts()` 加键必须同步导出检视的白名单，否则同一次执行两处事实不一致。"""
    from tavotto.rendercore import inspector

    facts = _rcpt_with(GOOD).public_facts()
    # `receipt_id` 是不透明实例号，导出检视有意只带 `receipt_identity`
    assert set(facts) - {"receipt_id"} <= set(inspector._PUBLIC_RECEIPT_KEYS), (
        set(facts) - {"receipt_id"} - set(inspector._PUBLIC_RECEIPT_KEYS)
    )
    # 透传的是已投影过的段：再投影一次不变（单一入口，没有第二套 schema）
    assert receipt.project_fidelity(facts["fidelity"]) == facts["fidelity"]


# ---- CI Windows：os.path.exists 等是 C 实现，不经 os.stat ----
def test_a_false_probe_result_counts_even_when_the_probe_never_reaches_os_stat(
    tmp_path, monkeypatch
):
    """Windows 3.12+ 的 `os.path.exists` / `isfile` / `Path.exists` 不经 `os.stat`：落空要从返回值上认。"""
    import os
    import pathlib

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os.path, "exists", lambda p: False)  # 模拟 C 实现：不碰 os.stat
    monkeypatch.setattr(os.path, "isfile", lambda p: False)
    for call in (
        lambda: os.path.exists("o.png"),
        lambda: os.path.isfile("o.png"),
    ):
        led = figcapture.BlockedFileOps()
        led.blocked("savefig", led.KIND_SAVEFIG, "o.png")
        led.install()
        try:
            call()
        finally:
            led.uninstall()
        assert led.explain_exit(controlled=True) is True
        assert led.material()  # 被升级为确定偏差
    # 返回 True（比如旧文件还在）不算落空；无关路径不算
    led = figcapture.BlockedFileOps()
    led.blocked("savefig", led.KIND_SAVEFIG, "o.png")
    led.install()
    try:
        os.path.exists("other.png")
        pathlib.Path("also_other.png").exists()
    finally:
        led.uninstall()
    assert led.explain_exit(controlled=True) is False
