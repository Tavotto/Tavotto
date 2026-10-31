"""桌面 sidecar 的「启动阶段」标记（TVD-DT76）。**纯标准库**：打包入口在 import 任何重模块之前就要用它。

握手超时（壳报 `sidecar_timeout`）时，壳只知道「60 秒内没写 ready」，分不出卡在哪一步。sidecar 在启动的几个关键点
往 fd 2 写一行 `TAVOTTO_STARTUP_PHASE=<阶段>`，壳超时后只读**本次启动新增**的日志，取最后一个合法标记进最小诊断包
（`failure.startup_phase`），一个都没有记 `none`（解释器根本没跑起来，多半是杀软在扫 / 拦）。

* 阶段名是闭集 [`PHASES`]，与壳（`src-tauri/src/sidecar/startup_phase.rs`）严格同源：两侧各读
  `tests/golden/desktop_startup_phase.json`，不读对方源码。
* 直接 `os.write(2, ...)`：不经 `sys.stderr`（Windows 窗口化冻结 exe 里它可能是 None，或已被改道进 app.log），
  也没有缓冲，进程被杀前写出的标记一定落进壳重定向的 `sidecar.log`。
* 只在 `--desktop-sidecar` 下写（[`enabled`]）；写失败一律吞掉——标记是诊断，不是启动的前提。
"""

from __future__ import annotations

import os
import sys

MARKER_PREFIX = "TAVOTTO_STARTUP_PHASE="
#: 按启动先后排列。
PHASES = (
    "interpreter",  # 打包入口最早处，重 import 之前
    "imports_done",  # app 模块 import 完、参数解析后
    "housekeeping",  # 启动清扫段开始
    "pdf_backend",  # PDF 后端预热前
    "project_open",  # 恢复 / 打开项目前
    "serving",  # 绑定端口、写 ready 之前
)


def enabled(argv: list[str] | None = None) -> bool:
    return "--desktop-sidecar" in (sys.argv[1:] if argv is None else argv)


def mark(phase: str, *, fd: int = 2) -> None:
    """写一行阶段标记。前导换行保证它独占一行（前一条日志没换行结尾也不粘连）。"""
    if phase not in PHASES:
        return
    try:
        os.write(fd, f"\n{MARKER_PREFIX}{phase}\n".encode("ascii"))
    except OSError:
        pass
