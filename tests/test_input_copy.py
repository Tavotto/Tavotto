"""ADR 0173：native 缺读取时，把用户指认过的数据复制到脚本要读的原路径（`engine/inputcopy.py`）。

单元层钉住每一条边界与每一个终态；真 native 运行的端到端在 `test_input_copy_e2e.py`。
"""

from __future__ import annotations

import errno
import os
import subprocess
import sys

import pytest

from tavotto.engine import config, inputcopy, inputremap

posix_only = pytest.mark.skipif(os.name == "nt", reason="权限位模拟只在 POSIX 上")
not_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root 无视权限位"
)


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "proj"
    root.mkdir()
    return root


def _link(root, requested, chosen):
    inputremap.link(str(root), str(requested), str(chosen), "file")


def _token(root, target):
    return inputcopy.offer(str(root), str(target))["token"]


def _setup(tmp_path, root, *, parent=True):
    source = tmp_path / "moved" / "values.csv"
    source.parent.mkdir()
    source.write_bytes(b"7\n")
    target = tmp_path / "wanted" / "values.csv"
    if parent:
        target.parent.mkdir()
    _link(root, target, source)
    return source, target


# ---------------------------------------------------------------- 何时提议


def test_offer_needs_an_absolute_path_with_a_confirmed_existing_source(project, tmp_path):
    source, target = _setup(tmp_path, project)
    got = inputcopy.offer(str(project), str(target))
    assert got["state"] == "ask"
    assert (got["source"], got["target"], got["dir"]) == (
        str(source),
        str(target),
        str(target.parent),
    )
    assert got["dir_name"] == "wanted"
    # 相对路径、没有规则命中的路径：不提议（走原有出路）
    assert inputcopy.offer(str(project), "wanted/values.csv") is None
    assert inputcopy.offer(str(project), str(tmp_path / "other" / "x.csv")) is None
    # 规则指向的文件已经不在了：没有可复制的东西
    source.unlink()
    assert inputcopy.offer(str(project), str(target)) is None


def test_offer_without_any_rule_is_none(project, tmp_path):
    assert inputcopy.offer(str(project), str(tmp_path / "wanted" / "values.csv")) is None


def test_offer_for_a_missing_parent_or_a_directory_source_is_the_final_card(project, tmp_path):
    _, target = _setup(tmp_path, project, parent=False)
    got = inputcopy.offer(str(project), str(target))
    assert (got["state"], got["reason"]) == ("auth_failed", "no_parent")
    assert not target.parent.exists()  # 不建目录


