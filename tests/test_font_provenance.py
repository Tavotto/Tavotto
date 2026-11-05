"""字体来源：**这个仓库的 git 里不含任何字体二进制**（Prompt 14 §四 / `00_SHARED_RULES` §10），
发行物里只带 allowlist 逐字节钉住的那几张（ADR 0060）。

字形回退很容易滑向「把一个覆盖全的字体塞进包里就都解决了」。那条路的代价是
许可证：字体是独立作品，AGPL 的仓库照样不能随手带一份别人的 .ttf 出门。所以
本仓库的每一张脸都必须来自
* matplotlib 自带的 DejaVu（随 matplotlib 走），或
* 用户自己机器上装的字体，或
* **allowlist 那一档（2026-09-21，统一实施包 U06 / ADR 0060；U10 起是画布文字的唯一来源，ADR 0072）**：
  `src/tavotto/rendercore/fonts_allowlist.json` 里 sha256 钉住的 OFL 1.1 字体（Liberation 2.1.5 + Noto Sans SC），
  由 `scripts/fetch_fonts.py` 取到 `src/tavotto/resources/fonts/`（.gitignore 挡住，随 wheel / 桌面包分发，
  许可证全文同行）。这一档的判据：目录里出现的每个字体文件都在 allowlist 里
  （`test_packaged_fonts_are_exactly_the_allowlist`），不在的一律被注册表拒绝——放一份 Times New Roman 进去
  不会让它变成可用字体（RC-022）。
* （历史）PyMuPDF 自带的 base-14 / CJK / 隐式回退——随 PyMuPDF 退役（U10）不再是来源。

这几条不是靠记性维持——**下面每一条都可以被一次提交破坏，所以每一条都要有
判据**。
"""

import re
import subprocess
from pathlib import Path

import pytest

from tavotto import pdfbackend

ROOT = Path(__file__).resolve().parent.parent
FONT_SUFFIXES = (
    ".ttf",
    ".otf",
    ".ttc",
    ".otc",
    ".woff",
    ".woff2",
    ".pfb",
    ".pfa",
    ".eot",
    ".dfont",
)


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        # Windows 上不给 encoding 就按系统代码页解码，中文路径会解成乱码
        encoding="utf-8",
        check=True,
    )
    return out.stdout.splitlines()


def test_repository_ships_no_font_binaries():
    """版本库里一个字体文件都没有。

    判据是 **git 登记的文件**，不是磁盘上的文件：node_modules 与构建产物里
    当然有字体，它们不进分发。
    """
    fonts = [f for f in _tracked_files() if f.lower().endswith(FONT_SUFFIXES)]
    assert fonts == [], f"仓库里出现了字体文件，先确认许可证：{fonts}"


def test_no_web_font_is_fetched_or_embedded():
    """前端不下载远程字体，也不内嵌 base64 字体。

    远程字体在离线的桌面壳里就是一次静默降级；内嵌的那种则等于把字体
    分发出去了，只是换了个编码。
    """
    bad: list[str] = []
    face = re.compile(r"@font-face|fonts\.googleapis\.com|fonts\.gstatic\.com|font/woff")
    for rel in _tracked_files():
        if not rel.startswith("web/") or not rel.endswith((".ts", ".tsx", ".css", ".html")):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        if face.search(text):
            bad.append(rel)
    assert bad == [], f"这些文件在引入外部/内嵌字体：{bad}"


def test_font_bytes_are_loaded_only_from_the_local_source_font_endpoint():
    """ADR 0164 修订「前端不下载远程字体」的**主语**：不是「前端永远不 `new FontFace`」，而是
    「前端加载字体字节的代码只有一处，且字节只来自本机服务的 `/api/source-fonts/…`（会话认证之内）」。

    上一条判据（`@font-face` / 字体 CDN / woff）一字不动，仍然挡着 CSS 里的 web 字体与远程字体；本条挡的是
    JS 里用 `FontFace` API 偷偷绕开它的写法：构造点唯一、源码里没有任何 http(s) / data URI 字节来源、取字节的
    函数只拼 `/api/source-fonts/` 路径。"""
    users = []
    for rel in _tracked_files():
        if not rel.startswith("web/src/") or not rel.endswith((".ts", ".tsx")) or ".test." in rel:
            continue
        if re.search(
            r"new\s+FontFace\s*\(|document\.fonts\.add|FontFaceSet",
            (ROOT / rel).read_text(encoding="utf-8"),
        ):
            users.append(rel)
    assert users == ["web/src/lib/sourceFontCache.ts"], users
    cache = (ROOT / "web/src/lib/sourceFontCache.ts").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", cache, flags=re.S)
    assert not re.search(r"https?://|data:", code), (
        "预览字体只能来自本机服务，不许有网络 / 内嵌来源"
    )
    api = (ROOT / "web/src/lib/api.ts").read_text(encoding="utf-8")
    fetcher = re.search(r"export const fetchSourceFontBytes = .*?\n}\n", api, re.S).group(0)
    assert "/api/source-fonts/" in fetcher and "apiUrl(" in fetcher
    assert not re.search(r"https?://", fetcher)


