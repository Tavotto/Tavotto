"""画布文字用原图字体（ADR 0164，修订 ADR 0060）：本机字体定位、许可、嵌入与回退。

判据的主语是**写出来的 PDF 里嵌入的字体程序**（用 fontTools 另读一遍，与写入侧不同源）和
`CompiledPage.problems`；字体取自**已安装的 matplotlib**（DejaVu，OFL），不进 git。

跑的前提同 `test_rendercore_writer.py`：候选包 + 批准字体目录；缺任一样 skip 并说明理由。
"""

from __future__ import annotations

import copy
import io
from pathlib import Path

import pytest

from tavotto.rendercore import fonts, pdfwriter, plan, sourcefont, sources, userfonts

pytest.importorskip("fontTools")
pytest.importorskip("pikepdf")
pytest.importorskip("uharfbuzz")
mpl_font_manager = pytest.importorskip("matplotlib.font_manager")

PAGE_MM = (150.0, 40.0)


def _mpl_font(name: str) -> Path:
    return Path(str(mpl_font_manager.findfont(mpl_font_manager.FontProperties(family=name))))


@pytest.fixture(scope="module")
def registry():
    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}；先跑 scripts/fetch_fonts.py")
    return reg


@pytest.fixture()
def provider(registry):
    """每个用例一个新的 provider：用户脸的登记（按身份缓存）不跨用例，否则上一条找到的脸会替下一条作证。"""
    from tavotto.rendercore.hbshaper import HbFaceProvider

    return HbFaceProvider(registry)


@pytest.fixture()
def locator(tmp_path, monkeypatch):
    """每个用例一个独立的定位器（缓存落临时目录、不扫真实系统字体目录）。"""
    loc = userfonts.FontLocator(tmp_path / "font-locator.json", system_dirs=lambda: [])
    monkeypatch.setattr(userfonts, "_LOCATOR", loc)
    return loc


def _text(source_font=None, text="Hamburgefonts 0123", **kw) -> dict:
    o = {
        "type": "text",
        "id": "t1",
        "text": text,
        "x_mm": 10.0,
        "y_mm": 10.0,
        "w_mm": 120.0,
        "h_mm": 10.0,
        "size_pt": 12.0,
        "font_family": "serif",
        **kw,
    }
    if source_font is not None:
        o["source_font"] = source_font
    return o


def _ref(path: Path, family: str, face: int = 0, **kw) -> dict:
    return {"family": family, "sha256": fonts.sha256_of(path), "face": face, **kw}


def _compile(provider, objs):
    return plan.compile_page(
        *PAGE_MM, objs, sources=sources.StaticSourceResolver(Path(".")), faces=provider
    )


def _embedded(provider, objs, tmp_path):
    """编译 + 写 PDF，回 (problems, 每个嵌入字体的 (BaseFont, TTFont 或 None), 文字层)。"""
    import pikepdf
    import pypdfium2
    from fontTools.ttLib import TTFont

    compiled = _compile(provider, objs)
    out = tmp_path / "o.pdf"
    pdfwriter.write_pdf(compiled.page, out, provider)
    pdf = pikepdf.open(out)
    found = []
    for _name, f in pdf.pages[0].Resources.Font.items():
        base = str(f.BaseFont)
        desc = f.DescendantFonts[0].FontDescriptor
        prog = desc.get("/FontFile2")
        found.append((base, TTFont(io.BytesIO(prog.read_bytes())) if prog is not None else None))
    doc = pypdfium2.PdfDocument(str(out))
    return compiled.problems, found, doc[0].get_textpage().get_text_range()


def _outlines(tt) -> set[tuple]:
    """字体里每个简单字形的轮廓坐标（指令 / 复合字形的组件序号会随子集改变，坐标不会）。"""
    glyf = tt["glyf"]
    out = set()
    for name in tt.getGlyphOrder():
        g = glyf[name]
        if g.numberOfContours > 0:
            coords, ends, _flags = g.getCoordinates(glyf)
            out.add((tuple(map(tuple, coords)), tuple(ends)))
    return out


# ------------------------------------------------------------------ 嵌入的就是这张脸


