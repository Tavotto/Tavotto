"""Windows NSIS 品牌安装界面的打包卫生。

src-tauri/windows/installer.nsi 是按钉住的 @tauri-apps/cli 版本 vendored 的
上游模板 + 品牌补丁：模板与打包器必须同源。这里看护三件事——
补丁标记还在、四处版本号一致、配置引用的资产真实存在。
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "src-tauri" / "windows" / "installer.nsi"


def _pinned_versions() -> dict[str, str]:
    pat = re.compile(r"@tauri-apps/cli@([\d.]+)")
    out = {}
    for label, path in [
        ("template", TEMPLATE),
        ("build_desktop", ROOT / "scripts" / "build_desktop.py"),
        ("workflow", ROOT / ".github" / "workflows" / "desktop-tauri.yml"),
        # nightly 的「装一遍再冒烟」也自己打一个 NSIS 安装器，
        # 版本漂了就等于拿另一个打包器去配这份 vendored 模板
        ("nightly", ROOT / ".github" / "workflows" / "nightly.yml"),
    ]:
        text = path.read_text(encoding="utf-8")
        m = pat.search(text) or re.search(r"tauri-cli-v([\d.]+)", text)
        assert m, f"{path} 里找不到钉住的 tauri CLI 版本"
        out[label] = m.group(1)
    return out


def test_template_exists_with_patch_markers():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "MAGPLOT PATCH" in text
    # 关键补丁点：欢迎页移除后原宏绝不能残留；极简进度必须生效
    assert "!insertmacro MUI_PAGE_WELCOME" not in text
    assert "ShowInstDetails nevershow" in text
    assert 'MUI_BGCOLOR "F2F2EF"' in text


def test_cli_version_pinned_and_in_sync():
    vers = _pinned_versions()
    assert len(set(vers.values())) == 1, f"CLI 版本不同源: {vers}"


def test_nsis_config_paths_resolve():
    conf = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text("utf-8"))
    nsis = conf["bundle"]["windows"]["nsis"]
    base = ROOT / "src-tauri"
    for key in ("template", "installerIcon", "headerImage", "sidebarImage"):
        p = (base / nsis[key]).resolve()
        assert p.is_file(), f"nsis.{key} 指向不存在的文件: {p}"
    # BMP 必须是 bottom-up 的 24 位 BMP3（top-down DIB 在 NSIS 里有兼容风险）
    for key in ("headerImage", "sidebarImage"):
        data = (base / nsis[key]).resolve().read_bytes()
        assert data[:2] == b"BM"
        height = int.from_bytes(data[22:26], "little", signed=True)
        assert height > 0, f"nsis.{key} 是 top-down DIB，应为 bottom-up"
