"""内置执行器一轮模型请求的**估算**费用（执行追踪用，ADR 0151；维护者 2026-10-27 裁决「按带版本的定价表估算」）。

只给 Tavotto 自己发请求、且服务端报了用量的那一条路（内置执行器）。第三方 CLI（Codex / Claude Code）的内部用量看不见，
不估；没有可信用量（`reported=false`，字符数保守估算）时 Token 照记、标 `estimated`，**费用不算**——拿估算的 Token 乘价格
是二次猜测。

定价表只收**从官方定价页逐条核过**的条目，每家带出处与核对日期；表里没有的厂商 / 模型（包括所有自定义端点）费用是 `None`，
不是 0。厂商只认连接的 `base_url` 与目录（`ai_catalog.json`）里预设的**逐字相同**——用户自填的网关 URL 不冒充官方计价。

币种：条目默认美元；人民币计价的站点（智谱 bigmodel.cn、MiniMax 国内站）条目带 `"currency": "CNY"`，**原样记人民币，绝不做汇率换算**——
费用对象里的 `currency` 就是条目的币种，下游（追踪词表 / OTel / 诊断包）只认 USD、CNY 两个闭集值；只会汇总美元的消费方
（`ai_structured` 的 `cost_usd`）遇到非美元条目当作没有价（None），不把人民币数字当美元加。

已知偏差（所以永远 `estimated=True`）：缓存**写入**按基础输入价算（服务端用量里没有拆出写入量）；
不含批量 / 优先级 / 数据驻留 / 缓存存储（智谱目前「限时免费」）等加价档位；按时段分价的（DeepSeek 高峰 / 低谷）表达不了，不收。
"""

from __future__ import annotations

from . import exectrace, hosted_ai

PRICING_VERSION = "2026-11-21"
CURRENCY = "USD"  # 条目没写 `currency` 时的默认币种
CURRENCIES = ("USD", "CNY")
#: 内置 AI 网关（Tavotto Agent，ADR 0200）回的权威结算：人民币、不是定价表估算，`pricing_version` 固定这个标记
GATEWAY_CURRENCY = "CNY"
GATEWAY_VERSION = "gateway"

#: 厂商 → 出处（核对日期即 `PRICING_VERSION`）。键是目录（`ai_catalog.json`）里的厂商 id 或官方端点的厂商名。
SOURCES = {
    "anthropic": "https://platform.claude.com/docs/en/about-claude/pricing",
    "openai": "https://developers.openai.com/api/docs/pricing",
    "zhipu": "https://docs.bigmodel.cn/cn/guide/start/pricing",
    "minimax": "https://platform.minimax.cn/docs/guides/pricing-paygo",
    "minimax-global": "https://platform.minimax.io/docs/guides/pricing-paygo",
}

#: 型号 id 的出处（Anthropic：官方型号页逐个核过，不按命名规律猜）。
MODEL_ID_SOURCES = {
    "anthropic": "https://platform.claude.com/docs/en/models/overview ; "
    "https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions ; "
    "https://platform.claude.com/docs/en/models/<型号>/overview",
}

#: 目录里的预设 base_url → 计价厂商（只有这些预设的官方端点）。
_BASE_URLS = {
    "https://api.anthropic.com": "anthropic",
    "https://api.openai.com/v1": "openai",
    "https://open.bigmodel.cn/api/anthropic": "zhipu",
    "https://api.minimaxi.com/anthropic": "minimax",
    "https://api.minimax.io/anthropic": "minimax-global",
}

