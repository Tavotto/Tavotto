"""桌面壳的多语言：Rust 侧文案表、启动/失败页、以及新命令的 ACL 三处同步。

为什么这些要用 Python 测：

* **两侧同源的看护得站在两侧之外。** 菜单文案在 `src-tauri/src/i18n.rs` 里
  另有一份（Rust 在 webview 起来之前就要建菜单，那时没有 i18next）。Rust 的
  单测只能证明「这份表自己是齐的」，证明不了「它和界面说的是同一件事」——
  菜单写 Undo、界面写 Revert 是两侧各自都绿的坏法。
* **`#[tauri::command]` 的 ACL 漏配是静默失败**（CLAUDE.md 记着 reveal_export
  那次：invoke 被直接拒，界面上就是「点了没反应」）。三处声明必须同时在。
* **启动画面与失败页不在前端的构建里**：它们是 `tauri://` 源下的静态 HTML，
  漏翻不会被 `pnpm i18n:check` 看见。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TAURI = ROOT / "src-tauri"
I18N_RS = TAURI / "src" / "i18n.rs"
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"

pytestmark = pytest.mark.skipif(
    not I18N_RS.is_file(), reason="没有 src-tauri/（wheel/sdist 里不含桌面壳）"
)

CJK = re.compile(r"[一-鿿]")


def _rs() -> str:
    return I18N_RS.read_text(encoding="utf-8")


def _table(name: str) -> dict[str, str]:
    """从 `const ZH: ShellText = ShellText { ... };` 里抠出字段 → 文案。"""
    src = _rs()
    start = src.index(f"const {name}: ShellText = ShellText {{")
    body = src[start : src.index("\n};", start)]
    # 字段值都是字符串字面量（可能跨行），取 `名字: "……"` 这一对
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r"^\s{4}(\w+):\s*\n?\s*\"((?:[^\"\\]|\\.)*)\"", body, re.M)
    }


def _struct_fields() -> list[str]:
    src = _rs()
    start = src.index("pub struct ShellText {")
    body = src[start : src.index("\n}", start)]
    return re.findall(r"^\s{4}pub (\w+): &'static str,", body, re.M)


def _web(locale: str, ns: str) -> dict:
    return json.loads((LOCALES / locale / f"{ns}.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Rust 侧的两张表
# --------------------------------------------------------------------------- #


def test_both_tables_cover_every_field():
    fields = set(_struct_fields())
    assert fields, "没解析出 ShellText 的字段——解析逻辑跟着源码走样了"
    for name in ("ZH", "EN"):
        assert set(_table(name)) == fields, f"{name} 的字段与 ShellText 对不上"


def test_english_table_has_no_chinese():
    leftovers = {k: v for k, v in _table("EN").items() if CJK.search(v)}
    assert not leftovers, f"英文文案里还留着中文：{leftovers}"


def test_interpolation_placeholders_match_across_languages():
    """`{path}` / `{status}` / `{tail}` 两边必须一样，替换靠的就是它们。"""
    zh, en = _table("ZH"), _table("EN")
    for key in zh:
        a = set(re.findall(r"\{(\w+)\}", zh[key]))
        b = set(re.findall(r"\{(\w+)\}", en[key]))
        assert a == b, f"{key} 的占位符不一致：zh={a} en={b}"


# --------------------------------------------------------------------------- #
# 与界面说同一件事
# --------------------------------------------------------------------------- #

# Rust 字段 → 前端 (命名空间, 点分 key)。菜单项后面的省略号不参与比较；
# 大小写也不比——macOS 菜单是 Title Case（Zoom In），界面是句首大写（Zoom in），
# 那是各自平台的写法，不是两个词。
SHARED_WITH_UI = {
    "edit_undo": ("workspace", "topbar.undo"),
    "edit_redo": ("workspace", "topbar.redo"),
    "file_export": ("workspace", "topbar.export"),
    "file_save_layout": ("dialogs", "palette.commands.save-layout.label"),
    "edit_duplicate": ("workspace", "quickEdit.duplicate"),
    "edit_delete": ("common", "actions.delete"),
    "align_left": ("inspector", "alignMode.left"),
    "align_hcenter": ("inspector", "alignMode.hcenter"),
    "align_right": ("inspector", "alignMode.right"),
    "align_top": ("inspector", "alignMode.top"),
    "align_vcenter": ("inspector", "alignMode.vcenter"),
    "align_bottom": ("inspector", "alignMode.bottom"),
    "align_hdist": ("inspector", "alignMode.hdist"),
    "align_vdist": ("inspector", "alignMode.vdist"),
    "view_zoom_in": ("workspace", "topbar.zoomIn"),
    "view_zoom_out": ("workspace", "topbar.zoomOut"),
    "view_fit": ("workspace", "topbar.fitCanvas"),
    "help_shortcuts": ("workspace", "topbar.shortcutHelp"),
    "help_diagnostics": ("dialogs", "settings.about.exportBundle"),
}


def _dig(node: dict, dotted: str) -> str:
    for part in dotted.split("."):
        node = node[part]
    assert isinstance(node, str)
    return node


@pytest.mark.parametrize("locale,table", [("zh-CN", "ZH"), ("en-US", "EN")])
def test_menu_and_ui_agree_on_shared_actions(locale: str, table: str):
    rs = _table(table)
    for field, (ns, key) in SHARED_WITH_UI.items():
        menu = rs[field].rstrip("…").strip()
        ui = _dig(_web(locale, ns), key).rstrip("…").strip()
        assert menu.casefold() == ui.casefold(), (
            f"{locale} 的「{field}」菜单写「{menu}」、界面写「{ui}」"
        )


# --------------------------------------------------------------------------- #
# 启动画面 / 失败页
# --------------------------------------------------------------------------- #

SHELL_PAGES = ["splash.html", "error.html"]


@pytest.mark.parametrize("page", SHELL_PAGES)
def test_shell_page_carries_both_languages(page: str):
    """两份文案都内联在页面里，键集合一致，英文那份不带中文。"""
    text = (TAURI / "shell" / page).read_text(encoding="utf-8")
    assert "'zh-CN':" in text and "'en-US':" in text, f"{page} 没有内联两种语言"

    def block(tag: str) -> dict[str, str]:
        # 两张页面一个写成单行、一个写成多行，按花括号配平截，别按缩进猜
        start = text.index(f"'{tag}': {{") + len(f"'{tag}': ")
        depth, end = 0, start
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        body = text[start : end + 1]
        return {m.group(1): m.group(2) for m in re.finditer(r"(\w+): '((?:[^'\\]|\\.)*)'", body)}

    zh, en = block("zh-CN"), block("en-US")
    assert zh and set(zh) == set(en), f"{page} 两种语言的条目对不上：{set(zh) ^ set(en)}"
    bad = {k: v for k, v in en.items() if CJK.search(v)}
    assert not bad, f"{page} 的英文文案里还留着中文：{bad}"


@pytest.mark.parametrize("page", SHELL_PAGES)
def test_shell_page_normalizes_locale_the_same_way(page: str):
    """
    与 `web/src/i18n/locale.ts` / `src-tauri/src/i18n.rs` 同一条规则：只看主
    子标签。三处任意一处走样，用户就会遇到「同一台机器上壳说英文、界面说中文」。
    """
    text = (TAURI / "shell" / page).read_text(encoding="utf-8")
    assert "primary === 'zh'" in text and "primary === 'en'" in text
    assert "q.get('lang')" in text, f"{page} 没有认壳带过来的 ?lang="
    assert "navigator.language" in text, f"{page} 没有系统语言这一档回退"


def test_error_page_shows_the_raw_message_untranslated():
    """报错原文（sidecar stderr / 平台错误码）要贴进 issue，翻过就对不上日志。"""
    text = (TAURI / "shell" / "error.html").read_text(encoding="utf-8")
    assert "document.getElementById('msg').textContent = msg || s.unknown" in text


# --------------------------------------------------------------------------- #
# 新命令的 ACL 三处同步
# --------------------------------------------------------------------------- #


def test_set_menu_locale_is_declared_in_all_three_places():
    build_rs = (TAURI / "build.rs").read_text(encoding="utf-8")
    main_rs = (TAURI / "src" / "main.rs").read_text(encoding="utf-8")
    cap = json.loads((TAURI / "capabilities" / "main.json").read_text(encoding="utf-8"))

    assert '"set_menu_locale"' in build_rs, "build.rs 的 AppManifest::commands 里没有它"
    assert "allow-set-menu-locale" in cap["permissions"], "capability 没放行它"
    assert "set_menu_locale" in main_rs.split("generate_handler![")[1].split("]")[0], (
        "generate_handler 里没有它"
    )


def _menu_builder() -> str:
    """`build_menu_in` 的函数体（菜单项 id 与加速键只该在这里出现）。"""
    main_rs = (TAURI / "src" / "main.rs").read_text(encoding="utf-8")
    start = main_rs.index("fn build_menu_in<R: tauri::Runtime>(")
    return main_rs[start : main_rs.index("\n}\n", start)]


# 自定义菜单项的加速键全集。每一条都是前端 `hooks/useKeyboard.ts` 本来就认的键
# （⌘, 除外：前端没有这个键，它只在菜单里），菜单转发与 keydown 的让位判断同一条
# （`web/src/hooks/menuActions.test.tsx` 逐键比）。**加一条就要回答它在输入框里
# 会不会被菜单劫持**——Delete / ? 这类不带修饰键的键因此一律不挂。
ACCELERATORS = {
    "CmdOrCtrl+Comma",
    "CmdOrCtrl+O",
    "CmdOrCtrl+S",
    "CmdOrCtrl+Shift+S",
    "CmdOrCtrl+E",
    "CmdOrCtrl+Z",
    "CmdOrCtrl+Shift+Z",
    "CmdOrCtrl+D",
    "CmdOrCtrl+Equal",
    "CmdOrCtrl+Minus",
    "CmdOrCtrl+0",
    "CmdOrCtrl+1",
}


def test_language_switch_does_not_touch_accelerators():
    """
    切语言只换显示文案：菜单项 id 与加速键必须只出现一次定义。
    改坏了的表现是「切成英文之后 ⌘Z 没反应」——用户不会往语言上联想。
    """
    body = _menu_builder()
    accels = re.findall(r'"(CmdOrCtrl\+[^"]+)"', body)
    assert set(accels) == ACCELERATORS, f"加速键集合变了：{set(accels) ^ ACCELERATORS}"
    for accel in ACCELERATORS:
        assert accels.count(accel) == 1, f"{accel} 被写了不止一次"
    ids = re.findall(r'"((?:menu|help)-[a-z-]+)"', body)
    assert ids, "没解析出菜单项 id——解析逻辑跟着源码走样了"
    for menu_id in set(ids):
        assert ids.count(menu_id) == 1, f"{menu_id} 被写了不止一次"
    # id 不许从 i18n 表里来（那样换语言就换了 id）
    assert "with_id(m." not in body and "menu_item(handle, m." not in body


def test_menu_ids_are_the_same_set_on_both_sides():
    """
    壳里转发的 `menu-*` 与前端 `MENU_ACTIONS` 严格同源：壳多一条 = 点了没反应，
    前端多一条 = 死分支。`help-*` 由壳自己开链接，不进前端。
    """
    rust = set(re.findall(r'"(menu-[a-z-]+)"', _menu_builder()))
    ts = (ROOT / "web" / "src" / "lib" / "desktop.ts").read_text(encoding="utf-8")
    block = ts[ts.index("export const MENU_ACTIONS = [") :]
    block = block[: block.index("] as const")]
    front = set(re.findall(r"'(menu-[a-z-]+)'", block))
    assert rust, "没解析出壳里的 menu-* id"
    assert rust == front, f"壳与前端的菜单 id 对不上：{rust ^ front}"


def test_shell_repo_url_mirrors_the_brand_constant():
    """
    壳在 webview 起来之前就建菜单，读不到前端常量，只能镜像一份 `REPO_URL`
    （帮助菜单的链接与「关于」的网站）。三处必须是同一个地址。
    """
    main_rs = (TAURI / "src" / "main.rs").read_text(encoding="utf-8")
    (rust,) = re.findall(r'^const REPO_URL: &str = "([^"]+)";', main_rs, re.M)
    brand_ts = (ROOT / "web" / "src" / "lib" / "brand.ts").read_text(encoding="utf-8")
    (ts,) = re.findall(r"^export const REPO_URL = '([^']+)'", brand_ts, re.M)
    from tavotto.engine import brand

    assert rust == ts == brand.REPO_URL
    # 壳里不许再有第二处手写的仓库地址
    assert main_rs.count("github.com/") == 1, "main.rs 里还有别处手写了仓库地址"
