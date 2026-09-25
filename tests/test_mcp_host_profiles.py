"""八个宿主 profile 的**独立期望**（多宿主 PR 2）。

判据不从生成器的 `HOSTS` 表里取：下面的 `EXPECTED` 是照着官方文档（`docs/implementation/
multi-host-mcp/hosts.md` 逐条带链接与查证日期）另写的一份，生成器与断言如果共用一份错误模板
就能自证正确，那样的测试没有意义。

* 范围契约：八个 id 一个不少（独立书写的闭集），支持矩阵与验收矩阵的行与它三方一致；
* 形状：每家的顶层 key、条目字段、超时字段与单位；VS Code 是 `servers`，误写 `mcpServers` 要红；
* 值：只有绝对路径，没有 `${…}` / `~` / `<占位>` / `!!js`，不带 Codex 专有字段与 CODEX_* 变量；
* 落点：Claude Code 项目 `.mcp.json`、VS Code `.vscode/mcp.json`、Claude Desktop 配置三者互不相同；
* Skill：原生入口 / instruction_fallback 的标签按证据给；等价说明由 SKILL.md 生成、引用全在包内可达；
  整个技能目录复制出去之后，脚本照样跑得起来。
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "codex-plugin"
CONFIGURE = PLUGIN / "integrations" / "configure.py"
SKILL = PLUGIN / "skills" / "tavotto-figure"
DOCS = ROOT / "docs" / "implementation" / "multi-host-mcp"

#: 本轮承诺的宿主闭集——**独立书写**，不从 configure.HOST_IDS 取（漏一个要红）
SCOPE = {
    "cursor",
    "zcode",
    "dsh",
    "workbuddy",
    "claude-code",
    "claude-desktop",
    "trae",
    "vscode",
}

#: 照官方文档写的期望：顶层 key、允许的条目字段、必须出现的固定字段、超时字段、Skill 模式
EXPECTED = {
    "cursor": {"top": "mcpServers", "fields": {"command", "args", "env"}, "fixed": {}},
    "zcode": {"top": "mcpServers", "fields": {"command", "args", "env"}, "fixed": {}},
    "workbuddy": {"top": "mcpServers", "fields": {"command", "args", "env"}, "fixed": {}},
    "trae": {"top": "mcpServers", "fields": {"command", "args", "env"}, "fixed": {}},
    "claude-desktop": {"top": "mcpServers", "fields": {"command", "args", "env"}, "fixed": {}},
    "claude-code": {
        "top": "mcpServers",
        "fields": {"type", "command", "args", "env", "timeout"},
        "fixed": {"type": "stdio", "timeout": 1_800_000},
    },
    "vscode": {
        "top": "servers",
        "fields": {"type", "command", "args", "env"},
        "fixed": {"type": "stdio"},
    },
}
SKILL_MODE = {
    "cursor": "native",
    "claude-code": "native",
    "vscode": "native",
    "dsh": "native",
    "claude-desktop": "instruction_fallback",
    "trae": "instruction_fallback",
    "workbuddy": "instruction_fallback",
    "zcode": "instruction_fallback",
}
CODEX_ONLY_FIELDS = {"tool_timeout_sec", "startup_timeout_sec", "env_vars", "cwd", "title"}


def _mod():
    spec = importlib.util.spec_from_file_location("_tavotto_configure_profiles", CONFIGURE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _mod()


@pytest.fixture(autouse=True)
def _no_carried_env(mod, monkeypatch):
    """这里判的是**各宿主的 schema**，不是「用户设了哪些位置变量」：把会被原样带进配置的
    CARRIED_ENV（遥测开关、配置 / 数据目录）清掉，期望才能逐字写死。带不带它们另有
    tests/test_mcp_configure.py 的用例。"""
    for name in mod.CARRIED_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def project(tmp_path) -> Path:
    p = tmp_path / "论文 项目" / "figures"
    p.mkdir(parents=True)
    return p


def _descriptor(mod, project: Path, engine: str | None = None) -> dict:
    """启动描述走真实函数；序列化逐家比对（不经解释器探针，探针另有 test_mcp_configure）。"""
    return mod.launch_descriptor(sys.executable, str(project), engine)


# ======================================================== 范围契约


def test_every_promised_host_has_a_profile(mod):
    assert set(mod.HOST_IDS) == SCOPE


def test_support_matrix_and_acceptance_matrix_cover_the_same_hosts(mod):
    matrix = json.loads((ROOT / "docs" / "support-matrix.json").read_text(encoding="utf-8"))
    ids = {h["id"] for h in matrix["mcp_hosts"]["hosts"]}
    assert ids == SCOPE
    acceptance = (DOCS / "acceptance.md").read_text(encoding="utf-8")
    matrix_section = acceptance.split("## 矩阵", 1)[1].split("\n## ", 1)[0]
    rows = set(re.findall(r"^\| `([a-z-]+)` \|", matrix_section, flags=re.M))
    assert rows == SCOPE | {"codex"}, "验收矩阵要逐行包含八个宿主 + Codex 回归行"


def test_no_host_is_claimed_verified_without_evidence():
    """支持口径只能是 experimental，直到验收矩阵里真有 host_verified 与证据段。"""
    matrix = json.loads((ROOT / "docs" / "support-matrix.json").read_text(encoding="utf-8"))
    acceptance = (DOCS / "acceptance.md").read_text(encoding="utf-8")
    for host in matrix["mcp_hosts"]["hosts"]:
        assert host["status"] in ("experimental", "verified")
        if host["status"] == "verified":
            row = re.search(rf"^\| `{host['id']}` \|.*$", acceptance, flags=re.M).group(0)
            assert "host_verified" in row
            assert f"### 证据：{host['id']}" in acceptance


def test_evidence_levels_match_the_hosts_doc(mod):
    doc = (DOCS / "hosts.md").read_text(encoding="utf-8")
    for host in SCOPE:
        row = re.search(rf"^\| `{host}` \| (\w+) \|", doc, flags=re.M)
        assert row, f"hosts.md 没有 {host} 那一行"
        assert mod.HOSTS[host]["evidence"] == row.group(1), host


# ======================================================== 形状


@pytest.mark.parametrize("host", sorted(EXPECTED))
def test_json_profiles_match_the_documented_shape(mod, project, host):
    exp = EXPECTED[host]
    config = mod.serialize(host, _descriptor(mod, project))
    assert list(config) == [exp["top"]]
    servers = config[exp["top"]]
    assert list(servers) == ["tavotto"]
    entry = servers["tavotto"]
    extra = set(entry) - exp["fields"]
    assert not extra, f"{host} 多出文档证明不了的字段 {extra}"
    assert {"command", "args", "env"} <= set(entry)
    for key, value in exp["fixed"].items():
        assert entry[key] == value, (host, key)
    assert not (set(entry) & CODEX_ONLY_FIELDS), f"{host} 抄了 Codex 的字段"


def test_vscode_uses_servers_and_a_mcpServers_mutation_is_rejected(mod, project):
    config = mod.serialize("vscode", _descriptor(mod, project))
    assert "servers" in config and "mcpServers" not in config

    mutated = {"mcpServers": config["servers"]}  # 最常见的抄错
    assert not _valid_vscode(mutated)
    assert _valid_vscode(config)


def _valid_vscode(config: dict) -> bool:
    """VS Code `.vscode/mcp.json` 的最小校验（独立于生成器）：顶层 `servers`，stdio 必有 type/command。"""
    servers = config.get("servers")
    if not isinstance(servers, dict) or set(config) - {"servers", "inputs"}:
        return False
    return all(
        isinstance(e, dict) and e.get("type") == "stdio" and isinstance(e.get("command"), str)
        for e in servers.values()
    )


def test_the_other_hosts_are_not_forced_into_the_vscode_shape(mod, project):
    for host in SCOPE - {"vscode", "dsh"}:
        assert "servers" not in mod.serialize(host, _descriptor(mod, project)), host


def test_dsh_is_the_documented_cordis_patch(mod, project):
    """DSH 官方 mcp-memory.md 的形状：一条 insert，插件名 @deepseek-ai/dsh-mcp-client，
    config 里 serverName / transport: stdio / command / args / env；超时是毫秒。"""
    desc = _descriptor(mod, project)
    text = mod.render("dsh", mod.serialize("dsh", desc))
    q = json.dumps
    expected = "\n".join(
        [
            "- insert:",
            "    - id: " + q("mcp-tavotto"),
            "      name: " + q("@deepseek-ai/dsh-mcp-client"),
            "      config:",
            "        serverName: " + q("tavotto"),
            "        transport: " + q("stdio"),
            "        command: " + q(sys.executable, ensure_ascii=False),
            "        args:",
            "          - " + q(desc["args"][0], ensure_ascii=False),
            "        env:",
            "          TAVOTTO_MCP_ROOTS: " + q(str(project), ensure_ascii=False),
            "        toolCallTimeoutMs: 1800000",
        ]
    )
    assert text == expected + "\n"
    assert "!!js" not in text


def test_timeouts_are_converted_from_the_single_source(mod, project):
    """唯一出处是包里 Codex `.mcp.json` 的 tool_timeout_sec（秒）；ms 字段 = 秒 × 1000。"""
    seconds = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"][
        "tavotto"
    ]["tool_timeout_sec"]
    desc = _descriptor(mod, project)
    assert mod.serialize("claude-code", desc)["mcpServers"]["tavotto"]["timeout"] == seconds * 1000
    dsh = mod.serialize("dsh", desc)[0]["insert"][0]["config"]
    assert dsh["toolCallTimeoutMs"] == seconds * 1000


# ======================================================== 值


def _values(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _values(v)
    else:
        yield obj


@pytest.mark.parametrize("host", sorted(SCOPE))
def test_values_are_concrete_absolute_paths_without_host_variables(mod, project, host):
    desc = _descriptor(mod, project)
    config = mod.serialize(host, desc)
    for value in _values(config):
        if isinstance(value, str):
            assert "${" not in value and "<" not in value and not value.startswith("~"), value
    entry = (
        config[0]["insert"][0]["config"]
        if host == "dsh"
        else next(iter(next(iter(config.values())).values()))
    )
    assert os.path.isabs(entry["command"]) and os.path.isfile(entry["command"])
    assert all(os.path.isabs(a) and os.path.isfile(a) for a in entry["args"])
    env = entry["env"]
    assert env["TAVOTTO_MCP_ROOTS"] == str(project)
    assert not any(k.startswith("CODEX_") for k in env), "非 Codex 宿主不依赖 CODEX_* 变量"
    assert ".codex" not in json.dumps(config)


def test_windows_claude_desktop_carries_appdata_only_there(mod, project, monkeypatch):
    monkeypatch.setattr(mod, "IS_WINDOWS", True)
    monkeypatch.setenv("APPDATA", r"C:\Users\测试\AppData\Roaming")
    desc = _descriptor(mod, project)
    desktop = mod.serialize("claude-desktop", desc)["mcpServers"]["tavotto"]["env"]
    assert desktop["APPDATA"] == r"C:\Users\测试\AppData\Roaming"
    for host in SCOPE - {"claude-desktop", "dsh"}:
        env = next(iter(next(iter(mod.serialize(host, desc).values())).values()))["env"]
        assert "APPDATA" not in env, host


# ======================================================== 落点互不覆盖


def test_claude_code_vscode_and_desktop_targets_are_distinct_files(mod):
    code = mod.HOSTS["claude-code"]["target"][0]
    vscode = mod.HOSTS["vscode"]["target"][0]
    desktop = " ".join(mod.HOSTS["claude-desktop"]["target"])
    assert code.startswith("<项目>/.mcp.json")
    assert vscode.startswith("<项目>/.vscode/mcp.json")
    assert "claude_desktop_config.json" in desktop
    # VS Code 的说明明确不往工作区根 .mcp.json 写（那是 Claude Code 的项目文件、另一种 schema）
    assert any("不要写进工作区根的 .mcp.json" in t for t in mod.HOSTS["vscode"]["target"])
    # Claude Code 的说明提示同名登记会遮蔽
    assert any("遮蔽" in t for t in mod.HOSTS["claude-code"]["target"])


def test_trae_separates_registration_from_agent_enablement(mod):
    verify = " ".join(mod.HOSTS["trae"]["verify"])
    assert "智能体" in verify and "登记" in verify
    assert "CN 版" in verify and "国际版" in verify


def test_claude_desktop_evidence_is_not_borrowed_from_claude_code(mod):
    verify = " ".join(mod.HOSTS["claude-desktop"]["verify"])
    assert "claude mcp add" in verify and "不要拿" in verify


def test_workbuddy_never_points_at_codebuddy_paths(mod, project):
    text = json.dumps(mod.serialize("workbuddy", _descriptor(mod, project)))
    assert ".codebuddy" not in text
    assert any("CodeBuddy" in t and "不要改" in t for t in mod.HOSTS["workbuddy"]["target"])


# ======================================================== Skill


def test_skill_modes_follow_the_evidence(mod):
    assert {h: mod.HOSTS[h]["skill"] for h in SCOPE} == SKILL_MODE
    for host, mode in SKILL_MODE.items():
        dirs = mod.HOSTS[host]["skill_dirs"]
        assert bool(dirs) == (mode == "native"), host
    # Trae 不抄别家的目录
    assert mod.HOSTS["trae"]["skill_dirs"] == []


def test_instruction_fallback_is_generated_from_skill_md_and_resolves_in_the_package(mod):
    text = mod.skill_instructions()
    assert "instruction_fallback" in text.splitlines()[0]
    body = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2]
    # 同一份正文（只改写了引用路径）：抽一段不含路径的原句对拍
    assert "图文件契约（核心，违反即死图）" in body and "图文件契约（核心，违反即死图）" in text
    assert "`references/" not in text and " scripts/" not in text
    for ref in re.findall(
        r"(/[^`\s]*?/skills/tavotto-figure/(?:references|scripts)/[\w.-]+)", text
    ):
        assert Path(ref).is_file(), f"等价说明里的引用悬空：{ref}"


def test_instruction_fallback_quotes_script_paths_for_a_package_dir_with_spaces(
    mod, tmp_path, monkeypatch
):
    """验收流程要求解压到带空格的目录：命令示例里的脚本路径必须整体是一个参数。"""
    import shlex

    skill = tmp_path / "Tavotto Package" / "it's here" / "tavotto-figure"
    shutil.copytree(SKILL, skill, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(mod, "SKILL_DIR", str(skill))
    monkeypatch.setattr(mod, "IS_WINDOWS", False)
    text = mod.skill_instructions()
    commands = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("python3 ") and ".py" in line
    ]
    scripts = {"prefs.py", "handoff.py"}
    seen = set()
    for line in commands:
        argv = shlex.split(line)
        name = Path(argv[1]).name
        if name in scripts:
            assert Path(argv[1]).is_file(), line
            assert Path(argv[1]).parent == skill / "scripts", line
            seen.add(name)
    assert seen == scripts
    # 行内代码里的引用只是路径，不加引号
    assert f"`{skill / 'references'}{os.sep}" in text


def test_shell_quote_on_windows_is_powershell_single_quoting(mod, monkeypatch):
    """PowerShell 的双引号会展开 `$x` 与反引号，单引号里只有 ' 要写成 ''（#578）。"""
    monkeypatch.setattr(mod, "IS_WINDOWS", True)
    assert mod._shell_quote(r"C:\Tav $x`y\it's\x.py") == "'C:\\Tav $x`y\\it''s\\x.py'"


