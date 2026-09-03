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

**但「大声」不等于「说清楚」。** 退出码一档一个原因，日志里也各自点名：

    0  落了；或者「预期内的未配置」（fork 里没有上游 secret，见 `token_situation`）
    1  采集失败（GitHub 那半边取不到）
    2  缺配置：本仓库没配 TAVOTTO_METRICS_TOKEN——**真失败**，数据在丢
    3  上报被拒（4xx）：对面明确不收这一批，重试无用。多半是线上代理落后于仓库
    4  上报未授权（401/403）：token 不对，或**代理侧**没配 token
    5  上游故障（5xx）：不是这一批的形状问题
    6  结果未知：连接中断/超时，或 200 但形状不对——**不知道落没落**，重跑安全
    7  被限流（429）：payload 是好的，**等过了 Retry-After 重试有意义**

2026-08-28 到 09-02 连红六天（issue #227）就是没分档的代价：日志上只有
「上报失败: HTTP 400」，而 400 的真正含义是「线上代理的白名单还是 d2d7187c
之前那份，不认识 `update_check` / `plugin_manifest`」。
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

CANONICAL_REPO = f"{REPO_OWNER}/{REPO_NAME}"

USER_AGENT = "tavotto-distribution-metrics/1 (+https://github.com/Tavotto/Tavotto)"
NETWORK_TIMEOUT_S = 20
SCHEMA_VERSION = 1

#: PyPIStats 只保留有限的历史窗口，而且当天的数字要等它自己跑批。每次多取
#: 几天是**自愈**：某天 GitHub Actions 没跑成（配额、宕机、密钥过期），下一次
#: 运行会把缺的那几天补上，而不需要人去手动回填。
PYPI_HEAL_DAYS = 14


# ---------------------------------------------------------------------------
# 上报的结局：一档一个名字
# ---------------------------------------------------------------------------
#: 这几档**处方完全不同**，合并任意两档都要下一个人重烧一轮才知道该改什么：
#:
#:   ACCEPTED      落了。
#:   REJECTED      代理明确拒收这一批（4xx）。重试一万次结果一样——要么 payload
#:                 变了、要么**线上代理落后于仓库**。2026-08-28 起连红六天正是
#:                 后者（issue #227）。
#:   UNAUTHORIZED  token 不对，或代理侧根本没配 token（端点是关着的）。
#:                 和「我们本地没有 token」是两件事，别混。
#:   SERVER_ERROR  对面坏了（5xx）。不是我们的 payload，等它恢复。
#:   UNKNOWN       **不知道落没落。** 见下面 `UNKNOWN` 的注释。
ACCEPTED = "accepted"
REJECTED = "rejected"
UNAUTHORIZED = "unauthorized"
#: **429 不是「重试无用」。** 它说的是「现在别来，等会儿再来」——把它并进
#: REJECTED 等于把一次限流变成一次真实的数据丢失（那一天的快照永远没了）。
#: 分档的精神就是「处方不同的必须分开」：REJECTED 的处方是改 payload / 重新
#: 部署代理，这一档的处方是**按 Retry-After 等一会儿再跑**。
RATE_LIMITED = "rate_limited"
SERVER_ERROR = "server_error"
#: **「不知道」是独立一档，不许并进相邻取值。** 连接断了、超时、或者回了 200
#: 但形状不是代理的那个形状——这三种情况下这批快照到底有没有被处理，我们没有
#: 任何证据。并进 ACCEPTED，看板会安静地缺一段；并进 REJECTED，会让人去改一个
#: 根本没坏的 payload。它自己一档，处方也只有它有：**重跑是安全的**
#: （同一天的 snapshot_key 相同，重复上报不会翻倍）。
UNKNOWN = "unknown"

#: 退出码也一档一个，好让「哪一种失败」在 workflow 的层面上就分得开。
EXIT_CODES = {
    ACCEPTED: 0,
    REJECTED: 3,
    UNAUTHORIZED: 4,
    SERVER_ERROR: 5,
    UNKNOWN: 6,
    RATE_LIMITED: 7,
}
EXIT_COLLECT_FAILED = 1
EXIT_MISSING_CONFIG = 2

