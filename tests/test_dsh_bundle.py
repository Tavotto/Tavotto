"""DeepSeek Harness bundle（`codex-plugin/package.json` + `codex-plugin/dsh/`）的形状看护（ADR 0104）。

同一份插件目录兼作 npm 包：pnpm 把它装进 `$DSH_HOME/profiles/<名>/node_modules/tavotto-dsh`，
dsh 按 `package.json` 的 `dsh.bundle.patch` 叠上补丁。补丁里没有「本包目录」变量，
所以由胶水插件 `dsh/index.js` 按 `import.meta.url` 算出启动参数，作为服务 `tavotto`
提供给 mcp-client 与技能提供者两行。这里盯的是「dsh 起得来但工具 / 技能悄悄不在」
的那几处：包名与补丁行对不上、server 名换了、超时漏换算、技能根指错、发行件缺文件。
"""

import json
import re
import shutil
import subprocess
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
        env={"PATH": "/usr/bin:/bin", "ComSpec": "C:\\Windows\\system32\\cmd.exe"},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize(
    ("platform", "command", "prefix"),
    [
        ("darwin", "/bin/sh", []),
        ("linux", "/bin/sh", []),
        ("win32", "C:\\Windows\\system32\\cmd.exe", ["/d", "/c"]),
    ],
)
def test_glue_launches_the_bundled_launcher(node, platform, command, prefix):
    """胶水插件交出去的是**包里那个**双语启动器 + server.py 的绝对路径。

    POSIX 上经 sh 解释，不依赖 pnpm 保不保留执行位；Windows 上经 cmd（Node 不经 shell 起不了
    .cmd）。超时唯一出处是 `.mcp.json` 的 `tool_timeout_sec`，dsh 的单位是毫秒。
    """
    spec = _launch_spec(node, platform)
    assert spec["command"] == command
    assert spec["args"] == [
        *prefix,
        str(PLUGIN / "mcp" / "launch.cmd"),
        str(PLUGIN / "mcp" / "server.py"),
    ]
    for path in spec["args"][len(prefix) :]:
        assert Path(path).is_file(), path
    entry = json.loads(MCP_JSON.read_text(encoding="utf-8"))["mcpServers"]["tavotto"]
    assert spec["toolCallTimeoutMs"] == entry["tool_timeout_sec"] * 1000
    assert Path(spec["skillsDir"]) == PLUGIN / "skills"
    assert (Path(spec["skillsDir"]) / "tavotto-figure" / "SKILL.md").is_file()


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


def test_readme_install_line_comes_from_brand():
    lines = [ln.strip() for ln in (ROOT / "README.md").read_text(encoding="utf-8").splitlines()]
    expected = f'dsh plugin --profile {brand.DSH_DEFAULT_PROFILE} add "{brand.DSH_PLUGIN_SPEC}"'
    assert [ln for ln in lines if ln.startswith("dsh plugin ")] == [expected]
    # pnpm 的 git 子目录规格：分支与目录都与两个市场同源
    assert brand.DSH_PLUGIN_SPEC == (
        f"git+{brand.CODEX_PLUGIN_SOURCE_URL}"
        f"#{brand.CODEX_PLUGIN_STABLE_BRANCH}&path:/{brand.CODEX_PLUGIN_SUBDIR}"
    )
