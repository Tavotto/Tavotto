"""环境故障家族的夹具：构建期在隔离临时目录里合成假 wheel（不提交二进制）。

题库里 `family == "environment"` 的题带 `fault`：

    {"name": "<闭集名>",
     "dev_fault": "selftest_blocked",          # 可选：对 sidecar 设 TAVOTTO_DEV=1 + TAVOTTO_DEV_FAULT_<名>=1
     "wheels": [{"dist", "import", "version", "tag", "init"}],   # 要放进本地 find-links 目录的假 wheel
     "env": {"TAVFX_X": "1"}}                  # 可选：额外的 sidecar 环境变量

`tag`：`py3-none-any`（装得上）或 `other_cp`（只有与本解释器不同 Python 小版本的 cp 标签，装不上）。
假包名一律以 `tavfx` 打头，PyPI 上没有，pip 只会从 find-links 找到它们；矩阵里别的包（matplotlib…）照常走索引。
"""

from __future__ import annotations

import base64
import hashlib
import subprocess
import sysconfig
import zipfile
from pathlib import Path


def _record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def other_cp_tag(python: str) -> str:
    """与 `python` 不同小版本的 cp 标签（如它是 3.13 就给 cp312）：「只有别的 Python 版本的 wheel」。"""
    out = subprocess.run(  # noqa: S603
        [python, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.split()
    major, minor = int(out[0]), int(out[1])
    other = 12 if minor != 12 else 13
    plat = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    return f"cp{major}{other}-cp{major}{other}-{plat}"


def build_wheel(
    house: Path, *, dist: str, import_name: str, version: str, init: str, tag: str
) -> Path:
    """在 `house` 里写一个只含 `<import_name>/__init__.py` 的 wheel，返回路径。"""
    house.mkdir(parents=True, exist_ok=True)
    norm = dist.replace("-", "_")
    info = f"{norm}-{version}.dist-info"
    files = {
        f"{import_name}/__init__.py": init.encode(),
        f"{info}/METADATA": (f"Metadata-Version: 2.1\nName: {dist}\nVersion: {version}\n").encode(),
        f"{info}/WHEEL": (
            f"Wheel-Version: 1.0\nGenerator: tavotto-eval\nRoot-Is-Purelib: true\nTag: {tag}\n"
        ).encode(),
        f"{info}/top_level.txt": f"{import_name}\n".encode(),
    }
    path = house / f"{norm}-{version}-{tag}.whl"
    record = "".join(f"{n},{_record_hash(b)},{len(b)}\n" for n, b in files.items())
    record += f"{info}/RECORD,,\n"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        zf.writestr(f"{info}/RECORD", record)
    return path


def build_fixtures(fault: dict, house: Path, python: str) -> dict[str, str]:
    """按题库的 `fault` 造 wheel，返回要并进 sidecar 环境的变量（find-links + 开发故障开关 + 题自带的）。"""
    env: dict[str, str] = {}
    for w in fault.get("wheels") or []:
        tag = other_cp_tag(python) if w["tag"] == "other_cp" else w["tag"]
        build_wheel(
            house,
            dist=w["dist"],
            import_name=w["import"],
            version=w["version"],
            init=w["init"],
            tag=tag,
        )
    if fault.get("wheels"):
        # 只加 find-links，不关 PyPI：受管环境还要装 matplotlib。用户自配了包源就不走镜像回退（`pip_source = user_config`）
        env["PIP_FIND_LINKS"] = str(house)
    if fault.get("dev_fault"):
        env["TAVOTTO_DEV"] = "1"
        env[f"TAVOTTO_DEV_FAULT_{str(fault['dev_fault']).upper()}"] = "1"
    env.update(fault.get("env") or {})
    return env
