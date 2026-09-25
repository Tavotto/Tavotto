#!/usr/bin/env python3
"""U10 切换前核验：运行时闭包里的每个原生包在 `docs/support-matrix.json` 的每一格都有目标 wheel。

    <venv>/bin/python scripts/dev/u10_wheel_matrix.py --out docs/implementation/tavotto-foundation/evidence/u10

对每个（包 == 钉死版本）×（Python 3.10 … 3.14）×（目标平台）跑一次
`pip download --only-binary=:all: --no-deps --python-version … --platform …`：拿得到 wheel 就记文件名（含平台标签），
拿不到就记 pip 的最后一行。**不装、不 import**——问的是「索引上有没有那个格子的二进制」，不是「本机装得上」。
包与版本从 `requirements.txt`（钉死镜像）读，闭包里的传递原生依赖（lxml：pikepdf 的硬依赖）单列。

目标平台按支持矩阵：Windows x64、macOS arm64、macOS x86_64（ADR 0076 起有 Intel 桌面版）、Linux x86_64 是有产物 /
有门禁的四格；Linux aarch64 只有 pip 渠道（矩阵里未列），一并量、单独标 `informational`——量出来的事实进 ADR 0072，
不据此给任何平台写「支持」。

**macOS 的最低系统版本**：wheel 文件名里的 `macosx_<maj>_<min>_<arch>` 就是它要求的最低 macOS。每格记 `macos_min`，
按目标取最大值记进 `macos_floor`——桌面版的 `minimumSystemVersion` 与 `docs/support-matrix.json` 的 `min_os` 不得低于它
（`tests/test_support_matrix.py` 看护）。第一版把一组 11–15 的标签一起交给 pip，拿到的是 14.0 的 wheel 也记「有」，
Intel 那组又只列到 14_0、漏了 pikepdf 的 15_0，于是报成「没有 x86_64 wheel」——两个方向都没量最低版本。所以标签一直
列到当前最新的系统，下限另算。

结果 `wheel_matrix.json`；退出码：正式目标 × 五个版本 × 每个包全部拿到 = 0，否则 1（informational 格不计）。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
#: 钉死镜像（U10 起 RenderCore 的原生包全在 requirements.txt）
REQUIREMENTS = (ROOT / "requirements.txt",)
SUPPORT_MATRIX = ROOT / "docs" / "support-matrix.json"

#: 闭包里带原生扩展的包（纯 Python 的 flask / packaging / fonttools 不在这张表上：它们的 wheel 是 `py3-none-any`，
#: 平台维度不存在）。lxml 不在 requirements.txt 里（pikepdf 的传递依赖），版本按 pikepdf 10.13 的下界之上、本机实测装到的那档。
NATIVE_PACKAGES = ("pikepdf", "pypdfium2", "uharfbuzz", "pillow", "lxml")
TRANSITIVE_PINS = {"lxml": "6.1.3"}

#: 平台标签：pip 允许多个 `--platform`，给一组是为了让 wheel 的最低系统版本标签能匹配上。macOS 一直列到当前
#: 最新的系统（26），wheel 要求多高由 `macos_min` 另记，不靠「给的标签里有没有」来判。
_MACOS = ("10_13", "10_15", "11_0", "12_0", "13_0", "14_0", "15_0", "26_0")
TARGETS = {
    "windows-x64": {"platforms": ["win_amd64"], "formal": True},
    "macos-arm64": {
        "platforms": [f"macosx_{v}_arm64" for v in _MACOS if not v.startswith("10_")],
        "formal": True,
    },
    "linux-x86_64": {
        "platforms": [
            "manylinux2014_x86_64",
            "manylinux_2_17_x86_64",
            "manylinux_2_24_x86_64",
            "manylinux_2_28_x86_64",
            "manylinux_2_34_x86_64",
        ],
        "formal": True,
    },
    "macos-x86_64": {
        "platforms": [f"macosx_{v}_x86_64" for v in _MACOS],
        "formal": True,
    },
    "linux-aarch64": {
        "platforms": [
            "manylinux2014_aarch64",
            "manylinux_2_17_aarch64",
            "manylinux_2_28_aarch64",
            "manylinux_2_34_aarch64",
        ],
        "formal": False,
    },
}


def _pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for req in REQUIREMENTS:
        if not req.is_file():
            continue
        for line in req.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, _, ver = line.partition("==")
            pins[name.strip().lower()] = ver.strip()
    pins.update(TRANSITIVE_PINS)
    return pins


_MACOS_TAG = re.compile(r"macosx_(\d+)_(\d+)_(?:arm64|x86_64|universal2|intel)")


def macos_min(wheel: str | None) -> str | None:
    """wheel 文件名要求的最低 macOS（`14.0`）；不是 macOS wheel 回 None。多个标签取最低的那个（pip 认任一即可）。"""
    if not wheel:
        return None
    found = [(int(a), int(b)) for a, b in _MACOS_TAG.findall(wheel)]
    if not found:
        return None
    lo = min(found)
    return f"{lo[0]}.{lo[1]}"


def _ver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def _python_versions() -> list[str]:
    return list(json.loads(SUPPORT_MATRIX.read_text(encoding="utf-8"))["python"]["tested"])


def _download(pkg: str, ver: str, py: str, platforms: list[str], dest: Path) -> dict:
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--only-binary=:all:",
        "--no-deps",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--python-version",
        py,
        "--implementation",
        "cp",
        "-d",
        str(dest),
        f"{pkg}=={ver}",
    ]
    for p in platforms:
        cmd += ["--platform", p]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    saved = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip().startswith("Saved ")]
    wheel = None
    if proc.returncode == 0:
        # `Saved <path>` 或已缓存时 `File was already downloaded <path>`
        for ln in proc.stdout.splitlines():
            m = re.search(r"(?:Saved|File was already downloaded)\s+(.+\.whl)", ln)
            if m:
                wheel = Path(m.group(1).strip()).name
        if wheel is None:
            found = sorted(dest.glob(f"{pkg.replace('-', '_')}-{ver}*.whl"), key=lambda p: p.name)
            wheel = found[-1].name if found else None
    tail = (proc.stderr.strip().splitlines() or proc.stdout.strip().splitlines() or [""])[-1]
    return {
        "ok": proc.returncode == 0 and wheel is not None,
        "wheel": wheel,
        "note": None if proc.returncode == 0 else tail[:300],
        "saved": saved[:1],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--packages", default=",".join(NATIVE_PACKAGES))
    ap.add_argument("--targets", default=",".join(TARGETS))
    args = ap.parse_args(argv)
    pins = _pins()
    pkgs = [p.strip().lower() for p in args.packages.split(",") if p.strip()]
    missing = [p for p in pkgs if p not in pins]
    if missing:
        print(f"requirements*.txt 里没有钉死版本：{missing}", file=sys.stderr)
        return 2
    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    pys = _python_versions()
    args.out.mkdir(parents=True, exist_ok=True)
    cells: list[dict] = []
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="u10-wheels-") as tmp:
        dest = Path(tmp)
        for pkg in pkgs:
            for target in targets:
                for py in pys:
                    res = _download(pkg, pins[pkg], py, TARGETS[target]["platforms"], dest)
                    cells.append(
                        {
                            "package": pkg,
                            "version": pins[pkg],
                            "python": py,
                            "target": target,
                            "formal": TARGETS[target]["formal"],
                            **res,
                            "macos_min": macos_min(res["wheel"]),
                        }
                    )
                    print(
                        f"{'OK  ' if res['ok'] else 'MISS'} {pkg}=={pins[pkg]} py{py} {target}: "
                        f"{res['wheel'] or res['note']}"
                    )
    formal_bad = [c for c in cells if c["formal"] and not c["ok"]]
    info_bad = [c for c in cells if not c["formal"] and not c["ok"]]
    report = {
        "schema": 1,
        "kind": "u10_wheel_matrix",
        "generated_with": {"python": sys.version.split()[0], "pip_host": sys.platform},
        "python_versions": pys,
        "targets": {k: v for k, v in TARGETS.items() if k in targets},
        "packages": {p: pins[p] for p in pkgs},
        "cells": cells,
        # 每个 macOS 目标：所有包里要求最高的那个最低系统版本，以及是谁要求的
        "macos_floor": {
            t: max(
                (
                    (c["macos_min"], c["package"])
                    for c in cells
                    if c["target"] == t and c["macos_min"]
                ),
                key=lambda x: _ver(x[0]),
                default=(None, None),
            )
            for t in targets
            if t.startswith("macos-")
        },
        "formal_missing": [f"{c['package']} py{c['python']} {c['target']}" for c in formal_bad],
        "informational_missing": [
            f"{c['package']} py{c['python']} {c['target']}" for c in info_bad
        ],
        "elapsed_s": round(time.perf_counter() - t0, 1),
    }
    (args.out / "wheel_matrix.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(
        f"formal cells {sum(1 for c in cells if c['formal'])}: missing {len(formal_bad)}; "
        f"informational cells {sum(1 for c in cells if not c['formal'])}: missing {len(info_bad)}"
    )
    return 1 if formal_bad else 0


if __name__ == "__main__":
    sys.exit(main())
