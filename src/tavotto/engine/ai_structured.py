"""无工具、单轮、只要一份结构化结果的模型调用（ADR 0178 修订 2026-11-19「接模型的地基」）。

给「从数据到图」这类**一问一答**的模型调用用：把系统提示 + 一段用户内容发给用户当前连着的 API 连接，回复必须是一个 JSON 对象，
过调用方给的校验函数才算数。**永不抛到调用方**——任何一种失败都落成闭集原因码（`REASONS`），调用方据此静默退回规则结果。

挂在哪一层：不另写 HTTP。请求构造 / 三种线上协议 / 出站传输 / 状态码分类 / 非必需字段降级 / 首轮换协议，全部复用内置执行器的
`agent_runner.loop.HttpModel`（经 `fixsession.build_model` 拿到），只是 `tools=[]`、`max_attempts=1`（传输失败不重发：本模块唯一
的重试是「回复不是合法 JSON」那一次）。出站只许 https / 回环 http、证书校验走 `tlstrust`、不跟随重定向，这些约束在 `wires.post_json`
里本来就在，本模块不碰 opener。

连接支持：
* API Key / 自填地址 / 账号登录（OAuth）这类内置执行器能直接用的连接（`ai_providers.builtin_conn`）：支持。
* 订阅登录（Codex / Claude Code CLI）：没有 Key 连接时走它们的无工具单轮入口（`AgentDefinition.build_structured_command`：
  只读沙盒 / 无工具、空临时工作目录、不落会话、不读用户的 CLI 配置、显式型号、用户内容只走 stdin）。适配器没覆写 = `unsupported_connection`。
  语义与 HTTP 路径同一套（同一个 `validate`、非 JSON 重试一次、总时限、永不抛）；CLI 拿不到 token / 花费，用量如实记 `usage_reported=False`。
  **真跑要订阅额度，不进 CI / 夜跑**（`scripts/dev/structured_cli_smoke.py` 只在维护者本机手工跑）。

隐私：本模块**不写日志、不发遥测**；失败的 `detail` 只是短代码（HTTP 状态、异常类型名、`not_json` 之类），不含响应原文、提示词、
地址或密钥；`StructuredResult` 里没有提示词与回复原文（成功时只有已过校验的 `value`）。CLI 路径的 stdout / stderr 只在内存里解析：
stderr 直接丢弃（`DEVNULL`），临时目录（系统提示 / 输出文件）退出后删除。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import hosted_ai
from .agent_runner.wires import Msg, RunnerFailure, Usage, post_json

#: 失败的闭集原因。调用方只按这些码决定「退回规则结果」，不展示给用户。
#: * no_connection          没有任何连接（或运维开关关了内置执行器）
#: * unsupported_connection  只有订阅登录、且它的适配器没有无工具单次调用（没覆写 `build_structured_command`）
#: * over_budget             输入超过上限（没发请求）、输出被截断（撞到最长输出），或 `max_requests` 到顶
#: * timeout                 超过总时限（含两次尝试）或网络层读超时
#: * refused                 服务端拒答 / 内容过滤
#: * invalid_output          回复不是合法 JSON（已重试一次）、没有文字、或没过校验函数
#: * auth / quota            密钥被拒 / 额度或限流
#: * transport               其余一切：网络错误、非 2xx、回复体不是预期形状、本模块内部异常
REASONS = (
    "no_connection",
    "unsupported_connection",
    "over_budget",
    "timeout",
    "refused",
    "invalid_output",
    "auth",
    "quota",
    "transport",
)

#: 第二次尝试时追加的一句固定文字（不含任何用户内容）。
RETRY_NOTICE = (
    "上一条回复不是合法的 JSON 对象。请只输出一个 JSON 对象，不要解释文字，不要 Markdown 围栏。"
)


@dataclass(frozen=True)
class Limits:
    """一次调用的上限。`max_input_chars` 数系统提示 + 用户内容的字符；`timeout_s` 是**总**时限（两次尝试共用）；
    `max_retries` 只在「回复不是合法 JSON」时用，上限 1。"""

    max_input_chars: int = 60_000
    max_output_tokens: int = 3_000
    timeout_s: float = 60.0
    max_retries: int = 1

    def __post_init__(self) -> None:
        if self.max_input_chars < 1 or self.max_output_tokens < 1 or self.timeout_s <= 0:
            raise ValueError("limits must be positive")
        if not 0 <= self.max_retries <= 1:
            raise ValueError("max_retries must be 0 or 1")


DEFAULT_LIMITS = Limits()


@dataclass(frozen=True)
class StructuredResult:
    """`ok` 时 `value` 是**已过校验**的 dict、`reason` 为空；失败时 `value` 是 None、`reason` 在 `REASONS` 里。
    用量跨两次尝试累加（失败的那次若服务端报了用量也算，照样计费）。`cost_usd` 只在服务端报了用量、且定价表认得这家官方端点 / 型号时有，
    自填地址与别的模型是 None（不是 0）。"""

    ok: bool
    value: dict | None = None
    reason: str = ""
    detail: str = ""
    attempts: int = 0
    #: 实际发出的 HTTP 请求数（在传输层数）。一次尝试里换协议 / 字段降级的恢复路径会多发请求，所以 `requests ≥ attempts`
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    usage_reported: bool = True
    elapsed_ms: int = 0
    cost_usd: float | None = None
    wire: str = ""
    #: 最后实际用的型号（兜底换回用户型号时是用户型号）
    model: str = ""
    #: True = 先用了「小型号」策略的型号、它这把 Key 用不了，又改用用户连接里原本的型号重试过（两次请求都计入 `requests` / 用量）
    fell_back: bool = False


# --------------------------------------------------------------------------- 失败归类
_TIMEOUT_MARK = "timeout"


def map_failure(exc: RunnerFailure) -> tuple[str, str]:
    """内置执行器的 `RunnerFailure` → `(本模块原因码, 短 detail)`。对 `loop.REASONS` 的每一个值都有结局（测试逐项核）。

    两种超时要认全：总时限到了是 `budget/time`；套接字读超时是 `network` + 异常类型名（`TimeoutError`）。"""
    reason, detail = exc.reason, exc.detail
    if reason == "refused":
        return "refused", detail
    if reason == "auth":
        return "auth", detail
    if reason == "quota":
        return "quota", detail
    if reason == "budget":
        if detail in (
            "requests",
            "tokens",
        ):  # tokens = 内置 AI 网关说这个任务花到上限了（`loop.classify_status`）
            return "over_budget", detail
        return "timeout", detail or "time"
    if reason == "network" and _TIMEOUT_MARK in detail.lower():
        return "timeout", detail
    return "transport", detail or reason


# --------------------------------------------------------------------------- 回复解析
def _reject_constant(_name: str):
    raise ValueError("non-finite number")


def parse_json_object(text: str) -> dict | None:
    """回复文字 → dict；不是合法 JSON 对象 = None。容忍两种常见包装：一对 Markdown 围栏、JSON 前后的几句话（从第一个 `{` 起解析一个对象）。
    不接受 NaN / Infinity。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
        t = t.strip()
    decoder = json.JSONDecoder(parse_constant=_reject_constant)
    try:
        obj = decoder.decode(t)
        return obj if isinstance(obj, dict) else None
    except (ValueError, RecursionError):
        pass
    start = t.find("{")
    if start < 0:
        return None
    try:
        obj, _end = decoder.raw_decode(t[start:])
    except (ValueError, RecursionError):
        return None
    return obj if isinstance(obj, dict) else None


