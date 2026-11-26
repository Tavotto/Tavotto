"""在线试用（网站 `/try`）是新用户的第一屏，中文界面不该漏出工程术语。

审计 T-008：试用页标题曾是「浏览器 playground」，报错里写「Python worker 崩溃了」，
来源说明写「编辑存成 Tavotto override，作用在 live figure 上」，展开报错的按钮叫
「Traceback」。这些词对第一次打开的用户没有意义。英文界面保持原样。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZH_DIALOGS = ROOT / "web" / "src" / "i18n" / "locales" / "zh-CN" / "dialogs.json"

BANNED = re.compile(r"playground|worker|traceback|override|live figure", re.IGNORECASE)

EXPECTED = {
    "title": "在线试用",
    "sourceNote": "修改单独保存，源脚本不会被改动。",
    "privacyNote": "Python 代码只在你的浏览器里运行，不会上传。",
    "errTimeout": "脚本运行超时，已停止。",
    "errWorkerCrashed": "Python 意外停止了。",
    "showTraceback": "报错详情",
}


def _playground() -> dict[str, str]:
    return json.loads(ZH_DIALOGS.read_text(encoding="utf-8"))["playground"]


def test_zh_playground_copy_has_no_engineering_terms() -> None:
    hits = {k: v for k, v in _playground().items() if isinstance(v, str) and BANNED.search(v)}
    assert hits == {}, f"在线试用的中文文案里有工程术语：{hits}"


def test_zh_playground_key_strings_are_pinned() -> None:
    pg = _playground()
    assert {k: pg[k] for k in EXPECTED} == EXPECTED
