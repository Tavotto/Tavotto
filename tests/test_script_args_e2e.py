"""T07：参数表单 / 数据指认 / 输出参数经**真实公共入口**到真 worker（真服务、会话认证默认开）。

判据的主语全部来自独立观测：脚本每执行一次往**项目外**的记录文件追加一行（它实际看到的 `sys.argv[1:]`
与它画出的数组）；原生参照是同一个解释器在独立目录里直接跑同一份脚本——不是请求回显、不是 store 里的期望值。

* A01：六必填 FFT 等价 fixture（字面量声明）。表单那条路（schema → golden 向量里表单编辑产出的 token）与
  手敲原始 token 那条路是**同一串 token**：同一个运行配置引用、同一次执行、与原生逐点相同的曲线。
* A05 / P03：`FileType('w')` 输出参数。读 schema 不碰输出；缺必填时只执行一次、不重试、不替用户加覆盖开关；
  运行动作的影响摘要说清输出相对路径落在哪个工作目录档。
* 数据缺失（ADR 0106）在同一个会话里成为 `input_location` 需求，用户亲手指认后 recheck → 新修订 → 画出来的是
  被指认的那份数据。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from support import foundation_app as fa
from tavotto.engine import figcapture, pool as _pool

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = json.loads(
    (Path(__file__).parent / "golden" / "script_args_form_vectors.json").read_text("utf-8")
)

try:
    WORKER_PY = _pool.find_worker_python()
except Exception:  # noqa: BLE001
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)
ENV = {"TAVOTTO_USER_ENV_DISCOVERY": "0", "PYTHONPATH": str(ROOT / "src")}
SESSIONS = "/api/engine/preparation-sessions"

#: 与向量 `fft6` 同一组字面量声明，多了画图与记录（记录文件在项目外）
FFT6 = """\
import argparse, json, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

p = argparse.ArgumentParser(description="FFT")
p.add_argument("--freq", type=float, required=True, help="Hz")
p.add_argument("--amp", type=float, required=True)
p.add_argument("--phase", type=float, required=True)
p.add_argument("--n", type=int, required=True)
p.add_argument("--tag", required=True)
p.add_argument("--mode", choices=["sin", "cos"], required=True)
a = p.parse_args()
x = np.arange(a.n)
f = np.sin if a.mode == "sin" else np.cos
y = a.amp * f(2 * np.pi * a.freq * x / a.n + a.phase)
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot(x, y)
ax.set_title(a.tag)
fig.savefig("fft.pdf")
with open({rec!r}, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"argv": sys.argv[1:], "ydata": [round(float(v), 9) for v in y]}}) + "\\n")
"""

WRITER = """\
import argparse
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

with open({rec!r}, "a", encoding="utf-8") as fh:
    fh.write("x")
p = argparse.ArgumentParser()
p.add_argument("--out", type=argparse.FileType("w"), required=True)
p.add_argument("--freq", type=float, required=True)
a = p.parse_args()
a.out.write("freq=%s\\n" % a.freq)
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1], [0, a.freq])
fig.savefig("w.pdf")
"""

READER = """\
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

with open("data/raw.csv", encoding="utf-8") as fh:
    ys = [float(line) for line in fh.read().split()]