# --------------------------------------------------------------------------- 连接
#: 联网刷新账号登录令牌（`ai_oauth.HTTP_TIMEOUT_S` = 30 s）/ 核 OpenAI 系地址少写 `/v1`（最多两次各 5 s）所需的剩余预算。
#: 不够就**不刷新**：这两步没法传入时限，放进线程超时后会留下继续写 provider 存储的半截副作用，所以只在预算够它跑完时才做。
REFRESH_NEEDS_S = 35.0


def resolve_connection(budget_s: float | None = None) -> tuple[dict | None, str, str]:
    """用户当前连着的、内置执行器能直接用的连接 → `(builtin_conn 形状 | None, 原因码, detail)`。

    「当前」= 连接视图里第一个可用的 Key / 账号登录连接（`ai_connections.first_key_connection`，旧版选中的接口排在前，与 AI 修复同一口径）。
    没有时看订阅登录（Codex / Claude Code CLI）：适配器支持无工具单轮 → 返回 CLI 连接描述 `{"kind": "cli", "agent", "model", "conn_id"}`（`call_structured`
    据此走 `_call_cli`）；有订阅登录但适配器不支持 → `unsupported_connection`；什么都没有 → `no_connection`。

    **时限**：挑连接本身不联网（`refresh=False`）。只对挑中的**那一条**记录、且只在 `budget_s`（剩余预算，默认 `DEFAULT_LIMITS.timeout_s`）
    ≥ `REFRESH_NEEDS_S` 时才联网刷新（账号登录令牌 `ensure_fresh` / 地址 `ensure_api_root`）；预算不够就用现有记录，令牌真过期了请求会得到 `auth`、
    调用方退回规则结果。不会顺序刷新多条连接。令牌刷不动（需重新登录）→ `no_connection` / `needs_login`。"""
    from . import ai_connections, ai_oauth, ai_providers

    budget = DEFAULT_LIMITS.timeout_s if budget_s is None else budget_s
    if ai_connections.builtin_forced() is False:
        return None, "no_connection", "builtin_disabled"
    # 「Tavotto Agent」（内置 AI）可用时排第一（维护者 2026-11-21）：拿到的是还没开任务的占位（`hosted_ai.needs_task`），真发请求前由 `_call`
    # 开任务；开不成时 `_call` 静默退到下面那条自己的 Key
    virtual = hosted_ai.virtual_record()
    hosted = ai_providers.builtin_conn(virtual) if virtual is not None else None
    if hosted is not None:
        return hosted, "", ""
    conn = ai_connections.first_key_connection(refresh=False)
    if conn is None:
        return find_cli_connection(cached_only=False)
    if budget >= REFRESH_NEEDS_S:
        rec = ai_providers.get(conn.get("provider_id"))
        if rec is not None:
            try:
                if rec.get("auth") == "oauth":
                    rec = ai_oauth.ensure_fresh(rec) or rec
                else:
                    rec = ai_providers.ensure_api_root(rec) or rec
            except ai_oauth.NeedsLogin:
                return None, "no_connection", "needs_login"
            fresh = ai_providers.builtin_conn(rec, conn.get("model"))
            if fresh is not None:
                conn = fresh
    return conn, "", ""


