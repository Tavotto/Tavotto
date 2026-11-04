"""Ed25519 **验签**（RFC 8032 §5.1.7，纯标准库；Flask 父进程 import）。

为什么自己写：Flask 父进程的依赖闭包刻意很窄（`src/tavotto/AGENTS.md`「进程边界」），不为一次几十 KB 清单的
验签引入 `cryptography`（原生轮子、PyInstaller 额外负担）。验签只在拉取期刊目录清单时跑一次（毫秒级），
**这里没有签名函数**：私钥不进客户端；测试夹具用的签名函数只在 `tests/` 里。

严格性：长度必须正好（公钥 32、签名 64）；`S < L`（拒绝可塑性签名）；`R` 与 `A` 必须是曲线上的规范编码点
（`y < p`、`x = 0` 且符号位为 1 的编码拒绝）；验的是未乘余因子的方程 `S·B == R + h·A`（与 OpenSSL / libsodium 一致）。
看护：`tests/test_ed25519.py`（RFC 8032 官方向量 + 变异）。
"""

from __future__ import annotations

import hashlib

_P = 2**255 - 19
_Q = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)

Point = tuple[int, int, int, int]


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def point_add(a: Point, b: Point) -> Point:
    pa = (a[1] - a[0]) * (b[1] - b[0]) % _P
    pb = (a[1] + a[0]) * (b[1] + b[0]) % _P
    pc = 2 * a[3] * b[3] * _D % _P
    pd = 2 * a[2] * b[2] % _P
    e, f, g, h = pb - pa, pd - pc, pd + pc, pb + pa
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def point_mul(s: int, p: Point) -> Point:
    q: Point = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = point_add(q, p)
        p = point_add(p, p)
        s >>= 1
    return q


def point_equal(a: Point, b: Point) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
assert _GX is not None
BASE: Point = (_GX, _GY, 1, _GX * _GY % _P)
ORDER = _Q


def point_compress(p: Point) -> bytes:
    zinv = _inv(p[2])
    x, y = p[0] * zinv % _P, p[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def point_decompress(s: bytes) -> Point | None:
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def hash_to_scalar(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _Q


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """签名是否是 `public_key` 对 `message` 的合法 Ed25519 签名。任何畸形输入都回 False，不抛。"""
    if not isinstance(public_key, bytes) or not isinstance(signature, bytes):
        return False
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = point_decompress(public_key)
    if a is None:
        return False
    r_bytes = signature[:32]
    r = point_decompress(r_bytes)
    if r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    h = hash_to_scalar(r_bytes + public_key + message)
    return point_equal(point_mul(s, BASE), point_add(r, point_mul(h, a)))
