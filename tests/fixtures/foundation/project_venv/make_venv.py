"""U00 合成 fixture ④ 的构造脚本：给这个项目建一个**与应用不同 Python** 的 `.venv`。

用法（纯标准库，只跑 `python -m venv`，**绝不 pip install 任何包**）::

    python make_venv.py --python /path/to/python3.11 [--app-python /path/to/app/python]
                        [--link-host-site] [--dest DIR]

* `--python`：项目 venv 的基础解释器。夹具的意义就在于它 **≠** 应用自己用的那个
  （给了 `--app-python` 时会核对 major.minor 不同，相同就退 2 报错——「不同 Python」
  是这条夹具的前提，不成立时不能装作成立）。
* `--link-host-site`：把基础解释器自己的 site-packages 经一个 `.pth` 接进 venv，
  模拟「用户本来就装了 matplotlib」的初始状态。**这不是 harness 替产品装包**：
  一个字节都不下载、不安装，只是让基础解释器上已有的东西可见。不给这个开关时
  venv 是空的（只有 pip）——正是「项目环境缺依赖」那条场景的初始状态。
* 产物：`<dest>/.venv/` 与 `<dest>/venv_receipt.json`（解释器身份、prefix、
  是否接了宿主 site-packages、matplotlib 能否 import）。两者都在 `.gitignore` 里。

回执里的 `matplotlib_importable` 是**建完当场量的**，不是从开关推的：`--link-host-site`
接进来的宿主也可能根本没有 matplotlib，那时回执如实写 false，用例据此 skip 并说明。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECEIPT_NAME = "venv_receipt.json"

_PROBE = (
    "import json, sys\n"
    "try:\n"
    "    import matplotlib\n"
    "    mpl = matplotlib.__version__\n"
    "except Exception:\n"
    "    mpl = None\n"
    "print(json.dumps({'version': list(sys.version_info[:3]), 'prefix': sys.prefix,\n"
    "  'base_prefix': sys.base_prefix, 'executable': sys.executable, 'matplotlib': mpl,\n"
    "  'site': [p for p in sys.path if p.endswith('site-packages')]}))\n"
)


def _probe(python: str) -> dict:
    out = subprocess.run(
        [python, "-I", "-c", _PROBE], capture_output=True, text=True, check=True, timeout=60
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def build(python: str, dest: Path, *, app_python: str | None, link_host_site: bool) -> dict:
    base = _probe(python)
    if app_python:
        app = _probe(app_python)
        if app["version"][:2] == base["version"][:2]:
            print(
                f"夹具前提不成立：项目 Python {base['version'][:2]} 与应用 Python "
                f"{app['version'][:2]} 同一个 minor；换一个 --python",
                file=sys.stderr,
            )
            raise SystemExit(2)
    venv = dest / ".venv"
    subprocess.run([python, "-m", "venv", str(venv)], check=True, timeout=300)
    vpy = _venv_python(venv)
    linked: list[str] = []
    if link_host_site:
        site_dirs = [p for p in base["site"] if Path(p).is_dir()]
        purelib = subprocess.run(
            [str(vpy), "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.strip()
        pth = Path(purelib) / "_u00_fixture_host_site.pth"
        pth.write_text("\n".join(site_dirs) + "\n", encoding="utf-8")
        linked = site_dirs
    after = _probe(str(vpy))
    receipt = {
        "fixture": "project_venv",
        "venv": str(venv),
        "venv_python": str(vpy),
        "base_python": python,
        "base_version": base["version"],
        "venv_version": after["version"],
        "venv_prefix": after["prefix"],
        "venv_base_prefix": after["base_prefix"],
        "app_python": app_python,
        "linked_host_site": linked,
        "matplotlib_importable": after["matplotlib"] is not None,
        "matplotlib_version": after["matplotlib"],
        "packages_installed_by_this_script": [],
    }
    (dest / RECEIPT_NAME).write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--python", required=True, help="项目 venv 的基础解释器（≠ 应用的）")
    ap.add_argument("--app-python", default=None, help="应用自己用的解释器；给了就核对 minor 不同")
    ap.add_argument(
        "--link-host-site", action="store_true", help="把基础解释器的 site-packages 接进 venv"
    )
    ap.add_argument("--dest", default=str(HERE), help="放 .venv 与回执的目录（默认本目录）")
    args = ap.parse_args(argv)
    receipt = build(
        args.python,
        Path(args.dest).resolve(),
        app_python=args.app_python,
        link_host_site=args.link_host_site,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
