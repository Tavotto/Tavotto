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


@pytest.mark.parametrize("name", [".env", "server.pem", "id_ed25519"])
def test_a_credential_named_symlink_to_a_plain_file_is_refused(home, tmp_path, name):
    """词法名字也要判（Codex #1757 P1）：`.env -> plain.txt` 解析后名字就丢了，不能因此放行。"""
    plain = _file(tmp_path / "elsewhere" / "shared" / "plain.txt")
    link = home / "papers" / "figs" / name
    try:
        link.symlink_to(plain)
    except OSError:
        pytest.skip("这台机器不能建符号链接")
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope(str(link))
    assert exc.value.code == roots.CODE_PATH_DENIED
    roots_now = bridge.allowed_roots()
    cwds = [str(home / "papers" / "figs")]
    assert bridge._argv_path_escapes(str(link), roots_now, cwds)
    assert bridge._argv_path_escapes(name, roots_now, cwds)
    # 链接的目标本身是普通文件：直接指它照样能开
    assert bridge.check_scope(str(plain)) == _real(plain)


@pytest.mark.parametrize("bad", ["home", "missing"])
def test_an_unusable_host_workspace_variable_falls_back_to_local_scope(
    home, tmp_path, monkeypatch, bad
):
    """宿主的工作区变量指到主目录 / 不存在的目录：与不可用的 roots/list 一样落到本机兜底（Codex #1757 P2）。"""
    monkeypatch.setenv(WORKSPACE_ENVS[0], str(home if bad == "home" else tmp_path / "gone"))
    snap = RootAuthority(str(tmp_path / "plugin")).snapshot()
    assert snap.source == SOURCE_LOCAL_SCOPE and snap.roots == roots.local_roots()


def test_a_usable_host_workspace_variable_still_wins(home, tmp_path, monkeypatch):
    project = home / "papers" / "figs"
    monkeypatch.setenv(WORKSPACE_ENVS[0], str(project))
    snap = RootAuthority(str(tmp_path / "plugin")).snapshot()
    assert snap.source == f"workspace_env:{WORKSPACE_ENVS[0]}" and snap.roots == (_real(project),)


def test_without_local_scope_an_unusable_workspace_variable_still_fails_closed(
    home, tmp_path, monkeypatch
):
    monkeypatch.delenv(SCOPE_ENV)
    monkeypatch.setenv(WORKSPACE_ENVS[0], str(home))
    snap = RootAuthority(str(tmp_path / "plugin")).snapshot()
    assert snap.source == f"workspace_env:{WORKSPACE_ENVS[0]}" and snap.roots == ()


def test_a_plain_named_symlink_to_a_credential_file_is_refused(home):
    """反方向：`plain.txt -> .env`，词法名字普通、解析后是凭据。"""
    secret = _file(home / "papers" / "figs" / ".env")
    link = home / "papers" / "plain.txt"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("这台机器不能建符号链接")
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope(str(link))
    assert exc.value.code == roots.CODE_PATH_DENIED
    assert bridge._argv_path_escapes(str(link), bridge.allowed_roots(), [str(home)])


def test_a_file_reached_through_a_directory_link_into_ssh_is_refused(home):
    """中间某一段目录是指向 `~/.ssh` 的链接：`papers/keys/notes.txt` 解析后在凭据目录里。"""
    _file(home / ".ssh" / "notes.txt")
    keys = home / "papers" / "keys"
    try:
        keys.symlink_to(home / ".ssh", target_is_directory=True)
    except OSError:
        pytest.skip("这台机器不能建符号链接")
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope(str(keys / "notes.txt"))
    assert exc.value.code == roots.CODE_PATH_DENIED
    assert bridge._argv_path_escapes(
        "keys/notes.txt", bridge.allowed_roots(), [str(home / "papers")]
    )


_POSIX_DOTDOT = pytest.mark.skipif(
    os.name == "nt",
    reason="Windows 的 Win32 路径在打开前先按字面折叠 `..` 再跟链接：`keys\\..\\x` 真的读 `x` 旁边那个，"
    "桥对 NT 路径同样先折叠，两边一致；「链接后再 ..」的落点差只在 POSIX 上存在",
)


@_POSIX_DOTDOT
def test_dotdot_after_a_directory_link_is_resolved_where_the_script_lands(home):
    """`keys -> ~/.ssh/subdir` 时 `keys/../notes.txt` 实际读 `~/.ssh/notes.txt`：真实路径不能先折叠 `..`
    再解析（Codex #1757 r4473502316）。相对、绝对两支都拦。"""
    _file(home / ".ssh" / "subdir" / "x")
    _file(home / ".ssh" / "notes.txt")
    keys = home / "papers" / "keys"
    try:
        keys.symlink_to(home / ".ssh" / "subdir", target_is_directory=True)
    except OSError:
        pytest.skip("这台机器不能建符号链接")
    roots_now = bridge.allowed_roots()
    cwds = [str(home / "papers")]
    assert bridge._argv_path_escapes("keys/../notes.txt", roots_now, cwds)
    assert bridge._argv_path_escapes(str(keys) + "/../notes.txt", roots_now, cwds)