def test_packaged_fonts_are_exactly_the_allowlist():
    """第四档的闭集判据：`resources/fonts/` 里（若已取过）每个字体文件的 sha256 都在 allowlist 里，
    且 allowlist 里每张脸都在。目录不存在时只核 allowlist 自身（那是没跑 fetch_fonts 的机器，不是缺陷）。"""
    from tavotto.rendercore import fonts

    allow = fonts.load_allowlist()
    assert len(allow.faces) == 16 and {f.license for f in allow.faces.values()} == {"OFL-1.1"}
    root = fonts.fonts_dir()
    if not root.is_dir():
        return
    reg = fonts.FontRegistry.discover(root, allow)
    assert reg.rejected == [], f"字体目录里有 allowlist 之外的文件：{reg.rejected}"
    assert reg.missing == [], reg.missing
    on_disk = sorted(p.name for p in root.rglob("*") if p.suffix.lower() in FONT_SUFFIXES)
    assert on_disk == sorted(Path(f.file).name for f in allow.faces.values())


def test_a_foreign_font_dropped_into_the_fonts_dir_is_refused(tmp_path):
    """上一条判据的反证：目录里多一份不在 allowlist 里的 .ttf → 注册表拒绝（不是静默当字体用）。"""
    from tavotto.rendercore import fonts

    (tmp_path / "TimesNewRoman.ttf").write_bytes(b"\x00\x01\x00\x00" + b"\x00" * 64)
    reg = fonts.FontRegistry.discover(tmp_path, fonts.load_allowlist())
    assert reg.faces == {} and reg.rejected[0]["reason"] == "not_in_allowlist"


def test_canvas_faces_all_come_from_the_allowlist_registry():
    """画布文字的每一张脸都经批准字体注册表（`FontRegistry`）取得，没有一个来自别处的文件。

    `rendercore` 里若出现 `TTFont(<别的路径>)` / 直接 `open(*.ttf)` / 摸系统字体目录，上面那条
    「仓库里没有字体文件」就会被绕过去（字体可以从别处下载再喂进来）。判据按 AST：native 适配层
    里打开字体字节的调用只许出现在 `fonts.py`（它按 allowlist 的 sha256 收）。"""
    import ast

    pkg = ROOT / "src" / "tavotto" / "rendercore"
    offenders = []
    for path in sorted(pkg.glob("*.py")):
        # `userfonts.py`（ADR 0164，修订 ADR 0060）：文档里 `source_font` 点名的**本机用户字体**，按 sha256 认、
        # 不按名字猜；它是唯一被允许写出系统字体目录与字体后缀的另一个模块，入口的唯一性由下一条用例钉住
        if path.name in ("fonts.py", "userfonts.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                low = node.value.lower()
                if (
                    low.endswith((".ttf", ".otf", ".ttc"))
                    or "/library/fonts" in low
                    or "c:\\windows\\fonts" in low
                ):
                    offenders.append(f"{path.name}:{node.lineno} {node.value!r}")
    assert not offenders, offenders


def test_user_fonts_enter_rendercore_only_through_a_source_font_object():
    """上一条用例给 `userfonts.py` 开的口子有多大：判据按 AST，主语是**调用点**。

    * 把一张本机用户脸请进排版只有一个入口：`HbFaceProvider.user_face`，而 rendercore 里唯一的调用方是
      `plan._admit`——它只在对象带 `source_font` 时才走（`_user_faces`）。没有 `source_font` 的文档，
      行为与 ADR 0060 逐字相同（这条在 `test_rendercore_userfonts.py::test_the_same_text_without_source_font…` 钉）。
    * `userfonts.admit` 只被 `hbshaper.user_face` 调用：许可 / 大小 / 格式的检查不会被绕过。"""
    import ast

    pkg = ROOT / "src" / "tavotto" / "rendercore"
    callers: dict[str, set[str]] = {"user_face": set(), "admit": set()}
    for path in sorted(pkg.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", "")
                )
                if name == "admit" and (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "userfonts"
                ):
                    callers["admit"].add(path.name)
            if isinstance(node, ast.Attribute) and node.attr == "user_face":
                callers["user_face"].add(path.name)
            if (
                isinstance(node, ast.Constant)
                and node.value == "user_face"
                and path.name == "plan.py"
            ):
                callers["user_face"].add(path.name)
    assert callers["admit"] == {"hbshaper.py"}, callers
    assert callers["user_face"] <= {"hbshaper.py", "plan.py"}, callers


def test_declared_dependencies_bring_no_font_package():
    """依赖里没有字体包（`pymupdf-fonts` 那一类）。"""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for name in ("pymupdf-fonts", "pymupdf_fonts", "fonts-", "font-roboto"):
        assert name not in text, f"pyproject 里出现了字体包：{name}"


@pytest.mark.parametrize("family", pdfbackend.CANVAS_TEXT_FAMILIES)
def test_every_offered_family_can_actually_be_drawn(family):
    """下拉里摆出来的每一个族，后端都真的画得出（T-78）。

    「摆一个画不出来的选项」的表现是：用户选中了、界面报告成功、导出的字形
    没有变。这条用最平凡的一串 ASCII 量它——族解析不出来时 `latin_font`
    会抛，或者悄悄回默认族，两种都会让这里红。
    """
    from tavotto.rendercore import typography

    assert typography.latin_family(family) == family
    assert pdfbackend.text_plan("Sample", family=family) == [("Sample", "primary")]
