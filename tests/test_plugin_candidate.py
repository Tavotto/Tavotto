"""对**真实构建物**的验收（ADR 0043）：只在 `TAVOTTO_PLUGIN_CANDIDATE` 指向一份解包好的
完整插件时执行——CI 的 `plugin-candidate` job 把 frontend 刚构建、归档、传输过来的候选
解包后指给这里；那个 job 要求这些用例**必须执行、不许 skip**（有产物时 skip 就是空门禁）。

没有产物时（普通 pytest、干净 checkout）它们 skip 并说清原因，**不放宽断言冒充通过**：
真正验证产品画布的判据只在有真实构建物的地方跑。

主语是「解包后那份目录」：不是源码树、不是 CI 里的 staging，而是用户会装到的东西。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import tavotto
from tests.support import pluginkit as kit

ROOT = kit.ROOT
CANDIDATE = os.environ.get("TAVOTTO_PLUGIN_CANDIDATE")

pytestmark = pytest.mark.skipif(
    not CANDIDATE,
    reason="没有真实插件构建物（TAVOTTO_PLUGIN_CANDIDATE 未指向解包后的候选）",
)


@pytest.fixture(scope="module")
def plugin() -> Path:
    p = Path(CANDIDATE or "")
    assert p.is_dir(), f"TAVOTTO_PLUGIN_CANDIDATE 指向的不是目录：{p}"
    return p


def _stage():
    return kit.load_script("plugin_stage")


def _widget_builder():
    return kit.load_script("build_mcp_widget")


def _head() -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


def test_the_candidate_verifies_against_this_checkout(plugin):
    """清单说的 source_sha 就是这次 checkout；内容逐文件对得上；不多不少。"""
    problems = _stage().verify_dir(plugin, source_sha=_head(), version=tavotto.__version__)
    assert problems == [], problems


def test_the_canvas_is_the_one_built_from_this_source(plugin):
    """画布的指纹戳 == 本次源码的指纹：装到的画布就是这份源码的画布，不是缓存的旧货。"""
    stage = _stage()
    canvas = plugin / "mcp" / "widget" / "canvas.html"
    assert (
        stage.widget_problems(canvas, expect_fingerprint=_widget_builder().source_fingerprint())
        == []
    )
    assert canvas.stat().st_size > stage.WIDGET_MIN_BYTES
    manifest = stage.read_manifest(plugin)
    assert manifest["build_inputs_fingerprint"] == _widget_builder().source_fingerprint()
    assert manifest["plugin_version"] == tavotto.__version__


def test_the_server_serves_that_exact_canvas_over_stdio(plugin):
    """从解包目录起 server：资源可读、自包含、与磁盘上那份逐字相同。"""
    problems = _stage().serve_check(plugin, sys.executable)
    assert problems == [], problems


def test_the_candidate_does_not_depend_on_the_source_tree(plugin):
    """脱离源码仓库后仍然自足：相对引用都在包内，没有指向外部的链接或绝对路径。"""
    for p in _stage()._walk(plugin):
        assert not p.is_symlink()
    mcp = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))
    for entry in mcp["mcpServers"].values():
        assert not os.path.isabs(entry["command"]), "发行件里 command 只许裸名字"
        for arg in entry.get("args", []):
            assert not os.path.isabs(arg)
            assert (plugin / arg).is_file() if arg.startswith("./") else True, arg
    assert (plugin / "LICENSE").is_file()


def test_dotfiles_and_modes_survived_the_archive_hop(plugin):
    stage = _stage()
    for rel in (".codex-plugin/plugin.json", ".mcp.json"):
        assert (plugin / rel).is_file(), f"{rel} 在归档传输中丢了"
    manifest = stage.read_manifest(plugin)
    for entry in manifest["files"]:
        assert (plugin / entry["path"]).is_file(), entry["path"]
        if entry["mode"] == "100755" and os.name != "nt":
            assert os.access(plugin / entry["path"], os.X_OK), f"{entry['path']} 丢了可执行位"


def test_an_isolated_install_from_a_local_stable_branch(plugin, tmp_path):
    """把候选投影成一条临时 `plugin-stable`，按 Codex 客户端的克隆形状取回来：字节相同。

    这里不需要 codex CLI（CI 机器上没有）：验的是分支投影 + 稀疏克隆这段；真客户端的
    安装 / 升级演练在 tests/test_codex_real_client.py（有 codex 时执行）。
    """
    pub = kit.load_script("plugin_publish")
    stage = _stage()
    # 临时远端的 main = 本次 checkout 的 HEAD：候选的 source_sha 必须可达 main 才能发布
    shallow = kit.git("rev-parse", "--is-shallow-repository", cwd=ROOT)
    assert shallow != "true", (
        "本仓库是浅克隆，推不出完整历史（shallow update not allowed）——CI 的 plugin-candidate "
        "job 用 fetch-depth: 0；本地先 git fetch --unshallow"
    )
    remote = tmp_path / "remote.git"
    kit.git("init", "--quiet", "--bare", str(remote))
    kit.git("push", "--quiet", str(remote), "HEAD:refs/heads/main", cwd=ROOT)
    manifest = stage.read_manifest(plugin)
    rc = pub.main(
        [
            "bootstrap",
            "--remote",
            str(remote),
            "--staging",
            str(plugin),
            "--source-sha",
            manifest["source_sha"],
            "--engine-check",
            "none",
            "--reason",
            "candidate test",
            "--yes",
        ]
    )
    assert rc == 0
    dest = tmp_path / "sparse"
    kit.git(
        "clone",
        "--quiet",
        "--filter=blob:none",
        "--sparse",
        "--no-checkout",
        str(remote),
        str(dest),
    )
    kit.git("sparse-checkout", "set", "--no-cone", "--", "codex-plugin", cwd=dest)
    kit.git("checkout", "--quiet", "plugin-stable", cwd=dest)
    got = dest / "codex-plugin"
    assert stage.verify_dir(got, expect_content_digest=manifest["content_digest"]) == []
    assert stage.serve_check(got, sys.executable) == []