@_POSIX_DOTDOT
def test_dotdot_after_a_directory_link_cannot_leave_explicit_roots(home, tmp_path, monkeypatch):
    project = home / "papers" / "figs"
    outside = tmp_path / "elsewhere" / "deep"
    _file(outside / "x")
    _file(tmp_path / "elsewhere" / "data.csv")
    link = project / "ln"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("这台机器不能建符号链接")
    monkeypatch.delenv(SCOPE_ENV)
    monkeypatch.setenv(ROOTS_ENV, str(project))
    bridge._ROOT_AUTHORITY.reset()
    roots_now = bridge.allowed_roots()
    assert bridge._argv_path_escapes("ln/../data.csv", roots_now, [str(project)])
    assert bridge._argv_path_escapes(str(link) + "/../data.csv", roots_now, [str(project)])


@_POSIX_DOTDOT
def test_a_credential_named_link_reached_through_dotdot_after_a_link_is_refused(home):
    """Codex #1757 r4475737144：`project/server.pem` 是**目录**、`project/keys -> other/deep`、
    `other/server.pem -> other/plain.txt`。`project/keys/../server.pem` 实际经过 `other/server.pem` 这一跳；
    字面折叠后却是那个目录（名字规则不管目录）、解析后又只叫 plain.txt——两点判法两头都放行。逐段解析判的是这一跳。"""
    project = home / "papers" / "project"
    (project / "server.pem").mkdir(parents=True)
    other = home / "papers" / "other"
    (other / "deep").mkdir(parents=True)
    _file(other / "plain.txt")
    try:
        (other / "server.pem").symlink_to(other / "plain.txt")
        (project / "keys").symlink_to(other / "deep", target_is_directory=True)
    except OSError:
        pytest.skip("这台机器不能建符号链接")
    target = str(project / "keys") + "/../server.pem"
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope(target)
    assert exc.value.code == roots.CODE_PATH_DENIED
    roots_now = bridge.allowed_roots()
    assert bridge._argv_path_escapes(target, roots_now, [str(project)])
    assert bridge._argv_path_escapes("keys/../server.pem", roots_now, [str(project)])
    # 叫 server.pem 的那个目录本身照样能进（目录不按名拦），plain.txt 直接读也行
    assert bridge.check_scope(str(project / "server.pem")) == _real(project / "server.pem")
    assert bridge.check_scope(str(other / "plain.txt")) == _real(other / "plain.txt")


def test_a_link_loop_is_refused_not_followed_forever(home):
    loop = home / "papers" / "loop"
    try:
        loop.symlink_to(loop)
    except OSError:
        pytest.skip("这台机器不能建符号链接")
    assert bridge._argv_path_escapes(str(loop / "x"), bridge.allowed_roots(), None)


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/proc/1234/fd/3", True),
        ("/proc/self/fd/3", True),
        ("/proc/thread-self/root/etc/passwd", True),
        ("/proc/1234/environ", True),
        ("/dev/fd/3", True),
        ("/proc/cpuinfo", False),
        ("/dev/null", False),
        ("/project/proc/1234/fd", False),
    ],
)
@pytest.mark.skipif(os.name == "nt", reason="POSIX 路径形状")
def test_process_views_are_recognised(path, expected):
    assert bridge._is_process_view(path) is expected


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="需要 procfs")
def test_an_open_deleted_credential_is_not_reachable_through_proc(home):
    """Codex #1757 r4476078785：打开 `server.pem`、删掉目录项，`/proc/<pid>/fd/<fd>` 仍读得到内容。"""
    secret = _file(home / "papers" / "server.pem")
    with open(secret, encoding="utf-8") as fh:
        secret.unlink()
        path = f"/proc/{os.getpid()}/fd/{fh.fileno()}"
        assert bridge._argv_path_escapes(path, bridge.allowed_roots(), None)
        assert bridge._argv_path_escapes(
            f"/proc/self/fd/{fh.fileno()}", bridge.allowed_roots(), None
        )
        with pytest.raises(bridge.BridgeError) as exc:
            bridge.check_scope(path)
        assert exc.value.code == roots.CODE_PATH_DENIED


@pytest.mark.parametrize(
    "target,expected",
    [
        ("\\\\?\\C:\\Users\\x", "C:\\Users\\x"),
        ("\\??\\C:\\Users\\x", "C:\\Users\\x"),
        ("\\\\?\\UNC\\srv\\share\\x", "\\\\srv\\share\\x"),
        ("\\??\\UNC\\srv\\share\\x", "\\\\srv\\share\\x"),
        ("C:\\plain", "C:\\plain"),
        ("..\\rel", "..\\rel"),
    ],
)
def test_nt_link_targets_lose_their_device_prefix(target, expected):
    assert bridge._strip_nt_link_prefix(target) == expected


@pytest.mark.skipif(os.name != "nt", reason="目录联接只在 Windows 上有")
def test_a_junction_into_ssh_is_followed_and_refused(home, tmp_path):
    """目录联接不要管理员权限，普通用户就能建：`os.path.islink` 看不出它，靠 reparse tag 认。"""
    import subprocess

    _file(home / ".ssh" / "notes.txt")
    plain = _file(tmp_path / "elsewhere" / "data" / "plain.txt")
    bad, good = home / "papers" / "keys", home / "papers" / "data"
    for link, target in ((bad, home / ".ssh"), (good, plain.parent)):
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
        )
    assert ".ssh" in bridge._lookup_chain(str(bad / "notes.txt"))[-1]  # 真的跟进了联接
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.check_scope(str(bad / "notes.txt"))
    assert exc.value.code == roots.CODE_PATH_DENIED
    assert bridge._argv_path_escapes(str(bad / "notes.txt"), bridge.allowed_roots(), None)
    # 指到普通目录的联接照常放行
    assert bridge.check_scope(str(good / "plain.txt")) == _real(plain)