def test_the_pdf_embeds_the_users_face_not_an_approved_one(provider, locator, tmp_path):
    dejavu = _mpl_font("DejaVu Sans")
    locator.add_hint_dirs([dejavu.parent])
    src = _ref(dejavu, "DejaVu Sans")
    problems, found, text = _embedded(provider, [_text(src)], tmp_path)

    assert problems == ()
    bases = [b for b, _ in found]
    assert any(b.endswith("DejaVuSans") for b in bases), bases
    assert not any("Liberation" in b for b in bases), bases  # 没有被批准族顶替
    tt = next(t for b, t in found if b.endswith("DejaVuSans"))
    # 按字体程序比对：嵌入的子集里每个轮廓都逐字节是原字体里的轮廓（不是另一张脸画的）
    from fontTools.ttLib import TTFont

    source = _outlines(TTFont(str(dejavu)))
    embedded = _outlines(tt)
    assert embedded and embedded <= source
    assert tt["head"].unitsPerEm == 2048
    # 文本层字符正确
    assert text.strip() == "Hamburgefonts 0123"


def test_the_same_text_without_source_font_is_drawn_by_the_approved_face(
    provider, locator, tmp_path
):
    """对照：同一段文字不带 source_font，嵌入的是批准的 Liberation——上一条断言的是本字段的效果。"""
    problems, found, _ = _embedded(provider, [_text(None)], tmp_path)
    assert problems == ()
    assert [b.split("+")[-1] for b, _ in found] == ["LiberationSerif"]


def test_a_collection_member_is_found_by_face_index(provider, locator, tmp_path):
    """字体集（.ttc）：身份是 (整个文件的 sha256, face 序号)；两张脸共用一个文件，只有序号分得开。"""
    from fontTools.ttLib import TTCollection, TTFont

    coll = TTCollection()
    coll.fonts = [TTFont(str(_mpl_font("DejaVu Sans"))), TTFont(str(_mpl_font("DejaVu Serif")))]
    ttc = tmp_path / "pair.ttc"
    coll.save(str(ttc))
    locator.add_hint_dirs([tmp_path])
    problems, found, _ = _embedded(provider, [_text(_ref(ttc, "DejaVu Serif", face=1))], tmp_path)
    assert problems == ()
    assert any(b.endswith("DejaVuSerif") for b, _ in found), [b for b, _ in found]
    problems, found, _ = _embedded(provider, [_text(_ref(ttc, "DejaVu Sans", face=0))], tmp_path)
    assert any(b.endswith("DejaVuSans") for b, _ in found)
    # 序号越界 = 读不出，不是悄悄落到第 0 张
    out, _, _ = _embedded(provider, [_text(_ref(ttc, "Nope", face=7))], tmp_path)
    assert [p["reason"] for p in out] == ["unreadable"]


# ------------------------------------------------------------------ 缺失：保留名字、回退、报问题码


def test_a_missing_font_falls_back_to_the_approved_family_and_reports(provider, locator, tmp_path):
    src = {"family": "Gone Sans", "sha256": "ab" * 32, "face": 0}
    obj = _text(src, font_family="sans-serif")
    before = copy.deepcopy(obj)
    problems, found, text = _embedded(provider, [obj], tmp_path)

    assert [(p["code"], p["reason"], p["font"]) for p in problems] == [
        ("source_font_unavailable", "not_found", "Gone Sans")
    ]
    assert [b.split("+")[-1] for b, _ in found] == ["LiberationSans"]  # fontFamily 当回退族
    assert text.strip() == "Hamburgefonts 0123"
    assert obj == before  # 文档对象一个字节没动：名字还在，没有换掉再改文档


def test_same_name_but_different_bytes_is_a_different_face(provider, locator, tmp_path):
    """只按 sha256 认：目录里有一份叫 DejaVuSans.ttf 的、字节不同的文件，不会被当成要找的那张脸。"""
    from fontTools.ttLib import TTFont

    real = _mpl_font("DejaVu Sans")
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    fake = other_dir / "DejaVuSans.ttf"
    tt = TTFont(str(real))
    tt["name"].setName("Impostor", 4, 3, 1, 0x409)  # 同名文件、字节不同
    tt.save(str(fake))
    assert fonts.sha256_of(fake) != fonts.sha256_of(real)
    locator.add_hint_dirs([other_dir])
    problems, found, _ = _embedded(provider, [_text(_ref(real, "DejaVu Sans"))], tmp_path)
    assert [p["reason"] for p in problems] == ["not_found"]
    assert not any("DejaVu" in b for b, _ in found)


