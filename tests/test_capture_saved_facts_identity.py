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

from support import linkkit

ENGINE_DIR = str(Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)
import figcapture  # noqa: E402 — figsession 平铺 import 的那一份
from test_capture_same_path_last_writer import _Fig, _State  # noqa: E402


@pytest.fixture(autouse=True)
def _close_figures():
    yield
    import matplotlib.pyplot as plt

    plt.close("all")


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
        monkeypatch.setattr(figcapture, "_dir_folds_unicode", lambda d, b=None: True)
        nfc, nfd = (unicodedata.normalize(f, "é.pdf") for f in ("NFC", "NFD"))
        assert nfc != nfd
        key = figcapture.savefig_target_key
        assert key(nfc, {}) == key(nfd, {})

    def test_nfc_and_nfd_stay_distinct_where_it_does_not(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(figcapture, "_dir_folds_unicode", lambda d, b=None: False)
        nfc, nfd = (unicodedata.normalize(f, "é.pdf") for f in ("NFC", "NFD"))
        key = figcapture.savefig_target_key
        assert key(nfc, {}) != key(nfd, {})

    def test_key_matches_the_real_filesystem(self, tmp_path, monkeypatch):
        """本机文件系统认为 NFC / NFD 是不是同一个文件，键就怎么判（darwin APFS 是同一个，Linux ext4 / NTFS 不是）。"""
        monkeypatch.chdir(tmp_path)
        nfc, nfd = (unicodedata.normalize(f, "é.pdf") for f in ("NFC", "NFD"))
        (tmp_path / nfc).write_bytes(b"x")
        figcapture._UNICODE_FOLD_CACHE.pop(str(tmp_path.resolve()), None)
        fs_same = os.path.exists(tmp_path / nfd) and os.path.samefile(
            tmp_path / nfc, tmp_path / nfd
        )
        key = figcapture.savefig_target_key
        assert (key(nfc, {}) == key(nfd, {})) is fs_same

    def test_unproven_directory_is_never_normalized(self, tmp_path, monkeypatch):
        """目录里没有任何证据（名字不在盘上、也没有非 ASCII 条目）：宁可漏并，不按平台猜。"""
        monkeypatch.chdir(tmp_path)
        d = str(tmp_path.resolve())
        figcapture._UNICODE_FOLD_CACHE.pop(d, None)
        nfc, nfd = (unicodedata.normalize(f, "é.pdf") for f in ("NFC", "NFD"))
        key = figcapture.savefig_target_key
        assert key(nfc, {}) != key(nfd, {})
        assert d not in figcapture._UNICODE_FOLD_CACHE  # 无证据的结论不缓存

    @pytest.mark.parametrize(
        "alt_exists,same,expected",
        [(True, True, True), (True, False, False), (False, False, False)],
    )
    def test_probe_logic_with_a_faked_filesystem(
        self, tmp_path, monkeypatch, alt_exists, same, expected
    ):
        """注入探测的两路：另一种写法能打开同一个文件 -> 归一；不存在 / 是另一个文件 -> 不归一。"""
        d = str(tmp_path.resolve())
        nfc = unicodedata.normalize("NFC", "é.pdf")
        figcapture._UNICODE_FOLD_CACHE.pop(d, None)
        monkeypatch.setattr(
            figcapture.os.path,
            "lexists",
            lambda p: True if os.path.basename(p) == nfc else alt_exists,
        )
        monkeypatch.setattr(figcapture.os, "lstat", lambda p: p)
        monkeypatch.setattr(figcapture.os.path, "samestat", lambda a, b: same)
        assert figcapture._dir_folds_unicode(d, nfc) is expected
        figcapture._UNICODE_FOLD_CACHE.pop(d, None)

    def test_probe_uses_a_directory_entry_as_evidence(self, tmp_path, monkeypatch):
        d = str(tmp_path.resolve())
        nfc = unicodedata.normalize("NFC", "é.txt")
        figcapture._UNICODE_FOLD_CACHE.pop(d, None)
        monkeypatch.setattr(figcapture, "_UNWATCHED_LISTDIR", lambda p: [nfc])
        monkeypatch.setattr(figcapture.os.path, "lexists", lambda p: True)
        monkeypatch.setattr(figcapture.os, "lstat", lambda p: p)
        monkeypatch.setattr(figcapture.os.path, "samestat", lambda a, b: True)
        assert figcapture._dir_folds_unicode(d) is True
        figcapture._UNICODE_FOLD_CACHE.pop(d, None)

    @pytest.mark.skipif(
        sys.platform == "darwin", reason="darwin 的文件系统本身归一化，建不出两个独立的目录项"
    )
    def test_hardlinked_nfc_nfd_entries_are_aliases_not_normalization(self, tmp_path):
        """区分归一化的文件系统上，NFC / NFD 两个名字互为硬链接：samefile 为真，但那是别名，不是归一化。"""
        d = str(tmp_path.resolve())
        nfc, nfd = (unicodedata.normalize(f, "é.pdf") for f in ("NFC", "NFD"))
        (tmp_path / nfc).write_bytes(b"x")
        os.link(tmp_path / nfc, tmp_path / nfd)
        figcapture._UNICODE_FOLD_CACHE.pop(d, None)
        assert figcapture._dir_folds_unicode(d, nfc) is False
        assert figcapture._dir_folds_unicode(d) is False
        assert figcapture._dir_folds_unicode(d, nfd) is False
        assert d not in figcapture._UNICODE_FOLD_CACHE or figcapture._UNICODE_FOLD_CACHE[d] is False
        figcapture._UNICODE_FOLD_CACHE.pop(d, None)

    def test_two_listed_spellings_are_never_normalization(self, tmp_path, monkeypatch):
        """注入目录列表：两种写法都是独立目录项 -> 不归一（即使 samefile 为真）。"""
        d = str(tmp_path.resolve())
        nfc, nfd = (unicodedata.normalize(f, "é.pdf") for f in ("NFC", "NFD"))
        figcapture._UNICODE_FOLD_CACHE.pop(d, None)
        monkeypatch.setattr(figcapture, "_UNWATCHED_LISTDIR", lambda p: [nfc, nfd])
        monkeypatch.setattr(figcapture.os.path, "lexists", lambda p: True)
        monkeypatch.setattr(figcapture.os, "lstat", lambda p: p)
        monkeypatch.setattr(figcapture.os.path, "samestat", lambda a, b: True)
        assert figcapture._dir_folds_unicode(d, nfc) is False

    def test_one_listed_spelling_that_opens_under_both_is_normalization(
        self, tmp_path, monkeypatch
    ):
        d = str(tmp_path.resolve())
        nfc = unicodedata.normalize("NFC", "é.pdf")
        figcapture._UNICODE_FOLD_CACHE.pop(d, None)
        monkeypatch.setattr(figcapture, "_UNWATCHED_LISTDIR", lambda p: [nfc])
        monkeypatch.setattr(figcapture.os.path, "lexists", lambda p: True)
        monkeypatch.setattr(figcapture.os, "lstat", lambda p: p)
        monkeypatch.setattr(figcapture.os.path, "samestat", lambda a, b: True)
        assert figcapture._dir_folds_unicode(d, nfc) is True
        figcapture._UNICODE_FOLD_CACHE.pop(d, None)

    def test_single_symlink_listed_once_is_evidence(self, tmp_path, monkeypatch):
        """归一卷上一个名字可分解的单个符号链接：目录里只列一次、两种写法 lstat 到同一项 -> 是「同一个名字」的证据。"""
        d = str(tmp_path.resolve())
        nfc = unicodedata.normalize("NFC", "é.pdf")
        figcapture._UNICODE_FOLD_CACHE.pop(d, None)
        monkeypatch.setattr(figcapture, "_UNWATCHED_LISTDIR", lambda p: [nfc])
        monkeypatch.setattr(figcapture.os.path, "lexists", lambda p: True)
        monkeypatch.setattr(
            figcapture.os.path, "islink", lambda p: True
        )  # 若有人又加回 islink 一刀切，这里红
        monkeypatch.setattr(figcapture.os, "lstat", lambda p: p)
        monkeypatch.setattr(figcapture.os.path, "samestat", lambda a, b: True)
        assert figcapture._dir_folds_unicode(d, nfc) is True
        figcapture._UNICODE_FOLD_CACHE.pop(d, None)

    def test_two_independent_entries_are_distinct_even_if_lstat_differs(
        self, tmp_path, monkeypatch
    ):
        """另一种写法 lstat 到不同的目录项（例如两个符号链接、只列出一种写法的异常列表）-> 不归一。"""
        d = str(tmp_path.resolve())
        nfc = unicodedata.normalize("NFC", "é.pdf")
        figcapture._UNICODE_FOLD_CACHE.pop(d, None)
        monkeypatch.setattr(figcapture, "_UNWATCHED_LISTDIR", lambda p: [nfc])
        monkeypatch.setattr(figcapture.os.path, "lexists", lambda p: True)
        monkeypatch.setattr(figcapture.os, "lstat", lambda p: p)
        monkeypatch.setattr(figcapture.os.path, "samestat", lambda a, b: False)
        assert figcapture._dir_folds_unicode(d, nfc) is False

    @pytest.mark.skipif(
        sys.platform == "darwin", reason="darwin 的文件系统本身归一化，建不出两个独立的目录项"
    )
    def test_two_symlinks_are_two_entries_not_normalization(self, tmp_path):
        d = str(tmp_path.resolve())
        nfc, nfd = (unicodedata.normalize(f, "é.pdf") for f in ("NFC", "NFD"))
        (tmp_path / "target.pdf").write_bytes(b"x")
        # 硬链接：同样是「两个独立的目录项」（见 `_unicode_evidence` 文档），NTFS 上不要符号链接特权
        linkkit.hardlink(tmp_path / "target.pdf", tmp_path / nfc)
        linkkit.hardlink(tmp_path / "target.pdf", tmp_path / nfd)
        figcapture._UNICODE_FOLD_CACHE.pop(d, None)
        assert figcapture._dir_folds_unicode(d, nfc) is False
        figcapture._UNICODE_FOLD_CACHE.pop(d, None)

    def test_probe_error_means_no_normalization(self, tmp_path, monkeypatch):
        d = str(tmp_path.resolve())
        figcapture._UNICODE_FOLD_CACHE.pop(d, None)
        nfc = unicodedata.normalize("NFC", "é.pdf")
        (tmp_path / nfc).write_bytes(b"x")

        def boom(p):
            raise OSError("nope")

        monkeypatch.setattr(figcapture.os, "lstat", boom)
        assert figcapture._dir_folds_unicode(d, nfc) is False

    def test_no_unicode_case_folding_beyond_lower(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(figcapture, "_dir_folds_case", lambda d, b=None: True)
        monkeypatch.setattr(figcapture, "_dir_folds_unicode", lambda d, b=None: True)
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

    @pytest.mark.parametrize(
        "kw",
        [
            {"bbox_extra_artists": 5},
            {"bbox_extra_artists": [object()]},
            {"bbox_extra_artists": "x"},
        ],
    )
    def test_irreproducible_arguments_are_a_failed_save_not_unknown(self, tmp_path, kw):
        """非法 / 引用原 Figure 内对象的参数在空白 Figure 上无法原样复现：回两值的 False，失败的保存不持有。"""
        import matplotlib.pyplot as plt

        real, a = plt.Figure.savefig, self._fig()
        v = figcapture.savefig_verifier(
            real, a, str(tmp_path / "x.pdf"), (), {**kw, "bbox_inches": "tight"}
        )
        assert v() is False  # native 下同样抛
        assert v.deferred() is False

    @pytest.mark.parametrize("empty", [[], (), None, {}])
    def test_empty_extra_artists_are_replayable_and_unknown_after_breakage(self, tmp_path, empty):
        """`bbox_extra_artists=[]`（空容器 / None）完全可复现：保存成功、之后 Figure 坏掉 -> 未知，按已保存算。"""
        st, a, b, real, tgt_y = self._setup(
            tmp_path, {"bbox_extra_artists": empty, "bbox_inches": "tight"}
        )
        a.text(0.5, 0.5, r"$\badmathtext{$")
        took = st.save(b, tgt_y, verify=figcapture.savefig_verifier(real, b, tgt_y, (), {}))
        assert took is False
        assert st.cap["x"] is a
        assert figcapture._replayable_on_blank((), {"bbox_extra_artists": empty}) is True

    def test_artist_valued_arguments_are_never_replayed_on_a_blank_figure(self, tmp_path):
        import matplotlib.pyplot as plt

        real, a = plt.Figure.savefig, self._fig()
        t = a.text(0.5, 0.5, "x")
        a.text(0.2, 0.2, r"$\badmathtext{$")
        v = figcapture.savefig_verifier(
            real, a, str(tmp_path / "x.pdf"), (), {"bbox_extra_artists": [t]}
        )
        assert v.deferred() is False
        assert figcapture._replayable_on_blank((), {"foo": [t]}) is False
        assert figcapture._replayable_on_blank((), {"dpi": 100, "bbox_inches": "tight"}) is True

    def test_invalid_extra_artists_holder_does_not_block_a_valid_writer(self, tmp_path):
        st, a, b, real, tgt_y = self._setup(
            tmp_path, {"bbox_extra_artists": 5, "bbox_inches": "tight"}
        )
        took = st.save(b, tgt_y, verify=figcapture.savefig_verifier(real, b, tgt_y, (), {}))
        assert took is True

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
