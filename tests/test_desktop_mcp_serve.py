"""`tavotto mcp`：在进程内跑插件那一份 MCP 服务端（「接入 AI 编辑器」，用户 2026-10-27）。

主语：**宿主按配置起的那个进程**——`<tavotto-cli> mcp`（这里用源码树的 `python -m tavotto mcp` 代它）；
它的 stdout 是不是只有 JSON-RPC、工具是不是插件那一份、`tavotto_health` 能不能真的答。冻结产物那一侧由
`packaging/tavotto.spec` 的收集与 `scripts/smoke_app.py --exe` 的握手看护（下面两条）。
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto.engine import cli as engine_cli  # noqa: E402, I001
from tavotto.engine import mcpserve  # noqa: E402
from tavotto_mcp import server as mcp_server  # noqa: E402

PROTOCOL = "2025-06-18"


def _serve(tmp_path: Path, calls: list[dict]) -> tuple[list[dict], str, int]:
    """像宿主那样起 `tavotto mcp`：握手 + 列工具 + 逐个调用，stdin 关掉即收摊。"""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("TAVOTTO_", "MM_", "PYTHONPATH"))
    }
    env.update(
        PYTHONPATH=str(ROOT / "src"),
        TAVOTTO_DATA_DIR=str(tmp_path / "data"),
        TAVOTTO_CONFIG_DIR=str(tmp_path / "config"),
        TAVOTTO_MCP_ROOTS=str(project),
        TAVOTTO_NO_TELEMETRY="1",
        PYTHONIOENCODING="utf-8",
    )
    msgs = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "mcp-serve-test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    for i, call in enumerate(calls):
        msgs.append({"jsonrpc": "2.0", "id": 10 + i, "method": "tools/call", "params": call})
    proc = subprocess.run(
        [sys.executable, "-m", "tavotto", "mcp"],
        input="".join(json.dumps(m) + "\n" for m in msgs),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(project),
        timeout=300,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return [json.loads(line) for line in lines], proc.stderr, proc.returncode


def test_tavotto_mcp_serves_the_plugin_tools_over_clean_stdio(tmp_path):
    out, err, rc = _serve(tmp_path, [{"name": "tavotto_health", "arguments": {}}])
    assert rc == 0, err[-2000:]
    by_id = {m.get("id"): m for m in out}
    # stdout 每一行都是 JSON-RPC（json.loads 没抛），且都是对请求的回答
    assert all(m.get("jsonrpc") == "2.0" for m in out)
    assert by_id[1]["result"]["serverInfo"]["name"] == mcp_server.SERVER_NAME
    listed = {t["name"] for t in by_id[2]["result"]["tools"]}
    assert listed == set(mcp_server.HANDLERS)  # 插件那一份，一个不多一个不少（不是降级 server）
    health = by_id[10]["result"]
    assert not health.get("isError"), health
    text = json.dumps(health, ensure_ascii=False)
    assert "tavotto" in text.lower()


def test_tavotto_mcp_is_dispatched_before_the_app_imports():
    assert "mcp" in engine_cli.COMMANDS


def test_without_the_server_package_it_says_so_on_stderr_and_never_touches_stdout(
    monkeypatch, capsys
):
    def missing():
        raise ImportError("No module named 'tavotto_mcp'")

    monkeypatch.setattr(mcpserve, "_import_server", missing)
    assert mcpserve.cli([]) == 2
    cap = capsys.readouterr()
    assert cap.out == ""  # stdout 归协议：宿主那边读到的是「没有回答」，而不是一行非 JSON
    assert json.loads(cap.err)["error_code"] == mcpserve.NOT_BUNDLED


def _analysis_hiddenimports(spec: Path) -> list[ast.AST]:
    tree = ast.parse(spec.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Analysis":
            for kw in node.keywords:
                if kw.arg == "hiddenimports":
                    return list(kw.value.elts)
    raise AssertionError("tavotto.spec 里没找到 Analysis(hiddenimports=…)")


def test_frozen_build_collects_the_mcp_server_package():
    """冻结产物里 mcpserve 运行时才 import tavotto_mcp，静态分析看不见：spec 必须点名收整包。"""
    spec = ROOT / "packaging" / "tavotto.spec"
    starred = {
        e.value.id
        for e in _analysis_hiddenimports(spec)
        if isinstance(e, ast.Starred) and isinstance(e.value, ast.Name)
    }
    assert "MCP_SERVER_MODULES" in starred
    tree = ast.parse(spec.read_text(encoding="utf-8"))
    collected = [
        n.value.args[0].value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", None) == "MCP_SERVER_MODULES" for t in n.targets)
        and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "id", None) == "collect_submodules"
    ]
    assert collected == ["tavotto_mcp"]


@pytest.mark.parametrize("name", ["--help", "-h"])
def test_help_goes_to_stderr(name, capsys):
    assert mcpserve.cli([name]) == 0
    cap = capsys.readouterr()
    assert cap.out == "" and "tavotto mcp" in cap.err


def test_smoke_resolves_the_frozen_cli_before_the_handshake_changes_directory(
    tmp_path, monkeypatch
):
    """CI 传相对的 `--exe dist/Tavotto/Tavotto`，握手的 cwd 是示例项目：CLI 路径必须先变成绝对的（Codex #1451 P1）。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    import smoke_app

    monkeypatch.chdir(tmp_path)
    for exe in ("dist/Tavotto/Tavotto", "dist/Tavotto/Tavotto.exe"):
        cli = smoke_app.frozen_cli_for(exe)
        assert cli.is_absolute()
        assert cli.parent == (tmp_path / "dist" / "Tavotto").resolve()
        assert cli.name == "tavotto-cli" + Path(exe).suffix


