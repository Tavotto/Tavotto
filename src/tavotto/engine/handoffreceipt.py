"""交接三态的「渲染结论」通道（ADR 0131 第六节，纯标准库）。

`tavotto open` 的 `ok` 只说「解析、登记、唤起成功」，不说图画出来没有。要诚实地回答
「渲染了吗」，只能去看**桌面端本来就会做的那一次渲染**的结果（不为验证去执行用户脚本）。
确认信号是一次**经认证的 HTTP 查询**：

    实例（sidecar / 浏览器模式服务端）
      `/api/engine/render` 收尾处把结论记进进程内的有界结论表（`LEDGER`，零 IO、旁路）
      `GET /api/handoff/render-status?project=&stem=&since=` 返回 `since` 之后该项目 + stem 的
      **第一份**结论，没有则 `{"status": "pending"}`
    CLI（`handoff.open_target`）
      唤起前记下 `since`；唤起后有界轮询：发现凭据 → 带凭据 GET → ok / failed / 到点 unverified

**认证**（不开无认证端点，security.py 的 guard 照旧管 Host / Origin）：端点接受浏览器模式的完整
secret（`X-Tavotto-Auth`，`port-<端口>.json`），以及桌面 sidecar 发布的 **scoped 只读 token**
（`handoff-status-<端口>.json`，只对这一个端点有效，见 `engine/session_client.py`）。
先曾考虑文件回执，维护者 2026-10-16 改为 HTTP 查询：文件通道要自带期望文件、写锁、清理，
且与 HTTP 会话认证是两套信任面；这里复用同一道 guard，first-wins 由时间序保证，不需要锁。

**已知盲区（未验证）**：CLI 若跑在 MSIX 打包进程（Codex Store 版）里，对 `%LOCALAPPDATA%` 的**读**预计会
合并到真实路径（读重定向通常合并），能读到桌面写的 scoped 凭据；但仍未在 Windows 真机验证。
读不到 = `unverified/channel_unavailable`（不误报，但没有信号）。

约束（每条都有用例）：
  * 结论表有界：最多 `MAX_ENTRIES` 条、每条最多保留 `TTL_S` 秒；
  * `since` 之前的结论不返回；同一项目 + stem 的第一份赢；项目 / stem 之间互相隔离；
  * 响应只有 status / code / stem / registry_match / manifest_elements，**不回显路径、不含 traceback**；
  * 等待预算必须是有限非负数：`nan` / `inf` / 负数一律按 0（不等）处理，CLI 入口在 argparse 层就拒绝；
  * CLI 不往磁盘写任何东西；只连 127.0.0.1，每次请求超时 <= `REQUEST_TIMEOUT_S`；
  * 带凭据的查询**绝不经过代理**：`http.client.HTTPConnection` 直连，不读 `HTTP_PROXY` / 系统代理设置。
"""

from __future__ import annotations

import collections
import http.client
import json
import math
import os
import re
import threading
import time
import urllib.parse

from . import config, session_client

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_UNVERIFIED = "unverified"
STATUS_PENDING = "pending"
#: `render.reason` 的闭集（status == unverified 时给出，说清为什么没法下结论）
REASON_NOT_WAITED = "not_waited"
REASON_TIMEOUT = "timeout"
REASON_CHANNEL_UNAVAILABLE = "channel_unavailable"
#: 目录级 / 多 Figure 交接：没有「目标 stem」，不存在「那一次渲染」可看
REASON_NO_TARGET_STEM = "no_target_stem"
#: 登记结果说这张图不可参数化（无脚本）：界面不会为它发起引擎渲染，等也是白等
REASON_NOT_RENDERABLE = "not_renderable"
REASONS = (
    REASON_NOT_WAITED,
    REASON_TIMEOUT,
    REASON_CHANNEL_UNAVAILABLE,
    REASON_NO_TARGET_STEM,
    REASON_NOT_RENDERABLE,
)
#: 实例报「渲染失败」却没给合法 code（或给了不合规的串）时用的稳定码
CODE_RENDER_FAILED = "render_failed"
CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