def test_windows_instructions_use_the_py_launcher_and_single_quotes(mod, monkeypatch):
    """Windows 上 `python3` 常常不存在或是 Store 别名：命令示例改成 `py -3 '<脚本>'`（#578）。"""
    monkeypatch.setattr(mod, "IS_WINDOWS", True)
    text = mod.skill_instructions()
    lines = [ln.strip() for ln in text.splitlines()]
    scripts = [ln for ln in lines if re.search(r"(prefs|handoff)\.py'", ln)]
    assert scripts, "等价说明里应有偏好 / 交接脚本的命令"
    for ln in scripts:
        assert ln.startswith(mod.WINDOWS_PYTHON + " '"), ln
    assert not any(ln.startswith("python3 '") for ln in lines)


@pytest.mark.skipif(os.name != "nt", reason="只在 Windows 上能真的交给 PowerShell 跑")
def test_windows_instruction_command_runs_in_powershell_with_dollar_and_backtick(
    mod, tmp_path, monkeypatch
):
    """退出条件：包路径里有 `$` 与反引号时，生成的 prefs.py 命令交给 PowerShell 真跑能成功。
    解释器前缀换成本测试的解释器（`py -3` 挑哪个 Python 与本条要证的引号无关）。"""
    skill = tmp_path / "Tav $env:x `n pkg" / "it's" / "tavotto-figure"
    shutil.copytree(SKILL, skill, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(mod, "SKILL_DIR", str(skill))
    text = mod.skill_instructions()
    line = next(
        ln.strip()
        for ln in text.splitlines()
        if ln.strip().startswith(mod.WINDOWS_PYTHON + " '") and "prefs.py' --json" in ln
    )
    cmd = "& '" + sys.executable.replace("'", "''") + "'" + line[len(mod.WINDOWS_PYTHON) :]
    ps = shutil.which("pwsh") or shutil.which("powershell")
    assert ps, "Windows runner 上应有 PowerShell"
    proc = subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "TAVOTTO_NO_TELEMETRY": "1",
            "TAVOTTO_CONFIG_DIR": str(tmp_path / "config"),
            "TAVOTTO_DATA_DIR": str(tmp_path / "data"),
        },
        timeout=180,
    )
    assert proc.returncode == 0, (cmd, proc.stderr)
    json.loads(proc.stdout.strip().splitlines()[-1])