def fallback_connection(include_cli: bool = False) -> dict | None:
    """内置 AI 开不成任务时的退路：自己的 Key 连接（不联网刷新）；`include_cli` 再退到订阅登录（CLI，只用缓存的探测结论）；没有 = None。"""
    from . import ai_connections

    own = ai_connections.first_key_connection(refresh=False)
    if own is None and include_cli:
        own = find_cli_connection(cached_only=True)[0]
    return own


def _effort_kwargs(conn: dict, effort: str | None) -> dict:
    """想要的强度 → `HttpModel` 的 `effort` / `thinking`（经 `builtin_effort_args` 核过才有）；任何一步不确定 = 空（不发）。"""
    if conn.get(
        "effort"
    ):  # 只有手动基准的临时连接带（存储里的连接没有这个键）；协议层仍按自己认的取值过滤
        return {"effort": conn["effort"]}
    if not effort:
        return {}
    try:
        from . import ai_providers

        rec = ai_providers.get(conn.get("provider_id"))
        if rec is None:
            return {}
        args = ai_providers.builtin_effort_args(rec, effort, conn.get("model"))
    except Exception:  # noqa: BLE001 - 查不到强度支持 = 不发
        return {}
    out: dict = {}
    if "--effort" in args:
        out["effort"] = args[args.index("--effort") + 1]
    if "--thinking" in args:
        out["thinking"] = args[args.index("--thinking") + 1]
    return out


def _cost(conn: dict, usage: Usage, reported: bool) -> float | None:
    if not reported or conn.get(
        "hosted"
    ):  # 内置 AI 的成本是网关按人民币结算的（执行追踪另记），这里的美元估算不适用
        return None
    from . import ai_pricing

    vendor = ai_pricing.vendor_of(conn)
    # `cost_usd` 只收美元：人民币计价的站点（智谱 / MiniMax 国内站）当没有价，不把人民币数字当美元累加，也不换算。
    if ai_pricing.currency_of(vendor, conn.get("model")) not in (None, "USD"):
        return None
    return ai_pricing.estimate(
        vendor,
        conn.get("model"),
        input_tokens=usage.input_tokens,
        cached=usage.cached_input_tokens,
        output=usage.output_tokens,
    )


# --------------------------------------------------------------------------- 小型号 + 最低档策略（ADR 0178 2026-11-21）
#: 「这个连接身份 + 小型号不可用」的记忆（进程内，到期自动忘）：别让每张表都先白撞一次 400 / 403。仿 `ai_data_roles._NEGATIVE`，只是时长更长。
SMALL_UNAVAILABLE_TTL_S = 600.0
_SMALL_BAD: dict[str, float] = {}
_SMALL_LOCK = threading.Lock()
#: 判定「这把 Key / 这个端点用不了小型号」的失败：型号不存在 / 不认这个参数（HTTP 400 / 404 / 422，在 `transport` 里，detail = `http_<状态>`）、
#: 无权访问该型号（`auth` 里的 403）。401（密钥本身被拒）不算——换型号也一样被拒；timeout / quota / refused / invalid_output / over_budget 各有各的处理，不换型号。
_SMALL_UNUSABLE = {
    ("transport", "http_400"),
    ("transport", "http_404"),
    ("transport", "http_422"),
    ("auth", "http_403"),
}


def _small_key(conn: dict, model: str) -> str:
    # 密钥只以摘要进进程内的键（不落盘、不外传）：用户换了 Key 就是另一个身份
    fp = hashlib.sha256(str(conn.get("api_key") or "").encode("utf-8")).hexdigest()[:16]
    return f"{conn.get('base_url', '')}|{conn.get('provider_id', '')}|{fp}|{model.lower()}"


