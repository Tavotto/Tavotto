"""SHA-256 校验与「只在私有目录里写」两条判据——纯标准库，spike 与其测试共用。

判据的主语写清楚：
* `verify_sha256(path, expected)` 量的是**磁盘上这个文件此刻的字节**；对不上就抛
  `HashMismatch`，调用方拿不到「已校验」的返回值，就没有下一步——「坏 hash 不执行」
  靠的是控制流（异常），不是靠调用方记得去看返回值。
* `download_verified()` 下载到 `.part`，**校验通过才 rename 成正式名**：磁盘上从不存在
  一个叫正式名字、却没校验过的文件。校验失败 `.part` 当场删掉。
* `assert_under(path, root)` 量的是 realpath 之后的前缀关系（软链接指出去也算越界）。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path


class HashMismatch(Exception):
    """文件字节的 SHA-256 与登记值不同。"""

    def __init__(self, path: Path, expected: str, got: str):
        super().__init__(f"SHA-256 不符：{path}\n  期望 {expected}\n  实得 {got}")
        self.path, self.expected, self.got = path, expected, got


class OutsidePrivateDir(Exception):
    """写入落点不在应用私有目录之下。"""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text_lf(path: Path) -> str:
    """文本文件按 LF 归一化之后的 SHA-256——给「内容身份」用，不给「字节身份」用。

    `packaging/runtime-lock.json` 这类没钉 `eol=lf` 的文本在 Windows 检出成 CRLF，
    `sha256_file` 会把同一份内容算成两个值（U02 spikes 的 windows-latest 腿实测红过）。
    归档 / wheel / 字体这类二进制**不许**用这个：它们的身份就是字节。"""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def verify_sha256(path: Path, expected: str) -> Path:
    """校验通过返回 `path`（作为「已校验」的凭据）；不通过抛 HashMismatch。"""
    expected = expected.strip().lower()
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError(f"登记的不是一个 SHA-256 十六进制串：{expected!r}")
    got = sha256_file(path)
    if got != expected:
        raise HashMismatch(Path(path), expected, got)
    return Path(path)


def download_verified(
    url: str, dest: Path, expected_sha256: str, *, timeout: float = 300, attempts: int = 3
) -> Path:
    """下载 → 校验 → 才 rename 成 `dest`。已有且校验通过的 `dest` 直接复用。

    **传输层**的失败（连接断、TLS EOF、超时）有界重试 `attempts` 次——那是网络的事；
    **hash 不符绝不重试**：对不上的文件不是「再下一次」的对象，是拒绝的对象。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        try:
            return verify_sha256(dest, expected_sha256)
        except HashMismatch:
            dest.unlink()
    part = dest.with_name(dest.name + ".part")
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp, part.open("wb") as fh:
                shutil.copyfileobj(resp, fh)
        except (urllib.error.URLError, OSError) as exc:
            part.unlink(missing_ok=True)
            last = exc
            if attempt < attempts:
                time.sleep(2.0 * attempt)
            continue
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        try:
            verify_sha256(part, expected_sha256)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        os.replace(part, dest)
        return dest
    assert last is not None
    raise last


def assert_under(path: Path, root: Path) -> Path:
    """`path`（realpath）必须在 `root`（realpath）之下，否则抛 OutsidePrivateDir。"""
    p = Path(os.path.realpath(path))
    r = Path(os.path.realpath(root))
    try:
        p.relative_to(r)
    except ValueError:
        raise OutsidePrivateDir(f"{p} 不在私有目录 {r} 之下") from None
    return p
