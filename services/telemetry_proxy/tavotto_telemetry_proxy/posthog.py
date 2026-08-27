"""**唯一**知道 PostHog 长什么样的模块。

把提供商特有的 JSON 收在这一个文件里，是为了让「换分析后端」变成重写一个
文件而不是全仓库找字符串——和仓库里 `pdfbackend/` 那条边界同一个道理。
上游契约见 https://posthog.com/docs/api/capture 。

三件事值得写下来：

* **`$geoip_disable: true`**：PostHog 默认会按请求 IP 补一堆地理属性。请求是
  代理发出去的，补出来的是机房位置而不是用户位置——既没用又容易被误读成
  「我们知道用户在哪」。显式关掉。
* **客户端 IP / UA / 头一律不转发**。PostHog 看到的是代理这台机器的请求。
  这不等于「没有任何日志」：托管方（Vercel / CDN）自己的访问日志不归我们
  控制，隐私政策里如实写着，不做我们证明不了的承诺。
* **person profile 的开关按事件类型分**（`anonymous` 参数）：
  - 产品事件默认**建** person（`identified`）。留存、活跃、漏斗这些按人算的
    分析要有 person 才完整，而这个「人」里除了一个随机 UUID 什么都没有——
    我们从不调 identify、从不写任何 person 属性。想更省钱/更彻底可以用
    `POSTHOG_PERSON_PROFILES=anonymous` 换成匿名事件，代价是部分按人建模的
    洞察会退化，**这一点没有在上游文档里被明确保证，所以默认不动**。
  - 发行量快照**永远匿名**（`$process_person_profile: false`）：它们压根不
    对应任何一个人，建出 person 只会在「有多少人」里凭空多出一个机器人。
* **去重**：每条事件带一个 `uuid`。PostHog 的公开文档**没有**承诺按它做幂等
  去重，所以定时采集器另外带一个稳定的 `snapshot_key` 属性，看板查询按它
  去重（见 docs/analytics/yc-metrics.md）。不猜，写下来。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

from .contract import SCHEMA_VERSION

#: PostHog 的**批量**摄取地址（整条 URL 从环境变量来，代码里不拼 provider 路径）。
#: 美区 https://us.i.posthog.com/batch/ ；欧区 https://eu.i.posthog.com/batch/ 。
DEFAULT_INGEST_URL = "https://us.i.posthog.com/batch/"
UPSTREAM_TIMEOUT_S = 5

#: 事件 uuid 的命名空间：让同一个 snapshot_key 每次都推出同一个 uuid，
#: 手动重跑采集器时上游**有机会**去重（但不保证，见模块开头）。
_SNAPSHOT_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


class UpstreamError(RuntimeError):
    """上游不可达或回了非 2xx。**消息里绝不带密钥，也不带事件内容。**"""


def ingest_url() -> str:
    return os.environ.get("POSTHOG_INGEST_URL") or DEFAULT_INGEST_URL


def project_key() -> str:
    return os.environ.get("POSTHOG_PROJECT_KEY") or ""


def person_profiles_mode() -> str:
    mode = (os.environ.get("POSTHOG_PERSON_PROFILES") or "identified").strip().lower()
    return mode if mode in ("identified", "anonymous") else "identified"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_event(
    event: str,
    distinct_id: str,
    properties: dict,
    *,
    anonymous: bool,
    snapshot_key: str | None = None,
) -> dict:
    """规范化事件 → PostHog 批次里的一条。**只有这里认识 `$` 开头的属性。**"""
    props = {
        **properties,
        "schema_version": SCHEMA_VERSION,
        "$geoip_disable": True,
    }
    if anonymous:
        props["$process_person_profile"] = False
    return {
        "event": event,
        "distinct_id": distinct_id,
        "properties": props,
        "timestamp": _now_iso(),
        "uuid": (
            str(uuid.uuid5(_SNAPSHOT_NS, snapshot_key)) if snapshot_key else str(uuid.uuid4())
        ),
    }


def send(events: list[dict]) -> None:
    """把一批事件交给 PostHog。失败抛 `UpstreamError`（不含密钥、不含载荷）。"""
    if not events:
        return
    key = project_key()
    if not key:
        # 没配密钥就明确失败，绝不假装成功——「一直没数据但服务是 200」
        # 是最难查的一种部署事故。
        raise UpstreamError("analytics backend is not configured")
    body = json.dumps({"api_key": key, "batch": events}).encode("utf-8")
    req = urllib.request.Request(
        ingest_url(),
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "tavotto-telemetry-proxy/1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT_S) as resp:
            resp.read(4096)
    except urllib.error.HTTPError as exc:
        # 只回状态码。上游的响应体可能把我们发过去的载荷原样回显，
        # 转发给公网调用方等于给了一面镜子。
        raise UpstreamError(f"analytics backend returned {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise UpstreamError("analytics backend unreachable") from None
