"""macOS 上让测试会话里的 matplotlib 子进程共用一份 `system_profiler SPFontsDataType` 输出。

matplotlib 在 `MPLCONFIGDIR` 里找不到 `fontlist-v*.json` 时重建字体缓存，macOS 上第一步是
`subprocess.check_output(["system_profiler", "-xml", "SPFontsDataType"])`
（`matplotlib/font_manager.py::_get_macos_fonts`）。用例为了隔离普遍给子进程一个**新的**
`MPLCONFIGDIR`——用例自己的 `tmp_path / "mpl"`、产品按 `TAVOTTO_DATA_DIR` 派生的
`cache/mpl`、受管环境的缓存根、Compatibility Guardian 的 `work/mpl`——于是每起一个出图子进程就
真跑一次 `system_profiler`。实测（M4 Pro，2026-10-21）：它独占约 12 s CPU、负载下墙钟 40 s 以上；
拿到它的输出后 matplotlib 解析 700 多个字体只要 0.5 s。几个 worktree 并发跑测试时，几十个
`system_profiler` 同时枚举全机字体，把整台机器拖满（`kernel_task` 高占用就是它）。

做法：conftest 在 import 时把一个只含 `system_profiler` 垫片的目录放到 `PATH` 最前面。垫片只认
matplotlib 那一种调用（恰好 `-xml SPFontsDataType` 两个参数）：本会话第一次真跑并原子落盘，之后
直接回放；其他任何参数原样 `exec` 真的 `/usr/sbin/system_profiler`。

为什么不在各个 `MPLCONFIGDIR` 里预置 `fontlist-v*.json`：那些目录有一半是产品代码自己选的，
逐处预置漏一处就照旧重建；而且 fontlist 按 matplotlib 版本分文件、里面写着各自 mpl-data 的绝对
路径，跨解释器复制会悄悄改变被测环境。垫片替换的只是「这台机器装了哪些字体」这份清单——会话内
不变——matplotlib 的扫描、解析与缓存写入仍在每个子进程里照常发生。

边界（写在明处）：子进程的 `PATH` 被用例整个换掉（例如 `"PATH": "/usr/bin:/bin"`）时垫片不在，
退回真跑；会话中途往系统装字体的用例看不到新字体（目前没有这样的用例）。
"""

from __future__ import annotations

import os
import shlex
import sys
import tempfile

REAL_SYSTEM_PROFILER = "/usr/sbin/system_profiler"

_SHIM = """#!/bin/sh
# Tavotto 测试会话的 system_profiler 垫片（tests/support/system_fonts_shim.py）。
if [ "$#" -eq 2 ] && [ "$1" = "-xml" ] && [ "$2" = "SPFontsDataType" ]; then
  cache={cache}
  if [ ! -s "$cache" ]; then
    tmp="$cache.$$"
    {real} -xml SPFontsDataType > "$tmp"
    status=$?
    if [ "$status" -ne 0 ]; then
      rm -f "$tmp"
      exit "$status"
    fi
    mv -f "$tmp" "$cache"
  fi
  exec cat "$cache"
fi
exec {real} "$@"
"""


def install(
    environ: dict[str, str] | None = None,
    *,
    real: str = REAL_SYSTEM_PROFILER,
    platform: str = sys.platform,
) -> str | None:
    """在 macOS 上建垫片目录并前置到 `environ["PATH"]`；返回该目录，别的平台返回 None。"""
    env = os.environ if environ is None else environ
    if platform != "darwin" or not os.access(real, os.X_OK):
        return None
    shim_dir = tempfile.mkdtemp(prefix="tavotto-sysfonts-")
    shim = os.path.join(shim_dir, "system_profiler")
    with open(shim, "w", encoding="utf-8") as f:
        f.write(
            _SHIM.format(
                cache=shlex.quote(os.path.join(shim_dir, "SPFontsDataType.xml")),
                real=shlex.quote(real),
            )
        )
    os.chmod(shim, 0o755)
    path = env.get("PATH")
    env["PATH"] = shim_dir if not path else shim_dir + os.pathsep + path
    return shim_dir
