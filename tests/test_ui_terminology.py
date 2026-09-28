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
* 桌面壳自己那份（`src-tauri/src/i18n.rs`，原生菜单文案不经语言包）——**始终在这里扫**，
  用同一个 `BANNED`。`tests/test_desktop_i18n.py` 的 `test_menu_uses_the_four_ui_nouns` 是
  菜单侧的用例，但这里不因为它存在就让位：让位的前提是它的禁用词集合与这里相同，而它
  曾经只认单数 document，「Recent Documents」两边都放行（#668 评审）。禁用词集合只有
  `BANNED` 这一个出处。

**不看** key 名（改 key 代价大、与界面无关）、代码注释与开发者文档（「文档」在那里指代码概念）。

**豁免按条枚举**，不按前缀、不按正则：每一条是一个具体位置 + 理由，而且必须**仍然
命中**——位置没了、或者那句话里已经没有这个词，豁免就过期了，用例跟着红，逼着删掉它
（一条永远不会被用到的豁免，是下一次回潮的藏身处）。「使用文档 / Documentation」这种
指帮助手册的，是豁免的唯一正当理由。英文 Documentation 本身不会被 `\\bdocuments?\\b`
命中，不需要豁免。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.support.tsconst import exported_string_union, function_string_switch

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"
I18N_RS = ROOT / "src-tauri" / "src" / "i18n.rs"
API_TS = ROOT / "web" / "src" / "lib" / "api.ts"
PROJECT_PICKER_TSX = ROOT / "web" / "src" / "components" / "ProjectPicker.tsx"

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
    ("shell:ZH", "help_docs"): "「使用文档」= 帮助手册（菜单「帮助」里那一项）",
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


def _shell_strings() -> dict[tuple[str, str], str]:
    """`const ZH/EN: ShellText = ShellText { 字段: "……", };` 的每一个字段值。"""
    if not I18N_RS.is_file():  # wheel / sdist 不含桌面壳
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
    # 壳内菜单：两张表都读到了、字段没漏（有 src-tauri/ 的树上）
    if I18N_RS.is_file():
        assert {src for src, _ in strings} >= {"shell:ZH", "shell:EN"}
        assert strings[("shell:EN", "edit_undo")]


def test_shortcut_ids_match_the_frontend_closed_set():
    """严格同源对（`docs/rules/repo/same-origin-pairs.md`）：后端常用起点实际发出的 `id`、
    前端 `ShortcutId` 联合类型、`shortcutLabel` 逐个翻译的 `case`、两种语言的翻译 key，
    四方逐字相等。少了 `case` 的那个 id 会静默回退成后端写死的中文名。"""
    backend = {loc for src, loc in _backend_strings()}
    frontend = exported_string_union(API_TS.read_text(encoding="utf-8"), "ShortcutId")
    assert set(frontend) == backend, f"后端 {sorted(backend)} ≠ 前端 {frontend}"
    discriminant, clauses = function_string_switch(
        PROJECT_PICKER_TSX.read_text(encoding="utf-8"), "shortcutLabel"
    )
    assert discriminant == "entry.id", discriminant
    cases = {}
    for label, code, lits in clauses:
        if label == "":
            continue  # default：认不出的 id / 老后端回退 name
        # 每个 case 恰好是 `return translate('browser.shortcut.<同一个 id>', { ns: 'project' })`
        assert re.fullmatch(r"\s*return\s+translate\(\s+,\s*\{\s*ns:\s+\}\s*\)\s*;?\s*", code), (
            f"case {label!r} 的分支不是一句 translate：{code!r}"
        )
        assert lits == [f"browser.shortcut.{label}", "project"], (label, lits)
        cases[label] = lits[0]
    assert set(cases) == backend, f"shortcutLabel 的 case {sorted(cases)}"
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
