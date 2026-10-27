"""内置执行器一轮模型请求的**估算**费用（执行追踪用，ADR 0151；维护者 2026-10-27 裁决「按带版本的定价表估算」）。

只给 Tavotto 自己发请求、且服务端报了用量的那一条路（内置执行器）。第三方 CLI（Codex / Claude Code）的内部用量看不见，
不估；没有可信用量（`reported=false`，字符数保守估算）时 Token 照记、标 `estimated`，**费用不算**——拿估算的 Token 乘价格
是二次猜测。

定价表只收**从官方定价页逐条核过**的条目，每家带出处与核对日期；表里没有的厂商 / 模型（包括所有自定义端点）费用是 `None`，
不是 0。厂商只认连接的 `base_url` 与目录（`ai_catalog.json`）里预设的**逐字相同**——用户自填的网关 URL 不冒充官方计价。

已知偏差（所以永远 `estimated=True`）：Anthropic 的缓存**写入**按基础输入价算（服务端用量里没有拆出写入量）；
不含批量 / 优先级 / 数据驻留等加价档位。
"""

from __future__ import annotations

from . import exectrace

PRICING_VERSION = "2026-10-27"
CURRENCY = "USD"

#: 厂商 → 出处（核对日期即 `PRICING_VERSION`）。
SOURCES = {
    "anthropic": "https://platform.claude.com/docs/en/about-claude/pricing",
    "openai": "https://developers.openai.com/api/docs/pricing",
}

#: 目录里的预设 base_url → 计价厂商（只有这两个预设的官方端点）。
_BASE_URLS = {
    "https://api.anthropic.com": "anthropic",
    "https://api.openai.com/v1": "openai",
}

#: (厂商, 模型 id) → 每百万 Token 的价格：input（未命中缓存）/ cached（缓存命中）/ output。
#: `tiers`：按这一轮的输入 Token 数分档（Haiku 5.5：≤100k / >100k）。
TABLE: dict[tuple[str, str], dict] = {
    ("anthropic", "claude-fable-5-1"): {"input": 10.0, "cached": 0.25, "output": 50.0},
    ("anthropic", "claude-opus-5-5"): {"input": 4.0, "cached": 0.20, "output": 20.0},
    ("anthropic", "claude-sonnet-5-5"): {"input": 2.0, "cached": 0.10, "output": 10.0},
    ("anthropic", "claude-haiku-5-5"): {
        "tiers": [
            (100_000, {"input": 0.10, "cached": 0.01, "output": 0.50}),
            (None, {"input": 0.50, "cached": 0.05, "output": 2.50}),
        ]
    },
    ("openai", "gpt-5"): {"input": 1.25, "cached": 0.125, "output": 10.0},
    ("openai", "gpt-5-mini"): {"input": 0.25, "cached": 0.025, "output": 2.0},
}

exectrace.register_vocabulary("pricing_version", [PRICING_VERSION])
exectrace.register_vocabulary("provider", sorted({v for v, _ in TABLE}))
exectrace.register_vocabulary("model", sorted({m for _, m in TABLE}))


def vendor_of(endpoint) -> str | None:
    """连接 → 计价厂商。只认目录预设的官方 base_url（逐字相同，去掉末尾斜杠）。"""
    if not isinstance(endpoint, dict):
        return None
    url = str(endpoint.get("base_url") or "").rstrip("/")
    return _BASE_URLS.get(url)


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
    """美元估算；查不到价格就是 None。`input_tokens` 含缓存命中（与 `agent_runner.wires.Usage` 同一口径）。"""
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
    if reported and inp is not None and out is not None:
        amount = estimate(
            vendor_of(endpoint), model, input_tokens=inp, cached=cached or 0, output=out
        )
        if amount is not None:
            usage["cost"] = {
                "amount": amount,
                "currency": CURRENCY,
                "pricing_version": PRICING_VERSION,
            }
    return usage


def model_attributes(endpoint, model) -> dict:
    """只有定价表里认得的厂商 / 模型才进 attributes（词表闭集）；自定义端点、别的模型什么都不带。"""
    vendor = vendor_of(endpoint)
    if vendor is None or (vendor, model) not in TABLE:
        return {}
    return {"provider": vendor, "model": model}
