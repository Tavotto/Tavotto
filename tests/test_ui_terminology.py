"""界面名词：项目 / 排版 / 画布 / 项目包，界面上不再出现「文档」（ADR 0001 2026-09-26 修订）。

用户拍板的四个名词，且只有这四个：

* **项目** / Project —— 用户的脚本文件夹；
* **排版** / Layout —— schema 3 的一份 JSON，可以包含多张画布（原来界面上叫「文档」
  「画布文件」「项目文档」的那样东西）；
* **画布** / Canvas —— 排版里的一张图（标签页）；
* **项目包** / Project package —— 导出的 `.tavotto` 单文件。

ADR 0001 早在 2026-08-15 就规定界面不再出现「文档」，后来的文案又一条条用了回来——
没有任何东西在看。这里是那个「在看的东西」。

**判据的主语**：用户能看见的文案，三处来源——

* 前端两种语言的全部语言包（八个命名空间、每一个叶子值，含命令面板的搜索关键词）；
* 后端原样交给界面显示的标签：目录选择器的常用起点（`app._browse_shortcuts` 的 `name`，
  老前端直接显示它；新前端按 `id` 翻译，`id` 闭集与前端 `ShortcutId` 在这里对拍）；
* 桌面壳自己那份（`src-tauri/src/i18n.rs`，原生菜单文案不经语言包）——**只在
  `tests/test_desktop_i18n.py` 还没有自己的名词用例（`test_menu_uses_the_four_ui_nouns`，
  #663 带来）时由这里扫**。那条用例一出现，壳内就归它，这里让位，免得同一条判据有两个
  出处；它不在的时候这里不让位，免得壳内在两者之间无人看管（#668 评审）。

**不看** key 名（改 key 代价大、与界面无关）、代码注释与开发者文档（「文档」在那里指代码概念）。

**豁免按条枚举**，不按前缀、不按正则：每一条是一个具体位置 + 理由，而且必须**仍然
命中**——位置没了、或者那句话里已经没有这个词，豁免就过期了，用例跟着红，逼着删掉它
（一条永远不会被用到的豁免，是下一次回潮的藏身处）。「使用文档 / Documentation」这种
指帮助手册的，是豁免的唯一正当理由。英文 Documentation 本身不会被 `\\bdocuments?\\b`
命中，不需要豁免。
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"
I18N_RS = ROOT / "src-tauri" / "src" / "i18n.rs"
DESKTOP_I18N_TEST = ROOT / "tests" / "test_desktop_i18n.py"
SHELL_NOUN_TEST = "test_menu_uses_the_four_ui_nouns"
API_TS = ROOT / "web" / "src" / "lib" / "api.ts"

#: 被禁的词。英文按整词（document / documents，大小写不论）：`docs`、`Documentation`
#: 都不是这个概念，也不会被它咬到。
BANNED = re.compile(r"文档|\bdocuments?\b", re.IGNORECASE)

#: (来源, 位置) → 理由。来源是 `web:<语言>` / `backend:<函数>` / `shell:<表名>`；位置是
#: `命名空间:点分 key` / 常用起点的 id / i18n.rs 字段名。**只收不是「排版」这个概念的**：
#: 帮助手册 / 官方说明，以及操作系统自己的文件夹名。
EXEMPT: dict[tuple[str, str], str] = {
    (
        "web:zh-CN",
        "errors:backend.ai_agent_install_unsupported",
    ): "「官方文档」= 那个 Agent 自己的使用说明",
    (
        "web:en-US",
        "project:browser.shortcut.documents",
    ): "系统文件夹名 Documents（目录选择器的常用起点）",
}


def _flatten(node: object, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for k, v in node.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(node, str):
        out[prefix] = node
    return out


def _web_strings() -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for locale_dir in sorted(p for p in LOCALES.iterdir() if p.is_dir()):
        for f in sorted(locale_dir.glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            for key, value in _flatten(data).items():
                found[(f"web:{locale_dir.name}", f"{f.stem}:{key}")] = value
    return found


def _backend_strings() -> dict[tuple[str, str], str]:
    """`_browse_shortcuts()` 在一个三个文件夹都在的假主目录下实际发出的 `name`。

    主语是**这个函数此刻会发什么**，不是源码里某个常量：所以真的调用它，并把主目录
    指到一个临时目录（三个文件夹都建好，一个分支都不漏）。"""
    import os
    import tempfile

    from tavotto import app as tavotto_app

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        for _sid, _label, folder in tavotto_app.BROWSE_SHORTCUTS:
            (home / folder).mkdir()
        saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
        os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)
        try:
            entries = tavotto_app._browse_shortcuts()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    return {("backend:_browse_shortcuts", e["id"]): e["name"] for e in entries}


def _shell_guarded_elsewhere() -> bool:
    """`test_desktop_i18n.py` 里有没有壳内菜单自己的名词用例（按 AST 找函数定义，不按子串）。"""
    if not DESKTOP_I18N_TEST.is_file():
        return False
    tree = ast.parse(DESKTOP_I18N_TEST.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.FunctionDef) and node.name == SHELL_NOUN_TEST
        for node in ast.walk(tree)
    )


def _shell_strings() -> dict[tuple[str, str], str]:
    """`const ZH/EN: ShellText = ShellText { 字段: "……", };` 的每一个字段值。"""
    if not I18N_RS.is_file() or _shell_guarded_elsewhere():  # wheel / sdist 不含桌面壳
        return {}
    src = I18N_RS.read_text(encoding="utf-8")
    found: dict[tuple[str, str], str] = {}
    for table in ("ZH", "EN"):
        start = src.index(f"const {table}: ShellText = ShellText {{")
        body = src[start : src.index("\n};", start)]
        for m in re.finditer(r"^\s{4}(\w+):\s*\n?\s*\"((?:[^\"\\]|\\.)*)\"", body, re.M):
            found[(f"shell:{table}", m.group(1))] = m.group(2)
    return found


def _all_strings() -> dict[tuple[str, str], str]:
    return {**_web_strings(), **_backend_strings(), **_shell_strings()}


def test_the_scan_sees_what_it_claims_to():
    """先证明尺子是活的：两种语言、八个命名空间都读到了。"""
    strings = _all_strings()
    sources = {src for src, _ in strings}
    assert {"web:zh-CN", "web:en-US"} <= sources
    namespaces = {loc.split(":", 1)[0] for src, loc in strings if src == "web:zh-CN"}
    assert len(namespaces) >= 8, f"只读到这些命名空间：{sorted(namespaces)}"
    # 已知在语言包里的一条，确认拍平与定位的写法对
    assert strings[("web:zh-CN", "workspace:topbar.saveDocumentAs")]
    # 后端常用起点：主目录 + 三个文件夹一个不少（少了就是有分支没跑到）
    backend = {loc for src, loc in strings if src == "backend:_browse_shortcuts"}
    assert backend == {"home", "desktop", "documents", "downloads"}, backend


def test_shell_menu_is_guarded_somewhere():
    """壳内菜单文案要么这里在扫，要么 `test_desktop_i18n.py` 有自己的名词用例——不能两头都不管。"""
    if not I18N_RS.is_file():
        pytest.skip("没有 src-tauri/（wheel/sdist 里不含桌面壳）")
    scanned_here = {src for src, _ in _shell_strings()} == {"shell:ZH", "shell:EN"}
    assert scanned_here or _shell_guarded_elsewhere()
    if scanned_here:
        assert _shell_strings()[("shell:EN", "edit_undo")]  # 解析没有漏字段


def test_shortcut_ids_match_the_frontend_closed_set():
    """后端常用起点的 `id` 与前端 `ShortcutId` 联合类型、两种语言的翻译 key 三方逐字相等。"""
    backend = {loc for src, loc in _backend_strings()}
    src = API_TS.read_text(encoding="utf-8")
    m = re.search(r"export type ShortcutId = ([^\n]+)", src)
    assert m, "api.ts 里找不到 ShortcutId"
    frontend = set(re.findall(r"'(\w+)'", m.group(1)))
    assert backend == frontend, f"后端 {sorted(backend)} ≠ 前端 {sorted(frontend)}"
    for locale in ("zh-CN", "en-US"):
        data = json.loads((LOCALES / locale / "project.json").read_text(encoding="utf-8"))
        assert set(data["browser"]["shortcut"]) == backend, locale


def test_no_document_wording_in_user_visible_text():
    strings = _all_strings()
    hits = {
        f"{src} {loc}": text
        for (src, loc), text in strings.items()
        if BANNED.search(text) and (src, loc) not in EXEMPT
    }
    assert not hits, (
        "界面上又出现了「文档 / document」（名词只有 项目 / 排版 / 画布 / 项目包，"
        f"ADR 0001 2026-09-26 修订）：{json.dumps(hits, ensure_ascii=False, indent=1)}"
    )


@pytest.mark.parametrize("where", sorted(EXEMPT), ids=lambda w: f"{w[0]} {w[1]}")
def test_every_exemption_is_still_live(where: tuple[str, str]):
    """豁免的位置必须还在、而且那句话里确实还有这个词；否则就是过期豁免，删掉它。"""
    strings = _all_strings()
    assert where in strings, f"豁免指向的位置已经不存在：{where}"
    assert BANNED.search(strings[where]), f"豁免的那句话里已经没有「文档」了，删掉这条豁免：{where}"