#: 目录里有 base_url、但**没有**官方逐型号 Token 价的厂商 → 原因（`ai_pricing` 不估价，费用 None；
#: 测试要求目录每个带 base_url 的厂商不是在 `_BASE_URLS` 里就是在这里，新增厂商必须二选一）。
NOT_PRICED = {
    "deepseek": "官方价按时段分高峰 / 低谷两档（UTC 01-04、06-10 工作日高峰，价格差一倍），一轮用量里没有时段，表达不了",
    "deepseek-codex": "同 deepseek：按时段分价",
    "moonshot": "官方定价页（platform.kimi.com）价格表由前端渲染，抓不到逐型号数字；K3 的缓存写入还按 5min / 1h 分价",
    "kimi-code": "订阅制 Kimi Code，不按 Token 计价",
    "kimi-code-codex": "订阅制 Kimi Code，不按 Token 计价",
    "dashscope": "百炼价格按地域 / 部署范围分表，页面没给缓存命中单价（只写「享有折扣」），且 qwen3.7-plus 另有 256K 分档与限时折扣",
    "dashscope-coding": "订阅制 Coding Plan，不按 Token 计价",
    "zai": "官方定价页（docs.z.ai）本次核对时连不上，没有两次读数一致的数字，不收",
    "synthetic": "订阅制（月费 + 额度），额外用量的 API 价没有稳定的逐型号官方页",
    "vercel-ai-gateway": "聚合网关：同一型号可由多个上游（Anthropic / Bedrock / Vertex）供应，价格按上游与地域变化，没有单一官方价",
    "opencode-go": "订阅制 Go 套餐，不按 Token 计价",
}

#: 一个厂商里已知「目录推荐了但官方定价页没有这个型号」的 (厂商, 型号)——没有价就是没有价，不拿别的型号顶替。
UNPRICED_HINTS = {
    ("minimax", "MiniMax-M2.7"): "国内站定价页没有 M2.7 的条目",
    ("minimax-global", "MiniMax-M2.7"): "国际站定价页没有 M2.7 的条目",
}

#: OpenAI 短上下文档的上限（一轮输入 Token，含缓存命中）
OPENAI_SHORT_CONTEXT = 272_000
#: MiniMax-M3 的分档边界（定价页写「≤ 512k 输入 tokens」，按 k = 1000 取；一轮输入含缓存命中）
MINIMAX_TIER = 512_000


def _short(rates: dict) -> dict:
    """只有短上下文价的条目：超过上限的一轮没有档位可落 → `_rates` 返回 None。"""
    return {"tiers": [(OPENAI_SHORT_CONTEXT, rates)]}


def _r(input_: float, cached: float, output: float) -> dict:
    return {"input": input_, "cached": cached, "output": output}


def _cny(entry: dict) -> dict:
    return {**entry, "currency": "CNY"}


