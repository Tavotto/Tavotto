"""Claude Code 插件（`codex-plugin/.claude-plugin/`）的形状看护（ADR 0103）。

同一份插件目录同时是 Codex 插件与 Claude Code 插件（也被 ZCode / WorkBuddy 当 Claude 插件装，
ADR 0106）。Codex 的 MCP 配置叫 `codex.mcp.json`、由 Codex 清单指向——**插件根不许有
`.mcp.json`**：Claude Code / ZCode / WorkBuddy / MiniMax Code 都会自动读那个名字，WorkBuddy 还让它
覆盖清单里的同名条目，Codex 形状的相对启动器在那边起不来。这里的断言盯的是「坏了不报错、
只是另一家悄悄起不来」的那几处：多出一份自动读取的配置、名字对不上、字段抄成 Codex 的、
超时漏换算、版本漂开、文档与常量漂开。
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
CLAUDE_MANIFEST = PLUGIN / ".claude-plugin" / "plugin.json"
CODEX_MANIFEST = PLUGIN / ".codex-plugin" / "plugin.json"
MCP_JSON = PLUGIN / pluginmanifest.mcp_config_rel(PLUGIN)
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return _load(CLAUDE_MANIFEST)


@pytest.fixture(scope="module")
def codex_entry() -> dict:
    return _load(MCP_JSON)["mcpServers"]["tavotto"]


def test_manifest_identity_tracks_the_codex_plugin(manifest):
    """名字与版本跟 Codex 清单、跟产品同一代：版本漂了，Claude Code 会一直钉在旧版上。"""
    codex = _load(CODEX_MANIFEST)
    assert manifest["name"] == codex["name"] == brand.CODEX_PLUGIN_NAME
    assert manifest["version"] == codex["version"] == tavotto.__version__
    for key in ("license", "homepage", "repository", "author"):
        assert manifest[key] == codex[key], f"{key} 与 Codex 清单漂开了"


def test_no_auto_discovered_mcp_config_in_the_plugin():
    """插件目录里**没有**别的宿主会自动读的 MCP 配置（ADR 0106）。

    Codex 的配置曾叫插件根 `.mcp.json`。Claude Code / ZCode 先读它、再让清单同名条目替换；
    WorkBuddy 反过来——清单在前、`.mcp.json` 与 `mcp/*.json` 在后覆盖，于是按 Codex 的
    `./mcp/launch` 在会话目录里起进程、ENOENT（5.6.2 隔离实测）。所以 Codex 的配置换了
    名字、由 Codex 清单的 `mcpServers` 指向，这里钉住两件事：会被自动读的名字一个都没有，
    Codex 清单指向的正是那份改了名的配置。
    """
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--", brand.CODEX_PLUGIN_SUBDIR],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.splitlines()
    rels = [t.removeprefix(brand.CODEX_PLUGIN_SUBDIR + "/") for t in tracked]
    assert rels, "git ls-files 没列出插件目录（判据没在量东西）"
    assert [r for r in rels if pluginmanifest.STAGE_FORBIDDEN.match(r)] == []
    assert _load(CODEX_MANIFEST)["mcpServers"] == f"./{pluginmanifest.CODEX_MCP}"
    assert MCP_JSON == PLUGIN / pluginmanifest.CODEX_MCP and MCP_JSON.is_file()


def test_server_names_cover_the_codex_config(manifest):
    """Claude 清单的 server 名与 Codex 配置同名：技能与 `tavotto_health` 的恢复话术按这个名字找工具。"""
    codex_names = set(_load(MCP_JSON)["mcpServers"])
    assert set(manifest["mcpServers"]) == codex_names


def test_server_entry_is_the_codex_launcher_in_claude_form(manifest, codex_entry):
    """同一个启动器、同一个 server.py，只换成 Claude Code 的写法。

    * 路径从 `./` 相对插件根改成 `${CLAUDE_PLUGIN_ROOT}/` 绝对：Claude Code 起 stdio
      server 时只在 command / args / env 里展开这个变量，`cwd` 这个 Codex 字段它不认；
    * 超时唯一出处是 Codex 配置的 `tool_timeout_sec`。Claude Code 的 `timeout` 是毫秒；
      ZCode 读同一份清单却只认 `timeoutMs`（毫秒，默认 30 s，导出大图不够），两个都写、同值；
    * 不带任何 Codex 专有字段——它们在 Claude Code 里不是报错而是被静默丢掉，读起来
      像是生效了。
    """
    entry = manifest["mcpServers"]["tavotto"]

    def claude_form(rel: str) -> str:
        assert rel.startswith("./"), rel
        return f"{ROOT_VAR}/{rel[2:]}"

    assert entry["type"] == "stdio"
    assert entry["command"] == claude_form(codex_entry["command"])
    assert entry["args"] == [claude_form(a) for a in codex_entry["args"]]
    assert entry["timeout"] == codex_entry["tool_timeout_sec"] * 1000
    assert entry["timeoutMs"] == entry["timeout"]
    assert set(entry) == {"type", "command", "args", "timeout", "timeoutMs"}
    for value in (entry["command"], *entry["args"]):
        assert (PLUGIN / value.removeprefix(f"{ROOT_VAR}/")).is_file(), value
    # Windows 上 cross-spawn 按 PATHEXT 把 command 解析成同目录的 `.cmd` 半边（#720）
    command = PLUGIN / entry["command"].removeprefix(f"{ROOT_VAR}/")
    assert command.with_name(command.name + ".cmd").is_file()


def test_skills_load_from_the_default_location(manifest):
    """Claude Code 默认扫 `skills/`；显式写 `skills` 会**替换**默认位置，所以不写。"""
    assert "skills" not in manifest
    assert (PLUGIN / "skills" / "tavotto-figure" / "SKILL.md").is_file()


def test_marketplace_points_at_the_release_branch():
    """仓库根即 Claude Code 市场根；插件本体与 Codex 一样来自发行分支（ADR 0043）。"""
    data = _load(MARKETPLACE)
    ((entry),) = data["plugins"]
    assert f"{entry['name']}@{data['name']}" == brand.CLAUDE_PLUGIN_REF
    # 条目名与清单名不同，按清单名装会报 not found（Claude Code 文档的第一条坑）
    assert entry["name"] == _load(CLAUDE_MANIFEST)["name"]
    assert entry["source"] == {
        "source": "git-subdir",
        "url": brand.CODEX_PLUGIN_SOURCE_URL,
        "path": brand.CODEX_PLUGIN_SUBDIR,
        "ref": brand.CODEX_PLUGIN_STABLE_BRANCH,
    }
    # 市场与条目上给人看的仓库地址也只有一个出处
    assert data["owner"]["url"] == entry["homepage"] == brand.REPO_URL
    # 版本只在 plugin.json 里：两处都写时 plugin.json 赢，validate 还要警告
    assert "version" not in entry
    assert (ROOT / brand.CLAUDE_SPARSE_PATHS[0] / "marketplace.json") == MARKETPLACE


def test_new_staging_must_carry_the_claude_manifest():
    """发行分支的插件里没有它，`claude plugin install` 装到的就只是一份 Codex 插件。

    只进 `STAGE_REQUIRED`、不进 `REQUIRED`：后者也用来体检已装的旧版 Codex 插件，
    旧包里本来就没有这份（#559 同一个道理）。
    """
    assert ".claude-plugin/plugin.json" in pluginmanifest.STAGE_REQUIRED
    assert ".claude-plugin/plugin.json" not in pluginmanifest.REQUIRED


def _claude_code_status() -> str:
    matrix = json.loads((ROOT / "docs" / "support-matrix.json").read_text(encoding="utf-8"))
    (host,) = [h for h in matrix["mcp_hosts"]["hosts"] if h["id"] == "claude-code"]
    return host["status"]


def test_install_lines_are_published_where_the_matrix_says():
    """两条安装命令整行等于由常量拼出来的那两行，放在哪由支持矩阵的这一档决定（2026-09-30 用户决定）。

    * `beta`：plugin-stable 已带清单，README 给出这两行（整行相等，不是包含）；
    * 其它：plugin-stable 还没带 `.claude-plugin/`，照 README 装会坏，所以 README 与插件（技能）里
      **一行都不许有**，两行只在待发说明 `UNRELEASED.md` 里等发版。发版时把 README 一节加回、矩阵改回 beta——
      漏了哪一步，这里或 `test_beta_label_follows_the_matrix_and_its_evidence` 就红。
    """
    expected = [
        " ".join(
            [
                "claude plugin marketplace add",
                brand.CLAUDE_MARKETPLACE,
                *[f"--sparse {p}" for p in brand.CLAUDE_SPARSE_PATHS],
            ]
        ),
        f"claude plugin install {brand.CLAUDE_PLUGIN_REF}",
    ]
    lines = [ln.strip() for ln in (ROOT / "README.md").read_text(encoding="utf-8").splitlines()]
    found = [ln for ln in lines if re.match(r"claude plugin (marketplace add|install) ", ln)]
    if _claude_code_status() == "beta":
        assert found == expected
        return
    assert found == []
    # 技能随插件发给所有宿主的 agent，也不许先教装法（Codex 在本 PR 评审里指出）
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
        and re.search(
            r"claude plugin (marketplace add|install|update) ",
            (ROOT / rel).read_text(encoding="utf-8"),
        )
    ]
    assert early == [], f"渠道还没 promote，插件里却已经在教 Claude Code 的装法：{early}"
    pending = (ROOT / "docs" / "release-notes" / "UNRELEASED.md").read_text(encoding="utf-8")
    pending = re.sub(r"\s+", " ", pending)
    for line in expected:
        assert f"`{line}`" in pending, f"待发说明里没有 {line!r}：发版时 README 拿什么加回去"


def test_workbuddy_section_installs_the_same_plugin_from_brand():
    """WorkBuddy 章节（ADR 0106）：「添加市场」那一格填的整行就是 `brand.WORKBUDDY_MARKETPLACE`，装的是
    `brand.CLAUDE_PLUGIN_REF`——同一份市场、同一个插件，不另起名字。"""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    section = text.split("### Using Tavotto with WorkBuddy (Beta)", 1)[1].split("\n### ", 1)[0]
    blocks = re.findall(r"```text\n(.*?)\n```", section, flags=re.S)
    assert blocks == [brand.WORKBUDDY_MARKETPLACE]
    assert f"`{brand.CLAUDE_PLUGIN_REF}`" in section
    assert brand.WORKBUDDY_MARKETPLACE == brand.CLAUDE_MARKETPLACE


@pytest.mark.skipif(shutil.which("claude") is None, reason="本机没有 Claude Code CLI")
@pytest.mark.parametrize("target", [PLUGIN, ROOT], ids=["plugin", "marketplace"])
def test_claude_validates_the_manifests(target):
    """真 CLI 的校验（`--strict` 把警告也算失败）。CI 上没有 `claude`，这条只在本机跑。"""
    proc = subprocess.run(
        ["claude", "plugin", "validate", "--strict", str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