#: 没有 token 时的三种处境。**它们不是同一件事**（见 `token_situation`）。
CONFIGURED = "configured"
MISSING_HERE = "missing_here"
UNCONFIGURED_FORK = "unconfigured_fork"


class CollectError(RuntimeError):
    """采集失败。**大声失败**——定时任务红一次远好过看板缺一段没人知道。

    `status` 是上游的 HTTP 状态码（没有就是 None）。**「大声失败」有一个例外**：
    数据源本身还不存在（404）不是故障，是预期内的状态，见 `fetch_pypi`。
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


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
        raise CollectError(f"GET {url} 失败: HTTP {exc.code}", status=exc.code) from None
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
      * `*-setup.nsis.zip`（更新包）必须早于 `*Setup.exe`（安装器）；
      * `codex-plugin*` 的三条必须整体早于 `.tar.gz` / `.zip` 兜底，否则
        `codex-plugin-*.zip` 会被算成 sdist。

    角色分四类，看板不能混（见 docs/analytics/yc-metrics.md）：
      * 人主动下载：`installer` / `plugin` / `wheel` / `sdist`
      * 机器自动拉取：`update_check` / `plugin_manifest` / `updater`
      * 附属文件：`checksum`
      * 不认识：`other`
    **`update_check` 与 `plugin_manifest` 是轮询次数，不是下载、更不是人。**
    未知形状回 ("other", "other")：**宁可少算，也不能把不认识的东西
    悄悄算进安装量**。
    """
    lower = name.lower()

    for suffix in (".sig", ".sha256sum", ".sha256", ".asc", ".minisig"):
        if lower.endswith(suffix):
            # 平台按**被签的那个文件**判：`Tavotto.app.tar.gz.sig` 的平台
            # 信息全在被剥掉的那一段里，直接看 `.sig` 只会得到 "any"
            return "checksum", _platform_hint(lower[: -len(suffix)])
    # 后缀之外的校验/溯源清单。`SHA256SUMS.txt` 由 release.yml 的
    # `shasum -a 256` 产出，`artifact-manifest*.json` 由 scripts/ci/
    # artifact_manifest.py 产出（role/path/sha256）。两者都以 .txt / .json
    # 结尾，**穿不过上面那个后缀循环**，2026-08-27 之前一直落在 other 里
    # 攒了 227 次。它们是供应链验证文件，任何情况下都不是产品下载。
    if lower.startswith("sha256sums") or lower.startswith("artifact-manifest"):
        return "checksum", "any"
    # Tauri 更新器的载荷：更新器自己拉的，不是人点的
    if lower.endswith(".app.tar.gz"):
        return "updater", "macos"
    if lower.endswith(".nsis.zip"):
        return "updater", "windows"
    # 更新检查**不是下载**：更新器每次启动都拉一次 latest.json，装了但
    # 从没升过级的机器也会天天贡献一次。2026-08-27 实测 updater 角色 66 次
    # 里 44 次是它。合成一个角色，「更新包下载量」就成了在线机器数的影子。
    if lower == "latest.json":
        return "update_check", "any"
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
    # Codex 插件：**manifest 与安装包必须分开**。`codex-plugin.json` 是
    # 插件宿主检查更新时拉的，2026-08-27 实测该角色 3387 次里 3382 次是它，
    # 真正下载 zip 只有 5 次——合成一个角色会把插件装机量放大近 700 倍。
    if lower == "codex-plugin.json":
        return "plugin_manifest", "any"
    if lower.startswith("codex-plugin-") and lower.endswith(".zip"):
        return "plugin", "any"
    # 其余 codex-plugin* 必须在下面 .tar.gz/.zip 兜底**之前**拦掉：漏下去会被
    # 算成 sdist，等于把插件流量混进 Python 包下载量。将来若真出了
    # codex-plugin-*.tar.gz，它会落到 other 而不是被悄悄算错。
    if lower.startswith("codex-plugin"):
        return "other", "other"
    if lower.endswith((".tar.gz", ".zip")):
        return "sdist", "any"
    return "other", "other"


