"""MCP 启动器（codex-plugin/mcp/server.py）的运行时解析与降级模式。

盯的是 2026-08-20 撞到的那组事：

* Codex 配置里的 `python3` 是 Homebrew 的、import 不到 tavotto，而机器上明明
  有能用的引擎（worker 解释器 / 设置里指定的 / 插件自管 venv）——resolver
  必须把这几条都走到，且**每一条都要真的验证过 `import tavotto.engine`**；
* frozen 的 `tavotto-cli` 永远不能被当成解释器；
* 找不到引擎时的降级 server **不许把六个工具伪装成可用**（tools/list 只列
  真的能用的 `tavotto_health`），更不许返回「画布已打开」；
* `--health` / `--provision` 是可执行的诊断与自建入口，输出结构化 JSON。
"""

import importlib
import io
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "codex-plugin"
sys.path.insert(0, str(PLUGIN / "mcp"))

launcher = importlib.import_module("server")


def _touch_exe(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


@pytest.fixture()
def no_path_pythons(tmp_path, monkeypatch):
    """PATH 里没有任何 python：候选链的 PATH 兜底被清空，测试才可控。"""
    empty = tmp_path / "empty-bin"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty))
    for name in ("TAVOTTO_MCP_PYTHON", "TAVOTTO_WORKER_PYTHON", "MM_WORKER_PYTHON"):
        monkeypatch.delenv(name, raising=False)
    return empty


# ------------------------------ 候选链次序 ---------------------------------
def test_candidate_priority_order(tmp_path, monkeypatch, no_path_pythons):
    """显式 > worker env > 设置 > 自管 venv > 从 CLI 反推。"""
    mcp_py = _touch_exe(tmp_path / "a" / "python3")
    worker_py = _touch_exe(tmp_path / "b" / "python3")
    cfg_py = _touch_exe(tmp_path / "c" / "python3")
    monkeypatch.setenv("TAVOTTO_MCP_PYTHON", mcp_py)
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", worker_py)
    cfg_dir = Path(os.environ["TAVOTTO_CONFIG_DIR"])
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"worker": {"python": cfg_py}}), encoding="utf-8"
    )

    shim = tmp_path / "bin" / "tavotto"
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text(f"#!{sys.executable}\nprint(1)\n", encoding="utf-8")
    shim.chmod(0o755)

    cands = launcher.resolver_candidates({"cmd": [str(shim)]})
    sources = [s for _, s in cands]
    assert sources[:4] == ["mcp_env", "worker_env", "configured", "managed"]
    assert "discovered" in sources[4:]
    by_source = dict((s, p) for p, s in cands)
    assert by_source["mcp_env"] == mcp_py
    assert by_source["worker_env"] == worker_py
    assert by_source["configured"] == cfg_py
    assert by_source["managed"] == launcher.managed_python()


def test_resolve_takes_the_first_candidate_that_actually_imports(
    tmp_path, monkeypatch, no_path_pythons
):
    """存在但 import 不了的候选要被**验证淘汰**，不是「找到文件就算数」。"""
    bad = _touch_exe(tmp_path / "bad" / "python3")
    good = _touch_exe(tmp_path / "good" / "python3")
    monkeypatch.setenv("TAVOTTO_MCP_PYTHON", bad)
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", good)
    monkeypatch.setattr(launcher, "_importable", lambda p, **kw: p == good)

    out = launcher.resolve({"cmd": None})
    assert out["python"] == good and out["source"] == "worker_env"
    tried = {t["source"]: t for t in out["tried"]}
    assert tried["mcp_env"]["importable"] is False
    assert tried["worker_env"]["importable"] is True


def test_frozen_cli_is_never_offered_as_an_interpreter(tmp_path, monkeypatch, no_path_pythons):
    """桌面版的 frozen CLI（ELF/PE 头、无 shebang、旁边无 python）出不了候选。"""
    frozen = tmp_path / "sidecar" / "tavotto-cli"
    frozen.parent.mkdir(parents=True)
    frozen.write_bytes(b"\x7fELF\x02\x01\x01\x00")
    frozen.chmod(0o755)

    cands = launcher.resolver_candidates({"cmd": [str(frozen)]})
    assert str(frozen) not in [p for p, _ in cands]
    monkeypatch.setattr(
        launcher,
        "_importable",
        lambda p, **kw: pytest.fail("不存在的候选不该被探测") if not os.path.isfile(p) else False,
    )
    out = launcher.resolve({"cmd": [str(frozen)]})
    assert out["python"] is None


def test_managed_runtime_wins_over_discovered(tmp_path, monkeypatch, no_path_pythons):
    """`--provision` 建出来的自管 venv 排在「从 CLI 反推 / PATH」之前。"""
    managed = Path(launcher.managed_python())
    _touch_exe(managed)
    path_py = _touch_exe(tmp_path / "pathbin" / "python3")
    monkeypatch.setenv("PATH", str(Path(path_py).parent))
    monkeypatch.setattr(launcher, "_importable", lambda p, **kw: True)

    out = launcher.resolve({"cmd": None})
    assert out["source"] == "managed"
    assert os.path.realpath(out["python"]) == os.path.realpath(str(managed))


