#!/usr/bin/env python3
"""Windows 上「产品不改注册表 / PATH / 用户目录」的前后快照（统一实施包 U05，ADR 0064 第二档证据）。

    private_python_windows_snapshot.py <out.json>                 # 拍一份快照
    private_python_windows_snapshot.py --diff <before> <after>    # 比对：变了就非零

量什么、怎么判（判据的主语写清楚）：

* 注册表：`HKCU\\Software\\Python`、`HKLM\\Software\\Python`（`reg query /s` 的原文；键不存在也是一种状态）、
  `HKCU\\Environment` 与 `HKLM\\…\\Session Manager\\Environment` 里的 `Path` 值——**逐字节相同**才算没改
  （py launcher 的登记、PEP 514 的解释器登记、用户 / 系统 PATH 全在这几处）。
* 本进程的 `PATH` 与 `USERPROFILE` 的值——逐字相同。
* `USERPROFILE` 顶层条目：允许多出来的只有 `.matplotlib`（`projectenv.probe_environment` 起 matplotlib 建字体缓存
  ——既有行为，worker 本身走 `child_env` 的 MPLCONFIGDIR）；别的新条目都算改了用户目录。
* `py --list-paths`（PEP 514 视角的登记表）只记录、不判——它读的就是上面那两个注册表键。

纯标准库；只在 Windows 上有意义（`reg.exe`），别的平台 `--diff` 照样能比两份 JSON。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

REG_KEYS = (
    r"HKCU\Software\Python",
    r"HKLM\Software\Python",
    r"HKLM\Software\WOW6432Node\Python",
)
PATH_VALUES = (
    (r"HKCU\Environment", "Path"),
    (r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment", "Path"),
)
#: USERPROFILE 顶层允许多出来的条目（各自的写入者写在模块说明里）。
ALLOWED_NEW_TOP_LEVEL = frozenset({".matplotlib"})


def _run(argv: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, f"<{exc}>"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def snapshot() -> dict:
    out: dict = {"registry": {}, "registry_path_values": {}, "env": {}, "userprofile_top": []}
    for key in REG_KEYS:
        rc, text = _run(["reg", "query", key, "/s"])
        out["registry"][key] = {"rc": rc, "text": text}
    for key, value in PATH_VALUES:
        rc, text = _run(["reg", "query", key, "/v", value])
        out["registry_path_values"][f"{key}::{value}"] = {"rc": rc, "text": text}
    out["env"]["PATH"] = os.environ.get("PATH", "")
    out["env"]["USERPROFILE"] = os.environ.get("USERPROFILE", "")
    profile = os.environ.get("USERPROFILE")
    if profile and Path(profile).is_dir():
        try:
            out["userprofile_top"] = sorted(p.name for p in Path(profile).iterdir())
        except OSError as exc:
            out["userprofile_top"] = [f"<unreadable: {exc}>"]
    rc, text = _run(["py", "--list-paths"])
    out["py_launcher"] = {"rc": rc, "text": text}
    return out


def diff(before: dict, after: dict) -> list[str]:
    problems: list[str] = []
    for section in ("registry", "registry_path_values"):
        for key in sorted(set(before.get(section, {})) | set(after.get(section, {}))):
            a, b = before.get(section, {}).get(key), after.get(section, {}).get(key)
            if a != b:
                problems.append(f"{section}: {key} 前后不同")
    for name in ("PATH", "USERPROFILE"):
        if before.get("env", {}).get(name) != after.get("env", {}).get(name):
            problems.append(f"env: {name} 前后不同")
    new_top = set(after.get("userprofile_top", [])) - set(before.get("userprofile_top", []))
    stray = sorted(new_top - ALLOWED_NEW_TOP_LEVEL)
    if stray:
        problems.append(f"USERPROFILE 顶层多出了 {stray}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "--diff":
        before = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        after = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        problems = diff(before, after)
        for p in problems:
            print("CHANGED " + p)
        if not problems:
            print(
                "OK 注册表 / PATH / USERPROFILE 前后一致（顶层新增 ⊆ %s）"
                % sorted(ALLOWED_NEW_TOP_LEVEL)
            )
        return 1 if problems else 0
    if len(argv) != 1:
        print(__doc__)
        return 2
    path = Path(argv[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"快照写到 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
