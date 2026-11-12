"""画布文字「用原图字体」（ADR 0164）的同源对：几处各持一份的闭集 / 常量逐项相同。

主语是**源码文本里的字面值**（两个进程、两种语言各持一份，运行时没有谁能 import 另一边）：
worker 侧 `manifest.py` 不能 import `rendercore`，前端不能 import Python，所以只能读文本对账。
"""

from __future__ import annotations

import re
from pathlib import Path

from tavotto.engine import preflight
from tavotto.rendercore import sourcefont, userfonts

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_reasons_match_the_frontend_closed_set():
    ts = _read("web/src/lib/sourceFont.ts")
    body = re.search(r"SOURCE_FONT_REASONS = \[(.*?)\] as const", ts, re.S).group(1)
    assert tuple(re.findall(r"'([a-z_]+)'", body)) == sourcefont.REASONS


def test_worker_side_size_cap_equals_the_flask_side_cap():
    """worker 侧算 sha256 的上限（manifest）与 Flask 侧读取上限（userfonts）是两个进程各持一份。"""
    m = re.search(
        r"FONT_SOURCE_MAX_BYTES = (\d+) \* 1024 \* 1024", _read("src/tavotto/engine/manifest.py")
    )
    assert int(m.group(1)) * 1024 * 1024 == userfonts.MAX_FONT_BYTES


def test_preflight_message_keys_agree_between_the_two_evaluators():
    ts = _read("web/src/lib/preflight.ts")
    body = re.search(r"SOURCE_FONT_MESSAGES: Record<string, string> = \{(.*?)\n\}", ts, re.S).group(
        1
    )
    ts_map = dict(re.findall(r"(\w+): '(\w+)'", body))
    assert ts_map == preflight._SOURCE_FONT_MESSAGES
    # 每个后端原因都有文案；前端自己的 unsupported 也有
    assert set(sourcefont.REASONS) | {"unsupported"} == set(ts_map)


def test_every_preflight_message_key_exists_in_both_locales():
    import json

    for loc in ("zh-CN", "en-US"):
        errors = json.loads(_read(f"web/src/i18n/locales/{loc}/errors.json"))
        keys = errors["preflight"] if "preflight" in errors else errors
        for key in preflight._SOURCE_FONT_MESSAGES.values():
            assert key in keys, (loc, key)


def test_export_payload_forwards_source_font_and_nothing_else_about_it():
    """载荷里是 `source_font`（身份），没有路径 / 字节的字段。"""
    ts = _read("web/src/lib/exportPayload.ts")
    assert "source_font: o.sourceFont" in ts
    assert not re.search(r"source_font_?(path|file|bytes)", ts)
