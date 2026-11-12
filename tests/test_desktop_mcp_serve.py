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
import tarfile
import venv
import zipfile
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


# ---------------------------------------------------------------------------
# pip / pipx / uvx 装的 wheel 自带 tavotto_mcp（用户 2026-11-11 同意；1.0 豁免 §1.8）
# ---------------------------------------------------------------------------


def _pyproject() -> dict:
    tomllib = pytest.importorskip("tomllib", reason="tomllib 需要 Python 3.11+")
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_force_includes_only_the_mcp_server_package_into_the_wheel():
    """wheel 的 `force-include` 只点名 `tavotto_mcp` 这一个目录，映射成顶层包；全局 exclude 继续挡着插件其余部分。

    sdist 要带同一个目录：`python -m build` 先打 sdist 再从它构建 wheel，wheel 的 force-include 在 sdist 里
    找不到源目录就整个构建失败。
    """
    build = _pyproject()["tool"]["hatch"]["build"]
    wheel = build["targets"]["wheel"]["force-include"]
    assert wheel == {"codex-plugin/mcp/tavotto_mcp": "tavotto_mcp"}
    assert build["targets"]["sdist"]["force-include"] == {
        "codex-plugin/mcp/tavotto_mcp": "codex-plugin/mcp/tavotto_mcp"
    }
    assert "codex-plugin" in build["exclude"] and "codex-plugin/**" in build["exclude"]
    assert (ROOT / "codex-plugin" / "mcp" / "tavotto_mcp" / "server.py").is_file()


def _dist_artifact(pattern: str) -> Path | None:
    d = (
        Path(os.environ["TAVOTTO_DIST_DIR"])
        if os.environ.get("TAVOTTO_DIST_DIR")
        else ROOT / "dist"
    )
    files = sorted(d.glob(pattern), key=lambda p: p.stat().st_mtime) if d.is_dir() else []
    return files[-1] if files else None


WHEEL = _dist_artifact("tavotto-*.whl")
SDIST = _dist_artifact("tavotto-*.tar.gz")
_NEED_DIST = "先 python -m build（或设 TAVOTTO_DIST_DIR）——这几条读的是产物不是源码树"


@pytest.mark.skipif(WHEEL is None, reason=_NEED_DIST)
def test_wheel_ships_the_mcp_server_package_and_nothing_else_of_the_plugin():
    with zipfile.ZipFile(WHEEL) as z:
        names = z.namelist()
    top = {n.split("/")[0] for n in names}
    assert top == {"tavotto", "tavotto_mcp"} | {n for n in top if n.endswith(".dist-info")}, top
    assert "tavotto_mcp/server.py" in names
    expected = {
        f"tavotto_mcp/{p.name}"
        for p in (ROOT / "codex-plugin" / "mcp" / "tavotto_mcp").glob("*.py")
    }
    shipped = {n for n in names if n.startswith("tavotto_mcp/")}
    assert shipped == expected, {"missing": expected - shipped, "extra": shipped - expected}
    # 插件的其它东西一律不进 pip 包（技能、启动器、市场清单、画布产物……）
    for n in names:
        assert not n.startswith(("codex-plugin/", ".agents/", ".codex-plugin/", "widget/")), n
        assert "canvas.html" not in n and "__pycache__" not in n, n


@pytest.mark.skipif(SDIST is None, reason=_NEED_DIST)
def test_sdist_carries_only_the_mcp_server_package_of_the_plugin():
    with tarfile.open(SDIST) as t:
        names = t.getnames()
    prefix = names[0].split("/")[0]
    rel = [n[len(prefix) + 1 :] for n in names if n.startswith(prefix + "/codex-plugin")]
    assert rel, "sdist 里没有 codex-plugin/mcp/tavotto_mcp：从 sdist 构建 wheel 会失败"
    assert all(
        n.startswith("codex-plugin/mcp/tavotto_mcp") for n in rel if "." in n.rsplit("/", 1)[-1]
    ), rel
    assert "codex-plugin/mcp/tavotto_mcp/server.py" in rel


@pytest.mark.skipif(WHEEL is None, reason=_NEED_DIST)
def test_installed_wheel_runs_tavotto_mcp_over_clean_stdio_without_the_source_tree(tmp_path):
    """真装：临时 venv 里 `pip install --no-deps <wheel>`，在**源码树之外**起 `tavotto mcp`，
    握手 + tools/list；stdout 第一个字节就是 `{`、每行都是 JSON-RPC（stdout 首帧前零字节）。

    不带依赖：握手与列工具只要引擎的纯标准库部分；这同时证明了「没装 `[worker]` 也能起服务」。
    """
    env_dir = tmp_path / "venv"
    venv.create(env_dir, with_pip=True)
    bindir = env_dir / ("Scripts" if os.name == "nt" else "bin")
    py = bindir / ("python.exe" if os.name == "nt" else "python")
    subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", "--no-deps", "--no-input", str(WHEEL)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    # 装好的包就是产物那一份，不是源码树
    where = subprocess.run(
        [str(py), "-c", "import tavotto_mcp; print(tavotto_mcp.__file__)"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(tmp_path),
        check=True,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
    ).stdout.strip()
    assert "site-packages" in where, where
    msgs = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "wheel-smoke", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("TAVOTTO_", "MM_", "PYTHONPATH"))
    }
    env.update(
        TAVOTTO_DATA_DIR=str(tmp_path / "data"),
        TAVOTTO_CONFIG_DIR=str(tmp_path / "config"),
        TAVOTTO_NO_TELEMETRY="1",
        PYTHONIOENCODING="utf-8",
    )
    work = tmp_path / "work"
    work.mkdir()
    proc = subprocess.run(
        [str(py), "-m", "tavotto", "mcp"],
        input="".join(json.dumps(m) + "\n" for m in msgs).encode("utf-8"),
        capture_output=True,
        env=env,
        cwd=str(work),
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.startswith(b'{"jsonrpc"'), proc.stdout[:80]
    replies = [
        json.loads(line) for line in proc.stdout.decode("utf-8").splitlines() if line.strip()
    ]
    assert all(m.get("jsonrpc") == "2.0" for m in replies)
    by_id = {m.get("id"): m for m in replies}
    assert by_id[1]["result"]["serverInfo"]["name"] == mcp_server.SERVER_NAME
    assert {t["name"] for t in by_id[2]["result"]["tools"]} == set(mcp_server.HANDLERS)
    # wheel 里没有画布产物：工具不挂 `_meta.ui`（如实降级，不让宿主对着白框等）
    assert not any("_meta" in t for t in by_id[2]["result"]["tools"])
