"""Windows NSIS 安装界面的打包卫生。

`src-tauri/windows/installer.nsi` 是按钉住的 @tauri-apps/cli 版本 vendored 的
上游模板 + 品牌补丁：模板与打包器必须同源。这里看护三类事——

1. **首次 GUI 安装只剩两页**：真实安装进度（MUI_PAGE_INSTFILES）→ 完成页
   （MUI_PAGE_FINISH）。欢迎页/目录页/开始菜单页/许可证页一律不可见。
2. **精简没有顺手删掉安装能力**：currentUser 安装、固定安装路径与升级时
   恢复历史路径、WebView2、快捷方式、卸载注册表、命令行开关、降级保护、
   WiX 迁移——每一条都还在。
3. 四处 CLI 版本同源、配置引用的品牌资产真实存在且是 NSIS 吃得下的形态。
4. **装完真的登记了 CLI 入口**。只写卸载注册表不等于外部程序（Codex 插件）
   能发现 Tavotto——它不读卸载信息，也不该只靠注册表（企业策略能锁掉它）。

**这些是源码级看护，不是「装出来是这样」的证据**：安装器真实页面序列、
UAC、中文路径这些只有 Windows 上跑真产物才算数，走
`.github/workflows/nightly.yml` 的「装一遍再冒烟」那条链路。
"""

import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "src-tauri" / "windows" / "installer.nsi"
CONF = ROOT / "src-tauri" / "tauri.conf.json"

TEXT = TEMPLATE.read_text(encoding="utf-8")
LINES = TEXT.splitlines()
CONFIG = json.loads(CONF.read_text(encoding="utf-8"))
NSIS_CONF = CONFIG["bundle"]["windows"]["nsis"]
FLAT = re.sub(r"[ \t]+", " ", TEXT)
# 整行注释剥掉后的代码。断言「某个 define 不存在」必须打在这上面——
# 补丁注释里正写着那些符号名，打在原文上会被自己的说明骗过去。
CODE = "\n".join(ln for ln in LINES if not ln.lstrip().startswith(";"))

# 补丁新增的 LangString。安装器只剩两页，这几条就是用户会读到的全部文案。
BRAND_STRINGS = (
    "preparingTavotto",
    "installingTavotto",
    "finishTitle",
    "finishText",
    "openTavotto",
    "registeringTavotto",
)


def _pre_function_for(macro: str, lines: list[str] | None = None) -> str | None:
    """返回某个页面宏生效的 MUI_PAGE_CUSTOMFUNCTION_PRE。

    MUI2 在每次插入页面后会 unset 这个 define，所以「生效的那一个」就是
    紧邻其上、上一次页面插入之后最后一条 PRE 定义。
    """
    target = re.compile(rf"^\s*!insertmacro\s+{re.escape(macro)}\b")
    pre = re.compile(r"^\s*!define\s+MUI_PAGE_CUSTOMFUNCTION_PRE\s+(\S+)")
    any_page = re.compile(r"^\s*!insertmacro\s+(MUI_PAGE_\w+|MULTIUSER_PAGE_\w+)\b")
    current = None
    for line in lines if lines is not None else LINES:
        if target.match(line):
            return current
        m = pre.match(line)
        if m:
            current = m.group(1)
            continue
        if any_page.match(line):
            current = None
    raise AssertionError(f"脚本里找不到 !insertmacro {macro}")


# ---------------------------------------------------------------- 页面结构


def test_no_welcome_page():
    """欢迎页连宏一起没了——不是藏起来，是不存在。"""
    assert "!insertmacro MUI_PAGE_WELCOME" not in CODE
    assert "MUI_WELCOMEPAGE_TITLE" not in CODE


def test_directory_page_is_never_visible():
    """目录页恒被 Abort：安装位置由 .onInit 决定，不问用户。

    注意判据是 `Skip` 而不是 `SkipIfPassive`——后者只在 /P 静默时跳过，
    普通双击安装照样会弹出「你要装到哪儿」。
    """
    assert _pre_function_for("MUI_PAGE_DIRECTORY") == "Skip"


