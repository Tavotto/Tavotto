"""QA 2026-09-24 §3 的 E1 夹具：两个**真实隔离**的 venv（uv 建），内含同名测试包、返回不同数值。

    python make_e1.py --dest <scratch>/e1 [--base <python3.13>]

产出（全部在 --dest 下，确定性：wheel 的 zip 时间戳固定、文件顺序固定，无随机数）：

* ``proj/.venv``      —— A：项目自己的 venv，matplotlib + ``qa_probe_pkg 1.0.0``，``value() == [0, 9, 2, 10]``
* ``envB/.venv``      —— B：另一个 venv（harness 会把它的 ``bin`` 放到 PATH 最前），``qa_probe_pkg 2.0.0``，
  ``value() == [1, 1, 1, 1]``
* ``bare/.venv``      —— 没有 matplotlib 的真实 venv（ENV-03 的「显式路径存在但不可用」）
* ``proj/figure.py``  —— 用户脚本：把 ``qa_probe_pkg.value()`` 画成折线，把完整点序列写进标题、把
  解释器身份（executable / prefix / base_prefix / 包 __file__ / 包版本）写进一行 figure 文字
* ``wheels/``         —— 两个手工 wheel（纯 zipfile，不需要构建后端 / 不联网）
* ``e1.json``         —— 每个环境的独立身份（``sys.executable`` / prefix / 包 ``__file__`` / 版本）与各文件 sha256

**harness 只建初始项目环境**：不设 ``TAVOTTO_WORKER_PYTHON``、不 remember、不替产品挑环境（spec §3）。
matplotlib 用 ``uv pip install``（先 ``--offline`` 走 uv 缓存，缓存没有再联网）。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

DEFAULT_BASE = "/opt/homebrew/opt/python@3.13/libexec/bin/python3"
MPL_SPEC = "matplotlib==3.10.8"
FIXED_ZIP_TIME = (2026, 9, 24, 0, 0, 0)

VALUES = {"A": [0, 9, 2, 10], "B": [1, 1, 1, 1]}
VERSIONS = {"A": "1.0.0", "B": "2.0.0"}

FIGURE_PY = '''"""E1 用户脚本：数据来自同名包 qa_probe_pkg（A / B 两份返回不同数值）。"""
import json
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import qa_probe_pkg  # noqa: E402

values = qa_probe_pkg.value()
ident = {
    "executable": sys.executable,
    "prefix": sys.prefix,
    "base_prefix": sys.base_prefix,
    "pkg_file": qa_probe_pkg.__file__,
    "pkg_version": qa_probe_pkg.VERSION,
}
fig, ax = plt.subplots(figsize=(3.2, 2.4))
ax.plot(range(len(values)), values, "o-")
ax.set_title("QA-E1 " + json.dumps(values))
fig.text(0.01, 0.01, "ID " + json.dumps(ident, sort_keys=True), fontsize=2)
fig.savefig("figure.pdf")
'''


def _run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(argv), flush=True)
    return subprocess.run(argv, check=True, text=True, capture_output=True, **kw)


def _py(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _zinfo(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
    info.external_attr = 0o644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def build_wheel(dest_dir: Path, variant: str) -> Path:
    """纯 zipfile 拼一个 py3-none-any wheel：``qa_probe_pkg/__init__.py`` + dist-info。"""
    version = VERSIONS[variant]
    init = (
        f'"""QA 探针包（变体 {variant}）。"""\n'
        f'VERSION = "{version}"\n'
        f'VARIANT = "{variant}"\n\n\n'
        f"def value():\n    return {VALUES[variant]!r}\n"
    ).encode()
    dist = f"qa_probe_pkg-{version}.dist-info"
    files = {
        "qa_probe_pkg/__init__.py": init,
        f"{dist}/METADATA": (
            f"Metadata-Version: 2.1\nName: qa-probe-pkg\nVersion: {version}\n"
            f"Summary: QA E1 probe variant {variant}\n"
        ).encode(),
        f"{dist}/WHEEL": b"Wheel-Version: 1.0\nGenerator: qa-make-e1\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{dist}/top_level.txt": b"qa_probe_pkg\n",
    }
    record_lines = []
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record_lines.append(f"{name},sha256={digest},{len(data)}")
    record_lines.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = ("\n".join(record_lines) + "\n").encode()
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"qa_probe_pkg-{version}-py3-none-any.whl"
    with zipfile.ZipFile(out, "w") as zf:
        for name, data in files.items():
            zf.writestr(_zinfo(name), data)
    return out


def make_venv(venv: Path, base: str, *packages: str) -> None:
    if venv.exists():
        shutil.rmtree(venv)
    _run(["uv", "venv", "--python", base, str(venv)])
    if not packages:
        return
    argv = ["uv", "pip", "install", "--python", str(_py(venv)), *packages]
    try:
        _run([*argv[:3], "--offline", *argv[3:]])
    except subprocess.CalledProcessError:
        _run(argv)


IDENT_SRC = (
    "import json, platform, sys\n"
    "out = {'executable': sys.executable, 'prefix': sys.prefix, 'base_prefix': sys.base_prefix,"
    " 'python_version': platform.python_version()}\n"
    "try:\n"
    "    import qa_probe_pkg\n"
    "    out.update(pkg_file=qa_probe_pkg.__file__, pkg_version=qa_probe_pkg.VERSION, value=qa_probe_pkg.value())\n"
    "except ImportError as e:\n"
    "    out['pkg_error'] = repr(e)\n"
    "try:\n"
    "    import matplotlib\n"
    "    out['matplotlib'] = matplotlib.__version__\n"
    "except ImportError:\n"
    "    out['matplotlib'] = None\n"
    "print(json.dumps(out))\n"
)


def identity(python: Path) -> dict:
    """独立探针：目标解释器自报身份（空目录 cwd、不经产品代码）。"""
    return json.loads(_run([str(python), "-c", IDENT_SRC], cwd=python.parent).stdout)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True)
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()
    dest = Path(args.dest).absolute()
    dest.mkdir(parents=True, exist_ok=True)
    wheels = dest / "wheels"
    wa, wb = build_wheel(wheels, "A"), build_wheel(wheels, "B")
    proj = dest / "proj"
    proj.mkdir(exist_ok=True)
    (proj / "figure.py").write_text(FIGURE_PY, encoding="utf-8")
    make_venv(proj / ".venv", args.base, MPL_SPEC, str(wa))
    make_venv(dest / "envB" / ".venv", args.base, MPL_SPEC, str(wb))
    make_venv(dest / "bare" / ".venv", args.base)
    envs = {
        "A": identity(_py(proj / ".venv")),
        "B": identity(_py(dest / "envB" / ".venv")),
        "bare": identity(_py(dest / "bare" / ".venv")),
    }
    assert envs["A"]["value"] == VALUES["A"] and envs["B"]["value"] == VALUES["B"], envs
    assert envs["bare"]["matplotlib"] is None and "pkg_error" in envs["bare"], envs["bare"]
    assert envs["A"]["prefix"] != envs["B"]["prefix"]
    record = {
        "dest": str(dest),
        "base": args.base,
        "mpl_spec": MPL_SPEC,
        "envs": envs,
        "pythons": {
            "A": str(_py(proj / ".venv")),
            "B": str(_py(dest / "envB" / ".venv")),
            "bare": str(_py(dest / "bare" / ".venv")),
        },
        "sha256": {
            "proj/figure.py": sha256(proj / "figure.py"),
            f"wheels/{wa.name}": sha256(wa),
            f"wheels/{wb.name}": sha256(wb),
        },
    }
    (dest / "e1.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(record, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