def _platform_hint(lower: str) -> str:
    if "macos" in lower or "darwin" in lower or lower.endswith((".dmg", ".app.tar.gz")):
        return "macos"
    if "windows" in lower or "win" in lower or lower.endswith((".exe", ".msi", ".nsis.zip")):
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
        release_id = _clamp(release.get("id"), 10**12)
        tag = str(release.get("tag_name") or "")[:32] or "untagged"
        for asset in release.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            asset_id = _clamp(asset.get("id"), 10**12)
            role, platform = classify_asset(name)
            out.append(
                {
                    "event": "github_release_asset_snapshot",
                    "properties": {
                        "release_id": release_id,
                        "release_tag": tag,
                        # 身份是 asset_id：资产被删掉重传时文件名会重复，id 不会
                        "asset_id": asset_id,
                        "asset_role": role,
                        "platform": platform,
                        # 累计计数器，不是日增量——看板做差才是区间下载量
                        "download_count_total": _clamp(asset.get("download_count"), 10**9),
                        "observed_date": observed_date,
                        "snapshot_key": f"gh-asset:{asset_id}:{observed_date}",
                    },
                }
            )
    return out


def github_repo_snapshot(repo: dict, observed_date: str) -> list[dict]:
    if not isinstance(repo, dict):
        return []
    return [
        {
            "event": "github_repo_snapshot",
            "properties": {
                "stars": _clamp(repo.get("stargazers_count"), 10**8),
                "forks": _clamp(repo.get("forks_count"), 10**8),
                "observed_date": observed_date,
                "snapshot_key": f"gh-repo:{REPO_OWNER}-{REPO_NAME}:{observed_date}",
            },
        }
    ]


# ---------------------------------------------------------------------------
# PyPI
# ---------------------------------------------------------------------------
def pypi_snapshots(payload: dict, today: str, window_days: int = PYPI_HEAL_DAYS) -> list[dict]:
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
        out.append(
            {
                "event": "pypi_daily_downloads",
                "properties": {
                    "date": date,
                    "downloads": _clamp(row.get("downloads"), 10**9),
                    "category": "without_mirrors",
                    # 同一天重复上报靠它去重（看板按 snapshot_key 取一条）
                    "snapshot_key": f"pypi:{PYPI_PACKAGE}:{date}",
                },
            }
        )
    return sorted(out, key=lambda e: e["properties"]["date"])


def _date(text: str):
    return datetime.strptime(text, "%Y-%m-%d").date()


# ---------------------------------------------------------------------------
# 采集与上报
# ---------------------------------------------------------------------------
def fetch_github(token: str | None) -> tuple[list[dict], dict]:
    releases: list[dict] = []
    for page in range(1, 11):  # 有上限：不给自己写一个无限翻页
        url = f"{GITHUB_API}/repos/{REPO_OWNER}/{REPO_NAME}/releases?per_page=100&page={page}"
        batch = _get_json(url, token)
        if not isinstance(batch, list) or not batch:
            break
        releases.extend(batch)
        if len(batch) < 100:
            break
    repo = _get_json(f"{GITHUB_API}/repos/{REPO_OWNER}/{REPO_NAME}", token)
    return releases, repo if isinstance(repo, dict) else {}


#: 这次为什么没拿到 PyPI 数据——放进 summary，别让看的人自己猜
_pypi_skip_reason = ""


def _pypi_reason(exc: "CollectError") -> str:
    """把上游状态码翻成一句**准确**的人话。

    404 特别容易被误读成「包还没发布」。它其实是「**PyPIStats 上还没有这个包的
    统计数据**」——那家服务从 PyPI 的下载日志跑批，新发布的包要等到有下载记录、
    且当天的批跑完才会出现。包已经在 PyPI 上了也照样 404。
    写错这一句的代价是：看到 notice 的人跑去查发布链路，而发布链路好着呢。
    """
    if exc.status == 404:
        return (
            f"PyPIStats 上还没有 {PYPI_PACKAGE} 的统计数据"
            "（刚发布、或还没有下载记录时正常；它按天跑批，不是实时的）"
        )
    if exc.status == 429:
        return "PyPIStats 限流（429）"
    if exc.status:
        return f"PyPIStats 回了 HTTP {exc.status}"
    return f"PyPIStats 取不到（{exc}）"