def small_plan(conn: dict) -> dict | None:
    """结构化调用想用的「小型号 + 最低档」→ `{"model", "effort"}`；不适用 = None（用连接原本的型号与强度）。

    只在 `ai_catalog.json` 的 `structured_small` 登记过的「官方端点 + 协议」上有；条件还有：Key 连接（不是账号登录）、连接没自带 `effort`
    （手动基准显式给了什么就用什么）、用户选的型号不已经是它、这个连接身份近期没被判过用不了它。"""
    if is_cli_conn(conn) or conn.get("effort") or conn.get("auth") != "key":
        return None  # 订阅登录（CLI）连接不适用：型号由 CLI 自己的连接决定
    from . import ai_catalog

    try:
        row = ai_catalog.structured_small(
            str(conn.get("base_url") or ""), str(conn.get("wire") or "")
        )
    except ai_catalog.CatalogError:  # 目录坏了 = 没有这条策略，不拖垮调用
        return None
    if row is None or str(conn.get("model") or "").strip().lower() == row["model"].lower():
        return None
    with _SMALL_LOCK:
        if _SMALL_BAD.get(_small_key(conn, row["model"]), 0.0) > time.time():
            return None
    return row


def effective_choice(conn: dict, effort: str | None = None) -> tuple[str, str]:
    """这个连接上结构化调用实际会用的 `(型号, 强度)`——缓存键用（换型号 / 强度 = 不同的判断来源）。强度对原型号是「想要的档」（实发由目录核）。"""
    plan = None if is_cli_conn(conn) else small_plan(conn)
    if plan is not None:
        return plan["model"], plan["effort"]
    return str(conn.get("model") or ""), str(conn.get("effort") or effort or "")


def mark_small_unusable(conn: dict, model: str) -> None:
    with _SMALL_LOCK:
        now = time.time()
        for k, until in list(_SMALL_BAD.items()):
            if until <= now:
                _SMALL_BAD.pop(k, None)
        _SMALL_BAD[_small_key(conn, model)] = now + SMALL_UNAVAILABLE_TTL_S


def reset_small_for_tests() -> None:
    with _SMALL_LOCK:
        _SMALL_BAD.clear()


# --------------------------------------------------------------------------- 订阅登录（CLI）连接
#: CLI 路径的总时限（两次尝试共用）。CLI 冷启动（起 node / 载入、连后端）比 HTTP 慢得多。**待本机真跑（`scripts/dev/structured_cli_smoke.py`）后校准**。
#: 前端轮询上限（30 次 × 1 秒）小于它：见 ADR 0178 与 `ai-agent-bridge.md`。
CLI_TIMEOUT_S = 60.0
#: 同一时刻最多一个结构化 CLI 进程；排队等待的上限（超过就放弃这次，静默退回规则结果）
CLI_QUEUE_MAX_S = 120.0
#: 读 CLI stdout 的内存上限（字符）；超出的丢弃（失败事件在最后，所以只丢中间没用的流水不现实——上限给宽）
CLI_MAX_STDOUT = 4_000_000
CLI_MAX_OUTPUT_FILE = 1_000_000
_CLI_SLOT = (
    threading.RLock()
)  # 可重入：`ai_data_roles.understand` 先占住，里面的 `_call_cli` 再占不会死锁


@contextlib.contextmanager
def cli_slot(timeout: float | None = None):
    """同一时刻最多起一个结构化 CLI 进程（用户连点几个文件时不同时起一堆 CLI）。拿不到 = `TimeoutError`。"""
    if not _CLI_SLOT.acquire(timeout=-1 if timeout is None else max(0.0, timeout)):
        raise TimeoutError("cli_slot")
    try:
        yield
    finally:
        _CLI_SLOT.release()


def is_cli_conn(conn: dict | None) -> bool:
    return isinstance(conn, dict) and conn.get("kind") == "cli"


def cli_identity(conn: dict) -> str:
    """缓存键里的连接身份：CLI 种类 + 型号（换型号 / 换家都换键）。"""
    return f"cli:{conn.get('agent', '')}|{conn.get('model', '')}"