def test_a_malformed_source_font_is_reported_not_crashing(provider, locator, tmp_path):
    for bad in ({"family": "X"}, {"family": "X", "sha256": "zz", "face": 0}, "dejavu", 3):
        problems, found, _ = _embedded(provider, [_text(bad)], tmp_path)
        assert [p["reason"] for p in problems] == ["invalid"], bad
        assert [b.split("+")[-1] for b, _ in found] == ["LiberationSerif"]


def test_a_stale_locator_entry_is_not_trusted(provider, locator, tmp_path):
    """定位缓存说是它、字节已经被换了：重算 sha256 对不上就当没找到（并忘掉这条）。"""
    f = tmp_path / "ft" / "A.ttf"
    f.parent.mkdir()
    f.write_bytes(_mpl_font("DejaVu Sans").read_bytes())
    ref = _ref(f, "A")
    locator.add_hint_dirs([f.parent])
    assert locator.locate(ref["sha256"]) == f
    f.write_bytes(b"\x00\x01\x00\x00" + b"x" * 64)  # 原地换成别的字节
    problems, _, _ = _embedded(provider, [_text(ref)], tmp_path)
    assert [p["reason"] for p in problems] == ["not_found"]


# ------------------------------------------------------------------ 许可：禁止嵌入的脸不嵌入


def _with_fstype(tmp_path: Path, fs_type: int) -> Path:
    from fontTools.ttLib import TTFont

    tt = TTFont(str(_mpl_font("DejaVu Sans")))
    tt["OS/2"].fsType = fs_type
    out = tmp_path / f"fs{fs_type}" / "DejaVuSans.ttf"
    out.parent.mkdir(exist_ok=True)
    tt.save(str(out))
    return out


@pytest.mark.parametrize("fs_type", [0x0002, 0x0200, 0x0100, 0x0004 | 0x0002])
def test_a_face_that_forbids_embedding_is_not_embedded(provider, locator, tmp_path, fs_type):
    f = _with_fstype(tmp_path, fs_type)
    locator.add_hint_dirs([f.parent])
    problems, found, text = _embedded(provider, [_text(_ref(f, "DejaVu Sans"))], tmp_path)
    assert [p["reason"] for p in problems] == ["embedding_restricted"]
    assert not any("DejaVu" in b for b, _ in found), [b for b, _ in found]
    assert text.strip() == "Hamburgefonts 0123"


@pytest.mark.parametrize("fs_type", [0x0000, 0x0004, 0x0008])
def test_installable_preview_and_editable_embedding_is_allowed(
    provider, locator, tmp_path, fs_type
):
    """对照：同一份字体只改 fsType 的允许档，就嵌得进去——上一条的拦截确实是许可位引起的。"""
    f = _with_fstype(tmp_path, fs_type)
    locator.add_hint_dirs([f.parent])
    problems, found, _ = _embedded(provider, [_text(_ref(f, "DejaVu Sans"))], tmp_path)
    assert problems == ()
    assert any(b.endswith("DejaVuSans") for b, _ in found)


def test_unsupported_programs_are_refused_explicitly(provider, locator, tmp_path):
    """变量字体（fvar）本轮不嵌：显式 unsupported_format，不换脸冒充（与批准字体同一条边界）。"""
    from fontTools.ttLib import TTFont, newTable

    tt = TTFont(str(_mpl_font("DejaVu Sans")))
    tt["fvar"] = newTable("fvar")
    tt["fvar"].axes = []
    tt["fvar"].instances = []
    out = tmp_path / "var" / "V.ttf"
    out.parent.mkdir()
    tt.save(str(out))
    locator.add_hint_dirs([out.parent])
    problems, _, _ = _embedded(provider, [_text(_ref(out, "V"))], tmp_path)
    assert [p["reason"] for p in problems] == ["unsupported_format"]


def test_oversized_files_are_not_read(provider, locator, tmp_path, monkeypatch):
    f = tmp_path / "big" / "B.ttf"
    f.parent.mkdir()
    f.write_bytes(_mpl_font("DejaVu Sans").read_bytes())
    ref = _ref(f, "B")
    locator.add_hint_dirs([f.parent])
    monkeypatch.setattr(userfonts, "MAX_FONT_BYTES", 1000)
    problems, _, _ = _embedded(provider, [_text(ref)], tmp_path)
    # 超限的文件在定位阶段就不读（连 sha256 都不算）= 找不到
    assert [p["reason"] for p in problems] == ["not_found"]