def test_a_directory_source_is_never_copied(project, tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    target = tmp_path / "wanted" / "values.csv"
    target.parent.mkdir()
    inputremap.add_rule(str(project), {"kind": "file", "from": str(target), "to": str(folder)})
    got = inputcopy.offer(str(project), str(target))
    assert (got["state"], got["reason"]) == ("auth_failed", "source_not_file")
    res = inputcopy.run(str(project), _token(project, target))
    assert (res["state"], res["reason"]) == ("auth_failed", "source_not_file")
    assert not target.exists()


# ---------------------------------------------------------------- 复制本身


def test_copy_makes_an_identical_file_and_leaves_no_temp_file(project, tmp_path):
    source, target = _setup(tmp_path, project)
    res = inputcopy.run(str(project), _token(project, target))
    assert res["state"] == "done" and res["via"] == "normal"
    assert target.read_bytes() == b"7\n"
    assert source.read_bytes() == b"7\n"  # 原件不动
    assert sorted(p.name for p in target.parent.iterdir()) == ["values.csv"]


def test_an_existing_target_is_never_overwritten(project, tmp_path):
    _, target = _setup(tmp_path, project)
    target.write_bytes(b"MINE")
    res = inputcopy.run(str(project), _token(project, target))
    assert res["state"] == "exists"
    assert target.read_bytes() == b"MINE"
    assert sorted(p.name for p in target.parent.iterdir()) == ["values.csv"]
    assert inputcopy.records(str(project)) == []  # 没复制就没有记录


@posix_only
def test_a_dangling_symlink_target_is_not_written_through(project, tmp_path):
    _, target = _setup(tmp_path, project)
    elsewhere = tmp_path / "elsewhere.csv"
    target.symlink_to(elsewhere)
    res = inputcopy.run(str(project), _token(project, target))
    assert res["state"] == "auth_failed"
    assert not elsewhere.exists()


@posix_only
@not_root
def test_no_permission_is_denied_and_not_elevated_unless_asked(project, tmp_path, monkeypatch):
    _, target = _setup(tmp_path, project)
    asked = []
    monkeypatch.setattr(inputcopy, "_elevate", lambda s, t: asked.append((s, t)))
    target.parent.chmod(0o555)
    try:
        res = inputcopy.run(str(project), _token(project, target))
        assert (res["state"], res["reason"]) == ("denied", "access_denied")
        assert asked == []  # 没点「授权后复制」绝不提权
        assert not target.exists()
        assert sorted(p.name for p in target.parent.iterdir()) == []
    finally:
        target.parent.chmod(0o755)


@posix_only
@not_root
def test_elevation_runs_only_after_denied_and_only_for_this_copy(project, tmp_path, monkeypatch):
    source, target = _setup(tmp_path, project)
    calls = []

    def fake(src, dst):
        calls.append((src, dst))
        target.parent.chmod(0o755)
        os.link(src, dst)  # 模拟助手：有权限的一方把它复制过去

    monkeypatch.setattr(inputcopy, "_elevate", fake)
    target.parent.chmod(0o555)
    try:
        res = inputcopy.run(str(project), _token(project, target), elevate=True)
    finally:
        target.parent.chmod(0o755)
    assert res["state"] == "done" and res["via"] == "elevated"
    assert calls == [(str(source), str(target))]
    assert target.read_bytes() == b"7\n"
    (row,) = inputcopy.records(str(project))
    assert row["via"] == "elevated" and row["target"] == str(target)


@pytest.mark.parametrize("failure", ["cancelled", "elevation_failed", "timeout", "helper_denied"])
def test_a_failed_or_cancelled_elevation_ends_on_the_last_card(
    project, tmp_path, monkeypatch, failure
):
    _, target = _setup(tmp_path, project)

    def deny(*_a, **_k):
        raise PermissionError(errno.EACCES, "denied")

    def fail(src, dst):
        raise inputcopy.CopyError("auth_failed", failure)

    monkeypatch.setattr(os, "open", deny)
    monkeypatch.setattr(inputcopy, "_elevate", fail)
    res = inputcopy.run(str(project), _token(project, target), elevate=True)
    assert (res["state"], res["reason"]) == ("auth_failed", failure)
    assert not target.exists()


def test_read_only_media_and_full_disks_end_on_the_last_card_without_offering_a_grant(
    project, tmp_path, monkeypatch
):
    _, target = _setup(tmp_path, project)
    asked = []
    monkeypatch.setattr(inputcopy, "_elevate", lambda s, t: asked.append(1))
    for code, reason in ((errno.EROFS, "read_only"), (errno.ENOSPC, "no_space")):

        def boom(*_a, _c=code, **_k):
            raise OSError(_c, os.strerror(_c))

        monkeypatch.setattr(os, "open", boom)
        res = inputcopy.run(str(project), _token(project, target), elevate=True)
        assert (res["state"], res["reason"]) == ("auth_failed", reason)
    assert asked == []


def test_eperm_in_a_protected_macos_folder_is_the_system_protection_card(
    project, tmp_path, monkeypatch
):
    _, target = _setup(tmp_path, project)
    monkeypatch.setattr(inputcopy, "protected_kind", lambda d: "desktop")
    asked = []
    monkeypatch.setattr(inputcopy, "_elevate", lambda s, t: asked.append(1))

    def eperm(*_a, **_k):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "open", eperm)
    res = inputcopy.run(str(project), _token(project, target), elevate=True)
    assert res["state"] == "macos_protected" and res["protected_kind"] == "desktop"
    assert asked == []  # 管理员密码解决不了 TCC，不提权


def test_eperm_elsewhere_is_an_ordinary_permission_problem(project, tmp_path, monkeypatch):
    _, target = _setup(tmp_path, project)
    monkeypatch.setattr(inputcopy, "protected_kind", lambda d: None)

    def eperm(*_a, **_k):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "open", eperm)
    assert inputcopy.run(str(project), _token(project, target))["state"] == "denied"


def test_an_unclassified_error_still_lands_on_a_card(project, tmp_path, monkeypatch):
    _, target = _setup(tmp_path, project)

    def odd(*_a, **_k):
        raise OSError(errno.EIO, "io")

    monkeypatch.setattr(os, "open", odd)
    assert inputcopy.run(str(project), _token(project, target))["state"] == "auth_failed"


@pytest.mark.skipif(sys.platform != "darwin", reason="TCC 只在 macOS")
def test_protected_kind_names_the_tcc_folders(monkeypatch, tmp_path):
    home = os.path.realpath(os.path.expanduser("~"))
    assert inputcopy.protected_kind(os.path.join(home, "Desktop", "x")) == "desktop"
    assert inputcopy.protected_kind(os.path.join(home, "Documents")) == "documents"
    assert inputcopy.protected_kind(os.path.join(home, "Downloads", "a", "b")) == "downloads"
    assert (
        inputcopy.protected_kind(os.path.join(home, "Library", "Mobile Documents", "x")) == "icloud"
    )
    assert inputcopy.protected_kind(os.path.join(home, "projects")) is None
    assert inputcopy.protected_kind("/tmp") is None


