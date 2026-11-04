"""`engine/ed25519.verify`：RFC 8032 官方向量 + 变异（畸形输入一律 False，不抛）。"""

from __future__ import annotations

import base64

import pytest

from journal_catalog_signing import TEST_SEED, public_key, sign
from tavotto.engine import ed25519

# RFC 8032 §7.1 TEST 1 / TEST 2
RFC = [
    (
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
]


@pytest.mark.parametrize("pub,msg,sig", RFC)
def test_rfc8032_vectors_verify(pub, msg, sig):
    assert ed25519.verify(bytes.fromhex(pub), bytes.fromhex(msg), bytes.fromhex(sig))


@pytest.mark.parametrize("pub,msg,sig", RFC)
def test_rfc8032_vectors_reject_mutations(pub, msg, sig):
    pub_b, msg_b, sig_b = bytes.fromhex(pub), bytes.fromhex(msg), bytes.fromhex(sig)
    assert not ed25519.verify(pub_b, msg_b + b"x", sig_b)
    for i in (0, 31, 32, 63):
        bad = bytearray(sig_b)
        bad[i] ^= 1
        assert not ed25519.verify(pub_b, msg_b, bytes(bad))


def test_known_answer_matches_independent_implementation():
    # 独立实现（pyca/cryptography）对同一测试种子给出的公钥与对 b"hello" 的签名
    pub = public_key(TEST_SEED)
    assert base64.b64encode(pub).decode() == "Amczm/R1l/iJuzZnwt8Qf3MzlRY2d2EO6qqR/XMOicI="
    expected = (
        "73cae7983eb833abe3c1c2897c1f5fcc92e88cb42861d1fd638e9bdbb0ecfb7b"
        "60d4244bbe12e0c1343379a013541907193b825515ea06f94fdd149c77b19304"
    )
    assert sign(TEST_SEED, b"hello").hex() == expected
    assert ed25519.verify(pub, b"hello", bytes.fromhex(expected))


def test_malformed_inputs_are_false_not_exceptions():
    pub = public_key(TEST_SEED)
    sig = sign(TEST_SEED, b"m")
    assert not ed25519.verify(pub[:31], b"m", sig)
    assert not ed25519.verify(pub, b"m", sig[:63])
    assert not ed25519.verify(pub, b"m", b"")
    assert not ed25519.verify(b"\xff" * 32, b"m", sig)  # y >= p
    assert not ed25519.verify(pub, b"m", sig[:32] + b"\xff" * 32)  # S >= L
    assert not ed25519.verify("x", b"m", sig)  # type: ignore[arg-type]


def test_malleable_signature_s_plus_l_is_rejected():
    pub = public_key(TEST_SEED)
    sig = sign(TEST_SEED, b"m")
    s = int.from_bytes(sig[32:], "little") + ed25519.ORDER
    assert s < 2**256
    assert not ed25519.verify(pub, b"m", sig[:32] + s.to_bytes(32, "little"))