def fetch_pypi() -> dict:
    """PyPI 日下载量。**取不到就跳过，不拖垮整次采集。**

    这一段整体是「尽力而为」，和 GitHub 那段不同，理由是**自愈窗口**：每次运行
    都重取最近 `PYPI_HEAL_DAYS` 天，所以今天漏掉的那几天明天会自动补回来
    （同一天的 `snapshot_key` 相同，重复上报不会翻倍）。为一次限流或一次
    跑批延迟就让 workflow 红、**顺带把 GitHub 那半边的发行量也丢掉**，
    代价和收益完全不成比例。

    GitHub 那段没有这个待遇：它是快照式的，漏一天就是看板上一个真实的缺口，
    而且没有任何机制会补回来。

    **但绝不静默跳过**：打一条 GitHub Actions 的 notice 说清原因，
    并把原因带进 summary。静默跳过 = 有人对着一张永远没有 PyPI 曲线的看板，
    却不知道为什么。连续多天出现同一条 notice，那才是该去查的信号。
    """
    global _pypi_skip_reason
    _pypi_skip_reason = ""
    try:
        payload = _get_json(f"{PYPISTATS_API}/packages/{PYPI_PACKAGE}/overall")
    except CollectError as exc:
        _pypi_skip_reason = _pypi_reason(exc)
        print(
            f"::notice::{_pypi_skip_reason}；本次跳过 PyPI 下载量，"
            f"GitHub 部分照常采集（最近 {PYPI_HEAL_DAYS} 天的自愈窗口会在"
            "下次运行时补上漏掉的日期）",
            file=sys.stderr,
        )
        return {}
    return payload if isinstance(payload, dict) else {}


def collect(
    observed_date: str,
    *,
    github_token: str | None,
    github_json: str | None = None,
    pypi_json: str | None = None,
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


class TransmitResult:
    """一次上报的结局。`tier` 是上面那几档之一，`message` 是给人看的那一句。"""

    __slots__ = ("tier", "status", "message", "hint")

    def __init__(self, tier: str, message: str, *, status: int | None = None, hint: str = ""):
        self.tier = tier
        self.status = status
        self.message = message
        self.hint = hint

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.tier]


def _scrub(text: object, limit: int = 200) -> str:
    """一段来自网络的字符串 → 能安全打进 CI 日志的一段。

    控制字符会伪造日志行（`::error::` 是行首指令，一个换行就能凭空捏出一条
    「错误」），所以先剥干净再截断。
    """
    if not isinstance(text, str):
        return ""
    clean = "".join(ch if ch.isprintable() else " " for ch in text).strip()
    return clean[:limit] + ("…" if len(clean) > limit else "")


def _upstream_note(raw: bytes, token: str) -> str:
    """把代理的响应体压成一句能读的话。**逐字段白名单，绝不整体回显。**

    只取 `code` / `error` / `detail` 三个字段：它们是代理自己拼出来的稳定
    标识，按设计不含任何我们发过去的内容（见 services/telemetry_proxy/
    tavotto_telemetry_proxy/core.py 的 `Rejected`）。

    原来这里**一个字节都不打**，理由写着「响应体可能回显我们发过去的东西」。
    那个顾虑对 `/v1/events` 成立，对这条链路不成立：这一批里全是公开计数
    （release id、下载数、日期），没有任何用户脚本 / 路径 / 图内文字——白名单
    在 schema 层面就让它们进不来。而代价是六天连红只看得到「HTTP 400」。

    仍然留三道闸：`TAVOTTO_TELEMETRY_METRICS_URL` 可以被指到任何地方，所以
    控制字符剥掉、每段截断、并且拿 token 查一遍——只要它出现，整句丢弃。

    **次序是这条保证的载体，不是随便排的。** token 检查必须跑在 `_scrub`
    **之前**，对着**原始响应体**做。反过来（先净化后检查）有一个真实的洞：
    `_scrub` 会在第 200 个字符处截断，如果 token 正好跨在那个边界上，被截剩的
    **token 前缀**会留在输出里，而 `token in note` 因为整枚 token 已经不完整
    而判否——净化器把证据毁掉了一部分，检查于是看不见它。实测：30 字的 token
    垫在第 190 字之后，`s3cret-met` 这 10 个字照样进了日志，而闸门说「没有
    token」。所以：**先对原文查，再净化，净化后再兜一次底。**
    """
    text = raw.decode("utf-8", errors="replace")
    if token and token in text:
        # 正常的代理不会这么干（它连收到的 token 都不区分「没带」和「带错了」），
        # 但这条断言不依赖对面的善意。查的是**没被动过的原文**。
        return "（响应体里出现了 token，已整段丢弃）"
    try:
        body = json.loads(text)
    except ValueError:
        return "（响应体不是 JSON）"
    if not isinstance(body, dict):
        return "（响应体不是 JSON 对象）"
    parts = [f"{k}={_scrub(body.get(k))}" for k in ("code", "error", "detail") if body.get(k)]
    note = " ".join(parts) or "（响应体里没有 code/error/detail）"
    if token and token in note:
        return "（响应体里出现了 token，已整段丢弃）"  # 兜底，不是主闸
    return note


