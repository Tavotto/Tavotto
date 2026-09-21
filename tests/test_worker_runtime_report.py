"""真 worker 的自报（ADR 0053）：v1 build 响应里的 `runtime` 是**那个子进程此刻量到的**。

判据的主语：**执行侧进程**的 `sys.prefix` / `sys.executable` / 包版本 / cwd——不是父进程
以为它会用哪个解释器。对拍的尺子刻意不同源：另起一个同样的解释器打印同一批值，
与 worker 自报逐字段比（`prefix` 与 `base_prefix` 两个都比，venv 与基础解释器就
差在这里）。legacy 信封的形状一字不变（`test_worker_roundtrip` 钉着）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import execspec, pool, receipt

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT = """\
import os
import matplotlib.pyplot as plt

# 每执行一次就往项目外的计数文件追加一行（写入守卫只管真实图库里的路径）
with open(os.environ["RT_RUNS_FILE"], "a", encoding="utf-8") as fh:
    fh.write("run\\n")
fig, ax = plt.subplots(figsize=(2, 1.5))
ax.plot([0, 1], [1, 0])
fig.savefig("Rt.pdf")
"""


@pytest.fixture
def figs(tmp_path, monkeypatch):
    root = tmp_path / "figs"
    root.mkdir()
    (root / "rt.py").write_text(SCRIPT, encoding="utf-8")
    monkeypatch.setenv("RT_RUNS_FILE", str(tmp_path / "runs.txt"))
    yield root
    pool.shutdown_all(str(root), wait=True)


def test_a_second_build_on_the_same_session_does_not_rerun_the_script(figs, tmp_path):
    """Codex #451 P1 的事实面：`pool.build()` 再来一次只是一次往返，用户脚本**不重跑**
    （worker 侧 `build()` 对已 build 的会话早返回）。计数的主语是脚本自己写的副作用，
    不是回执里的 generation——generation 不变量不到重跑。`build_owned()` 的所有权
    只给第一次。"""
    w1, resp1, created1 = pool.build_owned("rt.py", str(figs), "__main__")
    w2, resp2, created2 = pool.build_owned("rt.py", str(figs), "__main__")
    assert w1 is w2 and (created1, created2) == (True, False)
    assert (tmp_path / "runs.txt").read_text(encoding="utf-8").count("run") == 1
    assert resp2["runtime"]["prefix"] == resp1["runtime"]["prefix"]
    assert w2.last_build_runtime == resp1["runtime"]


def _independent_facts(python: str) -> dict:
    code = (
        "import sys, json, platform, importlib.metadata as md\n"
        "print(json.dumps({'executable': sys.executable, 'prefix': sys.prefix,"
        " 'base_prefix': sys.base_prefix, 'python_version': platform.python_version(),"
        " 'matplotlib': md.version('matplotlib')}))\n"
    )
    out = subprocess.run(
        [python, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_worker_reports_the_prefix_of_the_process_that_actually_ran(figs):
    worker, resp = pool.build("rt.py", str(figs), "__main__")
    rt = resp["runtime"]
    facts = _independent_facts(worker.python)
    assert rt["executable"] == facts["executable"]
    assert rt["prefix"] == facts["prefix"]
    assert rt["base_prefix"] == facts["base_prefix"]
    assert rt["python_version"] == facts["python_version"]
    assert rt["packages"]["matplotlib"] == facts["matplotlib"]
    # cwd 是脚本**真正**看到的：默认模式就是那个沙盒目录（写入边界）
    assert Path(rt["cwd"]).resolve() == worker.sandbox.resolve()
    assert rt["argv0"] == str((figs / "rt.py").resolve())
    assert worker.last_build_runtime == rt  # 控制面把它记在账上
    assert "runtime" in resp and resp["stems"] == {"Rt": resp["stems"]["Rt"]}


def test_the_receipt_assembled_from_a_real_worker_is_complete(figs):
    worker, resp = pool.build("rt.py", str(figs), "__main__")
    rcpt = receipt.from_worker(
        worker, resp, control_plane=pool.control_plane_of(worker), grant=None
    )
    assert rcpt.completeness == receipt.COMPLETENESS_COMPLETE
    assert rcpt.generation == worker.generation
    assert rcpt.source_revision == worker.script_sha1 != ""
    assert rcpt.launch_context["cwd_origin"] == execspec.CWD_ORIGIN_SANDBOX
    assert [d["stem"] for d in rcpt.descriptors] == ["Rt"]
    private = rcpt.to_payload(include_private=True)
    assert private["runtime"]["prefix"] == resp["runtime"]["prefix"]
    assert private["interpreter"] == worker.python
    # 同一条会话再取一次回执：id 相同（同一次执行实例）
    again = receipt.from_worker(worker, resp, control_plane="python_pool", grant=None)
    assert again.receipt_id == rcpt.receipt_id


# ---------------------------------------------------------------------------
# U09（ADR 0070）：自报来自跑脚本的那个进程；输入观察如实标 partial
# ---------------------------------------------------------------------------
OBSERVED_SCRIPT = """\
import os
import csv
import pathlib
import matplotlib.pyplot as plt

