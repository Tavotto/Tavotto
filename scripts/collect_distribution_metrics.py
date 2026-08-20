#!/usr/bin/env python3
"""发行量采集器：GitHub Releases 资产下载数 + PyPI 日下载量 + 仓库公开指标。

**和桌面遥测是两回事，别混。** 桌面遥测是「同意过的匿名安装在用什么功能」，
这里是「有多少人下载过安装包」——公开计数，不对应任何一个人，因此：
  * distinct_id 是常量（代理侧强制成 `distribution_metrics`），绝不混进用户队列；
  * 指标文档里一律叫 **distribution downloads**，不叫 users
    （一次下载可能是重装、是 CI、是同一个人换台机器）。

三件必须写下来的事：

1. **GitHub 的 `download_count` 是累计计数器**，不是「今天下了多少」。所以这里
   发的是**快照**（`download_count_total` + `observed_date`），区间下载量由看板
   在两个快照之间做差。把它当日增量直接相加，第一天就会把总量翻好几倍。
2. **自动更新包不是人下载的安装包**。`Tavotto.app.tar.gz`、`*-setup.nsis.zip`、
   `*.sig`、`latest.json` 全是更新器自己拉的；把它们算进「有多少人装过」会让
   这个数字随着老用户升级不断膨胀——那正是 YC 申请里最典型的一种虚报。
   分类规则从**真实的发布工作流**（.github/workflows/release.yml 与
   desktop-tauri.yml）推出来，不靠「.exe 就是安装包」这种直觉。
3. **资产身份是 `asset_id` 而不是文件名**。资产会被删掉重传（重打包、补签名），
   文件名会重复，asset_id 不会。历史快照因此永远可用。

用法：
    python scripts/collect_distribution_metrics.py --dry-run
    TAVOTTO_METRICS_TOKEN=… TAVOTTO_TELEMETRY_METRICS_URL=… \
        python scripts/collect_distribution_metrics.py

环境变量：
    TAVOTTO_TELEMETRY_METRICS_URL   代理的 /v1/metrics（默认 telemetry.tavotto.com）
    TAVOTTO_METRICS_TOKEN           代理的 bearer token（**只在 CI secret 里**）
    GITHUB_TOKEN                    只读，提高 API 配额（可省）

失败就**大声失败**（非零退出）：这条链路和桌面遥测相反——桌面丢事件必须无声，
定时采集器丢数据必须有人看见，否则看板会安静地缺一段而没人知道。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

REPO_OWNER = "Tavotto"
REPO_NAME = "Tavotto"
PYPI_PACKAGE = "tavotto"

GITHUB_API = "https://api.github.com"
PYPISTATS_API = "https://pypistats.org/api"
DEFAULT_METRICS_URL = "https://telemetry.tavotto.com/v1/metrics"

USER_AGENT = "tavotto-distribution-metrics/1 (+https://github.com/Tavotto/Tavotto)"
NETWORK_TIMEOUT_S = 20
SCHEMA_VERSION = 1

#: PyPIStats 只保留有限的历史窗口，而且当天的数字要等它自己跑批。每次多取
#: 几天是**自愈**：某天 GitHub Actions 没跑成（配额、宕机、密钥过期），下一次
#: 运行会把缺的那几天补上，而不需要人去手动回填。
PYPI_HEAL_DAYS = 14


class CollectError(RuntimeError):
    """采集失败。**大声失败**——定时任务红一次远好过看板缺一段没人知道。"""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _get_json(url: str, token: str | None = None) -> object:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 不回显 token；URL 里也不放密钥（它在请求头里）
        raise CollectError(f"GET {url} 失败: HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise CollectError(f"GET {url} 失败: {type(exc).__name__}") from None


# ---------------------------------------------------------------------------
# GitHub Release 资产分类
# ---------------------------------------------------------------------------
def classify_asset(name: str) -> tuple[str, str]:
    """资产文件名 → (asset_role, platform)。

    顺序是有讲究的，别按「看起来更具体」重排：
      * `.sig` / `.sha256` 必须**最先**判——`Tavotto.app.tar.gz.sig` 也以
        `.tar.gz` 结尾，先判后缀会把签名算成 sdist；
      * `Tavotto.app.tar.gz`（更新包）必须早于 `tavotto-0.8.0.tar.gz`（sdist），
        两者后缀完全一样，只有前半段能分开；
      * `*-setup.nsis.zip`（更新包）必须早于 `*Setup.exe`（安装器）。
    未知形状回 ("other", "other")：**宁可少算，也不能把不认识的东西
    悄悄算进安装量**。
    """
    lower = name.lower()

    for suffix in (".sig", ".sha256sum", ".sha256", ".asc", ".minisig"):
        if lower.endswith(suffix):
            # 平台按**被签的那个文件**判：`Tavotto.app.tar.gz.sig` 的平台
            # 信息全在被剥掉的那一段里，直接看 `.sig` 只会得到 "any"
            return "checksum", _platform_hint(lower[: -len(suffix)])
    # Tauri 更新器的载荷：更新器自己拉的，不是人点的
    if lower.endswith(".app.tar.gz"):
        return "updater", "macos"
    if lower.endswith(".nsis.zip"):
        return "updater", "windows"
    if lower == "latest.json":
        return "updater", "any"
    # 人下载的安装包
    if lower.endswith(".dmg"):
        return "installer", "macos"
    if lower.endswith((".msi", ".appimage", ".deb", ".rpm")):
        return "installer", _platform_hint(lower)
    if lower.endswith(".exe"):
        return "installer", "windows"
    # 包管理器分发
    if lower.endswith(".whl"):
        return "wheel", "any"
    if lower.startswith("codex-plugin"):
        return "plugin", "any"
    if lower.endswith((".tar.gz", ".zip")):
        return "sdist", "any"
    return "other", "other"


def _platform_hint(lower: str) -> str:
    if "macos" in lower or "darwin" in lower or lower.endswith((".dmg", ".app.tar.gz")):
        return "macos"
    if "windows" in lower or "win" in lower or lower.endswith((".exe", ".msi",
                                                               ".nsis.zip")):
        return "windows"
    if lower.endswith((".appimage", ".deb", ".rpm")) or "linux" in lower:
        return "linux"
    return "any"


def _clamp(value, maximum: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(n, maximum))


def github_release_snapshots(releases: list[dict], observed_date: str) -> list[dict]:
    """Releases 列表 → 一批 `github_release_asset_snapshot` 事件。"""
    out: list[dict] = []
    for release in releases or []:
        if not isinstance(release, dict):
            continue
        release_id = _clamp(release.get("id"), 10 ** 12)
        tag = str(release.get("tag_name") or "")[:32] or "untagged"
        for asset in release.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            asset_id = _clamp(asset.get("id"), 10 ** 12)
            role, platform = classify_asset(name)
            out.append({
                "event": "github_release_asset_snapshot",
                "properties": {
                    "release_id": release_id,
                    "release_tag": tag,
                    # 身份是 asset_id：资产被删掉重传时文件名会重复，id 不会
                    "asset_id": asset_id,
                    "asset_role": role,
                    "platform": platform,
                    # 累计计数器，不是日增量——看板做差才是区间下载量
                    "download_count_total": _clamp(asset.get("download_count"),
                                                   10 ** 9),
                    "observed_date": observed_date,
                    "snapshot_key": f"gh-asset:{asset_id}:{observed_date}",
                },
            })
    return out


def github_repo_snapshot(repo: dict, observed_date: str) -> list[dict]:
    if not isinstance(repo, dict):
        return []
    return [{
        "event": "github_repo_snapshot",
        "properties": {
            "stars": _clamp(repo.get("stargazers_count"), 10 ** 8),
            "forks": _clamp(repo.get("forks_count"), 10 ** 8),
            "observed_date": observed_date,
            "snapshot_key": f"gh-repo:{REPO_OWNER}-{REPO_NAME}:{observed_date}",
        },
    }]


# ---------------------------------------------------------------------------
# PyPI
# ---------------------------------------------------------------------------
def pypi_snapshots(payload: dict, today: str, window_days: int = PYPI_HEAL_DAYS
                   ) -> list[dict]:
    """PyPIStats overall 时间序列 → 一批 `pypi_daily_downloads` 事件。

    **只取 without_mirrors**：`with_mirrors` 把镜像同步也算进去，那个数字对
    「有多少人装过」毫无意义。即便如此，剩下的这个数里仍然混着 CI 与自动化，
    指标字典里写清楚了——它是下载量，不是用户数。

    每次都取最近 `window_days` 天而不是只取昨天：漏跑一次能自愈
    （同一天重复上报由 snapshot_key 去重）。
    """
    rows = (payload or {}).get("data") or []
    cutoff = _date(today) - timedelta(days=window_days)
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("category") != "without_mirrors":
            continue
        date = str(row.get("date") or "")
        if len(date) != 10 or date in seen:
            continue
        try:
            if _date(date) < cutoff or date > today:
                continue
        except ValueError:
            continue
        seen.add(date)
        out.append({
            "event": "pypi_daily_downloads",
            "properties": {
                "date": date,
                "downloads": _clamp(row.get("downloads"), 10 ** 9),
                "category": "without_mirrors",
                # 同一天重复上报靠它去重（看板按 snapshot_key 取一条）
                "snapshot_key": f"pypi:{PYPI_PACKAGE}:{date}",
            },
        })
    return sorted(out, key=lambda e: e["properties"]["date"])


def _date(text: str):
    return datetime.strptime(text, "%Y-%m-%d").date()


# ---------------------------------------------------------------------------
# 采集与上报
# ---------------------------------------------------------------------------
def fetch_github(token: str | None) -> tuple[list[dict], dict]:
    releases: list[dict] = []
    for page in range(1, 11):           # 有上限：不给自己写一个无限翻页
        url = f"{GITHUB_API}/repos/{REPO_OWNER}/{REPO_NAME}/releases?per_page=100&page={page}"
        batch = _get_json(url, token)
        if not isinstance(batch, list) or not batch:
            break
        releases.extend(batch)
        if len(batch) < 100:
            break
    repo = _get_json(f"{GITHUB_API}/repos/{REPO_OWNER}/{REPO_NAME}", token)
    return releases, repo if isinstance(repo, dict) else {}


def fetch_pypi() -> dict:
    payload = _get_json(f"{PYPISTATS_API}/packages/{PYPI_PACKAGE}/overall")
    return payload if isinstance(payload, dict) else {}


def collect(observed_date: str, *, github_token: str | None,
            github_json: str | None = None, pypi_json: str | None = None
            ) -> list[dict]:
    if github_json:
        data = json.loads(open(github_json, encoding="utf-8").read())
        releases, repo = data.get("releases") or [], data.get("repo") or {}
    else:
        releases, repo = fetch_github(github_token)
    pypi = json.loads(open(pypi_json, encoding="utf-8").read()) if pypi_json else fetch_pypi()

    events = github_release_snapshots(releases, observed_date)
    events += github_repo_snapshot(repo, observed_date)
    events += pypi_snapshots(pypi, observed_date)
    return events


def transmit(events: list[dict], url: str, token: str) -> None:
    body = json.dumps({"schema_version": SCHEMA_VERSION,
                       "events": events}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}",
                 "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT_S) as resp:
            resp.read(4096)
    except urllib.error.HTTPError as exc:
        # **绝不打印 token**，也不打印上游响应体（它可能回显我们发过去的东西）
        raise CollectError(f"上报失败: HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CollectError(f"上报失败: {type(exc).__name__}") from None


def summarize(events: list[dict]) -> dict:
    """给人看的汇总。**刻意把安装包与更新包分开列**——合起来的那个数
    没有任何意义，而且正是最容易被误当成「用户数」的那个。"""
    by_role: dict[str, dict] = {}
    for e in events:
        if e["event"] != "github_release_asset_snapshot":
            continue
        role = e["properties"]["asset_role"]
        slot = by_role.setdefault(role, {"assets": 0, "downloads_total": 0})
        slot["assets"] += 1
        slot["downloads_total"] += e["properties"]["download_count_total"]
    pypi = [e for e in events if e["event"] == "pypi_daily_downloads"]
    return {
        "events": len(events),
        "github_by_role": by_role,
        "github_installer_downloads_lifetime":
            by_role.get("installer", {}).get("downloads_total", 0),
        "pypi_days": len(pypi),
        "pypi_downloads_in_window": sum(e["properties"]["downloads"] for e in pypi),
        "note": "downloads != users（重装 / CI / 自动化都在里面）",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="只采集与打印，**不上报**")
    ap.add_argument("--date", default=None,
                    help="观测日期 YYYY-MM-DD（默认今天 UTC）")
    ap.add_argument("--github-json", default=None,
                    help="离线 fixture：{\"releases\": [...], \"repo\": {...}}")
    ap.add_argument("--pypi-json", default=None, help="离线 fixture：PyPIStats 响应")
    args = ap.parse_args(argv)

    observed = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    token = os.environ.get("GITHUB_TOKEN") or None

    try:
        events = collect(observed, github_token=token,
                         github_json=args.github_json, pypi_json=args.pypi_json)
    except (CollectError, OSError, ValueError) as exc:
        print(f"::error::采集失败: {exc}", file=sys.stderr)
        return 1

    summary = summarize(events)
    print(json.dumps(summary, ensure_ascii=False, indent=1))

    if args.dry_run:
        # 演练把事件本身也打出来（全是标量、无密钥），方便人工核对分类
        print(json.dumps(events, ensure_ascii=False, indent=1))
        print("* --dry-run：没有上报任何数据", file=sys.stderr)
        return 0

    url = os.environ.get("TAVOTTO_TELEMETRY_METRICS_URL") or DEFAULT_METRICS_URL
    metrics_token = os.environ.get("TAVOTTO_METRICS_TOKEN") or ""
    if not metrics_token:
        print("::error::缺少 TAVOTTO_METRICS_TOKEN（要上报就必须配）", file=sys.stderr)
        return 2
    try:
        transmit(events, url, metrics_token)
    except CollectError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    print(f"* 已上报 {len(events)} 条到 {url}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