# ------------------------------------------------------------------ CJK 与行内样式


def test_cjk_chars_still_use_the_approved_cjk_face_unless_the_source_names_one(
    provider, locator, tmp_path
):
    dejavu = _mpl_font("DejaVu Sans")
    locator.add_hint_dirs([dejavu.parent])
    problems, found, text = _embedded(
        provider, [_text(_ref(dejavu, "DejaVu Sans"), text="Ab 温度")], tmp_path
    )
    bases = [b.split("+")[-1] for b, _ in found]
    assert "DejaVuSans" in bases and "NotoSerifSC-Regular" in bases  # 默认族 serif → 衬线 CJK 脸
    assert [p["code"] for p in problems] == ["cjk_face"]  # 既有的提示，不是新问题
    assert text.strip() == "Ab 温度"


def test_an_inline_bold_run_simulates_bold_on_the_users_face(provider, locator, tmp_path):
    """用户字体是单个文件；行内 \\textbf 没有兄弟脸时模拟（ADR 0169 修订），不按名字猜兄弟文件，也不换成批准字体。"""
    dejavu = _mpl_font("DejaVu Sans")
    locator.add_hint_dirs([dejavu.parent])
    obj = _text(_ref(dejavu, "DejaVu Sans"), text="plain \\textbf{bold}", text_format="latex")
    problems, found, _ = _embedded(provider, [obj], tmp_path)
    bases = sorted(b.split("+")[-1] for b, _ in found)
    assert bases == ["DejaVuSans"], bases


# ------------------------------------------------------------------ 解析与定位器


def test_parse_source_font_shapes():
    sha = "0f" * 32
    ok, why = sourcefont.parse_source_font(
        {
            "family": " Arial ",
            "sha256": sha,
            "face": 2,
            "bold": True,
            "cjk": {"family": "SimSun", "sha256": sha},
        }
    )
    assert why is None and ok.family == "Arial" and ok.primary.face == 2 and ok.bold
    assert ok.italic is False and ok.cjk.family == "SimSun" and ok.cjk.face == 0
    assert sourcefont.parse_source_font(None) == (None, None)
    for bad in (
        {"family": "A", "sha256": sha.upper(), "face": 0},
        {"family": "A", "sha256": sha, "face": -1},
        {"family": "A", "sha256": sha, "face": True},
        {"family": "", "sha256": sha},
        {"family": "A", "sha256": sha, "cjk": {"family": "A"}},
        [],
    ):
        assert sourcefont.parse_source_font(bad) == (None, "invalid"), bad


def test_locator_persists_an_index_that_survives_a_restart(tmp_path):
    f = tmp_path / "fonts" / "D.ttf"
    f.parent.mkdir()
    f.write_bytes(_mpl_font("DejaVu Sans").read_bytes())
    sha = fonts.sha256_of(f)
    cache = tmp_path / "state" / "font-locator.json"
    a = userfonts.FontLocator(cache, system_dirs=lambda: [])
    assert a.locate(sha, extra_dirs=[f.parent]) == f
    assert cache.is_file()
    b = userfonts.FontLocator(cache, system_dirs=lambda: [])  # 新进程：不给线索也认得
    assert b.locate(sha) == f


def test_the_locator_never_matches_by_name(tmp_path):
    d = tmp_path / "fonts"
    d.mkdir()
    (d / "DejaVuSans.ttf").write_bytes(b"\x00\x01\x00\x00not the real thing")
    loc = userfonts.FontLocator(None, system_dirs=lambda: [])
    real_sha = fonts.sha256_of(_mpl_font("DejaVu Sans"))
    assert loc.locate(real_sha, extra_dirs=[d]) is None


def test_matplotlib_font_dirs_are_computed_from_the_interpreter_path_without_running_it(tmp_path):
    venv = tmp_path / "venv"
    d = venv / "lib" / "python3.12" / "site-packages" / "matplotlib" / "mpl-data" / "fonts" / "ttf"
    d.mkdir(parents=True)
    (venv / "bin").mkdir()
    py = venv / "bin" / "python"  # 不存在也行：只算路径
    assert d in userfonts.matplotlib_font_dirs(py)
    win = tmp_path / "winenv"
    wd = win / "Lib" / "site-packages" / "matplotlib" / "mpl-data" / "fonts" / "ttf"
    wd.mkdir(parents=True)
    assert wd in userfonts.matplotlib_font_dirs(win / "Scripts" / "python.exe")


