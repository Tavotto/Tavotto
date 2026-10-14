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