def test_instruction_fallback_carries_the_references_required_before_any_script(
    mod, tmp_path, monkeypatch
):
    """Claude Desktop 这类宿主读不了本机文件：写脚本前必读的 references 必须**原文**随等价
    说明走，而不是只给一个读不到的路径（#578）。生成后把包删掉，说明本身仍然完整。"""
    skill = tmp_path / "pkg" / "tavotto-figure"
    shutil.copytree(SKILL, skill, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(mod, "SKILL_DIR", str(skill))
    originals = {
        name: (skill / "references" / name).read_text(encoding="utf-8")
        for name in mod.INLINED_REFERENCES
    }
    text = mod.skill_instructions()
    shutil.rmtree(tmp_path / "pkg")
    for name, original in originals.items():
        # 同一份原文（只改写了引用路径），不是第二份手写规则
        assert mod._resolve_skill_paths(original).strip("\n") in text, name
        assert f"附录：references/{name}" in text
    assert text.index("附录：") > text.index("图文件契约（核心，违反即死图）")


def test_inlined_references_match_the_skill_md_row_for_writing_scripts(mod):
    """附哪几份由 SKILL.md「写任何画图脚本之前」那一行决定；两边漂移就红。"""
    body = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    row = next(ln for ln in body.splitlines() if ln.startswith("| 写任何画图脚本之前"))
    assert tuple(re.findall(r"references/([\w.-]+\.md)", row)) == mod.INLINED_REFERENCES


def test_every_reference_the_skill_names_ships_inside_the_skill_dir():
    """复制出去的技能目录自给自足：SKILL.md 与 references 里提到的 references/ scripts/ 都在。"""
    texts = [(SKILL / "SKILL.md").read_text(encoding="utf-8")]
    texts += [p.read_text(encoding="utf-8") for p in (SKILL / "references").glob("*.md")]
    for text in texts:
        for rel in re.findall(r"(?:references|scripts)/[\w.-]+\.(?:md|py)", text):
            assert (SKILL / rel).is_file(), rel
        for name in re.findall(r"`([\w-]+\.md)`", text):
            if name in ("SKILL.md", "README.md"):
                continue
            assert (SKILL / "references" / name).is_file(), name


def test_a_copied_skill_runs_its_scripts_outside_the_package(tmp_path):
    """按 Claude Code / VS Code 的方式把整个目录复制到项目里：脚本照样能跑（更新检查如实
    报「不知道版本」而不是崩）。"""
    dest = tmp_path / "proj" / ".claude" / "skills" / "tavotto-figure"
    shutil.copytree(SKILL, dest, ignore=shutil.ignore_patterns("__pycache__"))
    env = {**os.environ, "TAVOTTO_NO_TELEMETRY": "1"}
    prefs = subprocess.run(
        [sys.executable, str(dest / "scripts" / "prefs.py"), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(tmp_path),
        timeout=60,
    )
    assert prefs.returncode == 0, prefs.stderr
    json.loads(prefs.stdout.strip().splitlines()[-1])
    import importlib.util as iu

    spec = iu.spec_from_file_location("_copied_update_check", dest / "scripts" / "update_check.py")
    uc = iu.module_from_spec(spec)
    spec.loader.exec_module(uc)
    assert uc.current_version() is None  # 包外找不到版本：说「不知道」，不猜


def test_skill_entry_is_host_neutral_where_it_must_be():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "references/other-hosts.md" in text
    assert "不要默认让所有人执行 `codex plugin add`" in text
    assert "按原始名认工具" in text
    assert "一条普通消息" in text  # 没有专用提问工具时
    assert "不声称已经记住" in text  # 没有本机执行能力时不伪造偏好写入
    assert "不要声称已经保存或运行过" in text
    other = (SKILL / "references" / "other-hosts.md").read_text(encoding="utf-8")
    for host in SCOPE:
        assert f"`{host}`" in other, host
    assert "server.package_dir" in other
    assert "unknown_to_server" in other


def test_the_claude_cli_route_registers_the_same_entry_including_the_timeout(mod, project):
    """stderr 里给的 CLI 路线必须与 .mcp.json 是**同一份条目**：`claude mcp add` 没有超时选项，
    走它会丢掉 timeout（Codex 在 #560 上指出），所以用 add-json 把整条 JSON 交过去。"""
    config = mod.serialize("claude-code", _descriptor(mod, project))
    argv = mod.claude_cli_argv(config)
    assert argv[:3] == ["claude", "mcp", "add-json"]
    assert "--scope" in argv and argv[argv.index("--scope") + 1] == "project"
    assert argv[-2] == "tavotto"
    assert json.loads(argv[-1]) == config["mcpServers"]["tavotto"]
    assert json.loads(argv[-1])["timeout"] == 1_800_000


def test_update_hint_is_not_codex_only():
    """同一个 update_check 被 Codex 插件与完整包两种安装共用、分不出自己在哪种里：提示两条都给。"""
    spec = importlib.util.spec_from_file_location(
        "_uc_hosts", SKILL / "scripts" / "update_check.py"
    )
    uc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(uc)
    text = uc.hint(
        {
            "status": "available",
            "latest_version": "9.9.9",
            "current_version": "0.1.0",
            "upgrade_command": uc.UPGRADE_COMMAND,
        }
    )
    assert "codex plugin marketplace upgrade tavotto" in text
    assert "integrations/configure.py" in text and "其他宿主" in text
    # 重新生成配置不会更新复制出去的技能 / 粘贴的等价说明：两样都要明说（#578）
    assert "skills/tavotto-figure/" in text and "--emit instructions" in text


@pytest.mark.parametrize(
    "rel",
    [
        "README.md",
        "README.zh-CN.md",
        "codex-plugin/skills/tavotto-figure/references/other-hosts.md",
    ],
)
def test_bootstrap_docs_give_a_windows_invocation(rel):
    """工具还不存在时没有 `tavotto_health` 给真实命令：接入步骤本身要有 Windows 能跑的写法
    （`python3` 在那里常常是 Store 别名；#578）。"""
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert re.search(r"py -3 '<[^>]+>\\integrations\\configure\.py'", text), rel


def test_other_hosts_upgrade_steps_refresh_the_skill():
    """other-hosts.md 的升级说明与 update_check 同口径：重新复制技能或重新生成等价说明（#578）。"""
    text = (SKILL / "references" / "other-hosts.md").read_text(encoding="utf-8")
    upgrade = text[text.index("升级的方法") :]
    upgrade = upgrade[: upgrade.index("\n- ")]
    assert "tavotto-figure/" in upgrade and "--emit instructions" in upgrade