with open({rec!r}, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(ys) + "\\n")
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot(range(len(ys)), ys)
fig.savefig("reader.pdf")
"""


def _records(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _call(app, path, payload=None, **kw):
    try:
        return app.call(path, payload, **kw)
    except fa.HttpError as exc:
        return exc.status, exc.body


def _settle(app, sid: str, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    while True:
        _, report = app.call(f"{SESSIONS}/{sid}", timeout=30)
        if report["phase"] not in ("running", "awaiting_runtime_input", "scanning"):
            return report
        assert time.time() < deadline, report
        time.sleep(0.2)


def _run(app, report: dict) -> dict:
    (run,) = [a for a in report["actions"] if a["kind"] == "run"]
    status, body = app.call(
        f"{SESSIONS}/{report['session_id']}/actions",
        {"action_id": run["id"], "expected_config_revision": report["config_revision"]},
        timeout=60,
    )
    assert status == 202, body
    return _settle(app, report["session_id"])


@needs_worker
def test_a01_the_form_path_and_the_raw_token_path_are_the_same_execution(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    rec = tmp_path / "rec.jsonl"
    (proj / "fft.py").write_text(FFT6.format(rec=str(rec)), encoding="utf-8")
    case = next(c for c in GOLDEN["cases"] if c["name"] == "fft6_fill_all")
    form_tokens = case["after"]  # TS 侧证明：表单逐项填写恰好产出这串 token
    with fa.running_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        status, body = app.call("/api/engine/script-arguments?script=fft.py", timeout=30)
        assert status == 200
        schema = body["arguments"]
        # 记录行自己读了 sys.argv（测量用）——如实标 partial，表单照样可用
        assert schema["reasons"] == ["reads_sys_argv"] and schema["form_enabled"] is True
        # 真实 schema 与 TS 用的向量 schema 是同一份（除行号外脚本前几行相同，所以整份相同）
        assert [a["dest"] for a in schema["arguments"]] == [
            a["dest"] for a in GOLDEN["schemas"]["fft6"]["arguments"]
        ]
        assert sum(1 for a in schema["arguments"] if a["required"]) == 6
        assert _records(rec) == []  # 读 schema 零执行

        # 表单那条路：准备会话 + 表单产出的 token
        _, report = app.call(SESSIONS, {"script": "fft.py", "argv": form_tokens}, timeout=120)
        req = next(r for r in report["requirements"] if r["kind"] == "script_arguments")
        assert req["blocking"] is False and req["payload"]["argv_count"] == 12
        done = _run(app, report)
        assert done["phase"] == "completed", done
        rc = report["target"]["run_config"]
        # 原始 token 那条路：手敲同一串 token 走试运行端点 → 同一个运行配置引用（同一份执行意图）
        _, probed = app.call(
            "/api/registry/probe", {"script": "fft.py", "argv": list(form_tokens)}, timeout=300
        )
        assert probed.get("run_config") == rc or rc in json.dumps(probed), probed
        render = app.render(figcapture.runtime_asset_id("fft.py", "fft", rc))
        assert render.get("manifest"), render

    hot = _records(rec)
    assert hot and all(r["argv"] == form_tokens for r in hot)
    native_rec = tmp_path / "native.jsonl"
    native_dir = tmp_path / "native"
    native_dir.mkdir()
    native_script = native_dir / "fft.py"
    native_script.write_text(FFT6.format(rec=str(native_rec)), encoding="utf-8")
    subprocess.run(
        [WORKER_PY, str(native_script), *form_tokens],
        cwd=native_dir,
        check=True,
        timeout=300,
        env={**os.environ, "MPLBACKEND": "Agg"},
    )
    assert hot[0]["ydata"] == _records(native_rec)[0]["ydata"]
    assert len(hot[0]["ydata"]) == 16


@needs_worker
def test_a05_an_output_file_argument_is_never_overwritten_behind_the_users_back(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    rec = tmp_path / "runs.txt"
    (proj / "writer.py").write_text(WRITER.format(rec=str(rec)), encoding="utf-8")
    (proj / "result.txt").write_bytes(b"precious")
    with fa.running_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        _, body = app.call("/api/engine/script-arguments?script=writer.py", timeout=30)
        out = next(a for a in body["arguments"]["arguments"] if a["dest"] == "out")
        assert out["role"] == "output_file"
        assert (proj / "result.txt").read_bytes() == b"precious" and not rec.exists()

        # 缺必填 --freq：真 parser 报缺参；只执行一次，不重试，token 不被补上任何东西
        _, report = app.call(
            SESSIONS, {"script": "writer.py", "argv": ["--out", "result.txt"]}, timeout=120
        )
        (run,) = [a for a in report["actions"] if a["kind"] == "run"]
        assert run["impact"]["script_writes"] == {
            "declared_output_arguments": 1,
            "cwd_mode": "sandbox",
        }
        done = _run(app, report)
        assert done["outcome"]["kind"] == "failed", done
        assert done["outcome"]["code"] == "script_needs_arguments"
        time.sleep(1.0)
        assert rec.read_text(encoding="utf-8") == "x"  # 恰好一次
        # 沙盒档：脚本的相对输出落在沙盒，用户项目里的同名文件逐字节不变
        assert (proj / "result.txt").read_bytes() == b"precious"
        assert done["target"]["argv_count"] == 2  # 没有被追加 --overwrite / --force 一类 token

        # 用户明确选「在脚本目录里运行」之后：影响摘要如实说出输出落在项目里
        status, _ = app.call("/api/engine/workdir", {"mode": "project"}, method="PATCH")
        assert status == 200
        _, again = app.call(
            SESSIONS,
            {"script": "writer.py", "argv": ["--out", "result.txt", "--freq", "2"]},
            timeout=120,
        )
        (run2,) = [a for a in again["actions"] if a["kind"] == "run"]
        assert run2["impact"]["script_writes"]["cwd_mode"] == "project"
        finished = _run(app, again)
        assert finished["phase"] == "completed", finished
    # 用户亲手给的输出参数 + 亲手选的工作目录：这次写入就是他要的（原生语义）
    assert (proj / "result.txt").read_text(encoding="utf-8") == "freq=2.0\n"


@needs_worker
def test_missing_data_is_answered_in_the_same_session_and_the_plot_uses_the_chosen_file(tmp_path):
    proj = tmp_path / "proj"
    (proj / "elsewhere").mkdir(parents=True)
    rec = tmp_path / "rec.jsonl"
    (proj / "reader.py").write_text(READER.format(rec=str(rec)), encoding="utf-8")
    (proj / "elsewhere" / "raw.csv").write_text("3\n1\n4\n1\n5\n", encoding="utf-8")
    with fa.running_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        _, report = app.call(SESSIONS, {"script": "reader.py"}, timeout=120)
        failed = _run(app, report)
        assert failed["outcome"]["code"] == "missing_input", failed
        (req,) = [r for r in failed["requirements"] if r["kind"] == "input_location"]
        assert req["payload"]["requested"] == "data/raw.csv"
        status, _ = app.call(
            "/api/engine/input-remap",
            {
                "requested": "data/raw.csv",
                "chosen": str(proj / "elsewhere" / "raw.csv"),
                "chosen_kind": "file",
            },
        )
        assert status == 200
        (recheck,) = [a for a in failed["actions"] if a["kind"] == "recheck"]
        status, _ = _call(
            app,
            f"{SESSIONS}/{failed['session_id']}/actions",
            {"action_id": recheck["id"], "expected_config_revision": failed["config_revision"]},
        )
        assert status in (200, 202)
        _, fresh = app.call(f"{SESSIONS}/{failed['session_id']}", timeout=30)
        assert fresh["config_revision"] == failed["config_revision"] + 1
        assert not [r for r in fresh["requirements"] if r["kind"] == "input_location"]
        done = _run(app, fresh)
        assert done["phase"] == "completed", done
    assert _records(rec) == [[3.0, 1.0, 4.0, 1.0, 5.0]]
