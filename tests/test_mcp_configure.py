"""非 Codex 宿主的接入入口 `codex-plugin/integrations/configure.py`（PR 1：宿主无关的基础）。

判据的主语：

* **一份解包到源码树之外的完整包**——不是源码 checkout。configure 从它自己的位置
  找启动器；生成的配置在随机 cwd、剥掉 PYTHONPATH 的环境里真的能起 server、握手、
  列工具、调 `tavotto_health`，而且 health 报的包目录就是那份解包目录（旁边刚好有
  源码也掩盖不了漏打包）。
* **生成器只打印**：stdout 只有配置（可解析），说明只在 stderr；不写任何文件。
* **授权只来自用户选的目录**：文件系统根 / HOME / 包目录 / 相对路径 / `~` 一律拒绝。
* **三个解释器分开**：启动器起不来是硬失败；引擎只在当前 shell 环境里找得到时钉进
  `TAVOTTO_MCP_PYTHON`，且钉住后在最小环境里再验；引擎哪儿都没有时照样出配置
  （降级 server 会在宿主里说缺什么），stderr 给的恢复命令是真实绝对路径。

各宿主 schema 的逐家对拍在 tests/test_mcp_host_profiles.py。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from tests.support import pluginkit as kit

ROOT = kit.ROOT
SOURCE_CONFIGURE = ROOT / "codex-plugin" / "integrations" / "configure.py"
PROTOCOL = "2025-11-25"


def _load(path: Path, name: str = "_tavotto_configure_under_test"):
    spec = importlib.util.spec_from_file_location(f"{name}_{abs(hash(str(path)))}", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def unpacked(tmp_path_factory) -> Path:
    """合成 staging → 确定性 zip → 解包到一个带空格与中文、与仓库无关的目录。"""
    stage = kit.load_script("plugin_stage")
    base = tmp_path_factory.mktemp("发行 包")
    kit.synthetic_staging(base / "stage")
    archive = stage.write_zip(base / "stage", base / "codex-plugin-test.zip")
    plugin = stage.unpack_zip(archive, base / "解包 目录")
    assert not str(plugin).startswith(str(ROOT))
    return plugin


@pytest.fixture()
def project(tmp_path) -> Path:
    p = tmp_path / "我的 论文" / "figures"
    p.mkdir(parents=True)
    return p


def _minimal_path() -> str:
    """GUI 宿主那种最短的 PATH。**Windows 上也不能沿用 os.environ 的 PATH**：CI runner 的 PATH
    里就有装着 tavotto 的 hostedtoolcache python，「哪儿都没有引擎」的前提在那儿不成立，
    configure 的完整环境回退会（正确地）找到并钉住它（#559 的 Windows CI）。"""
    if os.name == "nt":
        windir = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT") or r"C:\Windows"
        return os.pathsep.join([os.path.join(windir, "System32"), windir])
    return os.pathsep.join(["/usr/bin", "/bin"])