# ---------------------------------------------------------------- 撤销记录


def test_undo_removes_only_the_copy_we_made(project, tmp_path):
    _, target = _setup(tmp_path, project)
    assert inputcopy.run(str(project), _token(project, target))["state"] == "done"
    (row,) = inputcopy.records(str(project))
    assert row["size"] == 2 and len(row["sha256"]) == 64
    # 用户后来改过：不删
    target.write_bytes(b"edited by user")
    assert inputcopy.undo(str(project), str(target))["state"] == "changed"
    assert target.read_bytes() == b"edited by user"
    target.write_bytes(b"7\n")
    assert inputcopy.undo(str(project), str(target))["state"] == "removed"
    assert not target.exists() and inputcopy.records(str(project)) == []
    assert inputcopy.undo(str(project), str(target))["state"] == "not_recorded"


def test_the_record_stays_in_local_project_settings_only(project, tmp_path):
    _, target = _setup(tmp_path, project)
    inputcopy.run(str(project), _token(project, target))
    assert config.project_settings(str(project))["input_copies"][0]["target"] == str(target)
    # 不进项目目录
    assert not any(p.name == "input_copies" for p in project.rglob("*"))


# ---------------------------------------------------------------- 提权的命令形状


def test_windows_command_line_is_a_fixed_subcommand_with_two_quoted_paths():
    src, dst = r"C:\Users\me\My Data\v.csv", r"D:\in\v.csv"
    line = inputcopy.windows_command_line(src, dst) if os.name == "nt" else None
    if os.name != "nt":  # 非 Windows 上路径不算绝对：构造同形状的参数串，验引号规则
        line = subprocess.list2cmdline([inputcopy.HELPER_FLAG, src, dst])
    assert line.startswith("--elevated-copy ")
    assert '"C:\\Users\\me\\My Data\\v.csv"' in line


@pytest.mark.skipif(os.name != "nt", reason="Windows 路径语义")
@pytest.mark.parametrize("bad", ['C:\\a"b.csv', "relative.csv", "C:rel.csv", "C:\\a\nb.csv"])
def test_windows_command_line_refuses_hostile_paths(bad):
    with pytest.raises(inputcopy.CopyError):
        inputcopy.windows_command_line(bad, r"C:\ok.csv")
    with pytest.raises(inputcopy.CopyError):
        inputcopy.windows_command_line(r"C:\ok.csv", bad)


@posix_only
def test_osascript_argv_keeps_paths_out_of_the_script_text():
    hostile = '/Users/a/it\'s; $(rm -rf ~) "x".csv'
    argv = inputcopy.macos_osascript_argv(hostile, "/Users/a/b c/v.csv", 501, 20)
    assert argv[0] == "/usr/bin/osascript"
    scripts = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert all(hostile not in s and "b c" not in s for s in scripts)
    assert argv[-3:] == [hostile, "/Users/a/b c/v.csv", "501:20"]
    with pytest.raises(inputcopy.CopyError):
        inputcopy.macos_osascript_argv("relative/x.csv", "/a/b.csv", 1, 1)


@pytest.mark.skipif(sys.platform != "darwin", reason="需要 osascript")
def test_applescript_quoting_survives_hostile_filenames(tmp_path):
    """反证：把同一段 AppleScript 去掉「with administrator privileges」真跑一遍——`quoted form` 必须挡住
    空格、引号、分号、`$()`，复制出来的文件内容与目标名都原样对得上，且没有产生副作用文件。"""
    src = tmp_path / 'it\'s a; $(touch PWNED) "q".csv'
    src.write_bytes(b"data")
    dst_dir = tmp_path / "out dir"
    dst_dir.mkdir()
    dst = dst_dir / "t; touch PWNED2.csv"
    argv = inputcopy.macos_osascript_argv(str(src), str(dst), os.getuid(), os.getgid())
    argv = [a.replace(" with administrator privileges", "") for a in argv]
    proc = subprocess.run(
        argv, capture_output=True, text=True, timeout=30, check=False, cwd=str(tmp_path)
    )
    assert proc.returncode == 0, proc.stderr
    assert dst.read_bytes() == b"data"
    assert not any("PWNED" in p.name for p in tmp_path.rglob("*") if p != src and p != dst)


# ---------------------------------------------------------------- 与 Rust 助手同源


def _rust_source():
    from pathlib import Path

    return (
        Path(__file__).resolve().parent.parent / "src-tauri" / "src" / "elevated_copy.rs"
    ).read_text("utf-8")