# ----------------------------------------------------------------------------- 画布随包（MCP App UI）
def test_frozen_build_bundles_the_canvas_where_the_server_looks_for_it():
    """`tavotto_mcp/widget.py` 按 `<tavotto_mcp 的上一级>/widget/canvas.html` 找画布；冻结后那一级是 _MEIPASS，
    所以 spec 必须把它收到 `widget/`，且缺了 / 过期就拒绝打包（不进库，ADR 0043）。"""
    from tavotto_mcp import widget

    pkg_parent = Path(widget.__file__).resolve().parent.parent
    assert widget._DEFAULT.relative_to(pkg_parent) == Path("widget") / "canvas.html"
    tree = ast.parse((ROOT / "packaging" / "tavotto.spec").read_text(encoding="utf-8"))
    appended = [
        n.args[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "append"
        and getattr(n.func.value, "id", None) == "datas"
        and n.args
        and isinstance(n.args[0], ast.Tuple)
    ]
    widget_entries = [
        t for t in appended if isinstance(t.elts[1], ast.Constant) and t.elts[1].value == "widget"
    ]
    assert len(widget_entries) == 1
    assert ast.unparse(widget_entries[0].elts[0]) == "str(MCP_WIDGET)"
    src = (ROOT / "packaging" / "tavotto.spec").read_text(encoding="utf-8")
    gate = src[src.index("if not MCP_WIDGET.is_file()") :]
    assert "current_fingerprint(MCP_WIDGET) != source_fingerprint()" in gate.splitlines()[0]
    assert "raise SystemExit" in gate[:400]
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_mcp_widget

    assert build_mcp_widget.OUT == widget._DEFAULT  # spec 收的就是服务端源码态找的那一份


def step_commands(text: str) -> dict[str, list[str]]:
    """workflow 里每个 job 的各 step 实际执行的 `run` 命令（按顺序）。**注释行不算**（Codex #1482 P1：子串位置判不出
    一步被注释掉了）。不引 PyYAML（测试依赖里没有）：只认本仓库 workflow 的两种写法——`run: <命令>` 与
    `run: |` 后面缩进更深的块。"""
    jobs: dict[str, list[str]] = {}
    lines = text.split("\n")
    body = lines[lines.index("jobs:") + 1 :]
    job = None
    i = 0
    while i < len(body):
        line = body[i]
        i += 1
        if line.lstrip().startswith("#") or not line.strip():
            continue
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if m:
            job = jobs.setdefault(m.group(1), [])
            continue
        m = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", line)
        if job is None or not m:
            continue
        if m.group(2) not in ("|", ">", "|-", ">-"):
            job.append(m.group(2))
            continue
        indent = len(m.group(1))
        block = []
        while i < len(body) and (
            not body[i].strip() or len(body[i]) - len(body[i].lstrip()) > indent
        ):
            if not body[i].lstrip().startswith("#"):
                block.append(body[i].strip())
            i += 1
        job.append("\n".join(block))
    return jobs


def _step_commands(workflow: str) -> dict[str, list[str]]:
    return step_commands((ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))


def _first(cmds: list[str], needle: str) -> int:
    return next((i for i, c in enumerate(cmds) if needle in c), -1)


def freeze_jobs_without_canvas(jobs: dict[str, list[str]]) -> list[str]:
    """跑 PyInstaller 打 sidecar、却没有在它之前的 step 里构建画布的 job。"""
    bad = []
    for name, cmds in jobs.items():
        frozen = _first(cmds, "pyinstaller packaging/tavotto.spec")
        if frozen >= 0 and not 0 <= _first(cmds, "scripts/build_mcp_widget.py") < frozen:
            bad.append(name)
    return bad


@pytest.mark.parametrize("workflow", ["ci.yml", "nightly.yml"])
def test_every_job_that_freezes_the_sidecar_builds_the_canvas_first(workflow):
    jobs = _step_commands(workflow)
    assert any("pyinstaller packaging/tavotto.spec" in c for cmds in jobs.values() for c in cmds), (
        f"{workflow} 里没找到跑 PyInstaller 的 step——判据空了"
    )
    assert freeze_jobs_without_canvas(jobs) == []


def test_the_workflow_gate_catches_a_missing_or_commented_out_canvas_step():
    """反证：画布那一步删掉、或只剩在注释里，判据都要报出来。"""
    good = {"j": ["python scripts/build_mcp_widget.py", "pyinstaller packaging/tavotto.spec"]}
    assert freeze_jobs_without_canvas(good) == []
    assert freeze_jobs_without_canvas({"j": ["", "pyinstaller packaging/tavotto.spec"]}) == ["j"]
    late = {"j": ["pyinstaller packaging/tavotto.spec", "python scripts/build_mcp_widget.py"]}
    assert freeze_jobs_without_canvas(late) == ["j"]
    commented = step_commands(
        "on: push\njobs:\n  j:\n    steps:\n"
        "      # - run: python scripts/build_mcp_widget.py\n"
        "      - name: 打包\n"
        "        run: |\n"
        "          # python scripts/build_mcp_widget.py\n"
        "          pyinstaller packaging/tavotto.spec --noconfirm\n"
    )
    assert freeze_jobs_without_canvas(commented) == ["j"]
    live = step_commands(
        "on: push\njobs:\n  j:\n    steps:\n"
        "      - run: python scripts/build_mcp_widget.py\n"
        "      - run: pyinstaller packaging/tavotto.spec --noconfirm\n"
    )
    assert freeze_jobs_without_canvas(live) == []


def _run_targets(fn: ast.FunctionDef) -> list[str]:
    """`main()` **最外层**一定会执行的 `run([...])` 语句，按顺序，各取参数里的字符串常量拼起来。
    只看顶层语句：注释不进 AST；`if` 分支（含 `if False:` 死分支）与内嵌函数里的调用都不算——它们不一定执行
    （Codex #1482 两轮 P1）。build_desktop 的构建步骤本来就都写在 main 的顶层。"""
    out = []
    for stmt in fn.body:
        call = stmt.value if isinstance(stmt, ast.Expr) else None
        if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "run" and call.args:
            consts = [n.value for n in ast.walk(call.args[0]) if isinstance(n, ast.Constant)]
            out.append(" ".join(str(c) for c in consts))
    return out


def canvas_before_pyinstaller(src: str) -> bool:
    tree = ast.parse(src)
    (main,) = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    runs = _run_targets(main)
    canvas = _first(runs, "build_mcp_widget.py")
    frozen = _first(runs, "PyInstaller")
    return 0 <= canvas < frozen


def test_build_desktop_builds_the_canvas_before_pyinstaller():
    src = (ROOT / "scripts" / "build_desktop.py").read_text(encoding="utf-8")
    assert canvas_before_pyinstaller(src)
    # 反证：把构建画布那一行注释掉 / 删掉，判据必须翻红
    line = next(ln for ln in src.splitlines() if '"build_mcp_widget.py"' in ln)
    assert not canvas_before_pyinstaller(src.replace(line, "    # " + line.strip()))
    assert not canvas_before_pyinstaller(src.replace(line, ""))
    # 挪进死分支、或挪进一个没人调用的内嵌函数：都不一定执行，也必须翻红
    assert not canvas_before_pyinstaller(src.replace(line, "    if False:\n    " + line))
    assert not canvas_before_pyinstaller(src.replace(line, "    def _later():\n    " + line))


@pytest.mark.parametrize(
    "result, available",
    [
        ({"structuredContent": {"canvas": {"available": True}}}, True),
        (
            {"content": [{"type": "text", "text": json.dumps({"canvas": {"available": False}})}]},
            False,
        ),
        ({"content": [{"type": "text", "text": "not json"}]}, None),
        ({}, None),
    ],
)
def test_smoke_reads_the_canvas_section_of_the_health_result(result, available):
    sys.path.insert(0, str(ROOT / "scripts"))
    import smoke_app

    assert smoke_app.health_canvas(result).get("available") is available
