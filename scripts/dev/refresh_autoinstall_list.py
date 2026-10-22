"""重新生成 `src/tavotto/engine/autoinstall_dists.txt`（「生成可编辑版本」自动安装名单，ADR 0143 修订）。

名单 = 种子（上一轮的 14 个科学计算包）∪ PyPI 30 天下载量前 N（默认 1000）的项目名，PEP 503 规范化、去重、排序。
数据来源默认取 hugovk/top-pypi-packages 的 `top-pypi-packages-30-days.min.json`；也可以 `--input` 指一份已下载的文件。
**开发侧工具，不进产品**：产品运行时只读生成好的文本文件，从不联网取名单。

**必须显式给 `--license`**：把你核过的许可证说明写进文件头；不给就拒绝写文件。该上游仓库写这行字时没有声明任何许可证
（维护者要求“许可证不允许再分发就停下”），所以这一步留给确认过的人，不在脚本里默认通过。

用法：
    python scripts/dev/refresh_autoinstall_list.py --license "<许可证说明>" [--top 1000] [--input FILE] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = ROOT / "src" / "tavotto" / "engine" / "autoinstall_dists.txt"
SOURCE_URL = "https://hugovk.github.io/top-pypi-packages/top-pypi-packages-30-days.min.json"
#: 与 `depresolve.normalize_distribution` 同一条规则（脚本不 import 产品代码，免得依赖打包环境）
_NORMALIZE_RE = re.compile(r"[-_.]+")
SEED = (
    "numpy",
    "pandas",
    "scipy",
    "statsmodels",
    "patsy",
    "seaborn",
    "matplotlib",
    "openpyxl",
    "xlrd",
    "pyarrow",
    "scikit-learn",
    "lifelines",
    "pingouin",
    "adjusttext",
)


def normalize(name: str) -> str:
    return _NORMALIZE_RE.sub("-", str(name or "").strip()).lower()


def load_snapshot(source: str) -> dict:
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=60) as resp:  # noqa: S310 —— 固定的 https 数据源
            return json.loads(resp.read().decode("utf-8"))
    return json.loads(Path(source).read_text(encoding="utf-8"))


def top_names(snapshot: dict, top: int) -> list[str]:
    rows = snapshot.get("rows")
    if not isinstance(rows, list) or not rows:
        raise SystemExit("快照里没有 rows：数据源格式变了，停下")
    ordered = sorted(
        (r for r in rows if isinstance(r, dict) and r.get("project")),
        key=lambda r: (-int(r.get("download_count") or 0), str(r["project"])),
    )
    return [normalize(r["project"]) for r in ordered[:top]]


def render(names: list[str], *, snapshot: dict, source: str, license_note: str, top: int) -> str:
    merged = sorted({normalize(n) for n in (*SEED, *names) if normalize(n)})
    header = [
        "# 「生成可编辑版本」允许自动安装的发行包名单（ADR 0143 修订，维护者 2026-10-23 裁决）。engine/convertdeps.py 读它。",
        "# 格式：一行一个 PEP 503 规范名；# 开头是注释；空行忽略。名单内的包才可能被自动安装，名单外的包一律先出卡片。",
        "#",
        f"# 名单 = 14 个科学计算种子  ∪  PyPI 30 天下载量前 {top} 的项目名快照。由 scripts/dev/refresh_autoinstall_list.py 生成，别手改。",
        f"# 快照来源：{source}",
        f"# 快照日期：{snapshot.get('last_update', '未知')}（数据源：{snapshot.get('source', '未知')}）",
        f"# 许可证：{license_note}",
        f"# 条数：{len(merged)}",
    ]
    return "\n".join([*header, *merged]) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--license", dest="license_note", required=True, help="你核过的许可证说明（写进文件头）"
    )
    ap.add_argument("--top", type=int, default=1000)
    ap.add_argument("--input", default=SOURCE_URL, help="快照 URL 或本地文件（默认官方 URL）")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    if not args.license_note.strip():
        raise SystemExit("--license 不能为空")
    snapshot = load_snapshot(args.input)
    text = render(
        top_names(snapshot, args.top),
        snapshot=snapshot,
        source=args.input if args.input.startswith("http") else SOURCE_URL,
        license_note=args.license_note.strip(),
        top=args.top,
    )
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
