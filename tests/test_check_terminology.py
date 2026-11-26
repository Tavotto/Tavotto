"""投稿前检查的名字与严重程度只有一套说法（T-007）。

同一个功能曾经叫「预检」「规范检查」「出版规范预检」「样式检查报告」，严重程度在导出对话框、问题面板、
Codex 内嵌画布里是三套词（「项要改 / 项留意」「阻断 / 警告」「阻断 · 警告 · 待核验」）。定下来的是：

* 功能名：**投稿前检查** / Pre-submission check；规则集仍叫「出版规范」；它写出的报告是「投稿前检查报告」
  （磁盘上的文件后缀 `_style-check.json` 是契约，不改）；
* 严重程度：**要改 / 需留意 / 建议 / 无法核验**；英文 to fix / to review / suggestion / not verifiable
  （与 README 同一套）。

这里扫两种语言的全部语言包（只看值，不看 key 名），再钉住三处严重程度的具体文案，防止各处又各自演进。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"

BANNED = {
    "zh-CN": re.compile(r"预检|样式检查报告|项留意|规范检查报告"),
    "en-US": re.compile(
        r"\bpre-?flight\b|style[- ]check report|pre-export check|\bspec check\b", re.IGNORECASE
    ),
}


def _flatten(node: object, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for k, v in node.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(node, str):
        out[prefix] = node
    return out


def _values(lang: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in sorted((LOCALES / lang).glob("*.json")):
        for k, v in _flatten(json.loads(f.read_text(encoding="utf-8"))).items():
            out[f"{f.stem}:{k}"] = v
    return out


@pytest.mark.parametrize("lang", sorted(BANNED))
def test_the_check_has_one_name(lang):
    hits = {k: v for k, v in _values(lang).items() if BANNED[lang].search(v)}
    assert not hits, hits


EXPECTED = {
    "zh-CN": {
        "dialogs:export.chipError_other": "项要改",
        "dialogs:export.chipWarn_other": "项需留意",
        "dialogs:export.chipSuggestion_other": "条建议",
        "dialogs:export.chipNotVerifiable_other": "项无法核验",
        "errors:problems.severity.error": "要改",
        "errors:problems.severity.warn": "需留意",
        "errors:problems.severity.suggestion": "建议",
        "errors:problems.severity.notVerifiable": "无法核验",
        "dialogs:mcp.pillError_other": "{{count}} 项要改",
        "dialogs:mcp.pillWarn_other": "{{count}} 项需留意",
        "dialogs:mcp.pillNotVerifiable_other": "{{count}} 项无法核验",
        "dialogs:mcp.issuesTitle": "投稿前检查",
    },
    "en-US": {
        "dialogs:export.chipError_other": "to fix",
        "dialogs:export.chipWarn_other": "to review",
        "dialogs:export.chipSuggestion_other": "suggestions",
        "dialogs:export.chipNotVerifiable_other": "not verifiable",
        "errors:problems.severity.error": "To fix",
        "errors:problems.severity.warn": "To review",
        "errors:problems.severity.suggestion": "Suggestion",
        "errors:problems.severity.notVerifiable": "Not verifiable",
        "dialogs:mcp.pillError_other": "{{count}} to fix",
        "dialogs:mcp.pillWarn_other": "{{count}} to review",
        "dialogs:mcp.pillNotVerifiable_other": "{{count}} not verifiable",
        "dialogs:mcp.issuesTitle": "Pre-submission check",
    },
}


@pytest.mark.parametrize("lang", sorted(EXPECTED))
def test_severity_words_match_across_dialog_panel_and_canvas(lang):
    values = _values(lang)
    got = {k: values.get(k) for k in EXPECTED[lang]}
    assert got == EXPECTED[lang]