def find_cli_connection(*, cached_only: bool) -> tuple[dict | None, str, str]:
    """订阅登录连接里第一个「可用且适配器支持无工具单轮」的 → `({"kind": "cli", "agent", "model", "conn_id"} | None, 原因码, detail)`。

    * 没有可用的订阅登录 = `no_connection`；有，但适配器都没覆写 `build_structured_command` / 解析不出型号 = `unsupported_connection`。
    * 型号 = 连接里已解析出的 `default_model`，没有就取它型号清单的第一项（适配器自己的目录顺序）；都没有 = 不支持（不写死型号名）。
    * `cached_only`：只看已有的能力缓存，绝不探测（同步路径用）；缓存还没有 = `no_connection`。"""
    from . import ai_agents, ai_bridge, ai_connections

    try:
        caps = ai_bridge.capabilities_if_cached() if cached_only else ai_bridge.capabilities()
    except Exception:  # noqa: BLE001 - 探测失败 = 没有连接
        caps = None
    if not caps:
        return None, "no_connection", "none"
    saw_unsupported = False
    for c in caps.get("connections") or []:
        sub = ai_connections.SUBSCRIPTIONS.get(c.get("id"))
        if sub is None or c.get("state") != "ready":
            continue
        agent = ai_agents.get_agent(sub[0])
        if agent is None or not agent.supports_structured():
            saw_unsupported = True
            continue
        model = str(c.get("default_model") or "").strip() or next(
            (str(m) for m in (c.get("models") or []) if m), ""
        )
        if not model:
            saw_unsupported = True
            continue
        return {"kind": "cli", "agent": agent.id, "model": model, "conn_id": c["id"]}, "", ""
    if saw_unsupported:
        return None, "unsupported_connection", "cli_only"
    return None, "no_connection", "none"


@dataclass(frozen=True)
class CliRun:
    """一次 CLI 进程的执行请求（`runner` 的入参；测试注入假 runner 时看它）。"""

    argv: list[str]
    env: dict
    cwd: str
    stdin: str
    timeout_s: float


@dataclass(frozen=True)
class CliProc:
    """一次 CLI 进程的结局：`launch_failed` = 起不来（没装 / 没权限）；`timed_out` = 超时已杀整个进程树。"""

    exit_code: int | None
    stdout: str = ""
    timed_out: bool = False
    launch_failed: bool = False


def _run_process(run: CliRun) -> CliProc:
    """真起进程（复用 `ai_bridge._popen` / `_terminate` / `_await_exit`：无窗口、POSIX 自成进程组、超时杀整棵树）。stderr 丢弃，stdout 只读进内存。"""
    from . import ai_bridge

    try:
        proc = ai_bridge._popen(run.argv, run.cwd, run.env, subprocess.DEVNULL, run.stdin)
    except (OSError, ValueError):
        return CliProc(None, launch_failed=True)
    chunks: list[str] = []
    size = 0

    def pump() -> None:
        nonlocal size
        try:
            assert proc.stdout is not None
            while True:
                piece = proc.stdout.read(65536)
                if not piece:
                    return
                if size < CLI_MAX_STDOUT:
                    chunks.append(piece)
                    size += len(piece)
        except (OSError, ValueError):
            return

    reader = threading.Thread(target=pump, daemon=True, name="ai-structured-stdout")
    reader.start()
    timed_out = False
    try:
        proc.wait(timeout=max(0.1, run.timeout_s))
    except subprocess.TimeoutExpired:
        timed_out = True
        ai_bridge._terminate({"proc": proc})
    finally:
        ai_bridge._await_exit({"proc": proc})  # 组里剩下的子进程也收掉
    reader.join(timeout=5)
    return CliProc(proc.returncode, "".join(chunks), timed_out)


#: CLI 自己的结构化失败字段 → 本模块闭集（`ai_agents.RUNNER_ERRORS` 的子集）。认不得的一律 `transport`
_RUNNER_REASON = {
    "agent_auth": "auth",
    "agent_quota": "quota",
}