def test_start_menu_page_is_never_visible():
    """开始菜单文件夹选择页同理，且不再由 startMenuFolder 配置顺带决定。"""
    assert _pre_function_for("MUI_PAGE_STARTMENU") == "Skip"
    # 上游的 `!else` 分支（只在没配 startMenuFolder 时才 Skip）必须已经拆掉，
    # 否则加一行配置就能把选择页无声地放回来
    block = TEXT.split("; 6. Start menu shortcut page")[1].split("; 7.")[0]
    assert "SkipIfPassive" not in block


def test_skip_really_aborts():
    """`Skip` 必须是无条件 Abort——上面两条测试全靠它。"""
    body = TEXT.split("Function Skip\n")[1].split("FunctionEnd")[0]
    assert body.strip() == "Abort"


def test_license_page_not_introduced():
    """当前不配许可证；许可证页只在 ${LICENSE} 非空时才插入，且配置里没有。"""
    assert '!if "${LICENSE}" != ""' in TEXT
    assert not NSIS_CONF.get("license")
    assert not CONFIG["bundle"].get("licenseFile")


def test_progress_and_finish_pages_survive():
    """真实进度页与完成页是仅剩的两页，一个都不能少。"""
    assert "!insertmacro MUI_PAGE_INSTFILES" in TEXT
    assert "!insertmacro MUI_PAGE_FINISH" in TEXT
    # 进度条必须仍由 NSIS 的真实安装过程驱动：没有自定义页顶替它
    assert "Page custom" not in CODE.replace("Page custom PageReinstall", "")


def test_no_log_list_and_no_manual_step_before_finish():
    """日志列表恒不可见；装完自动进完成页。"""
    assert "ShowInstDetails nevershow" in TEXT
    assert "ShowUninstDetails nevershow" in TEXT
    # MUI 只要看到这个 define 就不会 SetAutoClose，装完会停在日志页等用户再点一次
    assert "MUI_FINISHPAGE_NOAUTOCLOSE" not in CODE
    # DetailPrint 是排障现场，一条都不许删（nevershow 已经让用户看不到了）
    assert TEXT.count("DetailPrint") >= 10


def test_finish_page_has_no_fake_readme_option():
    """完成页不再借 showreadme 复选框当「创建桌面快捷方式」。"""
    assert "MUI_FINISHPAGE_SHOWREADME" not in CODE


def test_finish_page_runs_via_run_as_user():
    """「打开 Tavotto」走 MUI 官方 RUN 控件 + RunAsUser。"""
    assert "!define MUI_FINISHPAGE_RUN\n" in TEXT
    assert "!define MUI_FINISHPAGE_RUN_FUNCTION RunMainBinary" in TEXT
    body = TEXT.split("Function RunMainBinary\n")[1].split("FunctionEnd")[0]
    # 普通 Exec 会把安装器的令牌继承给应用
    assert "nsis_tauri_utils::RunAsUser" in body
    assert not re.search(r"^\s*Exec(Wait|Shell)?\s", body, re.M)


def test_user_facing_text_is_localized_for_every_language():
    """每个配置里的语言都必须给全所有品牌文案。

    NSIS 对缺失的 LangString 只给一条警告然后填空字符串——安装器照样打得
    出来，只是完成页没字。加语言时这条测试先红。
    """
    langs = NSIS_CONF["languages"]
    assert langs, "nsis.languages 不能为空"
    for key in BRAND_STRINGS:
        assert f"$({key})" in TEXT, f"{key} 定义了却没人用"
        for lang in langs:
            # 模板里为了对齐用了多个空格，比对前先把空白压平
            needle = 'LangString %s ${LANG_%s} "' % (key, lang.upper())
            assert needle in FLAT, f"{lang} 缺少 LangString {key}"


def test_progress_page_header_is_ours_start_to_finish():
    """进度页的页眉从开始到装完都是品牌文案，中间不闪 MUI 默认那两句。

    「Installation Complete / Setup was completed successfully.」是 MUI 在
    instfiles 的 LEAVE 里塞的，就闪在自动跳完成页之前——nightly 的 GUI 探针
    在真安装器上抓到过它。
    """
    assert '!define MUI_PAGE_HEADER_TEXT "$(installingTavotto)"' in TEXT
    assert '!define MUI_PAGE_HEADER_SUBTEXT ""' in TEXT
    assert '!define MUI_INSTFILESPAGE_FINISHHEADER_TEXT "$(finishTitle)"' in TEXT
    assert '!define MUI_INSTFILESPAGE_FINISHHEADER_SUBTEXT ""' in TEXT
    # 中止时的页眉不动：那是异常流程，「安装未完成」正是该说的话
    assert "MUI_INSTFILESPAGE_ABORTHEADER" not in CODE


