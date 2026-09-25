"""真实结果：按**生成的宿主配置**从**解包的完整包**起 server，走完一张真科研图（多宿主 PR 2）。

主语是「一个只会 tools/call 的宿主客户端」（没有画布、没有 roots、没有确认框——Claude
Desktop 聊天、DSH、Claude Code CLI 的共性）拿到的东西：

    打开 → 读状态 → 改（全量 patches）→ 再改（在已有修改上合并）→ 再读状态 → 预检
    → 导出 PDF/PNG（预检要确认时先被挡住）→ 产物检查器核对 → 源脚本未被改写
    → 全新 worker 重放一致 → 关闭

这是**协议级**证据（`protocol_tested`），不是任何品牌客户端的实测；真宿主验收见
docs/implementation/multi-host-mcp/acceptance.md。另外两条：两个独立客户端的会话互相
取不到；拒绝 / 缺失工作区时 fail closed。

缺科学栈就跳过（与 tests/test_mcp_roundtrip.py 同一判据）。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from support.pipedrain import StderrDrain
from tavotto.rendercore import inspector
from tests.support import pluginkit as kit
from tests.test_mcp_roundtrip import REGISTRY, SCRIPT, _worker_python, patches_for

pytestmark = pytest.mark.skipif(
    _worker_python() is None, reason="没有带科学栈的解释器，跳过真链路用例"
)


@pytest.fixture(scope="module")
def unpacked(tmp_path_factory) -> Path:
    stage = kit.load_script("plugin_stage")
    base = tmp_path_factory.mktemp("完整 包")
    kit.synthetic_staging(base / "stage")
    archive = stage.write_zip(base / "stage", base / "codex-plugin-wf.zip")
    return stage.unpack_zip(archive, base / "解包")


def _figures(root: Path) -> Path:
    figures = root / "figures"
    figures.mkdir(parents=True)
    (figures / "figm.py").write_text(SCRIPT, encoding="utf-8")
    (figures / "tavotto_registry.json").write_text(json.dumps(REGISTRY), encoding="utf-8")
    proc = subprocess.run(
        [_worker_python(), str(figures / "figm.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(figures),
    )
    assert proc.returncode == 0, proc.stderr
    return figures


def _config(unpacked: Path, host: str, project: Path, tmp_path: Path) -> dict:
    proc = subprocess.run(
        [
            sys.executable,
            str(unpacked / "integrations" / "configure.py"),
            "--host",
            host,
            "--project-root",
            str(project),
            "--python",
            sys.executable,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(tmp_path),
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    return next(iter(next(iter(data.values())).values()))


class HostClient:
    """按配置原样起 server（command + args，不过 shell），只会 tools/call 的宿主。"""

    def __init__(self, entry: dict, tmp_path: Path, data_dir: Path):
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "TAVOTTO_MCP_ROOTS")}
        env.update(entry["env"])
        env["TAVOTTO_DATA_DIR"] = str(data_dir)
        cwd = tmp_path / "宿主 cwd"
        cwd.mkdir(exist_ok=True)
        self.proc = subprocess.Popen(
            [entry["command"], *entry["args"]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(cwd),
        )
        self._stderr = StderrDrain(
            self.proc
        )  # 与子进程并发排空 stderr（support/pipedrain.py；#549 Windows 分片的教训）
        self.n = 0
        init = self.call(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "tools-only-host", "version": "1"},
            },
        )
        assert init["result"]["serverInfo"]["version"] != "0"

    def call(self, method: str, params=None) -> dict:
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError(self._stderr.tail(4000, wait=10))
        return json.loads(line.decode("utf-8"))

    def raw_tool(self, name: str, args: dict) -> dict:
        return self.call("tools/call", {"name": name, "arguments": args})["result"]

    def tool(self, name: str, args: dict) -> dict:
        res = self.raw_tool(name, args)
        assert not res.get("isError"), json.dumps(res.get("structuredContent"), ensure_ascii=False)[
            :2000
        ]
        return res["structuredContent"]

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        assert self.proc.wait(timeout=120) == 0  # stdin 关 = 宿主走了，server 干净退出
        self.proc.stdout.close()
        self._stderr.join()
        self.proc.stderr.close()


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_a_real_figure_end_to_end_through_a_generated_config(unpacked, tmp_path):
    figures = _figures(tmp_path / "项目 A")
    script_hash = _sha(figures / "figm.py")
    client = HostClient(
        _config(unpacked, "claude-desktop", figures, tmp_path), tmp_path, tmp_path / "data"
    )
    try:
        health = client.tool("tavotto_health", {})
        assert health["server"]["package_dir"] == str(unpacked)
        assert health["checks"]["workspace_authorized"]["ok"] is True

        opened = client.tool("tavotto_open_figure", {"project_path": str(figures)})
        sid = opened["session_id"]
        assert opened["stem"] == "FigM" and opened["registry"]["parameterizable"] is True

        state = client.tool("tavotto_session_state", {"session_id": sid})
        assert state["patches"] == []

        # 第一轮：标题 / 图例 / 线宽等（全量列表）
        first = patches_for(opened["manifest"])
        applied = client.tool("tavotto_apply_overrides", {"session_id": sid, "patches": first})
        assert applied["applied"] == len(first) and applied["rejected"] == []

        # 第二轮：**先读当前状态再合并**，不是只发增量（增量会把第一轮全还原）
        now = client.tool("tavotto_session_state", {"session_id": sid})
        merged = [p for p in now["patches"] if not (p["prop"] == "linewidth")]
        merged += [
            {"gid": p["gid"], "prop": "linewidth", "value": 1.25}
            for p in now["patches"]
            if p["prop"] == "linewidth"
        ]
        client.tool("tavotto_apply_overrides", {"session_id": sid, "patches": merged})
        after = client.tool("tavotto_session_state", {"session_id": sid})
        got = {(p["gid"], p["prop"]): p["value"] for p in after["patches"]}
        assert len(got) == len(first), "第二轮把第一轮的修改清掉了"
        assert all(v == 1.25 for (g, prop), v in got.items() if prop == "linewidth")
        title = next(p for p in first if p["prop"] == "fontsize")
        assert got[(title["gid"], "fontsize")] == title["value"]

        checks = client.tool("tavotto_preflight", {"session_id": sid})
        assert set(checks["counts"]) == {"error", "warn", "not_verifiable", "suggestion"}

        out_dir = figures / "export"
        res = client.raw_tool(
            "tavotto_export",
            {"session_id": sid, "formats": ["pdf", "png"], "out_dir": str(out_dir)},
        )
        if checks["needs_confirm"]:
            # 预检要确认：没有 explicit_confirm 就一张不出——不许自动置 true 绕过
            assert res.get("isError") or res["structuredContent"].get("code") == "needs_confirm"
            assert not out_dir.exists() or not any(out_dir.iterdir())
            # 模拟**用户**在对话里明确说了「就这样导出」
            done = client.tool(
                "tavotto_export",
                {
                    "session_id": sid,
                    "formats": ["pdf", "png"],
                    "out_dir": str(out_dir),
                    "explicit_confirm": True,
                },
            )
        else:
            assert not res.get("isError"), res
            done = res["structuredContent"]
        files = {f["format"]: f for f in done["files"]}
        assert set(files) == {"pdf", "png"}
        for f in files.values():
            p = Path(f["path"])
            assert p.is_file() and p.stat().st_size > 0
            assert os.path.realpath(p).startswith(os.path.realpath(figures))
            # 产物检查器的结论（与 HTTP 导出同一份接线），不是「文件存在」
            assert f["manifest"]["verdict"] == inspector.ACCEPTED, f["manifest"]
        assert done["patch_hash"] == after["patch_hash"]

        import pymupdf

        with pymupdf.open(files["pdf"]["path"]) as doc:
            text = doc[0].get_text()
            assert "Time (min)" in text and "Kinetics" in text
            assert not doc[0].get_images()

        # PNG 的 dpi 自己量（pHYs 块），不只信回执
        import struct

        raw = Path(files["png"]["path"]).read_bytes()
        at = raw.find(b"pHYs")
        assert at > 0
        px_per_m, _, unit = struct.unpack(">IIB", raw[at + 4 : at + 13])
        assert unit == 1 and round(px_per_m * 0.0254) == files["png"].get("dpi", 300)

        replay = client.tool("tavotto_verify_replay", {"session_id": sid})
        assert replay["ok"] is True, replay

        assert client.tool("tavotto_close_session", {"session_id": sid})["closed"] is True
    finally:
        client.close()
    assert _sha(figures / "figm.py") == script_hash, "源脚本被改写了"


def test_a_host_authorized_elsewhere_cannot_reach_the_session(unpacked, tmp_path):
    """两个宿主、两个进程、两个授权目录：A 的 session_id 在 B 那里拿不到。会话记录落在同一个
    数据目录也一样——恢复先按**当前连接**的授权校验。隔离的主语是授权目录，不是宿主（下一条）。"""
    a = _figures(tmp_path / "A")
    b_root = tmp_path / "B"
    b_root.mkdir()
    data = tmp_path / "shared-data"
    ca = HostClient(_config(unpacked, "claude-code", a, tmp_path), tmp_path, data)
    cb = HostClient(_config(unpacked, "vscode", b_root, tmp_path), tmp_path, data)
    try:
        sid = ca.tool("tavotto_open_figure", {"project_path": str(a)})["session_id"]
        res = cb.raw_tool("tavotto_session_state", {"session_id": sid})
        assert res.get("isError") is True
        assert res["structuredContent"]["code"] in ("workspace_root_changed", "unknown_session")
        denied = cb.raw_tool("tavotto_open_figure", {"project_path": str(a)})
        assert denied.get("isError") is True
        assert denied["structuredContent"]["code"] == "path_out_of_scope"
    finally:
        ca.close()
        cb.close()


def test_a_second_host_authorized_for_the_same_project_can_resume_the_session(unpacked, tmp_path):
    """反面：两个宿主授权**同一个**项目、共用默认数据目录时，B 能按记录恢复 A 的会话——
    所以文档不许写「session_id 按宿主隔离」（Codex 在 #560 上指出）。"""
    a = _figures(tmp_path / "共享")
    data = tmp_path / "shared-data"
    ca = HostClient(_config(unpacked, "claude-code", a, tmp_path), tmp_path, data)
    cb = HostClient(_config(unpacked, "vscode", a, tmp_path), tmp_path, data)
    try:
        sid = ca.tool("tavotto_open_figure", {"project_path": str(a)})["session_id"]
        state = cb.tool("tavotto_session_state", {"session_id": sid})
        assert state["session_id"] == sid
        assert state.get("restored") is True
    finally:
        ca.close()
        cb.close()
