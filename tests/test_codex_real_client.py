"""真 Codex 客户端上的安装 / 升级演练（ADR 0043）。有 `codex` 才执行，否则 skip 并说明。

不碰这台机器上真实的 `~/.codex`：`CODEX_HOME` 与 `HOME` 都指向临时目录；网络也不碰——
仓库地址经 `$HOME/.gitconfig` 的 `url.<本地 bare>.insteadOf` 改写到一个临时 bare 仓库
（Codex 的用户发起的 git 操作保留用户 git 配置，0.151.0 实测）。

三段：
1. **旧用户形态**：main 上的 marketplace 清单是 `local ./codex-plugin`，README 的两条命令原样
   装——装到的是快照目录里的插件；
2. **切换后**：main 上的清单改成 `git-subdir → plugin-stable`，`codex plugin marketplace
   upgrade tavotto` 之后插件来自发行分支、版本变、旧缓存被客户端换掉；
3. **诊断**：`tavotto codex doctor --json` 的 summary 回答四问，画布按随包清单核对。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support import pluginkit as kit

ROOT = kit.ROOT
CODEX = shutil.which("codex")

pytestmark = pytest.mark.skipif(
    CODEX is None or os.environ.get("TAVOTTO_SKIP_REAL_CODEX") == "1",
    reason="没有 codex CLI（或 TAVOTTO_SKIP_REAL_CODEX=1），真客户端演练未执行",
)

GITHUB_URL = "https://github.com/Tavotto/Tavotto"


def _write_old_main(work: Path, remote: Path) -> str:
    """一个「旧形态」的 main：marketplace 清单是 local ./codex-plugin，插件目录带画布。"""
    kit.git("init", "--quiet", "-b", "main", cwd=work)
    (work / ".agents" / "plugins").mkdir(parents=True)
    mk = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
    mk["plugins"][0]["source"] = {"source": "local", "path": "./codex-plugin"}
    (work / ".agents" / "plugins" / "marketplace.json").write_text(
        json.dumps(mk, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    kit.synthetic_staging(work / "codex-plugin", version="0.13.0", widget_salt="old")
    (work / "codex-plugin" / "plugin-build.json").unlink()  # 旧形态：源码目录里没有清单
    kit.git("add", "-A", cwd=work)
    kit.git("commit", "--quiet", "-m", "old shape", cwd=work)
    sha = kit.git("rev-parse", "HEAD", cwd=work)
    kit.git("push", "--quiet", str(remote), "HEAD:refs/heads/main", cwd=work)
    return sha


def _switch_main(work: Path, remote: Path) -> None:
    mk_path = work / ".agents" / "plugins" / "marketplace.json"
    mk = json.loads(mk_path.read_text(encoding="utf-8"))
    mk["plugins"][0]["source"] = {
        "source": "git-subdir",
        "url": GITHUB_URL + ".git",
        "path": "./codex-plugin",
        "ref": "plugin-stable",
    }
    mk_path.write_text(json.dumps(mk, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    kit.git("add", "-A", cwd=work)
    kit.git("commit", "--quiet", "-m", "switch marketplace to plugin-stable", cwd=work)
    kit.git("push", "--quiet", str(remote), "HEAD:refs/heads/main", cwd=work)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    home.mkdir()
    codex_home.mkdir()
    remote = tmp_path / "remote.git"
    kit.git("init", "--quiet", "--bare", str(remote))
    (home / ".gitconfig").write_text(
        f'[url "file://{remote}"]\n\tinsteadOf = {GITHUB_URL}.git\n'
        f'[url "file://{remote}"]\n\tinsteadOf = {GITHUB_URL}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    for name in ("XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL"):
        monkeypatch.delenv(name, raising=False)
    return {"home": home, "codex_home": codex_home, "remote": remote}


def _codex(*args: str) -> dict:
    proc = subprocess.run(
        [CODEX, *args, "--json"], capture_output=True, text=True, encoding="utf-8", timeout=300
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


def _doctor() -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "tavotto.cli_entry", "codex", "doctor", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_old_user_is_moved_to_the_stable_channel_by_marketplace_upgrade(isolated, tmp_path):
    stage = kit.load_script("plugin_stage")
    pub = kit.load_script("plugin_publish")
    remote = isolated["remote"]
    work = tmp_path / "main-work"
    work.mkdir()
    main_sha = _write_old_main(work, remote)

    # ---- 1. 旧用户：README 的两条命令原样 ----
    _codex(
        "plugin",
        "marketplace",
        "add",
        "Tavotto/Tavotto",
        "--sparse",
        ".agents/plugins",
        "--sparse",
        "codex-plugin",
    )
    added = _codex("plugin", "add", "tavotto@tavotto")
    assert added["version"] == "0.13.0"
    old_dir = Path(added["installedPath"])
    assert old_dir.is_dir()
    listing = _codex("plugin", "list", "-m", "tavotto")
    assert listing["installed"][0]["source"]["source"] == "local"
    d = _doctor()
    assert d["ok"], d
    assert d["summary"]["channel"]["channel"] == "legacy-local"
    assert d["summary"]["canvas"]["complete"] is True
    assert d["summary"]["canvas"]["verified_against_manifest"] is False

    # ---- 2. 发行分支上有 0.14.0；main 切到 git-subdir；用户只跑 marketplace upgrade ----
    staging = tmp_path / "stage14"
    manifest = kit.synthetic_staging(
        staging, source_sha=main_sha, version="0.14.0", widget_salt="v14"
    )
    rc = pub.main(
        [
            "bootstrap",
            "--remote",
            str(remote),
            "--staging",
            str(staging),
            "--source-sha",
            main_sha,
            "--engine-check",
            "none",
            "--reason",
            "real client test",
            "--yes",
        ]
    )
    assert rc == 0
    _switch_main(work, remote)
    up = _codex("plugin", "marketplace", "upgrade", "tavotto")
    assert up["errors"] == [] and "tavotto" in up["selectedMarketplaces"]
    listing = _codex("plugin", "list", "-m", "tavotto")
    entry = listing["installed"][0]
    assert entry["version"] == "0.14.0", "marketplace upgrade 之后插件应来自发行分支"
    assert entry["source"]["source"] == "git-subdir" and entry["source"]["ref"] == "plugin-stable"
    cache = isolated["codex_home"] / "plugins" / "cache" / "tavotto" / "tavotto"
    assert sorted(p.name for p in cache.iterdir()) == ["0.14.0"], "客户端换掉了旧缓存"
    installed = cache / "0.14.0"
    assert (
        stage.verify_dir(
            installed, installed=True, expect_content_digest=manifest["content_digest"]
        )
        == []
    )

    # ---- 3. 诊断四问 ----
    d = _doctor()
    assert d["ok"], d
    s = d["summary"]
    assert s["plugin"]["version"] == "0.14.0"
    assert s["plugin"]["install_dir"] == str(installed)
    assert s["channel"]["channel"] == "stable"
    assert s["canvas"]["complete"] is True and s["canvas"]["verified_against_manifest"] is True
    assert s["engine"]["satisfied"] is True

    # ---- 显式再装一次 = 幂等升级，不破坏别的配置 ----
    again = _codex("plugin", "add", "tavotto@tavotto")
    assert again["version"] == "0.14.0"
    config = (isolated["codex_home"] / "config.toml").read_text(encoding="utf-8")
    assert "[marketplaces.tavotto]" in config and '[plugins."tavotto@tavotto"]' in config
    assert config.count("[marketplaces.") == 1, "不该多出别的 marketplace"


def test_fresh_user_installs_the_stable_channel_directly(isolated, tmp_path):
    """全新安装：main 已是 git-subdir，一次 add 装到的就是发行分支的内容。"""
    stage = kit.load_script("plugin_stage")
    pub = kit.load_script("plugin_publish")
    remote = isolated["remote"]
    work = tmp_path / "main-work"
    work.mkdir()
    main_sha = _write_old_main(work, remote)
    staging = tmp_path / "stage"
    manifest = kit.synthetic_staging(staging, source_sha=main_sha, version="0.14.0")
    assert (
        pub.main(
            [
                "bootstrap",
                "--remote",
                str(remote),
                "--staging",
                str(staging),
                "--source-sha",
                main_sha,
                "--engine-check",
                "none",
                "--reason",
                "real client test",
                "--yes",
            ]
        )
        == 0
    )
    _switch_main(work, remote)
    _codex("plugin", "marketplace", "add", "Tavotto/Tavotto", "--sparse", ".agents/plugins")
    added = _codex("plugin", "add", "tavotto@tavotto")
    installed = Path(added["installedPath"])
    assert added["version"] == "0.14.0"
    assert (
        stage.verify_dir(
            installed, installed=True, expect_content_digest=manifest["content_digest"]
        )
        == []
    )
    assert stage.serve_check(installed, sys.executable) == []
    d = _doctor()
    assert d["ok"], d
    assert d["summary"]["channel"]["channel"] == "stable"