def test_a_venv_symlink_to_the_current_interpreter_is_still_probed(
    tmp_path, monkeypatch, no_path_pythons
):
    """**2026-08-20 实测回归**：venv 的 `bin/python3` 是指向基础解释器的符号
    链接。按 realpath 判「就是当前解释器」会把刚 provision 好的自管环境
    跳过不探测——provision 刚报成功，server 转头就降级。身份必须按调用
    路径算：链接到同一个二进制的 venv 是另一个解释器。"""
    managed = Path(launcher.managed_python())
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.symlink_to(sys.executable)  # 与真实 venv 一模一样的形状
    probed = []

    def fake_importable(p, **kw):
        probed.append(p)
        return True

    monkeypatch.setattr(launcher, "_importable", fake_importable)
    out = launcher.resolve({"cmd": None})
    assert str(managed) in probed, "realpath 又把 venv 符号链接当成了当前解释器"
    assert out["source"] == "managed" and out["python"] == str(managed)


def test_explicit_override_that_fails_is_engine_unavailable(tmp_path):
    """用户显式指的解释器用不了 → `engine_unavailable`，指名道姓，
    绝不静默落回「桌面版 / 没装」那两格。"""
    bad = _touch_exe(tmp_path / "bad" / "python3")
    resolution = {
        "python": None,
        "source": None,
        "tried": [
            {"python": bad, "source": "mcp_env", "exists": True, "importable": False, "ms": 1}
        ],
    }
    code, hint = launcher.diagnose_resolved({"cmd": None, "desktop": None}, resolution)
    assert code == "engine_unavailable"
    assert "TAVOTTO_MCP_PYTHON" in hint and bad in hint


def test_diagnose_without_override_keeps_the_three_states():
    resolution = {"python": None, "source": None, "tried": []}
    code, _ = launcher.diagnose_resolved(
        {"cmd": ["/x/tavotto-cli"], "desktop": "/x/Tavotto"}, resolution
    )
    assert code == "desktop_only"
    code, _ = launcher.diagnose_resolved({"cmd": None, "desktop": "/x"}, resolution)
    assert code == "desktop_found_cli_missing"
    code, _ = launcher.diagnose_resolved({"cmd": None, "desktop": None}, resolution)
    assert code == "tavotto_missing"


def test_bridge_import_probe_matches_the_bridge():
    """**2026-08-20 实测回归**：PyPI 的 0.8.0 wheel 发在 telemetry 合并之前，
    `import tavotto.engine` 过了、bridge 一 import 就炸——resolver 交棒过去
    server 当场崩死。探测语句必须覆盖 bridge 真正 import 的整组模块，
    两侧对拍，改 bridge 的 import 必须同步 `_BRIDGE_IMPORT`。"""
    import ast

    src = (PLUGIN / "mcp" / "tavotto_mcp" / "bridge.py").read_text(encoding="utf-8")
    bridge_imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module == "tavotto.engine":
            bridge_imports |= {a.name for a in node.names}
    probe = launcher._BRIDGE_IMPORT
    assert probe.startswith("from tavotto.engine import ")
    probed = {n.strip() for n in probe.removeprefix("from tavotto.engine import ").split(",")}
    assert bridge_imports, "bridge.py 里没找到 tavotto.engine 的 import？"
    assert bridge_imports <= probed, (
        f"bridge 需要但探测没验的模块: {sorted(bridge_imports - probed)}"
        "——放过它们的下场是交棒后崩死"
    )


# ------------------------------ 降级 server --------------------------------
def _degraded_roundtrip(*requests, code="desktop_only"):
    lines = "".join(json.dumps(r) + "\n" for r in requests)
    out = io.StringIO()
    launcher._degraded_server(
        code,
        launcher.DESKTOP_ONLY_HINT,
        {"python": None, "source": None, "tried": []},
        stdin=io.StringIO(lines),
        stdout=out,
    )
    return [json.loads(ln) for ln in out.getvalue().strip().splitlines()]


def test_degraded_initialize_is_version_zero():
    (res,) = _degraded_roundtrip(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )
    info = res["result"]["serverInfo"]
    assert info["version"] == "0"  # 健康的 server 报 tavotto 版本号
    assert "desktop_only" in res["result"]["instructions"]


