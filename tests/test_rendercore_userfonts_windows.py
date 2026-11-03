# ruff: noqa: F811  —— 复用夹具（import 进来再当参数名）是 pytest 的惯用写法
"""画布文字用原图字体（ADR 0164）在 **Windows** 上才有意义的几件事——给 Windows Lab 实测用。

Lab 只能跑仓库里登记的测试（`winlab.py test pytest --tests tests/test_rendercore_userfonts_windows.py`），
不能跑临时脚本，所以要在真 Windows 上验证的事都写成用例：

1. 系统字体目录（`%WINDIR%\\Fonts`、`%LOCALAPPDATA%\\Microsoft\\Windows\\Fonts`）含非 ASCII 用户名路径也能按 sha256 找到；
2. `matplotlib_font_dirs()` 对 Windows 布局（`Scripts\\python.exe` + `Lib\\site-packages`）的推导；
3. 系统字体（Times / 微软雅黑，含 TTC 的非 0 面）嵌入导出后，同进程里能 `os.replace` / `os.unlink` 字体副本
   （没有遗留的文件句柄 / 映射）；
4. 大 CJK 字体入场的耗时与内存占用（打印实测数，写进断言消息），以及超过字节上限后的淘汰。

整文件只在 Windows 跑；macOS / Linux 上 skip 是预期，不是降级。
"""

from __future__ import annotations

import os
import shutil
import struct
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="只在 Windows 有意义（系统字体目录、文件句柄语义、Windows 路径布局）；Lab 上跑",
)

pytest.importorskip("fontTools")
pytest.importorskip("pikepdf")
pytest.importorskip("uharfbuzz")
mpl_font_manager = pytest.importorskip("matplotlib.font_manager")

from tavotto.rendercore import fonts, userfonts  # noqa: E402
from tavotto.rendercore.hbshaper import HbFaceProvider  # noqa: E402
from tavotto.rendercore.sourcefont import FaceRef  # noqa: E402
from tests.test_rendercore_userfonts import (  # noqa: E402,F401  —— 夹具与辅助复用
    _embedded,
    _mpl_font,
    _ref,
    _text,
    locator,
    provider,
    registry,
)