def transmit(events: list[dict], url: str, token: str) -> TransmitResult:
    """把这一批送出去，并**说清楚结局是哪一档**。

    这个函数不抛异常：结局本身就是它的返回值。以前它把 4xx / 5xx / 网络中断
    统统折叠成一句 `上报失败: HTTP {code}`，于是 2026-08-28 起的六天里，日志上
    只有「HTTP 400」四个字——而 400 的真正含义是「线上代理的白名单还是旧的，
    不认识 `update_check` / `plugin_manifest` 这两个新角色」。
    """
    body = json.dumps({"schema_version": SCHEMA_VERSION, "events": events}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT_S) as resp:
            return _interpret_ok(resp.status, resp.read(8192), len(events), url)
    except urllib.error.HTTPError as exc:
        return _interpret_http_error(exc, token, url)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # **不知道落没落**：请求已经发出去了，断在哪一段无从得知。
        return TransmitResult(
            UNKNOWN,
            f"上报结果未知: {type(exc).__name__}（连接中断/超时，这批到底有没有被处理无从得知）",
            hint=(
                "重跑是安全的（同一天的 snapshot_key 相同，重复上报不会翻倍）："
                "gh workflow run telemetry-metrics.yml --ref main -f dry_run=false"
            ),
        )


def _interpret_ok(status: int, raw: bytes, sent: int, url: str) -> TransmitResult:
    """2xx 也要验形状。**「200」不等于「拿到了要的东西」。**

    指错地址（打字打错、代理域名过期被别人接管、公司网络的强制门户）都会回
    一个 200 加一坨 HTML。把它当成成功，看板就会安静地缺一段而所有灯都是绿的。
    `ok` 与 `accepted` 从代理的第一版（2026-08-20）起就在，验它们没有兼容风险。
    """
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        body = None
    if isinstance(body, dict) and body.get("ok") is True and body.get("accepted") == sent:
        return TransmitResult(ACCEPTED, f"已上报 {sent} 条到 {url}", status=status)
    return TransmitResult(
        UNKNOWN,
        (
            f"HTTP {status}，但响应不是代理的形状"
            f'（期望 {{"ok": true, "accepted": {sent}}}）——'
            f"这批到底有没有落无从得知"
        ),
        status=status,
        hint=(
            f"先确认 {url} 真的是 Tavotto 的遥测代理：curl -s {url.rsplit('/v1/', 1)[0]}/healthz"
        ),
    )


def _header(exc, name: str) -> str:
    """`HTTPError` 上取一个响应头。取不到就是空串，不炸。"""
    headers = getattr(exc, "headers", None)
    try:
        return headers.get(name) or "" if headers is not None else ""
    except (AttributeError, TypeError):
        return ""


