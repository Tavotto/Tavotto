"""G2 的文件身份与成败判定改读真值（#1150）：保存账只有一本事实，代理推断退到「未知则保守」。

四个已复现的形状（硬链接别名 / 缺目录 / 保存后 Figure 改坏 / NFC-NFD）各一组：
native 有磁盘真值（保存成功才登记、保存后对已登记目标做 samefile 对账），
safe / 浏览器没有磁盘，惰性验证只是代理——代理失败而 Figure 内容本身是唯一原因时记「未知」，不推翻选择。
"""

from __future__ import annotations

import os
import sys
import unicodedata
from pathlib import Path

import pytest

ENGINE_DIR = str(Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)
import figcapture  # noqa: E402 — figsession 平铺 import 的那一份

from test_capture_same_path_last_writer import _Fig, _State  # noqa: E402


class TestHardlinkAlias:
    """native：保存成功后，对已登记目标做 samefile 对账——硬链接别名写穿同一个 inode，是同一个文件。"""

    def test_write_through_a_hardlink_replaces_the_holders_file(self, tmp_path):
        a_path, alias = tmp_path / "x.pdf", tmp_path / "alias.pdf"
        a_path.write_bytes(b"a")
        os.link(a_path, alias)
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        st.save(a, str(a_path))
        assert st.save(b, str(alias)) is True  # 别名覆盖了 A 的 x.pdf：磁盘上那个文件现在是 B 的
        assert st.cap["x"] is b

    def test_alias_is_registered_under_the_existing_key(self, tmp_path):
        a_path, alias = tmp_path / "x.pdf", tmp_path / "alias.pdf"
        a_path.write_bytes(b"a")
        os.link(a_path, alias)
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        st.save(a, str(a_path))
        st.save(b, str(alias))
        assert list(st.w["x"]["files"]) == [str(a_path)]

    def test_distinct_files_stay_distinct(self, tmp_path):
        (tmp_path / "x.pdf").write_bytes(b"a")
        (tmp_path / "y.pdf").write_bytes(b"a")
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        st.save(a, str(tmp_path / "x.pdf"))
        assert st.save(b, str(tmp_path / "y.pdf")) is False  # A 的 x.pdf 仍在盘上
        assert st.cap["x"] is a

    def test_deleted_and_recreated_path_is_still_the_same_target(self, tmp_path):
        """不以 inode 取代路径键：删掉重建同一路径仍是同一目标（#1141 r4305717514）。"""
        p = tmp_path / "x.pdf"
        p.write_bytes(b"a")
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        st.save(a, str(p))
        p.unlink()
        p.write_bytes(b"b")
        assert st.save(b, str(p)) is True
        assert list(st.w["x"]["files"]) == [str(p)]

    def test_unreadable_keys_never_break_the_bookkeeping(self):
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        st.save(a, "/p/gone.pdf")
        assert st.save(b, "/p/other.pdf") is False

    def test_safe_verified_writes_do_not_probe_the_disk(self, tmp_path, monkeypatch):
        """safe / 浏览器不真写盘：磁盘上的同一 inode 与这次保存无关，不做对账。"""
        a_path, alias = tmp_path / "x.pdf", tmp_path / "alias.pdf"
        a_path.write_bytes(b"a")
        os.link(a_path, alias)
        st, a, b = _State(), _Fig(), _Fig()
        st.claim(a)
        st.save(a, str(a_path), verify=lambda: True)
        st.save(b, str(alias), verify=lambda: True)
        assert sorted(st.w["x"]["files"]) == sorted([str(a_path), str(alias)])