def _clean_env(tmp_path: Path, **extra: str) -> dict:
    """宿主会给的那种最小环境：没有 PYTHONPATH、没有仓库、PATH 最短。"""
    env = {
        "PATH": _minimal_path(),
        "HOME": str(tmp_path / "home"),
        "TAVOTTO_CONFIG_DIR": os.environ["TAVOTTO_CONFIG_DIR"],
        "TAVOTTO_DATA_DIR": os.environ["TAVOTTO_DATA_DIR"],
        "TAVOTTO_NO_TELEMETRY": "1",
    }
    for name in ("SYSTEMROOT", "SystemRoot", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP"):
        if name in os.environ:
            env[name] = os.environ[name]
    (tmp_path / "home").mkdir(exist_ok=True)
    env.update(extra)
    return env


def _run_configure(plugin: Path, args: list[str], tmp_path: Path, **env_extra: str):
    cwd = tmp_path / "随机 cwd"
    cwd.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, str(plugin / "integrations" / "configure.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(cwd),
        env=_clean_env(tmp_path, **env_extra),
        timeout=600,
    )


def _bare_python(tmp_path: Path) -> str:
    """一个**没有** tavotto 的解释器：不带 pip 的新 venv（不继承系统 site-packages）。"""
    d = tmp_path / "bare venv"
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(d)
    py = d / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert subprocess.run([str(py), "-c", "import tavotto"], capture_output=True).returncode != 0
    return str(py)


def _serve(command: str, args: list[str], env: dict, cwd: Path, calls: list[dict]) -> dict:
    """按配置原样起 server（不过 shell），走 stdio 握手 + 列工具 + 逐个调用。"""
    msgs = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "configure-test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    for i, call in enumerate(calls):
        msgs.append({"jsonrpc": "2.0", "id": 10 + i, "method": "tools/call", "params": call})
    proc = subprocess.run(
        [command, *args],
        input="".join(json.dumps(m) + "\n" for m in msgs),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(cwd),
        timeout=300,
    )
    replies = {}
    for line in proc.stdout.splitlines():
        msg = json.loads(line)  # stdout 只许有协议帧：解析失败就是污染
        if "id" in msg:
            replies[msg["id"]] = msg
    assert 1 in replies, proc.stderr[-2000:]
    return replies


# ======================================================== 包内入口与发行接线


def test_the_generator_is_required_for_new_stagings_only():
    """新组装的 staging 必须有它（漏打包当场失败）；但**已装的旧版**插件的体检清单
    `REQUIRED` 不含它——否则一份完好的旧 Codex 插件会被报成损坏（Codex 在 #559 上指出）。"""
    import ast

    pm = kit.load_script("plugin_stage")
    assert "integrations/configure.py" in pm.STAGE_REQUIRED
    assert "integrations/configure.py" not in pm.REQUIRED
    assert set(pm.REQUIRED) <= set(pm.STAGE_REQUIRED)
    tracked = {rel for rel, _mode in pm.tracked_plugin_files(ROOT)}
    assert "integrations/configure.py" in tracked
    # stage() 组装完逐条核对的是 STAGE_REQUIRED（判源码结构用 AST，不用子串）
    tree = ast.parse((ROOT / "scripts" / "plugin_stage.py").read_text(encoding="utf-8"))
    stage_fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "stage"
    )
    loops = [
        n.iter.id
        for n in ast.walk(stage_fn)
        if isinstance(n, ast.For) and isinstance(n.iter, ast.Name)
    ]
    assert "STAGE_REQUIRED" in loops, loops


def test_an_installed_older_bundle_without_the_generator_still_verifies(tmp_path):
    """用户机器上装着的旧版插件（清单里本来就没有 integrations/）：体检照样通过。"""
    stage = kit.load_script("plugin_stage")
    d = tmp_path / "old"
    kit.synthetic_staging(d)
    (d / "integrations" / "configure.py").unlink()
    (d / "integrations").rmdir()
    manifest = stage.read_manifest(d)
    modes = {
        e["path"]: e["mode"] for e in manifest["files"] if e["path"] != "integrations/configure.py"
    }
    stage.pm.write_build_manifest(
        d,
        modes=modes,
        source_sha=manifest["source_sha"],
        fingerprint=manifest["build_inputs_fingerprint"],
        lockfile_sha256="0" * 64,
        toolchain={"python": "3.13.0", "node": "22.0.0", "pnpm": "11.0.0"},
        min_tavotto_version="0.13.0",
    )
    assert stage.verify_dir(d, installed=True) == []


def test_a_staging_without_the_generator_fails_verification(tmp_path):
    stage = kit.load_script("plugin_stage")
    d = tmp_path / "stage"
    kit.synthetic_staging(d)
    assert stage.verify_dir(d) == []
    (d / "integrations" / "configure.py").unlink()
    problems = stage.verify_dir(d)
    assert any("integrations/configure.py" in p for p in problems), problems