def _windir() -> Path:
    return Path(os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows")


def _norm(p) -> str:
    return os.path.normcase(os.path.realpath(str(p)))


def _system_font(*names: str) -> Path:
    fonts_dir = _windir() / "Fonts"
    for n in names:
        p = fonts_dir / n
        if p.is_file():
            return p
    pytest.fail(f"Lab 字体环境不对：{fonts_dir} 里没有 {names} 中的任何一个")


def _ttc_faces(path: Path) -> int:
    head = path.read_bytes()[:12]
    if head[:4] != b"ttcf":
        return 1
    return struct.unpack(">I", head[8:12])[0]


def _copy_font(src: Path, dst_dir: Path, name: str | None = None) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / (name or src.name)
    shutil.copyfile(src, dst)
    return dst


# ------------------------------------------------------------------ 1. 系统字体目录


def test_system_font_dirs_follow_the_environment():
    dirs = [_norm(d) for d in userfonts.system_font_dirs()]
    assert _norm(_windir() / "Fonts") in dirs, dirs
    local = os.environ.get("LOCALAPPDATA")
    if local and (Path(local) / "Microsoft" / "Windows" / "Fonts").is_dir():
        assert _norm(Path(local) / "Microsoft" / "Windows" / "Fonts") in dirs, dirs


@pytest.mark.parametrize("user", ["用户名", "Zoë"])
def test_locator_finds_a_font_under_a_non_ascii_user_profile(tmp_path, monkeypatch, user):
    local = tmp_path / user / "AppData" / "Local"
    user_fonts = local / "Microsoft" / "Windows" / "Fonts"
    dejavu = _mpl_font("DejaVu Sans")
    copy = _copy_font(dejavu, user_fonts)
    monkeypatch.setenv("LOCALAPPDATA", str(local))

    dirs = [_norm(d) for d in userfonts.system_font_dirs()]
    assert _norm(user_fonts) in dirs, dirs

    loc = userfonts.FontLocator(tmp_path / "loc.json", system_dirs=userfonts.system_font_dirs)
    found = loc.locate(fonts.sha256_of(dejavu))
    assert found is not None, "非 ASCII 用户目录里的字体没被定位到"
    assert _norm(found) == _norm(copy)


# ------------------------------------------------------------------ 2. matplotlib 字体目录推导


def test_matplotlib_font_dirs_from_a_windows_layout_interpreter(tmp_path):
    venv = tmp_path / "venv"
    ttf = venv / "Lib" / "site-packages" / "matplotlib" / "mpl-data" / "fonts" / "ttf"
    ttf.mkdir(parents=True)
    exe = venv / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    got = [_norm(d) for d in userfonts.matplotlib_font_dirs(exe)]
    assert _norm(ttf) in got, got


def test_matplotlib_font_dirs_finds_the_running_interpreters_dejavu():
    dirs = userfonts.matplotlib_font_dirs(sys.executable)
    assert any((d / "DejaVuSans.ttf").is_file() for d in dirs), [str(d) for d in dirs]


# ------------------------------------------------------------------ 3. 嵌入后可替换 / 删除


def _embed_then_replace(provider, locator, tmp_path, original: Path, text: str, face: int):
    work = tmp_path / "copies"
    copy = _copy_font(original, work)
    locator.add_hint_dirs([work])
    src = _ref(copy, copy.stem, face=face)
    problems, found, layer = _embedded(provider, [_text(src, text=text)], tmp_path)
    assert problems == (), problems
    bases = [b for b, _ in found]
    assert bases and not any("Liberation" in b or "NotoSans" in b for b in bases), (
        f"嵌入的不是这张脸，而是批准脸顶替：{bases}"
    )
    assert layer.strip() == text
    # 同一进程里：provider 还活着、记录还在缓存里，但文件不该被占着
    assert (src["sha256"], face) in provider._user_records
    renamed = work / "renamed.bin"
    os.replace(copy, renamed)
    os.unlink(renamed)
    assert not any(work.iterdir())


def test_embedded_latin_system_font_can_be_replaced_and_deleted(provider, locator, tmp_path):
    original = _system_font("times.ttf", "arial.ttf")
    _embed_then_replace(provider, locator, tmp_path, original, "Hamburgefonts 0123", 0)


def test_embedded_cjk_system_font_can_be_replaced_and_deleted(provider, locator, tmp_path):
    original = _system_font("msyh.ttc", "simsun.ttc")
    n = _ttc_faces(original)
    face = 1 if n > 1 else 0
    _embed_then_replace(provider, locator, tmp_path, original, "中英文 Mixed 123", face)


def test_user_face_then_replace_even_before_any_export(provider, locator, tmp_path):
    original = _system_font("msyh.ttc", "simsun.ttc")
    work = tmp_path / "w"
    copy = _copy_font(original, work)
    locator.add_hint_dirs([work])
    provider.user_face(FaceRef("x", fonts.sha256_of(copy), 0))
    os.replace(copy, work / "b.ttc")
    os.unlink(work / "b.ttc")


# ------------------------------------------------------------------ 4. 大 CJK 字体：耗时、内存、淘汰


def _cache_bytes(provider) -> int:
    return sum(len(r.data or b"") for r in provider._user_records.values())


def test_large_cjk_face_admission_time_and_memory(provider, locator, tmp_path):
    original = _system_font("msyh.ttc", "simsun.ttc")
    copy = _copy_font(original, tmp_path / "w")
    locator.add_hint_dirs([copy.parent])
    size = copy.stat().st_size
    ref = FaceRef(copy.stem, fonts.sha256_of(copy), 0)

    t0 = time.perf_counter()
    face = provider.user_face(ref)
    glyphs = face.shape("中英文 Mixed text 汉字排版 0123")
    elapsed = time.perf_counter() - t0
    cached = _cache_bytes(provider)

    msg = (
        f"[{original.name}] 文件 {size / 1048576:.1f} MB / 首次入场+排版 {elapsed:.2f} s / "
        f"缓存字节 {cached} ({cached / 1048576:.1f} MB, 为文件的 {cached / size:.2f} 倍)"
    )
    print(msg)
    assert glyphs, msg
    assert elapsed < 10.0, msg
    assert size * 0.9 <= cached <= size * 1.2, msg


def test_user_face_cache_is_evicted_above_the_byte_cap(provider, locator, tmp_path, monkeypatch):
    original = _system_font("msyh.ttc", "simsun.ttc")
    work = tmp_path / "w"
    size = original.stat().st_size
    copies = []
    for i in range(3):  # 末尾补不同的字节 → 三个不同 sha256、仍是合法 sfnt
        c = _copy_font(original, work, f"f{i}.ttc")
        with open(c, "ab") as fh:
            fh.write(bytes([i + 1]) * 16)
        copies.append(c)
    locator.add_hint_dirs([work])
    cap = int(size * 2.5)
    monkeypatch.setattr(HbFaceProvider, "USER_FACE_BYTES_CAP", cap)

    refs = [FaceRef(c.stem, fonts.sha256_of(c), 0) for c in copies]
    for r in refs:
        provider.user_face(r)
        assert _cache_bytes(provider) <= cap, f"{_cache_bytes(provider)} > {cap}"
    assert refs[-1].identity in provider._user_records, "当前这张被淘汰了"
    assert refs[0].identity not in provider._user_records, "最久没用的没被淘汰"
    assert len(provider._user_records) == 2
    # 被淘汰的再用一次：重新入场、仍然成功，总量依旧不超
    provider.user_face(refs[0])
    assert _cache_bytes(provider) <= cap
    assert refs[0].identity in provider._user_records
