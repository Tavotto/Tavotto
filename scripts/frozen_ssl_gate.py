"""冻结产物里 OpenSSL 的 DLL 必须与 `_ssl.pyd` 同源（#1051 的构建期闸门；`packaging/tavotto.spec` 在 Analysis 之后调用）。

背景：Windows 上 `_ssl.pyd` 动态链接同目录的 `libcrypto-3.dll` / `libssl-3.dll`，**同名不同版本**。内置 runtime
（embeddable Python）的 PE 文件被 PyInstaller 按「二进制」做依赖分析，它的 `libcrypto-3.dll` 会作为依赖落到
`_internal/` 顶层；打包用的 Python 自己的那一份也落到同一个目标名，TOC 去重**先到先得**。两边的 Python 小版本一致时
两份内容一样，谁赢都无所谓；一旦托管 Python 升了 OpenSSL（3.13.15 → 3.13.16：3.0.21 → 3.5.9），顶层留下旧版，
新的 `_ssl.pyd` 就在导入时报 `DLL load failed while importing _ssl: The specified procedure could not be found.`。

这里只做一件事：顶层的 `libcrypto-*.dll` / `libssl-*.dll` 必须与被收进来的 `_ssl.pyd` 来自**同一个目录**。
纯函数，输入是 PyInstaller 的 TOC（`(dest, src, typecode)` 三元组），只比路径、不读文件。
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable

_OPENSSL_DLL = re.compile(r"^(libcrypto|libssl)-[\w.-]*\.dll$", re.IGNORECASE)


def _is_top_level(dest: str) -> bool:
    return "/" not in dest.replace("\\", "/")


def _same_dir(a: str, b: str) -> bool:
    return os.path.normcase(os.path.dirname(os.path.abspath(a))) == os.path.normcase(
        os.path.dirname(os.path.abspath(b))
    )


def ssl_dll_problems(toc: Iterable[tuple[str, str, str]]) -> list[str]:
    """TOC 里 OpenSSL DLL 与 `_ssl.pyd` 不同源的地方；空列表 = 通过。"""
    entries = list(toc)
    ssl_srcs = [
        src
        for dest, src, typecode in entries
        if typecode == "EXTENSION"
        and _is_top_level(dest)
        and os.path.basename(dest).lower().startswith("_ssl.")
    ]
    if not ssl_srcs:
        return ["冻结产物里没有收进 _ssl 扩展——应用启动时 import ssl 会失败"]
    ssl_src = ssl_srcs[0]
    dlls = [
        (dest, src)
        for dest, src, _tc in entries
        if _is_top_level(dest) and _OPENSSL_DLL.match(os.path.basename(dest))
    ]
    problems = [
        f"顶层 {dest} 来自 {src}，与 _ssl 扩展（{ssl_src}）不在同一个目录——版本很可能对不上"
        for dest, src in dlls
        if not _same_dir(src, ssl_src)
    ]
    if not any(os.path.basename(d).lower().startswith("libcrypto-") for d, _ in dlls):
        problems.append("顶层没有 libcrypto-*.dll——_ssl 会去加载别处的同名库")
    return problems


def assert_ssl_dlls_consistent(toc: Iterable[tuple[str, str, str]]) -> None:
    problems = ssl_dll_problems(toc)
    if problems:
        raise SystemExit(
            "[tavotto.spec] OpenSSL DLL 与 _ssl 不同源（#1051）：\n  - "
            + "\n  - ".join(problems)
            + "\n  多半是内置 runtime 里的同名 libcrypto/libssl 被当作依赖收到了顶层；"
            "runtime 必须在 Analysis 之后以 DATA 加入，不参与二进制依赖分析。"
        )
