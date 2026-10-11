#!/usr/bin/env python3
"""Import Origin Resolver 的性能 fixture（计划 P7.7）：5 / 30 / 100 个脚本的项目，量**默认扫描**的代价。

    PYTHONPATH=src python scripts/dev/import_origin_perf.py            # 打印表
    PYTHONPATH=src python scripts/dev/import_origin_perf.py --json     # 机器可读

量什么（每档脚本数各一行）：

* 默认项目扫描 `projscan.scan`（导入即扫描，零执行）冷启动：总耗时、打开的文件数、**打开的文件总字节**（独立于扫描自己的账：
  `sys.addaudithook` 收到的每个 `open` 事件，按文件当时的大小累加，是「读了多少」的上界）、目录列举次数、解析次数
  （`compile` 事件）、tracemalloc 峰值、扫描自己的预算账（`source_bytes`）；
* 同一份 `cache` 的第二遍（增量）：耗时、解析次数、缓存命中率 = 1 − 第二遍解析次数 / 第一遍解析次数；
* 静态来源解析 `importscan.scan` 把这 N 个脚本各解一遍：总耗时、读到的文件数（并集）；
* 三个**必须为 0** 的计数（全程审计事件）：子进程（`subprocess.Popen` / `os.system` / `os.exec*` / `os.posix_spawn` /
  `os.spawn*` / `os.fork*`）、网络（`socket.connect` / `socket.getaddrinfo` …）、数据文件与 `.venv` 里文件的打开。

**什么不是**：这不是 CI 的耗时门禁（耗时随机器抖动，这里只记录）；会断言的是确定性的计数
（`tests/test_import_origin_perf.py`）。数字写进 `docs/implementation/import-origin-resolver/FINAL_ACCEPTANCE.md`，
标明机器与日期。纯标准库 + tavotto；不起任何子进程。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

# Windows 上 stdout/stderr 被重定向成管道时会退回系统区域编码（cp1252/cp936），
# 第一句中文输出就 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from tavotto.engine import importscan, projscan, scanbudget  # noqa: E402

SIZES = (5, 30, 100)

#: 这些审计事件一次都不许出现（子进程 / 网络）。`os.exec` 是 execv 家族；`os.fork` 含 forkpty。
FORBIDDEN_EVENTS = frozenset(
    {
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.spawn",
        "os.fork",
        "os.forkpty",
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.__new__",
    }
)

SCRIPT = """\
import numpy as np
import matplotlib.pyplot as plt
import shared
from lab.util import smooth

