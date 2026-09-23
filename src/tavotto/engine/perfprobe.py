"""性能探针的机器事实（ADR 0075）。

前端探针量得到帧，量不到「这是台什么机器、此刻处于什么状态」：同一段代码在
M1 Air 插电时流畅、拔电开了低电量模式就掉一半帧，报告里没有这一行，就会被
误读成代码问题。这里只回**不识别个人的硬件与电源事实**：

* 机型标识（`hw.model`，如 ``MacBookAir10,1``）、CPU 名、核数、内存、系统版本；
* 电源：接电源还是电池、低电量模式、CPU 限速与热警告（过热降频）；
* 本进程是不是在 Rosetta 转译下跑（x86 构建跑在 Apple Silicon 上会慢一截）。

不回主机名、用户名、序列号、路径——报告是用户要发给别人的东西。
每条命令都有短超时、失败一律回 ``None``：探针是排障工具，不能自己卡住。
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

from . import atomicio, config, updater
from .runtime import CREATE_NO_WINDOW

#: 与 web/src/perf/session.ts 的 REPORT_SCHEMA 同一个字面量
REPORT_SCHEMA = "tavotto-perf-probe/1"
#: 一份报告 120 个片段 × 4000 帧封顶，实际几百 KB；超过这个量级就不是我们发的
MAX_REPORT_BYTES = 16 * 1024 * 1024

_TIMEOUT = 2.0


def _run(argv: list[str]) -> str | None:
    try:
        out = subprocess.run(  # noqa: S603 — 固定参数的系统工具，不含任何用户输入
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
            stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _sysctl(name: str) -> str | None:
    return _run(["/usr/sbin/sysctl", "-n", name])


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def _power_source(batt: str | None) -> str | None:
    """`pmset -g batt` 第一行：``Now drawing from 'AC Power'`` / ``'Battery Power'``"""
    if not batt:
        return None
    m = re.search(r"'([^']+)'", batt.splitlines()[0])
    if not m:
        return None
    src = m.group(1).lower()
    if "ac" in src:
        return "ac"
    if "battery" in src:
        return "battery"
    return "other"


def _low_power_mode(settings: str | None) -> bool | None:
    """`pmset -g` 当前生效的电源模式。

    两种写法都见过：老系统 / 笔记本是 ``lowpowermode 1``，新系统是
    ``powermode N``（0 自动、1 低电量、2 高性能）。都没有 = 不知道，回 None。
    """
    if not settings:
        return None
    m = re.search(r"^\s*lowpowermode\s+(\d+)", settings, re.MULTILINE)
    if m:
        return m.group(1) != "0"
    m = re.search(r"^\s*powermode\s+(\d+)", settings, re.MULTILINE)
    if m:
        return m.group(1) == "1"
    return None


def _thermal(therm: str | None) -> dict:
    """`pmset -g therm`：CPU 限速百分比（<100 = 正在降频）与热警告级别。

    没有记录过的那一项，pmset 写 ``No ... has been recorded``——那是「没有发生」，
    记 100 / 0；输出里既没有数值也没有这句话才是「不知道」（None）。
    """
    out: dict = {"cpu_speed_limit": None, "thermal_warning_level": None}
    if not therm:
        return out
    m = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", therm)
    if m:
        out["cpu_speed_limit"] = int(m.group(1))
    elif "No CPU power status has been recorded" in therm:
        out["cpu_speed_limit"] = 100
    m = re.search(r"[Tt]hermal warning level (?:set to|=)\s*(\d+)", therm)
    if m:
        out["thermal_warning_level"] = int(m.group(1))
    elif "No thermal warning level has been recorded" in therm:
        out["thermal_warning_level"] = 0
    return out


def system_facts() -> dict:
    facts: dict = {
        "tavotto_version": updater.current_version(),
        "os": sys.platform,
        "arch": platform.machine(),
        "cpu_count": os.cpu_count(),
    }
    if sys.platform != "darwin":
        facts["os_version"] = platform.release()
        return facts

    # 冻结构建里 platform.mac_ver() 在个别机器上回空串（2026-09-23 一份 M2 Pro 报告实测）：
    # 再问一次 sw_vers，仍拿不到才是「不知道」
    facts["os_version"] = platform.mac_ver()[0] or _run(["/usr/bin/sw_vers", "-productVersion"])
    facts["model"] = _sysctl("hw.model")
    facts["cpu"] = _sysctl("machdep.cpu.brand_string")
    # Apple Silicon 的性能核 / 能效核分开报（Intel 上没有这两个键 → None）
    facts["p_cores"] = _int(_sysctl("hw.perflevel0.physicalcpu"))
    facts["e_cores"] = _int(_sysctl("hw.perflevel1.physicalcpu"))
    mem = _int(_sysctl("hw.memsize"))
    facts["memory_gb"] = round(mem / 1024**3, 1) if mem else None
    # 1 = 本进程在 Rosetta 转译下；Intel 机器上这个键不存在
    translated = _sysctl("sysctl.proc_translated")
    facts["rosetta"] = None if translated is None else translated == "1"
    facts["power_source"] = _power_source(_run(["/usr/bin/pmset", "-g", "batt"]))
    facts["low_power_mode"] = _low_power_mode(_run(["/usr/bin/pmset", "-g"]))
    facts.update(_thermal(_run(["/usr/bin/pmset", "-g", "therm"])))
    return facts


class ReportRejected(ValueError):
    """载荷不是探针报告（形状不对 / 太大）。"""


def reports_dir() -> Path:
    """报告落在数据目录里（运行时可写数据一律走 ``config.data_dir()``）。"""
    return config.data_dir() / "perf-reports"


def save_report(raw: bytes) -> Path:
    """把前端交上来的报告原样落盘，回写好的路径。

    **文件名由这里生成**，请求体里的任何东西都不参与拼路径。桌面壳里
    WKWebView 会取消 ``<a download>``（壳没有注册下载处理器），所以报告
    经由后端写进数据目录，再由前端请壳在访达里把它显示出来。
    """
    if len(raw) > MAX_REPORT_BYTES:
        raise ReportRejected("too_large")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ReportRejected("malformed") from exc
    if not isinstance(body, dict) or body.get("schema") != REPORT_SCHEMA:
        raise ReportRejected("schema")
    if not isinstance(body.get("segments"), list):
        raise ReportRejected("schema")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    d = reports_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"tavotto-perf-{stamp}.json"
    n = 1
    while dest.exists():
        n += 1
        dest = d / f"tavotto-perf-{stamp}-{n}.json"
    atomicio.write_bytes(dest, raw)
    return dest