#: 结论表容量与保留时长（几个标量，没有交接时也记，零 IO）
MAX_ENTRIES = 128
TTL_S = 600.0
#: CLI 的 `since` 回拨余量（秒）：CLI 与实例同机同钟，但「唤起前记下 since」与「实例渲染完成」之间
#: 可能被调度打乱几百毫秒；留 2 秒避免把刚好早一点点的目标渲染漏掉。代价：2 秒内同一 stem 的上一次渲染
#: 也会被算作这次的结论，`verified` 还要求 stem 一致 + 注册表有 + manifest 非空，不会凭空放大
SINCE_SKEW_S = 2.0
#: 单次 HTTP 查询的超时（秒）与轮询间隔
REQUEST_TIMEOUT_S = 1.0
POLL_INTERVAL_S = 0.25
#: 响应体上限：只有几个标量
MAX_RESPONSE_BYTES = 4096


def finite_budget(value: float) -> float:
    """等待预算归一：非有限（nan / inf）或负数 = 0（不等），绝不让轮询无界。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) and v > 0 else 0.0


def _identity(project: str) -> str:
    """项目身份串：与 `pool._norm_dir` / `app._project_id` 同一份大小写判断。"""
    return config.normalize_path_identity(os.path.realpath(project))


def _safe_code(code) -> str:
    return code if isinstance(code, str) and CODE_RE.match(code) else CODE_RENDER_FAILED


# ------------------------------- 实例侧 ------------------------------------
class RenderLedger:
    """最近的渲染结论：有界、带过期、线程安全。键 = (项目身份, stem)。"""

    def __init__(self, max_entries: int = MAX_ENTRIES, ttl_s: float = TTL_S) -> None:
        self._max = max_entries
        self._ttl = ttl_s
        self._entries: collections.deque = collections.deque(maxlen=max_entries)
        self._seq = 0
        self._lock = threading.Lock()

    def _prune_locked(self, now: float) -> None:
        while self._entries and now - self._entries[0]["at"] > self._ttl:
            self._entries.popleft()

    def record(self, project: str, stem: str, result: dict, *, now=time.time) -> None:
        ident = _identity(project)
        t = now()
        with self._lock:
            self._prune_locked(t)
            self._seq += 1
            self._entries.append(
                {"seq": self._seq, "project": ident, "stem": stem, "at": t, "result": result}
            )

    def first_since(self, project: str, stem: str, since: float, *, now=time.time) -> dict | None:
        """`since` 之后（含）该项目 + stem 的第一份结论；没有 None。"""
        ident = _identity(project)
        t = now()
        with self._lock:
            self._prune_locked(t)
            hits = [
                e
                for e in self._entries
                if e["project"] == ident and e["stem"] == stem and e["at"] >= since
            ]
        return min(hits, key=lambda e: e["seq"])["result"] if hits else None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


LEDGER = RenderLedger()


def record_ok(
    project: str, stem: str, *, registry_match: bool, manifest_elements: int, now=time.time
) -> None:
    LEDGER.record(
        project,
        stem,
        {
            "status": STATUS_OK,
            "stem": stem,
            "registry_match": bool(registry_match),
            "manifest_elements": int(manifest_elements),
        },
        now=now,
    )


def record_failed(project: str, stem: str, code: str, *, now=time.time) -> None:
    LEDGER.record(project, stem, {"status": STATUS_FAILED, "code": _safe_code(code)}, now=now)


def query(project: str, stem: str, since: float, *, now=time.time) -> dict:
    """端点的响应体：只含公开字段（不回显路径）。"""
    got = LEDGER.first_since(project, stem, since, now=now)
    return dict(got) if got is not None else {"status": STATUS_PENDING}


# ------------------------------- CLI 侧 ------------------------------------
def unverified(reason: str, waited_ms: int = 0) -> dict:
    """没法下结论（不是失败）：`launch.render` 的 unverified 形状只此一处。"""
    return {
        "status": STATUS_UNVERIFIED,
        "reason": reason,
        "verified": False,
        "waited_ms": waited_ms,
    }


def clean_result(res) -> dict | None:
    """把端点响应净化成 ok / failed 两种形状；pending / 不合规 None。"""
    if not isinstance(res, dict):
        return None
    status = res.get("status")
    if status == STATUS_OK:
        count = res.get("manifest_elements")
        return {
            "status": STATUS_OK,
            "stem": res.get("stem") if isinstance(res.get("stem"), str) else None,
            "registry_match": res.get("registry_match") is True,
            "manifest_elements": count
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0
            else 0,
        }
    if status == STATUS_FAILED:
        return {"status": STATUS_FAILED, "code": _safe_code(res.get("code"))}
    return None


def summarize(stem: str, got: dict | None, waited_ms: int, *, reason: str) -> dict:
    """把结论收成 `launch.render`。

    `verified` 是 `verified_success` 的**唯一判据**（插件只消费它，不另写一份）：
    渲染成功、结论里的 stem 与目标一致、注册表里确有这个 stem、manifest 非空。
    少一条都不是 verified——渲染出来了不等于渲染的是目标那张图。
    """
    if got is None:
        return unverified(reason, waited_ms)
    if got["status"] == STATUS_FAILED:
        return {
            "status": STATUS_FAILED,
            "code": got["code"],
            "verified": False,
            "waited_ms": waited_ms,
        }
    verified = (
        got["stem"] == stem and got["registry_match"] is True and got["manifest_elements"] > 0
    )
    return {
        "status": STATUS_OK,
        "verified": verified,
        "stem": got["stem"],
        "registry_match": got["registry_match"],
        "manifest_elements": got["manifest_elements"],
        "waited_ms": waited_ms,
    }


def candidates(port: int | None) -> list[tuple[int, str]]:
    """可以查询的 (端口, 凭据)：桌面的 scoped 凭据文件 + 已知浏览器端口的完整 secret。"""
    out = list(session_client.status_credentials())
    seen = {p for p, _ in out}
    if port is not None and port not in seen:
        secret = session_client.read_secret(port)
        if secret:
            out.append((port, secret))
    return out


def fetch(port: int, credential: str, project: str, stem: str, since: float) -> dict | None:
    """GET 端点。返回净化后的 ok / failed、`{"status": "pending"}`；连不上 / 被拒 / 读不懂 None。"""
    qs = urllib.parse.urlencode({"project": project, "stem": stem, "since": repr(float(since))})
    # 直连 socket 而不是 `urlopen`：后者按环境变量 / 系统设置走 HTTP 代理，`X-Tavotto-Auth`（scoped token
    # 或浏览器模式的完整 secret）会连同请求发给代理——凭据外泄。`HTTPConnection` 不读任何代理设置，
    # 这条带凭据的回环请求绝不经过代理；也不跟随重定向（3xx 当作读不懂）。
    conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=REQUEST_TIMEOUT_S)
    try:
        conn.request(
            "GET",
            f"{session_client.HANDOFF_STATUS_PATH}?{qs}",
            headers={session_client.AUTH_HEADER: credential},
        )
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        data = json.loads(resp.read(MAX_RESPONSE_BYTES).decode("utf-8", "replace"))
    except (http.client.HTTPException, OSError, ValueError):
        return None
    finally:
        conn.close()
    if isinstance(data, dict) and data.get("status") == STATUS_PENDING:
        return {"status": STATUS_PENDING}
    return clean_result(data)


def wait(
    project: str,
    stem: str,
    since: float,
    budget_s: float,
    *,
    port: int | None = None,
    clock=time.monotonic,
    sleep=time.sleep,
    interval: float = POLL_INTERVAL_S,
    find=None,
    get=None,
) -> dict:
    """有界轮询（可注入时钟，不是裸 sleep）。

    每一轮重新发现凭据——冷启动时 sidecar 在唤起之后才发布。拿到结论即返回；到点：
    至少有一个实例应答过（pending）= `unverified/timeout`；一个都没连上 = `unverified/channel_unavailable`。
    两者都**不是 failed**。
    """
    budget_s = finite_budget(budget_s)  # nan / inf / 负数按 0：查一次就收，不无界等
    find = find or candidates
    get = get or fetch
    start = clock()
    reached = False
    while True:
        for cport, cred in find(port):
            res = get(cport, cred, project, stem, since)
            if res is None:
                continue
            reached = True
            if res["status"] != STATUS_PENDING:
                return summarize(stem, res, int((clock() - start) * 1000), reason=REASON_TIMEOUT)
        elapsed_ms = int((clock() - start) * 1000)
        if clock() - start >= budget_s:
            reason = REASON_TIMEOUT if reached else REASON_CHANNEL_UNAVAILABLE
            return summarize(stem, None, elapsed_ms, reason=reason)
        sleep(interval)
