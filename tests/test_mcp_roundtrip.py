"""Codex MCP server 的**真链路**验收：真 matplotlib、真 stdio、真产物。

`tests/test_mcp_server.py` 用假 worker 盯协议形状；这里盯的是**不变式**：

    hot_apply(canonical_patches)
      == fresh_worker_replay(canonical_patches)
      == writeback_then_reopen(canonical_patches)

MCP 是给引擎新开的一条入口。入口多一条，「热态所见 ≠ 重开后重放」这类事故就多
一条溜进来的路（FigS3 那次文字全体错位就是这个差）。所以这里逐条走一遍，
并且**用与写回事务同一把尺**比 manifest（bbox/anchor 0.5% figure 分数、
size_mm 0.01mm）。

缺 matplotlib 就跳过（.venv 里没有科学栈是常态）。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto.engine import pool as engine_pool  # noqa: E402

SCRIPT = '''\
"""两条曲线 + 图例 + 误差棒：能覆盖大多数可编辑角色。"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).resolve().parent


def main():
    rng = np.random.default_rng(20260818)
    x = np.linspace(0, 10, 12)
    y1 = 1 - np.exp(-x / 3)
    y2 = 1 - np.exp(-x / 6)
    fig, ax = plt.subplots(figsize=(80 / 25.4, 60 / 25.4))
    ax.errorbar(x, y1, yerr=rng.uniform(0.01, 0.04, x.size), marker="o", ms=3,
                lw=1.0, capsize=2, label="Sample A")
    ax.plot(x, y2, ls="--", lw=1.0, marker="s", ms=3, label="Sample B")
    ax.set_xlabel("Time (min)")
    ax.set_ylabel("Conversion (-)")
    ax.set_title("Kinetics")
    ax.legend(loc="lower right", frameon=False)
    ax.tick_params(direction="in")
    fig.tight_layout(pad=0.4)
    fig.savefig(OUT / "FigM.pdf")


if __name__ == "__main__":
    main()