def _interpret_http_error(exc, token: str, url: str) -> TransmitResult:
    """服务端明确回了一个状态码——**按它是谁的问题分档**。"""
    try:
        raw = exc.read(8192)
    except OSError:
        raw = b""
    note = _upstream_note(raw, token)
    base = f"{url.rsplit('/v1/', 1)[0]}/healthz"
    if exc.code in (401, 403):
        return TransmitResult(
            UNAUTHORIZED,
            f"上报未授权: HTTP {exc.code} {note}",
            status=exc.code,
            hint=(
                "token 不对，或者**代理侧**没配 TAVOTTO_METRICS_TOKEN（没配时这个端点是"
                "关着的，不是敞开的）。注意这和「本仓库没配 secret」是两件事——"
                "那种情况根本走不到发请求这一步。"
            ),
        )
    if exc.code == 429:
        # **可重试的一档。** `Retry-After` 是对面给的等待时长（秒，或 HTTP 日期），
        # 原样带出来——少了它，读日志的人只能瞎猜等多久。
        retry_after = _scrub(_header(exc, "Retry-After"), 64) or "（对面没给 Retry-After）"
        return TransmitResult(
            RATE_LIMITED,
            f"上报被限流: HTTP 429 Retry-After={retry_after} {note}",
            status=exc.code,
            hint=(
                "**这一批没丢，只是现在不能送。** 和「被拒」不同：payload 是好的，"
                "重试有意义。等过了 Retry-After 再跑一次即可"
                "（snapshot_key 去重让重跑安全）："
                "gh workflow run telemetry-metrics.yml --ref main -f dry_run=false"
            ),
        )
    if 400 <= exc.code < 500:
        return TransmitResult(
            REJECTED,
            f"上报被拒: HTTP {exc.code} {note}",
            status=exc.code,
            hint=(
                "代理明确拒收了这一批，**重试不会有任何变化**。`detail` 指的是"
                "批次里的第几条、哪个属性。最常见的成因是两侧白名单漂开了——"
                "采集器与 services/telemetry_proxy 的两张表在仓库里同步了，"
                "**不等于线上那份代理换了**（它是独立部署的服务）。"
                f"先比对指纹：curl -s {base} 的 contract.fingerprint 应当等于"
                " python -c \"import sys;sys.path.insert(0,'services/telemetry_proxy');"
                'from tavotto_telemetry_proxy.core import contract_fingerprint as f;print(f())"；'
                "对不上就先重新部署代理（services/telemetry_proxy/README.md）。"
            ),
        )
    return TransmitResult(
        SERVER_ERROR,
        f"上报失败: HTTP {exc.code} {note}",
        status=exc.code,
        hint="代理侧的故障，不是这一批的形状问题；等它恢复后重跑即可（snapshot_key 去重）。",
    )


def token_situation(token: str, repo: str | None) -> str:
    """没有 token 时，这算「预期内的未配置」还是「真失败」。

    **「不知道」不并进宽松的那一档。** 只有在**确知**自己不是
    `Tavotto/Tavotto` 时才降级成「预期内的未配置」——fork 里手动触发这个
    workflow 天经地义，它拿不到上游的 secret，不该把别人的 CI 弄红。
    仓库名取不到、或者认不出，一律按真失败处理：否则一次环境变量写错
    就能让主仓库的采集**静悄悄地绿着停掉**，而这条链路的全部意义就是
    「丢数据必须有人看见」。
    """
    if token:
        return CONFIGURED
    owner, _, name = (repo or "").partition("/")
    recognisable = bool(owner.strip()) and bool(name.strip()) and "/" not in name
    if recognisable and repo != CANONICAL_REPO:
        return UNCONFIGURED_FORK
    # 空、只有空格、`Tavotto`、`not-a-repo`……这些都不是「某个别的仓库」，
    # 而是「我们不知道自己在哪」。往 MISSING_HERE 走。
    return MISSING_HERE


#: 人主动点下来的东西。只有这一组可以叫 downloads。
HUMAN_DOWNLOAD_ROLES = ("installer", "plugin", "wheel", "sdist")
#: 机器拉的。轮询次数与载荷分开：前两个连「下载」都不算。
AUTOMATED_ROLES = ("update_check", "plugin_manifest", "updater")


