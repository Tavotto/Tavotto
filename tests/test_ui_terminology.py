"""界面名词：项目 / 排版 / 画布 / 项目包，界面上不再出现「文档」（ADR 0001 2026-09-26 修订）。

用户拍板的四个名词，且只有这四个：

* **项目** / Project —— 用户的脚本文件夹；
* **排版** / Layout —— schema 3 的一份 JSON，可以包含多张画布（原来界面上叫「文档」
  「画布文件」「项目文档」的那样东西）；
* **画布** / Canvas —— 排版里的一张图（标签页）；
* **项目包** / Project package —— 导出的 `.tavotto` 单文件。

ADR 0001 早在 2026-08-15 就规定界面不再出现「文档」，后来的文案又一条条用了回来——
没有任何东西在看。这里是那个「在看的东西」。

**判据的主语**：用户能看见的文案——前端两种语言的全部语言包（八个命名空间、每一个
叶子值，含命令面板的搜索关键词），加上桌面壳自己那份（`src-tauri/src/i18n.rs` 的
ZH / EN 两张表：原生菜单在 webview 起来之前就要建，文案不经语言包）。**不看** key 名
（改 key 代价大、与界面无关）、代码注释与开发者文档（「文档」在那里指代码概念）。

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

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"
I18N_RS = ROOT / "src-tauri" / "src" / "i18n.rs"

#: 被禁的词。英文按整词（document / documents，大小写不论）：`docs`、`Documentation`
#: 都不是这个概念，也不会被它咬到。
BANNED = re.compile(r"文档|\bdocuments?\b", re.IGNORECASE)

#: (来源, 位置) → 理由。来源是 `web:<语言>` 或 `shell:<表名>`；位置是 `命名空间:点分 key`
#: 或 i18n.rs 的字段名。**只收指帮助手册 / 官方说明的「文档」**。
EXEMPT: dict[tuple[str, str], str] = {
    (
        "web:zh-CN",
        "errors:backend.ai_agent_install_unsupported",
    ): "「官方文档」= 那个 Agent 自己的使用说明",
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


def _shell_strings() -> dict[tuple[str, str], str]:
    """`const ZH: ShellText = ShellText { 字段: "……", };` 两张表里的每一个字段值。"""
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
    return {**_web_strings(), **_shell_strings()}


def test_the_scan_sees_what_it_claims_to():
    """先证明尺子是活的：两种语言、八个命名空间都读到了，壳内两张表也读到了。"""
    strings = _all_strings()
    sources = {src for src, _ in strings}
    assert {"web:zh-CN", "web:en-US"} <= sources
    namespaces = {loc.split(":", 1)[0] for src, loc in strings if src == "web:zh-CN"}
    assert len(namespaces) >= 8, f"只读到这些命名空间：{sorted(namespaces)}"
    if I18N_RS.is_file():
        assert {"shell:ZH", "shell:EN"} <= sources
        # 已知在表里的一条，确认解析没有漏字段
        assert strings[("shell:EN", "edit_undo")]
    # 已知在语言包里的一条，确认拍平与定位的写法对
    assert strings[("web:zh-CN", "workspace:topbar.saveDocumentAs")]


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
