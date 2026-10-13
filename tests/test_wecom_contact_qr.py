"""企业微信联系二维码：仓库里那张 PNG 必须是维护者的名片码（防止以后被换成别的码）。

`web/src/lib/contact.ts` 的 `WECOM_CARD_URL` 是唯一常量。主断言钉 PNG 的 sha256（CI 的 venv 没有
`cv2`，只靠解码会变空门禁）：这张图解码为 `CARD_URL`；**有意换图时**先用 cv2 复核新图——

    cv2.QRCodeDetector().detectAndDecode(cv2.imread(path))[0] == CARD_URL

再同时更新 `QR_SHA256`。有 `cv2` 的环境额外跑一遍解码（可选）。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
QR = ROOT / "web" / "src" / "assets" / "wecom-contact-qr.png"
CONTACT_TS = ROOT / "web" / "src" / "lib" / "contact.ts"
QR_SHA256 = "bb1ab8e01c6536e774189a92ace12ba133cc902216c94b06a36478340eee188b"
CARD_URL = "https://work.weixin.qq.com/u/vc56c00d0f7cb8eae0?src=wx&bb=9c371d3ab1"


def test_card_constant_is_the_maintainer_card():
    m = re.search(r"WECOM_CARD_URL\s*=\s*'([^']+)'", CONTACT_TS.read_text(encoding="utf-8"))
    assert m and m.group(1) == CARD_URL


def test_qr_png_is_the_pinned_maintainer_card():
    assert hashlib.sha256(QR.read_bytes()).hexdigest() == QR_SHA256


def test_qr_png_is_small():
    assert QR.stat().st_size < 20 * 1024


def test_qr_png_decodes_to_the_maintainer_card():
    cv2 = pytest.importorskip("cv2", reason="可选：有 opencv 才解码；主门禁是上面的 sha256")
    img = cv2.imread(str(QR))
    assert img is not None
    text, _pts, _ = cv2.QRCodeDetector().detectAndDecode(img)
    assert text == CARD_URL
