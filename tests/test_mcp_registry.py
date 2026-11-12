"""MCP Registry 清单 `server.json`：它说的话要与真正发出去的 wheel 对得上。

Registry 拿 `server.json` 去 PyPI 找包、在包的 README（= PyPI 项目描述）里找
`mcp-name: <name>` 认领所有权，客户端再照 `packages[0]` 拼出启动命令。任何一处漂开，
表现都不是测试失败而是「上架被拒」或「装上起不来」——所以在这里逐条钉住。
版本号与 `__version__` 一致由 `test_source_hygiene` 的版本清单看着。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tavotto.engine import cli

#: 3.10 没有 tomllib；清单结构与解释器版本无关，3.11+ 的分片照跑
tomllib = pytest.importorskip("tomllib")

ROOT = Path(__file__).resolve().parents[1]
SERVER = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _pypi_package() -> dict:
    pkgs = [p for p in SERVER["packages"] if p["registryType"] == "pypi"]
    assert len(pkgs) == 1, "server.json 里应恰好一条 PyPI 包"
    return pkgs[0]


def test_registry_package_is_the_wheel_we_publish():
    pkg = _pypi_package()
    assert pkg["identifier"] == PYPROJECT["project"]["name"]
    assert pkg["transport"] == {"type": "stdio"}
    # 客户端跑的是 `<入口> <packageArguments...>`：入口就是项目名那条 console script
    assert PYPROJECT["project"]["scripts"][pkg["identifier"]]


def test_registry_launch_argument_is_a_real_subcommand():
    """`tavotto mcp` 是 stdio 服务的那条子命令；改名而清单没跟上，装上就起不来。"""
    args = [a["value"] for a in _pypi_package()["packageArguments"]]
    assert args == ["mcp"]
    assert args[0] in cli.COMMANDS


def test_readme_claims_the_registry_name():
    """PyPI 项目描述就是 README.md；Registry 在里面找 `mcp-name: <name>`，后面要跟边界。"""
    assert PYPROJECT["project"]["readme"] == "README.md"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    names = re.findall(r"mcp-name:\s*(\S+?)(?=\s|-->|<|$)", readme)
    assert names == [SERVER["name"]]


def test_registry_name_is_in_the_repository_namespace():
    """`io.github.<owner>/…` 命名空间靠 GitHub 登录认领，owner 必须是仓库所在的组织。"""
    owner = SERVER["repository"]["url"].removeprefix("https://github.com/").split("/")[0]
    assert SERVER["name"].split("/")[0] == f"io.github.{owner}"
    assert PYPROJECT["project"]["urls"]["Homepage"] == SERVER["repository"]["url"]
