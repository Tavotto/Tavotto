"""T10 真链路：GUI（真服务）与 MCP（真 stdio server 子进程）消费**同一份**运行配置，得到同一个执行快照与数据。

夹具沿用 T03 的形状（`test_run_argv_e2e.py`）：脚本每执行一次往**项目外**追加一行 `sys.argv[1:]`（执行次数与脚本
实际看到的参数的唯一真值）；曲线 = `YS × scale`，渲染回的 ylim 由它决定——「两个入口画的是同一份数据」由图里真实的
数据范围证明，不由请求回显证明。两个进程共用一个 Tavotto 数据目录（运行配置登记就在那里）。

另有两条「没有界面」的真 worker 用例：脚本要命令行参数 / 运行中要 `input()`——MCP 立即以结构化待办返回，不等待。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from support import foundation_app as fa
from support.pipedrain import StderrDrain
from tavotto.engine import figcapture, pool as _pool

ROOT = Path(__file__).resolve().parent.parent

try:
    WORKER_PY = _pool.find_worker_python()
except Exception:  # noqa: BLE001
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)
ENV = {"TAVOTTO_USER_ENV_DISCOVERY": "0", "PYTHONPATH": str(ROOT / "src")}

YS = [1.0, 2.0, 4.0]
SCRIPT = """\
import json, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

args = sys.argv[1:]
scale = float(args[args.index("--scale") + 1]) if "--scale" in args else 1.0
with open({counter!r}, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args, ensure_ascii=False) + "\\n")
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1, 2], [v * scale for v in {ys!r}])
fig.savefig("result.pdf")
"""

NEEDS_ARGS = """\
import argparse
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

p = argparse.ArgumentParser()
p.add_argument("--scale", type=float, required=True)
p.add_argument("--mode", choices=["lin", "log"], default="lin")
a = p.parse_args()
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1, 2], [a.scale, 2 * a.scale, 4 * a.scale])
fig.savefig("needs.pdf")
"""

ASKS = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

threshold = float(input("阈值？"))
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1], [0, threshold])
fig.savefig("asks.pdf")
"""


def _runs(counter: Path) -> list[list[str]]:
    if not counter.exists():
        return []
    return [json.loads(line) for line in counter.read_text(encoding="utf-8").splitlines() if line]


def _ylim(manifest: dict) -> list[float]:
    axes = next(e for e in manifest["elements"] if e["role"] == "axes")
    return next(f["value"] for f in axes["editable"] if f["prop"] == "ylim")


def _expected_ylim(scale: float) -> list[float]:
    ys = [v * scale for v in YS]
    margin = 0.05 * (max(ys) - min(ys))
    return [min(ys) - margin, max(ys) + margin]