class TestUnicodeNormalization:
    def test_nfc_and_nfd_spellings_agree_where_the_filesystem_normalizes(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(figcapture, "_dir_folds_unicode", lambda d: True)
        nfc, nfd = (unicodedata.normalize(f, "é.pdf") for f in ("NFC", "NFD"))
        assert nfc != nfd
        key = figcapture.savefig_target_key
        assert key(nfc, {}) == key(nfd, {})

    def test_nfc_and_nfd_stay_distinct_where_it_does_not(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(figcapture, "_dir_folds_unicode", lambda d: False)
        nfc, nfd = (unicodedata.normalize(f, "é.pdf") for f in ("NFC", "NFD"))
        key = figcapture.savefig_target_key
        assert key(nfc, {}) != key(nfd, {})

    def test_key_matches_the_real_filesystem(self, tmp_path, monkeypatch):
        """本机文件系统认为 NFC / NFD 是不是同一个文件，键就怎么判（darwin APFS 是同一个，Linux ext4 / NTFS 不是）。"""
        monkeypatch.chdir(tmp_path)
        nfc, nfd = (unicodedata.normalize(f, "é.pdf") for f in ("NFC", "NFD"))
        (tmp_path / nfc).write_bytes(b"x")
        fs_same = os.path.exists(tmp_path / nfd) and os.path.samefile(
            tmp_path / nfc, tmp_path / nfd
        )
        key = figcapture.savefig_target_key
        assert (key(nfc, {}) == key(nfd, {})) is fs_same

    def test_platform_default(self, tmp_path, monkeypatch):
        d = str(tmp_path)
        for plat, expected in (("darwin", True), ("linux", False), ("win32", False)):
            monkeypatch.setattr(figcapture.sys, "platform", plat)
            assert figcapture._dir_folds_unicode(d) is expected

    def test_no_unicode_case_folding_beyond_lower(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(figcapture, "_dir_folds_case", lambda d: True)
        monkeypatch.setattr(figcapture, "_dir_folds_unicode", lambda d: True)
        key = figcapture.savefig_target_key
        assert key("shape.ß", {"format": "pdf"}) != key("shape.ss", {"format": "pdf"})


class TestPostSaveFigureBreakage:
    """safe / 浏览器：保存后 Figure 被改成渲染不出，不把那次其实成功的保存判成失败。"""

    @staticmethod
    def _fig():
        import matplotlib

        matplotlib.use("agg")
        import matplotlib.pyplot as plt

        return plt.figure()

    def _setup(self, tmp_path, kwargs=None):
        import matplotlib.pyplot as plt

        real = plt.Figure.savefig
        st = _State()
        a, b = self._fig(), self._fig()
        st.claim(a)
        tgt_x, tgt_y = str(tmp_path / "x.pdf"), str(tmp_path / "y.pdf")
        st.save(a, tgt_x, verify=figcapture.savefig_verifier(real, a, tgt_x, (), kwargs or {}))
        return st, a, b, real, tgt_y

    def test_figure_broken_after_a_good_save_still_blocks_a_takeover(self, tmp_path):
        st, a, b, real, tgt_y = self._setup(tmp_path)
        a.text(0.5, 0.5, r"$\badmathtext{$")  # 保存之后 A 被改成渲染不出
        took = st.save(b, tgt_y, verify=figcapture.savefig_verifier(real, b, tgt_y, (), {}))
        assert took is False  # A 的 x.pdf 已成功保存、仍在盘上
        assert st.cap["x"] is a

    def test_invalid_arguments_are_still_a_failed_save(self, tmp_path):
        """失败是参数本身造成的（与 Figure 内容无关）：仍判失败，不挡顶替。"""
        st, a, b, real, tgt_y = self._setup(tmp_path, {"dpi": "bad"})
        took = st.save(b, tgt_y, verify=figcapture.savefig_verifier(real, b, tgt_y, (), {}))
        assert took is True
        assert str(tmp_path / "x.pdf") not in st.w["x"]["files"]

    def test_eager_verification_of_a_foreign_write_is_still_truthful(self, tmp_path):
        """外来写入当场验证，Figure 此刻的内容就是保存时的内容：渲染不出 = 失败，整次忽略。"""
        st, a, b, real, tgt_y = self._setup(tmp_path)
        b.text(0.5, 0.5, r"$\badmathtext{$")
        took = st.save(b, tgt_y, verify=figcapture.savefig_verifier(real, b, tgt_y, (), {}))
        assert took is False
        assert tgt_y not in st.w["x"]["files"]

    def test_deferred_verdict_is_three_valued(self, tmp_path):
        import matplotlib.pyplot as plt

        real, a = plt.Figure.savefig, self._fig()
        v = figcapture.savefig_verifier(real, a, str(tmp_path / "x.pdf"), (), {})
        assert v.deferred() is True
        a.text(0.5, 0.5, r"$\badmathtext{$")
        assert v() is False and v.deferred() is None
        bad = figcapture.savefig_verifier(real, a, str(tmp_path / "x.pdf"), (), {"dpi": "bad"})
        assert bad.deferred() is False

    def test_deferred_check_leaves_the_figure_and_pyplot_alone(self, tmp_path):
        import matplotlib.pyplot as plt

        real, a = plt.Figure.savefig, self._fig()
        a.text(0.5, 0.5, r"$\badmathtext{$")
        before = (list(plt.get_fignums()), a.get_size_inches().tolist(), a.dpi)
        figcapture.savefig_verifier(real, a, str(tmp_path / "x.pdf"), (), {}).deferred()
        assert (list(plt.get_fignums()), a.get_size_inches().tolist(), a.dpi) == before


class TestMissingDirectory:
    """缺目录：native 抛错、不进任何表（已是如此）；safe 无磁盘，渲染进 BytesIO 判成功是已知边界。"""

    def test_native_failure_never_registers(self, tmp_path):
        import matplotlib

        matplotlib.use("agg")
        import matplotlib.pyplot as plt

        fig = plt.figure()
        with pytest.raises(FileNotFoundError):
            plt.Figure.savefig(fig, str(tmp_path / "missing" / "x.pdf"))
        # native 钩子在原调用抛出时直接 raise，不走 note_savefig_writer：这里钉住的是原调用确实抛
        # 已知边界：safe / 浏览器的代理渲染进 BytesIO，看不见目录（脚本的 makedirs 在沙盒里可能被拦或落在别处，
        # 磁盘上没有目录不代表原生会失败，d7f5b24b4 的裁决）；这条钉住边界，行为一变就逼着升格
        assert (
            figcapture.savefig_verify_render(
                plt.Figure.savefig, fig, str(tmp_path / "missing" / "x.pdf"), (), {}
            )
            is True
        )