def test_the_generator_is_stdlib_only():
    """它跑在用户随便哪个 python3 上（引擎还没装时也要能跑）：只许 import 标准库。"""
    import ast

    tree = ast.parse(SOURCE_CONFIGURE.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.add((node.module or "").split(".")[0])
    names.discard("__future__")
    assert names <= set(sys.stdlib_module_names), names - set(sys.stdlib_module_names)


# ======================================================== CLI 契约


def test_help_lists_every_flag_the_docs_use(unpacked, tmp_path):
    proc = _run_configure(unpacked, ["--help"], tmp_path)
    assert proc.returncode == 0
    for flag in ("--host", "--project-root", "--python", "--engine-python", "--diagnose", "--emit"):
        assert flag in proc.stdout, flag


def test_stdout_is_only_the_config_and_notes_go_to_stderr(unpacked, project, tmp_path):
    proc = _run_configure(
        unpacked,
        ["--host", "vscode", "--project-root", str(project), "--python", sys.executable],
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)  # 整个 stdout 就是一个 JSON 对象
    assert list(data) == ["servers"]
    assert "只合并 tavotto 这一项" in proc.stderr
    assert "{" not in proc.stderr.splitlines()[0]


def test_diagnose_is_a_separate_machine_readable_mode(unpacked, project, tmp_path):
    proc = _run_configure(
        unpacked,
        [
            "--host",
            "cursor",
            "--project-root",
            str(project),
            "--python",
            sys.executable,
            "--diagnose",
        ],
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert "config" not in report  # 诊断与配置不混成一个对象
    assert report["package"]["dir"] == str(unpacked)
    assert report["package"]["release_build"] is True
    assert report["launcher"]["starts"] is True
    assert report["engine"]["ok"] is True


@pytest.mark.parametrize(
    "bad",
    ["relative/figures", "~/figures", "/", "__HOME__", "__PACKAGE__", "__MISSING__"],
)
def test_project_roots_that_would_over_authorize_are_refused(unpacked, tmp_path, bad):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    value = {
        "__HOME__": str(home),
        "__PACKAGE__": str(unpacked / "mcp"),
        "__MISSING__": str(tmp_path / "does-not-exist"),
    }.get(bad, bad)
    if bad == "/" and os.name == "nt":
        value = os.path.splitdrive(str(tmp_path))[0] + "\\"
    proc = _run_configure(unpacked, ["--host", "claude-desktop", "--project-root", value], tmp_path)
    assert proc.returncode == 2, (bad, proc.stdout, proc.stderr)
    assert proc.stdout == ""  # 失败时 stdout 不出半份配置
    assert "bad_project_root" in proc.stderr


def test_an_unknown_host_is_an_argument_error(unpacked, project, tmp_path):
    proc = _run_configure(unpacked, ["--host", "tare", "--project-root", str(project)], tmp_path)
    assert proc.returncode == 2
    assert proc.stdout == ""


def test_the_generator_writes_nothing(unpacked, project, tmp_path):
    """默认不写：HOME、配置目录、项目目录、包目录前后一字节不差。"""

    def snapshot(*dirs: Path) -> dict:
        out = {}
        for d in dirs:
            for p in sorted(d.rglob("*")):
                if p.is_file():  # 包括 __pycache__：探针不许在包里落 .pyc（#559）
                    out[str(p)] = p.read_bytes()
        return out

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    cfg = Path(os.environ["TAVOTTO_CONFIG_DIR"])
    cfg.mkdir(parents=True, exist_ok=True)
    before = snapshot(home, cfg, project, unpacked)
    for host in ("claude-code", "vscode", "claude-desktop", "trae"):
        proc = _run_configure(
            unpacked,
            ["--host", host, "--project-root", str(project), "--python", sys.executable],
            tmp_path,
        )
        assert proc.returncode == 0, proc.stderr
    assert snapshot(home, cfg, project, unpacked) == before


# ======================================================== 启动器探针与解释器


def test_an_interpreter_that_cannot_start_the_launcher_is_a_hard_failure(
    unpacked, project, tmp_path
):
    """「命令存在、零输出、非零退出」——商店别名的可观测形状。判据是执行，不是 which。"""
    if os.name == "nt":
        fake = tmp_path / "python.cmd"
        fake.write_text("@exit /b 9009\r\n", encoding="utf-8")
    else:
        fake = tmp_path / "python3"
        fake.write_text("#!/bin/sh\nexit 9009\n", encoding="utf-8")
        fake.chmod(0o755)
    proc = _run_configure(
        unpacked,
        ["--host", "vscode", "--project-root", str(project), "--python", str(fake)],
        tmp_path,
    )
    assert proc.returncode == 3, proc.stderr
    assert proc.stdout == ""
    assert "launcher_unstartable" in proc.stderr


def test_a_missing_interpreter_is_named(unpacked, project, tmp_path):
    proc = _run_configure(
        unpacked,
        ["--host", "vscode", "--project-root", str(project), "--python", str(tmp_path / "nope")],
        tmp_path,
    )
    assert proc.returncode == 2
    assert "python_not_found" in proc.stderr


@pytest.mark.parametrize("which", ["absent_path", "absent_name"])
def test_an_explicit_engine_python_that_does_not_exist_is_a_runtime_failure(
    unpacked, project, tmp_path, which
):
    """找不到的 `--engine-python` 与「装不上引擎的」同一类：退出码 3，不是参数错（#578）。"""
    raw = str(tmp_path / "nope") if which == "absent_path" else "tavotto-no-such-python-3"
    proc = _run_configure(
        unpacked,
        ["--host", "vscode", "--project-root", str(project), "--engine-python", raw],
        tmp_path,
    )
    assert proc.returncode == 3, proc.stderr
    assert proc.stdout == ""
    assert "engine_python_unusable" in proc.stderr


def test_engine_found_only_through_the_shell_env_is_pinned(
    unpacked, project, tmp_path, monkeypatch
):
    """启动器解释器没有引擎、引擎只能靠当前 shell 的变量找到：钉进 TAVOTTO_MCP_PYTHON，
    且钉住后在最小环境里再验过（来源变成 mcp_env）。"""
    mod = _load(unpacked / "integrations" / "configure.py")
    bare = _bare_python(tmp_path)
    for name in ("TAVOTTO_MCP_PYTHON", "TAVOTTO_WORKER_PYTHON", "MM_WORKER_PYTHON"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", "/bin"]))
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", sys.executable)  # 只在「shell」里有
    result = mod.build("claude-desktop", str(project), bare, None)
    assert result["engine_pinned"] == sys.executable
    assert result["engine"]["ok"] is True
    assert result["engine"]["source"] == "mcp_env"
    env = result["config"]["mcpServers"]["tavotto"]["env"]
    assert env["TAVOTTO_MCP_PYTHON"] == sys.executable
    assert result["config"]["mcpServers"]["tavotto"]["command"] == bare


def test_launcher_python_with_the_engine_is_not_pinned(unpacked, project, tmp_path):
    mod = _load(unpacked / "integrations" / "configure.py")
    result = mod.build("cursor", str(project), sys.executable, None)
    assert result["engine"]["source"] == "current"
    assert result["engine_pinned"] is None
    env = result["config"]["mcpServers"]["tavotto"]["env"]
    assert "TAVOTTO_MCP_PYTHON" not in env
    assert set(env) <= {"TAVOTTO_MCP_ROOTS", *mod.CARRIED_ENV}


def test_no_engine_anywhere_still_yields_a_config_with_real_recovery_steps(
    unpacked, project, tmp_path
):
    """引擎哪儿都没有：配置照出（降级 server 有 tavotto_health），stderr 的恢复命令是
    这份包里启动器的真实绝对路径，不是 `<插件目录>` 占位。"""
    bare = _bare_python(tmp_path)
    proc = _run_configure(
        unpacked,
        ["--host", "trae", "--project-root", str(project), "--python", bare],
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    config = json.loads(proc.stdout)
    assert "TAVOTTO_MCP_PYTHON" not in config["mcpServers"]["tavotto"]["env"]
    assert "引擎未就绪" in proc.stderr
    assert str(unpacked / "mcp" / "server.py") in proc.stderr
    assert "--provision" in proc.stderr
    assert "<插件目录>" not in proc.stderr


def test_an_explicit_engine_python_that_cannot_import_the_engine_is_refused(
    unpacked, project, tmp_path
):
    bare = _bare_python(tmp_path)
    proc = _run_configure(
        unpacked,
        [
            "--host",
            "vscode",
            "--project-root",
            str(project),
            "--python",
            bare,
            "--engine-python",
            bare,
        ],
        tmp_path,
    )
    assert proc.returncode == 3, proc.stderr
    assert "engine_python_unusable" in proc.stderr


# ======================================================== 脱离源码树真起 server


#: 输出 JSON 的全部 profile（DSH 是 Cordis YAML，没有独立的解析器消费它，不在此列——
#: 验收矩阵里它的工具流程因此是 not_run，Codex 在 #560 上指出不能靠「同一份启动描述」推定）
JSON_HOSTS = ["cursor", "zcode", "workbuddy", "claude-code", "claude-desktop", "trae", "vscode"]


@pytest.mark.parametrize("host", JSON_HOSTS)
def test_the_generated_config_starts_the_unpacked_server(unpacked, project, tmp_path, host):
    """按生成的 command / args / env 原样起 server：握手、真 server（不是降级）、
    工具齐、health 报的包目录 == 解包目录、授权根 == 用户选的那一个。"""
    proc = _run_configure(
        unpacked,
        ["--host", host, "--project-root", str(project), "--python", sys.executable],
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    entry = next(iter(next(iter(data.values())).values()))
    cwd = tmp_path / "宿主 cwd"
    cwd.mkdir()
    env = _clean_env(tmp_path, **entry["env"])
    replies = _serve(
        entry["command"],
        entry["args"],
        env,
        cwd,
        [{"name": "tavotto_health", "arguments": {}}],
    )
    info = replies[1]["result"]["serverInfo"]
    assert info["version"] not in (None, "0"), "降级 server：引擎没找到"
    names = {t["name"] for t in replies[2]["result"]["tools"]}
    assert {"tavotto_health", "tavotto_open_figure", "tavotto_export"} <= names
    health = replies[10]["result"]["structuredContent"]
    assert health["server"]["package_dir"] == str(unpacked)
    assert health["server"]["release_build"] is True
    assert health["roots"] == [os.path.realpath(project)]
    assert health["root_authority"]["source"] == "explicit_env"
    assert health["checks"]["workspace_authorized"]["ok"] is True
    assert health["checks"]["host_ui_rendered"]["status"] == "unknown_to_server"


def test_two_generated_configs_do_not_widen_each_other(unpacked, tmp_path):
    """给 A 宿主授权 A 目录、给 B 宿主授权 B 目录：互不带出对方（生成器无状态）。"""
    a = tmp_path / "项目A"
    b = tmp_path / "项目B"
    a.mkdir()
    b.mkdir()
    outs = []
    for host, root in (("claude-code", a), ("vscode", b)):
        proc = _run_configure(
            unpacked,
            ["--host", host, "--project-root", str(root), "--python", sys.executable],
            tmp_path,
        )
        assert proc.returncode == 0, proc.stderr
        outs.append(proc.stdout)
    assert str(b) not in outs[0] and str(a) not in outs[1]


def test_a_root_containing_the_path_separator_is_refused(unpacked, tmp_path):
    """`/tmp/a:/etc` 这样的目录名进 TAVOTTO_MCP_ROOTS 会被拆成两个根（Codex 在 #559 上指出）。"""
    if os.name == "nt":
        pytest.skip("Windows 目录名里不能有 ';' 以外的分隔符形状，这里只验 POSIX 的 ':'")
    tricky = tmp_path / "proj:etc"
    tricky.mkdir()
    proc = _run_configure(unpacked, ["--host", "vscode", "--project-root", str(tricky)], tmp_path)
    assert proc.returncode == 2, proc.stderr
    assert proc.stdout == ""
    assert "bad_project_root" in proc.stderr


def test_home_is_found_without_env_vars_and_ancestors_are_refused(unpacked, tmp_path, monkeypatch):
    """HOME / USERPROFILE 都不在（env -i、服务启动器）也认得出主目录；主目录的上级也不行。"""
    mod = _load(unpacked / "integrations" / "configure.py")
    if os.name != "nt":
        import pwd

        real_home = pwd.getpwuid(os.getuid()).pw_dir
        monkeypatch.delenv("HOME", raising=False)
        monkeypatch.delenv("USERPROFILE", raising=False)
        with pytest.raises(mod.ConfigureError) as exc:
            mod.validate_project_root(real_home)
        assert exc.value.code == "bad_project_root"
    parent = tmp_path / "users"
    home = parent / "someone"
    (home / "figures").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    with pytest.raises(mod.ConfigureError):
        mod.validate_project_root(str(parent))  # 上级目录把整个主目录都包进来了
    assert mod.validate_project_root(str(home / "figures")) == os.path.realpath(home / "figures")


def test_health_json_survives_a_non_utf8_locale_and_chinese_paths(unpacked, tmp_path):
    """Windows 上管道的默认编码是 ANSI 代码页：中文路径一进体检报告，启动器就
    UnicodeEncodeError、零 JSON，configure 与 `tavotto codex install` 都会把它误判成
    「启动器起不来」（#559 的 Windows CI）。这里用 PYTHONIOENCODING=cp1252 在任何平台上
    复现同一形状；解包目录本身带中文与空格。"""
    env = _clean_env(tmp_path, PYTHONIOENCODING="cp1252", PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [sys.executable, str(unpacked / "mcp" / "server.py"), "--health"],
        capture_output=True,
        cwd=str(tmp_path),
        env=env,
        timeout=300,
    )
    assert proc.returncode in (0, 3), proc.stderr.decode("utf-8", "replace")[-800:]
    report = json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])
    assert "发行 包" in report["widget"]["path"]


# ======================================================== #559 第二轮评审


def _copy_package(unpacked: Path, tmp_path: Path) -> Path:
    import shutil

    dest = tmp_path / "副本 包" / "codex-plugin"
    shutil.copytree(unpacked, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def test_a_partially_extracted_package_is_refused(unpacked, project, tmp_path):
    """启动器在、`tavotto_mcp` 缺一块：`--health` 照样回 JSON（它不 import 那个包），
    但宿主一起就 ImportError——必须在打印配置之前就拒绝（Codex 在 #559 上指出）。"""
    pkg = _copy_package(unpacked, tmp_path)
    (pkg / "mcp" / "tavotto_mcp" / "bridge.py").unlink()
    proc = _run_configure(
        pkg,
        ["--host", "vscode", "--project-root", str(project), "--python", sys.executable],
        tmp_path,
    )
    assert proc.returncode == 3, proc.stderr
    assert proc.stdout == ""
    assert "package_incomplete" in proc.stderr and "bridge.py" in proc.stderr


def test_a_server_that_cannot_handshake_is_refused(unpacked, project, tmp_path):
    """文件都在，但真 server 起不来（这里把 bridge 换成 import 即失败）：体检照样说引擎
    可用，只有按启动描述真起一次 initialize 才看得出来。"""
    pkg = _copy_package(unpacked, tmp_path)
    (pkg / "mcp" / "tavotto_mcp" / "bridge.py").write_text(
        'raise ImportError("broken on purpose")\n', encoding="utf-8"
    )
    proc = _run_configure(
        pkg,
        ["--host", "vscode", "--project-root", str(project), "--python", sys.executable],
        tmp_path,
    )
    assert proc.returncode == 3, proc.stderr
    assert proc.stdout == ""
    assert "server_unstartable" in proc.stderr


def test_an_explicit_engine_python_is_the_launch_command(unpacked, project, tmp_path):
    """`--engine-python` 直接当启动命令：只塞进 TAVOTTO_MCP_PYTHON 的话，启动器解释器自己
    装着引擎时它会被静默忽略（Codex 在 #559 上指出）。"""
    mod = _load(unpacked / "integrations" / "configure.py")
    # 引擎要是**另一个**解释器路径，否则「被忽略、落回默认解释器」与「被采用」看起来一样
    here = Path(sys.executable)
    alt = next(
        (
            here.with_name(n)
            for n in ("python3", "python", f"python3.{sys.version_info[1]}")
            if here.with_name(n).is_file() and here.with_name(n) != here
        ),
        None,
    )
    if alt is None:
        pytest.skip("这个环境里没有与当前解释器同 venv 的另一个解释器路径")
    result = mod.build("cursor", str(project), None, str(alt))
    entry = result["config"]["mcpServers"]["tavotto"]
    assert entry["command"] == os.path.abspath(str(alt))
    assert "TAVOTTO_MCP_PYTHON" not in entry["env"]
    assert result["engine"]["source"] == "current"


def test_engine_python_with_a_different_python_is_an_argument_error(unpacked, project, tmp_path):
    bare = _bare_python(tmp_path)
    proc = _run_configure(
        unpacked,
        [
            "--host",
            "vscode",
            "--project-root",
            str(project),
            "--python",
            bare,
            "--engine-python",
            sys.executable,
        ],
        tmp_path,
    )
    assert proc.returncode == 2, proc.stderr
    assert "bad_args" in proc.stderr


def test_the_windows_hand_off_keeps_paths_with_spaces_whole(unpacked, monkeypatch):
    """Windows 上 `os.execv` 不给参数加引号，带空格的包路径会被拆开（#559 Windows CI：
    引擎解释器去打开 `…\\发行`、零协议帧）。那里必须改用子进程、参数按列表传。"""
    launcher = _load(unpacked / "mcp" / "server.py", name="_tavotto_launcher_handoff")
    calls: list = []
    monkeypatch.setattr(launcher, "_IS_WINDOWS", True)
    monkeypatch.setattr(launcher.subprocess, "call", lambda args: calls.append(args) or 7)

    def _no_execv(*_a):
        raise AssertionError("Windows 上不许用 os.execv 交棒")

    monkeypatch.setattr(launcher.os, "execv", _no_execv)
    rc = launcher._hand_off(r"C:\Py 3\python.exe", ["--x"])
    assert rc == 7  # 子进程的退出码原样带回
    assert calls == [[r"C:\Py 3\python.exe", str(unpacked / "mcp" / "server.py"), "--x"]]
    assert " " in calls[0][1]  # 路径里确实有空格，而它仍是一个完整的参数


# ======================================================== #559 第三轮评审


def test_the_user_telemetry_and_location_settings_are_carried_into_the_host(
    unpacked, project, monkeypatch
):
    """GUI 宿主不继承 shell 环境：遥测硬开关与配置 / 数据目录必须写进配置，否则宿主里
    遥测可能又能发、引擎去了另一处找（Codex 在 #559 上指出）。没设的一个都不带。"""
    mod = _load(unpacked / "integrations" / "configure.py")
    for name in mod.CARRIED_ENV:
        monkeypatch.delenv(name, raising=False)
    bare = mod.launch_descriptor("/py", str(project))
    assert set(bare["env"]) == {"TAVOTTO_MCP_ROOTS"}
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", "/cfg/place")
    env = mod.launch_descriptor("/py", str(project))["env"]
    assert env["TAVOTTO_NO_TELEMETRY"] == "1"
    assert env["TAVOTTO_CONFIG_DIR"] == "/cfg/place"
    assert set(mod.CARRIED_ENV) <= set(mod.PROBE_ENV_KEEP)  # 探针验的就是这几处


def test_no_home_found_is_refused_on_every_platform(unpacked, tmp_path, monkeypatch):
    """查不到主目录时两个平台都 fail closed（原先对 Windows 的豁免会放行整个用户目录）。"""
    mod = _load(unpacked / "integrations" / "configure.py")
    monkeypatch.setattr(mod, "_home_dirs", lambda: [])
    target = tmp_path / "anything"
    target.mkdir()
    with pytest.raises(mod.ConfigureError) as exc:
        mod.validate_project_root(str(target))
    assert exc.value.code == "home_unknown"


def test_homedrive_homepath_counts_as_a_home(unpacked, tmp_path, monkeypatch):
    mod = _load(unpacked / "integrations" / "configure.py")
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)
    text = str(home)
    monkeypatch.setenv("HOMEDRIVE", text[:2] if os.name == "nt" else text[:1])
    monkeypatch.setenv("HOMEPATH", text[2:] if os.name == "nt" else text[1:])
    assert os.path.realpath(home) in mod._home_dirs()


def test_recovery_commands_survive_shell_metacharacters(unpacked, tmp_path):
    """恢复命令是给人复制进终端的：路径里有 `;` / `&` 也必须原样是一个参数。"""
    import shlex
    import shutil

    odd = tmp_path / ("Tavotto&old" if os.name == "nt" else "Tavotto;old") / "codex-plugin"
    shutil.copytree(unpacked, odd, ignore=shutil.ignore_patterns("__pycache__"))
    launcher = _load(odd / "mcp" / "server.py", name="_tavotto_launcher_quote")
    cmd = launcher._self_command()
    if os.name == "nt":
        # 真的粘进 PowerShell 跑一次：只比文本证明不了它可执行（Codex 在 #559 上指出）
        server = str(odd / "mcp" / "server.py")
        assert cmd == f"& '{sys.executable}' '{server}'"
        ps = shutil.which("pwsh") or shutil.which("powershell")
        assert ps, "Windows runner 上应有 PowerShell"
        proc = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command", cmd + " --health"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        assert "source" in json.loads(proc.stdout.strip().splitlines()[-1]), proc.stderr
    else:
        assert shlex.split(cmd) == [sys.executable, str(odd / "mcp" / "server.py")]
