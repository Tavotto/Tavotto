"""严格同源对：「pip install 从哪个包源装」怎么问（#737 / #767）。

引擎 `engine/deprepair`（问「用户配没配包源」，决定要不要换镜像、诊断里的 custom_index）与插件
`codex-plugin/mcp/server.py`（问「pip 从哪个索引装」，决定升级命令要不要带 `--index-url`）**都不自己读配置**：
同一段探测脚本（`PIP_OPTIONS_PROBE` ↔ `_PIP_OPTIONS_PROBE`，逐字相同）在目标解释器里让 pip 自己把 install
选项解析一遍。自己读 `pip config list` 的做法被实测否定（#767 两轮 Codex P1）：它的打印顺序不是覆盖顺序、
`config get` 不认 `PIP_CONFIG_FILE`。

判据的主语：
1. 两侧的探测脚本与「自定义索引」判据逐字 / 逐条一致（插件 import 不到引擎，是两份）；
2. **真 pip** 上（`tests/golden/pip_options_cases.json` 的每条情形），引擎解析出的 index-url、插件的结论、
   与真装包时 pip 打印的 `Looking in indexes:` 三方一致——在当前 pip 与一个旧版 pip（23.x）上各跑一遍。
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import deprepair

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))
launcher = importlib.import_module("server")

CASES = json.loads(
    (ROOT / "tests" / "golden" / "pip_options_cases.json").read_text(encoding="utf-8")
)["cases"]
IDS = [c["name"] for c in CASES]
#: 版本对：当前（venv 自带的）与一个旧版。旧版装不上（没网、这个 Python 上跑不起来）就 skip 并说明
OLD_PIP = "23.3.2"


# ---------------------------------------------------------------- 1. 两份实现逐字一致
def test_both_sides_run_the_same_probe():
    assert launcher._PIP_OPTIONS_PROBE == deprepair.PIP_OPTIONS_PROBE
    assert launcher._PYPI_DEFAULT_INDEX == deprepair.PYPI_DEFAULT_INDEX


OPTION_VECTORS = [
    ({"index_url": "https://pypi.org/simple", "extra_index_urls": []}, False),
    ({"index_url": "https://PyPI.org/simple/", "extra_index_urls": []}, False),
    ({"index_url": "", "extra_index_urls": []}, True),
    ({"index_url": "https://mirrors.aliyun.com/pypi/simple/", "extra_index_urls": []}, True),
    ({"index_url": "https://pypi.org/simple", "extra_index_urls": ["https://x.example"]}, True),
]


@pytest.mark.parametrize("opts,custom", OPTION_VECTORS)
def test_both_sides_judge_a_custom_index_the_same(opts, custom):
    assert deprepair.options_name_a_custom_index(opts) is custom
    assert launcher.options_name_a_custom_index(opts) is custom


@pytest.mark.parametrize(
    "out",
    [
        "",
        "garbage\n",
        '{"error": "ImportError"}\n',
        '{"pip_version": "9.0"}\n',
        'Warning: something\n{"pip_version": "25.3", "index_url": "https://pypi.org/simple",'
        ' "extra_index_urls": [], "no_index": false, "find_links": []}\n',
    ],
)
def test_both_sides_parse_the_probe_output_the_same(out):
    assert deprepair.parse_pip_options(out) == launcher.parse_pip_options(out)


# ---------------------------------------------------------------- 2. 真 pip 三方对拍
pytestmark_posix = pytest.mark.skipif(
    os.name == "nt",
    reason="用例摆的用户配置位置（~/.pip、$XDG_CONFIG_HOME/pip）是 POSIX 的形状；Windows 上的跨平台情形"
    "（PIP_CONFIG_FILE + site 文件）在 tests/test_mcp_diagnose.py 里真跑",
)


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


@pytest.fixture(scope="module", params=["current", OLD_PIP])
def pip_venv(request, tmp_path_factory):
    if os.name == "nt":
        pytest.skip("POSIX 用例")
    venv = tmp_path_factory.mktemp(f"pip-{request.param}") / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True, encoding="utf-8"
    )
    py = _venv_python(venv)
    if request.param != "current":
        done = subprocess.run(
            [str(py), "-m", "pip", "install", "-q", f"pip=={request.param}"],
            capture_output=True,
            encoding="utf-8",
            timeout=300,
        )
        if done.returncode != 0:
            pytest.skip(
                f"装不上 pip {request.param}（没网或这个 Python 上不支持）：{done.stdout[-300:]}"
            )
    version = subprocess.run(
        [str(py), "-m", "pip", "--version"], capture_output=True, encoding="utf-8"
    ).stdout.split()[1]
    if request.param != "current":
        assert version == request.param, f"前提：旧版真的装上了（实得 {version}）"
    return venv, version


def _lay_out(case: dict, venv: Path, home: Path) -> dict:
    """按 `files` 摆配置文件、按 `env` 组环境（不继承外面任何 `PIP_*`）；回子进程环境。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PIP_", "PIPX_"))}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"))
    site = venv / "pip.conf"
    site.unlink(missing_ok=True)
    places = {
        "legacy_user": home / ".pip" / "pip.conf",
        "user": home / ".config" / "pip" / "pip.conf",
        "site": site,
        "config_file": home / "envfile.conf",
    }
    for f in case["files"]:
        path = places[f["where"]]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f["text"], encoding="utf-8")
        if f["where"] == "config_file":
            env["PIP_CONFIG_FILE"] = str(path)
    env.update(case["env"])
    return env