def test_fs_type_policy_is_exactly_the_documented_bits():
    assert userfonts.embedding_refusal(0) is None
    assert userfonts.embedding_refusal(0x0004) is None  # Preview & Print
    assert userfonts.embedding_refusal(0x0008) is None  # Editable
    for bit in (0x0002, 0x0100, 0x0200):
        assert userfonts.embedding_refusal(bit)
    assert userfonts.FS_TYPE_REFUSE_MASK == 0x0302


# ------------------------------------------------------------------ 与前端同源的形状向量


def test_source_font_shape_vectors_agree_with_the_frontend_golden():
    """`tests/golden/source_font_vectors.json`：前端 `isSourceFont` 跑同一批（`sourceFont.test.ts`）。"""
    import json

    root = Path(__file__).resolve().parent / "golden" / "source_font_vectors.json"
    for case in json.loads(root.read_text(encoding="utf-8"))["cases"]:
        got, why = sourcefont.parse_source_font(case["value"])
        assert (why is None and got is not None) == case["valid"], case["name"]


# ------------------------------------------------------------------ 路径不出机器 / 登记后文件变了


def test_problems_and_warnings_never_carry_a_local_path(provider, locator, tmp_path):
    f = _with_fstype(tmp_path, 0x0002)
    locator.add_hint_dirs([f.parent])
    problems, _, _ = _embedded(
        provider,
        [_text(_ref(f, "DejaVu Sans")), _text({"family": "G", "sha256": "ab" * 32}, id="t2")],
        tmp_path,
    )
    assert {p["reason"] for p in problems} == {"embedding_restricted", "not_found"}
    import json as _json

    blob = _json.dumps(problems)
    assert str(tmp_path) not in blob and "DejaVuSans.ttf" not in blob
    # 导出作业把 problems 变成警告字符串的格式（job.produce）：同样不含路径
    assert all(
        str(tmp_path) not in f"{p['object_id']}: {p['code']} {''.join(p.get('chars', []))}"
        for p in problems
    )


def test_a_registered_face_is_revalidated_when_its_file_changes(provider, locator, tmp_path):
    """进程里活得比文件久：嵌过一次之后文件被换成别的字节，下一次不能拿旧记录嵌一张已经不是它的脸。"""
    f = tmp_path / "live" / "L.ttf"
    f.parent.mkdir()
    f.write_bytes(_mpl_font("DejaVu Sans").read_bytes())
    ref = _ref(f, "L")
    locator.add_hint_dirs([f.parent])
    problems, found, _ = _embedded(provider, [_text(ref)], tmp_path)
    assert problems == () and any(b.endswith("DejaVuSans") for b, _ in found)
    f.write_bytes(_mpl_font("DejaVu Serif").read_bytes())  # 同路径、换了字节
    problems, found, _ = _embedded(provider, [_text(ref)], tmp_path)
    assert [p["reason"] for p in problems] == ["not_found"]
    assert not any("DejaVu" in b for b, _ in found)


# ------------------------------------------------------------------ 其它载体（同一份 IR）


def test_svg_and_eps_carriers_accept_a_users_face(provider, locator, tmp_path):
    """SVG（`<text>` 与轮廓两种策略）与 EPS 叠加层都经 `face_by_resource`，用户脸与批准脸走同一条路。"""
    from tavotto.rendercore import epswriter, svgwriter

    dejavu = _mpl_font("DejaVu Sans")
    locator.add_hint_dirs([dejavu.parent])
    compiled = _compile(provider, [_text(_ref(dejavu, "DejaVu Sans"))])
    assert compiled.problems == ()
    svg = tmp_path / "o.svg"
    svgwriter.write_svg(compiled.page, svg, provider)
    assert "'DejaVu Sans'" in svg.read_text(encoding="utf-8")  # 真实族名，不是 Liberation
    outlined = tmp_path / "o2.svg"
    svgwriter.write_svg(compiled.page, outlined, provider, text_policy="outlines")
    assert "<path" in outlined.read_text(encoding="utf-8")
    ps, _facts = epswriter.overlay_ps(compiled.page, provider)
    assert ps.strip()


