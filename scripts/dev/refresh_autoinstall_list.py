"""重新生成 `src/tavotto/engine/autoinstall_dists.txt`（「生成可编辑版本」自动安装名单，ADR 0143 修订）。

名单 = 种子（14 个科学计算包）∪ PyPI 近 N 天下载量前 K 的项目名 − 排除名单，PEP 503 规范化、去重、排序。
数据来源：ClickHouse 公开的 PyPI 数据集（`pypi.pypi_downloads_per_day`，匿名只读 SQL 端点），其源头是 PSF 在 Google BigQuery
公开的 `bigquery-public-data.pypi` 下载日志。下载次数是事实数据：我们只取项目名、自己排序生成，不分发任何第三方文件。
**开发侧工具，不进产品**：产品运行时只读生成好的文本文件，从不联网取名单。

排除名单（EXCLUDE）：管理环境 / 打包工具本身，不许被自动装进用户环境。
宁可不更新也不写坏文件：网络失败、响应过大、解析失败、行数不足 top 的一半，一律非零退出且不写文件。

用法：
    python scripts/dev/refresh_autoinstall_list.py [--top 1000] [--days 30] [--out FILE]
    python scripts/dev/refresh_autoinstall_list.py --input FILE.tsv    # 离线：两列 project<TAB>count，不联网
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = ROOT / "src" / "tavotto" / "engine" / "autoinstall_dists.txt"
ENDPOINT = "https://sql-clickhouse.clickhouse.com/?user=demo"
SOURCE_DESC = "ClickHouse 公开 PyPI 数据集 pypi.pypi_downloads_per_day（源头：PSF 在 BigQuery 公开的 bigquery-public-data.pypi）"
MAX_BODY = 4 * 1024 * 1024
TIMEOUT = 60
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
#: 常见的大包（维护者 2026-10-23 裁决「加入常见的大包」）：不管当月下载排名，一律在名单里
BIG_PACKAGES = (
    "torch",
    "torchvision",
    "torchaudio",
    "tensorflow",
    "keras",
    "jax",
    "jaxlib",
    "transformers",
    "xgboost",
    "lightgbm",
    "catboost",
    "opencv-python",
    "opencv-python-headless",
    "scikit-image",
    "plotly",
    "bokeh",
    "altair",
    "polars",
    "numba",
    "sympy",
    "networkx",
    "h5py",
    "netcdf4",
    "xarray",
    "astropy",
    "biopython",
    "pymc",
    "arviz",
    "dask",
    "tqdm",
)
#: 管理环境 / 打包工具本身：不许被自动装进用户环境（规范名）
EXCLUDE = frozenset(
    "pip setuptools wheel distribute virtualenv pipenv poetry poetry-core pdm hatch hatchling "
    "flit flit-core uv conda conda-build mamba tox nox pip-tools build twine "
    # 构建后端 / 安装器 / 原生构建工具链：都是“改环境本身”的东西（Codex #1338）
    "installer pyproject-hooks maturin pbr pbs-installer cmake ninja scikit-build scikit-build-core "
    "meson meson-python jupyter-builder nodejs-wheel-binaries pipx".split()
)
#: 再加一道前缀规则：这些工具家族的插件 / 后端（uv-build、pip-audit、setuptools-scm、hatch-vcs、poetry-plugin-*…）同样排除
EXCLUDE_PREFIXES = (
    "pip-",
    "setuptools-",
    "wheel-",
    "virtualenv-",
    "pipenv-",
    "poetry-",
    "pdm-",
    "hatch-",
    "flit-",
    "uv-",
    "conda-",
    "mamba-",
    "tox-",
    "nox-",
)


def normalize(name: str) -> str:
    return _NORMALIZE_RE.sub("-", str(name or "").strip()).lower()


#: 按主键（project）切片查询：公开端点每次查询最多读 10 亿行、默认超了就**静默截断**（read_overflow_mode=break），
#: 全表 30 天聚合要扫约 11 亿行，截断后的计数会少一大截（2026-10-23 实测 numpy 少了一半多、torch 掉出前 1000）。
#: 每片带 read_overflow_mode='throw'：超了就报错停下，而不是给一份看着正常的错数。
SHARDS = (("", "d"), ("d", "m"), ("m", "s"), ("s", None))
#: 交叉核对用的项目：切片合并后的计数必须与单独按主键查的一致，否则视为截断、不写文件
CHECK_PROJECT = "numpy"


def _query(sql: str) -> str:
    req = urllib.request.Request(ENDPOINT, data=sql.encode("utf-8"), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 —— 固定的 https 数据源
            body = resp.read(MAX_BODY + 1)
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"数据源报错（HTTP {exc.code}）：{exc.read(300).decode('utf-8', 'replace')}"
        ) from None
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"连不上数据源：{exc}——不写文件") from None
    if len(body) > MAX_BODY:
        raise SystemExit("响应超过上限，停下")
    text = body.decode("utf-8")
    if "DB::Exception" in text[:2000]:
        raise SystemExit(f"数据源报错：{text[:300]}")
    return text


def _shard_where(lo: str, hi: str | None) -> str:
    cond = f"project >= '{lo}'"
    return cond + (f" AND project < '{hi}'" if hi is not None else "")


def fetch_tsv(top: int, days: int) -> tuple[str, str]:
    """(两列 TSV 文本, 快照日期)。"""
    snap = _query("SELECT max(date) FROM pypi.pypi_downloads_per_day FORMAT TSV").strip()
    try:
        datetime.date.fromisoformat(snap)
    except ValueError:
        raise SystemExit(f"快照日期解析失败：{snap[:40]!r}") from None
    # 窗口锚定在快照日（数据源最新的完整一天），恰好 days 个日期：数据延迟或今天的半天数据都不改变窗口长度
    window = f"date > toDate('{snap}') - {int(days)} AND date <= toDate('{snap}')"
    parts = []
    for lo, hi in SHARDS:
        parts.append(
            _query(
                "SELECT project, sum(count) c FROM pypi.pypi_downloads_per_day "
                f"WHERE {_shard_where(lo, hi)} AND {window} "
                f"GROUP BY project ORDER BY c DESC LIMIT {int(top)} "
                "SETTINGS read_overflow_mode='throw' FORMAT TSV"
            )
        )
    tsv = "".join(p if p.endswith("\n") or not p else p + "\n" for p in parts)
    exact = _query(
        "SELECT sum(count) FROM pypi.pypi_downloads_per_day "
        f"WHERE project = '{CHECK_PROJECT}' AND {window} "
        "SETTINGS read_overflow_mode='throw' FORMAT TSV"
    ).strip()
    got = {line.split("\t")[0]: line.split("\t")[-1] for line in tsv.splitlines() if "\t" in line}
    if got.get(CHECK_PROJECT) != exact:
        raise SystemExit(
            f"交叉核对失败：{CHECK_PROJECT} 切片合计 {got.get(CHECK_PROJECT)} ≠ 单查 {exact}——数据源可能截断，不写文件"
        )
    return tsv, snap


def parse_tsv(text: str) -> list[str]:
    """按下载量降序的规范项目名；任何一行格式不对 = 整份拒绝。"""
    rows: list[tuple[int, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 2 or not normalize(parts[0]):
            raise SystemExit(f"TSV 行格式不对：{line[:60]!r}")
        try:
            rows.append((int(parts[1]), normalize(parts[0])))
        except ValueError:
            raise SystemExit(f"TSV 计数不是整数：{line[:60]!r}") from None
    rows.sort(key=lambda r: (-r[0], r[1]))
    return [name for _, name in rows]


def excluded(name: str) -> bool:
    """规范名是否属于不许自动安装的环境 / 打包工具（精确名单 + 家族前缀）。"""
    return name in EXCLUDE or name.startswith(EXCLUDE_PREFIXES)


def build_names(names: list[str], top: int) -> list[str]:
    merged = {normalize(n) for n in (*SEED, *BIG_PACKAGES, *names[:top])}
    return sorted(n for n in merged if n and not excluded(n))


def render(names: list[str], *, source: str, snapshot: str, days: int, top: int) -> str:
    header = [
        "# 「生成可编辑版本」允许自动安装的发行包名单（ADR 0143 修订，维护者 2026-10-23 裁决）。engine/convertdeps.py 读它。",
        "# 格式：一行一个 PEP 503 规范名；# 开头是注释；空行忽略。名单内的包才可能被自动安装，名单外的包一律先出卡片。",
        "#",
        f"# 名单 = {len(SEED)} 个科学计算种子  ∪  {len(BIG_PACKAGES)} 个常见大包  ∪  PyPI 近 {days} 天下载量前 {top} 的项目名  −  排除名单。由 scripts/dev/refresh_autoinstall_list.py 生成，别手改。",
        f"# 来源：{source}",
        f"# 窗口天数：{days}",
        f"# 快照日期：{snapshot}",
        f"# top N：{top}",
        "# 排除（管理环境 / 打包工具本身，不许被自动装进用户环境）：" + " ".join(sorted(EXCLUDE)),
        "# 另按前缀排除这些工具家族的插件 / 后端：" + " ".join(f"{p}*" for p in EXCLUDE_PREFIXES),
        f"# 条数：{len(names)}",
    ]
    return "\n".join([*header, *names]) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--top", type=int, default=1000)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--input", help="本地 TSV（project<TAB>count），给了就不联网")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    if args.top < 1 or args.days < 1:
        raise SystemExit("--top / --days 要 ≥ 1")
    if args.input:
        tsv = Path(args.input).read_text(encoding="utf-8")
        source, snapshot = f"本地文件 {Path(args.input).name}（离线）", "未知（离线输入）"
    else:
        tsv, snapshot = fetch_tsv(args.top, args.days)
        source = SOURCE_DESC
    ranked = parse_tsv(tsv)
    if len(ranked) < args.top / 2:
        raise SystemExit(f"只拿到 {len(ranked)} 行，不足 top {args.top} 的一半：不写文件")
    names = build_names(ranked, args.top)
    text = render(names, source=source, snapshot=snapshot, days=args.days, top=args.top)
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"wrote {args.out}: {len(names)} 条，快照 {snapshot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