def test_degraded_tools_list_only_offers_the_health_tool():
    """**不把不可用的工具伪装成可用**：六个正常工具不进 tools/list。"""
    (res,) = _degraded_roundtrip({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = [t["name"] for t in res["result"]["tools"]]
    assert names == ["tavotto_health"]


def test_degraded_normal_tool_calls_are_structured_errors():
    (res,) = _degraded_roundtrip(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "tavotto_open_figure", "arguments": {"script_path": "/x/fig.py"}},
        }
    )
    result = res["result"]
    assert result["isError"] is True
    body = result["structuredContent"]
    assert body["ok"] is False and body["code"] == "desktop_only"
    assert body["canvas"]["available"] is False
    assert body["recovery"], "错误必须带恢复步骤"
    text = result["content"][0]["text"]
    assert "已打开" not in text and "已就绪" not in text
    # 降级 server 是同一形状的第二个消费点：code 只进 structuredContent
    assert body["code"] not in text
    assert all(step in text for step in body["recovery"])


def test_degraded_refresh_tool_is_a_structured_error_too():
    """(14) 旧会话里模型记住的 `tavotto_refresh_project`：降级 server 回结构化
    错误 + 恢复步骤，不是 method_not_found，也不伪装成刷新成功。"""
    assert "tavotto_refresh_project" in launcher.NORMAL_TOOLS
    (res,) = _degraded_roundtrip(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "tavotto_refresh_project", "arguments": {}},
        }
    )
    result = res["result"]
    assert result["isError"] is True
    body = result["structuredContent"]
    assert body["ok"] is False and body["code"] == "desktop_only" and body["recovery"]
    assert "已刷新" not in result["content"][0]["text"]


def test_degraded_health_tool_reports_the_gap_without_pretending():
    (res,) = _degraded_roundtrip(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "tavotto_health", "arguments": {}},
        }
    )
    result = res["result"]
    assert not result.get("isError")  # 体检本身成功，体检结论是不健康
    body = result["structuredContent"]
    assert body["engine"]["available"] is False
    assert body["canvas"]["available"] is False
    assert any("新开" in step for step in body["recovery"])


def test_degraded_server_declares_no_resources():
    """没有引擎就没有画布：声明资源 = 给 host 一个白框。"""
    (res,) = _degraded_roundtrip({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
    assert res["result"]["resources"] == []


# ----------------------------- health / provision ---------------------------
def test_health_in_an_engine_environment(capsys):
    """本测试进程（.venv）import 得到 tavotto：health 报 engine 模式。"""
    report, rc = launcher.health()
    assert rc == 0 and report["ok"] is True and report["mode"] == "engine"
    assert report["engine_version"] not in (None, "0")
    assert any("新开一次会话" in n or "新开" in n for n in report["notes"])
    assert report["timings"]["health_ms"] < 5000, "体检要快，它是出图前的门槛"
    # 画布不再随仓库提交（ADR 0043）：体检报的 available 必须如实反映磁盘上有没有那份产物
    canvas = PLUGIN / "mcp" / "widget" / "canvas.html"
    assert report["widget"]["available"] is (canvas.is_file() and canvas.stat().st_size > 0)


def test_plugin_version_is_read_from_the_manifest():
    manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert launcher._plugin_version() == manifest["version"]


def test_provision_pins_the_plugin_version_and_verifies(tmp_path, monkeypatch):
    """默认装 `tavotto[worker]==<插件版本>`（钉版本可复现；`[worker]` 自带
    渲染栈——pip 形态发现不了桌面 App 的内置 runtime），装完必须验证过 import。"""
    ran = []

    def fake_run(argv, **kw):
        ran.append(list(argv))
        if argv[1:3] == ["-m", "venv"]:
            Path(launcher.managed_python()).parent.mkdir(parents=True, exist_ok=True)
            Path(launcher.managed_python()).write_text("", encoding="utf-8")

        class R:
            returncode = 0
            stdout = stderr = ""

        return R()

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    monkeypatch.setattr(launcher, "_importable", lambda p, **kw: True)
    report, rc = launcher.provision()
    assert rc == 0 and report["ok"] is True
    assert report["spec"] == f"tavotto[worker]=={launcher._plugin_version()}"
    pip_call = next(c for c in ran if "pip" in c)
    assert report["spec"] in pip_call
    # 只写自管目录，不碰任何全局环境
    assert report["python"].startswith(launcher.managed_runtime_dir())


def test_provision_failure_is_structured(tmp_path, monkeypatch):
    def fake_run(argv, **kw):
        class R:
            returncode = 1
            stdout = ""
            stderr = "no network"

        return R()

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    report, rc = launcher.provision()
    assert rc == 1 and report["ok"] is False
    assert report["code"] == "provision_failed"


def test_provision_half_built_env_is_not_reported_as_success(monkeypatch):
    """pip 说成了、import 却失败（半成品环境）——不许报 ok。"""

    def fake_run(argv, **kw):
        if argv[1:3] == ["-m", "venv"]:
            Path(launcher.managed_python()).parent.mkdir(parents=True, exist_ok=True)
            Path(launcher.managed_python()).write_text("", encoding="utf-8")

        class R:
            returncode = 0
            stdout = stderr = ""

        return R()

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    monkeypatch.setattr(launcher, "_importable", lambda p, **kw: False)
    report, rc = launcher.provision()
    assert rc == 1 and report["ok"] is False