#: (厂商, 模型 id) → 每百万 Token 的价格：input（未命中缓存）/ cached（缓存命中）/ output；可选 `currency`（默认 USD）。
#: `tiers`：按这一轮的输入 Token 数分档（Haiku 5.5：≤100k / >100k）。
TABLE: dict[tuple[str, str], dict] = {
    # Anthropic（官方定价页 2026-11-21；缓存命中按每个型号自己的价：Fable 5.2 / 5.1 为 0.025x，Opus 5.5 / Sonnet 5.5 为 0.05x，
    # Fable 5 与其余型号 0.1x 或页面给的绝对价；型号 id 来自官方型号页）。Mythos 系列限量开放、已退役型号不收。
    ("anthropic", "claude-fable-5-2"): _r(8.0, 0.20, 40.0),
    ("anthropic", "claude-fable-5-1"): _r(10.0, 0.25, 50.0),
    ("anthropic", "claude-fable-5"): _r(10.0, 1.0, 50.0),
    ("anthropic", "claude-opus-5-5"): _r(4.0, 0.20, 20.0),
    ("anthropic", "claude-opus-5"): _r(5.0, 0.50, 25.0),
    ("anthropic", "claude-opus-4-8"): _r(5.0, 0.50, 25.0),
    ("anthropic", "claude-opus-4-7"): _r(5.0, 0.50, 25.0),
    ("anthropic", "claude-opus-4-6"): _r(5.0, 0.50, 25.0),
    ("anthropic", "claude-opus-4-5-20251101"): _r(5.0, 0.50, 25.0),
    ("anthropic", "claude-opus-4-5"): _r(5.0, 0.50, 25.0),  # 官方别名，指向上一行
    ("anthropic", "claude-sonnet-5-5"): _r(2.0, 0.10, 10.0),
    ("anthropic", "claude-sonnet-5"): _r(2.0, 0.20, 10.0),
    ("anthropic", "claude-sonnet-4-6"): _r(3.0, 0.30, 15.0),
    ("anthropic", "claude-sonnet-4-5-20250929"): _r(3.0, 0.30, 15.0),
    ("anthropic", "claude-sonnet-4-5"): _r(3.0, 0.30, 15.0),  # 官方别名，指向上一行
    ("anthropic", "claude-haiku-5-5"): {
        "tiers": [
            (100_000, _r(0.10, 0.01, 0.50)),
            (None, _r(0.50, 0.05, 2.50)),
        ]
    },
    ("anthropic", "claude-haiku-4-5-20251001"): _r(1.0, 0.10, 5.0),
    ("anthropic", "claude-haiku-4-5"): _r(1.0, 0.10, 5.0),  # 官方别名，指向上一行
    ("openai", "gpt-5"): _r(1.25, 0.125, 10.0),
    ("openai", "gpt-5-mini"): _r(0.25, 0.025, 2.0),
    ("openai", "gpt-5-nano"): _r(0.05, 0.005, 0.40),
    # GPT-6 / 5.6 系列：定价页分「短上下文（一轮输入 ≤ 272k）/ 长上下文」两档，这里只收核过的短上下文价；
    # 超过 272k 的一轮查不到价 = 费用 None（不是 0，也不拿短上下文价顶替）。
    ("openai", "gpt-6.2-astra"): _short(_r(8.0, 0.40, 40.0)),
    ("openai", "gpt-6.2-sol"): _short(_r(2.0, 0.10, 10.0)),
    ("openai", "gpt-6.1-sol"): _short(_r(2.0, 0.10, 10.0)),
    ("openai", "gpt-6.1-terra"): _short(_r(1.0, 0.05, 5.0)),
    ("openai", "gpt-6-astra"): _short(_r(10.0, 1.0, 50.0)),
    ("openai", "gpt-6-sol"): _short(_r(2.0, 0.20, 10.0)),
    ("openai", "gpt-6-luna"): _short(_r(0.10, 0.01, 0.50)),
    ("openai", "gpt-5.6-sol"): _short(_r(4.0, 0.40, 20.0)),
    ("openai", "gpt-5.6-terra"): _short(_r(2.0, 0.20, 12.0)),
    ("openai", "gpt-5.6-luna"): _short(_r(0.20, 0.02, 1.20)),
    # 智谱国内站（bigmodel.cn，元 / 百万 Token，1M 上下文不分档；缓存存储目前「限时免费」，不计）。
    ("zhipu", "glm-5.3"): _cny(_r(8.0, 2.0, 28.0)),
    ("zhipu", "glm-5.2"): _cny(_r(8.0, 2.0, 28.0)),
    # MiniMax 国内站（元）：标准层「永久五折」后的价；优先层（service_tier=priority，1.5x）不收。缓存写入页面没有单价。
    ("minimax", "MiniMax-M3"): _cny(
        {
            "tiers": [
                (MINIMAX_TIER, _r(2.10, 0.42, 8.40)),
                (None, _r(4.20, 0.84, 16.80)),
            ]
        }
    ),
    ("minimax", "MiniMax-M3.1-Flash"): _cny(_r(1.00, 0.10, 4.00)),
    # MiniMax 国际站（美元）：与国内站是两张独立的价目表（不是汇率换算），各按各的定价页。
    ("minimax-global", "MiniMax-M3"): {
        "tiers": [
            (MINIMAX_TIER, _r(0.30, 0.06, 1.20)),
            (None, _r(0.60, 0.12, 2.40)),
        ]
    },
    ("minimax-global", "MiniMax-M3.1-Flash"): _r(0.15, 0.015, 0.60),
}