x = np.linspace(0, 1, 50)
fig, ax = plt.subplots()
ax.plot(x, smooth(shared.curve(x)))
ax.set_title("Figure {i}")
fig.savefig("fig{i:03d}.pdf")
"""


class Collector:
    """审计事件的收集器。钩子装一次（装了就拆不掉），`active` 为空时什么都不记。"""

    def __init__(self) -> None:
        self.active: Collector | None = None
        self.opens = 0
        self.open_bytes = 0
        self.open_paths: list[str] = []
        self.scandirs = 0
        self.compiles = 0
        self.forbidden: list[str] = []

    def reset(self) -> None:
        self.opens = self.open_bytes = self.scandirs = self.compiles = 0
        self.open_paths = []
        self.forbidden = []


_COLLECTOR = Collector()
_INSTALLED = False


def _hook(event: str, args: tuple) -> None:
    c = _COLLECTOR
    if c.active is None:
        return
    if event == "open":
        path, mode = args[0], args[1]
        if isinstance(path, (str, bytes, os.PathLike)) and (mode is None or "w" not in str(mode)):
            p = os.fsdecode(path)
            c.opens += 1
            c.open_paths.append(p)
            try:
                c.open_bytes += os.stat(p).st_size
            except OSError:
                pass
    elif event == "os.scandir" or event == "os.listdir":
        c.scandirs += 1
    elif event == "compile":
        c.compiles += 1
    elif event in FORBIDDEN_EVENTS:
        c.forbidden.append(event)


def _install() -> None:
    global _INSTALLED
    if not _INSTALLED:
        sys.addaudithook(_hook)
        _INSTALLED = True


def make_project(base: Path, scripts: int) -> Path:
    """`scripts` 个绘图脚本 + 共享模块 + 与脚本无关的模块 + 一份大数据文件 + 一个很大的 `.venv`（都不许被读）。"""
    root = base / f"proj_{scripts}"
    sdir = root / "scripts"
    (sdir / "lab").mkdir(parents=True)
    for i in range(scripts):
        (sdir / f"fig{i:03d}.py").write_text(SCRIPT.format(i=i), encoding="utf-8")
    (sdir / "shared.py").write_text(
        "import scipy.signal\n\n\ndef curve(x):\n    return x**2\n", encoding="utf-8"
    )
    (sdir / "lab" / "__init__.py").write_text("", encoding="utf-8")
    (sdir / "lab" / "util.py").write_text(
        "import h5py\n\n\ndef smooth(y):\n    return y\n", encoding="utf-8"
    )
    for i in range(40):  # 与任何脚本都无关的模块：解析某个脚本时不能被读
        (sdir / f"unrelated{i}.py").write_text("import torch\n" * 20, encoding="utf-8")
    data = root / "data"
    data.mkdir()
    (data / "results.csv").write_bytes(
        b"x,y\n" + b"1,2\n" * 400_000
    )  # ~1.6 MB，只许被列出、不许被打开
    venv = root / ".venv" / "lib" / "python3.13" / "site-packages"
    venv.mkdir(parents=True)
    (root / ".venv" / "pyvenv.cfg").write_text("home = /nowhere\n", encoding="utf-8")
    for i in range(600):  # 一个像样的环境有几千个文件；这里 600 个已经足够看出「有没有读进去」
        (venv / f"pkg{i}.py").write_text("X = 1\n", encoding="utf-8")
    (root / "requirements.txt").write_text("numpy\nmatplotlib\nscipy\nh5py\n", encoding="utf-8")
    return root


def _measure(fn, *, memory: bool = False):
    c = _COLLECTOR
    c.reset()
    c.active = c
    if memory:
        tracemalloc.start()
    t0 = time.perf_counter()
    try:
        result = fn()
    finally:
        elapsed = time.perf_counter() - t0
        peak = 0
        if memory:
            _cur, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        c.active = None
    return result, {
        "seconds": round(elapsed, 4),
        "opens": c.opens,
        "open_bytes": c.open_bytes,
        "scandirs": c.scandirs,
        "compiles": c.compiles,
        "peak_mib": round(peak / (1024 * 1024), 2) if memory else None,
        "forbidden_events": list(c.forbidden),
        "opened_paths": list(c.open_paths),
    }


def _forbidden_opens(paths: list[str], root: Path) -> list[str]:
    """打开了 `data/` 或 `.venv/` 里的文件吗（不许）。"""
    bad = []
    for p in paths:
        try:
            rel = Path(p).resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            continue
        if rel.parts and rel.parts[0] in ("data", ".venv"):
            bad.append(rel.as_posix())
    return bad


def run_one(base: Path, scripts: int, repeats: int = 3) -> dict:
    _install()
    root = make_project(base, scripts)
    source_total = sum(p.stat().st_size for p in (root / "scripts").rglob("*.py"))

    # 默认扫描，冷：每次用空 cache；时间取中位数，计数取最后一遍（确定性的）
    colds = []
    for _ in range(repeats):
        cache: dict = {}
        _report, m = _measure(lambda: projscan.scan(root, cache=cache), memory=False)
        colds.append(m)
    cache = {}
    report, cold = _measure(lambda: projscan.scan(root, cache=cache), memory=True)
    cold["seconds"] = round(statistics.median([c["seconds"] for c in colds] + [cold["seconds"]]), 4)
    # 同一份 cache 再来一遍：增量
    _report2, warm = _measure(lambda: projscan.scan(root, cache=cache))

    # 静态来源解析：把这些脚本各解一遍
    def resolve_all():
        files: set[str] = set()
        classes = 0
        for i in range(scripts):
            budget = scanbudget.Budget()
            res = importscan.scan(root, f"scripts/fig{i:03d}.py", budget=budget)
            files.update(res.files)
            classes += len(res.classes)
        return files, classes

    (files, classes), resolve = _measure(resolve_all)

    hit = None
    if cold["compiles"]:
        hit = round(1 - warm["compiles"] / cold["compiles"], 4)
    forbidden_opens = _forbidden_opens(
        cold["opened_paths"] + warm["opened_paths"] + resolve["opened_paths"], root
    )
    for m in (cold, warm, resolve):
        m.pop("opened_paths")
    return {
        "scripts": scripts,
        "project_source_bytes": source_total,
        "scan_state": report["state"],
        "scan_scripts_found": report["budget"]["scripts"],
        "scan_budget_source_bytes": report["budget"]["source_bytes"],
        "default_scan_cold": cold,
        "default_scan_warm": warm,
        "cache_hit_rate": hit,
        "static_resolve_all": {**resolve, "files_read": len(files), "classes": classes},
        "extra_subprocesses": len(
            cold["forbidden_events"] + warm["forbidden_events"] + resolve["forbidden_events"]
        ),
        "data_or_venv_files_opened": forbidden_opens,
    }


def run(sizes=SIZES, repeats: int = 3) -> dict:
    out = {
        "machine": {
            "python": platform.python_version(),
            "system": f"{platform.system()} {platform.release()}",
            "machine": platform.machine(),
            "cpus": os.cpu_count(),
        },
        "date": time.strftime("%Y-%m-%d"),
        "rows": [],
    }
    for n in sizes:
        base = Path(tempfile.mkdtemp(prefix="tavotto-perf-"))
        try:
            out["rows"].append(run_one(base, n, repeats))
        finally:
            shutil.rmtree(base, ignore_errors=True)
    return out


def table(result: dict) -> str:
    lines = [
        f"{result['date']} · Python {result['machine']['python']} · {result['machine']['system']} {result['machine']['machine']} · {result['machine']['cpus']} 核",
        "",
        "| 脚本数 | 默认扫描冷（s） | 打开文件 / 字节 | 解析次数 | 峰值 MiB | 增量（s） | 缓存命中 | 静态解析全部（s） | 读到文件 | 额外子进程 | 误开数据 / .venv |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in result["rows"]:
        c, w, s = r["default_scan_cold"], r["default_scan_warm"], r["static_resolve_all"]
        lines.append(
            f"| {r['scripts']} | {c['seconds']} | {c['opens']} / {c['open_bytes']} | {c['compiles']} | {c['peak_mib']} "
            f"| {w['seconds']} | {r['cache_hit_rate']:.0%} | {s['seconds']} | {s['files_read']} "
            f"| {r['extra_subprocesses']} | {len(r['data_or_venv_files_opened'])} |"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--sizes", type=int, nargs="*", default=list(SIZES))
    args = ap.parse_args()
    result = run(tuple(args.sizes), args.repeats)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else table(result))
    return 0 if all(r["extra_subprocesses"] == 0 for r in result["rows"]) else 1


if __name__ == "__main__":
    sys.exit(main())