def test_status_text_is_honest():
    """进度页的状态行说的是真在做的事，不是假百分比。"""
    assert 'DetailPrint "$(preparingTavotto)"' in TEXT
    assert 'DetailPrint "$(installingTavotto)"' in TEXT
    assert 'DetailPrint "$(installingWebview2)"' in TEXT  # 上游字符串，已本地化
    # 展开文件时的 `Extract: xxx.dll` 只进日志，不刷状态行；复制完还原
    assert "SetDetailsPrint listonly" in TEXT
    assert "SetDetailsPrint both" in TEXT
    for fake in ("78%", "Progress:", 'IntFmt $0 "%d%%"'):
        assert fake not in TEXT


# ------------------------------------------------------------ 安装能力保留


def test_current_user_install_without_admin():
    assert NSIS_CONF["installMode"] == "currentUser"
    block = TEXT.split('!if "${INSTALLMODE}" == "currentUser"')[1].split("!endif")[0]
    assert "RequestExecutionLevel user" in block


def test_install_dir_is_localappdata_and_upgrades_keep_old_path():
    """新装固定 %LOCALAPPDATA%\\Tavotto；已有安装沿用注册表里的老路径。"""
    assert 'InstallDir "${PLACEHOLDER_INSTALL_DIR}"' in TEXT
    assert '${If} $INSTDIR == "${PLACEHOLDER_INSTALL_DIR}"' in TEXT
    assert 'StrCpy $INSTDIR "$LOCALAPPDATA\\${PRODUCTNAME}"' in TEXT
    assert "Call RestorePreviousInstallLocation" in TEXT
    body = TEXT.split("Function RestorePreviousInstallLocation\n")[1].split("FunctionEnd")[0]
    assert 'ReadRegStr $4 SHCTX "${MANUPRODUCTKEY}" ""' in body


def test_shortcut_helpers_and_policy():
    """快捷方式：不给选择页，但入口策略一个没变。"""
    assert "Function CreateOrUpdateStartMenuShortcut" in TEXT
    assert "Function CreateOrUpdateDesktopShortcut" in TEXT
    assert "Call CreateOrUpdateStartMenuShortcut" in TEXT
    # 行为变化：GUI 安装的桌面快捷方式从「完成页复选框（默认勾上）」
    # 改成 Section Install 里无条件创建，与静默/被动安装同一条路径
    section = TEXT.split("Section Install\n")[1].split("SectionEnd")[0]
    assert re.search(r"^\s*Call CreateOrUpdateDesktopShortcut\s*$", section, re.M)
    assert not re.search(
        r"\$\{If\} \$PassiveMode = 1\s*\n\s*\$\{OrIf\} \$\{Silent\}\s*\n"
        r"\s*Call CreateOrUpdateDesktopShortcut",
        section,
    )
    # /NS 与 /UPDATE 的豁免仍在函数内部
    for fn in ("CreateOrUpdateStartMenuShortcut", "CreateOrUpdateDesktopShortcut"):
        body = TEXT.split(f"Function {fn}\n")[1].split("FunctionEnd")[0]
        assert "$UpdateMode = 1" in body and "$NoShortcutMode = 1" in body
    # 卸载时清掉开始菜单与桌面快捷方式（含旧版落点）
    uninst = TEXT.split("Section Uninstall\n")[1].split("SectionEnd")[0]
    assert "$SMPROGRAMS\\$AppStartMenuFolder\\${PRODUCTNAME}.lnk" in uninst
    assert "$SMPROGRAMS\\${PRODUCTNAME}.lnk" in uninst
    assert "$DESKTOP\\${PRODUCTNAME}.lnk" in uninst


