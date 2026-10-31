"""PostHog 看板离线 manifest（docs/analytics/posthog-manifest.json）的结构门禁。

manifest 是**离线定义**，线上什么都没建。这份测试只守它自己的结构与它对事件契约的引用：

- 引用的事件要么在 `telemetry.EVENTS`（main 上的真相），要么是代理里的发行量事件，
  要么登记在 `pending_events` 且带 `depends_on`；
- 引用的属性在该事件的白名单里，枚举过滤值在该属性的取值集合里；
- 每张图都带分析纪律要求的字段；
- 告警与订阅一律禁用、收件人 UNSET；逻辑名唯一；没有任何远端 ID。

判据的主语：被量的是 manifest 里**查询本身**（series / 过滤 / 拆分，或 HogQL 文本），
不是旁边手写的 `events` 声明；声明只用来和从查询推出的集合对拍，防止两份说法漂移。
**盲点**（写在明处）：HogQL 里无法可靠地把属性归到某个事件，所以只校验『属性名存在于所引用事件
或自动属性的并集中』以及『`properties.x = '值'` 的值在含有 x 的某个被引事件的枚举里』；
查询形状没有对照线上 PostHog schema 校验（本会话无 PostHog 访问）。
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from tavotto.engine import telemetry

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "docs" / "analytics" / "posthog-manifest.json"
PROXY_CONTRACT = ROOT / "services" / "telemetry_proxy" / "tavotto_telemetry_proxy" / "contract.py"

DISCIPLINE_FIELDS = (
    "description",
    "window",
    "denominator",
    "data_start",
    "missing_data_note",
    "filters",
    "exclusions",
    "timezone",
    "events",
    "properties",
    "title",
)


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _metrics_events() -> dict[str, dict[str, dict]]:
    """代理侧发行量事件。代理不进 wheel，缺目录时返回空（对应图的引用校验随之跳过）。"""
    if not PROXY_CONTRACT.is_file():
        return {}
    spec = importlib.util.spec_from_file_location("_proxy_contract_for_manifest", PROXY_CONTRACT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.METRICS_EVENTS


def _schema_of(manifest: dict, event: str) -> dict[str, dict] | None:
    """事件的属性表（含自动属性），按 main 契约 / 发行量契约 / 待发布事件的顺序查。"""
    if event in telemetry.EVENTS:
        return {**telemetry.AUTO_PROPS, **telemetry.EVENTS[event]}
    metrics = _metrics_events()
    if event in metrics:
        return dict(metrics[event])
    pending = manifest["pending_events"].get(event)
    if pending:
        return {**telemetry.AUTO_PROPS, **pending["properties"]}
    return None


def _values_of(spec: dict) -> set | None:
    return set(spec["values"]) if spec.get("kind") == "enum" else None


def _is_pending_event(manifest: dict, event: str) -> bool:
    return event in manifest["pending_events"] and event not in telemetry.EVENTS


def _walk_typed(q: dict):
    """从类型化查询推出 (事件, 属性键, 过滤值) 引用。返回 (events, refs)。

    refs 是 (event_or_None, prop, values_or_None)：event 为 None 表示作用于所有 series 事件（拆分/整体过滤）。
    """
    events: list[str] = []
    refs: list[tuple[str | None, str, list | None]] = []
    kind = q["kind"]
    if kind == "RetentionQuery":
        rf = q["retentionFilter"]
        for k in ("targetEntity", "returningEntity"):
            events.append(rf[k]["id"])
        return events, refs
    for node in q["series"]:
        events.append(node["event"])
        for p in node.get("properties", []):
            refs.append((node["event"], p["key"], p["value"]))
    bf = q.get("breakdownFilter") or {}
    if "breakdown" in bf:
        refs.append((None, bf["breakdown"], None))
    for b in bf.get("breakdowns", []):
        refs.append((None, b["property"], None))
    top = q.get("properties")
    if top:
        for grp in top.get("values", []):
            for p in grp.get("values", []):
                refs.append((None, p["key"], p["value"]))
    return events, refs


_HOGQL_EVENT_EQ = re.compile(r"\bevent\s*=\s*'([a-z_]+)'")
_HOGQL_EVENT_IN = re.compile(r"\bevent\s+IN\s*\(([^)]*)\)")
_HOGQL_ANY_EVENT = re.compile(r"any\(event\)\s*=\s*'([a-z_]+)'")
_HOGQL_PROP = re.compile(r"\bproperties\.([a-z_]+)")
_HOGQL_PROP_EQ = re.compile(r"\bproperties\.([a-z_]+)\s*=\s*'([^']*)'")


def _walk_hogql(sql: str):
    events: set[str] = set(_HOGQL_EVENT_EQ.findall(sql)) | set(_HOGQL_ANY_EVENT.findall(sql))
    for body in _HOGQL_EVENT_IN.findall(sql):
        events |= set(re.findall(r"'([a-z_]+)'", body))
    props = set(_HOGQL_PROP.findall(sql))
    eq = _HOGQL_PROP_EQ.findall(sql)
    return events, props, eq


def _derived(manifest: dict, ins: dict) -> tuple[set[str], set[str]]:
    """(引用的事件集合, 引用的属性名集合)。"""
    q = ins["query"]
    if q["kind"] == "HogQLQuery":
        ev, props, _ = _walk_hogql(q["query"])
        return ev, props
    ev, refs = _walk_typed(q)
    return set(ev), {p for _, p, _ in refs}


def _declared(ins: dict) -> tuple[set[str], set[str]]:
    props = {p for ps in ins["properties"].values() for p in ps}
    return set(ins["events"]), props


# ---------------------------------------------------------------------------
# 前提：真的扫到了东西
# ---------------------------------------------------------------------------
def test_manifest_is_not_empty(manifest):
    """空集合上的断言恒真：先钉住 manifest 里有图、有告警、有订阅、有待发布事件。"""
    assert len(manifest["insights"]) >= 15
    assert manifest["alerts"] and manifest["subscriptions"] and manifest["dashboards"]
    # export_result 随 #1103 / CONSENT_VERSION 3 进了 EVENTS：不再是待发布事件（线上上线前仍无数据，见 posthog-dashboard.md）
    assert "export_result" in telemetry.EVENTS and "export_result" not in manifest["pending_events"]
    assert any(i["query"]["kind"] == "HogQLQuery" for i in manifest["insights"])
    assert any(i["query"]["kind"] == "TrendsQuery" for i in manifest["insights"])


# ---------------------------------------------------------------------------
# 1. 事件必须存在，或登记为待发布
# ---------------------------------------------------------------------------
def test_every_event_is_on_main_or_pending_with_depends_on(manifest):
    for ins in manifest["insights"]:
        derived_events, _ = _derived(manifest, ins)
        assert derived_events, f"{ins['logical_name']}: 查询里没有推出任何事件，解析多半失效"
        for e in derived_events:
            on_main = e in telemetry.EVENTS
            is_metrics = e in _metrics_events()
            pending = manifest["pending_events"].get(e)
            assert on_main or is_metrics or pending, (
                f"{ins['logical_name']}: 事件 {e} 既不在 telemetry.EVENTS、也不是发行量事件、"
                f"也没登记在 pending_events"
            )
            if _is_pending_event(manifest, e):
                assert pending.get("depends_on"), f"pending 事件 {e} 缺 depends_on"
                assert ins["status"] == "pending_event", (
                    f"{ins['logical_name']}: 引用了未上 main 的事件 {e}，status 必须是 pending_event"
                )
                assert ins.get("depends_on"), f"{ins['logical_name']}: 缺 depends_on"
                assert ins.get("fallback"), f"{ins['logical_name']}: 缺当前数据回退说明 fallback"


def test_pending_status_is_only_for_pending_events(manifest):
    """反向：标了 pending_event 的图必须真的引用了待发布事件，否则是空标记。"""
    for ins in manifest["insights"]:
        derived_events, _ = _derived(manifest, ins)
        uses_pending = any(_is_pending_event(manifest, e) for e in derived_events)
        assert (ins["status"] == "pending_event") == uses_pending, ins["logical_name"]


def test_declared_events_and_properties_match_the_query(manifest):
    for ins in manifest["insights"]:
        d_events, d_props = _declared(ins)
        q_events, q_props = _derived(manifest, ins)
        if ins["query"]["kind"] == "HogQLQuery":
            # HogQL 里属性名可以出现在声明之外（自动属性、辅助列）；事件集合必须完全一致
            assert d_events == q_events, f"{ins['logical_name']}: events 声明与查询不一致"
        else:
            assert d_events == q_events, f"{ins['logical_name']}: events 声明与查询不一致"
            assert d_props == q_props, f"{ins['logical_name']}: properties 声明与查询不一致"


# ---------------------------------------------------------------------------
# 2. 属性必须在该事件的白名单里
# ---------------------------------------------------------------------------
def test_every_typed_property_exists_on_its_event_and_filter_values_are_valid(manifest):
    for ins in manifest["insights"]:
        q = ins["query"]
        if q["kind"] == "HogQLQuery":
            continue
        events, refs = _walk_typed(q)
        for ev_name, prop, values in refs:
            targets = [ev_name] if ev_name else events
            for t in targets:
                schema = _schema_of(manifest, t)
                assert schema is not None, f"{ins['logical_name']}: 无法取得 {t} 的属性表"
                assert prop in schema, f"{ins['logical_name']}: 事件 {t} 没有属性 {prop}"
                if values is not None:
                    allowed = _values_of(schema[prop])
                    if allowed is not None:
                        assert set(values) <= allowed, (
                            f"{ins['logical_name']}: {t}.{prop} 过滤值 {values} 不在枚举 {sorted(allowed)}"
                        )
                    elif schema[prop]["kind"] == "int":
                        assert all(
                            isinstance(v, int) and 0 <= v <= schema[prop]["max"] for v in values
                        )


def test_every_hogql_property_exists_in_a_referenced_event(manifest):
    for ins in manifest["insights"]:
        q = ins["query"]
        if q["kind"] != "HogQLQuery":
            continue
        events, props, eq = _walk_hogql(q["query"])
        schemas = [s for s in (_schema_of(manifest, e) for e in events) if s is not None]
        union = {k for s in schemas for k in s}
        for p in props:
            assert p in union, (
                f"{ins['logical_name']}: HogQL 引用的 properties.{p} 不在所引用事件的属性并集里"
            )
        for p, val in eq:
            holders = [s[p] for s in schemas if p in s and _values_of(s[p]) is not None]
            assert holders, f"{ins['logical_name']}: properties.{p} 不是枚举属性却被拿来比较字面值"
            assert any(val in _values_of(h) for h in holders), (
                f"{ins['logical_name']}: properties.{p} = '{val}' 不在任何被引事件的枚举里"
            )


def test_the_unknown_property_probe_is_not_vacuous(manifest):
    """自检：解析器确实能从真实 manifest 里取到属性与值（否则上面两条在空集合上恒绿）。"""
    seen_typed = sum(
        len(_walk_typed(i["query"])[1])
        for i in manifest["insights"]
        if i["query"]["kind"] != "HogQLQuery"
    )
    seen_hogql = sum(
        len(_walk_hogql(i["query"]["query"])[2])
        for i in manifest["insights"]
        if i["query"]["kind"] == "HogQLQuery"
    )
    assert seen_typed >= 10 and seen_hogql >= 3


# ---------------------------------------------------------------------------
# 3. 分析纪律字段
# ---------------------------------------------------------------------------
def test_every_insight_has_the_discipline_fields(manifest):
    for ins in manifest["insights"]:
        for f in DISCIPLINE_FIELDS:
            assert f in ins and ins[f] not in (None, "", [], {}), f"{ins['logical_name']}: 缺 {f}"
        for f in ("denominator", "data_start", "missing_data_note", "window", "description"):
            assert isinstance(ins[f], str) and len(ins[f].strip()) >= 6, (
                f"{ins['logical_name']}: {f} 过短"
            )
        assert ins["population"] in ("opted_in_install", "distribution_metrics")
        assert ins["data_start_value"] is None and ins["data_start_verified"] is False, (
            f"{ins['logical_name']}: 没有线上访问，data_start 不能声称已核实"
        )


def test_hogql_installs_exclude_distribution_metrics_and_ci(manifest):
    """产品人群的 HogQL 必须排除发行量机器人；『疑似 CI』排除要么带上，要么图本身是敏感性对照。"""
    frag = manifest["shared_fragments"]["ci_suspected_exclusion_hogql"]
    for ins in manifest["insights"]:
        q = ins["query"]
        if q["kind"] != "HogQLQuery" or ins["population"] != "opted_in_install":
            continue
        sql = q["query"]
        assert "distribution_metrics" in sql, f"{ins['logical_name']}: 未排除 distribution_metrics"
        if ins["logical_name"] != "a6_observed_installs_ci_sensitivity":
            assert frag in sql, f"{ins['logical_name']}: 缺疑似 CI 排除片段"
    sens = next(
        i
        for i in manifest["insights"]
        if i["logical_name"] == "a6_observed_installs_ci_sensitivity"
    )
    assert "installs_including_suspected_ci" in sens["query"]["query"]
    assert "installs_excluding_suspected_ci" in sens["query"]["query"]


def test_distribution_insights_are_separate_from_product_population(manifest):
    for ins in manifest["insights"]:
        evs, _ = _derived(manifest, ins)
        dist = evs & set(_metrics_events())
        if dist:
            assert ins["population"] == "distribution_metrics", ins["logical_name"]
            assert not (evs & set(telemetry.EVENTS)), (
                f"{ins['logical_name']}: 发行量与产品事件混在一张图"
            )


# ---------------------------------------------------------------------------
# 4. 告警 / 订阅：全部禁用、收件人未设
# ---------------------------------------------------------------------------
ALERT_FIELDS = (
    "data_source",
    "executable_query",
    "window",
    "min_sample",
    "threshold",
    "threshold_label",
    "recovery_and_dedup",
    "observation_scope",
    "known_bias",
    "channel",
    "recipient",
    "frequency",
    "timezone",
    "consumes_free_slot",
)


def test_alerts_are_disabled_unset_and_complete(manifest):
    alerts = manifest["alerts"]
    assert 1 <= len(alerts) <= 5, "普通告警最多 5 个（免费名额）"
    by_name = {i["logical_name"]: i for i in manifest["insights"]}
    for a in alerts:
        assert a["enabled"] is False, f"{a['logical_name']}: 必须禁用"
        assert a["recipient"] == "UNSET" and a["channel"] == "UNSET", a["logical_name"]
        for f in ALERT_FIELDS:
            assert f in a and a[f] not in (None, "", {}), f"{a['logical_name']}: 缺 {f}"
        assert "建议值" in a["threshold_label"] and "SLO" in a["threshold_label"]
        src = by_name.get(a["insight"])
        assert src is not None, f"{a['logical_name']}: 数据源 insight {a['insight']} 不存在"
        assert src["query"]["kind"] == "TrendsQuery", (
            f"{a['logical_name']}: 告警只能挂 Trends insight"
        )
        assert a["remote_id"] is None
        if src["status"] == "pending_event":
            assert a["status"] == "pending_event" and a.get("depends_on"), a["logical_name"]


def test_subscriptions_are_disabled_unset_and_target_a_known_dashboard(manifest):
    dashboards = {d["logical_name"] for d in manifest["dashboards"]}
    names = {i["logical_name"] for i in manifest["insights"]}
    for s in manifest["subscriptions"]:
        assert s["enabled"] is False, s["logical_name"]
        assert s["recipient"] == "UNSET" and s["channel"] == "UNSET", s["logical_name"]
        assert s["ai_summary"] is False
        assert s["remote_id"] is None
        if s["target_type"] == "dashboard":
            assert s["target"] in dashboards
        for key in ("key_ops_insights", "version_insights", "pending_blank"):
            assert set(s["preview"][key]) <= names, (
                f"{s['logical_name']}: 预览引用了不存在的 insight"
            )


# ---------------------------------------------------------------------------
# 5. 逻辑名唯一；没有任何线上标识
# ---------------------------------------------------------------------------
def _all_assets(manifest: dict) -> list[dict]:
    return (
        manifest["dashboards"]
        + manifest["insights"]
        + manifest["alerts"]
        + manifest["subscriptions"]
    )


def test_logical_names_are_unique_across_all_assets(manifest):
    names = [a["logical_name"] for a in _all_assets(manifest)]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"logical_name 重复: {dupes}"
    for n in names:
        assert re.fullmatch(r"[a-z0-9_-]+", n), f"logical_name 形状不合法: {n}"


def test_nothing_is_created_online(manifest):
    assert manifest["online_state"]["created"] is False
    assert "线上未创建" in manifest["online_state"]["statement"]
    for a in _all_assets(manifest):
        assert a["remote_id"] is None, f"{a['logical_name']}: remote_id 必须为 null"
        assert a.get("url") in (None,), f"{a['logical_name']}: url 必须为 null"
        assert a["status"] in ("not_created", "pending_event"), a["logical_name"]
    assert manifest["project"]["id"] == 567339


def test_every_dashboard_group_lists_only_known_insights_and_all_are_placed(manifest):
    names = {i["logical_name"] for i in manifest["insights"]}
    placed: list[str] = []
    for d in manifest["dashboards"]:
        for g in d["groups"]:
            assert set(g["insights"]) <= names
            placed += g["insights"]
    assert sorted(placed) == sorted(names), "每张 insight 必须恰好出现在一个看板分组里"
    assert {g["name"] for g in manifest["dashboards"][0]["groups"]} == {
        "使用与激活",
        "关键操作健康",
        "引导与回访",
        "版本与分发",
    }
