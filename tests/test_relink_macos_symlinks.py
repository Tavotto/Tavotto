"""`scripts/relink_macos_symlinks.py`：Tauri 把 PyInstaller 的符号链接复制成真文件，.app 里同一份库存 2～3 次。

判据的主语：**最终 .app（签名前）sidecar 目录里的真文件**——同内容、≥256 KB 的重复不许再有（`runtime/` 子树除外，
它与 sidecar 自带 PIL 的同名库是有意分离的）。fixture 全在 tmp_path，不碰仓库与真实安装。
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "relink_macos_symlinks", ROOT / "scripts" / "relink_macos_symlinks.py"
)
rl = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rl  # dataclass 要按模块名找回自己
spec.loader.exec_module(rl)

#: 这些用例在 tmp_path 里造 macOS 式的相对符号链接（含跨目录、经目录链接的链）。Windows 跑器上
#: `os.symlink` 对这类链接报 EINVAL / 缺权限——而被测的还原本来只在 macOS 的 .app 上跑，接线看护照常全平台跑。
needs_posix_symlinks = pytest.mark.skipif(
    sys.platform == "win32",
    reason="还原符号链接只发生在 macOS 的 .app 上；Windows 跑器造不出这类链接",
)

BIG = os.urandom(300 * 1024)  # 随机字节：既过 256 KB 门槛，也不会被误当成「全零同内容」


def _pyinstaller_like(src: Path) -> None:
    """PyInstaller onedir 的形状：框架里一份真文件，其余全是相对符号链接。"""
    fw = src / "_internal" / "Python.framework"
    (fw / "Versions" / "3.13").mkdir(parents=True)
    (fw / "Versions" / "3.13" / "Python").write_bytes(BIG)
    os.symlink("3.13", fw / "Versions" / "Current")
    os.symlink("Versions/Current/Python", fw / "Python")
    os.symlink("Python.framework/Versions/3.13/Python", src / "_internal" / "Python")
    (src / "_internal" / "pikepdf" / ".dylibs").mkdir(parents=True)
    (src / "_internal" / "pikepdf" / ".dylibs" / "libqpdf.dylib").write_bytes(BIG[::-1])
    os.symlink("pikepdf/.dylibs/libqpdf.dylib", src / "_internal" / "libqpdf.dylib")


def _tauri_like(src: Path, sidecar: Path) -> None:
    """tauri 搬运后的形状：文件链接 → 真拷贝，目录链接 → 丢掉。"""
    for root, _dirs, files in os.walk(src):
        for name in files:
            p = Path(root) / name
            dst = sidecar / p.relative_to(src)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(p.read_bytes())  # read_bytes 跟随链接 == fs::copy


@pytest.fixture
def trees(tmp_path):
    src = tmp_path / "dist" / "Tavotto"
    _pyinstaller_like(src)
    app = tmp_path / "Tavotto.app"
    sidecar = app / rl.SIDECAR_REL
    _tauri_like(src, sidecar)
    return src, app, sidecar


def _links(root: Path) -> list[str]:
    return sorted(
        str(Path(r, n).relative_to(root))
        for r, ds, fs in os.walk(root)
        for n in [*ds, *fs]
        if Path(r, n).is_symlink()
    )


@needs_posix_symlinks
def test_fixture_reproduces_the_duplicates(trees):
    """反证的另一半：没还原时，check 必须红（否则下面的绿什么都证明不了）。"""
    _src, app, sidecar = trees
    groups = rl.find_duplicates(sidecar)
    assert [len(paths) for _size, paths in groups] == [3, 2]
    assert rl.main(["check", "--app", str(app)]) == 1


@needs_posix_symlinks
def test_relink_restores_pyinstaller_layout_and_removes_duplicates(trees):
    src, app, sidecar = trees
    rep = rl.relink(src, sidecar)
    assert rep.differs == [] and rep.skipped == []
    assert rep.saved_bytes == len(BIG) * 3  # Python ×2 多余 + libqpdf ×1 多余
    assert _links(sidecar) == _links(
        src
    )  # 与 PyInstaller 产物的链接集合逐一相同（含丢掉的 Versions/Current）
    for link in _links(src):
        assert os.readlink(sidecar / link) == os.readlink(src / link)
    assert (sidecar / "_internal" / "Python").read_bytes() == BIG  # 链接解得开、内容在
    assert rl.find_duplicates(sidecar) == []
    assert rl.main(["check", "--app", str(app)]) == 0


@needs_posix_symlinks
def test_relink_is_idempotent(trees):
    src, _app, sidecar = trees
    rl.relink(src, sidecar)
    again = rl.relink(src, sidecar)
    assert again.relinked == [] and again.created == [] and again.saved_bytes == 0


@needs_posix_symlinks
def test_relink_never_replaces_a_file_whose_content_differs(trees):
    src, _app, sidecar = trees
    victim = sidecar / "_internal" / "libqpdf.dylib"
    victim.write_bytes(b"x" * 10 + BIG[::-1])  # 与链接目标不同（例如被改写过）
    rep = rl.relink(src, sidecar)
    assert rep.differs == ["_internal/libqpdf.dylib"]
    assert not victim.is_symlink() and victim.read_bytes().startswith(b"xxxxxxxxxx")


@needs_posix_symlinks
def test_runtime_subtree_is_not_judged(trees):
    """runtime/ 与 sidecar 自带库同名同内容是有意分离：check 不看这棵树。"""
    src, app, sidecar = trees
    rl.relink(src, sidecar)
    rt = sidecar / "_internal" / "runtime" / "lib"
    rt.mkdir(parents=True)
    (rt / "libqpdf.dylib").write_bytes(BIG[::-1])
    assert rl.main(["check", "--app", str(app)]) == 0


@needs_posix_symlinks
def test_hardlinks_count_as_one_copy(trees):
    src, _app, sidecar = trees
    rl.relink(src, sidecar)
    a = sidecar / "_internal" / "a.bin"
    a.write_bytes(os.urandom(300 * 1024))
    os.link(a, sidecar / "_internal" / "b.bin")
    assert rl.find_duplicates(sidecar) == []


@needs_posix_symlinks
def test_links_pointing_outside_the_tree_are_skipped(tmp_path):
    src = tmp_path / "dist" / "Tavotto"
    (src / "_internal").mkdir(parents=True)
    os.symlink("../../outside.bin", src / "_internal" / "x.bin")
    (tmp_path / "outside.bin").write_bytes(BIG)
    sidecar = tmp_path / "side"
    (sidecar / "_internal").mkdir(parents=True)
    (sidecar / "_internal" / "x.bin").write_bytes(BIG)
    rep = rl.relink(src, sidecar)
    assert len(rep.skipped) == 1 and rep.relinked == []
    assert not (sidecar / "_internal" / "x.bin").is_symlink()


# ---- 接线看护：脚本存在不等于被调用 ----

WORKFLOW = (ROOT / ".github" / "workflows" / "desktop-tauri.yml").read_text(encoding="utf-8")


def _step_index(title: str) -> int:
    m = re.search(rf"^      - name: {re.escape(title)}", WORKFLOW, re.M)
    assert m, f"desktop-tauri.yml 里找不到步骤「{title}」"
    return m.start()


def test_release_workflow_relinks_before_architecture_check_and_signing():
    relink = _step_index("还原符号链接（macOS：去掉 Tauri 复制出来的重复二进制）")
    assert (
        _step_index("Tauri build")
        < relink
        < _step_index("架构核对（.app 里不许混进另一个架构的 Mach-O）")
    )
    assert relink < _step_index("签名 .app（含嵌套 sidecar 与内置渲染 runtime）")
    block = WORKFLOW[relink : relink + 1800]
    assert "relink_macos_symlinks.py relink --src dist/Tavotto" in block
    assert "relink_macos_symlinks.py check" in block
    assert "runner.os == 'macOS'" in block


def test_local_build_script_relinks_after_tauri():
    text = (ROOT / "scripts" / "build_desktop.py").read_text(encoding="utf-8")
    assert text.index("run(cmd)") < text.index("relink_macos_symlinks.py")
    # 换链接破坏 Tauri 的 adhoc 封存（Codex #1728）：本地路径还原后必须重签、验收，再用新 .app 重打 dmg
    relink = text.index("relink_macos_symlinks.py")
    sign = text.index('"sign"', relink)
    verify = text.index('"verify"', sign)
    dmg = text.index('"hdiutil"', verify)
    assert relink < sign < verify < dmg