def _read_capped(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(CLI_MAX_OUTPUT_FILE + 1)
    except OSError:
        return None


# --------------------------------------------------------------------------- 调用
def call_structured(
    *,
    system: str,
    user: str,
    validate: Callable[[dict], dict],
    limits: Limits = DEFAULT_LIMITS,
    conn: dict | None = None,
    post: Callable | None = None,
    max_requests: int | None = None,
    effort: str | None = None,
    auto_small: bool = False,
    runner: Callable[[CliRun], CliProc] | None = None,
    schema: dict | None = None,
) -> StructuredResult:
    """发一次无工具的单轮请求，要回一个过了 `validate` 的 dict。**永不抛**（`Exception` 一律落成 `transport`）。

    * `validate(obj) -> dict`：收一个 JSON 对象，合格就返回（可以是规整后的新 dict），不合格抛 `ValueError`（任何异常都算不合格）。
      形状不合格**不重试**（重发大概率还是同一个错）。
    * `conn`：`builtin_conn` 形状的连接；不给就 `resolve_connection()`。`post`：注入的传输函数（`wires.post_json` 的签名），只给测试用。
    * 上限：输入字符超限不发请求（`over_budget`）；输出被截断（撞到最长输出）→ `over_budget`、不重试；总时限（`limits.timeout_s`）两次尝试共用。
    * `max_requests`：实际 HTTP 请求数的硬上限（传输层数，含换协议 / 字段降级的恢复请求）；到顶不再发，回 `over_budget` / `requests`。
    * `effort`：想要的思考强度（`low` 等）。只有 `ai_providers.builtin_effort_args` 对「这个地址 + 这个型号」确认支持的才发（官方端点、目录核过的型号），
      其余（第三方网关、不认的型号、不支持的协议）一个字段都不发——不发对方不认的字段，也就不需要靠 400 再降级。
    * `auto_small`：**调用方显式开**（默认关；只有实测过「小型号 + 最低档不改坏」的任务才开，目前只有 `ai_data_roles` 的读懂数据）。开了且连接在 `ai_catalog.json` 的 `structured_small` 登记过的官方端点上时，改用那里给的小型号 + 最低档（见 `small_plan`）；
      这把 Key 用不了它（`_SMALL_UNUSABLE`）就记住一阵并改回用户原本的型号与强度再试一次（共用总时限与 `max_requests`）。其它连接一个字节都不变。
    * 重试：只在回复不是合法 JSON（含没有文字）时，最多 `limits.max_retries`（≤ 1）次；传输失败 / 拒答 / 鉴权失败都不重发。
    * 订阅登录（`conn` 是 `{"kind": "cli", …}`，或没给 `conn` 而用户只有订阅登录）走 `_call_cli`：`runner` 是注入的「跑进程」函数（只给测试用）；
      `schema` 是可选的 JSON Schema（给了才带 `--output-schema` / `--json-schema`；严格模式的 schema 要求在这条路径上**未实测**）。
      CLI 没法设输出 token 上限：`max_output_tokens` 只用来给回复长度设宽松上界；`max_requests` 数的是起的进程数。
    """
    t0 = time.monotonic()
    try:
        return _call(
            system,
            user,
            validate,
            limits,
            conn,
            post,
            t0,
            max_requests,
            effort,
            auto_small,
            runner,
            schema,
        )
    except Exception:  # noqa: BLE001 - 契约：永不抛；原因里不放异常消息（可能含提示词 / 地址）
        return StructuredResult(False, reason="transport", detail="internal", elapsed_ms=_ms(t0))


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _call(
    system: str,
    user: str,
    validate: Callable[[dict], dict],
    limits: Limits,
    conn: dict | None,
    post: Callable | None,
    t0: float,
    max_requests: int | None,
    effort: str | None = None,
    auto_small: bool = False,
    runner: Callable[[CliRun], CliProc] | None = None,
    schema: dict | None = None,
) -> StructuredResult:
    if len(system) + len(user) > limits.max_input_chars:
        return StructuredResult(False, reason="over_budget", detail="input", elapsed_ms=_ms(t0))
    if conn is None:
        conn, why, detail = resolve_connection(limits.timeout_s)
        if conn is None:
            return StructuredResult(False, reason=why, detail=detail, elapsed_ms=_ms(t0))
        if time.monotonic() - t0 >= limits.timeout_s:  # 解析连接已经用完总时限：不发请求
            return StructuredResult(False, reason="timeout", detail="resolve", elapsed_ms=_ms(t0))
    if is_cli_conn(conn):
        return _call_cli(
            system, user, validate, limits, conn, t0, max_requests, effort, runner, schema
        )
    if not hosted_ai.needs_task(conn):
        return _call_with_conn(system, user, validate, limits, conn, post, t0, max_requests, effort)
    # Tavotto Agent（内置 AI）：先开一个任务（联网一次），用它的通行证与网关下发的上限发请求，结束（任何结局）就关。
    # 开不成 = 安静的失败结局（调用方本来就静默退回规则结果），不抛、不卡
    lease = hosted_ai.lease("data_roles")
    if not lease.ok:
        own = fallback_connection(include_cli=True)
        if own is not None:  # 静默退到自己的 Key / 订阅登录
            if is_cli_conn(own):
                return _call_cli(
                    system, user, validate, limits, own, t0, max_requests, effort, runner, schema
                )
            return _call_with_conn(
                system, user, validate, limits, own, post, t0, max_requests, effort
            )
        return StructuredResult(
            False,
            reason=hosted_ai.structured_reason(lease.error),
            detail=lease.error.code if lease.error else "network",
            elapsed_ms=_ms(t0),
        )
    try:
        granted = lease.conn()
        if granted is None:
            return StructuredResult(
                False, reason="transport", detail="internal", elapsed_ms=_ms(t0)
            )
        return _call_with_conn(
            system, user, validate, limits, granted, post, t0, max_requests, effort
        )
    finally:
        lease.close()


def _call_with_conn(
    system: str,
    user: str,
    validate: Callable[[dict], dict],
    limits: Limits,
    conn: dict,
    post: Callable | None,
    t0: float,
    max_requests: int | None,
    effort: str | None = None,
) -> StructuredResult:
    from dataclasses import replace

    from . import fixsession

    lim = conn.get("limits")
    if lim:  # 内置 AI 任务（网关下发的上限，比默认紧）：输出长度与请求数取小，网关那边同样会卡一遍
        limits = replace(
            limits, max_output_tokens=min(limits.max_output_tokens, lim["max_output_tokens"])
        )
        max_requests = (
            lim["max_turns"] if max_requests is None else min(max_requests, lim["max_turns"])
        )

    sent = [
        0
    ]  # 传输层的真实请求数（`HttpModel.complete` 在换协议 / 字段降级时一次尝试可发多个请求）
    inner = post if post is not None else post_json

    def counted(url, headers, body, *, timeout, max_bytes=None):
        if max_requests is not None and sent[0] >= max_requests:
            raise RunnerFailure("budget", "requests")
        sent[0] += 1
        if max_bytes is None:
            return inner(url, headers, body, timeout=timeout)
        return inner(url, headers, body, timeout=timeout, max_bytes=max_bytes)

    deadline = t0 + limits.timeout_s  # 总时限从调用开始算：解析连接（可能联网刷新令牌）也在里面
    total = Usage()
    reported = True
    attempts = 0
    costs: list[float | None] = []  # 每条路线（型号）各按自己的定价估一次再相加
    max_chars = (
        limits.max_output_tokens * 8
    )  # 一个 token 至多 8 个字符的宽松上界：防不守 max_tokens 的端点回一大坨
    wire_name = ""

    def add(u: Usage | None, part: Usage) -> None:
        nonlocal reported
        if u is None:
            return
        for acc in (total, part):
            acc.input_tokens += u.input_tokens
            acc.output_tokens += u.output_tokens
            acc.cached_input_tokens += u.cached_input_tokens
        reported = reported and u.reported

    def usage_fields(c: dict, fell_back: bool) -> dict:
        cost: float | None = None
        if reported and costs and all(x is not None for x in costs):
            cost = sum(x for x in costs if x is not None)
        return {
            "attempts": attempts,
            "requests": sent[0],
            "input_tokens": total.input_tokens,
            "output_tokens": total.output_tokens,
            "cached_input_tokens": total.cached_input_tokens,
            "usage_reported": reported,
            "elapsed_ms": _ms(t0),
            "cost_usd": cost,
            "wire": wire_name,
            "model": str(c.get("model") or ""),
            "fell_back": fell_back,
        }

    def run_route(c: dict, ekw: dict, fell_back: bool) -> StructuredResult:
        """一条路线（一个型号）：至多 `1 + max_retries` 次尝试，只在回复不是合法 JSON 时重发。"""
        nonlocal attempts, wire_name
        model, _redactor = fixsession.build_model(
            c,
            request_timeout=limits.timeout_s,
            max_attempts=1,
            retry_base=0.0,
            post=counted,
            **ekw,
        )
        wire_name = model.wire.name
        part = Usage()
        history = [Msg("user", user)]
        n = 0
        max_attempts = 1 + limits.max_retries

        def fail(reason: str, detail: str) -> StructuredResult:
            costs.append(_cost(c, part, reported))
            return StructuredResult(
                False, reason=reason, detail=detail, **usage_fields(c, fell_back)
            )

        while n < max_attempts:
            n += 1
            attempts += 1
            try:
                turn = model.complete(
                    system=system,
                    tools=[],
                    history=history,
                    max_output_tokens=limits.max_output_tokens,
                    cancel=threading.Event(),
                    deadline=deadline,
                )
            except RunnerFailure as exc:
                add(exc.usage, part)
                reason, detail = map_failure(exc)
                return fail(reason, detail)
            add(turn.usage, part)
            if turn.stop == "max_tokens":
                return fail("over_budget", "output")
            if turn.stop == "tool_use":
                return fail("invalid_output", "tool_call")
            if len(turn.text) > max_chars:
                return fail("over_budget", "output")
            obj = parse_json_object(turn.text)
            if obj is None:
                if n < max_attempts:
                    history = [*history, Msg("assistant", turn=turn), Msg("user", RETRY_NOTICE)]
                    continue
                return fail("invalid_output", "not_json")
            try:
                value = validate(obj)
            except Exception:  # noqa: BLE001 - 校验函数说「不合格」的方式不限
                return fail("invalid_output", "shape")
            if not isinstance(value, dict):
                return fail("invalid_output", "shape")
            costs.append(_cost(c, part, reported))
            return StructuredResult(True, value=value, **usage_fields(c, fell_back))
        return fail("invalid_output", "not_json")  # 不可达（循环内必返回或继续）；兜底给闭集结局

    plan = small_plan(conn) if auto_small else None
    if plan is None:
        return run_route(conn, _effort_kwargs(conn, effort), False)
    small_conn = {**conn, "model": plan["model"], "effort": plan["effort"]}
    res = run_route(small_conn, {"effort": plan["effort"]}, False)
    if res.ok or (res.reason, res.detail) not in _SMALL_UNUSABLE:
        return res
    # 这把 Key / 这个端点用不了小型号：记住一阵，并在同一总时限与请求预算内改用用户连接里原本的型号与强度再来一次
    mark_small_unusable(conn, plan["model"])
    if time.monotonic() >= deadline or (max_requests is not None and sent[0] >= max_requests):
        return res
    return run_route(conn, _effort_kwargs(conn, effort), True)


def _call_cli(
    system: str,
    user: str,
    validate: Callable[[dict], dict],
    limits: Limits,
    conn: dict,
    t0: float,
    max_requests: int | None,
    effort: str | None,
    runner: Callable[[CliRun], CliProc] | None,
    schema: dict | None,
) -> StructuredResult:
    """订阅登录路径：与 `_call` 同一套语义（同一个 `validate`、非 JSON 重试一次、总时限、不抛），每次尝试起一个**新**的 CLI 进程
    （不落会话，没有「接着上一轮」，所以重试时把固定的那句话接在用户内容后，不带上一次的回复）。

    失败归类只认 CLI 自己的结构化字段（`parse_structured_output`）：auth / quota 来自它们；其余非零退出 = `transport`；起不来 = `no_connection`；
    空输出 / 非 JSON / 没过校验 = `invalid_output`；超时杀整个进程树 = `timeout`。stdout / stderr 不落盘、不进日志。"""
    from . import ai_agents

    agent = ai_agents.get_agent(str(conn.get("agent") or ""))
    do_run = runner or _run_process
    deadline = t0 + limits.timeout_s
    max_attempts = 1 + limits.max_retries
    max_chars = limits.max_output_tokens * 8
    attempts = 0
    started = 0

    def fields() -> dict:
        return {
            "attempts": attempts,
            "requests": started,
            "usage_reported": False,  # CLI 拿不到 token / 花费
            "elapsed_ms": _ms(t0),
            "wire": f"cli:{conn.get('agent', '')}",
            "model": str(conn.get("model") or ""),
        }

    def fail(reason: str, detail: str) -> StructuredResult:
        return StructuredResult(False, reason=reason, detail=detail, **fields())

    if agent is None or not agent.supports_structured():
        return fail("unsupported_connection", "cli_only")
    res = ai_agents.resolve(agent, probe_readiness=False)
    if res.argv is None:
        return fail("no_connection", "not_installed")
    env = ai_agents.spawn_env(res.path)
    prompt = user
    try:
        with cli_slot(max(0.0, deadline - time.monotonic())):
            while attempts < max_attempts:
                left = deadline - time.monotonic()
                if left <= 0:
                    return fail("timeout", "time")
                if max_requests is not None and started >= max_requests:
                    return fail("over_budget", "requests")
                attempts += 1
                tmp = tempfile.mkdtemp(prefix="tavotto-sc-")
                try:
                    cwd = os.path.join(tmp, "work")  # 空目录：模型工作区里什么都没有
                    aux = os.path.join(tmp, "aux")  # 系统提示 / schema / 输出文件在这里，不在工作区
                    os.mkdir(cwd)
                    os.mkdir(aux)
                    spec = agent.build_structured_command(
                        ai_agents.StructuredRunContext(
                            argv=list(res.argv),
                            system=system,
                            user=prompt,
                            model=str(conn["model"]),
                            cwd=cwd,
                            aux_dir=aux,
                            effort=effort,
                            schema=schema,
                        )
                    )
                    if spec is None:
                        return fail("unsupported_connection", "cli_only")
                    for path, text in spec.files.items():
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(text)
                    started += 1
                    proc = do_run(
                        CliRun(list(spec.argv), {**env, **spec.env}, cwd, spec.stdin, left)
                    )
                    if proc.launch_failed:
                        return fail("no_connection", "launch")
                    if proc.timed_out:
                        return fail("timeout", "time")
                    out_text = _read_capped(spec.output_file) if spec.output_file else None
                    reply = agent.parse_structured_output(
                        proc.stdout, out_text, proc.exit_code if proc.exit_code is not None else -1
                    )
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                if reply.failure is not None:
                    return fail(_RUNNER_REASON.get(reply.failure, "transport"), reply.failure)
                if proc.exit_code != 0:
                    return fail("transport", "exit")
                text = reply.text
                if text is not None and len(text) > max_chars:
                    return fail("over_budget", "output")
                obj = parse_json_object(text) if text else None
                if obj is None:
                    if attempts < max_attempts:
                        prompt = f"{user}\n\n{RETRY_NOTICE}"
                        continue
                    return fail("invalid_output", "not_json" if text else "empty")
                try:
                    value = validate(obj)
                except Exception:  # noqa: BLE001 - 校验函数说「不合格」的方式不限
                    return fail("invalid_output", "shape")
                if not isinstance(value, dict):
                    return fail("invalid_output", "shape")
                return StructuredResult(True, value=value, **fields())
    except TimeoutError:
        return fail("timeout", "queue")
    return fail("invalid_output", "not_json")  # 不可达；兜底给闭集结局