import labhelp                              # 本地模块：回执要记它（观察到的本地模块）

with open("data.csv", encoding="utf-8", newline="") as fh:   # Python open：观察得到
    xs = [float(r["x"]) for r in csv.DictReader(fh)]
raw = pathlib.Path("notes.txt").read_text(encoding="utf-8")   # Path.open：观察得到
HERE = os.path.dirname(os.path.abspath(__file__))
fd = os.open(os.path.join(HERE, "native.bin"), os.O_RDONLY)   # os.open：观察**不到**（如实 partial）
os.close(fd)
fig, ax = plt.subplots(figsize=(2, 1.5))
ax.plot(xs, [labhelp.scale(x) for x in xs])
ax.set_title(raw.strip())
fig.savefig("Obs.pdf")
"""


@pytest.fixture
def observed_project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "obs.py").write_text(OBSERVED_SCRIPT, encoding="utf-8")
    (root / "labhelp.py").write_text("def scale(x):\n    return 2 * x\n", encoding="utf-8")
    (root / "data.csv").write_text("x\n1\n2\n3\n", encoding="utf-8")
    (root / "notes.txt").write_text("hello\n", encoding="utf-8")
    (root / "native.bin").write_bytes(b"\x00\x01")
    yield root
    pool.shutdown_all(str(root), wait=True)


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_report_carries_origin_pid_and_observed_inputs_of_the_process_that_ran(
    observed_project,
):
    """自报是 build 回执（`report_origin`）、报的 pid 就是控制面起的那个子进程；脚本经 Python `open` /
    `Path.open` 读的项目内文件在 `inputs.files` 里（相对路径 + sha256），import 到的本地模块在
    `local_modules` 里；`os.open` 走的那份**不在**——`observation=partial`、`unobserved` 里写着，不冒充看全了。"""
    worker, resp = pool.build("obs.py", str(observed_project), "__main__")
    rt = resp["runtime"]
    # 真 worker 报的字面量就是控制面认的那个（`figsession.REPORT_ORIGIN_BUILD` ↔ `receipt.REPORT_ORIGIN_BUILD`
    # 的同源判据：worker 侧模块平铺 import，父进程 import 不动它，只能拿真跑出来的值对）
    assert rt["report_origin"] == receipt.REPORT_ORIGIN_BUILD == "build"
    assert rt["pid"] == worker.child_pid == worker.proc.pid
    inputs = rt["inputs"]
    assert inputs["observation"] == "partial"
    assert "native_io" in inputs["unobserved"] and "os_open" in inputs["unobserved"]
    files = {f["path"]: f for f in inputs["files"]}
    assert set(files) == {"data.csv", "notes.txt"}, files
    assert files["data.csv"]["sha256"] == _sha256(observed_project / "data.csv")
    assert files["notes.txt"]["size"] == len(b"hello\n")
    mods = {m["name"]: m for m in inputs["local_modules"]}
    assert mods["labhelp"]["path"] == "labhelp.py"
    assert mods["labhelp"]["sha256"] == _sha256(observed_project / "labhelp.py")
    assert inputs["truncated"] is False
    rcpt = receipt.from_worker(worker, resp, control_plane="python_pool", grant=None)
    assert rcpt.completeness == receipt.COMPLETENESS_COMPLETE
    assert rcpt.pid_check == receipt.PID_CHECK_OK
    assert rcpt.observed_files()["data.csv"] == files["data.csv"]["sha256"]
    # 公开投影：相对路径在、机器路径不在
    public = json.dumps(rcpt.to_payload(), ensure_ascii=False)
    assert "data.csv" in public and str(observed_project) not in public


def test_the_binding_recorded_at_plan_time_is_matched_against_what_the_script_read(
    observed_project,
):
    """预检记下 `data.csv` 的内容 → 脚本读到的一致：matched；预检之后换了内容再起会话：不一致并点名。"""
    from tavotto.engine import databinding

    binding = databinding.binding_for(observed_project / "obs.py", observed_project, "sandbox")
    # 静态证据只认数据类扩展名 / 带分隔符的字面量：`native.bin` 不在里面（窄判据，如实）
    assert set(binding["expected"]) == {"data.csv", "notes.txt"}
    worker, resp = pool.build("obs.py", str(observed_project), "__main__")
    same = receipt.from_worker(
        worker, resp, control_plane="python_pool", grant=None, binding=binding
    )
    chk = same.binding_check()
    assert chk["matched"] is True and chk["same"] == ["data.csv", "notes.txt"]
    assert chk["unobserved"] == [] and chk["changed"] == []
    pool.shutdown_all(str(observed_project), wait=True)
    (observed_project / "data.csv").write_text("x\n100\n200\n300\n", encoding="utf-8")
    worker2, resp2 = pool.build("obs.py", str(observed_project), "__main__")
    changed = receipt.from_worker(
        worker2, resp2, control_plane="python_pool", grant=None, binding=binding
    )
    chk2 = changed.binding_check()
    assert chk2["matched"] is False and chk2["changed"] == ["data.csv"]
    assert changed.public_identity() != same.public_identity()