exectrace.register_vocabulary("pricing_version", [PRICING_VERSION, GATEWAY_VERSION])
exectrace.register_vocabulary("provider", sorted({v for v, _ in TABLE} | {hosted_ai.PROVIDER_ID}))
exectrace.register_vocabulary("model", sorted({m for _, m in TABLE} | {hosted_ai.MODEL}))


def vendor_of(endpoint) -> str | None:
    """连接 → 计价厂商。只认目录预设的官方 base_url（逐字相同，去掉末尾斜杠）。"""
    if not isinstance(endpoint, dict):
        return None
    url = str(endpoint.get("base_url") or "").rstrip("/")
    return _BASE_URLS.get(url)


def currency_of(vendor: str | None, model) -> str | None:
    """条目的计价币种；表里没有这个条目就是 None。"""
    entry = TABLE.get((vendor, model)) if vendor and isinstance(model, str) else None
    return None if entry is None else entry.get("currency", CURRENCY)


def _rates(vendor: str | None, model, input_tokens: int) -> dict | None:
    entry = TABLE.get((vendor, model)) if vendor and isinstance(model, str) else None
    if entry is None:
        return None
    for limit, rates in entry.get("tiers") or [(None, entry)]:
        if limit is None or input_tokens <= limit:
            return rates
    return None


def estimate(
    vendor: str | None, model, *, input_tokens: int, cached: int, output: int
) -> float | None:
    """按条目币种（`currency_of`）估算，不换算；查不到价格就是 None。`input_tokens` 含缓存命中（与 `agent_runner.wires.Usage` 同一口径）。"""
    rates = _rates(vendor, model, input_tokens)
    if rates is None:
        return None
    cached = max(0, min(cached, input_tokens))
    return (
        (input_tokens - cached) * rates["input"]
        + cached * rates["cached"]
        + output * rates["output"]
    ) / 1_000_000


def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def usage_with_cost(event: dict, endpoint, model) -> dict | None:
    """`agent_runner` 的 `usage` 事件 → 执行追踪的 usage（`exectrace.project_usage` 再校验一遍）。"""
    if not isinstance(event, dict):
        return None
    reported = event.get("reported") is True
    inp, out, cached = (
        _count(event.get("input_tokens")),
        _count(event.get("output_tokens")),
        _count(event.get("cached_input_tokens")),
    )
    usage = {
        "source": "reported" if reported else "estimated",
        "input_tokens": inp,
        "output_tokens": out,
        "cached_input_tokens": cached,
    }
    charged = _count(event.get("charged_micro"))
    if charged is not None and hosted_ai.is_hosted(endpoint):
        # 内置 AI 网关实际结算的成本是权威值（比任何估算都准）；**只认连接确实是 Tavotto Agent 的**——第三方端点自己发的头不算数。
        # 网关回了就以它为准（用量没报也成立：钱已经花了）
        usage["cost"] = {
            "amount": charged / 1_000_000,
            "currency": GATEWAY_CURRENCY,
            "pricing_version": GATEWAY_VERSION,
            "authoritative": True,
        }
    elif reported and inp is not None and out is not None:
        amount = estimate(
            vendor_of(endpoint), model, input_tokens=inp, cached=cached or 0, output=out
        )
        if amount is not None:
            usage["cost"] = {
                "amount": amount,
                "currency": currency_of(vendor_of(endpoint), model),
                "pricing_version": PRICING_VERSION,
            }
    return usage


def model_attributes(endpoint, model) -> dict:
    """只有定价表里认得的厂商 / 模型才进 attributes（词表闭集）；自定义端点、别的模型什么都不带。"""
    if hosted_ai.is_hosted(endpoint):
        return {
            "provider": hosted_ai.PROVIDER_ID
        }  # 型号名是内部的，不进执行记录（界面会把它原样显示出来）
    vendor = vendor_of(endpoint)
    if vendor is None or (vendor, model) not in TABLE:
        return {}
    return {"provider": vendor, "model": model}
