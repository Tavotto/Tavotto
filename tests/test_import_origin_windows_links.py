"""Windows 上的符号链接与 junction 要分开对待（PR #880 的 Windows CI 跟进）。

Windows 的符号链接也是 reparse point：项目内的符号链接要和 POSIX 同等处理（默认模式 `readlink` 读目标、
目标词法上在项目内就放行；`no_follow` 下拒绝），而 junction / mount point / 云盘占位 / AppExecLink 一律拒
（`os.readlink` 在 Windows 上对 junction 也读得出来，所以必须先按 reparse tag 区分）。另外 Windows 的
`os.readlink` 返回带 `\\\\?\\` 前缀的替代路径，不规范化就永远对不上项目前缀。

本机没有 Windows：用假的 lstat 结果 / 假的 readlink 模拟，真实符号链接用例在 POSIX 上照跑。
"""

from __future__ import annotations

import os
import stat

import pytest

from tavotto.engine import importscan, scanbudget

SYMLINK_TAG = 0xA000000C
MOUNT_POINT_TAG = 0xA0000003
CLOUD_TAG = 0x9000701A
APPEXEC_TAG = 0x8000001B
REPARSE = 0x400


class _St:
    def __init__(self, mode: int, attrs: int = 0, tag: int = 0) -> None:
        self.st_mode = mode
        self.st_size = 0
        self.st_file_attributes = attrs
        self.st_reparse_tag = tag


class TestIsSymlink:
    def test_windows_symlink_is_a_symlink(self):
        st = _St(stat.S_IFLNK | 0o777, REPARSE | 0x10, SYMLINK_TAG)
        assert scanbudget.is_symlink(st) and scanbudget.is_redirect(st)

    def test_posix_symlink_has_no_reparse_bits(self):
        assert scanbudget.is_symlink(_St(stat.S_IFLNK | 0o777))

    @pytest.mark.parametrize("tag", [MOUNT_POINT_TAG, CLOUD_TAG, APPEXEC_TAG])
    @pytest.mark.parametrize("mode", [stat.S_IFDIR | 0o777, stat.S_IFLNK | 0o777])
    def test_other_reparse_points_are_never_symlinks(self, tag, mode):
        """即便某个 Python 版本把 junction 也标成 S_IFLNK，tag 复核也不认它是符号链接。"""
        st = _St(mode, REPARSE | 0x10, tag)
        assert not scanbudget.is_symlink(st)
        if tag & 0x20000000:  # name surrogate：junction / 符号链接一类，照拒
            assert scanbudget.is_redirect(st)

    def test_plain_file_is_not(self):
        assert not scanbudget.is_symlink(_St(stat.S_IFREG | 0o644))


class TestPlainLinkTarget:
    @pytest.mark.parametrize(
        ("raw", "want"),
        [
            ("\\\\?\\C:\\proj\\real\\ns", "C:\\proj\\real\\ns"),
            ("\\??\\C:\\proj\\real", "C:\\proj\\real"),
            ("\\\\?\\UNC\\srv\\share\\x", "\\\\srv\\share\\x"),
            ("C:\\proj\\real", "C:\\proj\\real"),
            ("..\\real", "..\\real"),
        ],
    )
    def test_windows_prefixes_are_removed(self, monkeypatch, raw, want):
        monkeypatch.setattr(importscan, "_is_windows", lambda: True)
        assert importscan._plain_link_target(raw) == want

    def test_posix_targets_are_left_alone(self, monkeypatch):
        monkeypatch.setattr(importscan, "_is_windows", lambda: False)
        assert importscan._plain_link_target("\\\\?\\C:\\x") == "\\\\?\\C:\\x"


def _by(res):
    return {c.module: c for c in res.classes}


def _world(tmp_path):
    (tmp_path / "real" / "ns").mkdir(parents=True)
    (tmp_path / "real" / "ns" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "real" / "ns" / "mod.py").write_text("import numpy\n", encoding="utf-8")
    (tmp_path / "s.py").write_text("import linked\n", encoding="utf-8")
    link = tmp_path / "linked"
    try:
        link.symlink_to(tmp_path / "real" / "ns", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")
    return link


def _pretend_windows(monkeypatch, link, *, tag: int, mode: int | None = None, readlink=None):
    """把 `link` 的 lstat 换成 Windows 的样子，`readlink` 换成带 `\\\\?\\` 前缀的替代路径。"""
    real_lstat, real_readlink = os.lstat, os.readlink
    target = os.path.abspath(link)

    class Fake(_St):
        pass

    def fake_lstat(path, *a, **kw):
        st = real_lstat(path, *a, **kw)
        if os.path.abspath(os.fspath(path)) == target:
            return Fake(st.st_mode if mode is None else mode, REPARSE | 0x10, tag)
        return st

    def fake_readlink(path, *a, **kw):
        if os.path.abspath(os.fspath(path)) == target:
            return readlink if readlink is not None else "\\\\?\\" + real_readlink(path)
        return real_readlink(path, *a, **kw)

    monkeypatch.setattr(os, "lstat", fake_lstat)
    monkeypatch.setattr(os, "readlink", fake_readlink)
    monkeypatch.setattr(importscan, "_is_windows", lambda: True)


class TestWindowsSymlinkVsJunction:
    def test_a_windows_symlink_with_extended_prefix_is_followed_inside_the_project(
        self, tmp_path, monkeypatch
    ):
        link = _world(tmp_path)
        # os.path.isabs / normpath 在 POSIX 上不认盘符，用相对目标之外的路径形状就够：去前缀后是项目内绝对路径
        _pretend_windows(monkeypatch, link, tag=SYMLINK_TAG)
        assert importscan._confined_path(tmp_path, link, no_follow=False) is not None

    def test_a_windows_symlink_is_refused_under_no_follow(self, tmp_path, monkeypatch):
        link = _world(tmp_path)
        _pretend_windows(monkeypatch, link, tag=SYMLINK_TAG)
        assert importscan._confined_path(tmp_path, link, no_follow=True) is None

    def test_a_windows_symlink_pointing_outside_is_refused(self, tmp_path, monkeypatch):
        link = _world(tmp_path)
        outside = tmp_path.parent / (tmp_path.name + "-outside")
        _pretend_windows(monkeypatch, link, tag=SYMLINK_TAG, readlink="\\\\?\\" + str(outside))
        assert importscan._confined_path(tmp_path, link, no_follow=False) is None

    def test_a_windows_unc_symlink_is_refused(self, tmp_path, monkeypatch):
        link = _world(tmp_path)
        _pretend_windows(monkeypatch, link, tag=SYMLINK_TAG, readlink="\\\\?\\UNC\\evil\\share\\ns")
        assert importscan._confined_path(tmp_path, link, no_follow=False) is None

    @pytest.mark.parametrize("as_lnk", [False, True])
    @pytest.mark.parametrize("no_follow", [False, True])
    def test_junction_is_always_refused(self, tmp_path, monkeypatch, as_lnk, no_follow):
        """junction 的 `readlink` 读得出项目内目标——也不能放行。云盘占位 / AppExecLink 不带 name-surrogate
        位，不是路径替身（`is_redirect` 本来就不当它是重定向，沿用占位文件判据），也永远不是符号链接，
        不会走到 `readlink`（见 `TestIsSymlink`）。"""
        link = _world(tmp_path)
        mode = (stat.S_IFLNK if as_lnk else stat.S_IFDIR) | 0o777
        _pretend_windows(monkeypatch, link, tag=MOUNT_POINT_TAG, mode=mode)
        assert importscan._confined_path(tmp_path, link, no_follow=no_follow) is None
