"""DeepSeek Harness bundle（`codex-plugin/package.json` + `codex-plugin/dsh/`）的形状看护（ADR 0104）。

同一份插件目录兼作 npm 包：pnpm 把它装进 `$DSH_HOME/profiles/<名>/node_modules/tavotto-dsh`，
dsh 按 `package.json` 的 `dsh.bundle.patch` 叠上补丁。补丁里没有「本包目录」变量，
所以由胶水插件 `dsh/index.js` 按 `import.meta.url` 算出启动参数，作为服务 `tavotto`
提供给 mcp-client 与技能提供者两行。这里盯的是「dsh 起得来但工具 / 技能悄悄不在」
的那几处：包名与补丁行对不上、server 名换了、超时漏换算、技能根指错、发行件缺文件。
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import tavotto
from tavotto.engine import brand, pluginmanifest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / brand.CODEX_PLUGIN_SUBDIR
PACKAGE = PLUGIN / "package.json"
PATCH = PLUGIN / "dsh" / "cordis.patch.yml"
GLUE = PLUGIN / "dsh" / "index.js"
MCP_JSON = PLUGIN / ".mcp.json"
#: dsh-mcp-client 的 serverName 判据（packages/mcp/mcp-client/src/index.ts）
SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
#: dsh-skill 的目录描述上限（catalogDescriptionMaxLength）
SKILL_DESCRIPTION_MAX = 500
#: 只在 staging 里才有的文件（发行件里由 plugin_stage 放进去）：`files` 列它们是对的
STAGING_ONLY = {"plugin-build.json", "LICENSE"}


def _package() -> dict:
    return json.loads(PACKAGE.read_text(encoding="utf-8"))


def _rows() -> dict[str, dict[str, str]]:
    """补丁里 insert 的每一行：id → {字段: 原文}。

    本仓库不带 YAML 解析器，这份补丁又是 dsh 的方言（`!!js` 标签），所以只按它自己的
    固定排版读：`    - id:` 开一行，其下 `      <键>:` 与 `        <键>:` 是字段。形状一变
    这里就读不出预期的行，用例会红，不会静默放过。
    """
    rows: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in PATCH.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^    - id: (\S+)$", line)
        if m:
            current = rows.setdefault(m.group(1), {})
            continue
        m = re.match(r"^ {6,8}([A-Za-z]+): ?(.*)$", line)
        if current is not None and m:
            current[m.group(1)] = m.group(2).strip()
    return rows


def _strip_quotes(value: str) -> str:
    return value.strip("'\"")


def test_package_identity_tracks_the_product():
    pkg = _package()
    assert pkg["name"] == brand.DSH_BUNDLE_NAME
    assert pkg["version"] == tavotto.__version__
    # 不许被误发到 npm：分发只走发行分支的 git 规格
    assert pkg["private"] is True
    assert pkg["type"] == "module"
    assert pkg["license"] == "AGPL-3.0-only"
    assert pkg["homepage"] == brand.REPO_URL
    assert pkg["repository"] == {
        "type": "git",
        "url": f"git+{brand.CODEX_PLUGIN_SOURCE_URL}",
        "directory": brand.CODEX_PLUGIN_SUBDIR,
    }


def test_package_points_at_the_bundle_and_the_glue():
    pkg = _package()
    assert pkg["dsh"] == {"bundle": {"patch": "./dsh/cordis.patch.yml"}}
    assert (PLUGIN / pkg["dsh"]["bundle"]["patch"]).is_file()
    assert pkg["main"] == "dsh/index.js" and pkg["exports"]["."] == "./dsh/index.js"
    assert GLUE.is_file()


def test_package_files_cover_what_the_glue_reads():
    """`files` 漏一项，pnpm 从 git 装时就把它打包掉了，胶水插件在 dsh 启动时才炸。"""
    files = _package()["files"]
    for entry in files:
        if entry.rstrip("/") in STAGING_ONLY:
            continue
        assert (PLUGIN / entry).exists(), f"files 里的 {entry} 不在插件目录里"
    for needed in ("dsh/", "mcp/", "skills/", ".mcp.json"):
        assert needed in files


def test_patch_rows_wire_the_glue_to_the_mcp_client_and_skills():
    rows = _rows()
    assert list(rows) == ["tavotto", "tavotto-mcp", "tavotto-skills"]
    # 胶水行的 name 就是包名：dsh 按包名在 profile 的 node_modules 里解析它
    assert _strip_quotes(rows["tavotto"]["name"]) == brand.DSH_BUNDLE_NAME

    mcp = rows["tavotto-mcp"]
    assert _strip_quotes(mcp["name"]) == "@deepseek-ai/dsh-mcp-client"
    assert mcp["inject"] == "[tavotto]"
    assert mcp["transport"] == "stdio"
    # 工具暴露名 mcp__<serverName>__<原名>；与 .mcp.json 的 server key 同名
    ((codex_name,),) = [tuple(json.loads(MCP_JSON.read_text(encoding="utf-8"))["mcpServers"])]
    assert mcp["serverName"] == codex_name and SERVER_NAME.match(codex_name)
    assert mcp["command"] == "!!js ctx.tavotto.command"
    assert mcp["args"] == "!!js ctx.tavotto.args"
    assert mcp["toolCallTimeoutMs"] == "!!js ctx.tavotto.toolCallTimeoutMs"
    assert mcp["cwd"] == "!!js process.cwd()"

    skills = rows["tavotto-skills"]
    assert _strip_quotes(skills["name"]) == "@deepseek-ai/dsh-skill-filesystem"
    assert skills["inject"] == "[tavotto]"
    # 只看包内的技能，不把用户的默认技能根再扫一遍（base 里那一行已经在扫）
    assert skills["includeDefaultRoots"] == "false"
    assert skills["customSkillDirs"] == "!!js '[ctx.tavotto.skillsDir]'"
    # 与 base 的 provider（filesystem）不能同名：registerProvider 同名直接失败
    assert skills["providerName"] not in ("filesystem", "runtime")


@pytest.fixture(scope="module")
def node() -> str:
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("没有 node")
    return exe


#: 胶水不该读的 ComSpec：出现在启动参数里就说明又退回了「cmd.exe 当 command」的形态
BOGUS_COMSPEC = "X:\\not-a-shell\\cmd.exe"


def _launch_spec(node: str, platform: str) -> dict:
    script = (
        f"const m = await import({json.dumps(GLUE.as_uri())});"
        f"console.log(JSON.stringify(m.launchSpec(m.packageRoot, {json.dumps(platform)})))"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        # 继承本机环境：从零造的环境在 Windows 上缺 SystemRoot，node 起不来（CSPRNG 断言，退出码 134）。
        # 只把 ComSpec 换成一个不存在的路径——胶水交出去的启动参数不许依赖它（旧形态就是拿它当 command）。
        env={**os.environ, "ComSpec": BOGUS_COMSPEC},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize(
    ("platform", "command", "args"),
    [
        ("darwin", "/bin/sh", ["mcp/launch", "mcp/server.py"]),
        ("linux", "/bin/sh", ["mcp/launch", "mcp/server.py"]),
        ("win32", "mcp/launch.cmd", ["mcp/server.py"]),
    ],
)
def test_glue_launches_the_bundled_launcher(node, platform, command, args):
    """胶水插件交出去的是**包里那对**启动器 + server.py 的绝对路径。

    POSIX 上经 sh 解释 `mcp/launch`，不依赖 pnpm 保不保留执行位；Windows 上 command 就是
    `mcp/launch.cmd`，由 cross-spawn 拼 `cmd /d /s /c`——把 cmd.exe 当 command 时路径一带空格
    就起不来（见 `test_windows_cmd_line_survives_awkward_paths`）。超时唯一出处是 `.mcp.json`
    的 `tool_timeout_sec`，dsh 的单位是毫秒。
    """
    spec = _launch_spec(node, platform)
    assert BOGUS_COMSPEC not in [spec["command"], *spec["args"]]
    if platform == "win32":
        assert spec["command"] == str(PLUGIN / command)
    else:
        assert spec["command"] == command
    assert spec["args"] == [str(PLUGIN / a) for a in args]
    for path in [spec["command"], *spec["args"]]:
        if path != "/bin/sh":
            assert Path(path).is_file(), path
    entry = json.loads(MCP_JSON.read_text(encoding="utf-8"))["mcpServers"]["tavotto"]
    assert spec["toolCallTimeoutMs"] == entry["tool_timeout_sec"] * 1000
    assert Path(spec["skillsDir"]) == PLUGIN / "skills"
    assert (Path(spec["skillsDir"]) / "tavotto-figure" / "SKILL.md").is_file()


def test_windows_launcher_has_no_shebang():
    """cross-spawn 起非 .exe 文件前先读首行找 shebang：有 `#!/bin/sh` 就改去执行 `sh`，
    Windows 上找不到，DSH 的 Tavotto 一个工具都没有。"""
    first = (PLUGIN / "mcp" / "launch.cmd").read_bytes().split(b"\n", 1)[0]
    assert not first.startswith(b"#!"), first


#: cross-spawn 的 cmd 元字符（7.0.6 `lib/util/escape.js` 的 metaCharsRegExp）
_CMD_META = re.compile(r'([()\][%!^"`<>&|;, *?])')


def _cross_spawn_cmdline(comspec: str, command: str, args: list[str]) -> str:
    """照抄 cross-spawn 7.0.6（`lib/parse.js` 的 parseNonShell + `lib/util/escape.js`）在
    Windows 上起非 .exe 文件时交给 CreateProcess 的整行命令（windowsVerbatimArguments）。

    MCP SDK 的 StdioClientTransport（dsh-mcp-client 用的 ^1.12，1.12.0 与 1.31.0 都核过）
    就是这样起 stdio server 的。这里抄的是算法，验证的是「cmd + launch.cmd 在这行命令下把
    参数原样交给 server.py」，验证不到 cross-spawn 本身。
    """

    def escape_argument(arg: str) -> str:
        arg = re.sub(r'(\\*)"', lambda m: m.group(1) * 2 + '\\"', arg)
        arg = re.sub(r"(\\*)$", lambda m: m.group(1) * 2, arg)
        return _CMD_META.sub(r"^\1", f'"{arg}"')

    shell = " ".join(
        [_CMD_META.sub(r"^\1", os.path.normpath(command))] + [escape_argument(a) for a in args]
    )
    return f'{comspec} /d /s /c "{shell}"'


def test_cross_spawn_copy_matches_the_real_one():
    """上面那份照抄与真 cross-spawn 7.0.6 逐字节相同（在 macOS 上把 process.platform 改成
    win32 跑 `parse()` 取的原文）；抄错了，Windows 那条真跑证明的就是另一行命令。"""
    line = _cross_spawn_cmdline(
        "C:\\Windows\\system32\\cmd.exe",
        "C:\\U\\paren (x86)\\mcp\\launch.cmd",
        ["C:\\U\\paren (x86)\\mcp\\server.py", 'a "b" & c %PATH% ^ !', "tail\\"],
    )
    assert line == (
        "C:\\Windows\\system32\\cmd.exe /d /s /c "
        '"C:\\U\\paren^ ^(x86^)\\mcp\\launch.cmd '
        '^"C:\\U\\paren^ ^(x86^)\\mcp\\server.py^" '
        '^"a^ \\^"b\\^"^ ^&^ c^ ^%PATH^%^ ^^^ ^!^" '
        '^"tail\\\\^""'
    )


@pytest.mark.skipif(sys.platform != "win32", reason="要真的 cmd.exe")
@pytest.mark.parametrize(
    "dirname",
    ["with space", "paren (x86)", "amp & semi; comma,", "pct %PATH% caret ^ bang !", "中文 目录"],
)
def test_windows_cmd_line_survives_awkward_paths(tmp_path, dirname):
    """包装在带空格 / 括号 / & / % / ^ / ! / 中文的目录里，按 cross-spawn 拼出的那行命令真起
    cmd → launch.cmd → Python，server.py 收到的路径与参数一个字节不差。"""
    mcp = tmp_path / dirname / "mcp"
    mcp.mkdir(parents=True)
    shutil.copy2(PLUGIN / "mcp" / "launch.cmd", mcp / "launch.cmd")
    server = mcp / "server.py"
    server.write_text(
        "import json, sys\nprint(json.dumps([__file__, *sys.argv[1:]]))\n", encoding="utf-8"
    )
    # DSH 只传 server.py 一个参数；多带一个含元字符的，确认启动器 `%*` 原样转交
    extra = "x & y %PATH% ^ ! (z)"
    comspec = os.environ.get("ComSpec", "cmd.exe")
    line = _cross_spawn_cmdline(comspec, str(mcp / "launch.cmd"), [str(server), extra])
    env = {
        **os.environ,
        "TAVOTTO_MCP_PYTHON": sys.executable,
        "TAVOTTO_CONFIG_DIR": str(tmp_path / "config"),
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.run(
        line,
        executable=comspec,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [str(server), extra]


def test_skill_fits_the_dsh_catalog():
    """dsh 的技能目录只收 kebab-case 名字，描述超过上限会被截断，路由条件就丢在截断处。"""
    text = (PLUGIN / "skills" / "tavotto-figure" / "SKILL.md").read_text(encoding="utf-8")
    name = re.search(r"^name: (.+)$", text, flags=re.M).group(1).strip()
    description = re.search(r"^description: (.+)$", text, flags=re.M).group(1).strip()
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name)
    assert len(description) <= SKILL_DESCRIPTION_MAX


def test_new_staging_must_carry_the_bundle():
    """发行分支的插件里没有它们，`dsh plugin add` 装到的包就不是 bundle。"""
    for rel in ("package.json", "dsh/index.js", "dsh/cordis.patch.yml"):
        assert rel in pluginmanifest.STAGE_REQUIRED, rel
        assert rel not in pluginmanifest.REQUIRED, rel


def test_install_line_is_published_where_the_matrix_says():
    """安装那行整行由 `brand.DSH_*` 拼出，放在哪由支持矩阵 `dsh` 这一档决定（2026-09-30 用户决定）：
    `beta` 时 README 给出；此前 plugin-stable 还没带 bundle，README 与插件（技能）里一行都不许有，
    规格只在待发说明里。"""
    expected = f'dsh plugin --profile {brand.DSH_DEFAULT_PROFILE} add "{brand.DSH_PLUGIN_SPEC}"'
    matrix = json.loads((ROOT / "docs" / "support-matrix.json").read_text(encoding="utf-8"))
    (host,) = [h for h in matrix["mcp_hosts"]["hosts"] if h["id"] == "dsh"]
    lines = [ln.strip() for ln in (ROOT / "README.md").read_text(encoding="utf-8").splitlines()]
    found = [ln for ln in lines if ln.startswith("dsh plugin ")]
    if host["status"] == "beta":
        assert found == [expected]
    else:
        assert found == []
        pending = (ROOT / "docs" / "release-notes" / "UNRELEASED.md").read_text(encoding="utf-8")
        assert f"`{expected}`" in pending, "待发说明里没有安装那行：发版时 README 拿什么加回去"
        # 技能随插件发给所有宿主的 agent，也不许先教装法（#694 评审）
        tracked = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--", brand.CODEX_PLUGIN_SUBDIR],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.splitlines()
        assert tracked, "git ls-files 没列出插件目录（判据没在量东西）"
        early = [
            rel
            for rel in tracked
            if rel.endswith((".md", ".py", ".json", ".yaml", ".js"))
            and "dsh plugin --profile" in (ROOT / rel).read_text(encoding="utf-8")
        ]
        assert early == [], f"渠道还没 promote，插件里却已经在教 DSH 的装法：{early}"
    # pnpm 的 git 子目录规格：分支与目录都与两个市场同源
    assert brand.DSH_PLUGIN_SPEC == (
        f"git+{brand.CODEX_PLUGIN_SOURCE_URL}"
        f"#{brand.CODEX_PLUGIN_STABLE_BRANCH}&path:/{brand.CODEX_PLUGIN_SUBDIR}"
    )