def test_same_size_and_mtime_but_different_bytes_is_caught_at_the_point_of_use(
    provider, locator, tmp_path
):
    """索引按 (size, mtime_ns) 认文件没变；用 `cp -p` 式的替换（字节不同、大小与 mtime 都还原）能骗过它。
    真要用这张脸的那一刻读字节重算 sha256，这里拦下。"""
    import os

    f = tmp_path / "sneaky" / "S.ttf"
    f.parent.mkdir()
    f.write_bytes(_mpl_font("DejaVu Sans").read_bytes())
    ref = _ref(f, "S")
    locator.add_hint_dirs([f.parent])
    assert locator.locate(ref["sha256"]) == f  # 进了索引
    st = f.stat()
    data = bytearray(f.read_bytes())
    data[-1] ^= 0xFF
    f.write_bytes(bytes(data))  # 大小不变
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))  # mtime 还原
    assert (f.stat().st_size, f.stat().st_mtime_ns) == (st.st_size, st.st_mtime_ns)
    problems, found, _ = _embedded(provider, [_text(ref)], tmp_path)
    assert [p["reason"] for p in problems] == ["not_found"]
    assert not any("DejaVu" in b for b, _ in found)


# ------------------------------------------------------------------ 不持有用户字体文件（ADR 0164）


def _holders_of(path) -> list[str]:
    """进程里仍指向 `path` 的句柄 / 映射；判不了的平台返回 None 由调用方改用 os.replace 判。"""
    import fcntl
    import os
    import sys

    real = os.path.realpath(path)
    hits: list[str] = []
    if sys.platform.startswith("linux"):
        for fd in os.listdir("/proc/self/fd"):
            try:
                if os.path.realpath(os.readlink(f"/proc/self/fd/{fd}")) == real:
                    hits.append(f"fd {fd}")
            except OSError:
                pass
        with open("/proc/self/maps", encoding="utf-8", errors="replace") as fh:
            hits += [f"map {ln.strip()}" for ln in fh if real in ln]
        return hits
    if sys.platform == "darwin":
        for fd in range(0, 1024):
            try:
                buf = fcntl.fcntl(fd, 50, b"\0" * 1024)  # F_GETPATH
            except OSError:
                continue
            if os.path.realpath(buf.split(b"\0", 1)[0].decode("utf-8", "replace")) == real:
                hits.append(f"fd {fd}")
        return hits
    return None  # Windows：没有等价的枚举，调用方用 os.replace 判


def test_embedding_a_users_face_leaves_no_open_handle_on_the_file(provider, locator, tmp_path):
    """用户字体按字节加载，进程不持有指向它的句柄 / 映射：Windows 上否则更新 / 卸载字体会「文件正在使用」。
    POSIX 枚举句柄（Linux 另查 maps；macOS 的 mmap 不可枚举，那一半只在 Linux / Windows 上能红）；
    Windows 直接 os.replace 换掉文件。"""
    import os
    import sys

    f = tmp_path / "held" / "H.ttf"
    f.parent.mkdir()
    f.write_bytes(_mpl_font("DejaVu Sans").read_bytes())
    ref = _ref(f, "H")
    locator.add_hint_dirs([f.parent])
    problems, found, _ = _embedded(provider, [_text(ref)], tmp_path)
    assert problems == () and any(b.endswith("DejaVuSans") for b, _ in found)
    if sys.platform == "win32":
        other = tmp_path / "held" / "other.ttf"
        other.write_bytes(_mpl_font("DejaVu Serif").read_bytes())
        os.replace(other, f)  # 有句柄 / 映射占着 → PermissionError
        f.unlink()  # 用户卸载字体
        assert not f.exists()
    else:
        holders = _holders_of(f)
        assert holders == [], holders


def test_user_face_cache_is_bounded_and_evicts_least_recently_used(
    provider, locator, tmp_path, monkeypatch
):
    """常驻进程里用户字体字节不能无界增长：超过张数上限，最久没用的被丢，当前这张保留，被丢的能再入场。"""
    from tavotto.rendercore import hbshaper

    monkeypatch.setattr(hbshaper.HbFaceProvider, "USER_FACE_COUNT_CAP", 2)
    names = ["DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono"]
    refs = []
    for i, n in enumerate(names):
        f = tmp_path / "lru" / f"F{i}.ttf"
        f.parent.mkdir(exist_ok=True)
        f.write_bytes(_mpl_font(n).read_bytes())
        locator.add_hint_dirs([f.parent])
        refs.append(userfonts.FaceRef(f"F{i}", fonts.sha256_of(f), 0))
    hp = provider
    for r in refs:
        hp.user_face(r)
    assert len(hp._user_records) == 2
    assert (refs[0].sha256, refs[0].face) not in hp._user_records  # 最久的被丢
    assert (refs[2].sha256, refs[2].face) in hp._user_records
    hp.user_face(refs[0])  # 再入场
    assert (refs[0].sha256, refs[0].face) in hp._user_records and len(hp._user_records) == 2