def test_command_line_switches_survive():
    for flag in ("/P", "/NS", "/UPDATE", "/R", "/ARGS"):
        assert f'$CMDLINE "{flag}"' in TEXT, f"命令行开关 {flag} 没了"


def test_exception_flows_still_have_their_pages():
    """异常流程该弹的还得弹——精简的是首次安装，不是安全判断。"""
    assert "Page custom PageReinstall PageLeaveReinstall" in TEXT
    assert "$(alreadyInstalledLong)" in TEXT  # 同版本
    assert "$(newerVersionInstalled)" in TEXT  # 降级
    assert '!if "${ALLOWDOWNGRADES}" == "false"' in TEXT
    assert "StrCpy $WixMode 1" in TEXT  # 旧 WiX 安装迁移
    assert "!insertmacro CheckIfAppIsRunning" in TEXT  # 应用仍在运行
    assert 'Abort "$(webview2AbortError)"' in TEXT  # WebView2 装不上
    assert "!insertmacro MUI_UNPAGE_CONFIRM" in TEXT  # 卸载确认
    assert "!insertmacro MUI_UNPAGE_INSTFILES" in TEXT


def test_no_magplot_migration_in_the_installer():
    """安装器**不许**识别 Magplot 0.7 的旧身份（PR #101 review 裁决）。

    曾试过在 Section Install 前静默卸载 `Uninstall\\Magplot`（让 0.7.0 的
    应用内更新一步换成 Tavotto），被 review 按 AGENTS.md 的「干净断裂」
    否掉：那段的语境正是**不认上一代的名字**，唯二例外都是「用户磁盘上
    我们改不到的东西」的读取端回退，安装器新添一处对旧身份的识别不在
    其列。0.7.0 的既定路径是发版说明写明「先卸载旧版」（issue #99），
    `docs/migration-magplot.md` 桌面一节照此描述。
    """
    assert "MigrateMagplot" not in CODE
    assert "Uninstall\\Magplot" not in CODE


def test_payload_and_registration_survive():
    section = TEXT.split("Section Install\n")[1].split("SectionEnd")[0]
    assert "{{#each binaries}}" in section  # sidecar / workerd / 内置 runtime
    assert "{{#each resources}}" in section
    assert "{{#each file_associations" in section
    assert "{{#each deep_link_protocols" in section
    assert 'WriteUninstaller "$INSTDIR\\uninstall.exe"' in section
    for value in ("DisplayName", "DisplayVersion", "InstallLocation", "UninstallString"):
        assert f'"${{UNINSTKEY}}" "{value}"' in section
    assert "Section WebView2" in TEXT


# ---------------------------------------------------------- 版本与品牌资产


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
    assert "TAVOTTO PATCH" in TEXT
    assert 'MUI_BGCOLOR "F2F2EF"' in TEXT
    assert 'MUI_TEXTCOLOR "1B1B18"' in TEXT
    # 前端的 selection blue #2F6FED 不是安装器主色，别混用
    assert "2F6FED" not in TEXT


def test_cli_version_pinned_and_in_sync():
    vers = _pinned_versions()
    assert len(set(vers.values())) == 1, f"CLI 版本不同源: {vers}"


