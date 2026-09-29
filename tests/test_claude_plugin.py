"""Claude Code 插件（`codex-plugin/.claude-plugin/`）的形状看护（ADR 0103）。

同一份插件目录同时是 Codex 插件与 Claude Code 插件：根 `.mcp.json` 是 Codex 形状，
Claude Code **也会读它**，然后再把 `.claude-plugin/plugin.json` 的 `mcpServers` 按名字
合并进来——同名的后者整条替换前者。这里的断言盯的是「坏了不报错、只是另一家悄悄
起不来」的那几处：名字对不上、字段抄成 Codex 的、超时漏换算、版本漂开、文档与常量漂开。
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
MCP_JSON = PLUGIN / ".mcp.json"
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


def test_server_names_cover_the_codex_mcp_json(manifest):
    """**每个** Codex `.mcp.json` 里的 server 名都要在 Claude 清单里有同名一条。

    Claude Code 先读插件根的 `.mcp.json`，再合并 plugin.json 的 `mcpServers`，同名才替换。
    名字一旦对不上，Codex 那条（相对路径 `./mcp/launch.cmd`、没有 Claude 认的 cwd）会
    作为第二个 server 被 Claude Code 起一遍并失败——2.1.283 实测：
    `plugin:tavotto:tavotto: ./mcp/launch.cmd ./mcp/server.py - ✘ Failed to connect`。
    """
    codex_names = set(_load(MCP_JSON)["mcpServers"])
    assert set(manifest["mcpServers"]) == codex_names


def test_server_entry_is_the_codex_launcher_in_claude_form(manifest, codex_entry):
    """同一个启动器、同一个 server.py，只换成 Claude Code 的写法。

    * 路径从 `./` 相对插件根改成 `${CLAUDE_PLUGIN_ROOT}/` 绝对：Claude Code 起 stdio
      server 时只在 command / args / env 里展开这个变量，`cwd` 这个 Codex 字段它不认；
    * 超时唯一出处是 `.mcp.json` 的 `tool_timeout_sec`，Claude Code 的 `timeout` 是毫秒；
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
    assert set(entry) == {"type", "command", "args", "timeout"}
    for value in (entry["command"], *entry["args"]):
        assert (PLUGIN / value.removeprefix(f"{ROOT_VAR}/")).is_file(), value


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


def test_readme_install_lines_come_from_brand():
    """README 的两条安装命令整行等于由常量拼出来的那两行（整行相等，不是包含）。"""
    lines = [ln.strip() for ln in (ROOT / "README.md").read_text(encoding="utf-8").splitlines()]
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
    found = [ln for ln in lines if re.match(r"claude plugin (marketplace add|install) ", ln)]
    assert found == expected


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