def _index_pip_install_uses(py: Path, env: dict) -> str:
    """真装包时 pip 打印的 `Looking in indexes:`（找一个不存在的包，不装任何东西；索引地址都是不存在的主机，
    立刻失败）。这是三方对拍里**不经过探测脚本**的那一方。"""
    out = subprocess.run(
        [
            str(py),
            "-m",
            "pip",
            "install",
            "--dry-run",
            "--no-deps",
            "--retries",
            "0",
            "--timeout",
            "1",
            "-v",
            "tavotto-no-such-package-767",
        ],
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    ).stdout
    m = re.search(r"Looking in indexes: (\S+?)(?:,|\s|$)", out)
    # PyPI 默认时 pip 不打印这一行（只有非默认索引才说）
    return m.group(1) if m else deprepair.PYPI_DEFAULT_INDEX


@pytestmark_posix
@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_engine_plugin_and_pip_itself_agree(case, pip_venv, tmp_path, monkeypatch):
    venv, version = pip_venv
    py = _venv_python(venv)
    home = tmp_path / "home"
    home.mkdir()
    env = _lay_out(case, venv, home)
    truth = _index_pip_install_uses(py, env)
    assert truth == case["index_url"], f"前提：pip {version} 真装包用的是 {truth}"

    # 引擎：子进程环境取自本进程的 os.environ
    for k in [k for k in os.environ if k.startswith(("PIP_", "PIPX_"))]:
        monkeypatch.delenv(k)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    opts = deprepair.pip_install_options(str(py))
    assert opts is not None and opts["pip_version"] == version, opts
    assert opts["index_url"] == truth, (version, opts)
    assert deprepair.custom_package_index(str(py)) is case["custom"]

    # 插件：显式传环境
    got = launcher.pip_index_of(str(py), env)
    if case["custom"]:
        assert got is not None and got["url"] == launcher._redact_url(truth), (version, got)
    else:
        assert got is None, (version, got)


def test_the_cases_cover_what_was_measured():
    """前提：维护者 2026-10-01 实测的五种情形 + 重复键都在用例里。"""
    wheres = [{f["where"] for f in c["files"]} for c in CASES]
    assert any({"legacy_user", "user", "site"} <= w for w in wheres)
    assert any("config_file" in w for w in wheres)
    assert any("PIP_INDEX_URL" in c["env"] for c in CASES)
    assert any("[download]" in f["text"] for c in CASES for f in c["files"])
    assert any(not c["files"] and not c["env"] for c in CASES)
    assert any(
        [f["where"] for f in c["files"]] == ["legacy_user", "user"] and c["custom"] for c in CASES
    )
