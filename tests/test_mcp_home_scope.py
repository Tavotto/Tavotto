"""主目录兜底 `TAVOTTO_MCP_SCOPE=home`（用户 2026-11-04，ADR 0009 §1b）。

主语：**MCP 服务端这条连接的授权边界**——「接入 AI 编辑器」按钮给不报项目目录的宿主（Claude 桌面版、Trae……）
写了这个变量之后：宿主自己给的目录仍优先；没给时范围是主目录下的具体项目；主目录本身、敏感目录
（与改图助手同一份黑名单 `engine/fixtools/readscope.py`）在打开目标、命令行参数路径、会话恢复三处都拦；
用户脚本能写的地方收窄到这个项目。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto.engine import hostconnect  # noqa: E402
from tavotto_mcp import bridge, roots  # noqa: E402
from tavotto_mcp.roots import (  # noqa: E402
    ROOTS_ENV,
    SCOPE_ENV,
    SCOPE_HOME,
    SOURCE_HOME_SCOPE,
    WORKSPACE_ENVS,
    RootAuthority,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "papers" / "figs").mkdir(parents=True)
    (h / ".ssh").mkdir()
    monkeypatch.setenv("HOME", str(h))
    for name in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH"):
        monkeypatch.delenv(name, raising=False)
    # 真实账户目录（pwd 那一格）也在 home_dirs 里：只留这一个，断言才不依赖跑用例的机器
    monkeypatch.setattr(bridge, "home_dirs", lambda: (os.path.realpath(h),))
    monkeypatch.setattr(roots, "home_dirs", lambda: (os.path.realpath(h),))
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    cwd = tmp_path / "host-app-dir"  # 宿主进程的 cwd：一个与用户项目无关的目录
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.delenv(ROOTS_ENV, raising=False)
    for name in WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(SCOPE_ENV, SCOPE_HOME)
    bridge._ROOT_AUTHORITY.reset()
    yield h
    bridge._ROOT_AUTHORITY.reset()


def _real(p) -> str:
    return os.path.realpath(str(p))


def test_constants_are_the_same_on_both_sides():
    assert (hostconnect.SCOPE_ENV, hostconnect.SCOPE_HOME) == (SCOPE_ENV, SCOPE_HOME)
    assert hostconnect.ROOTS_ENV == ROOTS_ENV


def test_without_the_variable_nothing_changes(home, tmp_path, monkeypatch):
    monkeypatch.delenv(SCOPE_ENV)
    a = RootAuthority(str(tmp_path / "plugin"))
    snap = a.snapshot()
    assert snap.source == "cwd" and snap.roots == (_real(tmp_path / "host-app-dir"),)


def test_home_scope_is_home_plus_a_safe_cwd(home, tmp_path):
    """ZCode 项目对话的 cwd 就是项目，可能在主目录外（D: 盘）：一并收下。"""
    a = RootAuthority(str(tmp_path / "plugin"))
    snap = a.snapshot()
    assert snap.source == SOURCE_HOME_SCOPE
    assert snap.roots == (_real(home), _real(tmp_path / "host-app-dir"))
    assert a.diagnostics()["authorization"] is None


@pytest.mark.parametrize("where", ["inside_home", "home", "fs_root", "plugin"])
def test_an_unsafe_or_redundant_cwd_adds_nothing(home, tmp_path, monkeypatch, where):
    target = {
        "inside_home": home / "papers",
        "home": home,
        "fs_root": Path(Path(tmp_path).anchor),
        "plugin": tmp_path / "plugin",
    }[where]
    monkeypatch.chdir(target)
    snap = RootAuthority(str(tmp_path / "plugin")).snapshot()
    assert snap.source == SOURCE_HOME_SCOPE and snap.roots == (_real(home),)


def test_host_roots_still_win(home, tmp_path):
    a = RootAuthority(str(tmp_path / "plugin"))
    a.observe_client("2025-06-18", {"roots": {}}, {"name": "x"})
    assert a.snapshot().source == "mcp_roots_pending"  # 还没问过宿主：先问，不抢先落到主目录
    project = home / "papers" / "figs"
    a.accept_protocol_result({"roots": [{"uri": project.as_uri()}]})
    snap = a.snapshot()
    assert snap.source == "mcp_roots" and snap.roots == (_real(project),)


@pytest.mark.parametrize("how", ["home_only", "empty", "error", "no_response"])
def test_unusable_host_roots_fall_back_to_home_scope(home, tmp_path, how):
    a = RootAuthority(str(tmp_path / "plugin"))
    a.observe_client("2025-06-18", {"roots": {}}, {"name": "x"})
    if how == "home_only":
        a.accept_protocol_result({"roots": [{"uri": home.as_uri()}]})
    elif how == "empty":
        a.accept_protocol_result({"roots": []})
    else:
        a.fail_protocol("x", state=how)
    assert a.snapshot().source == SOURCE_HOME_SCOPE


def test_explicit_roots_are_never_widened(home, tmp_path, monkeypatch):
    project = home / "papers" / "figs"
    monkeypatch.setenv(ROOTS_ENV, str(project))
    snap = RootAuthority(str(tmp_path / "plugin")).snapshot()
    assert snap.source == "explicit_env" and snap.roots == (_real(project),)


def test_a_project_inside_home_opens(home):
    fig = home / "papers" / "figs" / "plot.py"
    fig.write_text("", encoding="utf-8")
    assert bridge.check_scope(str(fig)) == _real(fig)
    assert bridge.check_scope(str(fig.parent)) == _real(fig.parent)


@pytest.mark.parametrize(
    "rel",
    [
        ".",  # 整个主目录
        "plot.py",  # 直接放在主目录里的脚本：项目会是整个主目录
        ".ssh",
        ".ssh/id_rsa",
        "papers/figs/.env",
    ],
)
def test_home_itself_and_sensitive_places_are_refused(home, rel):
    target = home / rel
    if rel.endswith((".py", "id_rsa", ".env")):
        target.write_text("", encoding="utf-8")
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope(str(target))
    assert exc.value.code == roots.CODE_PATH_DENIED


def test_a_symlink_into_a_sensitive_place_is_refused(home):
    link = home / "papers" / "keys"
    try:
        link.symlink_to(home / ".ssh", target_is_directory=True)
    except OSError:
        pytest.skip("这台机器不能建符号链接")
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope(str(link))
    assert exc.value.code == roots.CODE_PATH_DENIED


def test_outside_home_is_out_of_scope(home, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope(str(other))
    assert exc.value.code == "path_out_of_scope"


def test_a_project_in_the_cwd_root_outside_home_opens_but_key_files_do_not(home, tmp_path):
    cwd = tmp_path / "host-app-dir"
    fig = cwd / "plot.py"
    fig.write_text("", encoding="utf-8")
    assert bridge.check_scope(str(fig)) == _real(fig)
    key = cwd / "server.pem"
    key.write_text("", encoding="utf-8")
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope(str(key))
    assert exc.value.code == roots.CODE_PATH_DENIED


def test_without_the_engine_blacklist_everything_is_refused(home, monkeypatch):
    """旧引擎没有 readscope：失败封闭，不退回「整个主目录都行」。"""
    real_optional = bridge._optional_engine
    monkeypatch.setattr(
        bridge,
        "_optional_engine",
        lambda name: None if name == "fixtools.readscope" else real_optional(name),
    )
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope(str(home / "papers" / "figs"))
    assert exc.value.code == roots.CODE_PATH_DENIED


def test_argv_paths_into_sensitive_places_escape(home):
    roots_now = bridge.allowed_roots()
    cwds = [str(home / "papers" / "figs")]
    assert bridge._argv_path_escapes(str(home / "papers" / "out.png"), roots_now, cwds) is None
    assert bridge._argv_path_escapes(str(home / ".ssh" / "authorized_keys"), roots_now, cwds)
    assert bridge._argv_path_escapes("../../.ssh/authorized_keys", roots_now, cwds)


def test_scripts_may_only_write_into_their_project(home, monkeypatch):
    monkeypatch.setattr(bridge, "engine_features", lambda: {bridge.WRITE_ROOT_TRIPWIRE_FEATURE})
    project = str(home / "papers" / "figs")
    assert bridge._tripwire_roots(project) == (project,)
    monkeypatch.delenv(SCOPE_ENV)
    monkeypatch.setenv(ROOTS_ENV, project)
    assert bridge._tripwire_roots(project) == (_real(project),)