def test_user_face_cache_never_exceeds_its_cap_when_the_lock_is_released(
    provider, locator, tmp_path, monkeypatch
):
    """确定性看护（不靠线程撞时机）：每次放锁那一刻缓存都不超上限——插入和淘汰在同一段锁里。
    分两段时，插入后放锁、淘汰前再拿锁的缝里，别的线程会看到超上限的缓存（CI 偶发 'count 3'）。"""
    from tavotto.rendercore import hbshaper

    monkeypatch.setattr(hbshaper.HbFaceProvider, "USER_FACE_COUNT_CAP", 2)
    refs = []
    for i, n in enumerate(["DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono"]):
        f = tmp_path / "rel" / f"F{i}.ttf"
        f.parent.mkdir(exist_ok=True)
        f.write_bytes(_mpl_font(n).read_bytes())
        locator.add_hint_dirs([f.parent])
        refs.append(userfonts.FaceRef(f"F{i}", fonts.sha256_of(f), 0))

    seen: list[int] = []
    inner = provider._lock

    class _CheckOnRelease:
        def __enter__(self):
            inner.acquire()
            return self

        def __exit__(self, *exc):
            seen.append(len(provider._user_records))
            inner.release()
            return False

        def acquire(self, *a, **k):
            return inner.acquire(*a, **k)

        def release(self):
            seen.append(len(provider._user_records))
            inner.release()

    monkeypatch.setattr(provider, "_lock", _CheckOnRelease())
    for r in refs + refs:
        provider.user_face(r)
    assert seen and max(seen) <= provider.USER_FACE_COUNT_CAP, seen


def test_user_face_cache_holds_its_bounds_under_concurrent_requests(
    provider, locator, tmp_path, monkeypatch
):
    """进程级单例被多线程并发问：超过上限的不同脸 + 反复问同一张，不抛错、张数始终 ≤ 上限、字节 ≤ 上限、
    同一身份只有一份记录；被淘汰的脸别的线程手里还能用（淘汰只删缓存条目）。"""
    import threading

    from tavotto.rendercore import hbshaper

    monkeypatch.setattr(hbshaper.HbFaceProvider, "USER_FACE_COUNT_CAP", 2)
    names = ["DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono", "DejaVu Sans Bold"]
    refs = []
    for i, n in enumerate(dict.fromkeys(names)):
        src = _mpl_font(n)
        f = tmp_path / "cc" / f"F{i}.ttf"
        f.parent.mkdir(exist_ok=True)
        f.write_bytes(
            src.read_bytes() + bytes([i])
        )  # 不同字节 → 不同 sha（尾部多一字节不影响 sfnt 表）
        locator.add_hint_dirs([f.parent])
        refs.append(userfonts.FaceRef(f"F{i}", fonts.sha256_of(f), 0))
    errors: list[BaseException] = []
    violations: list[str] = []
    held: list = []
    cap = provider.USER_FACE_COUNT_CAP
    barrier = threading.Barrier(8)

    def work(n: int) -> None:
        try:
            barrier.wait()
            for k in range(12):
                r = refs[(n + k) % len(refs)] if k % 3 else refs[0]
                face = provider.user_face(r)
                held.append(face)
                with provider._lock:
                    if len(provider._user_records) > cap:
                        violations.append(f"count {len(provider._user_records)}")
                    if sum(len(x.data or b"") for x in provider._user_records.values()) > (
                        provider.USER_FACE_BYTES_CAP
                    ):
                        violations.append("bytes")
                face.shape("Hamburg")  # 被淘汰的脸仍可用
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    ts = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errors == [] and violations == []
    assert len(provider._user_records) <= cap
    assert len(set(provider._user_records)) == len(provider._user_records)
    assert set(provider._user_stat) <= set(provider._user_records)