'''

REGISTRY = {"scripts": {"figm.py": {"entry": "main", "cost": "light",
                                    "stems": ["FigM"]}}}

def _gid(manifest: dict, role: str, suffix: str = "") -> str:
    """按角色取一个真实存在的 gid。

    **不硬编码 gid**：`ax.errorbar()` 出来的是 errorbar 组而不是 `lines_0`，
    写死会让用例在「override 没写进去」上失败，而那本该是真缺陷的信号。
    """
    for el in manifest["elements"]:
        if el["role"] == role and (not suffix or el["gid"].endswith(suffix)):
            return el["gid"]
    raise AssertionError(f"manifest 里没有 role={role} 的元素")


def patches_for(manifest: dict) -> list[dict]:
    """覆盖 figure 锚定属性（拖动位置）、几何（子图 position）、样式三类。"""
    return [
        {"gid": _gid(manifest, "title"), "prop": "fontsize", "value": 10.0},
        {"gid": _gid(manifest, "title"), "prop": "pos_frac", "value": [0.35, 0.08]},
        {"gid": _gid(manifest, "axes"), "prop": "position",
         "value": [0.18, 0.2, 0.7, 0.68]},
        {"gid": _gid(manifest, "legend"), "prop": "loc_frac", "value": [0.55, 0.72]},
        {"gid": _gid(manifest, "line"), "prop": "linewidth", "value": 0.75},
        {"gid": _gid(manifest, "errorbar"), "prop": "linewidth", "value": 0.75},
        {"gid": _gid(manifest, "ticks", "xticks"), "prop": "direction", "value": "in"},
        {"gid": _gid(manifest, "axis_label", "xlabel"), "prop": "fontsize", "value": 9.0},
    ]


def _worker_python():
    try:
        return engine_pool.find_worker_python()
    except engine_pool.WorkerError:
        return None


pytestmark = pytest.mark.skipif(_worker_python() is None,
                                reason="没有带科学栈的解释器，跳过真链路用例")


@pytest.fixture
def project(tmp_path):
    figures = tmp_path / "figures"
    figures.mkdir()
    (figures / "figm.py").write_text(SCRIPT, encoding="utf-8")
    (figures / "tavotto_registry.json").write_text(
        json.dumps(REGISTRY, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run([_worker_python(), str(figures / "figm.py")],
                          capture_output=True, text=True, cwd=str(figures))
    assert proc.returncode == 0, proc.stderr
    assert (figures / "FigM.pdf").is_file()
    return figures


class Client:
    """跑一个真的 MCP server 子进程，按行收发 JSON-RPC。"""

    def __init__(self, roots: str, data_dir: str):
        env = {**os.environ,
               "TAVOTTO_MCP_ROOTS": roots,
               "TAVOTTO_DATA_DIR": data_dir,
               "PYTHONPATH": str(ROOT / "src")}
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "codex-plugin" / "mcp" / "server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=str(ROOT))
        self.n = 0

    def call(self, method: str, params=None) -> dict:
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError("server 挂了:\n" +
                                 self.proc.stderr.read().decode("utf-8", "replace")[-4000:])
        return json.loads(line.decode("utf-8"))

    def tool(self, name: str, args: dict) -> dict:
        res = self.call("tools/call", {"name": name, "arguments": args})["result"]
        assert not res.get("isError"), json.dumps(res.get("structuredContent"),
                                                  ensure_ascii=False)[:2000]
        return res["structuredContent"]

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.wait(timeout=120)


@pytest.fixture
def client(project, tmp_path):
    c = Client(str(tmp_path), str(tmp_path / "data"))
    c.call("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                          "clientInfo": {"name": "pytest", "version": "1"}})
    yield c
    c.close()


def test_full_flow_over_real_stdio(client, project, tmp_path):
    """打开 → 改 → 预检 → 导出 → 关。**没有 UI 也能走完**。"""
    opened = client.tool("tavotto_open_figure", {"project_path": str(project)})
    sid = opened["session_id"]
    assert opened["stem"] == "FigM"
    assert opened["registry"]["parameterizable"] is True
    assert opened["svg"].lstrip().startswith("<?xml") or "<svg" in opened["svg"]
    roles = {e["role"] for e in opened["manifest"]["elements"]}
    assert {"axes", "legend", "line", "ticks", "axis_label", "title"} <= roles

    patches = patches_for(opened["manifest"])
    applied = client.tool("tavotto_apply_overrides",
                          {"session_id": sid, "patches": patches})
    assert applied["applied"] == len(patches) and applied["rejected"] == []
    assert applied["warnings"] == [], f"override 没写进去: {applied['warnings']}"

    checks = client.tool("tavotto_preflight", {"session_id": sid})
    assert set(checks["counts"]) == {"error", "warn", "not_verifiable", "suggestion"}
    assert checks["profile"]["profile_id"] == "lab-publication-v1"

    out_dir = tmp_path / "export"
    done = client.tool("tavotto_export",
                       {"session_id": sid, "formats": ["pdf", "png", "svg"],
                        "dpi": 300, "out_dir": str(out_dir),
                        "explicit_confirm": True})
    files = {f["format"]: Path(f["path"]) for f in done["files"]}
    assert all(p.is_file() and p.stat().st_size > 0 for p in files.values())
    assert done["patch_hash"] == applied["patch_hash"]

    # PDF 必须是**真矢量**：文字取得出来才算
    import pymupdf
    with pymupdf.open(files["pdf"]) as doc:
        page = doc[0]
        assert "Time (min)" in page.get_text()
        assert len(page.get_drawings()) > 5
        assert not page.get_images(), "矢量导出里不该有嵌入位图"
        assert abs(page.rect.width / 72 * 25.4 - opened["manifest"]["size_mm"][0]) < 0.5

    # PNG 必须带**明确的 DPI**（pHYs 块）
    raw = files["png"].read_bytes()
    i = raw.find(b"pHYs")
    assert i > 0, "PNG 没写分辨率信息"
    import struct
    px_per_m, _, unit = struct.unpack(">IIB", raw[i + 4:i + 13])
    assert unit == 1 and round(px_per_m * 0.0254) == 300

    proof = json.loads(Path(done["proof_path"]).read_text(encoding="utf-8"))
    assert proof["profile"]["profile_id"] == "lab-publication-v1"
    assert proof["patch_hash"] == applied["patch_hash"]
    # forced 的含义是「**带着阻断项**导出的」，不是「给了 explicit_confirm」。
    # 这张图预检没有阻断项，所以即便给了确认也不该记成强制导出。
    assert proof["forced"] is False and done["forced"] is False
    assert checks["counts"]["error"] == 0

    assert client.tool("tavotto_close_session", {"session_id": sid})["closed"] is True


def test_hot_equals_fresh_worker_replay(client, project):
    """不变式一：热态 == 全新 worker 从零全量重放。"""
    opened = client.tool("tavotto_open_figure", {"project_path": str(project)})
    sid = opened["session_id"]
    client.tool("tavotto_apply_overrides",
                {"session_id": sid, "patches": patches_for(opened["manifest"])})
    verdict = client.tool("tavotto_verify_replay", {"session_id": sid})
    assert verdict["ok"], json.dumps(verdict["divergence"][:8], ensure_ascii=False)
    assert verdict["compared_elements"] > 10
    assert verdict["hot_manifest_hash"] == verdict["fresh_manifest_hash"]


def test_figure_size_change_keeps_frac_anchored_props(client, project):
    """不变式二：figure 尺寸变了之后，figure 锚定属性仍然正确。

    pos_frac / loc_frac 的 setter 在应用那一刻把 figure 分数换算进 artist 本地
    坐标——几何一变就必须重放它们，否则热态 ≠ 全量重放（FigS3 事故）。
    这条经 MCP 入口再走一遍。
    """
    opened = client.tool("tavotto_open_figure", {"project_path": str(project)})
    sid = opened["session_id"]
    patches = [{"gid": "figure", "prop": "size_mm", "value": [120.0, 70.0]},
               *patches_for(opened["manifest"])]
    applied = client.tool("tavotto_apply_overrides", {"session_id": sid, "patches": patches})
    assert applied["manifest"]["size_mm"] == [120.0, 70.0]
    verdict = client.tool("tavotto_verify_replay", {"session_id": sid})
    assert verdict["ok"], json.dumps(verdict["divergence"][:8], ensure_ascii=False)


def test_axes_position_change_keeps_dependent_props(client, project):
    """不变式三：子图几何变了之后，依赖 axes 几何的属性仍然正确。"""
    opened = client.tool("tavotto_open_figure", {"project_path": str(project)})
    sid = opened["session_id"]
    man = opened["manifest"]
    patches = [{"gid": _gid(man, "axes"), "prop": "position",
                "value": [0.25, 0.28, 0.6, 0.6]},
               {"gid": _gid(man, "title"), "prop": "pos_frac", "value": [0.4, 0.06]},
               {"gid": _gid(man, "legend"), "prop": "loc_frac", "value": [0.3, 0.5]}]
    client.tool("tavotto_apply_overrides", {"session_id": sid, "patches": patches})
    verdict = client.tool("tavotto_verify_replay", {"session_id": sid})
    assert verdict["ok"], json.dumps(verdict["divergence"][:8], ensure_ascii=False)


def test_reopening_a_session_replays_to_the_same_place(client, project):
    """不变式四：关掉会话重新打开、重放同一组 patches，结果逐元素一致。

    这是用户真正会做的事（关掉 Codex 明天接着改），也是「重开就变样」这类
    事故最常见的入口。
    """
    first = client.tool("tavotto_open_figure", {"project_path": str(project)})
    patches = patches_for(first["manifest"])
    a = client.tool("tavotto_apply_overrides",
                    {"session_id": first["session_id"], "patches": patches})
    client.tool("tavotto_close_session", {"session_id": first["session_id"]})

    second = client.tool("tavotto_open_figure", {"project_path": str(project)})
    b = client.tool("tavotto_apply_overrides",
                    {"session_id": second["session_id"], "patches": patches})
    assert a["patch_hash"] == b["patch_hash"]

    from tavotto_mcp import bridge
    diffs, compared = bridge.compare_manifests(a["manifest"], b["manifest"])
    assert not diffs, json.dumps(diffs[:8], ensure_ascii=False)
    assert compared > 10
    assert bridge.manifest_hash(a["manifest"]) == bridge.manifest_hash(b["manifest"])


def test_rejected_patches_are_never_silently_dropped(client, project):
    opened = client.tool("tavotto_open_figure", {"project_path": str(project)})
    sid = opened["session_id"]
    body = client.tool("tavotto_apply_overrides", {
        "session_id": sid,
        "patches": [{"gid": _gid(opened["manifest"], "title"),
                     "prop": "fontsize", "value": 10.0},
                    {"gid": "no-such-element", "prop": "fontsize", "value": 10.0}]})
    # 形状合法但元素不存在：**worker 的 warning**，不是 rejected——两者含义不同
    assert body["rejected"] == []
    assert any("no-such-element" in w for w in body["warnings"]), body["warnings"]


def test_export_is_blocked_until_explicitly_confirmed(client, project, tmp_path):
    opened = client.tool("tavotto_open_figure", {"project_path": str(project)})
    sid = opened["session_id"]
    # 把刻度字号压到 6pt：一定撞绝对下限
    client.tool("tavotto_apply_overrides", {
        "session_id": sid,
        "patches": [{"gid": _gid(opened["manifest"], "ticks", "xticks"),
                     "prop": "fontsize", "value": 6.0}]})
    out = tmp_path / "blocked"
    res = client.call("tools/call", {"name": "tavotto_export",
                                     "arguments": {"session_id": sid,
                                                   "formats": ["pdf"],
                                                   "out_dir": str(out)}})["result"]
    assert res["isError"]
    assert res["structuredContent"]["code"] == "preflight_blocked"
    assert not out.exists(), "被阻断时一张图都不该出"

    # 明确确认之后放行，并且**记进 proof**——「这次是带着问题出的」必须留痕
    done = client.tool("tavotto_export", {"session_id": sid, "formats": ["pdf"],
                                          "out_dir": str(out),
                                          "explicit_confirm": True})
    assert done["forced"] is True
    proof = json.loads(Path(done["proof_path"]).read_text(encoding="utf-8"))
    assert proof["forced"] is True and proof["acknowledged"]


def test_open_and_apply_latency_budget(client, project):
    """性能预算（固定 fixture 图，真渲染链路）：

    * open（冷启动：spawn worker + import 科学栈 + 跑脚本）预算 60s——正常
      在几秒内，超一个量级说明冷启动链路坏了（比如字体缓存每次重建）；
    * 热 apply 预算 5s——正常几十到几百 ms，超了说明热会话没被复用。

    实测值随断言打印，进 perf 报告。
    """
    import time as _time
    t0 = _time.monotonic()
    opened = client.tool("tavotto_open_figure", {"project_path": str(project)})
    open_ms = int((_time.monotonic() - t0) * 1000)
    sid = opened["session_id"]

    patches = [{"gid": _gid(opened["manifest"], "title"),
                "prop": "fontsize", "value": 10.0}]
    t1 = _time.monotonic()
    client.tool("tavotto_apply_overrides", {"session_id": sid,
                                            "patches": patches})
    first_apply_ms = int((_time.monotonic() - t1) * 1000)
    t2 = _time.monotonic()
    body = client.tool("tavotto_apply_overrides", {"session_id": sid,
                                                   "patches": patches})
    hot_apply_ms = int((_time.monotonic() - t2) * 1000)
    print(f"\n[perf] open(cold): {open_ms}ms  apply#1: {first_apply_ms}ms  "
          f"apply#2(hot): {hot_apply_ms}ms  worker timings: {body['timings']}")
    assert open_ms < 60_000, f"冷启动 {open_ms}ms 超预算"
    assert hot_apply_ms < 5_000, f"热 apply {hot_apply_ms}ms 超预算——会话没复用？"
    # 热路径不该重跑脚本：worker 计时里没有 script_build（只有 patch/draw）
    assert not body["timings"].get("script_build_ms"), \
        "热 apply 重跑了脚本 build——稳定产物被重复渲染"