def test_helper_exit_codes_flag_and_extension_table_match_the_rust_side():
    import re

    rust = _rust_source()

    def const(name):
        return re.search(rf"pub const {name}: i32 = (\d+);", rust).group(1)

    assert [
        int(const(n))
        for n in ("EXIT_OK", "EXIT_EXISTS", "EXIT_INVALID", "EXIT_DENIED", "EXIT_FAILED")
    ] == [
        inputcopy.HELPER_OK,
        inputcopy.HELPER_EXISTS,
        inputcopy.HELPER_INVALID,
        inputcopy.HELPER_DENIED,
        inputcopy.HELPER_FAILED,
    ]
    assert re.search(r'pub const FLAG: &str = "([^"]+)";', rust).group(1) == inputcopy.HELPER_FLAG
    block = re.search(r"REFUSED_EXTENSIONS: &\[&str\] = &\[(.*?)\];", rust, re.S).group(1)
    assert set(re.findall(r'"(\w+)"', block)) == set(inputcopy.REFUSED_EXTENSIONS)


def test_elevation_refuses_executable_looking_targets_and_system_directories(
    project, tmp_path, monkeypatch
):
    assert inputcopy.elevation_refused(str(tmp_path / "x.EXE"))
    assert inputcopy.elevation_refused(str(tmp_path / "a" / "b.ps1"))
    assert not inputcopy.elevation_refused(str(tmp_path / "values.csv"))
    if os.name != "nt":
        assert inputcopy.elevation_refused("/usr/local/values.csv")
        assert inputcopy.elevation_refused("/Library/LaunchDaemons/values.csv")
    _, target = _setup(tmp_path, project)
    monkeypatch.setattr(inputcopy, "elevation_refused", lambda t: True)
    monkeypatch.setattr(inputcopy, "_elevate_windows", lambda *a: pytest.fail("must not run"))
    monkeypatch.setattr(inputcopy, "_elevate_macos", lambda *a: pytest.fail("must not run"))

    def deny(*_a, **_k):
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(os, "open", deny)
    res = inputcopy.run(str(project), _token(project, target), elevate=True)
    assert (res["state"], res["reason"]) == ("auth_failed", "refused_target")


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL")
def test_windows_acl_deny_is_the_denied_card_and_leaves_nothing(project, tmp_path):
    """Windows 上用真 ACL 模拟「没有权限」：对 Everyone 拒绝在目录里创建文件 → `denied`，不提权、什么都不留。"""
    _, target = _setup(tmp_path, project)
    folder = str(target.parent)
    subprocess.run(
        ["icacls", folder, "/deny", "*S-1-1-0:(OI)(CI)(WD,AD)"], check=True, capture_output=True
    )
    try:
        res = inputcopy.run(str(project), _token(project, target))
        assert res["state"] == "denied", res
        assert not target.exists()
    finally:
        subprocess.run(
            ["icacls", folder, "/remove:d", "*S-1-1-0"], check=False, capture_output=True
        )
    assert sorted(p.name for p in target.parent.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows 路径语义")
def test_windows_copy_handles_non_ascii_and_spaces_without_overwriting(project, tmp_path):
    source = tmp_path / "已指认" / "数据 值.csv"
    source.parent.mkdir()
    source.write_bytes(b"1,2\r\n")
    target = tmp_path / "脚本 要的" / "数据 值.csv"
    target.parent.mkdir()
    inputremap.link(str(project), str(target), str(source), "file")
    assert inputcopy.run(str(project), _token(project, target))["state"] == "done"
    assert target.read_bytes() == b"1,2\r\n"
    assert inputcopy.run(str(project), _token(project, target))["state"] == "exists"


def test_a_forged_or_foreign_token_never_reaches_the_filesystem(project, tmp_path):
    _, target = _setup(tmp_path, project)
    token = _token(project, target)
    # 请求里能带的只有令牌：路径串、别的项目的令牌、非字符串都拒绝
    for bad in (str(target), "nope", None, 5, "../" + token):
        res = inputcopy.run(str(project), bad)
        assert (res["state"], res["reason"]) == ("auth_failed", "no_source"), bad
    other = tmp_path / "other-project"
    other.mkdir()
    assert inputcopy.run(str(other), token)["reason"] == "no_source"
    assert not target.exists()
    assert inputcopy.run(str(project), token)["state"] == "done"


def test_undo_uses_only_the_recorded_target(project, tmp_path):
    _, target = _setup(tmp_path, project)
    inputcopy.run(str(project), _token(project, target))
    stray = tmp_path / "wanted" / "other.csv"
    stray.write_bytes(b"7\n")
    assert inputcopy.undo(str(project), str(stray))["state"] == "not_recorded"
    assert stray.exists()