def test_installer_bitmaps_match_the_generator():
    """提交在仓库里的安装器位图必须与生成脚本的当前输出一致。

    这两张 BMP 是**进 git 的**受管构建物（`canvas.html` 2026-08-30 起改成不进
    git 现建了，这两张没有：它们只在改品牌时变，不会像前端产物那样每个 PR 都
    重建，所以不吃合并队列同组必撞那个亏）：改名 Magplot →
    Tavotto 时脚本改了、产物没重新生成，0.8.0 起发出去的每一个 Windows
    安装包侧栏都还写着 "Magplot"（2026-08-25 用户报告）。逐字节比对会被
    渲染器（PyMuPDF）版本的抗锯齿差异误伤，所以按**强差异像素占比**判：
    字标换字是大面积高强度差（实测 0.76%–3.1%），抗锯齿漂移是低强度差。
    红了的正确动作永远是重跑 `scripts/build_installer_assets.py` 并提交。
    """
    pytest.importorskip("pymupdf")
    import importlib.util
    import struct
    import sys

    spec = importlib.util.spec_from_file_location(
        "build_installer_assets", ROOT / "scripts" / "build_installer_assets.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("build_installer_assets", mod)
    spec.loader.exec_module(mod)

    import tempfile

    def decode(data: bytes) -> tuple[int, int, bytes]:
        off = struct.unpack_from("<I", data, 10)[0]
        w, h = struct.unpack_from("<ii", data, 18)
        stride = (w * 3 + 3) // 4 * 4
        rows = [data[off + y * stride : off + y * stride + w * 3] for y in range(abs(h))]
        return w, abs(h), b"".join(rows)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        mod.ROOT = tmp_path
        mod.BRAND = tmp_path
        mod.header()
        mod.sidebar()
        for name in ("installer-header.bmp", "installer-sidebar.bmp"):
            committed = (ROOT / "assets" / "brand" / name).read_bytes()
            fresh = (tmp_path / name).read_bytes()
            cw, ch, cpx = decode(committed)
            fw, fh, fpx = decode(fresh)
            assert (cw, ch) == (fw, fh), f"{name} 尺寸变了：提交 {cw}x{ch} vs 生成 {fw}x{fh}"
            strong = sum(1 for a, b in zip(cpx, fpx) if abs(a - b) > 64)
            ratio = strong / len(cpx)
            assert ratio < 0.002, (
                f"{name} 与生成脚本的输出差 {ratio:.2%} 强差异像素——产物过期了，"
                "重跑 scripts/build_installer_assets.py 并提交"
            )


def test_nsis_config_paths_resolve():
    base = ROOT / "src-tauri"
    for key in ("template", "installerIcon", "headerImage", "sidebarImage"):
        p = (base / NSIS_CONF[key]).resolve()
        assert p.is_file(), f"nsis.{key} 指向不存在的文件: {p}"
    # BMP 必须是 bottom-up 的 24 位 BMP3（top-down DIB 在 NSIS 里有兼容风险）
    for key in ("headerImage", "sidebarImage"):
        data = (base / NSIS_CONF[key]).resolve().read_bytes()
        assert data[:2] == b"BM"
        height = int.from_bytes(data[22:26], "little", signed=True)
        assert height > 0, f"nsis.{key} 是 top-down DIB，应为 bottom-up"
        bits = int.from_bytes(data[28:30], "little")
        assert bits == 24, f"nsis.{key} 是 {bits} 位 BMP，NSIS 要 24 位"


# ------------------------------------------------ Tauri 渲染出来的中间脚本
#
# 上面全部打在**仓库模板**上，而真正喂给 makensis 的是 Tauri 把 {{...}}
# 占位符展开后的那份。两者之间隔着一个打包器：`installMode` /
# `startMenuFolder` / `license` / 语言表都是那时候才落定的——模板里写着
# 「没配许可证就不插许可证页」，配置真值只有在中间脚本里才看得见。
#
# 只有 Windows 构建之后才有这个文件。CI 用 TAVOTTO_NSIS_GENERATED 指名，
# 指了就**必须**存在：让它悄悄 skip 等于把门禁变成一条 notice。

GENERATED_ENV = "TAVOTTO_NSIS_GENERATED"


def _generated_script() -> Path | None:
    explicit = os.environ.get(GENERATED_ENV)
    if explicit:
        p = Path(explicit)
        assert p.is_file(), f"{GENERATED_ENV} 指向的中间脚本不存在: {p}"
        return p
    base = ROOT / "src-tauri" / "target" / "release" / "nsis"
    hits = sorted(base.rglob("installer.nsi")) if base.is_dir() else []
    return hits[0] if hits else None


def test_generated_script_has_the_same_two_pages():
    gen = _generated_script()
    if gen is None:
        pytest.skip("没有 tauri 渲染出来的中间脚本（Windows 打包之后才有）")
    text = gen.read_text(encoding="utf-8")
    lines = text.splitlines()
    code = "\n".join(ln for ln in lines if not ln.lstrip().startswith(";"))

    # 占位符真的展开了（否则下面几条断言只是在重测模板）
    assert "{{" not in code, "中间脚本里还留着未展开的 handlebars 占位符"

    # 首次安装只剩这两页
    assert "!insertmacro MUI_PAGE_INSTFILES" in code
    assert "!insertmacro MUI_PAGE_FINISH" in code
    assert "!insertmacro MUI_PAGE_WELCOME" not in code
    assert _pre_function_for("MUI_PAGE_DIRECTORY", lines) == "Skip"
    assert _pre_function_for("MUI_PAGE_STARTMENU", lines) == "Skip"

    # 这三条决定了另外三页存不存在，且只有打包器说了算
    assert '!define INSTALLMODE "currentUser"' in code  # 没有安装模式页，不要管理员
    assert '!define LICENSE ""' in code  # 没有许可证页
    assert '!define STARTMENUFOLDER ""' in code  # 快捷方式直接落 $SMPROGRAMS

    # 完成页的形态
    assert "MUI_FINISHPAGE_SHOWREADME" not in code
    assert "MUI_FINISHPAGE_NOAUTOCLOSE" not in code
    assert "!define MUI_FINISHPAGE_RUN_FUNCTION RunMainBinary" in code

    # 语言表与品牌文案对得上（打包器决定插哪几种语言）
    langs = re.findall(r'!insertmacro MUI_LANGUAGE "(\w+)"', code)
    assert sorted(langs) == sorted(NSIS_CONF["languages"]), (
        f"中间脚本的语言表 {langs} 与 tauri.conf.json 不一致"
    )
    flat = re.sub(r"[ \t]+", " ", code)
    for key in BRAND_STRINGS:
        for lang in langs:
            assert 'LangString %s ${LANG_%s} "' % (key, lang.upper()) in flat, (
                f"中间脚本缺 {lang} 的 {key}"
            )

    # 精简没有把要装的东西弄丢：sidecar / workerd / 内置 runtime 都在
    assert '!define MAINBINARYNAME "Tavotto"' in code
    assert re.search(r'^\s*File /a "/oname=', code, re.M), "中间脚本里没有任何 binaries/resources"


# ------------------------------------------- 外部程序发现得了这台机器上的 CLI
#
# 起因：只装了桌面版的 Windows 用户那里，Codex 插件一直报「没找到 Tavotto」。
# 装出来的 Tavotto.exe 是 GUI 子系统的可执行文件，当命令行调它拿不到 stdout。
# 修法是安装包里另带一个 console 版 tavotto-cli，并在装完时由它写一份
# 安装清单。下面几条盯的是「安装器有没有真的把这一步做掉」。

from tavotto.engine import locate  # noqa: E402

SECTION_INSTALL = TEXT.split("Section Install\n")[1].split("SectionEnd")[0]
SECTION_UNINSTALL = TEXT.split("Section Uninstall\n")[1].split("SectionEnd")[0]


def _bundled_cli_path() -> str:
    """安装后 CLI 的落点，**由 tauri.conf.json 的 resources 映射推出来**。

    在测试里重新推一遍而不是写死，是为了让「改了那份映射却忘了改安装器」
    当场变红——那种漏改的表现是装完一切正常，只有外部程序发现不了 Tavotto。
    """
    target = CONFIG["bundle"]["resources"]["../dist/Tavotto"]
    return "$INSTDIR\\" + target.replace("/", "\\") + "\\" + locate.CLI_NAME


def test_sidecar_layout_has_a_single_source_of_truth():
    """tauri.conf.json 的 resources 映射 == engine/locate.SIDECAR_REL。

    Rust 壳（src-tauri/src/sidecar.rs）、Python 定位器、NSIS 安装段三处都按
    这个布局找 sidecar 目录；映射一改，三处必须同时改。
    """
    assert CONFIG["bundle"]["resources"]["../dist/Tavotto"] == "/".join(locate.SIDECAR_REL)
    rust = (ROOT / "src-tauri" / "src" / "sidecar.rs").read_text(encoding="utf-8")
    for part in locate.SIDECAR_REL:
        assert f'join("{part}")' in rust, f"sidecar.rs 里找不到 {part}"


def test_install_registers_the_cli_through_the_bundled_binary():
    """装完跑一次装进来的 tavotto-cli，由它写安装清单。

    为什么让 CLI 自己写、而不是让 NSIS 拼 JSON：安装目录可能带空格和中文，
    NSIS 里手工转义 JSON 反斜杠是纯粹的自找麻烦；而且这一跑同时就是**无 GUI
    的装后健康检查**——CLI 起不来的包，发出去只会表现为「Codex 找不到 Tavotto」。
    """
    assert _bundled_cli_path() in SECTION_INSTALL
    assert "doctor --json --write-manifest" in SECTION_INSTALL
    assert "nsExec::" in SECTION_INSTALL  # 不弹窗、不闪黑框
    assert "/TIMEOUT=" in SECTION_INSTALL  # 冷启动异常时不许挂住安装器


def test_registration_failure_never_aborts_the_install():
    """清单只是快路径：写不出来也不能让整个安装失败。

    已知安装位置那条腿还在，用户照样能用；为一个可选的加速文件把安装打断，
    换来的是一个装不上的产品。
    """
    block = SECTION_INSTALL.split("$(registeringTavotto)")[1]
    block = block.split("TAVOTTO PATCH END")[0]
    assert "Abort" not in block
    assert "DetailPrint" in block  # 但要留下痕迹，别静默


def test_uninstall_removes_the_manifest_before_deleting_files():
    """顺序不能换：清单是那个 CLI 自己删的，文件删完就没人删得掉了。

    留下一份指向已卸载路径的清单，外部程序会拿着不存在的路径去 spawn——
    报出来的是「执行不了」，而用户需要看到的是「没装」。
    """
    assert "doctor --json --remove-manifest" in SECTION_UNINSTALL
    assert SECTION_UNINSTALL.index("--remove-manifest") < SECTION_UNINSTALL.index(
        'Delete "$INSTDIR\\${MAINBINARYNAME}.exe"'
    )


def test_registration_does_not_need_admin():
    """全程仍是 currentUser：清单落在用户配置目录，注册表一个字没多写。"""
    assert NSIS_CONF["installMode"] == "currentUser"
    for needle in ("RequestExecutionLevel admin", "SetShellVarContext all"):
        assert needle not in SECTION_INSTALL


def test_installer_leaves_the_user_path_alone():
    """**不动用户的 PATH**——这是有意的取舍，不是漏做。

    发现链靠清单 + 已知安装位置就够了；改 PATH 要写注册表、广播
    WM_SETTINGCHANGE、处理 1024 字符截断，还要在卸载时准确摘掉自己那一段，
    每一步都可能把用户的 PATH 弄坏。风险与收益不成比例。
    """
    for needle in ("EnVar::", '"Environment"', "WM_SETTINGCHANGE"):
        assert needle not in CODE, f"安装器动了 PATH（{needle}）"


def test_nightly_packaging_does_not_need_a_signing_key():
    """nightly 打安装器时必须就地关掉 createUpdaterArtifacts。

    tauri.conf.json 里它常开（发行链要它），而打包器一开它就要 minisign 私钥；
    私钥只在 release 的 secret 里，nightly 没有也不该有。不关的话安装包明明
    已经打出来了，tauri 仍以「A public key has been found, but no private key」
    退出 1——整条「装一遍再冒烟」的验收根本跑不到。

    这不是假设：`createUpdaterArtifacts` 随应用内更新那个 PR 合进 main 之后，
    nightly 的安装腿就一直红着，而 scripts/build_desktop.py 早有同样的处理
    （那条链路走它，所以没暴露）。
    """
    assert CONFIG["bundle"]["createUpdaterArtifacts"] is True, "配置变了的话这条看护要重新想一遍"
    nightly = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")
    build = nightly.split("Tauri 打包")[1].split("- name:")[0]
    assert "createUpdaterArtifacts" in build and "false" in build, (
        "nightly 的 tauri build 没关掉更新包产出——没有私钥时它会直接失败"
    )
    # build_desktop.py 那条链路同样得有（它是本地/发行走的那条）
    desktop = (ROOT / "scripts" / "build_desktop.py").read_text(encoding="utf-8")
    assert "TAURI_SIGNING_PRIVATE_KEY" in desktop
    assert "createUpdaterArtifacts" in desktop
