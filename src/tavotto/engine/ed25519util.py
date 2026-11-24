"""Ed25519 验签的共用小函数（远程期刊目录 ADR 0155 与高级版授权令牌 ADR 0193 共用，不复制）。

纯标准库 import；PyNaCl 只在函数体里延后 import，Flask 父进程启动不为原生轮子付代价。
"""

from __future__ import annotations


def verify(public_key: object, message: object, signature: object) -> bool:
    """Ed25519 验签；任何异常（含 `nacl` 缺失）都是 False，永不抛。公钥 32 字节、签名 64 字节。"""
    if not (
        isinstance(public_key, bytes | bytearray | memoryview)
        and isinstance(message, bytes | bytearray | memoryview)
        and isinstance(signature, bytes | bytearray | memoryview)
    ):
        return False
    pk, msg, sig = bytes(public_key), bytes(message), bytes(signature)
    if len(pk) != 32 or len(sig) != 64:
        return False
    try:
        # 延后：Flask 父进程启动不为原生轮子付代价；只在验签时才加载
        from nacl.signing import VerifyKey

        # libsodium 的 crypto_sign_verify_detached：拒 S >= L 的可塑性签名与小阶 / 非规范公钥点
        VerifyKey(pk).verify(msg, sig)
    except Exception:  # noqa: BLE001 - BadSignatureError / ValueError / TypeError / CryptoError / ImportError 等一律验签失败
        return False
    return True
