"""本机兜底 `TAVOTTO_MCP_SCOPE=local`（维护者 2026-11-04 / 11-05，ADR 0009 §1b）。

主语：**MCP 服务端这条连接的授权边界**——「接入 AI 编辑器」按钮给不报项目目录的宿主（Claude 桌面版、Trae……）
写了这个变量之后：宿主自己给的目录仍优先；没给时范围是**本机任意位置**（用户的数据可能在任何盘、任何目录）；
只拦凭据类文件（与改图助手同一份黑名单 `engine/fixtools/readscope.py`），打开目标、命令行参数路径、会话恢复
三处都拦；这一档不装写入根绊线（与 GUI 里自己运行同一条线）。
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
    SCOPE_LOCAL,
    SOURCE_LOCAL_SCOPE,
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
    (tmp_path / "plugin").mkdir()
    cwd = tmp_path / "host-app-dir"  # 宿主进程的 cwd：一个与用户项目无关的目录
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.delenv(ROOTS_ENV, raising=False)
    for name in WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(SCOPE_ENV, SCOPE_LOCAL)
    bridge._ROOT_AUTHORITY.reset()
    yield h
    bridge._ROOT_AUTHORITY.reset()


def _real(p) -> str:
    return os.path.realpath(str(p))


def _file(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")
    return p


def test_constants_are_the_same_on_both_sides():
    assert (hostconnect.SCOPE_ENV, hostconnect.SCOPE_LOCAL) == (SCOPE_ENV, SCOPE_LOCAL)
    assert hostconnect.ROOTS_ENV == ROOTS_ENV


def test_without_the_variable_nothing_changes(home, tmp_path, monkeypatch):
    monkeypatch.delenv(SCOPE_ENV)
    snap = RootAuthority(str(tmp_path / "plugin")).snapshot()
    assert snap.source == "cwd" and snap.roots == (_real(tmp_path / "host-app-dir"),)


def test_local_scope_replaces_the_cwd_fallback(home, tmp_path):
    a = RootAuthority(str(tmp_path / "plugin"))
    snap = a.snapshot()
    assert snap.source == SOURCE_LOCAL_SCOPE and snap.roots == roots.local_roots()
    assert a.diagnostics()["authorization"] is None


def test_host_roots_still_win(home, tmp_path):
    a = RootAuthority(str(tmp_path / "plugin"))
    a.observe_client("2025-06-18", {"roots": {}}, {"name": "x"})
    assert a.snapshot().source == "mcp_roots_pending"  # 还没问过宿主：先问，不抢先落下来
    project = home / "papers" / "figs"
    a.accept_protocol_result({"roots": [{"uri": project.as_uri()}]})
    snap = a.snapshot()
    assert snap.source == "mcp_roots" and snap.roots == (_real(project),)


@pytest.mark.parametrize("how", ["home_only", "empty", "error", "no_response"])
def test_unusable_host_roots_fall_back_to_local_scope(home, tmp_path, how):
    a = RootAuthority(str(tmp_path / "plugin"))
    a.observe_client("2025-06-18", {"roots": {}}, {"name": "x"})
    if how == "home_only":
        a.accept_protocol_result({"roots": [{"uri": home.as_uri()}]})
    elif how == "empty":
        a.accept_protocol_result({"roots": []})
    else:
        a.fail_protocol("x", state=how)
    assert a.snapshot().source == SOURCE_LOCAL_SCOPE


def test_explicit_roots_are_never_widened(home, tmp_path, monkeypatch):
    project = home / "papers" / "figs"
    monkeypatch.setenv(ROOTS_ENV, str(project))
    snap = RootAuthority(str(tmp_path / "plugin")).snapshot()
    assert snap.source == "explicit_env" and snap.roots == (_real(project),)


@pytest.mark.parametrize(
    "where,is_file",
    [
        ("home/papers/figs/plot.py", True),
        ("home/plot.py", True),  # 直接放在主目录里
        ("home", False),  # 整个主目录也行：不替用户加锁
        ("elsewhere/data/plot.py", True),  # 主目录外（Windows 的 D: 盘、移动硬盘……）
        ("home/papers/figs/token_counts.csv", True),  # 名字像但不是凭据形状
    ],
)
def test_anywhere_on_this_machine_opens(home, tmp_path, where, is_file):
    target = tmp_path / where
    if is_file:
        _file(target)
    assert bridge.check_scope(str(target)) == _real(target)


@pytest.mark.parametrize(
    "rel,is_file",
    [
        (".ssh", False),
        (".ssh/id_rsa", True),
        ("papers/figs/.env", True),
        ("papers/figs/server.pem", True),
        (".aws/credentials", True),
    ],
)
def test_credential_files_and_places_are_refused(home, rel, is_file):
    target = home / rel
    if is_file:
        _file(target)
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope(str(target))
    assert exc.value.code == roots.CODE_PATH_DENIED


def test_a_key_file_outside_home_is_refused_too(home, tmp_path):
    key = _file(tmp_path / "elsewhere" / "deploy" / "id_ed25519")
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope(str(key))
    assert exc.value.code == roots.CODE_PATH_DENIED


def test_a_symlink_into_a_credential_dir_is_refused(home):
    link = home / "papers" / "keys"
    try:
        link.symlink_to(home / ".ssh", target_is_directory=True)
    except OSError:
        pytest.skip("这台机器不能建符号链接")
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope(str(link))
    assert exc.value.code == roots.CODE_PATH_DENIED


def test_relative_paths_need_an_absolute_one(home):
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope("papers/figs")
    assert exc.value.code == "ambiguous_workspace_root"


def test_without_the_engine_blacklist_everything_is_refused(home, monkeypatch):
    """旧引擎没有 readscope：失败封闭，不退回「什么都不拦」。"""
    real_optional = bridge._optional_engine
    monkeypatch.setattr(
        bridge,
        "_optional_engine",
        lambda name: None if name == "fixtools.readscope" else real_optional(name),
    )
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope(str(home / "papers" / "figs"))
    assert exc.value.code == roots.CODE_PATH_DENIED


def test_argv_paths_only_escape_into_credential_places(home, tmp_path):
    roots_now = bridge.allowed_roots()
    cwds = [str(home / "papers" / "figs")]
    assert bridge._argv_path_escapes(str(home / "papers" / "out.png"), roots_now, cwds) is None
    assert (
        bridge._argv_path_escapes(str(tmp_path / "elsewhere" / "out.png"), roots_now, cwds) is None
    )
    assert bridge._argv_path_escapes(str(home / ".ssh" / "authorized_keys"), roots_now, cwds)
    assert bridge._argv_path_escapes("../../.ssh/authorized_keys", roots_now, cwds)


def test_no_write_tripwire_in_local_scope(home, monkeypatch):
    monkeypatch.setattr(bridge, "engine_features", lambda: {bridge.WRITE_ROOT_TRIPWIRE_FEATURE})
    project = str(home / "papers" / "figs")
    assert bridge._tripwire_roots(project) is None
    monkeypatch.delenv(SCOPE_ENV)
    monkeypatch.setenv(ROOTS_ENV, project)
    assert bridge._tripwire_roots(project) == (_real(project),)
