"""字体政策（统一实施包 U06，ADR 0060）：allowlist 的形状、注册表只认 allowlist 里的字节（RC-022）、
身份是 sha256 不是族名（RC-023）、集合外明示限制（RC-037）、纯标准库的 sfnt 读取与 fontTools 对拍、
运行时依赖与 requirements 镜像一致、PyMuPDF 只剩 legacy extra（U10，ADR 0072）。

分两档：不需要字体文件也能跑的（allowlist / 拒绝 / 依赖表）永远跑；要真字体的在没跑过
`scripts/fetch_fonts.py` 的机器上 skip 并说明理由（skip 不是绿）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # tomllib 是 3.11 才进标准库的；3.10 上只跳过用到它的那几条
    tomllib = None

from tavotto.rendercore import fonts

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = fonts.load_allowlist()


# ---------------------------------------------------------------- allowlist（不需要字体文件）


def test_allowlist_is_the_thirteen_ofl_faces_with_sha256_identity():
    faces = ALLOWLIST.faces
    assert len(faces) == 13
    families = {(f.family, f.bold, f.italic) for f in faces.values()}
    for fam in ("serif", "sans-serif", "monospace"):
        for b in (False, True):
            for i in (False, True):
                assert (fam, b, i) in families, (fam, b, i)
    assert ("cjk", False, False) in families
    assert {f.license for f in faces.values()} == {"OFL-1.1"}
    assert {f.format for f in faces.values()} == {"truetype", "cff-cid"}
    assert len({f.sha256 for f in faces.values()}) == 13
    for f in faces.values():
        assert f.source["url"].startswith("https://") and f.source["sha256"]
        assert f.notice and f.license_file in ALLOWLIST.license_files
    assert len(ALLOWLIST.obligations) >= 4


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda d: d.update(schema=2), "allowlist_invalid"),
        (lambda d: d.__setitem__("faces", {}), "allowlist_invalid"),
        (lambda d: d["faces"]["LiberationSerif-Regular"].pop("sha256"), "allowlist_invalid"),
        (
            lambda d: d["faces"]["LiberationSerif-Regular"].update(sha256="zz" * 32),
            "allowlist_invalid",
        ),
        (
            lambda d: d["faces"]["LiberationSerif-Bold"].update(
                sha256=d["faces"]["LiberationSerif-Regular"]["sha256"]
            ),
            "allowlist_invalid",
        ),
        (
            lambda d: d["license_files"]["liberation/LICENSE"].pop("file_sha256"),
            "allowlist_invalid",
        ),
        (lambda d: d["license_files"].pop("noto/LICENSE"), "allowlist_invalid"),
    ],
)
def test_a_malformed_allowlist_is_refused(tmp_path, mutate, code):
    data = json.loads(fonts.allowlist_path().read_text(encoding="utf-8"))
    mutate(data)
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(fonts.FontsUnavailable) as ei:
        fonts.load_allowlist(p)
    assert ei.value.code == code


def test_files_not_in_the_allowlist_are_rejected_before_being_parsed(tmp_path):
    """RC-022：目录里出现一份不在 allowlist 里的 .ttf（哪怕是垃圾字节）→ 进 rejected，不进 faces。"""
    (tmp_path / "TimesNewRoman.ttf").write_bytes(b"\x00\x01\x00\x00" + b"junk" * 40)
    reg = fonts.FontRegistry.discover(tmp_path, ALLOWLIST)
    assert reg.faces == {}
    assert [r["file"] for r in reg.rejected] == ["TimesNewRoman.ttf"]
    assert reg.rejected[0]["reason"] == "not_in_allowlist"
    assert reg.missing == sorted(ALLOWLIST.faces)
    with pytest.raises(fonts.FontsUnavailable) as ei:
        reg.record_for("serif")
    assert ei.value.code == "face_missing"
    assert reg.cjk_record() is None


def test_a_missing_fonts_dir_is_a_structured_error_not_a_system_font(tmp_path):
    reg = fonts.FontRegistry.discover(tmp_path / "nope", ALLOWLIST)
    with pytest.raises(fonts.FontsUnavailable) as ei:
        reg.record_for("serif")
    assert ei.value.code == "fonts_dir_missing"


def test_env_override_is_exclusive(monkeypatch, tmp_path):
    monkeypatch.setenv(fonts.ENV_FONTS_DIR, str(tmp_path / "elsewhere"))
    assert fonts.fonts_dir() == tmp_path / "elsewhere"
    monkeypatch.delenv(fonts.ENV_FONTS_DIR)
    assert fonts.fonts_dir().name == "fonts" and fonts.fonts_dir().parent.name == "resources"


def test_font_kind_rejects_the_formats_the_writer_cannot_embed():
    assert fonts.font_kind(frozenset({"glyf", "cmap", "head"})) == "truetype"
    assert fonts.font_kind(frozenset({"CFF ", "cmap", "head"})) == "cff-cid"
    for bad in ({"glyf", "fvar"}, {"CFF2"}, {"glyf", "COLR"}, {"CFF ", "SVG "}, {"cmap", "head"}):
        assert fonts.font_kind(frozenset(bad)) is None, bad


# ---------------------------------------------------------------- 依赖表镜像


needs_tomllib = pytest.mark.skipif(tomllib is None, reason="需要 tomllib（Python ≥ 3.11）")


RENDERCORE_PACKAGES = ("pikepdf", "fonttools", "uharfbuzz", "pypdfium2", "pillow")


def _dep_names(specs: list[str]) -> list[str]:
    return [re.split(r"[<>=!~\[ ;]", spec, maxsplit=1)[0].lower() for spec in specs]


@needs_tomllib
def test_requirements_txt_mirrors_the_runtime_dependencies_and_is_pinned():
    """U10 起 RenderCore 的五个包是运行时依赖（ADR 0072）：pyproject `dependencies` 与 `requirements.txt`
    同名同序、后者钉死版本。两边各改一处会让「装 -e . 的人」与「装 requirements 的人」拿到不同闭包。"""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = _dep_names(cfg["project"]["dependencies"])
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    pins = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    names = [ln.split("==")[0].lower() for ln in pins]
    assert names == deps
    for ln in pins:
        assert "==" in ln, f"requirements.txt 必须钉死版本: {ln}"
    for name in RENDERCORE_PACKAGES:
        assert name in deps, f"{name} 应当是运行时依赖（切默认后不再是 extra）"


def test_every_source_setup_path_fetches_the_approved_fonts():
    """U10 起 RenderCore 是默认后端，批准字体又不进 git：新克隆照文档 / `run.sh` 装好之后，只要没跑
    `scripts/fetch_fonts.py`，文字与导出就是 `FontsUnavailable`（Codex #539）。`run.sh` 要先核再取、取不到就停；
    文档里每段 `pip install -e` 的源码安装，紧接着几行内要有取字体那一步。"""
    run_sh = (ROOT / "run.sh").read_text(encoding="utf-8")
    assert re.search(r"fetch_fonts\.py --check .*\|\| .*fetch_fonts\.py \|\| exit 1", run_sh), (
        "run.sh 要先 --check、缺了才取、取不到就停"
    )
    assert run_sh.index("fetch_fonts.py") < run_sh.index("exec .venv/bin/tavotto")
    blocks = 0
    for doc in ("README.md", "README.zh-CN.md", "CONTRIBUTING.md"):
        lines = (ROOT / doc).read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if re.search(r"\bpip install -e\b", line) and ".venv" in line:
                blocks += 1
                window = "\n".join(lines[i : i + 4])
                assert "fetch_fonts.py" in window, f"{doc}:{i + 1} 的源码安装段没有取批准字体"
    assert blocks >= 3, "一段源码安装说明都没找到——判据量在空集合上"


@needs_tomllib
def test_runtime_lock_versions_satisfy_the_app_dependency_ranges():
    """内置渲染 runtime 的锁（为像素基线钉死）与应用的依赖区间必须相容：两者被装进同一个解释器时（CI 的
    invariants 腿按 runtime-lock 装科学栈、pip 用户装 `tavotto[worker]`），后装的钉版本会覆盖前者，pip 报
    依赖冲突、产品的依赖一致性检查判 `dependency_consistency_failed`（#539 的 invariants 腿实红过：
    fonttools>=4.65 对 runtime-lock 的 4.63.0）。改任何一边都要让另一边仍然满足。"""
    from packaging.requirements import Requirement

    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ranges = {
        (req := Requirement(d)).name.lower(): req.specifier for d in cfg["project"]["dependencies"]
    }
    lock = json.loads((ROOT / "packaging" / "runtime-lock.json").read_text(encoding="utf-8"))
    checked = 0
    for target, spec in lock["targets"].items():
        for name, version in spec["packages"].items():
            if name.lower() in ranges:
                checked += 1
                assert ranges[name.lower()].contains(version, prereleases=True), (
                    f"runtime-lock {target} 钉 {name}=={version}，不满足应用依赖 {name}{ranges[name.lower()]}"
                )
    assert checked >= 3, "一个共有的包都没核到——判据量在空集合上"


@needs_tomllib
def test_pymupdf_is_only_the_legacy_extra_never_a_runtime_dependency():
    """退役的 PyMuPDF 只许经 `legacy-pymupdf` extra 进测试 / 维护者环境（D15）；`dependencies` /
    `requirements.txt` / 别的 extra 里出现它就是退役闭包被撕开。`rendercore` extra 也不该再存在
    （空别名会让 `pip install '.[rendercore]'` 看起来有效、其实什么都没装）。"""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "pymupdf" not in _dep_names(cfg["project"]["dependencies"])
    pins = [
        ln.split("==")[0].lower()
        for ln in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if ln and not ln.startswith("#")
    ]
    assert "pymupdf" not in pins  # 主语是钉住的包名，注释里提到它不算
    extras = cfg["project"]["optional-dependencies"]
    assert "rendercore" not in extras
    assert _dep_names(extras["legacy-pymupdf"]) == ["pymupdf"]
    for name, specs in extras.items():
        if name == "legacy-pymupdf":
            continue
        assert "pymupdf" not in _dep_names(specs), name
    # dev 经自引用 extra 拿到读取器，不另抄一份版本范围
    assert "tavotto[legacy-pymupdf]" in extras["dev"]
    assert not (ROOT / "requirements-rendercore.txt").exists()


@needs_tomllib
def test_wheel_artifacts_and_gitignore_cover_the_fonts_dir():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "src/tavotto/resources/fonts/**" in cfg["tool"]["hatch"]["build"]["artifacts"]
    assert "/src/tavotto/resources/fonts/" in (ROOT / ".gitignore").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 真字体


@pytest.fixture(scope="module")
def registry() -> fonts.FontRegistry:
    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(
            f"批准字体不全（not_run）：缺 {reg.missing}；先跑 scripts/fetch_fonts.py（目录 {reg.root}）"
        )
    return reg


def test_every_approved_face_is_discovered_with_the_expected_identity(registry):
    assert set(registry.faces) == set(ALLOWLIST.faces)
    assert registry.rejected == []
    for fid, rec in registry.faces.items():
        assert rec.identity == (ALLOWLIST.faces[fid].sha256, 0)
        assert rec.resource.postscript_name.replace("-", "").startswith(
            fid.split("-")[0].replace("Liberation", "Liberation")
        )
        assert rec.info.units_per_em in (1000, 2048)
        assert rec.ascender > 0 > rec.descender
        assert rec.info.num_glyphs > 1000
    serif = registry.record_for("serif")
    assert serif.approved.face_id == "LiberationSerif-Regular"
    assert (
        registry.record_for("monospace", True, True).approved.face_id == "LiberationMono-BoldItalic"
    )
    assert registry.cjk_record().approved.face_id == "NotoSansSC-Regular"
    payload = registry.to_payload()
    assert set(payload["faces"]) == set(ALLOWLIST.faces) and payload["missing"] == []


def test_the_stdlib_sfnt_reader_agrees_with_fonttools(registry):
    """注册表自己读的 head / OS/2 / name / cmap 与 fontTools 逐项相同（fontTools 是另一家实现）。"""
    ttlib = pytest.importorskip("fontTools.ttLib", reason="fontTools 未装（not_run）")
    for rec in registry.faces.values():
        tt = ttlib.TTFont(str(rec.path))
        assert rec.info.units_per_em == tt["head"].unitsPerEm
        assert rec.info.ascender == tt["OS/2"].sTypoAscender
        assert rec.info.descender == tt["OS/2"].sTypoDescender
        assert rec.info.postscript_name == tt["name"].getDebugName(6)
        theirs = {cp: tt.getGlyphID(name) for cp, name in tt.getBestCmap().items()}
        assert rec.info.cmap == theirs, rec.approved.face_id
        assert rec.info.num_glyphs == tt["maxp"].numGlyphs


def test_liberation_and_noto_coverage_facts(registry):
    """本轮明示的限制（ADR 0060 §3）：`⁻`（U+207B）哪张脸都没有；`℃` / `∇` 只在 Noto 里；Liberation
    有 Greek / `⁵` / `₂` / `Å` / `μ`。这些不是愿望，是 cmap 里量出来的。"""
    serif = registry.record_for("serif")
    cjk = registry.cjk_record()
    assert not serif.covers(0x207B) and not cjk.covers(0x207B)
    for cp in (0x2103, 0x2207):
        assert not serif.covers(cp) and cjk.covers(cp)
    for ch in "αβγΔ⁵₂Å μ±≤≥×°":
        assert serif.covers(ord(ch)), ch
    assert cjk.covers(ord("图")) and not serif.covers(ord("图"))


def test_a_same_named_copy_with_different_bytes_is_a_different_face(registry, tmp_path):
    """RC-023：把 LiberationSerif-Regular.ttf 改一个字节、同名放进另一个目录——它不在 allowlist 里，注册表
    拒绝；PostScript 名相同救不了它。"""
    src = registry.record_for("serif").path
    dst = tmp_path / "liberation" / src.name
    dst.parent.mkdir()
    data = bytearray(src.read_bytes())
    data[-1] ^= 0xFF
    dst.write_bytes(bytes(data))
    reg = fonts.FontRegistry.discover(tmp_path, ALLOWLIST)
    assert reg.faces == {}
    assert reg.rejected[0]["reason"] == "not_in_allowlist"


def test_fetch_fonts_check_passes_on_the_populated_dir(registry):
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "fetch_fonts.py"),
            "--check",
            "--dest",
            str(registry.root),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "13 张脸" in proc.stdout


def test_fetch_fonts_check_fails_when_a_face_is_tampered_or_missing(registry, tmp_path):
    root = tmp_path / "fonts"
    shutil.copytree(registry.root, root)
    (root / "liberation" / "LiberationSerif-Regular.ttf").write_bytes(b"not a font")
    (root / "noto" / "NotoSansSC-Regular.otf").unlink()
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "fetch_fonts.py"), "--check", "--dest", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert proc.returncode == 1
    assert "LiberationSerif-Regular" in proc.stderr and "NotoSansSC-Regular" in proc.stderr


def test_a_tampered_license_text_is_caught_and_replaced(registry, tmp_path):
    """许可证全文与字体同一条纪律（Codex #460 P2）：截断的 `liberation/LICENSE` 在 `--check` 下红；
    `fetch()` 复用目录时按 `file_sha256` 发现它不对就重新解出来（不联网：tarball 在缓存里）。"""
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "scripts"))
    import fetch_fonts

    root = tmp_path / "fonts"
    shutil.copytree(registry.root, root)
    lic = root / "liberation" / "LICENSE"
    lic.write_bytes(lic.read_bytes()[:100])
    problems = fetch_fonts.check(root)
    assert any("liberation/LICENSE" in p and "SHA-256" in p for p in problems), problems
    cache = ROOT / "build" / "fonts-cache"
    if not (cache / "liberation-fonts-ttf-2.1.5.tar.gz").is_file():
        pytest.skip("本机没有 tarball 缓存（不联网修复无法演示，not_run）")
    fetch_fonts.fetch(root, cache=cache)
    assert fetch_fonts.check(root) == []
    assert (
        fetch_fonts.sha256_file(lic)
        == fetch_fonts.load_allowlist()["license_files"]["liberation/LICENSE"]["file_sha256"]
    )


def test_license_texts_travel_with_the_fonts(registry):
    for rel in ALLOWLIST.license_files:
        text = (registry.root / rel).read_text(encoding="utf-8", errors="replace")
        assert "SIL OPEN FONT LICENSE" in text.upper(), rel
