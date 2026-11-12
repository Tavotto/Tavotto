# ruff: noqa: F811  —— 复用夹具（import 进来再当参数名）是 pytest 的惯用写法
"""画布文字用原图字体（ADR 0164）在 **Windows** 上才有意义的几件事——给 Windows Lab 实测用。

Lab 只能跑仓库里登记的测试（`winlab.py test pytest --tests tests/test_rendercore_userfonts_windows.py`），
不能跑临时脚本，所以要在真 Windows 上验证的事都写成用例：

1. 系统字体目录（`%WINDIR%\\Fonts`、`%LOCALAPPDATA%\\Microsoft\\Windows\\Fonts`）含非 ASCII 用户名路径也能按 sha256 找到；
2. `matplotlib_font_dirs()` 对 Windows 布局（`Scripts\\python.exe` + `Lib\\site-packages`）的推导；
3. 系统字体（Times / 微软雅黑，含 TTC 的非 0 面）嵌入导出后，同进程里能 `os.replace` / `os.unlink` 字体副本
   （没有遗留的文件句柄 / 映射）；
4. 大 CJK 字体入场的耗时与内存占用（打印实测数，写进断言消息），以及超过字节上限后的淘汰；
5. 本机字体清单（ADR 0169）：系统的宋体 / 微软雅黑 / Times New Roman 进清单、中文名正确、列出来的脸 `admit` 接受，首次扫描耗时。

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


def _system_catalog():
    from tavotto.rendercore import fontcatalog

    loc = userfonts.FontLocator(None)
    cat = fontcatalog.FontCatalog(None, loc)
    started = time.perf_counter()
    cat.scan()
    took = time.perf_counter() - started
    return cat, loc, took, {f["name"]: f for f in cat.snapshot()["families"]}


def test_the_catalog_lists_the_system_fonts_chinese_papers_ask_for():
    """同门的场景：英文 Times New Roman，中文宋体 / 微软雅黑。名字、中文名、四张脸都要对。"""
    _cat, _loc, took, rows = _system_catalog()
    print(f"catalog: {len(rows)} 族，首次扫描 {took:.1f} s")
    times = rows["Times New Roman"]
    assert {(f["bold"], f["italic"]) for f in times["faces"]} == {
        (False, False),
        (True, False),
        (False, True),
        (True, True),
    }
    assert times["generic"] == "serif" and times["cjk"] is False
    missing = {"SimSun", "Microsoft YaHei"} - set(rows)
    if missing:
        pytest.skip(
            f"这台 Windows 没装中文字体 {missing}（Server 版可选功能）；名单：{sorted(rows)[:40]}"
        )
    assert rows["SimSun"]["zh"] == "宋体" and rows["SimSun"]["cjk"] is True
    assert rows["Microsoft YaHei"]["zh"] == "微软雅黑" and rows["Microsoft YaHei"]["cjk"] is True
    assert took < 60, f"首次扫描 {took:.1f} s"


def test_every_face_the_catalog_lists_is_one_admit_accepts():
    """清单说能用的，导出那一刻的 `admit` 也要接受（判据同一组函数，这里在真系统字体上对一遍）。"""
    _cat, loc, _took, rows = _system_catalog()
    refused = []
    for row in rows.values():
        for f in row["faces"]:
            try:
                userfonts.admit(FaceRef(row["name"], f["sha256"], f["face"]), locator=loc)
            except userfonts.UserFontUnavailable as exc:
                refused.append((row["name"], f["face"], exc.reason))
    assert not refused


def test_a_ttc_face_from_the_catalog_embeds_with_times_new_roman(provider, tmp_path, monkeypatch):
    """Times New Roman + 宋体（`.ttc` 的非 0 面也走同一路）真实导出，两张脸都进 PDF。"""
    _cat, loc, _took, rows = _system_catalog()
    if "SimSun" not in rows:
        pytest.skip("这台 Windows 没装宋体")
    monkeypatch.setattr(userfonts, "_LOCATOR", loc)
    times = next(f for f in rows["Times New Roman"]["faces"] if not f["bold"] and not f["italic"])
    simsun = rows["SimSun"]["faces"][0]
    problems, found, _text_layer = _embedded(
        provider,
        [
            _text(
                {
                    "family": "Times New Roman",
                    "sha256": times["sha256"],
                    "face": times["face"],
                    "cjk": {"family": "SimSun", "sha256": simsun["sha256"], "face": simsun["face"]},
                },
                text="Temperature 温度",
            )
        ],
        tmp_path,
    )
    # `cjk_face` 只是记录「这些字走了中文脸」，这里要的正是它；不许出现的是字体不可用和缺字
    assert not {p["code"] for p in problems} & {"source_font_unavailable", "glyph_missing"}
    assert any(p["code"] == "cjk_face" for p in problems)
    names = " ".join(n for n, _ in found)
    assert "TimesNewRoman" in names and "SimSun" in names


def test_inline_bold_uses_times_bold_and_yahei_bold(provider, tmp_path, monkeypatch):
    """行内 `\\textbf{}`：Times New Roman 粗体与微软雅黑粗体（`msyhbd.ttc`）都进 PDF（ADR 0169 修订）。"""
    _cat, loc, _took, rows = _system_catalog()
    if "Microsoft YaHei" not in rows:
        pytest.skip("这台 Windows 没装微软雅黑")
    monkeypatch.setattr(userfonts, "_LOCATOR", loc)

    def by_style(row, bold):
        return next(f for f in row["faces"] if f["bold"] is bold and not f["italic"])

    def sibling(f):
        return {"sha256": f["sha256"], "face": f["face"], "bold": True}

    times, yahei = rows["Times New Roman"], rows["Microsoft YaHei"]
    regular, zh = by_style(times, False), by_style(yahei, False)
    obj = _text(
        {
            "family": "Times New Roman",
            "sha256": regular["sha256"],
            "face": regular["face"],
            "siblings": [sibling(by_style(times, True))],
            "cjk": {
                "family": "Microsoft YaHei",
                "sha256": zh["sha256"],
                "face": zh["face"],
                "siblings": [sibling(by_style(yahei, True))],
            },
        },
        text="Plain \\textbf{Bold} 常规\\textbf{粗体}",
    )
    obj["text_format"] = "latex"
    problems, found, _text_layer = _embedded(provider, [obj], tmp_path)
    assert not {p["code"] for p in problems} & {"source_font_unavailable", "glyph_missing"}
    names = " ".join(n for n, _ in found)
    assert "TimesNewRomanPS-BoldMT" in names, names
    assert "MicrosoftYaHei-Bold" in names or "MSYaHei-Bold" in names, names


def test_simsun_without_a_bold_face_is_simulated_in_the_pdf(provider, tmp_path, monkeypatch):
    """宋体只有常规体：对象加粗后 PDF 里仍是宋体这一张脸，加描边模拟（ADR 0169 修订）。"""
    _cat, loc, _took, rows = _system_catalog()
    if "SimSun" not in rows:
        pytest.skip("这台 Windows 没装宋体")
    monkeypatch.setattr(userfonts, "_LOCATOR", loc)
    times = next(f for f in rows["Times New Roman"]["faces"] if not f["bold"] and not f["italic"])
    simsun = rows["SimSun"]["faces"][0]
    obj = _text(
        {
            "family": "Times New Roman",
            "sha256": times["sha256"],
            "face": times["face"],
            "cjk": {"family": "SimSun", "sha256": simsun["sha256"], "face": simsun["face"]},
        },
        text="温度",
    )
    obj["bold"] = True
    problems, found, _text_layer = _embedded(provider, [obj], tmp_path)
    assert not {p["code"] for p in problems} & {"source_font_unavailable", "glyph_missing"}
    assert "SimSun" in " ".join(n for n, _ in found)