def summarize(events: list[dict]) -> dict:
    """给人看的汇总。**刻意把安装包与更新包分开列**——合起来的那个数
    没有任何意义，而且正是最容易被误当成「用户数」的那个。

    2026-08-27 起再分一层：`update_check` / `plugin_manifest` 是**轮询**，
    连「下载」都不是。实测 `codex-plugin.json` 一个文件就占了旧 `plugin`
    角色的 99.85%；不拆开，「插件装机量」这个数就是错的。
    """
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
        "github_installer_downloads_lifetime": by_role.get("installer", {}).get(
            "downloads_total", 0
        ),
        # 两个口径**必须并排出现**，不然读的人会拿 github_by_role 求和
        "github_human_downloads_lifetime": sum(
            by_role.get(r, {}).get("downloads_total", 0) for r in HUMAN_DOWNLOAD_ROLES
        ),
        "github_automated_requests_lifetime": sum(
            by_role.get(r, {}).get("downloads_total", 0) for r in AUTOMATED_ROLES
        ),
        "roles_note": (
            "update_check / plugin_manifest 是轮询次数，"
            "不是下载、更不是人；绝不能进 Downloads/Users/Installs"
        ),
        "pypi_days": len(pypi),
        "pypi_downloads_in_window": sum(e["properties"]["downloads"] for e in pypi),
        # 0 天有好几种可能（统计还没出批 / 限流 / 窗口内确实没人下载），
        # 别让看的人自己猜——取不到时 fetch_pypi 会把确切原因留在这里
        "pypi_note": (_pypi_skip_reason or ("窗口内没有下载记录" if not pypi else "")),
        "note": "downloads != users（重装 / CI / 自动化都在里面）",
    }


#: 只影响 `::error title=` 那几个字，判定不看它。
_TITLES = {
    REJECTED: "被拒（对面明确不收）",
    UNAUTHORIZED: "未授权（token 不对）",
    RATE_LIMITED: "被限流（等会儿再来）",
    SERVER_ERROR: "失败（代理侧故障）",
    UNKNOWN: "结果未知（不知道落没落）",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dry-run", action="store_true", help="只采集与打印，**不上报**")
    ap.add_argument("--date", default=None, help="观测日期 YYYY-MM-DD（默认今天 UTC）")
    ap.add_argument(
        "--github-json", default=None, help='离线 fixture：{"releases": [...], "repo": {...}}'
    )
    ap.add_argument("--pypi-json", default=None, help="离线 fixture：PyPIStats 响应")
    args = ap.parse_args(argv)

    observed = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    token = os.environ.get("GITHUB_TOKEN") or None

    try:
        events = collect(
            observed, github_token=token, github_json=args.github_json, pypi_json=args.pypi_json
        )
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
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    situation = token_situation(metrics_token, repo)
    if situation == UNCONFIGURED_FORK:
        # 预期内的未配置。**说出来但不弄红**：fork 没有上游的 secret 是常态，
        # 而「这一档存在」本身要写在日志里——否则读日志的人分不清「没配」
        # 和「配了但上报失败」。
        print(
            f"::notice::{repo} 不是 {CANONICAL_REPO}，没有 TAVOTTO_METRICS_TOKEN 是"
            "预期内的：这一批不上报（采集本身已经跑通，上面的汇总就是结果）。"
            "想在自己的部署上报，配一个 secret 并把 TAVOTTO_TELEMETRY_METRICS_URL "
            "指向你自己的代理。",
            file=sys.stderr,
        )
        return 0
    if situation == MISSING_HERE:
        print(
            f"::error title=缺少配置::{CANONICAL_REPO} 上没有 TAVOTTO_METRICS_TOKEN——"
            "这是**真失败**，不是预期内的未配置：数据从这一刻起就在丢。"
            "去 Settings → Secrets and variables → Actions 配 repository secret "
            "`TAVOTTO_METRICS_TOKEN`（值 = 代理侧同名环境变量），"
            "见 services/telemetry_proxy/README.md。",
            file=sys.stderr,
        )
        return EXIT_MISSING_CONFIG

    result = transmit(events, url, metrics_token)
    if result.tier == ACCEPTED:
        print(f"* {result.message}", file=sys.stderr)
        return result.exit_code
    print(f"::error title=上报{_TITLES[result.tier]}::{result.message}", file=sys.stderr)
    if result.hint:
        print(f"::notice::{result.hint}", file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