class Mcp:
    """真 MCP server 子进程，按行收发 JSON-RPC（`test_mcp_roundtrip.Client` 的形状），与 GUI 共用数据目录。"""

    def __init__(self, roots: Path, work: Path):
        env = {
            **os.environ,
            "TAVOTTO_MCP_ROOTS": str(roots),
            "TAVOTTO_DATA_DIR": str(work / "data"),
            "TAVOTTO_CONFIG_DIR": str(work / "config"),
            "HOME": str(work / "home"),
            "TAVOTTO_NO_UPDATE_CHECK": "1",
            "TAVOTTO_NO_TELEMETRY": "1",
            **ENV,
        }
        for name in ("TAVOTTO_WORKER_PYTHON", "MM_WORKER_PYTHON"):
            env.pop(name, None)
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "codex-plugin" / "mcp" / "server.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(ROOT),
        )
        self._stderr = StderrDrain(self.proc)
        self.n = 0
        self.call(
            "initialize",
            {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t10"}},
        )

    def call(self, method: str, params=None) -> dict:
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError("server 挂了:\n" + self._stderr.tail(4000, wait=10))
        return json.loads(line.decode("utf-8"))

    def tool(self, name: str, args: dict) -> dict:
        raw = self.call("tools/call", {"name": name, "arguments": args})
        assert "result" in raw, json.dumps(raw, ensure_ascii=False)[:1500]
        return raw["result"]

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.wait(timeout=120)


@pytest.fixture
def project(tmp_path):
    # 工作区根只含项目；HOME 在数据目录那一侧（根含 HOME 会被拒为 `workspace_root_too_broad`）
    proj = tmp_path / "ws" / "proj"
    proj.mkdir(parents=True)
    counter = tmp_path / "runs.jsonl"
    (proj / "scaled.py").write_text(SCRIPT.format(counter=str(counter), ys=YS), encoding="utf-8")
    return proj, counter, tmp_path / "work"


def test_gui_and_mcp_run_the_same_configuration_and_draw_the_same_data(project, tmp_path):
    """C19 / R02：GUI 带参数运行 → MCP 用同一份 token、同一个引用、缺省沿用（磁盘面板判据）→ 同一个 `rc_` 引用、
    脚本看到同样的 argv、图里是同一份数据；`argv=[]` 是另一张图，不串。"""
    proj, counter, work = project
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        status, two = app.call(
            "/api/registry/probe", {"script": "scaled.py", "argv": ["--scale", "2"]}, timeout=300
        )
        assert status == 200 and two["stems"] == ["result"], two
        rc = two["run_config"]
        gui_id = figcapture.runtime_asset_id("scaled.py", "result", rc)
        gui_ylim = _ylim(app.render(gui_id)["manifest"])
        assert gui_ylim == pytest.approx(_expected_ylim(2.0))
        assert _runs(counter) == [["--scale", "2"]]

        mcp = Mcp(proj.parent, work)
        try:
            health = mcp.tool("tavotto_health", {})["structuredContent"]
            assert "script-argv" in health["engine_features"]

            res = mcp.tool(
                "tavotto_open_figure",
                {"project_path": str(proj), "stem": "result", "argv": ["--scale", "2"]},
            )
            assert not res.get("isError"), res["structuredContent"]
            body = res["structuredContent"]
            assert body["run_config"] == {"id": rc, "argv_count": 2, "source": "argv"}
            assert _ylim(body["manifest"]) == pytest.approx(gui_ylim)
            assert _runs(counter)[-1] == ["--scale", "2"]
            assert "--scale" not in json.dumps(body, ensure_ascii=False)

            # 缺省：沿用这个脚本最近一次在 GUI 里明确运行的配置（磁盘上的产物就是那次写的）——同一个会话，不再执行
            runs = len(_runs(counter))
            again = mcp.tool("tavotto_open_figure", {"project_path": str(proj), "stem": "result"})
            again = again["structuredContent"]
            assert again["run_config"] == {"id": rc, "argv_count": 2, "source": "script_default"}
            assert again["session_id"] == body["session_id"] and again["reused"] is True
            assert len(_runs(counter)) == runs

            # 引用 GUI 那份配置：同一个
            ref = mcp.tool(
                "tavotto_open_figure",
                {"project_path": str(proj), "stem": "result", "run_config": rc},
            )["structuredContent"]
            assert ref["run_config"]["id"] == rc and ref["session_id"] == body["session_id"]

            # 明确不带参数：另一张图（另一份数据、另一个会话），不串
            plain = mcp.tool(
                "tavotto_open_figure", {"project_path": str(proj), "stem": "result", "argv": []}
            )["structuredContent"]
            assert "run_config" not in plain and plain["session_id"] != body["session_id"]
            assert _ylim(plain["manifest"]) == pytest.approx(_expected_ylim(1.0))
            assert _runs(counter)[-1] == []

            # 改图之后的冷重放仍用这个会话冻结的那份（不是之后的无参数）
            replay = mcp.tool("tavotto_verify_replay", {"session_id": body["session_id"]})
            assert not replay.get("isError"), replay["structuredContent"]
            assert _runs(counter)[-1] == ["--scale", "2"]
        finally:
            mcp.close()


def test_without_a_ui_the_answers_come_back_structured_and_fast(tmp_path):
    """I05 / C21：要命令行参数 → 只读 schema 摘要 + `argv` 怎么答；运行中要 `input()` → 立即
    `script_needs_input`（没有能答题的界面，不等待）；回答 argv 之后同一脚本照常出图。"""
    proj = tmp_path / "ws" / "proj"
    proj.mkdir(parents=True)
    (proj / "needs.py").write_text(NEEDS_ARGS, encoding="utf-8")
    (proj / "asks.py").write_text(ASKS, encoding="utf-8")
    (proj / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "needs.py": {"entry": "__main__", "stems": ["needs"]},
                    "asks.py": {"entry": "__main__", "stems": ["asks"]},
                }
            }
        ),
        encoding="utf-8",
    )
    work = tmp_path / "work"
    for d in ("data", "config", "home"):
        (work / d).mkdir(parents=True)
    mcp = Mcp(proj.parent, work)
    try:
        t0 = time.monotonic()
        res = mcp.tool("tavotto_open_figure", {"project_path": str(proj), "stem": "needs"})
        assert res["isError"] is True
        body = res["structuredContent"]
        assert body["code"] == "script_needs_arguments"
        assert body["requirements"] == [
            {"kind": "script_arguments", "answer_with": "argv", "where": "this_tool"}
        ]
        flags = {a["flags"][0]: a for a in body["arguments"]["schema"]["arguments"]}
        assert flags["--scale"]["required"] is True
        assert flags["--mode"]["choices"] == ["lin", "log"]
        assert body["arguments"]["parse_kind"] == "missing_required"

        ok = mcp.tool(
            "tavotto_open_figure",
            {"project_path": str(proj), "stem": "needs", "argv": ["--scale", "3"]},
        )
        assert not ok.get("isError"), ok["structuredContent"]
        assert ok["structuredContent"]["run_config"]["argv_count"] == 2

        t1 = time.monotonic()
        res = mcp.tool("tavotto_open_figure", {"project_path": str(proj), "stem": "asks"})
        waited = time.monotonic() - t1
        body = res["structuredContent"]
        assert res["isError"] is True and body["code"] == "script_needs_input"
        assert body["input"] == {"reason": "no_interactive_client", "secret": False}
        assert body["requirements"] == [
            {
                "kind": "runtime_input",
                "answer_with": None,
                "where": "tavotto_app",
                "reason": "no_interactive_client",
            }
        ]
        # 「立即」：远小于任何输入等待预算（冷启动一个 worker 的时间量级），不是挂到超时
        assert waited < 120, waited
        size = len(json.dumps(res, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        assert size <= 64 * 1024
        print({"needs_args_s": round(t1 - t0, 2), "needs_input_s": round(waited, 2), "bytes": size})
    finally:
        mcp.close()
