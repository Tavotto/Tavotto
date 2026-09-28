"""测试用的一次性私有 CA + 回环服务器证书：**纯标准库**现造（RSA-2048 / SHA-256 / X.509 v3 DER）。

为什么不提交静态 PEM：macOS 的 SecTrust（truststore 在 macOS 上走它）拒绝有效期超过 825 天的服务器证书，
提交进仓库的证书要么过期、要么过长——用例会在某一天无声地变红。为什么不用 trustme / cryptography：
那会给整个 dev 环境添一个原生依赖，只为了这一个装置。这里每个会话现造一次（`lru_cache`），有效期
「昨天 → 30 天后」，与测试同寿。

造出来的两张证书：

* 根：`CN=Tavotto Test Root CA`，basicConstraints cA=TRUE（critical）、keyUsage keyCertSign|cRLSign、SKI；
* 服务器：`CN=127.0.0.1`，SAN = IP 127.0.0.1（**没有** `localhost`——主机名不符的用例就靠这一点）、
  EKU serverAuth、keyUsage digitalSignature|keyEncipherment、AKI 指向根。

只给用例当装置：密钥在 `secrets` 的随机数上现生成，从不落在仓库里。
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as _dt
import functools
import hashlib
import secrets
from pathlib import Path

# ---------------------------------------------------------------- RSA（教科书式，够给装置用）
_SMALL_PRIMES = [p for p in range(3, 2000, 2) if all(p % d for d in range(3, int(p**0.5) + 1, 2))]


def _is_probable_prime(n: int, rounds: int = 40) -> bool:
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits: int, e: int) -> int:
    while True:
        cand = secrets.randbits(bits) | (0b11 << (bits - 2)) | 1  # 顶两位置 1：p·q 恰好 2·bits 位
        if _is_probable_prime(cand) and (cand - 1) % e != 0:
            return cand


@dataclasses.dataclass(frozen=True)
class _RSAKey:
    n: int
    e: int
    d: int
    p: int
    q: int

    @classmethod
    def generate(cls, bits: int = 2048) -> _RSAKey:
        e = 65537
        while True:
            p, q = _prime(bits // 2, e), _prime(bits // 2, e)
            if p != q and (p * q).bit_length() == bits:
                break
        d = pow(e, -1, (p - 1) * (q - 1))
        return cls(n=p * q, e=e, d=d, p=p, q=q)

    def sign_sha256(self, data: bytes) -> bytes:
        """PKCS#1 v1.5 签名（RFC 8017 §8.2）。"""
        k = (self.n.bit_length() + 7) // 8
        digest_info = _seq(
            _seq(_oid("2.16.840.1.101.3.4.2.1"), _NULL), _octets(hashlib.sha256(data).digest())
        )
        em = b"\x00\x01" + b"\xff" * (k - len(digest_info) - 3) + b"\x00" + digest_info
        return pow(int.from_bytes(em, "big"), self.d, self.n).to_bytes(k, "big")

    def public_der(self) -> bytes:
        return _seq(_int(self.n), _int(self.e))

    def private_pkcs1_der(self) -> bytes:
        return _seq(
            _int(0),
            _int(self.n),
            _int(self.e),
            _int(self.d),
            _int(self.p),
            _int(self.q),
            _int(self.d % (self.p - 1)),
            _int(self.d % (self.q - 1)),
            _int(pow(self.q, -1, self.p)),
        )


# ---------------------------------------------------------------- DER
def _tlv(tag: int, body: bytes) -> bytes:
    n = len(body)
    if n < 0x80:
        length = bytes([n])
    else:
        raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
        length = bytes([0x80 | len(raw)]) + raw
    return bytes([tag]) + length + body


def _seq(*items: bytes) -> bytes:
    return _tlv(0x30, b"".join(items))


def _set(*items: bytes) -> bytes:
    return _tlv(0x31, b"".join(items))


