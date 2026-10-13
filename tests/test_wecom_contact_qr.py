"""企业微信联系二维码：仓库里那张 PNG 必须解码为维护者的名片地址（防止以后被换成别的码）。

`web/src/lib/contact.ts` 的 `WECOM_CARD_URL` 是唯一常量；没有 `cv2` 的环境跳过
（本机系统 Python 有 opencv，CI 的 venv 没有就不检查，不为它加依赖）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
QR = ROOT / "web" / "src" / "assets" / "wecom-contact-qr.png"
CONTACT_TS = ROOT / "web" / "src" / "lib" / "contact.ts"
CARD_URL = "https://work.weixin.qq.com/u/vc56c00d0f7cb8eae0?src=wx&bb=9c371d3ab1"


def test_card_constant_is_the_maintainer_card():
    m = re.search(r"WECOM_CARD_URL\s*=\s*'([^']+)'", CONTACT_TS.read_text(encoding="utf-8"))
    assert m and m.group(1) == CARD_URL


def test_qr_png_is_small():
    assert QR.stat().st_size < 20 * 1024


def test_qr_png_decodes_to_the_maintainer_card():
    cv2 = pytest.importorskip("cv2", reason="需要 opencv 解码二维码；没有就不检查")
    img = cv2.imread(str(QR))
    assert img is not None
    text, _pts, _ = cv2.QRCodeDetector().detectAndDecode(img)
    assert text == CARD_URL