def _int(v: int) -> bytes:
    # 多留一字节：最高位为 1 时补 0，保持正数；多余的前导 0 在下面剥掉
    raw = v.to_bytes(max(1, (v.bit_length() + 8) // 8), "big")
    while len(raw) > 1 and raw[0] == 0 and raw[1] < 0x80:
        raw = raw[1:]
    return _tlv(0x02, raw)


def _oid(dotted: str) -> bytes:
    parts = [int(x) for x in dotted.split(".")]
    body = bytes([40 * parts[0] + parts[1]])
    for v in parts[2:]:
        chunk = [v & 0x7F]
        v >>= 7
        while v:
            chunk.append(0x80 | (v & 0x7F))
            v >>= 7
        body += bytes(reversed(chunk))
    return _tlv(0x06, body)


_NULL = b"\x05\x00"
_TRUE = b"\x01\x01\xff"


def _octets(b: bytes) -> bytes:
    return _tlv(0x04, b)


def _bits(b: bytes, unused: int = 0) -> bytes:
    return _tlv(0x03, bytes([unused]) + b)


def _utf8(s: str) -> bytes:
    return _tlv(0x0C, s.encode("utf-8"))


def _time(t: _dt.datetime) -> bytes:
    if t.year < 2050:
        return _tlv(0x17, t.strftime("%y%m%d%H%M%SZ").encode("ascii"))
    return _tlv(0x18, t.strftime("%Y%m%d%H%M%SZ").encode("ascii"))


def _explicit(n: int, body: bytes) -> bytes:
    return _tlv(0xA0 | n, body)


def _name(cn: str) -> bytes:
    return _seq(
        _set(_seq(_oid("2.5.4.10"), _utf8("Tavotto tests"))),
        _set(_seq(_oid("2.5.4.3"), _utf8(cn))),
    )


def _ext(oid: str, value: bytes, *, critical: bool = False) -> bytes:
    return _seq(_oid(oid), *([_TRUE] if critical else []), _octets(value))


_SHA256_RSA = _seq(_oid("1.2.840.113549.1.1.11"), _NULL)


def _spki(key: _RSAKey) -> bytes:
    return _seq(_seq(_oid("1.2.840.113549.1.1.1"), _NULL), _bits(key.public_der()))


def _key_id(key: _RSAKey) -> bytes:
    return hashlib.sha1(key.public_der()).digest()


def _certificate(
    *,
    subject: str,
    issuer: str,
    key: _RSAKey,
    signer: _RSAKey,
    extensions: list[bytes],
    not_before: _dt.datetime,
    not_after: _dt.datetime,
) -> bytes:
    tbs = _seq(
        _explicit(0, _int(2)),  # v3
        _int(int.from_bytes(secrets.token_bytes(16), "big") >> 1 or 1),
        _SHA256_RSA,
        _name(issuer),
        _seq(_time(not_before), _time(not_after)),
        _name(subject),
        _spki(key),
        _explicit(3, _seq(*extensions)),
    )
    return _seq(tbs, _SHA256_RSA, _bits(signer.sign_sha256(tbs)))


def _pem(label: str, der: bytes) -> str:
    b64 = base64.b64encode(der).decode("ascii")
    lines = [b64[i : i + 64] for i in range(0, len(b64), 64)]
    return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n"


# ---------------------------------------------------------------- 对外
@dataclasses.dataclass(frozen=True)
class TestPKI:
    """`ca_pem`：根证书（给客户端当信任锚）；`server_cert_pem` / `server_key_pem`：回环服务器用。"""

    ca_pem: str
    server_cert_pem: str
    server_key_pem: str

    def write(self, directory: Path) -> dict[str, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        out = {
            "ca": directory / "ca.pem",
            "cert": directory / "server.pem",
            "key": directory / "server.key",
        }
        out["ca"].write_text(self.ca_pem, encoding="ascii")
        out["cert"].write_text(self.server_cert_pem, encoding="ascii")
        out["key"].write_text(self.server_key_pem, encoding="ascii")
        return out


TestPKI.__test__ = False  # 名字以 Test 开头：别让 pytest 当成用例类收集


@functools.lru_cache(maxsize=1)
def make_pki() -> TestPKI:
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    not_before = now - _dt.timedelta(days=1)
    not_after = now + _dt.timedelta(days=30)  # < 825 天：macOS SecTrust 的服务器证书上限
    ca_key = _RSAKey.generate()
    leaf_key = _RSAKey.generate()
    ca_name = "Tavotto Test Root CA"
    ca = _certificate(
        subject=ca_name,
        issuer=ca_name,
        key=ca_key,
        signer=ca_key,
        not_before=not_before,
        not_after=not_after + _dt.timedelta(days=1),
        extensions=[
            _ext("2.5.29.19", _seq(_TRUE), critical=True),  # basicConstraints cA=TRUE
            _ext("2.5.29.15", _bits(b"\x06", 1), critical=True),  # keyCertSign | cRLSign
            _ext("2.5.29.14", _octets(_key_id(ca_key))),  # subjectKeyIdentifier
        ],
    )
    leaf = _certificate(
        subject="127.0.0.1",
        issuer=ca_name,
        key=leaf_key,
        signer=ca_key,
        not_before=not_before,
        not_after=not_after,
        extensions=[
            _ext("2.5.29.19", _seq(), critical=True),  # basicConstraints cA=FALSE
            # keyUsage: digitalSignature | keyEncipherment
            _ext("2.5.29.15", _bits(b"\xa0", 5), critical=True),
            _ext("2.5.29.37", _seq(_oid("1.3.6.1.5.5.7.3.1"))),  # EKU serverAuth
            _ext("2.5.29.17", _seq(_tlv(0x87, bytes([127, 0, 0, 1])))),  # SAN: IP 127.0.0.1
            _ext("2.5.29.14", _octets(_key_id(leaf_key))),
            _ext("2.5.29.35", _seq(_tlv(0x80, _key_id(ca_key)))),  # authorityKeyIdentifier
        ],
    )
    return TestPKI(
        ca_pem=_pem("CERTIFICATE", ca),
        server_cert_pem=_pem("CERTIFICATE", leaf),
        server_key_pem=_pem("RSA PRIVATE KEY", leaf_key.private_pkcs1_der()),
    )
